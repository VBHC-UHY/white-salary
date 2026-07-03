"""
white_salary/utils/audio.py

音频处理工具函数。

提供音频数据的格式转换、采样率转换、振幅计算等功能。
这些函数在语音识别（ASR）、语音合成（TTS）、口型同步等模块中都会用到。
"""

import struct
from typing import Sequence


def calculate_amplitudes(
    audio_bytes: bytes,
    dtype: str = "float32",
    frame_size: int = 1024,
) -> list[float]:
    """
    计算音频数据的振幅序列（用于口型同步）。

    把音频分成一个个小帧（frame），计算每帧的平均振幅。
    振幅越大说明声音越大，嘴巴就张得越开。

    参数:
        audio_bytes: 音频的原始字节数据
        dtype:       数据类型（"float32" 或 "int16"）
        frame_size:  每帧的采样数

    返回:
        振幅值列表（每个值在0.0~1.0之间）
    """
    if not audio_bytes:
        return []

    # 根据数据类型解析字节
    if dtype == "float32":
        # float32：每个采样4字节
        num_samples = len(audio_bytes) // 4
        samples = struct.unpack(f"{num_samples}f", audio_bytes[: num_samples * 4])
    elif dtype == "int16":
        # int16：每个采样2字节，需要归一化到 -1.0 ~ 1.0
        num_samples = len(audio_bytes) // 2
        raw = struct.unpack(f"{num_samples}h", audio_bytes[: num_samples * 2])
        samples = tuple(s / 32768.0 for s in raw)
    else:
        return []

    # 按帧计算平均振幅
    amplitudes: list[float] = []
    for i in range(0, len(samples), frame_size):
        frame = samples[i : i + frame_size]
        if frame:
            # 计算这帧的平均绝对值（RMS的简化版）
            avg_amplitude = sum(abs(s) for s in frame) / len(frame)
            # 限制在 0.0 ~ 1.0 范围内
            amplitudes.append(min(1.0, avg_amplitude))

    return amplitudes


def resample_linear(
    samples: Sequence[float],
    original_rate: int,
    target_rate: int,
) -> list[float]:
    """
    线性插值重采样（简化版）。

    当两个音频组件的采样率不同时，需要统一采样率。
    比如ASR要求16000Hz，但麦克风录的是44100Hz，就需要转换。

    注意：这是一个简化的线性插值实现。
    如果对音质有更高要求，后续可以替换为 scipy 或 librosa 的专业重采样。

    参数:
        samples:       原始采样数据
        original_rate: 原始采样率
        target_rate:   目标采样率

    返回:
        重采样后的数据
    """
    if original_rate == target_rate or not samples:
        return list(samples)

    # 计算采样率比值
    ratio = target_rate / original_rate
    new_length = int(len(samples) * ratio)

    result: list[float] = []
    for i in range(new_length):
        # 在原始数据中的对应位置
        src_pos = i / ratio
        src_idx = int(src_pos)
        frac = src_pos - src_idx

        if src_idx + 1 < len(samples):
            # 线性插值
            value = samples[src_idx] * (1 - frac) + samples[src_idx + 1] * frac
        else:
            value = samples[min(src_idx, len(samples) - 1)]

        result.append(value)

    return result
