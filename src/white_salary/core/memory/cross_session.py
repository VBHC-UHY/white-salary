"""
white_salary/core/memory/cross_session.py

跨会话记忆关联 — 自动关联不同对话中提到的同一人/事件。

功能：
  - 检测当前对话中提到的人名/事件是否在之前的记忆中出现过
  - 如果出现过，把相关的历史记忆一起注入到上下文中
  - 帮助AI在不同对话间保持连贯性

实现：基于关键词/人名匹配长期记忆和知识图谱。
"""

from typing import Optional

from loguru import logger

from white_salary.core.memory.long_term_store import LongTermMemoryStore
from white_salary.core.memory.knowledge_graph import KnowledgeGraph


class CrossSessionLinker:
    """
    跨会话记忆关联器。

    在当前对话中检测到已知人名/关键词时，自动检索关联记忆。
    """

    def __init__(
        self,
        long_term: LongTermMemoryStore,
        knowledge_graph: KnowledgeGraph,
    ) -> None:
        self._long_term = long_term
        self._kg = knowledge_graph

    def find_related_memories(self, text: str, limit: int = 5) -> str:
        """
        查找与当前文本相关的跨会话记忆。

        检查文本中是否提到了知识图谱中的已知人物，
        如果提到了，检索与该人物相关的长期记忆。

        Args:
            text: 当前对话文本
            limit: 最多返回多少条关联记忆

        Returns:
            关联记忆的文本（空字符串=没有关联）
        """
        if not text:
            return ""

        # 1. 检查知识图谱中的人物是否被提到
        try:
            all_persons = self._kg.get_all_entities()
        except Exception:
            return ""
        mentioned_persons = []

        for person in all_persons:
            name = person.get("name", "") if isinstance(person, dict) else getattr(person, "name", "")
            if name and name in text:
                mentioned_persons.append(name)

        if not mentioned_persons:
            return ""

        # 2. 用人名检索长期记忆
        related_memories = []
        for person in mentioned_persons:
            person_name = person if isinstance(person, str) else getattr(person, 'name', str(person))
            results = self._long_term.search(person_name, limit=3)
            for r in results:
                if r.content not in [m.content for m in related_memories]:
                    related_memories.append(r)

        if not related_memories:
            return ""

        # 3. 格式化输出
        lines = ["[跨会话关联记忆 — 之前对话中提到的相关信息]"]
        for m in related_memories[:limit]:
            lines.append(f"  {m.content}")

        result = "\n".join(lines)
        logger.debug(f"[CrossSession] 关联了 {len(related_memories)} 条记忆（提到: {mentioned_persons}）")
        return result


class DynamicRenderer:
    """
    记忆动态渲染 — 根据当前场景调整注入的记忆内容。

    不是每次都注入全部核心记忆，而是根据当前话题
    选择最相关的记忆注入，减少token浪费。
    """

    def __init__(self, core_store, long_term_store) -> None:
        self._core = core_store
        self._long_term = long_term_store

    def render_context(self, current_message: str, max_entries: int = 15) -> str:
        """
        动态渲染记忆上下文。

        Args:
            current_message: 当前用户消息
            max_entries: 最多注入多少条记忆

        Returns:
            渲染后的记忆上下文文本
        """
        if not current_message:
            # 没有具体话题时，返回最重要的记忆
            return self._core.get_context_string()

        parts = []

        # 1. 总是注入基本信息（名字、关系等）
        basic = self._core.get_by_category("basic_info")
        if basic:
            lines = ["[用户基本信息]"]
            for e in basic[:5]:
                lines.append(f"  {e.key}: {e.value}")
            parts.append("\n".join(lines))

        # 2. 根据话题检索相关记忆
        relevant_core = self._core.search(current_message)
        if relevant_core:
            lines = ["[相关的记忆]"]
            for e in relevant_core[:5]:
                if e.category != "basic_info":  # 避免重复
                    lines.append(f"  {e.key}: {e.value}")
            if len(lines) > 1:
                parts.append("\n".join(lines))

        # 3. 长期记忆语义检索
        lt_results = self._long_term.search(current_message, limit=5)
        if lt_results:
            lines = ["[相关的历史记忆]"]
            for e in lt_results:
                lines.append(f"  {e.content}")
            parts.append("\n".join(lines))

        return "\n\n".join(parts) if parts else self._core.get_context_string()
