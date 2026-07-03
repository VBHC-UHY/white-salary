"""
white_salary/core/qzone/social_manager.py

QQ空间社交管理器 — 统一调度所有QQ空间社交功能。

借鉴v2的qzone_social.py：
  - 聊天触发逛空间（兴趣积累→异步逛）
  - 逛空间时自动评论
  - @人推荐
  - 评论格式化（@标签+内容）
  - 频率控制集成

重写适配我们的架构：
  - 异步（用asyncio）
  - 调用我们的qzone_api异步客户端
  - 集成rate_limiter / interest_matcher / visit_trigger
  - 用memory_llm生成评论（不碰主对话模型）
"""

import asyncio
import threading
import time
from typing import Optional

from loguru import logger

from white_salary.core.qzone.rate_limiter import get_rate_limiter
from white_salary.core.qzone.interest_matcher import get_interest_matcher
from white_salary.core.qzone.visit_trigger import get_visit_trigger


class QzoneSocialManager:
    """
    QQ空间社交管理器。

    功能：
      1. on_chat_message — 聊天消息时学习兴趣+判断逛空间
      2. visit_and_comment — 逛某人空间+自动评论
      3. get_at_targets — 发说说时推荐@谁
      4. build_comment — 格式化评论（含@标签）
      5. 频率控制（所有操作经过rate_limiter）
    """

    def __init__(self, self_uin: str = "", agent=None) -> None:
        self._self_uin = self_uin
        self._agent = agent  # ChatAgent，走完整人设流程
        self._limiter = get_rate_limiter()
        self._matcher = get_interest_matcher()
        self._trigger = get_visit_trigger()

        # 防止并发逛同一个人
        self._visiting: set[str] = set()
        self._visiting_lock = threading.Lock()

    def set_self_uin(self, uin: str) -> None:
        """设置白的QQ号（从qzone_api获取）。"""
        self._self_uin = uin

    def set_agent(self, agent) -> None:
        """设置ChatAgent（走完整人设流程）。"""
        self._agent = agent

    # ================================================================
    # 聊天消息处理
    # ================================================================

    def on_chat_message(
        self,
        uin: str,
        nick: str,
        message: str,
        quality: str = "normal",
        interest_change: float | None = None,
    ) -> bool:
        """
        收到聊天消息时调用。

        - 学习用户兴趣
        - 积累逛空间兴趣值
        - 返回是否应该触发逛空间

        Args:
            uin: 用户QQ号
            nick: 用户昵称
            message: 消息内容
            quality: 消息质量 normal/positive/negative
            interest_change: 直接指定兴趣变化量

        Returns:
            True = 应该触发逛空间（调用方应异步调visit_and_comment）
        """
        if not uin or uin == self._self_uin:
            return False

        # 学习用户兴趣
        self._matcher.learn_from_message(uin, nick, message)

        # 积累兴趣值
        should_visit = self._trigger.record_interaction(
            uin, message, quality, interest_change
        )

        with self._visiting_lock:
            already_visiting = uin in self._visiting
        if should_visit and not already_visiting:
            logger.info(f"[QZone社交] 兴趣达标，准备逛{nick}({uin})的空间")
            return True

        return False

    async def trigger_visit_async(self, uin: str, nick: str = "") -> None:
        """异步触发逛空间（后台执行，不阻塞聊天）。"""
        with self._visiting_lock:
            if uin in self._visiting:
                return
            self._visiting.add(uin)
        try:
            await self._do_visit_and_comment(uin, nick)
        except Exception as e:
            logger.warning(f"[QZone社交] 逛空间失败: {e}")
        finally:
            with self._visiting_lock:
                self._visiting.discard(uin)

    # ================================================================
    # 逛空间+自动评论
    # ================================================================

    async def _do_visit_and_comment(self, target_uin: str, nick: str = "") -> None:
        """逛某人空间：获取说说→生成评论→发表。"""
        # 频率检查
        if not self._limiter.can_do("visit"):
            logger.debug(f"[QZone社交] 逛空间被限流")
            return
        if not self._limiter.can_do("comment"):
            logger.debug(f"[QZone社交] 评论被限流")
            return

        try:
            from white_salary.adapters.platform.qzone_api import get_client
            client = get_client()
            if not client.is_configured:
                return

            # 获取对方说说
            feeds = await client.get_feeds(count=3, target_uin=target_uin)
            if not feeds:
                logger.debug(f"[QZone社交] {nick}({target_uin})没有说说或无权限")
                return

            # 记录逛空间
            self._limiter.record("visit")
            self._trigger.record_visit(target_uin)

            # 选第一条有内容的说说来评论
            feed = None
            for f in feeds:
                if f.get("content"):
                    feed = f
                    break
            if not feed:
                logger.debug(f"[QZone社交] {nick}的说说都没有文字内容")
                return

            # 获取这条说说的评论，构建对话串（白能看到别人说了什么）
            tid = feed["tid"]
            existing_comments = await client.get_comments(tid, owner_uin=target_uin)
            thread_lines = []
            for ec in (existing_comments or []):
                ec_name = ec.get("name", "")
                ec_text = ec.get("content", "")[:80]
                thread_lines.append(f"{ec_name}：「{ec_text}」")
            if len(thread_lines) > 10:
                thread_lines = thread_lines[-10:]
            thread_context = "\n".join(thread_lines)

            # 生成评论（带对话串上下文）
            comment = await self._generate_comment(
                feed["content"], nick, target_uin, thread_context=thread_context
            )
            if not comment:
                return

            # @可能感兴趣的人（根据说说内容匹配兴趣）
            at_targets = self.get_at_targets(
                feed["content"],
                exclude_uins={target_uin, self._self_uin},
            )
            if at_targets:
                at_parts = []
                for t in at_targets:
                    t_uin = t.get("uin", "")
                    t_nick = t.get("nick", "")
                    if t_uin and self._limiter.can_at_user(t_uin):
                        at_parts.append(f"@{{uin:{t_uin},nick:{t_nick},who:1,auto:1}}")
                        self._limiter.record_at_user(t_uin)
                if at_parts:
                    comment = " ".join(at_parts) + " " + comment

            # 在别人的说说下发一级评论
            result = await client.reply_comment(
                tid=tid,
                content=comment,
                commentid="",
                reply_uin="",
                host_uin=target_uin,
            )

            if result.get("success"):
                self._limiter.record("comment")
                self._limiter.record_success()
                logger.info(f"[QZone社交] 在{nick}的说说下评论成功: {comment[:30]}")

                # 记录到记忆
                try:
                    from white_salary.adapters.platform.qzone_memory import get_qzone_memory
                    qm = get_qzone_memory()
                    qm.add_comment(nick, feed["content"], comment, tid=tid, owner_uin=target_uin)
                except Exception:
                    pass
            else:
                self._limiter.record_error()
                logger.warning(f"[QZone社交] 评论失败: {result.get('error')}")

        except Exception as e:
            self._limiter.record_error()
            logger.warning(f"[QZone社交] 逛空间异常: {e}")

    async def _generate_comment(
        self, feed_content: str, nick: str, uin: str, thread_context: str = ""
    ) -> str:
        """用ChatAgent生成评论（走完整人设流程，跟QQ聊天一样）。"""
        try:
            if not self._agent:
                return self._fallback_comment(feed_content)

            # 每次评论前清空短期记忆，防止上下文污染
            self._agent._memory.clear()

            # 构造输入，带上已有评论的对话串
            parts = [f"[QQ空间] 你在逛{nick}的QQ空间，看到了这条说说："]
            parts.append(f"「{feed_content[:200]}」")
            if thread_context:
                parts.append(f"\n底下已有的评论：\n{thread_context}")
            parts.append("\n你想在下面评论一句。简短自然地评论就好，10-30字，不要跟别人说重复的话。")
            user_input = "\n".join(parts)

            response = await self._agent.chat(
                user_input, user_name=nick, user_id=uin, is_group=False
            )
            comment = response.strip().strip('"\'')
            # 清理可能的XML标签
            import re
            comment = re.sub(r'<[^>]+>', '', comment).strip()
            if comment and len(comment) <= 100:
                return comment
            return self._fallback_comment(feed_content)

        except Exception as e:
            logger.debug(f"[QZone社交] 评论生成失败: {e}")
            return self._fallback_comment(feed_content)

    def _fallback_comment(self, content: str) -> str:
        """LLM不可用时的简单评论。"""
        import random
        templates = [
            "好棒！", "哈哈哈", "赞！", "写得好～",
            "真不错", "厉害了", "支持！", "好有趣",
            "嘿嘿", "加油鸭", "好耶！",
        ]
        return random.choice(templates)

    # ================================================================
    # @人推荐
    # ================================================================

    def get_at_targets(
        self,
        content: str,
        exclude_uins: set[str] | None = None,
        owner_uin: str = "",
        owner_nick: str = "",
    ) -> list[dict]:
        """发说说时推荐@谁。"""
        exclude = (exclude_uins or set()) | {self._self_uin}
        return self._matcher.get_at_targets(
            content, exclude, owner_uin, owner_nick
        )

    # ================================================================
    # 评论格式化
    # ================================================================

    @staticmethod
    def build_comment(
        content: str,
        at_users: list[dict] | None = None,
    ) -> str:
        """
        格式化评论内容（加@标签）。

        Args:
            content: 评论正文
            at_users: [{"uin": "123", "nick": "小白"}, ...]

        Returns:
            "@小白 评论内容" 或纯内容
        """
        if not at_users:
            return content

        at_parts = []
        for user in at_users:
            nick = user.get("nick") or user.get("uin", "")
            at_parts.append(f"@{nick}")

        at_str = " ".join(at_parts)
        return f"{at_str} {content}"

    # ================================================================
    # 自动发说说
    # ================================================================

    async def auto_post(self, mood: str = "", trigger: str = "random") -> str | None:
        """
        自动发一条说说（由auto_chat或定时触发）。

        用ChatAgent生成内容，走完整人设流程。

        Args:
            mood: 当前心情（可选，注入prompt）
            trigger: 触发原因（random/morning/night/mood）

        Returns:
            发布的内容，或None（限流/失败）
        """
        if not self._limiter.can_do("post"):
            return None

        try:
            from white_salary.adapters.platform.qzone_api import get_client
            client = get_client()
            if not client.is_configured or client.is_cookie_expired:
                return None

            if not self._agent:
                return None

            # 清空短期记忆
            self._agent._memory.clear()

            # 构造prompt让白自己想发什么
            mood_hint = f"你现在心情{mood}，" if mood else ""
            user_input = (
                f"[QQ空间] {mood_hint}你想在QQ空间发一条说说。\n"
                f"写一条自然的、符合你性格的说说内容。\n"
                f"简短就好，像真人发朋友圈一样，10-50字。"
            )

            response = await self._agent.chat(
                user_input, user_name="system", user_id="qzone_auto", is_group=False
            )
            content = response.strip().strip('"\'')
            import re
            content = re.sub(r'<[^>]+>', '', content).strip()

            if not content or len(content) > 200:
                return None

            # 发说说
            result = await client.post_emotion(content)
            if result.get("success"):
                self._limiter.record("post")
                # 记录到记忆
                try:
                    from white_salary.adapters.platform.qzone_memory import get_qzone_memory
                    get_qzone_memory().add_post(content, tid=result.get("tid", ""))
                except Exception:
                    pass
                logger.info(f"[QZone社交] 自动发说说: {content[:30]}")
                return content

        except Exception as e:
            logger.debug(f"[QZone社交] 自动发说说失败: {e}")
        return None

    # ================================================================
    # 频率检查代理
    # ================================================================

    def can_comment(self) -> bool:
        return self._limiter.can_do("comment")

    def can_reply(self) -> bool:
        return self._limiter.can_do("reply")

    def can_post(self) -> bool:
        return self._limiter.can_do("post")

    def record_post(self) -> None:
        self._limiter.record("post")

    def record_comment(self) -> None:
        self._limiter.record("comment")

    def record_reply(self) -> None:
        self._limiter.record("reply")


# 全局单例
_instance: Optional[QzoneSocialManager] = None


def get_social_manager(agent=None) -> QzoneSocialManager:
    """获取QQ空间社交管理器单例。"""
    global _instance
    if _instance is None:
        _instance = QzoneSocialManager(agent=agent)
        # 尝试从qzone_api获取自己的QQ号
        try:
            from white_salary.adapters.platform.qzone_api import get_client
            client = get_client()
            if client.uin:
                _instance.set_self_uin(client.uin)
        except Exception:
            pass
    elif agent and not _instance._agent:
        _instance.set_agent(agent)
    return _instance
