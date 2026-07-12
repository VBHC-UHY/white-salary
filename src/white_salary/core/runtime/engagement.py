"""Persistent per-user engagement leases for group conversations."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable


class EngagementState(str, Enum):
    PENDING = "pending"
    ENGAGED = "engaged"
    COOLING = "cooling"
    CLOSED = "closed"


@dataclass(frozen=True)
class EngagementConfig:
    base_ttl: float = 300.0
    pending_ttl: float = 60.0
    waiting_ttl: float = 600.0
    completion_grace: float = 300.0
    cooling_ttl: float = 60.0
    unrelated_limit: int = 2


@dataclass(frozen=True)
class EngagementLease:
    conversation_key: str
    user_id: str
    state: EngagementState
    opened_at: float
    last_relevant_at: float
    expires_at: float
    waiting_until: float
    unrelated_count: int
    last_reason: str
    updated_at: float
    active_task_count: int = 0

    def is_active(self, now: float) -> bool:
        if self.state == EngagementState.CLOSED:
            return False
        if self.active_task_count > 0:
            return True
        return max(self.expires_at, self.waiting_until) > now


class EngagementLeaseBook:
    """Owns activity state for `(conversation, user)` rather than a whole group."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        config: EngagementConfig | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._db_path = str(Path(db_path))
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._config = config or EngagementConfig()
        self._clock = clock
        self._initialize()

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
                CREATE TABLE IF NOT EXISTS runtime_engagement (
                    conversation_key TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    opened_at REAL NOT NULL,
                    last_relevant_at REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    waiting_until REAL NOT NULL DEFAULT 0,
                    unrelated_count INTEGER NOT NULL DEFAULT 0,
                    last_reason TEXT NOT NULL DEFAULT '',
                    updated_at REAL NOT NULL,
                    PRIMARY KEY(conversation_key, user_id)
                );

                CREATE TABLE IF NOT EXISTS runtime_engagement_tasks (
                    conversation_key TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY(conversation_key, user_id, task_id)
                );
                """
            )

    def trigger(self, conversation_key: str, user_id: str, reason: str) -> EngagementLease:
        """Open a short pending lease after a wake word, mention, or reply."""

        now = self._clock()
        return self._upsert(
            conversation_key,
            user_id,
            state=EngagementState.PENDING,
            opened_at=now,
            last_relevant_at=now,
            expires_at=now + self._config.pending_ttl,
            waiting_until=0,
            unrelated_count=0,
            last_reason=reason,
            updated_at=now,
        )

    def confirm_delivery(
        self,
        conversation_key: str,
        user_id: str,
        reason: str = "reply_delivered",
    ) -> EngagementLease:
        """Promote a trigger only after a real platform receipt or verified side effect."""

        now = self._clock()
        current = self.get(conversation_key, user_id)
        opened_at = current.opened_at if current is not None else now
        return self._upsert(
            conversation_key,
            user_id,
            state=EngagementState.ENGAGED,
            opened_at=opened_at,
            last_relevant_at=now,
            expires_at=now + self._config.base_ttl,
            waiting_until=current.waiting_until if current else 0,
            unrelated_count=0,
            last_reason=reason,
            updated_at=now,
        )

    def touch_relevant(
        self,
        conversation_key: str,
        user_id: str,
        reason: str = "relevant_followup",
    ) -> EngagementLease | None:
        now = self._clock()
        current = self.get(conversation_key, user_id)
        if current is None or not current.is_active(now):
            return None
        return self._upsert(
            conversation_key,
            user_id,
            state=EngagementState.ENGAGED,
            opened_at=current.opened_at,
            last_relevant_at=now,
            expires_at=now + self._config.base_ttl,
            waiting_until=current.waiting_until,
            unrelated_count=0,
            last_reason=reason,
            updated_at=now,
        )

    def mark_waiting_for_user(
        self,
        conversation_key: str,
        user_id: str,
        reason: str = "assistant_question",
    ) -> EngagementLease:
        now = self._clock()
        current = self.get(conversation_key, user_id)
        opened_at = current.opened_at if current else now
        expires_at = current.expires_at if current else now + self._config.base_ttl
        return self._upsert(
            conversation_key,
            user_id,
            state=EngagementState.ENGAGED,
            opened_at=opened_at,
            last_relevant_at=current.last_relevant_at if current else now,
            expires_at=expires_at,
            waiting_until=max(
                current.waiting_until if current else 0,
                now + self._config.waiting_ttl,
            ),
            unrelated_count=0,
            last_reason=reason,
            updated_at=now,
        )

    def pin_task(self, conversation_key: str, user_id: str, task_id: str) -> EngagementLease:
        now = self._clock()
        current = self.get(conversation_key, user_id)
        if current is None:
            current = self.trigger(conversation_key, user_id, "task_started")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO runtime_engagement_tasks
                    (conversation_key, user_id, task_id, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (conversation_key, user_id, task_id, now),
            )
        refreshed = self.get(conversation_key, user_id)
        if refreshed is None:
            raise RuntimeError("Pinned engagement lease disappeared")
        return refreshed

    def finish_task(
        self,
        conversation_key: str,
        user_id: str,
        task_id: str,
        reason: str = "task_finished",
    ) -> EngagementLease | None:
        now = self._clock()
        with self._connect() as conn:
            conn.execute(
                """
                DELETE FROM runtime_engagement_tasks
                WHERE conversation_key = ? AND user_id = ? AND task_id = ?
                """,
                (conversation_key, user_id, task_id),
            )
        current = self.get(conversation_key, user_id)
        if current is None:
            return None
        if current.active_task_count > 0:
            return current
        return self._upsert(
            conversation_key,
            user_id,
            state=EngagementState.ENGAGED,
            opened_at=current.opened_at,
            last_relevant_at=now,
            expires_at=max(current.expires_at, now + self._config.completion_grace),
            waiting_until=current.waiting_until,
            unrelated_count=0,
            last_reason=reason,
            updated_at=now,
        )

    def mark_unrelated(
        self,
        conversation_key: str,
        user_id: str,
        reason: str = "not_addressed_to_assistant",
    ) -> EngagementLease | None:
        now = self._clock()
        current = self.get(conversation_key, user_id)
        if current is None or not current.is_active(now):
            return current
        count = current.unrelated_count + 1
        state = (
            EngagementState.COOLING
            if count >= self._config.unrelated_limit
            else current.state
        )
        expires_at = current.expires_at
        if state == EngagementState.COOLING:
            expires_at = min(expires_at, now + self._config.cooling_ttl)
        return self._upsert(
            conversation_key,
            user_id,
            state=state,
            opened_at=current.opened_at,
            last_relevant_at=current.last_relevant_at,
            expires_at=expires_at,
            waiting_until=0 if state == EngagementState.COOLING else current.waiting_until,
            unrelated_count=count,
            last_reason=reason,
            updated_at=now,
        )

    def close(
        self,
        conversation_key: str,
        user_id: str,
        reason: str = "user_closed",
    ) -> EngagementLease:
        now = self._clock()
        current = self.get(conversation_key, user_id)
        with self._connect() as conn:
            conn.execute(
                """
                DELETE FROM runtime_engagement_tasks
                WHERE conversation_key = ? AND user_id = ?
                """,
                (conversation_key, user_id),
            )
        return self._upsert(
            conversation_key,
            user_id,
            state=EngagementState.CLOSED,
            opened_at=current.opened_at if current else now,
            last_relevant_at=current.last_relevant_at if current else now,
            expires_at=now,
            waiting_until=0,
            unrelated_count=current.unrelated_count if current else 0,
            last_reason=reason,
            updated_at=now,
        )

    def get(self, conversation_key: str, user_id: str) -> EngagementLease | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT e.*, COUNT(t.task_id) AS active_task_count
                FROM runtime_engagement e
                LEFT JOIN runtime_engagement_tasks t
                  ON t.conversation_key = e.conversation_key
                 AND t.user_id = e.user_id
                WHERE e.conversation_key = ? AND e.user_id = ?
                GROUP BY e.conversation_key, e.user_id
                """,
                (conversation_key, user_id),
            ).fetchone()
        return self._from_row(row) if row is not None else None

    def is_candidate(self, conversation_key: str, user_id: str) -> bool:
        lease = self.get(conversation_key, user_id)
        return bool(lease and lease.is_active(self._clock()))

    def _upsert(
        self,
        conversation_key: str,
        user_id: str,
        *,
        state: EngagementState,
        opened_at: float,
        last_relevant_at: float,
        expires_at: float,
        waiting_until: float,
        unrelated_count: int,
        last_reason: str,
        updated_at: float,
    ) -> EngagementLease:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO runtime_engagement (
                    conversation_key, user_id, state, opened_at,
                    last_relevant_at, expires_at, waiting_until,
                    unrelated_count, last_reason, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(conversation_key, user_id) DO UPDATE SET
                    state = excluded.state,
                    opened_at = excluded.opened_at,
                    last_relevant_at = excluded.last_relevant_at,
                    expires_at = excluded.expires_at,
                    waiting_until = excluded.waiting_until,
                    unrelated_count = excluded.unrelated_count,
                    last_reason = excluded.last_reason,
                    updated_at = excluded.updated_at
                """,
                (
                    conversation_key,
                    user_id,
                    state.value,
                    opened_at,
                    last_relevant_at,
                    expires_at,
                    waiting_until,
                    unrelated_count,
                    last_reason,
                    updated_at,
                ),
            )
        lease = self.get(conversation_key, user_id)
        if lease is None:
            raise RuntimeError("Engagement lease upsert did not produce a row")
        return lease

    @staticmethod
    def _from_row(row: sqlite3.Row) -> EngagementLease:
        return EngagementLease(
            conversation_key=str(row["conversation_key"]),
            user_id=str(row["user_id"]),
            state=EngagementState(str(row["state"])),
            opened_at=float(row["opened_at"]),
            last_relevant_at=float(row["last_relevant_at"]),
            expires_at=float(row["expires_at"]),
            waiting_until=float(row["waiting_until"]),
            unrelated_count=int(row["unrelated_count"]),
            last_reason=str(row["last_reason"]),
            updated_at=float(row["updated_at"]),
            active_task_count=int(row["active_task_count"]),
        )
