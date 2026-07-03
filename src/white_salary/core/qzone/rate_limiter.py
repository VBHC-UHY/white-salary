"""
white_salary/core/qzone/rate_limiter.py

QQ空间操作频率控制器。

借鉴v2的rate_limiter.py：
  - 5种操作类型独立限流（visit/comment/post/at_user/reply）
  - 小时+每日双重限制
  - 最小冷却间隔
  - 动态冷却倍率（连续出错时加大间隔，成功时恢复）
  - at_user按用户独立计冷却

重写适配我们的架构：
  - 纯同步（供social_manager调用）
  - JSON持久化
  - 单例模式
"""

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from loguru import logger


@dataclass
class _OpLimit:
    """单种操作的限流配置。"""
    hourly: int        # 每小时上限
    daily: int         # 每日上限
    cooldown: float    # 最小间隔（秒）


# 5种操作的默认限流
_DEFAULT_LIMITS: dict[str, _OpLimit] = {
    "visit":   _OpLimit(hourly=3,  daily=10, cooldown=600),
    "comment": _OpLimit(hourly=5,  daily=20, cooldown=120),
    "post":    _OpLimit(hourly=3,  daily=10, cooldown=300),
    "at_user": _OpLimit(hourly=2,  daily=4,  cooldown=1800),
    "reply":   _OpLimit(hourly=10, daily=30, cooldown=60),
}


class QzoneRateLimiter:
    """
    QQ空间频率控制器。

    使用方式:
        limiter = QzoneRateLimiter()
        if limiter.can_do("comment"):
            # 执行评论
            limiter.record("comment")
        else:
            # 被限流，跳过
    """

    def __init__(self, data_dir: str = "data/qzone") -> None:
        self._path = Path(data_dir) / "rate_limiter.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()  # 线程安全

        # 操作记录: {op_type: [timestamp, ...]}
        self._records: dict[str, list[float]] = {}
        # at_user按用户独立冷却: {uin: last_at_time}
        self._at_user_times: dict[str, float] = {}
        # 动态冷却倍率（连续出错时增大）
        self._cooldown_multiplier: float = 1.0

        self._load()

    # ================================================================
    # 公开接口
    # ================================================================

    def can_do(self, op_type: str) -> bool:
        """检查某种操作是否可以执行。"""
        limit = _DEFAULT_LIMITS.get(op_type)
        if not limit:
            return True

        with self._lock:
            now = time.time()
            records = self._records.get(op_type, [])

            # 1. 冷却间隔
            if records:
                last = records[-1]
                effective_cd = limit.cooldown * self._cooldown_multiplier
                if now - last < effective_cd:
                    return False

            # 2. 小时限制
            hour_ago = now - 3600
            hour_count = sum(1 for t in records if t > hour_ago)
            if hour_count >= limit.hourly:
                return False

            # 3. 每日限制
            day_start = now - (now % 86400)  # UTC当日0点
            day_count = sum(1 for t in records if t > day_start)
            if day_count >= limit.daily:
                return False

            return True

    def can_at_user(self, uin: str) -> bool:
        """检查是否可以@某个用户（per-user冷却）。"""
        if not self.can_do("at_user"):
            return False
        with self._lock:
            last = self._at_user_times.get(uin, 0)
            cd = _DEFAULT_LIMITS["at_user"].cooldown * self._cooldown_multiplier
            return time.time() - last >= cd

    def record(self, op_type: str) -> None:
        """记录一次操作。"""
        with self._lock:
            now = time.time()
            if op_type not in self._records:
                self._records[op_type] = []
            self._records[op_type].append(now)
            # 只保留最近24小时的记录
            cutoff = now - 86400
            self._records[op_type] = [t for t in self._records[op_type] if t > cutoff]
            self._save()

    def record_at_user(self, uin: str) -> None:
        """记录@某个用户。"""
        with self._lock:
            self._at_user_times[uin] = time.time()
        self.record("at_user")

    def record_success(self) -> None:
        """操作成功，降低冷却倍率。"""
        with self._lock:
            self._cooldown_multiplier = max(1.0, self._cooldown_multiplier * 0.9)
            self._save()

    def record_error(self) -> None:
        """操作失败，提高冷却倍率（最高5倍）。"""
        with self._lock:
            self._cooldown_multiplier = min(5.0, self._cooldown_multiplier * 1.5)
            logger.debug(f"[QZone限流] 冷却倍率提高到 {self._cooldown_multiplier:.1f}x")
            self._save()

    def get_stats(self) -> dict:
        """获取当前统计。"""
        with self._lock:
            now = time.time()
            hour_ago = now - 3600
            day_start = now - (now % 86400)
            stats = {}
            for op, limit in _DEFAULT_LIMITS.items():
                records = self._records.get(op, [])
                stats[op] = {
                    "hour": sum(1 for t in records if t > hour_ago),
                    "hour_limit": limit.hourly,
                    "day": sum(1 for t in records if t > day_start),
                    "day_limit": limit.daily,
                }
            stats["cooldown_multiplier"] = round(self._cooldown_multiplier, 2)
            return stats

    # ================================================================
    # 持久化
    # ================================================================

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self._records = data.get("records", {})
            self._at_user_times = data.get("at_user_times", {})
            self._cooldown_multiplier = data.get("cooldown_multiplier", 1.0)
            # 清理超过24小时的旧记录
            cutoff = time.time() - 86400
            for op in list(self._records):
                self._records[op] = [t for t in self._records[op] if t > cutoff]
        except Exception as e:
            logger.debug(f"[QZone限流] 加载失败: {e}")

    def _save(self) -> None:
        try:
            data = {
                "records": self._records,
                "at_user_times": self._at_user_times,
                "cooldown_multiplier": self._cooldown_multiplier,
            }
            self._path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            logger.debug(f"[QZone限流] 保存失败: {e}")


# 全局单例
_instance: Optional[QzoneRateLimiter] = None


def get_rate_limiter() -> QzoneRateLimiter:
    """获取频率控制器单例。"""
    global _instance
    if _instance is None:
        _instance = QzoneRateLimiter()
    return _instance
