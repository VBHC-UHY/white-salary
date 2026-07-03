"""
white_salary/core/memory/multi_index.py

多索引系统 — 关键词+时间+人物+分类+情感 五维索引。

借鉴v2的multi_index.py：
  - 五维索引结构，O(1)级别检索
  - 新记忆自动入索引
  - 多维联合查询（AND/OR组合条件）
  - JSON持久化，增量更新不全量重建
  - 时间范围查询、重要度过滤

自动发现：导出MODULE供MemoryManager加载。
"""

import json
import re
import time
from collections import defaultdict
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional

from loguru import logger
from white_salary.core.memory.module_base import MemoryModule


@dataclass
class IndexEntry:
    """索引条目 — 一条记忆的元数据。"""
    entry_id: str = ""
    content: str = ""
    keywords: list[str] = field(default_factory=list)
    people: list[str] = field(default_factory=list)
    category: str = ""            # person/event/promise/secret/knowledge/emotion
    emotion: str = ""             # 情感标签
    importance: int = 5           # 1-10
    created_at: float = 0.0
    source: str = ""              # 来源（core/long_term/important/graph等）


# 停用词（不进关键词索引）
_STOP_WORDS = {
    "的", "了", "是", "在", "我", "你", "他", "她", "它", "们",
    "这", "那", "有", "和", "也", "都", "就", "不", "没", "还",
    "个", "人", "会", "要", "到", "说", "去", "能", "着", "过",
    "很", "太", "吧", "啊", "呢", "吗", "嗯", "哦", "把", "被",
}


