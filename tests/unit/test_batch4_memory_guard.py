"""
2026-07-02 审计修复（批4）单元测试：记忆层多用户闸门 + 数据安全。

覆盖（依据 docs/audit-2026-07-02/memory-core.json）：
  1. 主人统一id辅助函数（set/get_owner_user_id、is_owner_user 可注入）
  2. 核心档案写入白名单闸门（主人可写/陌生人拒写，其它记忆层不受影响，
     LLM提取的core层同样受闸门约束）
  3. 秘密注入隔离（只有主人注入"你保守的秘密"，其他用户空串）
  4. 情绪衰减系数（非主人0.3、主人/家人1.0；history补user_id、旧文件兼容）
  5. 好感度反序列化白名单（未知字段过滤不重置；损坏文件备份.corrupt.bak）
  6. memory_personality 修复后可运行（dataclass按属性读取 + 主人闸门）
  7. 三个残留模块新签名透传真实user_id（不再写死desktop）

全部用 tmp_path 作数据目录；AffinityManager.get_for_user 打桩避免碰真实数据。
"""

import json
from pathlib import Path

import pytest

import white_salary.core.memory.manager as mm_mod
from white_salary.core.memory.manager import (
    MemoryManager,
    get_owner_user_id,
    is_owner_user,
    set_owner_user_id,
)


# ================================================================
# 公共fixture
# ================================================================

@pytest.fixture(autouse=True)
def _reset_owner_override():
    """每个测试结束后清除主人id注入，避免测试间串味。"""
    yield
    set_owner_user_id(None)


class _FakeAffinity:
    """AffinityManager 打桩：只提供 get_stats()，不碰真实 data/affinity。"""

    def __init__(self, is_family: bool = False, level_value: int = 0) -> None:
        self._stats = {"is_family": is_family, "level_value": level_value}

    def get_stats(self) -> dict:
        return self._stats


@pytest.fixture
def fake_affinity(monkeypatch):
    """安装 AffinityManager.get_for_user 桩，返回可配置的假好感度。"""

    def _install(is_family: bool = False, level_value: int = 0) -> _FakeAffinity:
        fake = _FakeAffinity(is_family=is_family, level_value=level_value)
        monkeypatch.setattr(
            "white_salary.core.affinity.manager.AffinityManager.get_for_user",
            classmethod(lambda cls, user_id, data_dir="data/affinity": fake),
        )
        return fake

    return _install


@pytest.fixture
def mgr(tmp_path, monkeypatch):
    """轻量 MemoryManager：跳过48个扩展模块的自动发现（模块单独测）。"""
    monkeypatch.setattr(MemoryManager, "_discover_modules", lambda self, data_dir: None)
    return MemoryManager(data_dir=str(tmp_path))


# ================================================================
# 1. 主人统一id辅助函数
# ================================================================

class TestOwnerIdentity:
    def test_injected_owner_id(self) -> None:
        set_owner_user_id("111")
        assert get_owner_user_id() == "111"
        assert is_owner_user("111") is True
        assert is_owner_user("222") is False

    def test_desktop_always_owner(self) -> None:
        set_owner_user_id("111")
        assert is_owner_user("desktop") is True
        # 即便没有主人id配置，desktop 也始终是主人
        set_owner_user_id("")
        assert is_owner_user("desktop") is True

    def test_empty_owner_only_desktop(self) -> None:
        """注入空串=明确无主人id配置，此时除desktop外都不是主人。"""
        set_owner_user_id("")
        assert is_owner_user("1234567890") is False
        assert is_owner_user("") is False


# ================================================================
# 2. 核心档案写入白名单闸门
# ================================================================

