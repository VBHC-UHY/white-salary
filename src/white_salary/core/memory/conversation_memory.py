"""
white_salary/core/memory/conversation_memory.py

对话记忆标记 — 标记对话中的重要时刻+情感标签。

借鉴v2的features/conversation_memory.py：
  - 不同于conversation_log（只记录所有消息）
  - 只标记"值得记住的重要时刻"
  - 每个时刻带情感标签+重要度+参与者
  - 支持按情感/时间/人物检索重要时刻

自动发现：导出MODULE供MemoryManager加载。
"""

import json
import re
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional

from loguru import logger
from white_salary.core.memory.module_base import MemoryModule


@dataclass
class ConversationMoment:
    """对话中的重要时刻。"""
    moment_id: str = ""
    content: str = ""                   # 触发这个时刻的消息
    ai_response: str = ""               # AI当时的回复
    emotion_label: str = "neutral"      # 情感标签
    importance: int = 5                 # 重要度 1-10
    moment_type: str = ""               # 时刻类型
    user_id: str = ""
    user_name: str = ""
    timestamp: float = 0.0


# 重要时刻检测规则
_MOMENT_RULES = {
    "confession": {
        "keywords": ["喜欢你", "爱你", "表白", "在一起", "心动"],
        "importance": 10,
        "emotion": "love",
    },
    "secret_sharing": {
        "keywords": ["秘密", "只告诉你", "别说出去", "悄悄话", "不要告诉"],
        "importance": 9,
        "emotion": "trust",
    },
    "achievement": {
        "keywords": ["考上", "通过了", "成功了", "录取", "升职", "赢了", "满分", "考过了", "拿到了"],
        "importance": 8,
        "emotion": "pride",
    },
    "comfort_seeking": {
        "keywords": ["好难过", "想哭", "崩溃", "不想活", "太累了", "受不了"],
        "importance": 8,
        "emotion": "sadness",
    },
    "gratitude": {
        "keywords": ["谢谢你", "多亏你", "感谢你", "你真好", "太好了你"],
        "importance": 7,
        "emotion": "gratitude",
    },
    "promise": {
        "keywords": ["答应我", "约好了", "保证", "说好的", "一定"],
        "importance": 7,
        "emotion": "trust",
    },
    "personal_info": {
        "keywords": ["我的名字", "我叫", "我的生日", "我住在", "我的职业"],
        "importance": 7,
        "emotion": "neutral",
    },
    "conflict": {
        "keywords": ["生气了", "吵架", "对不起", "道歉", "误会"],
        "importance": 7,
        "emotion": "anger",
    },
    "milestone": {
        "keywords": ["第一次", "从来没", "终于", "一直想", "梦想"],
        "importance": 6,
        "emotion": "anticipation",
    },
    "deep_conversation": {
        "keywords": ["说真的", "认真说", "其实我", "一直没说", "心里话"],
        "importance": 6,
        "emotion": "trust",
    },
}

# 最大存储数
MAX_MOMENTS = 500


