"""
white_salary/core/auto_chat.py

主动聊天系统 — AI会主动找用户聊天，像真正的桌面伙伴。

借鉴WhiteSalary-v2的auto_chat.py但为桌面应用重新设计：
  - v2是QQ机器人，通过QQ发消息；我们是桌面宠物，通过WebSocket推到前端
  - v2的追问机制过于复杂（3层递进），简化为2层
  - v2没有用户偏好检查，我们加上开关
  - v2被禁用了，说明原设计有骚扰问题，我们降低频率

功能：
  - 早安/晚安问候（可配置时间）
  - 关心提醒（从近况、状态和共同记忆中选择方向）
  - 随机话题聊天（3-6小时间隔）
  - 追问机制（用户长时间不理时，温柔地再问一次）
  - 启动保护期（启动后2分钟内不触发，避免竞态）
  - 每日限制（最多主动聊3次，不骚扰）

使用方式：
  由WebSocket handler在连接时启动，断开时停止。
  通过回调函数发送消息到前端。
"""

import asyncio
import random
import time
from dataclasses import dataclass
from typing import Optional, Callable, Awaitable

from loguru import logger


@dataclass
class AutoChatConfig:
    """主动聊天配置。"""
    enabled: bool = True

    # 活跃时间段
    active_start_hour: int = 7
    active_end_hour: int = 23

    # 问候
    morning_greeting: bool = True
    morning_hour: int = 8
    night_greeting: bool = True
    night_hour: int = 22

    # 关心提醒
    care_reminder: bool = True
    care_interval: int = 14400     # 4小时

    # 随机聊天
    random_chat: bool = True
    random_min_interval: int = 10800   # 3小时
    random_max_interval: int = 21600   # 6小时
    random_probability: float = 0.3

    # 追问
    followup_enabled: bool = True
    followup_delay: int = 1200     # 20分钟
    max_followup: int = 1

    # 每日限制
    daily_limit: int = 3


# 话题池（分类，避免重复）
TOPIC_POOL = {
    "日常": [
        "从用户今天正在做的事情自然接一个轻松话题",
        "问问最近有没有发生让用户印象深刻的小事",
        "从最近对话里找一个还可以继续聊的细节",
        "聊聊用户刚完成或正在推进的一件事",
        "分享一个和当前氛围相符的日常观察",
    ],
    "兴趣": [
        "从用户喜欢的游戏、作品或创作中挑一个具体话题",
        "根据用户近期兴趣问一个有内容、不是泛泛而谈的问题",
        "回忆用户提过的音乐、视频或故事并自然延伸",
        "聊聊用户最近学到或想尝试的新东西",
    ],
    "关心": [
        "结合最近互动判断用户是否疲惫，再决定要不要关心",
        "留意用户是否长时间专注，可以轻轻提醒放松一下",
        "问问手头事情是否顺利，不要把关心说成例行提醒",
        "如果用户像是在忙，就只留一句不打扰的陪伴",
    ],
    "分享": [
        "提出一个和最近对话有关、值得一起想想的小问题",
        "分享一个有具体内容的想法，不要只说自己有话想说",
        "从共同记忆中挑一件事，说说现在的新感受",
    ],
    "撒娇": [
        "关系足够亲近时，轻松地表示想和用户聊两句",
        "用一点亲昵感自然接话，但不要指责用户没理自己",
        "开一个符合当前关系的小玩笑，引出新的话题",
        "表达想陪着用户，不要求用户必须立刻回应",
    ],
}

CARE_DIRECTIONS = (
    "问问用户最近的精神和心情怎么样",
    "关心用户是不是坐得太久，可以稍微活动一下",
    "看看用户眼睛或肩颈是不是累了",
    "结合最近对话，关心用户手头的事情进展得顺不顺",
    "从共同记忆里找一个轻松的话题，陪用户缓一缓",
    "问问用户现在更想安静待着，还是随便聊两句",
)


