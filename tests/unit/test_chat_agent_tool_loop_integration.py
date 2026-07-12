"""End-to-end ChatAgent coverage for iterative tool planning."""

from __future__ import annotations

from types import SimpleNamespace
from typing import AsyncGenerator

from white_salary.core.agent.chat_agent import ChatAgent
from white_salary.core.interfaces.llm import LLMInterface
from white_salary.core.interfaces.types import Message, MessageRole, ToolCall, ToolResult
from white_salary.core.memory.short_term import ShortTermMemory


class _Personality:
    def get_system_message(self) -> Message:
        return Message(role=MessageRole.SYSTEM, content="You are Bai.")


class _MainLLM(LLMInterface):
    def __init__(self) -> None:
        self.seen_results: list[ToolResult] = []

    async def chat_completion(self, messages, temperature=0.7, max_tokens=2048) -> str:
        return "normal"

    async def chat_completion_stream(
        self, messages, temperature=0.7, max_tokens=2048
    ) -> AsyncGenerator[str, None]:
        yield "normal"

    async def chat_with_tools(self, messages, tools, temperature=0.7, max_tokens=2048):
        return "", []

    async def process_tool_results(
        self, messages, tool_results, temperature=0.7, max_tokens=2048
    ) -> str:
        self.seen_results = list(tool_results)
        return "finished: " + " | ".join(result.content for result in tool_results)


class _SequencedToolLLM(LLMInterface):
    def __init__(self) -> None:
        self.rounds: list[list[Message]] = []

    async def chat_completion(self, messages, temperature=0.7, max_tokens=2048) -> str:
        return ""

    async def chat_completion_stream(
        self, messages, temperature=0.7, max_tokens=2048
    ) -> AsyncGenerator[str, None]:
        if False:
            yield ""

    async def chat_with_tools(self, messages, tools, temperature=0.7, max_tokens=2048):
        self.rounds.append(list(messages))
        if len(self.rounds) == 1:
            return "", [ToolCall(id="read-1", name="read_state", arguments={})]
        if len(self.rounds) == 2:
            return "", [
                ToolCall(id="write-1", name="apply_change", arguments={"value": 7})
            ]
        return "ready", []

    async def process_tool_results(
        self, messages, tool_results, temperature=0.7, max_tokens=2048
    ) -> str:
        return ""


class _Registry:
    def __init__(self) -> None:
        self.calls: list[str] = []

    @property
    def count(self) -> int:
        return 2

    def get_openai_tools(self, context=None) -> list[dict]:
        return [
            {"type": "function", "function": {"name": "read_state", "parameters": {}}},
            {"type": "function", "function": {"name": "apply_change", "parameters": {}}},
        ]

    def get_tool(self, name: str):
        if name == "read_state":
            return SimpleNamespace(name=name, side_effect=False)
        if name == "apply_change":
            return SimpleNamespace(name=name, side_effect=True)
        return None

    async def execute_detailed(self, name: str, arguments: dict, context=None):
        self.calls.append(name)
        return SimpleNamespace(
            name=name,
            ok=True,
            content="state=6" if name == "read_state" else "changed=7",
            duration_ms=1,
            error_type="",
            side_effect=name == "apply_change",
        )


async def test_chat_agent_can_plan_multiple_tool_rounds_before_reply() -> None:
    main_llm = _MainLLM()
    tool_llm = _SequencedToolLLM()
    registry = _Registry()
    memory = ShortTermMemory(max_turns=10)
    agent = ChatAgent(
        llm=main_llm,
        personality=_Personality(),
        memory=memory,
        tool_registry=registry,
        tool_llm=tool_llm,
    )

    chunks = []
    async for chunk in agent.chat_stream_with_tools("inspect and update it"):
        chunks.append(chunk)

    reply = "".join(chunks)
    assert registry.calls == ["read_state", "apply_change"]
    assert len(tool_llm.rounds) == 3
    assert any("state=6" in message.content for message in tool_llm.rounds[1])
    assert any("changed=7" in message.content for message in tool_llm.rounds[2])
    assert "state=6" in reply and "changed=7" in reply
    assert len(main_llm.seen_results) == 2
    assert memory.turn_count == 1
    assert memory.message_count == 2
