"""
核心链路集成测试（批8，KEY=integration）。

2026-07-03 外部依赖优化（批8）：单测覆盖不到跨模块链路——历史上出现过
"325→468个单测全绿但真实功能坏过"（记忆存了读不回、桌面/QQ两套身份、
跨平台回忆断链、桌面端好感度从不涨、WebM 语音硬标 wav 全 500 等）。
本文件用 mock LLM/TTS/网络、tmp_path 隔离数据目录，真实跑通每一条
"曾经坏过"的链路，作为回归护栏。

不发真实请求、不起服务器、不写真实用户数据。

各测试覆盖的"曾经坏过"链路：
  1. 记忆往返        —— 桌面写入的记忆读不回（extract_and_store → get_context_injection）
  2. 跨平台身份统一  —— 桌面端硬编码 desktop、与 QQ 主人是两套账
                       （_resolve_owner_id / is_owner_user / 好感度按统一 user_id 累积）
  3. 跨平台回忆通道  —— QQ 说过的话桌面端回忆不到（ConversationLog + recall_conversation）
  4. 好感度累积      —— 桌面端 process_message 路径好感度从不涨（批2/4回归护栏）
  5. 音频格式探测+降级 —— WebM/Opus 硬标 wav 上传 ASR 全 500；ffmpeg 缺失时不降级会崩
  6. 三agent共享长期记忆隔离短期 —— 桌面/QQ/QQ空间共享人设+长期记忆但对话记忆互不污染
"""

import asyncio
from pathlib import Path
from typing import AsyncGenerator, Optional

import pytest

from white_salary.core.interfaces.llm import LLMInterface
from white_salary.core.interfaces.types import Message, ToolCall, ToolResult


# ===================================================================
# 公共 fixture / 假对象
# ===================================================================


@pytest.fixture(autouse=True)
def _isolate_shared_state():
    """
    2026-07-03 外部依赖优化（批8）：隔离所有进程级共享注册表与单例。

    批5 给 MemoryManager / CoreMemoryStore / LongTermMemoryStore / AffinityManager
    引入了按 data_dir 归一化路径缓存的进程级共享实例；AffinityManager 还有一份
    按 user_id（而非 data_dir）缓存的 _multi_user_cache，ConversationLog 是经典
    单例。tmp_path 各测试唯一，但共享注册表本身跨测试存活——尤其 _multi_user_cache
    只按 user_id 缓存，同一 user_id 会拿到别的测试首次用的 data_dir。
    每个测试前后都清空，确保互不串味、也不污染真实 data 目录。
    """
    from white_salary.core.affinity.manager import AffinityManager
    from white_salary.core.memory.core_store import CoreMemoryStore
    from white_salary.core.memory.long_term_store import LongTermMemoryStore
    from white_salary.core.memory.manager import MemoryManager
    from white_salary.core.memory.conversation_log import ConversationLog
    import white_salary.core.memory.manager as mm_mod

    def _clear() -> None:
        AffinityManager._shared_instances.clear()
        AffinityManager._multi_user_cache.clear()
        CoreMemoryStore._shared_instances.clear()
        LongTermMemoryStore._shared_instances.clear()
        MemoryManager._shared_instances.clear()
        ConversationLog._instance = None
        # 清主人 user_id 注入与缓存（批4闸门用）
        mm_mod.set_owner_user_id(None)

    _clear()
    yield
    _clear()


@pytest.fixture(autouse=True)
def _keyword_only_long_term():
    """
    2026-07-03 外部依赖优化（批8）：强制长期记忆走关键词检索，不碰 ChromaDB。

    本机装了 chromadb（1.5.0），MemoryManager 默认会为每个 tmp data_dir 现开一个
    PersistentClient（慢且引入向量检索的非确定性）。批6 提供的
    set_default_long_term_provider('none') 让 LongTermMemoryStore 跳过 Chroma、
    只用 SQLite 关键词检索——集成测试要的是链路确定性，故全程强制 'none'。
    """
    from white_salary.core.memory import long_term_store as lts_mod

    original = lts_mod._DEFAULT_PROVIDER
    lts_mod.set_default_long_term_provider("none")
    yield
    lts_mod.set_default_long_term_provider(original)


