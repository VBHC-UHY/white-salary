"""
white_salary/core/services/preference_learning.py

偏好学习服务 — 从记忆痕迹中学习深层偏好。

借鉴v2的services/preference_learning.py（319行）：
  - 不只是"喜欢X"，而是"因为Y所以喜欢X"
  - 按维度分类：食物/音乐/游戏/社交/学习等
  - 从对话历史中提取偏好模式
  - memory_llm分析深层原因（异步后台）
  - 偏好数据补充到用户画像

LLM通道：memory_llm
"""

import json
import re
import time
from pathlib import Path
from typing import Optional

from loguru import logger


# 偏好维度
PREFERENCE_DIMENSIONS = [
    "food", "music", "game", "movie", "book",
    "social", "hobby", "style", "schedule", "other",
]

# 偏好检测关键词
_LIKE_PATTERNS = [
    (r"(?:我|人家)(?:最)?喜欢(.{2,10})", "like"),
    (r"(?:我|人家)(?:最)?爱(.{2,8})", "like"),
    (r"(.{2,8})(?:最好|太好|超好|好好)", "like"),
    (r"(?:我|人家)讨厌(.{2,10})", "dislike"),
    (r"(?:我|人家)不喜欢(.{2,10})", "dislike"),
    (r"(.{2,8})(?:太难吃|太难看|太无聊|太烦)", "dislike"),
]

# LLM分析提示
_ANALYZE_PROMPT = """从以下偏好记录中分析深层原因。只返回JSON。

偏好记录:
{records}

请分析每条偏好背后的深层原因，返回:
[
  {{"item": "喜欢的东西", "type": "like/dislike", "reason": "深层原因", "dimension": "维度(food/music/game/movie/social/hobby/other)"}}
]

只返回JSON数组。"""


class PreferenceLearningService:
    """
    偏好学习服务。

    使用方式:
        service = PreferenceLearningService(data_dir)
        service.on_message("我最喜欢吃火锅因为热闹")
        prefs = service.get_preferences()
    """

    def __init__(self, data_dir: str = "data/memory") -> None:
        self._data_path = Path(data_dir) / "preferences.json"
        self._data_path.parent.mkdir(parents=True, exist_ok=True)

        # {item: {"type": like/dislike, "reason": str, "dimension": str,
        #         "context": str, "count": int, "first_seen": float}}
        self._preferences: dict[str, dict] = {}
        # 待分析的原始偏好（LLM分析前）
        self._raw_records: list[dict] = []

        self._compiled_patterns = [(re.compile(p), t) for p, t in _LIKE_PATTERNS]
        self._load()

    def on_message(self, text: str) -> list[dict]:
        """从消息中提取偏好。返回检测到的偏好列表。"""
        if not text or len(text) < 5:
            return []

        detected = []
        for pattern, pref_type in self._compiled_patterns:
            match = pattern.search(text)
            if match:
                item = match.group(1).strip()
                if item and 2 <= len(item) <= 15:
                    pref = self._record_preference(item, pref_type, text)
                    if pref:
                        detected.append(pref)

        return detected

    def _record_preference(self, item: str, pref_type: str,
                           context: str) -> Optional[dict]:
        """记录一条偏好。"""
        key = item.lower()

        if key in self._preferences:
            self._preferences[key]["count"] += 1
            return None  # 已知偏好不重复返回

        pref = {
            "item": item,
            "type": pref_type,
            "reason": "",  # 待LLM分析
            "dimension": self._guess_dimension(item, context),
            "context": context[:100],
            "count": 1,
            "first_seen": time.time(),
        }
        self._preferences[key] = pref
        self._raw_records.append({"item": item, "type": pref_type, "context": context[:100]})
        self._save()
        return pref

    def _guess_dimension(self, item: str, context: str) -> str:
        """简单猜测偏好维度。"""
        text = item + context
        dim_keywords = {
            "food": ["吃", "喝", "美食", "菜", "饭", "奶茶", "蛋糕", "火锅"],
            "music": ["歌", "音乐", "听", "唱"],
            "game": ["游戏", "玩", "打", "段位"],
            "movie": ["电影", "看", "剧", "番"],
            "book": ["书", "读", "小说"],
            "social": ["聊天", "朋友", "一起", "约"],
            "hobby": ["画", "跑步", "运动", "旅行"],
        }
        for dim, keywords in dim_keywords.items():
            for kw in keywords:
                if kw in text:
                    return dim
        return "other"

    async def analyze_deep_reasons(self, llm=None) -> int:
        """用memory_llm分析偏好的深层原因。"""
        if not self._raw_records or not llm:
            return 0

        records_text = "\n".join(
            f"- {r['type']}: {r['item']}（上下文：{r['context']}）"
            for r in self._raw_records[:15]
        )

        try:
            from white_salary.core.interfaces.types import Message, MessageRole
            prompt = _ANALYZE_PROMPT.format(records=records_text)
            reply = await llm.chat_completion(
                [
                    Message(role=MessageRole.SYSTEM, content="你是偏好分析专家。只返回JSON。"),
                    Message(role=MessageRole.USER, content=prompt),
                ],
                temperature=0.3,
                max_tokens=500,
            )

            results = self._parse_json_array(reply)
            updated = 0
            for r in results:
                item = r.get("item", "").lower()
                if item in self._preferences and r.get("reason"):
                    self._preferences[item]["reason"] = r["reason"]
                    if r.get("dimension"):
                        self._preferences[item]["dimension"] = r["dimension"]
                    updated += 1

            self._raw_records.clear()
            self._save()
            if updated:
                logger.info(f"[PrefLearn] 分析了{updated}条偏好的深层原因")
            return updated

        except Exception as e:
            logger.error(f"[PrefLearn] 分析失败: {e}")
            return 0

    def get_preferences(self, pref_type: str = "") -> list[dict]:
        """获取偏好列表。"""
        prefs = list(self._preferences.values())
        if pref_type:
            prefs = [p for p in prefs if p["type"] == pref_type]
        prefs.sort(key=lambda p: p.get("count", 0), reverse=True)
        return prefs

    def get_preference_prompt(self) -> str:
        """生成偏好提示（可注入对话）。"""
        likes = self.get_preferences("like")[:5]
        dislikes = self.get_preferences("dislike")[:3]
        if not likes and not dislikes:
            return ""

        parts = ["[用户偏好]"]
        for p in likes:
            reason = f"（{p['reason']}）" if p.get("reason") else ""
            parts.append(f"  喜欢: {p['item']}{reason}")
        for p in dislikes:
            reason = f"（{p['reason']}）" if p.get("reason") else ""
            parts.append(f"  不喜欢: {p['item']}{reason}")
        return "\n".join(parts)

    def _parse_json_array(self, text: str) -> list:
        try:
            r = json.loads(text)
            return r if isinstance(r, list) else []
        except json.JSONDecodeError:
            pass
        match = re.search(r'\[[\s\S]*\]', text)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        return []

    def _save(self) -> None:
        try:
            self._data_path.write_text(
                json.dumps(self._preferences, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass

    def _load(self) -> None:
        if self._data_path.exists():
            try:
                self._preferences = json.loads(
                    self._data_path.read_text(encoding="utf-8")
                )
            except Exception:
                pass

    @property
    def stats(self) -> dict:
        likes = sum(1 for p in self._preferences.values() if p["type"] == "like")
        dislikes = sum(1 for p in self._preferences.values() if p["type"] == "dislike")
        return {"total": len(self._preferences), "likes": likes, "dislikes": dislikes}
