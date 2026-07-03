"""
端口定义（Interfaces / Ports）

这个包定义了所有外部组件必须实现的抽象接口。
这是六边形架构中最关键的一层——核心代码只依赖这些接口，不依赖具体实现。

接口清单：
  - llm.py      LLM接口（大语言模型）
  - asr.py      ASR接口（语音识别）
  - tts.py      TTS接口（语音合成）
  - vad.py      VAD接口（语音活动检测）
  - vision.py   Vision接口（计算机视觉）
  - singing.py  Singing接口（唱歌/歌声合成）
  - avatar.py   Avatar接口（虚拟形象）
  - storage.py  Storage接口（数据持久化）
"""

from white_salary.core.interfaces.asr import ASRInterface
from white_salary.core.interfaces.avatar import AvatarInterface
from white_salary.core.interfaces.llm import LLMInterface
from white_salary.core.interfaces.singing import SingingInterface
from white_salary.core.interfaces.storage import (
    KeyValueStorageInterface,
    VectorStorageInterface,
)
from white_salary.core.interfaces.tts import TTSInterface
from white_salary.core.interfaces.vad import VADInterface
from white_salary.core.interfaces.vision import VisionInterface

__all__ = [
    "LLMInterface",
    "ASRInterface",
    "TTSInterface",
    "VADInterface",
    "VisionInterface",
    "SingingInterface",
    "AvatarInterface",
    "KeyValueStorageInterface",
    "VectorStorageInterface",
]
