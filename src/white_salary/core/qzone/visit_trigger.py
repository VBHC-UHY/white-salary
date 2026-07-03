"""
white_salary/core/qzone/visit_trigger.py

逛空间触发器 — 聊天积累兴趣，达标后触发逛空间。

借鉴v2的visit_trigger.py：
  - 每次聊天积累兴趣值（正面+5%/普通+2%/负面-3%）
  - 兴趣达到70%阈值触发逛空间
  - 逛完重置兴趣到0，24小时内不再逛同一人
  - 30分钟不聊天兴趣衰减
  - 0.5%随机触发概率
  - 每小时3次、每日10次上限

重写适配我们的架构：
  - 纯同步（触发判断由social_manager异步执行）
  - JSON持久化
  - 单例模式
"""

import json
import threading
import time
from pathlib import Path
from typing import Optional

from loguru import logger


# 触发参数
INTEREST_THRESHOLD = 0.7      # 70%兴趣触发逛空间
INTEREST_NORMAL = 0.02         # 普通消息+2%
INTEREST_POSITIVE = 0.05       # 积极消息+5%
INTEREST_NEGATIVE = -0.03      # 负面消息-3%
INTEREST_DECAY_TIME = 1800     # 30分钟无聊天开始衰减
INTEREST_DECAY_RATE = 0.01     # 每次检查衰减1%

HOURLY_LIMIT = 3               # 每小时最多逛3次
DAILY_LIMIT = 10               # 每日最多逛10次
USER_COOLDOWN = 24 * 3600      # 同一用户24小时冷却
GLOBAL_COOLDOWN = 600          # 全局600秒冷却
RANDOM_TRIGGER_PROB = 0.005    # 0.5%随机触发


class VisitTrigger:
    """
    逛空间触发器。

    使用方式:
        trigger = VisitTrigger()
        # 收到聊天消息时
        should_visit = trigger.record_interaction("123456", "积极消息", quality="positive")
        if should_visit:
            # 执行逛空间
            trigger.record_visit("123456")
    """

    def __init__(self, data_dir: str = "data/qzone") -> None:
        self._path = Path(data_dir) / "visit_trigger.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()  # 线程安全

        # {uin: {"interest": float, "last_interaction": float, "last_visit": float}}
        self._users: dict[str, dict] = {}
        # 全局逛空间记录: [timestamp, ...]
        self._visit_times: list[float] = []
        self._last_visit_time: float = 0

        self._load()

    # ================================================================
    # 交互记录
    # ================================================================

    def record_interaction(
        self,
        uin: str,
        message: str = "",
        quality: str = "normal",
        interest_change: float | None = None,
    ) -> bool:
        """
        记录一次聊天交互，返回是否应该触发逛空间。

        Args:
            uin: 用户QQ号
            message: 消息内容（暂不用）
            quality: 消息质量 normal/positive/negative
            interest_change: 直接指定兴趣变化量（覆盖quality推算）

        Returns:
            True = 应该触发逛空间
        """
        if not uin:
            return False

        with self._lock:
            now = time.time()

            if uin not in self._users:
                self._users[uin] = {"interest": 0.0, "last_interaction": 0, "last_visit": 0}

            user = self._users[uin]

            # 衰减检查（30分钟没聊天，兴趣降低）
            last = user.get("last_interaction", 0)
            if last and now - last > INTEREST_DECAY_TIME:
                decay_rounds = int((now - last) / INTEREST_DECAY_TIME)
                user["interest"] = max(0, user["interest"] - INTEREST_DECAY_RATE * decay_rounds)

            # 更新兴趣
            if interest_change is not None:
                delta = interest_change
            elif quality == "positive":
                delta = INTEREST_POSITIVE
            elif quality == "negative":
                delta = INTEREST_NEGATIVE
            else:
                delta = INTEREST_NORMAL

            user["interest"] = max(0, min(1.0, user["interest"] + delta))
            user["last_interaction"] = now

            self._save()

        # 判断是否应该逛空间（读操作，锁外即可）
        return self.should_visit(uin)

    # ================================================================
    # 触发判断
    # ================================================================

    def should_visit(self, uin: str) -> bool:
        """判断是否应该逛某人空间。"""
        if not self.can_visit(uin):
            return False

        user = self._users.get(uin, {})
        interest = user.get("interest", 0)

        # 兴趣达标
        if interest >= INTEREST_THRESHOLD:
            return True

        # 随机触发（需要至少有一点兴趣）
        if interest > 0.1:
            import random
            if random.random() < RANDOM_TRIGGER_PROB:
                return True

        return False

    def can_visit(self, uin: str) -> bool:
        """检查是否允许逛某人空间（限流检查）。"""
        now = time.time()

        # 全局冷却
        if now - self._last_visit_time < GLOBAL_COOLDOWN:
            return False

        # 每用户24小时冷却
        user = self._users.get(uin, {})
        last_visit = user.get("last_visit", 0)
        if last_visit and now - last_visit < USER_COOLDOWN:
            return False

        # 小时限制
        hour_ago = now - 3600
        hour_count = sum(1 for t in self._visit_times if t > hour_ago)
        if hour_count >= HOURLY_LIMIT:
            return False

        # 每日限制
        day_start = now - (now % 86400)
        day_count = sum(1 for t in self._visit_times if t > day_start)
        if day_count >= DAILY_LIMIT:
            return False

        return True

    def record_visit(self, uin: str) -> None:
        """记录已经逛了某人空间（重置兴趣+更新冷却）。"""
        with self._lock:
            now = time.time()

            if uin in self._users:
                self._users[uin]["interest"] = 0
                self._users[uin]["last_visit"] = now

            self._visit_times.append(now)
            self._last_visit_time = now

            # 清理超过24小时的记录
            cutoff = now - 86400
            self._visit_times = [t for t in self._visit_times if t > cutoff]

            self._save()

    def get_visit_candidates(self, exclude_uins: set[str] | None = None) -> list[dict]:
        """获取值得逛空间的候选用户列表。"""
        exclude = exclude_uins or set()
        candidates = []
        for uin, user in self._users.items():
            if uin in exclude:
                continue
            interest = user.get("interest", 0)
            if interest > 0.3 and self.can_visit(uin):
                candidates.append({
                    "uin": uin,
                    "interest": round(interest, 3),
                })
        candidates.sort(key=lambda x: x["interest"], reverse=True)
        return candidates[:5]

    def get_interest(self, uin: str) -> float:
        """获取对某用户的当前兴趣值。"""
        return self._users.get(uin, {}).get("interest", 0)

    # ================================================================
    # 持久化
    # ================================================================

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self._users = data.get("users", {})
            self._visit_times = data.get("visit_times", [])
            self._last_visit_time = data.get("last_visit_time", 0)
            # 清理旧记录
            cutoff = time.time() - 86400
            self._visit_times = [t for t in self._visit_times if t > cutoff]
        except Exception as e:
            logger.debug(f"[QZone触发] 加载失败: {e}")

    def _save(self) -> None:
        try:
            data = {
                "users": self._users,
                "visit_times": self._visit_times,
                "last_visit_time": self._last_visit_time,
            }
            self._path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            logger.debug(f"[QZone触发] 保存失败: {e}")


# 全局单例
_instance: Optional[VisitTrigger] = None


def get_visit_trigger() -> VisitTrigger:
    """获取逛空间触发器单例。"""
    global _instance
    if _instance is None:
        _instance = VisitTrigger()
    return _instance
