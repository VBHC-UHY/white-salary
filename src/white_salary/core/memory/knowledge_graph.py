"""
white_salary/core/memory/knowledge_graph.py

知识图谱 v2 — 多类型实体+关系的完整图谱系统。

升级说明（从v1）：
  - v1只存person（SQLite） → v2存13种实体类型（JSON）
  - v1只有单向关系 → v2支持双向+重要度+属性
  - v1只有正则提取 → v2加LLM自动提取
  - v1全量注入上下文 → v2智能选择相关关系
  - v1无查询 → v2支持自然语言查询+路径查询

数据存储：JSON文件（knowledge_graph.json）
  {
    "entities": [{"id","name","type","attributes","created_at","updated_at"}],
    "relations": [{"id","from_id","to_id","relation_type","importance","properties","created_at"}]
  }

实体类型：person/food/hobby/event/concept/group/skill/emotion/media/thing/activity/behavior/work

借鉴v2的knowledge_graph.py但重写适配我们的架构。
"""

import json
import re
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from loguru import logger


# ================================================================
# 实体类型
# ================================================================

ENTITY_TYPES = {
    "person": "人物",
    "food": "食物",
    "hobby": "爱好",
    "event": "事件",
    "concept": "概念",
    "group": "群组",
    "skill": "技能",
    "emotion": "情感",
    "media": "影视/游戏",
    "thing": "物品",
    "activity": "活动",
    "behavior": "行为",
    "work": "作品/项目",
}

# 关系类型（常见的，也支持自定义）
RELATION_TYPES = {
    "家人": "family", "朋友": "friend", "好朋友": "close_friend",
    "创造者": "creator", "喜欢": "likes", "讨厌": "dislikes",
    "关心": "cares", "属于": "belongs_to", "参与": "participates",
    "认识": "knows", "同学": "classmate", "同事": "colleague",
}

# 关系提取正则（v1保留，作为基础提取）
RELATIONSHIP_PATTERNS = [
    (r"我(?:的)?(?:妈妈|母亲|老妈|妈)(?:叫|是|名字是)?\s*([^\s，。！？,!?\n]{1,8})", "person", "家人"),
    (r"我(?:的)?(?:爸爸|父亲|老爸|爹)(?:叫|是|名字是)?\s*([^\s，。！？,!?\n]{1,8})", "person", "家人"),
    (r"我(?:的)?(?:哥哥|弟弟|姐姐|妹妹)(?:叫|是)?\s*([^\s，。！？,!?\n]{1,8})", "person", "家人"),
    (r"我(?:的)?(?:好朋友|朋友|闺蜜|兄弟|哥们)(?:叫|是)?\s*([^\s，。！？,!?\n]{1,8})", "person", "朋友"),
    (r"我(?:的)?(?:男朋友|女朋友|对象)(?:叫|是)?\s*([^\s，。！？,!?\n]{1,8})", "person", "恋人"),
    (r"我(?:喜欢|爱|超喜欢)\s*([^\s，。！？,!?\n]{2,10})", None, "喜欢"),
    (r"我(?:讨厌|不喜欢|烦)\s*([^\s，。！？,!?\n]{2,10})", None, "讨厌"),
]


# ================================================================
# 数据结构
# ================================================================

@dataclass
class Entity:
    """图谱中的一个实体节点。"""
    id: str = ""
    name: str = ""
    type: str = "person"
    attributes: dict = field(default_factory=dict)
    created_at: float = 0.0
    updated_at: float = 0.0
    mention_count: int = 0


@dataclass
class Relation:
    """图谱中的一条关系边。"""
    id: str = ""
    from_id: str = ""
    to_id: str = ""
    relation_type: str = ""
    importance: float = 50.0      # 0-100
    properties: dict = field(default_factory=dict)
    created_at: float = 0.0


# ================================================================
# 知识图谱
# ================================================================

