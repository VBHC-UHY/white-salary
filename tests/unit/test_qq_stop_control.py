"""Regression tests for QQ conversation stop and continuation parsing."""

from __future__ import annotations

from dataclasses import dataclass

from white_salary.core.runtime import EngagementLeaseBook
from white_salary.infrastructure.server.qq_handler import (
    _is_user_blocked,
    _parse_continuation_reply,
    _should_consume_stop_request,
)


@dataclass
class _Message:
    is_group: bool = True
    group_id: str = "g1"
    user_id: str = "u1"


def test_active_user_can_stop_without_repeating_wake_word(tmp_path) -> None:
    leases = EngagementLeaseBook(tmp_path / "runtime.db")
    leases.confirm_delivery("group:g1", "u1")

    assert _should_consume_stop_request(
        msg=_Message(),
        text="先别回复我了",
        is_direct=False,
        engagement_leases=leases,
    )


def test_unrelated_group_member_cannot_close_someone_elses_window(tmp_path) -> None:
    leases = EngagementLeaseBook(tmp_path / "runtime.db")
    leases.confirm_delivery("group:g1", "u1")

    assert not _should_consume_stop_request(
        msg=_Message(user_id="u2"),
        text="你先别回",
        is_direct=False,
        engagement_leases=leases,
    )
    assert leases.is_candidate("group:g1", "u1")


def test_direct_stop_is_consumed_even_without_existing_window(tmp_path) -> None:
    leases = EngagementLeaseBook(tmp_path / "runtime.db")

    assert _should_consume_stop_request(
        msg=_Message(),
        text="白，不要说话",
        is_direct=True,
        engagement_leases=leases,
    )


def test_negative_continuation_words_win_over_positive_substrings() -> None:
    assert not _parse_continuation_reply("不应该回复", fallback=True)
    assert not _parse_continuation_reply("不需要回复", fallback=True)
    assert not _parse_continuation_reply('{"reply": "false"}', fallback=True)


def test_positive_and_unknown_continuation_results() -> None:
    assert _parse_continuation_reply('{"reply": true}', fallback=False)
    assert _parse_continuation_reply("应该回复", fallback=False)
    assert _parse_continuation_reply("无法判断", fallback=True)


def test_persistent_filter_applies_to_system_event_path() -> None:
    class _Filter:
        def check(self, user_id: str) -> str:
            return "block" if user_id == "blocked" else "allow"

    assert _is_user_blocked(_Filter(), "blocked", is_family=False)
    assert not _is_user_blocked(_Filter(), "allowed", is_family=False)
    assert not _is_user_blocked(_Filter(), "blocked", is_family=True)
