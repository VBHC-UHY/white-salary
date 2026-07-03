"""
white_salary/core/interfaces/types.py

公共数据类型定义。

这个文件定义了在多个接口之间共享的数据结构。
使用 dataclass 而不是普通 dict，是为了：
  1. 类型安全——IDE能自动补全，写错字段名会报错
  2. 可读性好——一眼就知道这个数据长什么样
  3. 不可变性——frozen=True 防止意外修改数据
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# =============================================================================
# 消息相关
# =============================================================================

class MessageRole(str, Enum):
    """
    消息角色枚举。

    在对话中，每条消息都有一个角色标识：
      - SYSTEM:    系统指令（定义AI的行为规则，用户看不到）
      - USER:      用户说的话
      - ASSISTANT: AI的回复
      - TOOL:      工具调用的返回结果
    """
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass(frozen=True)
class Message:
    """
    一条对话消息。

    属性:
        role:    谁说的（system/user/assistant/tool）
        content: 说了什么（文本内容）
        name:    说话者的名字（可选，用于区分不同用户）
        metadata: 附加信息（可选，比如时间戳、情绪标签等）
    """
    role: MessageRole
    content: str
    name: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


# =============================================================================
# 音频相关
# =============================================================================

@dataclass(frozen=True)
class AudioData:
    """
    一段音频数据。

    属性:
        samples:     音频采样数据（numpy数组的原始字节）
        sample_rate: 采样率（每秒采样次数，常见值：16000、22050、44100）
        channels:    声道数（1=单声道，2=立体声）
        dtype:       数据类型（如 "float32"、"int16"）
    """
    samples: bytes
    sample_rate: int = 16000
    channels: int = 1
    dtype: str = "float32"


@dataclass(frozen=True)
class AudioSegment:
    """
    一个音频片段（带文本信息）。

    用于TTS输出：既包含合成的语音，也包含对应的文字。

    属性:
        audio:  音频数据
        text:   对应的文本内容
        emotion: 这段话的情绪标签（可选）
    """
    audio: AudioData
    text: str
    emotion: str | None = None


# =============================================================================
# 情感相关
# =============================================================================

class EmotionType(str, Enum):
    """
    情绪类型枚举。

    定义了AI可以表达的基本情绪。
    这些情绪会映射到虚拟形象的表情动画。
    """
    NEUTRAL = "neutral"      # 平静/中性
    HAPPY = "happy"          # 开心
    SAD = "sad"              # 难过
    ANGRY = "angry"          # 生气
    SURPRISED = "surprised"  # 惊讶
    SCARED = "scared"        # 害怕
    DISGUSTED = "disgusted"  # 厌恶
    SHY = "shy"              # 害羞
    EXCITED = "excited"      # 兴奋
    THINKING = "thinking"    # 思考中


@dataclass(frozen=True)
class EmotionState:
    """
    情感状态。

    描述当前的情绪，包括类型和强度。

    属性:
        emotion:   情绪类型
        intensity: 情绪强度（0.0=几乎没有，1.0=非常强烈）
        reason:    产生这个情绪的原因（可选，用于调试）
    """
    emotion: EmotionType
    intensity: float = 0.5
    reason: str | None = None


# =============================================================================
# 视觉相关
# =============================================================================

@dataclass(frozen=True)
class ImageData:
    """
    一张图片的数据。

    属性:
        data:   图片的原始字节数据
        width:  图片宽度（像素）
        height: 图片高度（像素）
        format: 图片格式（如 "png"、"jpg"、"rgb"）
    """
    data: bytes
    width: int
    height: int
    format: str = "png"


# =============================================================================
# 工具调用相关
# =============================================================================

@dataclass(frozen=True)
class ToolCall:
    """
    一次工具调用请求。

    当LLM决定要调用某个工具时，会生成这个数据结构。

    属性:
        id:        工具调用的唯一标识（用于匹配返回结果）
        name:      工具名称（如 "search_web"、"execute_code"）
        arguments: 调用参数（JSON格式的字典）
    """
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ToolResult:
    """
    工具调用的返回结果。

    属性:
        call_id:   对应的工具调用ID
        content:   返回内容（文本）
        success:   是否成功
        error:     错误信息（失败时）
    """
    call_id: str
    content: str
    success: bool = True
    error: str | None = None
