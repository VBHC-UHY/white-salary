"""
测试群聊回复决策 smart_reply.py。

覆盖：私聊直接回、@机器人、唤醒词、@别人忽略、冷群交给语义判断、
纯表情忽略、活跃窗口内紧接回复、连续没人理闭嘴、频率限制 OBSERVE。
"""

import time
from dataclasses import dataclass

from white_salary.core.smart_reply import (
    SmartReplyDecider,
    ReplyDecision,
    contains_wake_word,
)
from white_salary.core.runtime.engagement import EngagementLeaseBook, EngagementState


@dataclass
class _Msg:
    """模拟 QQ 消息对象（decide 只用到这几个字段）。"""
    is_group: bool = True
    group_id: str = "g1"
    user_id: str = "u1"
    raw_message: str = ""
    is_at_me: bool = False


class TestSmartReplyHardRules:
    """第一档：必回。"""

    def test_private_always_reply(self) -> None:
        d = SmartReplyDecider()
        r = d.decide(_Msg(is_group=False))
        assert r.decision == ReplyDecision.REPLY

    def test_at_me_reply(self) -> None:
        d = SmartReplyDecider()
        r = d.decide(_Msg(is_at_me=True, raw_message="在吗"))
        assert r.decision == ReplyDecision.REPLY

    def test_wakeword_reply(self) -> None:
        d = SmartReplyDecider()
        r = d.decide(_Msg(raw_message="白 在吗"))
        assert r.decision == ReplyDecision.REPLY

    def test_wakeword_punctuation_variants(self) -> None:
        for text in ["白，", "，白", "白", "白？", "白！", " 白", "白  "]:
            assert contains_wake_word(text, ["白"])
        assert not contains_wake_word("白白在吗", ["白"])

    def test_configurable_wakeword(self) -> None:
        d = SmartReplyDecider(wake_words=["问白"], bot_name="")
        r = d.decide(_Msg(raw_message="问白 这个怎么弄"))
        assert r.decision == ReplyDecision.REPLY


class TestSmartReplyIgnore:
    """第三档：明确不回。"""

    def test_at_others_ignored(self) -> None:
        d = SmartReplyDecider()
        r = d.decide(_Msg(raw_message="[CQ:at,qq=999] 你们好啊"))
        assert r.decision == ReplyDecision.IGNORE

    def test_cold_group_goes_to_semantic_check(self) -> None:
        d = SmartReplyDecider()  # 全新，群从未活跃
        r = d.decide(_Msg(raw_message="今天天气不错呀大家"))
        assert r.decision == ReplyDecision.SEMANTIC_CHECK
        assert "语义判断" in r.reason

    def test_manual_unblocked_group_bypasses_inactive_gate(self) -> None:
        d = SmartReplyDecider(unblocked_group_ids=["g1"])
        r = d.decide(_Msg(group_id="g1", user_id="u2", raw_message="今天天气不错呀大家"))
        assert r.decision == ReplyDecision.SEMANTIC_CHECK
        assert "不屏蔽" in r.reason

    def test_manual_unblocked_group_can_be_changed_runtime(self) -> None:
        d = SmartReplyDecider()
        assert not d.is_group_unblocked("g1")
        d.set_group_unblocked("g1", True)
        assert d.is_group_unblocked("g1")
        assert d.list_unblocked_groups() == ["g1"]
        d.set_group_unblocked("g1", False)
        assert not d.is_group_unblocked("g1")

    def test_pure_emoji_ignored(self) -> None:
        d = SmartReplyDecider()
        r = d.decide(_Msg(raw_message="[CQ:face,id=1]"))
        assert r.decision == ReplyDecision.IGNORE