class ConversationMemoryStore:
    """
    对话记忆标记存储。

    使用方式:
        store = ConversationMemoryStore(data_dir)
        moments = store.detect_and_mark("我考上研究生了！！", "太好了恭喜！")
        important = store.get_by_type("achievement")
    """

    def __init__(self, data_dir: str = "data/memory") -> None:
        self._data_path = Path(data_dir) / "conversation_moments.json"
        self._data_path.parent.mkdir(parents=True, exist_ok=True)
        self._moments: list[ConversationMoment] = []
        self._load()

    def detect_and_mark(self, user_msg: str, ai_reply: str = "",
                        user_id: str = "", user_name: str = "") -> list[ConversationMoment]:
        """
        检测消息中是否有重要时刻，自动标记。

        Returns:
            检测到的重要时刻列表
        """
        if not user_msg or len(user_msg) < 3:
            return []

        detected = []
        for moment_type, rule in _MOMENT_RULES.items():
            for kw in rule["keywords"]:
                if kw in user_msg:
                    moment = ConversationMoment(
                        moment_id=f"moment_{int(time.time() * 1000)}_{moment_type}",
                        content=user_msg[:200],
                        ai_response=ai_reply[:200] if ai_reply else "",
                        emotion_label=rule["emotion"],
                        importance=rule["importance"],
                        moment_type=moment_type,
                        user_id=user_id,
                        user_name=user_name,
                        timestamp=time.time(),
                    )
                    # 去重：同类型5分钟内不重复标记
                    if not self._is_duplicate(moment_type):
                        self._moments.append(moment)
                        detected.append(moment)
                    break  # 一个类型只标记一次

        if detected:
            self._trim()
            self._save()
            logger.debug(
                f"[ConvMemory] 标记{len(detected)}个重要时刻: "
                f"{[m.moment_type for m in detected]}"
            )

        return detected

    def _is_duplicate(self, moment_type: str, window: int = 300) -> bool:
        """检查是否在窗口期内已标记过同类型。"""
        now = time.time()
        for m in reversed(self._moments):
            if now - m.timestamp > window:
                break
            if m.moment_type == moment_type:
                return True
        return False

    # ================================================================
    # 检索
    # ================================================================

    def get_by_type(self, moment_type: str, limit: int = 20) -> list[ConversationMoment]:
        """按类型检索。"""
        results = [m for m in self._moments if m.moment_type == moment_type]
        return results[-limit:]

    def get_by_emotion(self, emotion: str, limit: int = 20) -> list[ConversationMoment]:
        """按情感检索。"""
        results = [m for m in self._moments if m.emotion_label == emotion]
        return results[-limit:]

    def get_by_user(self, user_id: str, limit: int = 20) -> list[ConversationMoment]:
        """按用户检索。"""
        results = [m for m in self._moments if m.user_id == user_id]
        return results[-limit:]

    def get_important(self, min_importance: int = 7,
                      limit: int = 20) -> list[ConversationMoment]:
        """获取高重要度的时刻。"""
        results = [m for m in self._moments if m.importance >= min_importance]
        results.sort(key=lambda m: m.importance, reverse=True)
        return results[:limit]

    def get_recent(self, limit: int = 10) -> list[ConversationMoment]:
        """获取最近的时刻。"""
        return self._moments[-limit:]

    def format_moments(self, moments: list[ConversationMoment]) -> str:
        """格式化为可注入的文本。"""
        if not moments:
            return ""
        lines = ["[重要对话时刻]"]
        for m in moments:
            from white_salary.core.memory.xml_formatter import relative_time
            time_str = relative_time(m.timestamp)
            lines.append(f"  ({time_str}) [{m.moment_type}] {m.content[:60]}")
        return "\n".join(lines)

    # ================================================================
    # 持久化
    # ================================================================

    def _trim(self) -> None:
        if len(self._moments) > MAX_MOMENTS:
            self._moments = self._moments[-MAX_MOMENTS:]

    def _save(self) -> None:
        try:
            data = [asdict(m) for m in self._moments]
            self._data_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            logger.warning(f"[ConvMemory] 保存失败: {e}")

    def _load(self) -> None:
        if not self._data_path.exists():
            return
        try:
            data = json.loads(self._data_path.read_text(encoding="utf-8"))
            for d in data:
                self._moments.append(ConversationMoment(**d))
            logger.debug(f"[ConvMemory] 加载: {len(self._moments)}个时刻")
        except Exception as e:
            logger.warning(f"[ConvMemory] 加载失败: {e}")

    @property
    def stats(self) -> dict:
        type_dist = {}
        for m in self._moments:
            type_dist[m.moment_type] = type_dist.get(m.moment_type, 0) + 1
        return {
            "total_moments": len(self._moments),
            "type_distribution": type_dist,
        }


# ================================================================
# 自动发现接口
# ================================================================

class ConversationMemoryModule(MemoryModule):
    """对话记忆标记模块 — 自动发现注册。"""
    name = "conversation_memory"

    def init(self, data_dir="data/memory", **kwargs):
        self._impl = ConversationMemoryStore(data_dir=data_dir)

    def get_context_prompt(self, message: str = "") -> str:
        """注入最近的重要时刻。"""
        if not hasattr(self, '_impl'):
            return ""
        important = self._impl.get_important(min_importance=8, limit=3)
        return self._impl.format_moments(important)

    def on_message(self, user_msg: str = "", ai_reply: str = "") -> None:
        if not user_msg or not hasattr(self, '_impl'):
            return
        self._impl.detect_and_mark(user_msg, ai_reply)


MODULE = ConversationMemoryModule
