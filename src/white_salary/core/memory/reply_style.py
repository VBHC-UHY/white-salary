"""
white_salary/core/memory/reply_style.py

回复风格记忆 — 记住过去的回复模式，5层相似度检测，避免重复模式。

借鉴v2的features/reply_style_memory.py（1217行）：
  - 保存最近500条AI回复+上下文
  - 5层相似度检测：完全相同/开头相同/结构相同/关键词重叠/语气相同
  - 检测到重复时返回"避免XXX风格"提示
  - 不用LLM，纯文本比较

不同于response_dedup（短期去重5条+5分钟窗口）。
这个是长期的回复模式记忆（500条+持久化）。

自动发现：导出MODULE供MemoryManager加载。
"""

import json
import re
import time
from collections import Counter
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

from loguru import logger
from white_salary.core.memory.module_base import MemoryModule


@dataclass
class ReplyRecord:
    """一条回复记录。"""
    content: str = ""
    context: str = ""       # 用户消息（上下文）
    timestamp: float = 0.0
    fingerprint: str = ""   # 清理后的指纹


# 句式模板检测（结构相同）
_STRUCTURE_PATTERNS = [
    (r"^.{0,5}啊[，。！]", "感叹开头"),
    (r"^哈哈", "哈哈开头"),
    (r"^嗯.{0,3}[，。]", "嗯开头"),
    (r"[！]{2,}", "多感叹号"),
    (r"^好的[，。]", "好的开头"),
    (r"^没关系", "安慰开头"),
    (r"^其实", "其实开头"),
    (r"~$|～$", "波浪号结尾"),
    (r"^是吗", "反问开头"),
    (r"嘛[。！~]?$", "嘛结尾"),
]

MAX_HISTORY = 500