class AutoChatManager:
    """
    主动聊天管理器。

    使用方式:
        manager = AutoChatManager(send_callback=my_send_func)
        await manager.start()
        manager.notify_user_active()
        await manager.stop()
    """

    def __init__(
        self,
        send_callback: Callable[[str], Awaitable[None]],
        config: Optional[AutoChatConfig] = None,
        *,
        user_id: str = "desktop",
        affinity_data_dir: str = "data/affinity",
    ) -> None:
        self._send = send_callback
        self._config = config or AutoChatConfig()
        self._user_id = str(user_id or "desktop")
        self._affinity_data_dir = affinity_data_dir
        self._running = False
        self._task: Optional[asyncio.Task] = None

        now = time.time()
        self._start_time = now
        self._last_user_active = now
        self._last_auto_chat = 0.0
        # A restart is not evidence that the user needs an immediate care
        # prompt. Start the interval here so repeated launches do not produce
        # the same proactive question a few minutes apart.
        self._last_care_time = now
        self._daily_count = 0
        self._daily_reset_date = ""
        self._followup_count = 0
        self._morning_done = False
        self._night_done = False
        self._last_topic_category = ""
        self._recent_topic_directions: list[str] = []
        self._recent_care_directions: list[str] = []

    async def start(self) -> None:
        """启动后台循环。"""
        if self._running:
            return
        self._running = True
        self._start_time = time.time()
        self._last_care_time = self._start_time
        self._task = asyncio.create_task(self._loop())
        logger.info("[AutoChat] 已启动")

    async def stop(self) -> None:
        """停止。"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("[AutoChat] 已停止")

    def notify_user_active(self) -> None:
        """用户交互时调用，重置追问。"""
        self._last_user_active = time.time()
        self._followup_count = 0

    async def _loop(self) -> None:
        """主循环 — 每60秒检查一次。"""
        while self._running:
            try:
                await asyncio.sleep(60)
                if not self._config.enabled:
                    continue

                # 启动保护期
                if time.time() - self._start_time < 120:
                    continue

                self._maybe_reset_daily()

                from datetime import datetime
                hour = datetime.now().hour

                # 不在活跃时段
                if hour < self._config.active_start_hour or hour >= self._config.active_end_hour:
                    if (self._config.night_greeting and not self._night_done
                            and hour == self._config.night_hour):
                        await self._send_greeting("night")
                    continue

                if self._daily_count >= self._config.daily_limit:
                    continue

                # 优先级检查
                if (self._config.morning_greeting and not self._morning_done
                        and hour == self._config.morning_hour):
                    await self._send_greeting("morning")
                    continue

                if self._config.care_reminder:
                    if time.time() - self._last_care_time > self._config.care_interval:
                        if self._should_send_care(hour):
                            await self._send_care(hour)
                            continue

                if self._config.followup_enabled:
                    idle = time.time() - self._last_user_active
                    if (idle > self._config.followup_delay
                            and self._followup_count < self._config.max_followup
                            and self._last_auto_chat > self._last_user_active):
                        await self._send_followup()
                        continue

                if self._config.random_chat:
                    idle_since = time.time() - max(self._last_auto_chat, self._last_user_active)
                    if idle_since > self._config.random_min_interval:
                        # 好感度影响主动聊天概率（好感越高越主动）
                        prob = self._config.random_probability * self._get_affinity_multiplier()
                        if random.random() < prob:
                            await self._send_random_topic()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[AutoChat] 错误: {e}")
                await asyncio.sleep(30)

    def _get_affinity_multiplier(self) -> float:
        """好感度→主动聊天概率系数。家人2倍，好友1.5倍，陌生人1倍，反感0.3倍。"""
        try:
            from white_salary.core.affinity.manager import AffinityManager
            aff = AffinityManager.get_for_user(
                self._user_id,
                data_dir=self._affinity_data_dir,
            )
            stats = aff.get_stats()
            if stats.get("is_family"):
                return 2.0
            lv = stats.get("level_value", 0)
            if lv >= 4:
                return 2.0
            elif lv >= 2:
                return 1.5
            elif lv >= 0:
                return 1.0
            else:
                return 0.3
        except Exception:
            return 1.0

    def _maybe_reset_daily(self) -> None:
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        if today != self._daily_reset_date:
            self._daily_reset_date = today
            self._daily_count = 0
            self._morning_done = False
            self._night_done = False

    async def _send_greeting(self, greeting_type: str) -> None:
        """发送问候触发提示给主模型。"""
        if greeting_type == "morning":
            self._morning_done = True
            await self._do_send("现在是早上，该跟用户说早安了。自然一点，可以关心一下今天的安排。")
        else:
            self._night_done = True
            await self._do_send("现在很晚了，该跟用户说晚安了。提醒用户早点休息，不要熬夜。")

    def _should_send_care(self, hour: int) -> bool:
        return hour in (11, 12, 14, 15, 17, 18, 20, 21)

    async def _send_care(self, hour: int) -> None:
        """给主模型一个不重复、不过度强调时间的关心方向。"""
        available = [
            item for item in CARE_DIRECTIONS
            if item not in self._recent_care_directions
        ] or list(CARE_DIRECTIONS)
        direction = random.choice(available)
        self._recent_care_directions.append(direction)
        self._recent_care_directions = self._recent_care_directions[-3:]
        hint = (
            "结合最近对话和用户状态，尝试一次轻松自然的关心。"
            f"可参考方向：{direction}。"
            "当前时段只作为内部背景，不要主动报时，也不要重复最近主动聊过的话题。"
        )
        self._last_care_time = time.time()
        await self._do_send(hint)

    async def _send_followup(self) -> None:
        """用户很久没回复，温柔地追问。"""
        self._followup_count += 1
        await self._do_send("用户已经很久没有回复了，可以温柔地问一下用户在不在、是不是在忙。不要太急切。")

    async def _send_random_topic(self) -> None:
        """随机找话题聊（20%概率变成怀旧分享）。"""
        # 10%概率分享B站推荐视频
        if random.random() < 0.1:
            bili_hint = await self._get_bili_recommendation()
            if bili_hint:
                await self._do_send(bili_hint)
                return

        # 5%概率在QQ空间发条说说
        if random.random() < 0.05:
            try:
                from white_salary.core.qzone.social_manager import get_social_manager
                qzone_mgr = get_social_manager()
                posted = await qzone_mgr.auto_post(trigger="random")
                if posted:
                    logger.info(f"[AutoChat] QQ空间自动发说说: {posted[:30]}")
                    # 发完说说不影响正常聊天，继续往下走
            except Exception as e:
                logger.debug(f"[AutoChat] QQ空间自动发说说失败: {e}")

        # 20%概率分享美好回忆（好感度高时更高）
        nostalgia_chance = 0.2 * self._get_affinity_multiplier()
        if random.random() < nostalgia_chance:
            hint = self._get_nostalgia_hint()
            if hint:
                await self._do_send(hint)
                return

        categories = [c for c in TOPIC_POOL if c != self._last_topic_category]
        if not categories:
            categories = list(TOPIC_POOL.keys())
        cat = random.choice(categories)
        self._last_topic_category = cat
        available = [
            item for item in TOPIC_POOL[cat]
            if item not in self._recent_topic_directions
        ] or list(TOPIC_POOL[cat])
        topic = random.choice(available)
        self._recent_topic_directions.append(topic)
        self._recent_topic_directions = self._recent_topic_directions[-5:]
        await self._do_send(
            "想主动陪用户聊一会儿。"
            f"只把这个语义方向当灵感：{topic}。"
            "结合最近对话重新组织一句自然开场，不要照抄方向，"
            "不要例行问吃饭或报时，也不要重复最近主动聊过的话题。"
        )

    async def _get_bili_recommendation(self) -> Optional[str]:
        """获取B站推荐视频并生成分享提示。"""
        try:
            from white_salary.adapters.tools.builtin.bilibili import bilibili_feed
            result = await bilibili_feed()
            if "推荐视频" in result and "bilibili.com" in result:
                import re
                urls = re.findall(r'https://www\.bilibili\.com/video/BV\w+', result)
                if urls:
                    return (
                        f"我在B站发现了一个有趣的视频想和你一起看！\n"
                        f"用watch_video工具打开这个视频，和用户一起看。\n"
                        f"视频链接: {urls[0]}\n"
                        f"用你自己的话自然地说'我发现一个好玩的视频，一起看看'，然后调用watch_video工具。"
                    )
        except Exception:
            pass
        return None

    def _get_nostalgia_hint(self) -> Optional[str]:
        """尝试获取怀旧提示。"""
        try:
            from white_salary.core.memory.enhanced.integrator import get_integrator
            hint = get_integrator().get_auto_chat_hint()
            if hint:
                return hint
        except Exception:
            pass
        return None

    async def _do_send(self, text: str) -> None:
        try:
            await self._send(text)
            self._last_auto_chat = time.time()
            self._daily_count += 1
            logger.info(f"[AutoChat] 发送({self._daily_count}/{self._config.daily_limit}): {text[:30]}")
        except Exception as e:
            logger.warning(f"[AutoChat] 发送失败: {e}")
