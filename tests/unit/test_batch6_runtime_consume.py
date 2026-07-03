"""
tests/unit/test_batch6_runtime_consume.py

2026-07-03 面板升级（批6）：运行时消费侧接活的单元测试（KEY=p3-runtime）。

覆盖点（依据 docs/panel-audit-2026-07-03/panel-chatcfg/voice/expressions/persona/users.json）：
  1. features 开关：websocket_handler/qq_handler 的装配工厂函数按开关产出 None/实例；
     ChatAgent 的 content_filter_enabled 参数真实生效
  2. 情绪语速：TTSConfig.speed 字段、GPTSoVITSAdapter 请求体的语速相乘与夹紧、
     websocket_handler 情绪倍率解析的防御回退
  3. 表情映射：config/expression_map.json 文件优先、非法/缺失回退硬编码
  4. HumanLikeFilter：prompt_templates.json 的 banned_words/banned_lecture 配置合并
  5. 长期记忆引擎：provider='none' 不建 Chroma、缓存键按引擎隔离、模块级默认注入
  6. PersonalityManager.reload() 热重载

不启动服务器、不发真实网络请求。
"""

import json
from pathlib import Path

import pytest
import yaml

import white_salary.core.memory.emotion_tracker as emotion_tracker_module
import white_salary.core.memory.long_term_store as long_term_store_module
import white_salary.infrastructure.server.qq_handler as qq_handler_module
import white_salary.infrastructure.server.websocket_handler as ws_module
from white_salary.adapters.tts.gpt_sovits_adapter import GPTSoVITSAdapter
from white_salary.core.agent.chat_agent import ChatAgent
from white_salary.core.memory.emotion_tracker import EmotionTracker
from white_salary.core.memory.human_like_filter import HumanLikeFilter
from white_salary.core.memory.long_term_store import (
    LongTermMemoryStore,
    set_default_long_term_provider,
)
from white_salary.core.memory.short_term import ShortTermMemory
from white_salary.core.personality.character import PersonalityManager
from white_salary.infrastructure.config.models import FeaturesConfig, TTSConfig

PROJECT_ROOT = Path(__file__).parent.parent.parent


# ================================================================
# 公共工具
# ================================================================

def _write_conf_project(tmp_path: Path, features: dict) -> Path:
    """构造一个含 conf.default.yaml + conf.yaml 的最小临时项目根目录。"""
    (tmp_path / "conf.default.yaml").write_text(
        yaml.dump({"system": {"name": "White Salary"}}, allow_unicode=True),
        encoding="utf-8",
    )
    (tmp_path / "conf.yaml").write_text(
        yaml.dump({"features": features}, allow_unicode=True),
        encoding="utf-8",
    )
    return tmp_path


class _StubLLM:
    """极简 LLM 桩（ChatAgent 构造只需要一个对象引用）。"""

    async def chat_completion(self, *args: object, **kwargs: object) -> str:
        return "ok"


# ================================================================
# 1. features 开关
# ================================================================