class ReplyStyleMemory:
    """
    回复风格记忆。

    使用方式:
        rsm = ReplyStyleMemory(data_dir)
        rsm.record_reply("今天天气真好呢~", context="今天天气怎么样")
        warnings = rsm.check_similarity("今天天气真不错呢~")
        # → ["开头相似: 最近3条回复都以'今天'开头", "波浪号结尾: 最近5条回复都用~结尾"]
    """

    def __init__(self, data_dir: str = "data/memory") -> None:
        self._data_path = Path(data_dir) / "reply_style_history.json"
        self._data_path.parent.mkdir(parents=True, exist_ok=True)
        self._history: list[ReplyRecord] = []
        self._load()

    def record_reply(self, reply: str, context: str = "") -> None:
        """记录一条AI回复。"""
        if not reply or len(reply) < 3:
            return

        record = ReplyRecord(
            content=reply[:300],
            context=context[:200],
            timestamp=time.time(),
            fingerprint=self._make_fingerprint(reply),
        )
        self._history.append(record)

        if len(self._history) > MAX_HISTORY:
            self._history = self._history[-MAX_HISTORY:]

        self._save_debounced()

    def check_similarity(self, candidate: str, window: int = 20) -> list[str]:
        """
        5层相似度检测，返回警告列表。

        Args:
            candidate: 候选回复
            window: 检查最近N条

        Returns:
            警告列表（空=没问题）
        """
        if not candidate or len(self._history) < 3:
            return []

        recent = self._history[-window:]
        warnings = []

        # ===== 第1层：完全相同 =====
        fp = self._make_fingerprint(candidate)
        for r in recent:
            if r.fingerprint == fp:
                warnings.append("完全相同：之前说过几乎一样的话")
                break

        # ===== 第2层：开头相同 =====
        prefix = candidate[:8]
        same_prefix = sum(1 for r in recent[-10:] if r.content[:8] == prefix)
        if same_prefix >= 3:
            warnings.append(f"开头重复：最近{same_prefix}条回复都以「{prefix}」开头")

        # ===== 第3层：结构相同 =====
        candidate_structures = self._detect_structures(candidate)
        if candidate_structures:
            for struct_name in candidate_structures:
                count = sum(
                    1 for r in recent[-10:]
                    if struct_name in self._detect_structures(r.content)
                )
                if count >= 4:
                    warnings.append(f"句式重复：最近多条回复都是「{struct_name}」模式")
                    break

        # ===== 第4层：关键词重叠 =====
        candidate_kw = set(self._extract_keywords(candidate))
        if candidate_kw:
            overlap_count = 0
            for r in recent[-10:]:
                r_kw = set(self._extract_keywords(r.content))
                if r_kw and len(candidate_kw & r_kw) / len(candidate_kw | r_kw) > 0.6:
                    overlap_count += 1
            if overlap_count >= 3:
                warnings.append("用词重复：最近多条回复使用了太多相同的词")

        # ===== 第5层：语气/长度模式 =====
        candidate_len = len(candidate)
        recent_lens = [len(r.content) for r in recent[-10:]]
        if recent_lens:
            avg_len = sum(recent_lens) / len(recent_lens)
            # 检查是否所有回复长度都差不多
            if all(abs(l - avg_len) < avg_len * 0.2 for l in recent_lens[-5:]):
                if abs(candidate_len - avg_len) < avg_len * 0.2:
                    warnings.append(f"长度单调：最近回复长度都在{int(avg_len)}字左右，缺乏变化")

        return warnings

    def get_style_hint(self, candidate: str = "") -> str:
        """
        生成风格提示（注入system prompt）。

        检查候选回复（如果有），或分析最近的回复模式，
        返回建议"避免什么/尝试什么"。
        """
        if len(self._history) < 5:
            return ""

        hints = []

        if candidate:
            warnings = self.check_similarity(candidate)
            if warnings:
                hints.extend(warnings)

        # 通用模式检测
        recent = self._history[-15:]

        # 检查常用开头词
        first_chars = [r.content[:4] for r in recent if len(r.content) > 4]
        char_counts = Counter(first_chars)
        common_starts = [c for c, n in char_counts.most_common(3) if n >= 4]
        if common_starts:
            hints.append(f"避免总是以「{'」「'.join(common_starts)}」开头")

        # 检查是否总是用同样的标点结尾
        endings = [r.content[-1] for r in recent if r.content]
        end_counts = Counter(endings)
        dominant_end = end_counts.most_common(1)
        if dominant_end and dominant_end[0][1] >= len(recent) * 0.7:
            hints.append(f"结尾太单一，不要总是用「{dominant_end[0][0]}」结尾")

        if not hints:
            return ""

        return "[回复风格提醒]\n" + "\n".join(f"  - {h}" for h in hints[:3])

    # ================================================================
    # 内部方法
    # ================================================================

    @staticmethod
    def _make_fingerprint(text: str) -> str:
        """文本指纹。"""
        cleaned = re.sub(r'[，。！？、；：\s,.:;!?\[\]()~～]+', '', text)
        return cleaned.lower()

    @staticmethod
    def _detect_structures(text: str) -> list[str]:
        """检测文本的句式结构。"""
        structures = []
        for pattern, name in _STRUCTURE_PATTERNS:
            if re.search(pattern, text):
                structures.append(name)
        return structures

    @staticmethod
    def _extract_keywords(text: str) -> list[str]:
        """提取关键词（简单2-3字切分）。"""
        segments = re.split(r'[，。！？、\s,.:;!?]+', text)
        keywords = []
        for seg in segments:
            seg = seg.strip()
            if 2 <= len(seg) <= 4:
                keywords.append(seg)
            elif len(seg) > 4:
                for i in range(0, len(seg) - 1, 2):
                    keywords.append(seg[i:i + 2])
        return keywords

    # ================================================================
    # 持久化
    # ================================================================

    _save_counter = 0

    def _save_debounced(self) -> None:
        self._save_counter += 1
        if self._save_counter % 20 == 0:
            self._save()

    def _save(self) -> None:
        try:
            data = [asdict(r) for r in self._history]
            self._data_path.write_text(
                json.dumps(data, ensure_ascii=False), encoding="utf-8"
            )
        except Exception as e:
            logger.warning(f"[ReplyStyle] 保存失败: {e}")

    def _load(self) -> None:
        if not self._data_path.exists():
            return
        try:
            data = json.loads(self._data_path.read_text(encoding="utf-8"))
            for d in data:
                self._history.append(ReplyRecord(**d))
            logger.debug(f"[ReplyStyle] 加载: {len(self._history)}条回复历史")
        except Exception as e:
            logger.warning(f"[ReplyStyle] 加载失败: {e}")

    def force_save(self) -> None:
        self._save()

    @property
    def stats(self) -> dict:
        return {
            "total_replies": len(self._history),
            "max_history": MAX_HISTORY,
        }


# ================================================================
# 自动发现接口
# ================================================================

class ReplyStyleModule(MemoryModule):
    """回复风格记忆模块 — 自动发现注册。"""
    name = "reply_style"

    def init(self, data_dir="data/memory", **kwargs):
        self._impl = ReplyStyleMemory(data_dir=data_dir)

    def get_context_prompt(self, message: str = "") -> str:
        """返回回复风格提醒。"""
        if not hasattr(self, '_impl'):
            return ""
        return self._impl.get_style_hint()

    def on_message(self, user_msg: str = "", ai_reply: str = "") -> None:
        """记录AI回复。"""
        if ai_reply and hasattr(self, '_impl'):
            self._impl.record_reply(ai_reply, context=user_msg)

    def on_session_end(self) -> None:
        if hasattr(self, '_impl'):
            self._impl.force_save()


MODULE = ReplyStyleModule
