"""
配置系统归一（2026-07-03 审计修复批5）的单元测试。

覆盖三块（依据 docs/audit-2026-07-02/config-audit.json）：
  1. AppConfig 补齐的 10 节配置（7个角色LLM + qq + auto_chat + features）
     能被 load_config 正确解析，不再被 Pydantic 静默丢弃；
  2. 深合并的"None 覆盖保护"——用户把某节写成空(None)时跳过覆盖并保留默认值；
  3. TTS/ASR 配置默认值 == 旧 run_server.py 硬编码值（防回归黄金测试：
     配置化不许改变现有行为）。
"""

from pathlib import Path

from white_salary.infrastructure.config.loader import _deep_merge, load_config
from white_salary.infrastructure.config.models import (
    AppConfig,
    ASRConfig,
    AutoChatConfig,
    FeaturesConfig,
    QQConfig,
    RoleLLMConfig,
    TTSConfig,
)

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent.parent

# 七个角色 LLM 在 AppConfig 上的字段名
ROLE_LLM_KEYS = (
    "llm_tool",
    "llm_memory",
    "llm_emotion",
    "llm_vision",
    "llm_postprocess",
    "llm_detect",
    "llm_background",
)


def _write_minimal_default(tmp_path: Path, extra: str = "") -> None:
    """写一份最小可用的 conf.default.yaml 到临时目录。"""
    (tmp_path / "conf.default.yaml").write_text(
        "system:\n  name: \"White Salary\"\n" + extra,
        encoding="utf-8",
    )


class TestRoleLLMConfig:
    """RoleLLMConfig 解析测试。"""

    def test_default_role_llm_is_unconfigured(self) -> None:
        """默认值全空字符串 == '该角色未配置'（run_server 不会创建适配器）。"""
        role = RoleLLMConfig()
        assert role.provider == ""
        assert role.api_key == ""
        assert role.model == ""
        assert role.base_url == ""

    def test_appconfig_has_all_seven_role_llms(self) -> None:
        """AppConfig 必须有全部 7 个角色 LLM 字段（getattr 供 run_server 消费）。"""
        config = AppConfig()
        for key in ROLE_LLM_KEYS:
            role = getattr(config, key)
            assert isinstance(role, RoleLLMConfig), f"{key} 应为 RoleLLMConfig"

    def test_role_llm_parsed_from_user_yaml(self, tmp_path: Path) -> None:
        """用户 conf.yaml 里的角色 LLM 配置不再被 Pydantic 静默丢弃。"""
        _write_minimal_default(tmp_path)
        (tmp_path / "conf.yaml").write_text(
            "llm_tool:\n"
            "  provider: deepseek\n"
            "  api_key: sk-test-123\n"
            "  model: deepseek-chat\n"
            "  base_url: https://api.deepseek.com/v1\n",
            encoding="utf-8",
        )
        config = load_config(project_root=tmp_path)
        assert config.llm_tool.provider == "deepseek"
        assert config.llm_tool.api_key == "sk-test-123"
        assert config.llm_tool.model == "deepseek-chat"
        assert config.llm_tool.base_url == "https://api.deepseek.com/v1"
        # 没配置的角色保持默认（未配置状态）
        assert config.llm_memory.api_key == ""

    def test_user_role_llm_overrides_default(self, tmp_path: Path) -> None:
        """深合并：用户只改 model 时，默认节的其余字段保留。"""
        _write_minimal_default(
            tmp_path,
            "llm_memory:\n"
            "  provider: siliconflow\n"
            "  api_key: \"\"\n"
            "  model: old-model\n"
            "  base_url: https://api.siliconflow.cn/v1\n",
        )
        (tmp_path / "conf.yaml").write_text(
            "llm_memory:\n  model: new-model\n",
            encoding="utf-8",
        )
        config = load_config(project_root=tmp_path)
        assert config.llm_memory.model == "new-model"
        assert config.llm_memory.provider == "siliconflow"  # 默认值保留
        assert config.llm_memory.base_url == "https://api.siliconflow.cn/v1"


