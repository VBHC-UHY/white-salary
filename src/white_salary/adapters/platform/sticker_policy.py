"""
QQ sticker attachment policy.

This stays outside the desktop chat path: QQ can attach CQ image stickers while
the desktop renderer should keep using plain text/UI effects.
"""

from __future__ import annotations

import random
import re
import time
from collections.abc import Callable


class QQStickerPolicy:
    """Decide when QQ replies may attach a sticker."""

    _SERIOUS_WORDS = (
        "错误", "失败", "异常", "报错", "权限", "隐私", "密码", "token",
        "api key", "崩溃", "警告", "危险", "删除", "回退", "提交",
    )

    def __init__(
        self,
        probability: float = 0.5,
        cooldown_seconds: float = 30.0,
        random_func: Callable[[], float] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.probability = max(0.0, min(1.0, probability))
        self.cooldown_seconds = max(0.0, cooldown_seconds)
        self._random = random_func or random.random
        self._clock = clock or time.time
        self._last_attached_at: float = 0.0

    def should_attach(
        self,
        raw_reply: str,
        clean_reply: str,
        user_text: str,
        *,
        is_group: bool,
    ) -> bool:
        if self.has_explicit_sticker(raw_reply):
            return True
        if not self._is_casual_reply(clean_reply, user_text, is_group=is_group):
            return False
        now = self._clock()
        if self.cooldown_seconds and now - self._last_attached_at < self.cooldown_seconds:
            return False
        if self._random() >= self.probability:
            return False
        self._last_attached_at = now
        return True

    @staticmethod
    def has_explicit_sticker(raw_reply: str) -> bool:
        return bool(re.search(r"<sticker\b[^>]*>.*?</sticker>", raw_reply or "", re.I | re.S))

    def _is_casual_reply(self, clean_reply: str, user_text: str, *, is_group: bool) -> bool:
        reply = (clean_reply or "").strip()
        if not reply:
            return False
        if "```" in reply or "\n\n" in reply:
            return False
        if len(reply) > (90 if is_group else 120):
            return False
        joined = f"{user_text}\n{reply}".lower()
        if any(word.lower() in joined for word in self._SERIOUS_WORDS):
            return False
        return True
