"""
white_salary/core/memory/expression_learner.py

表达学习器 — 学习每个群友/用户的说话风格。

借鉴v2的features/expression_learner.py（765行）：
  - 按user_id收集消息样本
  - memory_llm分析词汇/语气/习惯（异步后台）
  - 风格画像存储，每个用户独立JSON
  - 每100条消息或7天触发一次分析

LLM通道：memory_llm（异步后台，不阻塞对话）

自动发现：导出MODULE供MemoryManager加载。
"""

import asyncio
import json
import time
from pathlib import Path
from typing import Optional

from loguru import logger
from white_salary.core.memory.module_base import MemoryModule


# 触发阈值
MESSAGE_THRESHOLD = 100
DAY_THRESHOLD = 7
SAMPLE_SIZE = 50  # 分析时取最近50条

# LLM分析提示词
_ANALYZE_PROMPT = """分析以下用户的说话风格特征。只返回JSON。

用户昵称: {user_name}
消息样本:
{messages}

请分析以下维度：
{{
  "vocabulary": ["常用词汇/口头禅"],
  "tone": "整体语气(活泼/沉稳/可爱/正式等)",
  "habits": ["说话习惯(如: 喜欢用~结尾, 爱用表情包等)"],
  "sentence_length": "平均句子长度(短/中/长)",
  "emoji_usage": "表情使用频率(多/少/无)",
  "topics": ["经常聊的话题"]
}}

只返回JSON，不要其他文字。"""


