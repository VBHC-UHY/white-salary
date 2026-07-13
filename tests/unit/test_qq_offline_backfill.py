from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import pytest

from white_salary.adapters.platform.qq_adapter import QQAdapter
from white_salary.core.services import startup_checker as startup_module
from white_salary.core.services.startup_checker import StartupChecker


def _message(
    message_id: int,
    sender_id: str,
    text: str,
    when: int,
    *,
    group_id: str = "",
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "message_id": message_id,
        "user_id": sender_id,
        "sender": {"user_id": sender_id, "nickname": f"user-{sender_id}"},
        "message": text,
        "raw_message": text,
        "time": when,
    }
    if group_id:
        event["group_id"] = group_id
    return event


def _adapter_with_history(
    responses: dict[str, Any],
) -> tuple[QQAdapter, list[tuple[str, str, str]]]:
    adapter = QQAdapter()
    adapter._self_id = "999"
    seen: list[tuple[str, str, str]] = []

    async def call_api(action: str, params: dict, wait_response: bool = False) -> Any:
        value = responses.get(action)
        return value(params) if callable(value) else value

    async def on_message(msg: Any) -> None:
        seen.append((msg.message_type, msg.group_id or msg.user_id, msg.text))
        return None

    adapter._call_api = call_api  # type: ignore[method-assign]
    adapter.on_message = on_message
    return adapter, seen


def _wire_checker(adapter: QQAdapter, data_dir: Path) -> StartupChecker:
    checker = StartupChecker(adapter=adapter, agent=object(), data_dir=str(data_dir))
    adapter.on_message_claim = checker.claim_message
    adapter.on_message_completed = checker.complete_message
    return checker


def _seed_state(data_dir: Path, baseline: int) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "processed_msg_ids.json").write_text(
        json.dumps(
            {
                "version": 3,
                "baseline": baseline,
                "conversation_cursors": {},
                "processed": {},
            }
        ),
        encoding="utf-8",
    )


@pytest.fixture(autouse=True)
def _no_startup_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(startup_module, "STARTUP_DELAY", 0.0)


