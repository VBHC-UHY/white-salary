"""Persistent short-term-memory isolation for platform conversations."""

from __future__ import annotations

import hashlib
import re
import asyncio
from collections import OrderedDict
from pathlib import Path

from white_salary.core.memory.short_term import ShortTermMemory

from .chat_agent import ChatAgent


class ChatAgentSessionPool:
    """Lazily creates one ChatAgent short-term memory per session key.

    LLM adapters, personality, long-term memory, tools, and the tool judge are
    shared through ``ChatAgent.clone_with_memory``. Only mutable conversation
    history is isolated and persisted separately.
    """

    def __init__(
        self,
        template: ChatAgent,
        data_dir: str | Path,
        *,
        max_turns: int = 20,
        max_cached_sessions: int = 512,
    ) -> None:
        self._template = template
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._max_turns = max(1, int(max_turns))
        self._max_cached_sessions = max(1, int(max_cached_sessions))
        self._agents: OrderedDict[str, ChatAgent] = OrderedDict()
        self._execution_locks: dict[str, asyncio.Lock] = {}

    def get(self, session_key: str) -> ChatAgent:
        key = str(session_key or "").strip()
        if not key:
            raise ValueError("session_key must not be empty")
        existing = self._agents.pop(key, None)
        if existing is not None:
            self._agents[key] = existing
            return existing

        memory = ShortTermMemory(
            max_turns=self._max_turns,
            persist_path=str(self.memory_path(key)),
        )
        agent = self._template.clone_with_memory(memory)
        self._agents[key] = agent
        self._trim_cache()
        return agent

    def memory_path(self, session_key: str) -> Path:
        key = str(session_key or "").strip()
        if not key:
            raise ValueError("session_key must not be empty")
        slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", key).strip("._")[:64] or "session"
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]
        return self._data_dir / f"{slug}-{digest}.json"

    def clear_all(self) -> int:
        """Clear cached and persisted session histories, returning file count."""
        self._agents.clear()
        self._execution_locks.clear()
        files = list(self._data_dir.glob("*.json"))
        for path in files:
            path.unlink(missing_ok=True)
        return len(files)

    @property
    def cached_session_count(self) -> int:
        return len(self._agents)

    def execution_lock(self, conversation_key: str) -> asyncio.Lock:
        key = str(conversation_key or "").strip()
        if not key:
            raise ValueError("conversation_key must not be empty")
        lock = self._execution_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._execution_locks[key] = lock
        return lock

    def _trim_cache(self) -> None:
        while len(self._agents) > self._max_cached_sessions:
            self._agents.popitem(last=False)


def qq_session_key(*, user_id: str, group_id: str = "", is_group: bool = False) -> str:
    """Return a QQ participant-session key without mixing users or groups."""
    user = str(user_id or "anonymous").strip() or "anonymous"
    if is_group:
        group = str(group_id or "unknown").strip() or "unknown"
        return f"qq:group:{group}:user:{user}"
    return f"qq:private:{user}"


def qq_conversation_key(*, user_id: str, group_id: str = "", is_group: bool = False) -> str:
    """Return the serialized execution scope for a QQ conversation."""
    if is_group:
        group = str(group_id or "unknown").strip() or "unknown"
        return f"qq:group:{group}"
    user = str(user_id or "anonymous").strip() or "anonymous"
    return f"qq:private:{user}"
