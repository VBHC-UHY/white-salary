"""
white_salary/adapters/platform/group_engager.py

群聊自主参与系统 — AI在群聊中的主动行为。

借鉴v2的group_engager.py：
  - v2的水群机制（5小时沉默后35%概率发表情/图片），保留核心逻辑
  - v2的追问机制（3分钟后10%概率跟进），保留
  - v2的话题跳转检测（发链接/@别人时暂时屏蔽），保留
  - v2发送水群消息时只发表情包不发纯文字，这是好设计

功能：
  - 群聊沉默太久时主动发表情包活跃气氛
  - 对话后追问（3分钟后小概率跟进）
  - 话题跳转检测（防止在无关话题中乱回）
  - 与StickerManager配合发送表情包
"""

import random
import time
from dataclasses import dataclass, field
from typing import Optional

from loguru import logger


@dataclass
class GroupState:
    """单个群的状态。"""
    group_id: str = ""
    last_active: float = 0.0           # 最后活跃时间
    last_water: float = 0.0            # 上次水群时间
    last_bot_reply: float = 0.0        # 上次机器人回复时间
    last_user_id: str = ""             # 最后发消息的用户
    last_user_text: str = ""           # 最后消息内容
    pending_followup: bool = False     # 是否有待追问
    followup_set_at: float = 0.0       # 追问设定时间


class GroupEngager:
    """
    群聊自主参与管理器。

    使用方式:
        engager = GroupEngager()
        # 每条群消息都调用
        engager.on_message(group_id, user_id, text)
        # 检查是否要水群
        group_id = engager.pick_idle_group()
        if group_id:
            sticker = engager.build_water_message()
            # 发送sticker
    """

    # 配置
    MIN_IDLE_SECONDS = 18000       # 5小时没人说话才考虑水群
    MIN_WATER_INTERVAL = 18000     # 两次水群间隔至少5小时
    WATER_PROBABILITY = 0.35       # 35%概率水群
    FOLLOWUP_DELAY = 180           # 追问延迟3分钟
    FOLLOWUP_PROBABILITY = 0.10    # 10%概率追问
    TOPIC_JUMP_BLOCK_SECONDS = 600 # 话题跳转后屏蔽10分钟

    # 话题跳转关键词
    TOPIC_JUMP_KEYWORDS = ["http", "https", "转发", "通知", "公告", "[CQ:forward"]

    def __init__(self) -> None:
        self._groups: dict[str, GroupState] = {}

    def on_message(self, group_id: str, user_id: str, text: str) -> None:
        """收到群消息时调用。"""
        state = self._get_state(group_id)
        state.last_active = time.time()
        state.last_user_id = user_id
        state.last_user_text = text[:100]

    def on_bot_reply(self, group_id: str) -> None:
        """机器人回复后调用，设置追问。"""
        state = self._get_state(group_id)
        state.last_bot_reply = time.time()
        state.pending_followup = True
        state.followup_set_at = time.time()

    def should_skip(self, group_id: str, user_id: str, text: str) -> bool:
        """
        检测是否应该跳过这条消息（话题跳转检测）。

        Returns:
            True = 跳过不回复
        """
        # 话题跳转检测
        for kw in self.TOPIC_JUMP_KEYWORDS:
            if kw in text:
                return True
        return False

    def pick_idle_group(self) -> Optional[str]:
        """
        找一个适合水群的群（沉默够久 + 间隔够长）。

        Returns:
            group_id，或None
        """
        now = time.time()
        candidates = []

        for gid, state in self._groups.items():
            idle = now - state.last_active
            since_water = now - state.last_water

            if idle >= self.MIN_IDLE_SECONDS and since_water >= self.MIN_WATER_INTERVAL:
                candidates.append(gid)

        if not candidates:
            return None

        # 随机选一个
        group_id = random.choice(candidates)

        # 概率检查
        if random.random() > self.WATER_PROBABILITY:
            return None

        return group_id

    def build_water_action(self) -> dict:
        """
        构建水群动作（返回动作描述，由调用方执行）。

        Returns:
            {"type": "sticker"} 或 {"type": "text", "content": "..."}
        """
        # 优先发表情包（v2的经验：纯文字水群容易尬）
        return {"type": "sticker"}

    def record_water_sent(self, group_id: str) -> None:
        """记录水群成功。"""
        state = self._get_state(group_id)
        state.last_water = time.time()
        logger.info(f"[GroupEngager] 水群: {group_id}")

    def pick_followup_group(self) -> Optional[str]:
        """
        找一个需要追问的群。

        Returns:
            group_id，或None
        """
        now = time.time()

        for gid, state in self._groups.items():
            if not state.pending_followup:
                continue

            # 超过追问延迟
            elapsed = now - state.followup_set_at
            if elapsed < self.FOLLOWUP_DELAY:
                continue

            # 超过30分钟就放弃
            if elapsed > 1800:
                state.pending_followup = False
                continue

            # 概率检查
            if random.random() > self.FOLLOWUP_PROBABILITY:
                state.pending_followup = False
                continue

            state.pending_followup = False
            return gid

        return None

    def build_followup_hint(self, group_id: str) -> str:
        """
        构建追问提示（给LLM的指令）。

        Returns:
            提示文本
        """
        state = self._get_state(group_id)
        return (
            f"之前在群里和{state.last_user_id}聊了一些，"
            f"他最后说了「{state.last_user_text[:30]}」，"
            f"已经过去几分钟了，可以自然地跟进一下话题或发个表情包。"
        )

    def _get_state(self, group_id: str) -> GroupState:
        if group_id not in self._groups:
            self._groups[group_id] = GroupState(group_id=group_id)
        return self._groups[group_id]