class TestQQOfflineBackfill:
    async def test_private_and_group_history_use_normal_adapter_pipeline(
        self, tmp_path: Path
    ) -> None:
        now = int(time.time())
        responses = {
            "get_recent_contact": [
                {"chatType": 1, "peerUin": "100", "lastestMsg": {}},
                {"chatType": 2, "peerUin": "200", "lastestMsg": {}},
            ],
            "get_friend_msg_history": {
                "messages": [
                    _message(2, "100", "private second", now - 10),
                    _message(1, "100", "private first", now - 20),
                    _message(9, "999", "bot output", now - 15),
                ]
            },
            "get_group_msg_history": {
                "messages": [_message(3, "101", "group message", now - 5, group_id="200")]
            },
        }
        adapter, seen = _adapter_with_history(responses)
        _seed_state(tmp_path, now - 100)
        checker = _wire_checker(adapter, tmp_path)

        count = await checker.check_and_reply()

        assert count == 3
        assert seen == [
            ("private", "100", "private first"),
            ("private", "100", "private second"),
            ("group", "200", "group message"),
        ]
        state = json.loads((tmp_path / "processed_msg_ids.json").read_text(encoding="utf-8"))
        assert state["version"] == 3
        assert {
            "qq:private:100:1",
            "qq:private:100:2",
            "qq:group:200:3",
        } <= set(state["processed"])
        assert {"private:100", "group:200"} <= set(state["conversation_cursors"])

    async def test_immediate_reconnect_is_scanned_and_deduplicated(
        self, tmp_path: Path
    ) -> None:
        now = int(time.time())
        history = {"messages": [_message(1, "100", "first", now - 2)]}
        responses = {
            "get_recent_contact": [{"chatType": 1, "peerUin": "100"}],
            "get_friend_msg_history": lambda params: history,
            "get_group_msg_history": {"messages": []},
        }
        adapter, seen = _adapter_with_history(responses)
        _seed_state(tmp_path, now - 100)
        checker = _wire_checker(adapter, tmp_path)

        assert await checker.check_and_reply() == 1
        assert await checker.check_and_reply() == 0

        history["messages"].append(_message(2, "100", "arrived after reconnect", now))
        assert await checker.check_and_reply() == 1
        assert [item[2] for item in seen] == ["first", "arrived after reconnect"]

    async def test_reconnect_during_active_scan_queues_one_follow_up_scan(
        self, tmp_path: Path
    ) -> None:
        now = int(time.time())
        adapter, _seen = _adapter_with_history({})
        entered = asyncio.Event()
        release = asyncio.Event()
        contact_calls = 0

        async def call_api(action: str, params: dict, wait_response: bool = False) -> Any:
            nonlocal contact_calls
            if action == "get_recent_contact":
                contact_calls += 1
                if contact_calls == 1:
                    entered.set()
                    await release.wait()
                return []
            return {"messages": []}

        adapter._call_api = call_api  # type: ignore[method-assign]
        _seed_state(tmp_path, now - 100)
        checker = _wire_checker(adapter, tmp_path)

        first_scan = asyncio.create_task(checker.check_and_reply())
        await entered.wait()
        assert await checker.check_and_reply() == 0
        release.set()

        assert await first_scan == 0
        assert contact_calls == 2

    async def test_failed_processing_is_retried(self, tmp_path: Path) -> None:
        adapter = QQAdapter()
        adapter._self_id = "999"
        checker = _wire_checker(adapter, tmp_path)
        attempts = 0

        async def failing_handler(msg: Any) -> None:
            nonlocal attempts
            attempts += 1
            raise RuntimeError("temporary failure")

        adapter.on_message = failing_handler
        event = {
            "post_type": "message",
            "message_type": "private",
            "message_id": 44,
            "user_id": "100",
            "self_id": "999",
            "sender": {"user_id": "100", "nickname": "friend"},
            "message": "retry me",
            "raw_message": "retry me",
            "time": int(time.time()),
        }

        await adapter.replay_history_message(event)
        await adapter.replay_history_message(event)

        assert attempts == 2
        assert "qq:private:100:44" not in checker._processed

    async def test_processed_state_survives_restart(self, tmp_path: Path) -> None:
        event = {
            "post_type": "message",
            "message_type": "private",
            "message_id": 55,
            "user_id": "100",
            "self_id": "999",
            "sender": {"user_id": "100", "nickname": "friend"},
            "message": "once",
            "raw_message": "once",
            "time": int(time.time()),
        }
        first_adapter, first_seen = _adapter_with_history({})
        _wire_checker(first_adapter, tmp_path)
        await first_adapter.replay_history_message(event)
        assert len(first_seen) == 1

        second_adapter, second_seen = _adapter_with_history({})
        _wire_checker(second_adapter, tmp_path)
        await second_adapter.replay_history_message(event)
        assert second_seen == []

    async def test_legacy_processed_file_is_respected(self, tmp_path: Path) -> None:
        (tmp_path / "processed_msg_ids.json").write_text(
            json.dumps({"processed_ids": {"77": int(time.time())}}), encoding="utf-8"
        )
        adapter, seen = _adapter_with_history({})
        _wire_checker(adapter, tmp_path)
        await adapter.replay_history_message(
            {
                "post_type": "message",
                "message_type": "private",
                "message_id": 77,
                "user_id": "100",
                "self_id": "999",
                "sender": {"user_id": "100"},
                "message": "old state",
                "raw_message": "old state",
                "time": int(time.time()),
            }
        )
        assert seen == []

    async def test_first_run_establishes_baseline_without_replaying_old_history(
        self, tmp_path: Path
    ) -> None:
        now = int(time.time())
        responses = {
            "get_recent_contact": [{"chatType": 1, "peerUin": "100"}],
            "get_friend_msg_history": {
                "messages": [_message(1, "100", "already old", now - 10)]
            },
        }
        adapter, seen = _adapter_with_history(responses)
        checker = _wire_checker(adapter, tmp_path)

        assert await checker.check_and_reply() == 0
        assert seen == []
        state = json.loads((tmp_path / "processed_msg_ids.json").read_text(encoding="utf-8"))
        assert state["version"] == 3
        assert state["baseline"] >= now

    async def test_failed_backfill_does_not_advance_conversation_cursor(
        self, tmp_path: Path
    ) -> None:
        now = int(time.time())
        history = {"messages": [_message(44, "100", "retry me", now - 300)]}
        responses = {
            "get_recent_contact": [{"chatType": 1, "peerUin": "100"}],
            "get_friend_msg_history": lambda params: history,
        }
        adapter, _seen = _adapter_with_history(responses)
        attempts = 0

        async def handler(msg: Any) -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("temporary failure")
            return None

        adapter.on_message = handler
        _seed_state(tmp_path, now - 600)
        checker = _wire_checker(adapter, tmp_path)

        assert await checker.check_and_reply() == 1
        assert "private:100" not in checker._conversation_cursors
        assert await checker.check_and_reply() == 1
        assert attempts == 2
        assert "private:100" in checker._conversation_cursors

    async def test_newly_visible_contact_uses_original_baseline(
        self, tmp_path: Path
    ) -> None:
        now = int(time.time())
        contacts = [{"chatType": 1, "peerUin": "100"}]
        histories = {
            "100": {"messages": [_message(1, "100", "first peer", now - 30)]},
            "200": {"messages": [_message(2, "200", "late visible peer", now - 20)]},
        }
        responses = {
            "get_recent_contact": lambda params: contacts,
            "get_friend_msg_history": lambda params: histories[str(params["user_id"])],
        }
        adapter, seen = _adapter_with_history(responses)
        _seed_state(tmp_path, now - 100)
        checker = _wire_checker(adapter, tmp_path)

        assert await checker.check_and_reply() == 1
        contacts.append({"chatType": 1, "peerUin": "200"})
        assert await checker.check_and_reply() == 1
        assert [item[2] for item in seen] == ["first peer", "late visible peer"]

    async def test_history_pages_until_baseline(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        now = int(time.time())
        monkeypatch.setattr(startup_module, "PRIVATE_MSG_COUNT", 2)
        pages = {
            "": {"messages": [
                _message(3, "100", "third", now - 5),
                _message(2, "100", "second", now - 10),
            ]},
            "2": {"messages": [
                _message(2, "100", "second", now - 10),
                _message(1, "100", "first", now - 200),
            ]},
        }
        responses = {
            "get_recent_contact": [{"chatType": 1, "peerUin": "100"}],
            "get_friend_msg_history": lambda params: pages[str(params.get("message_seq", ""))],
        }
        adapter, seen = _adapter_with_history(responses)
        _seed_state(tmp_path, now)
        checker = _wire_checker(adapter, tmp_path)

        assert await checker.check_and_reply() == 2
        assert [item[2] for item in seen] == ["second", "third"]

    async def test_historical_reply_to_bot_is_marked_direct(self, tmp_path: Path) -> None:
        now = int(time.time())
        bot_message = _message(90, "999", "earlier bot reply", now - 20, group_id="200")
        user_reply = _message(91, "100", "[reply] follow-up", now - 10, group_id="200")
        user_reply["message"] = [
            {"type": "reply", "data": {"id": "90"}},
            {"type": "text", "data": {"text": "follow-up"}},
        ]
        user_reply["raw_message"] = "[CQ:reply,id=90]follow-up"
        responses = {
            "get_recent_contact": [{"chatType": 2, "peerUin": "200"}],
            "get_group_msg_history": {"messages": [bot_message, user_reply]},
        }
        adapter, _seen = _adapter_with_history(responses)
        direct_flags: list[bool] = []

        async def handler(msg: Any) -> None:
            direct_flags.append(bool(getattr(msg, "_is_reply_to_me", False)))
            return None

        adapter.on_message = handler
        _seed_state(tmp_path, now - 100)
        checker = _wire_checker(adapter, tmp_path)

        assert await checker.check_and_reply() == 1
        assert direct_flags == [True]

    async def test_history_reply_sequence_to_bot_is_marked_direct(
        self, tmp_path: Path
    ) -> None:
        now = int(time.time())
        bot_message = _message(900, "999", "earlier bot reply", now - 20, group_id="200")
        bot_message["message_seq"] = 90
        user_reply = _message(901, "100", "[reply] follow-up", now - 10, group_id="200")
        user_reply["message"] = [
            {"type": "reply", "data": {"seq": "90"}},
            {"type": "text", "data": {"text": "follow-up"}},
        ]
        responses = {
            "get_recent_contact": [{"chatType": 2, "peerUin": "200"}],
            "get_group_msg_history": {"messages": [bot_message, user_reply]},
        }
        adapter, _seen = _adapter_with_history(responses)
        direct_flags: list[bool] = []

        async def handler(msg: Any) -> None:
            direct_flags.append(bool(getattr(msg, "_is_reply_to_me", False)))

        adapter.on_message = handler
        _seed_state(tmp_path, now - 100)
        checker = _wire_checker(adapter, tmp_path)

        assert await checker.check_and_reply() == 1
        assert direct_flags == [True]

    async def test_same_message_id_in_different_conversations_is_not_deduplicated(
        self, tmp_path: Path
    ) -> None:
        now = int(time.time())
        responses = {
            "get_recent_contact": [
                {"chatType": 1, "peerUin": "100"},
                {"chatType": 2, "peerUin": "200"},
            ],
            "get_friend_msg_history": {
                "messages": [_message(77, "100", "private", now - 10)]
            },
            "get_group_msg_history": {
                "messages": [_message(77, "101", "group", now - 5, group_id="200")]
            },
        }
        adapter, seen = _adapter_with_history(responses)
        _seed_state(tmp_path, now - 100)
        checker = _wire_checker(adapter, tmp_path)

        assert await checker.check_and_reply() == 2
        assert [item[2] for item in seen] == ["private", "group"]
