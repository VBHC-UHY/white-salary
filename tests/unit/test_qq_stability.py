"""
QQ链路稳定性修复的单元测试（2026-07-02 审计修复（批4））。

覆盖：
  1. NapCat重连指数退避序列 + 日志节流判定（qq_adapter）
  2. 入群请求判定：family邀请通过/陌生邀请拒绝/申请按好感度（qq_adapter）
  3. 重生成撤销按对象身份删除，不误删他人对话（qq_handler._undo_generation_pair）
  4. QZone Cookie过期桌面提醒的每日一次节流（qzone_monitor）
"""

import asyncio
import time
from types import SimpleNamespace

from white_salary.adapters.platform.qq_adapter import QQAdapter, QQMessage
from white_salary.core.interfaces.types import Message, MessageRole
from white_salary.core.services.qzone_monitor import QzoneMonitor
from white_salary.infrastructure.server.qq_handler import (
    _expected_memory_tag,
    _transcribe_qq_voice,
    _undo_generation_pair,
)


# ================================================================
# 1. NapCat 重连指数退避
# ================================================================

class TestReconnectBackoff:
    """指数退避：5s起步、每次×2、封顶60s、连接成功归零。"""

    def test_backoff_sequence(self) -> None:
        """连续失败1..8次的退避序列：5,10,20,40,60,60,60,60。"""
        expected = [5.0, 10.0, 20.0, 40.0, 60.0, 60.0, 60.0, 60.0]
        actual = [QQAdapter._backoff_interval(n, base=5.0, cap=60.0)
                  for n in range(1, 9)]
        assert actual == expected

    def test_backoff_reset_on_success(self) -> None:
        """连接成功归零后（fail_count=0）回到起步间隔。"""
        assert QQAdapter._backoff_interval(0, base=5.0, cap=60.0) == 5.0

    def test_backoff_custom_base_and_cap(self) -> None:
        """自定义起步/上限也遵守×2与封顶规则。"""
        assert QQAdapter._backoff_interval(1, base=3.0, cap=30.0) == 3.0
        assert QQAdapter._backoff_interval(2, base=3.0, cap=30.0) == 6.0
        assert QQAdapter._backoff_interval(4, base=3.0, cap=30.0) == 24.0
        assert QQAdapter._backoff_interval(5, base=3.0, cap=30.0) == 30.0  # 48封顶

    def test_backoff_huge_fail_count_no_overflow(self) -> None:
        """极大失败次数不会溢出，稳定返回上限。"""
        assert QQAdapter._backoff_interval(10000, base=5.0, cap=60.0) == 60.0

    def test_log_throttle(self) -> None:
        """连续失败≤10次每次记日志；超过10次后每10次汇总一条。"""
        # 前10次全记
        assert all(QQAdapter._should_log_reconnect(n) for n in range(1, 11))
        # 11~19 不记
        assert not any(QQAdapter._should_log_reconnect(n) for n in range(11, 20))
        # 20/30/40 汇总记一条
        assert QQAdapter._should_log_reconnect(20)
        assert QQAdapter._should_log_reconnect(30)
        assert not QQAdapter._should_log_reconnect(21)


# ================================================================
# 2. 入群请求判定
# ================================================================

class TestGroupRequestDecision:
    """invite只认family白名单；add按好感度>=0。"""

    def _adapter(self) -> QQAdapter:
        return QQAdapter(family_qq=["1234567890", "10001"])

    def test_family_invite_approved(self) -> None:
        """家人邀请白入群→同意。"""
        approve, reason = self._adapter()._decide_group_request(
            "invite", "1234567890", None)
        assert approve is True
        assert "家人" in reason

    def test_stranger_invite_rejected(self) -> None:
        """陌生人邀请白入群→拒绝。"""
        approve, reason = self._adapter()._decide_group_request(
            "invite", "999999", None)
        assert approve is False

    def test_family_qq_normalized_to_str(self) -> None:
        """family_qq传int也能匹配（构造时统一转str）。"""
        adapter = QQAdapter(family_qq=[1234567890])  # type: ignore[list-item]
        approve, _ = adapter._decide_group_request("invite", "1234567890", None)
        assert approve is True

    def test_add_request_positive_affinity_approved(self) -> None:
        """他人申请入群：好感度>=0→同意。"""
        approve, _ = self._adapter()._decide_group_request("add", "888", 12.5)
        assert approve is True
        # 边界：正好0分也同意
        approve_zero, _ = self._adapter()._decide_group_request("add", "888", 0.0)
        assert approve_zero is True

    def test_add_request_negative_affinity_rejected(self) -> None:
        """他人申请入群：好感度<0→拒绝。"""
        approve, _ = self._adapter()._decide_group_request("add", "888", -5.0)
        assert approve is False

    def test_add_request_affinity_unavailable_defaults_approve(self) -> None:
        """好感度系统不可用（None）→保持旧行为默认同意。"""
        approve, reason = self._adapter()._decide_group_request("add", "888", None)
        assert approve is True
        assert "不可用" in reason


