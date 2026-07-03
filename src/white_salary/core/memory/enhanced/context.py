"""
white_salary/core/memory/enhanced/context.py

场景感知 — 识别当前对话氛围，影响记忆检索策略。

借鉴v2的设计：
  - 8种氛围：casual/serious/playful/intimate/tense/supportive/celebratory/melancholic
  - 从对话内容+时间+历史推断当前氛围
  - 氛围影响记忆检索优先级（亲密时优先情感记忆，严肃时优先知识记忆）
  - 自动衰减回casual

配置从 config/memory_settings.json 的 scene 节读取。

自动发现：导出MODULE供MemoryManager加载。
"""

import json
import re
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger
from white_salary.core.memory.module_base import MemoryModule


# ================================================================
# 8种氛围
# ================================================================

ATMOSPHERE_CASUAL = "casual"             # 日常闲聊
ATMOSPHERE_SERIOUS = "serious"           # 严肃认真
ATMOSPHERE_PLAYFUL = "playful"           # 调皮活泼
ATMOSPHERE_INTIMATE = "intimate"         # 亲密温馨
ATMOSPHERE_TENSE = "tense"              # 紧张对峙
ATMOSPHERE_SUPPORTIVE = "supportive"     # 支持鼓励
ATMOSPHERE_CELEBRATORY = "celebratory"   # 庆祝欢乐
ATMOSPHERE_MELANCHOLIC = "melancholic"   # 忧伤低落

ALL_ATMOSPHERES = [
    ATMOSPHERE_CASUAL, ATMOSPHERE_SERIOUS, ATMOSPHERE_PLAYFUL,
    ATMOSPHERE_INTIMATE, ATMOSPHERE_TENSE, ATMOSPHERE_SUPPORTIVE,
    ATMOSPHERE_CELEBRATORY, ATMOSPHERE_MELANCHOLIC,
]

# 氛围的中文标签
ATMOSPHERE_LABELS = {
    "casual": "日常",
    "serious": "严肃",
    "playful": "活泼",
    "intimate": "亲密",
    "tense": "紧张",
    "supportive": "温暖",
    "celebratory": "庆祝",
    "melancholic": "低落",
}

# 氛围→记忆类型偏好权重（影响检索排序）
ATMOSPHERE_MEMORY_BIAS = {
    "casual": {"knowledge": 1.0, "event": 1.0, "person": 1.0, "emotion": 0.8, "promise": 0.8, "secret": 0.5},
    "serious": {"knowledge": 1.5, "promise": 1.3, "event": 1.0, "person": 0.8, "emotion": 0.5, "secret": 0.5},
    "playful": {"emotion": 1.3, "event": 1.2, "person": 1.0, "knowledge": 0.8, "promise": 0.7, "secret": 0.8},
    "intimate": {"emotion": 1.5, "secret": 1.5, "person": 1.3, "promise": 1.2, "event": 1.0, "knowledge": 0.7},
    "tense": {"promise": 1.5, "event": 1.3, "emotion": 1.2, "person": 1.0, "knowledge": 0.8, "secret": 0.5},
    "supportive": {"emotion": 1.5, "person": 1.2, "event": 1.0, "promise": 1.0, "knowledge": 0.8, "secret": 0.5},
    "celebratory": {"event": 1.5, "emotion": 1.3, "person": 1.2, "secret": 1.0, "promise": 0.8, "knowledge": 0.7},
    "melancholic": {"emotion": 1.5, "secret": 1.3, "person": 1.2, "event": 1.0, "promise": 0.8, "knowledge": 0.5},
}

# 氛围检测关键词
_ATMOSPHERE_KEYWORDS = {
    "playful": [
        "哈哈", "嘻嘻", "笑死", "搞笑", "逗", "玩", "有趣",
        "呵呵", "哎呀", "调皮", "皮", "好玩", "整蛊",
    ],
    "intimate": [
        "想你", "亲爱的", "宝贝", "抱抱", "亲亲", "爱你",
        "好想", "在吗", "想聊聊", "陪我", "晚安", "心里话",
        "只告诉你", "我们的", "悄悄话",
    ],
    "tense": [
        "生气", "烦", "怒", "讨厌", "滚", "闭嘴", "别说了",
        "不想理", "吵架", "不开心", "受不了", "忍不了",
    ],
    "supportive": [
        "加油", "没关系", "会好的", "别担心", "放心",
        "鼓励", "相信", "坚持", "你可以", "别放弃",
    ],
    "celebratory": [
        "太好了", "耶", "庆祝", "恭喜", "万岁", "成功",
        "通过了", "赢了", "考上了", "录取", "升职",
    ],
    "melancholic": [
        "难过", "伤心", "哭", "眼泪", "失望", "孤独",
        "寂寞", "不开心", "心痛", "崩溃", "破防", "emo",
        "好累", "不想", "算了", "无所谓",
    ],
    "serious": [
        "认真", "说正事", "重要", "必须", "问题", "严肃",
        "工作", "计划", "决定", "考虑", "分析",
    ],
}


