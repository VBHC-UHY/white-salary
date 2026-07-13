"""
2026-07-03 工具实现（批9）的单元测试 — 提醒系统 + 忙碌/静默模式全链路。

覆盖：
  - parse_when 自然语言时间解析全场景（相对/绝对/时段/每天/解析失败追问）
  - ReminderService 调度到期触发（注入时钟）/迟到补提醒/每日重复推进/持久化
  - 双通道通知（桌面桥回调 + QQ回调；QQ通道缺失时不炸）
  - 提醒三工具真实现（解析失败返回追问文案/设置/取消/列表）
  - PresenceState 状态机（到期自动恢复/持久化/手动解除）
  - 忙碌/静默状态下 QQ 回复决策（群聊闭嘴/被@限频告知/主人急事与解除词放行）
  - 桌面主动搭话跳过（_should_skip_proactive）与桥消息分流（提醒穿透静默）
  - 注册表：7个旧假成功工具不在册、3个新静默工具+提醒三件套在册
  - MessageRouter 提醒/取消提醒/静默意图提示
  - ChatAgent 注入型提示（tool_llm 判断上下文带提示词、不强制执行）
"""

import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import AsyncGenerator

import pytest

from white_salary.adapters.tools.registry import ToolRegistry
from white_salary.core.agent.chat_agent import ChatAgent, ToolResultPresentationError
from white_salary.core.interfaces.llm import LLMInterface
from white_salary.core.interfaces.types import Message, MessageRole, ToolCall, ToolResult
from white_salary.core.memory.short_term import ShortTermMemory
from white_salary.core.message.processing import MessageRouter
from white_salary.core.personality.character import PersonalityManager
from white_salary.core.services.presence_state import (
    MODE_BUSY,
    MODE_NORMAL,
    MODE_SILENT,
    PresenceState,
)
from white_salary.core.services.reminder_service import (
    ASK_WHEN_TEXT,
    ReminderService,
    parse_when,
)
from white_salary.infrastructure.server.websocket_handler import (
    _partition_bridge_messages,
    _should_skip_proactive,
)


PROJECT_ROOT = Path(__file__).parent.parent.parent

# 固定基准时刻：2026-07-03 周五 14:00
NOW = datetime(2026, 7, 3, 14, 0, 0)


# ================================================================
# fixture：隔离进程级单例（用 tmp_path，不碰真实 data/ 目录）
# ================================================================

@pytest.fixture
def reminder_env(tmp_path):
    """带假双通道的 ReminderService（并注入为进程级单例）。"""
    calls: dict[str, list] = {"desktop": [], "qq": []}

    def fake_desktop(text: str) -> None:
        calls["desktop"].append(text)

    def fake_qq(user_id: str, text: str) -> bool:
        calls["qq"].append((user_id, text))
        return True

    service = ReminderService(
        data_dir=str(tmp_path),
        qq_send=fake_qq,
        owner_id="10001",
        desktop_push=fake_desktop,
    )
    ReminderService.set_instance(service)
    yield service, calls
    ReminderService.reset_instance()


@pytest.fixture
def presence(tmp_path):
    """独立数据目录的 PresenceState（并注入为进程级单例）。"""
    instance = PresenceState(data_dir=str(tmp_path))
    PresenceState.set_instance(instance)
    yield instance
    PresenceState.reset_instance()


# ================================================================
# 1. 自然语言时间解析全场景
# ================================================================