class TestFeatureSwitches:
    """五个 features 开关的装配行为（抽出的工厂函数）。"""

    def test_features_config_defaults_all_true(self) -> None:
        """默认值全 True = 原硬编码行为（保守默认）。"""
        feats = FeaturesConfig()
        assert feats.topic_tracker is True
        assert feats.rest_system is True
        assert feats.user_learning is True
        assert feats.memory_consolidation is True
        assert feats.content_filter is True

    def test_ws_load_features_reads_merged_config(self, tmp_path: Path) -> None:
        """websocket_handler._load_features 读合并配置里的 features 节。"""
        root = _write_conf_project(tmp_path, {
            "topic_tracker": False,
            "rest_system": False,
            "user_learning": False,
            "memory_consolidation": False,
            "content_filter": False,
        })
        feats = ws_module._load_features(project_root=root)
        assert feats.topic_tracker is False
        assert feats.rest_system is False
        assert feats.user_learning is False
        assert feats.memory_consolidation is False
        assert feats.content_filter is False

    def test_ws_load_features_falls_back_to_defaults_on_error(self, tmp_path: Path) -> None:
        """配置缺失/读取失败 → 保守回退全开（= 现状）。"""
        # 空目录：conf.default.yaml 不存在，load_config 抛异常 → 回退默认
        feats = ws_module._load_features(project_root=tmp_path / "no_such_dir")
        assert feats.topic_tracker is True
        assert feats.content_filter is True

    def test_ws_make_topic_tracker_switch(self) -> None:
        """topic_tracker 开=实例、关=None（调用点判空跳过）。"""
        assert ws_module._make_topic_tracker(False) is None
        tracker = ws_module._make_topic_tracker(True)
        assert tracker is not None
        assert hasattr(tracker, "record_message")

    def test_ws_make_content_filter_switch(self) -> None:
        """content_filter 开关直通 ContentFilter.enabled。"""
        assert ws_module._make_content_filter(True).enabled is True
        assert ws_module._make_content_filter(False).enabled is False

    def test_qq_resolve_features_passthrough(self) -> None:
        """qq_handler._resolve_features：显式传入原样返回。"""
        feats = FeaturesConfig(topic_tracker=False, rest_system=False)
        resolved = qq_handler_module._resolve_features(feats)
        assert resolved is feats

    def test_qq_resolve_features_fallback_on_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """qq_handler._resolve_features：未传且读配置失败 → 全默认开关。"""
        import white_salary.infrastructure.config as config_pkg

        def _boom(*args: object, **kwargs: object) -> None:
            raise RuntimeError("配置读取失败（测试注入）")

        monkeypatch.setattr(config_pkg, "load_config", _boom)
        feats = qq_handler_module._resolve_features(None)
        assert feats.topic_tracker is True
        assert feats.rest_system is True

    def test_chat_agent_content_filter_switch(self) -> None:
        """ChatAgent 的 content_filter_enabled 参数直通内部过滤器。"""
        personality = PersonalityManager(project_root=PROJECT_ROOT)

        agent_on = ChatAgent(
            llm=_StubLLM(), personality=personality,
            memory=ShortTermMemory(max_turns=5),
        )
        assert agent_on._content_filter.enabled is True  # 默认=原行为

        agent_off = ChatAgent(
            llm=_StubLLM(), personality=personality,
            memory=ShortTermMemory(max_turns=5),
            content_filter_enabled=False,
        )
        assert agent_off._content_filter.enabled is False


# ================================================================
# 2. 情绪语速
# ================================================================

class TestTTSSpeed:
    """配置基准语速 × 情绪倍率的传递链路。"""

    def test_tts_config_speed_default(self) -> None:
        """TTSConfig.speed 默认 1.0 = 原构造函数默认值（行为不变）。"""
        assert TTSConfig().speed == 1.0

    def test_build_payload_multiplies_speed(self) -> None:
        """请求体 speed_factor = 配置基准语速 × 情绪倍率。"""
        adapter = GPTSoVITSAdapter(speed=1.2)
        payload = adapter._build_payload("你好", speed_multiplier=1.1)
        assert payload["speed_factor"] == pytest.approx(1.32)

    def test_build_payload_default_multiplier_keeps_config_speed(self) -> None:
        """倍率默认 1.0 时 speed_factor = 配置值（与旧版 synthesize 一致）。"""
        adapter = GPTSoVITSAdapter(speed=1.2)
        payload = adapter._build_payload("你好")
        assert payload["speed_factor"] == pytest.approx(1.2)

    def test_build_payload_clamps_speed(self) -> None:
        """最终语速夹紧到 0.25~4.0 安全区间。"""
        adapter = GPTSoVITSAdapter(speed=3.0)
        assert adapter._build_payload("你好", 2.0)["speed_factor"] == pytest.approx(4.0)
        slow = GPTSoVITSAdapter(speed=0.5)
        assert slow._build_payload("你好", 0.1)["speed_factor"] == pytest.approx(0.25)

    async def test_synthesize_with_speed_empty_text_no_http(self) -> None:
        """空文本直接返回空音频，不发起HTTP请求。"""
        adapter = GPTSoVITSAdapter(speed=1.0)
        audio = await adapter.synthesize_with_speed("   ", speed_multiplier=1.1)
        assert audio.samples == b""

    def test_emotion_speed_multiplier_from_tracker(self) -> None:
        """websocket_handler._get_emotion_speed_multiplier 取追踪器的 speed_factor。"""

        class _Tracker:
            def get_tts_modifiers(self) -> dict:
                return {"speed_factor": 0.9, "pitch_hint": "slightly_lower"}

        class _Manager:
            _emotion_tracker = _Tracker()

        class _Agent:
            _memory_manager = _Manager()

        assert ws_module._get_emotion_speed_multiplier(_Agent()) == pytest.approx(0.9)

    def test_emotion_speed_multiplier_defensive_fallbacks(self) -> None:
        """管理器缺失/倍率非法（<=0）→ 保守回退 1.0（= 不调速的原行为）。"""

        class _NoManagerAgent:
            _memory_manager = None

        assert ws_module._get_emotion_speed_multiplier(_NoManagerAgent()) == 1.0

        class _BadTracker:
            def get_tts_modifiers(self) -> dict:
                return {"speed_factor": 0}

        class _BadManager:
            _emotion_tracker = _BadTracker()

        class _BadAgent:
            _memory_manager = _BadManager()

        assert ws_module._get_emotion_speed_multiplier(_BadAgent()) == 1.0


