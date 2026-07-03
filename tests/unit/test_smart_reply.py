"""
测试群聊回复决策 smart_reply.py。

覆盖：私聊直接回、@机器人、唤醒词、@别人忽略、群不活跃忽略、
纯表情忽略、活跃窗口内紧接回复、连续没人理闭嘴、频率限制 OBSERVE。
"""

import time
from dataclasses import dataclass

from white_salary.core.smart_reply import SmartReplyDecider, ReplyDecision


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


class TestSmartReplyIgnore:
    """第三档：明确不回。"""

    def test_at_others_ignored(self) -> None:
        d = SmartReplyDecider()
        r = d.decide(_Msg(raw_message="[CQ:at,qq=999] 你们好啊"))
        assert r.decision == ReplyDecision.IGNORE

    def test_group_inactive_ignored(self) -> None:
        d = SmartReplyDecider()  # 全新，群从未活跃
        r = d.decide(_Msg(raw_message="今天天气不错呀大家"))
        assert r.decision == ReplyDecision.IGNORE
        assert "不活跃" in r.reason

    def test_pure_emoji_ignored(self) -> None:
        d = SmartReplyDecider()
        r = d.decide(_Msg(raw_message="[CQ:face,id=1]"))
        assert r.decision == ReplyDecision.IGNORE


class TestSmartReplyActiveWindow:
    """第二档：活跃状态判断。"""

    def test_followup_after_bot_reply(self) -> None:
        """白刚回了这个人，对方紧接着说话 → 回复。"""
        d = SmartReplyDecider()
        d.record_reply("g1", "u1")  # 白回复 u1 → 群活跃 + 记录回了谁
        r = d.decide(_Msg(group_id="g1", user_id="u1", raw_message="那你觉得呢"))
        assert r.decision == ReplyDecision.REPLY

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