class TestParseWhen:
    """parse_when 纯函数：任务书要求的全部表达 + 消歧规则 + 解析失败。"""

    def _expect(self, text: str, expected: datetime, repeat: str = "none",
                now: datetime = NOW) -> None:
        parsed = parse_when(text, now=now)
        assert parsed is not None, f"{text!r} 应能解析"
        assert parsed.repeat == repeat, f"{text!r} repeat 应为 {repeat}"
        actual = datetime.fromtimestamp(parsed.due_ts)
        assert actual == expected, f"{text!r} 应解析为 {expected}，实际 {actual}"

    def test_relative_minutes(self) -> None:
        """「10分钟后」= 当前时刻 + 10分钟。"""
        self._expect("10分钟后", NOW + timedelta(minutes=10))

    def test_relative_half_hour(self) -> None:
        """「半小时后 / 半个小时后」= +30分钟。"""
        self._expect("半小时后", NOW + timedelta(minutes=30))
        self._expect("半个小时后", NOW + timedelta(minutes=30))

    def test_relative_hours_chinese_number(self) -> None:
        """「两小时后 / 一个半小时后」中文数字与「个半」组合。"""
        self._expect("两小时后", NOW + timedelta(hours=2))
        self._expect("一个半小时后", NOW + timedelta(minutes=90))

    def test_bare_hour_prefers_next_occurrence(self) -> None:
        """裸「3点」14:00说 → 今天15:00（{今天3点,今天15点,明天3点}里最近的未来）。"""
        self._expect("3点", NOW.replace(hour=15))

    def test_bare_hour_all_passed_rolls_to_tomorrow(self) -> None:
        """裸「3点」16:00说 → 今天两个候选都过点，取明天03:00。"""
        now_late = NOW.replace(hour=16)
        self._expect("3点", (now_late + timedelta(days=1)).replace(hour=3),
                     now=now_late)

    def test_pm_hint(self) -> None:
        """「下午3点」→ 15:00；已过点顺延到明天15:00。"""
        self._expect("下午3点", NOW.replace(hour=15))
        now_late = NOW.replace(hour=16)
        self._expect("下午3点", (now_late + timedelta(days=1)).replace(hour=15),
                     now=now_late)

    def test_tomorrow_morning(self) -> None:
        """「明天早上8点」→ 明天08:00。"""
        self._expect("明天早上8点", (NOW + timedelta(days=1)).replace(hour=8))

    def test_tonight_half(self) -> None:
        """「今晚9点半」→ 今天21:30。"""
        self._expect("今晚9点半", NOW.replace(hour=21, minute=30))

    def test_hour_with_minutes(self) -> None:
        """「3点20」→ 今天15:20（裸小时消歧同样适用）。"""
        self._expect("3点20", NOW.replace(hour=15, minute=20))

    def test_daily_repeat(self) -> None:
        """「每天8点」→ repeat=daily；14:00设 → 首次明天08:00；07:00设 → 今天08:00。"""
        self._expect("每天8点", (NOW + timedelta(days=1)).replace(hour=8),
                     repeat="daily")
        now_early = NOW.replace(hour=7)
        self._expect("每天8点", now_early.replace(hour=8), repeat="daily",
                     now=now_early)

    def test_unparseable_returns_none(self) -> None:
        """解析不出返回 None（工具层据此追问，不许瞎猜）。"""
        for text in ("开会", "随便什么时候", "马上", ""):
            assert parse_when(text, now=NOW) is None, f"{text!r} 应解析失败"


# ================================================================
# 2. ReminderService：调度到期/迟到/重复/持久化/双通道
# ================================================================

