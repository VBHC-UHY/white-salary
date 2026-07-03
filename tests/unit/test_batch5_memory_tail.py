"""
2026-07-03 审计修复（批5）单元测试：记忆系统扫尾。

覆盖（依据 docs/audit-2026-07-02/memory-core.json + logs.json）：
  1. 重组件进程级共享实例（CoreMemoryStore / LongTermMemoryStore /
     EmotionTracker / AffinityManager / MemoryManager 同一 data_dir
     两次获取同一对象；不同 data_dir 互不干扰；get_for_user 多用户
     路径仍是每用户独立实例）
  2. 模块落盘后台任务（flush_modules 只调用真正覆写 on_session_end 的
     模块；_ensure_flush_task 懒启动+防重；后台循环真的会触发落盘）
  3. ChromaDB 死向量清理（delete/_cleanup_expired/_trim_if_needed 同步
     删除向量；__init__ 对账清掉孤儿向量）
  4. LLM情感分析限额归位（真正花钱的LLM路径受每日限额约束、门槛查
     _emotion_llm；零成本关键词路径不再受限额）
  5. 模块启用开关（modules.disabled 配置生效；出厂配置默认禁用
     计划书9.2列出的20个精简候选）

全部用 tmp_path 作数据目录（共享实例按路径缓存，tmp_path 每测试唯一，
测试间天然隔离）；好感度用桩避免碰真实 data/affinity。
"""

import asyncio
import json
import sqlite3
import time
from pathlib import Path

import pytest

import white_salary.core.memory.long_term_store as lts_mod
from white_salary.core.affinity.manager import AffinityManager
from white_salary.core.memory.core_store import CoreMemoryStore
from white_salary.core.memory.emotion_tracker import EmotionTracker
from white_salary.core.memory.long_term_store import LongTermMemoryStore
from white_salary.core.memory.manager import MemoryManager
from white_salary.core.memory.module_base import MemoryModule

# 项目根目录（不依赖CWD）
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


# ================================================================
# 公共桩
# ================================================================

class _FakeChromaCollection:
    """Chroma collection 桩：只记录 id 集合，验证增删同步。"""

    def __init__(self, ids: list[str] | None = None) -> None:
        self._ids: set[str] = set(ids or [])

    def add(self, ids: list[str], documents=None, metadatas=None) -> None:
        self._ids.update(ids)

    def delete(self, ids: list[str]) -> None:
        self._ids.difference_update(ids)

    def get(self) -> dict:
        return {"ids": list(self._ids)}

    def count(self) -> int:
        return len(self._ids)


class _SavingModule(MemoryModule):
    """覆写了 on_session_end 的模块桩（记录落盘次数）。"""

    name = "batch5_saving_stub"

    def __init__(self) -> None:
        self.saved_count = 0

    def on_session_end(self) -> None:
        self.saved_count += 1


class _NoHookModule(MemoryModule):
    """未覆写 on_session_end 的模块桩（应被 flush 跳过）。"""

    name = "batch5_nohook_stub"


class _BrokenModule(MemoryModule):
    """on_session_end 抛异常的模块桩（不应影响其它模块落盘）。"""

    name = "batch5_broken_stub"

    def on_session_end(self) -> None:
        raise RuntimeError("模拟落盘失败")


@pytest.fixture
def lite_mgr_factory(monkeypatch):
    """轻量 MemoryManager 工厂：跳过扩展模块自动发现 + 空配置。"""
    monkeypatch.setattr(MemoryManager, "_discover_modules", lambda self, data_dir: None)
    monkeypatch.setattr(MemoryManager, "_load_config", lambda self: {})

    def _make(data_dir: str) -> MemoryManager:
        return MemoryManager(data_dir=data_dir)

    return _make


@pytest.fixture
def no_chroma(monkeypatch):
    """禁用真实 ChromaDB 初始化（测试注入 FakeChromaCollection）。"""
    monkeypatch.setattr(lts_mod, "CHROMADB_AVAILABLE", False)


# ================================================================
# 1. 进程级共享实例（两次获取同一对象）
# ================================================================

