"""
white_salary/core/conflict_detector.py

对话冲突检测 — 检测用户连发消息时的修正/补充/撤回意图。

借鉴v2的message_conflict_detector.py但简化：
  - v2有11种冲突类型+60+正则，太复杂
  - 我们只处理最常见的4种：修正、补充、撤回、打断
  - v2没有学习机制，我们预留接口

功能：
  - 检测"不对不对"、"说错了"等修正意图
  - 检测"对了还有"、"忘了说"等补充意图
  - 检测"算了"、"不问了"等撤回意图
  - 检测"等等"、"先别回"等打断意图
  - 返回是否需要重新生成回复
"""

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from loguru import logger


class ConflictType(Enum):
    NONE = "none"
    CORRECTION = "correction"     # 用户修正之前说的
    SUPPLEMENT = "supplement"     # 用户补充信息
    RETRACTION = "retraction"     # 用户撤回/取消
    INTERRUPT = "interrupt"       # 用户打断


@dataclass
class ConflictResult:
    has_conflict: bool
    conflict_type: ConflictType
    hint: str = ""              # 给LLM的提示
    should_regenerate: bool = False


# 正则模式（借鉴v2的60+模式，精选最高频的）
CORRECTION_PATTERNS = [
    r"不对[不了]?", r"说错了", r"打错了", r"不是[这那]",
    r"我(的意思|想说的)是", r"搞错了", r"弄错了", r"写错了",
    r"更正", r"纠正", r"不不不",
]

SUPPLEMENT_PATTERNS = [
    r"对了[还]?[有]?", r"忘了说", r"补充一下", r"另外",
    r"还有[一就]?", r"顺便", r"差点忘了",
]

RETRACTION_PATTERNS = [
    # 已废弃——"算了""不问了"等不再丢弃消息，改为正常对话让白自然回应
    # 真正的QQ撤回通过recall事件处理，不走文本匹配
]

INTERRUPT_PATTERNS = [
    r"等等[！!]?", r"先别[回说]", r"停[一！!]", r"慢着",
    r"先等[一下]", r"打断一下",
]

# 编译正则
_COMPILED = {
    ConflictType.CORRECTION: [re.compile(p) for p in CORRECTION_PATTERNS],
    ConflictType.SUPPLEMENT: [re.compile(p) for p in SUPPLEMENT_PATTERNS],
    ConflictType.RETRACTION: [re.compile(p) for p in RETRACTION_PATTERNS],
    ConflictType.INTERRUPT: [re.compile(p) for p in INTERRUPT_PATTERNS],
}

# 冲突类型对应的LLM提示
_HINTS = {
    ConflictType.CORRECTION: "用户修正了之前说的内容，请根据修正后的信息重新回答。",
    ConflictType.SUPPLEMENT: "用户补充了新信息，请结合之前的问题和补充内容一起回答。",
    ConflictType.RETRACTION: "用户取消了之前的请求，不需要回答之前的问题了。",
    ConflictType.INTERRUPT: "用户要求暂停，请等待用户说完再回复。",
}


class ConflictDetector:
    """
    对话冲突检测器。

    使用方式:
        detector = ConflictDetector()
        result = detector.check("不对不对，我说的是另一个")
        if result.should_regenerate:
            # 重新生成回复
    """

    def check(self, message: str) -> ConflictResult:
        """
        检查消息是否包含冲突信号。

        Args:
            message: 用户新消息

        Returns:
            ConflictResult
        """
        text = message.strip()
        if len(text) < 2:
            return ConflictResult(False, ConflictType.NONE)

        # 按优先级检查（打断 > 修正 > 补充 > 撤回）
        for conflict_type in [
            ConflictType.INTERRUPT,
            ConflictType.CORRECTION,
            ConflictType.SUPPLEMENT,
            ConflictType.RETRACTION,
        ]:
            for pattern in _COMPILED[conflict_type]:
                if pattern.search(text):
                    should_regen = conflict_type in (
                        ConflictType.CORRECTION,
                        ConflictType.SUPPLEMENT,
                    )
                    result = ConflictResult(
                        has_conflict=True,
                        conflict_type=conflict_type,
                        hint=_HINTS[conflict_type],
                        should_regenerate=should_regen,
                    )
                    logger.debug(
                        f"[Conflict] 检测到{conflict_type.value}: {text[:30]}"
                    )
                    return result

        return ConflictResult(False, ConflictType.NONE)
