"""
white_salary/core/memory/relationship_milestone.py

关系里程碑系统 — 记录AI与用户之间的重要事件。

借鉴v2的relationship_milestone.py：
  - 正面里程碑：初次聊天/被夸/生日/深夜聊天/分享秘密
  - 负面里程碑：吵架/被骂/被冷落
  - 关系变化：成为朋友/关系加深/和好/疏远

功能：
  - 自动从对话中检测重要事件
  - 记录事件（类型/时间/内容/情绪/强度）
  - 影响emotion_tracker的基准心情
  - 生成里程碑摘要注入对话上下文

自动发现：导出MODULE供MemoryManager自动加载
"""

import json
import time
from dataclasses import dataclass, asdict
from enum import Enum
from pathlib import Path
from typing import Optional

from loguru import logger
from white_salary.core.memory.module_base import MemoryModule


# ================================================================
# 里程碑类型
# ================================================================

class MilestoneType(str, Enum):
    # 正面
    FIRST_CHAT = "first_chat"           # 第一次聊天
    FIRST_COMPLIMENT = "first_compliment" # 第一次被夸
    BIRTHDAY = "birthday"               # 生日
    LATE_NIGHT_CHAT = "late_night_chat"  # 深夜聊天
    SHARED_SECRET = "shared_secret"     # 分享秘密
    COMFORTED_ME = "comforted_me"       # 安慰了白
    MADE_ME_LAUGH = "made_me_laugh"     # 逗白笑了
    SPECIAL_MOMENT = "special_moment"   # 特别时刻
    GIFT = "gift"                       # 送礼物

    # 负面
    FIRST_FIGHT = "first_fight"         # 第一次吵架
    HURT_ME = "hurt_me"                 # 伤害了白
    IGNORED_ME = "ignored_me"           # 冷落白
    SAID_MEAN = "said_mean"             # 说难听的话

    # 关系变化
    BECAME_FRIENDS = "became_friends"    # 成为朋友
    BECAME_CLOSE = "became_close"       # 关系加深
    MADE_UP = "made_up"                 # 和好
    DRIFTED_APART = "drifted_apart"     # 疏远
    REUNITED = "reunited"               # 重逢


# 里程碑的情绪影响
MILESTONE_MOOD_EFFECTS = {
    MilestoneType.FIRST_CHAT: +5,
    MilestoneType.FIRST_COMPLIMENT: +8,
    MilestoneType.BIRTHDAY: +10,
    MilestoneType.LATE_NIGHT_CHAT: +3,
    MilestoneType.SHARED_SECRET: +6,
    MilestoneType.COMFORTED_ME: +7,
    MilestoneType.MADE_ME_LAUGH: +5,
    MilestoneType.SPECIAL_MOMENT: +8,
    MilestoneType.GIFT: +10,
    MilestoneType.FIRST_FIGHT: -8,
    MilestoneType.HURT_ME: -10,
    MilestoneType.IGNORED_ME: -5,
    MilestoneType.SAID_MEAN: -7,
    MilestoneType.BECAME_FRIENDS: +10,
    MilestoneType.BECAME_CLOSE: +8,
    MilestoneType.MADE_UP: +6,
    MilestoneType.DRIFTED_APART: -6,
    MilestoneType.REUNITED: +8,
}

# 检测关键词
MILESTONE_KEYWORDS = {
    MilestoneType.FIRST_COMPLIMENT: ["好厉害", "真棒", "可爱", "好看", "漂亮", "喜欢你", "爱你"],
    MilestoneType.SHARED_SECRET: ["秘密", "只告诉你", "别跟别人说", "私下说"],
    MilestoneType.COMFORTED_ME: ["别难过", "没关系", "会好的", "抱抱", "陪你"],
    MilestoneType.MADE_ME_LAUGH: ["哈哈", "笑死", "太好笑了"],
    MilestoneType.GIFT: ["送你", "给你", "礼物", "红包"],
    MilestoneType.HURT_ME: ["讨厌你", "滚", "闭嘴", "别烦我"],
    MilestoneType.SAID_MEAN: ["废物", "垃圾", "白痴", "傻逼"],
    MilestoneType.IGNORED_ME: ["不想理你", "别说了", "算了"],
}


@dataclass
class Milestone:
    """一条里程碑记录。"""
    id: str = ""
    user_id: str = ""
    user_name: str = ""
    milestone_type: str = ""
    content: str = ""             # 事件内容摘要
    emotion: str = "neutral"      # 当时的情绪
    intensity: float = 0.5        # 情绪强度0-1
    timestamp: float = 0.0
    time_str: str = ""