class _ScriptedLLM(LLMInterface):
    """
    可编排的假 LLM：chat_completion 按调用序返回预设文本（用完循环最后一条），
    流式逐字吐、工具判断永不触发工具。记录收到的 messages 供断言。

    用于 memory_llm（提取器）、emotion_llm、主对话 LLM 三种角色。
    """

    def __init__(self, responses: Optional[list[str]] = None) -> None:
        self._responses = responses or [""]
        self._idx = 0
        self.seen_messages: list[list[Message]] = []

    def _next(self) -> str:
        if self._idx < len(self._responses):
            resp = self._responses[self._idx]
        else:
            resp = self._responses[-1]
        self._idx += 1
        return resp

    async def chat_completion(
        self, messages: list[Message], temperature: float = 0.7, max_tokens: int = 2048,
    ) -> str:
        self.seen_messages.append(list(messages))
        return self._next()

    async def chat_completion_stream(
        self, messages: list[Message], temperature: float = 0.7, max_tokens: int = 2048,
    ) -> AsyncGenerator[str, None]:
        self.seen_messages.append(list(messages))
        text = self._next()
        for ch in text:
            yield ch

    async def chat_with_tools(
        self, messages: list[Message], tools: list[dict],
        temperature: float = 0.7, max_tokens: int = 2048,
    ) -> tuple[str, list[ToolCall]]:
        return "", []

    async def process_tool_results(
        self, messages: list[Message], tool_results: list[ToolResult],
        temperature: float = 0.7, max_tokens: int = 2048,
    ) -> str:
        return self._next()


def _make_memory_manager(data_dir: Path, memory_llm=None, emotion_llm=None):
    """
    构造一个跳过 48 个扩展模块自动发现的轻量 MemoryManager（参考 test_batch4）。

    扩展模块单独测；集成测试只关心核心四层记忆链路，跳过可大幅提速并去噪。
    同时把批5 的模块落盘后台任务（_ensure_flush_task）打成 no-op：它只做每 5
    分钟的周期性落盘维护，与本链路无关；不禁用会在测试事件循环关闭时留下
    "Task was destroyed but it is pending" 的悬挂任务告警。
    """
    from white_salary.core.memory.manager import MemoryManager

    orig_discover = MemoryManager._discover_modules
    orig_flush = MemoryManager._ensure_flush_task
    MemoryManager._discover_modules = lambda self, data_dir: None
    # 类级 no-op 覆盖构造期的落盘任务启动（async 测试构造时已在运行循环内）
    MemoryManager._ensure_flush_task = lambda self: None
    try:
        mgr = MemoryManager(
            data_dir=str(data_dir), memory_llm=memory_llm, emotion_llm=emotion_llm,
        )
    finally:
        MemoryManager._discover_modules = orig_discover
        MemoryManager._ensure_flush_task = orig_flush
    # 实例级再打一次，覆盖类级还原后 extract_and_store 里的 _ensure_flush_task 调用
    mgr._ensure_flush_task = lambda: None  # type: ignore[method-assign]
    return mgr


# ===================================================================
# 子项 1：记忆往返 —— 桌面写入的记忆能被读回
#
# 曾经坏过：记忆存进四层 store 后，get_context_injection 拿不回来
# （核心档案/长期记忆的写入与读出链路各写各的，跨模块没接上）。
# ===================================================================


