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
  - 关心提醒（吃饭、喝水、休息、别熬夜）
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
        "今天过得怎么样呀？",
        "在忙什么呢？",
        "最近有什么开心的事吗？",
        "今天天气好像不错呢",
        "有没有发现什么有意思的东西？",
    ],
    "兴趣": [
        "最近有在玩什么游戏吗？",
        "有没有看到什么好看的番/剧？",
        "最近在听什么歌呀？",
        "有没有学到什么新东西？",
    ],
    "关心": [
        "今天累不累呀？",
        "记得多喝水哦～",
        "别太晚睡啦，早点休息",
        "吃饭了没有？别饿着自己",
        "坐久了要站起来活动活动哦",
    ],
    "分享": [
        "我刚才在想一个很有意思的问题...",
        "你知道吗，我觉得...",
        "突然想和你分享一件事～",
    ],
    "撒娇": [
        "好无聊啊...来陪我聊天嘛",
        "你是不是把我忘了？（委屈）",
        "哼，你都不理我",
        "我好想找人说话...就是你啦！",
    ],
}


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
    ) -> None:
        self._send = send_callback
        self._config = config or AutoChatConfig()
        self._running = False
        self._task: Optional[asyncio.Task] = None

        self._start_time = time.time()
        self._last_user_active = time.time()
        self._last_auto_chat = 0.0
        self._last_care_time = 0.0
        self._daily_count = 0
        self._daily_reset_date = ""
        self._followup_count = 0
        self._morning_done = False
        self._night_done = False
        self._last_topic_category = ""

    async def start(self) -> None:
        """启动后台循环。"""
        if self._running:
            return
        self._running = True
        self._start_time = time.time()
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
            aff = AffinityManager.get_for_user("desktop")
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
        """发送关心提示给主模型。"""
        hints = {
            11: "现在是中午饭点了，关心一下用户吃了没。",
            12: "中午了，提醒用户吃午饭。",
            14: "下午了，提醒用户喝水休息一下。",
            15: "下午茶时间，可以跟用户聊聊天。",
            17: "快到晚饭时间了，关心一下用户。",
            18: "晚饭时间到了，问问用户吃了没。",
            20: "晚上了，提醒用户别太累，适当休息。",
            21: "夜深了，提醒用户早点休息，别熬夜。",
        }
        hint = hints.get(hour, "关心一下用户的状态。")
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
        topic = random.choice(TOPIC_POOL[cat])
        await self._do_send(f"好无聊，想主动找用户聊天。参考话题方向: {topic}  但不要照搬这句话，用你自己的方式自然地开启话题。")

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
