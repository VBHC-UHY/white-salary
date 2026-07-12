"""Reliable cross-platform delivery worker with explicit ambiguity handling."""

from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from loguru import logger

from .models import DeliveryRecord
from .store import RuntimeStore, StaleDeliveryClaim


@dataclass(frozen=True)
class DeliveryResult:
    """Result returned by a platform adapter."""

    success: bool
    receipt: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    retryable: bool = False
    outcome_known: bool = True


DeliveryHandler = Callable[[DeliveryRecord], Awaitable[DeliveryResult] | DeliveryResult]


class DeliveryRouter:
    """Maps a durable ChannelAddress to its platform delivery adapter."""

    def __init__(self) -> None:
        self._handlers: dict[str, DeliveryHandler] = {}

    def register(self, platform: str, handler: DeliveryHandler) -> None:
        self._handlers[platform.strip().lower()] = handler

    def unregister(self, platform: str) -> None:
        self._handlers.pop(platform.strip().lower(), None)

    def get(self, platform: str) -> DeliveryHandler | None:
        return self._handlers.get(platform.strip().lower())


class DeliveryWorker:
    """Claims outbox entries and records only verified platform outcomes."""

    def __init__(
        self,
        store: RuntimeStore,
        router: DeliveryRouter,
        *,
        poll_interval: float = 0.5,
        lease_seconds: float = 30.0,
        batch_size: int = 20,
    ) -> None:
        self._store = store
        self._router = router
        self._poll_interval = max(0.05, float(poll_interval))
        self._lease_seconds = max(1.0, float(lease_seconds))
        self._batch_size = max(1, int(batch_size))
        self._runner: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    def start(self) -> None:
        if self._runner is None or self._runner.done():
            self._stop.clear()
            self._runner = asyncio.create_task(self._run(), name="delivery-worker")

    async def stop(self) -> None:
        self._stop.set()
        if self._runner is not None:
            await self._runner
            self._runner = None

    async def process_once(self) -> int:
        deliveries = self._store.claim_due_deliveries(
            limit=self._batch_size,
            lease_seconds=self._lease_seconds,
        )
        for delivery in deliveries:
            await self._deliver(delivery)
        return len(deliveries)

    async def _run(self) -> None:
        while not self._stop.is_set():
            processed = await self.process_once()
            if processed:
                continue
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self._poll_interval)
            except asyncio.TimeoutError:
                pass

    async def _deliver(self, delivery: DeliveryRecord) -> None:
        handler = self._router.get(delivery.target.platform)
        if handler is None:
            if not self._apply_claim_update(
                delivery,
                self._store.mark_delivery_failed,
                delivery.id,
                f"No delivery handler registered for {delivery.target.platform}",
            ):
                return
            return
        try:
            value = handler(delivery)
            result = await value if inspect.isawaitable(value) else value
        except Exception as exc:
            # The adapter may have completed the external side effect before
            # losing its response. Do not duplicate a message automatically.
            if not self._apply_claim_update(
                delivery,
                self._store.mark_delivery_unknown,
                delivery.id,
                str(exc),
            ):
                return
            self._append_task_event(delivery, "delivery_unknown", str(exc))
            return

        if result.success:
            if not result.receipt:
                if not self._apply_claim_update(
                    delivery,
                    self._store.mark_delivery_unknown,
                    delivery.id,
                    "Platform reported success without a verifiable receipt",
                ):
                    return
                self._append_task_event(delivery, "delivery_unknown", "missing receipt")
                return
            if not self._apply_claim_update(
                delivery,
                self._store.mark_delivery_delivered,
                delivery.id,
                result.receipt,
            ):
                return
            self._append_task_event(delivery, "delivery_completed", "")
            return

        if not result.outcome_known:
            updated = self._apply_claim_update(
                delivery,
                self._store.mark_delivery_unknown,
                delivery.id,
                result.error or "Unknown outcome",
            )
            if not updated:
                return
            self._append_task_event(delivery, "delivery_unknown", result.error)
        elif result.retryable:
            updated = self._apply_claim_update(
                delivery,
                self._store.mark_delivery_failed,
                delivery.id,
                result.error or "Retryable failure",
            )
            if not updated:
                return
            self._append_task_event(delivery, "delivery_retry_scheduled", result.error)
        else:
            updated = self._apply_claim_update(
                delivery,
                self._store.mark_delivery_permanently_failed,
                delivery.id,
                result.error or "Permanent failure",
            )
            if not updated:
                return
            self._append_task_event(delivery, "delivery_failed", result.error)

    @staticmethod
    def _apply_claim_update(
        delivery: DeliveryRecord,
        operation: Callable[..., Any],
        *args: Any,
    ) -> bool:
        try:
            operation(*args, claim_token=delivery.claim_token)
            return True
        except StaleDeliveryClaim:
            logger.info(
                f"[Delivery] 忽略过期投递结果: {delivery.id} "
                f"attempt={delivery.attempts}"
            )
            return False

    def _append_task_event(self, delivery: DeliveryRecord, event_type: str, error: str) -> None:
        if not delivery.task_id or self._store.get_task(delivery.task_id) is None:
            return
        self._store.append_event(
            delivery.task_id,
            event_type,
            {
                "delivery_id": delivery.id,
                "platform": delivery.target.platform,
                "error": error,
            },
        )
