"""Delivery worker tests for verified success, retry, and ambiguity."""

from __future__ import annotations

from pathlib import Path

import pytest

from white_salary.core.runtime import (
    ChannelAddress,
    DeliveryResult,
    DeliveryRouter,
    DeliveryState,
    DeliveryWorker,
    RuntimeStore,
    StaleDeliveryClaim,
)


def _enqueue(store: RuntimeStore, *, max_attempts: int = 3):
    return store.enqueue_delivery(
        ChannelAddress("qq", "123", is_group=False),
        {"type": "text", "content": "hello"},
        conversation_key="desktop:private:owner",
        max_attempts=max_attempts,
    )


@pytest.mark.asyncio
async def test_success_requires_real_platform_receipt(tmp_path: Path) -> None:
    store = RuntimeStore(tmp_path / "runtime.db")
    delivery = _enqueue(store)
    router = DeliveryRouter()
    router.register("qq", lambda item: DeliveryResult(
        success=True,
        receipt={"message_id": 42},
    ))

    assert await DeliveryWorker(store, router).process_once() == 1
    saved = store.get_delivery(delivery.id)
    assert saved is not None
    assert saved.state == DeliveryState.DELIVERED
    assert saved.receipt == {"message_id": 42}


@pytest.mark.asyncio
async def test_success_without_receipt_becomes_unknown(tmp_path: Path) -> None:
    store = RuntimeStore(tmp_path / "runtime.db")
    delivery = _enqueue(store)
    router = DeliveryRouter()
    router.register("qq", lambda item: DeliveryResult(success=True))

    await DeliveryWorker(store, router).process_once()
    saved = store.get_delivery(delivery.id)
    assert saved is not None
    assert saved.state == DeliveryState.UNKNOWN


@pytest.mark.asyncio
async def test_adapter_exception_is_not_blindly_replayed(tmp_path: Path) -> None:
    store = RuntimeStore(tmp_path / "runtime.db")
    delivery = _enqueue(store)
    router = DeliveryRouter()

    async def uncertain(item):
        raise TimeoutError("socket closed after send")

    router.register("qq", uncertain)
    await DeliveryWorker(store, router).process_once()
    saved = store.get_delivery(delivery.id)
    assert saved is not None
    assert saved.state == DeliveryState.UNKNOWN
    assert store.claim_due_deliveries() == []


@pytest.mark.asyncio
async def test_known_retryable_failure_returns_to_pending(tmp_path: Path) -> None:
    store = RuntimeStore(tmp_path / "runtime.db")
    delivery = _enqueue(store)
    router = DeliveryRouter()
    router.register("qq", lambda item: DeliveryResult(
        success=False,
        retryable=True,
        error="NapCat offline before send",
    ))

    await DeliveryWorker(store, router).process_once()
    saved = store.get_delivery(delivery.id)
    assert saved is not None
    assert saved.state == DeliveryState.PENDING
    assert saved.last_error == "NapCat offline before send"


@pytest.mark.asyncio
async def test_permanent_failure_reaches_terminal_state(tmp_path: Path) -> None:
    store = RuntimeStore(tmp_path / "runtime.db")
    delivery = _enqueue(store, max_attempts=2)
    router = DeliveryRouter()
    router.register("qq", lambda item: DeliveryResult(
        success=False,
        retryable=False,
        error="invalid target",
    ))

    await DeliveryWorker(store, router).process_once()
    saved = store.get_delivery(delivery.id)
    assert saved is not None
    assert saved.state == DeliveryState.FAILED


def test_unknown_delivery_requires_explicit_reconciliation(tmp_path: Path) -> None:
    store = RuntimeStore(tmp_path / "runtime.db")
    delivery = _enqueue(store)
    claimed = store.claim_due_deliveries(now=delivery.available_at)
    store.mark_delivery_unknown(
        delivery.id,
        "receipt lost",
        claim_token=claimed[0].claim_token,
    )

    requeued = store.requeue_unknown_delivery(delivery.id)
    assert requeued.state == DeliveryState.PENDING


def test_late_worker_cannot_overwrite_newer_success(tmp_path: Path) -> None:
    store = RuntimeStore(tmp_path / "runtime.db")
    delivery = store.enqueue_delivery(
        ChannelAddress("internal", "cache"),
        {"type": "refresh"},
        conversation_key="desktop:private:owner",
        replay_safe=True,
    )
    first = store.claim_due_deliveries(
        now=delivery.available_at,
        lease_seconds=1,
    )[0]
    second = store.claim_due_deliveries(
        now=first.lease_until + 0.01,
        lease_seconds=1,
    )[0]
    assert first.claim_token and second.claim_token != first.claim_token

    store.mark_delivery_delivered(
        delivery.id,
        {"revision": 2},
        claim_token=second.claim_token,
    )
    with pytest.raises(StaleDeliveryClaim):
        store.mark_delivery_failed(
            delivery.id,
            "late failure",
            claim_token=first.claim_token,
            retry_delay=0,
        )

    saved = store.get_delivery(delivery.id)
    assert saved is not None
    assert saved.state == DeliveryState.DELIVERED
    assert saved.receipt == {"revision": 2}


def test_expired_delivery_does_not_exceed_max_attempts(tmp_path: Path) -> None:
    store = RuntimeStore(tmp_path / "runtime.db")
    delivery = store.enqueue_delivery(
        ChannelAddress("internal", "cache"),
        {"type": "refresh"},
        conversation_key="desktop:private:owner",
        max_attempts=1,
        replay_safe=True,
    )
    first = store.claim_due_deliveries(
        now=delivery.available_at,
        lease_seconds=1,
    )[0]

    assert store.claim_due_deliveries(now=first.lease_until + 0.01) == []
    saved = store.get_delivery(delivery.id)
    assert saved is not None
    assert saved.state == DeliveryState.FAILED
    assert saved.attempts == 1