class TestReminderService:
    """提醒服务核心行为（注入时钟驱动，不依赖真实等待）。"""

    def test_due_triggers_both_channels(self, reminder_env) -> None:
        """到点触发：双通道都收到自然口语通知，状态转 done。"""
        service, calls = reminder_env
        ok, msg = service.add("开会", "10分钟后", created_by="10001")
        assert ok and "开会" in msg

        # 到点后70秒检查（<120秒迟到阈值 → 正常文案）
        fired = service.check_due(now=time.time() + 600 + 70)
        assert len(fired) == 1
        assert "到点啦" in fired[0] and "开会" in fired[0]
        # 双通道：桌面桥 + QQ给主人
        assert calls["desktop"] == [fired[0]]
        assert calls["qq"] == [("10001", fired[0])]
        # 一次性提醒触发后不再 pending
        assert service.pending_count == 0

    def test_not_due_no_fire(self, reminder_env) -> None:
        """没到点不触发、通道无动静。"""
        service, calls = reminder_env
        service.add("开会", "10分钟后")
        assert service.check_due(now=time.time()) == []
        assert calls["desktop"] == [] and calls["qq"] == []
        assert service.pending_count == 1

    def test_late_reminder_marked(self, reminder_env) -> None:
        """错过超过阈值（如后端宕机）→ 补通知并标注「迟到」。"""
        service, calls = reminder_env
        service.add("取快递", "10分钟后")
        fired = service.check_due(now=time.time() + 600 + 3600)
        assert len(fired) == 1
        assert "迟到" in fired[0] and "取快递" in fired[0]

    def test_daily_repeat_advances(self, reminder_env) -> None:
        """每日重复：触发后推进到下一个未来时刻，保持 pending。"""
        service, calls = reminder_env
        ok, msg = service.add("吃药", "每天8点")
        assert ok and "每天" in msg
        with service._lock:
            due = service._reminders[0].due_ts
        fired = service.check_due(now=due + 30)
        assert len(fired) == 1 and "吃药" in fired[0]
        assert service.pending_count == 1  # 仍在册
        with service._lock:
            assert service._reminders[0].due_ts == pytest.approx(due + 86400)

    def test_persistence_reload(self, reminder_env, tmp_path) -> None:
        """落盘后新实例能加载回待提醒事项。"""
        service, _calls = reminder_env
        service.add("开会", "10分钟后")
        reloaded = ReminderService(data_dir=str(tmp_path))
        assert reloaded.pending_count == 1

    def test_missing_qq_channel_is_safe(self, tmp_path) -> None:
        """未注入QQ回调（QQ未启用）时只走桌面通道，不炸。"""
        desktop_calls: list[str] = []
        service = ReminderService(
            data_dir=str(tmp_path), qq_send=None, owner_id="",
            desktop_push=desktop_calls.append,
        )
        service.add("喝水", "10分钟后")
        fired = service.check_due(now=time.time() + 700)
        assert len(fired) == 1 and desktop_calls == [fired[0]]

    def test_parse_failure_asks_back(self, reminder_env) -> None:
        """时间解析不出 → 返回追问文案，不创建提醒、不瞎猜。"""
        service, _calls = reminder_env
        ok, msg = service.add("开会", "随便什么时候")
        assert not ok and msg == ASK_WHEN_TEXT
        assert service.pending_count == 0

    def test_cancel_by_keyword_and_unknown(self, reminder_env) -> None:
        """按关键词取消；查无匹配时如实说没找到。"""
        service, _calls = reminder_env
        service.add("开会", "10分钟后")
        assert "没找到" in service.cancel("跑步")
        assert "已取消" in service.cancel("开会")
        assert service.pending_count == 0


# ================================================================
# 3. 提醒三工具（basic.py 真实现）
# ================================================================

class TestReminderTools:
    """set_reminder / cancel_reminder / list_reminders 工具行为。"""

    async def test_set_list_cancel_flow(self, reminder_env) -> None:
        """设置 → 列表可见 → 取消 → 列表为空。"""
        from white_salary.adapters.tools.builtin.basic import (
            cancel_reminder, list_reminders, set_reminder,
        )
        reply = await set_reminder(content="开会", when="10分钟后")
        assert "记下了" in reply and "开会" in reply
        listed = await list_reminders()
        assert "开会" in listed
        cancelled = await cancel_reminder(keyword="开会")
        assert "已取消" in cancelled
        assert "没有待提醒" in await list_reminders()

    async def test_set_reminder_asks_when_unparseable(self, reminder_env) -> None:
        """时间说不清 → 工具返回追问文案（含「几点」），绝不假装成功。"""
        from white_salary.adapters.tools.builtin.basic import set_reminder
        reply = await set_reminder(content="开会", when="")
        assert "几点" in reply
        service, _calls = reminder_env
        assert service.pending_count == 0

    def test_tools_registered_with_scenario_descriptions(self) -> None:
        """三工具在注册表且 description 含触发例句（tool_llm 一读就懂）。"""
        tools = {t.name: t for t in ToolRegistry().get_all()}
        assert "set_reminder" in tools
        assert "提醒我三点开会" in tools["set_reminder"].description
        assert "cancel_reminder" in tools and "list_reminders" in tools


# ================================================================
# 4. PresenceState 状态机
# ================================================================

