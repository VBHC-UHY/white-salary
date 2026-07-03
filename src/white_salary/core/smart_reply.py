"""
white_salary/core/smart_reply.py

智能回复决策 — QQ群聊中判断是否应该回复。

三档回复规则：
  第一档（必回）：@白 / 回复白的消息 / 唤醒词
  第二档（活跃状态内回）：白刚回了这个人且紧接着说话 / 明确对白说的
  第三档（不回）：@别人 / 回复别人 / 纯表情 / 群聊太快

活跃状态管理：
  - 白回复后开启5分钟活跃（只有白回复才刷新）
  - 追踪"白最近在跟谁聊"（用户级别）
  - 频率限制：活跃状态内每分钟最多回3条
  - 连续3条白回了但没人理 → 自动退出活跃
  - 上下文是群级别共享的（白能看到所有人说的话）
"""

import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from loguru import logger


class ReplyDecision(Enum):
    REPLY = "reply"
    OBSERVE = "observe"
    IGNORE = "ignore"


@dataclass
class DecisionResult:
    decision: ReplyDecision
    score: float
    reason: str


# 唤醒词正则
_NAME_ONLY_PATTERN = re.compile(r"^\s*[，,]?\s*白\s*[，,]?\s*$")
_NAME_IN_TEXT_PATTERN = re.compile(r"(?:^|(?<=[\s，,]))白(?=[\s，,]|$)")


