"""Behavioral tests for the conversation-scoped runtime service."""

from __future__ import annotations

import asyncio
import sqlite3

import pytest

from white_salary.core.runtime import (
    AgentRuntimeService,
    ChannelAddress,
    ConversationRef,
    RuntimeStore,
    TaskExecutionResult,
    TaskState,
)
from white_salary.core.runtime.store import InvalidTaskTransition


def _conversation(name: str) -> ConversationRef:
    return ConversationRef(
        platform="qq",
        conversation_id=name,
        scope="group",
        user_id="owner",
        group_id=name,
    )


async def test_same_conversation_runs_fifo_without_blocking_submit(tmp_path) -> None:
    service = AgentRuntimeService(RuntimeStore(tmp_path / "runtime.db"))
    first_release = asyncio.Event()
    order: list[str] = []

    async def first(record, token):
        order.append("first-start")
        await first_release.wait()
        order.append("first-end")
        return "first done"

    async def second(record, token):
        order.append("second-start")
        return "second done"

    one = await service.submit(_conversation("g1"), "one", first)
    two = await service.submit(_conversation("g1"), "two", second)
    assert service.store.get_task(one.id).state == TaskState.WORKING
    assert service.store.get_task(two.id).state == TaskState.SUBMITTED
    assert order == ["first-start"]

    first_release.set()
    await service.wait_for_idle(_conversation("g1").key)
    assert order == ["first-start", "first-end", "second-start"]
    assert service.store.get_task(one.id).state == TaskState.COMPLETED
    assert service.store.get_task(two.id).state == TaskState.COMPLETED
    await service.close()


async def test_different_conversations_run_concurrently(tmp_path) -> None:
    service = AgentRuntimeService(RuntimeStore(tmp_path / "runtime.db"))
    both_started = asyncio.Event()
    started: set[str] = set()

    async def runner(record, token):
        started.add(record.conversation.key)
        if len(started) == 2:
            both_started.set()
        await both_started.wait()

    await asyncio.gather(
        service.submit(_conversation("g1"), "one", runner),
        service.submit(_conversation("g2"), "two", runner),
    )
    await asyncio.wait_for(both_started.wait(), timeout=1)
    await service.wait_for_idle()
    assert started == {_conversation("g1").key, _conversation("g2").key}
    await service.close()


async def test_cancel_active_task_does_not_kill_next_message(tmp_path) -> None:
    service = AgentRuntimeService(RuntimeStore(tmp_path / "runtime.db"))
    next_ran = asyncio.Event()

    async def cancellable(record, token):
        await token.wait()
        token.raise_if_cancelled()

    async def next_runner(record, token):
        next_ran.set()

    active = await service.submit(_conversation("g1"), "active", cancellable)
    queued = await service.submit(_conversation("g1"), "queued", next_runner)
    await service.cancel(active.id, "user stopped")
    await service.wait_for_idle(_conversation("g1").key)

    assert service.store.get_task(active.id).state == TaskState.CANCELLED
    assert service.store.get_task(queued.id).state == TaskState.COMPLETED
    assert next_ran.is_set()
    await service.close()


async def test_completion_queues_one_idempotent_response(tmp_path) -> None:
    store = RuntimeStore(tmp_path / "runtime.db")
    service = AgentRuntimeService(store)
    target = ChannelAddress(platform="qq", address="10001", is_group=False)

    async def runner(record, token):
        return TaskExecutionResult(
            summary="done",
            response_payload={"type": "text", "text": "finished"},
        )

    task = await service.submit(
        _conversation("private-10001"),
        "work",
        runner,
        response_address=target,
    )
    await service.wait_for_idle(task.conversation.key)
    deliveries = store.claim_due_deliveries(limit=10, lease_seconds=30)
    assert len(deliveries) == 1
    assert deliveries[0].task_id == task.id
    assert deliveries[0].payload["text"] == "finished"
    await service.close()


async def test_restart_lists_unfinished_task_without_replaying_it(tmp_path) -> None:
    store = RuntimeStore(tmp_path / "runtime.db")
    record = store.create_task(_conversation("g1"), "recover me")
    store.transition_task(record.id, TaskState.WORKING)

    service = AgentRuntimeService(RuntimeStore(tmp_path / "runtime.db"))
    recovered = service.recoverable_tasks()
    assert [task.id for task in recovered] == [record.id]
    assert service.store.list_events(record.id)[-1].event_type == "task_state_changed"
    await service.close()


