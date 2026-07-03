"""
white_salary/core/memory/multimodal_memory.py

多模态记忆 — 图片/语音记忆处理。

借鉴v2的services/multimodal_memory.py（260行）：
  - 记录用户发送的图片描述
  - 记录语音转文字的内容
  - 图片描述由vision_llm生成（不是主模型）
  - 多模态记忆参与检索

自动发现：导出MODULE供MemoryManager加载。
"""

import json
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional

from loguru import logger
from white_salary.core.memory.module_base import MemoryModule


@dataclass
class MultimodalEntry:
    """一条多模态记忆。"""
    entry_id: str = ""
    media_type: str = ""        # image/voice/video
    description: str = ""       # 内容描述（图片描述/语音文字）
    context: str = ""           # 发送时的对话上下文
    user_id: str = ""
    timestamp: float = 0.0


MAX_ENTRIES = 200


class MultimodalMemoryStore:
    """多模态记忆存储。"""

    def __init__(self, data_dir: str = "data/memory") -> None:
        self._data_path = Path(data_dir) / "multimodal_memory.json"
        self._data_path.parent.mkdir(parents=True, exist_ok=True)
        self._entries: list[MultimodalEntry] = []
        self._load()

    def add_image_memory(self, description: str, context: str = "",
                         user_id: str = "") -> MultimodalEntry:
        """添加图片记忆。"""
        return self._add("image", description, context, user_id)

    def add_voice_memory(self, transcript: str, context: str = "",
                         user_id: str = "") -> MultimodalEntry:
        """添加语音记忆（语音转文字后的内容）。"""
        return self._add("voice", transcript, context, user_id)

    def _add(self, media_type: str, description: str, context: str,
             user_id: str) -> MultimodalEntry:
        entry = MultimodalEntry(
            entry_id=f"mm_{int(time.time() * 1000)}",
            media_type=media_type,
            description=description[:300],
            context=context[:100],
            user_id=user_id,
            timestamp=time.time(),
        )
        self._entries.append(entry)
        if len(self._entries) > MAX_ENTRIES:
            self._entries = self._entries[-MAX_ENTRIES:]
        self._save()
        return entry

    def search(self, keyword: str, limit: int = 5) -> list[MultimodalEntry]:
        """搜索多模态记忆。"""
        results = [e for e in self._entries if keyword in e.description]
        return results[-limit:]

    def get_recent(self, limit: int = 5) -> list[MultimodalEntry]:
        return self._entries[-limit:]

    def _save(self) -> None:
        try:
            self._data_path.write_text(
                json.dumps([asdict(e) for e in self._entries], ensure_ascii=False, indent=2),
                encoding="utf-8")
        except Exception:
            pass

    def _load(self) -> None:
        if self._data_path.exists():
            try:
                data = json.loads(self._data_path.read_text(encoding="utf-8"))
                self._entries = [MultimodalEntry(**d) for d in data]
            except Exception:
                pass

    @property
    def stats(self) -> dict:
        types = {}
        for e in self._entries:
            types[e.media_type] = types.get(e.media_type, 0) + 1
        return {"total": len(self._entries), "by_type": types}


class MultimodalMemoryModule(MemoryModule):
    name = "multimodal_memory"

    def init(self, data_dir="data/memory", **kwargs):
        self._impl = MultimodalMemoryStore(data_dir=data_dir)


MODULE = MultimodalMemoryModule
