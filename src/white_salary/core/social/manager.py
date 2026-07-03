"""
white_salary/core/social/manager.py

社交系统 — 包含10个社交功能模块，全部纯逻辑不调LLM。

借鉴v2的social_intelligence/social_fatigue/social_pattern/auto_friend/
auto_group/auto_notify/group_unreplied_detector/user_cooldown/user_filter。

所有功能整合到一个SocialManager中，避免文件碎片化。
"""

import re
import time
from dataclasses import dataclass, field
from typing import Optional

from loguru import logger


# ================================================================
# 1. UserCooldown — 用户冷却（防刷屏）
# ================================================================

class UserCooldown:
    """每个用户的消息频率限制。"""

    def __init__(self, cooldown_seconds: int = 3, burst_limit: int = 5, burst_window: int = 10) -> None:
        self._cooldown = cooldown_seconds
        self._burst_limit = burst_limit
        self._burst_window = burst_window
        self._last_msg: dict[str, float] = {}        # user_id -> last_msg_time
        self._burst_count: dict[str, list[float]] = {}  # user_id -> recent timestamps

    def check(self, user_id: str) -> bool:
        """检查用户是否可以发消息。True=允许，False=冷却中。"""
        now = time.time()

        # 好感度调整冷却时间
        effective_cooldown = self._get_affinity_cooldown(user_id)

        # 基本冷却
        last = self._last_msg.get(user_id, 0)
        if now - last < effective_cooldown:
            return False

        # 突发检测
        times = self._burst_count.setdefault(user_id, [])
        times.append(now)
        times[:] = [t for t in times if now - t < self._burst_window]
        if len(times) > self._burst_limit:
            logger.debug(f"[Cooldown] {user_id} 刷屏 ({len(times)}条/{self._burst_window}秒)")
            return False

        self._last_msg[user_id] = now
        return True

    def _get_affinity_cooldown(self, user_id: str) -> float:
        """根据好感度调整冷却时间。家人/好友更短，陌生人/反感更长。"""
        try:
            from white_salary.core.affinity.manager import AffinityManager
            aff = AffinityManager.get_for_user(user_id)
            stats = aff.get_stats()
            level_value = stats.get("level_value", 0)

            if stats.get("is_family") or level_value >= 5:
                return 0  # 家人/挚友：无冷却
            elif level_value >= 3:
                return self._cooldown * 0.5  # 好朋友：冷却减半
            elif level_value >= 1:
                return self._cooldown  # 认识的人：正常冷却
            elif level_value <= -2:
                return self._cooldown * 3  # 反感的人：冷却加倍
            else:
                return self._cooldown
        except Exception:
            return self._cooldown


# ================================================================
# 2. UserFilter — 用户过滤（黑名单）
# ================================================================

class UserFilter:
    """用户黑名单管理。"""

    def __init__(self) -> None:
        self._blocked: set[str] = set()
        self._muted_until: dict[str, float] = {}  # user_id -> mute_until

    def block(self, user_id: str) -> None:
        self._blocked.add(user_id)
        logger.info(f"[Filter] 已屏蔽: {user_id}")

    def unblock(self, user_id: str) -> None:
        self._blocked.discard(user_id)

    def mute(self, user_id: str, minutes: int = 10) -> None:
        self._muted_until[user_id] = time.time() + minutes * 60
        logger.info(f"[Filter] 已禁言 {user_id} {minutes}分钟")

    def is_blocked(self, user_id: str) -> bool:
        if user_id in self._blocked:
            return True
        mute_end = self._muted_until.get(user_id, 0)
        if mute_end > time.time():
            return True
        return False


# ================================================================
# 3. SocialFatigue — 社交疲劳
# ================================================================

class SocialFatigue:
    """社交能量系统 — 聊太久会累。"""

    MAX_ENERGY = 100.0
    RECOVERY_PER_MINUTE = 0.5   # 每分钟恢复0.5
    COST_PER_MESSAGE = 1.0      # 每条消息消耗1点
    COST_PER_GROUP = 0.5        # 群聊消耗更少

    def __init__(self) -> None:
        self._energy = self.MAX_ENERGY
        self._last_update = time.time()

    @property
    def energy(self) -> float:
        self._recover()
        return self._energy

    @property
    def is_tired(self) -> bool:
        return self.energy < 20

    def consume(self, is_group: bool = False) -> None:
        self._recover()
        cost = self.COST_PER_GROUP if is_group else self.COST_PER_MESSAGE
        self._energy = max(0, self._energy - cost)

    def _recover(self) -> None:
        now = time.time()
        minutes = (now - self._last_update) / 60
        self._energy = min(self.MAX_ENERGY, self._energy + minutes * self.RECOVERY_PER_MINUTE)
        self._last_update = now