class RelationshipMilestone:
    """
    关系里程碑管理。

    使用方式:
        ms = RelationshipMilestone(data_dir="data/memory")
        detected = ms.detect_from_message("小白", "1234567890", "你好可爱啊！")
        ms.get_milestones_for_user("1234567890")
    """

    MAX_MILESTONES = 500

    def __init__(self, data_dir: str = "data/memory") -> None:
        self._path = Path(data_dir) / "relationship_milestones.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._milestones: list[Milestone] = []
        self._first_chat_users: set[str] = set()  # 已记录过"第一次聊天"的用户
        self._load()

    def detect_from_message(self, user_name: str, user_id: str,
                            message: str) -> list[Milestone]:
        """
        从用户消息中检测里程碑事件。

        Returns:
            新检测到的里程碑列表
        """
        detected = []

        # 第一次聊天检测
        if user_id not in self._first_chat_users:
            self._first_chat_users.add(user_id)
            # 检查是否真的是第一次（历史里没有这个用户的记录）
            existing = [m for m in self._milestones if m.user_id == user_id]
            if not existing:
                ms = self._add(user_name, user_id, MilestoneType.FIRST_CHAT,
                               f"与{user_name}的第一次聊天")
                detected.append(ms)

        # 深夜聊天检测
        from datetime import datetime
        hour = datetime.now().hour
        if 0 <= hour < 5:
            # 检查今天是否已记录过
            today = time.strftime("%Y-%m-%d")
            has_today = any(
                m.user_id == user_id
                and m.milestone_type == MilestoneType.LATE_NIGHT_CHAT
                and m.time_str.startswith(today)
                for m in self._milestones
            )
            if not has_today:
                ms = self._add(user_name, user_id, MilestoneType.LATE_NIGHT_CHAT,
                               f"凌晨{hour}点和{user_name}聊天")
                detected.append(ms)

        # 关键词检测
        for ms_type, keywords in MILESTONE_KEYWORDS.items():
            for kw in keywords:
                if kw in message:
                    # 同类型24小时内不重复记录
                    recent = any(
                        m.user_id == user_id
                        and m.milestone_type == ms_type
                        and time.time() - m.timestamp < 86400
                        for m in self._milestones
                    )
                    if not recent:
                        ms = self._add(user_name, user_id, ms_type,
                                       f"{user_name}: {message[:30]}")
                        detected.append(ms)
                    break  # 每种类型只检测一次

        return detected

    def get_milestones_for_user(self, user_id: str, limit: int = 10) -> list[Milestone]:
        """获取某个用户的里程碑。"""
        user_ms = [m for m in self._milestones if m.user_id == user_id]
        return sorted(user_ms, key=lambda m: m.timestamp, reverse=True)[:limit]

    def get_recent(self, limit: int = 10) -> list[Milestone]:
        """获取最近的里程碑。"""
        return sorted(self._milestones, key=lambda m: m.timestamp, reverse=True)[:limit]

    def get_mood_effect(self, milestone: Milestone) -> float:
        """获取里程碑对心情的影响值。"""
        try:
            ms_type = MilestoneType(milestone.milestone_type)
            return MILESTONE_MOOD_EFFECTS.get(ms_type, 0) * milestone.intensity
        except ValueError:
            return 0

    def get_summary(self, user_id: str = "", max_items: int = 5) -> str:
        """生成里程碑摘要（可注入对话上下文）。"""
        if user_id:
            ms_list = self.get_milestones_for_user(user_id, max_items)
        else:
            ms_list = self.get_recent(max_items)

        if not ms_list:
            return ""

        lines = ["[关系里程碑]"]
        for m in ms_list:
            lines.append(f"  [{m.time_str}] {m.content}")
        return "\n".join(lines)

    @property
    def count(self) -> int:
        return len(self._milestones)

    def _add(self, user_name: str, user_id: str, ms_type: MilestoneType,
             content: str, emotion: str = "neutral", intensity: float = 0.6) -> Milestone:
        import uuid
        ms = Milestone(
            id=str(uuid.uuid4())[:8],
            user_id=user_id,
            user_name=user_name,
            milestone_type=ms_type.value,
            content=content,
            emotion=emotion,
            intensity=intensity,
            timestamp=time.time(),
            time_str=time.strftime("%Y-%m-%d %H:%M"),
        )
        self._milestones.append(ms)
        if len(self._milestones) > self.MAX_MILESTONES:
            self._milestones = self._milestones[-self.MAX_MILESTONES:]
        self._save()
        logger.debug(f"[Milestone] 新里程碑: {ms_type.value} - {content[:30]}")
        return ms

    def _load(self) -> None:
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    self._milestones = [Milestone(**d) for d in data]
                elif isinstance(data, dict):
                    self._milestones = [Milestone(**d) for d in data.get("milestones", [])]
                # 记录已有"第一次聊天"的用户
                for m in self._milestones:
                    self._first_chat_users.add(m.user_id)
            except Exception as e:
                logger.debug(f"[Milestone] 加载失败: {e}")

    def _save(self) -> None:
        try:
            data = [asdict(m) for m in self._milestones]
            self._path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass


# ================================================================
# 自动发现接口（MemoryModule）
# ================================================================

class MilestoneModule(MemoryModule):
    name = "relationship_milestone"

    def init(self, data_dir="data/memory", **kwargs):
        self._impl = RelationshipMilestone(data_dir=data_dir)

    def get_context_prompt(self, message=""):
        if hasattr(self, '_impl'):
            return self._impl.get_summary()
        return ""

    def on_message(self, user_msg="", ai_reply=""):
        # 需要user_id才能检测里程碑，这里暂时跳过
        # 实际的检测在qq_handler和websocket_handler中调用
        pass


MODULE = MilestoneModule
