"""
white_salary/core/interfaces/asr.py

ASR（Automatic Speech Recognition，自动语音识别）的抽象接口定义。

简单说就是：把人说的话（声音）转成文字。
比如用户对着麦克风说"你好"，ASR系统就输出文字"你好"。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from white_salary.core.interfaces.types import AudioData


@dataclass(frozen=True)
class TranscriptionResult:
    """
    语音识别的结果。

    属性:
        text:       识别出来的文字
        language:   检测到的语言（如 "zh"=中文, "en"=英文）
        confidence: 识别置信度（0.0=完全不确定，1.0=非常确定）
    """
    text: str
    language: str = "unknown"
    confidence: float = 1.0


class ASRInterface(ABC):
    """
    语音识别（ASR）的抽象接口。

    所有ASR适配器（Whisper、FunASR等）都必须继承这个类并实现下面的方法。
    """

    @abstractmethod
    async def transcribe(self, audio: AudioData) -> TranscriptionResult:
        """
        把一段语音转成文字。

        参数:
            audio: 音频数据（包含采样数据、采样率等信息）

        返回:
            识别结果（包含文字、语言、置信度）
        """
        ...

    @abstractmethod
    async def is_available(self) -> bool:
        """
        检查ASR引擎是否可用（模型已加载、服务已启动等）。

        返回:
            True=可用，False=不可用
        """
        ...
