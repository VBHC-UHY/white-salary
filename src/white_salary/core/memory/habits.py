"""
white_salary/core/memory/habits.py

习惯系统 — AI的固定小习惯（早安/晚安/关心等）。

借鉴v2的features/habits.py（452行）：
  - 记录AI的固定行为模式
  - 时间触发（早上问好、晚上提醒睡觉）
  - 事件触发（用户久没说话→关心）
  - 习惯可以随互动形成和消退
  - 不用LLM

自动发现：导出MODULE供MemoryManager加载。
"""

import json
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path

from loguru import logger
from white_salary.core.memory.module_base import MemoryModule


@dataclass
class Habit:
    """一个习惯。"""
    habit_id: str = ""
    name: str = ""
    trigger_type: str = ""      # time/event/interval
    trigger_value: str = ""     # 触发条件（"08:00"/"user_idle_30m"）
    action_hint: str = ""       # 触发时注入的提示
    strength: float = 0.5       # 习惯强度 0-1（越强越固定）
    times_triggered: int = 0
    last_triggered: float = 0.0
    enabled: bool = True


# 默认习惯
_DEFAULT_HABITS = [
    Habit(habit_id="morning_care", name="早上关心",
          trigger_type="time", trigger_value="08:00-09:00",
          action_hint="早上了，可以问问用户今天有什么安排",
          strength=0.7),
    Habit(habit_id="lunch_remind", name="午饭提醒",
          trigger_type="time", trigger_value="11:30-12:30",
          action_hint="中午了，提醒用户吃午饭",
          strength=0.5),
    Habit(habit_id="night_care", name="晚上关心",
          trigger_type="time", trigger_value="22:00-23:00",
          action_hint="很晚了，提醒用户早点休息不要熬夜",
          strength=0.6),
    Habit(habit_id="idle_care", name="久没说话关心",
          trigger_type="interval", trigger_value="3600",
          action_hint="用户很久没说话了，可以关心一下",
          strength=0.4),
]


class HabitSystem:
    """习惯系统。"""

    def __init__(self, data_dir: str = "data/memory") -> None:
        self._data_path = Path(data_dir) / "habits.json"
        self._data_path.parent.mkdir(parents=True, exist_ok=True)
        self._habits: dict[str, Habit] = {}
        self._load()
        # 确保默认习惯存在
        for h in _DEFAULT_HABITS:
            if h.habit_id not in self._habits:
                self._habits[h.habit_id] = h

    def check_triggers(self, last_user_active: float = 0) -> list[Habit]:
        """检查哪些习惯应该触发。"""
        now = datetime.now()
        current_hour = now.hour
        current_minute = now.minute
        current_time = f"{current_hour:02d}:{current_minute:02d}"
        triggered = []

        for habit in self._habits.values():
            if not habit.enabled:
                continue

            # 今天是否已触发
            if habit.last_triggered > 0:
                last_date = datetime.fromtimestamp(habit.last_triggered).date()
                if last_date == now.date() and habit.trigger_type == "time":
                    continue  # 今天已触发

            should_trigger = False

            if habit.trigger_type == "time":
                # 时间范围触发
                parts = habit.trigger_value.split("-")
                if len(parts) == 2:
                    start, end = parts
                    if start <= current_time <= end:
                        should_trigger = True

            elif habit.trigger_type == "interval":
                # 间隔触发
                interval = int(habit.trigger_value)
                if last_user_active > 0:
                    idle_seconds = time.time() - last_user_active
                    if idle_seconds >= interval:
                        # 间隔触发也有冷却
                        if time.time() - habit.last_triggered >= interval:
                            should_trigger = True

            if should_trigger:
                habit.times_triggered += 1
                habit.last_triggered = time.time()
                # 强度随触发次数提升
                habit.strength = min(1.0, habit.strength + 0.02)
                triggered.append(habit)

        if triggered:
            self._save()
        return triggered

    def add_habit(self, habit_id: str, name: str, trigger_type: str,
                  trigger_value: str, action_hint: str) -> Habit:
        """添加新习惯。"""
        h = Habit(
            habit_id=habit_id, name=name,
            trigger_type=trigger_type,
            trigger_value=trigger_value,
            action_hint=action_hint,
        )
        self._habits[habit_id] = h
        self._save()
        return h

    def remove_habit(self, habit_id: str) -> bool:
        if habit_id in self._habits:
            del self._habits[habit_id]
            self._save()
            return True
        return False

    def get_habits_prompt(self, last_user_active: float = 0) -> str:
        """检查并生成习惯提示。"""
        triggered = self.check_triggers(last_user_active)
        if not triggered:
            return ""
        lines = ["[习惯提醒]"]
        for h in triggered:
            lines.append(f"  - {h.action_hint}")
        return "\n".join(lines)

    def _save(self) -> None:
        try:
            data = {k: asdict(v) for k, v in self._habits.items()}
            self._data_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _load(self) -> None:
        if self._data_path.exists():
            try:
                data = json.loads(self._data_path.read_text(encoding="utf-8"))
                for k, d in data.items():
                    self._habits[k] = Habit(**d)
            except Exception:
                pass

    @property
    def stats(self) -> dict:
        return {
            "total_habits": len(self._habits),
            "enabled": sum(1 for h in self._habits.values() if h.enabled),
        }


class HabitsModule(MemoryModule):
    name = "habits"

    def init(self, data_dir="data/memory", **kwargs):
        self._impl = HabitSystem(data_dir=data_dir)

    def get_context_prompt(self, message: str = "") -> str:
        if not hasattr(self, '_impl'):
            return ""
        return self._impl.get_habits_prompt()


MODULE = HabitsModule
