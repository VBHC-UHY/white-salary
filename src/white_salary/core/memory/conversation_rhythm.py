"""
white_salary/core/memory/conversation_rhythm.py

对话节奏 — 回复节奏匹配用户速度。

统计用户的消息间隔和长度，让AI回复节奏自然匹配。不用LLM。

自动发现：导出MODULE供MemoryManager加载。
"""

import time
from collections import deque
from typing import Optional

from loguru import logger
from white_salary.core.memory.module_base import MemoryModule


class ConversationRhythm:
    """对话节奏追踪器。"""

    def __init__(self, window_size: int = 10) -> None:
        self._msg_times: deque[float] = deque(maxlen=window_size)
        self._msg_lengths: deque[int] = deque(maxlen=window_size)
        self._last_msg_time = 0.0

    def on_user_message(self, message: str) -> None:
        """记录用户消息的时间和长度。"""
        now = time.time()
        if self._last_msg_time > 0:
            interval = now - self._last_msg_time
            if interval < 300:  # 5分钟内才算连续
                self._msg_times.append(interval)
        self._last_msg_time = now
        self._msg_lengths.append(len(message))

    @property
    def avg_interval(self) -> float:
        """用户的平均消息间隔（秒）。"""
        if not self._msg_times:
            return 10.0
        return sum(self._msg_times) / len(self._msg_times)

    @property
    def avg_length(self) -> float:
        """用户的平均消息长度。"""
        if not self._msg_lengths:
            return 20.0
        return sum(self._msg_lengths) / len(self._msg_lengths)

    @property
    def user_pace(self) -> str:
        """用户的节奏类型。"""
        avg = self.avg_interval
        if avg < 5:
            return "fast"       # 快速连发
        elif avg < 15:
            return "normal"     # 正常
        else:
            return "slow"       # 慢节奏

    @property
    def user_style(self) -> str:
        """用户的消息风格。"""
        avg = self.avg_length
        if avg < 10:
            return "brief"      # 简短
        elif avg < 40:
            return "normal"     # 中等
        else:
            return "detailed"   # 详细

    def get_rhythm_hint(self) -> str:
        """生成节奏匹配提示。"""
        hints = []
        pace = self.user_pace
        style = self.user_style

        if pace == "fast":
            hints.append("用户发消息很快，回复也简短一些")
        elif pace == "slow":
            hints.append("用户发消息较慢，可以回复详细一些")

        if style == "brief":
            hints.append("用户消息简短，回复也别太长")
        elif style == "detailed":
            hints.append("用户消息详细，可以多说一些")

        if not hints:
            return ""
        return "[对话节奏] " + "；".join(hints)


class ConversationRhythmModule(MemoryModule):
    name = "conversation_rhythm"

    def init(self, data_dir="data/memory", **kwargs):
        self._impl = ConversationRhythm()

    def get_context_prompt(self, message: str = "") -> str:
        if not hasattr(self, '_impl'):
            return ""
        return self._impl.get_rhythm_hint()

    def on_message(self, user_msg: str = "", ai_reply: str = "") -> None:
        if user_msg and hasattr(self, '_impl'):
            self._impl.on_user_message(user_msg)


MODULE = ConversationRhythmModule