# ================================================================
# 3. 表情映射文件优先/回退
# ================================================================

class TestExpressionMap:
    """config/expression_map.json 实时读取 → 覆盖硬编码 EXPRESSION_MAP。"""

    def test_load_overrides_valid_file(self, tmp_path: Path) -> None:
        """合法文件：返回合法条目，剔除缺 expression 字段的非法条目。"""
        map_file = tmp_path / "expression_map.json"
        map_file.write_text(json.dumps({
            "happy": {"expression": "wink_L", "motion_group": "idle", "mouth_form": 0.2},
            "sad": {"motion_group": "idle"},          # 非法：缺 expression
            "angry": "not_a_dict",                     # 非法：不是 dict
        }, ensure_ascii=False), encoding="utf-8")

        overrides = emotion_tracker_module._load_expression_map_overrides([map_file])
        assert overrides == {
            "happy": {"expression": "wink_L", "motion_group": "idle", "mouth_form": 0.2},
        }

    def test_load_overrides_invalid_json_falls_back(self, tmp_path: Path) -> None:
        """非法JSON/非对象顶层 → 空覆盖（= 回退硬编码现状）。"""
        bad = tmp_path / "expression_map.json"
        bad.write_text("{不是json", encoding="utf-8")
        assert emotion_tracker_module._load_expression_map_overrides([bad]) == {}

        bad.write_text(json.dumps(["happy"]), encoding="utf-8")
        assert emotion_tracker_module._load_expression_map_overrides([bad]) == {}

    def test_load_overrides_missing_file(self, tmp_path: Path) -> None:
        """文件不存在 → 空覆盖。"""
        missing = tmp_path / "no_such.json"
        assert emotion_tracker_module._load_expression_map_overrides([missing]) == {}

    def test_get_expression_command_file_priority_and_fallback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """get_expression_command：文件覆盖优先，文件删除后回退硬编码。"""
        map_file = tmp_path / "expression_map.json"
        monkeypatch.setattr(
            emotion_tracker_module, "_EXPRESSION_MAP_CANDIDATES", [map_file],
        )
        tracker = EmotionTracker(data_dir=str(tmp_path / "emo_data"))
        tracker._current_emotion = "happy"

        # 文件存在且覆盖 happy → 用文件里的映射
        map_file.write_text(json.dumps({
            "happy": {"expression": "wink_L", "motion_group": "idle", "mouth_form": 0.2},
        }, ensure_ascii=False), encoding="utf-8")
        assert tracker.get_expression_command()["expression"] == "wink_L"

        # 文件存在但未覆盖当前情绪 → 硬编码默认
        tracker._current_emotion = "sad"
        assert tracker.get_expression_command() == EmotionTracker.EXPRESSION_MAP["sad"]

        # 文件删除 → 完全回退硬编码
        map_file.unlink()
        tracker._current_emotion = "happy"
        assert tracker.get_expression_command() == EmotionTracker.EXPRESSION_MAP["happy"]


# ================================================================
# 4. HumanLikeFilter 配置合并
# ================================================================

