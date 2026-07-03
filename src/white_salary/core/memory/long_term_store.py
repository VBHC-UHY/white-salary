"""
white_salary/core/memory/long_term_store.py

长期记忆存储 — 带过期机制的大容量记忆。

四层过期机制（Mem0风格）：
  - fact:    永久存储（个人事实）
  - event:   365天过期（具体事件经历）
  - emotion: 30天过期（情绪表达和心情）
  - temp:    1天过期（临时请求、短期任务）

存储：SQLite主存储 + JSON备份
检索：关键词匹配 + 时间衰减排序（后续升级为向量语义检索）

参考: WhiteSalary-v2 long_term_store.py (35KB)
"""

import json
import sqlite3
import threading
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

from loguru import logger

# ChromaDB is optional — if not installed, fallback to keyword search
try:
    import chromadb
    CHROMADB_AVAILABLE = True
except ImportError:
    CHROMADB_AVAILABLE = False


# 四层过期配置
MEMORY_LAYERS = {
    "fact": {"expire_days": None, "description": "永久性个人事实"},
    "event": {"expire_days": 365, "description": "具体事件经历（1年过期）"},
    "emotion": {"expire_days": 30, "description": "情绪表达和心情（30天过期）"},
    "temp": {"expire_days": 1, "description": "临时请求（1天过期）"},
}


# ================================================================
# 2026-07-03 面板升级（批6）：长期记忆引擎开关（memory.long_term_provider）
# ================================================================

# 模块级默认引擎：run_server 装配时用 set_default_long_term_provider 注入
# config.memory.long_term_provider；未注入时默认 "chroma" = 原行为
# （装了 chromadb 就用向量检索）。MemoryManager / settings_api 等既有调用方
# 不传 provider 参数，自动跟随这里的默认值，全进程口径一致。
_DEFAULT_PROVIDER: str = "chroma"


def set_default_long_term_provider(provider: str) -> None:
    """
    2026-07-03 面板升级（批6）：注入长期记忆引擎的进程级默认值。

    run_server 装配期调用一次（早于 MemoryManager 创建），之后所有
    不显式传 provider 的 LongTermMemoryStore 构造都用该值——修复
    "对话设置"页 mem-provider 下拉零消费方的问题
    （依据 docs/panel-audit-2026-07-03/panel-chatcfg.json：
    下拉选'未启用'也拦不住Chroma跑）。

    Args:
        provider: "chroma"=启用向量检索（chromadb未安装时自动降级关键词）；
                  "none"=跳过Chroma初始化，只用关键词检索。
                  空串/None 视为 "chroma"（保守回退原行为）
    """
    global _DEFAULT_PROVIDER
    normalized = (provider or "").strip().lower()
    _DEFAULT_PROVIDER = normalized if normalized else "chroma"


def _resolve_provider(provider: Optional[str]) -> str:
    """归一化 provider 参数：None=用模块级默认值；其余小写去空格。"""
    if provider is None:
        return _DEFAULT_PROVIDER
    normalized = provider.strip().lower()
    return normalized if normalized else "chroma"


@dataclass
class LongTermEntry:
    """一条长期记忆。"""
    id: int = 0
    content: str = ""           # 记忆内容
    layer: str = "event"        # 所属层（fact/event/emotion/temp）
    source: str = ""            # 来源会话/场景
    keywords: str = ""          # 关键词（逗号分隔，用于检索）
    importance: int = 5         # 重要程度 1-10
    is_highlight: bool = False  # 是否为精华记忆
    created_at: float = 0.0
    expires_at: float = 0.0     # 过期时间戳（0=永不过期）
    access_count: int = 0


