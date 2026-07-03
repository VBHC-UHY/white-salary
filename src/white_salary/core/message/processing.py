"""
white_salary/core/message/processing.py

消息处理模块 — 消息缓冲、消息路由、时间感知。

借鉴v2的message_buffer/message_router/time_perception，整合为一个模块。
"""

import asyncio
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, Callable, Awaitable

from loguru import logger


# ================================================================
# 1. TimePerception — 时间感知
# ================================================================

class TimePerception:
    """
    时间感知 — AI理解时间流逝，生成时间相关的上下文。

    使用方式:
        tp = TimePerception()
        tp.record_interaction(user_id)
        hint = tp.get_time_context(user_id)
        # → "距离上次聊天已经过了3小时" / "现在是深夜了" 等
    """

    def __init__(self) -> None:
        self._last_interaction: dict[str, float] = {}  # user_id -> timestamp
        self._session_start: dict[str, float] = {}     # user_id -> session start

    def record_interaction(self, user_id: str) -> None:
        now = time.time()
        if user_id not in self._session_start:
            self._session_start[user_id] = now
        self._last_interaction[user_id] = now

    def get_time_context(self, user_id: str) -> str:
        """生成时间相关的上下文提示（注入system prompt）。"""
        parts = []
        now = datetime.now()

        # 精确日期时间
        parts.append(f"现在是{now.year}年{now.month}月{now.day}日 {now.hour}:{now.minute:02d}")

        # 当前时段
        hour = now.hour
        if 0 <= hour < 6:
            parts.append("深夜/凌晨")
        elif 6 <= hour < 9:
            parts.append("早上")
        elif 9 <= hour < 12:
            parts.append("上午")
        elif 12 <= hour < 14:
            parts.append("中午")
        elif 14 <= hour < 18:
            parts.append("下午")
        elif 18 <= hour < 21:
            parts.append("晚上")
        else:
            parts.append("深夜了")

        # 距离上次聊天
        last = self._last_interaction.get(user_id)
        if last:
            gap = time.time() - last
            if gap > 86400:
                days = int(gap / 86400)
                parts.append(f"距离上次聊天已经过了{days}天")
            elif gap > 3600:
                hours = int(gap / 3600)
                parts.append(f"距离上次聊天过了{hours}小时")
            elif gap > 600:
                mins = int(gap / 60)
                parts.append(f"刚才聊过，过了{mins}分钟")

        # 当前会话时长
        session_start = self._session_start.get(user_id)
        if session_start:
            duration = (time.time() - session_start) / 60
            if duration > 60:
                parts.append(f"这次已经聊了{int(duration)}分钟")

        # 星期几
        weekdays = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        parts.append(f"今天是{weekdays[now.weekday()]}")

        return "；".join(parts) if parts else ""

    def get_gap_minutes(self, user_id: str) -> float:
        """获取距离上次交互的分钟数。"""
        last = self._last_interaction.get(user_id, 0)
        if last == 0:
            return float('inf')
        return (time.time() - last) / 60


# ================================================================
# 2. MessageBuffer — 消息缓冲（合并连发消息）
# ================================================================