class TestMemoryRoundTrip:
    async def test_core_and_long_term_survive_roundtrip(self, tmp_path):
        """
        一轮对话 extract_and_store 后，核心档案（正则命中"我叫X"）与
        关键词触发的长期记忆都能被 get_context_injection 读回。
        """
        from white_salary.core.memory.manager import set_owner_user_id

        # 主人身份注入：核心档案白名单闸门只放行主人消息
        set_owner_user_id("desktop")

        # memory_llm 返回空数组（不额外提取），聚焦验证正则+关键词零成本路径的往返
        memory_llm = _ScriptedLLM(["[]"])
        mgr = _make_memory_manager(tmp_path, memory_llm=memory_llm)

        extracted = await mgr.extract_and_store(
            user_message="我叫小白，记住我最喜欢水果蛋糕",
            ai_reply="好的我记住啦",
            user_id="desktop",
        )
        # 提取动作确实发生（核心名字 + 关键词"记住"触发长期）
        assert any("user_name" in e for e in extracted), extracted
        assert any("记住" in e or "长期" in e for e in extracted), extracted

        # 核心档案：名字必须落进 core store 且能读回
        assert mgr.core.get("user_name") == "小白"

        # get_context_injection 里应含刚写入的核心名字（DynamicRenderer basic_info 注入）
        ctx = mgr.get_context_injection(current_message="随便聊聊", user_id="desktop")
        assert "小白" in ctx, f"核心记忆没被读回:\n{ctx}"

        # 关键词触发的长期记忆能被检索回（关键词 token 命中 content）
        lt_hits = mgr.long_term.search("水果蛋糕", limit=5)
        assert any("水果蛋糕" in e.content for e in lt_hits), \
            f"长期记忆没被读回: {[e.content for e in lt_hits]}"

    async def test_second_manager_same_dir_reads_prior_writes(self, tmp_path):
        """
        持久化往返：同一 data_dir 新开一个 MemoryManager（清掉共享实例缓存后），
        能读到上一个实例写盘的核心记忆——证明记忆真的落盘、不是只在内存。
        """
        from white_salary.core.memory.manager import MemoryManager, set_owner_user_id

        set_owner_user_id("desktop")
        memory_llm = _ScriptedLLM(["[]"])
        mgr1 = _make_memory_manager(tmp_path, memory_llm=memory_llm)
        await mgr1.extract_and_store(
            user_message="我叫小白", ai_reply="嗯", user_id="desktop",
        )
        assert mgr1.core.get("user_name") == "小白"

        # 清共享注册表，强制第二个实例从磁盘重新加载
        MemoryManager._shared_instances.clear()
        from white_salary.core.memory.core_store import CoreMemoryStore
        from white_salary.core.memory.long_term_store import LongTermMemoryStore
        CoreMemoryStore._shared_instances.clear()
        LongTermMemoryStore._shared_instances.clear()

        mgr2 = _make_memory_manager(tmp_path, memory_llm=_ScriptedLLM(["[]"]))
        assert mgr2.core.get("user_name") == "小白", "换实例后核心记忆丢了（没落盘）"


# ===================================================================
# 子项 2：跨平台身份统一
#
# 曾经坏过：桌面端硬编码 user_id="desktop"，与 QQ 端主人 QQ 号是两套账，
# 在 QQ 积累的好感度桌面端完全无感。
# ===================================================================


