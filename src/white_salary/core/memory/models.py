"""
white_salary/core/memory/models.py

记忆数据模型 — 统一的数据结构/schema定义。

借鉴v2的memory/models.py（207行）：
  - MemoryEntry: 统一记忆格式（所有store通用）
  - MemorySource: 记忆来源枚举
  - MemoryCategory: 记忆分类枚举
  - 各store数据→统一格式的转换工具

所有模块共用这些数据结构。
"""

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional, Any


# ================================================================
# 枚举
# ================================================================

class MemorySource(str, Enum):
    """记忆来源。"""
    USER_SAID = "user_said"             # 用户直接说的
    AI_EXTRACTED = "ai_extracted"       # AI从对话提取的
    LLM_ANALYZED = "llm_analyzed"       # LLM分析得到的
    SYSTEM_DETECTED = "system_detected" # 系统自动检测的
    USER_COMMAND = "user_command"        # 用户命令添加的
    CONTEXT_REVIEW = "context_review"   # 上下文审查提取的
    TOOL_RESULT = "tool_result"         # 工具返回的
    IMPORTED = "imported"               # 导入的


class MemoryCategory(str, Enum):
    """记忆分类（与auto_classifier一致）。"""
    PERSON = "person"
    EVENT = "event"
    PROMISE = "promise"
    SECRET = "secret"
    KNOWLEDGE = "knowledge"
    EMOTION = "emotion"
    BASIC_INFO = "basic_info"
    PREFERENCE = "preference"
    RELATIONSHIP = "relationship"
    HABIT = "habit"
    MILESTONE = "milestone"
    OTHER = "other"


class MemoryLayer(str, Enum):
    """记忆存储层。"""
    CORE = "core"               # 核心记忆（永久）
    IMPORTANT = "important"     # 重要记忆（承诺/约定）
    LONG_TERM = "long_term"     # 长期记忆（可过期）
    SHORT_TERM = "short_term"   # 短期记忆（对话内）


class EmotionType(str, Enum):
    """情感类型。"""
    JOY = "joy"
    SADNESS = "sadness"
    ANGER = "anger"
    FEAR = "fear"
    SURPRISE = "surprise"
    LOVE = "love"
    GRATITUDE = "gratitude"
    PRIDE = "pride"
    NOSTALGIA = "nostalgia"
    NEUTRAL = "neutral"


# ================================================================
# 统一数据结构
# ================================================================

@dataclass
class MemoryEntry:
    """
    统一记忆格式 — 所有store通用。

    不管是CoreMemory、LongTermMemory还是ImportantMemory，
    都可以转成这个统一格式来处理。
    """
    id: str = ""                        # 唯一标识
    content: str = ""                   # 记忆内容
    category: str = "other"             # 分类
    layer: str = "long_term"            # 存储层
    source: str = "user_said"           # 来源
    importance: int = 5                 # 重要度 1-10
    emotion_type: str = "neutral"       # 情感类型
    emotion_intensity: float = 0.0      # 情感强度 0-1
    keywords: list[str] = field(default_factory=list)  # 关键词
    people: list[str] = field(default_factory=list)     # 涉及人物
    user_id: str = ""                   # 关联用户
    created_at: float = 0.0             # 创建时间
    updated_at: float = 0.0             # 更新时间
    expires_at: float = 0.0             # 过期时间（0=永不过期）
    access_count: int = 0               # 访问次数
    metadata: dict = field(default_factory=dict)  # 扩展元数据

    def to_dict(self) -> dict:
        """转为字典。"""
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> "MemoryEntry":
        """从字典创建。"""
        # 只取已知字段
        known_fields = {f.name for f in MemoryEntry.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known_fields}
        return MemoryEntry(**filtered)

    @property
    def is_expired(self) -> bool:
        """是否已过期。"""
        import time
        if self.expires_at <= 0:
            return False
        return time.time() > self.expires_at

    @property
    def is_important(self) -> bool:
        """是否重要。"""
        return self.importance >= 7


# ================================================================
# 转换工具
# ================================================================

def core_entry_to_memory(key: str, value: str, category: str = "",
                         **kwargs) -> MemoryEntry:
    """CoreMemoryStore的条目 → 统一格式。"""
    import time
    return MemoryEntry(
        id=f"core_{key}",
        content=f"{key}: {value}",
        category=category or "basic_info",
        layer="core",
        source="user_said",
        importance=8,
        created_at=kwargs.get("created_at", time.time()),
        **{k: v for k, v in kwargs.items() if k in MemoryEntry.__dataclass_fields__},
    )


def long_term_to_memory(entry_id: int, content: str, layer: str = "fact",
                         **kwargs) -> MemoryEntry:
    """LongTermMemoryStore的条目 → 统一格式。"""
    import time
    importance_map = {"fact": 7, "event": 5, "emotion": 4, "temp": 2}
    return MemoryEntry(
        id=f"lt_{entry_id}",
        content=content,
        layer="long_term",
        source=kwargs.get("source", "ai_extracted"),
        importance=importance_map.get(layer, 5),
        created_at=kwargs.get("created_at", time.time()),
        **{k: v for k, v in kwargs.items()
           if k in MemoryEntry.__dataclass_fields__ and k not in ("source", "created_at")},
    )


def important_to_memory(entry_id: str, content: str, category: str = "promise",
                         **kwargs) -> MemoryEntry:
    """ImportantMemoryStore的条目 → 统一格式。"""
    import time
    return MemoryEntry(
        id=f"imp_{entry_id}",
        content=content,
        category=category,
        layer="important",
        source="ai_extracted",
        importance=8,
        created_at=kwargs.get("created_at", time.time()),
        **{k: v for k, v in kwargs.items()
           if k in MemoryEntry.__dataclass_fields__ and k not in ("created_at",)},
    )
