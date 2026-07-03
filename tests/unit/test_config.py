"""
测试配置管理系统。

验证配置加载、合并、验证功能。
"""

from pathlib import Path

import pytest

from white_salary.core.exceptions import ConfigFileNotFoundError, ConfigValidationError
from white_salary.infrastructure.config.loader import _deep_merge, load_config
from white_salary.infrastructure.config.models import AppConfig


# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent.parent


class TestDeepMerge:
    """测试深度合并功能。"""

    def test_simple_merge(self) -> None:
        """简单值合并：override覆盖base。"""
        base = {"a": 1, "b": 2}
        override = {"b": 3, "c": 4}
        result = _deep_merge(base, override)
        assert result == {"a": 1, "b": 3, "c": 4}

    def test_nested_merge(self) -> None:
        """嵌套字典：递归合并而不是直接覆盖。"""
        base = {"server": {"host": "localhost", "port": 12400}}
        override = {"server": {"port": 8080}}
        result = _deep_merge(base, override)
        # host保留默认值，port被覆盖
        assert result["server"]["host"] == "localhost"
        assert result["server"]["port"] == 8080

    def test_does_not_modify_original(self) -> None:
        """合并不修改原始字典。"""
        base = {"a": 1}
        override = {"a": 2}
        _deep_merge(base, override)
        assert base["a"] == 1  # 原字典未被修改


class TestAppConfigModel:
    """测试 Pydantic 配置模型。"""

    def test_default_config(self) -> None:
        """使用默认值创建配置。"""
        config = AppConfig()
        assert config.system.name == "White Salary"
        assert config.server.port == 12400
        assert config.llm.provider == "openai"
        assert config.llm.temperature == 0.7

    def test_custom_config(self) -> None:
        """使用自定义值创建配置。"""
        config = AppConfig(
            system={"name": "Test", "debug": True},
            server={"port": 9999},
        )
        assert config.system.name == "Test"
        assert config.system.debug is True
        assert config.server.port == 9999

    def test_port_validation(self) -> None:
        """端口号必须在有效范围内（1-65535）。"""
        with pytest.raises(Exception):  # Pydantic验证错误
            AppConfig(server={"port": 0})
        with pytest.raises(Exception):
            AppConfig(server={"port": 70000})

    def test_temperature_validation(self) -> None:
        """temperature 必须在 0-2 之间。"""
        with pytest.raises(Exception):
            AppConfig(llm={"temperature": -1.0})
        with pytest.raises(Exception):
            AppConfig(llm={"temperature": 3.0})


class TestLoadConfig:
    """测试配置文件加载。"""

    def test_load_default_config(self) -> None:
        """能正确加载默认配置文件。"""
        config = load_config(project_root=PROJECT_ROOT)
        assert isinstance(config, AppConfig)
        assert config.system.name == "White Salary"

    def test_missing_default_config_raises_error(self, tmp_path: Path) -> None:
        """默认配置文件不存在时抛出异常。"""
        with pytest.raises(ConfigFileNotFoundError):
            load_config(project_root=tmp_path)

    def test_user_config_overrides_default(self, tmp_path: Path) -> None:
        """用户配置能正确覆盖默认配置（用临时配置，不依赖仓库里 conf.yaml 的实际内容）。"""
        (tmp_path / "conf.default.yaml").write_text("system:\n  debug: false\n", encoding="utf-8")
        (tmp_path / "conf.yaml").write_text("system:\n  debug: true\n", encoding="utf-8")
        config = load_config(project_root=tmp_path)
        assert config.system.debug is True