class TestCrossPlatformIdentity:
    def test_desktop_and_qq_resolve_same_owner_id(self, tmp_path):
        """
        _resolve_owner_id（桌面端）与 is_owner_user（记忆闸门）对同一 conf 解析
        到同一主人 user_id = qq.family_qq[0]，两端不再是两套身份。
        """
        from white_salary.infrastructure.server.websocket_handler import _resolve_owner_id
        from white_salary.core.memory import manager as mm_mod

        conf = tmp_path / "conf.yaml"
        conf.write_text("qq:\n  family_qq:\n    - 1234567890\n", encoding="utf-8")

        # 桌面端解析
        owner_id = _resolve_owner_id(conf_path=conf)
        assert owner_id == "1234567890"

        # 记忆闸门口径：注入同一号后，该号与桌面历史身份都被视为主人
        mm_mod.set_owner_user_id(owner_id)
        assert mm_mod.get_owner_user_id() == "1234567890"
        assert mm_mod.is_owner_user("1234567890") is True
        assert mm_mod.is_owner_user("desktop") is True   # 桌面历史身份始终是主人
        assert mm_mod.is_owner_user("99999999") is False  # 陌生 QQ 用户不是主人

    def test_affinity_accumulates_to_same_profile_across_entries(self, tmp_path):
        """
        桌面端与 QQ 端用同一主人 user_id 时，好感度累积到同一份档案：
        模拟"先在 QQ 涨分、桌面端立刻继承"——两个入口拿到的是同一 manager 实例，
        分数连续叠加（不是各涨各的）。
        """
        from white_salary.core.affinity.manager import AffinityManager

        aff_dir = str(tmp_path / "affinity")
        owner_id = "1234567890"

        # QQ 入口：process_interaction + 一条夸奖消息
        qq_aff = AffinityManager.get_for_user(owner_id, data_dir=aff_dir)
        qq_aff.process_interaction()
        qq_aff.process_message("你好厉害")  # compliment +2
        points_after_qq = qq_aff.get_stats()["points"]
        assert points_after_qq > 0

        # 桌面入口：同一 user_id + 同一 data_dir → 应是同一实例，读到 QQ 的分
        desktop_aff = AffinityManager.get_for_user(owner_id, data_dir=aff_dir)
        assert desktop_aff is qq_aff, "同一主人身份在两个入口拿到了不同实例（身份没统一）"
        assert desktop_aff.get_stats()["points"] == points_after_qq

        # 桌面端再涨分，累积到同一档案
        desktop_aff.process_message("你好可爱")  # compliment 再 +
        assert desktop_aff.get_stats()["points"] > points_after_qq

        # 陌生 QQ 用户是独立档案，不沾主人的分
        stranger = AffinityManager.get_for_user("99999999", data_dir=aff_dir)
        assert stranger is not desktop_aff
        assert stranger.get_stats()["points"] != desktop_aff.get_stats()["points"]


# ===================================================================
# 子项 3：跨平台回忆通道
#
# 曾经坏过：QQ 上说过的话，桌面端（或反过来）用 recall_conversation 检索不到——
# 两端写入各自的日志、检索没打通。
# ===================================================================


