"""
white_salary/core/memory/enhanced/emotional.py

情感记忆标签 — 给每条记忆打情感标签(emotion_type+intensity)。

借鉴v2的enhanced/emotional.py：
  - EmotionalTag: 情感类型+强度+效价+上下文
  - 自动情感标注（关键词规则，不用LLM）
  - 按情感检索（查"所有开心的记忆"）
  - 情感统计（正面/负面/中性分布）
  - 接入integrator的on_memory_created

不同于emotion_tracker(追踪当前心情)和emotion_trigger(触发回忆)。
这个模块是给记忆数据本身打情感标签。

自动发现：导出MODULE供MemoryManager加载。
"""

import json
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional

from loguru import logger
from white_salary.core.memory.module_base import MemoryModule


# ================================================================
# 情感类型定义
# ================================================================

# 基础情感（Plutchik情感轮简化版）
EMOTION_TYPES = {
    "joy": "快乐",
    "sadness": "悲伤",
    "anger": "愤怒",
    "fear": "恐惧",
    "surprise": "惊讶",
    "disgust": "厌恶",
    "trust": "信任",
    "anticipation": "期待",
    "love": "爱",
    "gratitude": "感恩",
    "pride": "骄傲",
    "nostalgia": "怀旧",
    "neutral": "中性",
}

# 情感效价（正面/负面/中性）
EMOTION_VALENCE = {
    "joy": 1.0, "love": 1.0, "gratitude": 0.9, "pride": 0.8,
    "trust": 0.7, "anticipation": 0.6, "surprise": 0.3,
    "nostalgia": 0.4, "neutral": 0.0,
    "fear": -0.5, "sadness": -0.7, "disgust": -0.6, "anger": -0.8,
}

# 情感检测关键词
_EMOTION_KEYWORDS = {
    "joy": [
        "开心", "高兴", "快乐", "太好了", "哈哈", "嘻嘻", "好玩",
        "有趣", "棒", "厉害", "赞", "爽", "耶", "万岁",
    ],
    "sadness": [
        "难过", "伤心", "哭", "泪", "痛", "不开心", "心痛",
        "失望", "遗憾", "可惜", "唉", "emo", "崩溃", "破防",
    ],
    "anger": [
        "生气", "愤怒", "烦", "讨厌", "滚", "气死", "受不了",
        "火大", "怒", "恨", "可恶",
    ],
    "fear": [
        "害怕", "恐惧", "紧张", "担心", "焦虑", "慌",
        "吓", "怕", "不安",
    ],
    "surprise": [
        "惊讶", "天哪", "不会吧", "居然", "竟然", "没想到",
        "意外", "震惊", "what", "啊？",
    ],
    "disgust": [
        "恶心", "讨厌", "无语", "受不了", "烦死",
    ],
    "trust": [
        "相信", "信任", "靠谱", "放心", "依赖",
    ],
    "anticipation": [
        "期待", "等不及", "盼望", "希望", "想要", "计划",
    ],
    "love": [
        "爱", "喜欢", "想你", "亲爱", "宝贝", "心动",
        "暖心", "温暖", "感动",
    ],
    "gratitude": [
        "谢谢", "感谢", "多亏", "感恩", "太好了你",
    ],
    "pride": [
        "骄傲", "自豪", "成功", "考上", "通过", "第一",
        "最棒", "了不起",
    ],
    "nostalgia": [
        "以前", "那时候", "记得吗", "想起", "回忆",
        "小时候", "当年", "曾经",
    ],
}


@dataclass
class EmotionalTag:
    """一条记忆的情感标签。"""
    memory_id: str = ""
    emotion_type: str = "neutral"       # 情感类型
    intensity: float = 0.0              # 情感强度 0-1
    valence: float = 0.0               # 效价 -1(消极)~+1(积极)
    secondary_emotion: str = ""         # 次要情感（可选）
    context_hint: str = ""              # 情感上下文（"因为考试通过了"）
    tagged_at: float = 0.0