# ================================================================
# 4. SocialPattern — 社交模式识别
# ================================================================

class SocialPattern:
    """识别用户的社交模式（活跃时段、聊天频率等）。"""

    def __init__(self) -> None:
        self._user_hours: dict[str, list[int]] = {}  # user_id -> [hour, hour, ...]

    def record(self, user_id: str) -> None:
        from datetime import datetime
        hour = datetime.now().hour
        hours = self._user_hours.setdefault(user_id, [])
        hours.append(hour)
        if len(hours) > 200:
            hours[:] = hours[-200:]

    def get_active_hours(self, user_id: str) -> list[int]:
        """获取用户最活跃的时段。"""
        hours = self._user_hours.get(user_id, [])
        if not hours:
            return []
        from collections import Counter
        c = Counter(hours)
        return [h for h, _ in c.most_common(3)]

    def is_unusual_time(self, user_id: str) -> bool:
        """用户是否在不常见的时段发消息。"""
        from datetime import datetime
        hour = datetime.now().hour
        active = self.get_active_hours(user_id)
        if not active:
            return False
        return hour not in active


# ================================================================
# 5. SocialIntelligence — 社交智能（分析社交信号）
# ================================================================

# 正面社交信号
_POSITIVE_SIGNALS = ["谢谢", "感谢", "好的", "明白", "懂了", "辛苦", "厉害", "可以"]
# 负面社交信号
_NEGATIVE_SIGNALS = ["算了", "不用了", "别说了", "烦", "无语", "呵呵"]
# 求助信号
_HELP_SIGNALS = ["帮我", "怎么办", "怎么弄", "救命", "急", "能不能"]


class SocialIntelligence:
    """分析消息中的社交信号。"""

    @staticmethod
    def analyze(text: str) -> dict:
        """返回 {signal_type: str, confidence: float}"""
        for kw in _HELP_SIGNALS:
            if kw in text:
                return {"signal": "help", "confidence": 0.8}
        for kw in _NEGATIVE_SIGNALS:
            if kw in text:
                return {"signal": "negative", "confidence": 0.7}
        for kw in _POSITIVE_SIGNALS:
            if kw in text:
                return {"signal": "positive", "confidence": 0.6}
        return {"signal": "neutral", "confidence": 0.5}


# ================================================================
# 6. AutoFriend — 自动处理好友请求
# ================================================================

class AutoFriend:
    """自动处理QQ好友请求（基于规则）。"""

    def __init__(self, auto_accept: bool = False, whitelist: Optional[list[str]] = None) -> None:
        self._auto_accept = auto_accept
        self._whitelist = set(whitelist or [])

    def should_accept(self, user_id: str, message: str = "") -> bool:
        """根据白名单+好感度决定是否接受好友请求。"""
        if user_id in self._whitelist:
            return True
        if self._auto_accept:
            return True

        # 好感度判断：认识的人(>=15分)自动接受
        try:
            from white_salary.core.affinity.manager import AffinityManager
            aff = AffinityManager.get_for_user(user_id)
            stats = aff.get_stats()
            points = stats.get("points", 0)
            if points >= 40:   # 朋友级别→自动接受
                logger.info(f"[AutoFriend] {user_id} 好感度{points:.0f}分→自动接受")
                return True
            elif points <= -20:  # 反感→自动拒绝
                logger.info(f"[AutoFriend] {user_id} 好感度{points:.0f}分→自动拒绝")
                return False
        except Exception:
            pass

        return False


# ================================================================
# 7. AutoGroup — 自动处理群邀请
# ================================================================

