"""
white_salary/core/cross_platform.py

跨平台消息桥 — QQ和桌面端互通的消息队列。

场景：
  - 用户在QQ说"在电脑上跟我说一声" → 消息推到桌面端WebSocket
  - 用户在桌面端说"帮我在QQ给XX发消息" → 通过QQ API工具发送（已有）

实现：
  - 共享的消息队列（内存级别，不需要持久化）
  - WebSocket handler定期检查队列
  - QQ handler往队列里放消息
"""

import asyncio
from collections import deque
from typing import Optional

from loguru import logger


class CrossPlatformBridge:
    """跨平台消息桥（单例）。"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._desktop_queue = deque(maxlen=50)
            cls._instance._qq_queue = deque(maxlen=50)
        return cls._instance

    def push_to_desktop(self, message: str, from_user: str = "",
                        source: str = "qq") -> None:
        """推消息到桌面端（下次WebSocket轮询时发出）。"""
        self._desktop_queue.append({
            "message": message,
            "from_user": from_user,
            "source": source,
        })
        logger.debug(f"[Bridge] → 桌面端: {message[:30]}")

    def pop_desktop_messages(self) -> list[dict]:
        """取出所有待发到桌面端的消息（WebSocket handler调用）。"""
        messages = list(self._desktop_queue)
        self._desktop_queue.clear()
        return messages

    def push_to_qq(self, message: str, target_id: str = "",
                   is_group: bool = False) -> None:
        """推消息到QQ端。"""
        self._qq_queue.append({
            "message": message,
            "target_id": target_id,
            "is_group": is_group,
        })
        logger.debug(f"[Bridge] → QQ: {message[:30]}")

    def pop_qq_messages(self) -> list[dict]:
        """取出所有待发到QQ的消息。"""
        messages = list(self._qq_queue)
        self._qq_queue.clear()
        return messages

    @property
    def has_desktop_messages(self) -> bool:
        return len(self._desktop_queue) > 0

    @property
    def has_qq_messages(self) -> bool:
        return len(self._qq_queue) > 0
