"""
white_salary/core/services/startup_checker.py

离线消息自动回复 — 白上线后检查离线期间的未回复消息。

功能：
  1. 私聊：拉最近联系人的消息历史，最后一条是对方发的=未回复→自动回复
  2. 群聊：拉活跃群的消息历史，@白/唤醒词且白之后没发言=未回复→自动回复
  3. 已处理消息ID持久化（7天过期）
  4. 时间上下文（"这是X小时前的消息"）
  5. 防重复（30分钟内重连不重复检查）

借鉴v2的realtime_private_memory/group_unreplied_detector，重写适配我们架构。
"""

import asyncio
import json
import time
from pathlib import Path
from typing import Optional

from loguru import logger


# 配置
PRIVATE_MSG_COUNT = 20       # 每人拉多少条历史
GROUP_MSG_COUNT = 100        # 每群拉多少条历史
PRIVATE_MAX_AGE = 86400      # 私聊最多回24小时内的
GROUP_MAX_AGE = 21600        # 群聊最多回6小时内的
MAX_GROUP_REPLIES = 3        # 每群最多回3条
REPLY_INTERVAL = 2.0         # 每条回复间隔2秒
RECHECK_COOLDOWN = 1800      # 30分钟内重连不重复检查
STARTUP_DELAY = 10           # 启动延迟10秒
PROCESSED_EXPIRE_DAYS = 7    # 已处理ID过期天数