class TestSharedInstances:
    def test_core_store_same_dir_same_object(self, tmp_path) -> None:
        a = CoreMemoryStore(data_dir=str(tmp_path))
        b = CoreMemoryStore(data_dir=str(tmp_path))
        assert a is b

    def test_core_store_different_dir_different_object(self, tmp_path) -> None:
        a = CoreMemoryStore(data_dir=str(tmp_path / "d1"))
        b = CoreMemoryStore(data_dir=str(tmp_path / "d2"))
        assert a is not b

    def test_core_store_shared_state_visible(self, tmp_path) -> None:
        """共享实例写入后，第二次'构造'能直接读到（同一份内存缓存）。"""
        a = CoreMemoryStore(data_dir=str(tmp_path))
        a.set(key="batch5_key", value="批5", category="other")
        b = CoreMemoryStore(data_dir=str(tmp_path))
        assert b.get("batch5_key") == "批5"

    def test_long_term_store_same_dir_same_object(self, tmp_path, no_chroma) -> None:
        a = LongTermMemoryStore(data_dir=str(tmp_path))
        b = LongTermMemoryStore(data_dir=str(tmp_path))
        assert a is b

    def test_emotion_tracker_same_dir_same_object(self, tmp_path) -> None:
        a = EmotionTracker(data_dir=str(tmp_path))
        b = EmotionTracker(data_dir=str(tmp_path))
        assert a is b

    def test_affinity_manager_same_dir_same_object(self, tmp_path) -> None:
        a = AffinityManager(data_dir=str(tmp_path))
        b = AffinityManager(data_dir=str(tmp_path))
        assert a is b

    def test_affinity_get_for_user_still_per_user(self, tmp_path) -> None:
        """多用户路径（shared=False逃生口）：每个用户独立实例、独立档案文件。"""
        try:
            u1 = AffinityManager.get_for_user("batch5_u1", data_dir=str(tmp_path))
            u2 = AffinityManager.get_for_user("batch5_u2", data_dir=str(tmp_path))
            assert u1 is not u2
            assert u1._data_path != u2._data_path
            # 同一用户第二次获取命中 _multi_user_cache
            assert AffinityManager.get_for_user("batch5_u1", data_dir=str(tmp_path)) is u1
        finally:
            # 清理多用户缓存，避免污染其它测试
            AffinityManager._multi_user_cache.pop("batch5_u1", None)
            AffinityManager._multi_user_cache.pop("batch5_u2", None)

    def test_memory_manager_same_dir_same_object(self, tmp_path, lite_mgr_factory) -> None:
        a = lite_mgr_factory(str(tmp_path))
        b = lite_mgr_factory(str(tmp_path))
        assert a is b

    def test_relative_and_resolved_path_same_object(self, tmp_path, monkeypatch) -> None:
        """相对路径与绝对路径解析到同一目录时也复用同一实例。"""
        monkeypatch.chdir(tmp_path)
        a = CoreMemoryStore(data_dir="mem_rel")
        b = CoreMemoryStore(data_dir=str(tmp_path / "mem_rel"))
        assert a is b


# ================================================================
# 2. 模块落盘后台任务
# ================================================================

class TestModuleFlush:
    def test_flush_modules_calls_overriding_hooks_only(self, tmp_path, lite_mgr_factory) -> None:
        mgr = lite_mgr_factory(str(tmp_path))
        saving = _SavingModule()
        mgr._modules = [saving, _NoHookModule()]
        flushed = mgr.flush_modules()
        assert flushed == 1
        assert saving.saved_count == 1

    def test_flush_modules_survives_broken_module(self, tmp_path, lite_mgr_factory) -> None:
        """单个模块落盘抛异常不影响其它模块。"""
        mgr = lite_mgr_factory(str(tmp_path))
        saving = _SavingModule()
        mgr._modules = [_BrokenModule(), saving]
        flushed = mgr.flush_modules()
        assert flushed == 1
        assert saving.saved_count == 1

    def test_no_flush_task_without_event_loop(self, tmp_path, lite_mgr_factory) -> None:
        """无事件循环的线程构造 manager：懒启动不报错、任务保持未启动。"""
        mgr = lite_mgr_factory(str(tmp_path))
        assert mgr._flush_task is None

    def test_flush_task_lazy_start_dedup_and_periodic_save(self, tmp_path, lite_mgr_factory) -> None:
        """事件循环内懒启动：防重（同一任务）+ 周期性触发模块落盘。"""
        mgr = lite_mgr_factory(str(tmp_path))
        saving = _SavingModule()
        mgr._modules = [saving]
        mgr._flush_interval_seconds = 0.05

        async def _run() -> None:
            mgr._ensure_flush_task()
            assert mgr._flush_task is not None
            first_task = mgr._flush_task
            # 防重：再次调用不会另起任务
            mgr._ensure_flush_task()
            assert mgr._flush_task is first_task
            # 等两个周期，落盘应已发生
            await asyncio.sleep(0.15)
            assert saving.saved_count >= 1
            # 收尾：取消任务（取消路径会做最后一次落盘）
            first_task.cancel()
            try:
                await first_task
            except asyncio.CancelledError:
                pass

        asyncio.run(_run())

    def test_extract_and_store_lazy_starts_flush_task(self, tmp_path, lite_mgr_factory, monkeypatch) -> None:
        """首次 async 调用（extract_and_store）自动补启动落盘任务。"""
        mgr = lite_mgr_factory(str(tmp_path))
        assert mgr._flush_task is None

        async def _run() -> None:
            await mgr.extract_and_store("随便聊聊今天的安排吧", "好的呀")
            assert mgr._flush_task is not None
            mgr._flush_task.cancel()
            try:
                await mgr._flush_task
            except asyncio.CancelledError:
                pass

        asyncio.run(_run())


