"""
white_salary/adapters/vad/energy_vad.py

基于音量能量的简单VAD（语音活动检测）。

原理：计算音频片段的平均能量，超过阈值就认为有人在说话。
优点：零依赖，不需要模型文件，速度极快。
缺点：对噪声环境不够鲁棒（后续可升级为Silero VAD）。
"""

import struct

from white_salary.core.interfaces.vad import VADInterface
from white_salary.core.interfaces.types import AudioData


class EnergyVAD(VADInterface):
    """
    基于音量能量的VAD。

    通过计算音频的RMS能量值来判断是否有人说话。
    """

    def __init__(self, threshold: float = 0.02) -> None:
        """
        Args:
            threshold: 能量阈值（0.0-1.0），超过此值认为有语音活动。
                       默认0.02适合安静环境，嘈杂环境可调高。
        """
        self._threshold = threshold

    async def detect_speech(self, audio: AudioData) -> bool:
        """检测音频中是否有人说话。"""
        prob = await self.get_speech_probability(audio)
        return prob > self._threshold

    async def get_speech_probability(self, audio: AudioData) -> float:
        """计算音频的RMS能量作为语音概率。"""
        if not audio.samples or len(audio.samples) < 2:
            return 0.0

        try:
            # 假设16bit PCM格式
            n_samples = len(audio.samples) // 2
            if n_samples == 0:
                return 0.0

            samples = struct.unpack(f"<{n_samples}h", audio.samples[:n_samples * 2])

            # 计算RMS能量
            sum_sq = sum(s * s for s in samples)
            rms = (sum_sq / n_samples) ** 0.5

            # 归一化到0-1（16bit最大值32768）
            normalized = min(1.0, rms / 32768.0 * 10)  # 放大10倍使其更敏感
            return normalized

        except Exception:
            return 0.0
