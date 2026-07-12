"""Contract tests for the platform-neutral Agent Runtime v2 foundation."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from white_salary.core.runtime import (
    CancellationRegistry,
    ChannelAddress,
    ConversationActorRegistry,
    ConversationRef,
    DeliveryState,
    RuntimeStore,
    TaskState,
)
from white_salary.core.runtime.store import InvalidTaskTransition


def test_conversation_keys_separate_platform_scope_and_private_user() -> None:
    group = ConversationRef(
        platform="qq",
        scope="group",
        conversation_id="10001",
        group_id="10001",
        user_id="owner",
    )
    private = ConversationRef(
        platform="qq",
        scope="private",
        conversation_id="owner",
        user_id="owner",
    )
    desktop = ConversationRef(
        platform="desktop",
        scope="private",
        conversation_id="owner",
        user_id="owner",
    )

    assert group.key == "qq:group:10001"
    assert private.key == "qq:private:owner"
    assert desktop.key == "desktop:private:owner"
    assert group.private_state_key == "qq:group:10001:user:owner"
    assert len({group.key, private.key, desktop.key}) == 3


def test_task_state_and_events_survive_store_restart(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    conversation = ConversationRef("qq", "10001", scope="group", user_id="owner")
    target = ChannelAddress("desktop", "owner")
    first = RuntimeStore(db_path)

    task = first.create_task(
        conversation,
        "在桌面告诉我一声",
        owner_id="owner",
        response_address=target,
        metadata={"source": "qq"},
    )
    first.transition_task(task.id, TaskState.WORKING)
    first.append_event(task.id, "tool_started", {"tool": "push_to_desktop"})

    reopened = RuntimeStore(db_path)
    restored = reopened.get_task(task.id)
    assert restored is not None
    assert restored.state == TaskState.WORKING
    assert restored.response_address == target
    assert restored.metadata == {"source": "qq"}
    assert [event.sequence for event in reopened.list_events(task.id)] == [1, 2, 3]


def test_task_creation_is_idempotent(tmp_path: Path) -> None:
    store = RuntimeStore(tmp_path / "runtime.db")
    conversation = ConversationRef("desktop", "owner", user_id="owner")
    first = store.create_task(conversation, "生成图片", idempotency_key="message:42")
    second = store.create_task(conversation, "不应重复创建", idempotency_key="message:42")

    assert first.id == second.id
    assert second.request_text == "生成图片"


def test_invalid_terminal_transition_is_rejected(tmp_path: Path) -> None:
    store = RuntimeStore(tmp_path / "runtime.db")
    task = store.create_task(ConversationRef("desktop", "owner"), "测试")
    store.transition_task(task.id, TaskState.WORKING)
    store.transition_task(task.id, TaskState.COMPLETED, result_summary="完成")

    with pytest.raises(InvalidTaskTransition):
        store.transition_task(task.id, TaskState.WORKING)


def test_side_effect_task_can_report_cancel_requested_before_real_terminal_state(
    tmp_path: Path,
) -> None:
    store = RuntimeStore(tmp_path / "runtime.db")
    task = store.create_task(ConversationRef("desktop", "owner"), "生成长视频")
    store.transition_task(task.id, TaskState.WORKING)
    requested = store.transition_task(task.id, TaskState.CANCEL_REQUESTED)
    assert requested.state == TaskState.CANCEL_REQUESTED

    completed = store.transition_task(
        task.id,
        TaskState.COMPLETED,
        result_summary="工具无法中途停止，但已完成",
    )
    assert completed.state == TaskState.COMPLETED


def test_outbox_retries_then_acknowledges_real_delivery(tmp_path: Path) -> None:
    store = RuntimeStore(tmp_path / "runtime.db")
    delivery = store.enqueue_delivery(
        ChannelAddress("qq", "123", is_group=False),
        {"type": "text", "content": "完成了"},
        conversation_key="desktop:private:owner",
        idempotency_key="task:1:final",
        max_attempts=3,
    )

    claimed = store.claim_due_deliveries(now=delivery.available_at)
    assert len(claimed) == 1
    assert claimed[0].state == DeliveryState.SENDING
    assert claimed[0].attempts == 1

    retry = store.mark_delivery_failed(
        delivery.id,
        "NapCat offline",
        claim_token=claimed[0].claim_token,
        retry_delay=0,
    )
    assert retry.state == DeliveryState.PENDING
    claimed_again = store.claim_due_deliveries(now=retry.available_at)
    assert claimed_again[0].attempts == 2

    delivered = store.mark_delivery_delivered(
        delivery.id,
        {"message_id": 99},
        claim_token=claimed_again[0].claim_token,
    )
    assert delivered.state == DeliveryState.DELIVERED
    assert delivered.receipt == {"message_id": 99}


def test_ambiguous_stale_delivery_is_not_replayed_after_restart(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    store = RuntimeStore(db_path)
    delivery = store.enqueue_delivery(
        ChannelAddress("desktop", "owner"),
        {"type": "text", "content": "hello"},
        conversation_key="qq:private:owner",
    )
    claimed = store.claim_due_deliveries(now=delivery.available_at, lease_seconds=1)
    assert claimed[0].state == DeliveryState.SENDING

    reopened = RuntimeStore(db_path)
    recovered = reopened.claim_due_deliveries(now=claimed[0].lease_until + 0.01)
    assert recovered == []
    ambiguous = reopened.get_delivery(delivery.id)
    assert ambiguous is not None
    assert ambiguous.state == DeliveryState.UNKNOWN


def test_replay_safe_stale_delivery_can_be_recovered(tmp_path: Path) -> None:
    db_path = tmp_path / "runtime.db"
    store = RuntimeStore(db_path)
    delivery = store.enqueue_delivery(
        ChannelAddress("internal", "cache"),
        {"type": "refresh"},
        conversation_key="desktop:private:owner",
        replay_safe=True,
    )
    claimed = store.claim_due_deliveries(now=delivery.available_at, lease_seconds=1)

    reopened = RuntimeStore(db_path)
    recovered = reopened.claim_due_deliveries(now=claimed[0].lease_until + 0.01)
    assert len(recovered) == 1
    assert recovered[0].attempts == 2


@pytest.mark.asyncio
async def test_conversation_actor_serializes_one_conversation() -> None:
    registry = ConversationActorRegistry()
    active = 0
    max_active = 0
    order: list[str] = []

    async def handler(value: str) -> str:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        order.append(f"start:{value}")
        await asyncio.sleep(0.01)
        order.append(f"end:{value}")
        active -= 1
        return value.upper()

    actor = await registry.get_or_create("qq:group:1", handler)
    results = await asyncio.gather(actor.ask("a"), actor.ask("b"), actor.ask("c"))
    await registry.close_all()

    assert results == ["A", "B", "C"]
    assert max_active == 1
    assert order == ["start:a", "end:a", "start:b", "end:b", "start:c", "end:c"]


@pytest.mark.asyncio
async def test_different_conversation_actors_can_run_in_parallel() -> None:
    registry = ConversationActorRegistry()
    started = 0
    both_started = asyncio.Event()
    release = asyncio.Event()

    async def handler(value: str) -> str:
        nonlocal started
        started += 1
        if started == 2:
            both_started.set()
        await release.wait()
        return value

    first = await registry.get_or_create("qq:group:1", handler)
    second = await registry.get_or_create("qq:group:2", handler)
    calls = [asyncio.create_task(first.ask("a")), asyncio.create_task(second.ask("b"))]
    await asyncio.wait_for(both_started.wait(), timeout=1)
    release.set()
    assert await asyncio.gather(*calls) == ["a", "b"]
    await registry.close_all()


@pytest.mark.asyncio
async def test_actor_survives_handler_exception() -> None:
    registry = ConversationActorRegistry()

    async def handler(value: str) -> str:
        if value == "bad":
            raise ValueError("bad message")
        return value

    actor = await registry.get_or_create("qq:private:1", handler)
    with pytest.raises(ValueError):
        await actor.ask("bad")
    assert await actor.ask("good") == "good"
    await registry.close_all()


@pytest.mark.asyncio
async def test_actor_survives_cooperative_message_cancellation() -> None:
    registry = ConversationActorRegistry()

    async def handler(value: str) -> str:
        if value == "cancel":
            raise asyncio.CancelledError("cancel this message only")
        return value

    actor = await registry.get_or_create("qq:private:1", handler)
    with pytest.raises(asyncio.CancelledError):
        await actor.ask("cancel")
    assert await actor.ask("next") == "next"
    await registry.close_all()


@pytest.mark.asyncio
async def test_timed_out_queued_actor_message_never_executes() -> None:
    registry = ConversationActorRegistry()
    release = asyncio.Event()
    executed: list[str] = []

    async def handler(value: str) -> str:
        if value == "block":
            await release.wait()
        executed.append(value)
        return value

    actor = await registry.get_or_create("qq:private:timeout", handler)
    blocker = asyncio.create_task(actor.ask("block"))
    await asyncio.sleep(0)
    with pytest.raises(asyncio.TimeoutError):
        await actor.ask("side-effect", timeout=0.01)
    release.set()
    await blocker
    await actor.join()

    assert executed == ["block"]
    await registry.close_all()


@pytest.mark.asyncio
async def test_cancellation_registry_is_cooperative() -> None:
    registry = CancellationRegistry()
    token = registry.get_or_create("task-1")
    assert not token.cancelled
    assert registry.cancel("task-1", "用户要求停止")
    await asyncio.wait_for(token.wait(), timeout=0.1)
    assert token.cancelled
    assert token.reason == "用户要求停止"
    with pytest.raises(asyncio.CancelledError):
        token.raise_if_cancelled()


@pytest.mark.asyncio
async def test_cancellation_registry_only_hard_cancels_safe_tasks() -> None:
    registry = CancellationRegistry()
    safe_started = asyncio.Event()
    side_effect_started = asyncio.Event()

    async def wait_forever(started: asyncio.Event) -> None:
        started.set()
        await asyncio.Event().wait()

    safe_task = asyncio.create_task(wait_forever(safe_started))
    side_effect_task = asyncio.create_task(wait_forever(side_effect_started))
    await safe_started.wait()
    await side_effect_started.wait()
    registry.bind_task("safe", safe_task, hard_cancel=True)
    side_token = registry.bind_task("side", side_effect_task, hard_cancel=False)

    registry.cancel("safe", "replace foreground response")
    registry.cancel("side", "user asked to stop")
    await asyncio.sleep(0)

    assert safe_task.cancelled()
    assert side_token.cancelled
    assert not side_effect_task.cancelled()
    side_effect_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await side_effect_task
