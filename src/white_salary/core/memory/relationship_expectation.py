"""
white_salary/core/memory/relationship_expectation.py

关系期望 — 对不同人有不同的期望和互动方式。

借鉴v2的features/relationship_expectation.py（506行）：
  - 根据好感度/互动频率/关系类型设定期望
  - 亲密的人期望更高（不回复会失望）
  - 陌生人期望低（不回复也没关系）
  - 影响主动聊天的频率和语气

不用LLM，纯规则/数据。

自动发现：导出MODULE供MemoryManager加载。
"""

import json
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

from loguru import logger
from white_salary.core.memory.module_base import MemoryModule


@dataclass
class RelationExpectation:
    """对某人的关系期望。"""
    user_id: str = ""
    user_name: str = ""
    relationship_level: str = "stranger"  # stranger/acquaintance/friend/close_friend/family
    reply_expectation: float = 0.5       # 期望对方回复的程度 0-1
    initiative_level: float = 0.3        # 主动联系的意愿 0-1
    formality: float = 0.5               # 正式程度 0(随意)-1(正式)
    total_interactions: int = 0
    last_interaction: float = 0.0


# 关系等级→默认期望
_LEVEL_DEFAULTS = {
    "stranger": {"reply_expectation": 0.2, "initiative_level": 0.1, "formality": 0.8},
    "acquaintance": {"reply_expectation": 0.4, "initiative_level": 0.2, "formality": 0.5},
    "friend": {"reply_expectation": 0.6, "initiative_level": 0.5, "formality": 0.3},
    "close_friend": {"reply_expectation": 0.8, "initiative_level": 0.7, "formality": 0.1},
    "family": {"reply_expectation": 0.9, "initiative_level": 0.8, "formality": 0.0},
}

# 互动次数→自动升级关系
_LEVEL_THRESHOLDS = {
    5: "acquaintance",
    30: "friend",
    100: "close_friend",
}


class RelationExpectationStore:
    """关系期望存储。"""

    def __init__(self, data_dir: str = "data/memory") -> None:
        self._data_path = Path(data_dir) / "relation_expectations.json"
        self._data_path.parent.mkdir(parents=True, exist_ok=True)
        self._expectations: dict[str, RelationExpectation] = {}
        self._load()

    def on_interaction(self, user_id: str, user_name: str = "") -> RelationExpectation:
        """记录一次互动，自动更新关系等级。"""
        if user_id not in self._expectations:
            self._expectations[user_id] = RelationExpectation(
                user_id=user_id,
                user_name=user_name,
                last_interaction=time.time(),
            )
        exp = self._expectations[user_id]
        exp.total_interactions += 1
        exp.last_interaction = time.time()
        if user_name:
            exp.user_name = user_name

        # 自动升级（优先用好感度，其次用互动次数）
        affinity_level = self._get_affinity_level(user_id)
        if affinity_level is not None:
            # 好感度→关系等级映射
            aff_map = {
                99: "family", 5: "close_friend", 4: "close_friend",
                3: "friend", 2: "friend", 1: "acquaintance",
                0: "stranger", -1: "stranger",
            }
            mapped = aff_map.get(affinity_level, "stranger")
            if mapped != exp.relationship_level and exp.relationship_level != "family":
                exp.relationship_level = mapped
                defaults = _LEVEL_DEFAULTS.get(mapped, {})
                exp.reply_expectation = defaults.get("reply_expectation", exp.reply_expectation)
                exp.initiative_level = defaults.get("initiative_level", exp.initiative_level)
                exp.formality = defaults.get("formality", exp.formality)
        else:
            # 没有好感度数据时用互动次数
            for threshold, level in sorted(_LEVEL_THRESHOLDS.items()):
                if exp.total_interactions >= threshold:
                    if exp.relationship_level != level and exp.relationship_level != "family":
                        exp.relationship_level = level
                        defaults = _LEVEL_DEFAULTS.get(level, {})
                        exp.reply_expectation = defaults.get("reply_expectation", exp.reply_expectation)
                        exp.initiative_level = defaults.get("initiative_level", exp.initiative_level)
                        exp.formality = defaults.get("formality", exp.formality)

        self._save_debounced()
        return exp

    def get_expectation(self, user_id: str) -> Optional[RelationExpectation]:
        return self._expectations.get(user_id)

    def set_level(self, user_id: str, level: str) -> None:
        """手动设置关系等级。"""
        if user_id in self._expectations and level in _LEVEL_DEFAULTS:
            exp = self._expectations[user_id]
            exp.relationship_level = level
            defaults = _LEVEL_DEFAULTS[level]
            exp.reply_expectation = defaults["reply_expectation"]
            exp.initiative_level = defaults["initiative_level"]
            exp.formality = defaults["formality"]
            self._save()

    def get_prompt(self, user_id: str) -> str:
        """生成关系期望提示。"""
        exp = self._expectations.get(user_id)
        if not exp or exp.total_interactions < 3:
            return ""
        level_cn = {
            "stranger": "陌生人", "acquaintance": "认识的人",
            "friend": "朋友", "close_friend": "好朋友", "family": "家人",
        }
        return f"[与{exp.user_name}的关系: {level_cn.get(exp.relationship_level, '未知')}]"

    @staticmethod
    def _get_affinity_level(user_id: str) -> int | None:
        """获取用户的好感度等级（None=无数据）。"""
        try:
            from white_salary.core.affinity.manager import AffinityManager
            aff = AffinityManager.get_for_user(user_id)
            stats = aff.get_stats()
            if stats.get("is_family"):
                return 99
            return stats.get("level_value", None)
        except Exception:
            return None

    _save_counter = 0

    def _save_debounced(self) -> None:
        self._save_counter += 1
        if self._save_counter % 10 == 0:
            self._save()

    def _save(self) -> None:
        try:
            data = {k: asdict(v) for k, v in self._expectations.items()}
            self._data_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _load(self) -> None:
        if self._data_path.exists():
            try:
                data = json.loads(self._data_path.read_text(encoding="utf-8"))
                for k, d in data.items():
                    self._expectations[k] = RelationExpectation(**d)
            except Exception:
                pass

    @property
    def stats(self) -> dict:
        levels = {}
        for exp in self._expectations.values():
            levels[exp.relationship_level] = levels.get(exp.relationship_level, 0) + 1
        return {"total_users": len(self._expectations), "level_distribution": levels}


class RelationExpectationModule(MemoryModule):
    name = "relation_expectation"

    def init(self, data_dir: str = "data/memory", **kwargs) -> None:
        self._impl = RelationExpectationStore(data_dir=data_dir)

    def get_context_prompt(self, message: str = "",
                           user_id: str = "desktop",
                           is_group: bool = False) -> str:
        """
        2026-07-02 审计修复（批4）：BUG2残留——旧签名写死"desktop"，
        对任意QQ用户注入的都是desktop的关系期望。改新签名按真实user_id
        返回对应关系提示（参照emotion_memory.py:278已修写法）。
        """
        if not hasattr(self, '_impl'):
            return ""
        return self._impl.get_prompt(user_id)

    def on_message(self, user_msg: str = "", ai_reply: str = "",
                   user_id: str = "desktop",
                   is_group: bool = False) -> None:
        """2026-07-02 审计修复（批4）：改新签名，互动记录到真实user_id名下。"""
        if user_msg and hasattr(self, '_impl'):
            self._impl.on_interaction(user_id, "用户")


MODULE = RelationExpectationModule
