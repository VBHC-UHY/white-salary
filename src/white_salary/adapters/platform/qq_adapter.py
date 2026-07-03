"""
white_salary/adapters/platform/qq_adapter.py

QQ平台适配器 — 通过NapCat(OneBot v11)接入QQ。

功能：
  - 连接NapCat的WebSocket端口
  - 接收QQ消息（私聊/群聊）
  - 群聊中@白时回复
  - 私聊直接回复
  - 发送文字/图片消息
  - 自动重连

协议: OneBot v11 WebSocket (正向连接)
框架: NapCatQQ

使用方式:
  1. 先启动NapCat并登录QQ
  2. 在NapCat WebUI中配置正向WebSocket，如 ws://127.0.0.1:3001
  3. 在White Salary conf.yaml中配置qq.ws_url = "ws://127.0.0.1:3001"
"""

import asyncio
import json
import time
from typing import Optional, Callable, Awaitable

import aiohttp
from loguru import logger


class QQMessage:
    """QQ消息数据。"""
    def __init__(self, data: dict) -> None:
        self.raw = data
        self.post_type = data.get("post_type", "")
        self.message_type = data.get("message_type", "")  # private / group
        self.sub_type = data.get("sub_type", "")
        self.message_id = data.get("message_id", 0)
        self.user_id = str(data.get("user_id", ""))
        self.group_id = str(data.get("group_id", ""))
        self.message = data.get("message", "")
        self.raw_message = data.get("raw_message", "") or self._segments_to_raw(self.message)
        self.sender = data.get("sender", {})
        self.sender_name = self.sender.get("nickname", "") or self.sender.get("card", "")
        self.self_id = str(data.get("self_id", ""))
        self.time = data.get("time", 0)

    @staticmethod
    def _segments_to_raw(message) -> str:
        """把 OneBot 结构化 message 段轻量转成现有 CQ/text 处理链可识别的文本。"""
        if isinstance(message, str):
            return message
        if not isinstance(message, list):
            return ""

        parts: list[str] = []
        for seg in message:
            if not isinstance(seg, dict):
                continue
            seg_type = seg.get("type", "")
            data = seg.get("data", {}) or {}
            if seg_type == "text":
                parts.append(str(data.get("text", "")))
            elif seg_type == "at":
                parts.append(f"[CQ:at,qq={data.get('qq', '')}]")
            elif seg_type == "image":
                src = data.get("url") or data.get("file") or ""
                key = "url" if data.get("url") else "file"
                parts.append(f"[CQ:image,{key}={src}]")
            elif seg_type == "record":
                parts.append(f"[CQ:record,file={data.get('file', '')}]")
            elif seg_type == "reply":
                reply_id = data.get("id", "")
                qq = data.get("qq", "")
                suffix = f",qq={qq}" if qq else ""
                parts.append(f"[CQ:reply,id={reply_id}{suffix}]")
            elif seg_type == "face":
                parts.append(f"[CQ:face,id={data.get('id', '')}]")
        return "".join(parts)

    @property
    def is_private(self) -> bool:
        return self.message_type == "private"

    @property
    def is_group(self) -> bool:
        return self.message_type == "group"

    # QQ内置表情ID → 中文名映射
    _FACE_MAP = {
        0: "惊讶", 1: "撇嘴", 2: "色", 3: "发呆", 4: "得意", 5: "流泪",
        6: "害羞", 7: "闭嘴", 8: "睡", 9: "大哭", 10: "尴尬", 11: "发怒",
        12: "调皮", 13: "呲牙", 14: "微笑", 15: "难过", 16: "酷", 18: "抓狂",
        19: "吐", 20: "偷笑", 21: "可爱", 22: "白眼", 23: "傲慢", 24: "饥饿",
        25: "困", 26: "惊恐", 27: "流汗", 28: "憨笑", 29: "悠闲", 30: "奋斗",
        31: "咒骂", 32: "疑问", 33: "嘘", 34: "晕", 35: "折磨", 36: "衰",
        37: "骷髅", 38: "敲打", 39: "再见", 41: "发抖", 42: "爱情", 43: "跳跳",
        46: "猪头", 49: "拥抱", 53: "蛋糕", 54: "闪电", 55: "炸弹", 56: "刀",
        57: "足球", 59: "便便", 60: "咖啡", 61: "饭", 63: "玫瑰", 64: "凋谢",
        66: "爱心", 67: "心碎", 69: "礼物", 74: "太阳", 75: "月亮", 76: "赞",
        77: "踩", 78: "握手", 79: "胜利", 85: "飞吻", 86: "怄火",
        96: "冷汗", 97: "擦汗", 98: "抠鼻", 99: "鼓掌", 100: "糗大了",
        101: "坏笑", 102: "左哼哼", 103: "右哼哼", 104: "哈欠", 105: "鄙视",
        106: "委屈", 107: "快哭了", 108: "阴险", 109: "亲亲", 110: "吓",
        111: "可怜", 112: "菜刀", 114: "篮球", 116: "示爱", 118: "抱拳",
        120: "拳头", 122: "爱你", 123: "NO", 124: "OK", 147: "棒棒糖",
        171: "茶", 173: "泪奔", 174: "无奈", 175: "卖萌", 176: "小纠结",
        177: "喷血", 178: "斜眼笑", 179: "doge", 180: "惊喜", 181: "骚扰",
        182: "笑哭", 183: "我最美", 201: "点赞", 203: "托腮", 212: "托脸",
        214: "666", 219: "社会社会", 222: "旺柴", 227: "汪汪", 232: "哇",
        240: "敬礼", 243: "好的", 246: "加油", 262: "脸红", 264: "天啊",
        265: "Emm", 266: "社会社会", 267: "旺柴", 277: "汪汪", 281: "无眼笑",
        282: "敬礼", 284: "面无表情", 285: "摸鱼", 287: "魔鬼笑",
        289: "哦", 290: "让我看看", 293: "哦哟", 294: "裂开", 297: "苦涩",
    }

    @property
    def text(self) -> str:
        """提取纯文字内容（CQ码翻译成有意义的文字）。"""
        import re
        text = self.raw_message

        # @去掉
        text = re.sub(r"\[CQ:at,qq=\d+\]", "", text)
        # 引用回复去掉（is_at_me单独处理）
        text = re.sub(r"\[CQ:reply,[^\]]*\]", "", text)

        # QQ表情翻译：[CQ:face,id=14] → (微笑)
        def _face_replace(m):
            fid = int(m.group(1))
            name = self._FACE_MAP.get(fid, f"表情{fid}")
            return f"({name})"
        text = re.sub(r"\[CQ:face,id=(\d+)\]", _face_replace, text)

        # 图片（vision单独处理，这里只标记）
        text = re.sub(r"\[CQ:image,[^\]]*\]", "[图片]", text)
        # 戳一戳
        text = re.sub(r"\[CQ:poke,[^\]]*\]", "[戳一戳]", text)

        # 视频
        text = re.sub(r"\[CQ:video,[^\]]*\]", "[对方发了一个视频]", text)
        # 语音（ASR单独处理，这里标记）
        text = re.sub(r"\[CQ:record,[^\]]*\]", "[语音消息]", text)
        # 文件
        def _file_replace(m):
            fname = re.search(r"file=([^\],]+)", m.group())
            return f"[对方发了文件: {fname.group(1)[:30]}]" if fname else "[对方发了一个文件]"
        text = re.sub(r"\[CQ:file,[^\]]*\]", _file_replace, text)
        # 红包
        def _redbag_replace(m):
            title = re.search(r"title=([^\],]+)", m.group())
            return f"[对方发了红包: {title.group(1)[:20]}]" if title else "[对方发了一个红包]"
        text = re.sub(r"\[CQ:redbag,[^\]]*\]", _redbag_replace, text)
        # JSON卡片（链接/小程序）
        def _json_replace(m):
            try:
                import json as _json
                data_match = re.search(r"data=(\{.*?\})", m.group())
                if data_match:
                    d = _json.loads(data_match.group(1).replace("&#44;", ","))
                    title = d.get("prompt") or d.get("meta", {}).get("detail_1", {}).get("title", "")
                    if title:
                        return f"[分享: {title[:30]}]"
            except Exception:
                pass
            return "[对方分享了一个链接/小程序]"
        text = re.sub(r"\[CQ:json,[^\]]*\]", _json_replace, text)
        # XML卡片
        text = re.sub(r"\[CQ:xml,[^\]]*\]", "[对方分享了一条消息]", text)
        # 位置
        text = re.sub(r"\[CQ:location,[^\]]*\]", "[对方发了一个位置]", text)
        # 名片
        text = re.sub(r"\[CQ:contact,[^\]]*\]", "[对方发了一张名片]", text)

        # 去掉剩余未知CQ码
        text = re.sub(r"\[CQ:[^\]]+\]", "", text)
        return text.strip()

    @property
    def image_urls(self) -> list[str]:
        """提取消息中的所有图片URL。"""
        import re
        urls = re.findall(r"\[CQ:image,[^\]]*url=([^\],]+)", self.raw_message)
        if isinstance(self.message, list):
            for seg in self.message:
                if not isinstance(seg, dict) or seg.get("type") != "image":
                    continue
                data = seg.get("data", {}) or {}
                url = data.get("url") or data.get("file")
                if url:
                    urls.append(str(url))
        deduped: list[str] = []
        seen: set[str] = set()
        for url in urls:
            if url in seen:
                continue
            seen.add(url)
            deduped.append(url)
        return deduped

    @property
    def is_at_me(self) -> bool:
        """检查是否@了机器人自己（包括直接@和回复机器人的消息）。"""
        # 直接@
        if f"[CQ:at,qq={self.self_id}]" in self.raw_message:
            return True
        # 回复机器人的消息（NapCat会在reply元素中带sender_id）
        if "[CQ:reply," in self.raw_message:
            # 检查原始数据中reply的sender是否是自己
            reply_data = self.raw.get("message", [])
            if isinstance(reply_data, list):
                for seg in reply_data:
                    if isinstance(seg, dict) and seg.get("type") == "reply":
                        # OneBot v11的reply段可能带有qq字段指向原消息发送者
                        reply_qq = str(seg.get("data", {}).get("qq", ""))
                        if reply_qq == self.self_id:
                            return True
        return False

    @property
    def has_image(self) -> bool:
        """检查是否包含图片。"""
        if "[CQ:image," in self.raw_message:
            return True
        if isinstance(self.message, list):
            return any(
                isinstance(seg, dict) and seg.get("type") == "image"
                for seg in self.message
            )
        return False


