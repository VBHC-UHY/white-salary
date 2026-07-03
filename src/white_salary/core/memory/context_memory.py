"""
white_salary/core/memory/context_memory.py

情境记忆 — 根据当前对话场景检索最相关的记忆。

借鉴v2的context_memory但简化：
  - v2用LLM做场景分类，太重；我们用关键词+时间匹配
  - 根据当前消息的关键词，从核心记忆和长期记忆中找最相关的
  - 结果注入system prompt，让AI回复时有上下文

功能：
  - 从当前消息提取关键词
  - 匹配核心记忆中的相关条目
  - 匹配长期记忆中的相关事件
  - 返回格式化的上下文片段
"""

import re
import time
from typing import Optional

from loguru import logger


from white_salary.core.memory.module_base import MemoryModule


class ContextMemoryModule(MemoryModule):
    """自动发现接口。"""
    name = "context_memory"

    def init(self, data_dir="data/memory", **kwargs):
        core = kwargs.get("core_store")
        long_term = kwargs.get("long_term_store")
        self._impl = ContextMemory(core, long_term)

    def get_context_prompt(self, message=""):
        if hasattr(self, '_impl'):
            return self._impl.get_relevant(message)
        return ""


# 导出供自动发现
MODULE = ContextMemoryModule


class ContextMemory:
    """
    情境记忆检索器。

    使用方式:
        ctx_mem = ContextMemory(core_store, long_term_store)
        context = ctx_mem.get_relevant(user_message)
        # → "[相关记忆] 用户喜欢Minecraft; 上次聊过Python学习"
    """

    def __init__(self, core_store=None, long_term_store=None) -> None:
        self._core = core_store
        self._long_term = long_term_store

    def get_relevant(self, message: str, max_results: int = 5) -> str:
        """
        根据当前消息检索相关记忆。

        Args:
            message: 用户当前消息
            max_results: 最多返回几条

        Returns:
            格式化的相关记忆文本，或空字符串
        """
        keywords = self._extract_keywords(message)
        if not keywords:
            return ""

        results = []

        # 搜核心记忆
        if self._core and hasattr(self._core, '_cache'):
            for key, entry in self._core._cache.items():
                val = str(entry.get("value", "")).lower()
                for kw in keywords:
                    if kw in key.lower() or kw in val:
                        results.append(f"[核心] {entry.get('value', '')}")
                        break

        # 搜长期记忆
        if self._long_term:
            try:
                for kw in keywords[:3]:  # 最多搜3个关键词
                    hits = self._long_term.search(kw, limit=3)
                    for h in hits:
                        content = h.get("content", "") if isinstance(h, dict) else str(h)
                        if content and content not in [r for r in results]:
                            results.append(f"[记忆] {content[:80]}")
            except Exception:
                pass

        if not results:
            return ""

        unique = list(dict.fromkeys(results))[:max_results]
        return "[相关记忆]\n" + "\n".join(unique)

    def _extract_keywords(self, text: str) -> list[str]:
        """从消息中提取关键词。"""
        # 去掉标点和停用词
        cleaned = re.sub(r'[，。！？、…\s\n""''【】（）(){}]', ' ', text)
        # 停用词
        stops = {"的", "了", "吗", "呢", "啊", "吧", "是", "在", "有", "和", "不", "我", "你", "他", "她",
                 "这", "那", "什么", "怎么", "为什么", "可以", "能", "会", "要", "就", "都", "也", "还"}
        words = [w.strip() for w in cleaned.split() if len(w.strip()) >= 2 and w.strip() not in stops]
        return words[:5]