class TestCrossPlatformRecall:
    async def test_recall_tool_finds_both_platforms(self, tmp_path, monkeypatch):
        """
        ConversationLog 两端（desktop/qq）写入后，recall_conversation 工具函数
        能跨平台检索到对端内容。直接调工具 handler（不起服务器）验证返回文本
        同时含桌面与 QQ 两侧的对话。
        """
        from white_salary.core.memory.conversation_log import ConversationLog
        from white_salary.adapters.tools.builtin import memory_tools

        # 共享 conv_log（tmp 隔离），并把工具用的单例指向它
        conv_log = ConversationLog(data_dir=str(tmp_path))
        monkeypatch.setattr(
            ConversationLog, "get_instance", classmethod(lambda cls, data_dir="data/memory": conv_log),
        )

        # 桌面端写一条 + QQ 端写一条，都提到"生日蛋糕"
        conv_log.record(
            platform="desktop", user_name="小白", user_id="1234567890",
            group_id="", user_msg="我想订个生日蛋糕", ai_reply="好呀帮你看看",
        )
        conv_log.record(
            platform="qq", user_name="小白", user_id="1234567890",
            group_id="123456", user_msg="QQ 上再问下生日蛋糕的事", ai_reply="记得的",
        )

        # 直接调 recall_conversation 工具函数（recall 工具是打通跨平台回忆的落点）
        result = await memory_tools.recall_conversation(keyword="生日蛋糕")

        assert "桌面" in result and ("QQ" in result), \
            f"跨平台回忆没同时命中两端:\n{result}"
        assert "生日蛋糕" in result
        # 两条都在（对端内容可被检索到）
        assert result.count("生日蛋糕") >= 2, result

    async def test_recall_platform_filter_isolates_side(self, tmp_path, monkeypatch):
        """
        带 platform 过滤时只返回该端记录——证明检索确实按平台维度切片，
        不是把所有记录一股脑倒出来（跨平台检索是"能查到对端"，不是"分不清端"）。
        """
        from white_salary.core.memory.conversation_log import ConversationLog
        from white_salary.adapters.tools.builtin import memory_tools

        conv_log = ConversationLog(data_dir=str(tmp_path))
        monkeypatch.setattr(
            ConversationLog, "get_instance", classmethod(lambda cls, data_dir="data/memory": conv_log),
        )
        conv_log.record("desktop", "小白", "u1", "", "桌面端专属话题苹果", "嗯")
        conv_log.record("qq", "小白", "u1", "g1", "QQ端专属话题香蕉", "好")

        only_qq = await memory_tools.recall_conversation(keyword="", platform="qq")
        assert "香蕉" in only_qq
        assert "苹果" not in only_qq

    def test_recent_by_user_filters_same_identity_only(self, tmp_path):
        """最近跨平台对话必须按 user_id 过滤，不能把其他QQ用户串进来。"""
        from white_salary.core.memory.conversation_log import ConversationLog

        conv_log = ConversationLog(data_dir=str(tmp_path))
        conv_log.record("desktop", "小白", "owner", "", "桌面聊过芝士蛋糕", "记得")
        conv_log.record("qq", "小白", "owner", "g1", "QQ聊过草莓蛋糕", "记得")
        conv_log.record("qq", "路人", "stranger", "g1", "路人聊过榴莲蛋糕", "嗯")

        entries = conv_log.get_recent_by_user("owner", limit=10)
        text = "\n".join(e.user_msg for e in entries)

        assert "芝士蛋糕" in text
        assert "草莓蛋糕" in text
        assert "榴莲蛋糕" not in text

    def test_memory_context_auto_injects_recent_cross_platform_log(self, tmp_path, monkeypatch):
        """
        普通对话也应自然带入同一 user_id 的最近 QQ/桌面对话，而不是只能靠
        用户显式说“你还记得吗”触发 recall_conversation。
        """
        from white_salary.core.memory.conversation_log import ConversationLog
        from white_salary.core.memory.manager import set_owner_user_id

        set_owner_user_id("owner")

        conv_log = ConversationLog(data_dir=str(tmp_path / "conv"))
        monkeypatch.setattr(
            ConversationLog,
            "get_instance",
            classmethod(lambda cls, data_dir="data/memory": conv_log),
        )
        conv_log.record("qq", "小白", "owner", "216", "QQ里说我要买蓝莓蛋糕", "好")
        conv_log.record("desktop", "小白", "owner", "", "桌面说蛋糕要少糖", "记下了")
        conv_log.record("qq", "路人", "stranger", "216", "路人说榴莲蛋糕", "嗯")

        mgr = _make_memory_manager(tmp_path / "memory")
        ctx = mgr.get_context_injection("继续刚才那个蛋糕", user_id="owner")

        assert "[最近跨平台对话上下文]" in ctx
        assert "蓝莓蛋糕" in ctx
        assert "少糖" in ctx
        assert "榴莲蛋糕" not in ctx

    def test_group_memory_only_uses_same_user_same_group(self, tmp_path, monkeypatch):
        """A group prompt must never receive private, desktop, or other-group history."""
        from white_salary.core.memory.conversation_log import ConversationLog
        from white_salary.core.memory.manager import set_owner_user_id

        set_owner_user_id("owner")
        conv_log = ConversationLog(data_dir=str(tmp_path / "conv"))
        monkeypatch.setattr(
            ConversationLog,
            "get_instance",
            classmethod(lambda cls, data_dir="data/memory": conv_log),
        )
        conv_log.record("qq", "主人", "owner", "g1", "本群公开信息", "知道了")
        conv_log.record("qq", "主人", "owner", "g2", "另一个群的秘密", "知道了")
        conv_log.record("qq", "主人", "owner", "", "QQ私聊秘密", "知道了")
        conv_log.record("desktop", "主人", "owner", "", "桌面端秘密", "知道了")

        mgr = _make_memory_manager(tmp_path / "memory")
        ctx = mgr.get_context_injection(
            "继续",
            user_id="owner",
            is_group=True,
            group_id="g1",
        )

        assert "本群公开信息" in ctx
        assert "另一个群的秘密" not in ctx
        assert "QQ私聊秘密" not in ctx
        assert "桌面端秘密" not in ctx
        assert "不得引用任何私聊" in ctx

    async def test_non_owner_cannot_read_or_write_owner_global_memory(self, tmp_path, monkeypatch):
        from white_salary.core.memory.conversation_log import ConversationLog
        from white_salary.core.memory.manager import set_owner_user_id

        set_owner_user_id("owner")
        conv_log = ConversationLog(data_dir=str(tmp_path / "conv"))
        monkeypatch.setattr(
            ConversationLog,
            "get_instance",
            classmethod(lambda cls, data_dir="data/memory": conv_log),
        )
        conv_log.record("qq", "朋友", "friend", "", "朋友自己的私聊", "记住了")

        mgr = _make_memory_manager(tmp_path / "memory")
        await mgr.extract_and_store("我叫主人秘密名，记住主人密钥", "好", user_id="owner")
        stranger_result = await mgr.extract_and_store(
            "记住朋友的私密安排",
            "好",
            user_id="friend",
        )
        ctx = mgr.get_context_injection("继续", user_id="friend", is_group=False)

        assert stranger_result == []
        assert "朋友自己的私聊" in ctx
        assert "主人秘密名" not in ctx
        assert "主人密钥" not in ctx
        assert "朋友的私密安排" not in ctx

    def test_memory_context_filters_low_signal_ai_replies(self, tmp_path, monkeypatch):
        """最近上下文不应把“注意/如果”这类事故短回复继续喂给模型学习。"""
        from white_salary.core.memory.conversation_log import ConversationLog
        from white_salary.core.memory.manager import set_owner_user_id

        set_owner_user_id("owner")

        conv_log = ConversationLog(data_dir=str(tmp_path / "conv"))
        monkeypatch.setattr(
            ConversationLog,
            "get_instance",
            classmethod(lambda cls, data_dir="data/memory": conv_log),
        )
        conv_log.record("qq", "小白", "owner", "216", "白，发个表情包", "注意")
        conv_log.record("qq", "小白", "owner", "216", "刚才那个继续聊", "这次我听懂了，是想接着刚才那张表情包说。")

        mgr = _make_memory_manager(tmp_path / "memory")
        ctx = mgr.get_context_injection("继续刚才那个", user_id="owner")

        assert "白，发个表情包" in ctx
        assert "注意" not in ctx
        assert "这次我听懂了" in ctx


