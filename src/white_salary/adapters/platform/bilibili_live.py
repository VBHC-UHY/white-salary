"""
white_salary/adapters/platform/bilibili_live.py

B站直播弹幕互动 — 连接B站直播间读取弹幕并回复。

功能：
  - 连接B站直播间WebSocket
  - 读取弹幕消息
  - 通过ChatAgent生成回复
  - 发送弹幕回复（需要登录cookie）

依赖: bilibili extra（pip install -e ".[bilibili]"）
如果没有安装，优雅降级为不可用。

2026-07-03 功能大项（批11二波）：本适配器早已实现 connect()/on_danmaku 回调，
但全项目无人装配它（死架子）。本次施工：
  1. 修复发弹幕的 API 误用——原代码调用 live.send_danmaku(...)（模块级函数根本
     不存在），正确用法是 live.LiveRoom(room_id, credential).send_danmaku(Danmaku(text))。
  2. 抽出可测的纯函数：弹幕触发判定（should_reply_to_danmaku）、同用户冷却
     （DanmakuCooldown），供 run_server 装配 + 单元测试复用，防刷屏被B站封号。
  3. 新增 read_bili_credential() 从 config/bili.ini 读登录凭证（读不到=只读弹幕不发）。
"""

import time
from pathlib import Path
from typing import Awaitable, Callable, Optional

from loguru import logger


# =============================================================================
# 纯函数：弹幕触发判定 + 同用户冷却（抽出以便单元测试，不依赖B站/网络）
# =============================================================================

def should_reply_to_danmaku(
    text: str,
    bot_name: str,
    trigger_keywords: Optional[list[str]] = None,
) -> bool:
    """
    2026-07-03 功能大项（批11二波）：判定一条弹幕是否值得回复（纯函数，可测）。

    规则（满足其一即回复）：
      - 弹幕 @机器人 或提到机器人名字（bot_name 出现在文本里）；
      - 配置了 trigger_keywords 且弹幕含任一关键词。

    留空 trigger_keywords 时只按机器人名字判定——这是防刷屏的关键：直播弹幕量大，
    逢弹幕必回极易被B站风控封号，所以默认只回"点名找白"的弹幕。

    Args:
        text: 弹幕文本
        bot_name: 机器人名字（如"白"）
        trigger_keywords: 触发关键词列表（None/空=只按机器人名字判定）

    Returns:
        是否应该生成并（可选）发送回复
    """
    if not text or not text.strip():
        return False

    # 提到机器人名字（含 @白 这种写法，bot_name 已包含在内）
    if bot_name and bot_name in text:
        return True

    # 命中任一触发关键词
    if trigger_keywords:
        for kw in trigger_keywords:
            if kw and kw in text:
                return True

    return False


class DanmakuCooldown:
    """
    2026-07-03 功能大项（批11二波）：同一用户弹幕回复冷却（防刷屏，可测）。

    同一用户在 cooldown_seconds 秒内最多被回复一次——挡住"一个人连刷十条弹幕、
    白连回十条"的刷屏场景，降低被B站风控的风险。纯内存、无网络依赖，便于单测。
    """

    def __init__(self, cooldown_seconds: float = 30.0) -> None:
        """
        Args:
            cooldown_seconds: 同一用户两次回复的最小间隔秒数（默认30秒）
        """
        self._cooldown = cooldown_seconds
        # 用户标识 -> 上次回复的时间戳
        self._last_reply_at: dict[str, float] = {}

    def allow(self, user_key: str, now: Optional[float] = None) -> bool:
        """
        判定是否允许现在回复该用户；允许则占用其冷却名额。

        Args:
            user_key: 用户唯一标识（B站用户名或uid）
            now: 当前时间戳（None=用 time.time()，测试时可注入固定值）

        Returns:
            True=允许回复（并已记录本次时间），False=仍在冷却中
        """
        ts = time.time() if now is None else now
        last = self._last_reply_at.get(user_key)
        if last is not None and (ts - last) < self._cooldown:
            return False
        self._last_reply_at[user_key] = ts
        return True


# =============================================================================
# 登录凭证读取（config/bili.ini，与 bili_qr_login / bili_cookie_reader 同格式）
# =============================================================================

def read_bili_credential(
    ini_path: str = "config/bili.ini",
) -> Optional[dict[str, str]]:
    """
    2026-07-03 功能大项（批11二波）：从 config/bili.ini 读取B站登录凭证。

    该文件由设置面板的B站扫码登录 / 浏览器cookie读取写入（[bili] 节，小写键：
    sessdata / bili_jct / buvid3 / dedeuserid / ac_time_value）。发弹幕需要这些
    凭证；文件不存在或 sessdata 为空时返回 None（表示未登录，只读弹幕不发送）。

    Args:
        ini_path: bili.ini 路径（相对项目根或绝对路径）

    Returns:
        {"sessdata","bili_jct","buvid3","dedeuserid","ac_time_value"} 或 None（未登录）
    """
    path = Path(ini_path)
    if not path.exists():
        return None
    try:
        import configparser

        cp = configparser.RawConfigParser()
        cp.read(str(path), encoding="utf-8")
        sessdata = cp.get("bili", "sessdata", fallback="")
        if not sessdata:
            return None
        return {
            "sessdata": sessdata,
            "bili_jct": cp.get("bili", "bili_jct", fallback=""),
            "buvid3": cp.get("bili", "buvid3", fallback=""),
            "dedeuserid": cp.get("bili", "dedeuserid", fallback=""),
            "ac_time_value": cp.get("bili", "ac_time_value", fallback=""),
        }
    except Exception as e:  # noqa: BLE001 - 读凭证失败一律降级为"未登录"，不能拖垮启动
        logger.warning(f"[Bilibili] 读取登录凭证失败（降级为只读弹幕不发送）: {e}")
        return None


