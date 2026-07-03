"""
white_salary/core/memory/topic_history.py

话题历史 + 跨会话话题延续。

合并v2的topic_history_manager + topic_history_integration：
  - 记录每个话题的完整生命周期（首次提到→最后提到→讨论次数）
  - 跨会话延续：下次聊天时自动恢复之前没聊完的话题
  - 持久化到JSON

功能：
  - 记录话题历史（带时间戳和讨论深度）
  - 检测"未完成"的话题（上次聊到一半中断的）
  - 生成话题延续提示（"上次我们聊到了X，要继续吗？"）
"""

import json
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

from loguru import logger


@dataclass
class TopicRecord:
    """一条话题记录。"""
    topic: str
    first_seen: float = 0.0
    last_seen: float = 0.0
    discuss_count: int = 1
    depth: int = 1              # 讨论深度（来回几轮）
    concluded: bool = False     # 是否自然结束
    summary: str = ""           # 简要总结


from white_salary.core.memory.module_base import MemoryModule


class TopicHistoryModule(MemoryModule):
    name = "topic_history"

    def init(self, data_dir="data/memory", **kwargs):
        self._impl = TopicHistory(data_dir=data_dir)

    def get_context_prompt(self, message=""):
        if hasattr(self, '_impl'):
            return self._impl.get_continuation_prompt()
        return ""


MODULE = TopicHistoryModule


class TopicHistory:
    """
    话题历史管理 + 跨会话延续。

    使用方式:
        th = TopicHistory(data_dir="data/memory")
        th.record_topic("Python学习", depth=3)
        unfinished = th.get_unfinished_topics()
        hint = th.get_continuation_prompt()
    """

    MAX_TOPICS = 100  # 最多保留100个话题

    def __init__(self, data_dir: str = "data/memory") -> None:
        self._path = Path(data_dir) / "topic_history.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._topics: list[TopicRecord] = []
        self._load()

    def record_topic(self, topic: str, depth: int = 1, concluded: bool = False) -> None:
        """记录一个话题。"""
        now = time.time()

        # 查找是否已有
        existing = self._find(topic)
        if existing:
            existing.last_seen = now
            existing.discuss_count += 1
            existing.depth = max(existing.depth, depth)
            if concluded:
                existing.concluded = True
        else:
            self._topics.append(TopicRecord(
                topic=topic[:50],
                first_seen=now,
                last_seen=now,
                discuss_count=1,
                depth=depth,
                concluded=concluded,
            ))

        # 限制数量
        if len(self._topics) > self.MAX_TOPICS:
            self._topics = self._topics[-self.MAX_TOPICS:]

        self._save()

    def mark_concluded(self, topic: str) -> None:
        """标记话题已自然结束。"""
        existing = self._find(topic)
        if existing:
            existing.concluded = True
            self._save()

    def get_unfinished_topics(self, max_age_days: int = 7) -> list[TopicRecord]:
        """获取未完成的话题（最近N天内讨论但没自然结束的）。"""
        cutoff = time.time() - max_age_days * 86400
        return [
            t for t in self._topics
            if not t.concluded
            and t.last_seen > cutoff
            and t.depth >= 2  # 至少讨论了2轮才算"进行中"
        ]

    def get_continuation_prompt(self) -> str:
        """
        生成话题延续提示（注入system prompt）。

        Returns:
            提示文本，或空字符串
        """
        unfinished = self.get_unfinished_topics(max_age_days=3)
        if not unfinished:
            return ""

        # 只取最近的2个
        recent = sorted(unfinished, key=lambda t: t.last_seen, reverse=True)[:2]

        lines = ["[未完成的话题]"]
        for t in recent:
            hours_ago = (time.time() - t.last_seen) / 3600
            if hours_ago < 1:
                time_desc = "刚才"
            elif hours_ago < 24:
                time_desc = f"{int(hours_ago)}小时前"
            else:
                time_desc = f"{int(hours_ago/24)}天前"
            lines.append(f"- {t.topic}（{time_desc}聊了{t.depth}轮，没聊完）")

        lines.append("如果合适的话，可以自然地提起这些话题。")
        return "\n".join(lines)

    def get_hot_topics(self, limit: int = 5) -> list[TopicRecord]:
        """获取最热门的话题（按讨论次数排序）。"""
        return sorted(self._topics, key=lambda t: t.discuss_count, reverse=True)[:limit]

    def _find(self, topic: str) -> Optional[TopicRecord]:
        """模糊查找话题。"""
        topic_lower = topic.lower()[:30]
        for t in self._topics:
            if topic_lower in t.topic.lower() or t.topic.lower() in topic_lower:
                return t
        return None

    def _load(self) -> None:
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                self._topics = [TopicRecord(**d) for d in data]
            except Exception:
                pass

    def _save(self) -> None:
        try:
            data = [asdict(t) for t in self._topics]
            self._path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
