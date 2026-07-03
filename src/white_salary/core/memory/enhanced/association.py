"""
white_salary/core/memory/enhanced/association.py

记忆关联图 — 实现"蛋糕→生日→开心"的联想记忆。

借鉴v2的enhanced/association.py：
  - MemoryNode: 记忆节点（内容/标签/关键词/人物/权重）
  - MemoryEdge: 关联边（类型/强度/共访问次数）
  - 6种关联类型：TEMPORAL/PERSON/TOPIC/EMOTION/CAUSAL/SIMILAR
  - BFS遍历召回关联记忆
  - 共访问自动加强关联
  - 新记忆自动与相关旧记忆建立边

配置从 config/memory_settings.json 的 association 节读取。

自动发现：导出MODULE供MemoryManager加载。
"""

import json
import re
import time
from collections import deque
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional

from loguru import logger
from white_salary.core.memory.module_base import MemoryModule


# ================================================================
# 数据结构
# ================================================================

@dataclass
class MemoryNode:
    """记忆节点。"""
    node_id: str = ""
    content: str = ""
    tags: list[str] = field(default_factory=list)       # 分类标签（person/event等）
    keywords: list[str] = field(default_factory=list)    # 自动提取的关键词
    people: list[str] = field(default_factory=list)      # 涉及的人物
    created_at: float = 0.0
    weight: float = 1.0                                  # 节点重要度


@dataclass
class MemoryEdge:
    """关联边。"""
    source_id: str = ""
    target_id: str = ""
    relation_type: str = ""      # 关联类型
    strength: float = 0.5        # 关联强度 0.0-1.0
    created_at: float = 0.0
    co_access_count: int = 0     # 共同被访问的次数


# 6种关联类型
RELATION_TEMPORAL = "temporal"   # 时间相近
RELATION_PERSON = "person"      # 涉及相同人物
RELATION_TOPIC = "topic"        # 相同话题
RELATION_EMOTION = "emotion"    # 相似情感
RELATION_CAUSAL = "causal"      # 因果关系
RELATION_SIMILAR = "similar"    # 内容相似

ALL_RELATION_TYPES = [
    RELATION_TEMPORAL, RELATION_PERSON, RELATION_TOPIC,
    RELATION_EMOTION, RELATION_CAUSAL, RELATION_SIMILAR,
]

# 关键词提取：简单的中文分词（按标点和停用词切分）
_STOP_WORDS = {
    "的", "了", "是", "在", "我", "你", "他", "她", "它", "们",
    "这", "那", "有", "和", "与", "也", "都", "就", "不", "没",
    "还", "很", "太", "吧", "啊", "呢", "吗", "嗯", "哦", "哈",
    "个", "人", "会", "要", "到", "说", "去", "能", "可以", "着",
    "被", "把", "让", "从", "对", "给", "做", "看", "来", "过",
    "上", "下", "里", "中", "后", "前", "时", "好", "大", "小",
    "多", "少", "一", "二", "三", "什么", "怎么", "为什么",
}

# 人物关系词
_PERSON_PATTERNS = [
    r"(?:我|你|他|她)(?:的)?(?:妈妈?|爸爸?|哥哥?|姐姐?|弟弟?|妹妹?|"
    r"朋友|同学|同事|老师|老板|对象|男朋友|女朋友|老公|老婆)",
    r"(?:他|她)(?:叫|是|名字叫?)(\S{1,4})",
]