class MultiIndex:
    """
    五维索引系统。

    使用方式:
        idx = MultiIndex(data_dir="data/memory")
        idx.add_entry("m1", "今天吃蛋糕好开心",
                       category="event", emotion="happy", people=["小白"])
        results = idx.query(keywords=["蛋糕"], category="event")
        results = idx.query(people=["小白"], time_range=(start, end))
    """

    def __init__(self, data_dir: str = "data/memory") -> None:
        self._data_path = Path(data_dir) / "multi_index.json"
        self._data_path.parent.mkdir(parents=True, exist_ok=True)

        # 主存储
        self._entries: dict[str, IndexEntry] = {}

        # 五维倒排索引
        self._keyword_index: dict[str, set[str]] = defaultdict(set)   # keyword → {entry_id}
        self._time_index: list[tuple[float, str]] = []                 # [(created_at, entry_id)] 有序
        self._person_index: dict[str, set[str]] = defaultdict(set)     # person → {entry_id}
        self._category_index: dict[str, set[str]] = defaultdict(set)   # category → {entry_id}
        self._emotion_index: dict[str, set[str]] = defaultdict(set)    # emotion → {entry_id}

        self._load()

    # ================================================================
    # 索引操作
    # ================================================================

    def add_entry(
        self,
        entry_id: str,
        content: str,
        keywords: list[str] = None,
        people: list[str] = None,
        category: str = "",
        emotion: str = "",
        importance: int = 5,
        source: str = "",
    ) -> IndexEntry:
        """添加或更新一条索引条目。"""
        # 自动提取关键词
        if keywords is None:
            keywords = self._extract_keywords(content)

        entry = IndexEntry(
            entry_id=entry_id,
            content=content,
            keywords=keywords,
            people=people or [],
            category=category,
            emotion=emotion,
            importance=importance,
            created_at=time.time(),
            source=source,
        )

        # 如果已存在，先清除旧索引
        if entry_id in self._entries:
            self._remove_from_indexes(entry_id)

        self._entries[entry_id] = entry
        self._add_to_indexes(entry)
        self._save_debounced()
        return entry

    def remove_entry(self, entry_id: str) -> bool:
        """删除索引条目。"""
        if entry_id not in self._entries:
            return False
        self._remove_from_indexes(entry_id)
        del self._entries[entry_id]
        self._save_debounced()
        return True

    def get_entry(self, entry_id: str) -> Optional[IndexEntry]:
        """获取条目。"""
        return self._entries.get(entry_id)

    # ================================================================
    # 多维查询
    # ================================================================

    def query(
        self,
        keywords: list[str] = None,
        people: list[str] = None,
        category: str = "",
        emotion: str = "",
        time_range: tuple[float, float] = None,
        min_importance: int = 0,
        limit: int = 20,
        mode: str = "or",  # "and" 交集, "or" 并集
    ) -> list[IndexEntry]:
        """
        多维联合查询。

        Args:
            keywords: 关键词列表
            people: 人物列表
            category: 分类
            emotion: 情感标签
            time_range: (start_timestamp, end_timestamp)
            min_importance: 最低重要度
            limit: 最大返回数
            mode: "and"=交集, "or"=并集

        Returns:
            匹配的IndexEntry列表，按重要度降序
        """
        candidate_sets = []

        # 关键词索引
        if keywords:
            kw_ids = set()
            for kw in keywords:
                kw_ids |= self._keyword_index.get(kw, set())
            if kw_ids:
                candidate_sets.append(kw_ids)

        # 人物索引
        if people:
            ppl_ids = set()
            for p in people:
                ppl_ids |= self._person_index.get(p, set())
            if ppl_ids:
                candidate_sets.append(ppl_ids)

        # 分类索引
        if category:
            cat_ids = self._category_index.get(category, set())
            if cat_ids:
                candidate_sets.append(cat_ids)

        # 情感索引
        if emotion:
            emo_ids = self._emotion_index.get(emotion, set())
            if emo_ids:
                candidate_sets.append(emo_ids)

        # 时间范围
        if time_range:
            start, end = time_range
            time_ids = set()
            for ts, eid in self._time_index:
                if start <= ts <= end:
                    time_ids.add(eid)
            if time_ids:
                candidate_sets.append(time_ids)

        # 合并（AND或OR）
        if not candidate_sets:
            # 没有任何条件，返回全部
            result_ids = set(self._entries.keys())
        elif mode == "and":
            result_ids = candidate_sets[0]
            for s in candidate_sets[1:]:
                result_ids &= s
        else:
            result_ids = set()
            for s in candidate_sets:
                result_ids |= s

        # 过滤重要度
        results = []
        for eid in result_ids:
            entry = self._entries.get(eid)
            if entry and entry.importance >= min_importance:
                results.append(entry)

        # 按重要度+时间排序
        results.sort(key=lambda e: (e.importance, e.created_at), reverse=True)
        return results[:limit]

    def search_by_text(self, text: str, limit: int = 10) -> list[IndexEntry]:
        """文本搜索 — 提取关键词后查询。"""
        keywords = self._extract_keywords(text)
        return self.query(keywords=keywords, limit=limit) if keywords else []

    def get_by_category(self, category: str, limit: int = 20) -> list[IndexEntry]:
        """按分类获取。"""
        return self.query(category=category, limit=limit)

    def get_by_person(self, person: str, limit: int = 20) -> list[IndexEntry]:
        """按人物获取。"""
        return self.query(people=[person], limit=limit)

    def get_recent(self, limit: int = 20) -> list[IndexEntry]:
        """获取最近的条目。"""
        entries = sorted(
            self._entries.values(),
            key=lambda e: e.created_at,
            reverse=True,
        )
        return entries[:limit]

    # ================================================================
    # 范围查询（结构化索引增强）
    # ================================================================

    def query_by_importance_range(self, min_imp: int = 1, max_imp: int = 10,
                                  limit: int = 50) -> list[IndexEntry]:
        """按重要度范围查询。"""
        results = [
            e for e in self._entries.values()
            if min_imp <= e.importance <= max_imp
        ]
        results.sort(key=lambda e: e.importance, reverse=True)
        return results[:limit]

    def query_by_time_range(self, start: float, end: float,
                            limit: int = 50) -> list[IndexEntry]:
        """按时间范围查询。"""
        results = [
            e for e in self._entries.values()
            if start <= e.created_at <= end
        ]
        results.sort(key=lambda e: e.created_at, reverse=True)
        return results[:limit]

    def aggregate(self) -> dict:
        """聚合统计 — 按各维度统计记忆分布。"""
        cat_dist: dict[str, int] = {}
        emo_dist: dict[str, int] = {}
        src_dist: dict[str, int] = {}
        imp_dist: dict[int, int] = {}
        people_count: dict[str, int] = {}

        for e in self._entries.values():
            if e.category:
                cat_dist[e.category] = cat_dist.get(e.category, 0) + 1
            if e.emotion:
                emo_dist[e.emotion] = emo_dist.get(e.emotion, 0) + 1
            if e.source:
                src_dist[e.source] = src_dist.get(e.source, 0) + 1
            imp_dist[e.importance] = imp_dist.get(e.importance, 0) + 1
            for p in e.people:
                people_count[p] = people_count.get(p, 0) + 1

        # 按天统计
        from datetime import datetime
        daily: dict[str, int] = {}
        for e in self._entries.values():
            day = datetime.fromtimestamp(e.created_at).strftime("%Y-%m-%d")
            daily[day] = daily.get(day, 0) + 1

        return {
            "total": len(self._entries),
            "by_category": cat_dist,
            "by_emotion": emo_dist,
            "by_source": src_dist,
            "by_importance": dict(sorted(imp_dist.items())),
            "by_person": dict(sorted(people_count.items(), key=lambda x: x[1], reverse=True)[:20]),
            "by_day": dict(sorted(daily.items())[-30:]),  # 最近30天
        }

    # ================================================================
    # 统计
    # ================================================================

    @property
    def stats(self) -> dict:
        cat_dist = {}
        for cat, ids in self._category_index.items():
            cat_dist[cat] = len(ids)
        emo_dist = {}
        for emo, ids in self._emotion_index.items():
            emo_dist[emo] = len(ids)
        return {
            "total_entries": len(self._entries),
            "total_keywords": len(self._keyword_index),
            "total_people": len(self._person_index),
            "category_distribution": cat_dist,
            "emotion_distribution": emo_dist,
        }

    # ================================================================
    # 内部方法
    # ================================================================

    def _extract_keywords(self, text: str) -> list[str]:
        """简单中文关键词提取。"""
        segments = re.split(r'[，。！？、；：\s,.:;!?\[\]()]+', text)
        keywords = []
        for seg in segments:
            seg = seg.strip()
            if len(seg) < 2:
                continue
            if len(seg) <= 4:
                if seg not in _STOP_WORDS:
                    keywords.append(seg)
            else:
                for size in (3, 2):
                    for i in range(0, len(seg) - size + 1, size):
                        word = seg[i:i + size]
                        if word not in _STOP_WORDS:
                            keywords.append(word)
        return list(set(keywords))[:15]

    def _add_to_indexes(self, entry: IndexEntry) -> None:
        """将条目加入所有索引。"""
        eid = entry.entry_id
        for kw in entry.keywords:
            self._keyword_index[kw].add(eid)
        for p in entry.people:
            self._person_index[p].add(eid)
        if entry.category:
            self._category_index[entry.category].add(eid)
        if entry.emotion:
            self._emotion_index[entry.emotion].add(eid)
        # 有序插入时间索引
        self._time_index.append((entry.created_at, eid))

    def _remove_from_indexes(self, entry_id: str) -> None:
        """从所有索引中移除条目。"""
        entry = self._entries.get(entry_id)
        if not entry:
            return
        for kw in entry.keywords:
            self._keyword_index.get(kw, set()).discard(entry_id)
        for p in entry.people:
            self._person_index.get(p, set()).discard(entry_id)
        if entry.category:
            self._category_index.get(entry.category, set()).discard(entry_id)
        if entry.emotion:
            self._emotion_index.get(entry.emotion, set()).discard(entry_id)
        self._time_index = [(t, e) for t, e in self._time_index if e != entry_id]

    # ================================================================
    # 持久化
    # ================================================================

    _save_counter = 0

    def _save_debounced(self) -> None:
        self._save_counter += 1
        if self._save_counter % 20 == 0:
            self._save()

    def _save(self) -> None:
        try:
            data = {eid: asdict(e) for eid, e in self._entries.items()}
            self._data_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            logger.warning(f"[MultiIndex] 保存失败: {e}")

    def _load(self) -> None:
        if not self._data_path.exists():
            return
        try:
            data = json.loads(self._data_path.read_text(encoding="utf-8"))
            for eid, edata in data.items():
                entry = IndexEntry(**edata)
                self._entries[eid] = entry
                self._add_to_indexes(entry)
            # 排序时间索引
            self._time_index.sort(key=lambda x: x[0])
            logger.debug(f"[MultiIndex] 加载完成: {len(self._entries)}条")
        except Exception as e:
            logger.warning(f"[MultiIndex] 加载失败: {e}")

    def force_save(self) -> None:
        self._save()


# ================================================================
# 自动发现接口
# ================================================================

class MultiIndexModule(MemoryModule):
    """多索引系统模块 — 自动发现注册。"""
    name = "multi_index"

    def init(self, data_dir="data/memory", **kwargs):
        self._impl = MultiIndex(data_dir=data_dir)

    def on_message(self, user_msg: str = "", ai_reply: str = "") -> None:
        """每次对话后将用户消息加入索引。"""
        if not user_msg or not hasattr(self, '_impl') or len(user_msg) < 5:
            return
        # 自动分类
        category = ""
        try:
            from white_salary.core.memory.auto_classifier import MemoryClassifier
            classifier = MemoryClassifier()
            category = classifier.classify(user_msg)
        except Exception:
            pass

        entry_id = f"msg_{int(time.time() * 1000)}"
        self._impl.add_entry(
            entry_id=entry_id,
            content=user_msg,
            category=category,
            source="conversation",
        )

    def on_session_end(self) -> None:
        if hasattr(self, '_impl'):
            self._impl.force_save()


MODULE = MultiIndexModule
