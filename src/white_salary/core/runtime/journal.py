"""Non-invasive task journaling for existing interactive platform handlers."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from loguru import logger

from .models import ChannelAddress, ConversationRef, TaskRecord, TaskState
from .store import InvalidTaskTransition, RuntimeStore


@dataclass
class InteractiveTaskHandle:
    """A best-effort lifecycle handle that must never break user messaging."""

    store: RuntimeStore
    record: TaskRecord
    created: bool

    @property
    def id(self) -> str:
        return self.record.id

    @property
    def should_process(self) -> bool:
        """False when the same idempotent platform event was already journaled."""

        return self.created

    def refresh(self) -> TaskRecord:
        current = self.store.get_task(self.id)
        if current is not None:
            self.record = current
        return self.record

    def append_event(self, event_type: str, payload: dict[str, Any] | None = None) -> None:
        try:
            self.store.append_event(self.id, event_type, payload or {})
        except Exception as exc:
            logger.warning(f"[Runtime] 记录任务事件失败 task={self.id}: {exc}")

    def response_ready(self, summary: str = "", *, awaiting_delivery: bool) -> None:
        """Record a generated response while preserving real delivery semantics."""

        self._transition(
            TaskState.WORKING,
            result_summary=summary,
            event_payload={
                "phase": "response_ready",
                "awaiting_delivery": bool(awaiting_delivery),
            },
        )

    def complete(
        self,
        summary: str = "",
        *,
        receipt: dict[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] = {"phase": "delivered"}
        if receipt:
            payload["receipt"] = dict(receipt)
        self._transition(
            TaskState.COMPLETED,
            result_summary=summary or None,
            event_payload=payload,
        )

    def cancel(self, reason: str = "") -> None:
        self._transition(
            TaskState.CANCELLED,
            error=reason or "Interactive response cancelled",
            event_payload={"reason": reason or "cancelled"},
        )

    def fail(self, error: str) -> None:
        self._transition(
            TaskState.FAILED,
            error=error,
            event_payload={"phase": "handler_failed"},
        )

    def require_reconciliation(self, error: str) -> None:
        """Mark an ambiguous platform send without automatically replaying it."""

        self._transition(
            TaskState.RECONCILIATION_REQUIRED,
            error=error,
            event_payload={"phase": "delivery_unconfirmed"},
        )

    def _transition(
        self,
        state: TaskState,
        *,
        result_summary: str | None = None,
        error: str | None = None,
        event_payload: dict[str, Any] | None = None,
    ) -> None:
        try:
            current = self.refresh()
            if current.state.terminal:
                return
            self.record = self.store.transition_task(
                self.id,
                state,
                result_summary=result_summary,
                error=error,
                event_payload=event_payload,
            )
        except (InvalidTaskTransition, KeyError) as exc:
            logger.warning(f"[Runtime] 忽略无效任务状态变化 task={self.id}: {exc}")
        except Exception as exc:
            logger.warning(f"[Runtime] 更新任务状态失败 task={self.id}: {exc}")


class InteractiveTaskJournal:
    """Creates durable sidecar tasks without replacing current platform flows."""

    def __init__(self, store: RuntimeStore) -> None:
        self.store = store

    def begin(
        self,
        conversation: ConversationRef,
        request_text: str,
        *,
        owner_id: str = "",
        response_address: ChannelAddress | None = None,
        metadata: dict[str, Any] | None = None,
        idempotency_key: str = "",
    ) -> InteractiveTaskHandle:
        candidate_id = str(uuid.uuid4())
        record = self.store.create_task(
            conversation,
            request_text,
            owner_id=owner_id,
            response_address=response_address,
            metadata=metadata,
            idempotency_key=idempotency_key,
            task_id=candidate_id,
        )
        created = record.id == candidate_id
        handle = InteractiveTaskHandle(self.store, record, created)
        if created:
            handle._transition(
                TaskState.WORKING,
                event_payload={"phase": "platform_handler_started"},
            )
        else:
            handle.append_event(
                "duplicate_input_ignored",
                {"idempotency_key": idempotency_key},
            )
        return handle
