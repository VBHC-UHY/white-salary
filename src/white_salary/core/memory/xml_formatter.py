"""
white_salary/core/memory/xml_formatter.py

XML格式化注入 — 把记忆转成结构化XML给LLM，防混淆。

借鉴v2的xml_formatter.py：
  - 清晰的XML结构：speaker/time/source
  - 相对时间计算（刚才/分钟前/小时前/天前）
  - 分类分块展示（核心记忆/长期记忆/情感记忆/关系）
  - XML特殊字符转义
  - 区分自己说的和用户说的

自动发现：导出MODULE供MemoryManager加载。
"""

import time
import html
from datetime import datetime
from typing import Optional

from loguru import logger
from white_salary.core.memory.module_base import MemoryModule


def relative_time(timestamp: float) -> str:
    """将时间戳转为相对时间描述。"""
    if not timestamp:
        return "未知时间"

    now = time.time()
    diff = now - timestamp

    if diff < 60:
        return "刚才"
    elif diff < 3600:
        minutes = int(diff / 60)
        return f"{minutes}分钟前"
    elif diff < 86400:
        hours = int(diff / 3600)
        return f"{hours}小时前"
    elif diff < 86400 * 2:
        return "昨天"
    elif diff < 86400 * 3:
        return "前天"
    elif diff < 86400 * 7:
        days = int(diff / 86400)
        return f"{days}天前"
    elif diff < 86400 * 30:
        weeks = int(diff / 86400 / 7)
        return f"{weeks}周前"
    elif diff < 86400 * 365:
        months = int(diff / 86400 / 30)
        return f"{months}个月前"
    else:
        return datetime.fromtimestamp(timestamp).strftime("%Y年%m月")


def escape_xml(text: str) -> str:
    """转义XML特殊字符。"""
    return html.escape(str(text), quote=False)


class XMLFormatter:
    """
    XML格式化器 — 将记忆数据转为结构化XML。

    使用方式:
        fmt = XMLFormatter()
        xml = fmt.format_memories(core_memories, long_term, emotions, relations)
    """

    def __init__(self, max_tokens: int = 2000) -> None:
        self._max_tokens = max_tokens  # 大致字符限制

    def format_memories(
        self,
        core_memories: list[dict] = None,
        long_term_memories: list[dict] = None,
        emotion_memories: list[dict] = None,
        relations: list[dict] = None,
        important_memories: list[dict] = None,
    ) -> str:
        """
        格式化所有记忆为XML。

        每种记忆格式:
            {"content": str, "time": float, "source": str, "category": str, "importance": int}

        Returns:
            XML格式字符串
        """
        parts = []
        char_count = 0

        # 核心记忆（最重要，永远展示）
        if core_memories:
            section = self._format_section("核心记忆", "core", core_memories)
            parts.append(section)
            char_count += len(section)

        # 重要记忆（承诺/约定）
        if important_memories and char_count < self._max_tokens:
            section = self._format_section("重要记忆", "important", important_memories)
            parts.append(section)
            char_count += len(section)

        # 关系信息
        if relations and char_count < self._max_tokens:
            section = self._format_relations(relations)
            parts.append(section)
            char_count += len(section)

        # 长期记忆
        if long_term_memories and char_count < self._max_tokens:
            remaining = self._max_tokens - char_count
            trimmed = self._trim_to_budget(long_term_memories, remaining)
            if trimmed:
                section = self._format_section("长期记忆", "long_term", trimmed)
                parts.append(section)
                char_count += len(section)

        # 情感记忆
        if emotion_memories and char_count < self._max_tokens:
            remaining = self._max_tokens - char_count
            trimmed = self._trim_to_budget(emotion_memories, remaining)
            if trimmed:
                section = self._format_section("情感记忆", "emotion", trimmed)
                parts.append(section)

        if not parts:
            return ""

        return "<memories>\n" + "\n".join(parts) + "\n</memories>"

    def format_single(self, content: str, source: str = "",
                      timestamp: float = 0.0, category: str = "") -> str:
        """格式化单条记忆。"""
        time_str = relative_time(timestamp) if timestamp else ""
        attrs = []
        if source:
            attrs.append(f'source="{escape_xml(source)}"')
        if time_str:
            attrs.append(f'time="{escape_xml(time_str)}"')
        if category:
            attrs.append(f'category="{escape_xml(category)}"')
        attr_str = " " + " ".join(attrs) if attrs else ""
        return f"<memory{attr_str}>{escape_xml(content)}</memory>"

    def format_conversation_context(
        self,
        messages: list[dict],
        max_messages: int = 10,
    ) -> str:
        """
        格式化对话上下文为XML。

        每条消息: {"role": "user"/"assistant", "content": str, "time": float}
        """
        if not messages:
            return ""

        lines = []
        for msg in messages[-max_messages:]:
            role = msg.get("role", "user")
            speaker = "用户" if role == "user" else "白"
            content = escape_xml(msg.get("content", "")[:200])
            time_str = ""
            if "time" in msg:
                if isinstance(msg["time"], (int, float)):
                    time_str = f' time="{relative_time(msg["time"])}"'
                else:
                    time_str = f' time="{escape_xml(str(msg["time"]))}"'
            lines.append(f'  <message speaker="{speaker}"{time_str}>{content}</message>')

        return "<conversation>\n" + "\n".join(lines) + "\n</conversation>"

    # ================================================================
    # 内部方法
    # ================================================================

    def _format_section(self, label: str, section_type: str,
                        memories: list[dict]) -> str:
        """格式化一个记忆区块。"""
        lines = [f'  <section type="{section_type}" label="{label}">']
        for mem in memories:
            content = escape_xml(mem.get("content", ""))
            attrs = []
            if mem.get("time"):
                attrs.append(f'time="{relative_time(mem["time"])}"')
            if mem.get("source"):
                attrs.append(f'source="{escape_xml(mem["source"])}"')
            if mem.get("category"):
                attrs.append(f'category="{escape_xml(mem["category"])}"')
            if mem.get("importance"):
                attrs.append(f'importance="{mem["importance"]}"')
            attr_str = " " + " ".join(attrs) if attrs else ""
            lines.append(f'    <memory{attr_str}>{content}</memory>')
        lines.append('  </section>')
        return "\n".join(lines)

    def _format_relations(self, relations: list[dict]) -> str:
        """格式化关系信息。"""
        lines = ['  <section type="relations" label="人物关系">']
        for rel in relations:
            name = escape_xml(rel.get("name", ""))
            relation = escape_xml(rel.get("relation", ""))
            detail = escape_xml(rel.get("detail", ""))
            lines.append(
                f'    <relation name="{name}" type="{relation}">{detail}</relation>'
            )
        lines.append('  </section>')
        return "\n".join(lines)

    def _trim_to_budget(self, memories: list[dict], budget: int) -> list[dict]:
        """根据字符预算裁剪记忆列表。"""
        result = []
        total = 0
        for mem in memories:
            content = mem.get("content", "")
            est = len(content) + 50  # 估算XML标签开销
            if total + est > budget:
                break
            result.append(mem)
            total += est
        return result


# ================================================================
# 自动发现接口
# ================================================================

class XMLFormatterModule(MemoryModule):
    """XML格式化模块 — 自动发现注册。"""
    name = "xml_formatter"

    def init(self, data_dir="data/memory", **kwargs):
        self._impl = XMLFormatter()

    def get_context_prompt(self, message: str = "") -> str:
        """暂不在这里注入（由manager统一调用format_memories）。"""
        return ""


MODULE = XMLFormatterModule
