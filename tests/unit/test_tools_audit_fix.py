"""
2026-07-02 审计修复（批2）的单元测试 — 工具系统瘦身与超时修复。

覆盖：
  - registry 按工具超时表的选择逻辑（get_tool_timeout）
  - 瘦身后注册表不再含被下架的空壳/假成功工具，保留的真实工具仍在
  - MessageRouter.get_tool_hint 回忆意图命中/未命中
  - ChatAgent 回忆意图直连 recall_conversation（绕过 tool_llm）、
    未命中走原并行判断、同一工具相同参数不重复执行
"""

from pathlib import Path
from typing import AsyncGenerator

import pytest

from white_salary.adapters.tools.registry import (
    DEFAULT_TOOL_TIMEOUT,
    TOOL_TIMEOUTS,
    ToolRegistry,
    get_tool_timeout,
)
from white_salary.core.agent.chat_agent import ChatAgent
from white_salary.core.interfaces.llm import LLMInterface
from white_salary.core.interfaces.types import Message, ToolCall, ToolResult
from white_salary.core.memory.short_term import ShortTermMemory
from white_salary.core.message.processing import MessageRouter
from white_salary.core.personality.character import PersonalityManager


PROJECT_ROOT = Path(__file__).parent.parent.parent


# ================================================================
# 1. 按工具超时表的选择逻辑
# ================================================================

class TestToolTimeouts:
    """按工具超时表（工具名→秒数）的选择逻辑。"""

    def test_make_video_at_least_360(self) -> None:
        """make_video 云端轮询300秒+多段拼接，超时预算至少360秒。"""
        assert get_tool_timeout("make_video") >= 360

    def test_generate_video_at_least_1100(self) -> None:
        """generate_video 本地分支60秒冷启动+900秒Wan2.2轮询，至少1100秒。"""
        assert get_tool_timeout("generate_video") >= 1100
        assert get_tool_timeout("local_generate_video") >= 1100

    def test_local_lip_sync_at_least_300(self) -> None:
        """local_lip_sync 内部 subprocess timeout=120，外层至少300秒。"""
        assert get_tool_timeout("local_lip_sync") >= 300

    def test_original_slow_tools_keep_120(self) -> None:
        """原慢工具白名单的六个工具维持120秒。"""
        for name in ("watch_video", "deep_search", "research",
                     "generate_image", "draw", "edit_image"):
            assert get_tool_timeout(name) == 120, f"{name} 应为120秒"

    def test_unknown_tool_uses_default_30(self) -> None:
        """表外工具走默认30秒。"""
        assert DEFAULT_TOOL_TIMEOUT == 30
        assert get_tool_timeout("calculator") == 30
        assert get_tool_timeout("完全不存在的工具") == 30

    def test_table_values_all_positive_ints(self) -> None:
        """超时表内全部为正整数秒数。"""
        for name, seconds in TOOL_TIMEOUTS.items():
            assert isinstance(seconds, int) and seconds > 0, f"{name} 超时值非法"


# ================================================================
# 2. 瘦身后的注册表
# ================================================================

# 本批下架的全部工具名（空壳/假成功）
# 2026-07-03 工具实现（批9）：describe_image（真调视觉模型）与 download_video
# （yt_dlp真下载，builtin/download.py）已真实现并重新上架，从下架名单移除
# 2026-07-03 工具实现（批9）：basic.py 提醒三件套（set_reminder/cancel_reminder/
# list_reminders）已接入 ReminderService 真实现并加回 TOOLS，移至 KEPT_TOOLS
DELISTED_TOOLS = [
    # media.py 空壳
    "sing", "music_gen",
    # reasoning.py 两个
    "reasoning", "deep_reasoning",
    # coding.py 15个提示词复读空壳
    "write_code", "explain_code", "fix_code", "optimize_code", "review_code",
    "convert_code", "generate_tests", "generate_docs", "design_algorithm",
    "design_architecture", "design_api", "debug", "sql", "refactor",
    "coding_helper",
    # chat.py 空壳
    "recall_message", "check_unread", "dm_cleanup",
    "view_learned_phrases", "view_learned_slang",
    # video.py 空壳（download_video 已由 builtin/download.py 真实现，见上方注释）
    "send_file", "local_generate_sfx",
    "local_full_video_pipeline", "local_generate_voice",
    # social.py 假成功
    "set_busy_mode", "clear_busy_mode", "global_silent", "switch_filter_mode",
    "check_filter_mode", "filter_toggle", "silent_toggle",
]

