"""
white_salary/core/memory/core_store.py

核心记忆存储 — 永久性事实记忆（最高级别，永不过期）。

存储内容：
  - 用户基本信息（名字、生日、年龄、职业）
  - 重要人物关系（家人、朋友、同事）
  - 用户喜好和讨厌的东西
  - 用户的重要经历和里程碑
  - AI自身的学习记录（用户教的规则）

存储方式：SQLite + JSON + TXT 三写（最高数据安全级别）
- SQLite: 主存储，支持高效查询
- JSON: 结构化备份，便于程序读取
- TXT: 人类可读备份，便于调试和查看

参考: WhiteSalary-v2 core_store.py (44KB)
"""

import json
import sqlite3
import threading
import time
import yaml
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from loguru import logger


@dataclass
class CoreMemoryEntry:
    """一条核心记忆。"""
    key: str                    # 记忆键名（如 "user_name", "like_cats"）
    value: str                  # 记忆内容
    category: str = "other"     # 分类：basic_info / preference / relationship / rule / milestone
    source: str = "inferred"    # 来源：user_said / inferred / manual / system
    importance: int = 5         # 重要程度 1-10（10最重要）
    tags: list[str] = field(default_factory=list)  # 标签
    created_at: float = 0.0
    updated_at: float = 0.0
    access_count: int = 0       # 被检索次数（衡量记忆活跃度）


# 核心记忆分类定义
CATEGORIES = {
    "basic_info": "基本信息（姓名、年龄、生日、职业等）",
    "preference": "喜好偏好（喜欢/讨厌的食物、颜色、音乐等）",
    "relationship": "人际关系（家人、朋友、同事等重要人物）",
    "rule": "互动规则（用户教给AI的行为准则）",
    "milestone": "重要事件（纪念日、成就、转折点）",
    "habit": "生活习惯（作息、日常活动）",
    "other": "其他",
}


