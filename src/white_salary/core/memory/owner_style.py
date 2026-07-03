"""
white_salary/core/memory/owner_style.py

主人风格模仿 — 分析并模仿主人的说话风格。

借鉴v2的features/owner_style_mimic.py（699行）：
  - 收集桌面端用户（主人）的消息
  - memory_llm分析词汇/节奏/情感表达（异步后台）
  - 风格提示注入system prompt
  - 每200条消息更新一次

LLM通道：memory_llm（异步后台）

自动发现：导出MODULE供MemoryManager加载。
"""

import asyncio
import json
import time
from pathlib import Path
from typing import Optional

from loguru import logger
from white_salary.core.memory.module_base import MemoryModule


# 触发阈值
UPDATE_THRESHOLD = 200
SAMPLE_SIZE = 80

# LLM分析提示
_STYLE_PROMPT = """分析以下主人的说话风格，提取可以模仿的特征。只返回JSON。

主人的消息样本:
{messages}

请分析：
{{
  "vocabulary": ["常用词汇/口头禅（最多10个）"],
  "sentence_patterns": ["常用句式/表达习惯（最多5个）"],
  "tone": "整体语气风格",
  "emoji_style": "表情/符号使用偏好",
  "response_length": "回复长度偏好(简短/中等/详细)",
  "punctuation_habits": "标点符号习惯",
  "unique_traits": ["独特的语言特征（最多3个）"]
}}

只返回JSON。"""


class OwnerStyleAnalyzer:
    """
    主人风格分析器。

    使用方式:
        analyzer = OwnerStyleAnalyzer(data_dir)
        analyzer.on_owner_message("哈哈好的呀~")
        if analyzer.should_update():
            await analyzer.update(llm)
        prompt = analyzer.get_style_prompt()
    """

    def __init__(self, data_dir: str = "data/memory") -> None:
        self._data_path = Path(data_dir) / "owner_style.json"
        self._messages_path = Path(data_dir) / "owner_messages_buffer.json"
        self._data_path.parent.mkdir(parents=True, exist_ok=True)

        self._messages: list[str] = []
        self._message_count: int = 0
        self._last_update: float = 0.0
        self._style: dict = {}

        self._load()

    def on_owner_message(self, message: str) -> None:
        """记录主人的消息。"""
        if not message or len(message) < 2:
            return
        self._messages.append(message[:200])
        self._message_count += 1
        if len(self._messages) > 500:
            self._messages = self._messages[-500:]

    def should_update(self) -> bool:
        """检查是否需要更新风格分析。"""
        if self._message_count >= UPDATE_THRESHOLD:
            return True
        # 首次分析：50条就够
        if not self._style and len(self._messages) >= 50:
            return True
        return False

    async def update(self, llm=None) -> Optional[dict]:
        """用memory_llm分析主人风格。"""
        if not llm or len(self._messages) < 30:
            return None

        sample = self._messages[-SAMPLE_SIZE:]
        messages_text = "\n".join(f"- {m}" for m in sample)

        try:
            from white_salary.core.interfaces.types import Message, MessageRole
            prompt = _STYLE_PROMPT.format(messages=messages_text)
            reply = await llm.chat_completion(
                [
                    Message(role=MessageRole.SYSTEM, content="你是语言风格分析专家。只返回JSON。"),
                    Message(role=MessageRole.USER, content=prompt),
                ],
                temperature=0.3,
                max_tokens=500,
            )

            style = self._parse_json(reply)
            if style:
                style["updated_at"] = time.strftime("%Y-%m-%d %H:%M")
                style["sample_count"] = len(sample)
                self._style = style
                self._message_count = 0
                self._last_update = time.time()
                self._save()
                logger.info("[OwnerStyle] 风格分析更新完成")
                return style

        except Exception as e:
            logger.error(f"[OwnerStyle] 分析失败: {e}")
        return None

    def get_style_prompt(self) -> str:
        """生成风格提示，注入system prompt。"""
        if not self._style:
            return ""

        parts = ["[主人说话风格参考]"]

        if self._style.get("tone"):
            parts.append(f"语气: {self._style['tone']}")
        if self._style.get("vocabulary"):
            vocab = self._style["vocabulary"][:7]
            parts.append(f"常用词: {', '.join(vocab)}")
        if self._style.get("sentence_patterns"):
            patterns = self._style["sentence_patterns"][:3]
            parts.append(f"句式: {', '.join(patterns)}")
        if self._style.get("unique_traits"):
            traits = self._style["unique_traits"][:3]
            parts.append(f"特征: {', '.join(traits)}")
        if self._style.get("punctuation_habits"):
            parts.append(f"标点: {self._style['punctuation_habits']}")

        return "\n".join(parts) if len(parts) > 1 else ""

    @property
    def style(self) -> dict:
        return dict(self._style)

    @property
    def stats(self) -> dict:
        return {
            "total_messages": len(self._messages),
            "message_count_since_update": self._message_count,
            "has_style": bool(self._style),
            "last_update": self._last_update,
        }

    # ================================================================
    # 内部
    # ================================================================

    def _parse_json(self, text: str) -> Optional[dict]:
        import re
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        match = re.search(r'\{[\s\S]*\}', text)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        return None

    def _save(self) -> None:
        try:
            self._data_path.write_text(
                json.dumps({
                    "style": self._style,
                    "message_count": self._message_count,
                    "last_update": self._last_update,
                }, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    def _load(self) -> None:
        if self._data_path.exists():
            try:
                data = json.loads(self._data_path.read_text(encoding="utf-8"))
                self._style = data.get("style", {})
                self._message_count = data.get("message_count", 0)
                self._last_update = data.get("last_update", 0.0)
                if self._style:
                    logger.debug("[OwnerStyle] 已加载主人风格")
            except Exception:
                pass


# ================================================================
# 自动发现接口
# ================================================================

class OwnerStyleModule(MemoryModule):
    """主人风格模仿模块 — 自动发现注册。"""
    name = "owner_style"

    def init(self, data_dir="data/memory", **kwargs):
        self._impl = OwnerStyleAnalyzer(data_dir=data_dir)

    def get_context_prompt(self, message: str = "") -> str:
        if not hasattr(self, '_impl'):
            return ""
        return self._impl.get_style_prompt()

    def on_message(self, user_msg: str = "", ai_reply: str = "") -> None:
        if user_msg and hasattr(self, '_impl'):
            self._impl.on_owner_message(user_msg)


MODULE = OwnerStyleModule