class MessageBuffer:
    """
    消息缓冲器 — 用户连续发多条消息时，等3秒合并后再处理。

    借鉴v2的message_buffer.py：
      - 3秒主超时（收到第一条消息后等3秒）
      - 每收到新消息重置计时器（等最后一条消息后再等3秒）
      - 1秒最小间隔（最后一条消息后至少等1秒）
      - 超过20条直接flush

    使用方式:
        buffer = MessageBuffer()
        # 用户连发3条
        buffer.add("user1", "你好")      → None（缓冲中）
        buffer.add("user1", "在吗")      → None（缓冲中）
        # 3秒后自动flush
        merged = await buffer.wait_and_flush("user1")  → "你好\n在吗"
    """

    def __init__(
        self,
        wait_timeout: float = 5.0,     # 每条消息后等5秒看有没有下一条
        min_wait: float = 3.0,         # 最后一条消息后至少等3秒确认说完
        max_buffer: int = 20,          # 超过20条直接flush
        max_total_wait: float = 30.0,  # 最长等30秒封顶
    ) -> None:
        self._timeout = wait_timeout
        self._min_wait = min_wait
        self._max = max_buffer
        self._max_total = max_total_wait
        self._first_msg_time: dict[str, float] = {}  # 第一条消息的时间
        self._buffers: dict[str, list[str]] = {}
        self._last_msg_time: dict[str, float] = {}
        self._tasks: dict[str, Optional[asyncio.Task]] = {}
        self._callbacks: dict[str, Optional[Callable]] = {}

    def add(self, user_id: str, text: str) -> bool:
        """
        添加消息到缓冲。返回True=需要等待flush，False=已满直接处理。
        """
        buf = self._buffers.setdefault(user_id, [])
        buf.append(text)
        now = time.time()
        self._last_msg_time[user_id] = now

        # 记录第一条消息时间（用于30秒封顶）
        if user_id not in self._first_msg_time:
            self._first_msg_time[user_id] = now

        if len(buf) >= self._max:
            return False  # 满了，调用方应该立即flush
        return True  # 缓冲中

    async def wait_and_flush(self, user_id: str) -> Optional[str]:
        """
        等待缓冲超时后返回合并的消息。

        流程：
          1. 收到消息后等5秒
          2. 5秒内有新消息 → 重置，再等5秒
          3. 最后一条消息后至少等3秒确认说完
          4. 总等待不超过30秒封顶
        """
        while True:
            # 检查30秒封顶
            first = self._first_msg_time.get(user_id, time.time())
            total_waited = time.time() - first
            if total_waited >= self._max_total:
                return self._flush(user_id)

            # 等5秒
            remaining_total = self._max_total - total_waited
            wait_time = min(self._timeout, remaining_total)
            await asyncio.sleep(wait_time)

            # 检查是否有新消息
            last = self._last_msg_time.get(user_id, 0)
            since_last = time.time() - last

            # 最后一条消息后不到3秒，再等一等
            if since_last < self._min_wait:
                extra = min(self._min_wait - since_last, self._max_total - (time.time() - first))
                if extra > 0:
                    await asyncio.sleep(extra)

            # 再检查一次
            new_last = self._last_msg_time.get(user_id, 0)
            if new_last > last:
                # 有新消息进来了，继续等
                continue

            # 没有新消息了，flush
            return self._flush(user_id)

    def flush_now(self, user_id: str) -> Optional[str]:
        """立即flush（不等待）。"""
        return self._flush(user_id)

    def has_pending(self, user_id: str) -> bool:
        """是否有待处理的缓冲消息。"""
        return bool(self._buffers.get(user_id))

    def _flush(self, user_id: str) -> Optional[str]:
        buf = self._buffers.pop(user_id, [])
        self._last_msg_time.pop(user_id, None)
        self._first_msg_time.pop(user_id, None)
        if not buf:
            return None
        merged = "\n".join(buf)
        if len(buf) > 1:
            logger.debug(f"[Buffer] 合并{len(buf)}条消息: {merged[:50]}")
        return merged


# ================================================================
# 3. MessageRouter — 消息路由
# ================================================================

@dataclass
class RouteRule:
    """路由规则。"""
    name: str
    pattern: Optional[str] = None       # 正则匹配
    keywords: list[str] = field(default_factory=list)  # 关键词匹配
    handler: str = "chat"               # 处理器名称
    priority: int = 50                  # 优先级（越小越高）