class TestSmartReplyActiveWindow:
    """第二档：活跃状态判断。"""

    def test_followup_after_bot_reply(self) -> None:
        """白刚回了这个人，对方紧接着说话 → 交给语义续聊判断。"""
        d = SmartReplyDecider()
        d.record_reply("g1", "u1")  # 白回复 u1 → 群活跃 + 记录回了谁
        r = d.decide(_Msg(group_id="g1", user_id="u1", raw_message="那你觉得呢"))
        assert r.decision == ReplyDecision.SEMANTIC_CHECK

    def test_active_media_needs_semantic_check(self) -> None:
        d = SmartReplyDecider()
        d.record_reply("g1", "u1")
        msg = _Msg(group_id="g1", user_id="u1", raw_message="[CQ:image,file=1.jpg]")
        msg.has_media = True
        r = d.decide(msg)
        assert r.decision == ReplyDecision.SEMANTIC_CHECK

    def test_silence_makes_bot_shut_up(self) -> None:
        """连续没人理（ignored_count 到阈值）→ 闭嘴。"""
        d = SmartReplyDecider()
        d._group_last_reply["g1"] = time.time()       # 群活跃
        d._ignored_count["g1"] = SmartReplyDecider.MAX_IGNORED_REPLIES
        r = d.decide(_Msg(group_id="g1", user_id="u2", raw_message="随便说句话啊啊"))
        assert r.decision == ReplyDecision.IGNORE
        assert "没人理" in r.reason

    def test_frequency_limit_observe(self) -> None:
        """活跃状态但本分钟已回 3 条 → OBSERVE 不回。"""
        d = SmartReplyDecider()
        now = time.time()
        d._group_last_reply["g1"] = now
        d._reply_timestamps["g1"] = [now, now, now]   # 已达上限
        r = d.decide(_Msg(group_id="g1", user_id="u1", raw_message="再说一句吧大家"))
        assert r.decision == ReplyDecision.OBSERVE
        assert "频率" in r.reason


class TestPersistentPerUserEngagement:
    def test_one_users_window_never_activates_another_user(self, tmp_path) -> None:
        leases = EngagementLeaseBook(tmp_path / "runtime.db")
        decider = SmartReplyDecider(engagement_leases=leases)

        wake = decider.decide(_Msg(group_id="g1", user_id="u1", raw_message="白，在吗"))
        same_user = decider.decide(
            _Msg(group_id="g1", user_id="u1", raw_message="我接着说刚才的事")
        )
        other_user = decider.decide(
            _Msg(group_id="g1", user_id="u2", raw_message="我在和别人聊天")
        )

        assert wake.decision == ReplyDecision.REPLY
        assert same_user.decision == ReplyDecision.SEMANTIC_CHECK
        assert other_user.decision == ReplyDecision.OBSERVE

    def test_delivery_promotes_pending_lease_to_engaged(self, tmp_path) -> None:
        leases = EngagementLeaseBook(tmp_path / "runtime.db")
        decider = SmartReplyDecider(engagement_leases=leases)
        decider.decide(_Msg(group_id="g1", user_id="u1", raw_message="白"))

        before = leases.get("qq:group:g1", "u1")
        decider.record_reply("g1", "u1")
        after = leases.get("qq:group:g1", "u1")

        assert before is not None and before.state == EngagementState.PENDING
        assert after is not None and after.state == EngagementState.ENGAGED

    def test_unrelated_messages_cool_only_that_users_lease(self, tmp_path) -> None:
        leases = EngagementLeaseBook(tmp_path / "runtime.db")
        decider = SmartReplyDecider(engagement_leases=leases)
        for user_id in ("u1", "u2"):
            decider.decide(_Msg(group_id="g1", user_id=user_id, raw_message="白"))
            decider.record_reply("g1", user_id)

        decider.record_unrelated_message("g1", "u1")
        decider.record_unrelated_message("g1", "u1")

        first = leases.get("qq:group:g1", "u1")
        second = leases.get("qq:group:g1", "u2")
        assert first is not None and first.state == EngagementState.COOLING
        assert second is not None and second.state == EngagementState.ENGAGED
