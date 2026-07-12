"""Tests for the adaptive QQ activity lease."""

from __future__ import annotations

from pathlib import Path

from white_salary.core.runtime import (
    EngagementConfig,
    EngagementLeaseBook,
    EngagementState,
)


class Clock:
    def __init__(self, value: float = 1000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def _book(tmp_path: Path, clock: Clock) -> EngagementLeaseBook:
    return EngagementLeaseBook(
        tmp_path / "runtime.db",
        clock=clock,
        config=EngagementConfig(
            base_ttl=300,
            pending_ttl=30,
            waiting_ttl=600,
            completion_grace=300,
            cooling_ttl=60,
            unrelated_limit=2,
        ),
    )


def test_wake_trigger_is_pending_until_real_reply_delivery(tmp_path: Path) -> None:
    clock = Clock()
    book = _book(tmp_path, clock)
    pending = book.trigger("qq:group:g1", "u1", "wake_word")
    assert pending.state == EngagementState.PENDING
    assert pending.expires_at == 1030

    clock.advance(5)
    engaged = book.confirm_delivery("qq:group:g1", "u1")
    assert engaged.state == EngagementState.ENGAGED
    assert engaged.expires_at == 1305


def test_activity_is_isolated_by_group_and_user(tmp_path: Path) -> None:
    clock = Clock()
    book = _book(tmp_path, clock)
    book.trigger("qq:group:g1", "u1", "wake")
    book.confirm_delivery("qq:group:g1", "u1")

    assert book.is_candidate("qq:group:g1", "u1")
    assert not book.is_candidate("qq:group:g1", "u2")
    assert not book.is_candidate("qq:group:g2", "u1")


def test_relevant_followup_slides_the_window(tmp_path: Path) -> None:
    clock = Clock()
    book = _book(tmp_path, clock)
    book.confirm_delivery("qq:group:g1", "u1")
    clock.advance(250)
    refreshed = book.touch_relevant("qq:group:g1", "u1")

    assert refreshed is not None
    assert refreshed.expires_at == 1550
    clock.advance(299)
    assert book.is_candidate("qq:group:g1", "u1")


def test_assistant_question_extends_waiting_window(tmp_path: Path) -> None:
    clock = Clock()
    book = _book(tmp_path, clock)
    book.confirm_delivery("qq:group:g1", "u1")
    waiting = book.mark_waiting_for_user("qq:group:g1", "u1")
    assert waiting.waiting_until == 1600

    clock.advance(500)
    assert book.is_candidate("qq:group:g1", "u1")


def test_active_task_pins_lease_and_completion_adds_grace(tmp_path: Path) -> None:
    clock = Clock()
    book = _book(tmp_path, clock)
    book.confirm_delivery("qq:group:g1", "u1")
    pinned = book.pin_task("qq:group:g1", "u1", "task-1")
    assert pinned.active_task_count == 1

    clock.advance(1000)
    assert book.is_candidate("qq:group:g1", "u1")
    finished = book.finish_task("qq:group:g1", "u1", "task-1")
    assert finished is not None
    assert finished.active_task_count == 0
    assert finished.expires_at == 2300


def test_two_unrelated_turns_only_cool_the_same_user(tmp_path: Path) -> None:
    clock = Clock()
    book = _book(tmp_path, clock)
    book.confirm_delivery("qq:group:g1", "u1")
    book.confirm_delivery("qq:group:g1", "u2")

    first = book.mark_unrelated("qq:group:g1", "u1")
    second = book.mark_unrelated("qq:group:g1", "u1")
    assert first is not None and first.state == EngagementState.ENGAGED
    assert second is not None and second.state == EngagementState.COOLING
    assert book.is_candidate("qq:group:g1", "u2")

    clock.advance(61)
    assert not book.is_candidate("qq:group:g1", "u1")


def test_explicit_close_revokes_tasks_and_activity(tmp_path: Path) -> None:
    clock = Clock()
    book = _book(tmp_path, clock)
    book.confirm_delivery("qq:group:g1", "u1")
    book.pin_task("qq:group:g1", "u1", "task-1")
    closed = book.close("qq:group:g1", "u1", "stop_talking")

    assert closed.state == EngagementState.CLOSED
    assert closed.active_task_count == 0
    assert not book.is_candidate("qq:group:g1", "u1")


def test_lease_survives_process_restart(tmp_path: Path) -> None:
    clock = Clock()
    db_path = tmp_path / "runtime.db"
    first = EngagementLeaseBook(db_path, clock=clock)
    first.confirm_delivery("qq:group:g1", "u1")

    reopened = EngagementLeaseBook(db_path, clock=clock)
    restored = reopened.get("qq:group:g1", "u1")
    assert restored is not None
    assert restored.state == EngagementState.ENGAGED
    assert reopened.is_candidate("qq:group:g1", "u1")
