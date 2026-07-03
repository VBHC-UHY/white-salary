"""
white_salary/adapters/asr/faster_whisper_adapter.py

Faster-Whisper本地语音识别适配器。

优点：
  - 完全离线运行，不需要网络
  - GPU加速（CUDA），速度快
  - 支持多语言自动检测
  - 比云端ASR更稳定

模型大小选择：
  - tiny:    ~39MB  (最快，准确度低)
  - base:    ~74MB  (平衡)
  - small:   ~244MB (推荐)
  - medium:  ~769MB (高准确度)
  - large-v3: ~1.5GB (最高准确度)

首次使用会自动下载模型（存到~/.cache/huggingface/）。
"""

from loguru import logger

from white_salary.core.interfaces.asr import ASRInterface, TranscriptionResult
from white_salary.core.interfaces.types import AudioData


class FasterWhisperAdapter(ASRInterface):
    """
    Faster-Whisper本地ASR适配器。

    使用CTranslate2加速的Whisper模型进行本地语音识别。
    """

    def __init__(
        self,
        model_size: str = "small",
        device: str = "auto",    # auto / cuda / cpu
        language: str = "zh",
        compute_type: str = "float16",  # float16 / int8 / float32
    ) -> None:
        self._model_size = model_size
        self._device = device
        self._language = language
        self._compute_type = compute_type
        self._model = None
        self._loaded = False

        self._load_model()

    def _load_model(self) -> None:
        """加载Faster-Whisper模型。"""
        try:
            from faster_whisper import WhisperModel

            # 自动选择设备
            device = self._device
            if device == "auto":
                try:
                    import torch
                    device = "cuda" if torch.cuda.is_available() else "cpu"
                except ImportError:
                    logger.info("[ASR-Local] torch 未安装，自动使用 CPU 模式。")
                    device = "cpu"

            # CPU不支持float16
            compute_type = self._compute_type
            if device == "cpu" and compute_type == "float16":
                compute_type = "int8"

            logger.info(f"[ASR-Local] 加载模型: {self._model_size} (device={device}, compute={compute_type})")

            self._model = WhisperModel(
                self._model_size,
                device=device,
                compute_type=compute_type,
            )
            self._loaded = True
            logger.info(f"[ASR-Local] 模型加载完成: {self._model_size}")

        except ImportError:
            logger.error("[ASR-Local] faster-whisper 未安装: pip install faster-whisper")
        except Exception as e:
            logger.error(f"[ASR-Local] 模型加载失败: {e}")

    async def transcribe(self, audio: AudioData) -> TranscriptionResult:
        """将音频转为文字。"""
        if not self._loaded or not self._model:
            return TranscriptionResult(text="", language="unknown", confidence=0.0)

        if not audio.samples or len(audio.samples) < 100:
            return TranscriptionResult(text="", language="unknown", confidence=0.0)

        try:
            import io
            import struct
            import numpy as np

            # 转换bytes到numpy array
            n_samples = len(audio.samples) // 2
            samples = struct.unpack(f"<{n_samples}h", audio.samples[:n_samples * 2])
            audio_array = np.array(samples, dtype=np.float32) / 32768.0

            # 执行识别
            segments, info = self._model.transcribe(
                audio_array,
                beam_size=5,
                language=self._language if self._language != "auto" else None,
                vad_filter=True,
                vad_parameters=dict(min_silence_duration_ms=500),
            )

            # 收集所有segment的文字
            text = ""
            for segment in segments:
                text += segment.text

            text = text.strip()

            # 过滤非中文（和SiliconFlow ASR一样的保护）
            if text and self._language == "zh" and not self._has_chinese(text):
                return TranscriptionResult(text="", language="unknown", confidence=0.0)

            logger.debug(f"[ASR-Local] 识别: {text[:50]}... (lang={info.language})")

            return TranscriptionResult(
                text=text,
                language=info.language or "unknown",
                confidence=info.language_probability or 0.5,
            )

        except Exception as e:
            logger.warning(f"[ASR-Local] 识别失败: {e}")
            return TranscriptionResult(text="", language="unknown", confidence=0.0)

    @staticmethod
    def _has_chinese(text: str) -> bool:
        for char in text:
            if '\u4e00' <= char <= '\u9fff':
                return True
        return False

    async def is_available(self) -> bool:
        return self._loaded
