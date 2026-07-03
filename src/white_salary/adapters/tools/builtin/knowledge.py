"""知识图谱工具 — 查询/添加/修改/删除实体和关系 + 人物评估 + 路径查询。"""
import asyncio
from ._helpers import tool, P, S, I, NONE_PARAMS


def _get_kg():
    """获取知识图谱单例。"""
    from white_salary.core.memory.knowledge_graph import KnowledgeGraph
    return KnowledgeGraph(data_dir="data/memory")


def _get_evaluator():
    """获取人物评估器。"""
    from white_salary.core.memory.person_evaluator import PersonEvaluator
    from white_salary.core.memory.conversation_log import ConversationLog
    kg = _get_kg()
    log = ConversationLog.get_instance(data_dir="data/memory")
    return PersonEvaluator(knowledge_graph=kg, conversation_log=log)


@tool("query_knowledge_graph",
      "查询知识图谱中的人物关系信息。当用户问'XX是谁'、'白认识谁'、'XX和XX什么关系'时使用。",
      P(question=S("自然语言问题", True)))
async def query_knowledge_graph(question: str = "") -> str:
    if not question:
        return "请输入问题"
    kg = _get_kg()
    # 先尝试直接查实体
    for e in kg._entities:
        if e.name in question:
            rels = kg.get_relations_of(e.name)
            if rels:
                lines = [f"{e.name} ({e.type}):"]
                for r in rels[:8]:
                    if r.get("direction") == "out":
                        lines.append(f"  → {r['relation']} → {r.get('target', '?')}")
                    else:
                        lines.append(f"  ← {r['relation']} ← {r.get('source', '?')}")
                attrs = ", ".join(f"{k}={v}" for k, v in list(e.attributes.items())[:5])
                if attrs:
                    lines.append(f"  属性: {attrs}")
                return "\n".join(lines)
    # 没有直接匹配，返回整体摘要
    ctx = kg._build_context_for_query(question)
    return ctx if ctx else "知识图谱中没有找到相关信息"


@tool("add_knowledge",
      "往知识图谱中添加新的实体或关系。当用户告诉白新的人物关系或信息时使用。",
      P(entity_name=S("实体名称", True),
        entity_type=S("类型:person/food/hobby/event/concept/group/skill/media/thing"),
        relation_from=S("关系起点（如'白'）"),
        relation_type=S("关系类型（如'朋友'、'喜欢'）"),
        attributes=S("属性JSON（如'{\"qq\":\"123\"}'）")))
async def add_knowledge(entity_name: str = "", entity_type: str = "person",
                        relation_from: str = "", relation_type: str = "",
                        attributes: str = "") -> str:
    if not entity_name:
        return "请提供实体名称"
    kg = _get_kg()

    # 解析属性
    attrs = {}
    if attributes:
        try:
            import json
            attrs = json.loads(attributes)
        except Exception:
            attrs = {"note": attributes}

    # 添加实体
    entity = kg.add_entity(entity_name, entity_type, attrs)

    # 添加关系
    result = f"已添加实体: {entity_name} ({entity_type})"
    if relation_from and relation_type:
        kg.add_relation(relation_from, relation_type, entity_name,
                        importance=60, from_type="person", to_type=entity_type)
        result += f"\n已添加关系: {relation_from} --{relation_type}--> {entity_name}"

    return result


@tool("update_knowledge",
      "修改知识图谱中已有的实体信息。",
      P(entity_name=S("实体名称", True),
        new_type=S("新类型"),
        new_attributes=S("新属性JSON")))
async def update_knowledge(entity_name: str = "", new_type: str = "",
                           new_attributes: str = "") -> str:
    if not entity_name:
        return "请提供实体名称"
    kg = _get_kg()
    entity = kg.find_entity(entity_name)
    if not entity:
        return f"未找到实体: {entity_name}"

    attrs = None
    if new_attributes:
        try:
            import json
            attrs = json.loads(new_attributes)
        except Exception:
            attrs = {"note": new_attributes}

    kg.update_entity(entity.id, entity_type=new_type or None, attributes=attrs)
    return f"已更新: {entity_name}"


@tool("delete_knowledge",
      "从知识图谱中删除实体（会同时删除相关的所有关系）。",
      P(entity_name=S("要删除的实体名称", True)))
async def delete_knowledge(entity_name: str = "") -> str:
    if not entity_name:
        return "请提供实体名称"
    kg = _get_kg()
    entity = kg.find_entity(entity_name)
    if not entity:
        return f"未找到: {entity_name}"
    rels = kg.get_relations_of(entity_name)
    kg.delete_entity(entity.id)
    return f"已删除 {entity_name} 及其 {len(rels)} 条关系"


@tool("evaluate_person",
      "评价某个人。当用户问'你觉得XX怎么样'、'你对XX的印象'时使用。",
      P(person_name=S("人名", True)))
async def evaluate_person(person_name: str = "") -> str:
    if not person_name:
        return "请提供人名"
    evaluator = _get_evaluator()
    result = evaluator.evaluate(person_name)
    return result["context"]


@tool("path_query",
      "查询知识图谱中的关系路径。如'小白的朋友都有谁'、'华月和谁有关系'。",
      P(start_name=S("起点实体名称", True),
        relation_filter=S("关系类型过滤（留空=全部）"),
        max_depth=I("最大搜索深度（默认2）")))
async def path_query(start_name: str = "", relation_filter: str = "",
                     max_depth: int = 2) -> str:
    if not start_name:
        return "请提供起点名称"
    kg = _get_kg()

    entity = kg.find_entity(start_name)
    if not entity:
        return f"未找到: {start_name}"

    paths = kg.query_path(start_name, relation_filter, max_depth)
    if not paths:
        filter_str = f"（关系类型: {relation_filter}）" if relation_filter else ""
        return f"{start_name} 没有找到相关路径{filter_str}"

    lines = [f"{start_name} 的关系路径（{len(paths)}条）:"]
    for p in paths[:15]:
        lines.append(f"  {' → '.join(p)}")

    return "\n".join(lines)


TOOLS = [fn._tool_def for fn in [
    query_knowledge_graph, add_knowledge, update_knowledge,
    delete_knowledge, evaluate_person, path_query,
]]
