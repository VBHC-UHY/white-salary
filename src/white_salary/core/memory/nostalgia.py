"""
white_salary/core/memory/nostalgia.py

怀旧系统 — 主动回忆美好记忆并分享。

借鉴v2的features/nostalgia_system.py：
  - 从正面情感记忆池中选择美好回忆
  - 话题相关/时间相近/随机怀旧三种触发
  - 注入上下文让AI自然提起"想起之前..."
  - 冷却+去重，同一记忆不重复怀旧
  - 不用LLM，从已有记忆数据中选择

自动发现：导出MODULE供MemoryManager加载。
"""

import random
import time
from typing import Optional

from loguru import logger
from white_salary.core.memory.module_base import MemoryModule


# 怀旧触发的话题关键词
_NOSTALGIA_TRIGGERS = [
    "以前", "那时候", "记得吗", "想起", "回忆", "小时候", "当年",
    "曾经", "之前", "上次", "老朋友", "好久", "怀念", "时光",
]

# 怀旧引导词（注入prompt时用）
_NOSTALGIA_INTROS = [
    "说起这个，想起之前",
    "突然想起",
    "这让我想到之前",
    "说到这个，记得有一次",
    "忽然想起以前",
]


class NostalgiaEngine:
    """
    怀旧引擎。

    使用方式:
        engine = NostalgiaEngine()
        hint = engine.maybe_recall(message, available_memories)
    """

    def __init__(
        self,
        cooldown_seconds: int = 1800,    # 30分钟冷却
        random_chance: float = 0.05,      # 5%随机怀旧概率
        max_per_session: int = 3,         # 每会话最多3次
    ) -> None:
        self._cooldown = cooldown_seconds
        self._random_chance = random_chance
        self._max_per_session = max_per_session

        self._last_recall_time = 0.0
        self._session_count = 0
        self._recalled_ids: set[str] = set()  # 已怀旧过的记忆ID

    def maybe_recall(
        self,
        message: str,
        positive_memories: list[dict] = None,
    ) -> Optional[str]:
        """
        检查是否应该触发怀旧。

        Args:
            message: 当前用户消息
            positive_memories: 正面记忆列表
                每条: {"id": str, "content": str, "emotion": str, "time": float}

        Returns:
            怀旧提示文本（None则不触发）
        """
        if not positive_memories:
            return None

        # 会话限制
        if self._session_count >= self._max_per_session:
            return None

        # 冷却检查
        if time.time() - self._last_recall_time < self._cooldown:
            return None

        # 触发条件判断
        triggered = False
        trigger_reason = ""

        # 1. 话题相关触发
        for kw in _NOSTALGIA_TRIGGERS:
            if kw in message:
                triggered = True
                trigger_reason = "topic"
                break

        # 2. 随机触发
        if not triggered and random.random() < self._random_chance:
            triggered = True
            trigger_reason = "random"

        if not triggered:
            return None

        # 选择一条未用过的美好记忆
        candidates = [
            m for m in positive_memories
            if m.get("id", "") not in self._recalled_ids
        ]
        if not candidates:
            # 全部用过了，重置
            self._recalled_ids.clear()
            candidates = positive_memories

        # 优先选择情感强烈的
        candidates.sort(
            key=lambda m: m.get("intensity", 0.5),
            reverse=True,
        )
        memory = candidates[0]

        # 标记已使用
        mem_id = memory.get("id", str(id(memory)))
        self._recalled_ids.add(mem_id)
        self._last_recall_time = time.time()
        self._session_count += 1

        # 生成怀旧提示
        intro = random.choice(_NOSTALGIA_INTROS)
        content = memory.get("content", "")[:100]
        return f"[怀旧] {intro}「{content}」"

    def reset_session(self) -> None:
        """重置会话计数（新会话开始时）。"""
        self._session_count = 0

    @property
    def stats(self) -> dict:
        return {
            "session_count": self._session_count,
            "recalled_count": len(self._recalled_ids),
            "max_per_session": self._max_per_session,
        }


# ================================================================
# 自动发现接口
# ================================================================

class NostalgiaModule(MemoryModule):
    """怀旧系统模块 — 自动发现注册。"""
    name = "nostalgia"

    def init(self, data_dir="data/memory", **kwargs):
        self._impl = NostalgiaEngine()

    def get_context_prompt(self, message: str = "") -> str:
        """检查是否触发怀旧，返回提示。"""
        if not message or not hasattr(self, '_impl'):
            return ""

        # 尝试从emotion_trigger获取正面记忆
        positive_memories = self._get_positive_memories()
        if not positive_memories:
            return ""

        result = self._impl.maybe_recall(message, positive_memories)
        return result or ""

    def _get_positive_memories(self) -> list[dict]:
        """从情感记忆标签中获取正面记忆。"""
        try:
            from white_salary.core.memory.enhanced.emotional import EmotionalMemory
            em = EmotionalMemory()
            tags = em.get_positive_memories(limit=20)
            return [
                {
                    "id": t.memory_id,
                    "content": t.context_hint or t.memory_id,
                    "emotion": t.emotion_type,
                    "intensity": t.intensity,
                    "time": t.tagged_at,
                }
                for t in tags
            ]
        except Exception:
            return []

    def on_session_start(self) -> None:
        if hasattr(self, '_impl'):
            self._impl.reset_session()


MODULE = NostalgiaModule