class LongTermMemoryStore:
    """
    长期记忆存储器。

    大容量记忆存储，支持四层过期机制和关键词检索。

    2026-07-03 审计修复（批5）：进程级共享实例（按 data_dir 归一化路径缓存）。
    审计实锤：settings_api 的 GET /memory 每分钟被前端轮询并直接 new 本类，
    每60秒重开一次 ChromaDB（__init__ 日志单日上千次）。改为同一 data_dir
    全进程只保留一个实例。注意：命中缓存时构造参数 max_entries 以首次创建
    为准（生产两处调用均为默认5000，行为不变）。

    2026-07-03 面板升级（批6）：新增 provider 参数（'none'=跳过Chroma只用
    关键词检索；None=跟随模块级默认值，见 set_default_long_term_provider）。
    共享实例缓存键升级为 "路径|引擎"——同一 data_dir 但引擎不同时不复用
    （防止 provider='none' 拿到别处已开好Chroma的实例）。
    """

    # 进程级共享实例注册表：归一化路径|引擎 -> 实例
    _shared_instances: dict[str, "LongTermMemoryStore"] = {}
    _shared_lock: threading.Lock = threading.Lock()

    def __new__(
        cls,
        data_dir: str = "data/memory",
        max_entries: int = 5000,
        provider: Optional[str] = None,
    ) -> "LongTermMemoryStore":
        # 2026-07-03 审计修复（批5）：按归一化路径复用实例，禁止整套重实例化
        # 2026-07-03 面板升级（批6）：缓存键追加引擎名，引擎不同不复用实例
        key = f"{Path(data_dir).resolve()}|{_resolve_provider(provider)}"
        with cls._shared_lock:
            inst = cls._shared_instances.get(key)
            if inst is None:
                inst = super().__new__(cls)
                cls._shared_instances[key] = inst
        return inst

    def __init__(
        self,
        data_dir: str = "data/memory",
        max_entries: int = 5000,
        provider: Optional[str] = None,
    ) -> None:
        # 2026-07-03 审计修复（批5）：命中共享实例时跳过重复初始化
        # （标志在初始化末尾才置位，若上次初始化中途抛异常会自动重试）
        if getattr(self, "_shared_inited", False):
            return

        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)

        self._db_path = self._data_dir / "long_term.db"
        self._json_path = self._data_dir / "long_term.json"
        self._max_entries = max_entries
        # 2026-07-03 面板升级（批6）：记录本实例的引擎（None=跟随模块级默认）
        self._provider: str = _resolve_provider(provider)

        self._init_db()

        # 初始化ChromaDB向量检索（可选）
        # 2026-07-03 面板升级（批6）：provider='none' 时按配置跳过Chroma初始化，
        # 只用关键词检索（SQLite检索路径 search→_keyword_search 天然兼容）
        self._chroma_collection = None
        if self._provider == "none":
            logger.info("[LongTermMemory] 长期记忆引擎=none（按配置跳过ChromaDB，仅关键词检索）")
        elif CHROMADB_AVAILABLE:
            try:
                chroma_path = str(self._data_dir / "chroma_db")
                client = chromadb.PersistentClient(path=chroma_path)
                self._chroma_collection = client.get_or_create_collection(
                    name="long_term_memory",
                    metadata={"hnsw:space": "cosine"},
                )
                logger.info(
                    f"[LongTermMemory] ChromaDB 向量检索已启用 "
                    f"({self._chroma_collection.count()} vectors)"
                )
                # 2026-07-03 审计修复（批5）：启动时对账清理死向量
                # （审计实锤：chroma 286 向量 vs SQLite 175 活记录，39%为已删记录残留）
                self._reconcile_chroma()
            except Exception as e:
                logger.warning(f"[LongTermMemory] ChromaDB 初始化失败，降级为关键词检索: {e}")
        else:
            logger.info("[LongTermMemory] ChromaDB 未安装，使用关键词检索")
        self._shared_inited: bool = True

    def _reconcile_chroma(self) -> int:
        """
        2026-07-03 审计修复（批5）：ChromaDB 与 SQLite 对账。

        历史版本删除路径（delete/_cleanup_expired/_trim_if_needed）只删 SQLite
        不删 Chroma，积累了大量指向已删记录的死向量（检索命中后查不到条目，
        白白挤占 n_results 名额）。启动时把"Chroma 有而 SQLite 没有"的 id
        全部删掉，并日志报告清理数。

        Returns:
            本次清理的死向量数量（异常时返回0，不影响启动）
        """
        if self._chroma_collection is None:
            return 0
        try:
            # Chroma 侧全部 id（当前量级几百条，直接全量取回）
            chroma_ids: list[str] = list(self._chroma_collection.get()["ids"])
            if not chroma_ids:
                return 0
            # SQLite 侧全部活 id
            conn = sqlite3.connect(str(self._db_path))
            rows = conn.execute("SELECT id FROM long_term_memory").fetchall()
            conn.close()
            sqlite_ids = {str(r[0]) for r in rows}
            orphans = [cid for cid in chroma_ids if cid not in sqlite_ids]
            if orphans:
                self._chroma_collection.delete(ids=orphans)
                logger.info(
                    f"[LongTermMemory] ChromaDB 对账: 清理了 {len(orphans)} 条死向量 "
                    f"(对账前 {len(chroma_ids)} 向量 / SQLite {len(sqlite_ids)} 条记录)"
                )
            return len(orphans)
        except Exception as e:
            logger.warning(f"[LongTermMemory] ChromaDB 对账失败（不影响启动）: {e}")
            return 0

    def _chroma_delete_ids(self, ids: list[int]) -> None:
        """
        2026-07-03 审计修复（批5）：删除路径同步清理 Chroma 向量。

        所有 SQLite 删除路径（delete/_cleanup_expired/_trim_if_needed）
        必须调用本方法，否则会再次积累死向量。

        Args:
            ids: 已从 SQLite 删除的记录 id 列表
        """
        if self._chroma_collection is None or not ids:
            return
        try:
            self._chroma_collection.delete(ids=[str(i) for i in ids])
        except Exception as e:
            logger.warning(f"[LongTermMemory] ChromaDB 同步删除失败 (ids={ids[:10]}...): {e}")

    def _init_db(self) -> None:
        """初始化数据库。"""
        conn = sqlite3.connect(str(self._db_path))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS long_term_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                layer TEXT DEFAULT 'event',
                source TEXT DEFAULT '',
                keywords TEXT DEFAULT '',
                importance INTEGER DEFAULT 5,
                is_highlight INTEGER DEFAULT 0,
                created_at REAL DEFAULT 0,
                expires_at REAL DEFAULT 0,
                access_count INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_layer ON long_term_memory(layer)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_keywords ON long_term_memory(keywords)
        """)
        conn.commit()
        conn.close()

    # ================================================================
    # 添加记忆
    # ================================================================

    def add(
        self,
        content: str,
        layer: str = "event",
        source: str = "",
        keywords: str = "",
        importance: int = 5,
        is_highlight: bool = False,
    ) -> int:
        """
        添加一条长期记忆。

        Args:
            content: 记忆内容
            layer: 所属层（fact/event/emotion/temp）
            source: 来源描述
            keywords: 关键词（逗号分隔）
            importance: 重要程度 1-10
            is_highlight: 是否为精华

        Returns:
            新记忆的ID
        """
        now = time.time()

        # 计算过期时间
        layer_config = MEMORY_LAYERS.get(layer, MEMORY_LAYERS["event"])
        expire_days = layer_config["expire_days"]
        expires_at = now + (expire_days * 86400) if expire_days else 0

        conn = sqlite3.connect(str(self._db_path))
        cursor = conn.execute("""
            INSERT INTO long_term_memory
            (content, layer, source, keywords, importance, is_highlight, created_at, expires_at, access_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
        """, (content, layer, source, keywords, importance, int(is_highlight), now, expires_at))
        entry_id = cursor.lastrowid
        conn.commit()
        conn.close()

        # 同步到ChromaDB向量索引
        if self._chroma_collection is not None:
            try:
                self._chroma_collection.add(
                    ids=[str(entry_id)],
                    documents=[content],
                    metadatas=[{
                        "layer": layer,
                        "importance": importance,
                        "is_highlight": int(is_highlight),
                        "created_at": now,
                    }],
                )
            except Exception as e:
                logger.warning(f"[LongTermMemory] ChromaDB 写入失败: {e}")

        logger.debug(
            f"[LongTermMemory] 添加: [{layer}] {content[:50]}... "
            f"(ID={entry_id}, 过期={'永久' if not expire_days else f'{expire_days}天'})"
        )

        # 清理过期记忆
        self._cleanup_expired()

        # 如果超出最大条数，删除最旧的非精华记忆
        self._trim_if_needed()

        return entry_id

    # ================================================================
    # 检索记忆
    # ================================================================

    def search(self, query: str, limit: int = 10) -> list[LongTermEntry]:
        """
        搜索长期记忆。

        优先使用ChromaDB向量语义检索（更精准），
        没有ChromaDB时降级为关键词匹配。

        Args:
            query: 搜索词
            limit: 返回数量上限

        Returns:
            匹配的记忆列表（按相关性排序）
        """
        if not query or not query.strip():
            return []

        # 优先用ChromaDB语义检索
        if self._chroma_collection is not None and self._chroma_collection.count() > 0:
            try:
                results = self._chroma_collection.query(
                    query_texts=[query],
                    n_results=min(limit, self._chroma_collection.count()),
                )
                if results and results["ids"] and results["ids"][0]:
                    entry_ids = [int(id_str) for id_str in results["ids"][0]]
                    return self._get_entries_by_ids(entry_ids)
            except Exception as e:
                logger.warning(f"[LongTermMemory] ChromaDB 检索失败，降级为关键词: {e}")

        # 降级：关键词匹配
        return self._keyword_search(query, limit)

    def _keyword_search(self, query: str, limit: int = 10) -> list[LongTermEntry]:
        """关键词匹配检索（ChromaDB不可用时的降级方案）。"""
        query_tokens = set(query.lower().replace(",", " ").replace("，", " ").split())
        if not query_tokens:
            return []

        conn = sqlite3.connect(str(self._db_path))
        now = time.time()
        rows = conn.execute("""
            SELECT id, content, layer, source, keywords, importance,
                   is_highlight, created_at, expires_at, access_count
            FROM long_term_memory
            WHERE expires_at = 0 OR expires_at > ?
            ORDER BY created_at DESC
            LIMIT 1000
        """, (now,)).fetchall()
        conn.close()

        scored = []
        for row in rows:
            entry = self._row_to_entry(row)
            score = self._calc_relevance(entry, query_tokens)
            if score > 0:
                scored.append((score, entry))

        scored.sort(key=lambda x: x[0], reverse=True)
        results = [entry for _, entry in scored[:limit]]

        if results:
            self._update_access_count([e.id for e in results])

        return results

    def _get_entries_by_ids(self, ids: list[int]) -> list[LongTermEntry]:
        """通过ID列表从SQLite获取完整记忆条目。"""
        if not ids:
            return []
        conn = sqlite3.connect(str(self._db_path))
        placeholders = ",".join("?" * len(ids))
        rows = conn.execute(f"""
            SELECT id, content, layer, source, keywords, importance,
                   is_highlight, created_at, expires_at, access_count
            FROM long_term_memory
            WHERE id IN ({placeholders})
        """, ids).fetchall()
        conn.close()

        self._update_access_count(ids)

        # 保持原始排序（ChromaDB返回的顺序）
        entry_map = {self._row_to_entry(r).id: self._row_to_entry(r) for r in rows}
        return [entry_map[i] for i in ids if i in entry_map]

    def _update_access_count(self, ids: list[int]) -> None:
        """批量更新访问计数。"""
        conn = sqlite3.connect(str(self._db_path))
        for entry_id in ids:
            conn.execute(
                "UPDATE long_term_memory SET access_count = access_count + 1 WHERE id = ?",
                (entry_id,)
            )
        conn.commit()
        conn.close()

    def get_highlights(self, limit: int = 20) -> list[LongTermEntry]:
        """获取精华记忆。"""
        conn = sqlite3.connect(str(self._db_path))
        rows = conn.execute("""
            SELECT id, content, layer, source, keywords, importance,
                   is_highlight, created_at, expires_at, access_count
            FROM long_term_memory
            WHERE is_highlight = 1
            ORDER BY importance DESC, created_at DESC
            LIMIT ?
        """, (limit,)).fetchall()
        conn.close()
        return [self._row_to_entry(r) for r in rows]

    def get_recent(self, limit: int = 20) -> list[LongTermEntry]:
        """获取最近的记忆。"""
        conn = sqlite3.connect(str(self._db_path))
        now = time.time()
        rows = conn.execute("""
            SELECT id, content, layer, source, keywords, importance,
                   is_highlight, created_at, expires_at, access_count
            FROM long_term_memory
            WHERE expires_at = 0 OR expires_at > ?
            ORDER BY created_at DESC
            LIMIT ?
        """, (now, limit)).fetchall()
        conn.close()
        return [self._row_to_entry(r) for r in rows]

    def get_by_layer(self, layer: str, limit: int = 50) -> list[LongTermEntry]:
        """按层获取记忆。"""
        conn = sqlite3.connect(str(self._db_path))
        now = time.time()
        rows = conn.execute("""
            SELECT id, content, layer, source, keywords, importance,
                   is_highlight, created_at, expires_at, access_count
            FROM long_term_memory
            WHERE layer = ? AND (expires_at = 0 OR expires_at > ?)
            ORDER BY importance DESC, created_at DESC
            LIMIT ?
        """, (layer, now, limit)).fetchall()
        conn.close()
        return [self._row_to_entry(r) for r in rows]

    # ================================================================
    # 删除和清理
    # ================================================================

    def delete(self, entry_id: int) -> bool:
        """删除一条记忆。"""
        conn = sqlite3.connect(str(self._db_path))
        cursor = conn.execute("DELETE FROM long_term_memory WHERE id = ?", (entry_id,))
        conn.commit()
        conn.close()
        deleted = cursor.rowcount > 0
        # 2026-07-03 审计修复（批5）：同步删除 Chroma 向量，避免死向量积累
        if deleted:
            self._chroma_delete_ids([entry_id])
        return deleted

    def _cleanup_expired(self) -> None:
        """清理所有过期的记忆。"""
        conn = sqlite3.connect(str(self._db_path))
        now = time.time()
        # 2026-07-03 审计修复（批5）：先查出待删 id，删完 SQLite 后同步删 Chroma 向量
        expired_ids: list[int] = [
            row[0] for row in conn.execute(
                "SELECT id FROM long_term_memory WHERE expires_at > 0 AND expires_at < ?",
                (now,)
            ).fetchall()
        ]
        if expired_ids:
            placeholders = ",".join("?" * len(expired_ids))
            conn.execute(
                f"DELETE FROM long_term_memory WHERE id IN ({placeholders})",
                expired_ids,
            )
            logger.debug(f"[LongTermMemory] 清理了 {len(expired_ids)} 条过期记忆")
        conn.commit()
        conn.close()
        if expired_ids:
            self._chroma_delete_ids(expired_ids)

    def _trim_if_needed(self) -> None:
        """如果超出最大条数，删除最旧的非精华记忆。"""
        conn = sqlite3.connect(str(self._db_path))
        count = conn.execute("SELECT COUNT(*) FROM long_term_memory").fetchone()[0]
        trimmed_ids: list[int] = []
        if count > self._max_entries:
            excess = count - self._max_entries
            # 2026-07-03 审计修复（批5）：先查出待删 id，删完 SQLite 后同步删 Chroma 向量
            trimmed_ids = [
                row[0] for row in conn.execute("""
                    SELECT id FROM long_term_memory
                    WHERE is_highlight = 0
                    ORDER BY importance ASC, created_at ASC
                    LIMIT ?
                """, (excess,)).fetchall()
            ]
            if trimmed_ids:
                placeholders = ",".join("?" * len(trimmed_ids))
                conn.execute(
                    f"DELETE FROM long_term_memory WHERE id IN ({placeholders})",
                    trimmed_ids,
                )
                conn.commit()
                logger.debug(f"[LongTermMemory] 容量裁剪: 删除了 {len(trimmed_ids)} 条低重要性记忆")
        conn.close()
        if trimmed_ids:
            self._chroma_delete_ids(trimmed_ids)

    # ================================================================
    # 上下文注入
    # ================================================================

    def get_context_string(self, query: str = "", limit: int = 10) -> str:
        """
        生成注入LLM上下文的长期记忆摘要。

        如果有query，返回与query最相关的记忆；
        否则返回精华记忆和最近记忆。
        """
        entries = []

        if query:
            entries = self.search(query, limit=limit)
        else:
            entries = self.get_highlights(limit=5) + self.get_recent(limit=5)

        if not entries:
            return ""

        lines = ["[长期记忆 — 过往对话中的重要信息]"]
        for entry in entries:
            layer_name = MEMORY_LAYERS.get(entry.layer, {}).get("description", entry.layer)
            highlight = " ★" if entry.is_highlight else ""
            lines.append(f"  [{layer_name}]{highlight} {entry.content}")

        return "\n".join(lines)

    # ================================================================
    # 统计
    # ================================================================

    @property
    def count(self) -> int:
        conn = sqlite3.connect(str(self._db_path))
        c = conn.execute("SELECT COUNT(*) FROM long_term_memory").fetchone()[0]
        conn.close()
        return c

    def get_stats(self) -> dict:
        conn = sqlite3.connect(str(self._db_path))
        total = conn.execute("SELECT COUNT(*) FROM long_term_memory").fetchone()[0]
        by_layer = {}
        for layer in MEMORY_LAYERS:
            c = conn.execute(
                "SELECT COUNT(*) FROM long_term_memory WHERE layer = ?", (layer,)
            ).fetchone()[0]
            if c > 0:
                by_layer[layer] = c
        highlights = conn.execute(
            "SELECT COUNT(*) FROM long_term_memory WHERE is_highlight = 1"
        ).fetchone()[0]
        conn.close()
        return {"total": total, "by_layer": by_layer, "highlights": highlights}

    # ================================================================
    # 内部工具
    # ================================================================

    def _row_to_entry(self, row: tuple) -> LongTermEntry:
        return LongTermEntry(
            id=row[0], content=row[1], layer=row[2], source=row[3],
            keywords=row[4], importance=row[5], is_highlight=bool(row[6]),
            created_at=row[7], expires_at=row[8], access_count=row[9],
        )

    def _calc_relevance(self, entry: LongTermEntry, query_tokens: set[str]) -> float:
        """计算记忆与查询的相关性得分。"""
        score = 0.0

        # 内容匹配
        content_lower = entry.content.lower()
        for token in query_tokens:
            if token in content_lower:
                score += 2.0

        # 关键词匹配（权重更高）
        keywords_lower = entry.keywords.lower()
        for token in query_tokens:
            if token in keywords_lower:
                score += 3.0

        # 重要程度加成
        score *= (entry.importance / 5.0)

        # 精华加成
        if entry.is_highlight:
            score *= 1.5

        # 时间衰减（越旧的记忆得分越低）
        age_days = (time.time() - entry.created_at) / 86400
        time_factor = max(0.3, 1.0 - age_days / 365)
        score *= time_factor

        return score
