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
