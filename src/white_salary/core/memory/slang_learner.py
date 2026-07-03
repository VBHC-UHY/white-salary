"""
white_salary/core/memory/slang_learner.py

流行语学习 — 学习新网络用语，带语境理解。

借鉴v2的features/slang_learner.py（684行）：
  - 检测不在词库中的新词/网络用语
  - memory_llm推断新词含义（异步后台）
  - 学会的词加入词库
  - JSON持久化

LLM通道：memory_llm（异步后台）

自动发现：导出MODULE供MemoryManager加载。
"""

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Optional

from loguru import logger
from white_salary.core.memory.module_base import MemoryModule


# 常见网络用语模式（已知的不需要学习）
_KNOWN_SLANG = {
    "yyds", "awsl", "xswl", "emo", "dddd", "nsdd", "bdjw",
    "gg", "nb", "666", "233", "886", "520", "1314",
    "绝了", "无语", "笑死", "裂开", "破防", "离谱", "逆天",
    "摆烂", "躺平", "内卷", "凡尔赛", "pua", "cpu", "gpu",
    "家人们", "老铁", "兄弟", "宝子", "集美", "姐妹",
    "社死", "显眼包", "乐子人", "整活", "水灵灵",
}

# 检测可能是新词的模式
_NEW_WORD_PATTERNS = [
    r'[a-zA-Z]{2,6}',           # 短英文缩写
    r'[\u4e00-\u9fff]{2,4}',    # 2-4字中文可能是新词
]

# LLM分析提示
_LEARN_PROMPT = """以下是对话中出现的一些可能的新网络用语/流行语。
请分析哪些是真正的网络用语/流行语（不是普通词语），并解释含义。

待分析词汇（附带上下文）:
{words_with_context}

只返回JSON数组，每个元素格式：
{{"word": "词", "is_slang": true/false, "meaning": "含义", "usage": "用法示例"}}

如果都不是网络用语，返回空数组 []"""

MAX_LEARNED = 200


