"""
white_salary/core/memory/enhanced/temporal.py

时间衰减+周期记忆 — 生日/纪念日自动提醒，旧记忆自然衰减。

借鉴v2的设计：
  - TemporalDecay: 半衰期指数衰减（half_life_days=7）
  - PeriodicMemory: YEARLY/MONTHLY/WEEKLY/DAILY 四种周期事件
  - days_until_next(): 计算下次触发的天数
  - 接入AutoChat: 临近的周期事件触发主动聊天

配置从 config/memory_settings.json 的 temporal 节读取。

自动发现：导出MODULE供MemoryManager加载。
"""

import json
import math
import re
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Optional

from loguru import logger
from white_salary.core.memory.module_base import MemoryModule


# ================================================================
# 周期类型
# ================================================================

class PeriodType(str, Enum):
    YEARLY = "yearly"       # 每年（生日、纪念日）
    MONTHLY = "monthly"     # 每月
    WEEKLY = "weekly"       # 每周
    DAILY = "daily"         # 每天
    ONCE = "once"           # 一次性（特定日期提醒）


# ================================================================
# 数据结构
# ================================================================

@dataclass
class PeriodicEvent:
    """周期性事件。"""
    event_id: str = ""
    name: str = ""                      # 事件名称（"小白的生日"）
    description: str = ""               # 描述（"记得给小白准备礼物"）
    period_type: str = "yearly"         # 周期类型
    month: int = 0                      # 月份（1-12，yearly/once用）
    day: int = 0                        # 日期（1-31）
    weekday: int = 0                    # 星期几（0=周一，weekly用）
    hour: int = 9                       # 提醒时间（小时）
    user_id: str = ""                   # 关联用户
    importance: int = 5                 # 重要度1-10
    created_at: float = 0.0
    last_triggered: float = 0.0         # 上次触发时间
    enabled: bool = True


# 日期提取正则
_DATE_PATTERNS = [
    # "生日是6月16日" / "生日6月16号"
    (r"生日(?:是)?(?:在)?(\d{1,2})月(\d{1,2})[日号]", "birthday"),
    # "纪念日是3月14日"
    (r"纪念日(?:是)?(?:在)?(\d{1,2})月(\d{1,2})[日号]", "anniversary"),
    # "每年X月X日"
    (r"每年(\d{1,2})月(\d{1,2})[日号]", "yearly_event"),
    # "每月X日/号"
    (r"每月(\d{1,2})[日号]", "monthly_event"),
    # "每周X" (一到日)
    (r"每(?:个)?(?:星期|周)([一二三四五六日天])", "weekly_event"),
]

_WEEKDAY_MAP = {
    "一": 0, "二": 1, "三": 2, "四": 3,
    "五": 4, "六": 5, "日": 6, "天": 6,
}