class TestPresenceState:
    """正常/忙碌/静默三态、到期自动恢复、持久化。"""

    def test_default_normal(self, presence) -> None:
        assert presence.get_mode() == MODE_NORMAL
        assert not presence.is_quiet

    def test_busy_expires_automatically(self, presence) -> None:
        """忙碌30分钟：期内 quiet，过点自动恢复正常。"""
        now = time.time()
        reply = presence.set_quiet(MODE_BUSY, duration_minutes=30, now=now)
        assert "30" in reply
        assert presence.get_mode(now=now + 29 * 60) == MODE_BUSY
        assert presence.get_mode(now=now + 31 * 60) == MODE_NORMAL

    def test_silent_without_duration_stays(self, presence) -> None:
        """静默不给时长 → 无限期，直到手动解除。"""
        now = time.time()
        presence.set_quiet(MODE_SILENT, now=now)
        assert presence.get_mode(now=now + 100 * 86400) == MODE_SILENT
        assert "可以说话" in presence.clear()
        assert presence.get_mode() == MODE_NORMAL

    def test_persistence_across_instances(self, presence, tmp_path) -> None:
        """状态落盘：新实例（如重启后）恢复忙碌状态。"""
        presence.set_quiet(MODE_BUSY, duration_minutes=120)
        reloaded = PresenceState(data_dir=str(tmp_path))
        assert reloaded.get_mode() == MODE_BUSY

    def test_expired_state_not_loaded(self, tmp_path) -> None:
        """已到期的持久化状态启动时直接丢弃。"""
        first = PresenceState(data_dir=str(tmp_path))
        now = time.time() - 3600
        first.set_quiet(MODE_BUSY, duration_minutes=10, now=now)
        reloaded = PresenceState(data_dir=str(tmp_path))
        assert reloaded.get_mode() == MODE_NORMAL

    def test_invalid_mode_rejected(self, presence) -> None:
        assert "不认识" in presence.set_quiet("sleep")
        assert presence.get_mode() == MODE_NORMAL


# ================================================================
# 5. 忙碌/静默下的 QQ 回复决策
# ================================================================

class TestQuietQQDecision:
    """decide_qq_reply：群聊闭嘴/被@限频告知/主人急事与解除词放行。"""

    def _busy(self, presence, now: float) -> None:
        presence.set_quiet(MODE_BUSY, duration_minutes=60, now=now)

    def test_normal_mode_always_replies(self, presence) -> None:
        d = presence.decide_qq_reply(
            user_id="123", text="你好", is_group=True, is_at_me=False,
            is_owner=False,
        )
        assert d.action == "reply_normal"

    def test_group_chatter_skipped(self, presence) -> None:
        """忙碌时群聊闲聊（未@）→ 真的闭嘴。"""
        now = time.time()
        self._busy(presence, now)
        d = presence.decide_qq_reply(
            user_id="123", text="白白在吗", is_group=True, is_at_me=False,
            is_owner=False, now=now,
        )
        assert d.action == "skip"

    def test_group_at_gets_rate_limited_notice(self, presence) -> None:
        """被@ → 简短告知一次；同用户30分钟内再@ → skip；30分钟后可再告知。"""
        now = time.time()
        self._busy(presence, now)
        first = presence.decide_qq_reply(
            user_id="123", text="@白 在吗", is_group=True, is_at_me=True,
            is_owner=False, now=now,
        )
        assert first.action == "brief_notice" and "忙" in first.notice_text
        again = presence.decide_qq_reply(
            user_id="123", text="@白 快回", is_group=True, is_at_me=True,
            is_owner=False, now=now + 60,
        )
        assert again.action == "skip"
        # 另一个用户不受这个人的限频影响
        other = presence.decide_qq_reply(
            user_id="456", text="@白 在吗", is_group=True, is_at_me=True,
            is_owner=False, now=now + 60,
        )
        assert other.action == "brief_notice"
        # 30分钟后同用户可再次告知
        later = presence.decide_qq_reply(
            user_id="123", text="@白 咋样了", is_group=True, is_at_me=True,
            is_owner=False, now=now + 31 * 60,
        )
        assert later.action == "brief_notice"

    def test_owner_private_urgent_passes(self, presence) -> None:
        """主人私聊带「紧急/在吗」→ 正常回复。"""
        now = time.time()
        self._busy(presence, now)
        d = presence.decide_qq_reply(
            user_id="10001", text="在吗？有点急事", is_group=False,
            is_at_me=False, is_owner=True, now=now,
        )
        assert d.action == "reply_normal"

    def test_owner_private_clear_intent_passes(self, presence) -> None:
        """主人私聊说「我忙完了」→ 放行（clear_quiet_mode 工具才有机会执行）。"""
        now = time.time()
        self._busy(presence, now)
        d = presence.decide_qq_reply(
            user_id="10001", text="我忙完了，出来聊", is_group=False,
            is_at_me=False, is_owner=True, now=now,
        )
        assert d.action == "reply_normal"

    def test_owner_private_smalltalk_gets_notice(self, presence) -> None:
        """主人私聊普通闲聊（无紧急/解除词）→ 限频简短告知。"""
        now = time.time()
        self._busy(presence, now)
        d = presence.decide_qq_reply(
            user_id="10001", text="哈哈哈看个视频", is_group=False,
            is_at_me=False, is_owner=True, now=now,
        )
        assert d.action == "brief_notice"

    def test_silent_notice_text_differs(self, presence) -> None:
        """静默模式的简短告知与忙碌措辞不同。"""
        now = time.time()
        presence.set_quiet(MODE_SILENT, now=now)
        d = presence.decide_qq_reply(
            user_id="789", text="在？", is_group=False, is_at_me=False,
            is_owner=False, now=now,
        )
        assert d.action == "brief_notice" and "不太方便" in d.notice_text


