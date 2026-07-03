"""
white_salary/core/memory/llm_extractor.py

LLM记忆提取器 — 用大模型分析对话，智能判断值得记住的信息。

比正则匹配更智能：
  - 能理解上下文含义
  - 能判断信息的重要程度
  - 能自动分类到合适的记忆层
  - 能提取隐含信息（如"明天我要考试"→用户是学生）

使用独立的 memory_llm 通道，不抢主对话模型的资源。
每日调用次数限制（默认20次），避免过度消耗API。

参考: WhiteSalary-v2 memory_manager.py 情感分析部分
"""

import json
import time
from typing import Optional

from loguru import logger

from white_salary.core.interfaces.llm import LLMInterface
from white_salary.core.interfaces.types import Message, MessageRole


# LLM记忆提取的系统提示词
EXTRACTION_PROMPT = """你是记忆分析助手。分析用户和白的对话，提取值得永久保存的信息。

判断标准：
1. 用户的个人信息（姓名、年龄、生日、职业、住所）
2. 用户的喜好偏好（喜欢/讨厌的食物、颜色、音乐、游戏等）
3. 重要的人际关系（家人、朋友、恋人的名字和关系）
4. 重大生活事件（毕业、结婚、搬家、换工作等里程碑）
5. 用户教给AI的规则或特殊要求
6. 强烈的情感表达（非常开心/难过/感动的时刻）

回复格式（JSON数组，没有值得记住的返回空数组 []）：
[
  {
    "key": "记忆键名（英文下划线命名，如 user_name）",
    "value": "记忆内容（简洁描述，20字以内）",
    "layer": "core 或 event 或 emotion",
    "category": "basic_info / preference / relationship / rule / milestone / habit",
    "importance": 1-10的数字,
    "keywords": "关键词1,关键词2"
  }
]

注意：
- 只提取确定的事实，不要猜测
- 不要记录普通闲聊内容
- 每条记忆尽量简短精炼
- 如果没有值得记住的信息，返回 []"""


class LLMMemoryExtractor:
    """
    LLM记忆提取器。

    使用独立的LLM通道分析对话，智能提取记忆。
    """

    def __init__(
        self,
        llm: Optional[LLMInterface] = None,
        max_calls_per_day: int = 20,
    ) -> None:
        """
        Args:
            llm: 用于记忆分析的LLM适配器（memory_llm通道）
            max_calls_per_day: 每日最大调用次数
        """
        self._llm = llm
        self._max_calls = max_calls_per_day
        self._call_count = 0
        self._call_date = ""

    async def extract(
        self,
        user_message: str,
        ai_reply: str,
    ) -> list[dict]:
        """
        用LLM分析一轮对话，提取记忆。

        Args:
            user_message: 用户的消息
            ai_reply: AI的回复

        Returns:
            提取的记忆列表，每项包含 key/value/layer/category/importance/keywords
        """
        if not self._llm:
            return []

        # 检查每日调用限制
        today = time.strftime("%Y-%m-%d")
        if self._call_date != today:
            self._call_date = today
            self._call_count = 0

        if self._call_count >= self._max_calls:
            return []

        # 消息太短不值得分析
        if len(user_message) < 5:
            return []

        try:
            self._call_count += 1

            messages = [
                Message(role=MessageRole.SYSTEM, content=EXTRACTION_PROMPT),
                Message(
                    role=MessageRole.USER,
                    content=f"用户说：{user_message}\nAI回复：{ai_reply[:200]}",
                ),
            ]

            response = await self._llm.chat_completion(
                messages=messages,
                temperature=0.3,  # 低温度，更确定性的输出
                max_tokens=300,
            )

            # 解析JSON结果
            memories = self._parse_response(response)
            if memories:
                logger.debug(f"[LLM-Extract] 提取了 {len(memories)} 条记忆")
            return memories

        except Exception as e:
            logger.warning(f"[LLM-Extract] 提取失败: {e}")
            return []

    def _parse_response(self, response: str) -> list[dict]:
        """解析LLM返回的JSON记忆列表。"""
        try:
            # 尝试提取JSON部分（LLM可能会包裹在其他文字中）
            text = response.strip()

            # 找到JSON数组的开始和结束
            start = text.find("[")
            end = text.rfind("]")
            if start == -1 or end == -1:
                return []

            json_str = text[start:end + 1]
            memories = json.loads(json_str)

            if not isinstance(memories, list):
                return []

            # 验证每条记忆的格式
            valid = []
            for m in memories:
                if isinstance(m, dict) and "key" in m and "value" in m:
                    valid.append({
                        "key": str(m.get("key", "")),
                        "value": str(m.get("value", "")),
                        "layer": str(m.get("layer", "event")),
                        "category": str(m.get("category", "other")),
                        "importance": int(m.get("importance", 5)),
                        "keywords": str(m.get("keywords", "")),
                    })

            return valid

        except (json.JSONDecodeError, ValueError):
            return []

    @property
    def calls_remaining_today(self) -> int:
        """今日剩余调用次数。"""
        today = time.strftime("%Y-%m-%d")
        if self._call_date != today:
            return self._max_calls
        return max(0, self._max_calls - self._call_count)
