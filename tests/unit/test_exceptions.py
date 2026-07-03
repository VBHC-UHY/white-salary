"""
测试自定义异常体系。

验证异常类的继承关系和功能。
"""

import pytest

from white_salary.core.exceptions import (
    ASRError,
    ASRModelNotLoadedError,
    AvatarError,
    ConfigError,
    ConfigFileNotFoundError,
    ConfigValidationError,
    LLMAuthenticationError,
    LLMConnectionError,
    LLMError,
    LLMRateLimitError,
    LLMResponseError,
    PipelineError,
    SessionError,
    SingingError,
    StorageError,
    TTSError,
    VisionError,
    WhiteSalaryError,
)


class TestExceptionHierarchy:
    """测试异常继承关系。"""

    def test_all_inherit_from_base(self) -> None:
        """所有自定义异常都必须继承 WhiteSalaryError。"""
        exceptions = [
            ConfigError, ConfigFileNotFoundError, ConfigValidationError,
            LLMError, LLMConnectionError, LLMAuthenticationError,
            LLMRateLimitError, LLMResponseError,
            ASRError, ASRModelNotLoadedError,
            TTSError, VisionError, SingingError, AvatarError,
            StorageError, SessionError, PipelineError,
        ]
        for exc_class in exceptions:
            assert issubclass(exc_class, WhiteSalaryError), (
                f"{exc_class.__name__} 没有继承 WhiteSalaryError"
            )

    def test_llm_errors_inherit_from_llm_error(self) -> None:
        """LLM相关异常必须继承 LLMError。"""
        for exc_class in [LLMConnectionError, LLMAuthenticationError, LLMRateLimitError]:
            assert issubclass(exc_class, LLMError)

    def test_config_errors_inherit_from_config_error(self) -> None:
        """配置相关异常必须继承 ConfigError。"""
        for exc_class in [ConfigFileNotFoundError, ConfigValidationError]:
            assert issubclass(exc_class, ConfigError)


class TestExceptionFeatures:
    """测试异常功能。"""

    def test_error_message(self) -> None:
        """异常必须能携带错误消息。"""
        error = WhiteSalaryError("出错了")
        assert str(error) == "出错了"
        assert error.message == "出错了"

    def test_error_details(self) -> None:
        """异常必须能携带详情字典。"""
        error = LLMConnectionError(
            "连接失败",
            details={"host": "api.openai.com", "status": 500},
        )
        assert error.details["host"] == "api.openai.com"
        assert error.details["status"] == 500

    def test_default_details_is_empty_dict(self) -> None:
        """没有提供details时，默认是空字典。"""
        error = WhiteSalaryError("测试")
        assert error.details == {}

    def test_catch_by_base_class(self) -> None:
        """可以用基类捕获所有子类异常。"""
        with pytest.raises(WhiteSalaryError):
            raise LLMConnectionError("连接失败")

    def test_catch_by_mid_class(self) -> None:
        """可以用中间类捕获其子类异常。"""
        with pytest.raises(LLMError):
            raise LLMRateLimitError("限流了")