class QQAdapter:
    """
    QQ平台适配器 — 通过OneBot v11 WebSocket连接NapCat。

    使用方法:
        adapter = QQAdapter(ws_url="ws://127.0.0.1:3001")
        adapter.on_message = my_handler  # 设置消息处理函数
        await adapter.connect()          # 开始监听
    """

    def __init__(
        self,
        ws_url: str = "ws://127.0.0.1:3001",
        bot_name: str = "白",
        token: str = "",
        reconnect_interval: int = 5,
        family_qq: Optional[list[str]] = None,
    ) -> None:
        """
        Args:
            ws_url: NapCat的正向WebSocket地址
            bot_name: 机器人名字（用于群聊中检测是否被叫）
            reconnect_interval: 断线重连起步间隔（秒，指数退避的base）
            family_qq: 家人QQ号白名单（入群邀请判定用）
        """
        self._ws_url = ws_url
        self._bot_name = bot_name
        self._token = token
        self._reconnect_interval = reconnect_interval
        # 2026-07-02 审计修复（批4）：家人QQ白名单——入群邀请只认家人（防被拉进恶意群）
        self._family_qq: set[str] = {str(q) for q in (family_qq or [])}
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._running = False
        self._self_id = ""

        # 消息处理回调：async def handler(msg: QQMessage) -> str | None
        self.on_message: Optional[Callable[[QQMessage], Awaitable[Optional[str]]]] = None

        # 群消息记录回调：所有群消息都调（不管回不回复），用于记录上下文
        # def recorder(group_id: str, sender_name: str, text: str) -> None
        self.on_group_record: Optional[Callable] = None

        # API响应等待（echo → Future，用于拿message_id）
        self._pending_api: dict[str, asyncio.Future] = {}

        # 最近发送的消息记录（用于撤回检测+引用回复检测，最多存50条）
        # 字段：msg_id, content, time, group_id, is_event, reply_to_user
        self._sent_messages: list[dict] = []
        self._max_sent = 50

        # 收到的消息缓存（用于撤回时查原文，最多500条，1小时过期）
        # {msg_id: {"content": 文本, "sender_name": 昵称, "sender_id": QQ号, "group_id": 群号, "time": 时间}}
        self._msg_cache: dict[str, dict] = {}
        self._msg_cache_max = 500

    @staticmethod
    def _backoff_interval(fail_count: int, base: float = 5.0, cap: float = 60.0) -> float:
        """
        2026-07-02 审计修复（批4）：重连指数退避间隔计算（原为固定5秒，
        NapCat长时间不在线时5-7秒一次刷屏，审计实证单日3869条失败日志）。

        Args:
            fail_count: 连续失败次数（0=上次连接成功，退避归零）
            base: 起步间隔（秒）
            cap: 间隔上限（秒）

        Returns:
            下次重连前等待的秒数：base起步、每多失败一次×2、封顶cap
        """
        if fail_count <= 1:
            return min(base, cap)
        exponent = min(fail_count - 1, 10)  # 指数封顶，防大数运算（2^10已远超cap）
        return min(base * (2 ** exponent), cap)

    @staticmethod
    def _should_log_reconnect(fail_count: int) -> bool:
        """
        2026-07-02 审计修复（批4）：重连日志节流判定。

        连续失败≤10次时每次都记日志；超过10次后改为每10次汇总记一条，
        防止NapCat长时间不在线时WARNING刷屏。

        Args:
            fail_count: 连续失败次数

        Returns:
            这一次失败是否应该记可见级别日志
        """
        return fail_count <= 10 or fail_count % 10 == 0

    async def connect(self) -> None:
        """连接到NapCat WebSocket并开始监听消息。"""
        self._running = True
        logger.info(f"[QQ] 正在连接 NapCat: {self._ws_url}")

        # 2026-07-02 审计修复（批4）：固定5秒重连改为指数退避（5s起步、每次×2、
        # 封顶60s、连接成功归零）；连续失败超过10次后不再每次刷WARNING，
        # 改为每10次汇总一条（含累计次数与下次重试间隔）
        consecutive_failures: int = 0

        while self._running:
            try:
                # OneBot v11鉴权：通过Authorization头传递token
                headers = {}
                if self._token:
                    headers["Authorization"] = f"Bearer {self._token}"
                self._session = aiohttp.ClientSession()
                self._ws = await self._session.ws_connect(
                    self._ws_url, headers=headers
                )
                consecutive_failures = 0  # 连接成功→退避归零
                logger.info("[QQ] WebSocket 已连接")

                # 触发连接成功回调（离线消息检查等）
                if hasattr(self, 'on_connected') and self.on_connected:
                    asyncio.create_task(self.on_connected())

                async for msg in self._ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        # 用create_task并发处理，不阻塞WebSocket读取
                        # 这样API响应能在处理用户消息的同时被接收
                        asyncio.create_task(self._safe_handle_raw(msg.data))
                    elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        logger.warning(f"[QQ] WebSocket 断开: {msg.type}")
                        break

            except aiohttp.ClientError as e:
                consecutive_failures += 1
                next_wait = self._backoff_interval(
                    consecutive_failures, base=float(self._reconnect_interval))
                if self._should_log_reconnect(consecutive_failures):
                    logger.warning(
                        f"[QQ] 连接失败(连续第{consecutive_failures}次): {e}"
                        f"（约{next_wait:.0f}秒后重试）"
                    )
                else:
                    logger.debug(f"[QQ] 连接失败(连续第{consecutive_failures}次): {e}")
            except Exception as e:
                consecutive_failures += 1
                logger.error(f"[QQ] 未知错误: {e}")
            finally:
                if self._session:
                    await self._session.close()
                    self._session = None
                self._ws = None

            if self._running:
                wait = self._backoff_interval(
                    consecutive_failures, base=float(self._reconnect_interval))
                if self._should_log_reconnect(consecutive_failures):
                    logger.info(f"[QQ] {wait:.0f}秒后重连...")
                await asyncio.sleep(wait)

    async def disconnect(self) -> None:
        """断开连接。"""
        self._running = False
        if self._ws:
            await self._ws.close()

    async def _safe_handle_raw(self, data: str) -> None:
        """安全包装_handle_raw，create_task用（异常不会崩掉主循环）。"""
        try:
            await self._handle_raw(data)
        except Exception as e:
            logger.error(f"[QQ] 消息处理异常: {e}")

    async def _handle_raw(self, data: str) -> None:
        """处理原始WebSocket消息。"""
        try:
            event = json.loads(data)
        except json.JSONDecodeError:
            return

        # API调用的响应（有echo字段、没有post_type）
        echo = event.get("echo", "")
        if echo and echo in self._pending_api:
            future = self._pending_api.pop(echo)
            if not future.done():
                future.set_result(event.get("data"))
            return

        post_type = event.get("post_type", "")

        # 元事件（心跳等）
        if post_type == "meta_event":
            if event.get("meta_event_type") == "lifecycle":
                self._self_id = str(event.get("self_id", ""))
                logger.info(f"[QQ] Bot QQ号: {self._self_id}")
            return

        # 通知事件
        if post_type == "notice":
            await self._handle_notice(event)
            return

        # 请求事件（好友申请/入群申请）
        if post_type == "request":
            await self._handle_request(event)
            return

        # 消息事件
        if post_type == "message":
            msg = QQMessage(event)
            msg.self_id = self._self_id or msg.self_id
            await self._handle_message(msg)

    async def _handle_message(self, msg: QQMessage) -> None:
        """处理QQ消息。"""
        # 跳过自己发的消息（self_id未初始化时也跳过，防死循环）
        if not self._self_id:
            # meta_event还没来，不知道自己是谁，先不处理
            logger.debug("[QQ] self_id未初始化，跳过消息")
            return
        if msg.user_id == self._self_id:
            return

        # 缓存消息内容（撤回时用来查原文）
        if msg.message_id and msg.text:
            self._cache_message(msg)

        # 群消息上下文记录（所有群消息都记，不管白回不回复）
        if msg.is_group and self.on_group_record and msg.text:
            try:
                self.on_group_record(msg.group_id, msg.sender_name, msg.text)
            except Exception:
                pass

        # 引用白的消息检测（引用白=叫白，不需要唤醒词）
        _is_reply_to_me = False
        if "[CQ:reply," in msg.raw_message and self._sent_messages:
            import re as _re
            reply_id_match = _re.search(r"\[CQ:reply,id=(-?\d+)", msg.raw_message)
            if reply_id_match:
                reply_mid = str(reply_id_match.group(1))
                if any(str(m["msg_id"]) == reply_mid for m in self._sent_messages):
                    _is_reply_to_me = True

        # 群聊：智能回复决策
        if msg.is_group:
            if not hasattr(self, '_smart_decider'):
                from white_salary.core.smart_reply import SmartReplyDecider
                self._smart_decider = SmartReplyDecider(
                    bot_self_id=self._self_id,
                    bot_name=self._bot_name,
                    owner_ids=list(self._family_qq),
                )
            from white_salary.core.smart_reply import ReplyDecision

            # 通知SmartReply有人说话了（用于"没人理"计数）
            self._smart_decider.record_user_response(msg.group_id, msg.user_id)

            # 引用白的消息→强制回复，跳过SmartReplyDecider
            _is_owner_media = (
                msg.user_id in self._family_qq
                and (msg.has_image or "[CQ:record," in msg.raw_message)
            )
            if not _is_reply_to_me and not _is_owner_media:
                result = self._smart_decider.decide(msg)
                if result.decision != ReplyDecision.REPLY:
                    return
            logger.debug(f"[QQ] 群聊({msg.group_id}) {msg.sender_name}: {msg.text[:50]}")
        else:
            logger.debug(f"[QQ] 私聊({msg.user_id}) {msg.sender_name}: {msg.text[:50]}")

        # 调用消息处理回调
        if self.on_message and msg.text:
            try:
                reply = await self.on_message(msg)
                if reply:
                    await self.send_reply(msg, reply)
                    # 记录回复（传user_id，支持对话延续）
                    if msg.is_group and hasattr(self, '_smart_decider'):
                        self._smart_decider.record_reply(msg.group_id, msg.user_id)
            except Exception as e:
                logger.error(f"[QQ] 消息处理失败: {e}")

    # ================================================================
    # 通知事件处理
    # ================================================================

    async def _handle_notice(self, event: dict) -> None:
        """处理通知事件（戳一戳/撤回/入群/退群等）。"""
        notice_type = event.get("notice_type", "")
        sub_type = event.get("sub_type", "")
        user_id = str(event.get("user_id", ""))
        group_id = str(event.get("group_id", ""))

        # --- 消息撤回 ---
        if notice_type in ("group_recall", "friend_recall"):
            message_id = event.get("message_id", 0)
            logger.info(f"[QQ] 消息撤回: user={user_id} msg_id={message_id}")
            # 撤回回应条件：白最近5分钟回复过这个人才回应（不是随便谁撤回都回应）
            if self.on_message and user_id != self._self_id:
                now = time.time()
                # 检查白是否最近回复过撤回消息的这个人
                is_active = False
                for m in reversed(self._sent_messages):
                    if now - m.get("time", 0) > 300:
                        break  # 超过5分钟的不看了
                    if m.get("is_event"):
                        continue  # 事件回应不算
                    if group_id and m.get("group_id") == group_id:
                        # 群聊：白回复过这个人才算（reply_to_user匹配）
                        if m.get("reply_to_user") == user_id:
                            is_active = True
                            break
                    if not group_id:
                        # 私聊：白跟这个人聊过就算
                        if m.get("reply_to_user") == user_id:
                            is_active = True
                            break

                # 频率限制：同一个群5分钟内最多回应1次撤回
                recall_key = f"recall_{group_id or user_id}"
                last_recall = getattr(self, '_last_recall_time', {})
                if not hasattr(self, '_last_recall_time'):
                    self._last_recall_time = {}
                if now - self._last_recall_time.get(recall_key, 0) < 300:
                    is_active = False  # 5分钟内已回应过

                if is_active:
                    try:
                        # 查缓存拿撤回消息的原文
                        cached = self.get_cached_message(str(message_id))
                        if not cached:
                            # 查不到原文（白没收到过这条消息或已过期），不回应
                            return

                        original_content = cached["content"]
                        recall_name = cached["sender_name"] or await self._get_user_name(user_id, group_id)

                        # 提示词带原文：让AI知道撤回的是什么内容，自然回应
                        if group_id:
                            fake_text = (
                                f"[系统提示] 在群{group_id}，{recall_name}(QQ:{user_id})"
                                f"刚才说了「{original_content[:200]}」，但是撤回了。"
                                f"你已经看到了这条消息的内容，根据你的性格自然地回应就好，简短一句话。"
                            )
                        else:
                            fake_text = (
                                f"[系统提示] {recall_name}(QQ:{user_id})"
                                f"刚才说了「{original_content[:200]}」，但是撤回了。"
                                f"你已经看到了这条消息的内容，根据你的性格自然地回应就好，简短一句话。"
                            )
                        reply = await self._generate_event_reply(
                            fake_text, user_id, group_id, user_name=recall_name)
                        if reply:
                            if group_id:
                                await self.send_group_message(
                                    group_id, reply,
                                    reply_to_user=user_id, is_event=True)
                                self._record_to_context(group_id, reply)
                            else:
                                await self.send_private_message(
                                    user_id, reply,
                                    reply_to_user=user_id, is_event=True)
                            self._last_recall_time[recall_key] = now
                    except Exception as e:
                        logger.debug(f"[QQ] 撤回回应失败: {e}")
            return

        # --- 戳一戳 ---
        if notice_type == "notify" and sub_type == "poke":
            target_id = str(event.get("target_id", ""))
            # 只处理戳白的（target_id是白自己）
            if target_id == self._self_id and user_id != self._self_id:
                logger.info(f"[QQ] 被戳一戳: user={user_id} group={group_id}")
                # 回戳
                try:
                    if group_id:
                        await self._call_api("group_poke", {
                            "group_id": int(group_id), "user_id": int(user_id)
                        })
                    else:
                        await self._call_api("friend_poke", {"user_id": int(user_id)})
                except Exception:
                    pass
                # LLM回应（标清楚谁在哪个群戳的，防串）
                if self.on_message:
                    try:
                        # 查昵称
                        poke_name = await self._get_user_name(user_id, group_id)
                        if group_id:
                            fake_text = (
                                f"[系统提示] 在群{group_id}，{poke_name}(QQ:{user_id})"
                                f"戳了你一下（戳一戳），用你自己的性格回应一下，简短一句话。"
                            )
                        else:
                            fake_text = (
                                f"[系统提示] {poke_name}(QQ:{user_id})"
                                f"戳了你一下（戳一戳），用你自己的性格回应一下，简短一句话。"
                            )
                        reply = await self._generate_event_reply(
                            fake_text, user_id, group_id, user_name=poke_name)
                        if reply:
                            if group_id:
                                await self.send_group_message(
                                    group_id, f"[CQ:at,qq={user_id}] {reply}",
                                    reply_to_user=user_id, is_event=True)
                                self._record_to_context(group_id, reply)
                            else:
                                await self.send_private_message(
                                    user_id, reply,
                                    reply_to_user=user_id, is_event=True)
                    except Exception as e:
                        logger.debug(f"[QQ] 戳一戳回应失败: {e}")
            return

        # --- 新人入群 ---
        if notice_type == "group_increase":
            logger.info(f"[QQ] 新人入群: user={user_id} group={group_id}")
            if user_id != self._self_id and self.on_message:
                try:
                    new_name = await self._get_user_name(user_id, group_id)
                    fake_text = (
                        f"[系统提示] 在群{group_id}，{new_name}(QQ:{user_id})"
                        f"加入了群聊，跟新人打个招呼欢迎一下，简短自然就好。"
                    )
                    reply = await self._generate_event_reply(
                        fake_text, user_id, group_id, user_name=new_name)
                    if reply:
                        await self.send_group_message(
                            group_id, f"[CQ:at,qq={user_id}] {reply}",
                            reply_to_user=user_id, is_event=True)
                        self._record_to_context(group_id, reply)
                except Exception as e:
                    logger.debug(f"[QQ] 入群欢迎失败: {e}")
            return

        # --- 成员退群/被踢 ---
        if notice_type == "group_decrease":
            operator_id = str(event.get("operator_id", ""))
            logger.info(f"[QQ] 成员退群: user={user_id} group={group_id} sub={sub_type} op={operator_id}")
            # 白自己被踢了
            if user_id == self._self_id:
                logger.warning(f"[QQ] 白被踢出群: group={group_id} 操作者={operator_id}")
            return

        # --- 群禁言 ---
        if notice_type == "group_ban":
            duration = event.get("duration", 0)
            operator_id = str(event.get("operator_id", ""))
            if user_id == self._self_id:
                if duration > 0:
                    logger.warning(f"[QQ] 白被禁言: group={group_id} 时长={duration}秒 操作者={operator_id}")
                else:
                    logger.info(f"[QQ] 白被解除禁言: group={group_id}")
            else:
                if duration > 0:
                    logger.info(f"[QQ] 群成员被禁言: user={user_id} group={group_id} 时长={duration}秒")
                else:
                    logger.info(f"[QQ] 群成员被解禁: user={user_id} group={group_id}")
            return

        # --- 群管理变动 ---
        if notice_type == "group_admin":
            logger.info(f"[QQ] 管理变动: user={user_id} group={group_id} sub={sub_type}")
            return

    # ================================================================
    # 请求事件处理
    # ================================================================

    async def _handle_request(self, event: dict) -> None:
        """处理请求事件（好友申请/入群申请）。"""
        request_type = event.get("request_type", "")
        flag = event.get("flag", "")
        user_id = str(event.get("user_id", ""))
        comment = event.get("comment", "")

        # --- 好友申请 ---
        if request_type == "friend":
            logger.info(f"[QQ] 好友申请: user={user_id} comment={comment[:30]}")
            # 根据好感度决定是否接受
            try:
                from white_salary.core.affinity.manager import AffinityManager
                mgr = AffinityManager.get_for_user(user_id)
                points = mgr._affinity.points
                # 好感度>=0接受，<-20拒绝
                if points >= -20:
                    await self._call_api("set_friend_add_request", {
                        "flag": flag, "approve": True
                    })
                    logger.info(f"[QQ] 自动接受好友: {user_id} (好感度={points})")
                else:
                    await self._call_api("set_friend_add_request", {
                        "flag": flag, "approve": False, "reason": ""
                    })
                    logger.info(f"[QQ] 自动拒绝好友: {user_id} (好感度={points})")
            except Exception as e:
                # 好感度系统不可用时默认接受
                await self._call_api("set_friend_add_request", {
                    "flag": flag, "approve": True
                })
                logger.debug(f"[QQ] 好友申请默认接受: {e}")
            return

        # --- 入群申请/邀请 ---
        if request_type == "group":
            sub_type = event.get("sub_type", "")  # add=主动申请, invite=被邀请
            group_id = str(event.get("group_id", ""))
            logger.info(f"[QQ] 入群{sub_type}: user={user_id} group={group_id}")
            # 2026-07-02 审计修复（批4）：原代码两个分支都无条件 approve=True，
            # 与注释"邀请直接同意，申请看好感度"不符（陌生人可随意把白拉进恶意群）。
            # 现按本意实现：invite=仅family_qq白名单成员的邀请才同意；
            # add=申请人好感度>=0才同意（白是群管理时收到）。
            affinity_points: Optional[float] = None
            if sub_type != "invite":
                # 申请入群：查申请人好感度（好感度系统不可用时置None→默认同意）
                try:
                    from white_salary.core.affinity.manager import AffinityManager
                    mgr = AffinityManager.get_for_user(user_id)
                    affinity_points = float(mgr._affinity.points)
                except Exception as e:
                    logger.warning(f"[QQ] 入群申请查好感度失败（默认同意）: {e}")

            approve, reason = self._decide_group_request(
                sub_type, user_id, affinity_points)
            await self._call_api("set_group_add_request", {
                "flag": flag,
                "sub_type": "invite" if sub_type == "invite" else "add",
                "approve": approve,
            })
            if approve:
                logger.info(f"[QQ] 入群{sub_type}已同意: user={user_id} group={group_id} ({reason})")
            else:
                logger.warning(f"[QQ] 入群{sub_type}已拒绝: user={user_id} group={group_id} ({reason})")
            return

    def _decide_group_request(
        self,
        sub_type: str,
        user_id: str,
        affinity_points: Optional[float],
    ) -> tuple[bool, str]:
        """
        2026-07-02 审计修复（批4）：入群请求判定逻辑（纯函数，方便单测）。

        规则：
          - invite（有人邀请白入群）：仅 family_qq 白名单成员的邀请才同意
          - add（他人申请入群、白是群管理）：申请人好感度 >= 0 才同意；
            好感度不可用（None）时保持旧行为默认同意

        Args:
            sub_type: "invite"=被邀请入群 / 其他（"add"）=他人申请入群
            user_id: 邀请人/申请人的QQ号
            affinity_points: 申请人好感度积分（None=好感度系统不可用）

        Returns:
            (是否同意, 原因说明)
        """
        uid = str(user_id)
        if sub_type == "invite":
            if uid in self._family_qq:
                return True, "家人邀请"
            return False, "非家人邀请，白名单外一律拒绝"
        # add：按好感度判定
        if affinity_points is None:
            return True, "好感度系统不可用，默认同意"
        if affinity_points >= 0:
            return True, f"好感度{affinity_points:.1f}>=0"
        return False, f"好感度{affinity_points:.1f}<0"

    def _cache_message(self, msg: "QQMessage") -> None:
        """缓存收到的消息（撤回时查原文用，最多500条，1小时过期）。"""
        msg_id = str(msg.message_id)
        self._msg_cache[msg_id] = {
            "content": msg.text[:500],
            "sender_name": msg.sender_name,
            "sender_id": msg.user_id,
            "group_id": msg.group_id if msg.is_group else "",
            "time": time.time(),
        }
        # 超过上限时清理最早的
        if len(self._msg_cache) > self._msg_cache_max:
            # 按时间排序，删最早的一半
            sorted_ids = sorted(self._msg_cache, key=lambda k: self._msg_cache[k]["time"])
            for old_id in sorted_ids[:len(sorted_ids) // 2]:
                del self._msg_cache[old_id]
        # 清理超过1小时的
        now = time.time()
        expired = [k for k, v in self._msg_cache.items() if now - v["time"] > 3600]
        for k in expired:
            del self._msg_cache[k]

    def get_cached_message(self, msg_id: str) -> dict | None:
        """查缓存的消息内容（撤回时用）。"""
        return self._msg_cache.get(str(msg_id))

    async def _get_user_name(self, user_id: str, group_id: str = "") -> str:
        """查询用户昵称（群里查群名片，私聊查QQ昵称，查不到就返回QQ号）。"""
        try:
            if group_id:
                # 群里：查群名片/昵称
                info = await self._call_api("get_group_member_info", {
                    "group_id": int(group_id), "user_id": int(user_id),
                }, wait_response=True)
                if info:
                    return info.get("card") or info.get("nickname") or user_id
            else:
                # 私聊：查QQ昵称
                info = await self._call_api("get_stranger_info", {
                    "user_id": int(user_id),
                }, wait_response=True)
                if info:
                    return info.get("nickname") or user_id
        except Exception:
            pass
        return user_id

    def _record_to_context(self, group_id: str, text: str) -> None:
        """把白的事件回应记到群聊上下文（这样白记得自己说过什么）。"""
        if self.on_group_record and group_id:
            try:
                import re
                clean = re.sub(r'\[CQ:[^\]]+\]', '', text).strip()
                self.on_group_record(group_id, self._bot_name or "白", clean[:100])
            except Exception:
                pass

    async def _generate_event_reply(self, prompt: str, user_id: str,
                                      group_id: str, user_name: str = "") -> str | None:
        """用ChatAgent为事件生成回应（戳一戳/撤回等）。"""
        if not self.on_message:
            return None
        try:
            # 如果没传用户名，查一下
            if not user_name:
                user_name = await self._get_user_name(user_id, group_id)
            class _EventMsg:
                pass
            fake = _EventMsg()
            fake.text = prompt
            fake.user_id = user_id
            fake.group_id = group_id
            fake.is_group = bool(group_id)
            fake.sender_name = user_name  # 传真实用户名，不再写死"event"
            fake.is_at_me = True
            fake.self_id = self._self_id
            fake.sub_type = ""
            fake.image_urls = []
            fake.message_id = 0
            fake._is_system_event = True  # 标记为系统事件，qq_handler跳过缓冲/社交检查
            return await self.on_message(fake)
        except Exception as e:
            logger.debug(f"[QQ] 事件回应生成失败: {e}")
            return None

    # ================================================================
    # 发送消息
    # ================================================================

    async def send_reply(self, original: QQMessage, text: str) -> int:
        """
        回复一条消息（引用+智能@+分段发送）。

        长回复自动分段，每段间隔0.8-1.5秒，防被群检测成机器人。
        """
        import random

        # 异常消息检测（分段前检查，异常就不发了）
        if self._is_abnormal_reply(text):
            # 先发出去再撤回+解释
            if original.is_group:
                msg_id = await self.send_group_message(original.group_id, text)
            else:
                msg_id = await self.send_private_message(original.user_id, text)
            if msg_id:
                await self._auto_recall(msg_id, text, original)
            return msg_id or 0

        # 引用原消息
        reply_prefix = ""
        if original.message_id:
            reply_prefix = f"[CQ:reply,id={original.message_id}]"

        # 智能@（群聊）
        at_prefix = ""
        if original.is_group:
            should_at = True
            last_user = getattr(self, '_last_reply_user', {}).get(original.group_id)
            if last_user == original.user_id:
                should_at = False
            if not hasattr(self, '_last_reply_user'):
                self._last_reply_user = {}
            self._last_reply_user[original.group_id] = original.user_id
            if should_at:
                at_prefix = f"[CQ:at,qq={original.user_id}] "

        # 分段处理
        segments = self._split_message(text)
        max_segments = 3 if original.is_group else 5

        if len(segments) > max_segments:
            segments = segments[:max_segments]

        first_msg_id = 0
        sent_texts = []  # 用于重复检测

        for i, seg in enumerate(segments):
            # 重复检测：跟前面发过的内容一样就跳过
            if i > 0 and seg in sent_texts:
                continue

            # 第1段加引用+@，后续段不加
            if i == 0:
                seg = f"{reply_prefix}{at_prefix}{seg}"

            # 发送（记录回复给谁，用于撤回人员匹配）
            if original.is_group:
                msg_id = await self.send_group_message(
                    original.group_id, seg, reply_to_user=original.user_id)
            else:
                msg_id = await self.send_private_message(
                    original.user_id, seg, reply_to_user=original.user_id)

            if i == 0:
                first_msg_id = msg_id or 0

            sent_texts.append(seg)

            # 段间延迟（最后一段不等）
            if i < len(segments) - 1:
                await asyncio.sleep(random.uniform(0.8, 1.5))

        return first_msg_id

    @staticmethod
    def _split_message(text: str) -> list[str]:
        """
        将长消息分段。

        优先级：换行 → 句号/感叹号/问号 → 超40字硬切
        30字以内或含代码块不分段。
        """
        import re
        if not text:
            return []

        text = text.strip()
        if not text:
            return []

        # 短消息不分段
        if len(text) <= 30:
            return [text]

        # 代码块不分段
        if "```" in text:
            return [text]

        segments = []

        # 1. 按换行分段
        if "\n" in text:
            for line in text.split("\n"):
                line = line.strip()
                if line:
                    segments.append(line)

        # 2. 没有换行，按标点分段
        if not segments:
            parts = re.split(r"(?<=[。！？!?…])\s*", text)
            segments = [p.strip() for p in parts if p.strip()]

        # 3. 还是只有一段且太长，硬切
        if len(segments) == 1 and len(segments[0]) > 40:
            chunk = segments[0]
            segments = [chunk[i:i + 20] for i in range(0, len(chunk), 20)]

        # 合并太短的段（<5字的段合并到前一段）
        merged = []
        for seg in segments:
            if merged and len(seg) < 5:
                merged[-1] = merged[-1] + seg
            else:
                merged.append(seg)

        return merged if merged else [text]

    # 异常内容检测模式
    _ABNORMAL_PATTERNS = [
        r'Traceback \(most recent',     # Python错误堆栈
        r'File ".*", line \d+',          # Python文件行号
        r'Exception|Error:.*\n',         # 异常信息
        r'\[QQ\]|\[QZone\]|\[Memory\]',  # 日志前缀
        r'logger\.(info|debug|warning)', # 日志代码
        r'async def |await |import ',    # Python代码
        r'\{\"action\":',                # JSON API请求
        r'^\s*\{.*\}\s*$',              # 纯JSON
    ]

    def _is_abnormal_reply(self, text: str) -> bool:
        """检测回复内容是否异常（日志/乱码/代码/报错）。"""
        import re
        # 去掉CQ码再检测
        clean = re.sub(r'\[CQ:[^\]]+\]', '', text).strip()
        if not clean:
            return False
        # 匹配异常模式
        for pattern in self._ABNORMAL_PATTERNS:
            if re.search(pattern, clean):
                return True
        # 乱码检测：非中英文数字标点占比超过50%
        normal_chars = len(re.findall(r'[-\u4e00-\u9fff\w\s，。！？、；：""''（）…～·,.!?;:\'"()@#%&*+=/<>]', clean))
        if len(clean) > 10 and normal_chars / len(clean) < 0.5:
            return True
        return False

    async def _auto_recall(self, msg_id: int, text: str, original: QQMessage) -> None:
        """自动撤回异常消息并用LLM解释。"""
        try:
            # 先撤回
            await self._call_api("delete_msg", {"message_id": msg_id})
            logger.info(f"[QQ] 自动撤回异常消息: {text[:50]}")

            # 用ChatAgent生成解释（白自己想为什么发了这个）
            explanation = None
            if self.on_message:
                # 构造一个假的系统消息让ChatAgent解释
                fake_text = (
                    f"[系统提示] 你刚才不小心发了一条奇怪的消息出去，已经撤回了。"
                    f"那条消息的内容是：「{text[:100]}」"
                    f"现在跟对方解释一下怎么回事，用你自己的方式说。简短就好。"
                )
                # 直接用on_message回调获取ChatAgent的回复
                class _FakeMsg:
                    pass
                fake = _FakeMsg()
                fake.text = fake_text
                fake.user_id = original.user_id
                fake.group_id = original.group_id if original.is_group else ""
                fake.is_group = original.is_group
                fake.sender_name = original.sender_name or "用户"
                fake.is_at_me = True
                fake.self_id = self._self_id
                fake.sub_type = ""
                fake.image_urls = []
                fake.message_id = 0
                fake._is_system_event = True
                try:
                    explanation = await self.on_message(fake)
                except Exception:
                    pass

            if not explanation:
                explanation = "啊不好意思，刚才发错了..."

            # 发解释（事件性质，不算正常聊天）
            if original.is_group:
                await self.send_group_message(
                    original.group_id, explanation,
                    reply_to_user=original.user_id, is_event=True)
            else:
                await self.send_private_message(
                    original.user_id, explanation,
                    reply_to_user=original.user_id, is_event=True)

        except Exception as e:
            logger.debug(f"[QQ] 自动撤回失败: {e}")

    async def send_private_message(self, user_id: str, text: str,
                                   reply_to_user: str = "",
                                   is_event: bool = False) -> int:
        """发送私聊消息，返回消息ID。"""
        result = await self._call_api("send_private_msg", {
            "user_id": int(user_id),
            "message": text,
        }, wait_response=True)
        msg_id = (result or {}).get("message_id", 0)
        if msg_id:
            self._record_sent(msg_id, text, reply_to_user=reply_to_user or user_id,
                             is_event=is_event)
        return msg_id

    async def send_group_message(self, group_id: str, text: str,
                                 reply_to_user: str = "",
                                 is_event: bool = False) -> int:
        """发送群聊消息，返回消息ID。"""
        result = await self._call_api("send_group_msg", {
            "group_id": int(group_id),
            "message": text,
        }, wait_response=True)
        msg_id = (result or {}).get("message_id", 0)
        if msg_id:
            self._record_sent(msg_id, text, group_id=group_id,
                             reply_to_user=reply_to_user, is_event=is_event)
        return msg_id

    def _record_sent(self, msg_id: int, content: str, group_id: str = "",
                     is_event: bool = False, reply_to_user: str = "") -> None:
        """记录发送的消息ID（用于撤回+撤回检测）。"""
        self._sent_messages.append({
            "msg_id": msg_id,
            "content": content[:200],
            "time": time.time(),
            "group_id": group_id,
            "is_event": is_event,  # 事件回应不算"参与对话"
            "reply_to_user": reply_to_user,  # 这条消息是回复给谁的（用于撤回人员匹配）
        })
        if len(self._sent_messages) > self._max_sent:
            self._sent_messages = self._sent_messages[-self._max_sent:]

    def get_last_sent_id(self) -> int:
        """获取最近发送的消息ID（用于撤回）。"""
        if self._sent_messages:
            return self._sent_messages[-1]["msg_id"]
        return 0

    async def _call_api(self, action: str, params: dict, wait_response: bool = False) -> Optional[dict]:
        """
        调用OneBot v11 API。

        Args:
            action: API动作名
            params: 参数
            wait_response: 是否等待返回值（发消息时需要拿message_id）

        Returns:
            API返回的data字典，或None
        """
        if not self._ws:
            logger.warning("[QQ] WebSocket未连接，无法调用API")
            return None

        echo = f"{action}_{int(time.time() * 1000)}_{id(params) % 10000}"
        payload = {
            "action": action,
            "params": params,
            "echo": echo,
        }

        try:
            # 需要等返回值时，创建Future
            if wait_response:
                loop = asyncio.get_running_loop()
                future: asyncio.Future = loop.create_future()
                self._pending_api[echo] = future

            await self._ws.send_json(payload)
            logger.debug(f"[QQ] API调用: {action}")

            if wait_response:
                try:
                    # 最多等5秒
                    result = await asyncio.wait_for(future, timeout=5.0)
                    return result
                except asyncio.TimeoutError:
                    self._pending_api.pop(echo, None)
                    logger.debug(f"[QQ] API响应超时: {action}")
                    return None

            return None
        except Exception as e:
            self._pending_api.pop(echo, None)
            logger.warning(f"[QQ] API调用失败 {action}: {e}")
            return None
