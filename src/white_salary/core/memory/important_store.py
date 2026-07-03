"""
white_salary/core/memory/important_store.py

重要记忆存储 — 介于核心记忆和长期记忆之间的中间层。

特点：
  - 比核心记忆更灵活（可配置过期时间）
  - 比长期记忆更重要（不会被容量裁剪清理）
  - 存储用户的重要承诺、约定、特殊请求
  - 支持去重和冲突解决

存储：SQLite + JSON双写
参考: WhiteSalary-v2 important_store.py (13KB)
"""

import json
import sqlite3
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

from loguru import logger


@dataclass
class ImportantEntry:
    """一条重要记忆。"""
    id: int = 0
    content: str = ""
    category: str = "promise"   # promise/request/event/warning
    source: str = "auto"        # auto/manual/llm
    importance: int = 7
    expires_at: float = 0.0     # 0=永不过期
    created_at: float = 0.0
    updated_at: float = 0.0


# 重要记忆触发关键词
IMPORTANT_KEYWORDS = [
    "答应我", "你答应", "别忘了", "一定要", "千万别",
    "约好了", "说好了", "保证", "承诺", "拜托",
    "记住这个", "这很重要", "帮我记一下",
    "答應我", "你答應", "別忘了", "一定要", "千萬別",
    "promise", "don't forget", "remember this",
]


class ImportantMemoryStore:
    """
    重要记忆存储器。

    管理用户的重要承诺、约定和特殊请求。
    支持去重和冲突检测。
    """

    def __init__(self, data_dir: str = "data/memory", default_expire_days: int = 90) -> None:
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = self._data_dir / "important.db"
        self._json_path = self._data_dir / "important.json"
        self._default_expire_days = default_expire_days
        self._init_db()

    def _init_db(self) -> None:
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS important_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                category TEXT DEFAULT 'promise',
                source TEXT DEFAULT 'auto',
                importance INTEGER DEFAULT 7,
                expires_at REAL DEFAULT 0,
                created_at REAL DEFAULT 0,
                updated_at REAL DEFAULT 0
            )
        """)
        conn.commit()
        conn.close()

    # ================================================================
    # 添加（带去重）
    # ================================================================

    def add(
        self,
        content: str,
        category: str = "promise",
        source: str = "auto",
        importance: int = 7,
        expire_days: int | None = None,
    ) -> int:
        """
        添加一条重要记忆（自动去重）。

        如果已存在相似内容，更新而不是新增。

        Returns: 记忆ID
        """
        # 去重检查
        existing = self._find_similar(content)
        if existing:
            # 更新已有记忆
            self._update(existing.id, content, importance)
            logger.debug(f"[Important] 更新(去重): {content[:40]}")
            return existing.id

        now = time.time()
        days = expire_days if expire_days is not None else self._default_expire_days
        expires_at = now + (days * 86400) if days > 0 else 0

        conn = sqlite3.connect(str(self._db_path))
        cursor = conn.execute("""
            INSERT INTO important_memory (content, category, source, importance,
                                          expires_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (content, category, source, importance, expires_at, now, now))
        entry_id = cursor.lastrowid
        conn.commit()
        conn.close()

        self._save_json()
        logger.debug(f"[Important] 新增: [{category}] {content[:40]}")
        return entry_id

    def _find_similar(self, content: str, threshold: float = 0.8) -> Optional[ImportantEntry]:
        """
        查找相似记忆（简单的字符重叠检测）。

        如果两条记忆的关键词重叠度超过阈值，认为是重复。
        """
        content_tokens = set(content)
        entries = self.get_all()

        for entry in entries:
            entry_tokens = set(entry.content)
            if not content_tokens or not entry_tokens:
                continue
            overlap = len(content_tokens & entry_tokens) / max(len(content_tokens), len(entry_tokens))
            if overlap > threshold:
                return entry

        return None

    def _update(self, entry_id: int, content: str, importance: int) -> None:
        """更新已有记忆。"""
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("""
            UPDATE important_memory SET content = ?, importance = ?, updated_at = ?
            WHERE id = ?
        """, (content, importance, time.time(), entry_id))
        conn.commit()
        conn.close()
        self._save_json()

    # ================================================================
    # 冲突解决
    # ================================================================

    def resolve_conflict(self, key_topic: str, new_info: str, old_info: str) -> str:
        """
        解决记忆冲突（新信息和旧信息矛盾时）。

        策略：保留新信息，标记旧信息为已更新。

        Args:
            key_topic: 冲突主题（如"用户年龄"）
            new_info: 新信息
            old_info: 旧信息

        Returns:
            解决方案描述
        """
        # 简单策略：新信息覆盖旧信息，记录变更历史
        self.add(
            content=f"[更正] {key_topic}: {old_info} → {new_info}",
            category="warning",
            source="conflict_resolution",
            importance=8,
        )
        logger.info(f"[Important] 冲突解决: {key_topic}: {old_info} → {new_info}")
        return f"已更新: {key_topic} 从 '{old_info}' 改为 '{new_info}'"

    # ================================================================
    # 查询
    # ================================================================

    def get_all(self) -> list[ImportantEntry]:
        """获取所有未过期的重要记忆。"""
        conn = sqlite3.connect(str(self._db_path))
        now = time.time()
        rows = conn.execute("""
            SELECT id, content, category, source, importance, expires_at, created_at, updated_at
            FROM important_memory
            WHERE expires_at = 0 OR expires_at > ?
            ORDER BY importance DESC, created_at DESC
        """, (now,)).fetchall()
        conn.close()
        return [self._row_to_entry(r) for r in rows]

    def search(self, query: str) -> list[ImportantEntry]:
        """搜索重要记忆。"""
        entries = self.get_all()
        query_lower = query.lower()
        return [e for e in entries if query_lower in e.content.lower()]

    def delete(self, entry_id: int) -> bool:
        conn = sqlite3.connect(str(self._db_path))
        cursor = conn.execute("DELETE FROM important_memory WHERE id = ?", (entry_id,))
        conn.commit()
        conn.close()
        self._save_json()
        return cursor.rowcount > 0

    # ================================================================
    # 关键词触发检测
    # ================================================================

    def check_and_store(self, text: str) -> list[str]:
        """
        检查文本中是否包含重要记忆触发关键词。

        Returns: 提取结果列表
        """
        results = []
        for keyword in IMPORTANT_KEYWORDS:
            if keyword in text:
                self.add(
                    content=text[:120],
                    category="promise" if keyword in ("答应", "保证", "承诺") else "request",
                    source="keyword_trigger",
                    importance=8,
                )
                results.append(f"重要:{keyword}触发")
                break
        return results

    # ================================================================
    # 上下文注入
    # ================================================================

    def get_context_string(self) -> str:
        entries = self.get_all()
        if not entries:
            return ""

        lines = ["[重要记忆 — 用户的承诺/约定/特殊请求]"]
        for e in entries[:10]:
            lines.append(f"  [{e.category}] {e.content}")
        return "\n".join(lines)

    # ================================================================
    # 持久化
    # ================================================================

    @property
    def count(self) -> int:
        return len(self.get_all())

    def _save_json(self) -> None:
        entries = self.get_all()
        data = [asdict(e) for e in entries]
        with open(self._json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _row_to_entry(self, row: tuple) -> ImportantEntry:
        return ImportantEntry(
            id=row[0], content=row[1], category=row[2], source=row[3],
            importance=row[4], expires_at=row[5], created_at=row[6], updated_at=row[7],
        )
