"""
white_salary/core/agent/chat_agent.py

对话智能体 - White Salary 的"大脑"。

这是最核心的模块，负责：
  1. 接收用户输入
  2. 组装上下文（系统提示词 + 记忆 + 用户消息）
  3. 调用LLM生成回复
  4. 保存对话到记忆
  5. 返回回复

整个对话流程：
  用户说话 → chat_agent接收 → 组装上下文 → 发给LLM → 拿到回复 → 存入记忆 → 返回回复
"""

# 2026-07-03 工具实现（批9）：Callable 用于 hint→(工具名,参数抽取器) 注册表类型注解
from typing import Any, AsyncGenerator, Callable
from pathlib import Path

from loguru import logger

from white_salary.core.interfaces.llm import LLMInterface
from white_salary.core.interfaces.types import Message, MessageRole, ToolCall, ToolResult
from white_salary.core.memory.short_term import ShortTermMemory
from white_salary.core.memory.manager import MemoryManager
from white_salary.core.filter.content_filter import ContentFilter
from white_salary.core.personality.character import PersonalityManager
# 2026-07-02 审计修复（批2）：接通 MessageRouter.get_tool_hint（原为死代码）
from white_salary.core.message.processing import MessageRouter


TOOL_SILENT_SENTINEL = "__WHITE_SALARY_TOOL_SILENT__"


class ToolResultPresentationError(RuntimeError):
    """Tool ran, but no safe persona reply could be produced for the user."""