# 默认路由规则
DEFAULT_ROUTES = [
    RouteRule(name="command", pattern=r"^[/!！]", handler="command", priority=10),
    RouteRule(name="image_request", keywords=["画一张", "生成图片", "画个", "画一个"], handler="image", priority=20),
    RouteRule(name="search_request", keywords=["搜索", "搜一下", "查一下", "百度"], handler="search", priority=20),
    RouteRule(name="screenshot_request", keywords=["看看屏幕", "看我屏幕", "截屏", "看看我在干什么"], handler="vision", priority=20),
    RouteRule(name="bilibili_request", keywords=["B站", "b站", "哔哩哔哩", "bilibili"], handler="bilibili", priority=20),
    # 2026-07-03 工具实现（批9）：提醒/静默高频意图关键词直连提示（沿批2 recall 打样机制）。
    # 取消提醒的优先级高于设提醒——"不用提醒我了"包含"提醒我"，先匹配取消
    RouteRule(name="reminder_cancel_request",
              keywords=["取消提醒", "不用提醒", "别提醒", "把提醒删"],
              handler="reminder_cancel", priority=21),
    RouteRule(name="reminder_request",
              keywords=["提醒我", "别忘了叫我", "闹钟", "到点叫我", "记得叫我"],
              handler="reminder", priority=22),
    RouteRule(name="quiet_request",
              keywords=["别吵", "安静点", "安静一会", "我要工作", "闭嘴一会",
                        "别打扰", "别烦我", "让我静静", "静音"],
              handler="quiet", priority=22),
    RouteRule(name="recall_request", keywords=["之前聊过", "还记得", "上次说的", "QQ上说的"], handler="recall", priority=25),
    RouteRule(name="default", handler="chat", priority=100),
]


class MessageRouter:
    """
    消息路由器 — 根据消息内容决定处理方式。

    注意：这只是辅助分类，实际的工具调用还是由tool_llm决定。
    路由结果可以用来优化tool_llm的判断（提前告诉它可能需要什么工具）。

    使用方式:
        router = MessageRouter()
        route = router.classify("帮我搜一下Python教程")
        # → "search"
    """

    def __init__(self, rules: Optional[list[RouteRule]] = None) -> None:
        self._rules = sorted(rules or DEFAULT_ROUTES, key=lambda r: r.priority)
        # 预编译正则
        self._compiled = {}
        for rule in self._rules:
            if rule.pattern:
                self._compiled[rule.name] = re.compile(rule.pattern)

    def classify(self, text: str) -> str:
        """
        分类消息，返回handler名称。

        Returns:
            "chat" / "command" / "image" / "search" / "vision" / "bilibili" / "recall"
        """
        for rule in self._rules:
            # 正则匹配
            if rule.name in self._compiled:
                if self._compiled[rule.name].search(text):
                    return rule.handler

            # 关键词匹配
            if rule.keywords:
                for kw in rule.keywords:
                    if kw in text:
                        return rule.handler

        return "chat"

    def get_tool_hint(self, text: str) -> str:
        """
        生成工具提示（可注入system prompt帮助tool_llm判断）。

        Returns:
            提示文本，或空字符串
        """
        route = self.classify(text)
        hints = {
            "image": "用户可能想要生成图片，考虑使用generate_image工具。",
            "search": "用户可能想搜索信息，考虑使用web_search工具。",
            "vision": "用户可能想让你看屏幕，考虑使用screenshot工具。",
            "bilibili": "用户提到了B站，考虑使用bilibili_search工具。",
            "recall": "用户想回忆之前聊过的内容，考虑使用recall_conversation工具。",
            # 2026-07-03 工具实现（批9）：提醒/静默意图提示（注入 tool_llm 判断上下文，
            # 提高高频意图的工具选中率；参数抽取仍交给 tool_llm，不强制执行）
            "reminder": "用户想设置定时提醒，考虑使用set_reminder工具"
                        "（content=提醒内容，when=时间原话）。",
            "reminder_cancel": "用户想取消已设的提醒，考虑使用cancel_reminder工具。",
            "quiet": "用户想让你安静/别打扰他，考虑使用set_quiet_mode工具"
                     "（busy=忙碌一段时间自动恢复，silent=静默到用户解除）。",
        }
        return hints.get(route, "")
