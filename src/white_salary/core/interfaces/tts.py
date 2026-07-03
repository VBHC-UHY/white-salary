"""
white_salary/core/interfaces/tts.py

TTS（Text-to-Speech，文字转语音）的抽象接口定义。

简单说就是：把文字变成声音。
比如AI生成回复"你好啊"，TTS系统就把这几个字读出来。
"""

from abc import ABC, abstractmethod
from typing import AsyncGenerator

from white_salary.core.interfaces.types import AudioData


class TTSInterface(ABC):
    """
    语音合成（TTS）的抽象接口。

    所有TTS适配器（Edge TTS、Azure、GPTSoVITS等）都必须继承这个类。
    """

    @abstractmethod
    async def synthesize(self, text: str) -> AudioData:
        """
        把一段文字转成语音。

        等全部合成完才返回。适用于短句子。

        参数:
            text: 要转成语音的文字

        返回:
            合成的音频数据
        """
        ...

    @abstractmethod
    async def synthesize_stream(self, text: str) -> AsyncGenerator[AudioData, None]:
        """
        以流式方式把文字转成语音。

        边合成边返回音频片段，不用等全部合成完。
        适用于长句子或实时对话场景，能更快让用户听到声音。

        参数:
            text: 要转成语音的文字

        返回:
            异步生成器，逐段返回合成的音频数据
        """
        ...
        yield AudioData(samples=b"")  # pragma: no cover

    @abstractmethod
    async def is_available(self) -> bool:
        """
        检查TTS引擎是否可用。

        返回:
            True=可用，False=不可用
        """
        ...