class SlangLearner:
    """
    流行语学习器。

    使用方式:
        learner = SlangLearner(data_dir)
        candidates = learner.detect_candidates("这个太6了xdm")
        await learner.learn_batch(candidates, llm)
        meaning = learner.get_meaning("xdm")  # → "兄弟们"
    """

    def __init__(self, data_dir: str = "data/memory") -> None:
        self._data_path = Path(data_dir) / "learned_slang.json"
        self._data_path.parent.mkdir(parents=True, exist_ok=True)

        # {word: {"meaning": str, "usage": str, "learned_at": str, "seen_count": int}}
        self._learned: dict[str, dict] = {}
        # {word: {"contexts": [str], "first_seen": float}}
        self._candidates: dict[str, dict] = {}

        self._load()

    def on_message(self, text: str) -> list[str]:
        """
        处理消息，检测并收集可能的新词。

        Returns:
            检测到的候选新词列表
        """
        if not text or len(text) < 2:
            return []

        candidates = self.detect_candidates(text)
        for word in candidates:
            if word not in self._candidates:
                self._candidates[word] = {
                    "contexts": [],
                    "first_seen": time.time(),
                }
            ctx = self._candidates[word]
            ctx["contexts"].append(text[:100])
            if len(ctx["contexts"]) > 5:
                ctx["contexts"] = ctx["contexts"][-5:]

        return candidates

    def detect_candidates(self, text: str) -> list[str]:
        """检测可能的新网络用语。"""
        candidates = []

        # 提取所有2-4字的中文片段和短英文
        words = set()
        for pattern in _NEW_WORD_PATTERNS:
            for match in re.finditer(pattern, text):
                words.add(match.group())

        for word in words:
            word_lower = word.lower()
            # 跳过已知的
            if word_lower in _KNOWN_SLANG:
                continue
            # 跳过已学过的
            if word_lower in self._learned:
                self._learned[word_lower]["seen_count"] = \
                    self._learned[word_lower].get("seen_count", 0) + 1
                continue
            # 跳过太常见的中文词（简单过滤）
            if len(word) <= 2 and all('\u4e00' <= c <= '\u9fff' for c in word):
                # 2字中文词太多了，需要在上下文中出现多次才考虑
                if word in self._candidates and len(self._candidates[word].get("contexts", [])) < 3:
                    continue
            candidates.append(word)

        return candidates

    def get_candidates_for_learning(self, min_seen: int = 3) -> list[tuple[str, list[str]]]:
        """获取出现次数足够多的候选词（准备送给LLM分析）。"""
        ready = []
        for word, info in self._candidates.items():
            if len(info["contexts"]) >= min_seen:
                ready.append((word, info["contexts"]))
        return ready

    async def learn_batch(self, words_with_context: list[tuple[str, list[str]]],
                          llm=None) -> int:
        """用memory_llm批量学习新词。返回学到的数量。"""
        if not words_with_context or not llm:
            return 0

        # 构建提示
        lines = []
        for word, contexts in words_with_context[:10]:  # 最多10个
            ctx_str = " / ".join(contexts[:3])
            lines.append(f"  「{word}」出现在：{ctx_str}")

        try:
            from white_salary.core.interfaces.types import Message, MessageRole
            prompt = _LEARN_PROMPT.format(words_with_context="\n".join(lines))
            reply = await llm.chat_completion(
                [
                    Message(role=MessageRole.SYSTEM, content="你是网络用语专家。只返回JSON。"),
                    Message(role=MessageRole.USER, content=prompt),
                ],
                temperature=0.3,
                max_tokens=500,
            )

            results = self._parse_json_array(reply)
            learned = 0
            for item in results:
                if item.get("is_slang") and item.get("word"):
                    word = item["word"].lower()
                    self._learned[word] = {
                        "meaning": item.get("meaning", ""),
                        "usage": item.get("usage", ""),
                        "learned_at": time.strftime("%Y-%m-%d %H:%M"),
                        "seen_count": 1,
                    }
                    # 从候选中移除
                    self._candidates.pop(word, None)
                    learned += 1

            if learned:
                self._trim()
                self._save()
                logger.info(f"[SlangLearner] 学到{learned}个新词")
            return learned

        except Exception as e:
            logger.error(f"[SlangLearner] 学习失败: {e}")
            return 0

    def get_meaning(self, word: str) -> Optional[str]:
        """查询已学词汇的含义。"""
        info = self._learned.get(word.lower())
        return info.get("meaning") if info else None

    def get_all_learned(self) -> dict[str, dict]:
        """获取所有已学词汇。"""
        return dict(self._learned)

    def _trim(self) -> None:
        if len(self._learned) > MAX_LEARNED:
            # 按seen_count排序，删最少用的
            sorted_words = sorted(
                self._learned.items(),
                key=lambda x: x[1].get("seen_count", 0),
            )
            for word, _ in sorted_words[:len(self._learned) - MAX_LEARNED]:
                del self._learned[word]

    def _parse_json_array(self, text: str) -> list:
        try:
            result = json.loads(text)
            return result if isinstance(result, list) else []
        except json.JSONDecodeError:
            pass
        match = re.search(r'\[[\s\S]*\]', text)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        return []

    # ================================================================
    # 持久化
    # ================================================================

    def _save(self) -> None:
        try:
            self._data_path.write_text(
                json.dumps(self._learned, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    def _load(self) -> None:
        if self._data_path.exists():
            try:
                self._learned = json.loads(
                    self._data_path.read_text(encoding="utf-8")
                )
                logger.debug(f"[SlangLearner] 加载: {len(self._learned)}个已学词汇")
            except Exception:
                pass

    @property
    def stats(self) -> dict:
        return {
            "learned_words": len(self._learned),
            "pending_candidates": len(self._candidates),
        }


# ================================================================
# 自动发现接口
# ================================================================

class SlangLearnerModule(MemoryModule):
    name = "slang_learner"

    def init(self, data_dir="data/memory", **kwargs):
        self._impl = SlangLearner(data_dir=data_dir)

    def on_message(self, user_msg: str = "", ai_reply: str = "") -> None:
        if user_msg and hasattr(self, '_impl'):
            self._impl.on_message(user_msg)


MODULE = SlangLearnerModule
