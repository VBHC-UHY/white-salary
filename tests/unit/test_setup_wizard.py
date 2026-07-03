"""
tests/unit/test_setup_wizard.py

配置向导纯函数层的单元测试（2026-07-03 新手体验（批10））。

只测 wizard_core 纯函数（ensure_conf / set_yaml_scalar / write_key_to_conf /
test_connection 的离线分支），完全不碰 tkinter 界面、不联网。
"""

import sys
from pathlib import Path

import pytest
import yaml

# scripts/ 不是包，这里把它加进 sys.path 以便导入向导模块
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import setup_wizard  # noqa: E402


# =============================================================================
# 测试用的迷你 conf 模板（结构与 conf.default.yaml 一致，带注释验证"注释保留"）
# =============================================================================
MINI_CONF = """\
# 顶部说明注释——写入后必须还在
system:
  name: "White Salary"

# llm 节注释
llm:
  provider: "siliconflow"
  api_key: ""
  model: "deepseek-ai/DeepSeek-V3.2"
  base_url: "https://api.siliconflow.cn/v1"
  temperature: 0.7

llm_vision:
  provider: "siliconflow"
  api_key: ""
  model: "Qwen/Qwen3-VL-8B-Instruct"
  base_url: "https://api.siliconflow.cn/v1"

server:
  port: 12400
"""


@pytest.fixture
def fake_project(tmp_path: Path) -> Path:
    """造一个假项目根：有 conf.default.yaml 模板，没有 conf.yaml。"""
    (tmp_path / "conf.default.yaml").write_text(MINI_CONF, encoding="utf-8")
    return tmp_path


# =============================================================================
# ensure_conf：conf 缺失自动复制
# =============================================================================
class TestEnsureConf:
    def test_missing_conf_copied_from_default(self, fake_project: Path):
        """conf.yaml 不存在时，自动从 conf.default.yaml 复制一份。"""
        conf_path = setup_wizard.ensure_conf(fake_project)
        assert conf_path == fake_project / "conf.yaml"
        assert conf_path.exists()
        assert conf_path.read_text(encoding="utf-8") == MINI_CONF

    def test_existing_conf_untouched(self, fake_project: Path):
        """conf.yaml 已存在时原样保留，不被模板覆盖。"""
        existing = fake_project / "conf.yaml"
        existing.write_text("llm:\n  api_key: \"old-key\"\n", encoding="utf-8")
        conf_path = setup_wizard.ensure_conf(fake_project)
        assert conf_path.read_text(encoding="utf-8") == "llm:\n  api_key: \"old-key\"\n"

    def test_missing_default_raises_chinese_error(self, tmp_path: Path):
        """连模板都没有：抛 FileNotFoundError 且带中文提示。"""
        with pytest.raises(FileNotFoundError) as exc_info:
            setup_wizard.ensure_conf(tmp_path)
        assert "conf.default.yaml" in str(exc_info.value)


# =============================================================================
# ensure_system_prompt：提示词缺失自动复制
# =============================================================================
class TestEnsureSystemPrompt:
    def test_copied_from_example(self, tmp_path: Path):
        """system_prompt.txt 不存在但 example 在：复制并返回 True。"""
        prompts = tmp_path / "prompts"
        prompts.mkdir()
        (prompts / "system_prompt.example.txt").write_text("你是白", encoding="utf-8")
        assert setup_wizard.ensure_system_prompt(tmp_path) is True
        assert (prompts / "system_prompt.txt").read_text(encoding="utf-8") == "你是白"

    def test_existing_untouched(self, tmp_path: Path):
        """system_prompt.txt 已存在：不覆盖，返回 False。"""
        prompts = tmp_path / "prompts"
        prompts.mkdir()
        (prompts / "system_prompt.example.txt").write_text("模板", encoding="utf-8")
        (prompts / "system_prompt.txt").write_text("用户自己的人格", encoding="utf-8")
        assert setup_wizard.ensure_system_prompt(tmp_path) is False
        assert (prompts / "system_prompt.txt").read_text(encoding="utf-8") == "用户自己的人格"

    def test_no_example_returns_false(self, tmp_path: Path):
        """example 也没有：静默返回 False，不抛异常（不阻塞配置流程）。"""
        assert setup_wizard.ensure_system_prompt(tmp_path) is False


