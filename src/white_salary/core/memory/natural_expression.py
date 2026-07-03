"""
white_salary/core/memory/natural_expression.py

情感间接表达 — 不直接说"我很生气"，而是通过语气和行为暗示。

借鉴v2的features/natural_expression.py（305行）：
  - 7种情感表达模式（嘴硬心软）
  - emotion_llm检测当前情感
  - 对家人可以直接表达，对陌生人用间接方式
  - 禁止直接说"我很XX"

LLM通道：emotion_llm（检测情感状态）

自动发现：导出MODULE供MemoryManager加载。
"""

import re
import random
from typing import Optional

from loguru import logger
from white_salary.core.memory.module_base import MemoryModule


# 7种情感间接表达模式
EXPRESSION_MODES = {
    "angry_caring": {
        "description": "生气但其实关心",
        "direct_banned": "我很生气但是关心你",
        "indirect_example": "哼...算了，你自己注意点",
        "tone_words": ["哼", "切", "算了"],
    },
    "sad_hiding": {
        "description": "难过但逞强",
        "direct_banned": "我很难过",
        "indirect_example": "没事啦...就是有点累",
        "tone_words": ["没事", "嗯", "..."],
    },
    "happy_shy": {
        "description": "开心但害羞",
        "direct_banned": "我很开心",
        "indirect_example": "还、还好啦...也没有很开心",
        "tone_words": ["才没有", "还好啦", "一般般"],
    },
    "worried_cool": {
        "description": "担心但装酷",
        "direct_banned": "我很担心你",
        "indirect_example": "你...自己小心点吧",
        "tone_words": ["随便", "无所谓", "你看着办"],
    },
    "hurt_tough": {
        "description": "受伤但嘴硬",
        "direct_banned": "你伤害了我",
        "indirect_example": "...没什么，我没事",
        "tone_words": ["没什么", "无所谓", "随便"],
    },
    "jealous_deny": {
        "description": "吃醋但否认",
        "direct_banned": "我吃醋了",
        "indirect_example": "谁、谁吃醋了！才没有！",
        "tone_words": ["才没有", "谁", "关我什么事"],
    },
    "miss_tsundere": {
        "description": "想念但傲娇",
        "direct_banned": "我想你了",
        "indirect_example": "才不是想你...就是有点无聊",
        "tone_words": ["才不是", "无聊", "随便问问"],
    },
}

# 禁止的直接情感表达
_DIRECT_EMOTION_BANNED = [
    r"我(?:很|非常|特别|真的)(?:生气|难过|开心|担心|受伤|吃醋|想你)",
    r"(?:但又|同时又|而且还)(?:心疼|关心|喜欢)",
    r"我的情绪(?:是|变得)",
    r"让我感到(?:难过|开心|生气)",
]

# 语气词池
_TONE_WORDS = ["哼", "切", "唉", "嗯...", "那个", "就是说", "啊", "欸"]


class NaturalExpressionEngine:
    """情感间接表达引擎。"""

    def __init__(self) -> None:
        self._banned_compiled = [re.compile(p) for p in _DIRECT_EMOTION_BANNED]
        self._current_mode: Optional[str] = None

    def _get_mode_from_config(self, mode_name: str) -> Optional[dict]:
        """从配置文件读取表达模式（GUI可编辑）。"""
        try:
            import json
            from pathlib import Path
            path = Path("config/prompt_templates.json")
            if path.exists():
                templates = json.loads(path.read_text(encoding="utf-8"))
                modes = templates.get("natural_expression", {})
                if mode_name in modes:
                    return modes[mode_name]
        except Exception:
            pass
        # 回退到硬编码
        return EXPRESSION_MODES.get(mode_name)

    def get_expression_rules(self, emotion: str = "",
                             is_family: bool = False) -> str:
        """
        根据当前情感生成表达规则（注入system prompt）。

        Args:
            emotion: 当前情感（angry/sad/happy/worried/hurt/jealous/miss）
            is_family: 是否是家人（家人可以直接表达）
        """
        if is_family:
            return ""  # 对家人不需要间接表达

        # 映射情感到表达模式
        emotion_to_mode = {
            "angry": "angry_caring",
            "sad": "sad_hiding",
            "happy": "happy_shy",
            "worried": "worried_cool",
            "hurt": "hurt_tough",
            "jealous": "jealous_deny",
            "miss": "miss_tsundere",
        }

        mode_name = emotion_to_mode.get(emotion)
        if not mode_name:
            return ""

        # 从配置文件读取（GUI可编辑）
        mode = self._get_mode_from_config(mode_name)
        if not mode:
            return ""
        self._current_mode = mode_name

        return (
            f"[表达方式] {mode['description']}。"
            f"不要直接说「{mode.get('banned', '')}」，"
            f"而是像「{mode.get('example', '')}」这样表达。"
            f"可以用的语气词：{'、'.join(mode.get('tone_words', []))}"
        )

    def validate_response(self, text: str) -> list[str]:
        """
        检查回复中是否有不自然的直接情感表达。

        Returns:
            问题列表（空=没问题）
        """
        issues = []
        for p in self._banned_compiled:
            if p.search(text):
                issues.append("有直接情感陈述，应该用间接方式表达")
                break
        return issues

    def add_tone_word(self, text: str) -> str:
        """偶尔在文本中加入语气词（30%概率）。"""
        if random.random() > 0.3 or not text or len(text) < 10:
            return text
        tone = random.choice(_TONE_WORDS)
        # 50%放开头，50%放中间
        if random.random() < 0.5:
            return f"{tone}，{text}"
        else:
            mid = len(text) // 2
            # 找最近的标点
            for i in range(mid, min(mid + 10, len(text))):
                if text[i] in "，。！？、":
                    return text[:i + 1] + f"{tone}，" + text[i + 1:]
            return text


# ================================================================
# 自动发现接口
# ================================================================

class NaturalExpressionModule(MemoryModule):
    name = "natural_expression"

    def init(self, data_dir="data/memory", **kwargs):
        self._impl = NaturalExpressionEngine()

    def get_context_prompt(self, message: str = "", **kwargs) -> str:
        if not hasattr(self, '_impl'):
            return ""
        # 从emotion_tracker获取当前情感（缓存实例）
        # 2026-07-03 审计修复（批5）：EmotionTracker() 现返回进程级共享实例
        # （按data_dir缓存），与主 manager 的情绪状态天然一致
        try:
            from white_salary.core.memory.emotion_tracker import EmotionTracker
            if not hasattr(self, '_tracker'):
                self._tracker = EmotionTracker()
            emotion = self._tracker.current_emotion
            if emotion and emotion != "neutral":
                return self._impl.get_expression_rules(emotion)
        except Exception:
            pass
        return ""


MODULE = NaturalExpressionModule
