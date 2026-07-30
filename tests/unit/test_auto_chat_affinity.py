"""AutoChat must use the same owner affinity profile as desktop and QQ."""

from white_salary.core.affinity.manager import AffinityManager
from white_salary.core.auto_chat import AutoChatManager


async def _noop_send(_: str) -> None:
    return None


def test_auto_chat_reads_configured_owner_profile(tmp_path) -> None:
    AffinityManager._multi_user_cache.clear()
    owner = AffinityManager.get_for_user("owner", data_dir=str(tmp_path))
    owner.set_points(160)

    manager = AutoChatManager(
        _noop_send,
        user_id="owner",
        affinity_data_dir=str(tmp_path),
    )

    assert manager._get_affinity_multiplier() == 2.0


def test_auto_chat_restart_does_not_make_care_immediately_due() -> None:
    manager = AutoChatManager(_noop_send)

    assert manager._last_care_time == manager._start_time


async def test_care_prompts_rotate_and_do_not_force_meal_or_clock_talk(monkeypatch) -> None:
    sent: list[str] = []

    async def collect(text: str) -> None:
        sent.append(text)

    monkeypatch.setattr("white_salary.core.auto_chat.random.choice", lambda items: items[0])
    manager = AutoChatManager(collect)

    await manager._send_care(12)
    await manager._send_care(18)

    assert len(sent) == 2
    assert sent[0] != sent[1]
    assert all("吃饭" not in hint and "午饭" not in hint and "晚饭" not in hint for hint in sent)
    assert all("现在是" not in hint and "时间到了" not in hint for hint in sent)


async def test_random_topics_rotate_semantic_directions_without_meal_template(
    monkeypatch,
) -> None:
    sent: list[str] = []

    async def collect(text: str) -> None:
        sent.append(text)

    monkeypatch.setattr("white_salary.core.auto_chat.random.random", lambda: 1.0)
    monkeypatch.setattr("white_salary.core.auto_chat.random.choice", lambda items: items[0])
    manager = AutoChatManager(collect)

    await manager._send_random_topic()
    await manager._send_random_topic()

    assert len(sent) == 2
    assert sent[0] != sent[1]
    assert all("只把这个语义方向当灵感" in hint for hint in sent)
    assert all("吃饭了吗" not in hint and "吃了没" not in hint for hint in sent)
