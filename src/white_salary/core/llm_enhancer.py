"""
white_salary/core/llm_enhancer.py

LLM回复增强器 — 分析用户意图，优化回复自然度。

借鉴v2的llm_enhancer.py（3276行24个分析器），但大幅简化：
  - v2有24个分析器分4层，太重了，每条消息调十几次LLM
  - 我们只做最关键的3个：意图分析、语气调整、自然度优化
  - 不用LLM做分析（v2每条消息要调6+次LLM太贵），用规则+关键词
  - 结果注入system prompt，让主LLM自己调整

功能：
  - 检测用户意图（提问/请求/闲聊/情感/分享/调侃）
  - 检测紧急程度
  - 生成回复风格提示（注入system prompt）
"""

import re
from enum import Enum
from dataclasses import dataclass
from typing import Optional


class IntentType(Enum):
    QUESTION = "question"       # 提问
    REQUEST = "request"         # 请求帮忙
    CHAT = "chat"               # 闲聊
    EMOTION = "emotion"         # 情感表达
    SHARE = "share"             # 分享信息
    TEASE = "tease"             # 调侃/玩笑
    COMPLAIN = "complain"       # 抱怨
    GREETING = "greeting"       # 问候
    UNKNOWN = "unknown"


class Urgency(Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"


@dataclass
class EnhanceResult:
    """增强分析结果。"""
    intent: IntentType
    urgency: Urgency
    style_hint: str = ""      # 注入到system prompt的风格提示


# 意图检测模式
_QUESTION_PATTERNS = re.compile(
    r"(什么|怎么|为什么|如何|哪个|哪里|多少|是不是|能不能|会不会|可以吗|吗\?|吗？|呢\?|呢？|\?|？)$"
)
_REQUEST_PATTERNS = re.compile(
    r"(帮我|帮忙|请|麻烦|能不能|可以|给我|替我|搜一下|查一下|算一下|翻译|"
    r"发个|发一[个张下]|发送|发语音|发图片|发表情包|来[个张]|截屏|截图|"
    r"打开|点开|播放|生成|画[个张]?|做一下|弄一下)"
)
_EMOTION_PATTERNS = re.compile(
    r"(好开心|好难过|好生气|好累|好烦|伤心|难受|委屈|崩溃|焦虑|害怕|紧张|兴奋|感动|"
    r"呜呜|哭了|泪目|心疼|烦死|气死|累死|无语|裂开|emo|破防)"
)
_TEASE_PATTERNS = re.compile(
    r"(哈哈|笑死|绷不住|草|xswl|乐了|蚌埠|离谱|抽象|6|666|逆天|"
    r"笨蛋|傻瓜|笨笨|坏蛋|讨厌啦)"
)
_GREETING_PATTERNS = re.compile(
    r"^(早安?|晚安|你好|嗨|hi|hello|在吗|在不在|起床|睡了)[\s！!～~]*$", re.IGNORECASE
)
_COMPLAIN_PATTERNS = re.compile(
    r"(烦死了|受不了|好无聊|讨厌|不想|算了|没意思|无所谓|随便吧)"
)
_URGENT_PATTERNS = re.compile(
    r"(急|紧急|马上|立刻|快[点些]|赶紧|来不及|deadline|ddl|救命|help|sos)"
)
_SHARE_PATTERNS = re.compile(
    r"(你[看猜知道]|告诉你|跟你说|分享|推荐|安利|发现了)"
)


class LLMEnhancer:
    """
    LLM回复增强器。

    使用方式:
        enhancer = LLMEnhancer()
        result = enhancer.analyze("帮我查一下明天的天气")
        # result.intent = REQUEST, result.style_hint = "用户在请求帮助，直接给出有用的回答"
    """

    def analyze(self, user_message: str) -> EnhanceResult:
        """分析用户消息，返回增强提示。"""
        text = user_message.strip()
        if not text:
            return EnhanceResult(IntentType.UNKNOWN, Urgency.NORMAL)

        intent = self._detect_intent(text)
        urgency = self._detect_urgency(text)
        style_hint = self._build_style_hint(intent, urgency)

        return EnhanceResult(intent=intent, urgency=urgency, style_hint=style_hint)

    def _detect_intent(self, text: str) -> IntentType:
        """检测用户意图。"""
        if _GREETING_PATTERNS.search(text):
            return IntentType.GREETING
        if _REQUEST_PATTERNS.search(text):
            return IntentType.REQUEST
        if _EMOTION_PATTERNS.search(text):
            return IntentType.EMOTION
        if _TEASE_PATTERNS.search(text):
            return IntentType.TEASE
        if _COMPLAIN_PATTERNS.search(text):
            return IntentType.COMPLAIN
        if _SHARE_PATTERNS.search(text):
            return IntentType.SHARE
        if _QUESTION_PATTERNS.search(text):
            return IntentType.QUESTION
        return IntentType.CHAT

    def _detect_urgency(self, text: str) -> Urgency:
        """检测紧急程度。"""
        if _URGENT_PATTERNS.search(text):
            return Urgency.HIGH
        if len(text) > 100:
            return Urgency.NORMAL
        return Urgency.LOW

    def _build_style_hint(self, intent: IntentType, urgency: Urgency) -> str:
        """根据意图和紧急度生成风格提示。"""
        hints = {
            IntentType.QUESTION: "用户在提问，先回答清楚；普通聊天不要只回一两个字，可以自然补一句。",
            IntentType.REQUEST: "用户在请求你做事或调用能力；先判断是否需要工具/动作，成功或失败都用自然口吻说明，不要只回一两个字。",
            IntentType.CHAT: "用户在闲聊，轻松自然地回应，可以适当展开话题，不要把正常聊天压成单字。",
            IntentType.EMOTION: "用户在表达情绪，先共情再回应，不要急于给建议。",
            IntentType.SHARE: "用户在分享信息，表现出兴趣和好奇，适当追问。",
            IntentType.TEASE: "用户在开玩笑/调侃，用活泼俏皮的方式回应，可以反击。",
            IntentType.COMPLAIN: "用户在抱怨，理解他的感受，不要说教。",
            IntentType.GREETING: "用户在打招呼，热情自然地回应。",
            IntentType.UNKNOWN: "",
        }

        hint = hints.get(intent, "")
        if urgency == Urgency.HIGH and hint:
            hint = "【紧急】" + hint
        return hint