@dataclass
class SceneContext:
    """当前场景上下文。"""
    atmosphere: str = "casual"          # 当前氛围
    confidence: float = 0.5             # 判断置信度 0-1
    time_period: str = ""               # 时间段（morning/afternoon/evening/night）
    topic: str = ""                     # 当前话题
    last_updated: float = 0.0           # 上次更新时间
    history: list[str] = None           # 最近5次氛围变化

    def __post_init__(self):
        if self.history is None:
            self.history = []


class SceneEngine:
    """
    场景感知引擎。

    使用方式:
        engine = SceneEngine()
        engine.update_from_message("好开心啊今天")
        atm = engine.current_atmosphere  # → "playful" or "celebratory"
        bias = engine.get_memory_bias("emotion")  # → 1.3
    """

    def __init__(self, config: dict = None) -> None:
        cfg = config or {}
        self._atmospheres = cfg.get("atmospheres", ALL_ATMOSPHERES)
        self._decay_turns = cfg.get("decay_turns", 10)  # N轮无关键词后衰减回casual

        self._context = SceneContext()
        self._turns_since_detection = 0

    @property
    def current_atmosphere(self) -> str:
        return self._context.atmosphere

    @property
    def context(self) -> SceneContext:
        return self._context

    def update_from_message(self, text: str) -> str:
        """
        根据消息更新场景氛围。

        Returns:
            当前氛围
        """
        if not text:
            return self._context.atmosphere

        # 更新时间段
        self._update_time_period()

        # 检测关键词
        scores: dict[str, int] = {}
        for atm, keywords in _ATMOSPHERE_KEYWORDS.items():
            for kw in keywords:
                if kw in text:
                    scores[atm] = scores.get(atm, 0) + 1

        if scores:
            # 取得分最高的氛围
            best_atm = max(scores, key=scores.get)
            best_score = scores[best_atm]

            # 置信度计算
            confidence = min(0.3 + best_score * 0.2, 1.0)

            # 时间段加成
            if self._context.time_period == "night" and best_atm in ("intimate", "melancholic"):
                confidence += 0.1

            # 更新氛围（需要超过阈值或超过当前置信度）
            if confidence > self._context.confidence or best_score >= 2:
                old = self._context.atmosphere
                self._context.atmosphere = best_atm
                self._context.confidence = confidence
                self._context.last_updated = time.time()
                self._turns_since_detection = 0

                # 记录历史
                if old != best_atm:
                    self._context.history.append(best_atm)
                    if len(self._context.history) > 5:
                        self._context.history = self._context.history[-5:]

        else:
            # 无关键词，衰减计数
            self._turns_since_detection += 1
            if self._turns_since_detection >= self._decay_turns:
                # 衰减回casual
                if self._context.atmosphere != "casual":
                    self._context.atmosphere = "casual"
                    self._context.confidence = 0.5
                    self._context.last_updated = time.time()

        return self._context.atmosphere

    def get_memory_bias(self, memory_category: str) -> float:
        """
        获取当前氛围对某类记忆的偏好权重。

        Args:
            memory_category: 记忆分类（person/event/promise/secret/knowledge/emotion）

        Returns:
            权重系数（1.0=无偏好，>1.0=偏好，<1.0=抑制）
        """
        bias = ATMOSPHERE_MEMORY_BIAS.get(self._context.atmosphere, {})
        return bias.get(memory_category, 1.0)

    def get_all_biases(self) -> dict[str, float]:
        """获取当前氛围的所有记忆类型偏好。"""
        return ATMOSPHERE_MEMORY_BIAS.get(self._context.atmosphere, {})

    def _update_time_period(self) -> None:
        """更新时间段。"""
        hour = datetime.now().hour
        if 5 <= hour < 12:
            self._context.time_period = "morning"
        elif 12 <= hour < 18:
            self._context.time_period = "afternoon"
        elif 18 <= hour < 22:
            self._context.time_period = "evening"
        else:
            self._context.time_period = "night"

    @property
    def stats(self) -> dict:
        return {
            "atmosphere": self._context.atmosphere,
            "atmosphere_label": ATMOSPHERE_LABELS.get(self._context.atmosphere, "未知"),
            "confidence": round(self._context.confidence, 2),
            "time_period": self._context.time_period,
            "history": self._context.history,
        }


# ================================================================
# 自动发现接口
# ================================================================

class SceneModule(MemoryModule):
    """场景感知模块 — 自动发现注册。"""
    name = "scene_context"

    def init(self, data_dir="data/memory", **kwargs):
        config = {}
        try:
            cfg_path = Path("config/memory_settings.json")
            if cfg_path.exists():
                all_cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
                config = all_cfg.get("scene", {})
        except Exception:
            pass
        self._impl = SceneEngine(config=config)

    def get_context_prompt(self, message: str = "") -> str:
        """注入当前场景氛围到对话上下文。"""
        if not hasattr(self, '_impl'):
            return ""
        atm = self._impl.current_atmosphere
        if atm == "casual":
            return ""  # 日常不需要特别提示
        label = ATMOSPHERE_LABELS.get(atm, atm)
        return f"[当前氛围: {label}]"

    def on_message(self, user_msg: str = "", ai_reply: str = "") -> None:
        """每次对话后更新场景。"""
        if not hasattr(self, '_impl') or not user_msg:
            return
        self._impl.update_from_message(user_msg)


MODULE = SceneModule
