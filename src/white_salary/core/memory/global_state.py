"""
white_salary/core/memory/global_state.py

全局状态 — 跨私聊/群聊同步AI的情绪和决策状态。

借鉴v2的features/global_state.py（346行）：
  - AI的情绪状态在所有对话中保持一致
  - 一个对话里生气了，其他对话里也会表现出来
  - 记录最近的重要决策（防止不同对话里给出矛盾的决策）
  - 不用LLM，纯状态同步

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
class GlobalStateData:
    """AI的全局状态。"""
    current_mood: str = "neutral"        # 当前心情
    mood_intensity: float = 0.5          # 心情强度 0-1
    mood_reason: str = ""                # 心情原因
    mood_updated_at: float = 0.0

    energy_level: float = 1.0            # 精力 0-1（随时间和互动消耗）
    is_resting: bool = False             # 是否在休息
    is_angry: bool = False               # 是否在生气

    recent_decisions: list[dict] = field(default_factory=list)  # 最近的决策
    active_conversations: dict = field(default_factory=dict)    # 活跃的对话 {session_id: last_msg_time}

    last_saved: float = 0.0


# 心情自然回归速率（每分钟）
MOOD_RECOVERY_RATE = 0.01
ENERGY_RECOVERY_RATE = 0.005
MAX_DECISIONS = 20


class GlobalStateManager:
    """全局状态管理器（单例使用）。"""

    def __init__(self, data_dir: str = "data/memory") -> None:
        self._data_path = Path(data_dir) / "global_state.json"
        self._data_path.parent.mkdir(parents=True, exist_ok=True)
        self._state = GlobalStateData()
        self._load()

    @property
    def state(self) -> GlobalStateData:
        self._apply_natural_recovery()
        return self._state

    def set_mood(self, mood: str, intensity: float = 0.5, reason: str = "") -> None:
        """设置全局心情。"""
        self._state.current_mood = mood
        self._state.mood_intensity = max(0, min(1, intensity))
        self._state.mood_reason = reason
        self._state.mood_updated_at = time.time()

        # 特殊状态
        if mood == "angry" and intensity > 0.7:
            self._state.is_angry = True
        elif mood in ("happy", "neutral", "calm"):
            self._state.is_angry = False

        self._save()

    def record_decision(self, decision: str, context: str = "") -> None:
        """记录一次重要决策。"""
        self._state.recent_decisions.append({
            "decision": decision[:100],
            "context": context[:50],
            "time": time.time(),
        })
        if len(self._state.recent_decisions) > MAX_DECISIONS:
            self._state.recent_decisions = self._state.recent_decisions[-MAX_DECISIONS:]
        self._save()

    def consume_energy(self, amount: float = 0.05) -> None:
        """消耗精力。"""
        self._state.energy_level = max(0, self._state.energy_level - amount)

    def register_conversation(self, session_id: str) -> None:
        """注册一个活跃对话。"""
        self._state.active_conversations[session_id] = time.time()
        # 清理超过1小时不活跃的
        now = time.time()
        self._state.active_conversations = {
            sid: t for sid, t in self._state.active_conversations.items()
            if now - t < 3600
        }

    def get_prompt(self) -> str:
        """生成全局状态提示。"""
        s = self.state
        parts = []

        # 心情
        if s.current_mood != "neutral" and s.mood_intensity > 0.3:
            mood_cn = {
                "happy": "开心", "sad": "难过", "angry": "生气",
                "tired": "疲惫", "excited": "兴奋", "anxious": "焦虑",
            }
            mood_str = mood_cn.get(s.current_mood, s.current_mood)
            parts.append(f"当前心情: {mood_str}")
            if s.mood_reason:
                parts.append(f"原因: {s.mood_reason}")

        # 精力
        if s.energy_level < 0.3:
            parts.append("精力较低，可能会表现得有点累")
        elif s.energy_level < 0.1:
            parts.append("非常疲惫")

        # 生气状态
        if s.is_angry:
            parts.append("目前还在生气中")

        if not parts:
            return ""
        return "[全局状态]\n" + "\n".join(f"  - {p}" for p in parts)

    def _apply_natural_recovery(self) -> None:
        """自然恢复（心情趋向中性，精力恢复）。"""
        now = time.time()
        minutes_since = (now - self._state.mood_updated_at) / 60 if self._state.mood_updated_at else 0

        # 心情自然消退
        if minutes_since > 5 and self._state.mood_intensity > 0.2:
            decay = MOOD_RECOVERY_RATE * minutes_since
            self._state.mood_intensity = max(0.1, self._state.mood_intensity - decay)
            if self._state.mood_intensity <= 0.2:
                self._state.current_mood = "neutral"
                self._state.is_angry = False

        # 精力恢复
        if self._state.energy_level < 1.0:
            recovery = ENERGY_RECOVERY_RATE * minutes_since
            self._state.energy_level = min(1.0, self._state.energy_level + recovery)

    def _save(self) -> None:
        try:
            self._state.last_saved = time.time()
            self._data_path.write_text(
                json.dumps(asdict(self._state), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    def _load(self) -> None:
        if self._data_path.exists():
            try:
                data = json.loads(self._data_path.read_text(encoding="utf-8"))
                self._state = GlobalStateData(**data)
            except Exception:
                pass

    @property
    def stats(self) -> dict:
        return {
            "mood": self._state.current_mood,
            "energy": round(self._state.energy_level, 2),
            "is_angry": self._state.is_angry,
            "active_conversations": len(self._state.active_conversations),
            "recent_decisions": len(self._state.recent_decisions),
        }


class GlobalStateModule(MemoryModule):
    name = "global_state"

    def init(self, data_dir="data/memory", **kwargs):
        self._impl = GlobalStateManager(data_dir=data_dir)

    def get_context_prompt(self, message: str = "") -> str:
        if not hasattr(self, '_impl'):
            return ""
        return self._impl.get_prompt()

    def on_message(self, user_msg: str = "", ai_reply: str = "") -> None:
        if hasattr(self, '_impl'):
            self._impl.consume_energy(0.03)


MODULE = GlobalStateModule
