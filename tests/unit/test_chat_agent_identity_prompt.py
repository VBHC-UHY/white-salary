"""Current QQ identity and platform must override desktop-only persona assumptions."""

from __future__ import annotations

from white_salary.core.agent.chat_agent import ChatAgent
from white_salary.core.interfaces.types import Message, MessageRole


class _Personality:
    def get_system_message(self) -> Message:
        return Message(
            role=MessageRole.SYSTEM,
            content="你面对的是主人；这是桌面一对一对话。",
        )


class _Affinity:
    def get_context_hint(self) -> str:
        return "[与用户的关系等级: 陌生人]"


def _agent() -> ChatAgent:
    agent = object.__new__(ChatAgent)
    agent._personality = _Personality()
    agent._memory_manager = None
    return agent


def test_qq_stranger_gets_identity_and_group_overrides(monkeypatch) -> None:
    import white_salary.core.affinity.manager as affinity_module
    import white_salary.core.memory.manager as memory_module

    monkeypatch.setattr(
        affinity_module.AffinityManager,
        "get_for_user",
        classmethod(lambda cls, user_id, data_dir="data/affinity": _Affinity()),
    )
    monkeypatch.setattr(memory_module, "is_owner_user", lambda user_id: False)

    message = _agent()._build_system_message(
        current_message="你好",
        user_id="stranger",
        is_group=True,
        group_id="g1",
    )

    assert "当前说话者不是主人" in message.content
    assert "这是 QQ 群聊" in message.content
    assert "陌生人" in message.content


def test_desktop_owner_does_not_get_stranger_override(monkeypatch) -> None:
    import white_salary.core.affinity.manager as affinity_module
    import white_salary.core.memory.manager as memory_module

    monkeypatch.setattr(
        affinity_module.AffinityManager,
        "get_for_user",
        classmethod(lambda cls, user_id, data_dir="data/affinity": _Affinity()),
    )
    monkeypatch.setattr(memory_module, "is_owner_user", lambda user_id: True)

    message = _agent()._build_system_message(user_id="desktop", is_group=False)

    assert "当前说话者不是主人" not in message.content
    assert "这是 QQ 群聊" not in message.content
