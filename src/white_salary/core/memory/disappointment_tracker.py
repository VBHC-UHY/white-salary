"""
white_salary/core/memory/disappointment_tracker.py

失望追踪 — 累积负面事件，影响关系态度。

借鉴v2的features/disappointment_tracker.py（272行）：
  - 记录让AI失望/受伤的事件
  - 失望值累积，影响对该用户的态度
  - 时间自然消退（慢慢原谅）
  - 严重事件记录（betrayal级别）

不用LLM，纯规则+积分。

自动发现：导出MODULE供MemoryManager加载。
"""

import json
import math
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional

from loguru import logger
from white_salary.core.memory.module_base import MemoryModule


@dataclass
class DisappointmentRecord:
    """一条失望记录。"""
    event_type: str = ""        # insult/lie/ignore/betray/break_promise
    content: str = ""
    user_id: str = ""
    user_name: str = ""
    severity: float = 1.0       # 严重度 0.1-3.0
    timestamp: float = 0.0
    resolved: bool = False       # 是否已释怀


# 失望事件检测
_HURT_PATTERNS = {
    "insult": (["骂", "白痴", "傻逼", "垃圾", "废物", "滚", "闭嘴"], 1.5),
    "lie": (["骗", "说谎", "假的", "忽悠"], 2.0),
    "ignore": (["不理", "无视", "不回", "不想说"], 1.0),
    "break_promise": (["说好的", "答应了", "忘了吧", "算了不去了", "食言"], 2.0),
    "betray": (["背叛", "出卖", "告诉别人了", "泄露"], 3.0),
}

DECAY_HALF_LIFE_DAYS = 14  # 14天失望值衰减一半
MAX_RECORDS = 100


class DisappointmentTracker:
    """失望追踪器。"""

    def __init__(self, data_dir: str = "data/memory") -> None:
        self._data_path = Path(data_dir) / "disappointments.json"
        self._data_path.parent.mkdir(parents=True, exist_ok=True)
        self._records: list[DisappointmentRecord] = []
        self._load()

    def detect_and_record(self, message: str, user_id: str = "",
                          user_name: str = "") -> Optional[DisappointmentRecord]:
        """检测消息中的伤害事件。"""
        if not message:
            return None

        for event_type, (keywords, severity) in _HURT_PATTERNS.items():
            for kw in keywords:
                if kw in message:
                    record = DisappointmentRecord(
                        event_type=event_type,
                        content=message[:100],
                        user_id=user_id,
                        user_name=user_name,
                        severity=severity,
                        timestamp=time.time(),
                    )
                    self._records.append(record)
                    if len(self._records) > MAX_RECORDS:
                        self._records = self._records[-MAX_RECORDS:]
                    self._save()
                    return record
        return None

    def get_disappointment_level(self, user_id: str = "") -> float:
        """获取对某人的当前失望值（考虑时间衰减）。"""
        now = time.time()
        total = 0.0
        for r in self._records:
            if user_id and r.user_id != user_id:
                continue
            if r.resolved:
                continue
            days = (now - r.timestamp) / 86400
            decay = math.pow(0.5, days / DECAY_HALF_LIFE_DAYS)
            total += r.severity * decay
        return round(total, 2)

    def get_prompt(self, user_id: str = "") -> str:
        """生成失望提示。"""
        level = self.get_disappointment_level(user_id)
        if level < 1.0:
            return ""
        if level < 3.0:
            return "[情绪状态] 有一点受伤/失望，但还好"
        if level < 6.0:
            return "[情绪状态] 感到明显的失望和受伤"
        return "[情绪状态] 非常受伤和失望，态度会比较冷淡"

    def resolve(self, user_id: str) -> int:
        """标记某人的所有失望为已释怀（道歉后调用）。"""
        count = 0
        for r in self._records:
            if r.user_id == user_id and not r.resolved:
                r.resolved = True
                count += 1
        if count:
            self._save()
        return count

    def _save(self) -> None:
        try:
            self._data_path.write_text(
                json.dumps([asdict(r) for r in self._records], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    def _load(self) -> None:
        if self._data_path.exists():
            try:
                data = json.loads(self._data_path.read_text(encoding="utf-8"))
                self._records = [DisappointmentRecord(**d) for d in data]
            except Exception:
                pass

    @property
    def stats(self) -> dict:
        return {"total_records": len(self._records),
                "unresolved": sum(1 for r in self._records if not r.resolved)}


class DisappointmentModule(MemoryModule):
    name = "disappointment_tracker"

    def init(self, data_dir="data/memory", **kwargs):
        self._impl = DisappointmentTracker(data_dir=data_dir)

    def get_context_prompt(self, message: str = "") -> str:
        if not hasattr(self, '_impl'):
            return ""
        return self._impl.get_prompt()

    def on_message(self, user_msg: str = "", ai_reply: str = "") -> None:
        if user_msg and hasattr(self, '_impl'):
            self._impl.detect_and_record(user_msg)


MODULE = DisappointmentModule
