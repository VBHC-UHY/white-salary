"""
white_salary/core/memory/conversation_log.py

跨平台统一对话日志 — 记录所有平台（桌面/QQ）的对话，支持关键词检索。

功能：
  - 所有平台的消息统一写入SQLite
  - 按关键词/用户/平台/时间范围检索
  - 供 recall_conversation 工具调用
  - 不影响正常对话的token消耗（只在需要时查询）

表结构：
  conversation_log (
      id          INTEGER PRIMARY KEY,
      timestamp   REAL,           -- Unix时间戳
      platform    TEXT,           -- 'desktop' / 'qq'
      user_name   TEXT,           -- 发送者名字
      user_id     TEXT,           -- 用户ID（QQ号等）
      group_id    TEXT,           -- 群号（私聊为空）
      user_msg    TEXT,           -- 用户消息
      ai_reply    TEXT,           -- AI回复
  )
"""

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from loguru import logger


@dataclass
class ConversationEntry:
    """一条对话记录。"""
    id: int
    timestamp: float
    platform: str
    user_name: str
    user_id: str
    group_id: str
    user_msg: str
    ai_reply: str

    @property
    def time_str(self) -> str:
        """格式化时间显示。"""
        import datetime
        dt = datetime.datetime.fromtimestamp(self.timestamp)
        return dt.strftime("%m-%d %H:%M")

    @property
    def platform_label(self) -> str:
        if self.platform == "qq":
            return f"QQ{'群' + self.group_id if self.group_id else '私聊'}"
        return "桌面"