class MemoryGraph:
    """
    记忆关联图。

    使用方式:
        graph = MemoryGraph(config, data_dir)
        graph.add_node("m1", "今天吃了蛋糕", tags=["event"])
        graph.add_node("m2", "小白的生日", tags=["event"])
        graph.add_edge("m1", "m2", "topic", strength=0.8)
        related = graph.get_associated("m1", depth=2)
    """

    def __init__(self, config: dict = None, data_dir: str = "data/memory") -> None:
        cfg = config or {}
        self._max_edges_per_node = cfg.get("max_edges_per_node", 20)
        self._auto_associate = cfg.get("auto_associate", True)
        self._min_similarity = cfg.get("min_similarity_for_link", 0.5)

        self._data_path = Path(data_dir) / "enhanced" / "graph.json"
        self._data_path.parent.mkdir(parents=True, exist_ok=True)

        # 核心数据
        self._nodes: dict[str, MemoryNode] = {}
        self._edges: list[MemoryEdge] = []

        # 索引（加速查询）
        self._adjacency: dict[str, list[str]] = {}       # node_id → [edge_index, ...]
        self._keyword_index: dict[str, set[str]] = {}     # keyword → {node_id, ...}
        self._person_index: dict[str, set[str]] = {}      # person → {node_id, ...}
        self._tag_index: dict[str, set[str]] = {}         # tag → {node_id, ...}

        self._load()

        # 编译人物提取正则
        self._person_patterns = [re.compile(p) for p in _PERSON_PATTERNS]

    # ================================================================
    # 节点操作
    # ================================================================

    def add_node(self, node_id: str, content: str,
                 tags: list[str] = None, keywords: list[str] = None,
                 people: list[str] = None, weight: float = 1.0,
                 auto_extract: bool = True) -> MemoryNode:
        """
        添加记忆节点。

        Args:
            node_id: 唯一标识
            content: 记忆内容
            tags: 分类标签
            keywords: 关键词（None则自动提取）
            people: 涉及人物（None则自动提取）
            weight: 重要度
            auto_extract: 是否自动提取关键词和人物
        """
        if node_id in self._nodes:
            # 已存在，更新内容
            node = self._nodes[node_id]
            node.content = content
            if tags:
                node.tags = list(set(node.tags + tags))
            if keywords:
                node.keywords = list(set(node.keywords + keywords))
            if people:
                node.people = list(set(node.people + people))
            self._update_indexes(node)
            self._save_debounced()
            return node

        # 自动提取
        extracted_kw = self._extract_keywords(content) if auto_extract else []
        extracted_ppl = self._extract_people(content) if auto_extract else []

        node = MemoryNode(
            node_id=node_id,
            content=content,
            tags=tags or [],
            keywords=keywords or extracted_kw,
            people=people or extracted_ppl,
            created_at=time.time(),
            weight=weight,
        )
        self._nodes[node_id] = node
        self._adjacency[node_id] = []
        self._update_indexes(node)

        # 自动关联：与已有节点建立边
        if self._auto_associate and len(self._nodes) > 1:
            self._auto_link(node)

        self._save_debounced()
        return node

    def remove_node(self, node_id: str) -> bool:
        """删除节点及其所有边。"""
        if node_id not in self._nodes:
            return False

        node = self._nodes.pop(node_id)

        # 删除相关边
        self._edges = [
            e for e in self._edges
            if e.source_id != node_id and e.target_id != node_id
        ]

        # 清理索引
        self._adjacency.pop(node_id, None)
        for kw in node.keywords:
            if kw in self._keyword_index:
                self._keyword_index[kw].discard(node_id)
        for p in node.people:
            if p in self._person_index:
                self._person_index[p].discard(node_id)
        for t in node.tags:
            if t in self._tag_index:
                self._tag_index[t].discard(node_id)

        # 重建邻接表
        self._rebuild_adjacency()
        self._save()
        return True

    def get_node(self, node_id: str) -> Optional[MemoryNode]:
        """获取节点。"""
        return self._nodes.get(node_id)

    # ================================================================
    # 边操作
    # ================================================================

    def add_edge(self, source_id: str, target_id: str,
                 relation_type: str, strength: float = 0.5) -> Optional[MemoryEdge]:
        """
        添加或加强关联边。

        如果边已存在，加强strength（取较大值）并增加co_access_count。
        """
        if source_id not in self._nodes or target_id not in self._nodes:
            return None
        if source_id == target_id:
            return None

        # 检查是否已存在
        for edge in self._edges:
            if (edge.source_id == source_id and edge.target_id == target_id and
                    edge.relation_type == relation_type):
                # 加强已有边
                edge.strength = min(1.0, max(edge.strength, strength) + 0.05)
                edge.co_access_count += 1
                self._save_debounced()
                return edge
            # 反向也算同一条边
            if (edge.source_id == target_id and edge.target_id == source_id and
                    edge.relation_type == relation_type):
                edge.strength = min(1.0, max(edge.strength, strength) + 0.05)
                edge.co_access_count += 1
                self._save_debounced()
                return edge

        # 检查节点边数限制
        src_edges = len(self._adjacency.get(source_id, []))
        if src_edges >= self._max_edges_per_node:
            # 删除最弱的边
            self._prune_weakest_edge(source_id)

        edge = MemoryEdge(
            source_id=source_id,
            target_id=target_id,
            relation_type=relation_type,
            strength=strength,
            created_at=time.time(),
            co_access_count=0,
        )
        edge_idx = len(self._edges)
        self._edges.append(edge)
        self._adjacency.setdefault(source_id, []).append(str(edge_idx))
        self._adjacency.setdefault(target_id, []).append(str(edge_idx))
        self._save_debounced()
        return edge

    # ================================================================
    # 查询
    # ================================================================

    def get_associated(self, node_id: str, depth: int = 2,
                       min_strength: float = 0.2, limit: int = 10) -> list[tuple[str, float]]:
        """
        BFS遍历获取关联记忆。

        Args:
            node_id: 起始节点
            depth: 搜索深度
            min_strength: 最小关联强度
            limit: 最多返回数量

        Returns:
            [(node_id, cumulative_strength), ...] 按强度降序
        """
        if node_id not in self._nodes:
            return []

        visited = {node_id}
        queue = deque([(node_id, 1.0, 0)])  # (id, cumulative_strength, current_depth)
        results = []

        while queue:
            current_id, cum_strength, cur_depth = queue.popleft()
            if cur_depth >= depth:
                continue

            # 找所有相邻边
            for edge in self._edges:
                neighbor_id = None
                if edge.source_id == current_id:
                    neighbor_id = edge.target_id
                elif edge.target_id == current_id:
                    neighbor_id = edge.source_id

                if neighbor_id and neighbor_id not in visited and edge.strength >= min_strength:
                    visited.add(neighbor_id)
                    new_strength = cum_strength * edge.strength
                    results.append((neighbor_id, new_strength))
                    queue.append((neighbor_id, new_strength, cur_depth + 1))

        # 按强度降序排列
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:limit]

    def search_by_keyword(self, keyword: str, limit: int = 10) -> list[str]:
        """按关键词查找节点。"""
        node_ids = self._keyword_index.get(keyword, set())
        return list(node_ids)[:limit]

    def search_by_person(self, person: str, limit: int = 10) -> list[str]:
        """按人物查找节点。"""
        node_ids = self._person_index.get(person, set())
        return list(node_ids)[:limit]

    def search_by_tag(self, tag: str, limit: int = 10) -> list[str]:
        """按标签查找节点。"""
        node_ids = self._tag_index.get(tag, set())
        return list(node_ids)[:limit]

    def search_by_content(self, query: str, limit: int = 10) -> list[tuple[str, int]]:
        """
        按内容关键词匹配查找节点。

        Returns:
            [(node_id, match_count), ...] 按匹配数降序
        """
        query_keywords = self._extract_keywords(query)
        if not query_keywords:
            return []

        scores: dict[str, int] = {}
        for kw in query_keywords:
            for nid in self._keyword_index.get(kw, set()):
                scores[nid] = scores.get(nid, 0) + 1

        results = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return results[:limit]

    def record_co_access(self, node_ids: list[str]) -> None:
        """
        记录共同访问 — 同时被检索到的记忆之间加强关联。
        """
        if len(node_ids) < 2:
            return

        for i in range(len(node_ids)):
            for j in range(i + 1, len(node_ids)):
                src, tgt = node_ids[i], node_ids[j]
                if src in self._nodes and tgt in self._nodes:
                    # 找已有边
                    found = False
                    for edge in self._edges:
                        if ((edge.source_id == src and edge.target_id == tgt) or
                                (edge.source_id == tgt and edge.target_id == src)):
                            edge.co_access_count += 1
                            edge.strength = min(1.0, edge.strength + 0.02)
                            found = True
                            break
                    if not found:
                        self.add_edge(src, tgt, RELATION_SIMILAR, strength=0.3)

        self._save_debounced()

    # ================================================================
    # 统计
    # ================================================================

    @property
    def stats(self) -> dict:
        """图谱统计信息。"""
        type_counts = {}
        for edge in self._edges:
            type_counts[edge.relation_type] = type_counts.get(edge.relation_type, 0) + 1

        tag_counts = {}
        for node in self._nodes.values():
            for t in node.tags:
                tag_counts[t] = tag_counts.get(t, 0) + 1

        return {
            "total_nodes": len(self._nodes),
            "total_edges": len(self._edges),
            "relation_types": type_counts,
            "tag_distribution": tag_counts,
            "avg_edges_per_node": (
                len(self._edges) * 2 / max(len(self._nodes), 1)
            ),
        }

    # ================================================================
    # 内部方法
    # ================================================================

    def _extract_keywords(self, text: str) -> list[str]:
        """从文本提取关键词（简单中文分词）。"""
        # 按标点符号切分
        segments = re.split(r'[，。！？、；：\u201c\u201d\u2018\u2019【】（）\s,.:;!?\[\]()]+', text)
        keywords = []
        for seg in segments:
            seg = seg.strip()
            if len(seg) < 2:
                continue
            # 按2-4字切分（简单的N-gram）
            if len(seg) <= 4:
                if seg not in _STOP_WORDS:
                    keywords.append(seg)
            else:
                # 切成2字和3字词
                for size in (3, 2):
                    for i in range(0, len(seg) - size + 1, size):
                        word = seg[i:i + size]
                        if word not in _STOP_WORDS:
                            keywords.append(word)
        return list(set(keywords))[:20]  # 最多20个

    def _extract_people(self, text: str) -> list[str]:
        """从文本提取人物引用。"""
        people = []
        for pattern in self._person_patterns:
            matches = pattern.findall(text)
            for m in matches:
                if isinstance(m, tuple):
                    m = m[0]
                m = m.strip()
                if m and len(m) <= 10:
                    people.append(m)
        return list(set(people))

    def _auto_link(self, new_node: MemoryNode) -> None:
        """新节点自动与已有节点建立关联。"""
        new_kw = set(new_node.keywords)
        new_ppl = set(new_node.people)
        new_tags = set(new_node.tags)
        now = new_node.created_at

        candidates: list[tuple[str, str, float]] = []  # (node_id, relation_type, strength)

        for nid, node in self._nodes.items():
            if nid == new_node.node_id:
                continue

            # 关键词重叠 → TOPIC关联
            common_kw = new_kw & set(node.keywords)
            if common_kw:
                overlap = len(common_kw) / max(len(new_kw), 1)
                if overlap >= 0.2:
                    candidates.append((nid, RELATION_TOPIC, min(0.3 + overlap * 0.5, 0.9)))

            # 人物重叠 → PERSON关联
            common_ppl = new_ppl & set(node.people)
            if common_ppl:
                candidates.append((nid, RELATION_PERSON, 0.6))

            # 标签重叠 → SIMILAR关联
            common_tags = new_tags & set(node.tags)
            if common_tags and "emotion" in common_tags:
                candidates.append((nid, RELATION_EMOTION, 0.4))
            elif common_tags:
                candidates.append((nid, RELATION_SIMILAR, 0.3))

            # 时间相近（5分钟内）→ TEMPORAL关联
            if abs(now - node.created_at) < 300:
                candidates.append((nid, RELATION_TEMPORAL, 0.4))

        # 按强度排序，取top N
        candidates.sort(key=lambda x: x[2], reverse=True)
        max_auto = min(5, self._max_edges_per_node)
        for nid, rel_type, strength in candidates[:max_auto]:
            self.add_edge(new_node.node_id, nid, rel_type, strength)

    def _prune_weakest_edge(self, node_id: str) -> None:
        """删除节点最弱的一条边。"""
        weakest_idx = -1
        weakest_strength = float('inf')
        for i, edge in enumerate(self._edges):
            if edge.source_id == node_id or edge.target_id == node_id:
                if edge.strength < weakest_strength:
                    weakest_strength = edge.strength
                    weakest_idx = i
        if weakest_idx >= 0:
            self._edges.pop(weakest_idx)
            self._rebuild_adjacency()

    def _update_indexes(self, node: MemoryNode) -> None:
        """更新节点的索引。"""
        nid = node.node_id
        for kw in node.keywords:
            self._keyword_index.setdefault(kw, set()).add(nid)
        for p in node.people:
            self._person_index.setdefault(p, set()).add(nid)
        for t in node.tags:
            self._tag_index.setdefault(t, set()).add(nid)

    def _rebuild_adjacency(self) -> None:
        """重建邻接表。"""
        self._adjacency = {nid: [] for nid in self._nodes}
        for i, edge in enumerate(self._edges):
            self._adjacency.setdefault(edge.source_id, []).append(str(i))
            self._adjacency.setdefault(edge.target_id, []).append(str(i))

    # ================================================================
    # 持久化
    # ================================================================

    _save_counter = 0

    def _save_debounced(self) -> None:
        """防抖保存（每30次操作保存一次）。"""
        self._save_counter += 1
        if self._save_counter % 30 == 0:
            self._save()

    def _save(self) -> None:
        """保存到JSON。"""
        try:
            data = {
                "nodes": {nid: asdict(n) for nid, n in self._nodes.items()},
                "edges": [asdict(e) for e in self._edges],
            }
            self._data_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            logger.warning(f"[Association] 保存失败: {e}")

    def _load(self) -> None:
        """从JSON加载。"""
        if not self._data_path.exists():
            return
        try:
            data = json.loads(self._data_path.read_text(encoding="utf-8"))

            # 加载节点
            for nid, ndata in data.get("nodes", {}).items():
                self._nodes[nid] = MemoryNode(**ndata)
                self._update_indexes(self._nodes[nid])

            # 加载边
            for edata in data.get("edges", []):
                self._edges.append(MemoryEdge(**edata))

            # 构建邻接表
            self._rebuild_adjacency()

            logger.debug(
                f"[Association] 加载完成: {len(self._nodes)}节点, {len(self._edges)}边"
            )
        except Exception as e:
            logger.warning(f"[Association] 加载失败: {e}")

    def force_save(self) -> None:
        """强制保存（外部调用）。"""
        self._save()


