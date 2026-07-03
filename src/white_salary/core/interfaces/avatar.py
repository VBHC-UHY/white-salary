"""
white_salary/core/interfaces/avatar.py

Avatar（虚拟形象）的抽象接口定义。

虚拟形象就是AI在屏幕上的"身体"——一个能动的2D或3D角色。
它需要能做到：
  - 嘴巴跟着说话动（口型同步）
  - 根据情绪变化表情（开心就笑，生气就皱眉）
  - 做各种动作（挥手、点头等）
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum

from white_salary.core.interfaces.types import EmotionState


class ActionType(str, Enum):
    """
    虚拟形象可以做的动作类型。
    """
    IDLE = "idle"              # 待机（正常状态）
    TALK = "talk"              # 说话中
    SING = "sing"              # 唱歌中
    WAVE = "wave"              # 挥手
    NOD = "nod"                # 点头
    SHAKE_HEAD = "shake_head"  # 摇头
    THINK = "think"            # 思考中
    DANCE = "dance"            # 跳舞


@dataclass(frozen=True)
class AvatarCommand:
    """
    发送给虚拟形象的控制指令。

    属性:
        action:       要做的动作
        emotion:      要表达的情绪状态（可选）
        lip_sync:     口型同步数据（可选，音频振幅值）
        duration:     动作持续时间（秒，可选）
    """
    action: ActionType
    emotion: EmotionState | None = None
    lip_sync: list[float] | None = None
    duration: float | None = None


class AvatarInterface(ABC):
    """
    虚拟形象的抽象接口。

    所有形象适配器（Live2D等）都必须继承这个类。
    """

    @abstractmethod
    async def send_command(self, command: AvatarCommand) -> None:
        """
        发送控制指令给虚拟形象。

        比如：让她笑、让她说话、让她跳舞。

        参数:
            command: 控制指令（包含动作、情绪、口型等信息）
        """
        ...

    @abstractmethod
    async def set_emotion(self, emotion: EmotionState) -> None:
        """
        设置虚拟形象的情绪状态（只改表情，不做动作）。

        参数:
            emotion: 情绪状态（类型+强度）
        """
        ...

    @abstractmethod
    async def start_lip_sync(self, audio_amplitudes: list[float]) -> None:
        """
        开始口型同步。

        根据音频的振幅数据，让虚拟形象的嘴巴跟着动。

        参数:
            audio_amplitudes: 音频振幅值列表（每个值对应一帧的嘴巴张开程度）
        """
        ...

    @abstractmethod
    async def stop_lip_sync(self) -> None:
        """
        停止口型同步，嘴巴恢复闭合状态。
        """
        ...

    @abstractmethod
    async def is_available(self) -> bool:
        """
        检查虚拟形象系统是否可用。

        返回:
            True=可用，False=不可用
        """
        ...
