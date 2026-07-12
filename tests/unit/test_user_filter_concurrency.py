"""Concurrency and persistence tests for the runtime QQ user filter."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

from white_salary.core.memory.user_filter import FilterResult, UserFilter


def test_parallel_blacklist_updates_keep_valid_complete_json(tmp_path) -> None:
    user_filter = UserFilter(
        data_dir=str(tmp_path / "memory"),
        affinity_data_dir=str(tmp_path / "affinity"),
    )

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(
            pool.map(
                lambda index: user_filter.add_to_blacklist(
                    f"user-{index}", reason="parallel", permanent=True
                ),
                range(40),
            )
        )

    stored = json.loads(user_filter._data_path.read_text(encoding="utf-8"))
    assert len(stored["hard_blacklist"]) == 40
    assert all(
        user_filter.check(f"user-{index}") == FilterResult.BLOCK
        for index in range(40)
    )
