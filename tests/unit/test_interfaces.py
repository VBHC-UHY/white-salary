"""
测试核心接口定义。

验证所有接口类能正确导入，且有正确的抽象方法定义。
"""

import inspect
from abc import ABC


class TestInterfaceImports:
    """测试所有接口能正确导入。"""

    def test_import_all_interfaces(self) -> None:
        """从 core.interfaces 包导入所有接口。"""
        from white_salary.core.interfaces import (
            ASRInterface,
            AvatarInterface,
            KeyValueStorageInterface,
            LLMInterface,
            SingingInterface,
            TTSInterface,
            VADInterface,
            VectorStorageInterface,
            VisionInterface,
        )
        # 确保它们都是类
        for cls in [
            LLMInterface, ASRInterface, TTSInterface, VADInterface,
            VisionInterface, SingingInterface, AvatarInterface,
            KeyValueStorageInterface, VectorStorageInterface,
        ]:
            assert inspect.isclass(cls), f"{cls} 不是类"

    def test_import_types(self) -> None:
        """导入公共数据类型。"""
        from white_salary.core.interfaces.types import (
            AudioData,
            AudioSegment,
            EmotionState,
            EmotionType,
            ImageData,
            Message,
            MessageRole,
            ToolCall,
            ToolResult,
        )
        # 确保枚举值正确
        assert MessageRole.USER == "user"
        assert MessageRole.ASSISTANT == "assistant"
        assert EmotionType.HAPPY == "happy"


class TestLLMInterface:
    """测试LLM接口定义。"""

    def test_is_abstract(self) -> None:
        """LLMInterface 必须是抽象基类，不能直接实例化。"""
        from white_salary.core.interfaces.llm import LLMInterface
        assert issubclass(LLMInterface, ABC)

    def test_has_required_methods(self) -> None:
        """LLMInterface 必须定义所有必要的抽象方法。"""
        from white_salary.core.interfaces.llm import LLMInterface
        required_methods = [
            "chat_completion",
            "chat_completion_stream",
            "chat_with_tools",
            "process_tool_results",
        ]
        for method_name in required_methods:
            assert hasattr(LLMInterface, method_name), f"缺少方法: {method_name}"
            method = getattr(LLMInterface, method_name)
            assert callable(method), f"{method_name} 不可调用"


class TestASRInterface:
    """测试ASR接口定义。"""

    def test_is_abstract(self) -> None:
        from white_salary.core.interfaces.asr import ASRInterface
        assert issubclass(ASRInterface, ABC)

    def test_has_required_methods(self) -> None:
        from white_salary.core.interfaces.asr import ASRInterface
        assert hasattr(ASRInterface, "transcribe")
        assert hasattr(ASRInterface, "is_available")


class TestTTSInterface:
    """测试TTS接口定义。"""

    def test_is_abstract(self) -> None:
        from white_salary.core.interfaces.tts import TTSInterface
        assert issubclass(TTSInterface, ABC)

    def test_has_required_methods(self) -> None:
        from white_salary.core.interfaces.tts import TTSInterface
        assert hasattr(TTSInterface, "synthesize")
        assert hasattr(TTSInterface, "synthesize_stream")
        assert hasattr(TTSInterface, "is_available")


class TestVADInterface:
    """测试VAD接口定义。"""

    def test_is_abstract(self) -> None:
        from white_salary.core.interfaces.vad import VADInterface
        assert issubclass(VADInterface, ABC)

    def test_has_required_methods(self) -> None:
        from white_salary.core.interfaces.vad import VADInterface
        assert hasattr(VADInterface, "detect_speech")
        assert hasattr(VADInterface, "get_speech_probability")


class TestVisionInterface:
    """测试Vision接口定义。"""

    def test_is_abstract(self) -> None:
        from white_salary.core.interfaces.vision import VisionInterface
        assert issubclass(VisionInterface, ABC)

    def test_has_required_methods(self) -> None:
        from white_salary.core.interfaces.vision import VisionInterface
        assert hasattr(VisionInterface, "describe_image")
        assert hasattr(VisionInterface, "extract_text")
        assert hasattr(VisionInterface, "is_available")


class TestSingingInterface:
    """测试Singing接口定义。"""

    def test_is_abstract(self) -> None:
        from white_salary.core.interfaces.singing import SingingInterface
        assert issubclass(SingingInterface, ABC)

    def test_has_required_methods(self) -> None:
        from white_salary.core.interfaces.singing import SingingInterface
        assert hasattr(SingingInterface, "convert_vocals")
        assert hasattr(SingingInterface, "is_available")


class TestAvatarInterface:
    """测试Avatar接口定义。"""

    def test_is_abstract(self) -> None:
        from white_salary.core.interfaces.avatar import AvatarInterface
        assert issubclass(AvatarInterface, ABC)

    def test_has_required_methods(self) -> None:
        from white_salary.core.interfaces.avatar import AvatarInterface
        assert hasattr(AvatarInterface, "send_command")
        assert hasattr(AvatarInterface, "set_emotion")
        assert hasattr(AvatarInterface, "start_lip_sync")
        assert hasattr(AvatarInterface, "stop_lip_sync")
        assert hasattr(AvatarInterface, "is_available")


class TestStorageInterfaces:
    """测试Storage接口定义。"""

    def test_kv_storage_is_abstract(self) -> None:
        from white_salary.core.interfaces.storage import KeyValueStorageInterface
        assert issubclass(KeyValueStorageInterface, ABC)

    def test_kv_storage_has_required_methods(self) -> None:
        from white_salary.core.interfaces.storage import KeyValueStorageInterface
        for method in ["get", "set", "delete", "exists"]:
            assert hasattr(KeyValueStorageInterface, method), f"缺少方法: {method}"

    def test_vector_storage_is_abstract(self) -> None:
        from white_salary.core.interfaces.storage import VectorStorageInterface
        assert issubclass(VectorStorageInterface, ABC)

    def test_vector_storage_has_required_methods(self) -> None:
        from white_salary.core.interfaces.storage import VectorStorageInterface
        for method in ["add", "search", "delete"]:
            assert hasattr(VectorStorageInterface, method), f"缺少方法: {method}"