class TemporalEngine:
    """
    时间衰减+周期记忆引擎。

    使用方式:
        engine = TemporalEngine(config, data_dir)
        engine.add_periodic_event("birthday_xiaobai", "小白的生日",
                                  period_type="yearly", month=6, day=16)
        upcoming = engine.get_upcoming_events(days=7)
        weight = engine.calculate_decay(created_at, last_accessed)
    """

    def __init__(self, config: dict = None, data_dir: str = "data/memory") -> None:
        cfg = config or {}
        self._half_life_days = cfg.get("half_life_days", 7.0)
        self._min_weight = cfg.get("min_weight", 0.1)
        self._periodic_enabled = cfg.get("periodic_check_enabled", True)

        self._data_path = Path(data_dir) / "periodic_events.json"
        self._data_path.parent.mkdir(parents=True, exist_ok=True)
        self._events: dict[str, PeriodicEvent] = {}
        self._load()

        # 编译日期正则
        self._date_patterns = [
            (re.compile(p), event_type) for p, event_type in _DATE_PATTERNS
        ]

    # ================================================================
    # 时间衰减
    # ================================================================

    def calculate_decay(self, created_at: float, last_accessed: float = 0.0) -> float:
        """
        计算时间衰减权重。

        公式: weight = 2^(-days / half_life)
        以last_accessed为基准（如果有的话），否则用created_at。

        Returns:
            0.0 ~ 1.0 的衰减权重
        """
        now = time.time()
        base_time = last_accessed if last_accessed > 0 else created_at
        days = (now - base_time) / 86400

        if days <= 0:
            return 1.0

        weight = math.pow(2, -days / self._half_life_days)
        return max(weight, self._min_weight)

    # ================================================================
    # 周期事件管理
    # ================================================================

    def add_periodic_event(self, event_id: str, name: str,
                           description: str = "",
                           period_type: str = "yearly",
                           month: int = 0, day: int = 0,
                           weekday: int = 0, hour: int = 9,
                           user_id: str = "",
                           importance: int = 5) -> PeriodicEvent:
        """添加周期性事件。"""
        event = PeriodicEvent(
            event_id=event_id,
            name=name,
            description=description,
            period_type=period_type,
            month=month,
            day=day,
            weekday=weekday,
            hour=hour,
            user_id=user_id,
            importance=importance,
            created_at=time.time(),
        )
        self._events[event_id] = event
        self._save()
        logger.debug(f"[Temporal] 添加周期事件: {name} ({period_type})")
        return event

    def remove_event(self, event_id: str) -> bool:
        """删除事件。"""
        if event_id in self._events:
            del self._events[event_id]
            self._save()
            return True
        return False

    def get_event(self, event_id: str) -> Optional[PeriodicEvent]:
        """获取事件。"""
        return self._events.get(event_id)

    def list_events(self) -> list[PeriodicEvent]:
        """列出所有事件。"""
        return list(self._events.values())

    # ================================================================
    # 时间计算
    # ================================================================

    def days_until_next(self, event: PeriodicEvent) -> int:
        """
        计算距离下次触发还有几天。

        Returns:
            天数（0=今天，负数=已过期的once事件）
        """
        now = datetime.now()
        today = now.date()

        if event.period_type == PeriodType.YEARLY:
            # 今年的日期
            try:
                this_year = today.replace(month=event.month, day=event.day)
            except ValueError:
                return 365  # 无效日期
            if this_year < today:
                # 今年已过，算明年
                try:
                    next_date = this_year.replace(year=today.year + 1)
                except ValueError:
                    next_date = this_year + timedelta(days=365)
                return (next_date - today).days
            return (this_year - today).days

        elif event.period_type == PeriodType.MONTHLY:
            # 这个月的日期
            try:
                this_month = today.replace(day=event.day)
            except ValueError:
                return 30  # 无效日期（如2月30日）
            if this_month < today:
                # 下个月
                if today.month == 12:
                    next_date = today.replace(year=today.year + 1, month=1, day=event.day)
                else:
                    try:
                        next_date = today.replace(month=today.month + 1, day=event.day)
                    except ValueError:
                        return 30
                return (next_date - today).days
            return (this_month - today).days

        elif event.period_type == PeriodType.WEEKLY:
            current_weekday = today.weekday()  # 0=周一
            target = event.weekday
            days_ahead = target - current_weekday
            if days_ahead < 0:
                days_ahead += 7
            return days_ahead

        elif event.period_type == PeriodType.DAILY:
            return 0

        elif event.period_type == PeriodType.ONCE:
            try:
                target = today.replace(month=event.month, day=event.day)
            except ValueError:
                return -1
            return (target - today).days

        return 365

    def get_upcoming_events(self, days: int = 7) -> list[tuple[PeriodicEvent, int]]:
        """
        获取未来N天内的事件。

        Returns:
            [(event, days_until), ...] 按天数升序
        """
        upcoming = []
        for event in self._events.values():
            if not event.enabled:
                continue
            d = self.days_until_next(event)
            if 0 <= d <= days:
                upcoming.append((event, d))
        upcoming.sort(key=lambda x: x[1])
        return upcoming

    def get_today_events(self) -> list[PeriodicEvent]:
        """获取今天的事件。"""
        return [event for event, days in self.get_upcoming_events(days=0)]

    def mark_triggered(self, event_id: str) -> None:
        """标记事件已触发。"""
        if event_id in self._events:
            self._events[event_id].last_triggered = time.time()
            self._save()

    def should_trigger(self, event: PeriodicEvent) -> bool:
        """检查事件是否应该触发（今天+未触发过）。"""
        if not event.enabled:
            return False
        if self.days_until_next(event) != 0:
            return False
        # 检查今天是否已触发
        if event.last_triggered > 0:
            last_date = datetime.fromtimestamp(event.last_triggered).date()
            if last_date == datetime.now().date():
                return False
        # 检查是否到了提醒时间
        if datetime.now().hour < event.hour:
            return False
        return True

    # ================================================================
    # 从对话自动检测周期事件
    # ================================================================

    def detect_from_text(self, text: str, user_id: str = "") -> list[PeriodicEvent]:
        """
        从文本中自动检测周期性事件。

        Examples:
            "我的生日是6月16日" → yearly event
            "每周三要开会" → weekly event
        """
        detected = []

        for pattern, event_type in self._date_patterns:
            match = pattern.search(text)
            if not match:
                continue

            groups = match.groups()

            if event_type == "birthday":
                month, day = int(groups[0]), int(groups[1])
                eid = f"birthday_{user_id or 'default'}"
                if eid not in self._events:
                    evt = self.add_periodic_event(
                        event_id=eid,
                        name=f"{'用户' if not user_id else user_id}的生日",
                        description=f"生日: {month}月{day}日",
                        period_type="yearly",
                        month=month, day=day,
                        user_id=user_id,
                        importance=9,
                    )
                    detected.append(evt)

            elif event_type == "anniversary":
                month, day = int(groups[0]), int(groups[1])
                eid = f"anniversary_{user_id or 'default'}_{month}_{day}"
                if eid not in self._events:
                    evt = self.add_periodic_event(
                        event_id=eid,
                        name="纪念日",
                        description=f"纪念日: {month}月{day}日",
                        period_type="yearly",
                        month=month, day=day,
                        user_id=user_id,
                        importance=8,
                    )
                    detected.append(evt)

            elif event_type == "yearly_event":
                month, day = int(groups[0]), int(groups[1])
                # 提取事件名（正则前面的文字）
                prefix = text[:match.start()].strip()
                name = prefix[-10:] if prefix else "年度事件"
                eid = f"yearly_{month}_{day}_{hash(name) % 10000}"
                if eid not in self._events:
                    evt = self.add_periodic_event(
                        event_id=eid, name=name,
                        period_type="yearly",
                        month=month, day=day,
                        user_id=user_id,
                        importance=5,
                    )
                    detected.append(evt)

            elif event_type == "monthly_event":
                day = int(groups[0])
                prefix = text[:match.start()].strip()
                name = prefix[-10:] if prefix else "月度事件"
                eid = f"monthly_{day}_{hash(name) % 10000}"
                if eid not in self._events:
                    evt = self.add_periodic_event(
                        event_id=eid, name=name,
                        period_type="monthly",
                        day=day,
                        user_id=user_id,
                        importance=4,
                    )
                    detected.append(evt)

            elif event_type == "weekly_event":
                weekday_char = groups[0]
                weekday = _WEEKDAY_MAP.get(weekday_char, 0)
                prefix = text[:match.start()].strip()
                name = prefix[-10:] if prefix else "每周事件"
                eid = f"weekly_{weekday}_{hash(name) % 10000}"
                if eid not in self._events:
                    evt = self.add_periodic_event(
                        event_id=eid, name=name,
                        period_type="weekly",
                        weekday=weekday,
                        user_id=user_id,
                        importance=3,
                    )
                    detected.append(evt)

        return detected

    # ================================================================
    # 生成提醒文本（注入到system prompt或触发auto_chat）
    # ================================================================

    def get_reminder_prompt(self) -> str:
        """
        生成即将到来的事件提醒，注入到对话上下文。

        Returns:
            提醒文本（为空则无提醒）
        """
        # 今天的事件
        today_events = self.get_today_events()
        # 未来3天的事件
        upcoming = self.get_upcoming_events(days=3)

        lines = []

        for event in today_events:
            if event.importance >= 7:
                lines.append(f"🎂 今天是{event.name}！{event.description}")
            else:
                lines.append(f"📅 今天: {event.name}")

        for event, days in upcoming:
            if days == 0:
                continue  # 已在today_events中
            if days == 1:
                lines.append(f"📅 明天是{event.name}")
            elif days <= 3:
                lines.append(f"📅 {days}天后是{event.name}")

        if not lines:
            return ""
        return "[周期事件提醒]\n" + "\n".join(lines)

    def get_auto_chat_hint(self) -> Optional[str]:
        """
        检查是否有需要触发主动聊天的事件。

        Returns:
            触发提示（None则无需触发）
        """
        for event in self._events.values():
            if self.should_trigger(event):
                self.mark_triggered(event.event_id)
                if event.importance >= 7:
                    return (
                        f"今天是{event.name}！{event.description} "
                        f"主动跟用户提起这件事，表达你的关心和祝福。"
                    )
                else:
                    return f"今天有一个事件：{event.name}。可以跟用户提一下。"
        return None

    # ================================================================
    # 统计
    # ================================================================

    @property
    def stats(self) -> dict:
        """统计信息。"""
        type_counts = {}
        for e in self._events.values():
            type_counts[e.period_type] = type_counts.get(e.period_type, 0) + 1
        upcoming_7d = len(self.get_upcoming_events(days=7))
        return {
            "total_events": len(self._events),
            "type_distribution": type_counts,
            "upcoming_7_days": upcoming_7d,
            "half_life_days": self._half_life_days,
        }

    # ================================================================
    # 持久化
    # ================================================================

    def _save(self) -> None:
        try:
            data = {eid: asdict(e) for eid, e in self._events.items()}
            self._data_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            logger.warning(f"[Temporal] 保存失败: {e}")

    def _load(self) -> None:
        if not self._data_path.exists():
            return
        try:
            data = json.loads(self._data_path.read_text(encoding="utf-8"))
            for eid, edata in data.items():
                self._events[eid] = PeriodicEvent(**edata)
            logger.debug(f"[Temporal] 加载完成: {len(self._events)}个周期事件")
        except Exception as e:
            logger.warning(f"[Temporal] 加载失败: {e}")


# ================================================================
# 自动发现接口
# ================================================================

class TemporalModule(MemoryModule):
    """时间衰减+周期记忆模块 — 自动发现注册。"""
    name = "temporal_engine"

    def init(self, data_dir="data/memory", **kwargs):
        config = {}
        try:
            cfg_path = Path("config/memory_settings.json")
            if cfg_path.exists():
                all_cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
                config = all_cfg.get("temporal", {})
        except Exception:
            pass
        self._impl = TemporalEngine(config=config, data_dir=data_dir)

    def get_context_prompt(self, message: str = "") -> str:
        """注入周期事件提醒到对话上下文。"""
        if not hasattr(self, '_impl'):
            return ""
        return self._impl.get_reminder_prompt()

    def on_message(self, user_msg: str = "", ai_reply: str = "") -> None:
        """每次对话后检测是否包含周期性事件。"""
        if not user_msg or not hasattr(self, '_impl') or len(user_msg) < 5:
            return
        detected = self._impl.detect_from_text(user_msg)
        if detected:
            names = [e.name for e in detected]
            logger.info(f"[Temporal] 自动检测到周期事件: {names}")


MODULE = TemporalModule