class KnowledgeGraph:
    """
    知识图谱 v2 — 多类型实体+关系网络。

    使用方式:
        kg = KnowledgeGraph(data_dir="data/memory")
        kg.add_entity("小白", "person", {"qq": "1234567890"})
        kg.add_relation("白", "家人", "小白", importance=100)
        context = kg.get_context_for_message("小白喜欢什么？")
    """

    def __init__(self, data_dir: str = "data/memory") -> None:
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._json_path = self._data_dir / "knowledge_graph.json"

        # 内存中的图谱
        self._entities: list[Entity] = []
        self._relations: list[Relation] = []

        # 名字→ID索引
        self._name_index: dict[str, str] = {}

        self._load()
        self._migrate_from_sqlite()  # 自动迁移旧SQLite数据

    # ================================================================
    # 实体 CRUD
    # ================================================================

    def add_entity(self, name: str, entity_type: str = "person",
                   attributes: dict = None) -> Entity:
        """添加实体。如果同名已存在则更新。"""
        existing = self.find_entity(name)
        if existing:
            existing.updated_at = time.time()
            existing.mention_count += 1
            if attributes:
                existing.attributes.update(attributes)
            if entity_type != "person":
                existing.type = entity_type
            self._save()
            return existing

        entity = Entity(
            id=str(uuid.uuid4())[:8],
            name=name,
            type=entity_type,
            attributes=attributes or {},
            created_at=time.time(),
            updated_at=time.time(),
            mention_count=1,
        )
        self._entities.append(entity)
        self._name_index[name.lower()] = entity.id
        self._save()
        logger.debug(f"[KG] 新增实体: {name} ({entity_type})")
        return entity

    def find_entity(self, name: str) -> Optional[Entity]:
        """按名字查找实体（不区分大小写）。"""
        eid = self._name_index.get(name.lower())
        if eid:
            for e in self._entities:
                if e.id == eid:
                    return e
        # 备选：模糊匹配
        for e in self._entities:
            if e.name.lower() == name.lower():
                return e
        return None

    def find_entity_by_id(self, entity_id: str) -> Optional[Entity]:
        for e in self._entities:
            if e.id == entity_id:
                return e
        return None

    def update_entity(self, entity_id: str, name: str = None,
                      entity_type: str = None, attributes: dict = None) -> bool:
        e = self.find_entity_by_id(entity_id)
        if not e:
            return False
        if name:
            old_name = e.name
            self._name_index.pop(old_name.lower(), None)
            e.name = name
            self._name_index[name.lower()] = e.id
        if entity_type:
            e.type = entity_type
        if attributes:
            e.attributes.update(attributes)
        e.updated_at = time.time()
        self._save()
        return True

    def delete_entity(self, entity_id: str) -> bool:
        """删除实体 + 级联删除所有相关关系。"""
        entity = self.find_entity_by_id(entity_id)
        if not entity:
            return False
        self._name_index.pop(entity.name.lower(), None)
        self._entities = [e for e in self._entities if e.id != entity_id]
        self._relations = [r for r in self._relations
                           if r.from_id != entity_id and r.to_id != entity_id]
        self._save()
        logger.debug(f"[KG] 删除实体: {entity.name}")
        return True

    # ================================================================
    # 关系 CRUD
    # ================================================================

    def add_relation(self, from_name: str, relation_type: str, to_name: str,
                     importance: float = 50.0, properties: dict = None,
                     from_type: str = "person", to_type: str = "person") -> Optional[Relation]:
        """添加关系。自动创建不存在的实体。"""
        from_entity = self.find_entity(from_name) or self.add_entity(from_name, from_type)
        to_entity = self.find_entity(to_name) or self.add_entity(to_name, to_type)

        # 检查是否已有同类关系
        for r in self._relations:
            if (r.from_id == from_entity.id and r.to_id == to_entity.id
                    and r.relation_type == relation_type):
                r.importance = max(r.importance, importance)
                if properties:
                    r.properties.update(properties)
                self._save()
                return r

        relation = Relation(
            id=str(uuid.uuid4())[:8],
            from_id=from_entity.id,
            to_id=to_entity.id,
            relation_type=relation_type,
            importance=importance,
            properties=properties or {},
            created_at=time.time(),
        )
        self._relations.append(relation)
        self._save()
        logger.debug(f"[KG] 新增关系: {from_name} --{relation_type}--> {to_name}")
        return relation

    def get_relations_of(self, entity_name: str) -> list[dict]:
        """获取某个实体的所有关系。"""
        entity = self.find_entity(entity_name)
        if not entity:
            return []

        results = []
        for r in self._relations:
            if r.from_id == entity.id:
                target = self.find_entity_by_id(r.to_id)
                results.append({
                    "relation_id": r.id,
                    "direction": "out",
                    "relation": r.relation_type,
                    "target": target.name if target else "?",
                    "target_type": target.type if target else "?",
                    "importance": r.importance,
                })
            elif r.to_id == entity.id:
                source = self.find_entity_by_id(r.from_id)
                results.append({
                    "relation_id": r.id,
                    "direction": "in",
                    "relation": r.relation_type,
                    "source": source.name if source else "?",
                    "source_type": source.type if source else "?",
                    "importance": r.importance,
                })

        return sorted(results, key=lambda x: x["importance"], reverse=True)

    def delete_relation(self, relation_id: str) -> bool:
        before = len(self._relations)
        self._relations = [r for r in self._relations if r.id != relation_id]
        if len(self._relations) < before:
            self._save()
            return True
        return False

    def update_relation(self, relation_id: str, relation_type: str = None,
                        importance: float = None, properties: dict = None) -> bool:
        for r in self._relations:
            if r.id == relation_id:
                if relation_type:
                    r.relation_type = relation_type
                if importance is not None:
                    r.importance = importance
                if properties:
                    r.properties.update(properties)
                self._save()
                return True
        return False

    # ================================================================
    # 路径查询（1.8）
    # ================================================================

    def query_path(self, start_name: str, relation_type: str = "",
                   max_depth: int = 2) -> list[list[str]]:
        """
        路径查询。
        "小白→朋友→?"  → 找小白的朋友的所有关系。

        Returns:
            路径列表，每条路径是 [实体名, 关系, 实体名, ...]
        """
        start = self.find_entity(start_name)
        if not start:
            return []

        paths = []
        self._dfs(start.id, relation_type, max_depth, [start_name], set(), paths)
        return paths

    def _dfs(self, current_id: str, relation_filter: str, depth: int,
             path: list[str], visited: set, results: list) -> None:
        if depth <= 0:
            return
        visited.add(current_id)

        for r in self._relations:
            next_id = None
            if r.from_id == current_id:
                next_id = r.to_id
            elif r.to_id == current_id:
                next_id = r.from_id

            if next_id and next_id not in visited:
                if relation_filter and r.relation_type != relation_filter:
                    continue
                target = self.find_entity_by_id(next_id)
                if target:
                    new_path = path + [r.relation_type, target.name]
                    results.append(new_path)
                    self._dfs(next_id, "", depth - 1, new_path, visited.copy(), results)

    # ================================================================
    # 正则提取（v1兼容）
    # ================================================================

    def extract_from_text(self, text: str) -> list[str]:
        """从文本中用正则提取实体和关系。"""
        results = []
        pronouns = {"我", "你", "他", "她", "它", "谁", "什么", "这个", "那个",
                     "大家", "别人", "自己", "对方", "人家"}

        for pattern, entity_type, relation in RELATIONSHIP_PATTERNS:
            match = re.search(pattern, text)
            if match:
                name = match.group(1).strip()
                if name and 1 <= len(name) <= 10 and name not in pronouns:
                    e_type = entity_type or self._guess_type(name, text)
                    self.add_entity(name, e_type)

                    if relation:
                        self.add_relation("白", relation, name,
                                          importance=60, from_type="person", to_type=e_type)
                        results.append(f"图谱:{name}({relation})")
                    else:
                        results.append(f"图谱:新增{name}")

        return results

    def _guess_type(self, name: str, context: str) -> str:
        """根据上下文猜测实体类型。"""
        food_kw = ["吃", "喝", "做饭", "好吃", "味道", "料理"]
        hobby_kw = ["玩", "打", "看", "听", "画", "写"]
        game_kw = ["游戏", "番", "动漫", "电影", "音乐", "歌"]

        for kw in food_kw:
            if kw in context:
                return "food"
        for kw in game_kw:
            if kw in context:
                return "media"
        for kw in hobby_kw:
            if kw in context:
                return "hobby"
        return "thing"

    # ================================================================
    # 上下文注入
    # ================================================================

    def get_context_string(self, max_items: int = 15) -> str:
        """生成注入LLM上下文的图谱摘要。"""
        if not self._entities:
            return ""

        lines = ["[知识图谱]"]

        # 按重要关系排序
        top_relations = sorted(self._relations, key=lambda r: r.importance, reverse=True)

        seen = set()
        for r in top_relations[:max_items]:
            from_e = self.find_entity_by_id(r.from_id)
            to_e = self.find_entity_by_id(r.to_id)
            if from_e and to_e:
                key = f"{from_e.name}-{r.relation_type}-{to_e.name}"
                if key not in seen:
                    seen.add(key)
                    lines.append(f"  {from_e.name} --{r.relation_type}--> {to_e.name}")

        # 没有关系的重要实体
        for e in sorted(self._entities, key=lambda x: x.mention_count, reverse=True)[:5]:
            if e.name not in str(seen):
                attrs = ", ".join(f"{k}={v}" for k, v in list(e.attributes.items())[:3])
                if attrs:
                    lines.append(f"  {e.name}({ENTITY_TYPES.get(e.type, e.type)}): {attrs}")

        return "\n".join(lines) if len(lines) > 1 else ""

    def get_network_summary(self) -> str:
        """生成社交关系网络摘要。"""
        return self.get_context_string()

    # ================================================================
    # 统计
    # ================================================================

    @property
    def count(self) -> int:
        return len(self._entities)

    def get_stats(self) -> dict:
        by_type = {}
        for e in self._entities:
            by_type[e.type] = by_type.get(e.type, 0) + 1
        return {
            "total_entities": len(self._entities),
            "total_relations": len(self._relations),
            "by_type": by_type,
            "most_mentioned": [
                {"name": e.name, "count": e.mention_count, "type": e.type}
                for e in sorted(self._entities, key=lambda x: x.mention_count, reverse=True)[:5]
            ],
        }

    def get_all_entities(self) -> list[dict]:
        """获取所有实体（给API/前端用）。"""
        return [asdict(e) for e in self._entities]

    def get_all_relations(self) -> list[dict]:
        """获取所有关系（给API/前端用）。"""
        result = []
        for r in self._relations:
            d = asdict(r)
            from_e = self.find_entity_by_id(r.from_id)
            to_e = self.find_entity_by_id(r.to_id)
            d["from_name"] = from_e.name if from_e else "?"
            d["to_name"] = to_e.name if to_e else "?"
            result.append(d)
        return result

    # ================================================================
    # 持久化
    # ================================================================

    def _load(self) -> None:
        if self._json_path.exists():
            try:
                data = json.loads(self._json_path.read_text(encoding="utf-8"))
                self._entities = [Entity(**e) for e in data.get("entities", [])]
                self._relations = [Relation(**r) for r in data.get("relations", [])]
                self._rebuild_index()
            except Exception as e:
                logger.warning(f"[KG] 加载失败: {e}")

    def _save(self) -> None:
        try:
            data = {
                "entities": [asdict(e) for e in self._entities],
                "relations": [asdict(r) for r in self._relations],
            }
            self._json_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            logger.warning(f"[KG] 保存失败: {e}")

    def _rebuild_index(self) -> None:
        self._name_index.clear()
        for e in self._entities:
            self._name_index[e.name.lower()] = e.id

    # ================================================================
    # 数据迁移（从v1 SQLite自动迁移）
    # ================================================================

    def _migrate_from_sqlite(self) -> None:
        """自动迁移旧的SQLite数据到新的JSON格式。"""
        import sqlite3
        old_db = self._data_dir / "knowledge_graph.db"
        if not old_db.exists():
            return
        if self._entities:
            return  # 已有JSON数据，不重复迁移

        try:
            conn = sqlite3.connect(str(old_db))
            rows = conn.execute("SELECT * FROM persons ORDER BY mention_count DESC").fetchall()
            conn.close()

            if not rows:
                return

            migrated = 0
            for row in rows:
                name = row[1]
                relationship = row[2]
                role_label = row[3]
                traits = json.loads(row[4]) if row[4] else []
                mention_count = row[5]
                notes = row[8] if len(row) > 8 else ""

                attrs = {}
                if role_label:
                    attrs["role"] = role_label
                if traits:
                    attrs["traits"] = traits
                if notes:
                    attrs["notes"] = notes

                entity = self.add_entity(name, "person", attrs)
                entity.mention_count = mention_count

                # 添加与白的关系
                if relationship and relationship != "other":
                    rel_label = role_label or relationship
                    self.add_relation("白", rel_label, name, importance=60)

                migrated += 1

            if migrated:
                self._save()
                logger.info(f"[KG] 从SQLite迁移了 {migrated} 个人物到JSON图谱")

        except Exception as e:
            logger.debug(f"[KG] SQLite迁移跳过: {e}")

    # ================================================================
    # 1.4 LLM自动提取（对话后自动发现实体和关系）
    # ================================================================

    async def extract_with_llm(self, user_msg: str, ai_reply: str,
                               llm=None, user_name: str = "") -> list[str]:
        """
        用LLM从对话中提取实体和关系。

        Args:
            user_msg: 用户消息
            ai_reply: AI回复
            llm: LLM适配器（memory_llm）
            user_name: 用户名

        Returns:
            提取结果描述列表
        """
        if not llm:
            return self.extract_from_text(user_msg)

        # 先用正则快速提取
        results = self.extract_from_text(user_msg)

        # 再用LLM深度提取
        try:
            from white_salary.core.interfaces.types import Message, MessageRole

            # 构建已知实体列表（帮LLM识别已有人物）
            known = [e.name for e in self._entities[:30]]
            known_str = ", ".join(known) if known else "无"

            prompt = [
                Message(role=MessageRole.SYSTEM, content=(
                    "你是知识图谱提取专家。从对话中提取重要的实体和关系。\n"
                    "只提取对'白'重要的信息，跳过日常闲聊。\n"
                    f"已知实体: {known_str}\n\n"
                    "返回JSON数组，每项格式:\n"
                    '{"entity": "名字", "type": "person/food/hobby/event/...", '
                    '"relation_to": "白或其他实体", "relation": "关系描述", "importance": 50}\n'
                    "没有值得提取的返回空数组 []"
                )),
                Message(role=MessageRole.USER, content=(
                    f"用户({user_name or '用户'}): {user_msg}\n"
                    f"白: {ai_reply[:200]}"
                )),
            ]

            reply = await llm.chat_completion(prompt, temperature=0.2, max_tokens=500)

            # 解析JSON
            extracted = self._parse_extraction(reply)
            for item in extracted:
                name = item.get("entity", "")
                etype = item.get("type", "thing")
                relation_to = item.get("relation_to", "白")
                relation = item.get("relation", "")
                importance = float(item.get("importance", 50))

                if name and len(name) <= 20:
                    self.add_entity(name, etype)
                    if relation and relation_to:
                        self.add_relation(relation_to, relation, name,
                                          importance=importance)
                        results.append(f"图谱LLM:{name}({relation})")

        except Exception as e:
            logger.debug(f"[KG] LLM提取失败: {e}")

        return results

    def _parse_extraction(self, text: str) -> list[dict]:
        """从LLM回复中解析JSON数组。"""
        import re as _re
        # 尝试直接解析
        try:
            result = json.loads(text)
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass
        # 尝试提取[]块
        match = _re.search(r'\[[\s\S]*\]', text)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        return []

    # ================================================================
    # 1.5 自然语言查询
    # ================================================================

    async def query_natural(self, question: str, llm=None) -> str:
        """
        自然语言查询知识图谱。

        Args:
            question: "小白喜欢什么？" / "谁是白的家人？"
            llm: LLM适配器

        Returns:
            回答文本
        """
        # 构建相关的图谱上下文
        context = self._build_context_for_query(question)
        if not context:
            return "知识图谱中没有相关信息。"

        if not llm:
            return context  # 没有LLM就直接返回原始上下文

        try:
            from white_salary.core.interfaces.types import Message, MessageRole
            prompt = [
                Message(role=MessageRole.SYSTEM, content=(
                    "根据以下知识图谱信息回答问题。只用图谱中的信息，不要编造。\n"
                    f"图谱信息:\n{context}"
                )),
                Message(role=MessageRole.USER, content=question),
            ]
            reply = await llm.chat_completion(prompt, temperature=0.3, max_tokens=300)
            return reply
        except Exception as e:
            logger.debug(f"[KG] 自然语言查询失败: {e}")
            return context

    def _build_context_for_query(self, question: str) -> str:
        """根据问题关键词构建相关的图谱上下文。"""
        # 提取问题中的实体名
        matched_entities = []
        for e in self._entities:
            if e.name in question:
                matched_entities.append(e)

        if not matched_entities:
            # 没有直接匹配，搜所有跟"白"有关的
            bai = self.find_entity("白")
            if bai:
                matched_entities = [bai]

        if not matched_entities:
            return ""

        lines = []
        seen_relations = set()
        for entity in matched_entities:
            lines.append(f"{entity.name}({ENTITY_TYPES.get(entity.type, entity.type)})")
            attrs = ", ".join(f"{k}={v}" for k, v in list(entity.attributes.items())[:5])
            if attrs:
                lines.append(f"  属性: {attrs}")

            for r in self._relations:
                if r.from_id == entity.id or r.to_id == entity.id:
                    if r.id in seen_relations:
                        continue
                    seen_relations.add(r.id)
                    from_e = self.find_entity_by_id(r.from_id)
                    to_e = self.find_entity_by_id(r.to_id)
                    if from_e and to_e:
                        lines.append(f"  {from_e.name} --{r.relation_type}--> {to_e.name} (重要度{r.importance:.0f})")

        return "\n".join(lines)

    # ================================================================
    # 1.6 智能上下文选择
    # ================================================================

    def get_smart_context(self, message: str, max_relations: int = 10) -> str:
        """
        根据当前消息智能选择最相关的关系注入上下文。

        不是全量注入，而是根据消息内容选择语义相关的。
        """
        if not self._relations:
            return ""

        # 对每条关系计算与消息的相关度
        scored_relations = []
        for r in self._relations:
            from_e = self.find_entity_by_id(r.from_id)
            to_e = self.find_entity_by_id(r.to_id)
            if not from_e or not to_e:
                continue

            # 文本匹配评分
            score = r.importance / 100.0  # 基础分=重要度
            rel_text = f"{from_e.name} {r.relation_type} {to_e.name}"

            # 消息中提到了实体名 → 加分
            if from_e.name in message:
                score += 3.0
            if to_e.name in message:
                score += 3.0
            if r.relation_type in message:
                score += 1.0

            # 关键词匹配
            for attr_val in from_e.attributes.values():
                if isinstance(attr_val, str) and attr_val in message:
                    score += 1.0

            scored_relations.append((score, rel_text, r))

        # 按相关度排序，取top N
        scored_relations.sort(key=lambda x: x[0], reverse=True)
        top = scored_relations[:max_relations]

        if not top or top[0][0] < 0.5:
            return ""  # 没有相关的

        lines = ["[相关关系]"]
        for score, text, r in top:
            if score >= 0.5:
                lines.append(f"  {text}")

        return "\n".join(lines) if len(lines) > 1 else ""

    # ================================================================
    # 1.7 SiliconFlow嵌入重排序
    # ================================================================

    async def get_smart_context_with_embedding(
        self, message: str, max_relations: int = 10,
        sf_api_key: str = "",
    ) -> str:
        """
        用SiliconFlow的BAAI/bge-m3做语义重排序，选最相关的关系。

        比纯关键词匹配更精准。
        """
        if not self._relations or not sf_api_key:
            return self.get_smart_context(message, max_relations)

        try:
            import aiohttp

            # 构建所有关系的文本
            rel_texts = []
            rel_objects = []
            for r in self._relations:
                from_e = self.find_entity_by_id(r.from_id)
                to_e = self.find_entity_by_id(r.to_id)
                if from_e and to_e:
                    text = f"{from_e.name} {r.relation_type} {to_e.name}"
                    rel_texts.append(text)
                    rel_objects.append(r)

            if not rel_texts:
                return ""

            # 调SiliconFlow Embedding API
            all_texts = [message] + rel_texts
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://api.siliconflow.cn/v1/embeddings",
                    headers={
                        "Authorization": f"Bearer {sf_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": "BAAI/bge-m3",
                        "input": all_texts[:50],  # 最多50条（API限制）
                        "encoding_format": "float",
                    },
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status != 200:
                        logger.debug(f"[KG] Embedding API {resp.status}")
                        return self.get_smart_context(message, max_relations)

                    data = await resp.json()
                    embeddings = [d["embedding"] for d in data.get("data", [])]

            if len(embeddings) < 2:
                return self.get_smart_context(message, max_relations)

            # 计算余弦相似度
            query_emb = embeddings[0]
            scores = []
            for i, rel_emb in enumerate(embeddings[1:]):
                sim = self._cosine_sim(query_emb, rel_emb)
                scores.append((sim, rel_texts[i], rel_objects[i]))

            # 按相似度排序
            scores.sort(key=lambda x: x[0], reverse=True)
            top = scores[:max_relations]

            lines = ["[相关关系（语义匹配）]"]
            for sim, text, r in top:
                if sim > 0.3:  # 相似度阈值
                    lines.append(f"  {text}")

            return "\n".join(lines) if len(lines) > 1 else ""

        except Exception as e:
            logger.debug(f"[KG] Embedding重排序失败，回退关键词: {e}")
            return self.get_smart_context(message, max_relations)

    @staticmethod
    def _cosine_sim(a: list[float], b: list[float]) -> float:
        """计算余弦相似度。"""
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(x * x for x in b) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)