# ================================================================
# 6. 桌面端：auto_chat 跳过 + 桥消息分流（提醒穿透静默）
# ================================================================

class TestDesktopProactiveGate:
    """websocket_handler 的主动搭话闸门与桥消息分流。"""

    def test_skip_when_quiet(self, presence) -> None:
        """忙碌/静默 → 主动搭话跳过；ignore_quiet=True（提醒）穿透。"""
        presence.set_quiet(MODE_BUSY, duration_minutes=60)
        assert _should_skip_proactive() is True
        assert _should_skip_proactive(ignore_quiet=True) is False

    def test_no_skip_when_normal(self, presence) -> None:
        assert _should_skip_proactive() is False

    def test_partition_bridge_messages(self) -> None:
        """Finished reminder/QQ text is transported directly, not re-generated."""
        messages = [
            {"message": "到点啦，你让我提醒你：开会", "source": "reminder"},
            {"message": "记得吃饭", "source": "qq", "from_user": "10001"},
            {"message": "", "source": "qq"},  # 空消息剔除
        ]
        direct, passthrough_events, normal_events = _partition_bridge_messages(messages)
        assert [item["message"] for item in direct] == [
            "到点啦，你让我提醒你：开会",
            "记得吃饭",
        ]
        assert passthrough_events == []
        assert normal_events == []

    def test_partition_game_events_passthrough(self) -> None:
        """2026-07-03 批11：source=game 的游戏事件归穿透组（静默期也播报），
        且原样作为触发提示，不加'收到来自xx的消息'前缀。"""
        messages = [
            {"message": "阿白刚打赢了Boss，快夸夸他！", "source": "game"},
            {"message": "普通QQ转发", "source": "qq"},
        ]
        direct, passthrough_events, normal_events = _partition_bridge_messages(messages)
        assert [item["message"] for item in direct] == ["普通QQ转发"]
        assert len(passthrough_events) == 1
        assert passthrough_events[0][1] == "阿白刚打赢了Boss，快夸夸他！"
        assert normal_events == []

    def test_explicit_normal_event_requires_one_model_response(self) -> None:
        messages = [{
            "message": "外部状态变化",
            "source": "monitor",
            "delivery_kind": "event_prompt",
        }]

        direct, passthrough_events, normal_events = _partition_bridge_messages(messages)

        assert direct == []
        assert passthrough_events == []
        assert normal_events[0][0] is messages[0]
        assert "外部状态变化" in normal_events[0][1]


# ================================================================
# 7. 注册表：旧7名不在册、新3名+提醒三件套在册
# ================================================================

