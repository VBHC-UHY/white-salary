"""SQLite task journal and reliable delivery outbox."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

from .models import (
    ChannelAddress,
    ConversationRef,
    DeliveryRecord,
    DeliveryState,
    RuntimeEvent,
    TaskRecord,
    TaskState,
)


class InvalidTaskTransition(ValueError):
    """Raised when a task is moved through an invalid lifecycle edge."""


class StaleDeliveryClaim(RuntimeError):
    """Raised when a delivery worker no longer owns the active lease."""


_ALLOWED_TRANSITIONS: dict[TaskState, set[TaskState]] = {
    TaskState.SUBMITTED: {
        TaskState.WORKING,
        TaskState.CANCEL_REQUESTED,
        TaskState.INPUT_REQUIRED,
        TaskState.AUTH_REQUIRED,
        TaskState.CANCELLED,
        TaskState.REJECTED,
        TaskState.FAILED,
    },
    TaskState.WORKING: {
        TaskState.CANCEL_REQUESTED,
        TaskState.INPUT_REQUIRED,
        TaskState.AUTH_REQUIRED,
        TaskState.COMPLETED,
        TaskState.FAILED,
        TaskState.CANCELLED,
        TaskState.REJECTED,
        TaskState.RECONCILIATION_REQUIRED,
    },
    TaskState.INPUT_REQUIRED: {
        TaskState.WORKING,
        TaskState.CANCEL_REQUESTED,
        TaskState.CANCELLED,
        TaskState.FAILED,
    },
    TaskState.AUTH_REQUIRED: {
        TaskState.WORKING,
        TaskState.CANCEL_REQUESTED,
        TaskState.CANCELLED,
        TaskState.FAILED,
    },
    TaskState.CANCEL_REQUESTED: {
        TaskState.WORKING,
        TaskState.COMPLETED,
        TaskState.FAILED,
        TaskState.CANCELLED,
        TaskState.RECONCILIATION_REQUIRED,
    },
    TaskState.RECONCILIATION_REQUIRED: {
        TaskState.COMPLETED,
        TaskState.FAILED,
        TaskState.CANCELLED,
    },
    TaskState.COMPLETED: set(),
    TaskState.FAILED: set(),
    TaskState.CANCELLED: set(),
    TaskState.REJECTED: set(),
}


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _json_load(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


class RuntimeStore:
    """Durable storage used by the platform-neutral runtime.

    Every operation opens a short-lived SQLite connection. This keeps the class
    safe to use from the backend event loop and from delivery worker threads.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(Path(db_path))
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @property
    def db_path(self) -> str:
        return self._db_path

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 10000")
        return conn

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS runtime_tasks (
                    id TEXT PRIMARY KEY,
                    conversation_key TEXT NOT NULL,
                    conversation_json TEXT NOT NULL,
                    owner_id TEXT NOT NULL DEFAULT '',
                    state TEXT NOT NULL,
                    request_text TEXT NOT NULL DEFAULT '',
                    response_address_json TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    result_summary TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '',
                    parent_task_id TEXT NOT NULL DEFAULT '',
                    idempotency_key TEXT UNIQUE
                );

                CREATE INDEX IF NOT EXISTS idx_runtime_tasks_conversation
                    ON runtime_tasks(conversation_key, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_runtime_tasks_state
                    ON runtime_tasks(state, updated_at DESC);

                CREATE TABLE IF NOT EXISTS runtime_events (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL,
                    conversation_key TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    UNIQUE(task_id, sequence),
                    FOREIGN KEY(task_id) REFERENCES runtime_tasks(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_runtime_events_task
                    ON runtime_events(task_id, sequence);

                CREATE TABLE IF NOT EXISTS runtime_outbox (
                    id TEXT PRIMARY KEY,
                    task_id TEXT NOT NULL DEFAULT '',
                    conversation_key TEXT NOT NULL,
                    target_platform TEXT NOT NULL DEFAULT '',
                    target_json TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    state TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 5,
                    available_at REAL NOT NULL,
                    lease_until REAL NOT NULL DEFAULT 0,
                    claim_token TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    last_error TEXT NOT NULL DEFAULT '',
                    receipt_json TEXT NOT NULL DEFAULT '{}',
                    idempotency_key TEXT UNIQUE,
                    replay_safe INTEGER NOT NULL DEFAULT 0
                );

                CREATE INDEX IF NOT EXISTS idx_runtime_outbox_due
                    ON runtime_outbox(state, available_at, lease_until);
                """
            )
            columns = {
                str(row[1]) for row in conn.execute("PRAGMA table_info(runtime_outbox)").fetchall()
            }
            if "replay_safe" not in columns:
                conn.execute(
                    "ALTER TABLE runtime_outbox "
                    "ADD COLUMN replay_safe INTEGER NOT NULL DEFAULT 0"
                )
            if "target_platform" not in columns:
                conn.execute(
                    "ALTER TABLE runtime_outbox "
                    "ADD COLUMN target_platform TEXT NOT NULL DEFAULT ''"
                )
                rows = conn.execute(
                    "SELECT id, target_json FROM runtime_outbox WHERE target_platform = ''"
                ).fetchall()
                for row in rows:
                    target = ChannelAddress.from_dict(_json_load(row["target_json"], {}))
                    conn.execute(
                        "UPDATE runtime_outbox SET target_platform = ? WHERE id = ?",
                        (target.platform.strip().lower(), str(row["id"])),
                    )
            if "claim_token" not in columns:
                conn.execute(
                    "ALTER TABLE runtime_outbox "
                    "ADD COLUMN claim_token TEXT NOT NULL DEFAULT ''"
                )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_runtime_outbox_platform_due "
                "ON runtime_outbox(target_platform, state, available_at, lease_until)"
            )

    def create_task(
        self,
        conversation: ConversationRef,
        request_text: str,
        *,
        owner_id: str = "",
        response_address: ChannelAddress | None = None,
        metadata: dict[str, Any] | None = None,
        parent_task_id: str = "",
        idempotency_key: str = "",
        task_id: str = "",
    ) -> TaskRecord:
        now = time.time()
        task_id = task_id or str(uuid.uuid4())
        key_value = idempotency_key or None
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if key_value:
                row = conn.execute(
                    "SELECT * FROM runtime_tasks WHERE idempotency_key = ?",
                    (key_value,),
                ).fetchone()
                if row is not None:
                    return self._task_from_row(row)
            conn.execute(
                """
                INSERT INTO runtime_tasks (
                    id, conversation_key, conversation_json, owner_id, state,
                    request_text, response_address_json, created_at, updated_at,
                    metadata_json, parent_task_id, idempotency_key
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    conversation.key,
                    _json_dump(conversation.to_dict()),
                    str(owner_id),
                    TaskState.SUBMITTED.value,
                    request_text,
                    _json_dump(response_address.to_dict()) if response_address else None,
                    now,
                    now,
                    _json_dump(metadata or {}),
                    parent_task_id,
                    key_value,
                ),
            )
            self._append_event_conn(
                conn,
                task_id=task_id,
                conversation_key=conversation.key,
                event_type="task_submitted",
                payload={"request_text": request_text},
                created_at=now,
            )
            row = conn.execute("SELECT * FROM runtime_tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise RuntimeError("Task insert did not produce a row")
        return self._task_from_row(row)

    def get_task(self, task_id: str) -> TaskRecord | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM runtime_tasks WHERE id = ?", (task_id,)).fetchone()
        return self._task_from_row(row) if row is not None else None

    def transition_task(
        self,
        task_id: str,
        state: TaskState,
        *,
        result_summary: str | None = None,
        error: str | None = None,
        event_payload: dict[str, Any] | None = None,
    ) -> TaskRecord:
        now = time.time()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM runtime_tasks WHERE id = ?", (task_id,)).fetchone()
            if row is None:
                raise KeyError(f"Unknown task: {task_id}")
            current = TaskState(str(row["state"]))
            if state != current and state not in _ALLOWED_TRANSITIONS[current]:
                raise InvalidTaskTransition(f"Cannot move task {task_id} from {current} to {state}")
            next_result = str(row["result_summary"]) if result_summary is None else result_summary
            next_error = str(row["error"]) if error is None else error
            conn.execute(
                """
                UPDATE runtime_tasks
                SET state = ?, updated_at = ?, result_summary = ?, error = ?
                WHERE id = ?
                """,
                (state.value, now, next_result, next_error, task_id),
            )
            if state != current or event_payload:
                payload = {"from": current.value, "to": state.value}
                payload.update(event_payload or {})
                self._append_event_conn(
                    conn,
                    task_id=task_id,
                    conversation_key=str(row["conversation_key"]),
                    event_type="task_state_changed",
                    payload=payload,
                    created_at=now,
                )
            updated = conn.execute("SELECT * FROM runtime_tasks WHERE id = ?", (task_id,)).fetchone()
        if updated is None:
            raise RuntimeError("Task transition lost its row")
        return self._task_from_row(updated)

    def transition_task_and_enqueue_delivery(
        self,
        task_id: str,
        state: TaskState,
        *,
        target: ChannelAddress | None,
        payload: dict[str, Any] | None,
        result_summary: str | None = None,
        error: str | None = None,
        event_payload: dict[str, Any] | None = None,
        max_attempts: int = 5,
        idempotency_key: str = "",
        replay_safe: bool = False,
    ) -> tuple[TaskRecord, DeliveryRecord | None]:
        """Commit a task terminal state and its response outbox atomically."""

        now = time.time()
        delivery: DeliveryRecord | None = None
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM runtime_tasks WHERE id = ?", (task_id,)).fetchone()
            if row is None:
                raise KeyError(f"Unknown task: {task_id}")
            current = TaskState(str(row["state"]))
            if state != current and state not in _ALLOWED_TRANSITIONS[current]:
                raise InvalidTaskTransition(f"Cannot move task {task_id} from {current} to {state}")
            next_result = str(row["result_summary"]) if result_summary is None else result_summary
            next_error = str(row["error"]) if error is None else error
            conn.execute(
                """
                UPDATE runtime_tasks
                SET state = ?, updated_at = ?, result_summary = ?, error = ?
                WHERE id = ?
                """,
                (state.value, now, next_result, next_error, task_id),
            )
            if state != current or event_payload:
                state_payload = {"from": current.value, "to": state.value}
                state_payload.update(event_payload or {})
                self._append_event_conn(
                    conn,
                    task_id=task_id,
                    conversation_key=str(row["conversation_key"]),
                    event_type="task_state_changed",
                    payload=state_payload,
                    created_at=now,
                )

            if payload is not None:
                if target is None:
                    raise ValueError("Response payload requires a delivery target")
                key_value = idempotency_key or None
                delivery_row = None
                if key_value:
                    delivery_row = conn.execute(
                        "SELECT * FROM runtime_outbox WHERE idempotency_key = ?",
                        (key_value,),
                    ).fetchone()
                if delivery_row is None:
                    delivery_id = str(uuid.uuid4())
                    conn.execute(
                        """
                        INSERT INTO runtime_outbox (
                            id, task_id, conversation_key, target_platform,
                            target_json, payload_json, state, attempts,
                            max_attempts, available_at, lease_until, claim_token,
                            created_at, updated_at, idempotency_key, replay_safe
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, 0, '', ?, ?, ?, ?)
                        """,
                        (
                            delivery_id,
                            task_id,
                            str(row["conversation_key"]),
                            target.platform.strip().lower(),
                            _json_dump(target.to_dict()),
                            _json_dump(payload),
                            DeliveryState.PENDING.value,
                            max(1, int(max_attempts)),
                            now,
                            now,
                            now,
                            key_value,
                            1 if replay_safe else 0,
                        ),
                    )
                    delivery_row = conn.execute(
                        "SELECT * FROM runtime_outbox WHERE id = ?",
                        (delivery_id,),
                    ).fetchone()
                if delivery_row is not None:
                    delivery = self._delivery_from_row(delivery_row)

            updated = conn.execute(
                "SELECT * FROM runtime_tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
        if updated is None:
            raise RuntimeError("Atomic task transition lost its row")
        return self._task_from_row(updated), delivery

    def append_event(
        self,
        task_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> RuntimeEvent:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT conversation_key FROM runtime_tasks WHERE id = ?",
                (task_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown task: {task_id}")
            return self._append_event_conn(
                conn,
                task_id=task_id,
                conversation_key=str(row["conversation_key"]),
                event_type=event_type,
                payload=payload or {},
                created_at=time.time(),
            )

    def list_events(self, task_id: str) -> list[RuntimeEvent]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM runtime_events WHERE task_id = ? ORDER BY sequence",
                (task_id,),
            ).fetchall()
        return [self._event_from_row(row) for row in rows]

    def list_active_tasks(self, conversation_key: str = "") -> list[TaskRecord]:
        terminal = tuple(state.value for state in TaskState if state.terminal)
        placeholders = ",".join("?" for _ in terminal)
        params: list[Any] = list(terminal)
        where = f"state NOT IN ({placeholders})"
        if conversation_key:
            where += " AND conversation_key = ?"
            params.append(conversation_key)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM runtime_tasks WHERE {where} ORDER BY updated_at DESC",
                params,
            ).fetchall()
        return [self._task_from_row(row) for row in rows]

    def enqueue_delivery(
        self,
        target: ChannelAddress,
        payload: dict[str, Any],
        *,
        conversation_key: str,
        task_id: str = "",
        max_attempts: int = 5,
        idempotency_key: str = "",
        available_at: float | None = None,
        delivery_id: str = "",
        replay_safe: bool = False,
    ) -> DeliveryRecord:
        now = time.time()
        delivery_id = delivery_id or str(uuid.uuid4())
        key_value = idempotency_key or None
        max_attempts = max(1, int(max_attempts))
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            if key_value:
                row = conn.execute(
                    "SELECT * FROM runtime_outbox WHERE idempotency_key = ?",
                    (key_value,),
                ).fetchone()
                if row is not None:
                    return self._delivery_from_row(row)
            conn.execute(
                """
                INSERT INTO runtime_outbox (
                    id, task_id, conversation_key, target_platform, target_json, payload_json,
                    state, attempts, max_attempts, available_at, lease_until,
                    claim_token, created_at, updated_at, idempotency_key, replay_safe
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?, 0, '', ?, ?, ?, ?)
                """,
                (
                    delivery_id,
                    task_id,
                    conversation_key,
                    target.platform.strip().lower(),
                    _json_dump(target.to_dict()),
                    _json_dump(payload),
                    DeliveryState.PENDING.value,
                    max_attempts,
                    now if available_at is None else float(available_at),
                    now,
                    now,
                    key_value,
                    1 if replay_safe else 0,
                ),
            )
            row = conn.execute(
                "SELECT * FROM runtime_outbox WHERE id = ?",
                (delivery_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("Delivery insert did not produce a row")
        return self._delivery_from_row(row)

    def claim_due_deliveries(
        self,
        *,
        limit: int = 20,
        lease_seconds: float = 30.0,
        now: float | None = None,
        platform: str = "",
    ) -> list[DeliveryRecord]:
        now = time.time() if now is None else float(now)
        lease_until = now + max(1.0, float(lease_seconds))
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                UPDATE runtime_outbox
                SET state = CASE
                        WHEN replay_safe = 0 THEN ?
                        WHEN attempts >= max_attempts THEN ?
                        ELSE ?
                    END,
                    lease_until = 0, claim_token = '', updated_at = ?,
                    last_error = CASE
                        WHEN replay_safe = 0
                            THEN 'Delivery lease expired before receipt was recorded'
                        WHEN attempts >= max_attempts
                            THEN 'Delivery exhausted max attempts after lease expiry'
                        ELSE last_error
                    END
                WHERE state = ? AND lease_until > 0 AND lease_until <= ?
                """,
                (
                    DeliveryState.UNKNOWN.value,
                    DeliveryState.FAILED.value,
                    DeliveryState.PENDING.value,
                    now,
                    DeliveryState.SENDING.value,
                    now,
                ),
            )
            conn.execute(
                """
                UPDATE runtime_outbox
                SET state = ?, lease_until = 0, claim_token = '', updated_at = ?,
                    last_error = CASE WHEN last_error = ''
                        THEN 'Delivery exhausted max attempts' ELSE last_error END
                WHERE state = ? AND attempts >= max_attempts
                """,
                (
                    DeliveryState.FAILED.value,
                    now,
                    DeliveryState.PENDING.value,
                ),
            )
            normalized_platform = platform.strip().lower()
            if normalized_platform:
                rows = conn.execute(
                    """
                    SELECT id FROM runtime_outbox
                    WHERE state = ? AND available_at <= ? AND attempts < max_attempts
                        AND target_platform = ?
                    ORDER BY created_at
                    LIMIT ?
                    """,
                    (
                        DeliveryState.PENDING.value,
                        now,
                        normalized_platform,
                        max(1, int(limit)),
                    ),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT id FROM runtime_outbox
                    WHERE state = ? AND available_at <= ? AND attempts < max_attempts
                    ORDER BY created_at
                    LIMIT ?
                    """,
                    (DeliveryState.PENDING.value, now, max(1, int(limit))),
                ).fetchall()
            ids: list[str] = []
            for row in rows:
                delivery_id = str(row["id"])
                claim_token = uuid.uuid4().hex
                updated = conn.execute(
                    """
                    UPDATE runtime_outbox
                    SET state = ?, attempts = attempts + 1,
                        lease_until = ?, claim_token = ?, updated_at = ?
                    WHERE id = ? AND state = ? AND attempts < max_attempts
                    """,
                    (
                        DeliveryState.SENDING.value,
                        lease_until,
                        claim_token,
                        now,
                        delivery_id,
                        DeliveryState.PENDING.value,
                    ),
                )
                if updated.rowcount == 1:
                    ids.append(delivery_id)
            if not ids:
                return []
            placeholders = ",".join("?" for _ in ids)
            claimed = conn.execute(
                f"SELECT * FROM runtime_outbox WHERE id IN ({placeholders}) ORDER BY created_at",
                ids,
            ).fetchall()
        return [self._delivery_from_row(row) for row in claimed]

    def has_pending_deliveries(self, platform: str) -> bool:
        normalized_platform = platform.strip().lower()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT 1 FROM runtime_outbox
                WHERE target_platform = ? AND state IN (?, ?)
                LIMIT 1
                """,
                (
                    normalized_platform,
                    DeliveryState.PENDING.value,
                    DeliveryState.SENDING.value,
                ),
            ).fetchone()
        return row is not None

    def mark_delivery_delivered(
        self,
        delivery_id: str,
        receipt: dict[str, Any] | None = None,
        *,
        claim_token: str,
    ) -> DeliveryRecord:
        return self._update_delivery(
            delivery_id,
            state=DeliveryState.DELIVERED,
            receipt=receipt or {},
            last_error="",
            lease_until=0,
            claim_token=claim_token,
            require_claim=True,
        )

    def mark_delivery_failed(
        self,
        delivery_id: str,
        error: str,
        *,
        claim_token: str,
        retry_delay: float | None = None,
    ) -> DeliveryRecord:
        now = time.time()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM runtime_outbox WHERE id = ?",
                (delivery_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown delivery: {delivery_id}")
            if (
                DeliveryState(str(row["state"])) != DeliveryState.SENDING
                or not claim_token
                or str(row["claim_token"]) != claim_token
            ):
                raise StaleDeliveryClaim(f"Delivery claim is stale: {delivery_id}")
            attempts = int(row["attempts"])
            max_attempts = int(row["max_attempts"])
            if attempts >= max_attempts:
                next_state = DeliveryState.FAILED
                available_at = float(row["available_at"])
            else:
                next_state = DeliveryState.PENDING
                delay = min(300.0, 2.0 ** max(0, attempts)) if retry_delay is None else retry_delay
                available_at = now + max(0.0, float(delay))
            conn.execute(
                """
                UPDATE runtime_outbox
                SET state = ?, available_at = ?, lease_until = 0,
                    claim_token = '', updated_at = ?, last_error = ?
                WHERE id = ? AND state = ? AND claim_token = ?
                """,
                (
                    next_state.value,
                    available_at,
                    now,
                    error,
                    delivery_id,
                    DeliveryState.SENDING.value,
                    claim_token,
                ),
            )
            updated = conn.execute(
                "SELECT * FROM runtime_outbox WHERE id = ?",
                (delivery_id,),
            ).fetchone()
        if updated is None:
            raise RuntimeError("Delivery update lost its row")
        return self._delivery_from_row(updated)

    def cancel_delivery(self, delivery_id: str) -> DeliveryRecord:
        return self._update_delivery(
            delivery_id,
            state=DeliveryState.CANCELLED,
            lease_until=0,
        )

    def mark_delivery_unknown(
        self,
        delivery_id: str,
        error: str,
        *,
        claim_token: str,
    ) -> DeliveryRecord:
        """Record an ambiguous outcome without automatically replaying it."""

        return self._update_delivery(
            delivery_id,
            state=DeliveryState.UNKNOWN,
            last_error=error,
            lease_until=0,
            claim_token=claim_token,
            require_claim=True,
        )

    def mark_delivery_permanently_failed(
        self,
        delivery_id: str,
        error: str,
        *,
        claim_token: str,
    ) -> DeliveryRecord:
        """Move a known non-retryable failure directly to its terminal state."""

        return self._update_delivery(
            delivery_id,
            state=DeliveryState.FAILED,
            last_error=error,
            lease_until=0,
            claim_token=claim_token,
            require_claim=True,
        )

    def requeue_unknown_delivery(
        self,
        delivery_id: str,
        *,
        available_at: float | None = None,
    ) -> DeliveryRecord:
        """Explicitly replay an ambiguous delivery after reconciliation."""

        current = self.get_delivery(delivery_id)
        if current is None:
            raise KeyError(f"Unknown delivery: {delivery_id}")
        if current.state != DeliveryState.UNKNOWN:
            raise ValueError(f"Delivery {delivery_id} is not unknown")
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE runtime_outbox
                SET state = ?, available_at = ?, lease_until = 0,
                    claim_token = '', updated_at = ?, last_error = ''
                WHERE id = ?
                """,
                (
                    DeliveryState.PENDING.value,
                    now if available_at is None else float(available_at),
                    now,
                    delivery_id,
                ),
            )
        restored = self.get_delivery(delivery_id)
        if restored is None:
            raise RuntimeError("Requeued delivery disappeared")
        return restored

    def get_delivery(self, delivery_id: str) -> DeliveryRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM runtime_outbox WHERE id = ?",
                (delivery_id,),
            ).fetchone()
        return self._delivery_from_row(row) if row is not None else None

    def list_deliveries(
        self,
        *,
        states: Iterable[DeliveryState] | None = None,
        conversation_key: str = "",
    ) -> list[DeliveryRecord]:
        conditions: list[str] = []
        params: list[Any] = []
        if states:
            values = [state.value for state in states]
            conditions.append(f"state IN ({','.join('?' for _ in values)})")
            params.extend(values)
        if conversation_key:
            conditions.append("conversation_key = ?")
            params.append(conversation_key)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM runtime_outbox{where} ORDER BY created_at",
                params,
            ).fetchall()
        return [self._delivery_from_row(row) for row in rows]

    def _update_delivery(
        self,
        delivery_id: str,
        *,
        state: DeliveryState,
        receipt: dict[str, Any] | None = None,
        last_error: str | None = None,
        lease_until: float | None = None,
        claim_token: str = "",
        require_claim: bool = False,
    ) -> DeliveryRecord:
        now = time.time()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM runtime_outbox WHERE id = ?",
                (delivery_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"Unknown delivery: {delivery_id}")
            if require_claim and (
                DeliveryState(str(row["state"])) != DeliveryState.SENDING
                or not claim_token
                or str(row["claim_token"]) != claim_token
            ):
                raise StaleDeliveryClaim(f"Delivery claim is stale: {delivery_id}")
            where = "id = ?"
            params: list[Any] = [delivery_id]
            if require_claim:
                where += " AND state = ? AND claim_token = ?"
                params.extend([DeliveryState.SENDING.value, claim_token])
            cursor = conn.execute(
                """
                UPDATE runtime_outbox
                SET state = ?, updated_at = ?,
                    receipt_json = ?, last_error = ?, lease_until = ?, claim_token = ''
                WHERE """ + where,
                tuple([
                    state.value,
                    now,
                    _json_dump(receipt if receipt is not None else _json_load(row["receipt_json"], {})),
                    str(row["last_error"]) if last_error is None else last_error,
                    float(row["lease_until"]) if lease_until is None else lease_until,
                ] + params),
            )
            if require_claim and cursor.rowcount != 1:
                raise StaleDeliveryClaim(f"Delivery claim is stale: {delivery_id}")
            updated = conn.execute(
                "SELECT * FROM runtime_outbox WHERE id = ?",
                (delivery_id,),
            ).fetchone()
        if updated is None:
            raise RuntimeError("Delivery update lost its row")
        return self._delivery_from_row(updated)

    def _append_event_conn(
        self,
        conn: sqlite3.Connection,
        *,
        task_id: str,
        conversation_key: str,
        event_type: str,
        payload: dict[str, Any],
        created_at: float,
    ) -> RuntimeEvent:
        row = conn.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 AS next_sequence "
            "FROM runtime_events WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        sequence = int(row["next_sequence"]) if row is not None else 1
        event = RuntimeEvent(
            id=str(uuid.uuid4()),
            task_id=task_id,
            conversation_key=conversation_key,
            event_type=event_type,
            sequence=sequence,
            created_at=created_at,
            payload=payload,
        )
        conn.execute(
            """
            INSERT INTO runtime_events (
                id, task_id, conversation_key, event_type,
                sequence, created_at, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.id,
                event.task_id,
                event.conversation_key,
                event.event_type,
                event.sequence,
                event.created_at,
                _json_dump(event.payload),
            ),
        )
        return event

    @staticmethod
    def _task_from_row(row: sqlite3.Row) -> TaskRecord:
        response_value = _json_load(row["response_address_json"], None)
        return TaskRecord(
            id=str(row["id"]),
            conversation=ConversationRef.from_dict(_json_load(row["conversation_json"], {})),
            owner_id=str(row["owner_id"]),
            state=TaskState(str(row["state"])),
            request_text=str(row["request_text"]),
            response_address=(
                ChannelAddress.from_dict(response_value)
                if isinstance(response_value, dict)
                else None
            ),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            metadata=_json_load(row["metadata_json"], {}),
            result_summary=str(row["result_summary"]),
            error=str(row["error"]),
            parent_task_id=str(row["parent_task_id"]),
            idempotency_key=str(row["idempotency_key"] or ""),
        )

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> RuntimeEvent:
        return RuntimeEvent(
            id=str(row["id"]),
            task_id=str(row["task_id"]),
            conversation_key=str(row["conversation_key"]),
            event_type=str(row["event_type"]),
            sequence=int(row["sequence"]),
            created_at=float(row["created_at"]),
            payload=_json_load(row["payload_json"], {}),
        )

    @staticmethod
    def _delivery_from_row(row: sqlite3.Row) -> DeliveryRecord:
        return DeliveryRecord(
            id=str(row["id"]),
            task_id=str(row["task_id"]),
            conversation_key=str(row["conversation_key"]),
            target=ChannelAddress.from_dict(_json_load(row["target_json"], {})),
            payload=_json_load(row["payload_json"], {}),
            state=DeliveryState(str(row["state"])),
            attempts=int(row["attempts"]),
            max_attempts=int(row["max_attempts"]),
            available_at=float(row["available_at"]),
            lease_until=float(row["lease_until"]),
            claim_token=str(row["claim_token"]),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            last_error=str(row["last_error"]),
            receipt=_json_load(row["receipt_json"], {}),
            idempotency_key=str(row["idempotency_key"] or ""),
            replay_safe=bool(row["replay_safe"]),
        )