class EmotionalMemory:
    """
    情感记忆标签系统。

    使用方式:
        em = EmotionalMemory(data_dir="data/memory")
        tag = em.tag_memory("m1", "今天考试通过了太开心了")
        # → EmotionalTag(emotion_type="joy", intensity=0.8, valence=1.0)
        happy_memories = em.get_by_emotion("joy")
        positive = em.get_positive_memories()
    """

    def __init__(self, data_dir: str = "data/memory") -> None:
        self._data_path = Path(data_dir) / "enhanced" / "emotional_tags.json"
        self._data_path.parent.mkdir(parents=True, exist_ok=True)
        self._tags: dict[str, EmotionalTag] = {}
        self._load()

    # ================================================================
    # 标注
    # ================================================================

    def tag_memory(self, memory_id: str, content: str,
                   context_hint: str = "") -> EmotionalTag:
        """
        给一条记忆自动打情感标签。

        Args:
            memory_id: 记忆ID
            content: 记忆内容
            context_hint: 上下文提示（可选）

        Returns:
            EmotionalTag
        """
        # 关键词检测
        emotion_scores: dict[str, int] = {}
        for emo, keywords in _EMOTION_KEYWORDS.items():
            score = 0
            for kw in keywords:
                if kw in content:
                    score += 1
            if score > 0:
                emotion_scores[emo] = score

        # 选最高分
        if emotion_scores:
            primary = max(emotion_scores, key=emotion_scores.get)
            max_score = emotion_scores[primary]
            intensity = min(0.3 + max_score * 0.2, 1.0)

            # 次要情感
            secondary = ""
            sorted_emos = sorted(emotion_scores.items(), key=lambda x: x[1], reverse=True)
            if len(sorted_emos) > 1:
                secondary = sorted_emos[1][0]
        else:
            primary = "neutral"
            intensity = 0.0
            secondary = ""

        valence = EMOTION_VALENCE.get(primary, 0.0)

        tag = EmotionalTag(
            memory_id=memory_id,
            emotion_type=primary,
            intensity=intensity,
            valence=valence,
            secondary_emotion=secondary,
            context_hint=context_hint,
            tagged_at=time.time(),
        )
        self._tags[memory_id] = tag
        self._save_debounced()
        return tag

    def get_tag(self, memory_id: str) -> Optional[EmotionalTag]:
        """获取记忆的情感标签。"""
        return self._tags.get(memory_id)

    def update_tag(self, memory_id: str, emotion_type: str = "",
                   intensity: float = -1, context_hint: str = "") -> Optional[EmotionalTag]:
        """手动更新情感标签。"""
        tag = self._tags.get(memory_id)
        if not tag:
            return None
        if emotion_type:
            tag.emotion_type = emotion_type
            tag.valence = EMOTION_VALENCE.get(emotion_type, 0.0)
        if intensity >= 0:
            tag.intensity = intensity
        if context_hint:
            tag.context_hint = context_hint
        self._save_debounced()
        return tag

    # ================================================================
    # 检索
    # ================================================================

    def get_by_emotion(self, emotion_type: str, limit: int = 20) -> list[EmotionalTag]:
        """按情感类型检索。"""
        results = [
            t for t in self._tags.values()
            if t.emotion_type == emotion_type
        ]
        results.sort(key=lambda t: t.intensity, reverse=True)
        return results[:limit]

    def get_positive_memories(self, min_valence: float = 0.3,
                              limit: int = 20) -> list[EmotionalTag]:
        """获取正面情感的记忆。"""
        results = [t for t in self._tags.values() if t.valence >= min_valence]
        results.sort(key=lambda t: t.valence * t.intensity, reverse=True)
        return results[:limit]

    def get_negative_memories(self, max_valence: float = -0.3,
                              limit: int = 20) -> list[EmotionalTag]:
        """获取负面情感的记忆。"""
        results = [t for t in self._tags.values() if t.valence <= max_valence]
        results.sort(key=lambda t: abs(t.valence) * t.intensity, reverse=True)
        return results[:limit]

    def get_intense_memories(self, min_intensity: float = 0.7,
                             limit: int = 20) -> list[EmotionalTag]:
        """获取情感强烈的记忆（不论正负）。"""
        results = [t for t in self._tags.values() if t.intensity >= min_intensity]
        results.sort(key=lambda t: t.intensity, reverse=True)
        return results[:limit]

    # ================================================================
    # 统计
    # ================================================================

    @property
    def stats(self) -> dict:
        total = len(self._tags)
        if total == 0:
            return {"total": 0}

        type_dist = {}
        for t in self._tags.values():
            type_dist[t.emotion_type] = type_dist.get(t.emotion_type, 0) + 1

        positive = sum(1 for t in self._tags.values() if t.valence > 0.2)
        negative = sum(1 for t in self._tags.values() if t.valence < -0.2)
        neutral = total - positive - negative
        avg_intensity = sum(t.intensity for t in self._tags.values()) / total

        return {
            "total": total,
            "positive": positive,
            "negative": negative,
            "neutral": neutral,
            "avg_intensity": round(avg_intensity, 3),
            "type_distribution": type_dist,
        }

    # ================================================================
    # 持久化
    # ================================================================

    _save_counter = 0

    def _save_debounced(self) -> None:
        self._save_counter += 1
        if self._save_counter % 20 == 0:
            self._save()

    def _save(self) -> None:
        try:
            data = {mid: asdict(t) for mid, t in self._tags.items()}
            self._data_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            logger.warning(f"[EmotionalMemory] 保存失败: {e}")

    def _load(self) -> None:
        if not self._data_path.exists():
            return
        try:
            data = json.loads(self._data_path.read_text(encoding="utf-8"))
            for mid, tdata in data.items():
                self._tags[mid] = EmotionalTag(**tdata)
            logger.debug(f"[EmotionalMemory] 加载: {len(self._tags)}条标签")
        except Exception as e:
            logger.warning(f"[EmotionalMemory] 加载失败: {e}")

    def force_save(self) -> None:
        self._save()


# ================================================================
# 自动发现接口
# ================================================================

class EmotionalMemoryModule(MemoryModule):
    """情感记忆标签模块 — 自动发现注册。"""
    name = "emotional_memory"

    def init(self, data_dir="data/memory", **kwargs):
        self._impl = EmotionalMemory(data_dir=data_dir)

    def on_message(self, user_msg: str = "", ai_reply: str = "") -> None:
        """每次对话后给用户消息打情感标签。"""
        if not user_msg or not hasattr(self, '_impl') or len(user_msg) < 5:
            return
        import time as _time
        mid = f"msg_{int(_time.time() * 1000)}"
        self._impl.tag_memory(mid, user_msg)

    def on_session_end(self) -> None:
        if hasattr(self, '_impl'):
            self._impl.force_save()


MODULE = EmotionalMemoryModule
