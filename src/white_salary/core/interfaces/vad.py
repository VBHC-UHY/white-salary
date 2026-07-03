"""
white_salary/core/interfaces/vad.py

VAD（Voice Activity Detection，语音活动检测）的抽象接口定义。

简单说就是：判断现在有没有人在说话。
麦克风一直在录音，但我们只需要处理有人说话的部分，
沉默的部分不需要送去识别，这就是VAD的作用。

工作流程：
  麦克风持续录音 → VAD检测"有人说话了！" → 把这段语音送给ASR识别
"""

from abc import ABC, abstractmethod

from white_salary.core.interfaces.types import AudioData


class VADInterface(ABC):
    """
    语音活动检测（VAD）的抽象接口。

    所有VAD适配器（Silero等）都必须继承这个类。
    """

    @abstractmethod
    async def detect_speech(self, audio: AudioData) -> bool:
        """
        检测这段音频中是否有人在说话。

        参数:
            audio: 一小段音频数据（通常是几十毫秒到几百毫秒）

        返回:
            True=检测到有人说话，False=只有静音/噪音
        """
        ...

    @abstractmethod
    async def get_speech_probability(self, audio: AudioData) -> float:
        """
        获取这段音频中有人说话的概率。

        和 detect_speech 不同，这个方法返回的是概率值而不是布尔值。
        可以用来做更细粒度的控制。

        参数:
            audio: 一小段音频数据

        返回:
            说话概率（0.0=肯定没人说话，1.0=肯定有人在说话）
        """
        ...
