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
from typing import AsyncGenerator, Callable

from loguru import logger

from white_salary.core.interfaces.llm import LLMInterface
from white_salary.core.interfaces.types import Message, MessageRole, ToolCall, ToolResult
from white_salary.core.memory.short_term import ShortTermMemory
from white_salary.core.memory.manager import MemoryManager
from white_salary.core.filter.content_filter import ContentFilter
from white_salary.core.personality.character import PersonalityManager
# 2026-07-02 审计修复（批2）：接通 MessageRouter.get_tool_hint（原为死代码）
from white_salary.core.message.processing import MessageRouter


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
            current_message=user_input, user_id=user_id, is_group=is_group
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
            current_message=user_input, user_id=user_id, is_group=is_group
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
                             is_group: bool = False) -> Message:
        """构建系统消息（人格提示词 + 核心记忆 + 相关长期记忆 + 好感度提示）。"""
        base_msg = self._personality.get_system_message()
        extra_parts = []

        # 注入核心记忆 + 与当前话题相关的长期记忆 + 所有模块上下文
        if self._memory_manager:
            memory_ctx = self._memory_manager.get_context_injection(
                current_message=current_message,
                user_id=user_id,
                is_group=is_group,
            )
            if memory_ctx:
                extra_parts.append(memory_ctx)

        # 注入好感度提示（按用户动态获取真实好感度，不用全局实例）
        try:
            from white_salary.core.affinity.manager import AffinityManager as _AffMgr
            _user_aff = _AffMgr.get_for_user(user_id)
            extra_parts.append(_user_aff.get_context_hint())
        except Exception:
            pass

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
    ) -> AsyncGenerator[str, None]:
        """
        并行双模型对话（借鉴v2架构）。

        核心：主模型和tool_llm同时启动，不互相等待。

        流程：
          1. 用户消息存入记忆，组装上下文
          2. 同时启动两个任务：
             - Task A: tool_llm判断是否需要工具（快）
             - Task B: 主模型直接开始流式回复（不等工具判断）
          3. Task A先返回：
             - 没有工具 → Task B继续输出（0延迟，跟没有工具一样快）
             - 有工具 → 取消Task B → 执行工具 → 工具结果喂给主模型重新生成
          4. 主模型永远不看工具定义（省token），只在有工具结果时收到

        没有tool_llm或没有工具时退化为普通chat_stream。
        """
        import asyncio
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

        logger.debug(f"收到用户输入(并行模式): {user_input[:50]}...")
        route_source = route_text if route_text is not None else user_input

        # 1. 用户消息存入记忆
        self._memory.add_user_message(user_input, name=user_name)

        # 2. 组装上下文（主模型用的，不带工具定义）
        system_msg = self._build_system_message(
            current_message=user_input, user_id=user_id, is_group=is_group
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
                result_text = ""
                for candidate_args in arg_candidates:
                    result_text = await self._tools.execute(forced_name, candidate_args)
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

        # 3. 并行启动：tool_llm判断 + 主模型流式回复
        tool_result_holder = {"calls": None, "text": "", "error": None}

        async def _tool_judge():
            """Task A: tool_llm判断是否需要工具。"""
            try:
                if tool_context is None:
                    tools_payload = self._tools.get_openai_tools()
                else:
                    try:
                        tools_payload = self._tools.get_openai_tools(context=tool_context)
                    except TypeError:
                        tools_payload = self._tools.get_openai_tools()
                if not tools_payload:
                    tool_result_holder["calls"] = []
                    tool_result_holder["text"] = ""
                    return
                # 2026-07-03 工具实现（批9）：判断上下文用 judge_context——
                # 命中提醒/静默类意图时已追加提示词（未命中时与 context 同一对象）
                text, calls = await self._tool_llm.chat_with_tools(
                    messages=judge_context,
                    tools=tools_payload,
                    temperature=0.3,  # 工具判断用低temperature，更准确
                    max_tokens=1024,  # 工具判断不需要太多token
                )
                tool_result_holder["calls"] = calls
                tool_result_holder["text"] = text or ""
            except Exception as e:
                tool_result_holder["error"] = e
                logger.debug(f"工具判断失败（正常降级）: {e}")

        # 启动工具判断任务（后台跑）
        # 2026-07-02 审计修复（批2）：已强制执行回忆工具时绕过 tool_llm 判断
        tool_task: "asyncio.Task | None" = None
        if not forced_results:
            tool_task = asyncio.create_task(_tool_judge())

        # 4. 同时启动主模型流式回复
        main_chunks: list[str] = []
        main_cancelled = False

        try:
            # 强制执行的工具结果（回忆直连）先并入
            tool_results: list[ToolResult] = list(forced_results)
            silent_side_effect_done = False

            if tool_task is not None:
                # 先等工具判断完成（通常很快，DeepSeek 1-2秒）
                # 但设置超时，如果tool_llm太慢就不等了
                try:
                    # 2026-07-02 审计修复（批2）：8.0→15.0秒——工具瘦身后payload变小，
                    # 15秒足够完成判断，减少「判断超时被跳过」（审计日志一天最多35次）
                    await asyncio.wait_for(tool_task, timeout=15.0)
                except asyncio.TimeoutError:
                    logger.warning("[Agent] 工具判断超时(15s)，跳过工具")
                    tool_result_holder["calls"] = None

                # 5. 检查工具判断结果
                tool_calls = tool_result_holder["calls"]

                if tool_calls:
                    # ====== 有工具调用：执行工具 → 主模型重新生成 ======
                    logger.info(f"[Agent] 小助理判断需要 {len(tool_calls)} 个工具")

                    for tc in tool_calls:
                        try:
                            args = _json.loads(tc.arguments) if isinstance(tc.arguments, str) else (tc.arguments or {})
                            # 2026-07-02 审计修复（批2）：防止同一工具+相同参数重复执行
                            call_key = (tc.name, _json.dumps(args, sort_keys=True, ensure_ascii=False))
                            if call_key in executed_tool_keys:
                                logger.debug(f"[Agent] 工具 {tc.name} 相同调用已执行过，跳过重复执行")
                                continue
                            executed_tool_keys.add(call_key)
                            result = await self._tools.execute(tc.name, args)
                            if tc.name == "qq_send_voice" and str(result).strip().startswith("语音已发送"):
                                silent_side_effect_done = True
                        except Exception as tool_err:
                            result = f"工具{tc.name}执行失败: {tool_err}"
                            logger.warning(f"工具执行失败 {tc.name}: {tool_err}")
                        tool_results.append(ToolResult(call_id=tc.id, content=result))
                        logger.debug(f"工具 {tc.name}: {result[:80]}")

            if tool_results:
                if silent_side_effect_done and len(tool_results) == 1:
                    yield "__WHITE_SALARY_TOOL_SILENT__"
                    full_response = ""
                    return
                # 把工具结果喂给主模型（process_tool_results内部会加提示）
                try:
                    final_reply = await self._llm.process_tool_results(
                        messages=context,
                        tool_results=tool_results,
                        temperature=self._temperature,
                        max_tokens=self._max_tokens,
                    )
                    final_reply = (final_reply or "").strip()
                    if not final_reply:
                        tool_summary = "; ".join(
                            r.content.strip()[:200]
                            for r in tool_results
                            if r.content and r.content.strip()
                        )
                        final_reply = tool_summary or "操作完成了，但没有拿到可用结果。"
                        logger.warning("[Agent] 工具结果后处理返回空回复，已降级为工具结果摘要")
                    parts = re.split(r'(?<=[。！？!?\n])', final_reply)
                    for part in parts:
                        if part:
                            yield part
                    full_response = final_reply
                except Exception as e:
                    # process_tool_results失败时，降级为直接用工具结果回复
                    logger.warning(f"工具结果处理失败，降级回复: {e}")
                    tool_summary = "; ".join(r.content[:50] for r in tool_results)
                    fallback = f"操作完成了，结果是：{tool_summary}"
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

        except Exception as e:
            logger.warning(f"并行对话失败，降级为普通流式: {e}")
            # 降级：普通流式（不带工具）
            async for chunk in self._llm.chat_completion_stream(
                messages=context,
                temperature=self._temperature,
                max_tokens=self._max_tokens,
            ):
                main_chunks.append(chunk)
                yield chunk
            full_response = "".join(main_chunks)

        # 确保工具任务结束
        # 2026-07-02 审计修复（批2）：回忆直连时未启动判断任务，tool_task 可能为 None
        if tool_task is not None and not tool_task.done():
            tool_task.cancel()
            try:
                await tool_task
            except (asyncio.CancelledError, Exception):
                pass

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