class ExpressionLearner:
    """
    表达学习器。

    使用方式:
        learner = ExpressionLearner(data_dir="data/memory")
        learner.on_user_message("user_123", "小明", "哈哈今天好开心~")
        if learner.should_analyze("user_123"):
            await learner.analyze("user_123", llm)
        style = learner.get_style("user_123")
    """

    def __init__(self, data_dir: str = "data/memory") -> None:
        self._styles_dir = Path(data_dir) / "expression_styles"
        self._styles_dir.mkdir(parents=True, exist_ok=True)
        self._state_path = Path(data_dir) / "expression_learner_state.json"

        # {user_id: {"messages": [], "count": int, "last_analyze": float, "name": str}}
        self._users: dict[str, dict] = {}
        # {user_id: style_dict}
        self._styles: dict[str, dict] = {}

        self._load()

    def on_user_message(self, user_id: str, user_name: str, message: str) -> None:
        """记录用户消息。"""
        if not message or len(message) < 3:
            return

        if user_id not in self._users:
            self._users[user_id] = {
                "messages": [], "count": 0,
                "last_analyze": 0.0, "name": user_name,
            }

        state = self._users[user_id]
        state["messages"].append(message[:200])
        state["count"] += 1
        state["name"] = user_name or state["name"]

        # 限制缓冲
        if len(state["messages"]) > 200:
            state["messages"] = state["messages"][-200:]

    def should_analyze(self, user_id: str) -> bool:
        """检查是否需要触发分析（好感度高的用户更容易触发）。"""
        state = self._users.get(user_id)
        if not state:
            return False

        # 好感度调整触发阈值（好感高→更早触发学习）
        threshold = MESSAGE_THRESHOLD
        try:
            from white_salary.core.affinity.manager import AffinityManager
            aff = AffinityManager.get_for_user(user_id)
            stats = aff.get_stats()
            lv = stats.get("level_value", 0)
            if stats.get("is_family") or lv >= 4:
                threshold = 50  # 家人/挚友：50条就分析
            elif lv >= 2:
                threshold = 70  # 朋友：70条
            # 反感的人不学习风格
            if lv <= -2:
                return False
        except Exception:
            pass

        if state["count"] >= threshold:
            return True

        if state["last_analyze"] > 0:
            days = (time.time() - state["last_analyze"]) / 86400
            if days >= DAY_THRESHOLD and len(state["messages"]) >= 30:
                return True

        if state["last_analyze"] == 0 and len(state["messages"]) >= 20:
            return True

        return False

    async def analyze(self, user_id: str, llm=None) -> Optional[dict]:
        """用memory_llm分析用户风格。"""
        state = self._users.get(user_id)
        if not state or not llm:
            return None

        name = state["name"] or user_id
        sample = state["messages"][-SAMPLE_SIZE:]
        messages_text = "\n".join(f"- {m}" for m in sample)

        try:
            from white_salary.core.interfaces.types import Message, MessageRole
            prompt = _ANALYZE_PROMPT.format(
                user_name=name, messages=messages_text,
            )
            reply = await llm.chat_completion(
                [
                    Message(role=MessageRole.SYSTEM, content="你是语言风格分析专家。只返回JSON。"),
                    Message(role=MessageRole.USER, content=prompt),
                ],
                temperature=0.3,
                max_tokens=500,
            )

            style = self._parse_json(reply)
            if style:
                style["user_name"] = name
                style["user_id"] = user_id
                style["analyzed_at"] = time.strftime("%Y-%m-%d %H:%M")
                style["sample_count"] = len(sample)

                self._styles[user_id] = style
                state["count"] = 0
                state["last_analyze"] = time.time()
                self._save_style(user_id, style)
                self._save_state()
                logger.info(f"[ExprLearner] {name} 风格分析完成")
                return style

        except Exception as e:
            logger.error(f"[ExprLearner] {name} 分析失败: {e}")
        return None

    def get_style(self, user_id: str) -> dict:
        """获取用户的风格画像。"""
        return self._styles.get(user_id, {}).copy()

    def get_style_prompt(self, user_id: str) -> str:
        """生成风格提示（可注入对话上下文）。"""
        style = self._styles.get(user_id)
        if not style:
            return ""
        name = style.get("user_name", "用户")
        parts = [f"[{name}的说话风格]"]
        if style.get("tone"):
            parts.append(f"语气: {style['tone']}")
        if style.get("vocabulary"):
            parts.append(f"常用词: {', '.join(style['vocabulary'][:5])}")
        if style.get("habits"):
            parts.append(f"习惯: {', '.join(style['habits'][:3])}")
        return "\n".join(parts) if len(parts) > 1 else ""

    # ================================================================
    # 内部
    # ================================================================

    def _parse_json(self, text: str) -> Optional[dict]:
        import re
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        match = re.search(r'\{[\s\S]*\}', text)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        return None

    def _save_style(self, user_id: str, style: dict) -> None:
        try:
            path = self._styles_dir / f"{user_id}.json"
            path.write_text(json.dumps(style, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _save_state(self) -> None:
        try:
            data = {}
            for uid, state in self._users.items():
                data[uid] = {
                    "count": state["count"],
                    "last_analyze": state["last_analyze"],
                    "name": state["name"],
                    "msg_count": len(state["messages"]),
                }
            self._state_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def _load(self) -> None:
        # 加载状态
        if self._state_path.exists():
            try:
                data = json.loads(self._state_path.read_text(encoding="utf-8"))
                for uid, d in data.items():
                    self._users[uid] = {
                        "messages": [], "count": d.get("count", 0),
                        "last_analyze": d.get("last_analyze", 0.0),
                        "name": d.get("name", ""),
                    }
            except Exception:
                pass
        # 加载风格
        for f in self._styles_dir.glob("*.json"):
            try:
                self._styles[f.stem] = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                pass


# ================================================================
# 自动发现接口
# ================================================================

class ExpressionLearnerModule(MemoryModule):
    name = "expression_learner"

    def init(self, data_dir: str = "data/memory", **kwargs) -> None:
        self._impl = ExpressionLearner(data_dir=data_dir)

    def on_message(self, user_msg: str = "", ai_reply: str = "",
                   user_id: str = "desktop",
                   is_group: bool = False) -> None:
        """
        2026-07-02 审计修复（批4）：BUG2残留——旧签名不收user_id，
        manager新kwargs调用先抛TypeError再回退旧调用，所有平台用户的
        消息全记到"desktop"名下（表达风格串用户）。改新签名透传真实
        user_id（参照emotion_memory.py:284已修写法），走manager新签名路径。
        """
        if not user_msg or not hasattr(self, '_impl'):
            return
        self._impl.on_user_message(user_id, "用户", user_msg)


MODULE = ExpressionLearnerModule
