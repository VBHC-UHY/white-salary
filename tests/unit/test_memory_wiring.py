"""黄金测试 — 锁住记忆系统的两处"接线断了但没人知道"的缺陷。

两处缺陷的共同形状：**调用方按一个名字/协议去用，被调方是另一个，
而中间隔着一个吞掉一切的 except，于是功能整体失效却零报错。**

1. services/memory_consolidation.py 调 store.cleanup_expired()，
   而 LongTermMemoryStore 只有私有的 _cleanup_expired → AttributeError
   被 _consolidate 的外层 except 接住 → 每日整理的四步（过期清理、长期
   记忆去重、核心记忆去重、批5 挂上来的 enhanced 遗忘曲线维护）全都
   从未执行过，日志里只有一行"整理失败"。

2. adapters/tools/builtin/memory_tools.py 对 CoreMemoryStore._cache 的值
   调 .get("value")，而那是 CoreMemoryEntry dataclass → AttributeError
   被裸 except 吞掉 → 白的"搜索记忆"工具查核心记忆恒返回空。
"""

import sqlite3
import time

import pytest

from white_salary.core.memory.core_store import CoreMemoryStore
from white_salary.core.memory.long_term_store import LongTermMemoryStore


# ---------------------------------------------------------------------------
# 1. 每日整理服务依赖的公开入口必须存在且语义正确
# ---------------------------------------------------------------------------


def test_consolidation_service_entry_point_exists() -> None:
    """整理服务按名字调用的入口必须是公开可调用的。

    这条断言直接对着 memory_consolidation.py 的调用点，
    改名/改私有都会先炸在这里而不是变成一行"整理失败"。
    """
    assert callable(getattr(LongTermMemoryStore, "cleanup_expired", None)), (
        "LongTermMemoryStore.cleanup_expired 不存在，"
        "每日整理服务第一步就会 AttributeError，四步全废"
    )


def test_cleanup_expired_removes_only_expired_and_reports_count(tmp_path) -> None:
    """过期的删掉、未过期的留下，并如实返回删除条数。"""
    store = LongTermMemoryStore(data_dir=str(tmp_path), provider="none")
    store.add("这条马上过期", layer="temp", source="test")
    store.add("这条永久保留", layer="fact", source="test")

    conn = sqlite3.connect(str(store._db_path))
    conn.execute(
        "UPDATE long_term_memory SET expires_at = ? WHERE layer = 'temp'",
        (time.time() - 1,),
    )
    conn.commit()
    conn.close()

    removed = store.cleanup_expired()

    assert removed == 1, "应当如实报告删除了 1 条（整理服务的统计依赖这个返回值）"

    conn = sqlite3.connect(str(store._db_path))
    layers = [row[0] for row in conn.execute("SELECT layer FROM long_term_memory")]
    conn.close()
    assert layers == ["fact"], "永久层记忆不得被误删"


def test_cleanup_expired_on_empty_store_is_zero(tmp_path) -> None:
    """空库不得抛异常，返回 0。"""
    store = LongTermMemoryStore(data_dir=str(tmp_path), provider="none")

    assert store.cleanup_expired() == 0


def test_consolidation_call_site_still_matches(tmp_path) -> None:
    """整理服务的调用点与 store 的公开入口必须持续对得上。

    只测 store 单边不够——缺陷本身就是"两边各自看起来都对"。
    """
    import inspect

    from white_salary.core.services import memory_consolidation

    source = inspect.getsource(memory_consolidation)
    assert "_long_term.cleanup_expired()" in source, (
        "整理服务改了调用方式，需同步确认 store 侧公开入口仍然匹配"
    )


# ---------------------------------------------------------------------------
# 2. memory_search 必须真能查到核心记忆
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memory_search_finds_core_memory(tmp_path, monkeypatch) -> None:
    """核心记忆分支必须真的返回结果，而不是被 except 吞成空。"""
    from white_salary.adapters.tools.builtin import memory_tools

    store = CoreMemoryStore(data_dir=str(tmp_path))
    store.set("用户爱好", "喜欢玩Minecraft和养猫", source="test")

    monkeypatch.setattr(
        "white_salary.core.memory.core_store.CoreMemoryStore",
        lambda *args, **kwargs: store,
    )

    result = await memory_tools.memory_search(keyword="Minecraft", memory_type="core")

    assert "Minecraft" in result, f"核心记忆没被检索到：{result}"
    assert "没找到" not in result


def test_core_memory_entry_is_not_a_dict() -> None:
    """固化前提：_cache 的值是 dataclass，不能用 .get() 取值。

    这条断言的作用是——如果哪天 CoreMemoryEntry 真的变成了 dict，
    它会失败并提示去检查 memory_tools 的取值方式，避免两边再次错位。
    """
    from white_salary.core.memory.core_store import CoreMemoryEntry

    assert not hasattr(CoreMemoryEntry, "get"), (
        "CoreMemoryEntry 变成了类似 dict 的类型，请同步检查 memory_tools 的取值方式"
    )