# ================================================================
# 3. ChromaDB 死向量清理
# ================================================================

class TestChromaSync:
    def _make_store(self, tmp_path) -> tuple[LongTermMemoryStore, _FakeChromaCollection]:
        store = LongTermMemoryStore(data_dir=str(tmp_path))
        fake = _FakeChromaCollection()
        store._chroma_collection = fake
        return store, fake

    def test_delete_syncs_chroma(self, tmp_path, no_chroma) -> None:
        store, fake = self._make_store(tmp_path)
        i1 = store.add("批5测试记忆一", layer="fact")
        i2 = store.add("批5测试记忆二", layer="fact")
        assert {str(i1), str(i2)} <= fake._ids
        assert store.delete(i1) is True
        assert str(i1) not in fake._ids
        assert str(i2) in fake._ids

    def test_delete_nonexistent_no_chroma_touch(self, tmp_path, no_chroma) -> None:
        store, fake = self._make_store(tmp_path)
        i1 = store.add("批5测试记忆", layer="fact")
        assert store.delete(999999) is False
        assert str(i1) in fake._ids

    def test_cleanup_expired_syncs_chroma(self, tmp_path, no_chroma) -> None:
        store, fake = self._make_store(tmp_path)
        i1 = store.add("批5临时记忆", layer="temp")
        i2 = store.add("批5永久记忆", layer="fact")
        # 手动把 i1 的过期时间改到过去
        conn = sqlite3.connect(str(store._db_path))
        conn.execute(
            "UPDATE long_term_memory SET expires_at = ? WHERE id = ?",
            (time.time() - 10, i1),
        )
        conn.commit()
        conn.close()
        store._cleanup_expired()
        assert str(i1) not in fake._ids
        assert str(i2) in fake._ids

    def test_trim_syncs_chroma(self, tmp_path, no_chroma) -> None:
        store, fake = self._make_store(tmp_path)
        store._max_entries = 1
        store.add("批5记忆甲", layer="fact", importance=1)
        store.add("批5记忆乙", layer="fact", importance=9)
        # 容量裁剪后，Chroma 里的 id 应与 SQLite 存活 id 完全一致
        conn = sqlite3.connect(str(store._db_path))
        alive = {str(r[0]) for r in conn.execute("SELECT id FROM long_term_memory").fetchall()}
        conn.close()
        assert fake._ids == alive
        assert len(alive) == 1

    def test_reconcile_removes_orphan_vectors(self, tmp_path, no_chroma) -> None:
        """对账：Chroma有而SQLite没有的孤儿向量被清掉，活向量保留。"""
        store, fake = self._make_store(tmp_path)
        i1 = store.add("批5活记忆", layer="fact")
        fake.add(ids=["999901", "999902"])  # 模拟历史死向量
        removed = store._reconcile_chroma()
        assert removed == 2
        assert fake._ids == {str(i1)}

    def test_reconcile_noop_when_consistent(self, tmp_path, no_chroma) -> None:
        store, fake = self._make_store(tmp_path)
        store.add("批5活记忆", layer="fact")
        assert store._reconcile_chroma() == 0


# ================================================================
# 4. LLM情感分析限额归位
# ================================================================