# =============================================================================
# 直播弹幕适配器
# =============================================================================

class BilibiliLiveAdapter:
    """
    B站直播弹幕适配器。

    连接直播间，读取弹幕，支持回复。
    """

    def __init__(
        self,
        room_id: int,
        credential: Optional[dict] = None,
        bot_name: str = "白",
        trigger_keywords: Optional[list[str]] = None,
        reply_danmaku: bool = False,
        cooldown_seconds: float = 30.0,
    ) -> None:
        """
        Args:
            room_id: B站直播间ID
            credential: 登录凭证（sessdata, bili_jct, buvid3, dedeuserid）用于发送弹幕
            bot_name: 机器人名字
            trigger_keywords: 触发关键词（空=只回复@机器人/提到名字的弹幕）
            reply_danmaku: 是否真的发弹幕回复（False=只监听不发；需有凭证才生效）
            cooldown_seconds: 同一用户回复冷却秒数（防刷屏）

        2026-07-03 功能大项（批11二波）：新增 trigger_keywords/reply_danmaku/
        cooldown_seconds 参数，把触发判定与冷却下沉到适配器，装配层只需传配置。
        """
        self._room_id = room_id
        self._credential = credential
        self._bot_name = bot_name
        self._trigger_keywords = trigger_keywords or []
        self._reply_danmaku = reply_danmaku
        self._running = False

        # 2026-07-03 功能大项（批11二波）：同用户冷却器（防刷屏被封）
        self._cooldown = DanmakuCooldown(cooldown_seconds=cooldown_seconds)

        # 弹幕处理回调：入参 (sender, text)，返回白的回复文本（或 None=不回复）
        self.on_danmaku: Optional[
            Callable[[str, str], Awaitable[Optional[str]]]
        ] = None

    async def connect(self) -> None:
        """连接直播间并开始监听弹幕。"""
        try:
            from bilibili_api import Credential
            from bilibili_api import live
        except ImportError:
            logger.warning(
                "[Bilibili] B站可选依赖未安装。"
                "请在项目根目录运行: pip install -e \".[bilibili]\""
            )
            return

        self._running = True
        logger.info(f"[Bilibili] 连接直播间: {self._room_id}")

        # 创建凭证（如果有的话）——发弹幕需要
        cred = None
        if self._credential:
            cred = Credential(
                sessdata=self._credential.get("sessdata", ""),
                bili_jct=self._credential.get("bili_jct", ""),
                buvid3=self._credential.get("buvid3", ""),
                dedeuserid=self._credential.get("dedeuserid", ""),
                ac_time_value=self._credential.get("ac_time_value", ""),
            )

        # 弹幕监听器（读弹幕用）
        room = live.LiveDanmaku(self._room_id, credential=cred)
        # 直播间操作句柄（发弹幕用，与监听器分开）
        live_room = live.LiveRoom(self._room_id, credential=cred)

        # 2026-07-03 功能大项（批11二波）：能否发弹幕 = 开关打开 且 有凭证
        can_send = self._reply_danmaku and cred is not None
        if self._reply_danmaku and cred is None:
            logger.warning(
                "[Bilibili] reply_danmaku=true 但未登录（config/bili.ini 无凭证），"
                "降级为只读弹幕不发送。请在控制面板登录B站后重启。"
            )

        @room.on("DANMU_MSG")
        async def on_danmaku_event(event: dict) -> None:
            """处理弹幕消息。"""
            info = event.get("data", {}).get("info", [])
            if len(info) < 3:
                return

            text = info[1]  # 弹幕内容
            # 发送者昵称（info[2] = [uid, 昵称, ...]）
            sender = info[2][1] if len(info[2]) > 1 else "unknown"

            # 2026-07-03 功能大项（批11二波）：触发判定（纯函数，防逢弹幕必回）
            if not should_reply_to_danmaku(
                text, self._bot_name, self._trigger_keywords
            ):
                return

            # 同用户冷却（防一个人连刷刷屏被封）
            if not self._cooldown.allow(sender):
                logger.debug(f"[Bilibili] {sender} 冷却中，跳过弹幕: {text[:20]}")
                return

            logger.debug(f"[Bilibili] 弹幕 {sender}: {text}")

            if not self.on_danmaku:
                return

            # 经白的人格生成回复（装配层注入的 on_danmaku 走 ChatAgent）
            reply = await self.on_danmaku(sender, text)
            if not reply:
                return

            if not can_send:
                # 未开启发送 或 未登录：只记日志，不真的发弹幕
                logger.info(f"[Bilibili] （未发送，仅监听）拟回复{sender}: {reply[:30]}")
                return

            # 发送弹幕回复（B站单条弹幕长度有限，截断到20字）
            try:
                await live_room.send_danmaku(live.Danmaku(reply[:20]))
                logger.info(f"[Bilibili] 已回复{sender}: {reply[:20]}")
            except Exception as e:  # noqa: BLE001 - 发送失败不能中断监听循环
                logger.warning(f"[Bilibili] 发送弹幕失败: {e}")

        try:
            await room.connect()
        except Exception as e:  # noqa: BLE001 - 连接失败仅告警，不抛出拖垮主程序
            logger.error(f"[Bilibili] 连接失败: {e}")

    async def disconnect(self) -> None:
        """停止监听（标记运行状态；LiveDanmaku 的连接由其自身生命周期管理）。"""
        self._running = False
