"""端到端测试用的假 NapCat / OneBot v11 服务端。

用途：让 QQ 链路可以真跑起来而不需要真的 QQ 客户端。测试可以：
  - 推送群消息/私聊消息事件给白；
  - 收集白发出的 API 调用（send_group_msg / send_private_msg 等）并回真实回执；
  - 断言"白到底有没有说话"——这是验证"别说话"停止指令唯一可靠的方式，
    因为停止生效的表现就是**没有 send_group_msg 发生**。

协议要点（OneBot v11 反向 WebSocket）：
  - 白作为客户端连上来；
  - 事件由服务端主动下推（post_type=message 等）；
  - 白发起的 API 调用形如 {"action": "...", "params": {...}, "echo": "..."}，
    服务端需回 {"status":"ok","retcode":0,"data":{...},"echo":"..."}。
    ``send_*_msg`` 必须回一个 message_id，否则白会把投递记为"结果不明"。
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Optional

import websockets
from websockets.asyncio.server import ServerConnection, serve


class FakeNapCat:
    """可编程的假 NapCat 服务端。"""

    def __init__(self, port: int, self_id: int = 999000) -> None:
        self.port = port
        self.self_id = self_id
        self._server = None
        self._conn: Optional[ServerConnection] = None
        self._connected = asyncio.Event()
        # 白发起的所有 API 调用（按顺序）
        self.api_calls: list[dict[str, Any]] = []
        # message_id 必须每次运行都不同！
        #
        # StartupChecker 会把已处理的消息键（qq:group:{gid}:{message_id}）持久化到
        # data/qq/processed_msg_ids.json 并保留 7 天。而 e2e 的数据目录在项目根、
        # 跨运行共享，所以如果这里从固定值起编号，第二次以后推的消息会被判为
        # "这条已处理过"而**静默丢弃**，表现为"白收不到消息"，极难定位。
        # 用当前时间取模做起点，保证每次运行落在不同区间。
        self._next_message_id = int(time.time()) % 2_000_000_000
        self._reader_task: Optional[asyncio.Task] = None

    # ---------------- 生命周期 ----------------

    async def start(self) -> None:
        self._server = await serve(self._handler, "127.0.0.1", self.port)

    async def stop(self) -> None:
        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):
                pass
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def wait_connected(self, timeout: float = 30.0) -> bool:
        try:
            await asyncio.wait_for(self._connected.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    # ---------------- 连接处理 ----------------

    async def _handler(self, conn: ServerConnection) -> None:
        self._conn = conn
        self._connected.set()
        # 先发一个 lifecycle 事件，真实 NapCat 也是这么做的
        await self._send({
            "post_type": "meta_event",
            "meta_event_type": "lifecycle",
            "sub_type": "connect",
            "self_id": self.self_id,
            "time": int(time.time()),
        })
        try:
            async for raw in conn:
                await self._on_client_message(raw)
        except websockets.exceptions.ConnectionClosed:
            pass

    async def _on_client_message(self, raw: str | bytes) -> None:
        try:
            payload = json.loads(raw)
        except Exception:
            return
        action = payload.get("action")
        if not action:
            return
        self.api_calls.append(payload)

        data: dict[str, Any] = {}
        if action in ("send_group_msg", "send_private_msg", "send_msg"):
            self._next_message_id += 1
            data = {"message_id": self._next_message_id}
        elif action == "get_login_info":
            data = {"user_id": self.self_id, "nickname": "白"}
        elif action == "get_group_list":
            data = []  # type: ignore[assignment]
        elif action == "get_friend_list":
            data = []  # type: ignore[assignment]

        await self._send({
            "status": "ok",
            "retcode": 0,
            "data": data,
            "echo": payload.get("echo", ""),
        })

    async def _send(self, payload: dict[str, Any]) -> None:
        if self._conn is None:
            raise RuntimeError("白还没连上假 NapCat")
        await self._conn.send(json.dumps(payload, ensure_ascii=False))

    # ---------------- 事件下推 ----------------

    async def push_group_message(
        self,
        *,
        text: str,
        group_id: int = 700100,
        user_id: int = 800200,
        nickname: str = "群友甲",
        message_id: Optional[int] = None,
    ) -> int:
        if message_id is None:
            self._next_message_id += 1
            message_id = self._next_message_id
        await self._send({
            "post_type": "message",
            "message_type": "group",
            "sub_type": "normal",
            "message_id": message_id,
            "group_id": group_id,
            "user_id": user_id,
            "self_id": self.self_id,
            "time": int(time.time()),
            "raw_message": text,
            "message": [{"type": "text", "data": {"text": text}}],
            "sender": {"user_id": user_id, "nickname": nickname, "card": nickname},
        })
        return message_id

    async def push_private_message(
        self,
        *,
        text: str,
        user_id: int = 10001,
        nickname: str = "小白",
    ) -> int:
        self._next_message_id += 1
        message_id = self._next_message_id
        await self._send({
            "post_type": "message",
            "message_type": "private",
            "sub_type": "friend",
            "message_id": message_id,
            "user_id": user_id,
            "self_id": self.self_id,
            "time": int(time.time()),
            "raw_message": text,
            "message": [{"type": "text", "data": {"text": text}}],
            "sender": {"user_id": user_id, "nickname": nickname},
        })
        return message_id

    # ---------------- 断言辅助 ----------------

    def sent_messages(self) -> list[str]:
        """白实际发出去的文本消息（按顺序）。"""
        out: list[str] = []
        for call in self.api_calls:
            if call.get("action") not in ("send_group_msg", "send_private_msg", "send_msg"):
                continue
            params = call.get("params") or {}
            message = params.get("message")
            if isinstance(message, str):
                out.append(message)
            elif isinstance(message, list):
                out.append(
                    "".join(
                        seg.get("data", {}).get("text", "")
                        for seg in message
                        if isinstance(seg, dict) and seg.get("type") == "text"
                    )
                )
        return out

    def clear(self) -> None:
        self.api_calls.clear()

    async def wait_for_send(self, timeout: float = 20.0) -> bool:
        """等到白真的发出一条消息；超时返回 False（用于断言"它没说话"）。"""
        deadline = asyncio.get_running_loop().time() + timeout
        baseline = len(self.sent_messages())
        while asyncio.get_running_loop().time() < deadline:
            if len(self.sent_messages()) > baseline:
                return True
            await asyncio.sleep(0.2)
        return False
