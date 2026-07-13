"""Recover QQ messages that arrived while the OneBot bridge was offline.

History is fetched from the connected OneBot implementation, normalized into
ordinary QQ message events, and replayed through ``QQAdapter``. This keeps the
same context, filters, affinity, plugins, tool routing, message buffering, and
delivery journal used by live QQ messages.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Optional

from loguru import logger


RECENT_CONTACT_COUNT = 100
PRIVATE_MSG_COUNT = 50
GROUP_MSG_COUNT = 100
PRIVATE_MAX_AGE = 86400
GROUP_MAX_AGE = 21600
STARTUP_DELAY = 2.0
READY_TIMEOUT = 10.0
WATERMARK_OVERLAP = 120
PROCESSED_EXPIRE_DAYS = 7
MAX_HISTORY_PAGES = 5
REPLAY_BATCH_SIZE = 20
MAX_REPLY_LOOKUPS = 20


class StartupChecker:
    """Persistent, idempotent QQ history backfill coordinator."""

    def __init__(
        self,
        adapter: Any,
        agent: Any,
        bot_name: str = "白",
        family_qq: Optional[list[str]] = None,
        wake_words: Optional[list[str]] = None,
        data_dir: str = "data/qq",
    ) -> None:
        self._adapter = adapter
        self._agent = agent
        self._bot_name = bot_name
        self._family_qq = {str(value) for value in (family_qq or [])}
        self._wake_words = list(wake_words or [])
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)

        # Keep the historical filename so existing installations retain their
        # dedupe state. Version 1 was a flat {message_id: timestamp} mapping.
        self._state_path = self._data_dir / "processed_msg_ids.json"
        self._processed: dict[str, int] = {}
        self._baseline = 0
        self._conversation_cursors: dict[str, int] = {}
        self._state_initialized = False
        self._inflight: set[str] = set()
        self._state_lock = asyncio.Lock()
        self._scan_lock = asyncio.Lock()
        self._scan_requested = False
        self._load_state()

    async def _isolated_reply(self, user_input: str, **chat_kwargs: Any) -> str:
        """Compatibility helper retained for older callers and regression tests.

        The backfill path no longer calls this method; recovered messages use the
        normal QQ pipeline. Keeping it avoids breaking integrations that imported
        the old helper directly.
        """
        from white_salary.core.memory.short_term import ShortTermMemory

        temp_agent = copy.copy(self._agent)
        temp_agent._memory = ShortTermMemory(max_turns=20)
        return await temp_agent.chat(user_input, **chat_kwargs)

    async def claim_message(self, msg: Any) -> bool:
        """Claim one live or replayed message before QQ processing starts."""
        key = self._message_key_from_object(msg)
        if not key:
            return True
        async with self._state_lock:
            self._cleanup_expired()
            if self._is_processed_key(key) or key in self._inflight:
                return False
            self._inflight.add(key)
            return True

    async def complete_message(self, msg: Any, success: bool) -> None:
        """Finish a claim, persisting successful processing atomically."""
        key = self._message_key_from_object(msg)
        if not key:
            return
        async with self._state_lock:
            self._inflight.discard(key)
            if success:
                self._processed[key] = int(time.time())
                self._save_state()

    async def check_and_reply(self) -> int:
        """Fetch missed history and replay it through the ordinary QQ pipeline."""
        self._scan_requested = True
        if self._scan_lock.locked():
            logger.debug("[QQ backfill] Reconnect scan queued behind the active scan")
            return 0

        async with self._scan_lock:
            await asyncio.sleep(STARTUP_DELAY)
            if not await self._wait_until_ready():
                logger.warning("[QQ backfill] Bot self_id was not ready; scan postponed")
                return 0

            replayed = 0
            while self._scan_requested:
                self._scan_requested = False
                replayed += await self._run_scan_once()
            return replayed

    async def _run_scan_once(self) -> int:
        """Run one reconnect scan while the outer single-worker lock is held."""
        scan_started = int(time.time())
        if not self._state_initialized:
            async with self._state_lock:
                self._baseline = scan_started
                self._state_initialized = True
                self._save_state()
            logger.info(
                "[QQ backfill] Established the first safe baseline; old history was not replayed"
            )
            return 0

        logger.info("[QQ backfill] Checking messages received while offline")
        try:
            contacts_result = await self._adapter._call_api(
                "get_recent_contact",
                {"count": RECENT_CONTACT_COUNT},
                wait_response=True,
            )
        except Exception as exc:
            logger.warning(f"[QQ backfill] Recent-contact query failed: {exc}")
            return 0

        if contacts_result is None:
            logger.warning("[QQ backfill] Recent-contact query returned no response")
            return 0

        private_ids, group_ids = self._parse_contacts(contacts_result)
        replayed = 0
        failures = 0

        for user_id in private_ids:
            count, success = await self._scan_conversation(
                action="get_friend_msg_history",
                message_type="private",
                peer_id=user_id,
                page_size=PRIVATE_MSG_COUNT,
                max_age=PRIVATE_MAX_AGE,
                scan_started=scan_started,
            )
            replayed += count
            failures += int(not success)

        for group_id in group_ids:
            count, success = await self._scan_conversation(
                action="get_group_msg_history",
                message_type="group",
                peer_id=group_id,
                page_size=GROUP_MSG_COUNT,
                max_age=GROUP_MAX_AGE,
                scan_started=scan_started,
            )
            replayed += count
            failures += int(not success)

        logger.info(
            f"[QQ backfill] Replayed {replayed} message(s); failed conversations={failures}"
        )
        return replayed

    async def _scan_conversation(
        self,
        *,
        action: str,
        message_type: str,
        peer_id: str,
        page_size: int,
        max_age: int,
        scan_started: int,
    ) -> tuple[int, bool]:
        conversation_key = f"{message_type}:{peer_id}"
        now = int(time.time())
        cursor = self._conversation_cursors.get(conversation_key, self._baseline)
        since = max(now - max_age, cursor - WATERMARK_OVERLAP)
        rows, history_complete = await self._fetch_history_pages(
            action=action,
            message_type=message_type,
            peer_id=peer_id,
            page_size=page_size,
            since=since,
        )
        if not history_complete:
            return 0, False

        events = self._normalize_history_rows(
            rows,
            message_type=message_type,
            peer_id=peer_id,
            since=since,
        )
        await self._mark_historical_replies(events, rows)
        events = self._dedupe_and_sort(events)
        replay_failed = False
        for offset in range(0, len(events), REPLAY_BATCH_SIZE):
            chunk = events[offset : offset + REPLAY_BATCH_SIZE]
            tasks = [
                asyncio.create_task(self._adapter.replay_history_message(event))
                for event in chunk
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for event, result in zip(chunk, results):
                if isinstance(result, Exception) or result is False:
                    replay_failed = True
                    logger.warning(
                        "[QQ backfill] Replay failed for message "
                        f"{event.get('message_id')}: {result}"
                    )

        if not replay_failed:
            async with self._state_lock:
                self._conversation_cursors[conversation_key] = scan_started
                self._save_state()
        return len(events), not replay_failed

    async def _fetch_history_pages(
        self,
        *,
        action: str,
        message_type: str,
        peer_id: str,
        page_size: int,
        since: int,
    ) -> tuple[list[dict[str, Any]], bool]:
        """Page backwards from the latest message until the conversation cursor."""
        rows: list[dict[str, Any]] = []
        page_cursor = ""
        seen_cursors: set[str] = set()
        id_field = "user_id" if message_type == "private" else "group_id"

        for page_index in range(MAX_HISTORY_PAGES):
            params: dict[str, Any] = {
                id_field: self._api_id(peer_id),
                "count": page_size,
                "reverse_order": False,
            }
            if page_cursor:
                params["message_seq"] = page_cursor
            result = await self._safe_history_call(
                action,
                params,
                f"{message_type}:{peer_id}:page:{page_index + 1}",
            )
            if result is None:
                return rows, False

            page_rows = self._history_rows(result)
            for row in page_rows:
                row_copy = dict(row)
                row_copy["_history_index"] = len(rows)
                rows.append(row_copy)
            if not page_rows:
                return rows, True

            oldest = min(
                page_rows,
                key=lambda item: (
                    self._message_time(item) or 0,
                    self._numeric_message_id(item),
                ),
            )
            oldest_time = self._message_time(oldest)
            if len(page_rows) < page_size or (oldest_time and oldest_time <= since):
                return rows, True

            next_cursor = str(
                oldest.get("message_seq")
                or oldest.get("message_id")
                or oldest.get("id")
                or ""
            ).strip()
            if not next_cursor or next_cursor in seen_cursors:
                logger.warning(
                    f"[QQ backfill] History pagination stalled for {message_type}:{peer_id}"
                )
                return rows, False
            seen_cursors.add(next_cursor)
            page_cursor = next_cursor

        logger.warning(
            f"[QQ backfill] History page limit reached for {message_type}:{peer_id}; "
            "the cursor was not advanced"
        )
        return rows, False

    async def _wait_until_ready(self) -> bool:
        deadline = time.monotonic() + READY_TIMEOUT
        while time.monotonic() < deadline:
            if str(getattr(self._adapter, "_self_id", "")):
                return True
            await asyncio.sleep(0.2)
        return False

    async def _safe_history_call(
        self,
        action: str,
        params: dict[str, Any],
        label: str,
    ) -> Any | None:
        try:
            result = await self._adapter._call_api(action, params, wait_response=True)
        except Exception as exc:
            logger.warning(f"[QQ backfill] History query failed ({label}): {exc}")
            return None
        if result is None:
            logger.warning(f"[QQ backfill] History query timed out ({label})")
        return result

    @staticmethod
    def _api_id(value: str) -> int | str:
        return int(value) if value.isdigit() else value

    @staticmethod
    def _contact_rows(result: Any) -> list[dict[str, Any]]:
        if isinstance(result, list):
            return [item for item in result if isinstance(item, dict)]
        if not isinstance(result, dict):
            return []
        for key in ("list", "contacts", "data"):
            value = result.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
            if isinstance(value, dict):
                nested = StartupChecker._contact_rows(value)
                if nested:
                    return nested
        return []

    def _parse_contacts(self, result: Any) -> tuple[list[str], list[str]]:
        private_ids: list[str] = []
        group_ids: list[str] = []
        self_id = str(getattr(self._adapter, "_self_id", ""))

        for contact in self._contact_rows(result):
            latest = contact.get("lastestMsg") or contact.get("latestMsg") or {}
            if not isinstance(latest, dict):
                latest = {}
            chat_type = str(contact.get("chatType") or contact.get("chat_type") or "")
            message_type = str(
                latest.get("message_type") or contact.get("message_type") or ""
            ).lower()
            peer = str(
                contact.get("peerUin")
                or contact.get("peerUid")
                or contact.get("peer_id")
                or latest.get("group_id")
                or latest.get("user_id")
                or contact.get("group_id")
                or contact.get("user_id")
                or ""
            )
            if not peer or peer == self_id:
                continue
            if chat_type == "2" or message_type == "group":
                if peer not in group_ids:
                    group_ids.append(peer)
            elif chat_type == "1" or message_type == "private" or not chat_type:
                if peer not in private_ids:
                    private_ids.append(peer)

        family = [value for value in private_ids if value in self._family_qq]
        others = [value for value in private_ids if value not in self._family_qq]
        return family + others, group_ids

    @staticmethod
    def _history_rows(result: Any) -> list[dict[str, Any]]:
        if isinstance(result, list):
            return [item for item in result if isinstance(item, dict)]
        if not isinstance(result, dict):
            return []
        messages = result.get("messages") or result.get("messageList")
        if isinstance(messages, list):
            return [item for item in messages if isinstance(item, dict)]
        data = result.get("data")
        if data is not None and data is not result:
            return StartupChecker._history_rows(data)
        return []

    @staticmethod
    def _message_time(raw: dict[str, Any]) -> int:
        try:
            return int(raw.get("time") or raw.get("timestamp") or 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _numeric_message_id(raw: dict[str, Any]) -> int:
        try:
            return int(raw.get("message_seq") or raw.get("message_id") or raw.get("id") or 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _sender_id(raw: dict[str, Any]) -> str:
        sender = raw.get("sender") or {}
        if not isinstance(sender, dict):
            sender = {}
        return str(
            sender.get("user_id")
            or sender.get("uin")
            or raw.get("sender_id")
            or raw.get("user_id")
            or ""
        )

    @staticmethod
    def _reply_message_id(raw: dict[str, Any]) -> str:
        message = raw.get("message")
        if isinstance(message, list):
            for segment in message:
                if not isinstance(segment, dict) or segment.get("type") != "reply":
                    continue
                data = segment.get("data") or {}
                if isinstance(data, dict):
                    reply_id = str(data.get("id") or data.get("seq") or "").strip()
                    if reply_id:
                        return reply_id
        raw_message = str(raw.get("raw_message") or "")
        match = re.search(r"\[CQ:reply,(?:[^\]]*?)(?:id|seq)=([^,\]]+)", raw_message)
        return match.group(1).strip() if match else ""

    async def _mark_historical_replies(
        self,
        events: list[dict[str, Any]],
        history_rows: list[dict[str, Any]],
    ) -> None:
        """Mark replies to Bai, including replies whose target predates this page."""
        self_id = str(getattr(self._adapter, "_self_id", ""))
        bot_message_ids = {
            str(identifier).strip()
            for row in history_rows
            if self._sender_id(row) == self_id
            for identifier in (
                row.get("message_id"),
                row.get("message_seq"),
                row.get("id"),
            )
            if identifier not in (None, "")
        }
        bot_message_ids.discard("")
        lookup_cache: dict[str, bool] = {}
        lookup_count = 0

        for event in events:
            reply_id = self._reply_message_id(event)
            if not reply_id:
                continue
            is_bot_reply = reply_id in bot_message_ids
            if not is_bot_reply and reply_id in lookup_cache:
                is_bot_reply = lookup_cache[reply_id]
            elif not is_bot_reply and lookup_count < MAX_REPLY_LOOKUPS:
                lookup_count += 1
                result = await self._safe_history_call(
                    "get_msg",
                    {"message_id": self._api_id(reply_id)},
                    f"reply-target:{reply_id}",
                )
                is_bot_reply = isinstance(result, dict) and self._sender_id(result) == self_id
                lookup_cache[reply_id] = is_bot_reply
            if is_bot_reply:
                event["_offline_reply_to_me"] = True

    def _normalize_history_rows(
        self,
        rows: list[dict[str, Any]],
        *,
        message_type: str,
        peer_id: str,
        since: int,
    ) -> list[dict[str, Any]]:
        self_id = str(getattr(self._adapter, "_self_id", ""))
        events: list[dict[str, Any]] = []

        for raw in rows:
            sender = raw.get("sender") or {}
            if not isinstance(sender, dict):
                sender = {}
            sender_id = str(
                sender.get("user_id")
                or sender.get("uin")
                or raw.get("sender_id")
                or raw.get("user_id")
                or (peer_id if message_type == "private" else "")
            )
            if not sender_id or sender_id == self_id:
                continue

            message_time = self._message_time(raw)
            # A missing timestamp cannot be compared with the offline cursor.
            # Skipping it is safer than replaying an arbitrarily old side effect.
            if not message_time or message_time < since:
                continue

            event = dict(raw)
            event["post_type"] = "message"
            event["message_type"] = message_type
            event["self_id"] = self_id
            event["user_id"] = sender_id
            event["sender"] = dict(sender)
            event["sender"].setdefault("user_id", sender_id)
            if message_type == "group":
                event["group_id"] = str(raw.get("group_id") or peer_id)
            else:
                event.pop("group_id", None)
            if "message" not in event:
                event["message"] = event.get("raw_message", "")
            event["raw_message"] = event.get("raw_message", "")
            event["time"] = message_time
            event["_offline_replay"] = True

            if not self._is_event_processed(event):
                events.append(event)

        return events

    def _dedupe_and_sort(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        deduped: dict[str, dict[str, Any]] = {}
        for event in events:
            key = self._message_key_from_event(event)
            if key:
                deduped.setdefault(key, event)
        return sorted(
            deduped.values(),
            key=lambda item: (
                self._message_time(item),
                self._numeric_message_id(item),
                int(item.get("_history_index") or 0),
            ),
        )

    @staticmethod
    def _stable_fallback_key(payload: dict[str, Any]) -> str:
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        return "hash:" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def _message_key_from_event(self, event: dict[str, Any]) -> str:
        message_id = str(event.get("message_id") or "").strip()
        if message_id and message_id != "0":
            message_type = str(event.get("message_type") or "unknown")
            peer_value = (
                event.get("group_id")
                if message_type == "group"
                else event.get("user_id")
            )
            peer_id = str(peer_value or "unknown")
            return f"qq:{message_type}:{peer_id}:{message_id}"
        return self._stable_fallback_key(
            {
                "type": event.get("message_type"),
                "user": event.get("user_id"),
                "group": event.get("group_id"),
                "time": event.get("time"),
                "message": event.get("message") or event.get("raw_message"),
            }
        )

    def _message_key_from_object(self, msg: Any) -> str:
        raw = getattr(msg, "raw", None)
        if isinstance(raw, dict):
            return self._message_key_from_event(raw)
        return self._message_key_from_event(
            {
                "message_id": getattr(msg, "message_id", 0),
                "message_type": getattr(msg, "message_type", ""),
                "user_id": getattr(msg, "user_id", ""),
                "group_id": getattr(msg, "group_id", ""),
                "time": getattr(msg, "time", 0),
                "raw_message": getattr(msg, "raw_message", ""),
            }
        )

    def _is_event_processed(self, event: dict[str, Any]) -> bool:
        return self._is_processed_key(self._message_key_from_event(event))

    def _is_processed_key(self, key: str) -> bool:
        if key in self._processed:
            return True
        if key.startswith("qq:") and key[3:] in self._processed:
            return True
        if key.startswith("qq:"):
            message_id = key.rsplit(":", 1)[-1]
            if message_id in self._processed or f"qq:{message_id}" in self._processed:
                return True
        return False

    def _load_state(self) -> None:
        if not self._state_path.exists():
            return
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("processed"), dict):
                self._processed = {
                    str(key): int(value)
                    for key, value in data["processed"].items()
                    if isinstance(value, (int, float))
                }
                self._baseline = int(data.get("baseline") or data.get("watermark") or 0)
                raw_cursors = data.get("conversation_cursors") or {}
                if isinstance(raw_cursors, dict):
                    self._conversation_cursors = {
                        str(key): int(value)
                        for key, value in raw_cursors.items()
                        if isinstance(value, (int, float))
                    }
            elif isinstance(data, dict) and isinstance(data.get("processed_ids"), dict):
                # The pre-v0.1.9 checker wrote {"processed_ids": {...}}.
                self._processed = {
                    str(key): int(value)
                    for key, value in data["processed_ids"].items()
                    if isinstance(value, (int, float))
                }
                self._baseline = max(self._processed.values(), default=0)
            elif isinstance(data, dict):
                # Also accept the early flat mapping used by development builds.
                self._processed = {
                    str(key): int(value)
                    for key, value in data.items()
                    if isinstance(value, (int, float))
                }
                self._baseline = max(self._processed.values(), default=0)
            self._cleanup_expired()
            self._state_initialized = self._baseline > 0
        except Exception as exc:
            logger.warning(f"[QQ backfill] Could not load state: {exc}")
            self._processed = {}
            self._baseline = 0
            self._conversation_cursors = {}
            self._state_initialized = False

    def _save_state(self) -> None:
        self._cleanup_expired()
        payload = {
            "version": 3,
            "baseline": self._baseline,
            "conversation_cursors": self._conversation_cursors,
            "processed": self._processed,
        }
        temp_path = self._state_path.with_suffix(".json.tmp")
        temp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temp_path, self._state_path)

    def _cleanup_expired(self) -> None:
        cutoff = int(time.time()) - PROCESSED_EXPIRE_DAYS * 86400
        self._processed = {
            key: timestamp
            for key, timestamp in self._processed.items()
            if timestamp >= cutoff
        }
