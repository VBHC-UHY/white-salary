"""
测试LLM适配器和工厂。
"""

import pytest
from pathlib import Path

from white_salary.core.exceptions import ConfigError
from white_salary.core.interfaces.llm import LLMInterface
from white_salary.adapters.llm.factory import create_llm, PRESET_PROVIDERS
from white_salary.infrastructure.config.models import LLMConfig


class TestLLMFactory:
    """测试LLM工厂。"""

    def test_preset_providers_not_empty(self) -> None:
        """预置提供商列表不为空。"""
        assert len(PRESET_PROVIDERS) > 0

    def test_create_llm_returns_interface(self) -> None:
        """工厂创建的对象实现了LLMInterface。"""
        config = LLMConfig(
            provider="siliconflow",
            api_key="test-key",
            model="test-model",
        )
        llm = create_llm(config)
        assert isinstance(llm, LLMInterface)

    def test_create_llm_with_custom_base_url(self) -> None:
        """支持自定义base_url。"""
        config = LLMConfig(
            provider="custom",
            api_key="test-key",
            model="test-model",
            base_url="https://my-custom-api.com/v1",
        )
        llm = create_llm(config)
        assert isinstance(llm, LLMInterface)

    def test_unknown_provider_without_base_url_raises_error(self) -> None:
        """未知提供商且没有base_url时报错。"""
        config = LLMConfig(
            provider="unknown_provider_xyz",
            api_key="test-key",
            model="test-model",
        )
        with pytest.raises(ConfigError):
            create_llm(config)

    def test_missing_api_key_raises_error(self) -> None:
        """缺少API密钥时报错。"""
        config = LLMConfig(
            provider="siliconflow",
            api_key="",
            model="test-model",
        )
        with pytest.raises(ConfigError):
            create_llm(config)

    def test_all_preset_providers_have_required_fields(self) -> None:
        """所有预置提供商都有必要字段。"""
        for name, info in PRESET_PROVIDERS.items():
            assert "base_url" in info, f"{name} 缺少 base_url"
            assert "default_model" in info, f"{name} 缺少 default_model"
            assert info["base_url"], f"{name} 的 base_url 为空"
            assert info["default_model"], f"{name} 的 default_model 为空"
