"""
white_salary/core/services/qzone_monitor.py

QQ空间评论监控服务 — 定期检查新评论并自动回复。

借鉴v2的monitor.py + qzone.py的check_and_get_new_comments：
  - 3小时轮询检查新评论
  - 只在8:00-23:00活跃
  - LLM生成个性化回复（用memory_llm）
  - 已回复评论ID持久化（防重复）
  - 启动时检查未回复评论（B13）
  - 连续错误计数+Cookie过期标记

重写适配我们的架构：
  - 异步后台任务
  - 集成rate_limiter
  - 用memory_llm生成回复（不碰主对话模型）
"""

import asyncio
import json
import threading
import time
from pathlib import Path
from typing import Optional

from loguru import logger


CHECK_INTERVAL = 900  # 15分钟检查一次（太频繁会被QQ空间限流）
ACTIVE_HOUR_START = 8   # 早上8点开始活跃
ACTIVE_HOUR_END = 23    # 晚上23点停止
MAX_REPLIED_IDS = 1000  # 最多记录1000个已回复评论ID
MAX_REPLIES_PER_CHECK = 5  # 每次检查最多回复5条


class QzoneMonitor:
    """
    QQ空间评论监控服务。

    使用方式:
        monitor = QzoneMonitor()
        await monitor.start()   # 启动后台监控
        await monitor.stop()    # 停止监控

    启动时自动检查未回复评论（B13）。
    """

    def __init__(self, data_dir: str = "data/qzone", agent=None) -> None:
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._agent = agent  # ChatAgent，走完整人设流程

        self._replied_path = self._data_dir / "replied_comments.json"
        self._replied_ids: set[str] = set()
        self._replied_lock = threading.Lock()  # 线程安全
        self._checking = False  # 防止并发检查（手动+自动同时跑）
        self._running = False
        self._task: Optional[asyncio.Task] = None
        # 2026-07-02 审计修复（批4）：Cookie过期桌面提醒的每日节流标记（"YYYY-MM-DD"）
        self._last_cookie_notice_date: str = ""

        self._load_replied()

    # ================================================================
    # 启停
    # ================================================================

    async def start(self) -> None:
        """
        启动监控循环（会一直运行，直到stop()被调用）。

        注意：这个方法不会返回！它会一直await _loop()。
        所以用run_until_complete(start())时线程不会退出。
        """
        if self._running:
            return
        self._running = True
        logger.info("[QZone监控] 启动评论监控")

        # 启动时先检查一次未回复评论（B13）
        try:
            await self.check_and_reply()
        except Exception as e:
            logger.warning(f"[QZone监控] 启动检查失败: {e}")

        # 直接await _loop()，不用create_task
        # 这样run_until_complete(start())不会提前退出
        await self._loop()

    async def stop(self) -> None:
        """停止监控（下一个循环周期结束后退出）。"""
        self._running = False
        logger.info("[QZone监控] 已停止")

    async def _loop(self) -> None:
        """主监控循环。"""
        while self._running:
            try:
                # 2026-07-02 审计修复（批4）：Cookie过期时轮询间隔放大4倍
                # （减刷"Cookie已过期"日志），恢复登录后自动回到正常间隔
                await asyncio.sleep(self._current_check_interval())

                # 检查是否在活跃时段
                hour = time.localtime().tm_hour
                if hour < ACTIVE_HOUR_START or hour >= ACTIVE_HOUR_END:
                    logger.debug("[QZone监控] 非活跃时段，跳过检查")
                    continue

                # 5分钟超时保护，防止网络卡住导致永远不进入下一轮
                await asyncio.wait_for(self.check_and_reply(), timeout=300)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"[QZone监控] 循环异常: {e}")
                await asyncio.sleep(60)  # 出错后等1分钟再试

    # ================================================================
    # 检查+回复
    # ================================================================

    async def check_and_reply(self) -> int:
        """
        检查新评论并自动回复。

        Returns:
            成功回复的数量
        """
        # 防止并发（手动触发和自动监控同时跑）
        if self._checking:
            logger.debug("[QZone监控] 已有检查在进行中，跳过")
            return 0
        self._checking = True

        try:  # finally释放_checking标记
            from white_salary.adapters.platform.qzone_api import get_client
            from white_salary.core.qzone.rate_limiter import get_rate_limiter

            client = get_client()
            if not client.is_configured:
                logger.debug("[QZone监控] Cookie未配置，跳过检查")
                return 0
            if client.is_cookie_expired:
                logger.warning("[QZone监控] Cookie已过期，请在控制面板重新登录QQ空间")
                # 2026-07-02 审计修复（批4）：过期时推送桌面提醒（每天最多一次）
                self._push_cookie_expired_notice()
                return 0

            limiter = get_rate_limiter()

            # 获取最近的说说
            feeds = await client.get_feeds(count=5)
            if not feeds:
                return 0

            replied_count = 0

            for feed in feeds:
                tid = feed.get("tid", "")
                if not tid:
                    continue

                # 获取这条说说的评论
                comments = await client.get_comments(tid)
                if not comments:
                    continue

                # 构建这条说说下面的完整对话串（白能看到之前聊了什么）
                feed_content = feed.get("content", "")
                thread_lines = []
                for c in comments:
                    c_uin = str(c.get("uin", ""))
                    c_name = c.get("name", "")
                    c_text = c.get("content", "")[:80]
                    if c_uin == str(client.uin):
                        thread_lines.append(f"你回复：「{c_text}」")
                    else:
                        thread_lines.append(f"{c_name}：「{c_text}」")
                # 限制对话串长度（最多15条，防止上下文太长）
                if len(thread_lines) > 15:
                    thread_lines = thread_lines[-15:]
                thread_context = "\n".join(thread_lines)

                # 同一说说下已回复过的用户（per-user限制，防刷评论）
                replied_users_this_feed: set[str] = set()

                for cmt in comments:
                    cmt_uin = str(cmt.get("uin", ""))
                    cmt_id = str(cmt.get("commentid", ""))
                    cmt_content = cmt.get("content", "")
                    cmt_name = cmt.get("name", "")

                    # 跳过自己的评论
                    if cmt_uin == str(client.uin):
                        continue

                    # 跳过空评论（纯图片/纯表情/无文字）
                    if not cmt_content or not cmt_content.strip():
                        continue

                    # 黑名单检查（被拉黑的用户不回复）
                    try:
                        from white_salary.core.memory.user_filter import UserFilter, FilterResult
                        _uf = UserFilter()
                        if _uf.check(cmt_uin) == FilterResult.BLOCK:
                            continue
                    except Exception:
                        pass

                    # 同一说说下同一用户最多回1次（防刷评论攻击）
                    if cmt_uin in replied_users_this_feed:
                        continue

                    # 跳过已回复的
                    reply_key = f"{tid}_{cmt_id}"
                    if reply_key in self._replied_ids:
                        replied_users_this_feed.add(cmt_uin)
                        continue

                    # 新评论才学习兴趣（放在已回复检查之后，避免重复学习）
                    try:
                        from white_salary.core.qzone.interest_matcher import get_interest_matcher
                        get_interest_matcher().learn_from_qzone(cmt_uin, cmt_name, cmt_content)
                    except Exception:
                        pass

                    # 频率检查
                    if not limiter.can_do("reply"):
                        logger.debug("[QZone监控] 回复被限流")
                        return replied_count

                    # 生成回复（带完整对话串上下文）
                    reply = await self._generate_reply(
                        cmt_content, cmt_name, user_id=cmt_uin,
                        feed_content=feed_content, thread_context=thread_context,
                    )
                    if not reply:
                        continue

                    # 自动加@标签，让对方收到通知
                    at_tag = f"@{{uin:{cmt_uin},nick:{cmt_name},auto:1}}"
                    reply_with_at = f"{at_tag} {reply}"

                    # 发送引用回复（commentid+reply_uin=引用格式）
                    result = await client.reply_comment(
                        tid=tid,
                        content=reply_with_at,
                        commentid=cmt_id,
                        reply_uin=cmt_uin,
                    )

                    if result.get("success"):
                        self._mark_replied(reply_key)
                        replied_users_this_feed.add(cmt_uin)
                        limiter.record("reply")
                        limiter.record_success()
                        replied_count += 1
                        logger.info(
                            f"[QZone监控] 回复{cmt_name}的评论: {reply[:30]}"
                        )

                        # 记录到记忆
                        try:
                            from white_salary.adapters.platform.qzone_memory import get_qzone_memory
                            qm = get_qzone_memory()
                            qm.add_comment(cmt_name, cmt_content, reply, tid, cmt_id, owner_uin=client.uin)
                        except Exception:
                            pass

                        # 通知主人（通过QQ发消息）
                        await self._notify_owner(
                            f"QQ空间新评论: {cmt_name}说「{cmt_content[:30]}」，我回复了「{reply[:30]}」"
                        )

                        if replied_count >= MAX_REPLIES_PER_CHECK:
                            return replied_count
                    else:
                        limiter.record_error()
                        error = result.get("error", "")
                        logger.warning(f"[QZone监控] 回复失败: {error}")

                        # Cookie可能过期
                        if client.is_cookie_expired:
                            logger.warning("[QZone监控] Cookie已过期，停止回复")
                            # 2026-07-02 审计修复（批4）：检查中途过期同样推送桌面提醒（每日节流）
                            self._push_cookie_expired_notice()
                            return replied_count

            # ============================================================
            # 第二轮：检查白在别人空间评论过的说说，有没有人回复白
            # ============================================================
            try:
                from white_salary.adapters.platform.qzone_memory import get_qzone_memory
                qm = get_qzone_memory()
                # 获取白最近评论过的别人的说说（tid+目标用户信息）
                recent_comments = qm.get_recent_comments(count=10)
                # 收集白评论过的外部说说（tid+owner_uin）
                checked_tids = {f.get("tid", "") for f in feeds}  # 上面已经检查过的
                external_feeds = []  # [(tid, owner_uin), ...]
                for c in recent_comments:
                    tid = c.get("tid", "")
                    owner = c.get("owner_uin", "")
                    if tid and owner and tid not in checked_tids and len(external_feeds) < 5:
                        external_feeds.append((tid, owner))
                        checked_tids.add(tid)

                for tid, owner_uin in external_feeds:
                    if replied_count >= MAX_REPLIES_PER_CHECK:
                        break
                    comments = await client.get_comments(tid, owner_uin=owner_uin)
                    if not comments:
                        continue

                    # 构建外部说说的对话串
                    ext_thread_lines = []
                    for c in comments:
                        c_uin = str(c.get("uin", ""))
                        c_name = c.get("name", "")
                        c_text = c.get("content", "")[:80]
                        if c_uin == str(client.uin):
                            ext_thread_lines.append(f"你评论：「{c_text}」")
                        else:
                            ext_thread_lines.append(f"{c_name}：「{c_text}」")
                    if len(ext_thread_lines) > 15:
                        ext_thread_lines = ext_thread_lines[-15:]
                    ext_thread = "\n".join(ext_thread_lines)

                    ext_replied_users: set[str] = set()

                    for cmt in comments:
                        cmt_uin = str(cmt.get("uin", ""))
                        cmt_id = str(cmt.get("commentid", ""))
                        cmt_content = cmt.get("content", "")
                        cmt_name = cmt.get("name", "")

                        if cmt_uin == str(client.uin):
                            continue

                        # 跳过空评论
                        if not cmt_content or not cmt_content.strip():
                            continue

                        # 黑名单检查
                        try:
                            from white_salary.core.memory.user_filter import UserFilter, FilterResult
                            if UserFilter().check(cmt_uin) == FilterResult.BLOCK:
                                continue
                        except Exception:
                            pass

                        # 同一说说下同一用户最多回1次
                        if cmt_uin in ext_replied_users:
                            continue

                        reply_key = f"{tid}_{cmt_id}"
                        if reply_key in self._replied_ids:
                            ext_replied_users.add(cmt_uin)
                            continue
                        if not limiter.can_do("reply"):
                            break

                        reply = await self._generate_reply(
                            cmt_content, cmt_name, user_id=cmt_uin,
                            feed_content="", thread_context=ext_thread,
                        )
                        if not reply:
                            continue

                        # 自动加@标签
                        ext_at_tag = f"@{{uin:{cmt_uin},nick:{cmt_name},auto:1}}"
                        ext_reply = f"{ext_at_tag} {reply}"

                        result = await client.reply_comment(
                            tid=tid, content=ext_reply,
                            commentid=cmt_id, reply_uin=cmt_uin,
                            host_uin=owner_uin,
                        )
                        if result.get("success"):
                            self._mark_replied(reply_key)
                            ext_replied_users.add(cmt_uin)
                            limiter.record("reply")
                            limiter.record_success()
                            replied_count += 1
                            logger.info(f"[QZone监控] 回复{cmt_name}在外部说说的评论: {reply[:30]}")
                            qm.add_comment(cmt_name, cmt_content, reply, tid, cmt_id, owner_uin=owner_uin)
                        else:
                            limiter.record_error()
            except Exception as e:
                logger.debug(f"[QZone监控] 外部评论检查异常: {e}")

            # ============================================================
            # 第三轮：查"与我相关"通知，找所有@白/评论白的记录
            # 不管在谁的说说下@白，这里都能检测到
            # ============================================================
            try:
                notifications = await client.get_notifications(count=10)
                for notif in notifications:
                    if replied_count >= MAX_REPLIES_PER_CHECK:
                        break
                    n_uin = notif.get("uin", "")
                    n_action = notif.get("action", "")
                    n_tid = notif.get("tid", "")
                    n_feed_uin = notif.get("feed_uin", "")

                    # 跳过自己、点赞、转发（只处理评论/@提到/回复）
                    if n_uin == str(client.uin):
                        continue
                    if n_action in ("点赞", "转发", "未知"):
                        continue
                    if not n_tid or not n_feed_uin:
                        continue
                    # 已处理过的跳过
                    notif_key = f"notif_{n_tid}_{n_uin}"
                    with self._replied_lock:
                        if notif_key in self._replied_ids:
                            continue

                    # 获取这条说说的评论，找到需要回复的
                    notif_comments = await client.get_comments(n_tid, owner_uin=n_feed_uin)
                    if not notif_comments:
                        continue

                    # 找这个人的最新评论
                    target_cmt = None
                    for nc in reversed(notif_comments):
                        if str(nc.get("uin", "")) == n_uin:
                            cmt_key = f"{n_tid}_{nc.get('commentid', '')}"
                            with self._replied_lock:
                                if cmt_key not in self._replied_ids:
                                    target_cmt = nc
                                    break

                    if not target_cmt:
                        self._mark_replied(notif_key)
                        continue

                    if not limiter.can_do("reply"):
                        break

                    cmt_content = target_cmt.get("content", "")
                    cmt_name = notif.get("nick", "")
                    cmt_id = target_cmt.get("commentid", "")

                    if not cmt_content or not cmt_content.strip():
                        self._mark_replied(notif_key)
                        continue

                    # 构建对话串
                    notif_thread = []
                    for nc in notif_comments:
                        nc_uin = str(nc.get("uin", ""))
                        nc_text = nc.get("content", "")[:80]
                        nc_name = nc.get("name", "")
                        if nc_uin == str(client.uin):
                            notif_thread.append(f"你回复：「{nc_text}」")
                        else:
                            notif_thread.append(f"{nc_name}：「{nc_text}」")
                    if len(notif_thread) > 15:
                        notif_thread = notif_thread[-15:]

                    reply = await self._generate_reply(
                        cmt_content, cmt_name, user_id=n_uin,
                        thread_context="\n".join(notif_thread),
                    )
                    if not reply:
                        continue

                    at_tag = f"@{{uin:{n_uin},nick:{cmt_name},auto:1}}"
                    result = await client.reply_comment(
                        tid=n_tid, content=f"{at_tag} {reply}",
                        commentid=cmt_id, reply_uin=n_uin,
                        host_uin=n_feed_uin,
                    )
                    if result.get("success"):
                        self._mark_replied(notif_key)
                        self._mark_replied(f"{n_tid}_{cmt_id}")
                        limiter.record("reply")
                        replied_count += 1
                        logger.info(f"[QZone监控] 通知回复{cmt_name}: {reply[:30]}")

                        await self._notify_owner(
                            f"QQ空间通知: {cmt_name}{n_action}了，我回复了「{reply[:30]}」"
                        )
            except Exception as e:
                logger.debug(f"[QZone监控] 通知检查异常: {e}")

            return replied_count

        except Exception as e:
            logger.warning(f"[QZone监控] 检查异常: {e}")
            return 0
        finally:
            self._checking = False

    # ================================================================
    # Cookie过期提醒（2026-07-02 审计修复（批4））
    # ================================================================

    def _should_push_cookie_notice(self, now: Optional[float] = None) -> bool:
        """
        2026-07-02 审计修复（批4）：Cookie过期桌面提醒的每日一次节流（可测函数）。

        同一自然日内只允许提醒一次；判定通过即占用当天名额。

        Args:
            now: 当前时间戳（None=用time.time()，测试时可注入固定值）

        Returns:
            这一次是否应该推送提醒
        """
        ts = time.time() if now is None else now
        today = time.strftime("%Y-%m-%d", time.localtime(ts))
        if self._last_cookie_notice_date == today:
            return False
        self._last_cookie_notice_date = today
        return True

    def _push_cookie_expired_notice(self) -> None:
        """
        2026-07-02 审计修复（批4）：Cookie过期时推送桌面提醒（每天最多一次）。

        审计实证Cookie全程过期336次仅刷WARNING日志、主人无感知——
        现改为经 CrossPlatformBridge 推到桌面端，下次WebSocket轮询时弹出。
        """
        if not self._should_push_cookie_notice():
            return
        try:
            from white_salary.core.cross_platform import CrossPlatformBridge
            CrossPlatformBridge().push_to_desktop(
                "QQ空间登录过期了，去设置面板重新登录下",
                from_user="白",
                source="qzone",
            )
            logger.info("[QZone监控] 已推送Cookie过期桌面提醒（每日最多一次）")
        except Exception as e:
            logger.warning(f"[QZone监控] Cookie过期提醒推送失败: {e}")

    def _current_check_interval(self) -> int:
        """
        2026-07-02 审计修复（批4）：计算当前轮询间隔。

        Cookie过期状态下间隔放大4倍（减刷日志），恢复登录后
        （save_cookie会重置过期标记）自动回到正常间隔。

        Returns:
            下一轮检查前等待的秒数
        """
        try:
            from white_salary.adapters.platform.qzone_api import get_client
            client = get_client()
            if client.is_configured and client.is_cookie_expired:
                return CHECK_INTERVAL * 4
        except Exception as e:
            logger.debug(f"[QZone监控] 轮询间隔计算失败，用默认值: {e}")
        return CHECK_INTERVAL

    # ================================================================
    # LLM回复生成
    # ================================================================

    def set_agent(self, agent) -> None:
        """设置ChatAgent（走完整人设流程）。"""
        self._agent = agent

    async def _generate_reply(
        self, comment: str, name: str, user_id: str = "",
        feed_content: str = "", thread_context: str = "",
    ) -> str:
        """用ChatAgent生成回复（走完整人设流程，跟QQ聊天一样）。"""
        try:
            if not self._agent:
                return self._fallback_reply(comment)

            # 每次回复前清空短期记忆，防止不同说说的上下文互相污染
            self._agent._memory.clear()

            # 构造输入：说说内容 + 完整对话串 + 当前要回复的评论
            parts = ["[QQ空间评论]"]
            if feed_content:
                parts.append(f"你发的说说：「{feed_content[:100]}」")
            if thread_context:
                parts.append(f"\n这条说说下面的对话：\n{thread_context}")
            parts.append(f"\n{name}最新的评论：「{comment[:200]}」")
            parts.append("回复他，简短自然就好。")
            user_input = "\n".join(parts)

            response = await self._agent.chat(
                user_input, user_name=name,
                user_id=user_id or "qzone_unknown",  # 传真实QQ号，好感度按人区分
                is_group=False,
            )
            reply = response.strip().strip('"\'')
            # 清理可能的XML标签
            import re
            reply = re.sub(r'<[^>]+>', '', reply).strip()
            if reply and len(reply) <= 100:
                return reply
        except Exception as e:
            logger.debug(f"[QZone监控] 回复生成失败: {e}")

        return self._fallback_reply(comment)

    def _fallback_reply(self, comment: str) -> str:
        """LLM不可用时的简单回复。"""
        import random
        templates = [
            "谢谢～", "嘿嘿", "好哒", "嗯嗯！",
            "哈哈哈", "么么", "爱你", "感谢支持！",
        ]
        return random.choice(templates)

    _owner_qq_cache: int = 0  # 缓存主人QQ号，只读一次conf.yaml

    async def _notify_owner(self, message: str) -> None:
        """通过QQ通知主人有新评论。"""
        try:
            # 缓存主人QQ号（只读一次conf.yaml）
            if not self._owner_qq_cache:
                try:
                    import yaml
                    conf = yaml.safe_load(Path("conf.yaml").read_text(encoding="utf-8"))
                    family_qq = conf.get("qq", {}).get("family_qq", [])
                    if family_qq:
                        self.__class__._owner_qq_cache = int(family_qq[0])
                except Exception:
                    pass

            if not self._owner_qq_cache:
                return

            from white_salary.adapters.tools.builtin.qq_api import _call
            await _call("send_private_msg", {
                "user_id": self._owner_qq_cache,
                "message": message,
            })
        except Exception as e:
            # QQ未启动时不要反复报错，只记一次debug日志
            logger.debug(f"[QZone监控] QQ通知跳过（QQ可能未启动）: {e}")

    # ================================================================
    # 已回复ID管理（B4）
    # ================================================================

    def is_replied(self, tid: str, commentid: str) -> bool:
        """检查某条评论是否已回复。"""
        with self._replied_lock:
            return f"{tid}_{commentid}" in self._replied_ids

    def _mark_replied(self, reply_key: str) -> None:
        """标记评论已回复。"""
        with self._replied_lock:
            self._replied_ids.add(reply_key)
            # 超过上限时淘汰旧的
            if len(self._replied_ids) > MAX_REPLIED_IDS:
                ids_list = list(self._replied_ids)
                self._replied_ids = set(ids_list[-MAX_REPLIED_IDS:])
            self._save_replied()

    def _load_replied(self) -> None:
        if not self._replied_path.exists():
            return
        try:
            data = json.loads(self._replied_path.read_text(encoding="utf-8"))
            self._replied_ids = set(data.get("replied_ids", []))
        except Exception as e:
            logger.debug(f"[QZone监控] 已回复列表加载失败: {e}")

    def _save_replied(self) -> None:
        try:
            data = {"replied_ids": list(self._replied_ids)}
            self._replied_path.write_text(
                json.dumps(data, ensure_ascii=False), encoding="utf-8"
            )
        except Exception as e:
            logger.debug(f"[QZone监控] 已回复列表保存失败: {e}")


# 全局单例
_instance: Optional[QzoneMonitor] = None


def get_qzone_monitor(agent=None) -> QzoneMonitor:
    """获取监控服务单例。"""
    global _instance
    if _instance is None:
        _instance = QzoneMonitor(agent=agent)
    elif agent and not _instance._agent:
        _instance.set_agent(agent)
    return _instance
