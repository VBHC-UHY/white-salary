"""
white_salary/core/memory/dialogue_evolution.py

口头禅进化 — 口头禅有热度值，用多了自然切换。

借鉴v2的features/dialogue_nuance.py（290行）：
  - 口头禅池，每个有0-2的热度值
  - 用多了热度降低，自然换新的
  - 说话风格会渐渐漂移（formal→casual→playful）
  - 根据时间/场景自动调整

不用LLM（偶尔用detect_llm检测场景，10%概率）。

自动发现：导出MODULE供MemoryManager加载。
"""

import json
import random
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger
from white_salary.core.memory.module_base import MemoryModule


# 口头禅池（初始+可进化）
_INITIAL_PHRASES = {
    "欸": 1.0, "哦": 0.8, "啊": 0.7, "嗯": 0.9,
    "那个": 0.6, "说真的": 0.5, "不是吧": 0.4,
    "话说": 0.5, "对了": 0.6, "怎么说": 0.3,
}

# 备选口头禅（可以被进化加入）
_PHRASE_POOL = [
    "诶", "好家伙", "绝了", "离谱", "就很", "真的假的",
    "属于是", "有一说一", "怎么讲", "我的天", "好吧",
]

# 说话风格
STYLES = {
    "formal": {"suffix": ["。", "。"], "desc": "正式"},
    "casual": {"suffix": ["~", ""], "desc": "随意"},
    "playful": {"suffix": ["~", "！", "hhh"], "desc": "活泼"},
    "gentle": {"suffix": ["呢", "~"], "desc": "温柔"},
    "cool": {"suffix": ["。", ""], "desc": "冷酷"},
}


class DialogueEvolution:
    """口头禅进化引擎。"""

    def __init__(self, data_dir: str = "data/memory") -> None:
        self._data_path = Path(data_dir) / "dialogue_evolution.json"
        self._data_path.parent.mkdir(parents=True, exist_ok=True)

        self._phrases: dict[str, float] = dict(_INITIAL_PHRASES)  # phrase → popularity
        self._current_style: str = "casual"
        self._style_drift_progress: float = 0.0
        self._style_drift_target: str = ""
        self._check_count: int = 0

        self._load()

    def get_current_phrase(self) -> str:
        """根据热度加权随机选一个口头禅。"""
        if not self._phrases:
            return ""
        phrases = list(self._phrases.items())
        weights = [max(0.1, p) for _, p in phrases]
        chosen = random.choices(phrases, weights=weights, k=1)[0]
        return chosen[0]

    def on_phrase_used(self, phrase: str) -> None:
        """口头禅被使用后降低热度。"""
        if phrase in self._phrases:
            self._phrases[phrase] = max(0.1, self._phrases[phrase] - 0.05)

    def evolve(self) -> None:
        """
        进化检查（每50次对话调用一次）。
        - 热度太低的口头禅淘汰
        - 随机加入新口头禅
        """
        self._check_count += 1
        if self._check_count % 50 != 0:
            return

        # 淘汰热度<0.2的
        to_remove = [p for p, pop in self._phrases.items() if pop < 0.2]
        for p in to_remove:
            del self._phrases[p]
            logger.debug(f"[DialogueEvo] 口头禅淘汰: {p}")

        # 5%概率加入新口头禅
        if random.random() < 0.05 and _PHRASE_POOL:
            new_phrase = random.choice(_PHRASE_POOL)
            if new_phrase not in self._phrases:
                self._phrases[new_phrase] = 0.5
                logger.debug(f"[DialogueEvo] 新口头禅: {new_phrase}")

        # 风格漂移
        if not self._style_drift_target:
            styles = [s for s in STYLES if s != self._current_style]
            self._style_drift_target = random.choice(styles)
            self._style_drift_progress = 0.0
        else:
            self._style_drift_progress += 0.1
            if self._style_drift_progress >= 1.0:
                old = self._current_style
                self._current_style = self._style_drift_target
                self._style_drift_target = ""
                self._style_drift_progress = 0.0
                logger.debug(f"[DialogueEvo] 风格漂移: {old} → {self._current_style}")

        self._save()

    def get_style_hint(self) -> str:
        """生成当前风格提示。"""
        style = STYLES.get(self._current_style, {})
        desc = style.get("desc", "随意")

        # 时间段调整
        hour = datetime.now().hour
        if 0 <= hour < 6:
            time_hint = "深夜，语气更温柔安静"
        elif 22 <= hour <= 23:
            time_hint = "晚上，可以更放松随意"
        elif 6 <= hour < 9:
            time_hint = "早上，精神但不过于亢奋"
        else:
            time_hint = ""

        parts = [f"[说话风格: {desc}]"]
        if time_hint:
            parts.append(time_hint)

        phrase = self.get_current_phrase()
        if phrase:
            parts.append(f"可以偶尔用「{phrase}」这样的口头禅")

        return "\n".join(parts)

    def _save(self) -> None:
        try:
            data = {
                "phrases": self._phrases,
                "style": self._current_style,
                "drift_target": self._style_drift_target,
                "drift_progress": self._style_drift_progress,
            }
            self._data_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _load(self) -> None:
        if self._data_path.exists():
            try:
                data = json.loads(self._data_path.read_text(encoding="utf-8"))
                self._phrases = data.get("phrases", dict(_INITIAL_PHRASES))
                self._current_style = data.get("style", "casual")
                self._style_drift_target = data.get("drift_target", "")
                self._style_drift_progress = data.get("drift_progress", 0.0)
            except Exception:
                pass

    @property
    def stats(self) -> dict:
        return {
            "phrases": len(self._phrases),
            "style": self._current_style,
            "top_phrases": sorted(self._phrases.items(), key=lambda x: x[1], reverse=True)[:5],
        }


class DialogueEvolutionModule(MemoryModule):
    name = "dialogue_evolution"

    def init(self, data_dir="data/memory", **kwargs):
        self._impl = DialogueEvolution(data_dir=data_dir)

    def get_context_prompt(self, message: str = "") -> str:
        if not hasattr(self, '_impl'):
            return ""
        return self._impl.get_style_hint()

    def on_message(self, user_msg: str = "", ai_reply: str = "") -> None:
        if hasattr(self, '_impl'):
            self._impl.evolve()


MODULE = DialogueEvolutionModule
