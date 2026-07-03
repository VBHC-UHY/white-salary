"""
white_salary/core/memory/condition_engine.py

条件引擎 — 通用的好感度/状态条件判断系统。

借鉴v2的features/condition_engine.py（427行）：
  - 支持多种条件运算符（>=, <=, ==, !=, contains, in等）
  - 可以检查好感度、情绪、时间、角色等多种条件
  - 用于触发特定行为或功能

不用LLM，纯逻辑。

自动发现：导出MODULE供MemoryManager加载。
"""

import time
from datetime import datetime
from enum import Enum
from typing import Any, Optional

from loguru import logger
from white_salary.core.memory.module_base import MemoryModule


class Operator(str, Enum):
    EQ = "eq"           # ==
    NEQ = "neq"         # !=
    GT = "gt"           # >
    GTE = "gte"         # >=
    LT = "lt"           # <
    LTE = "lte"         # <=
    CONTAINS = "contains"
    IN = "in"
    NOT_IN = "not_in"


class Condition:
    """一个条件。"""

    def __init__(self, field: str, operator: str, value: Any) -> None:
        self.field = field
        self.operator = operator
        self.value = value

    def evaluate(self, context: dict) -> bool:
        """在给定上下文中评估条件。"""
        actual = context.get(self.field)
        if actual is None:
            return False

        op = self.operator
        if op == Operator.EQ:
            return actual == self.value
        elif op == Operator.NEQ:
            return actual != self.value
        elif op == Operator.GT:
            return actual > self.value
        elif op == Operator.GTE:
            return actual >= self.value
        elif op == Operator.LT:
            return actual < self.value
        elif op == Operator.LTE:
            return actual <= self.value
        elif op == Operator.CONTAINS:
            return self.value in str(actual)
        elif op == Operator.IN:
            return actual in self.value
        elif op == Operator.NOT_IN:
            return actual not in self.value
        return False

    def __repr__(self) -> str:
        return f"Condition({self.field} {self.operator} {self.value})"


class ConditionEngine:
    """
    条件引擎。

    使用方式:
        engine = ConditionEngine()
        ctx = engine.build_context(user_id="123")
        if engine.check(Condition("affinity_level", "gte", 3), ctx):
            # 好感度>=3时执行
    """

    def build_context(self, user_id: str = "desktop") -> dict:
        """
        构建当前上下文（好感度+情绪+时间等）。
        """
        ctx = {
            "user_id": user_id,
            "hour": datetime.now().hour,
            "weekday": datetime.now().weekday(),
            "timestamp": time.time(),
        }

        # 好感度
        try:
            from white_salary.core.affinity.manager import AffinityManager
            aff = AffinityManager.get_for_user(user_id)
            stats = aff.get_stats()
            ctx["affinity_points"] = stats.get("points", 0)
            ctx["affinity_level"] = stats.get("level_value", 0)
            ctx["is_family"] = stats.get("is_family", False)
            ctx["consecutive_days"] = stats.get("consecutive_days", 0)
        except Exception:
            ctx["affinity_points"] = 0
            ctx["affinity_level"] = 0
            ctx["is_family"] = False

        # 情绪（使用缓存实例避免重复创建）
        # 2026-07-03 审计修复（批5）：EmotionTracker() 现返回进程级共享实例
        # （按data_dir缓存），与主 manager 的情绪状态天然一致
        try:
            from white_salary.core.memory.emotion_tracker import EmotionTracker
            if not hasattr(self, '_emotion_tracker'):
                self._emotion_tracker = EmotionTracker()
            ctx["emotion"] = self._emotion_tracker.current_emotion
            ctx["mood_score"] = self._emotion_tracker.mood_score
        except Exception:
            ctx["emotion"] = "neutral"
            ctx["mood_score"] = 80

        # 全局状态（使用缓存实例）
        try:
            from white_salary.core.memory.global_state import GlobalStateManager
            if not hasattr(self, '_global_state'):
                self._global_state = GlobalStateManager()
            ctx["energy"] = self._global_state.state.energy_level
            ctx["is_angry"] = self._global_state.state.is_angry
            ctx["is_resting"] = self._global_state.state.is_resting
        except Exception:
            pass

        return ctx

    def check(self, condition: Condition, context: dict = None) -> bool:
        """检查单个条件。"""
        if context is None:
            context = self.build_context()
        return condition.evaluate(context)

    def check_all(self, conditions: list[Condition], context: dict = None) -> bool:
        """检查所有条件（AND）。"""
        if context is None:
            context = self.build_context()
        return all(c.evaluate(context) for c in conditions)

    def check_any(self, conditions: list[Condition], context: dict = None) -> bool:
        """检查任一条件（OR）。"""
        if context is None:
            context = self.build_context()
        return any(c.evaluate(context) for c in conditions)


# ================================================================
# 自动发现接口
# ================================================================

class ConditionEngineModule(MemoryModule):
    """条件引擎模块 — 自动发现注册。"""
    name = "condition_engine"

    def init(self, data_dir="data/memory", **kwargs):
        self._impl = ConditionEngine()


MODULE = ConditionEngineModule