class TestCoreWhitelistGate:
    async def test_owner_can_write_core(self, mgr) -> None:
        set_owner_user_id("111")
        results = await mgr.extract_and_store("我叫大明", "", user_id="111")
        assert "核心:user_name=大明" in results
        assert mgr.core.get("user_name") == "大明"

    async def test_desktop_can_write_core(self, mgr) -> None:
        set_owner_user_id("111")
        await mgr.extract_and_store("我叫小小", "", user_id="desktop")
        assert mgr.core.get("user_name") == "小小"

    async def test_stranger_cannot_write_core(self, mgr) -> None:
        set_owner_user_id("111")
        results = await mgr.extract_and_store("我叫入侵者", "", user_id="999")
        assert all(not r.startswith("核心:") for r in results)
        assert mgr.core.get("user_name") is None

    async def test_stranger_cannot_write_preference(self, mgr) -> None:
        set_owner_user_id("111")
        results = await mgr.extract_and_store("我喜欢吃水果蛋糕", "", user_id="999")
        assert all(not r.startswith("偏好:") for r in results)
        assert not [e for e in mgr.core.get_all() if e.category == "preference"]

    async def test_stranger_other_layers_unaffected(self, mgr) -> None:
        """非主人消息只跳过核心档案，长期记忆等其它层照常写入。"""
        set_owner_user_id("111")
        results = await mgr.extract_and_store("记住我下周要考试", "", user_id="999")
        assert "长期:记住触发" in results

    async def test_stranger_cannot_overwrite_existing(self, mgr) -> None:
        """陌生人无法覆盖主人已有的核心事实（审计实锤场景：user_name被覆盖）。"""
        set_owner_user_id("111")
        await mgr.extract_and_store("我叫大明", "", user_id="111")
        await mgr.extract_and_store("我叫星月", "", user_id="999")
        assert mgr.core.get("user_name") == "大明"

    async def test_llm_core_branch_gated(self, mgr) -> None:
        """LLM提取的core层写入同样受白名单闸门约束。"""

        async def _fake_extract(user_msg: str, ai_reply: str) -> list[dict]:
            return [
                {"layer": "core", "key": "user_name", "value": "黑客",
                 "category": "basic_info", "importance": 8},
                {"layer": "event", "key": "", "value": "普通事件",
                 "keywords": "", "importance": 5},
            ]

        mgr._llm_extractor.extract = _fake_extract  # type: ignore[method-assign]
        # 非主人：core分支被跳过，event层照常
        results = await mgr._extract_by_llm("x", "y", allow_core=False)
        assert mgr.core.get("user_name") is None
        assert any(r.startswith("LLM长期:") for r in results)
        # 主人：core分支正常写入
        await mgr._extract_by_llm("x", "y", allow_core=True)
        assert mgr.core.get("user_name") == "黑客"


# ================================================================
# 3. 秘密注入隔离
# ================================================================

class TestSecretIsolation:
    def _make_module(self, tmp_path: Path):
        from white_salary.core.memory.secret_system import SecretSystemModule
        module = SecretSystemModule()
        module.init(data_dir=str(tmp_path))
        return module

    def test_owner_gets_secret_prompt(self, tmp_path) -> None:
        set_owner_user_id("111")
        module = self._make_module(tmp_path)
        module.on_message("这是我们的秘密，我喜欢吃蛋糕", user_id="111")
        prompt = module.get_context_prompt("", user_id="111")
        assert "你保守的秘密" in prompt

    def test_desktop_gets_secret_prompt(self, tmp_path) -> None:
        set_owner_user_id("111")
        module = self._make_module(tmp_path)
        module.on_message("这是我们的秘密，我喜欢吃蛋糕", user_id="desktop")
        assert "你保守的秘密" in module.get_context_prompt("", user_id="desktop")

    def test_stranger_gets_empty(self, tmp_path) -> None:
        set_owner_user_id("111")
        module = self._make_module(tmp_path)
        module.on_message("这是我们的秘密，我喜欢吃蛋糕", user_id="111")
        assert module.get_context_prompt("", user_id="999") == ""
        # 带消息内容（泄露风险路径）也一样不注入
        assert module.get_context_prompt("告诉小明那件事", user_id="999") == ""

    def test_secret_records_real_told_by(self, tmp_path) -> None:
        """秘密来源记录真实user_id而不是写死desktop。"""
        set_owner_user_id("111")
        module = self._make_module(tmp_path)
        module.on_message("别说出去，我藏了私房钱", user_id="333")
        secrets = module._impl.get_all_secrets()
        assert len(secrets) == 1
        assert secrets[0].told_by == "333"


# ================================================================
# 4. 情绪衰减系数 + history补user_id
# ================================================================