class ConversationLog:
    """
    跨平台统一对话日志。

    使用方式:
        log = ConversationLog("data/memory")
        log.record("qq", "小白", "1234567890", "群号", "你好", "你好呀～")
        results = log.search("你好")
    """

    _instance: Optional["ConversationLog"] = None

    def __init__(self, data_dir: str = "data/memory") -> None:
        db_path = Path(data_dir) / "conversation_log.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = str(db_path)
        self._init_db()

    @classmethod
    def get_instance(cls, data_dir: str = "data/memory") -> "ConversationLog":
        """单例获取（全局共用一个实例）。"""
        if cls._instance is None:
            cls._instance = cls(data_dir=data_dir)
        return cls._instance

    def _init_db(self) -> None:
        """初始化数据库表。"""
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS conversation_log (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp   REAL NOT NULL,
                    platform    TEXT NOT NULL,
                    user_name   TEXT NOT NULL DEFAULT '',
                    user_id     TEXT NOT NULL DEFAULT '',
                    group_id    TEXT NOT NULL DEFAULT '',
                    user_msg    TEXT NOT NULL,
                    ai_reply    TEXT NOT NULL DEFAULT ''
                )
            """)
            # 建索引加速检索
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_log_timestamp
                ON conversation_log(timestamp)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_log_platform
                ON conversation_log(platform)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_log_user_id
                ON conversation_log(user_id)
            """)
        logger.debug(f"[ConvLog] 数据库就绪: {self._db_path}")

    def record(
        self,
        platform: str,
        user_name: str,
        user_id: str,
        group_id: str,
        user_msg: str,
        ai_reply: str,
    ) -> None:
        """
        记录一条对话。

        Args:
            platform: 平台标识 ('desktop' / 'qq')
            user_name: 用户名/昵称
            user_id: 用户唯一ID
            group_id: 群ID（私聊传空字符串）
            user_msg: 用户发送的消息
            ai_reply: AI的回复
        """
        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute(
                    """INSERT INTO conversation_log
                       (timestamp, platform, user_name, user_id, group_id, user_msg, ai_reply)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (time.time(), platform, user_name, user_id, group_id, user_msg, ai_reply),
                )
        except Exception as e:
            logger.warning(f"[ConvLog] 写入失败: {e}")

    def search(
        self,
        keyword: str = "",
        platform: str = "",
        user_name: str = "",
        limit: int = 15,
        days: int = 30,
    ) -> list[ConversationEntry]:
        """
        检索对话记录。

        Args:
            keyword: 关键词（搜索用户消息和AI回复）
            platform: 过滤平台（'desktop'/'qq'，空=全部）
            user_name: 过滤用户名
            limit: 最多返回条数
            days: 搜索最近多少天

        Returns:
            匹配的对话记录列表（按时间倒序）
        """
        conditions = []
        params: list = []

        # 时间范围
        cutoff = time.time() - days * 86400
        conditions.append("timestamp > ?")
        params.append(cutoff)

        # 关键词匹配
        if keyword:
            conditions.append("(user_msg LIKE ? OR ai_reply LIKE ?)")
            params.extend([f"%{keyword}%", f"%{keyword}%"])

        # 平台过滤
        if platform:
            conditions.append("platform = ?")
            params.append(platform)

        # 用户名过滤
        if user_name:
            conditions.append("user_name LIKE ?")
            params.append(f"%{user_name}%")

        where = " AND ".join(conditions)
        sql = f"""
            SELECT id, timestamp, platform, user_name, user_id, group_id, user_msg, ai_reply
            FROM conversation_log
            WHERE {where}
            ORDER BY timestamp DESC
            LIMIT ?
        """
        params.append(limit)

        try:
            with sqlite3.connect(self._db_path) as conn:
                rows = conn.execute(sql, params).fetchall()
            return [
                ConversationEntry(
                    id=r[0], timestamp=r[1], platform=r[2], user_name=r[3],
                    user_id=r[4], group_id=r[5], user_msg=r[6], ai_reply=r[7],
                )
                for r in rows
            ]
        except Exception as e:
            logger.warning(f"[ConvLog] 检索失败: {e}")
            return []

    def format_results(self, entries: list[ConversationEntry]) -> str:
        """把检索结果格式化为LLM可读的文本。"""
        if not entries:
            return "没有找到相关的对话记录。"

        lines = [f"找到 {len(entries)} 条相关对话记录（按时间倒序）：\n"]
        # 倒序变正序显示（时间从早到晚）
        for e in reversed(entries):
            lines.append(f"[{e.time_str}] [{e.platform_label}] {e.user_name}: {e.user_msg}")
            if e.ai_reply:
                # AI回复截断到150字，避免太长
                reply = e.ai_reply[:150] + ("..." if len(e.ai_reply) > 150 else "")
                lines.append(f"  → 白: {reply}")
            lines.append("")
        return "\n".join(lines)

    @property
    def total_count(self) -> int:
        """总记录数。"""
        try:
            with sqlite3.connect(self._db_path) as conn:
                row = conn.execute("SELECT COUNT(*) FROM conversation_log").fetchone()
                return row[0] if row else 0
        except Exception:
            return 0

    # ================================================================
    # 实时索引（realtime_group_memory + realtime_private_memory）
    # ================================================================

    def get_recent_by_user(self, user_id: str, limit: int = 10) -> list[ConversationEntry]:
        """获取某个用户的最近对话（跨平台）。"""
        return self.search(user_name="", platform="", limit=limit)

    def get_recent_by_group(self, group_id: str, limit: int = 10) -> list[ConversationEntry]:
        """获取某个群的最近对话。"""
        try:
            with sqlite3.connect(self._db_path) as conn:
                rows = conn.execute(
                    "SELECT id, timestamp, platform, user_name, user_id, group_id, user_msg, ai_reply "
                    "FROM conversation_log WHERE group_id = ? ORDER BY timestamp DESC LIMIT ?",
                    (group_id, limit),
                ).fetchall()
            return [ConversationEntry(*r) for r in rows]
        except Exception:
            return []

    def get_user_stats(self, user_id: str) -> dict:
        """获取用户的对话统计。"""
        try:
            with sqlite3.connect(self._db_path) as conn:
                row = conn.execute(
                    "SELECT COUNT(*), MIN(timestamp), MAX(timestamp) "
                    "FROM conversation_log WHERE user_id = ?", (user_id,)
                ).fetchone()
                if row and row[0]:
                    return {
                        "total_messages": row[0],
                        "first_chat": row[1],
                        "last_chat": row[2],
                    }
        except Exception:
            pass
        return {"total_messages": 0}

    def get_active_users(self, days: int = 7, limit: int = 20) -> list[dict]:
        """获取最近活跃的用户列表。"""
        cutoff = time.time() - days * 86400
        try:
            with sqlite3.connect(self._db_path) as conn:
                rows = conn.execute(
                    "SELECT user_id, user_name, COUNT(*) as cnt, MAX(timestamp) as last "
                    "FROM conversation_log WHERE timestamp > ? "
                    "GROUP BY user_id ORDER BY cnt DESC LIMIT ?",
                    (cutoff, limit),
                ).fetchall()
            return [{"user_id": r[0], "user_name": r[1], "count": r[2], "last": r[3]} for r in rows]
        except Exception:
            return []