class TestQuietToolsRegistry:
    """social.py 合并重写后的注册表内容。"""

    OLD_FAKE_TOOLS = [
        "set_busy_mode", "clear_busy_mode", "global_silent",
        "switch_filter_mode", "check_filter_mode", "filter_toggle",
        "silent_toggle",
    ]
    NEW_TOOLS = ["set_quiet_mode", "clear_quiet_mode", "get_quiet_status"]

    def test_old_seven_delisted_new_three_registered(self) -> None:
        names = {t.name for t in ToolRegistry().get_all()}
        still_there = [n for n in self.OLD_FAKE_TOOLS if n in names]
        assert not still_there, f"旧假成功工具应保持下架: {still_there}"
        missing = [n for n in self.NEW_TOOLS if n not in names]
        assert not missing, f"新静默工具缺失: {missing}"

    def test_registry_stays_under_deepseek_limit(self) -> None:
        """加回提醒三件套+新3个后总数仍须留足 DeepSeek 128 上限余量。"""
        assert ToolRegistry().count < 128

    async def test_quiet_tools_drive_real_state(self, presence) -> None:
        """新工具真的写状态：set → 状态变忙碌；status → 描述；clear → 恢复。"""
        from white_salary.adapters.tools.builtin.social import (
            clear_quiet_mode, get_quiet_status, set_quiet_mode,
        )
        reply = await set_quiet_mode(mode="busy", duration_minutes=45)
        assert "45" in reply
        assert presence.get_mode() == MODE_BUSY
        status = await get_quiet_status()
        assert "忙碌" in status
        await clear_quiet_mode()
        assert presence.get_mode() == MODE_NORMAL


# ================================================================
# 8. MessageRouter：提醒/取消提醒/静默意图提示
# ================================================================

class TestBatch9ToolHints:
    """get_tool_hint 新增意图的命中与优先级。"""

    def test_reminder_intent(self) -> None:
        router = MessageRouter()
        for text in ("提醒我三点开会", "别忘了叫我起床", "定个闹钟八点"):
            assert "set_reminder" in router.get_tool_hint(text), text

    def test_cancel_beats_set(self) -> None:
        """「不用提醒我了」含「提醒我」，但取消意图优先级更高。"""
        router = MessageRouter()
        assert "cancel_reminder" in router.get_tool_hint("不用提醒我了")
        assert "cancel_reminder" in router.get_tool_hint("取消提醒")

    def test_quiet_intent(self) -> None:
        router = MessageRouter()
        for text in ("别吵我，我要工作了", "安静一会好吗", "闭嘴一会", "别打扰我"):
            assert "set_quiet_mode" in router.get_tool_hint(text), text

    def test_normal_chat_no_hint(self) -> None:
        assert MessageRouter().get_tool_hint("今天天气真好啊") == ""


# ================================================================
# 9. ChatAgent：注入型提示（不强制执行）
# ================================================================

class RecordingToolLLM(LLMInterface):
    """工具判断LLM桩：记录收到的 messages，返回预设工具调用。"""

    def __init__(self, tool_calls: "list[ToolCall] | None" = None) -> None:
        self.calls = 0
        self.seen_messages: list[list[Message]] = []
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
    ) -> "tuple[str, list[ToolCall]]":
        self.calls += 1
        self.seen_messages.append(list(messages))
        return "", self._tool_calls

    async def process_tool_results(
        self, messages: list[Message], tool_results: list[ToolResult],
        temperature: float = 0.7, max_tokens: int = 2048,
    ) -> str:
        return ""


class StreamMainLLM(LLMInterface):
    """主模型桩：流式返回固定文本。"""

    def __init__(self, stream_response: str = "好的") -> None:
        self._stream_response = stream_response

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
    ) -> "tuple[str, list[ToolCall]]":
        return self._stream_response, []

    async def process_tool_results(
        self, messages: list[Message], tool_results: list[ToolResult],
        temperature: float = 0.7, max_tokens: int = 2048,
    ) -> str:
        return "；".join(r.content for r in tool_results)


class EmptyToolResultMainLLM(StreamMainLLM):
    """Main LLM stub that simulates an empty post-tool reply."""

    async def process_tool_results(
        self, messages: list[Message], tool_results: list[ToolResult],
        temperature: float = 0.7, max_tokens: int = 2048,
    ) -> str:
        return "   "


class HintFakeRegistry:
    """注册表桩：声明 set_reminder / recall_conversation 存在，记录执行。"""

    def __init__(self) -> None:
        self.execute_calls: list[tuple[str, dict]] = []

    @property
    def count(self) -> int:
        return 2

    def get_tool(self, name: str):
        if name in ("set_reminder", "recall_conversation"):
            return object()
        return None

    def get_openai_tools(self) -> list[dict]:
        return [{"type": "function",
                 "function": {"name": "set_reminder",
                              "description": "设提醒", "parameters": {}}}]

    async def execute(self, name: str, arguments: dict) -> str:
        self.execute_calls.append((name, dict(arguments)))
        return "执行完成"


