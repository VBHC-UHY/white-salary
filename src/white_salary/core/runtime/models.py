"""Serializable contracts shared by every White Salary platform adapter."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TaskState(str, Enum):
    """Lifecycle of an asynchronous agent task."""

    SUBMITTED = "submitted"
    WORKING = "working"
    CANCEL_REQUESTED = "cancel_requested"
    INPUT_REQUIRED = "input_required"
    AUTH_REQUIRED = "auth_required"
    RECONCILIATION_REQUIRED = "reconciliation_required"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    REJECTED = "rejected"

    @property
    def terminal(self) -> bool:
        return self in {
            TaskState.COMPLETED,
            TaskState.FAILED,
            TaskState.CANCELLED,
            TaskState.REJECTED,
        }


class DeliveryState(str, Enum):
    """Lifecycle of a message waiting to be delivered to a platform."""

    PENDING = "pending"
    SENDING = "sending"
    DELIVERED = "delivered"
    FAILED = "failed"
    UNKNOWN = "unknown"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class ChannelAddress:
    """A concrete destination on a platform."""

    platform: str
    address: str
    is_group: bool = False
    client_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "address": self.address,
            "is_group": self.is_group,
            "client_id": self.client_id,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ChannelAddress":
        return cls(
            platform=str(value.get("platform", "")),
            address=str(value.get("address", "")),
            is_group=bool(value.get("is_group", False)),
            client_id=str(value.get("client_id", "")),
        )


@dataclass(frozen=True)
class ConversationRef:
    """Identity of an isolated conversation actor."""

    platform: str
    conversation_id: str
    scope: str = "private"
    user_id: str = ""
    group_id: str = ""

    @property
    def key(self) -> str:
        platform = self.platform.strip().lower() or "unknown"
        scope = self.scope.strip().lower() or "private"
        conversation_id = self.conversation_id.strip() or self.user_id.strip() or "default"
        return f"{platform}:{scope}:{conversation_id}"

    @property
    def private_state_key(self) -> str:
        user_id = self.user_id.strip() or "anonymous"
        return f"{self.key}:user:{user_id}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "conversation_id": self.conversation_id,
            "scope": self.scope,
            "user_id": self.user_id,
            "group_id": self.group_id,
        }

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ConversationRef":
        return cls(
            platform=str(value.get("platform", "")),
            conversation_id=str(value.get("conversation_id", "")),
            scope=str(value.get("scope", "private")),
            user_id=str(value.get("user_id", "")),
            group_id=str(value.get("group_id", "")),
        )


@dataclass(frozen=True)
class RuntimeEvent:
    """An append-only fact emitted while a task is running."""

    id: str
    task_id: str
    conversation_key: str
    event_type: str
    sequence: int
    created_at: float
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TaskRecord:
    """Durable task snapshot."""

    id: str
    conversation: ConversationRef
    owner_id: str
    state: TaskState
    request_text: str
    response_address: ChannelAddress | None
    created_at: float
    updated_at: float
    metadata: dict[str, Any] = field(default_factory=dict)
    result_summary: str = ""
    error: str = ""
    parent_task_id: str = ""
    idempotency_key: str = ""


@dataclass(frozen=True)
class DeliveryRecord:
    """Durable outbox entry."""

    id: str
    task_id: str
    conversation_key: str
    target: ChannelAddress
    payload: dict[str, Any]
    state: DeliveryState
    attempts: int
    max_attempts: int
    available_at: float
    lease_until: float
    claim_token: str
    created_at: float
    updated_at: float
    last_error: str = ""
    receipt: dict[str, Any] = field(default_factory=dict)
    idempotency_key: str = ""
    replay_safe: bool = False
