"""
white_salary/core/response_dedup.py

回复去重 — 防止短时间内说重复的话。

借鉴v2的message/utils/dedup.py：
  - 文本指纹（正则清理后比较）
  - 相似度阈值（默认0.7）
  - 历史窗口（保留最近5条回复）
  - 检测到重复时返回True，让调用方重新生成

适用于桌面端和QQ端。
"""

import re
import time
from collections import deque
from typing import Optional

from loguru import logger


class ResponseDeduplicator:
    """
    回复去重器。

    使用方式:
        dedup = ResponseDeduplicator()
        if dedup.is_duplicate("你好啊，今天天气真不错"):
            # 重新生成回复
        else:
            dedup.record("你好啊，今天天气真不错")
    """

    def __init__(
        self,
        window_size: int = 5,
        similarity_threshold: float = 0.5,
        time_window: int = 300,  # 5分钟内的回复才检查
    ) -> None:
        self._window_size = window_size
        self._threshold = similarity_threshold
        self._time_window = time_window
        self._history: deque[tuple[str, float]] = deque(maxlen=window_size)  # (fingerprint, timestamp)

    def is_duplicate(self, text: str) -> bool:
        """
        检查回复是否与最近的回复重复。

        Returns:
            True = 重复，应重新生成
        """
        if not text or len(text) < 3:
            return False

        fingerprint = self._make_fingerprint(text)
        now = time.time()

        for hist_fp, hist_time in self._history:
            # 只检查时间窗口内的
            if now - hist_time > self._time_window:
                continue
            # 完全相同直接判重
            if fingerprint == hist_fp:
                logger.debug("[Dedup] 检测到完全相同的回复")
                return True
            sim = self._similarity(fingerprint, hist_fp)
            if sim >= self._threshold:
                logger.debug(
                    f"[Dedup] 检测到重复回复 (相似度={sim:.2f})"
                )
                return True

        return False

    def record(self, text: str) -> None:
        """记录一条回复到历史。"""
        if not text:
            return
        fingerprint = self._make_fingerprint(text)
        self._history.append((fingerprint, time.time()))

    def check_and_record(self, text: str) -> bool:
        """检查+记录一步完成。返回True=重复。"""
        if self.is_duplicate(text):
            return True
        self.record(text)
        return False

    def clear(self) -> None:
        """清空历史。"""
        self._history.clear()

    # ================================================================
    # 内部方法
    # ================================================================

    @staticmethod
    def _make_fingerprint(text: str) -> str:
        """
        生成文本指纹 — 清理标点符号和空白后的纯文字。
        """
        # 去除标点、空白、emoji
        cleaned = re.sub(r'[，。！？、；：\u201c\u201d\u2018\u2019【】（）\s,.:;!?\[\]()~`@#$%^&*\-=+{}|\\/<>]+', '', text)
        # 转小写
        cleaned = cleaned.lower()
        return cleaned

    @staticmethod
    def _similarity(a: str, b: str) -> float:
        """
        计算两个指纹的相似度（Jaccard系数，基于2-gram）。
        """
        if not a or not b:
            return 0.0

        # 完全相同
        if a == b:
            return 1.0

        # 2-gram集合
        def bigrams(s):
            return set(s[i:i + 2] for i in range(len(s) - 1))

        set_a = bigrams(a)
        set_b = bigrams(b)

        if not set_a or not set_b:
            return 0.0

        intersection = len(set_a & set_b)
        union = len(set_a | set_b)

        return intersection / union if union > 0 else 0.0

    @property
    def stats(self) -> dict:
        return {
            "history_size": len(self._history),
            "window_size": self._window_size,
            "threshold": self._threshold,
        }