# ================================================================
# 自动发现接口
# ================================================================

class AssociationModule(MemoryModule):
    """记忆关联图模块 — 自动发现注册。"""
    name = "association_graph"

    def init(self, data_dir="data/memory", **kwargs):
        # 从配置文件读取参数
        config = {}
        try:
            cfg_path = Path("config/memory_settings.json")
            if cfg_path.exists():
                all_cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
                config = all_cfg.get("association", {})
        except Exception:
            pass
        self._impl = MemoryGraph(config=config, data_dir=data_dir)

    def get_context_prompt(self, message: str = "", **kwargs) -> str:
        """联想记忆注入由IntegratorModule统一管理，这里不重复注入。"""
        # 联想记忆的注入交给enhanced/integrator.py统一处理
        # 避免AssociationModule和IntegratorModule重复注入相同内容
        return ""

    def _get_context_prompt_impl(self, message: str = "") -> str:
        """内部方法：实际的联想记忆检索（供IntegratorModule调用）。"""
        if not message or not hasattr(self, '_impl'):
            return ""

        matches = self._impl.search_by_content(message, limit=3)
        if not matches:
            return ""

        # 对每个匹配节点，获取关联记忆
        associated_contents = []
        seen = set()
        for nid, _ in matches:
            node = self._impl.get_node(nid)
            if node and node.content not in seen:
                seen.add(node.content)
                associated_contents.append(node.content)

            # 获取关联节点
            for assoc_id, strength in self._impl.get_associated(nid, depth=1, limit=3):
                assoc_node = self._impl.get_node(assoc_id)
                if assoc_node and assoc_node.content not in seen and strength >= 0.3:
                    seen.add(assoc_node.content)
                    associated_contents.append(assoc_node.content)

        if not associated_contents:
            return ""

        # 限制最多5条
        associated_contents = associated_contents[:5]
        lines = "\n".join(f"  - {c}" for c in associated_contents)
        return f"[联想记忆]\n{lines}"

    def on_message(self, user_msg: str = "", ai_reply: str = "") -> None:
        """每次对话后，将用户消息作为节点加入关联图。"""
        if not user_msg or not hasattr(self, '_impl') or len(user_msg) < 5:
            return

        # 生成节点ID
        node_id = f"msg_{int(time.time() * 1000)}"

        # 获取分类标签（如果分类器可用）
        tags = []
        try:
            from white_salary.core.memory.auto_classifier import MemoryClassifier
            classifier = MemoryClassifier()
            cat = classifier.classify(user_msg)
            if cat:
                tags.append(cat)
        except Exception:
            pass

        self._impl.add_node(
            node_id=node_id,
            content=user_msg,
            tags=tags,
            weight=1.0,
        )

    def on_session_end(self) -> None:
        """会话结束时强制保存。"""
        if hasattr(self, '_impl'):
            self._impl.force_save()


MODULE = AssociationModule
