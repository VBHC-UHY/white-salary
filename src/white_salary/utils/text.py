"""
white_salary/utils/text.py

文本处理工具函数。

提供各种文本处理的辅助功能，比如分句、清理文本、截断等。
"""

import re


def split_sentences(text: str) -> list[str]:
    """
    把一段文本按句子拆分。

    为什么需要分句？因为TTS（语音合成）一次处理一个句子效果更好，
    而且可以实现"边合成边播放"，不用等全部文本都合成完。

    支持中文和英文的常见标点分句。

    参数:
        text: 要拆分的文本

    返回:
        句子列表（不包含空句子）

    示例:
        >>> split_sentences("你好！今天天气真好。你觉得呢？")
        ['你好！', '今天天气真好。', '你觉得呢？']
    """
    if not text or not text.strip():
        return []

    # 按中英文标点分句：句号、感叹号、问号、省略号
    # 使用正向后瞻（lookbehind）保留标点符号
    sentences = re.split(r"(?<=[。！？!?…\n])", text)

    # 过滤掉空字符串，并去掉首尾空白
    return [s.strip() for s in sentences if s.strip()]


def truncate_text(text: str, max_length: int, suffix: str = "...") -> str:
    """
    截断文本到指定长度。

    如果文本超过最大长度，就截断并在末尾加上省略号。

    参数:
        text:       要截断的文本
        max_length: 最大字符数
        suffix:     截断后添加的后缀（默认"..."）

    返回:
        截断后的文本

    示例:
        >>> truncate_text("这是一段很长很长的文本", 6)
        '这是一段很长...'
    """
    if len(text) <= max_length:
        return text
    return text[: max_length - len(suffix)] + suffix


def clean_text(text: str) -> str:
    """
    清理文本：去除多余空白、控制字符等。

    参数:
        text: 要清理的文本

    返回:
        清理后的文本
    """
    if not text:
        return ""

    # 替换连续空白为单个空格
    text = re.sub(r"\s+", " ", text)

    # 去除首尾空白
    text = text.strip()

    return text


def strip_action_tags(text: str) -> str:
    """
    去除文本中的动作描述和XML标签，只保留纯对话文字。

    清理内容：
    - 中文括号动作：（轻轻歪头）
    - 英文括号动作：(smiles)
    - XML标签：<msg>, <text>, <sticker>xxx</sticker>, <poke>, <at>等
    """
    # 去掉带内容的XML标签（如 <sticker>嫌弃.jpg</sticker>）
    text = re.sub(r"<\w+>[^<]*</\w+>", "", text)
    # 去掉自闭合和开闭标签（如 <msg>, </msg>, <br/>）
    text = re.sub(r"</?[\w]+/?>", "", text)
    # 去掉中文括号和英文括号中的内容
    text = re.sub(r"[（(][^）)]*[）)]", "", text)
    # 去掉多余空格
    text = re.sub(r"\s+", " ", text).strip()
    return text


def is_valid_for_tts(text: str) -> bool:
    """
    检查文本是否适合发给TTS合成。

    过滤掉只有标点符号、省略号、空白等无实际内容的文本。

    Returns:
        True=有实际内容可以合成, False=纯标点/空白应该跳过
    """
    if not text:
        return False
    # 去掉所有标点符号和空白后，看还剩什么
    cleaned = re.sub(r'[。！？!?…\s，,、；;：:"\'""''（）()【】\-—～~·.\[\]]+', "", text)
    return len(cleaned) >= 2  # 至少2个非标点字符


def strip_xml_tags(text: str) -> str:
    """
    从显示文本中去掉XML标签，但保留标签内的文字内容。

    用于聊天显示：去掉<msg>等标签壳，保留括号里的动作描述。

    示例:
        >>> strip_xml_tags("<msg>（歪头）你好啊<sticker>开心.jpg</sticker></msg>")
        '（歪头）你好啊'
    """
    # 去掉带内容的特殊标签（sticker/poke/at等不需要显示的）
    text = re.sub(r"<(sticker|poke|at|image|voice|video|emoji)[^>]*>[^<]*</\1>", "", text)
    text = re.sub(r"<(sticker|poke|at|image|voice|video|emoji)[^>]*/>", "", text)
    # 去掉剩余的标签壳，保留内容
    text = re.sub(r"</?[\w]+/?>", "", text)
    # 去掉多余空格
    text = re.sub(r"\s+", " ", text).strip()
    return text


def extract_emotion_tags(text: str) -> tuple[str, list[str]]:
    """
    从文本中提取情绪标签。

    LLM生成的回复中可能包含情绪标签，格式如 [happy] 或 [angry]。
    这个函数把标签提取出来，并返回去掉标签后的纯文本。

    参数:
        text: 可能包含情绪标签的文本

    返回:
        一个元组：(去掉标签后的纯文本, 提取到的情绪标签列表)

    示例:
        >>> extract_emotion_tags("[happy]你好啊！[excited]今天真开心！")
        ('你好啊！今天真开心！', ['happy', 'excited'])
    """
    # 匹配方括号中的情绪标签
    tags = re.findall(r"\[(\w+)\]", text)

    # 去掉所有标签
    clean = re.sub(r"\[\w+\]", "", text).strip()

    return clean, tags