# 各分类文件中必须保留的代表性真实工具（防止误删/文件加载失败导致断言空过）
KEPT_TOOLS = [
    "get_current_time", "calculator", "dice_roller",          # basic.py
    # 2026-07-03 工具实现（批9）：提醒三件套真实现后重新上架
    "set_reminder", "cancel_reminder", "list_reminders",      # basic.py 批9
    "generate_image", "draw", "generate_sticker", "screenshot",  # media.py
    "regex", "execute_code",                                   # coding.py
    "view_chat_history", "group_history", "reply_to_user",
    "message_send", "push_to_desktop", "ntfy_push",            # chat.py
    "get_video_info", "make_video", "generate_video",
    "local_generate_video", "local_lip_sync",                  # video.py
    "block_user", "unblock_user", "check_blocked_users",
    "manage_blacklist",                                        # social.py
    "recall_conversation",                                     # memory_tools.py
    "qq_recall_last",                                          # qq_api.py 真实撤回
]


class TestRegistrySlimming:
    """瘦身后注册表内容校验。"""

    def _registry_names(self) -> set:
        return {t.name for t in ToolRegistry().get_all()}

    def test_delisted_tools_not_registered(self) -> None:
        """被下架的空壳/假成功工具不再出现在注册表。"""
        names = self._registry_names()
        still_there = [n for n in DELISTED_TOOLS if n in names]
        assert not still_there, f"以下工具应已下架但仍在注册表: {still_there}"

    def test_kept_tools_still_registered(self) -> None:
        """保留的真实工具仍然在注册表（防止误删和分类文件加载失败）。"""
        names = self._registry_names()
        missing = [n for n in KEPT_TOOLS if n not in names]
        assert not missing, f"以下真实工具意外丢失: {missing}"

    def test_registry_under_deepseek_limit(self) -> None:
        """瘦身后工具总数低于 DeepSeek 的128个上限。"""
        assert ToolRegistry().count < 128


