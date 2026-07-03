"""
white_salary/core/memory/emotion_trigger.py

情感触发记忆 — 当前情绪自动触发相关记忆回忆。

借鉴v2的emotion_trigger_memory.py：
  - 情感→记忆映射（开心→开心的记忆，难过→安慰/鼓励的记忆）
  - 触发强度（情绪越强触发越多记忆）
  - 冷却机制（同一情绪短时间内不重复触发）
  - 触发计数和历史

自动发现：导出MODULE供MemoryManager加载。
"""

import time
from dataclasses import dataclass, field
from typing import Optional

from loguru import logger
from white_salary.core.memory.module_base import MemoryModule


# 情感分组
EMOTION_GROUPS = {
    "positive": ["happy", "excited", "grateful", "touched", "playful", "proud"],
    "negative": ["sad", "angry", "anxious", "lonely", "frustrated", "hurt"],
    "neutral": ["calm", "neutral", "curious", "nostalgic"],
}

# 情感→应触发的记忆类型偏好
# 当用户某种情绪时，优先回忆哪类记忆
EMOTION_MEMORY_MAP = {
    # 正面情绪：回忆更多开心的事，强化正面感受
    "happy": {"prefer": ["emotion", "event"], "keywords": ["开心", "快乐", "好玩", "有趣"]},
    "excited": {"prefer": ["event", "promise"], "keywords": ["期待", "兴奋", "成功", "太好了"]},
    "grateful": {"prefer": ["person", "event"], "keywords": ["感谢", "感动", "帮助", "谢谢"]},
    "touched": {"prefer": ["emotion", "person"], "keywords": ["感动", "温暖", "暖心"]},
    "playful": {"prefer": ["event", "emotion"], "keywords": ["搞笑", "有趣", "好玩"]},
    "proud": {"prefer": ["event", "knowledge"], "keywords": ["成功", "通过", "赢", "第一"]},

    # 负面情绪：回忆安慰/鼓励/承诺，帮助缓解
    "sad": {"prefer": ["emotion", "promise"], "keywords": ["没关系", "会好的", "加油", "陪你"]},
    "angry": {"prefer": ["promise", "person"], "keywords": ["答应", "约定", "道歉"]},
    "anxious": {"prefer": ["promise", "knowledge"], "keywords": ["放心", "没问题", "可以的"]},
    "lonely": {"prefer": ["person", "emotion"], "keywords": ["朋友", "陪", "一起", "想你"]},
    "frustrated": {"prefer": ["emotion", "event"], "keywords": ["加油", "坚持", "相信"]},
    "hurt": {"prefer": ["emotion", "secret"], "keywords": ["在乎", "重要", "保护"]},

    # 中性情绪
    "calm": {"prefer": ["knowledge", "event"], "keywords": []},
    "neutral": {"prefer": ["event", "knowledge"], "keywords": []},
    "curious": {"prefer": ["knowledge", "event"], "keywords": ["怎么", "为什么", "是什么"]},
    "nostalgic": {"prefer": ["event", "person", "emotion"], "keywords": ["以前", "那时候", "记得"]},
}

# 触发强度映射（情绪强度→召回数量）
INTENSITY_TO_COUNT = {
    0.2: 1,   # 轻微情绪→1条
    0.4: 2,   # 中等情绪→2条
    0.6: 3,   # 较强情绪→3条
    0.8: 4,   # 强烈情绪→4条
    1.0: 5,   # 极强情绪→5条
}


@dataclass
class TriggerRecord:
    """触发记录。"""
    emotion: str = ""
    trigger_count: int = 0
    last_triggered: float = 0.0