async def test_shutdown_marks_unconfirmed_side_effect_for_reconciliation(tmp_path) -> None:
    store = RuntimeStore(tmp_path / "runtime.db")
    service = AgentRuntimeService(store, shutdown_grace_seconds=0.01)
    started = asyncio.Event()

    async def long_runner(record, token):
        started.set()
        await asyncio.Event().wait()

    task = await service.submit(_conversation("g1"), "keep me", long_runner)
    await started.wait()
    await service.close()

    saved = store.get_task(task.id)
    assert saved is not None
    assert saved.state == TaskState.RECONCILIATION_REQUIRED
    assert "outcome was confirmed" in saved.error

    reconciled = service.reconcile(
        task.id,
        TaskState.COMPLETED,
        summary="remote service confirmed completion",
    )
    assert reconciled.state == TaskState.COMPLETED


async def test_shutdown_suspends_hard_cancel_safe_task_for_recovery(tmp_path) -> None:
    store = RuntimeStore(tmp_path / "runtime.db")
    service = AgentRuntimeService(store, shutdown_grace_seconds=0.01)
    started = asyncio.Event()

    async def safe_runner(record, token):
        started.set()
        await asyncio.Event().wait()

    task = await service.submit(
        _conversation("g1"),
        "safe read",
        safe_runner,
        hard_cancel=True,
    )
    await started.wait()
    await service.close()

    saved = store.get_task(task.id)
    assert saved is not None and saved.state == TaskState.WORKING
    assert store.list_events(task.id)[-1].event_type == "task_suspended"


async def test_rejected_task_does_not_block_next_conversation_item(tmp_path) -> None:
    service = AgentRuntimeService(RuntimeStore(tmp_path / "runtime.db"))
    next_ran = asyncio.Event()

    async def rejected(record, token):
        return TaskExecutionResult(state=TaskState.REJECTED, error="policy denied")

    async def next_runner(record, token):
        next_ran.set()

    first = await service.submit(_conversation("g1"), "unsafe", rejected)
    second = await service.submit(_conversation("g1"), "safe", next_runner)
    await service.wait_for_idle(_conversation("g1").key)

    assert service.store.get_task(first.id).state == TaskState.REJECTED
    assert service.store.get_task(second.id).state == TaskState.COMPLETED
    assert next_ran.is_set()
    await service.close()


def test_task_completion_and_response_enqueue_roll_back_together(tmp_path) -> None:
    store = RuntimeStore(tmp_path / "runtime.db")
    task = store.create_task(_conversation("g1"), "work")
    store.transition_task(task.id, TaskState.WORKING)
    with store._connect() as conn:
        conn.execute(
            """
            CREATE TRIGGER reject_test_outbox
            BEFORE INSERT ON runtime_outbox
            BEGIN
                SELECT RAISE(ABORT, 'forced outbox failure');
            END
            """
        )

    with pytest.raises(sqlite3.IntegrityError):
        store.transition_task_and_enqueue_delivery(
            task.id,
            TaskState.COMPLETED,
            target=ChannelAddress("qq", "10001"),
            payload={"type": "text", "text": "done"},
            result_summary="done",
            idempotency_key=f"task-response:{task.id}",
        )

    saved = store.get_task(task.id)
    assert saved is not None and saved.state == TaskState.WORKING
    assert store.list_deliveries() == []


async def test_resume_cannot_bypass_input_or_authorization_gates(tmp_path) -> None:
    service = AgentRuntimeService(RuntimeStore(tmp_path / "runtime.db"))
    ran: list[str] = []

    async def runner(record, token):
        ran.append(record.id)

    input_task = service.store.create_task(_conversation("input"), "need input")
    service.store.transition_task(input_task.id, TaskState.INPUT_REQUIRED)
    auth_task = service.store.create_task(_conversation("auth"), "need auth")
    service.store.transition_task(auth_task.id, TaskState.AUTH_REQUIRED)

    with pytest.raises(InvalidTaskTransition):
        await service.resume(input_task.id, runner)
    with pytest.raises(InvalidTaskTransition):
        await service.resume(auth_task.id, runner)
    assert ran == []

    await service.provide_input(input_task.id, runner, field_names=("city",))
    await service.authorize(auth_task.id, runner, scope="send_qq_message")
    await service.wait_for_idle()

    assert set(ran) == {input_task.id, auth_task.id}
    assert service.store.get_task(input_task.id).state == TaskState.COMPLETED
    assert service.store.get_task(auth_task.id).state == TaskState.COMPLETED
    await service.close()