# =============================================================================
# set_yaml_scalar：按行替换、注释保留、缺节缺键自愈
# =============================================================================
class TestSetYamlScalar:
    def test_replace_existing_key(self):
        """替换已有键，且其它行（含注释）原样保留。"""
        out = setup_wizard.set_yaml_scalar(MINI_CONF, "llm", "api_key", "sk-abc")
        parsed = yaml.safe_load(out)
        assert parsed["llm"]["api_key"] == "sk-abc"
        # 注释必须还在（这是不用 yaml.dump 回写的全部意义）
        assert "# 顶部说明注释——写入后必须还在" in out
        assert "# llm 节注释" in out
        # 其它节不受影响
        assert parsed["server"]["port"] == 12400
        assert parsed["llm_vision"]["api_key"] == ""

    def test_only_target_section_modified(self):
        """llm 和 llm_vision 同名键互不串扰（llm_vision 以 llm 开头，易误匹配）。"""
        out = setup_wizard.set_yaml_scalar(MINI_CONF, "llm_vision", "api_key", "sk-v")
        parsed = yaml.safe_load(out)
        assert parsed["llm_vision"]["api_key"] == "sk-v"
        assert parsed["llm"]["api_key"] == ""

    def test_missing_key_inserted(self):
        """节里没有这个键：紧跟节头插入。"""
        out = setup_wizard.set_yaml_scalar(MINI_CONF, "server", "host", "localhost")
        parsed = yaml.safe_load(out)
        assert parsed["server"]["host"] == "localhost"
        assert parsed["server"]["port"] == 12400

    def test_missing_section_appended(self):
        """整节缺失：在文末追加，YAML 仍可解析。"""
        out = setup_wizard.set_yaml_scalar(MINI_CONF, "llm_tool", "api_key", "sk-t")
        parsed = yaml.safe_load(out)
        assert parsed["llm_tool"]["api_key"] == "sk-t"
        assert parsed["llm"]["api_key"] == ""

    def test_special_characters_escaped(self):
        """值里带双引号 / 反斜杠也不会写坏 YAML。"""
        tricky = 'sk-a"b\\c'
        out = setup_wizard.set_yaml_scalar(MINI_CONF, "llm", "api_key", tricky)
        parsed = yaml.safe_load(out)
        assert parsed["llm"]["api_key"] == tricky