class EmotionTriggerMemory:
    """
    情感触发记忆引擎。

    使用方式:
        trigger = EmotionTriggerMemory()
        memories = trigger.get_triggered_memories("happy", intensity=0.7, available_memories=[...])
    """

    def __init__(self, cooldown_seconds: int = 300) -> None:
        self._cooldown = cooldown_seconds  # 同一情绪的冷却时间（秒）
        self._records: dict[str, TriggerRecord] = {}

    def get_triggered_memories(
        self,
        emotion: str,
        intensity: float = 0.5,
        available_memories: list[dict] = None,
    ) -> list[dict]:
        """
        根据当前情绪触发相关记忆。

        Args:
            emotion: 当前情绪（happy/sad/angry等）
            intensity: 情绪强度 0-1
            available_memories: 可用的记忆列表
                每条: {"content": str, "category": str, "time": float, ...}

        Returns:
            触发的记忆列表
        """
        if not emotion or not available_memories:
            return []

        # 检查冷却
        if not self._check_cooldown(emotion):
            return []

        # 获取触发配置
        config = EMOTION_MEMORY_MAP.get(emotion, {})
        prefer_categories = config.get("prefer", [])
        trigger_keywords = config.get("keywords", [])

        # 确定触发数量
        count = self._intensity_to_count(intensity)
        if count == 0:
            return []

        # 评分每条记忆
        scored = []
        for mem in available_memories:
            score = self._score_memory(mem, prefer_categories, trigger_keywords)
            if score > 0:
                scored.append((mem, score))

        # 按分数排序
        scored.sort(key=lambda x: x[1], reverse=True)

        # 取top N
        triggered = [mem for mem, _ in scored[:count]]

        # 记录触发
        if triggered:
            self._record_trigger(emotion)

        return triggered

    def get_memory_categories_for_emotion(self, emotion: str) -> list[str]:
        """获取某种情绪偏好的记忆类型。"""
        config = EMOTION_MEMORY_MAP.get(emotion, {})
        return config.get("prefer", [])

    def get_emotion_group(self, emotion: str) -> str:
        """获取情绪所属分组（positive/negative/neutral）。"""
        for group, emotions in EMOTION_GROUPS.items():
            if emotion in emotions:
                return group
        return "neutral"

    @property
    def stats(self) -> dict:
        return {
            "total_triggers": sum(r.trigger_count for r in self._records.values()),
            "trigger_history": {
                e: {"count": r.trigger_count, "last": r.last_triggered}
                for e, r in self._records.items()
            },
        }

    # ================================================================
    # 内部方法
    # ================================================================

    def _check_cooldown(self, emotion: str) -> bool:
        """检查情绪是否在冷却中。"""
        record = self._records.get(emotion)
        if not record:
            return True
        return time.time() - record.last_triggered >= self._cooldown

    def _record_trigger(self, emotion: str) -> None:
        """记录触发。"""
        if emotion not in self._records:
            self._records[emotion] = TriggerRecord(emotion=emotion)
        self._records[emotion].trigger_count += 1
        self._records[emotion].last_triggered = time.time()

    def _intensity_to_count(self, intensity: float) -> int:
        """情绪强度→触发数量。"""
        count = 0
        for threshold, n in sorted(INTENSITY_TO_COUNT.items()):
            if intensity >= threshold:
                count = n
        return count

    def _score_memory(
        self,
        memory: dict,
        prefer_categories: list[str],
        trigger_keywords: list[str],
    ) -> float:
        """评分一条记忆的相关度。"""
        score = 0.0
        content = memory.get("content", "")
        category = memory.get("category", "")

        # 类型偏好加分
        if category in prefer_categories:
            idx = prefer_categories.index(category)
            score += 2.0 - idx * 0.5  # 越靠前加分越多

        # 关键词匹配加分
        for kw in trigger_keywords:
            if kw in content:
                score += 1.0

        # 重要度加分
        importance = memory.get("importance", 5)
        score += importance * 0.1

        return score


# ================================================================
# 自动发现接口
# ================================================================

class EmotionTriggerModule(MemoryModule):
    """情感触发记忆模块 — 自动发现注册。"""
    name = "emotion_trigger"

    def init(self, data_dir="data/memory", **kwargs):
        self._impl = EmotionTriggerMemory(cooldown_seconds=300)

    def get_context_prompt(self, message: str = "") -> str:
        """暂不在此注入（由emotion_tracker触发时调用）。"""
        return ""


MODULE = EmotionTriggerModule