def _make_hint_agent(tool_llm: LLMInterface,
                     registry: HintFakeRegistry) -> ChatAgent:
    return ChatAgent(
        llm=StreamMainLLM(),
        personality=PersonalityManager(project_root=PROJECT_ROOT),
        memory=ShortTermMemory(max_turns=20),
        tool_registry=registry,  # type: ignore[arg-type]
        tool_llm=tool_llm,
    )


class TestHintInjection:
    """提醒/静默意图 → 提示词注入 tool_llm 判断上下文（不强制执行）。"""

    async def test_reminder_hint_injected_not_forced(self) -> None:
        """「提醒我三点开会」：tool_llm 收到注入提示，且不发生强制执行。"""
        registry = HintFakeRegistry()
        tool_llm = RecordingToolLLM()
        agent = _make_hint_agent(tool_llm, registry)

        async for _ in agent.chat_stream_with_tools("提醒我三点开会"):
            pass

        # 走了 tool_llm 判断（未被绕过），且没有任何强制直连执行
        assert tool_llm.calls == 1
        assert registry.execute_calls == []
        # 判断上下文末尾追加了含 set_reminder 的 system 提示
        injected = tool_llm.seen_messages[0][-1]
        assert injected.role == MessageRole.SYSTEM
        assert "set_reminder" in injected.content

    async def test_normal_chat_no_injection(self) -> None:
        """普通聊天：tool_llm 上下文不带工具选择提示。"""
        registry = HintFakeRegistry()
        tool_llm = RecordingToolLLM()
        agent = _make_hint_agent(tool_llm, registry)

        async for _ in agent.chat_stream_with_tools("今天天气真好啊"):
            pass

        assert tool_llm.calls == 1
        assert all(
            "[工具选择提示]" not in m.content
            for m in tool_llm.seen_messages[0]
        )

    async def test_recall_still_forced_via_registry(self) -> None:
        """通用化后 recall 直连不回退：命中回忆意图仍强制执行且绕过 tool_llm。"""
        registry = HintFakeRegistry()
        tool_llm = RecordingToolLLM()
        agent = _make_hint_agent(tool_llm, registry)

        async for _ in agent.chat_stream_with_tools("还记得我们之前聊过的周末计划吗"):
            pass

        recall_calls = [c for c in registry.execute_calls
                        if c[0] == "recall_conversation"]
        assert len(recall_calls) >= 1
        assert recall_calls[0][1].get("keyword") == "周末计划"
        assert tool_llm.calls == 0

    async def test_route_text_limits_tool_intent_to_current_qq_message(self) -> None:
        """QQ群历史含回忆词时，工具路由只看当前合并后的用户话。"""
        registry = HintFakeRegistry()
        tool_llm = RecordingToolLLM()
        agent = _make_hint_agent(tool_llm, registry)

        enriched_input = "群历史：还记得我们之前聊过的周末计划吗\n\n路人 对你说: 今天天气真好啊"
        async for _ in agent.chat_stream_with_tools(
            enriched_input,
            route_text="今天天气真好啊",
        ):
            pass

        assert registry.execute_calls == []
        assert tool_llm.calls == 1
        assert "周末计划" not in tool_llm.seen_messages[0][-1].content

    async def test_empty_tool_postprocess_never_leaks_internal_result(self) -> None:
        """Empty persona output becomes one transport error, never an internal tool log."""
        registry = HintFakeRegistry()
        tool_llm = RecordingToolLLM([
            ToolCall(id="call_1", name="set_reminder", arguments={}),
        ])
        agent = ChatAgent(
            llm=EmptyToolResultMainLLM(),
            personality=PersonalityManager(project_root=PROJECT_ROOT),
            memory=ShortTermMemory(max_turns=20),
            tool_registry=registry,  # type: ignore[arg-type]
            tool_llm=tool_llm,
        )

        with pytest.raises(ToolResultPresentationError):
            async for _ in agent.chat_stream_with_tools("提醒我三点开会"):
                pass