class AutoGroup:
    """自动处理QQ群邀请。"""

    def __init__(self, auto_accept: bool = False, whitelist_groups: Optional[list[str]] = None) -> None:
        self._auto_accept = auto_accept
        self._whitelist = set(whitelist_groups or [])

    def should_accept(self, group_id: str, inviter_id: str = "") -> bool:
        """根据白名单+邀请人好感度决定是否接受群邀请。"""
        if group_id in self._whitelist:
            return True
        if self._auto_accept:
            return True

        # 邀请人好感度判断
        if inviter_id:
            try:
                from white_salary.core.affinity.manager import AffinityManager
                aff = AffinityManager.get_for_user(inviter_id)
                stats = aff.get_stats()
                points = stats.get("points", 0)
                if stats.get("is_family") or points >= 40:
                    logger.info(f"[AutoGroup] 邀请人{inviter_id}好感度{points:.0f}→接受群邀请")
                    return True
            except Exception:
                pass

        return False


# ================================================================
# 8. AutoNotify — 自动通知主人
# ================================================================

# 需要通知主人的关键词
_NOTIFY_KEYWORDS = ["找你", "叫你", "老板", "主人", "管理员", "紧急", "出问题", "崩了", "挂了"]


class AutoNotify:
    """检测是否需要通知主人。"""

    def __init__(self, owner_ids: Optional[list[str]] = None) -> None:
        self._owner_ids = set(owner_ids or [])
        self._last_notify = 0.0
        self._cooldown = 300  # 5分钟内不重复通知

    def should_notify(self, user_id: str, text: str) -> bool:
        """检查是否需要通知主人。"""
        if user_id in self._owner_ids:
            return False  # 主人自己说的不通知

        now = time.time()
        if now - self._last_notify < self._cooldown:
            return False

        for kw in _NOTIFY_KEYWORDS:
            if kw in text:
                self._last_notify = now
                logger.info(f"[AutoNotify] 触发通知: {kw} (from {user_id})")
                return True
        return False


# ================================================================
# 9. GroupUnrepliedDetector — 未回复消息检测
# ================================================================

class GroupUnrepliedDetector:
    """检测群里@了机器人但没被回复的消息。"""

    def __init__(self) -> None:
        self._pending: dict[str, dict] = {}  # msg_id -> {group_id, user_id, text, time}

    def record_mention(self, msg_id: str, group_id: str, user_id: str, text: str) -> None:
        self._pending[msg_id] = {
            "group_id": group_id, "user_id": user_id,
            "text": text[:50], "time": time.time(),
        }
        # 清理超过10分钟的
        cutoff = time.time() - 600
        self._pending = {k: v for k, v in self._pending.items() if v["time"] > cutoff}

    def mark_replied(self, group_id: str) -> None:
        """标记某群已回复（清除该群的pending）。"""
        self._pending = {k: v for k, v in self._pending.items() if v["group_id"] != group_id}

    def get_unreplied(self) -> list[dict]:
        """获取超过2分钟未回复的消息。"""
        cutoff = time.time() - 120
        return [v for v in self._pending.values() if v["time"] < cutoff]


# ================================================================
# 统一管理器
# ================================================================

class SocialManager:
    """
    社交系统统一管理器 — 整合所有社交功能。

    使用方式:
        social = SocialManager(owner_ids=["1234567890"])
        # 每条消息调用
        if social.should_process(user_id, text, is_group):
            social.on_message(user_id, text, is_group)
    """

    def __init__(self, owner_ids: Optional[list[str]] = None) -> None:
        self.cooldown = UserCooldown()
        self.filter = UserFilter()
        self.fatigue = SocialFatigue()
        self.pattern = SocialPattern()
        self.intelligence = SocialIntelligence()
        self.auto_friend = AutoFriend()
        self.auto_group = AutoGroup()
        self.auto_notify = AutoNotify(owner_ids=owner_ids)
        self.unreplied = GroupUnrepliedDetector()

    def should_process(self, user_id: str, text: str = "", is_group: bool = False) -> bool:
        """综合判断是否处理这条消息。"""
        if self.filter.is_blocked(user_id):
            return False
        if not self.cooldown.check(user_id):
            return False
        return True

    def on_message(self, user_id: str, text: str = "", is_group: bool = False) -> None:
        """记录消息（更新各系统状态）。"""
        self.pattern.record(user_id)
        self.fatigue.consume(is_group)
