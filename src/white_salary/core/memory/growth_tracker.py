"""
white_salary/core/memory/growth_tracker.py

成长追踪 — AI性格随时间变化。

记录AI的性格特征值随时间的演变。不用LLM。

自动发现：导出MODULE供MemoryManager加载。
"""

import json
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path

from loguru import logger
from white_salary.core.memory.module_base import MemoryModule


@dataclass
class GrowthSnapshot:
    """性格快照。"""
    timestamp: float = 0.0
    traits: dict = field(default_factory=dict)  # {trait: value}
    trigger: str = ""  # 什么导致了变化


# 默认性格维度
DEFAULT_TRAITS = {
    "openness": 0.6,       # 开放性 0-1
    "warmth": 0.7,         # 温暖度
    "confidence": 0.5,     # 自信
    "playfulness": 0.6,    # 调皮度
    "patience": 0.7,       # 耐心
    "curiosity": 0.7,      # 好奇心
}

MAX_SNAPSHOTS = 50


class GrowthTracker:
    """性格成长追踪器。"""

    def __init__(self, data_dir: str = "data/memory") -> None:
        self._data_path = Path(data_dir) / "growth_tracker.json"
        self._data_path.parent.mkdir(parents=True, exist_ok=True)
        self._current_traits: dict[str, float] = dict(DEFAULT_TRAITS)
        self._history: list[GrowthSnapshot] = []
        self._load()

    def adjust_trait(self, trait: str, delta: float, trigger: str = "") -> None:
        """调整某个性格维度（微调，幅度小）。"""
        if trait not in self._current_traits:
            return
        old = self._current_traits[trait]
        self._current_traits[trait] = max(0, min(1, old + delta * 0.1))
        if abs(self._current_traits[trait] - old) > 0.01:
            self._record_snapshot(trigger or f"adjust_{trait}")

    def get_traits(self) -> dict[str, float]:
        return {k: round(v, 2) for k, v in self._current_traits.items()}

    def get_trait_prompt(self) -> str:
        """生成性格提示。"""
        notable = []
        for trait, value in self._current_traits.items():
            trait_cn = {"openness": "开放", "warmth": "温暖", "confidence": "自信",
                        "playfulness": "调皮", "patience": "耐心", "curiosity": "好奇"}
            if value > 0.8:
                notable.append(f"非常{trait_cn.get(trait, trait)}")
            elif value < 0.3:
                notable.append(f"不太{trait_cn.get(trait, trait)}")
        if not notable:
            return ""
        return f"[性格特征] {', '.join(notable)}"

    def _record_snapshot(self, trigger: str) -> None:
        self._history.append(GrowthSnapshot(
            timestamp=time.time(),
            traits=dict(self._current_traits),
            trigger=trigger,
        ))
        if len(self._history) > MAX_SNAPSHOTS:
            self._history = self._history[-MAX_SNAPSHOTS:]
        self._save()

    def _save(self) -> None:
        try:
            data = {
                "current": self._current_traits,
                "history": [asdict(s) for s in self._history],
            }
            self._data_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _load(self) -> None:
        if self._data_path.exists():
            try:
                data = json.loads(self._data_path.read_text(encoding="utf-8"))
                self._current_traits = data.get("current", dict(DEFAULT_TRAITS))
                for s in data.get("history", []):
                    self._history.append(GrowthSnapshot(**s))
            except Exception:
                pass

    @property
    def stats(self) -> dict:
        return {"traits": self.get_traits(), "snapshots": len(self._history)}


class GrowthTrackerModule(MemoryModule):
    name = "growth_tracker"

    def init(self, data_dir="data/memory", **kwargs):
        self._impl = GrowthTracker(data_dir=data_dir)

    def get_context_prompt(self, message: str = "") -> str:
        if not hasattr(self, '_impl'):
            return ""
        return self._impl.get_trait_prompt()


MODULE = GrowthTrackerModule
