"""
white_salary/core/interfaces/singing.py

Singing（唱歌 / 歌声合成）的抽象接口定义。

和TTS（说话）是完全不同的系统！
TTS把文字变成"说话的声音"，而Singing把文字/曲谱变成"唱歌的声音"。

Neuro-sama的唱歌流程是：
  1. 拿到一首歌的音频
  2. 把人声和伴奏分离
  3. 用RVC模型把人声转成自己的声音
  4. 合并新的人声和伴奏

White Salary 也会用类似的流程。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from white_salary.core.interfaces.types import AudioData


@dataclass(frozen=True)
class SongRequest:
    """
    唱歌请求。

    属性:
        audio_source: 原始歌曲的音频数据
        pitch_shift:  音调偏移（半音数，正数升调，负数降调）
    """
    audio_source: AudioData
    pitch_shift: int = 0


@dataclass(frozen=True)
class SongResult:
    """
    唱歌结果。

    属性:
        vocals:       转换后的人声音频
        instrumental: 伴奏音频
        mixed:        混合后的完整音频（人声+伴奏）
    """
    vocals: AudioData
    instrumental: AudioData
    mixed: AudioData


class SingingInterface(ABC):
    """
    唱歌系统的抽象接口。

    所有唱歌适配器（RVC等）都必须继承这个类。
    """

    @abstractmethod
    async def convert_vocals(self, request: SongRequest) -> SongResult:
        """
        把一首歌的人声转换成White Salary的声音。

        流程：
          1. 分离原曲的人声和伴奏
          2. 用声音模型把人声转成White Salary的声音
          3. 合并新人声和伴奏

        参数:
            request: 唱歌请求（包含原始音频和调节参数）

        返回:
            唱歌结果（包含分离的人声、伴奏和混合后的完整音频）
        """
        ...

    @abstractmethod
    async def is_available(self) -> bool:
        """
        检查唱歌系统是否可用（模型是否已加载）。

        返回:
            True=可用，False=不可用
        """
        ...
