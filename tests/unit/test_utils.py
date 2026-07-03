"""
测试工具函数。

验证文本处理、音频处理、异步工具等功能。
"""

import struct

import pytest

from white_salary.utils.text import (
    clean_text,
    extract_emotion_tags,
    split_sentences,
    truncate_text,
)
from white_salary.utils.audio import calculate_amplitudes, resample_linear


class TestSplitSentences:
    """测试分句功能。"""

    def test_chinese_sentences(self) -> None:
        """中文标点分句。"""
        result = split_sentences("你好！今天天气真好。你觉得呢？")
        assert result == ["你好！", "今天天气真好。", "你觉得呢？"]

    def test_english_sentences(self) -> None:
        """英文标点分句。"""
        result = split_sentences("Hello! How are you? I'm fine.")
        assert len(result) == 3

    def test_empty_input(self) -> None:
        """空输入返回空列表。"""
        assert split_sentences("") == []
        assert split_sentences("   ") == []

    def test_single_sentence(self) -> None:
        """没有标点就是一整句。"""
        result = split_sentences("这是一句没有标点的话")
        assert result == ["这是一句没有标点的话"]

    def test_newline_split(self) -> None:
        """换行符也应该分句。"""
        result = split_sentences("第一行\n第二行")
        assert len(result) == 2


class TestTruncateText:
    """测试文本截断。"""

    def test_short_text_not_truncated(self) -> None:
        """短文本不需要截断。"""
        assert truncate_text("短文本", 10) == "短文本"

    def test_long_text_truncated(self) -> None:
        """长文本会被截断并加省略号。"""
        result = truncate_text("这是一段很长很长的文本", 6)
        assert len(result) <= 6 + len("...")
        assert result.endswith("...")

    def test_exact_length(self) -> None:
        """刚好等于最大长度不截断。"""
        text = "12345"
        assert truncate_text(text, 5) == "12345"


class TestCleanText:
    """测试文本清理。"""

    def test_remove_extra_whitespace(self) -> None:
        """多余空白压缩成一个空格。"""
        assert clean_text("你好    世界") == "你好 世界"

    def test_strip_whitespace(self) -> None:
        """去掉首尾空白。"""
        assert clean_text("  你好  ") == "你好"

    def test_empty_input(self) -> None:
        """空输入返回空字符串。"""
        assert clean_text("") == ""


class TestExtractEmotionTags:
    """测试情绪标签提取。"""

    def test_extract_tags(self) -> None:
        """正确提取情绪标签。"""
        text, tags = extract_emotion_tags("[happy]你好啊！[excited]太棒了！")
        assert "happy" in tags
        assert "excited" in tags
        assert "[" not in text  # 标签已被移除

    def test_no_tags(self) -> None:
        """没有标签的文本原样返回。"""
        text, tags = extract_emotion_tags("普通的文本")
        assert text == "普通的文本"
        assert tags == []


class TestCalculateAmplitudes:
    """测试振幅计算。"""

    def test_empty_audio(self) -> None:
        """空音频返回空列表。"""
        assert calculate_amplitudes(b"") == []

    def test_float32_audio(self) -> None:
        """float32格式的音频能正确计算振幅。"""
        # 创建一些float32采样数据
        samples = [0.5, -0.5, 0.3, -0.3]
        audio_bytes = struct.pack(f"{len(samples)}f", *samples)
        result = calculate_amplitudes(audio_bytes, dtype="float32", frame_size=2)
        assert len(result) == 2  # 4个采样 / 每帧2个 = 2帧
        assert all(0.0 <= v <= 1.0 for v in result)

    def test_int16_audio(self) -> None:
        """int16格式的音频能正确计算振幅。"""
        samples = [16384, -16384, 8192, -8192]
        audio_bytes = struct.pack(f"{len(samples)}h", *samples)
        result = calculate_amplitudes(audio_bytes, dtype="int16", frame_size=2)
        assert len(result) == 2
        assert all(0.0 <= v <= 1.0 for v in result)


class TestResampleLinear:
    """测试线性重采样。"""

    def test_same_rate(self) -> None:
        """相同采样率不做任何处理。"""
        samples = [0.1, 0.2, 0.3, 0.4]
        result = resample_linear(samples, 16000, 16000)
        assert result == samples

    def test_upsample(self) -> None:
        """上采样（从低到高采样率）。"""
        samples = [0.0, 1.0]
        result = resample_linear(samples, 16000, 32000)
        assert len(result) == 4  # 数据量翻倍

    def test_downsample(self) -> None:
        """下采样（从高到低采样率）。"""
        samples = [0.0, 0.25, 0.5, 0.75]
        result = resample_linear(samples, 32000, 16000)
        assert len(result) == 2  # 数据量减半

    def test_empty_input(self) -> None:
        """空输入返回空列表。"""
        assert resample_linear([], 16000, 44100) == []