# ===================================================================
# 子项 4：好感度累积（桌面端 process_message 路径能让好感度真的涨）
#
# 曾经坏过（批2/4修复）：桌面端从不调 process_interaction/process_message，
# 好感度永远 0；QQ 端涨的分桌面端也读不到。这是回归护栏。
# ===================================================================


class TestAffinityAccumulation:
    def test_desktop_process_message_raises_points(self, tmp_path):
        """
        复刻 websocket_handler chat 分支的好感度入口
        （AffinityManager.get_for_user(owner_id).process_interaction()+process_message()）：
        一条夸奖消息后好感度必须从 0 涨上去。
        """
        from white_salary.core.affinity.manager import AffinityManager

        aff = AffinityManager.get_for_user("1234567890", data_dir=str(tmp_path))
        assert aff.get_stats()["points"] == 0.0

        # 与 websocket_handler.py chat 分支同一入口
        aff.process_interaction()
        aff.process_message("你好厉害，好聪明")

        assert aff.get_stats()["points"] > 0.0, "桌面端好感度没涨（批2/4回归）"
        assert aff.get_stats()["total_interactions"] >= 1

    def test_negative_message_lowers_points(self, tmp_path):
        """负面消息真的扣分（好感度双向可动，不是只加不减的摆设）。"""
        from white_salary.core.affinity.manager import AffinityManager

        aff = AffinityManager.get_for_user("hater", data_dir=str(tmp_path))
        aff.set_points(20.0)  # 先垫到认识档
        before = aff.get_stats()["points"]
        aff.process_message("你这个傻逼")  # insult -5
        assert aff.get_stats()["points"] < before

    def test_whitelist_blocks_false_positive(self, tmp_path):
        """撒娇用语"笨笨的"含负面词但在白名单里，不该扣分（防误伤回归）。"""
        from white_salary.core.affinity.manager import AffinityManager

        aff = AffinityManager.get_for_user("cutie", data_dir=str(tmp_path))
        aff.set_points(10.0)
        before = aff.get_stats()["points"]
        aff.process_message("你笨笨的好可爱")  # 白名单命中 → 跳过负面检测
        # 只应因"可爱"(compliment)加分，不应因"笨"扣分
        assert aff.get_stats()["points"] >= before


