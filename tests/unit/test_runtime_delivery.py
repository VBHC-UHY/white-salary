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


def test_requeue_after_exhaustion_grants_fresh_attempts(tmp_path: Path) -> None:
    """人工复活必须重置尝试预算。

    UNKNOWN 行的 attempts 往往已等于 max_attempts（正是最后一次尝试结果
    不明才落到 UNKNOWN）。修复前复活成 PENDING 后，下一次 claim 的耗尽
    清扫会立刻把它打回 FAILED——号称唯一的人工恢复路径实际静默无效。
    """
    store = RuntimeStore(tmp_path / "runtime.db")
    delivery = _enqueue(store, max_attempts=1)
    claimed = store.claim_due_deliveries(now=delivery.available_at)
    store.mark_delivery_unknown(
        delivery.id,
        "receipt lost",
        claim_token=claimed[0].claim_token,
    )

    requeued = store.requeue_unknown_delivery(delivery.id)
    assert requeued.attempts == 0

    reclaimed = store.claim_due_deliveries(now=requeued.available_at + 0.01)
    assert [item.id for item in reclaimed] == [delivery.id]


def test_claim_sweep_only_touches_requested_platform(tmp_path: Path) -> None:
    """清扫只碰本平台：桌面桥的高频 claim 不得把 QQ 在飞行打成 UNKNOWN。

    QQ 经 NapCat 逐条串行发送，一批轻易超过 30 秒租约；修复前桌面桥每
    2 秒一次的 claim 会把"租约刚过期、实际仍在发送"的 QQ 行清扫成
    UNKNOWN 终态，随后真实的 ack 抛 StaleDeliveryClaim 被结算保护吞掉，
    已送达的消息在账面上永远停在"结果不明"。
    """
    store = RuntimeStore(tmp_path / "runtime.db")
    qq_row = store.enqueue_delivery(
        ChannelAddress("qq_bridge", "10001"),
        {"message": "in flight"},
        conversation_key="bridge:qq:private:10001",
    )
    store.enqueue_delivery(
        ChannelAddress("desktop_bridge", "primary"),
        {"message": "desktop"},
        conversation_key="bridge:desktop:primary",
    )

    claimed = store.claim_due_deliveries(
        platform="qq_bridge", now=qq_row.available_at, lease_seconds=30.0
    )
    assert [item.id for item in claimed] == [qq_row.id]
    expired_at = claimed[0].lease_until + 0.01

    # 桌面平台的 claim 发生在 QQ 行租约过期之后——不得动 QQ 的行
    store.claim_due_deliveries(platform="desktop_bridge", now=expired_at)
    in_flight = store.get_delivery(qq_row.id)
    assert in_flight is not None
    assert in_flight.state == DeliveryState.SENDING

    # 真正的消费端此刻完成发送，迟到但真实的 ack 仍然有效
    store.mark_delivery_delivered(
        qq_row.id, {"message_id": 7}, claim_token=claimed[0].claim_token
    )
    saved = store.get_delivery(qq_row.id)
    assert saved is not None and saved.state == DeliveryState.DELIVERED

    # 平台内不存在并发 claim（消费循环结算完上一批才取下一批），所以
    # 本平台清扫命中的只会是真正无人认领的行（如进程崩溃遗留）——仍要兜住
    orphan = store.enqueue_delivery(
        ChannelAddress("qq_bridge", "10002"),
        {"message": "orphan"},
        conversation_key="bridge:qq:private:10002",
    )
    orphan_claimed = store.claim_due_deliveries(
        platform="qq_bridge", now=orphan.available_at, lease_seconds=1.0
    )
    store.claim_due_deliveries(
        platform="qq_bridge", now=orphan_claimed[0].lease_until + 0.01
    )
    swept = store.get_delivery(orphan.id)
    assert swept is not None
    assert swept.state == DeliveryState.UNKNOWN
