"""Tests for persistent per-conversation ChatAgent isolation."""

from __future__ import annotations

import asyncio

from white_salary.core.agent.chat_agent import ChatAgent
from white_salary.core.agent.session_pool import (
    ChatAgentSessionPool,
    qq_conversation_key,
    qq_session_key,
)
from white_salary.core.memory.short_term import ShortTermMemory


class _FakeTemplate:
    def __init__(self) -> None:
        self.created = []

    def clone_with_memory(self, memory):
        clone = type("Clone", (), {})()
        clone._memory = memory
        self.created.append(clone)
        return clone


def test_qq_session_key_isolates_private_group_and_participant() -> None:
    assert qq_session_key(user_id="u1") == "qq:private:u1"
    assert qq_session_key(user_id="u1", group_id="g1", is_group=True) != qq_session_key(
        user_id="u2", group_id="g1", is_group=True
    )
    assert qq_session_key(user_id="u1", group_id="g1", is_group=True) != qq_session_key(
        user_id="u1", group_id="g2", is_group=True
    )
    assert qq_conversation_key(user_id="u1", group_id="g1", is_group=True) == (
        qq_conversation_key(user_id="u2", group_id="g1", is_group=True)
    )


def test_pool_reuses_same_session_and_isolates_different_sessions(tmp_path) -> None:
    template = _FakeTemplate()
    pool = ChatAgentSessionPool(template, tmp_path, max_turns=5)

    first = pool.get("qq:private:u1")
    same = pool.get("qq:private:u1")
    other = pool.get("qq:private:u2")

    assert first is same
    assert first is not other
    first._memory.add_user_message("only u1 knows this")
    assert other._memory.message_count == 0


def test_evicted_session_restores_its_own_persisted_history(tmp_path) -> None:
    template = _FakeTemplate()
    pool = ChatAgentSessionPool(template, tmp_path, max_cached_sessions=1)
    first = pool.get("qq:private:u1")
    first._memory.add_user_message("persist me")

    pool.get("qq:private:u2")
    restored = pool.get("qq:private:u1")

    assert restored is not first
    assert [message.content for message in restored._memory.get_messages()] == ["persist me"]


def test_memory_paths_are_stable_and_cannot_escape_data_dir(tmp_path) -> None:
    pool = ChatAgentSessionPool(_FakeTemplate(), tmp_path)
    first = pool.memory_path("../../group/危险")
    second = pool.memory_path("../../group/危险")

    assert first == second
    assert first.parent == tmp_path
    assert first.suffix == ".json"


def test_clear_all_removes_cached_and_persisted_histories(tmp_path) -> None:
    pool = ChatAgentSessionPool(_FakeTemplate(), tmp_path)
    pool.get("one")._memory.add_user_message("1")
    pool.get("two")._memory.add_user_message("2")

    assert pool.clear_all() == 2
    assert pool.cached_session_count == 0
    assert list(tmp_path.glob("*.json")) == []


def test_chat_agent_clone_shares_services_but_not_short_term_memory() -> None:
    template_memory = ShortTermMemory(max_turns=2)
    isolated_memory = ShortTermMemory(max_turns=2)
    llm = object()
    personality = object()
    template = ChatAgent(
        llm=llm,
        personality=personality,
        memory=template_memory,
        content_filter_enabled=False,
    )

    clone = template.clone_with_memory(isolated_memory)

    assert clone is not template
    assert clone._llm is llm
    assert clone._personality is personality
    assert clone._memory is isolated_memory
    assert template._memory is template_memory
    assert clone._content_filter_enabled is False


async def test_execution_lock_serializes_same_conversation_only(tmp_path) -> None:
    pool = ChatAgentSessionPool(_FakeTemplate(), tmp_path)
    same = pool.execution_lock("qq:group:g1")
    same_again = pool.execution_lock("qq:group:g1")
    other = pool.execution_lock("qq:group:g2")

    assert same is same_again
    await same.acquire()
    blocked = asyncio.create_task(same_again.acquire())
    await asyncio.sleep(0)
    assert not blocked.done()

    await other.acquire()
    other.release()
    same.release()
    await blocked
    same_again.release()
