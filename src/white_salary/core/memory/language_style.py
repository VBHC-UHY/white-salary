"""
white_salary/core/memory/language_style.py

语言风格学习 — 学习用户的说话模式并适应。

借鉴v2的features/language_style.py（487行）：
  - 指数移动平均追踪用户消息长度
  - 学习常用词汇top200
  - 学习标点符号习惯
  - 根据用户心情调整回复风格
  - background_llm异步分析

LLM通道：background_llm（每100条消息异步分析）

自动发现：导出MODULE供MemoryManager加载。
"""

import json
import re
import time
from collections import Counter
from pathlib import Path
from typing import Optional

from loguru import logger
from white_salary.core.memory.module_base import MemoryModule


# 指数移动平均参数
EMA_ALPHA = 0.1

# 统计阈值
ANALYZE_THRESHOLD = 100  # 每100条消息触发一次分析
MAX_WORDS = 200
MAX_ENDINGS = 50


class LanguageStyleLearner:
    """
    语言风格学习器。

    使用方式:
        learner = LanguageStyleLearner(data_dir)
        learner.on_user_message("哈哈好的呀~")
        hint = learner.get_style_hint()
    """

    def __init__(self, data_dir: str = "data/memory") -> None:
        self._data_path = Path(data_dir) / "language_style.json"
        self._data_path.parent.mkdir(parents=True, exist_ok=True)

        # 用户的语言模式
        self._avg_length: float = 20.0           # 平均消息长度（EMA）
        self._word_freq: Counter = Counter()      # 词频统计
        self._ending_freq: Counter = Counter()    # 句尾词频
        self._punctuation_style: str = "normal"   # expressive/thoughtful/enthusiastic/normal
        self._emoji_frequency: float = 0.0        # 表情使用频率 0-1
        self._message_count: int = 0
        self._last_analyze: float = 0.0

        self._load()

    def on_user_message(self, message: str) -> None:
        """记录用户消息，更新统计。"""
        if not message or len(message) < 2:
            return

        self._message_count += 1

        # EMA更新平均长度
        self._avg_length = EMA_ALPHA * len(message) + (1 - EMA_ALPHA) * self._avg_length

        # 提取词汇（2-3字）
        words = self._extract_words(message)
        self._word_freq.update(words)

        # 提取句尾
        ending = self._extract_ending(message)
        if ending:
            self._ending_freq[ending] += 1

        # 表情频率
        emoji_count = len(re.findall(r'[\U0001F300-\U0001F9FF]|[~～]|[哈嘻]', message))
        self._emoji_frequency = EMA_ALPHA * (emoji_count / max(len(message), 1)) + \
                                (1 - EMA_ALPHA) * self._emoji_frequency

        # 标点风格
        self._update_punctuation_style(message)

        # 限制词频大小
        if len(self._word_freq) > MAX_WORDS * 2:
            self._word_freq = Counter(dict(self._word_freq.most_common(MAX_WORDS)))
        if len(self._ending_freq) > MAX_ENDINGS * 2:
            self._ending_freq = Counter(dict(self._ending_freq.most_common(MAX_ENDINGS)))

        # 定期保存
        if self._message_count % 20 == 0:
            self._save()

    def get_style_hint(self) -> str:
        """生成风格提示（注入system prompt）。"""
        if self._message_count < 10:
            return ""  # 数据太少

        parts = []

        # 消息长度偏好
        if self._avg_length < 10:
            parts.append("用户喜欢简短消息，回复也别太长")
        elif self._avg_length > 50:
            parts.append("用户消息比较详细，可以多说一些")

        # 常用词
        top_words = self._word_freq.most_common(5)
        if top_words:
            words_str = "、".join(w for w, _ in top_words)
            parts.append(f"用户常用词: {words_str}")

        # 标点风格
        style_hints = {
            "expressive": "用户标点丰富（感叹号/问号多），回复也可以活泼一些",
            "thoughtful": "用户标点较少较冷静，回复也保持冷静",
            "enthusiastic": "用户很热情（多感叹号），可以匹配热情",
        }
        if self._punctuation_style in style_hints:
            parts.append(style_hints[self._punctuation_style])

        # 表情频率
        if self._emoji_frequency > 0.1:
            parts.append("用户爱用表情/颜文字，回复也可以用一些")
        elif self._emoji_frequency < 0.01 and self._message_count > 30:
            parts.append("用户很少用表情，回复也不要用太多")

        if not parts:
            return ""
        return "[用户语言风格]\n" + "\n".join(f"  - {p}" for p in parts)

    # ================================================================
    # 内部
    # ================================================================

    def _extract_words(self, text: str) -> list[str]:
        """提取2-3字词。"""
        # 去除标点
        clean = re.sub(r'[，。！？、；：\s,.:;!?\[\]()~～]+', '', text)
        words = []
        for size in (2, 3):
            for i in range(0, len(clean) - size + 1):
                word = clean[i:i + size]
                words.append(word)
        return words

    def _extract_ending(self, text: str) -> Optional[str]:
        """提取句尾词（最后1-2个字+标点）。"""
        text = text.strip()
        if not text:
            return None
        # 取最后3个字符
        ending = text[-3:] if len(text) >= 3 else text
        return ending

    def _update_punctuation_style(self, text: str) -> None:
        """更新标点风格判断。"""
        excl = text.count("！") + text.count("!")
        ques = text.count("？") + text.count("?")
        period = text.count("。") + text.count(".")
        total_punct = excl + ques + period

        if total_punct == 0:
            return

        if excl / max(total_punct, 1) > 0.5:
            self._punctuation_style = "enthusiastic"
        elif ques / max(total_punct, 1) > 0.4:
            self._punctuation_style = "expressive"
        elif period / max(total_punct, 1) > 0.6:
            self._punctuation_style = "thoughtful"
        else:
            self._punctuation_style = "normal"

    # ================================================================
    # 持久化
    # ================================================================

    def _save(self) -> None:
        try:
            data = {
                "avg_length": self._avg_length,
                "word_freq": dict(self._word_freq.most_common(MAX_WORDS)),
                "ending_freq": dict(self._ending_freq.most_common(MAX_ENDINGS)),
                "punctuation_style": self._punctuation_style,
                "emoji_frequency": self._emoji_frequency,
                "message_count": self._message_count,
                "last_analyze": self._last_analyze,
            }
            self._data_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass

    def _load(self) -> None:
        if self._data_path.exists():
            try:
                data = json.loads(self._data_path.read_text(encoding="utf-8"))
                self._avg_length = data.get("avg_length", 20.0)
                self._word_freq = Counter(data.get("word_freq", {}))
                self._ending_freq = Counter(data.get("ending_freq", {}))
                self._punctuation_style = data.get("punctuation_style", "normal")
                self._emoji_frequency = data.get("emoji_frequency", 0.0)
                self._message_count = data.get("message_count", 0)
                self._last_analyze = data.get("last_analyze", 0.0)
            except Exception:
                pass

    @property
    def stats(self) -> dict:
        return {
            "message_count": self._message_count,
            "avg_length": round(self._avg_length, 1),
            "punctuation_style": self._punctuation_style,
            "top_words": self._word_freq.most_common(5),
            "unique_words": len(self._word_freq),
        }


# ================================================================
# 自动发现接口
# ================================================================

class LanguageStyleModule(MemoryModule):
    name = "language_style"

    def init(self, data_dir="data/memory", **kwargs):
        self._impl = LanguageStyleLearner(data_dir=data_dir)

    def get_context_prompt(self, message: str = "") -> str:
        if not hasattr(self, '_impl'):
            return ""
        return self._impl.get_style_hint()

    def on_message(self, user_msg: str = "", ai_reply: str = "") -> None:
        if user_msg and hasattr(self, '_impl'):
            self._impl.on_user_message(user_msg)


MODULE = LanguageStyleModule
