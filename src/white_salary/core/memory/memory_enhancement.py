"""
white_salary/core/memory/memory_enhancement.py

高级记忆增强 — 权重管理+选择性遗忘+记忆变形+情感渲染。

借鉴v2的memory_enhancement.py（400+行版本）：
  - 基础权重管理（访问频率+时间衰减）
  - 选择性遗忘（SelectiveForgetting）: 重要记忆清晰，琐碎记忆模糊
  - 记忆变形（MemoryDistortion）: 记忆随时间微妙变化（人类特征）
  - 情感渲染（EmotionalRendering）: 开心的记忆回忆时更美好
  - FadingMemory: clarity(0-1) + recall_count + importance

自动发现：导出MODULE供MemoryManager加载。
"""

import math
import random
import time
import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

from loguru import logger
from white_salary.core.memory.module_base import MemoryModule


# ================================================================
# FadingMemory — 会褪色的记忆
# ================================================================

@dataclass
class FadingMemory:
    """一条会褪色的记忆。"""
    key: str = ""
    content: str = ""
    clarity: float = 1.0        # 清晰度 0-1（越低越模糊）
    recall_count: int = 0       # 被回忆的次数
    importance: int = 5         # 重要度 1-10
    emotional_valence: float = 0.0  # 情感效价 -1(消极)~+1(积极)
    created_at: float = 0.0
    last_recalled: float = 0.0


# 重要度→遗忘速率（越重要越慢遗忘）
FORGETTING_RATES = {
    10: 0.01,   # 极重要：几乎不忘
    9: 0.02,
    8: 0.03,
    7: 0.05,
    6: 0.07,
    5: 0.10,    # 一般：正常遗忘
    4: 0.13,
    3: 0.16,
    2: 0.20,
    1: 0.25,    # 不重要：快速遗忘
}