class TestQQMessageMedia:
    """结构化 OneBot 消息也要进入现有文本/图片处理链。"""

    def test_structured_image_message_keeps_text_and_url(self) -> None:
        msg = QQMessage({
            "post_type": "message",
            "message_type": "group",
            "user_id": "10001",
            "group_id": "2163039710",
            "raw_message": "",
            "message": [
                {"type": "text", "data": {"text": "看看这个"}},
                {"type": "image", "data": {"url": "https://example.com/a.png"}},
            ],
        })

        assert msg.has_image is True
        assert msg.image_urls == ["https://example.com/a.png"]
        assert "看看这个" in msg.text
        assert "[图片]" in msg.text

    def test_owner_group_image_bypasses_inactive_smart_reply(self) -> None:
        adapter = QQAdapter(family_qq=["10001"])
        adapter._self_id = "99999"
        seen: list[str] = []
        sent: list[str] = []

        async def on_message(msg: QQMessage) -> str:
            seen.append(msg.text)
            return "收到图片了"

        async def send_reply(msg: QQMessage, reply: str) -> None:
            sent.append(reply)

        adapter.on_message = on_message
        adapter.send_reply = send_reply  # type: ignore[method-assign]
        msg = QQMessage({
            "post_type": "message",
            "message_type": "group",
            "user_id": "10001",
            "group_id": "2163039710",
            "self_id": "99999",
            "raw_message": "",
            "message": [
                {"type": "image", "data": {"url": "https://example.com/a.png"}},
            ],
            "sender": {"nickname": "小白"},
        })

        asyncio.run(adapter._handle_message(msg))

        assert seen == ["[图片]"]
        assert sent == ["收到图片了"]


class TestQQVoiceASR:
    """QQ语音识别必须走 ASRInterface.transcribe。"""

    def test_transcribe_helper_uses_asr_interface(self) -> None:
        class FakeASR:
            def __init__(self) -> None:
                self.audio = None

            async def transcribe(self, audio):
                self.audio = audio
                return SimpleNamespace(text="你好呀")

        asr = FakeASR()
        text = asyncio.run(_transcribe_qq_voice(asr, b"voice-bytes", "mp3"))

        assert text == "你好呀"
        assert asr.audio.samples == b"voice-bytes"
        assert asr.audio.dtype == "mp3"


# ================================================================
# 3. 重生成撤销按对象身份
# ================================================================

def _msg(role: MessageRole, content: str) -> Message:
    return Message(role=role, content=content)


