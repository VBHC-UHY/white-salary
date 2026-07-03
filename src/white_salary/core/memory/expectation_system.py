"""
white_salary/core/memory/expectation_system.py

期望系统 — AI对未来的期望和预期。

记录AI期待发生的事情（"期待下周一起看电影"）。不用LLM。

自动发现：导出MODULE供MemoryManager加载。
"""

import json
import re
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path

from loguru import logger
from white_salary.core.memory.module_base import MemoryModule


@dataclass
class Expectation:
    """一条期望。"""
    content: str = ""
    user_id: str = ""
    created_at: float = 0.0
    fulfilled: bool = False

# 期望检测关键词
_EXPECT_KEYWORDS = [
    "期待", "等不及", "希望", "盼望", "下次", "以后",
    "到时候", "约好了", "说好的", "计划", "打算",
]

MAX_EXPECTATIONS = 50


class ExpectationSystem:
    """期望存储。"""

    def __init__(self, data_dir: str = "data/memory") -> None:
        self._data_path = Path(data_dir) / "expectations.json"
        self._data_path.parent.mkdir(parents=True, exist_ok=True)
        self._expectations: list[Expectation] = []
        self._load()

    def detect_and_store(self, message: str, user_id: str = "") -> bool:
        """检测并存储期望。"""
        if not message:
            return False
        for kw in _EXPECT_KEYWORDS:
            if kw in message:
                self._expectations.append(Expectation(
                    content=message[:100], user_id=user_id,
                    created_at=time.time(),
                ))
                if len(self._expectations) > MAX_EXPECTATIONS:
                    self._expectations = self._expectations[-MAX_EXPECTATIONS:]
                self._save()
                return True
        return False

    def get_active(self, limit: int = 5) -> list[Expectation]:
        return [e for e in self._expectations if not e.fulfilled][-limit:]

    def get_prompt(self) -> str:
        active = self.get_active(3)
        if not active:
            return ""
        lines = ["[期待的事]"]
        for e in active:
            lines.append(f"  - {e.content[:40]}")
        return "\n".join(lines)

    def _save(self) -> None:
        try:
            self._data_path.write_text(
                json.dumps([asdict(e) for e in self._expectations], ensure_ascii=False, indent=2),
                encoding="utf-8")
        except Exception:
            pass

    def _load(self) -> None:
        if self._data_path.exists():
            try:
                data = json.loads(self._data_path.read_text(encoding="utf-8"))
                self._expectations = [Expectation(**d) for d in data]
            except Exception:
                pass

    @property
    def stats(self) -> dict:
        return {"total": len(self._expectations),
                "active": sum(1 for e in self._expectations if not e.fulfilled)}


class ExpectationModule(MemoryModule):
    name = "expectation_system"

    def init(self, data_dir="data/memory", **kwargs):
        self._impl = ExpectationSystem(data_dir=data_dir)

    def get_context_prompt(self, message: str = "") -> str:
        if not hasattr(self, '_impl'):
            return ""
        return self._impl.get_prompt()

    def on_message(self, user_msg: str = "", ai_reply: str = "") -> None:
        if user_msg and hasattr(self, '_impl'):
            self._impl.detect_and_store(user_msg)


MODULE = ExpectationModule
