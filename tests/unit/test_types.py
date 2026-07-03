"""
测试公共数据类型。

验证所有数据类型的创建、属性和不可变性。
"""

import pytest

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


class TestMessage:
    """测试 Message 数据类型。"""

    def test_create_basic_message(self) -> None:
        """创建一个基本消息。"""
        msg = Message(role=MessageRole.USER, content="你好")
        assert msg.role == MessageRole.USER
        assert msg.content == "你好"
        assert msg.name is None
        assert msg.metadata == {}

    def test_create_message_with_all_fields(self) -> None:
        """创建一个包含所有字段的消息。"""
        msg = Message(
            role=MessageRole.ASSISTANT,
            content="你好啊！",
            name="White Salary",
            metadata={"emotion": "happy"},
        )
        assert msg.name == "White Salary"
        assert msg.metadata["emotion"] == "happy"

    def test_message_is_immutable(self) -> None:
        """消息是不可变的（frozen=True），不能修改属性。"""
        msg = Message(role=MessageRole.USER, content="测试")
        with pytest.raises(AttributeError):
            msg.content = "修改后"  # type: ignore[misc]


class TestAudioData:
    """测试 AudioData 数据类型。"""

    def test_create_with_defaults(self) -> None:
        """使用默认参数创建音频数据。"""
        audio = AudioData(samples=b"\x00" * 100)
        assert audio.sample_rate == 16000
        assert audio.channels == 1
        assert audio.dtype == "float32"

    def test_create_with_custom_params(self) -> None:
        """使用自定义参数创建音频数据。"""
        audio = AudioData(samples=b"\x00" * 100, sample_rate=44100, channels=2, dtype="int16")
        assert audio.sample_rate == 44100
        assert audio.channels == 2


class TestEmotionState:
    """测试 EmotionState 数据类型。"""

    def test_create_basic_emotion(self) -> None:
        """创建基本情感状态。"""
        state = EmotionState(emotion=EmotionType.HAPPY)
        assert state.emotion == EmotionType.HAPPY
        assert state.intensity == 0.5  # 默认值
        assert state.reason is None

    def test_create_with_intensity(self) -> None:
        """创建带强度的情感状态。"""
        state = EmotionState(emotion=EmotionType.ANGRY, intensity=0.9, reason="被骂了")
        assert state.intensity == 0.9
        assert state.reason == "被骂了"

    def test_all_emotion_types_exist(self) -> None:
        """所有预定义的情绪类型都必须存在。"""
        expected = [
            "neutral", "happy", "sad", "angry", "surprised",
            "scared", "disgusted", "shy", "excited", "thinking",
        ]
        actual = [e.value for e in EmotionType]
        for e in expected:
            assert e in actual, f"缺少情绪类型: {e}"


class TestToolCall:
    """测试 ToolCall 数据类型。"""

    def test_create_tool_call(self) -> None:
        """创建工具调用请求。"""
        call = ToolCall(id="call_123", name="search_web", arguments={"query": "天气"})
        assert call.id == "call_123"
        assert call.name == "search_web"
        assert call.arguments["query"] == "天气"


class TestToolResult:
    """测试 ToolResult 数据类型。"""

    def test_create_success_result(self) -> None:
        """创建成功的工具结果。"""
        result = ToolResult(call_id="call_123", content="今天晴天")
        assert result.success is True
        assert result.error is None

    def test_create_failure_result(self) -> None:
        """创建失败的工具结果。"""
        result = ToolResult(
            call_id="call_123", content="", success=False, error="网络超时"
        )
        assert result.success is False
        assert result.error == "网络超时"


class TestImageData:
    """测试 ImageData 数据类型。"""

    def test_create_image(self) -> None:
        """创建图片数据。"""
        img = ImageData(data=b"\x89PNG", width=1920, height=1080)
        assert img.width == 1920
        assert img.format == "png"


class TestAudioSegment:
    """测试 AudioSegment 数据类型。"""

    def test_create_segment(self) -> None:
        """创建音频片段。"""
        audio = AudioData(samples=b"\x00" * 100)
        segment = AudioSegment(audio=audio, text="你好", emotion="happy")
        assert segment.text == "你好"
        assert segment.emotion == "happy"
