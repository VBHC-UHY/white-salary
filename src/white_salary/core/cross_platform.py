"""Durable QQ/desktop bridge backed by the Agent Runtime outbox."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger

from white_salary.core.runtime.models import ChannelAddress, DeliveryRecord
from white_salary.core.runtime.store import RuntimeStore


class CrossPlatformBridge:
    """Process-wide bridge with backwards-compatible push/pop methods."""

    DESKTOP_PLATFORM = "desktop_bridge"
    QQ_PLATFORM = "qq_bridge"
    DIRECT_DELIVERY = "direct"
    EVENT_PROMPT_DELIVERY = "event_prompt"
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            instance = super().__new__(cls)
            instance._store = RuntimeStore(
                Path.cwd() / "data" / "runtime" / "agent_runtime.db"
            )
            cls._instance = instance
        return cls._instance

    @classmethod
    def configure(cls, db_path: str | Path) -> "CrossPlatformBridge":
        bridge = cls()
        bridge._store = RuntimeStore(db_path)
        return bridge

    @property
    def store(self) -> RuntimeStore:
        return self._store

    def push_to_desktop(
        self,
        message: str,
        from_user: str = "",
        source: str = "qq",
        delivery_kind: str = "",
    ) -> str:
        source = str(source or "qq").strip() or "qq"
        delivery_kind = self._normalize_desktop_delivery_kind(
            delivery_kind,
            source=source,
        )
        delivery = self._store.enqueue_delivery(
            ChannelAddress(platform=self.DESKTOP_PLATFORM, address="primary"),
            {
                "message": str(message),
                "from_user": str(from_user),
                "source": source,
                "delivery_kind": delivery_kind,
            },
            conversation_key="bridge:desktop:primary",
            replay_safe=False,
        )
        logger.debug(f"[Bridge] queued -> desktop: {str(message)[:30]}")
        return delivery.id

    def claim_desktop_messages(self, limit: int = 50) -> list[dict[str, Any]]:
        records = self._store.claim_due_deliveries(
            platform=self.DESKTOP_PLATFORM,
            limit=limit,
            lease_seconds=30.0,
        )
        return [self._to_message(record) for record in records]

    def pop_desktop_messages(self) -> list[dict[str, Any]]:
        """Legacy destructive pop; new consumers should claim and then ack."""
        messages = self.claim_desktop_messages()
        for message in messages:
            self.ack_message(message, receipt={"mode": "legacy_pop"})
        return messages

    def push_to_qq(
        self,
        message: str,
        target_id: str = "",
        is_group: bool = False,
    ) -> str:
        target_id = str(target_id or "").strip()
        delivery = self._store.enqueue_delivery(
            ChannelAddress(
                platform=self.QQ_PLATFORM,
                address=target_id,
                is_group=bool(is_group),
            ),
            {
                "message": str(message),
                "target_id": target_id,
                "is_group": bool(is_group),
            },
            conversation_key=(
                f"bridge:qq:group:{target_id}"
                if is_group
                else f"bridge:qq:private:{target_id or 'default'}"
            ),
            replay_safe=False,
        )
        logger.debug(f"[Bridge] queued -> QQ: {str(message)[:30]}")
        return delivery.id

    def claim_qq_messages(self, limit: int = 50) -> list[dict[str, Any]]:
        records = self._store.claim_due_deliveries(
            platform=self.QQ_PLATFORM,
            limit=limit,
            lease_seconds=30.0,
        )
        return [self._to_message(record) for record in records]

    def pop_qq_messages(self) -> list[dict[str, Any]]:
        """Legacy destructive pop; new consumers should claim and then ack."""
        messages = self.claim_qq_messages()
        for message in messages:
            self.ack_message(message, receipt={"mode": "legacy_pop"})
        return messages

    def ack_message(
        self,
        message_or_id: dict[str, Any] | str,
        *,
        receipt: dict[str, Any] | None = None,
    ) -> None:
        delivery_id = self._delivery_id(message_or_id)
        if delivery_id:
            self._store.mark_delivery_delivered(
                delivery_id,
                receipt or {"accepted": True},
                claim_token=self._claim_token(message_or_id),
            )

    def retry_message(self, message_or_id: dict[str, Any] | str, error: str) -> None:
        delivery_id = self._delivery_id(message_or_id)
        if delivery_id:
            self._store.mark_delivery_failed(
                delivery_id,
                error,
                claim_token=self._claim_token(message_or_id),
            )

    def mark_message_unknown(self, message_or_id: dict[str, Any] | str, error: str) -> None:
        delivery_id = self._delivery_id(message_or_id)
        if delivery_id:
            self._store.mark_delivery_unknown(
                delivery_id,
                error,
                claim_token=self._claim_token(message_or_id),
            )

    def reject_message(self, message_or_id: dict[str, Any] | str, error: str) -> None:
        delivery_id = self._delivery_id(message_or_id)
        if delivery_id:
            self._store.mark_delivery_permanently_failed(
                delivery_id,
                error,
                claim_token=self._claim_token(message_or_id),
            )

    @property
    def has_desktop_messages(self) -> bool:
        return self._store.has_pending_deliveries(self.DESKTOP_PLATFORM)

    @property
    def has_qq_messages(self) -> bool:
        return self._store.has_pending_deliveries(self.QQ_PLATFORM)

    @classmethod
    def _to_message(cls, record: DeliveryRecord) -> dict[str, Any]:
        message = dict(record.payload)
        if record.target.platform == cls.DESKTOP_PLATFORM:
            message["delivery_kind"] = cls._normalize_desktop_delivery_kind(
                message.get("delivery_kind", ""),
                source=str(message.get("source", "qq")),
            )
        message["_delivery_id"] = record.id
        message["_delivery_attempt"] = record.attempts
        message["_delivery_claim_token"] = record.claim_token
        return message

    @classmethod
    def _normalize_desktop_delivery_kind(cls, value: object, *, source: str) -> str:
        """Keep old outbox rows compatible while separating transport from prompts."""
        normalized = str(value or "").strip().lower()
        if normalized in {cls.DIRECT_DELIVERY, cls.EVENT_PROMPT_DELIVERY}:
            return normalized
        # Existing game rows are raw events that need one persona response. Every
        # other legacy source already contains user-facing text and must not be
        # sent through the LLM again.
        if str(source or "").strip().lower() == "game":
            return cls.EVENT_PROMPT_DELIVERY
        return cls.DIRECT_DELIVERY

    @staticmethod
    def _delivery_id(message_or_id: dict[str, Any] | str) -> str:
        if isinstance(message_or_id, dict):
            return str(message_or_id.get("_delivery_id", ""))
        return str(message_or_id or "")

    @staticmethod
    def _claim_token(message_or_id: dict[str, Any] | str) -> str:
        if isinstance(message_or_id, dict):
            return str(message_or_id.get("_delivery_claim_token", ""))
        return ""