class TestEmotionAnalysisQuota:
    @pytest.fixture
    def quota_mgr(self, tmp_path, lite_mgr_factory, monkeypatch) -> MemoryManager:
        mgr = lite_mgr_factory(str(tmp_path))
        # 好感度/主人判定打桩，避免碰真实 data/affinity
        monkeypatch.setattr(
            EmotionTracker, "_get_affinity_emotion_multiplier",
            staticmethod(lambda user_id="desktop": 1.0),
        )
        monkeypatch.setattr(
            EmotionTracker, "_get_owner_dampening",
            staticmethod(lambda user_id="desktop": 1.0),
        )
        return mgr

    def test_llm_path_respects_daily_quota(self, quota_mgr, monkeypatch) -> None:
        """LLM情感分析路径：超过每日限额后不再发起LLM调用。"""
        calls: list[str] = []

        async def _fake_analyze(self, text: str):
            calls.append(text)
            return "happy"

        monkeypatch.setattr(MemoryManager, "_analyze_emotion_by_llm", _fake_analyze)
        quota_mgr._emotion_llm = object()  # 门槛为真
        quota_mgr._max_emotion_analysis_per_day = 2

        async def _run() -> None:
            for _ in range(4):
                await quota_mgr.extract_and_store("这是一句足够长的普通对话内容哈", "好的")

        asyncio.run(_run())
        assert len(calls) == 2
        assert quota_mgr._emotion_analysis_count == 2

    def test_llm_gate_checks_emotion_llm_not_extractor(self, quota_mgr, monkeypatch) -> None:
        """门槛查 self._emotion_llm：只配了 memory_llm 时不做LLM情感分析。"""
        calls: list[str] = []

        async def _fake_analyze(self, text: str):
            calls.append(text)
            return "happy"

        monkeypatch.setattr(MemoryManager, "_analyze_emotion_by_llm", _fake_analyze)
        quota_mgr._llm_extractor._llm = object()  # 原实现看的是它（张冠李戴）
        quota_mgr._emotion_llm = None

        async def _run() -> None:
            await quota_mgr.extract_and_store("这是一句足够长的普通对话内容哈", "好的")

        asyncio.run(_run())
        assert calls == []

    def test_quota_resets_on_new_day(self, quota_mgr) -> None:
        quota_mgr._emotion_analysis_date = "2000-01-01"
        quota_mgr._emotion_analysis_count = 999
        assert quota_mgr._check_emotion_analysis_quota() is True
        assert quota_mgr._emotion_analysis_count == 0
        assert quota_mgr._emotion_analysis_date == time.strftime("%Y-%m-%d")

    def test_keyword_path_no_longer_limited(self, quota_mgr) -> None:
        """零成本关键词路径：限额用尽也照常提取情感记忆。"""
        quota_mgr._emotion_analysis_date = time.strftime("%Y-%m-%d")
        quota_mgr._emotion_analysis_count = 999
        results = quota_mgr._extract_emotion_memory("我今天好开心啊")
        assert any(r.startswith("情感:positive_strong") for r in results)
        # 关键词路径不消耗LLM限额计数
        assert quota_mgr._emotion_analysis_count == 999


# ================================================================
# 5. 模块启用开关（计划书9.2决策落地）
# ================================================================

class TestModuleDisableConfig:
    def test_disabled_module_skipped(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(
            MemoryManager, "_load_config",
            lambda self: {"modules": {"disabled": ["nostalgia"]}},
        )
        mgr = MemoryManager(data_dir=str(tmp_path))
        names = [m.name for m in mgr._modules]
        assert "nostalgia" not in names

    def test_module_loaded_when_not_disabled(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(MemoryManager, "_load_config", lambda self: {})
        mgr = MemoryManager(data_dir=str(tmp_path))
        names = [m.name for m in mgr._modules]
        assert "nostalgia" in names

    def test_shipped_config_disables_20_candidates(self) -> None:
        """出厂配置：计划书9.2列出的精简候选仍在disabled里。

        2026-07-03 工具实现（批9）：slang_learner/expression_learner 从禁用列表
        移除（view_learned_style 工具需要这两个模块的学习数据，用户明确要恢复
        学习），期望集合相应从20个缩到18个，并断言这两个已不在禁用列表。
        """
        cfg_path = _PROJECT_ROOT / "config" / "memory_settings.json"
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        disabled = set(cfg.get("modules", {}).get("disabled", []))
        expected = {
            "language_style", "owner_style",
            "natural_expression", "dialogue_evolution", "dialogue_depth",
            "opinion_system", "secret_system", "nostalgia", "habits",
            "growth_tracker", "memory_personality", "relationship_milestone",
            "relationship_expectation", "expectation_system",
            "disappointment_tracker", "social_pattern", "conversation_rhythm",
            "condition_engine",
        }
        assert expected <= disabled
        assert len(expected) == 18
        # 批9恢复学习的两个模块必须不在禁用列表
        assert "slang_learner" not in disabled
        assert "expression_learner" not in disabled
