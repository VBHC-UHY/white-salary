"""
white_salary/core/ai_diary.py

AI日记系统 — 自动生成每日对话总结。

功能：
  - 每天自动总结当天的对话内容
  - 记录重要事件、情绪变化、用户提到的信息
  - 以白的第一人称视角撰写日记
  - 保存到 data/diary/ 目录，按日期命名

触发条件：
  - 每天晚上安静时间开始时（默认21:00）
  - 当天至少有5轮对话才生成
"""

import json
import time
from pathlib import Path
from typing import Optional

from loguru import logger

from white_salary.core.interfaces.llm import LLMInterface
from white_salary.core.interfaces.types import Message, MessageRole


DIARY_PROMPT = """你是白（White Salary），一个21岁的程序员女生。
现在是一天结束的时候，请以第一人称写一篇简短的日记（150-300字），总结今天和用户的互动。

要求：
1. 用白的语气和性格来写（有点傲娇、喜欢自嘲、偶尔撒娇）
2. 提到今天聊了什么话题
3. 提到用户的心情变化（如果有的话）
4. 写写自己的感受
5. 如果有记住什么新的事情，也提一下
6. 自然、口语化，像真的在写日记

今天的对话摘要：
"""


class AIDiary:
    """
    AI日记系统。

    每天自动生成一篇日记，记录当天的对话和感受。
    """

    def __init__(
        self,
        data_dir: str = "data/diary",
        llm: Optional[LLMInterface] = None,
        min_turns: int = 5,        # 最少对话轮数才生成
        trigger_hour: int = 21,    # 触发生成的时间（小时）
    ) -> None:
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._llm = llm
        self._min_turns = min_turns
        self._trigger_hour = trigger_hour
        self._last_generated_date = ""
        self._daily_messages: list[dict] = []  # 今天的对话摘要

    def record_exchange(self, user_msg: str, ai_reply: str) -> None:
        """
        记录一轮对话（用于日记素材）。

        每天重置。只保留摘要（前50字）以节省空间。
        """
        today = time.strftime("%Y-%m-%d")

        # 新的一天，重置
        if not self._daily_messages or self._daily_messages[0].get("date") != today:
            self._daily_messages = []

        self._daily_messages.append({
            "date": today,
            "time": time.strftime("%H:%M"),
            "user": user_msg[:80],
            "ai": ai_reply[:80],
        })

    async def maybe_generate(self) -> Optional[str]:
        """
        检查是否应该生成今天的日记。

        Returns:
            生成的日记内容（如果生成了），否则None
        """
        today = time.strftime("%Y-%m-%d")
        hour = int(time.strftime("%H"))

        # 已经生成过了
        if self._last_generated_date == today:
            return None

        # 还没到触发时间
        if hour < self._trigger_hour:
            return None

        # 对话轮数不够
        if len(self._daily_messages) < self._min_turns:
            return None

        # 没有LLM
        if not self._llm:
            return None

        # 生成日记
        diary = await self._generate_diary()
        if diary:
            self._save_diary(today, diary)
            self._last_generated_date = today
            logger.info(f"[Diary] 生成了今天的日记 ({len(diary)}字)")
            return diary

        return None

    async def _generate_diary(self) -> str:
        """用LLM生成日记。"""
        # 构建对话摘要
        summary_lines = []
        for msg in self._daily_messages[-20:]:  # 最多取最近20轮
            summary_lines.append(f"[{msg['time']}] 用户: {msg['user']}")
            summary_lines.append(f"[{msg['time']}] 白: {msg['ai']}")

        summary = "\n".join(summary_lines)

        try:
            response = await self._llm.chat_completion(
                messages=[
                    Message(role=MessageRole.SYSTEM, content=DIARY_PROMPT),
                    Message(role=MessageRole.USER, content=summary),
                ],
                temperature=0.8,  # 稍高温度，写出更有个性的日记
                max_tokens=500,
            )
            return response.strip()
        except Exception as e:
            logger.warning(f"[Diary] 生成失败: {e}")
            return ""

    def generate_affinity_report(self, affinity_stats: dict) -> str:
        """
        生成好感度日报。

        Args:
            affinity_stats: AffinityManager.get_stats() 的返回值

        Returns:
            好感度日报文本
        """
        if not affinity_stats:
            return ""

        lines = [
            f"好感度日报 — {time.strftime('%Y-%m-%d')}",
            f"当前等级: {affinity_stats.get('level_name', '?')} {affinity_stats.get('emoji', '')}",
            f"当前分数: {affinity_stats.get('points', 0)}",
            f"连续互动: {affinity_stats.get('consecutive_days', 0)} 天",
            f"总互动次数: {affinity_stats.get('total_interactions', 0)}",
        ]

        history = affinity_stats.get("history", [])
        if history:
            lines.append("\n今日变化:")
            for h in history[-5:]:
                delta = h.get("delta", 0)
                lines.append(f"  {'+' if delta > 0 else ''}{delta} ({h.get('reason', '')})")

        return "\n".join(lines)

    def _save_diary(self, date: str, content: str) -> None:
        """保存日记到文件。"""
        diary_file = self._data_dir / f"{date}.md"

        text = f"""# 白的日记 — {date}

{content}

---
*对话轮数: {len(self._daily_messages)} | 生成时间: {time.strftime('%H:%M:%S')}*
"""
        diary_file.write_text(text, encoding="utf-8")
        logger.debug(f"[Diary] 保存: {diary_file}")

    def get_recent_diaries(self, days: int = 7) -> list[dict]:
        """获取最近几天的日记。"""
        diaries = []
        for diary_file in sorted(self._data_dir.glob("*.md"), reverse=True)[:days]:
            try:
                content = diary_file.read_text(encoding="utf-8")
                diaries.append({
                    "date": diary_file.stem,
                    "content": content,
                })
            except Exception:
                pass
        return diaries

    @property
    def today_exchange_count(self) -> int:
        """今天的对话轮数。"""
        return len(self._daily_messages)
