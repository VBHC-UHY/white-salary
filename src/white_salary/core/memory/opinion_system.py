"""
white_salary/core/memory/opinion_system.py

观点系统 — 记住AI对话题的观点/立场，保持一致。

借鉴v2的features/opinion_system.py（766行）：
  - 记录AI对不同话题表达过的观点
  - 下次聊到同一话题时保持一致
  - 防止自相矛盾（今天说喜欢猫明天说不喜欢）
  - 观点可以随时间演变（但不会突然反转）
  - 不用LLM，纯规则存储和匹配

自动发现：导出MODULE供MemoryManager加载。
"""

import json
import re
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional

from loguru import logger
from white_salary.core.memory.module_base import MemoryModule


@dataclass
class Opinion:
    """一条观点。"""
    topic: str = ""             # 话题（"猫"、"编程"、"数学"）
    stance: str = ""            # 立场（positive/negative/neutral/mixed）
    expression: str = ""        # 表达过的原文（"我觉得猫很可爱"）
    confidence: float = 0.5     # 确信度 0-1（多次表达会提升）
    times_expressed: int = 1    # 表达次数
    first_expressed: float = 0.0
    last_expressed: float = 0.0


# 观点检测模式
_OPINION_PATTERNS = [
    # 正面观点
    (r"(?:我)?觉得(.{1,10})(?:很|特别|超|太)(?:好|棒|厉害|有趣|可爱|好看)", "positive"),
    (r"(?:我)?喜欢(.{1,10})", "positive"),
    (r"(.{1,10})(?:真的)?(?:很好|不错|挺好|蛮好)", "positive"),

    # 负面观点
    (r"(?:我)?(?:觉得|认为)(.{1,10})(?:很|太|特别)(?:差|烂|无聊|难|讨厌|丑)", "negative"),
    (r"(?:我)?(?:不喜欢|讨厌|反感)(.{1,10})", "negative"),
    (r"(.{1,10})(?:太烂了|太差了|太无聊|没意思)", "negative"),

    # 中立/混合
    (r"(?:我)?觉得(.{1,10})(?:还行|一般|还好|凑合)", "neutral"),
    (r"(.{1,10})(?:有好有坏|各有优缺|看情况)", "mixed"),
]

MAX_OPINIONS = 200