class MemoryWeight:
    """
    记忆权重管理器 + 高级增强功能。

    使用方式:
        mw = MemoryWeight(data_dir="data/memory")
        mw.record_access("memory_key_123")
        weight = mw.get_weight("memory_key_123")

        # 高级功能
        fading = mw.get_fading_memory("m1")
        rendered = mw.emotional_render("上次考试考了100分", valence=0.8)
        distorted = mw.distort_memory("大概是上周三的事", clarity=0.4)
    """

    DECAY_HALF_LIFE_DAYS = 30

    def __init__(self, data_dir: str = "data/memory") -> None:
        self._path = Path(data_dir) / "memory_weights.json"
        self._fading_path = Path(data_dir) / "fading_memories.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._weights: dict[str, dict] = {}
        self._fading: dict[str, FadingMemory] = {}
        self._load()

    # ================================================================
    # 基础权重管理（保留原有功能）
    # ================================================================

    def record_access(self, key: str) -> None:
        """记录一次记忆访问。"""
        now = time.time()
        if key not in self._weights:
            self._weights[key] = {"access_count": 0, "last_access": now, "created": now}
        self._weights[key]["access_count"] += 1
        self._weights[key]["last_access"] = now

        # 更新FadingMemory
        if key in self._fading:
            fm = self._fading[key]
            fm.recall_count += 1
            fm.last_recalled = now
            # 回忆会短暂提升清晰度
            fm.clarity = min(1.0, fm.clarity + 0.1)

        if sum(w["access_count"] for w in self._weights.values()) % 50 == 0:
            self._save()

    def get_weight(self, key: str, base_importance: float = 1.0) -> float:
        """计算记忆的动态权重。"""
        entry = self._weights.get(key)
        if not entry:
            return base_importance

        freq = math.log(entry["access_count"] + 1) + 1
        days_since = (time.time() - entry["last_access"]) / 86400
        recency = 0.5 ** (days_since / self.DECAY_HALF_LIFE_DAYS)

        return base_importance * freq * recency

    def sort_by_weight(self, keys: list[str], base_importance: float = 1.0) -> list[str]:
        """按权重排序。"""
        return sorted(keys, key=lambda k: self.get_weight(k, base_importance), reverse=True)

    # ================================================================
    # 选择性遗忘（Selective Forgetting）
    # ================================================================

    def register_fading(self, key: str, content: str,
                        importance: int = 5,
                        emotional_valence: float = 0.0) -> FadingMemory:
        """注册一条会褪色的记忆。"""
        fm = FadingMemory(
            key=key,
            content=content,
            clarity=1.0,
            importance=importance,
            emotional_valence=emotional_valence,
            created_at=time.time(),
            last_recalled=time.time(),
        )
        self._fading[key] = fm
        return fm

    def get_fading_memory(self, key: str) -> Optional[FadingMemory]:
        """获取褪色记忆（自动更新清晰度）。"""
        fm = self._fading.get(key)
        if not fm:
            return None

        # 计算当前清晰度
        fm.clarity = self._calculate_clarity(fm)
        return fm

    def update_clarity_all(self) -> int:
        """批量更新所有褪色记忆的清晰度。返回已完全遗忘的数量。"""
        forgotten = 0
        for key, fm in list(self._fading.items()):
            fm.clarity = self._calculate_clarity(fm)
            if fm.clarity < 0.05:
                forgotten += 1
        if forgotten:
            self._save_fading()
        return forgotten

    def _calculate_clarity(self, fm: FadingMemory) -> float:
        """计算记忆清晰度。"""
        # 基础衰减率（由重要度决定）
        rate = FORGETTING_RATES.get(fm.importance, 0.10)

        # 时间因子
        days_since = (time.time() - fm.last_recalled) / 86400

        # 情感保留（强烈情感的记忆衰减更慢）
        emotional_factor = 1.0 - abs(fm.emotional_valence) * 0.3

        # 回忆加成（被回忆越多衰减越慢）
        recall_factor = 1.0 / (1 + fm.recall_count * 0.1)

        # 最终衰减
        decay = rate * emotional_factor * recall_factor
        clarity = math.exp(-decay * days_since)

        return max(0.0, min(1.0, clarity))

    # ================================================================
    # 记忆变形（Memory Distortion）
    # ================================================================

    def distort_memory(self, content: str, clarity: float = 1.0) -> str:
        """
        根据清晰度对记忆内容做变形（模拟人类记忆模糊）。

        clarity=1.0 → 完全清晰，原文
        clarity=0.7 → 稍微模糊，加"大概"
        clarity=0.4 → 明显模糊，细节丢失
        clarity=0.1 → 几乎忘记，只剩印象
        """
        if clarity >= 0.9:
            return content  # 清晰记忆，原文

        if clarity >= 0.7:
            # 稍微模糊 — 加不确定词
            prefixes = ["好像", "大概", "似乎", "记得"]
            prefix = random.choice(prefixes)
            return f"{prefix}{content}"

        if clarity >= 0.4:
            # 明显模糊 — 截断细节
            if len(content) > 20:
                # 保留前半，后半用省略
                mid = len(content) // 2
                return f"隐约记得{content[:mid]}...具体的忘了"
            return f"隐约记得{content}"

        if clarity >= 0.1:
            # 几乎忘记 — 只剩模糊印象
            # 提取前几个字作为印象
            hint = content[:6] if len(content) > 6 else content
            return f"依稀记得跟「{hint}」有关..."

        return "完全想不起来了..."

    # ================================================================
    # 情感渲染（Emotional Rendering）
    # ================================================================

    def emotional_render(self, content: str, valence: float = 0.0) -> str:
        """
        根据情感效价渲染记忆内容。

        正面情感(valence>0) → 记忆变得更美好
        负面情感(valence<0) → 记忆变得更消极
        中性(valence≈0) → 不变

        模拟人类倾向：好的记忆越想越好，坏的记忆越想越坏。
        """
        if abs(valence) < 0.2:
            return content  # 情感太弱，不渲染

        if valence > 0.6:
            # 强烈正面 — 美化
            enhancers = ["那次真的很开心，", "想起来就觉得温暖，", "特别美好的回忆，"]
            return random.choice(enhancers) + content
        elif valence > 0.2:
            # 轻微正面
            return content  # 轻微正面不过度渲染

        elif valence < -0.6:
            # 强烈负面 — 加重
            intensifiers = ["那次真的很难受，", "想起来还是有点难过，"]
            return random.choice(intensifiers) + content
        elif valence < -0.2:
            # 轻微负面
            return content  # 轻微负面不过度渲染

        return content

    # ================================================================
    # 综合方法
    # ================================================================

    def recall_with_effects(self, key: str) -> Optional[str]:
        """
        带所有效果的完整回忆。

        1. 查找FadingMemory
        2. 计算清晰度
        3. 记忆变形
        4. 情感渲染
        5. 记录访问
        """
        fm = self.get_fading_memory(key)
        if not fm:
            return None

        # 变形
        content = self.distort_memory(fm.content, fm.clarity)

        # 情感渲染
        content = self.emotional_render(content, fm.emotional_valence)

        # 记录访问
        self.record_access(key)

        return content

    # ================================================================
    # 持久化
    # ================================================================

    def cleanup(self, max_entries: int = 5000) -> int:
        """清理太旧的权重记录。"""
        if len(self._weights) <= max_entries:
            return 0
        sorted_keys = sorted(self._weights.keys(),
                             key=lambda k: self._weights[k]["last_access"])
        to_remove = sorted_keys[:len(self._weights) - max_entries]
        for k in to_remove:
            del self._weights[k]
        self._save()
        return len(to_remove)

    def _load(self) -> None:
        if self._path.exists():
            try:
                self._weights = json.loads(self._path.read_text(encoding="utf-8"))
            except Exception:
                pass
        if self._fading_path.exists():
            try:
                data = json.loads(self._fading_path.read_text(encoding="utf-8"))
                for k, v in data.items():
                    self._fading[k] = FadingMemory(**v)
            except Exception:
                pass

    def _save(self) -> None:
        try:
            self._path.write_text(
                json.dumps(self._weights, ensure_ascii=False), encoding="utf-8"
            )
        except Exception:
            pass

    def _save_fading(self) -> None:
        try:
            data = {k: asdict(v) for k, v in self._fading.items()}
            self._fading_path.write_text(
                json.dumps(data, ensure_ascii=False), encoding="utf-8"
            )
        except Exception:
            pass

    @property
    def stats(self) -> dict:
        total_fading = len(self._fading)
        clear = sum(1 for fm in self._fading.values() if fm.clarity >= 0.7)
        fuzzy = sum(1 for fm in self._fading.values() if 0.3 <= fm.clarity < 0.7)
        fading = sum(1 for fm in self._fading.values() if fm.clarity < 0.3)
        return {
            "total_weights": len(self._weights),
            "total_fading": total_fading,
            "clear_memories": clear,
            "fuzzy_memories": fuzzy,
            "fading_memories": fading,
        }


# ================================================================
# 自动发现接口
# ================================================================

class MemoryWeightModule(MemoryModule):
    name = "memory_weight"

    def init(self, data_dir="data/memory", **kwargs):
        self._impl = MemoryWeight(data_dir=data_dir)

    def on_message(self, user_msg="", ai_reply=""):
        if hasattr(self, '_impl') and self._impl._weights and len(self._impl._weights) % 100 == 0:
            self._impl.cleanup()


MODULE = MemoryWeightModule
