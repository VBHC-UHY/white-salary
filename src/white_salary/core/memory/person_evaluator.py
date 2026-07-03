"""
white_salary/core/memory/person_evaluator.py

人物评估 — "你觉得XX怎么样？"

借鉴v2的person_evaluator.py：
  - 检测用户问白对某人的评价
  - 从知识图谱+对话日志+好感度综合评价
  - 用主模型组织语言回复

使用方式（通过工具调用）：
  用户: "你觉得华月姐姐怎么样？"
  → tool_llm判断需要evaluate_person
  → 收集图谱关系+历史对话+好感度
  → 交给主模型生成评价
"""

import re
from typing import Optional

from loguru import logger


# 检测"评价某人"的模式
_EVAL_PATTERNS = [
    r"你觉得\s*(.{1,10})\s*怎么样",
    r"你对\s*(.{1,10})\s*的?印象",
    r"你认为\s*(.{1,10})\s*这个人",
    r"(.{1,10})\s*是个怎样的人",
    r"评价一下\s*(.{1,10})",
    r"你喜欢\s*(.{1,10})\s*吗",
]

# 排除的代词
_EXCLUDE = {"我", "你", "他", "她", "它", "谁", "什么", "这个", "那个", "自己", "大家"}


class PersonEvaluator:
    """
    人物评估器。

    使用方式:
        evaluator = PersonEvaluator(knowledge_graph, conversation_log)
        result = evaluator.evaluate("华月姐姐")
        # → {"name": "华月姐姐", "relations": [...], "history": [...], "summary": "..."}
    """

    def __init__(self, knowledge_graph=None, conversation_log=None) -> None:
        self._kg = knowledge_graph
        self._log = conversation_log

    def detect_target(self, message: str) -> Optional[str]:
        """检测消息中是否在问对某人的评价。返回人名或None。"""
        for pattern in _EVAL_PATTERNS:
            match = re.search(pattern, message)
            if match:
                name = match.group(1).strip()
                if name and name not in _EXCLUDE and len(name) <= 10:
                    return name
        return None

    def evaluate(self, person_name: str) -> dict:
        """
        综合评价一个人。

        Returns:
            {
                "name": "华月姐姐",
                "found": True,
                "relations": ["白 --很重要的人--> 华月姐姐", ...],
                "attributes": {"key": "value"},
                "history_snippets": ["[03-29] 华月: 白你好", ...],
                "context": "完整的评价上下文（给主模型用）"
            }
        """
        result = {
            "name": person_name,
            "found": False,
            "relations": [],
            "attributes": {},
            "history_snippets": [],
            "context": "",
        }

        # 1. 从知识图谱获取关系
        if self._kg:
            entity = self._kg.find_entity(person_name)
            if entity:
                result["found"] = True
                result["attributes"] = entity.attributes

                rels = self._kg.get_relations_of(person_name)
                for r in rels[:10]:
                    if r.get("direction") == "out":
                        result["relations"].append(
                            f"{person_name} --{r['relation']}--> {r.get('target', '?')}"
                        )
                    else:
                        result["relations"].append(
                            f"{r.get('source', '?')} --{r['relation']}--> {person_name}"
                        )

        # 2. 从对话日志获取历史
        if self._log:
            try:
                entries = self._log.search(keyword=person_name, limit=5)
                for e in entries:
                    result["history_snippets"].append(
                        f"[{e.time_str}] {e.user_name}: {e.user_msg[:30]}"
                    )
            except Exception:
                pass

        # 3. 构建评价上下文
        parts = [f"关于{person_name}的信息："]

        if result["relations"]:
            parts.append("关系：")
            for r in result["relations"]:
                parts.append(f"  {r}")

        if result["attributes"]:
            attrs = ", ".join(f"{k}={v}" for k, v in list(result["attributes"].items())[:5])
            parts.append(f"属性：{attrs}")

        if result["history_snippets"]:
            parts.append("最近的对话：")
            for s in result["history_snippets"]:
                parts.append(f"  {s}")

        # 4. 好感度信息
        affinity_info = self._get_affinity_info(person_name)
        if affinity_info:
            parts.append(affinity_info)

        # 5. 情感印象
        impression_info = self._get_impression_info(person_name)
        if impression_info:
            parts.append(impression_info)

        if not result["found"] and not affinity_info:
            parts.append(f"（没有找到{person_name}的记录）")

        result["context"] = "\n".join(parts)
        return result

    def _get_affinity_info(self, person_name: str) -> str:
        """获取好感度信息。"""
        try:
            from white_salary.core.affinity.manager import AffinityManager
            # 尝试用名字找user_id（从知识图谱的属性里找）
            user_id = ""
            if self._kg:
                entity = self._kg.find_entity(person_name)
                if entity and entity.attributes:
                    user_id = entity.attributes.get("qq_id", "")
                    if not user_id:
                        user_id = entity.attributes.get("user_id", "")
            if not user_id:
                return ""

            aff = AffinityManager.get_for_user(user_id)
            stats = aff.get_stats()
            level_cn = stats.get("level_name", "")
            points = stats.get("points", 0)
            if level_cn:
                return f"好感度：{level_cn}（{points:.0f}分）"
        except Exception:
            pass
        return ""

    def _get_impression_info(self, person_name: str) -> str:
        """获取情感印象信息。"""
        try:
            from white_salary.core.memory.emotion_memory import EmotionMemoryStore
            store = EmotionMemoryStore()
            impressions = store.get_all_impressions()
            for uid, imp in impressions.items():
                if imp.get("name") == person_name:
                    w = imp.get("warmth", 0)
                    t = imp.get("trust", 0)
                    if w > 2:
                        return f"情感印象：挺喜欢这个人（温暖度{w:.0f}）"
                    elif w < -2:
                        return f"情感印象：对这个人有点反感（温暖度{w:.0f}）"
        except Exception:
            pass
        return ""