class OpinionStore:
    """
    观点存储。

    使用方式:
        store = OpinionStore(data_dir)
        store.detect_and_record("我觉得猫很可爱")
        opinion = store.get_opinion("猫")
        consistency = store.check_consistency("猫", "negative")
    """

    def __init__(self, data_dir: str = "data/memory") -> None:
        self._data_path = Path(data_dir) / "opinions.json"
        self._data_path.parent.mkdir(parents=True, exist_ok=True)
        self._opinions: dict[str, Opinion] = {}  # topic → Opinion
        self._compiled = [(re.compile(p), stance) for p, stance in _OPINION_PATTERNS]
        self._load()

    def detect_and_record(self, message: str) -> list[Opinion]:
        """检测消息中的观点并记录。"""
        if not message or len(message) < 5:
            return []

        detected = []
        for pattern, stance in self._compiled:
            match = pattern.search(message)
            if match:
                topic = match.group(1).strip()
                if topic and 1 <= len(topic) <= 10:
                    opinion = self._record(topic, stance, message)
                    if opinion:
                        detected.append(opinion)

        if detected:
            self._save()
        return detected

    def _record(self, topic: str, stance: str, expression: str) -> Optional[Opinion]:
        """记录或更新一条观点。"""
        now = time.time()
        key = topic.lower()

        if key in self._opinions:
            op = self._opinions[key]
            if op.stance == stance:
                # 同一立场重复表达→加强确信
                op.confidence = min(1.0, op.confidence + 0.1)
                op.times_expressed += 1
                op.last_expressed = now
                op.expression = expression[:100]  # 更新为最新表达
            else:
                # 不同立场→观点可能在演变
                # 如果新立场表达次数还少，暂不覆盖
                if op.confidence > 0.7:
                    # 高确信度的观点不轻易改变
                    return None
                # 低确信度→可以更新
                op.stance = stance
                op.expression = expression[:100]
                op.confidence = 0.4  # 重置确信度
                op.times_expressed += 1
                op.last_expressed = now
            return op

        # 新观点
        op = Opinion(
            topic=topic,
            stance=stance,
            expression=expression[:100],
            confidence=0.5,
            times_expressed=1,
            first_expressed=now,
            last_expressed=now,
        )
        self._opinions[key] = op

        if len(self._opinions) > MAX_OPINIONS:
            # 删除最旧的
            oldest_key = min(self._opinions, key=lambda k: self._opinions[k].last_expressed)
            del self._opinions[oldest_key]

        return op

    def get_opinion(self, topic: str) -> Optional[Opinion]:
        """获取对某个话题的观点。"""
        return self._opinions.get(topic.lower())

    def check_consistency(self, topic: str, new_stance: str) -> Optional[str]:
        """
        检查新观点是否与已有观点矛盾。

        Returns:
            矛盾警告（None=没矛盾）
        """
        op = self._opinions.get(topic.lower())
        if not op:
            return None

        if op.stance == new_stance:
            return None  # 一致

        if op.confidence < 0.5:
            return None  # 不确信的观点不算矛盾

        stance_cn = {"positive": "正面", "negative": "负面", "neutral": "中立", "mixed": "复杂"}
        return (
            f"[观点一致性] 你之前说过「{op.expression[:30]}」"
            f"（{stance_cn.get(op.stance, op.stance)}），"
            f"请保持一致或自然地解释态度变化。"
        )

    def get_relevant_opinions(self, message: str, limit: int = 3) -> list[Opinion]:
        """找出与当前消息相关的已有观点。"""
        relevant = []
        for key, op in self._opinions.items():
            if key in message.lower() or op.topic in message:
                relevant.append(op)
        relevant.sort(key=lambda o: o.confidence, reverse=True)
        return relevant[:limit]

    def get_opinions_prompt(self, message: str = "") -> str:
        """生成观点提示（注入system prompt）。"""
        if not message:
            return ""

        relevant = self.get_relevant_opinions(message)
        if not relevant:
            return ""

        lines = ["[你表达过的观点（请保持一致）]"]
        for op in relevant:
            stance_cn = {"positive": "👍", "negative": "👎", "neutral": "😐", "mixed": "🤔"}
            icon = stance_cn.get(op.stance, "")
            lines.append(f"  {icon} {op.topic}: {op.expression[:40]}")
        return "\n".join(lines)

    # ================================================================
    # 持久化
    # ================================================================

    def _save(self) -> None:
        try:
            data = {k: asdict(v) for k, v in self._opinions.items()}
            self._data_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            logger.warning(f"[Opinion] 保存失败: {e}")

    def _load(self) -> None:
        if not self._data_path.exists():
            return
        try:
            data = json.loads(self._data_path.read_text(encoding="utf-8"))
            for k, d in data.items():
                self._opinions[k] = Opinion(**d)
            logger.debug(f"[Opinion] 加载: {len(self._opinions)}条观点")
        except Exception as e:
            logger.warning(f"[Opinion] 加载失败: {e}")

    @property
    def stats(self) -> dict:
        stances = {}
        for op in self._opinions.values():
            stances[op.stance] = stances.get(op.stance, 0) + 1
        return {"total_opinions": len(self._opinions), "stance_distribution": stances}


# ================================================================
# 自动发现接口
# ================================================================

class OpinionSystemModule(MemoryModule):
    name = "opinion_system"

    def init(self, data_dir="data/memory", **kwargs):
        self._impl = OpinionStore(data_dir=data_dir)

    def get_context_prompt(self, message: str = "") -> str:
        if not message or not hasattr(self, '_impl'):
            return ""
        return self._impl.get_opinions_prompt(message)

    def on_message(self, user_msg: str = "", ai_reply: str = "") -> None:
        # 记录AI回复中的观点（AI说的才是AI的观点）
        if ai_reply and hasattr(self, '_impl'):
            self._impl.detect_and_record(ai_reply)


MODULE = OpinionSystemModule