# ===================================================================
# 子项 5：音频格式探测 + 转码降级
#
# 曾经坏过（批2修复）：前端 MediaRecorder 产出 WebM/Opus，后端硬标 wav 上传
# SiliconFlow ASR，一天 68 次 HTTP 500，语音输入事实上从未可用。转码依赖
# ffmpeg，缺失时若不降级会直接崩。
# ===================================================================


class TestAudioConvertChain:
    def test_detect_webm_and_wav_magic_bytes(self):
        """魔数探测：WebM 的 EBML 头识别为 webm、RIFF/WAVE 识别为 wav。"""
        from white_salary.utils.audio_convert import detect_audio_format

        webm = b"\x1a\x45\xdf\xa3" + b"\x00" * 64
        wav = b"RIFF" + b"\x24\x00\x00\x00" + b"WAVE" + b"fmt " + b"\x00" * 16
        assert detect_audio_format(webm) == "webm"
        assert detect_audio_format(wav) == "wav"
        # 真实链路的关键判据：webm != wav，据此才会走 ffmpeg 转码分支
        assert detect_audio_format(webm) != detect_audio_format(wav)

    async def test_convert_returns_none_when_ffmpeg_missing(self, monkeypatch):
        """
        ffmpeg 不可用时 convert_to_wav 返回 None 且不抛异常——
        websocket voice 分支据此降级为"按真实容器格式直接上传"，不会崩。
        """
        from white_salary.utils import audio_convert

        monkeypatch.setattr(audio_convert, "find_ffmpeg", lambda: None)
        webm = b"\x1a\x45\xdf\xa3" + b"\x00" * 200
        result = await audio_convert.convert_to_wav(webm)
        assert result is None  # 降级信号，不是异常

    async def test_voice_branch_degradation_logic(self, monkeypatch):
        """
        复刻 websocket_handler voice 分支的探测+降级决策（不起服务器、不发 ASR）：
        webm 且 ffmpeg 缺失 → 保持 detected 格式上传（audio_format='webm'），
        不再硬标 wav。这是"68 次 500"根因修复的链路级护栏。
        """
        from white_salary.utils import audio_convert
        from white_salary.utils.audio_convert import detect_audio_format, convert_to_wav

        monkeypatch.setattr(audio_convert, "find_ffmpeg", lambda: None)

        audio_bytes = b"\x1a\x45\xdf\xa3" + b"\x00" * 200  # 伪 WebM
        # ↓↓↓ 与 websocket_handler.py voice 分支同一决策逻辑 ↓↓↓
        detected = detect_audio_format(audio_bytes)
        audio_format = detected if detected != "unknown" else "wav"
        if detected in ("webm", "ogg"):
            wav_bytes = await convert_to_wav(audio_bytes)
            if wav_bytes:
                audio_bytes = wav_bytes
                audio_format = "wav"
            # else: 保持 detected 格式（降级）
        # ↑↑↑ ------------------------------------------------ ↑↑↑

        # 转码不可用 → 不再谎报 wav，按真实 webm 上传
        assert detected == "webm"
        assert audio_format == "webm", "ffmpeg 缺失时仍被硬标 wav（回归到 500 根因）"


