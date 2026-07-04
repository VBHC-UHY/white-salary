"""
测试对话智能体。

使用Mock LLM来测试，不需要真实的API连接。
"""

import pytest
from typing import AsyncGenerator
from pathlib import Path

from white_salary.core.agent.chat_agent import ChatAgent
from white_salary.core.interfaces.llm import LLMInterface
from white_salary.core.interfaces.types import Message, ToolCall, ToolResult
from white_salary.core.memory.short_term import ShortTermMemory
from white_salary.core.personality.character import PersonalityManager


PROJECT_ROOT = Path(__file__).parent.parent.parent


class MockLLM(LLMInterface):
    """
    模拟的LLM，用于测试。
    不调用真实API，直接返回预设的回复。
    """

    def __init__(self, response: str = "我是White Salary，你好！") -> None:
        self._response = response

    async def chat_completion(
        self,
        messages: list[Message],
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> str:
        return self._response

    async def chat_completion_stream(
        self,
        messages: list[Message],
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> AsyncGenerator[str, None]:
        # 分字逐个返回
        for char in self._response:
            yield char

    async def chat_with_tools(
        self,
        messages: list[Message],
        tools: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> tuple[str, list[ToolCall]]:
        return self._response, []

    async def process_tool_results(
        self,
        messages: list[Message],
        tool_results: list[ToolResult],
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> str:
        return self._response


class ExplodingToolLLM(MockLLM):
    async def chat_with_tools(
        self,
        messages: list[Message],
        tools: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> tuple[str, list[ToolCall]]:
        raise AssertionError("tool_llm should not be called")


class FakeToolRegistry:
    @property
    def count(self) -> int:
        return 1

    def get_openai_tools(self, context=None) -> list[dict]:
        return [{
            "type": "function",
            "function": {
                "name": "fake_tool",
                "description": "fake",
                "parameters": {"type": "object", "properties": {}},
            },
        }]


class TestChatAgent:
    """测试对话智能体。"""

    def _create_agent(self, response: str = "测试回复") -> ChatAgent:
        """创建一个用于测试的ChatAgent。"""
        llm = MockLLM(response=response)
        personality = PersonalityManager(project_root=PROJECT_ROOT)
        memory = ShortTermMemory(max_turns=20)
        return ChatAgent(llm=llm, personality=personality, memory=memory)

    @pytest.mark.asyncio
    async def test_basic_chat(self) -> None:
        """基本对话功能。"""
        agent = self._create_agent(response="你好！我是White Salary。")
        reply = await agent.chat("你好")
        assert reply == "你好！我是White Salary。"

    @pytest.mark.asyncio
    async def test_chat_saves_to_memory(self) -> None:
        """对话后消息被保存到记忆中。"""
        agent = self._create_agent()
        await agent.chat("你好")

        # 应该有1轮对话（用户1条 + AI 1条 = 2条消息）
        assert agent.conversation_turns == 1

    @pytest.mark.asyncio
    async def test_multi_turn_conversation(self) -> None:
        """多轮对话。"""
        agent = self._create_agent()
        await agent.chat("第一句话")
        await agent.chat("第二句话")
        await agent.chat("第三句话")

        assert agent.conversation_turns == 3

    @pytest.mark.asyncio
    async def test_stream_chat(self) -> None:
        """流式对话功能。"""
        agent = self._create_agent(response="流式回复测试")

        chunks = []
        async for chunk in agent.chat_stream("你好"):
            chunks.append(chunk)

        full_reply = "".join(chunks)
        assert full_reply == "流式回复测试"

    @pytest.mark.asyncio
    async def test_stream_saves_to_memory(self) -> None:
        """流式对话也能正确保存到记忆。"""
        agent = self._create_agent(response="回复")

        # 消费所有流式输出
        async for _ in agent.chat_stream("你好"):
            pass

        assert agent.conversation_turns == 1

    @pytest.mark.asyncio
    async def test_chat_stream_with_tools_can_disable_tool_judge(self) -> None:
        """主动续聊等系统输入可显式禁用工具，真实用户默认行为不变。"""
        agent = ChatAgent(
            llm=MockLLM(response="不用工具的回复"),
            personality=PersonalityManager(project_root=PROJECT_ROOT),
            memory=ShortTermMemory(max_turns=20),
            tool_registry=FakeToolRegistry(),
            tool_llm=ExplodingToolLLM(),
        )

        chunks = []
        async for chunk in agent.chat_stream_with_tools(
            "系统主动续聊",
            allow_tools=False,
        ):
            chunks.append(chunk)

        assert "".join(chunks) == "不用工具的回复"

    def test_reset_conversation(self) -> None:
        """重置对话清空记忆。"""
        agent = self._create_agent()
        # 直接操作记忆添加一些消息
        agent._memory.add_user_message("测试")
        agent._memory.add_assistant_message("回复")
        assert agent.conversation_turns == 1

        agent.reset_conversation()
        assert agent.conversation_turns == 0

    def test_character_name(self) -> None:
        """角色名称正确。"""
        agent = self._create_agent()
        assert agent.character_name == "White Salary"
