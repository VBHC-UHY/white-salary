"""
white_salary/core/topic_tracker.py

话题追踪器 — 防止AI重复聊同样的话题。

借鉴v2的topic_manager.py但简化：
  - v2有6个话题类别和复杂的兴趣评分，太重了
  - v2是session-only，我们支持跨会话
  - v2用token overlap做相似度，我们改用更简单的jaccard

功能：
  - 追踪最近聊过的话题
  - 检测话题重复，提示AI换话题
  - 3小时自动过期
  - 可注入到system prompt提醒AI
"""

import re
import time
from collections import deque
from dataclasses import dataclass, field

from loguru import logger


@dataclass
class TopicEntry:
    """一个话题记录。"""
    text: str           # 话题文本（前50字）
    count: int = 1      # 重复次数
    first_time: float = 0.0
    last_time: float = 0.0


class TopicTracker:
    """
    话题追踪器。

    使用方式:
        tracker = TopicTracker()
        tracker.record_message("用户消息", source="user")
        tracker.record_message("AI回复", source="assistant")
        hint = tracker.get_hint()  # 如果话题重复，返回提示
    """

    TTL = 10800  # 话题3小时过期
    MAX_TOPICS = 10
    REPEAT_THRESHOLD = 4  # 重复4次才提示

    def __init__(self) -> None:
        self._topics: deque[TopicEntry] = deque(maxlen=self.MAX_TOPICS)
        self._last_hint_time = 0.0

    def record_message(self, text: str, source: str = "user") -> None:
        """记录一条消息，更新话题追踪。"""
        # 只追踪用户消息（AI回复是被动的）
        if source != "user":
            return

        # 标准化
        normalized = self._normalize(text)
        if len(normalized) < 3:
            return

        now = time.time()

        # 清理过期话题
        self._cleanup(now)

        # 检查是否与已有话题相似
        for topic in self._topics:
            if self._similar(normalized, topic.text):
                topic.count += 1
                topic.last_time = now
                return

        # 新话题
        self._topics.append(TopicEntry(
            text=normalized[:50],
            count=1,
            first_time=now,
            last_time=now,
        ))

    def get_hint(self) -> str:
        """
        获取话题提示。如果检测到重复话题，返回提示文本。

        Returns:
            提示文本，或空字符串
        """
        now = time.time()

        # 10分钟内不重复提示
        if now - self._last_hint_time < 600:
            return ""

        self._cleanup(now)

        for topic in self._topics:
            if topic.count >= self.REPEAT_THRESHOLD:
                self._last_hint_time = now
                return (
                    f"[话题提示] 用户最近反复提到类似的话题（{topic.count}次），"
                    f"可以尝试引导到其他话题，或者深入探讨这个话题的不同角度。"
                )

        return ""

    def _normalize(self, text: str) -> str:
        """标准化文本。"""
        # 去掉标签、标点
        text = re.sub(r'<[^>]+>', '', text)
        text = re.sub(r'[，。！？、…\s]+', '', text)
        return text.strip().lower()

    def _similar(self, a: str, b: str) -> bool:
        """判断两个文本是否话题相似（Jaccard相似度）。"""
        if not a or not b:
            return False

        # 用2-gram做jaccard
        set_a = set(a[i:i+2] for i in range(len(a)-1)) if len(a) > 1 else {a}
        set_b = set(b[i:i+2] for i in range(len(b)-1)) if len(b) > 1 else {b}

        if not set_a or not set_b:
            return False

        intersection = len(set_a & set_b)
        union = len(set_a | set_b)
        return (intersection / union) >= 0.5 if union > 0 else False

    def _cleanup(self, now: float) -> None:
        """清理过期话题。"""
        while self._topics and (now - self._topics[0].last_time > self.TTL):
            self._topics.popleft()
