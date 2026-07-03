"""
white_salary/adapters/platform/bilibili_live.py

B站直播弹幕互动 — 连接B站直播间读取弹幕并回复。

功能：
  - 连接B站直播间WebSocket
  - 读取弹幕消息
  - 通过ChatAgent生成回复
  - 发送弹幕回复（需要登录cookie）

依赖: bilibili-api-python（pip install bilibili-api-python）
如果没有安装，优雅降级为不可用。
"""

import asyncio
import json
from typing import Optional, Callable, Awaitable

from loguru import logger


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
    ) -> None:
        """
        Args:
            room_id: B站直播间ID
            credential: 登录凭证（sessdata, bili_jct, buvid3）用于发送弹幕
            bot_name: 机器人名字
        """
        self._room_id = room_id
        self._credential = credential
        self._bot_name = bot_name
        self._running = False

        # 弹幕处理回调
        self.on_danmaku: Optional[Callable[[str, str], Awaitable[Optional[str]]]] = None

    async def connect(self) -> None:
        """连接直播间并开始监听弹幕。"""
        try:
            from bilibili_api import live, Credential
        except ImportError:
            logger.warning(
                "[Bilibili] bilibili-api-python 未安装。"
                "请运行: pip install bilibili-api-python"
            )
            return

        self._running = True
        logger.info(f"[Bilibili] 连接直播间: {self._room_id}")

        # 创建凭证（如果有的话）
        cred = None
        if self._credential:
            cred = Credential(
                sessdata=self._credential.get("sessdata", ""),
                bili_jct=self._credential.get("bili_jct", ""),
                buvid3=self._credential.get("buvid3", ""),
            )

        room = live.LiveDanmaku(self._room_id, credential=cred)

        @room.on("DANMU_MSG")
        async def on_danmaku(event):
            """处理弹幕消息。"""
            info = event.get("data", {}).get("info", [])
            if len(info) < 3:
                return

            text = info[1]  # 弹幕内容
            sender = info[2][1] if len(info[2]) > 1 else "unknown"  # 发送者昵称

            # 只处理@机器人或提到名字的弹幕
            if self._bot_name not in text and f"@{self._bot_name}" not in text:
                return

            logger.debug(f"[Bilibili] 弹幕 {sender}: {text}")

            if self.on_danmaku:
                reply = await self.on_danmaku(sender, text)
                if reply and cred:
                    # 发送弹幕回复（需要登录）
                    try:
                        await live.send_danmaku(
                            self._room_id,
                            reply[:20],  # B站弹幕长度限制
                            credential=cred,
                        )
                    except Exception as e:
                        logger.warning(f"[Bilibili] 发送弹幕失败: {e}")

        try:
            await room.connect()
        except Exception as e:
            logger.error(f"[Bilibili] 连接失败: {e}")

    async def disconnect(self) -> None:
        self._running = False
