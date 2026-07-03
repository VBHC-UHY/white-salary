"""
white_salary/core/memory/social_pattern.py

社交模式 — 学习每个人的互动模式。

借鉴v2的features/social_pattern.py（243行）：
  - 统计每个用户的消息频率/时间偏好/字数/话题
  - 按user_id独立追踪
  - 不用LLM，纯统计

自动发现：导出MODULE供MemoryManager加载。
"""

import json
import time
from collections import Counter
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path

from loguru import logger
from white_salary.core.memory.module_base import MemoryModule


@dataclass
class UserPattern:
    """一个用户的社交模式。"""
    user_id: str = ""
    user_name: str = ""
    total_messages: int = 0
    avg_length: float = 0.0                # 平均消息长度
    active_hours: list[int] = field(default_factory=list)  # 最活跃的小时
    message_lengths: list[int] = field(default_factory=list)  # 最近50条的长度
    hour_distribution: dict = field(default_factory=dict)  # {hour: count}
    first_seen: float = 0.0
    last_seen: float = 0.0


class SocialPatternTracker:
    """社交模式追踪器。"""

    def __init__(self, data_dir: str = "data/memory") -> None:
        self._data_path = Path(data_dir) / "social_patterns.json"
        self._data_path.parent.mkdir(parents=True, exist_ok=True)
        self._patterns: dict[str, UserPattern] = {}
        self._load()

    def on_message(self, user_id: str, user_name: str, message: str) -> None:
        """记录一条消息。"""
        if not message or not user_id:
            return

        if user_id not in self._patterns:
            self._patterns[user_id] = UserPattern(
                user_id=user_id, user_name=user_name,
                first_seen=time.time(),
            )

        p = self._patterns[user_id]
        p.total_messages += 1
        p.last_seen = time.time()
        if user_name:
            p.user_name = user_name

        # 消息长度
        msg_len = len(message)
        p.message_lengths.append(msg_len)
        if len(p.message_lengths) > 50:
            p.message_lengths = p.message_lengths[-50:]
        p.avg_length = sum(p.message_lengths) / len(p.message_lengths)

        # 时间分布
        hour = datetime.now().hour
        hour_str = str(hour)
        p.hour_distribution[hour_str] = p.hour_distribution.get(hour_str, 0) + 1

        # 计算活跃时段（top 3）
        sorted_hours = sorted(p.hour_distribution.items(), key=lambda x: x[1], reverse=True)
        p.active_hours = [int(h) for h, _ in sorted_hours[:3]]

        if p.total_messages % 20 == 0:
            self._save()

    def get_pattern(self, user_id: str) -> dict:
        """获取用户的社交模式。"""
        p = self._patterns.get(user_id)
        if not p:
            return {}
        return {
            "name": p.user_name,
            "total_messages": p.total_messages,
            "avg_length": round(p.avg_length, 1),
            "active_hours": p.active_hours,
            "message_style": "简短" if p.avg_length < 15 else "中等" if p.avg_length < 50 else "详细",
        }

    def get_prompt(self, user_id: str) -> str:
        """生成社交模式提示。"""
        pattern = self.get_pattern(user_id)
        if not pattern or pattern.get("total_messages", 0) < 10:
            return ""
        return (
            f"[{pattern['name']}的聊天习惯] "
            f"消息风格: {pattern['message_style']}，"
            f"活跃时段: {pattern['active_hours']}"
        )

    def _save(self) -> None:
        try:
            data = {k: asdict(v) for k, v in self._patterns.items()}
            self._data_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _load(self) -> None:
        if self._data_path.exists():
            try:
                data = json.loads(self._data_path.read_text(encoding="utf-8"))
                for k, d in data.items():
                    self._patterns[k] = UserPattern(**d)
            except Exception:
                pass

    @property
    def stats(self) -> dict:
        return {"tracked_users": len(self._patterns)}


class SocialPatternModule(MemoryModule):
    name = "social_pattern"

    def init(self, data_dir: str = "data/memory", **kwargs) -> None:
        self._impl = SocialPatternTracker(data_dir=data_dir)

    def on_message(self, user_msg: str = "", ai_reply: str = "",
                   user_id: str = "desktop",
                   is_group: bool = False) -> None:
        """
        2026-07-02 审计修复（批4）：BUG2残留——旧签名写死"desktop"，所有平台
        用户的社交模式统计全串到同一账下。改新签名透传真实user_id
        （参照emotion_memory.py:284已修写法），走manager新签名路径。
        """
        if user_msg and hasattr(self, '_impl'):
            self._impl.on_message(user_id, "用户", user_msg)


MODULE = SocialPatternModule
