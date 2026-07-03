"""
white_salary/core/memory/proactive_recall.py

主动回忆 — 对话时自动检索相关记忆注入上下文。

借鉴v2的proactive_recall：
  - 不需要用户明确说"你还记得吗"
  - AI在回复前自动检查是否有相关记忆
  - 只在确实相关时才注入，避免每次都带一堆无关记忆

功能：
  - 分析当前消息的话题
  - 从长期记忆、对话日志中找相关内容
  - 只返回高相关度的（避免噪音）
  - 记录哪些记忆被"主动回忆"过（配合memory_enhancement更新权重）
"""

import time
from typing import Optional

from loguru import logger


from white_salary.core.memory.module_base import MemoryModule


class ProactiveRecallModule(MemoryModule):
    name = "proactive_recall"

    def init(self, data_dir="data/memory", **kwargs):
        ctx_mem = kwargs.get("context_memory")
        weight = kwargs.get("memory_weight")
        self._impl = ProactiveRecall(ctx_mem, weight)

    def get_context_prompt(self, message=""):
        if hasattr(self, '_impl'):
            return self._impl.maybe_recall(message)
        return ""

    def on_session_start(self):
        if hasattr(self, '_impl'):
            self._impl.reset_session()


MODULE = ProactiveRecallModule


class ProactiveRecall:
    """
    主动回忆系统。

    使用方式:
        recall = ProactiveRecall(context_memory, memory_weight)
        hint = recall.maybe_recall(user_message)
        if hint:
            # 注入到对话上下文
    """

    MIN_MESSAGE_LENGTH = 5        # 太短的消息不触发
    COOLDOWN_SECONDS = 120        # 同一话题2分钟内不重复回忆
    MAX_RECALL_PER_SESSION = 10   # 每个会话最多回忆10次

    def __init__(self, context_memory=None, memory_weight=None) -> None:
        self._ctx_mem = context_memory
        self._weight = memory_weight
        self._last_recall_time = 0.0
        self._recall_count = 0
        self._recent_topics: set[str] = set()  # 最近回忆过的话题关键词

    def maybe_recall(self, message: str) -> str:
        """
        检查是否有值得主动回忆的内容。

        Args:
            message: 用户当前消息

        Returns:
            相关记忆文本，或空字符串（无需回忆）
        """
        # 太短不触发
        if len(message.strip()) < self.MIN_MESSAGE_LENGTH:
            return ""

        # 冷却中
        now = time.time()
        if now - self._last_recall_time < self.COOLDOWN_SECONDS:
            return ""

        # 会话回忆次数限制
        if self._recall_count >= self.MAX_RECALL_PER_SESSION:
            return ""

        # 用情境记忆检索
        if not self._ctx_mem:
            return ""

        result = self._ctx_mem.get_relevant(message, max_results=3)
        if not result:
            return ""

        # 检查是不是刚回忆过类似的
        keywords = self._ctx_mem._extract_keywords(message)
        topic_key = "+".join(sorted(keywords[:2])) if keywords else ""
        if topic_key and topic_key in self._recent_topics:
            return ""

        # 触发回忆
        self._last_recall_time = now
        self._recall_count += 1
        if topic_key:
            self._recent_topics.add(topic_key)
            # 最多记20个话题
            if len(self._recent_topics) > 20:
                self._recent_topics = set(list(self._recent_topics)[-15:])

        # 更新记忆权重（被回忆到的记忆加权）
        if self._weight and keywords:
            for kw in keywords:
                self._weight.record_access(f"recall_{kw}")

        logger.debug(f"[ProactiveRecall] 触发: {topic_key} ({self._recall_count}次)")
        return result

    def reset_session(self) -> None:
        """重置会话计数（新会话时调用）。"""
        self._recall_count = 0
        self._recent_topics.clear()