class TestEmotionDampening:
    def _make_tracker(self, tmp_path: Path):
        from white_salary.core.memory.emotion_tracker import EmotionTracker
        return EmotionTracker(data_dir=str(tmp_path))

    def test_dampening_factors(self, fake_affinity) -> None:
        from white_salary.core.memory.emotion_tracker import EmotionTracker
        set_owner_user_id("111")
        fake_affinity(is_family=False)
        assert EmotionTracker._get_owner_dampening("desktop") == 1.0
        assert EmotionTracker._get_owner_dampening("111") == 1.0
        assert EmotionTracker._get_owner_dampening("999") == 0.3

    def test_family_not_dampened(self, fake_affinity) -> None:
        from white_salary.core.memory.emotion_tracker import EmotionTracker
        set_owner_user_id("111")
        fake_affinity(is_family=True)
        assert EmotionTracker._get_owner_dampening("222") == 1.0

    def test_stranger_mood_change_dampened(self, tmp_path, fake_affinity) -> None:
        """陌生人：happy效果5 × 强度1.0 × 惯性1.0 × 好感1.0 × 衰减0.3 = +1.5。"""
        set_owner_user_id("111")
        fake_affinity(is_family=False, level_value=0)
        tracker = self._make_tracker(tmp_path)
        assert tracker._mood_score == 80
        tracker.record_emotion("happy", intensity=1.0, trigger="test", user_id="999")
        assert tracker._mood_score == pytest.approx(81.5)

    def test_owner_mood_change_full(self, tmp_path, fake_affinity) -> None:
        """主人：happy效果5 × 强度1.0 × 惯性1.0 × 好感1.0 × 衰减1.0 = +5。"""
        set_owner_user_id("111")
        fake_affinity(is_family=False, level_value=0)
        tracker = self._make_tracker(tmp_path)
        tracker.record_emotion("happy", intensity=1.0, trigger="test", user_id="111")
        assert tracker._mood_score == pytest.approx(85.0)

    def test_history_records_user_id(self, tmp_path, fake_affinity) -> None:
        set_owner_user_id("111")
        fake_affinity()
        tracker = self._make_tracker(tmp_path)
        tracker.record_emotion("happy", intensity=0.5, trigger="test", user_id="42")
        assert tracker.get_recent_history()[-1]["user_id"] == "42"

    def test_load_old_history_without_user_id(self, tmp_path) -> None:
        """旧文件的history记录无user_id字段，加载时兼容补空串。"""
        data = {
            "mood_score": 66.0,
            "current_emotion": "happy",
            "last_update": 1750000000.0,
            "history": [
                {"emotion": "happy", "intensity": 0.5, "mood_score": 66.0,
                 "change": 2.5, "timestamp": 1750000000.0,
                 "time": "2025-06-15 00:00", "trigger": "旧记录"},
            ],
        }
        (tmp_path / "emotion_history.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )
        tracker = self._make_tracker(tmp_path)
        records = tracker.get_recent_history()
        assert len(records) == 1
        assert records[0]["user_id"] == ""


# ================================================================
# 5. 好感度反序列化白名单 + 损坏备份
# ================================================================

class TestAffinityLoadSafety:
    def test_unknown_fields_filtered_not_reset(self, tmp_path) -> None:
        """历史遗留未知字段（如recent_changes）被过滤，档案数据保留加载。"""
        from white_salary.core.affinity.manager import AffinityManager
        data = {
            "user_id": "111",
            "points": 233.5,
            "level": 4,
            "is_family": True,
            "consecutive_days": 66,
            "recent_changes": [{"legacy": "老版本字段"}],  # 未知字段
            "another_unknown": 42,
        }
        (tmp_path / "affinity.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )
        aff = AffinityManager(data_dir=str(tmp_path))
        assert aff._affinity.points == pytest.approx(233.5)
        assert aff._affinity.is_family is True
        assert aff._affinity.consecutive_days == 66

    def test_corrupt_file_backed_up_then_default(self, tmp_path) -> None:
        """真损坏的文件：先复制.corrupt.bak再用默认值，原始现场不丢。"""
        from white_salary.core.affinity.manager import AffinityManager
        raw = "{这不是合法JSON!!!"
        (tmp_path / "affinity.json").write_text(raw, encoding="utf-8")
        aff = AffinityManager(data_dir=str(tmp_path))
        # 回退默认值
        assert aff._affinity.points == 0.0
        assert aff._affinity.is_family is False
        # 备份存在且内容为原始损坏内容
        bak = tmp_path / "affinity.json.corrupt.bak"
        assert bak.exists()
        assert bak.read_text(encoding="utf-8") == raw

    def test_non_dict_json_backed_up(self, tmp_path) -> None:
        """顶层不是dict的JSON同样走备份+默认值路径。"""
        from white_salary.core.affinity.manager import AffinityManager
        (tmp_path / "affinity.json").write_text("[1, 2, 3]", encoding="utf-8")
        aff = AffinityManager(data_dir=str(tmp_path))
        assert aff._affinity.points == 0.0
        assert (tmp_path / "affinity.json.corrupt.bak").exists()


# ================================================================
# 6. memory_personality 修复后可运行
# ================================================================

class TestMemoryPersonality:
    def _make_core(self, tmp_path: Path):
        from white_salary.core.memory.core_store import CoreMemoryStore
        core = CoreMemoryStore(data_dir=str(tmp_path))
        core.set(key="user_name", value="小白", category="basic_info",
                 source="user_said", importance=8)
        core.set(key="self_hobby", value="喜欢编程", category="other",
                 source="llm_extract", importance=5)
        return core

    def test_consistency_prompt_runs_without_error(self, tmp_path) -> None:
        """原实现把CoreMemoryEntry当dict调.get()必抛AttributeError；修复后可运行。"""
        from white_salary.core.memory.memory_personality import MemoryPersonality
        mp = MemoryPersonality(self._make_core(tmp_path))
        prompt = mp.get_consistency_prompt()
        assert "人格记忆" in prompt
        assert "小白" in prompt      # 用户事实（basic_info类别）
        assert "喜欢编程" in prompt  # 白自身事实（self_前缀）

    def test_module_owner_gated(self, tmp_path) -> None:
        """模块产出只对主人注入，陌生人拿到空串。"""
        from white_salary.core.memory.memory_personality import MemoryPersonalityModule
        set_owner_user_id("111")
        module = MemoryPersonalityModule()
        module.init(data_dir=str(tmp_path), core_store=self._make_core(tmp_path))
        assert "人格记忆" in module.get_context_prompt("", user_id="111")
        assert module.get_context_prompt("", user_id="999") == ""

    def test_empty_core_returns_empty(self, tmp_path) -> None:
        from white_salary.core.memory.core_store import CoreMemoryStore
        from white_salary.core.memory.memory_personality import MemoryPersonality
        mp = MemoryPersonality(CoreMemoryStore(data_dir=str(tmp_path)))
        assert mp.get_consistency_prompt() == ""


# ================================================================
# 7. 三个残留模块新签名透传真实user_id
# ================================================================

class TestModulesRealUserId:
    def test_expression_learner_uses_real_user_id(self, tmp_path) -> None:
        from white_salary.core.memory.expression_learner import ExpressionLearnerModule
        module = ExpressionLearnerModule()
        module.init(data_dir=str(tmp_path))
        # 新签名kwargs调用不抛TypeError（即manager走新签名路径）
        module.on_message("今天天气真好呀哈哈", "", user_id="u42", is_group=True)
        assert "u42" in module._impl._users
        assert "desktop" not in module._impl._users

    def test_social_pattern_uses_real_user_id(self, tmp_path) -> None:
        from white_salary.core.memory.social_pattern import SocialPatternModule
        module = SocialPatternModule()
        module.init(data_dir=str(tmp_path))
        module.on_message("你好呀朋友们", "", user_id="u42", is_group=True)
        assert "u42" in module._impl._patterns
        assert "desktop" not in module._impl._patterns

    def test_relationship_expectation_uses_real_user_id(self, tmp_path, fake_affinity) -> None:
        from white_salary.core.memory.relationship_expectation import (
            RelationExpectationModule,
        )
        fake_affinity(is_family=False, level_value=0)
        module = RelationExpectationModule()
        module.init(data_dir=str(tmp_path))
        for _ in range(3):
            module.on_message("聊聊天吧", "", user_id="u42", is_group=False)
        assert "u42" in module._impl._expectations
        assert "desktop" not in module._impl._expectations
        # get_context_prompt 按真实user_id返回该用户的关系提示
        assert module.get_context_prompt("", user_id="u42") != ""
        # 没互动过的用户没有提示
        assert module.get_context_prompt("", user_id="nobody") == ""