# =============================================================================
# write_key_to_conf：主通道写入 + 供应商预设 + 硅基流动顺手填视觉
# =============================================================================
class TestWriteKeyToConf:
    def test_siliconflow_fills_llm_and_vision(self, fake_project: Path):
        """选硅基流动：llm 四件套按预设写入，同一把 key 顺手填进 llm_vision。"""
        conf_path = setup_wizard.ensure_conf(fake_project)
        summary = setup_wizard.write_key_to_conf(conf_path, "siliconflow", "  sk-sf-123  ")

        parsed = yaml.safe_load(conf_path.read_text(encoding="utf-8"))
        presets = setup_wizard.get_provider_presets()
        assert parsed["llm"]["api_key"] == "sk-sf-123"  # 首尾空白已去除
        assert parsed["llm"]["provider"] == "siliconflow"
        assert parsed["llm"]["base_url"] == presets["siliconflow"]["base_url"]
        assert parsed["llm"]["model"] == presets["siliconflow"]["default_model"]
        # 同一把硅基流动 key 让"看图"直接可用
        assert parsed["llm_vision"]["api_key"] == "sk-sf-123"
        # llm_vision 的看图模型不能被主对话模型覆盖
        assert parsed["llm_vision"]["model"] == "Qwen/Qwen3-VL-8B-Instruct"
        assert summary["vision_filled"] == "yes"

    def test_deepseek_does_not_touch_vision(self, fake_project: Path):
        """选 DeepSeek：只写主通道，llm_vision 不动（那是硅基流动的预设通道）。"""
        conf_path = setup_wizard.ensure_conf(fake_project)
        summary = setup_wizard.write_key_to_conf(conf_path, "deepseek", "sk-ds-1")

        parsed = yaml.safe_load(conf_path.read_text(encoding="utf-8"))
        presets = setup_wizard.get_provider_presets()
        assert parsed["llm"]["provider"] == "deepseek"
        assert parsed["llm"]["api_key"] == "sk-ds-1"
        assert parsed["llm"]["base_url"] == presets["deepseek"]["base_url"]
        assert parsed["llm"]["model"] == presets["deepseek"]["default_model"]
        assert parsed["llm_vision"]["api_key"] == ""  # 不串供应商
        assert summary["vision_filled"] == "no"

    def test_comments_preserved_after_write(self, fake_project: Path):
        """写入后 conf.yaml 的中文注释仍然保留（新手以后照着注释改配置）。"""
        conf_path = setup_wizard.ensure_conf(fake_project)
        setup_wizard.write_key_to_conf(conf_path, "siliconflow", "sk-x")
        text = conf_path.read_text(encoding="utf-8")
        assert "# 顶部说明注释——写入后必须还在" in text
        assert "# llm 节注释" in text

    def test_unknown_provider_raises(self, fake_project: Path):
        """不认识的供应商：抛 ValueError（中文提示），且不改动文件。"""
        conf_path = setup_wizard.ensure_conf(fake_project)
        before = conf_path.read_text(encoding="utf-8")
        with pytest.raises(ValueError, match="未知的供应商"):
            setup_wizard.write_key_to_conf(conf_path, "not-a-provider", "sk-1")
        assert conf_path.read_text(encoding="utf-8") == before

    def test_empty_key_raises(self, fake_project: Path):
        """空 key（含纯空白）：抛 ValueError，不写文件。"""
        conf_path = setup_wizard.ensure_conf(fake_project)
        with pytest.raises(ValueError, match="空"):
            setup_wizard.write_key_to_conf(conf_path, "siliconflow", "   ")

    def test_write_against_real_default_template(self, tmp_path: Path):
        """用项目真实的 conf.default.yaml 走一遍完整流程，确保和真模板兼容。"""
        real_default = PROJECT_ROOT / "conf.default.yaml"
        (tmp_path / "conf.default.yaml").write_text(
            real_default.read_text(encoding="utf-8"), encoding="utf-8"
        )
        conf_path = setup_wizard.ensure_conf(tmp_path)
        setup_wizard.write_key_to_conf(conf_path, "siliconflow", "sk-real-test")

        parsed = yaml.safe_load(conf_path.read_text(encoding="utf-8"))
        assert parsed["llm"]["api_key"] == "sk-real-test"
        assert parsed["llm_vision"]["api_key"] == "sk-real-test"
        # 真模板里的其它配置不能被破坏
        assert parsed["server"]["port"] == 12400
        assert parsed["tts"]["fallback_provider"] == "siliconflow"
        # 模板头部的安全警告注释必须保留
        assert "不要直接修改这个文件" in conf_path.read_text(encoding="utf-8")


# =============================================================================
# 供应商预设 & test_connection 的离线分支
# =============================================================================
class TestProvidersAndConnection:
    def test_wizard_providers_all_have_presets(self):
        """向导展示的每家供应商都必须能查到 base_url / 默认模型预设。"""
        presets = setup_wizard.get_provider_presets()
        for pid, label, url in setup_wizard.WIZARD_PROVIDERS:
            assert pid in presets, f"{label} 缺预设"
            assert presets[pid]["base_url"].startswith("http")
            assert presets[pid]["default_model"]
            assert url.startswith("https://")

    def test_presets_come_from_factory_single_source(self):
        """预设优先读 factory.PRESET_PROVIDERS（单一事实来源，测试环境依赖齐全时必须一致）。"""
        from white_salary.adapters.llm.factory import PRESET_PROVIDERS
        assert setup_wizard.get_provider_presets() is PRESET_PROVIDERS

    def test_connection_empty_key_offline(self):
        """空 key：不联网直接返回失败 + 中文原因。"""
        ok, msg = setup_wizard.test_connection("siliconflow", "")
        assert ok is False
        assert "API Key" in msg

    def test_connection_unknown_provider_offline(self):
        """未知供应商：不联网直接返回失败。"""
        ok, msg = setup_wizard.test_connection("nope", "sk-1")
        assert ok is False
        assert "未知" in msg