class TestUndoGenerationPair:
    """撤销只删本轮新增且匹配的一问一答；找不到就不删。"""

    def test_removes_only_own_round_pair(self) -> None:
        """并发场景：本轮之后混入他人问答，只删本轮的两条。"""
        old_u = _msg(MessageRole.USER, "旧对话")
        old_a = _msg(MessageRole.ASSISTANT, "[回复 群1 甲] 旧回复")
        messages = [old_u, old_a]
        before_ids = {id(m) for m in messages}

        # 本轮生成写入的一问一答
        my_u = _msg(MessageRole.USER, "本轮输入")
        my_a = _msg(MessageRole.ASSISTANT, "[回复 群1 甲] 本轮回复")
        # 并发用户乙在本轮期间写入的问答（交错在中间）
        other_u = _msg(MessageRole.USER, "乙的消息")
        other_a = _msg(MessageRole.ASSISTANT, "[回复 群2 乙] 乙的回复")
        messages.extend([my_u, other_u, other_a, my_a])

        removed = _undo_generation_pair(
            messages, before_ids, "本轮输入",
            _expected_memory_tag("甲", "1", True),
        )

        assert removed == 2
        assert my_u not in [m for m in messages]
        # 用身份检查：他人对话和旧对话全部保留
        assert any(m is other_u for m in messages)
        assert any(m is other_a for m in messages)
        assert any(m is old_u for m in messages)
        assert any(m is old_a for m in messages)
        assert not any(m is my_u for m in messages)
        assert not any(m is my_a for m in messages)

    def test_identical_content_elsewhere_not_removed(self) -> None:
        """记忆里已有等值消息（frozen dataclass相等）时，按身份删不按值删。"""
        # 旧记录内容与本轮完全相同（Message带__eq__，list.remove会按值误删）
        old_u = _msg(MessageRole.USER, "重复内容")
        old_a = _msg(MessageRole.ASSISTANT, "[回复 甲] 重复回复")
        messages = [old_u, old_a]
        before_ids = {id(m) for m in messages}

        my_u = _msg(MessageRole.USER, "重复内容")
        my_a = _msg(MessageRole.ASSISTANT, "[回复 甲] 重复回复")
        messages.extend([my_u, my_a])

        removed = _undo_generation_pair(
            messages, before_ids, "重复内容",
            _expected_memory_tag("甲", "", False),
        )

        assert removed == 2
        # 删的是本轮的对象，不是旧的等值对象
        assert any(m is old_u for m in messages)
        assert any(m is old_a for m in messages)
        assert not any(m is my_u for m in messages)
        assert not any(m is my_a for m in messages)

    def test_not_found_removes_nothing(self) -> None:
        """本轮消息不在列表里（找不到）→一条都不删。"""
        old_u = _msg(MessageRole.USER, "旧对话")
        old_a = _msg(MessageRole.ASSISTANT, "旧回复")
        messages = [old_u, old_a]
        before_ids = {id(m) for m in messages}

        removed = _undo_generation_pair(
            messages, before_ids, "本轮输入",
            _expected_memory_tag("甲", "1", True),
        )
        assert removed == 0
        assert len(messages) == 2

    def test_assistant_tag_mismatch_keeps_reply(self) -> None:
        """新增回复的来源标记对不上本轮（是别人的）→只删用户消息不删回复。"""
        messages: list[Message] = []
        before_ids: set[int] = set()

        my_u = _msg(MessageRole.USER, "本轮输入")
        other_a = _msg(MessageRole.ASSISTANT, "[回复 群9 丙] 别人的回复")
        messages.extend([my_u, other_a])

        removed = _undo_generation_pair(
            messages, before_ids, "本轮输入",
            _expected_memory_tag("甲", "1", True),
        )
        assert removed == 1
        assert any(m is other_a for m in messages)
        assert not any(m is my_u for m in messages)

    def test_expected_tag_rules(self) -> None:
        """来源标记前缀与 ChatAgent._tag_response 规则一致。"""
        assert _expected_memory_tag("甲", "123", True) == "[回复 群123 甲] "
        assert _expected_memory_tag("甲", "", False) == "[回复 甲] "
        assert _expected_memory_tag("", "", False) == ""  # 无名私聊无标记


# ================================================================
# 4. QZone Cookie过期提醒的每日节流
# ================================================================

class TestCookieNoticeThrottle:
    """同一天只提醒一次，跨天恢复。"""

    def test_first_notice_allowed(self, tmp_path) -> None:
        monitor = QzoneMonitor(data_dir=str(tmp_path))
        base = time.mktime((2026, 7, 2, 10, 0, 0, 0, 0, -1))
        assert monitor._should_push_cookie_notice(now=base) is True

    def test_same_day_second_notice_blocked(self, tmp_path) -> None:
        monitor = QzoneMonitor(data_dir=str(tmp_path))
        base = time.mktime((2026, 7, 2, 10, 0, 0, 0, 0, -1))
        assert monitor._should_push_cookie_notice(now=base) is True
        # 同一天再来（哪怕过了几小时）→拦截
        assert monitor._should_push_cookie_notice(now=base + 3600 * 5) is False

    def test_next_day_notice_allowed_again(self, tmp_path) -> None:
        monitor = QzoneMonitor(data_dir=str(tmp_path))
        base = time.mktime((2026, 7, 2, 23, 0, 0, 0, 0, -1))
        assert monitor._should_push_cookie_notice(now=base) is True
        # 第二天→重新放行
        assert monitor._should_push_cookie_notice(now=base + 86400) is True
