"""
white_salary/core/memory/auto_classifier.py

记忆自动分类器 — 自动将记忆归类为6种类型。

借鉴v2的auto_classifier.py：
  - person: 关于某个人的信息（名字/关系/特征）
  - event: 发生的事件（日期/地点/经过）
  - promise: 承诺/约定（"记住"/"下次"/"一定"）
  - secret: 秘密（"只告诉你"/"别说出去"）
  - knowledge: 知识/技能（"怎么做"/"方法"/"教程"）
  - emotion: 情感表达（"开心"/"难过"/"喜欢"）

双重分类：先规则匹配（快），规则分不出的用LLM（准）。

自动发现：导出MODULE供MemoryManager加载。
"""

import re
from typing import Optional
from loguru import logger
from white_salary.core.memory.module_base import MemoryModule


# 6种记忆分类
MEMORY_CATEGORIES = {
    "person": "人物信息",
    "event": "事件记录",
    "promise": "承诺约定",
    "secret": "秘密",
    "knowledge": "知识技能",
    "emotion": "情感表达",
}

# 分类关键词规则（优先级从高到低）
_CLASSIFY_RULES = {
    "secret": [
        "秘密", "只告诉你", "别说出去", "别跟别人说", "偷偷",
        "不要告诉", "保密", "私下", "悄悄", "我们的秘密",
    ],
    "emotion": [
        "开心", "难过", "伤心", "生气", "害怕", "紧张",
        "喜欢", "讨厌", "爱", "恨", "感动", "感谢",
        "好开心", "好难过", "好烦", "好累", "好无聊",
        "哭了", "笑了", "崩溃", "破防", "emo",
    ],
    "promise": [
        "记住", "别忘了", "要记得", "答应我", "约定", "一定要",
        "下次", "以后", "保证", "拜托", "承诺", "说好的",
    ],
    "event": [
        "今天", "昨天", "上次", "那天", "刚才", "之前",
        "发生了", "出了", "去了", "看了", "买了", "吃了",
        "生日", "纪念日", "考试", "面试", "比赛", "旅行",
    ],
    "person": [
        "我妈", "我爸", "我的朋友", "我同学", "我同事", "我老师",
        "我男朋友", "我女朋友", "我老公", "我老婆", "我哥", "我姐",
        "叫什么", "是谁", "认识", "介绍",
        "他是", "她是", "他叫", "她叫",
    ],
    "knowledge": [
        "怎么做", "怎么弄", "怎样", "如何", "方法", "教程",
        "教我", "告诉我怎么", "步骤", "技巧", "学会",
        "是什么意思", "什么是", "解释一下",
    ],
}

# 正则模式（更精确的分类）
_CLASSIFY_PATTERNS = {
    "secret": [
        r"秘密",
        r"(?:不要|别)(?:告诉|说给)(?:别人|其他人)",
    ],
    "promise": [
        r"记住",
        r"别忘",
        r"要记得",
        r"(?:你|我)(?:要|得|必须)记住",
        r"(?:答应|保证|约定)(?:我|你)",
    ],
    "person": [
        r"我(?:的)?(?:妈|爸|哥|姐|弟|妹|朋友|同学|同事|老师|对象)",
        r"(?:他|她)(?:叫|是|名字)",
    ],
    "event": [
        r"(?:今天|昨天|上周|上个月|去年).{2,20}(?:了|过|完)",
        r"\d{1,2}月\d{1,2}日",
    ],
}


class MemoryClassifier:
    """
    记忆自动分类器。

    使用方式:
        classifier = MemoryClassifier()
        category = classifier.classify("我妈妈叫张丽")
        # → "person"
        category = classifier.classify("记住我的生日是6月16日")
        # → "promise"
    """

    def __init__(self, use_llm_fallback: bool = True) -> None:
        self._use_llm = use_llm_fallback
        # 编译正则
        self._compiled_patterns = {}
        for cat, patterns in _CLASSIFY_PATTERNS.items():
            self._compiled_patterns[cat] = [re.compile(p) for p in patterns]

    def classify(self, text: str, llm=None) -> str:
        """
        分类一条记忆文本。

        Args:
            text: 记忆内容
            llm: 可选的LLM适配器（规则分不出时用）

        Returns:
            分类名（person/event/promise/secret/knowledge/emotion）
        """
        if not text or len(text) < 2:
            return "knowledge"  # 默认

        # 第一轮：正则模式匹配（最精确）
        for cat, patterns in self._compiled_patterns.items():
            for p in patterns:
                if p.search(text):
                    return cat

        # 第二轮：关键词匹配
        for cat, keywords in _CLASSIFY_RULES.items():
            for kw in keywords:
                if kw in text:
                    return cat

        # 第三轮：LLM分类（如果启用且可用）
        # LLM分类是异步的，这里不做（由外部调用classify_with_llm）

        return "knowledge"  # 默认归类为知识

    async def classify_with_llm(self, text: str, llm) -> str:
        """用LLM进行精确分类。"""
        if not llm:
            return self.classify(text)

        try:
            from white_salary.core.interfaces.types import Message, MessageRole
            prompt = [
                Message(role=MessageRole.SYSTEM, content=(
                    "将以下文本分类为一种类型。只返回类型名，不要其他文字。\n"
                    "可选类型：person(人物) / event(事件) / promise(承诺) / "
                    "secret(秘密) / knowledge(知识) / emotion(情感)"
                )),
                Message(role=MessageRole.USER, content=text),
            ]
            reply = await llm.chat_completion(prompt, temperature=0.1, max_tokens=20)
            reply = reply.strip().lower()

            for cat in MEMORY_CATEGORIES:
                if cat in reply:
                    return cat

        except Exception as e:
            logger.debug(f"[Classifier] LLM分类失败: {e}")

        return self.classify(text)  # 回退到规则

    def classify_batch(self, texts: list[str]) -> dict[str, list[str]]:
        """批量分类，返回按类别分组的结果。"""
        result = {cat: [] for cat in MEMORY_CATEGORIES}
        for text in texts:
            cat = self.classify(text)
            result[cat].append(text)
        return result

    def get_category_label(self, category: str) -> str:
        """获取分类的中文标签。"""
        return MEMORY_CATEGORIES.get(category, "未知")


# ================================================================
# 自动发现接口
# ================================================================

class ClassifierModule(MemoryModule):
    name = "auto_classifier"

    def init(self, data_dir="data/memory", **kwargs):
        self._impl = MemoryClassifier()

    def on_message(self, user_msg="", ai_reply=""):
        # 分类在llm_extractor里调用，这里不重复
        pass


MODULE = ClassifierModule
