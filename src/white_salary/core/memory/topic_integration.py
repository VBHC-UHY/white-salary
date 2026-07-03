"""
white_salary/core/memory/topic_integration.py

话题历史整合 — 跨话题历史整合到对话系统。

借鉴v2的features/topic_history_integration.py（412行）：
  - 整合topic_history和topic_association的数据
  - 话题延续检测（"之前聊过的话题"被重新提起）
  - 注入上下文提示
  - 未完成话题追踪

自动发现：导出MODULE供MemoryManager加载。
"""

import time
from typing import Optional

from loguru import logger
from white_salary.core.memory.module_base import MemoryModule


class TopicIntegrator:
    """
    话题历史整合器。

    使用方式:
        integrator = TopicIntegrator()
        prompt = integrator.get_continuation_prompt(message)
    """

    def __init__(self, cooldown_seconds: int = 600) -> None:
        self._cooldown = cooldown_seconds
        self._last_inject_topic: dict[str, float] = {}  # topic → last_inject_time
        self._active_topics: list[str] = []               # 最近活跃的话题

    def detect_topic_continuation(
        self,
        message: str,
        topic_history: list[dict] = None,
    ) -> Optional[dict]:
        """
        检测消息是否延续了之前的话题。

        Args:
            message: 当前消息
            topic_history: 话题历史列表
                每条: {"topic": str, "last_content": str, "timestamp": float}

        Returns:
            匹配的历史话题（None=不是延续）
        """
        if not message or not topic_history:
            return None

        # 提取消息中的关键词
        keywords = self._extract_keywords(message)
        if not keywords:
            return None

        # 与历史话题匹配
        for history in topic_history:
            topic_content = history.get("last_content", "")
            topic_keywords = self._extract_keywords(topic_content)

            # 关键词重叠检查
            overlap = set(keywords) & set(topic_keywords)
            if len(overlap) >= 2:
                # 检查冷却
                topic = history.get("topic", "")
                if self._check_cooldown(topic):
                    self._mark_injected(topic)
                    return history

        return None

    def get_continuation_prompt(
        self,
        message: str,
        topic_history: list[dict] = None,
        topic_associations: dict = None,
    ) -> str:
        """
        生成话题延续提示。

        Args:
            message: 当前消息
            topic_history: 从topic_history模块获取的历史
            topic_associations: 从topic_association服务获取的关联

        Returns:
            话题延续提示（空=无延续）
        """
        parts = []

        # 1. 话题延续检测
        if topic_history:
            continuation = self.detect_topic_continuation(message, topic_history)
            if continuation:
                topic = continuation.get("topic", "")
                content = continuation.get("last_content", "")[:50]
                parts.append(f"这个话题之前聊过：{content}")

        # 2. 话题关联注入
        if topic_associations:
            # 检测当前消息涉及的话题类型
            try:
                from white_salary.core.services.topic_association import TopicAssociationService
                svc = TopicAssociationService.__new__(TopicAssociationService)
                svc._topics = {}  # 不需要完整初始化
                from white_salary.core.services.topic_association import TOPIC_CATEGORIES
                for topic, keywords in TOPIC_CATEGORIES.items():
                    for kw in keywords:
                        if kw in message:
                            # 有关联历史
                            history = topic_associations.get(topic, [])
                            if history:
                                recent = history[-1]
                                parts.append(
                                    f"之前聊{topic}时提过：{recent.get('content', '')[:40]}"
                                )
                            break
            except Exception:
                pass

        # 3. 更新活跃话题
        self._update_active_topics(message)

        if not parts:
            return ""
        return "[话题延续]\n" + "\n".join(f"  - {p}" for p in parts[:3])

    def _check_cooldown(self, topic: str) -> bool:
        last = self._last_inject_topic.get(topic, 0)
        return time.time() - last >= self._cooldown

    def _mark_injected(self, topic: str) -> None:
        self._last_inject_topic[topic] = time.time()

    def _update_active_topics(self, message: str) -> None:
        """更新活跃话题列表。"""
        try:
            from white_salary.core.services.topic_association import TOPIC_CATEGORIES
            for topic, keywords in TOPIC_CATEGORIES.items():
                for kw in keywords:
                    if kw in message:
                        if topic not in self._active_topics:
                            self._active_topics.append(topic)
                        if len(self._active_topics) > 5:
                            self._active_topics = self._active_topics[-5:]
                        break
        except Exception:
            pass

    @staticmethod
    def _extract_keywords(text: str) -> list[str]:
        """提取2-3字关键词。"""
        import re
        segments = re.split(r'[，。！？、\s,.:;!?]+', text)
        keywords = []
        for seg in segments:
            seg = seg.strip()
            if 2 <= len(seg) <= 4:
                keywords.append(seg)
            elif len(seg) > 4:
                for i in range(0, len(seg) - 1, 2):
                    keywords.append(seg[i:i + 2])
        return keywords

    @property
    def stats(self) -> dict:
        return {
            "active_topics": self._active_topics,
            "tracked_topics": len(self._last_inject_topic),
        }


# ================================================================
# 自动发现接口
# ================================================================

class TopicIntegrationModule(MemoryModule):
    """话题历史整合模块 — 自动发现注册。"""
    name = "topic_integration"

    def init(self, data_dir="data/memory", **kwargs):
        self._impl = TopicIntegrator()

    def get_context_prompt(self, message: str = "") -> str:
        if not message or not hasattr(self, '_impl'):
            return ""
        return self._impl.get_continuation_prompt(message)


MODULE = TopicIntegrationModule
