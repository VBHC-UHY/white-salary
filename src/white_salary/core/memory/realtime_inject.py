"""
white_salary/core/memory/realtime_inject.py

实时记忆注入 — 群聊/私聊中实时检测人名和话题，注入相关记忆。

借鉴v2的realtime_group_memory.py和realtime_private_memory.py：
  - 实时人名检测（消息中提到已知人名→注入该人相关记忆）
  - 实时话题关联（话题关键词→检索相关记忆）
  - 群聊模式（多人对话的记忆匹配）
  - 私聊模式（用户画像+历史记忆）
  - 冷却机制（同一人/话题不重复注入）

自动发现：导出MODULE供MemoryManager加载。
"""

import re
import time
from typing import Optional

from loguru import logger
from white_salary.core.memory.module_base import MemoryModule


class RealtimeInjector:
    """
    实时记忆注入器。

    使用方式:
        injector = RealtimeInjector()
        context = injector.process_message("小红说她明天要考试", known_people=["小红", "小明"])
    """

    def __init__(self, cooldown_seconds: int = 600) -> None:
        self._cooldown = cooldown_seconds  # 同一人/话题的冷却时间
        self._last_injected: dict[str, float] = {}  # key → last_inject_time
        self._max_inject_per_message = 3  # 每条消息最多注入3条记忆

    def detect_people(self, text: str, known_people: list[str] = None) -> list[str]:
        """
        检测消息中提到的已知人名。

        Args:
            text: 消息文本
            known_people: 已知人名列表（从知识图谱获取）

        Returns:
            检测到的人名列表
        """
        if not text or not known_people:
            return []

        detected = []
        for name in known_people:
            if name and len(name) >= 2 and name in text:
                detected.append(name)

        return detected

    def detect_topics(self, text: str) -> list[str]:
        """
        检测消息中的话题关键词。

        Returns:
            检测到的话题列表
        """
        # 常见话题关键词
        topic_keywords = {
            "考试": "学习",
            "工作": "职场",
            "旅行": "出行",
            "旅游": "出行",
            "生日": "庆祝",
            "结婚": "感情",
            "分手": "感情",
            "表白": "感情",
            "减肥": "健康",
            "生病": "健康",
            "游戏": "娱乐",
            "电影": "娱乐",
            "做饭": "美食",
            "吃饭": "美食",
            "搬家": "生活",
            "养猫": "宠物",
            "养狗": "宠物",
        }

        detected = []
        for keyword, topic in topic_keywords.items():
            if keyword in text and topic not in detected:
                detected.append(topic)

        return detected

    def should_inject(self, key: str) -> bool:
        """检查是否应该注入（冷却检查）。"""
        last = self._last_injected.get(key, 0)
        return time.time() - last >= self._cooldown

    def mark_injected(self, key: str) -> None:
        """标记已注入。"""
        self._last_injected[key] = time.time()

    def process_message(
        self,
        text: str,
        known_people: list[str] = None,
        memory_searcher=None,
    ) -> list[dict]:
        """
        处理一条消息，返回需要注入的记忆。

        Args:
            text: 消息文本
            known_people: 已知人名列表
            memory_searcher: 记忆搜索函数 (query: str) -> list[dict]

        Returns:
            需要注入的记忆列表
            每条: {"content": str, "source": str, "trigger": str}
        """
        if not text or not memory_searcher:
            return []

        injections = []

        # 1. 人名检测
        people = self.detect_people(text, known_people)
        for person in people:
            if not self.should_inject(f"person:{person}"):
                continue
            # 搜索该人相关的记忆
            memories = memory_searcher(person)
            if memories:
                self.mark_injected(f"person:{person}")
                for mem in memories[:2]:  # 每人最多2条
                    injections.append({
                        "content": mem.get("content", ""),
                        "source": "realtime_person",
                        "trigger": f"提到了{person}",
                    })

        # 2. 话题检测
        topics = self.detect_topics(text)
        for topic in topics:
            if not self.should_inject(f"topic:{topic}"):
                continue
            memories = memory_searcher(topic)
            if memories:
                self.mark_injected(f"topic:{topic}")
                for mem in memories[:1]:  # 每话题最多1条
                    injections.append({
                        "content": mem.get("content", ""),
                        "source": "realtime_topic",
                        "trigger": f"话题:{topic}",
                    })

        return injections[:self._max_inject_per_message]

    def format_injections(self, injections: list[dict]) -> str:
        """将注入列表格式化为prompt文本。"""
        if not injections:
            return ""

        lines = ["[实时记忆]"]
        for inj in injections:
            trigger = inj.get("trigger", "")
            content = inj.get("content", "")
            lines.append(f"  ({trigger}) {content}")
        return "\n".join(lines)

    @property
    def stats(self) -> dict:
        active_cooldowns = sum(
            1 for t in self._last_injected.values()
            if time.time() - t < self._cooldown
        )
        return {
            "total_tracked": len(self._last_injected),
            "active_cooldowns": active_cooldowns,
        }


# ================================================================
# 自动发现接口
# ================================================================

class RealtimeInjectModule(MemoryModule):
    """实时记忆注入模块 — 自动发现注册。"""
    name = "realtime_inject"

    def init(self, data_dir="data/memory", **kwargs):
        self._impl = RealtimeInjector()

    def get_context_prompt(self, message: str = "") -> str:
        """暂不在这里注入（由chat_agent在处理消息时调用）。"""
        return ""


MODULE = RealtimeInjectModule