class TestSocialBlacklistTools:
    """社交黑名单工具应操作 QQ 运行中的 UserFilter。"""

    @pytest.mark.asyncio
    async def test_block_tools_use_runtime_user_filter(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import white_salary.infrastructure.server.settings_api as settings_api
        from white_salary.adapters.tools.builtin import social
        from white_salary.core.memory.user_filter import FilterResult, UserFilter

        monkeypatch.setattr(settings_api, "_runtime_registry", {})
        runtime_filter = UserFilter(data_dir=str(tmp_path))
        settings_api.register_runtime_instance("user_filter", runtime_filter)

        assert "已屏蔽" in await social.block_user("unit-tool-user", "测试")
        assert runtime_filter.check("unit-tool-user") == FilterResult.BLOCK
        assert "unit-tool-user" in await social.check_blocked_users()

        assert "已移出" in await social.manage_blacklist("remove", "unit-tool-user")
        assert runtime_filter.check("unit-tool-user") == FilterResult.ALLOW


# ================================================================
# 3. get_tool_hint 命中/未命中
# ================================================================

class TestRecallToolHint:
    """MessageRouter.get_tool_hint 回忆类意图路由。"""

    def test_recall_keywords_hit(self) -> None:
        """「回忆/记得/之前聊过」类问句应提示 recall_conversation。"""
        router = MessageRouter()
        for text in ("还记得我们之前说的旅行吗", "之前聊过的那个电影",
                     "上次说的事情怎么样了", "QQ上说的那件事"):
            assert "recall_conversation" in router.get_tool_hint(text), text

    def test_normal_chat_no_hint(self) -> None:
        """普通聊天不产生回忆提示。"""
        assert MessageRouter().get_tool_hint("今天天气真好啊") == ""

    def test_extract_recall_keyword(self) -> None:
        """回忆问句能提取出有意义的关键词；纯触发词返回空串。"""
        kw = ChatAgent._extract_recall_keyword("还记得我们之前聊过的周末计划吗")
        assert kw == "周末计划"
        # 只有触发词时提取为空（空关键词=检索最近记录，仍可用）
        assert ChatAgent._extract_recall_keyword("还记得吗") == ""


# ================================================================
# 4. ChatAgent 回忆直连 / 未命中走原逻辑 / 防重复执行
# ================================================================

class FakeToolLLM(LLMInterface):
    """工具判断LLM桩：记录是否被调用，返回预设的工具调用列表。"""

    def __init__(self, tool_calls: list[ToolCall] | None = None) -> None:
        self.chat_with_tools_called = 0
        self._tool_calls = tool_calls or []

    async def chat_completion(self, messages: list[Message],
                              temperature: float = 0.7,
                              max_tokens: int = 2048) -> str:
        return ""

    async def chat_completion_stream(
        self, messages: list[Message],
        temperature: float = 0.7, max_tokens: int = 2048,
    ) -> AsyncGenerator[str, None]:
        yield ""

    async def chat_with_tools(
        self, messages: list[Message], tools: list[dict],
        temperature: float = 0.7, max_tokens: int = 2048,
    ) -> tuple[str, list[ToolCall]]:
        self.chat_with_tools_called += 1
        return "", self._tool_calls

    async def process_tool_results(
        self, messages: list[Message], tool_results: list[ToolResult],
        temperature: float = 0.7, max_tokens: int = 2048,
    ) -> str:
        return ""


class FakeMainLLM(LLMInterface):
    """主模型桩：流式返回固定文本；process_tool_results 拼接工具结果。"""

    def __init__(self, stream_response: str = "普通流式回复") -> None:
        self._stream_response = stream_response
        self.tool_results_seen: list[list[ToolResult]] = []

    async def chat_completion(self, messages: list[Message],
                              temperature: float = 0.7,
                              max_tokens: int = 2048) -> str:
        return self._stream_response

    async def chat_completion_stream(
        self, messages: list[Message],
        temperature: float = 0.7, max_tokens: int = 2048,
    ) -> AsyncGenerator[str, None]:
        for ch in self._stream_response:
            yield ch

    async def chat_with_tools(
        self, messages: list[Message], tools: list[dict],
        temperature: float = 0.7, max_tokens: int = 2048,
    ) -> tuple[str, list[ToolCall]]:
        return self._stream_response, []

    async def process_tool_results(
        self, messages: list[Message], tool_results: list[ToolResult],
        temperature: float = 0.7, max_tokens: int = 2048,
    ) -> str:
        self.tool_results_seen.append(list(tool_results))
        merged = "；".join(r.content for r in tool_results)
        return f"基于工具结果的回复：{merged}"


class FakeRegistry:
    """工具注册表桩：记录 execute 调用，模拟 recall_conversation。"""

    def __init__(self) -> None:
        self.execute_calls: list[tuple[str, dict]] = []

    @property
    def count(self) -> int:
        return 2

    def get_tool(self, name: str):
        # 只声明 recall_conversation 和 get_current_time 存在
        if name in ("recall_conversation", "get_current_time"):
            return object()
        return None

    def get_openai_tools(self) -> list[dict]:
        return [{"type": "function",
                 "function": {"name": "recall_conversation",
                              "description": "回忆", "parameters": {}}}]

    async def execute(self, name: str, arguments: dict) -> str:
        self.execute_calls.append((name, dict(arguments)))
        if name == "recall_conversation":
            return "找到 1 条相关对话记录：[QQ] 主人: 周末去爬山"
        return "执行完成"


def _make_agent(tool_llm: LLMInterface, registry: FakeRegistry,
                main_response: str = "普通流式回复") -> tuple[ChatAgent, FakeMainLLM]:
    main_llm = FakeMainLLM(stream_response=main_response)
    agent = ChatAgent(
        llm=main_llm,
        personality=PersonalityManager(project_root=PROJECT_ROOT),
        memory=ShortTermMemory(max_turns=20),
        tool_registry=registry,  # type: ignore[arg-type]
        tool_llm=tool_llm,
    )
    return agent, main_llm


class TestForcedRecallRouting:
    """回忆意图直连 recall_conversation 的路由行为。"""

    async def test_recall_intent_forces_tool_and_bypasses_tool_llm(self) -> None:
        """命中回忆意图：强制执行 recall_conversation，且不调用 tool_llm。"""
        registry = FakeRegistry()
        tool_llm = FakeToolLLM()
        agent, main_llm = _make_agent(tool_llm, registry)

        chunks = []
        async for chunk in agent.chat_stream_with_tools("还记得我们之前聊过的周末计划吗"):
            chunks.append(chunk)
        reply = "".join(chunks)

        # 强制执行了 recall_conversation（且只执行一次相同调用）
        recall_calls = [c for c in registry.execute_calls if c[0] == "recall_conversation"]
        assert len(recall_calls) == 1
        assert recall_calls[0][1].get("keyword") == "周末计划"
        # 绕过了 tool_llm 判断
        assert tool_llm.chat_with_tools_called == 0
        # 回忆结果并入了上下文（走 process_tool_results 生成最终回复）
        assert len(main_llm.tool_results_seen) == 1
        assert "周末去爬山" in reply

    async def test_recall_keyword_fallback_to_recent(self) -> None:
        """带关键词没查到时，退回空关键词检索最近记录。"""

        class NoHitRegistry(FakeRegistry):
            async def execute(self, name: str, arguments: dict) -> str:
                self.execute_calls.append((name, dict(arguments)))
                if arguments.get("keyword"):
                    return "没有找到相关的对话记录。"
                return "找到 2 条相关对话记录"

        registry = NoHitRegistry()
        agent, main_llm = _make_agent(FakeToolLLM(), registry)

        async for _ in agent.chat_stream_with_tools("还记得我们之前聊过的周末计划吗"):
            pass

        # 第一次带关键词，第二次退回空关键词
        assert len(registry.execute_calls) == 2
        assert registry.execute_calls[0][1].get("keyword") == "周末计划"
        assert registry.execute_calls[1][1].get("keyword") == ""

    async def test_no_recall_intent_uses_parallel_judge(self) -> None:
        """未命中回忆意图：走原并行判断逻辑（tool_llm被调用，无强制执行）。"""
        registry = FakeRegistry()
        tool_llm = FakeToolLLM(tool_calls=[])  # 判断结果：不需要工具
        agent, main_llm = _make_agent(tool_llm, registry, main_response="今天很开心")

        chunks = []
        async for chunk in agent.chat_stream_with_tools("今天天气真好啊"):
            chunks.append(chunk)

        assert tool_llm.chat_with_tools_called == 1
        assert registry.execute_calls == []  # 没有强制执行任何工具
        assert "".join(chunks) == "今天很开心"

    async def test_duplicate_tool_calls_executed_once(self) -> None:
        """tool_llm 返回同一工具+相同参数两次时，只执行一次。"""
        registry = FakeRegistry()
        dup_calls = [
            ToolCall(id="c1", name="get_current_time", arguments="{}"),
            ToolCall(id="c2", name="get_current_time", arguments="{}"),
        ]
        tool_llm = FakeToolLLM(tool_calls=dup_calls)
        agent, main_llm = _make_agent(tool_llm, registry)

        async for _ in agent.chat_stream_with_tools("现在几点了"):
            pass

        time_calls = [c for c in registry.execute_calls if c[0] == "get_current_time"]
        assert len(time_calls) == 1, "同一工具相同参数应只执行一次"
