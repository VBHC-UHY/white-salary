"""
white_salary/core/memory/enhanced/forgetting.py

Ebbinghaus遗忘曲线 — 记忆随时间自然衰减。

借鉴v2的enhanced/forgetting.py：
  - 每条记忆有权重（base_weight × recency × frequency × emotional）
  - 情感记忆衰减慢（emotional_retention=0.5）
  - 被访问的记忆权重提升（access_boost=0.1）
  - 权重低于阈值进入冷存储（不删除但检索时跳过）
  - 重要记忆受保护（protect_important=true）

配置从 config/memory_settings.json 的 forgetting 节读取。

自动发现：导出MODULE供MemoryManager加载。
"""

import json
import math
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional

from loguru import logger
from white_salary.core.memory.module_base import MemoryModule


@dataclass
class MemoryWeight:
    """一条记忆的权重信息。"""
    memory_key: str = ""
    base_weight: float = 1.0
    access_count: int = 0
    created_at: float = 0.0
    last_accessed: float = 0.0
    is_important: bool = False
    is_cold: bool = False           # 进入冷存储
    emotional_intensity: float = 0.0  # 情感强度0-1
    category: str = ""               # 记忆分类


class ForgettingEngine:
    """
    遗忘引擎 — Ebbinghaus遗忘曲线实现。

    使用方式:
        engine = ForgettingEngine(config, data_dir)
        engine.record_access("memory_key_123")
        weight = engine.get_weight("memory_key_123")
        if engine.is_cold("memory_key_123"):
            # 这条记忆已经快被忘记了
    """

    def __init__(self, config: dict = None, data_dir: str = "data/memory") -> None:
        cfg = config or {}
        self._forgetting_rate = cfg.get("forgetting_rate", 0.1)
        self._cold_threshold = cfg.get("cold_threshold", 0.2)
        self._emotional_retention = cfg.get("emotional_retention", 0.5)
        self._access_boost = cfg.get("access_boost", 0.1)
        self._protect_important = cfg.get("protect_important", True)

        self._data_path = Path(data_dir) / "forgetting_weights.json"
        self._data_path.parent.mkdir(parents=True, exist_ok=True)
        self._weights: dict[str, MemoryWeight] = {}
        self._load()

    def record_access(self, key: str, emotional_intensity: float = 0.0,
                      is_important: bool = False, category: str = "") -> None:
        """记录一次记忆访问（提升权重）。"""
        now = time.time()
        if key not in self._weights:
            self._weights[key] = MemoryWeight(
                memory_key=key,
                base_weight=1.0,
                created_at=now,
                last_accessed=now,
                emotional_intensity=emotional_intensity,
                is_important=is_important,
                category=category,
            )
        w = self._weights[key]
        w.access_count += 1
        w.last_accessed = now
        if emotional_intensity > w.emotional_intensity:
            w.emotional_intensity = emotional_intensity
        if is_important:
            w.is_important = True
        if category:
            w.category = category
        # 访问后取消冷存储
        w.is_cold = False
        self._save_debounced()

    def get_weight(self, key: str) -> float:
        """
        计算记忆的当前权重。

        公式: weight = base × recency_factor × frequency_factor × emotional_factor
          - recency: exp(-forgetting_rate × days)
          - frequency: 1 + access_boost × log(1 + count)
          - emotional: 1 + emotional_retention × intensity
        """
        w = self._weights.get(key)
        if not w:
            return 0.5  # 未追踪的记忆默认权重

        now = time.time()
        days_since = (now - w.last_accessed) / 86400

        # 时间衰减（Ebbinghaus）
        recency = math.exp(-self._forgetting_rate * days_since)

        # 频率加成
        frequency = 1 + self._access_boost * math.log(1 + w.access_count)

        # 情感保留
        emotional = 1 + self._emotional_retention * w.emotional_intensity

        # 重要记忆保护
        if w.is_important and self._protect_important:
            recency = max(recency, 0.5)  # 重要记忆至少保留50%

        weight = w.base_weight * recency * frequency * emotional
        return min(weight, 5.0)  # 上限5.0

    def is_cold(self, key: str) -> bool:
        """检查记忆是否进入冷存储。"""
        w = self._weights.get(key)
        if not w:
            return False
        if w.is_important and self._protect_important:
            return False
        return self.get_weight(key) < self._cold_threshold

    def update_cold_status(self) -> int:
        """批量更新所有记忆的冷存储状态。返回新进入冷存储的数量。"""
        new_cold = 0
        for key, w in self._weights.items():
            was_cold = w.is_cold
            w.is_cold = self.is_cold(key)
            if w.is_cold and not was_cold:
                new_cold += 1
        if new_cold:
            self._save()
            logger.debug(f"[Forgetting] {new_cold} 条记忆进入冷存储")
        return new_cold

    def get_hot_memories(self, limit: int = 20) -> list[str]:
        """获取权重最高的记忆key列表。"""
        scored = [(self.get_weight(k), k) for k, w in self._weights.items() if not w.is_cold]
        scored.sort(key=lambda x: x[0], reverse=True)
        return [k for _, k in scored[:limit]]

    def get_cold_memories(self) -> list[str]:
        """获取所有冷存储记忆的key列表。"""
        return [k for k, w in self._weights.items() if w.is_cold]

    @property
    def stats(self) -> dict:
        total = len(self._weights)
        cold = sum(1 for w in self._weights.values() if w.is_cold)
        important = sum(1 for w in self._weights.values() if w.is_important)
        return {"total": total, "cold": cold, "important": important, "hot": total - cold}

    # ================================================================
    # 持久化
    # ================================================================

    _save_counter = 0

    def _save_debounced(self) -> None:
        """防抖保存（每50次操作保存一次）。"""
        self._save_counter += 1
        if self._save_counter % 50 == 0:
            self._save()

    def _save(self) -> None:
        try:
            data = {k: asdict(v) for k, v in self._weights.items()}
            self._data_path.write_text(
                json.dumps(data, ensure_ascii=False), encoding="utf-8"
            )
        except Exception:
            pass

    def _load(self) -> None:
        if self._data_path.exists():
            try:
                data = json.loads(self._data_path.read_text(encoding="utf-8"))
                for k, v in data.items():
                    self._weights[k] = MemoryWeight(**v)
            except Exception:
                pass


# ================================================================
# 自动发现接口
# ================================================================

class ForgettingModule(MemoryModule):
    name = "forgetting_engine"

    def init(self, data_dir="data/memory", **kwargs):
        # 从MemoryManager获取配置
        config = {}
        try:
            import json
            from pathlib import Path
            cfg_path = Path("config/memory_settings.json")
            if cfg_path.exists():
                all_cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
                config = all_cfg.get("forgetting", {})
        except Exception:
            pass
        self._impl = ForgettingEngine(config=config, data_dir=data_dir)

    def on_message(self, user_msg="", ai_reply=""):
        # 每次对话后更新冷存储状态（偶尔）
        if hasattr(self, '_impl'):
            import random
            if random.random() < 0.1:  # 10%概率检查
                self._impl.update_cold_status()


MODULE = ForgettingModule
