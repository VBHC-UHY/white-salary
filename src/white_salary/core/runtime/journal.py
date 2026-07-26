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


class _DetachedTaskHandle(InteractiveTaskHandle):
    """任务账本不可用时的降级句柄：所有生命周期操作都变成空操作。

    关键语义是 ``should_process`` 恒为 True。任务账本是纯观测性的 sidecar，
    它写不进去时，正确的行为是"照常回复用户，只是这一轮没有审计记录"，
    而不是"因为记不上账所以干脆不说话"。

    历史版本 ``begin()`` 不做任何保护，store 一旦不可写（DB 被杀软/备份
    占用、磁盘满、WAL 脏），异常会一路冒到平台处理器的外层 except：
    桌面端每条消息只收到红字"处理失败"，QQ 端则是纯粹的已读不回——
    一个只负责记日志的组件把核心陪伴功能整个绑架了。
    """

    def __init__(self) -> None:
        super().__init__(store=None, record=None, created=True)  # type: ignore[arg-type]

    @property
    def id(self) -> str:
        return ""

    @property
    def should_process(self) -> bool:
        return True

    def refresh(self) -> Any:  # type: ignore[override]
        return None

    def append_event(self, event_type: str, payload: dict[str, Any] | None = None) -> None:
        return None

    def _transition(self, state: TaskState, **kwargs: Any) -> None:  # type: ignore[override]
        return None


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
        try:
            record = self.store.create_task(
                conversation,
                request_text,
                owner_id=owner_id,
                response_address=response_address,
                metadata=metadata,
                idempotency_key=idempotency_key,
                task_id=candidate_id,
            )
        except Exception as exc:
            # 记账失败绝不能阻断用户消息：降级为空操作句柄，照常回复。
            # 同类保护在 append_event / _transition 里早就有了，唯独入口漏了。
            logger.warning(f"[Runtime] 创建任务账本失败，本轮降级为无账本运行: {exc}")
            return _DetachedTaskHandle()
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
