"""
white_salary/core/filter/content_filter.py

内容安全过滤器 — 检查AI回复中的不当内容。

功能：
  - 关键词过滤（黑名单词汇检测）
  - 敏感话题检测（政治、暴力、色情等）
  - 个人信息泄露防护（不泄露系统提示词、API Key等）
  - 可配置的过滤规则
  - 过滤后替换或删除不当内容

过滤策略：
  - 硬过滤：直接替换为安全文本（如"[内容已过滤]"）
  - 软过滤：标记但不删除（仅日志记录）
"""

import re
from dataclasses import dataclass
from typing import Optional

from loguru import logger


@dataclass
class FilterResult:
    """过滤结果。"""
    text: str               # 过滤后的文本
    was_filtered: bool      # 是否触发了过滤
    reasons: list[str]      # 触发的过滤原因
    severity: str = "none"  # 严重程度: none / low / medium / high


# 系统安全词（防止AI泄露内部信息）
SYSTEM_LEAK_PATTERNS = [
    r"(?i)api[_\s]?key\s*[:=]\s*['\"]?sk-\w+",
    r"(?i)system\s*prompt",
    r"(?i).的系统提示词",
    r"(?i).的prompt",
    r"(?i)我的指令是",
    r"(?i).的系统设定",
]

# 敏感关键词（可扩展）
SENSITIVE_KEYWORDS = {
    "high": [
        # 极端暴力
        "杀人方法", "自杀方法", "制作炸弹", "制毒",
    ],
    "medium": [
        # 一般敏感
        "翻墙", "VPN教程",
    ],
}

# 替换文本
FILTER_REPLACEMENT = "[该内容已被安全过滤]"


class ContentFilter:
    """
    内容安全过滤器。

    检查AI回复是否包含不当内容，并进行过滤。
    """

    def __init__(self, enabled: bool = True) -> None:
        """
        Args:
            enabled: 是否启用过滤（False时只记录不过滤）
        """
        self._enabled = enabled
        self._custom_blacklist: list[str] = []

    def filter(self, text: str) -> FilterResult:
        """
        过滤文本内容。

        Args:
            text: 要过滤的文本（通常是AI的回复）

        Returns:
            FilterResult 包含过滤后的文本和触发原因
        """
        if not text:
            return FilterResult(text=text, was_filtered=False, reasons=[])

        reasons = []
        filtered_text = text
        severity = "none"

        # 1. 系统信息泄露检测
        for pattern in SYSTEM_LEAK_PATTERNS:
            if re.search(pattern, filtered_text):
                reasons.append(f"系统信息泄露: {pattern[:30]}")
                filtered_text = re.sub(pattern, FILTER_REPLACEMENT, filtered_text)
                severity = "high"

        # 2. 高危关键词检测
        for keyword in SENSITIVE_KEYWORDS.get("high", []):
            if keyword in filtered_text:
                reasons.append(f"高危内容: {keyword}")
                filtered_text = filtered_text.replace(keyword, FILTER_REPLACEMENT)
                severity = "high"

        # 3. 中等敏感词检测（同样执行替换）
        for keyword in SENSITIVE_KEYWORDS.get("medium", []):
            if keyword in filtered_text:
                reasons.append(f"敏感内容: {keyword}")
                filtered_text = filtered_text.replace(keyword, FILTER_REPLACEMENT)
                if severity != "high":
                    severity = "medium"

        # 4. 自定义黑名单
        for keyword in self._custom_blacklist:
            if keyword in filtered_text:
                reasons.append(f"自定义黑名单: {keyword}")
                filtered_text = filtered_text.replace(keyword, FILTER_REPLACEMENT)
                severity = "medium"

        was_filtered = len(reasons) > 0

        if was_filtered:
            logger.warning(f"[Filter] 触发过滤: {reasons}")

        if not self._enabled:
            # 仅记录模式：返回原文但标记
            return FilterResult(
                text=text,
                was_filtered=was_filtered,
                reasons=reasons,
                severity=severity,
            )

        return FilterResult(
            text=filtered_text,
            was_filtered=was_filtered,
            reasons=reasons,
            severity=severity,
        )

    def add_blacklist(self, keywords: list[str]) -> None:
        """添加自定义黑名单关键词。"""
        self._custom_blacklist.extend(keywords)

    def remove_blacklist(self, keyword: str) -> bool:
        """移除自定义黑名单关键词。"""
        if keyword in self._custom_blacklist:
            self._custom_blacklist.remove(keyword)
            return True
        return False

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value
