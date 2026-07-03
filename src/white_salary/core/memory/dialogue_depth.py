"""
white_salary/core/memory/dialogue_depth.py

说话深度 — 欲言又止+试探性提问+潜台词。

借鉴v2的features/dialogue_depth.py（358行）：
  - 欲言又止（5-30%概率，关系越近越低）
  - 试探性提问（敏感话题先试探反应）
  - 潜台词（嘴上说"没事"但语气暗示"有事"）
  - emotion_llm判断话题是否敏感

LLM通道：emotion_llm（偶尔判断话题敏感度）

自动发现：导出MODULE供MemoryManager加载。
"""

import random
import re
import time
from typing import Optional

from loguru import logger
from white_salary.core.memory.module_base import MemoryModule


# 敏感话题关键词（可能触发欲言又止）
_SENSITIVE_TOPICS = [
    "喜欢", "讨厌", "觉得我", "想念", "担心", "在乎",
    "为什么不", "是不是不", "你会不会", "以后",
    "表白", "告白", "分手", "离开",
]

# 犹豫原因
_HESITATION_REASONS = {
    "fear_rejection": "怕被拒绝",
    "fear_awkward": "怕尴尬",
    "not_sure": "不确定该不该说",
    "timing_bad": "觉得时机不对",
    "too_personal": "太私人了",
}

# 试探信号检测（用户回应是积极还是消极）
_POSITIVE_SIGNALS = ["好", "可以", "想", "嗯", "行", "当然", "没问题", "愿意"]
_NEGATIVE_SIGNALS = ["不", "算了", "没兴趣", "无聊", "别", "不想", "不要"]


class DialogueDepthEngine:
    """说话深度引擎。"""

    def __init__(self) -> None:
        self._suppressed_message: Optional[str] = None  # 想说但没说的话
        self._hesitation_reason: str = ""
        self._probe_active: bool = False  # 是否在试探中
        self._probe_topic: str = ""
        self._last_hesitation_time: float = 0.0

    def should_hesitate(self, intended_message: str,
                        relationship_level: int = 5) -> Optional[str]:
        """
        判断是否应该欲言又止。

        Args:
            intended_message: AI打算说的话
            relationship_level: 关系亲密度 0-10（越高越不犹豫）

        Returns:
            犹豫提示（None=不犹豫）
        """
        # 冷却：10分钟内不重复犹豫
        if time.time() - self._last_hesitation_time < 600:
            return None

        # 检测是否涉及敏感话题
        is_sensitive = any(kw in intended_message for kw in _SENSITIVE_TOPICS)
        if not is_sensitive:
            return None

        # 关系越近越不犹豫（5-30%概率）
        hesitation_chance = max(0.05, 0.30 - relationship_level * 0.025)
        if random.random() > hesitation_chance:
            return None

        # 选择犹豫原因
        reason_key = random.choice(list(_HESITATION_REASONS.keys()))
        reason = _HESITATION_REASONS[reason_key]

        # 记录想说但没说的话
        self._suppressed_message = intended_message
        self._hesitation_reason = reason
        self._last_hesitation_time = time.time()

        return (
            f"[说话深度] 你现在想说「{intended_message[:20]}...」但犹豫了（{reason}）。"
            f"可以表现出欲言又止的样子，比如说一半停下来，或者用'那个...'、'算了没什么'这样的话。"
            f"不要直接说出你犹豫的原因。"
        )

    def should_probe(self, message: str) -> Optional[str]:
        """
        检查是否应该先试探再说。

        Args:
            message: 用户的消息

        Returns:
            试探提示（None=不需要试探）
        """
        # 检测敏感话题
        for kw in _SENSITIVE_TOPICS:
            if kw in message:
                if random.random() < 0.3:  # 30%概率试探
                    self._probe_active = True
                    self._probe_topic = kw
                    return (
                        f"[试探] 用户提到了「{kw}」相关话题。"
                        f"不要直接回答，先试探性地问一下对方的想法，"
                        f"看看反应再决定说什么。"
                    )
                break
        return None

    def check_probe_response(self, user_response: str) -> Optional[str]:
        """
        检查试探后用户的反应。

        Returns:
            后续建议
        """
        if not self._probe_active:
            return None

        self._probe_active = False

        # 判断积极还是消极
        is_positive = any(s in user_response for s in _POSITIVE_SIGNALS)
        is_negative = any(s in user_response for s in _NEGATIVE_SIGNALS)

        if is_positive:
            if self._suppressed_message:
                msg = self._suppressed_message
                self._suppressed_message = None
                return f"[试探结果] 对方反应积极，可以说出之前想说的话了"
            return None
        elif is_negative:
            self._suppressed_message = None
            return f"[试探结果] 对方反应消极，先不要说太直接的话"

        return None

    def get_depth_prompt(self, message: str = "") -> str:
        """综合生成说话深度提示。"""
        parts = []

        # 检查试探反应
        if self._probe_active and message:
            probe_result = self.check_probe_response(message)
            if probe_result:
                parts.append(probe_result)

        # 检查是否需要新试探
        if message and not self._probe_active:
            probe = self.should_probe(message)
            if probe:
                parts.append(probe)

        return "\n".join(parts) if parts else ""


# ================================================================
# 自动发现接口
# ================================================================

class DialogueDepthModule(MemoryModule):
    name = "dialogue_depth"

    def init(self, data_dir="data/memory", **kwargs):
        self._impl = DialogueDepthEngine()

    def get_context_prompt(self, message: str = "") -> str:
        if not message or not hasattr(self, '_impl'):
            return ""
        return self._impl.get_depth_prompt(message)


MODULE = DialogueDepthModule
