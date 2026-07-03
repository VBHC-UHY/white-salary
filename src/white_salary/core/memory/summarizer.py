"""
white_salary/core/memory/summarizer.py

对话摘要压缩器 — 将过长对话压缩为摘要。

当对话历史超过一定长度时，把旧的消息压缩成一段摘要，
保留关键信息同时节省token。

压缩策略：
  - 保留最近N轮对话原文
  - 把更早的对话用LLM压缩成一段摘要
  - 摘要放在对话最前面，作为"之前聊过的内容"
  - 压缩有最小间隔（默认2小时），避免频繁压缩

参考: WhiteSalary-v2 summary_enabled / summary_min_interval_seconds
"""

import time
from typing import Optional

from loguru import logger

from white_salary.core.interfaces.llm import LLMInterface
from white_salary.core.interfaces.types import Message, MessageRole


SUMMARY_PROMPT = """你是对话摘要助手。把下面的对话历史压缩成一段简洁的摘要。

要求：
1. 保留关键信息（用户提到的名字、事件、喜好、承诺等）
2. 用第三人称描述（"用户说..."、"AI回复..."）
3. 控制在200字以内
4. 按时间顺序组织

对话内容：
"""


class ConversationSummarizer:
    """
    对话摘要压缩器。

    当对话太长时，把旧消息压缩成摘要。
    """

    def __init__(
        self,
        llm: Optional[LLMInterface] = None,
        compress_threshold: int = 30,    # 超过30条消息触发压缩
        keep_recent: int = 10,           # 保留最近10条原文
        min_interval: int = 7200,        # 最少2小时压缩一次
        token_threshold: int = 4000,     # token估算阈值（中文1字≈1.5token）
    ) -> None:
        self._llm = llm
        self._compress_threshold = compress_threshold
        self._keep_recent = keep_recent
        self._min_interval = min_interval
        self._token_threshold = token_threshold
        self._last_compress_time = 0.0
        self._summary: str = ""  # 当前摘要

    @property
    def summary(self) -> str:
        """当前的对话摘要。"""
        return self._summary

    async def maybe_compress(self, messages: list[Message]) -> tuple[list[Message], bool]:
        """
        检查是否需要压缩，如果需要就执行压缩。

        Args:
            messages: 当前对话消息列表

        Returns:
            (处理后的消息列表, 是否执行了压缩)
        """
        # 检查是否需要压缩（条数或token估算）
        total_chars = sum(len(m.content) for m in messages)
        est_tokens = int(total_chars * 1.5)  # 中文1字≈1.5token
        if len(messages) < self._compress_threshold and est_tokens < self._token_threshold:
            return messages, False

        # 检查间隔
        now = time.time()
        if now - self._last_compress_time < self._min_interval:
            return messages, False

        # 没有LLM就不压缩
        if not self._llm:
            return messages, False

        try:
            # 分割：系统消息保留 + 要压缩的 + 要保留的
            # 系统消息（system role）永不压缩
            system_msgs = [m for m in messages if m.role == MessageRole.SYSTEM]
            non_system = [m for m in messages if m.role != MessageRole.SYSTEM]

            to_compress = non_system[:-self._keep_recent]
            to_keep = non_system[-self._keep_recent:]

            if len(to_compress) < 5:  # 太少不值得压缩
                return messages, False

            # 用LLM生成摘要
            summary = await self._generate_summary(to_compress)
            if summary:
                self._summary = summary
                self._last_compress_time = now

                # 构建压缩后的消息列表：系统消息 + 摘要 + 最近消息
                summary_msg = Message(
                    role=MessageRole.SYSTEM,
                    content=f"[之前的对话摘要]\n{summary}\n[以上是摘要，下面是最近的对话]",
                )
                result = system_msgs + [summary_msg] + to_keep

                logger.info(
                    f"[Summarizer] 压缩完成: {len(messages)}条 → "
                    f"1条摘要 + {len(to_keep)}条最近消息"
                )
                return result, True

        except Exception as e:
            logger.warning(f"[Summarizer] 压缩失败: {e}")

        return messages, False

    async def _generate_summary(self, messages: list[Message]) -> str:
        """用LLM生成对话摘要。"""
        # 构建对话文本
        lines = []
        for msg in messages:
            role = "用户" if msg.role == MessageRole.USER else "AI"
            lines.append(f"{role}: {msg.content[:100]}")

        conversation_text = "\n".join(lines)

        response = await self._llm.chat_completion(
            messages=[
                Message(role=MessageRole.SYSTEM, content=SUMMARY_PROMPT),
                Message(role=MessageRole.USER, content=conversation_text),
            ],
            temperature=0.3,
            max_tokens=300,
        )

        return response.strip()