class TestHumanLikeFilterConfig:
    """prompt_templates.json 的 human_like_filter 节合并进过滤器。"""

    def _write_config(self, tmp_path: Path, section: dict) -> Path:
        path = tmp_path / "prompt_templates.json"
        path.write_text(
            json.dumps({"human_like_filter": section}, ensure_ascii=False),
            encoding="utf-8",
        )
        return path

    def test_banned_words_deleted(self, tmp_path: Path) -> None:
        """banned_words 编译成删除正则，逐词从回复里删除。"""
        cfg = self._write_config(tmp_path, {"banned_words": ["咩"]})
        f = HumanLikeFilter(config_path=cfg)
        assert "咩" not in f.filter_response("你好咩，今天咩怎么样")

    def test_banned_lecture_merged_into_detection(self, tmp_path: Path) -> None:
        """banned_lecture 并入说教句式检测。"""
        cfg = self._write_config(tmp_path, {"banned_lecture": ["测试禁用句式"]})
        f = HumanLikeFilter(config_path=cfg)
        assert f.check_lecture_tone("这是测试禁用句式哦") is True
        assert f.check_lecture_tone("普通的一句话") is False

    def test_invalid_regex_degrades_to_literal(self, tmp_path: Path) -> None:
        """非法正则的 banned_lecture 退化为字面量匹配，不炸过滤器。"""
        cfg = self._write_config(tmp_path, {"banned_lecture": ["(["]})
        f = HumanLikeFilter(config_path=cfg)
        assert f.check_lecture_tone("这里有([符号") is True

    def test_missing_config_keeps_hardcoded_behavior(self, tmp_path: Path) -> None:
        """配置缺失 = 纯硬编码现状（自定义词不删、自定义句式不检）。"""
        missing = tmp_path / "no_such.json"
        f = HumanLikeFilter(config_path=missing)
        assert "咩" in f.filter_response("你好咩，今天怎么样")
        assert f.check_lecture_tone("这是测试禁用句式哦") is False
        # 硬编码规则仍然生效（波浪号删除）
        assert "~" not in f.filter_response("好的~没问题~")


# ================================================================
# 5. 长期记忆引擎开关
# ================================================================

class TestLongTermProvider:
    """LongTermMemoryStore 的 provider 参数与共享实例缓存隔离。"""

    def test_provider_none_skips_chroma(self, tmp_path: Path) -> None:
        """provider='none' 跳过 Chroma 初始化，只用关键词检索。"""
        store = LongTermMemoryStore(data_dir=str(tmp_path), provider="none")
        assert store._chroma_collection is None
        assert store._provider == "none"

        # 关键词检索路径可用
        store.add(content="今天吃了辣子鸡丁", keywords="辣子鸡丁", layer="event")
        results = store.search("辣子鸡丁")
        assert len(results) == 1
        assert "辣子鸡丁" in results[0].content

    def test_cache_key_isolated_by_provider(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """同一 data_dir 不同 provider 不复用实例；同 provider 复用。"""
        # 避免真的建 Chroma 库：让 chroma 分支不可用（不影响本测试目的）
        monkeypatch.setattr(long_term_store_module, "CHROMADB_AVAILABLE", False)
        a = LongTermMemoryStore(data_dir=str(tmp_path), provider="none")
        b = LongTermMemoryStore(data_dir=str(tmp_path), provider="chroma")
        c = LongTermMemoryStore(data_dir=str(tmp_path), provider="none")
        assert a is not b
        assert a is c

    def test_default_provider_injection(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """set_default_long_term_provider 注入后，不传 provider 的构造跟随默认值。"""
        # 用 monkeypatch 保证测试结束后模块级默认值自动还原
        monkeypatch.setattr(long_term_store_module, "_DEFAULT_PROVIDER", "chroma")
        set_default_long_term_provider("none")
        assert long_term_store_module._DEFAULT_PROVIDER == "none"
        store = LongTermMemoryStore(data_dir=str(tmp_path))
        assert store._provider == "none"
        assert store._chroma_collection is None

        # 空串/None 保守回退 chroma（= 原行为）
        set_default_long_term_provider("")
        assert long_term_store_module._DEFAULT_PROVIDER == "chroma"


# ================================================================
# 6. PersonalityManager 热重载
# ================================================================

class TestPersonalityReload:
    """reload() 重读提示词文件（设置面板保存人设后热更新）。"""

    def test_reload_picks_up_file_changes(self, tmp_path: Path) -> None:
        prompt_file = tmp_path / "prompts" / "system_prompt.txt"
        prompt_file.parent.mkdir(parents=True)
        prompt_file.write_text("版本1的人设", encoding="utf-8")

        manager = PersonalityManager(project_root=tmp_path)
        assert manager.system_prompt == "版本1的人设"

        prompt_file.write_text("版本2的人设", encoding="utf-8")
        assert manager.reload() is True
        assert manager.system_prompt == "版本2的人设"

    def test_reload_keeps_prompt_when_file_missing(self, tmp_path: Path) -> None:
        """文件被删时 reload 返回 False 且保留当前提示词（不换成内置默认）。"""
        prompt_file = tmp_path / "prompts" / "system_prompt.txt"
        prompt_file.parent.mkdir(parents=True)
        prompt_file.write_text("完整精调人设", encoding="utf-8")

        manager = PersonalityManager(project_root=tmp_path)
        prompt_file.unlink()
        assert manager.reload() is False
        assert manager.system_prompt == "完整精调人设"