class TestQQConfig:
    """QQConfig 解析测试。"""

    def test_defaults_match_old_hardcoded(self) -> None:
        """默认值必须与 run_server 旧 yaml 旁路的 .get() 默认值一致（防回归）。"""
        qq = QQConfig()
        assert qq.enabled is False
        assert qq.ws_url == "ws://127.0.0.1:3001"
        assert qq.bot_name == "白"
        assert qq.token == ""
        assert qq.family_qq == []
        assert qq.owner_name == ""

    def test_parsed_from_user_yaml_with_int_qq(self, tmp_path: Path) -> None:
        """family_qq 写成 int 列表（conf.yaml 的实际写法）应正常解析。"""
        _write_minimal_default(tmp_path)
        (tmp_path / "conf.yaml").write_text(
            "qq:\n"
            "  enabled: true\n"
            "  ws_url: ws://127.0.0.1:3004\n"
            "  bot_name: 白\n"
            "  token: abc\n"
            "  family_qq:\n"
            "    - 1234567890\n",
            encoding="utf-8",
        )
        config = load_config(project_root=tmp_path)
        assert config.qq.enabled is True
        assert config.qq.ws_url == "ws://127.0.0.1:3004"
        assert config.qq.token == "abc"
        # int 应被保留（下游 qq_handler 会统一 str() 转换）
        assert str(config.qq.family_qq[0]) == "1234567890"


class TestAutoChatAndFeatures:
    """auto_chat / features 节解析测试。"""

    def test_auto_chat_defaults(self) -> None:
        """默认值与 conf.default.yaml 的 auto_chat 节一致。"""
        ac = AutoChatConfig()
        assert ac.enabled is True
        assert ac.morning_greeting is True
        assert ac.night_greeting is True
        assert ac.care_reminder is True
        assert ac.random_chat is True
        assert ac.daily_limit == 3

    def test_features_defaults(self) -> None:
        """默认值与 conf.default.yaml 的 features 节一致（注：暂无消费方）。"""
        feats = FeaturesConfig()
        assert feats.topic_tracker is True
        assert feats.rest_system is True
        assert feats.user_learning is True
        assert feats.memory_consolidation is True
        assert feats.content_filter is True

    def test_parsed_from_user_yaml(self, tmp_path: Path) -> None:
        """用户配置能覆盖 auto_chat/features。"""
        _write_minimal_default(tmp_path)
        (tmp_path / "conf.yaml").write_text(
            "auto_chat:\n  enabled: false\n  daily_limit: 5\n"
            "features:\n  topic_tracker: false\n",
            encoding="utf-8",
        )
        config = load_config(project_root=tmp_path)
        assert config.auto_chat.enabled is False
        assert config.auto_chat.daily_limit == 5
        assert config.auto_chat.morning_greeting is True  # 未覆盖的保持默认
        assert config.features.topic_tracker is False
        assert config.features.rest_system is True


class TestNoneOverrideProtection:
    """None 覆盖保护测试——用户把某节写成空时不许整节抹掉默认值。"""

    def test_deep_merge_skips_none_over_dict(self) -> None:
        """override 里某键为 None 且 base 对应值是字典 → 跳过覆盖。"""
        base = {"llm_tool": {"model": "deepseek-chat", "api_key": "k"}}
        override = {"llm_tool": None}
        result = _deep_merge(base, override)
        assert result["llm_tool"] == {"model": "deepseek-chat", "api_key": "k"}

    def test_deep_merge_none_over_scalar_still_overrides(self) -> None:
        """base 对应值不是字典时，None 照旧覆盖（保持旧行为，交给Pydantic校验）。"""
        base = {"a": 1}
        override = {"a": None}
        result = _deep_merge(base, override)
        assert result["a"] is None

    def test_deep_merge_nested_none_protected(self) -> None:
        """嵌套层级的 None 同样受保护（递归生效）。"""
        base = {"outer": {"inner": {"x": 1}, "y": 2}}
        override = {"outer": {"inner": None}}
        result = _deep_merge(base, override)
        assert result["outer"]["inner"] == {"x": 1}
        assert result["outer"]["y"] == 2

    def test_load_config_empty_section_keeps_default(self, tmp_path: Path) -> None:
        """conf.yaml 里只写一行 `llm_tool:`（YAML解析为None）→ 默认节保留，不抛异常。"""
        _write_minimal_default(
            tmp_path,
            "llm_tool:\n"
            "  provider: deepseek\n"
            "  api_key: \"\"\n"
            "  model: deepseek-chat\n"
            "  base_url: https://api.deepseek.com/v1\n",
        )
        (tmp_path / "conf.yaml").write_text("llm_tool:\n", encoding="utf-8")
        config = load_config(project_root=tmp_path)
        assert config.llm_tool.model == "deepseek-chat"  # 默认值未被 None 抹掉
        assert config.llm_tool.provider == "deepseek"


