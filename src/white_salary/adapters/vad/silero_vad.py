"""
white_salary/adapters/vad/silero_vad.py

Silero VAD适配器 — 基于神经网络的语音活动检测。

比能量VAD更精准，能在嘈杂环境中准确区分语音和噪声。
使用预训练的Silero VAD模型（~1MB，CPU即可运行）。
未安装 torch 或模型加载失败时，自动降级到零依赖的 EnergyVAD。
"""

from loguru import logger

from white_salary.adapters.vad.energy_vad import EnergyVAD
from white_salary.core.interfaces.vad import VADInterface
from white_salary.core.interfaces.types import AudioData


class SileroVAD(VADInterface):
    """
    Silero VAD — 神经网络语音活动检测。

    使用Silero预训练模型，比能量VAD更抗噪。
    """

    def __init__(self, threshold: float = 0.5) -> None:
        """
        Args:
            threshold: 检测阈值（0.0-1.0），越高越严格
        """
        self._threshold = threshold
        self._model = None
        self._torch = None
        self._fallback = EnergyVAD()
        self._loaded = False

        try:
            import torch

            self._torch = torch
            model, utils = torch.hub.load(
                repo_or_dir='snakers4/silero-vad',
                model='silero_vad',
                force_reload=False,
                onnx=True,
            )
            self._model = model
            self._loaded = True
            logger.info("[SileroVAD] 模型加载成功")
        except ImportError:
            logger.warning("[SileroVAD] torch 未安装，降级为能量VAD。需要 Silero 时请安装: pip install -e \".[vad-silero]\"")
        except Exception as e:
            logger.warning(f"[SileroVAD] 模型加载失败，降级为能量VAD: {e}")

    async def detect_speech(self, audio: AudioData) -> bool:
        prob = await self.get_speech_probability(audio)
        return prob > self._threshold

    async def get_speech_probability(self, audio: AudioData) -> float:
        if not self._loaded or not self._model or self._torch is None:
            return await self._fallback.get_speech_probability(audio)

        try:
            import struct
            n_samples = len(audio.samples) // 2
            if n_samples == 0:
                return 0.0

            samples = struct.unpack(f"<{n_samples}h", audio.samples[:n_samples * 2])
            torch = self._torch
            tensor = torch.FloatTensor(samples) / 32768.0

            # Silero VAD expects 16kHz mono
            if audio.sample_rate != 16000:
                # Simple resample by ratio
                ratio = 16000 / audio.sample_rate
                new_len = int(len(tensor) * ratio)
                tensor = torch.nn.functional.interpolate(
                    tensor.unsqueeze(0).unsqueeze(0),
                    size=new_len,
                    mode='linear',
                    align_corners=False,
                ).squeeze()

            prob = self._model(tensor, 16000).item()
            return prob

        except Exception as e:
            logger.debug(f"[SileroVAD] 检测失败: {e}")
            return 0.0
