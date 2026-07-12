"""Behavior tests for iterative tool execution."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncGenerator

import pytest

from white_salary.adapters.tools.registry import ToolDefinition, ToolRegistry
from white_salary.core.interfaces.llm import LLMInterface
from white_salary.core.interfaces.types import Message, MessageRole, ToolCall, ToolResult
from white_salary.core.runtime import (
    CancellationRegistry,
    ConversationRef,
    RuntimeStore,
    ToolLoopRunner,
)


class SequenceToolLLM(LLMInterface):
    def __init__(self, rounds: list[list[ToolCall]]) -> None:
        self.rounds = list(rounds)
        self.messages_seen: list[list[Message]] = []

    async def chat_completion(self, messages, temperature=0.7, max_tokens=2048) -> str:
        return ""

    async def chat_completion_stream(
        self, messages, temperature=0.7, max_tokens=2048,
    ) -> AsyncGenerator[str, None]:
        if False:
            yield ""

    async def chat_with_tools(
        self, messages, tools, temperature=0.7, max_tokens=2048,
    ) -> tuple[str, list[ToolCall]]:
        self.messages_seen.append(list(messages))
        return "", self.rounds.pop(0) if self.rounds else []

    async def process_tool_results(
        self, messages, tool_results, temperature=0.7, max_tokens=2048,
    ) -> str:
        return ""


def _empty_registry() -> ToolRegistry:
    registry = ToolRegistry.__new__(ToolRegistry)
    registry._tools = {}
    return registry


def _register(
    registry: ToolRegistry,
    name: str,
    handler,
    *,
    side_effect: bool = False,
    permission: str = "",
    side_effect_group: str = "global",
) -> None:
    registry.register(ToolDefinition(
        name=name,
        description=name,
        parameters={"type": "object", "properties": {}},
        handler=handler,
        side_effect=side_effect,
        requires_permission=permission,
        side_effect_group=side_effect_group,
    ))


@pytest.mark.asyncio
async def test_tool_loop_can_call_a_second_tool_after_first_result() -> None:
    registry = _empty_registry()

    async def search(query: str = "") -> str:
        return f"found:{query}"

    async def summarize() -> str:
        return "summary-ready"

    _register(registry, "search", search)
    _register(registry, "summarize", summarize)
    llm = SequenceToolLLM([
        [ToolCall(id="1", name="search", arguments={"query": "White Salary"})],
        [ToolCall(id="2", name="summarize", arguments={})],
        [],
    ])
    runner = ToolLoopRunner(tool_llm=llm, registry=registry)

    outcome = await runner.run([Message(MessageRole.USER, "查完再总结")])

    assert [run.name for run in outcome.runs] == ["search", "summarize"]
    assert outcome.stop_reason == "no_more_tools"
    assert len(llm.messages_seen) == 3
    assert "found:White Salary" in llm.messages_seen[1][-1].content


@pytest.mark.asyncio
async def test_read_only_tools_run_in_parallel_but_side_effects_are_serial() -> None:
    registry = _empty_registry()
    active_reads = 0
    max_reads = 0
    active_writes = 0
    max_writes = 0

    async def read_tool() -> str:
        nonlocal active_reads, max_reads
        active_reads += 1
        max_reads = max(max_reads, active_reads)
        await asyncio.sleep(0.02)
        active_reads -= 1
        return "read"

    async def write_tool() -> str:
        nonlocal active_writes, max_writes
        active_writes += 1
        max_writes = max(max_writes, active_writes)
        await asyncio.sleep(0.01)
        active_writes -= 1
        return "write"

    _register(registry, "read_a", read_tool)
    _register(registry, "read_b", read_tool)
    _register(registry, "write_a", write_tool, side_effect=True, permission="owner")
    _register(registry, "write_b", write_tool, side_effect=True, permission="owner")
    llm = SequenceToolLLM([[
        ToolCall(id="1", name="read_a", arguments={}),
        ToolCall(id="2", name="read_b", arguments={}),
        ToolCall(id="3", name="write_a", arguments={}),
        ToolCall(id="4", name="write_b", arguments={}),
    ], []])
    runner = ToolLoopRunner(tool_llm=llm, registry=registry)

    outcome = await runner.run(
        [Message(MessageRole.USER, "run")],
        access_context={"permissions": ["owner"], "allow_side_effects": True},
    )

    assert len(outcome.runs) == 4
    assert max_reads == 2
    assert max_writes == 1


@pytest.mark.asyncio
async def test_duplicate_tool_cycle_stops_without_reexecuting() -> None:
    registry = _empty_registry()
    calls = 0

    async def once() -> str:
        nonlocal calls
        calls += 1
        return "done"

    _register(registry, "once", once)
    repeated = [ToolCall(id="1", name="once", arguments={})]
    llm = SequenceToolLLM([repeated, repeated])
    runner = ToolLoopRunner(tool_llm=llm, registry=registry)

    outcome = await runner.run([Message(MessageRole.USER, "run")])

    assert calls == 1
    assert outcome.stop_reason == "duplicate_cycle"


@pytest.mark.asyncio
async def test_cancellation_prevents_side_effect_from_starting() -> None:
    registry = _empty_registry()
    called = False

    async def dangerous() -> str:
        nonlocal called
        called = True
        return "sent"

    _register(registry, "dangerous", dangerous, side_effect=True, permission="owner")
    llm = SequenceToolLLM([[
        ToolCall(id="1", name="dangerous", arguments={}),
    ]])
    runner = ToolLoopRunner(tool_llm=llm, registry=registry)
    cancellation = CancellationRegistry().get_or_create("task")
    cancellation.cancel("user stopped")

    with pytest.raises(asyncio.CancelledError):
        await runner.run(
            [Message(MessageRole.USER, "run")],
            access_context={"permissions": ["owner"], "allow_side_effects": True},
            cancellation=cancellation,
        )
    assert not called


@pytest.mark.asyncio
async def test_execution_boundary_rejects_stale_unauthorized_call() -> None:
    registry = _empty_registry()
    called = False

    async def dangerous() -> str:
        nonlocal called
        called = True
        return "sent"

    _register(registry, "dangerous", dangerous, side_effect=True, permission="owner")
    # Simulate a stale or malicious tool call returned after the candidate list changed.
    llm = SequenceToolLLM([[
        ToolCall(id="1", name="dangerous", arguments={}),
    ], []])
    runner = ToolLoopRunner(tool_llm=llm, registry=registry)

    outcome = await runner.run(
        [Message(MessageRole.USER, "run")],
        access_context={"permissions": [], "allow_side_effects": False},
    )

    assert not called
    assert outcome.runs == []
    assert outcome.stop_reason == "no_available_tools"


@pytest.mark.asyncio
async def test_tool_loop_writes_observable_task_events(tmp_path: Path) -> None:
    registry = _empty_registry()

    async def lookup() -> str:
        return "value"

    _register(registry, "lookup", lookup)
    llm = SequenceToolLLM([[ToolCall(id="1", name="lookup", arguments={})], []])
    runner = ToolLoopRunner(tool_llm=llm, registry=registry)
    store = RuntimeStore(tmp_path / "runtime.db")
    task = store.create_task(ConversationRef("desktop", "owner"), "lookup")

    await runner.run(
        [Message(MessageRole.USER, "lookup")],
        store=store,
        task_id=task.id,
    )

    event_types = [event.event_type for event in store.list_events(task.id)]
    assert event_types == [
        "task_submitted",
        "tool_judging",
        "tool_calls_planned",
        "tool_started",
        "tool_completed",
        "tool_judging",
    ]


@pytest.mark.asyncio
async def test_side_effect_lock_is_shared_across_runner_instances() -> None:
    registry = _empty_registry()
    active = 0
    max_active = 0

    async def mutate() -> str:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.02)
        active -= 1
        return "done"

    _register(registry, "mutate", mutate, side_effect=True, permission="owner")
    first = ToolLoopRunner(
        tool_llm=SequenceToolLLM([[ToolCall(id="1", name="mutate", arguments={})], []]),
        registry=registry,
    )
    second = ToolLoopRunner(
        tool_llm=SequenceToolLLM([[ToolCall(id="2", name="mutate", arguments={})], []]),
        registry=registry,
    )

    await asyncio.gather(*(
        runner.run(
            [Message(MessageRole.USER, "run")],
            access_context={"permissions": ["owner"], "allow_side_effects": True},
        )
        for runner in (first, second)
    ))

    assert max_active == 1


@pytest.mark.asyncio
async def test_side_effect_exception_is_reported_as_unknown_outcome() -> None:
    registry = _empty_registry()

    async def uncertain() -> str:
        raise TimeoutError("connection lost after request")

    _register(registry, "uncertain", uncertain, side_effect=True, permission="owner")
    runner = ToolLoopRunner(
        tool_llm=SequenceToolLLM([
            [ToolCall(id="1", name="uncertain", arguments={})],
            [],
        ]),
        registry=registry,
    )

    outcome = await runner.run(
        [Message(MessageRole.USER, "run")],
        access_context={"permissions": ["owner"], "allow_side_effects": True},
    )

    assert outcome.runs[0].ok is False
    assert outcome.runs[0].outcome_known is False
    assert outcome.unconfirmed_side_effects == ["uncertain"]
    assert "outcome_unknown_do_not_retry" in outcome.tool_results[0].content


@pytest.mark.asyncio
async def test_post_execution_observability_failure_keeps_real_result() -> None:
    registry = _empty_registry()
    called = 0

    async def mutate() -> str:
        nonlocal called
        called += 1
        return "actually completed"

    class FailingCompletionStore:
        def append_event(self, task_id, event_type, payload):
            if event_type == "tool_completed":
                raise OSError("journal temporarily unavailable")

    _register(registry, "mutate", mutate, side_effect=True, permission="owner")
    runner = ToolLoopRunner(
        tool_llm=SequenceToolLLM([
            [ToolCall(id="1", name="mutate", arguments={})],
            [],
        ]),
        registry=registry,
    )

    outcome = await runner.run(
        [Message(MessageRole.USER, "run")],
        access_context={"permissions": ["owner"], "allow_side_effects": True},
        store=FailingCompletionStore(),
        task_id="task-1",
    )

    assert called == 1
    assert outcome.runs[0].ok is True
    assert outcome.runs[0].content == "actually completed"