class SmartReplyDecider:
    """
    智能回复决策器 — 支持对话延续。

    使用方式:
        decider = SmartReplyDecider(bot_self_id="2997735486", bot_name="白")
        result = decider.decide(msg)
        if result.decision == ReplyDecision.REPLY:
            # 回复
        decider.record_reply(group_id, user_id)  # 回复后记录
    """

    ACTIVE_WINDOW = 300.0          # 活跃窗口5分钟
    FOLLOWUP_WINDOW = 60.0         # 紧接着白回复的窗口60秒（用户需要时间看回复+打字）
    MAX_REPLIES_PER_MINUTE = 3     # 活跃状态每分钟最多回3条
    MAX_IGNORED_REPLIES = 3        # 连续3条没人理就闭嘴

    def __init__(
        self,
        bot_self_id: str = "",
        bot_name: str = "白",
        owner_ids: Optional[list[str]] = None,
    ) -> None:
        self._bot_id = bot_self_id
        self._bot_name = bot_name
        self._owner_ids = set(owner_ids or [])

        # 群级别状态
        self._group_msg_times: dict[str, list[float]] = {}

        # 用户级别活跃追踪 — {group_id: {user_id: last_reply_time}}
        self._active_users: dict[str, dict[str, float]] = {}

        # 白最近回复追踪 — {group_id: {user_id: last_bot_reply_time}}
        self._bot_replied_to: dict[str, dict[str, float]] = {}

        # 白在群里的最后回复时间 — {group_id: time}
        self._group_last_reply: dict[str, float] = {}

        # 频率限制 — {group_id: [reply_timestamps]}
        self._reply_timestamps: dict[str, list[float]] = {}

        # 连续没人理计数 — {group_id: count}
        self._ignored_count: dict[str, int] = {}

        # 白最后回复的用户 — {group_id: user_id}
        self._last_replied_user: dict[str, str] = {}

        # 白最后回复时间 — {group_id: time}（用于判断是否是"同一轮"）
        self._last_reply_time: dict[str, float] = {}

    def decide(self, msg) -> DecisionResult:
        """判断是否应该回复这条群聊消息。"""
        if not msg.is_group:
            return DecisionResult(ReplyDecision.REPLY, 100.0, "私聊直接回复")

        gid = msg.group_id
        uid = msg.user_id
        now = time.time()
        raw = msg.raw_message
        raw_clean = re.sub(r"\[CQ:[^\]]+\]", "", raw)

        # ============================================================
        # 第一档：硬规则（必回）
        # ============================================================

        # @机器人
        if msg.is_at_me:
            self._on_user_triggered(gid, uid)
            return DecisionResult(ReplyDecision.REPLY, 90.0, "@了机器人")

        # 回复白的消息（CQ:reply检测）
        # （QQ消息的is_at_me已经覆盖了大部分情况）

        # 唤醒词
        if _NAME_ONLY_PATTERN.match(raw_clean) or _NAME_IN_TEXT_PATTERN.search(raw_clean):
            self._on_user_triggered(gid, uid)
            return DecisionResult(ReplyDecision.REPLY, 85.0, "唤醒词")

        # ============================================================
        # 第三档检查（先排除明确不回的）
        # ============================================================

        # @了别人
        if "[CQ:at," in raw:
            return DecisionResult(ReplyDecision.IGNORE, 0.0, "@别人")

        # 纯表情/图片/转发
        if not raw_clean.strip() or len(raw_clean.strip()) < 2:
            return DecisionResult(ReplyDecision.IGNORE, 0.0, "纯表情/空消息")

        # 群消息太密集（>10条/分钟）
        g_times = self._group_msg_times.setdefault(gid, [])
        g_times.append(now)
        g_times[:] = [t for t in g_times if now - t < 60]
        if len(g_times) > 10:
            return DecisionResult(ReplyDecision.IGNORE, 0.0, "消息太密集")

        # ============================================================
        # 第二档：活跃状态判断
        # ============================================================

        # 检查群活跃状态
        group_last = self._group_last_reply.get(gid, 0)
        is_group_active = (now - group_last) < self.ACTIVE_WINDOW

        if not is_group_active:
            # 群不活跃，需要唤醒词
            return DecisionResult(ReplyDecision.IGNORE, 0.0, "群不活跃，需唤醒")

        # 连续没人理 → 闭嘴（优先于频率限制检查）
        if self._ignored_count.get(gid, 0) >= self.MAX_IGNORED_REPLIES:
            return DecisionResult(ReplyDecision.IGNORE, 0.0, "连续没人理，闭嘴")

        # 频率限制
        reply_ts = self._reply_timestamps.setdefault(gid, [])
        reply_ts[:] = [t for t in reply_ts if now - t < 60]
        if len(reply_ts) >= self.MAX_REPLIES_PER_MINUTE:
            return DecisionResult(ReplyDecision.OBSERVE, 10.0, "频率限制(3条/分钟)")

        # ---- 判断这条消息是不是对白说的 ----

        score = 0.0
        reasons = []

        # 白刚回了这个人（30秒内），这个人接着说话 → 大概率是接着聊
        user_last_replied = self._bot_replied_to.get(gid, {}).get(uid, 0)
        if (now - user_last_replied) < self.FOLLOWUP_WINDOW:
            score += 35.0
            reasons.append("紧接白的回复")

        # 白最近回了这个人（5分钟内）
        elif (now - user_last_replied) < self.ACTIVE_WINDOW:
            score += 15.0
            reasons.append("活跃对话中")

        # 消息包含"你"且白刚说过话（可能在问白）
        if "你" in raw_clean and (now - group_last) < 60:
            score += 10.0
            reasons.append("可能在问白")

        # 有问号（可能在问白）
        if re.search(r"[？?]", raw):
            score += 5.0
            reasons.append("有问号")

        # 是主人
        if uid in self._owner_ids:
            score += 10.0
            reasons.append("主人")

        # 决策
        score = max(0, min(100, score))

        if score >= 30:
            return DecisionResult(ReplyDecision.REPLY, score, ", ".join(reasons))
        elif score >= 10:
            # OBSERVE不回复，但记录
            logger.debug(f"[SmartReply] observe: {', '.join(reasons)} ({score:.0f}分)")
            return DecisionResult(ReplyDecision.OBSERVE, score, ", ".join(reasons))
        else:
            return DecisionResult(ReplyDecision.IGNORE, score, "活跃状态但不是对白说的")

    def record_reply(self, group_id: str, user_id: str = "") -> None:
        """白回复了某个用户后调用。"""
        now = time.time()

        # 刷新群活跃状态
        self._group_last_reply[group_id] = now

        # 记录白回了谁
        if user_id:
            self._bot_replied_to.setdefault(group_id, {})[user_id] = now

        # 频率限制：同一轮对话（5秒内的多条回复）只算1次
        last_time = self._last_reply_time.get(group_id, 0)
        if now - last_time > 5.0:
            # 新的一轮对话
            self._reply_timestamps.setdefault(group_id, []).append(now)

            # "没人理"计数：只在新一轮时+1（不是每条消息+1）
            prev_user = self._last_replied_user.get(group_id)
            if not prev_user or prev_user == user_id:
                # 连续回同一个人（或第一次回），等对方回应
                self._ignored_count[group_id] = self._ignored_count.get(group_id, 0) + 1
            else:
                # 换人了，重置计数
                self._ignored_count[group_id] = 0

        self._last_reply_time[group_id] = now
        if user_id:
            self._last_replied_user[group_id] = user_id

    def record_user_response(self, group_id: str, user_id: str) -> None:
        """
        有人在白回复后说话了（不管是不是对白说的）。
        用于重置"没人理"计数。
        """
        last_user = self._last_replied_user.get(group_id)
        if last_user and user_id == last_user:
            # 白回的那个人回应了 → 重置计数
            self._ignored_count[group_id] = 0

    def _on_user_triggered(self, group_id: str, user_id: str) -> None:
        """用户通过唤醒词/@触发了白。"""
        now = time.time()
        self._group_last_reply[group_id] = now  # 标记群即将活跃
        self._active_users.setdefault(group_id, {})[user_id] = now
        self._ignored_count[group_id] = 0  # 重置