class StartupChecker:
    """
    离线消息自动回复。

    使用方式：
        checker = StartupChecker(adapter, agent, data_dir="data/qq")
        await checker.check_and_reply()  # 在独立task中运行
    """

    def __init__(
        self,
        adapter,
        agent,
        bot_name: str = "白",
        family_qq: list[str] = None,
        data_dir: str = "data/qq",
    ) -> None:
        self._adapter = adapter
        self._agent = agent
        self._bot_name = bot_name
        self._family_qq = set(family_qq or [])
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)

        # 已处理消息ID
        self._processed_path = self._data_dir / "processed_msg_ids.json"
        self._processed: dict[str, int] = {}  # {msg_id: timestamp}
        self._load_processed()

        # 上次检查时间
        self._last_check_time: float = 0

    async def _isolated_reply(self, user_input: str, **chat_kwargs) -> str:
        """
        用独立的临时上下文生成一条回复，不读、不写共享 agent 的短期记忆。

        2026-07-02 审计修复（批4）：旧实现"快照→置空→恢复"直接整体替换共享
        agent 的 _messages——补发在独立 task 里逐联系人串行跑（每条等 LLM 数秒），
        期间并发进入 handle_qq_message 的实时问答会写进临时列表，finally 恢复
        快照时被整体丢弃（并发对话记忆丢失，且污染是双向的）。

        现改为【构造轻量独立 agent】方案（二选一中改动最小、并发最安全的一个）：
        浅拷贝共享 agent（复用 LLM/人格/工具等组件），仅把短期记忆换成一次性
        ShortTermMemory——共享记忆全程不被触碰，补发与实时消息天然并发安全。
        没选 asyncio.Lock 互斥方案：补发可能持续几分钟，锁会让期间的实时回复
        长时间阻塞，且需要同步改动 qq_handler 的生成路径。

        Args:
            user_input: 要回复的内容
            **chat_kwargs: 透传给 agent.chat 的参数（user_name/user_id/is_group 等）

        Returns:
            agent 生成的回复文本
        """
        import copy

        from white_salary.core.memory.short_term import ShortTermMemory

        temp_agent = copy.copy(self._agent)
        # 一次性空上下文：不带persist_path，不落盘、不影响共享记忆
        temp_agent._memory = ShortTermMemory(max_turns=20)
        return await temp_agent.chat(user_input, **chat_kwargs)

    # ================================================================
    # 主入口
    # ================================================================

    async def check_and_reply(self) -> None:
        """
        启动后检查离线期间的未回复消息。

        在独立asyncio task中运行，不阻塞正常消息处理。
        """
        # 等待NapCat稳定
        await asyncio.sleep(STARTUP_DELAY)

        # 30分钟内重连不重复
        if time.time() - self._last_check_time < RECHECK_COOLDOWN:
            logger.debug("[启动检查] 30分钟内重连，跳过")
            return

        # 休息中不检查
        try:
            from white_salary.core.rest_system import RestSystem
            rest = RestSystem()
            if rest.is_resting:
                logger.info("[启动检查] 白在休息，跳过离线消息检查")
                return
        except Exception:
            pass

        self._last_check_time = time.time()
        logger.info("[启动检查] 开始检查离线未回复消息...")

        total = 0

        # 1. 私聊（family_qq优先）
        try:
            total += await self._check_private_messages()
        except Exception as e:
            logger.warning(f"[启动检查] 私聊检查失败: {e}")

        # 2. 群聊
        try:
            total += await self._check_group_messages()
        except Exception as e:
            logger.warning(f"[启动检查] 群聊检查失败: {e}")

        if total > 0:
            logger.info(f"[启动检查] 完成，共回复{total}条离线消息")
        else:
            logger.info("[启动检查] 没有未回复的离线消息")

    # ================================================================
    # 私聊检查
    # ================================================================

    async def _check_private_messages(self) -> int:
        """检查私聊未读消息并回复。"""
        replied = 0

        # 获取最近联系人
        contacts = await self._adapter._call_api(
            "get_recent_contact", {"count": 20}, wait_response=True
        )
        if not contacts:
            return 0

        # 提取私聊联系人（chatType=1或message_type=private）
        data = contacts if isinstance(contacts, list) else (
            contacts.get("data", contacts.get("list", []))
            if isinstance(contacts, dict) else []
        )
        user_ids = []
        for c in data:
            latest = c.get("lastestMsg", {})
            chat_type = c.get("chatType", 0)
            msg_type = latest.get("message_type", c.get("message_type", ""))
            # 只要私聊（chatType=1 或 message_type=private）
            is_private = (str(chat_type) == "1" or msg_type == "private"
                          or (not chat_type and not msg_type))  # 没类型信息也尝试
            if not is_private:
                continue
            uid = str(latest.get("user_id") or c.get("peerUin")
                      or c.get("user_id") or c.get("peerUid") or "")
            if uid and uid != self._adapter._self_id:
                user_ids.append(uid)

        if not user_ids:
            return 0

        # family_qq排前面
        family = [u for u in user_ids if u in self._family_qq]
        others = [u for u in user_ids if u not in self._family_qq]
        ordered = family + others

        for uid in ordered:
            try:
                result = await self._adapter._call_api(
                    "get_friend_msg_history",
                    {"user_id": int(uid), "count": PRIVATE_MSG_COUNT},
                    wait_response=True,
                )
                if not result:
                    continue

                # 兼容多种返回格式：{data:{messages:[]}}, {messages:[]}, 或直接列表
                if isinstance(result, dict):
                    messages = (result.get("data") or {}).get("messages", [])
                    if not messages:
                        messages = result.get("messages", [])
                elif isinstance(result, list):
                    messages = result
                else:
                    messages = []
                if not isinstance(messages, list) or not messages:
                    continue

                # 找白最后一次发言的位置，收集之后对方发的所有消息
                # 这样别人连续发了多条，白能看到完整内容，回复才连贯
                bot_last_idx = -1  # 白最后发言的位置（-1=白没发过言）
                for i, m in enumerate(messages):
                    sid = str(m.get("sender", {}).get("user_id", m.get("user_id", "")))
                    if sid == self._adapter._self_id:
                        bot_last_idx = i

                # 收集白最后发言之后、对方发的所有消息
                unreplied_msgs = []
                start_idx = bot_last_idx + 1  # 白没发过言就从头开始(0)
                for m in messages[start_idx:]:
                    sid = str(m.get("sender", {}).get("user_id", m.get("user_id", "")))
                    if sid == self._adapter._self_id:
                        continue  # 跳过白自己的消息（理论上不会有，但防万一）
                    msg_time = m.get("time", 0)
                    msg_id = str(m.get("message_id", ""))
                    # 时间检查（24小时内）
                    if msg_time and time.time() - msg_time > PRIVATE_MAX_AGE:
                        continue
                    # 已处理的跳过
                    if self._is_processed(msg_id):
                        continue
                    content = self._extract_text(m)
                    if content:
                        unreplied_msgs.append(m)

                if not unreplied_msgs:
                    continue

                # 合并所有未回复消息的内容
                sender_name = unreplied_msgs[0].get("sender", {}).get("nickname", uid)
                merged_parts = []
                all_msg_ids = []
                for m in unreplied_msgs:
                    content = self._extract_text(m)
                    if content:
                        merged_parts.append(content)
                    all_msg_ids.append(str(m.get("message_id", "")))

                merged_content = "\n".join(merged_parts)
                # 用最后一条消息的时间做提示
                last_time = unreplied_msgs[-1].get("time", 0)
                time_hint = self._format_time_hint(last_time)

                msg_count = len(unreplied_msgs)
                if msg_count == 1:
                    user_input = (
                        f"[私聊] {sender_name}(QQ:{uid})给你发了消息：\n"
                        f"「{merged_content[:500]}」\n"
                        f"{time_hint}"
                        f"回复他就好。"
                    )
                else:
                    user_input = (
                        f"[私聊] {sender_name}(QQ:{uid})给你发了{msg_count}条消息：\n"
                        f"「{merged_content[:500]}」\n"
                        f"{time_hint}"
                        f"回复他就好，针对他说的内容一起回复。"
                    )

                # 用隔离上下文回复，不冲掉正常对话记忆（见 _isolated_reply）
                reply = await self._isolated_reply(
                    user_input, user_name=sender_name, user_id=uid,
                )
                if reply:
                    import re
                    reply = re.sub(r'<[^>]+>', '', reply).strip()
                    await self._adapter.send_private_message(uid, reply)
                    # 把所有未回复消息都标记为已处理
                    for mid in all_msg_ids:
                        self._mark_processed(mid)
                    replied += 1
                    logger.info(f"[启动检查] 私聊回复{sender_name}({msg_count}条合并): {reply[:30]}")
                    await asyncio.sleep(REPLY_INTERVAL)

            except Exception as e:
                logger.debug(f"[启动检查] 私聊{uid}检查失败: {e}")

        return replied

    # ================================================================
    # 群聊检查
    # ================================================================

    async def _check_group_messages(self) -> int:
        """检查群聊中@白或唤醒词且未回复的消息。"""
        replied = 0

        # 获取最近联系人中的群
        contacts = await self._adapter._call_api(
            "get_recent_contact", {"count": 20}, wait_response=True
        )
        # 提取群聊联系人（chatType=2或message_type=group）
        data = contacts if isinstance(contacts, list) else (
            contacts.get("data", contacts.get("list", []))
            if isinstance(contacts, dict) else []
        )
        group_ids = []
        for c in data:
            latest = c.get("lastestMsg", {})
            chat_type = c.get("chatType", 0)
            msg_type = latest.get("message_type", c.get("message_type", ""))
            is_group = (str(chat_type) == "2" or msg_type == "group")
            if not is_group:
                continue
            gid = str(c.get("peerUin") or latest.get("group_id")
                      or c.get("group_id") or "")
            if gid:
                group_ids.append(gid)

        if not group_ids:
            # 尝试用群列表
            try:
                groups = await self._adapter._call_api("get_group_list", {}, wait_response=True)
                if isinstance(groups, list):
                    group_ids = [str(g.get("group_id", "")) for g in groups[:10]]
            except Exception:
                pass

        if not group_ids:
            return 0

        for gid in group_ids:
            try:
                group_replied = await self._check_single_group(gid)
                replied += group_replied
            except Exception as e:
                logger.debug(f"[启动检查] 群{gid}检查失败: {e}")

        return replied

    async def _check_single_group(self, group_id: str) -> int:
        """检查单个群的未回复消息。"""
        replied = 0

        result = await self._adapter._call_api(
            "get_group_msg_history",
            {"group_id": int(group_id), "count": GROUP_MSG_COUNT},
            wait_response=True,
        )
        if not result:
            return 0

        # 兼容多种返回格式
        if isinstance(result, dict):
            messages = result.get("messages", []) or (result.get("data") or {}).get("messages", [])
        elif isinstance(result, list):
            messages = result
        else:
            messages = []
        if not isinstance(messages, list) or not messages:
            return 0

        # 找白发言的时间点
        bot_msg_times = set()
        for msg in messages:
            sender_id = str(msg.get("sender", {}).get("user_id", msg.get("user_id", "")))
            if sender_id == self._adapter._self_id:
                msg_time = msg.get("time", 0)
                if msg_time:
                    bot_msg_times.add(msg_time)

        # 找@白或唤醒词的消息
        unreplied = []
        for msg in messages:
            sender_id = str(msg.get("sender", {}).get("user_id", msg.get("user_id", "")))
            if sender_id == self._adapter._self_id:
                continue  # 白自己的消息跳过

            msg_time = msg.get("time", 0)
            msg_id = str(msg.get("message_id", ""))

            # 时间检查（6小时内）
            if msg_time and time.time() - msg_time > GROUP_MAX_AGE:
                continue

            # 已处理
            if self._is_processed(msg_id):
                continue

            # 检查是否@白或包含唤醒词
            raw_msg = msg.get("raw_message", "")
            content = self._extract_text(msg)

            is_at = f"[CQ:at,qq={self._adapter._self_id}]" in raw_msg
            is_wake = self._check_wake_word(content)

            if not is_at and not is_wake:
                continue

            # 检查白之后有没有发言
            has_replied = any(bt > msg_time for bt in bot_msg_times) if msg_time else False
            if has_replied:
                self._mark_processed(msg_id)
                continue

            unreplied.append(msg)

        # 从旧到新回复，最多3条
        unreplied.sort(key=lambda m: m.get("time", 0))
        for msg in unreplied[:MAX_GROUP_REPLIES]:
            msg_id = str(msg.get("message_id", ""))
            content = self._extract_text(msg)
            sender_name = msg.get("sender", {}).get("card", "") or msg.get("sender", {}).get("nickname", "")
            sender_id = str(msg.get("sender", {}).get("user_id", ""))
            msg_time = msg.get("time", 0)

            time_hint = self._format_time_hint(msg_time)
            user_input = (
                f"[QQ群消息] {sender_name}(QQ:{sender_id})在群里叫你：\n"
                f"「{content[:200]}」\n"
                f"{time_hint}"
                f"回复他就好，简短自然。"
            )

            # 用隔离上下文回复，不冲掉正常对话记忆（见 _isolated_reply）
            reply = await self._isolated_reply(
                user_input, user_name=sender_name, user_id=sender_id, is_group=True,
            )
            if reply:
                import re
                reply = re.sub(r'<[^>]+>', '', reply).strip()
                # 引用原消息+@
                orig_msg_id = msg.get("message_id", 0)
                reply_text = f"[CQ:reply,id={orig_msg_id}][CQ:at,qq={sender_id}] {reply}"
                await self._adapter.send_group_message(group_id, reply_text)
                self._mark_processed(msg_id)
                replied += 1
                logger.info(f"[启动检查] 群{group_id}回复{sender_name}: {reply[:30]}")
                await asyncio.sleep(REPLY_INTERVAL)

        return replied

    # ================================================================
    # 唤醒词检测
    # ================================================================

    def _check_wake_word(self, text: str) -> bool:
        """检查文本是否包含唤醒词（复用SmartReplyDecider的逻辑）。"""
        if not text or not self._bot_name:
            return False
        import re
        name = re.escape(self._bot_name)
        # 整条消息只有唤醒词
        if re.match(rf"^\s*[，,]?\s*{name}\s*[，,。！？!?]?\s*$", text):
            return True
        # 唤醒词独立出现（前后是开头/结尾/空白/标点）
        if re.search(rf"(?:^|(?<=[\s，,。！？!?]))\s*{name}\s*(?=[\s，,。！？!?]|$)", text):
            return True
        return False

    # ================================================================
    # 工具方法
    # ================================================================

    def _extract_text(self, msg: dict) -> str:
        """从消息中提取文本内容。"""
        raw = msg.get("raw_message", "")
        if not raw:
            # 尝试从message数组提取
            message = msg.get("message", [])
            if isinstance(message, list):
                parts = []
                for seg in message:
                    if isinstance(seg, dict) and seg.get("type") == "text":
                        parts.append(seg.get("data", {}).get("text", ""))
                raw = "".join(parts)

        if not raw:
            return ""

        import re
        # 去掉CQ码，保留文本
        text = re.sub(r"\[CQ:at,qq=\d+\]", "", raw)
        text = re.sub(r"\[CQ:[^\]]+\]", "", text)
        return text.strip()

    def _format_time_hint(self, msg_time: int) -> str:
        """生成时间上下文提示。"""
        if not msg_time:
            return "（你之前不在线没看到这条消息）\n"
        diff = time.time() - msg_time
        if diff < 300:
            return ""  # 5分钟内不需要提示
        elif diff < 3600:
            mins = int(diff / 60)
            return f"（这是{mins}分钟前发的，你当时不在线）\n"
        elif diff < 86400:
            hours = int(diff / 3600)
            return f"（这是{hours}小时前发的，你当时不在线没看到）\n"
        else:
            days = int(diff / 86400)
            return f"（这是{days}天前发的，你当时不在线）\n"

    # ================================================================
    # 已处理消息ID
    # ================================================================

    def _is_processed(self, msg_id: str) -> bool:
        return str(msg_id) in self._processed

    def _mark_processed(self, msg_id: str) -> None:
        self._processed[str(msg_id)] = int(time.time())
        self._save_processed()

    def _load_processed(self) -> None:
        if not self._processed_path.exists():
            return
        try:
            data = json.loads(self._processed_path.read_text(encoding="utf-8"))
            self._processed = data.get("processed_ids", {})
            self._cleanup_expired()
        except Exception:
            self._processed = {}

    def _save_processed(self) -> None:
        try:
            data = {"processed_ids": self._processed}
            self._processed_path.write_text(
                json.dumps(data, ensure_ascii=False), encoding="utf-8"
            )
        except Exception as e:
            logger.debug(f"[启动检查] 保存已处理ID失败: {e}")

    def _cleanup_expired(self) -> None:
        """清理过期的已处理消息ID。"""
        now = int(time.time())
        expire = PROCESSED_EXPIRE_DAYS * 86400
        expired = [mid for mid, ts in self._processed.items() if now - ts > expire]
        for mid in expired:
            del self._processed[mid]
        if expired:
            self._save_processed()