# ===================================================================
# 子项 6：三 agent 共享长期记忆、隔离短期
#
# 曾经坏过：桌面/QQ/QQ空间三个 ChatAgent 若共用同一 ShortTermMemory，
# 对话记忆互相污染串味；若各开一个 MemoryManager，核心/长期记忆又不共享。
# run_server 的装配是"各自独立 ShortTermMemory + 共享同一 MemoryManager"。
# ===================================================================


class TestThreeAgentMemoryTopology:
    def _make_agent(self, memory_manager, tmp_path, llm=None):
        """按 run_server 的装配口径造一个轻量 ChatAgent（独立短期、共享 manager）。"""
        from white_salary.core.agent.chat_agent import ChatAgent
        from white_salary.core.memory.short_term import ShortTermMemory
        from white_salary.core.personality.character import PersonalityManager

        # project_root 指向 tmp_path：找不到 prompt 文件 → 用内置默认提示词，不依赖仓库
        personality = PersonalityManager(project_root=tmp_path)
        return ChatAgent(
            llm=llm or _ScriptedLLM(["好的。"]),
            personality=personality,
            memory=ShortTermMemory(max_turns=20),  # 每个 agent 独立短期记忆
            memory_manager=memory_manager,          # 共享同一记忆管理器
        )

    def test_short_term_independent_long_term_shared(self, tmp_path):
        """
        三个 ChatAgent 各自的 ShortTermMemory 是不同对象，但 _memory_manager
        指向同一实例（拓扑装配正确）。
        """
        mgr = _make_memory_manager(tmp_path / "mem", memory_llm=_ScriptedLLM(["[]"]))

        desktop = self._make_agent(mgr, tmp_path)
        qq = self._make_agent(mgr, tmp_path)
        qzone = self._make_agent(mgr, tmp_path)

        # 短期记忆三个都是独立对象
        shorts = [desktop._memory, qq._memory, qzone._memory]
        assert len({id(s) for s in shorts}) == 3, "短期记忆被共享了（会串味）"

        # 记忆管理器是同一个（共享核心/长期记忆 + 人设由各自 personality 提供）
        assert desktop._memory_manager is qq._memory_manager is qzone._memory_manager

    async def test_conversation_does_not_leak_across_agents(self, tmp_path):
        """
        真跑一轮：桌面 agent 聊完后，它的短期记忆有内容，而 QQ/QQ空间 agent
        的短期记忆仍为空——对话记忆不串台。同时共享的 MemoryManager 里
        核心记忆对三端都可见（长期记忆共享）。
        """
        from white_salary.core.memory.manager import set_owner_user_id

        set_owner_user_id("desktop")
        mgr = _make_memory_manager(tmp_path / "mem", memory_llm=_ScriptedLLM(["[]", "[]"]))

        desktop = self._make_agent(mgr, tmp_path, llm=_ScriptedLLM(["你好呀。"]))
        qq = self._make_agent(mgr, tmp_path, llm=_ScriptedLLM(["在的。"]))
        qzone = self._make_agent(mgr, tmp_path, llm=_ScriptedLLM(["嗯嗯。"]))

        # 桌面端跑一轮对话（会写自己的短期记忆 + 往共享 manager 提取记忆）
        chunks = []
        async for c in desktop.chat_stream_with_tools(
            "我叫小白", user_id="desktop",
        ):
            chunks.append(c)
        assert "".join(chunks) == "你好呀。"

        # 桌面短期记忆有内容（用户 + 回复），另两端仍是空的（没串台）
        assert desktop._memory.message_count >= 2
        assert qq._memory.message_count == 0
        assert qzone._memory.message_count == 0

        # 共享 MemoryManager 提取到的核心记忆，三端读的是同一份
        assert mgr.core.get("user_name") == "小白"
        assert qq._memory_manager.core.get("user_name") == "小白"
        assert qzone._memory_manager.core.get("user_name") == "小白"