class CoreMemoryStore:
    """
    核心记忆存储器。

    管理永久性事实信息，支持增删改查。
    三写机制确保数据安全（SQLite + JSON + TXT）。

    2026-07-03 审计修复（批5）：进程级共享实例（按 data_dir 归一化路径缓存）。
    审计实锤：settings_api 的 GET /memory 每分钟被前端轮询，函数体内直接
    new 本类，导致 _load_cache 日志每60秒成组刷屏、重复打开SQLite浪费I/O，
    且与主 Agent 并发读写同一存储存在竞争风险。改为同一 data_dir 全进程
    只保留一个实例：CoreMemoryStore(data_dir=X) 第二次调用返回同一对象。
    """

    # 进程级共享实例注册表：归一化路径 -> 实例
    _shared_instances: dict[str, "CoreMemoryStore"] = {}
    _shared_lock: threading.Lock = threading.Lock()

    def __new__(cls, data_dir: str = "data/memory") -> "CoreMemoryStore":
        # 2026-07-03 审计修复（批5）：按归一化路径复用实例，禁止整套重实例化
        key = str(Path(data_dir).resolve())
        with cls._shared_lock:
            inst = cls._shared_instances.get(key)
            if inst is None:
                inst = super().__new__(cls)
                cls._shared_instances[key] = inst
        return inst

    def __init__(self, data_dir: str = "data/memory") -> None:
        # 2026-07-03 审计修复（批5）：命中共享实例时跳过重复初始化
        # （标志在初始化末尾才置位，若上次初始化中途抛异常会自动重试）
        if getattr(self, "_shared_inited", False):
            return

        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)

        self._db_path = self._data_dir / "core.db"
        self._json_path = self._data_dir / "core.json"
        self._txt_path = self._data_dir / "core.txt"

        self._init_db()
        self._cache: dict[str, CoreMemoryEntry] = {}
        self._load_cache()
        self._shared_inited: bool = True

    def _init_db(self) -> None:
        """初始化SQLite数据库表结构。"""
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS core_memory (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                category TEXT DEFAULT 'other',
                source TEXT DEFAULT 'inferred',
                importance INTEGER DEFAULT 5,
                tags TEXT DEFAULT '[]',
                created_at REAL DEFAULT 0,
                updated_at REAL DEFAULT 0,
                access_count INTEGER DEFAULT 0
            )
        """)
        conn.commit()
        conn.close()

    def _load_cache(self) -> None:
        """从SQLite加载所有核心记忆到内存缓存。"""
        conn = sqlite3.connect(str(self._db_path))
        rows = conn.execute(
            "SELECT key, value, category, source, importance, tags, "
            "created_at, updated_at, access_count FROM core_memory"
        ).fetchall()
        conn.close()

        for row in rows:
            tags = json.loads(row[5]) if row[5] else []
            self._cache[row[0]] = CoreMemoryEntry(
                key=row[0], value=row[1], category=row[2],
                source=row[3], importance=row[4], tags=tags,
                created_at=row[6], updated_at=row[7], access_count=row[8],
            )

        logger.debug(f"[CoreMemory] 加载了 {len(self._cache)} 条核心记忆")

    # ================================================================
    # CRUD 操作
    # ================================================================

    def set(
        self,
        key: str,
        value: str,
        category: str = "other",
        source: str = "inferred",
        importance: int = 5,
        tags: list[str] | None = None,
    ) -> bool:
        """
        设置一条核心记忆（新增或更新）。

        Args:
            key: 记忆键名（建议用下划线命名，如 user_name）
            value: 记忆内容
            category: 分类（basic_info/preference/relationship/rule/milestone/habit/other）
            source: 来源（user_said/inferred/manual/system）
            importance: 重要程度 1-10
            tags: 标签列表

        Returns:
            True=新增, False=更新
        """
        now = time.time()
        existing = self._cache.get(key)
        is_new = existing is None

        entry = CoreMemoryEntry(
            key=key,
            value=value,
            category=category,
            source=source,
            importance=max(1, min(10, importance)),
            tags=tags or (existing.tags if existing else []),
            created_at=existing.created_at if existing else now,
            updated_at=now,
            access_count=existing.access_count if existing else 0,
        )

        # 写入SQLite
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("""
            INSERT OR REPLACE INTO core_memory
            (key, value, category, source, importance, tags, created_at, updated_at, access_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            key, value, category, source, entry.importance,
            json.dumps(entry.tags, ensure_ascii=False),
            entry.created_at, entry.updated_at, entry.access_count,
        ))
        conn.commit()
        conn.close()

        # 更新内存缓存
        self._cache[key] = entry

        # 三写：同步JSON和TXT
        self._save_json()
        self._save_txt()

        action = "新增" if is_new else "更新"
        logger.debug(f"[CoreMemory] {action}: [{category}] {key} = {value[:60]}")
        return is_new

    def get(self, key: str) -> Optional[str]:
        """获取一条核心记忆的值（同时增加访问计数）。"""
        entry = self._cache.get(key)
        if entry:
            entry.access_count += 1
            return entry.value
        return None

    def get_entry(self, key: str) -> Optional[CoreMemoryEntry]:
        """获取一条核心记忆的完整信息。"""
        return self._cache.get(key)

    def delete(self, key: str) -> bool:
        """删除一条核心记忆。"""
        if key not in self._cache:
            return False

        conn = sqlite3.connect(str(self._db_path))
        conn.execute("DELETE FROM core_memory WHERE key = ?", (key,))
        conn.commit()
        conn.close()

        del self._cache[key]
        self._save_json()
        self._save_txt()

        logger.debug(f"[CoreMemory] 删除: {key}")
        return True

    def search(self, query: str) -> list[CoreMemoryEntry]:
        """
        搜索核心记忆（关键词匹配）。

        在key、value、tags中搜索包含query的记忆。
        """
        query_lower = query.lower()
        results = []
        for entry in self._cache.values():
            if (query_lower in entry.key.lower()
                    or query_lower in entry.value.lower()
                    or any(query_lower in t.lower() for t in entry.tags)):
                results.append(entry)
        return sorted(results, key=lambda e: e.importance, reverse=True)

    # ================================================================
    # 批量查询
    # ================================================================

    def get_all(self) -> list[CoreMemoryEntry]:
        """获取所有核心记忆（按重要程度排序）。"""
        return sorted(self._cache.values(), key=lambda e: e.importance, reverse=True)

    def get_by_category(self, category: str) -> list[CoreMemoryEntry]:
        """按分类获取核心记忆。"""
        return [e for e in self._cache.values() if e.category == category]

    def get_most_important(self, limit: int = 10) -> list[CoreMemoryEntry]:
        """获取最重要的N条记忆。"""
        return sorted(self._cache.values(), key=lambda e: e.importance, reverse=True)[:limit]

    def get_recently_updated(self, limit: int = 10) -> list[CoreMemoryEntry]:
        """获取最近更新的N条记忆。"""
        return sorted(self._cache.values(), key=lambda e: e.updated_at, reverse=True)[:limit]

    # ================================================================
    # 上下文注入
    # ================================================================

    def get_context_string(self) -> str:
        """
        生成注入LLM上下文的核心记忆摘要。

        按分类组织，重要的排前面。

        示例输出:
          [核心记忆 — 关于用户的永久信息]
          【基本信息】
            用户名字: 小白
            用户年龄: 21岁
          【喜好偏好】
            喜欢水果蛋糕
            讨厌辣的食物
          【重要人物】
            家人: chowmanbun（创造者）
        """
        if not self._cache:
            return ""

        lines = ["[核心记忆 — 关于用户的永久信息]"]

        # 按分类分组
        by_category: dict[str, list[CoreMemoryEntry]] = {}
        for entry in self._cache.values():
            by_category.setdefault(entry.category, []).append(entry)

        # 按分类顺序输出
        category_order = ["basic_info", "preference", "relationship", "rule", "milestone", "habit", "other"]
        category_names = {
            "basic_info": "基本信息", "preference": "喜好偏好",
            "relationship": "重要人物", "rule": "互动规则",
            "milestone": "重要事件", "habit": "生活习惯", "other": "其他",
        }

        for cat in category_order:
            entries = by_category.get(cat, [])
            if not entries:
                continue
            lines.append(f"  【{category_names.get(cat, cat)}】")
            for entry in sorted(entries, key=lambda e: e.importance, reverse=True):
                lines.append(f"    {entry.key}: {entry.value}")

        return "\n".join(lines)

    # ================================================================
    # 统计
    # ================================================================

    @property
    def count(self) -> int:
        return len(self._cache)

    def get_stats(self) -> dict:
        """获取统计信息。"""
        by_cat = {}
        for e in self._cache.values():
            by_cat[e.category] = by_cat.get(e.category, 0) + 1
        return {
            "total": len(self._cache),
            "by_category": by_cat,
            "most_accessed": sorted(
                self._cache.values(),
                key=lambda e: e.access_count,
                reverse=True
            )[:5] if self._cache else [],
        }

    # ================================================================
    # 三写持久化
    # ================================================================

    def _save_json(self) -> None:
        """写入JSON备份。"""
        data = {k: asdict(v) for k, v in self._cache.items()}
        with open(self._json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def _save_txt(self) -> None:
        """写入TXT人类可读备份。"""
        lines = [
            "=" * 60,
            "  White Salary 核心记忆",
            f"  总计: {len(self._cache)} 条",
            f"  更新时间: {time.strftime('%Y-%m-%d %H:%M:%S')}",
            "=" * 60,
            "",
        ]

        category_names = {
            "basic_info": "基本信息", "preference": "喜好偏好",
            "relationship": "重要人物", "rule": "互动规则",
            "milestone": "重要事件", "habit": "生活习惯", "other": "其他",
        }

        by_category: dict[str, list[CoreMemoryEntry]] = {}
        for entry in self._cache.values():
            by_category.setdefault(entry.category, []).append(entry)

        for cat in ["basic_info", "preference", "relationship", "rule", "milestone", "habit", "other"]:
            entries = by_category.get(cat, [])
            if not entries:
                continue

            lines.append(f"【{category_names.get(cat, cat)}】")
            for e in sorted(entries, key=lambda x: x.importance, reverse=True):
                src = f" (来源:{e.source})" if e.source != "inferred" else ""
                lines.append(f"  [{e.importance}★] {e.key}: {e.value}{src}")
            lines.append("")

        with open(self._txt_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