class TestTTSASRGoldenDefaults:
    """TTS/ASR 配置默认值黄金测试——必须逐字等于旧 run_server.py 硬编码值。

    这些字面量以前硬编码在 run_server.py 第295-380行，批5把它们搬进配置默认值；
    本测试保证"配置化"没有偷偷改变行为。若要改音色/模型请改配置文件，不许改这里。
    """

    def test_tts_defaults_equal_old_hardcoded(self) -> None:
        tts = TTSConfig()
        assert tts.local_api_url == "http://127.0.0.1:9880"
        assert tts.ref_audio == "assets/tts/ref_default.wav"
        assert tts.ref_text == "你怎么不会想让我去试辣子鸡丁吧"
        assert tts.fallback_provider == "siliconflow"
        assert tts.fallback_api_key == ""  # 空=沿用"扫角色配置找密钥"兜底
        assert tts.fallback_model == "FunAudioLLM/CosyVoice2-0.5B"
        # 2026-07-03 有意变更（开源修复）：原默认值是项目作者账号专属的克隆音色，
        # 其他账号的 key 调用会失败（开箱即坏）。默认改为公共预置音色；
        # 作者的专属音色已显式写入其本地 conf.yaml，行为不变。
        assert tts.fallback_voice == "FunAudioLLM/CosyVoice2-0.5B:anna"

    def test_asr_defaults_equal_old_hardcoded(self) -> None:
        asr = ASRConfig()
        assert asr.provider == "siliconflow"
        assert asr.api_key == ""  # 空=沿用"扫角色配置找密钥"兜底
        assert asr.model == "FunAudioLLM/SenseVoiceSmall"

    def test_real_project_config_tts_asr(self) -> None:
        """真实项目配置加载后 tts/asr 结构完整（音色允许用户 conf.yaml 覆盖）。"""
        config = load_config(project_root=PROJECT_ROOT)
        # conf.yaml 里的旧 tts 键（provider/voice/rate）是被忽略的死键，
        # 新字段来自 conf.default.yaml 的默认值 + 用户 conf.yaml 的覆盖
        assert config.tts.local_api_url == "http://127.0.0.1:9880"
        assert config.tts.fallback_model == "FunAudioLLM/CosyVoice2-0.5B"
        # 2026-07-03 修改：不再断言具体音色值——它是用户可覆盖项（项目作者
        # 的 conf.yaml 就覆盖成了自己的专属克隆音色），且 conf.yaml 被
        # gitignore，clone 用户的取值会不同。只断言"非空且格式像音色ID"。
        assert config.tts.fallback_voice and ":" in config.tts.fallback_voice
        assert config.asr.model == "FunAudioLLM/SenseVoiceSmall"

    def test_real_project_config_roles_parsed(self) -> None:
        """真实项目配置加载后角色 LLM 不再被丢弃（qq 节同理）。"""
        config = load_config(project_root=PROJECT_ROOT)
        for key in ROLE_LLM_KEYS:
            assert isinstance(getattr(config, key), RoleLLMConfig)
        assert isinstance(config.qq, QQConfig)
        assert isinstance(config.auto_chat, AutoChatConfig)
        assert isinstance(config.features, FeaturesConfig)