class ChatAgent:
    """
    对话智能体。

    White Salary 的核心对话引擎。
    把LLM、记忆、人格三个系统串联起来，实现完整的对话能力。
    """

    def __init__(
        self,
        llm: LLMInterface,
        personality: PersonalityManager,
        memory: ShortTermMemory,
        memory_manager: MemoryManager | None = None,
        tool_registry: "ToolRegistry | None" = None,
        tool_llm: "LLMInterface | None" = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
        content_filter_enabled: bool = True,
    ) -> None:
        """
        初始化对话智能体。

        参数:
            llm:         LLM适配器（用来调用大语言模型）
            personality: 人格管理器（提供系统提示词）
            memory:      短期记忆（管理对话上下文）
            temperature: 创造性程度（传给LLM的参数）
            max_tokens:  最大回复长度（传给LLM的参数）
            content_filter_enabled: 2026-07-03 面板升级（批6）：内容过滤开关——
                         run_server 装配时传 config.features.content_filter，
                         修复原硬编码 enabled=True 导致面板开关空转的问题
                         （默认True=原行为；False时ContentFilter只记录不过滤）
        """
        self._llm = llm
        self._personality = personality
        self._memory = memory
        self._memory_manager = memory_manager
        self._tools = tool_registry
        self._tool_llm = tool_llm  # 独立的工具判断LLM（不占用主对话模型）
        self._content_filter_enabled = bool(content_filter_enabled)
        # 2026-07-03 面板升级（批6）：enabled 改为装配期可控（原硬编码True）
        self._content_filter = ContentFilter(enabled=content_filter_enabled)
        self._temperature = temperature
        self._max_tokens = max_tokens
        # 2026-07-02 审计修复（批2）：消息路由器——命中「回忆/记得/之前聊过」类意图时
        # 直连 recall_conversation，不依赖 tool_llm 判断（打通跨平台回忆的关键）
        self._router = MessageRouter()
        # 2026-07-02 审计修复（批2）：DeepSeek function calling 的 tools 上限为128，
        # 注册数超过100时提前告警，防止工具列表再次膨胀逼近上限、拖慢工具判断
        if tool_registry is not None and tool_registry.count > 100:
            logger.warning(
                f"[Agent] 工具注册数达 {tool_registry.count} 个（>100），"
                f"接近 DeepSeek 的 128 个上限，建议继续瘦身工具列表"
            )

    def clone_with_memory(self, memory: ShortTermMemory) -> "ChatAgent":
        """Create an isolated conversation agent while sharing stateless services."""
        return ChatAgent(
            llm=self._llm,
            personality=self._personality,
            memory=memory,
            memory_manager=self._memory_manager,
            tool_registry=self._tools,
            tool_llm=self._tool_llm,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
            content_filter_enabled=self._content_filter_enabled,
        )

    async def chat(self, user_input: str, user_name: str | None = None,
                   user_id: str = "desktop", is_group: bool = False,
                   group_id: str = "") -> str:
        """
        和 White Salary 对话（完整回复模式）。

        流程：
          1. 用户输入存入记忆
          2. 组装完整上下文
          3. 调用LLM
          4. AI回复存入记忆
          5. 返回回复

        参数:
            user_input: 用户说的话
            user_name:  用户名（可选）
            group_id:   群号（用于记忆标记，防并发交错串群）

        返回:
            White Salary 的回复文本
        """
        logger.debug(f"收到用户输入: {user_input[:50]}...")

        # 1. 用户消息存入记忆
        self._memory.add_user_message(user_input, name=user_name)

        # 2. 组装上下文（系统提示词 + 核心记忆 + 相关记忆 + 好感度 + 对话历史）
        system_msg = self._build_system_message(
            current_message=user_input, user_id=user_id, is_group=is_group,
            group_id=group_id,
        )
        context = self._memory.get_context_messages(system_message=system_msg)
        context = self._strip_memory_tags(context)  # 去掉记忆标记，防大模型模仿

        # 3. 调用LLM
        logger.debug(f"发送给LLM: {len(context)}条消息")
        response = await self._llm.chat_completion(
            messages=context,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
        )

        # 4. 内容过滤
        filter_result = self._content_filter.filter(response)
        response = filter_result.text

        # 4.5 机器话过滤（去掉说教/波浪号/重复语气词等）
        response = self._apply_human_like_filter(response)

        # 5. AI回复存入记忆（加标记防并发交错串群）
        memory_response = self._tag_response(response, user_name, group_id, is_group)
        self._memory.add_assistant_message(memory_response)

        # 6. 提取并存储记忆
        await self._extract_memory(user_input, response,
                                   user_id=user_id, is_group=is_group)

        logger.debug(f"LLM回复: {response[:50]}...")
        return response

    async def chat_stream(
        self,
        user_input: str,
        user_id: str = "desktop",
        is_group: bool = False,
        user_name: str | None = None,
        group_id: str = "",
    ) -> AsyncGenerator[str, None]:
        """
        和 White Salary 对话（流式回复模式）。

        LLM一边想一边返回文本片段，实现更低的响应延迟。
        完整的回复会自动存入记忆。

        参数:
            user_input: 用户说的话
            user_name:  用户名（可选）
            group_id:   群号（用于记忆标记，防并发交错串群）

        返回:
            异步生成器，逐段返回回复文本
        """
        logger.debug(f"收到用户输入(流式): {user_input[:50]}...")

        # 1. 用户消息存入记忆
        self._memory.add_user_message(user_input, name=user_name)

        # 2. 组装上下文（系统提示词 + 核心记忆 + 相关记忆 + 好感度 + 对话历史）
        system_msg = self._build_system_message(
            current_message=user_input, user_id=user_id, is_group=is_group,
            group_id=group_id,
        )
        context = self._memory.get_context_messages(system_message=system_msg)
        context = self._strip_memory_tags(context)  # 去掉记忆标记，防大模型模仿

        # 3. 流式调用LLM，同时收集完整回复
        full_response_parts: list[str] = []

        async for chunk in self._llm.chat_completion_stream(
            messages=context,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
        ):
            full_response_parts.append(chunk)
            yield chunk

        # 4. 存入记忆（加标记防并发交错串群）
        full_response = "".join(full_response_parts)
        memory_response = self._tag_response(full_response, user_name, group_id, is_group)
        self._memory.add_assistant_message(memory_response)

        # 5. 提取并存储记忆
        await self._extract_memory(user_input, full_response,
                                   user_id=user_id, is_group=is_group)

        logger.debug(f"LLM流式回复完成: {full_response[:50]}...")

    def _build_system_message(self, current_message: str = "",
                             user_id: str = "desktop",
                             is_group: bool = False,
                             group_id: str = "") -> Message:
        """构建系统消息（人格提示词 + 核心记忆 + 相关长期记忆 + 好感度提示）。"""
        base_msg = self._personality.get_system_message()
        extra_parts = []

        # 注入核心记忆 + 与当前话题相关的长期记忆 + 所有模块上下文
        if self._memory_manager:
            memory_ctx = self._memory_manager.get_context_injection(
                current_message=current_message, user_id=user_id,
                is_group=is_group, group_id=group_id,
            )
            if memory_ctx:
                extra_parts.append(memory_ctx)

        # 注入好感度提示（按用户动态获取真实好感度，不用全局实例）
        try:
            from white_salary.core.affinity.manager import AffinityManager as _AffMgr
            _affinity_dir = str(Path(__file__).resolve().parents[4] / "data" / "affinity")
            _user_aff = _AffMgr.get_for_user(user_id, data_dir=_affinity_dir)
            extra_parts.append(_user_aff.get_context_hint())
        except Exception:
            pass

        # The persona file contains desktop-owner assumptions. They are valid on
        # the desktop, but must not silently turn every QQ stranger into the
        # owner or disable group-chat rules. Current-turn identity wins.
        try:
            from white_salary.core.memory.manager import is_owner_user

            if not is_owner_user(user_id):
                extra_parts.append(
                    "[当前对话身份边界 - 高优先级]\n"
                    "当前说话者不是主人。不要套用人设中只属于小白/chowmanbun/主人的"
                    "称呼、共同经历或家人式亲密；以当前用户自己的好感度和记忆为准。"
                )
        except Exception:
            pass

        if is_group:
            extra_parts.append(
                "[当前平台规则 - 高优先级]\n"
                "这是 QQ 群聊，不是一对一桌面对话。人设中任何“忽略群聊/QQ规则”"
                "或“当前只有主人”的桌面专用说明在本轮不适用；只回应当前发送者，"
                "不要泄露其他群或私聊内容。"
            )

        if extra_parts:
            return Message(
                role=MessageRole.SYSTEM,
                content=base_msg.content + "\n\n" + "\n\n".join(extra_parts),
            )

        return base_msg

    async def chat_stream_with_tools(
        self,
        user_input: str,
        user_name: str | None = None,
        user_id: str = "desktop",
        is_group: bool = False,
        group_id: str = "",
        route_text: str | None = None,
        allow_tools: bool = True,
        tool_context: dict | None = None,
        cancellation: Any = None,
        runtime_store: "RuntimeStore | None" = None,
        runtime_task_id: str = "",
        tool_progress: Callable | None = None,
    ) -> AsyncGenerator[str, None]:
        """
        双模型多轮工具对话。

        流程：
          1. 用户消息存入会话记忆并组装上下文
          2. tool_llm只在当前平台/权限可用的工具中判断
          3. 执行工具并把真实结果交回tool_llm，最多继续四轮
          4. 只读工具可并行；有副作用的工具串行并再次校验权限
          5. 工具阶段结束后，主模型结合结果按人设组织最终回复

        没有tool_llm或没有工具时退化为普通chat_stream。
        """
        import json as _json
        import re

        # 没有独立的tool_llm/没有工具/本轮禁止工具 → 普通流式
        if not allow_tools or not self._tool_llm or not self._tools or self._tools.count == 0:
            async for chunk in self.chat_stream(
                user_input, user_name=user_name, user_id=user_id,
                is_group=is_group, group_id=group_id,
            ):
                yield chunk
            return

        logger.debug(f"收到用户输入(工具模式): {user_input[:50]}...")
        route_source = route_text if route_text is not None else user_input

        # 1. 用户消息存入记忆
        self._memory.add_user_message(user_input, name=user_name)

        # 2. 组装上下文（主模型用的，不带工具定义）
        system_msg = self._build_system_message(
            current_message=user_input, user_id=user_id, is_group=is_group,
            group_id=group_id,
        )
        context = self._memory.get_context_messages(system_message=system_msg)
        context = self._strip_memory_tags(context)  # 去掉记忆标记，防大模型模仿

        judge_context = context
        if route_text is not None:
            judge_context = list(context)
            last_user_index = next(
                (
                    i for i in range(len(judge_context) - 1, -1, -1)
                    if judge_context[i].role == MessageRole.USER
                ),
                None,
            )
            route_msg = Message(role=MessageRole.USER, content=route_source)
            if last_user_index is None:
                judge_context.append(route_msg)
            else:
                judge_context[last_user_index] = route_msg

        # 2.5 2026-07-02 审计修复（批2）：接通 MessageRouter.get_tool_hint（原为死代码）。
        # 2026-07-03 工具实现（批9）：把批2写死的「只认 recall_conversation」直连逻辑
        # 通用化为 hint→(工具名, 参数抽取器) 注册表（见 _FORCED_ROUTES / _HINT_INJECT_TOOLS）：
        #   - 强制直连（绕过 tool_llm 直接执行）：仅注册 recall_conversation——
        #     它的参数抽取失败可退化为空关键词查最近记录，仍然可用；
        #   - 注入提示（把提示词并入 tool_llm 判断上下文提高选中率，不强制执行）：
        #     set_reminder/cancel_reminder/set_quiet_mode——提醒的时间参数用正则硬抽
        #     一旦抽错会设出【错误的提醒】，比不设更糟，故参数仍交给 tool_llm 抽，
        #     只用提示词把选中率拉高（这就是本批为 set_reminder 选择的方案与理由）。
        forced_results: list[ToolResult] = []
        executed_tool_keys: set[tuple[str, str]] = set()  # (工具名, 规范化参数) 防重复执行
        try:
            tool_hint = self._router.get_tool_hint(route_source)
        except Exception as hint_err:
            logger.warning(f"[Agent] 工具意图提示路由失败: {hint_err}")
            tool_hint = ""

        forced_name, arg_candidates = self._match_forced_route(tool_hint, route_source)
        if forced_name and self._tools.get_tool(forced_name):
            try:
                if cancellation is not None:
                    cancellation.raise_if_cancelled()
                result_text = ""
                for candidate_args in arg_candidates:
                    result_text = await self._execute_registered_tool(
                        forced_name,
                        candidate_args,
                        tool_context,
                    )
                    executed_tool_keys.add(
                        (forced_name,
                         _json.dumps(candidate_args, sort_keys=True, ensure_ascii=False))
                    )
                    # 前一组参数查不到（"没有找到"开头）才尝试下一组兜底参数
                    if not result_text.startswith("没有找到"):
                        break
                forced_results.append(ToolResult(
                    call_id=f"forced_{forced_name}", content=result_text,
                ))
                logger.info(f"[Agent] 命中直连意图，强制执行 {forced_name}")
            except Exception as forced_err:
                logger.warning(f"[Agent] 强制执行 {forced_name} 失败: {forced_err}")

        # 2026-07-03 工具实现（批9）：注入型提示——命中注册表中的提醒/静默类意图时，
        # 给 tool_llm 的判断上下文追加一条 system 提示，提高对应工具被选中的概率。
        # 只影响 tool_llm 的判断输入，不改主模型上下文、不强制执行任何工具。
        if tool_hint and not forced_results and any(
            name in tool_hint for name in self._HINT_INJECT_TOOLS
        ):
            judge_context = context + [
                Message(role=MessageRole.SYSTEM, content=f"[工具选择提示] {tool_hint}")
            ]
            logger.debug(f"[Agent] 注入工具选择提示: {tool_hint[:40]}")

        # 3. 保留 tool_llm 作为最终判断者，最多连续判断四轮。
        # 每轮都会看到之前的真实工具结果，因此可以自然地继续调用下一个工具。
        tool_results: list[ToolResult] = list(forced_results)
        silent_side_effect_done = False
        if not forced_results:
            try:
                from white_salary.core.runtime.tool_loop import ToolLoopRunner

                loop_outcome = await ToolLoopRunner(
                    tool_llm=self._tool_llm,
                    registry=self._tools,
                    max_rounds=4,
                    judge_timeout=15.0,
                ).run(
                    judge_context,
                    access_context=tool_context,
                    cancellation=cancellation,
                    store=runtime_store,
                    task_id=runtime_task_id,
                    progress=tool_progress,
                )
                tool_results.extend(loop_outcome.tool_results)
                silent_success_prefixes = {
                    "qq_send_voice": "语音已发送",
                    "qq_send_sticker": "表情包已发送",
                    "push_to_desktop": "已推送到桌面端",
                }
                silent_side_effect_done = any(
                    run.ok
                    and run.name in silent_success_prefixes
                    and run.content.strip().startswith(silent_success_prefixes[run.name])
                    for run in loop_outcome.runs
                )
                logger.debug(
                    f"[Agent] 多轮工具循环结束: rounds={loop_outcome.rounds}, "
                    f"runs={len(loop_outcome.runs)}, reason={loop_outcome.stop_reason}"
                )
            except Exception as loop_error:
                logger.warning(f"[Agent] 多轮工具循环失败，降级为无工具回复: {loop_error}")

        # 4. 工具阶段结束后，由主模型组织最终自然回复。
        main_chunks: list[str] = []

        try:
            if tool_results:
                if silent_side_effect_done and len(tool_results) == 1:
                    yield TOOL_SILENT_SENTINEL
                    full_response = ""
                    return
                # 把工具结果喂给主模型（process_tool_results内部会加提示）
                try:
                    tool_failed = self._tool_results_have_failure(tool_results)
                    final_reply = await self._llm.process_tool_results(
                        messages=context,
                        tool_results=tool_results,
                        temperature=self._temperature,
                        max_tokens=self._max_tokens,
                    )
                    final_reply = (final_reply or "").strip()
                    if (
                        tool_failed
                        and self._is_weak_tool_failure_reply(final_reply)
                    ):
                        repaired_reply = await self._repair_tool_failure_reply(
                            user_input=user_input,
                            context=context,
                            tool_results=tool_results,
                        )
                        if repaired_reply:
                            final_reply = repaired_reply
                            logger.warning(
                                "[Agent] 工具失败后处理回复过短，已要求主模型按人设自然重写"
                            )
                        else:
                            raise ToolResultPresentationError(
                                "主模型未能把工具失败结果转换为自然回复"
                            )
                    if not final_reply:
                        final_reply = await self._repair_tool_failure_reply(
                            user_input=user_input,
                            context=context,
                            tool_results=tool_results,
                        )
                        if not final_reply:
                            raise ToolResultPresentationError(
                                "主模型未能把工具结果转换为自然回复"
                            )
                    parts = re.split(r'(?<=[。！？!?\n])', final_reply)
                    for part in parts:
                        if part:
                            yield part
                    full_response = final_reply
                except ToolResultPresentationError:
                    raise
                except Exception as e:
                    # process_tool_results失败时，先让主模型按人设解释失败；不拼固定回复。
                    logger.warning(f"工具结果处理失败，降级回复: {e}")
                    fallback = ""
                    fallback = await self._repair_tool_failure_reply(
                        user_input=user_input,
                        context=context,
                        tool_results=tool_results,
                    )
                    if not fallback:
                        raise ToolResultPresentationError(
                            "工具结果的两次自然语言整理均失败"
                        ) from e
                    yield fallback
                    full_response = fallback

            else:
                # ====== 没有工具调用：直接用主模型流式回复 ======
                logger.debug("[Agent] 小助理判断不需要工具，主模型直接回复")

                async for chunk in self._llm.chat_completion_stream(
                    messages=context,
                    temperature=self._temperature,
                    max_tokens=self._max_tokens,
                ):
                    main_chunks.append(chunk)
                    yield chunk

                full_response = "".join(main_chunks)

        except ToolResultPresentationError:
            raise
        except Exception as e:
            # Retrying the same provider immediately used to create duplicate
            # requests and repeated desktop error cards. The transport layer now
            # reports this single failure and decides when a later retry is safe.
            logger.warning(f"工具模式对话失败: {e}")
            raise

        # 7. 存入记忆（加标记防并发交错串群）
        if full_response:
            # 机器话过滤
            full_response = self._apply_human_like_filter(full_response)
            memory_response = self._tag_response(full_response, user_name, group_id, is_group)
            self._memory.add_assistant_message(memory_response)
            await self._extract_memory(user_input, full_response,
                                       user_id=user_id, is_group=is_group)

        logger.debug(f"工具模式回复完成: {full_response[:50]}...")

    async def _extract_memory(self, user_input: str, ai_reply: str,
                             user_id: str = "desktop",
                             is_group: bool = False) -> None:
        """从对话中提取记忆并存储，同时分析好感度变化。"""
        if self._memory_manager:
            try:
                extracted = await self._memory_manager.extract_and_store(
                    user_input, ai_reply, user_id=user_id, is_group=is_group
                )
                if extracted:
                    logger.debug(f"[Memory] Extracted: {extracted}")
            except Exception as e:
                logger.warning(f"Memory extraction failed: {e}")

        # 好感度的process_message由qq_handler调用（用get_for_user获取正确用户实例）
        # 这里不再重复调用，避免同一条消息加两次分

    def _apply_human_like_filter(self, text: str) -> str:
        """机器话过滤（去掉说教/波浪号/重复语气词等）。"""
        try:
            from white_salary.core.memory.human_like_filter import HumanLikeFilter
            if not hasattr(self, '_human_filter'):
                self._human_filter = HumanLikeFilter()
            return self._human_filter.filter_response(text)
        except Exception:
            return text

    def _tool_results_have_failure(self, tool_results: list[ToolResult]) -> bool:
        """Whether tool results clearly say a tool failed or timed out."""
        contents = [str(r.content or "").strip() for r in tool_results if str(r.content or "").strip()]
        if not contents:
            return False

        joined = "；".join(contents)
        lower = joined.lower()
        failure_markers = (
            "执行失败", "执行超时", "操作失败", "失败", "超时",
            "结果未知", "未确认", "failed", "timeout", "timed out", "error",
            "outcome_unknown", "outcome unknown",
        )
        return any(marker in joined or marker in lower for marker in failure_markers)

    def _summarize_tool_results_for_llm(self, tool_results: list[ToolResult]) -> str:
        """Compact tool results for a repair prompt without turning them into user-facing wording."""
        lines: list[str] = []
        for result in tool_results:
            content = str(result.content or "").strip()
            if not content:
                continue
            lines.append(f"- {content[:400]}")
        return "\n".join(lines)

    async def _repair_tool_failure_reply(
        self,
        *,
        user_input: str,
        context: list[Message],
        tool_results: list[ToolResult],
    ) -> str:
        """
        Ask the main model to explain a failed tool call naturally.

        This intentionally does not return hand-written fixed wording. The model
        already has Bai's system prompt in context, so it should answer in the
        current persona and adapt to the actual request/result.
        """
        tool_summary = self._summarize_tool_results_for_llm(tool_results)
        if not tool_summary:
            return ""

        lowered_summary = tool_summary.lower()
        outcome_unknown = any(marker in lowered_summary or marker in tool_summary for marker in (
            "outcome_unknown", "outcome unknown", "结果未知", "未确认", "不要自动重发",
        ))
        failed = self._tool_results_have_failure(tool_results)
        if outcome_unknown:
            outcome_rule = (
                "工具的外部操作结果不确定。不要说它一定成功或一定失败，也不要建议立刻重试；"
                "请自然说明目前没有拿到可靠回执，并结合场景告诉用户先到对应位置核对。"
            )
        elif failed:
            outcome_rule = (
                "工具调用已知没有成功。自然说明这次没办成；能看出原因就简短说原因。"
            )
        else:
            outcome_rule = "工具已有结果。结合用户请求自然说明结果，不要照抄日志。"

        repair_messages = list(context)
        repair_messages.extend([
            Message(
                role=MessageRole.SYSTEM,
                content=(
                    "工具调用后的回复规则：你仍然保持当前角色和说话方式。"
                    f"{outcome_rule}"
                    "不要照抄工具日志，不要说固定模板，不要只回复“注意/如果/好的/失败了”。"
                ),
            ),
            Message(
                role=MessageRole.USER,
                content=(
                    f"用户刚才的请求：{user_input}\n\n"
                    f"工具返回结果：\n{tool_summary}\n\n"
                    "请现在自然地回复用户。"
                ),
            ),
        ])

        try:
            reply = await self._llm.chat_completion(
                messages=repair_messages,
                temperature=self._temperature,
                max_tokens=min(self._max_tokens, 320),
            )
        except Exception as e:
            logger.warning(f"[Agent] 工具失败自然回复重写失败: {e}")
            return ""

        reply = (reply or "").strip()
        if self._is_weak_tool_failure_reply(reply):
            return ""
        return reply

    def _is_weak_tool_failure_reply(self, reply: str) -> bool:
        """Whether the LLM reply is too short to explain a tool failure."""
        text = (reply or "").strip()
        if not text:
            return True
        stripped = text.strip("。.!！?？…~～ \t\r\n")
        weak_exact = {"注意", "如果", "好的", "好", "嗯", "行", "可以"}
        if stripped in weak_exact:
            return True
        return len(stripped) <= 4 and not any(ch in stripped for ch in "失败超时错误原因")

    # ================================================================
    # 2026-07-03 工具实现（批9）：hint→(工具名, 参数抽取器) 注册表
    # ================================================================
    # 注入型提示工具名单：get_tool_hint 命中这些工具时，把提示词作为额外 system
    # 消息注入 tool_llm 判断上下文（提高选中率），参数仍由 tool_llm 抽取。
    # 保守起见本批只注册提醒/静默三个新工具；image/search 等旧提示行为不变（不注入）。
    _HINT_INJECT_TOOLS: tuple[str, ...] = (
        "set_reminder", "cancel_reminder", "set_quiet_mode",
    )

    def _match_forced_route(
        self, hint: str, user_input: str,
    ) -> tuple[str, list[dict]]:
        """
        2026-07-03 工具实现（批9）：匹配强制直连路由（通用化批2写死的 recall 逻辑）。

        强制直连注册表：工具名 → 参数抽取器（返回按顺序尝试的候选参数列表，
        前一组结果以「没有找到」开头才尝试下一组兜底参数）。
        保守起见本批只注册 recall_conversation——它的参数抽取失败可退化为
        空关键词查最近记录；set_reminder 的时间参数不宜正则硬抽（抽错=设出
        错误提醒），走注入提示方案（见 _HINT_INJECT_TOOLS）。

        Args:
            hint: MessageRouter.get_tool_hint 的提示文本
            user_input: 用户原始输入（交给参数抽取器）

        Returns:
            (工具名, 候选参数列表)；未命中返回 ("", [])
        """
        forced_routes: dict[str, "Callable[[str], list[dict]]"] = {
            "recall_conversation": self._recall_arg_candidates,
        }
        for name, extractor in forced_routes.items():
            if name in hint:
                return name, extractor(user_input)
        return "", []

    @staticmethod
    def _recall_arg_candidates(text: str) -> list[dict]:
        """
        recall_conversation 的候选参数序列（保持批2原行为）：
        先带抽取的关键词查；查不到再退回空关键词（=最近的跨平台对话记录）。
        """
        keyword = ChatAgent._extract_recall_keyword(text)
        if keyword:
            return [{"keyword": keyword}, {"keyword": ""}]
        return [{"keyword": ""}]

    @staticmethod
    def _extract_recall_keyword(text: str) -> str:
        """
        2026-07-02 审计修复（批2）：从回忆类问句中提取 ConversationLog 检索关键词。

        去掉「还记得/之前聊过」等触发词与常见语气词后，
        取剩余最长片段作为关键词；提取不到（或整句都是噪声）时
        返回空串——空关键词检索会返回最近的跨平台对话记录，仍然可用。

        参数:
            text: 用户原始输入

        返回:
            检索关键词（可能为空串）
        """
        import re as _re
        cleaned = text
        # 触发词与常见语气词（先长后短，避免部分覆盖导致残留）
        for phrase in ("之前聊过", "上次说的", "QQ上说的", "你还记得", "还记得",
                       "你记得", "记得", "聊过", "说过", "上次", "之前",
                       "咱们", "我们", "什么", "来着", "吗", "呢", "吧", "啊"):
            cleaned = cleaned.replace(phrase, " ")
        cleaned = _re.sub(r"[，。！？!?、,.：:\s]+", " ", cleaned).strip()
        # 去掉片段开头的功能字（的/在/是等），过滤空片段
        parts = [p.lstrip("的地得就还都也在是有个把和跟对给被让了") for p in cleaned.split(" ") if p]
        parts = [p for p in parts if p]
        if not parts:
            return ""
        # 取最长片段作为关键词；超过20字视为提取失败（整句噪声），退回空串
        best = max(parts, key=len)
        return best if len(best) <= 20 else ""

    @staticmethod
    def _tag_response(response: str, user_name: str | None,
                      group_id: str, is_group: bool) -> str:
        """
        给AI回复加来源标记（存记忆用，防并发交错串群）。

        发给用户的回复不加标记，只有存入短期记忆时才加。
        调大模型前会用 _strip_memory_tags 去掉标记，大模型看不到。
        """
        name = user_name or "用户"
        if is_group and group_id:
            return f"[回复 群{group_id} {name}] {response}"
        elif name != "用户":
            return f"[回复 {name}] {response}"
        return response

    async def _execute_registered_tool(
        self,
        name: str,
        arguments: dict,
        context: dict | None,
    ) -> str:
        """Call modern registries with policy context and legacy ones unchanged."""
        import inspect

        if self._tools is None:
            raise RuntimeError("Tool registry is not configured")
        execute = self._tools.execute
        parameters = inspect.signature(execute).parameters.values()
        supports_context = any(
            parameter.name == "context"
            or parameter.kind == inspect.Parameter.VAR_KEYWORD
            for parameter in parameters
        )
        if supports_context:
            return await execute(name, arguments, context=context)
        return await execute(name, arguments)

    @staticmethod
    def _strip_memory_tags(messages: list["Message"]) -> list["Message"]:
        """
        去掉记忆中白的回复里的来源标记，防止大模型模仿格式。

        记忆里存的是带标记的（[回复 群号 用户名] 回复内容），
        但给大模型看的时候去掉标记，大模型看到的是干净的回复。
        只处理临时副本，记忆本身不动。
        """
        import re
        result = []
        for m in messages:
            if m.role == MessageRole.ASSISTANT and m.content.startswith("[回复"):
                cleaned = re.sub(r'^\[回复[^\]]*\]\s*', '', m.content)
                result.append(Message(role=m.role, content=cleaned, name=m.name))
            else:
                result.append(m)
        return result

    def reset_conversation(self) -> None:
        """
        重置对话（清空记忆，开始新话题）。
        人格设定不会被清除。
        """
        self._memory.clear()
        logger.info("对话已重置")

    @property
    def conversation_turns(self) -> int:
        """当前对话的轮数。"""
        return self._memory.turn_count

    @property
    def character_name(self) -> str:
        """角色名称。"""
        return self._personality.character_name
