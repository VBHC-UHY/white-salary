"""
white_salary/core/plugins/context.py

插件上下文 — 给插件提供安全的只读API。

借鉴v2的plugins/context.py：
  - 插件不能直接访问MemoryManager/AffinityManager
  - 只能通过这个上下文读取数据
  - 所有返回值都是安全的副本
"""

from typing import Any, Optional

from loguru import logger


class PluginContext:
    """
    插件安全上下文 — 插件通过这个访问系统数据。

    使用方式（在插件里）:
        class MyPlugin(Plugin):
            async def on_message(self, text, user_id=""):
                # 通过context访问系统数据
                mood = self.context.get_current_mood()
                affinity = self.context.get_user_affinity(user_id)
    """

    def __init__(self) -> None:
        self._message_context: dict[str, Any] = {}

    def set_message_context(self, metadata: Optional[dict[str, Any]]) -> None:
        """设置当前消息的只读上下文，由 PluginManager 在调用钩子前注入。"""
        self._message_context = dict(metadata or {})

    def clear_message_context(self) -> None:
        """清空当前消息上下文，避免跨消息串台。"""
        self._message_context = {}

    def get_message_context(self) -> dict[str, Any]:
        """获取当前消息上下文副本，如 platform/group_id/is_group 等。"""
        return dict(self._message_context)

    def get_message_context_value(self, key: str, default: Any = None) -> Any:
        """读取单个消息上下文字段。"""
        return self._message_context.get(key, default)

    def get_bot_name(self) -> str:
        """获取bot名字。"""
        return "白"

    def get_user_affinity(self, user_id: str) -> float:
        """获取用户好感度分数。"""
        try:
            from white_salary.core.affinity.manager import AffinityManager
            aff = AffinityManager.get_for_user(user_id)
            return aff.get_stats().get("points", 0)
        except Exception:
            return 0.0

    def get_user_affinity_level(self, user_id: str) -> str:
        """获取用户好感度等级名。"""
        try:
            from white_salary.core.affinity.manager import AffinityManager
            aff = AffinityManager.get_for_user(user_id)
            return aff.get_stats().get("level_name", "陌生人")
        except Exception:
            return "陌生人"

    def get_current_mood(self) -> str:
        """获取当前心情。"""
        try:
            # 2026-07-03 审计修复（批5）：EmotionTracker() 现返回进程级共享实例
            # （按data_dir缓存），不再每次调用都重建组件/重读json
            from white_salary.core.memory.emotion_tracker import EmotionTracker
            tracker = EmotionTracker()
            return tracker.current_emotion
        except Exception:
            return "neutral"

    def get_mood_score(self) -> int:
        """获取心情分数(0-100)。"""
        try:
            # 2026-07-03 审计修复（批5）：EmotionTracker() 现返回进程级共享实例
            from white_salary.core.memory.emotion_tracker import EmotionTracker
            tracker = EmotionTracker()
            return tracker.mood_score
        except Exception:
            return 80

    def search_memory(self, query: str, limit: int = 5) -> list[dict]:
        """搜索记忆（返回安全副本）。"""
        try:
            # 2026-07-03 审计修复（批5）：LongTermMemoryStore() 现返回进程级共享实例
            # （按data_dir缓存），不再每次搜索都重开ChromaDB/SQLite
            from white_salary.core.memory.long_term_store import LongTermMemoryStore
            store = LongTermMemoryStore()
            results = store.search(query, limit=limit)
            return [{"content": r.content, "time": r.created_at} for r in results]
        except Exception:
            return []
