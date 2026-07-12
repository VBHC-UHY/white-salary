"""Durability and routing tests for the QQ/desktop bridge."""

from __future__ import annotations

from white_salary.core.cross_platform import CrossPlatformBridge
from white_salary.core.runtime.models import DeliveryState


def _bridge(tmp_path) -> CrossPlatformBridge:
    return CrossPlatformBridge.configure(tmp_path / "runtime.db")


def test_desktop_message_is_claimed_then_explicitly_acked(tmp_path) -> None:
    bridge = _bridge(tmp_path)
    delivery_id = bridge.push_to_desktop("hello", from_user="u1", source="qq")

    messages = bridge.claim_desktop_messages()
    assert len(messages) == 1
    assert messages[0]["message"] == "hello"
    assert bridge.claim_desktop_messages() == []

    bridge.ack_message(messages[0], receipt={"accepted": True})
    saved = bridge.store.get_delivery(delivery_id)
    assert saved is not None and saved.state == DeliveryState.DELIVERED


def test_platform_claims_do_not_steal_each_others_messages(tmp_path) -> None:
    bridge = _bridge(tmp_path)
    bridge.push_to_desktop("desktop")
    bridge.push_to_qq("qq", target_id="10001")

    desktop = bridge.claim_desktop_messages()
    qq = bridge.claim_qq_messages()

    assert [item["message"] for item in desktop] == ["desktop"]
    assert [item["message"] for item in qq] == ["qq"]


def test_unknown_non_replay_safe_delivery_is_not_duplicated(tmp_path) -> None:
    bridge = _bridge(tmp_path)
    delivery_id = bridge.push_to_qq("once", target_id="10001")
    claimed = bridge.claim_qq_messages()
    assert len(claimed) == 1
    saved = bridge.store.get_delivery(delivery_id)
    assert saved is not None

    retried = bridge.store.claim_due_deliveries(
        platform=bridge.QQ_PLATFORM,
        now=saved.lease_until + 1,
    )
    after = bridge.store.get_delivery(delivery_id)

    assert retried == []
    assert after is not None and after.state == DeliveryState.UNKNOWN


def test_known_failure_can_be_retried(tmp_path) -> None:
    bridge = _bridge(tmp_path)
    delivery_id = bridge.push_to_desktop("retry")
    message = bridge.claim_desktop_messages()[0]
    bridge.retry_message(message, "desktop temporarily unavailable")
    failed = bridge.store.get_delivery(delivery_id)
    assert failed is not None and failed.state == DeliveryState.PENDING

    claimed_again = bridge.store.claim_due_deliveries(
        platform=bridge.DESKTOP_PLATFORM,
        now=failed.available_at + 1,
    )
    assert [item.id for item in claimed_again] == [delivery_id]


def test_legacy_pop_preserves_shape_and_marks_delivered(tmp_path) -> None:
    bridge = _bridge(tmp_path)
    delivery_id = bridge.push_to_desktop("legacy", source="reminder")

    messages = bridge.pop_desktop_messages()

    assert messages[0]["message"] == "legacy"
    assert messages[0]["source"] == "reminder"
    saved = bridge.store.get_delivery(delivery_id)
    assert saved is not None and saved.state == DeliveryState.DELIVERED
