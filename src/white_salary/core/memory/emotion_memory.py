"""
white_salary/core/memory/emotion_memory.py

人际情感印象 — 对每个人有独立的情感记忆。

借鉴v2的features/emotion_memory.py（853行）：
  - 不同于emotion_tracker（追踪AI当前心情）
  - 不同于enhanced/emotional.py（给记忆打情感标签）
  - 这个模块记录"我对XX的印象/感受"
  - 每个人独立的情感积分和印象历史
  - 影响AI对不同人的态度

不用LLM，纯规则+积分计算。

自动发现：导出MODULE供MemoryManager加载。
"""

import json
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional

from loguru import logger
from white_salary.core.memory.module_base import MemoryModule


@dataclass
class PersonImpression:
    """对某个人的情感印象。"""
    user_id: str = ""
    user_name: str = ""
    warmth: float = 0.0         # 温暖度 -10~+10（正=喜欢，负=反感）
    trust: float = 0.0          # 信任度 -10~+10
    fun: float = 0.0            # 有趣度 0~+10
    recent_events: list[dict] = field(default_factory=list)  # 最近的互动事件
    first_met: float = 0.0      # 第一次互动时间
    last_interact: float = 0.0  # 最近互动时间
    total_interactions: int = 0


# 情感事件类型→积分影响
_EVENT_EFFECTS = {
    # 正面
    "compliment": {"warmth": 1.5, "trust": 0.5, "fun": 0.3},     # 夸奖
    "comfort": {"warmth": 2.0, "trust": 1.0, "fun": 0.0},        # 安慰
    "share_secret": {"warmth": 1.0, "trust": 2.0, "fun": 0.0},   # 分享秘密
    "gift": {"warmth": 2.0, "trust": 0.5, "fun": 0.5},           # 送礼物
    "help": {"warmth": 1.5, "trust": 1.5, "fun": 0.0},           # 帮助
    "laugh": {"warmth": 0.5, "trust": 0.3, "fun": 1.5},          # 逗笑
    "agree": {"warmth": 0.5, "trust": 0.5, "fun": 0.0},          # 认同
    # 负面
    "insult": {"warmth": -2.0, "trust": -1.5, "fun": -0.5},      # 侮辱
    "lie": {"warmth": -1.0, "trust": -3.0, "fun": 0.0},          # 说谎
    "ignore": {"warmth": -1.0, "trust": -0.5, "fun": -0.5},      # 忽视
    "betray": {"warmth": -3.0, "trust": -3.0, "fun": -1.0},      # 背叛
    "anger": {"warmth": -1.5, "trust": -1.0, "fun": -1.0},       # 发火
    "tease": {"warmth": -0.3, "trust": 0.0, "fun": 0.5},         # 调侃（轻微负面但有趣）
}

# 检测关键词→事件类型
_DETECT_PATTERNS = {
    "compliment": ["好厉害", "真棒", "太强了", "厉害了", "好好看", "好漂亮", "真好", "太好了"],
    "comfort": ["没关系", "别难过", "会好的", "加油", "别担心", "我在"],
    "share_secret": ["秘密", "只告诉你", "别说出去", "悄悄话"],
    "gift": ["送你", "送给你", "礼物", "给你买"],
    "help": ["帮你", "帮忙", "教你", "给你看"],
    "laugh": ["哈哈", "笑死", "太搞笑", "逗死"],
    "agree": ["对的", "没错", "赞同", "你说得对", "确实"],
    "insult": ["白痴", "傻逼", "垃圾", "废物", "滚"],
    "lie": ["骗人", "骗我", "说谎", "假的"],
    "ignore": ["不理你", "不想说", "闭嘴", "别烦我"],
    "anger": ["生气", "发火", "怒了", "烦死", "受不了"],
    "tease": ["调侃", "逗你", "开玩笑", "皮一下"],
}

MAX_EVENTS_PER_PERSON = 50
MAX_PERSONS = 200


class EmotionMemoryStore:
    """
    人际情感印象存储。

    使用方式:
        store = EmotionMemoryStore(data_dir)
        store.on_interaction("user1", "小明", "你真棒！好厉害")
        impression = store.get_impression("user1")
        prompt = store.get_impression_prompt("user1")
    """

    def __init__(self, data_dir: str = "data/memory") -> None:
        self._data_path = Path(data_dir) / "emotion_impressions.json"
        self._data_path.parent.mkdir(parents=True, exist_ok=True)
        self._impressions: dict[str, PersonImpression] = {}
        self._load()

    def on_interaction(self, user_id: str, user_name: str, message: str) -> list[str]:
        """
        处理一次互动，检测情感事件并更新印象。

        Returns:
            检测到的事件类型列表
        """
        if not message or not user_id:
            return []

        # 获取或创建印象
        imp = self._get_or_create(user_id, user_name)
        imp.last_interact = time.time()
        imp.total_interactions += 1

        # 检测情感事件
        detected = []
        for event_type, keywords in _DETECT_PATTERNS.items():
            for kw in keywords:
                if kw in message:
                    detected.append(event_type)
                    self._apply_event(imp, event_type, message)
                    break

        if detected:
            self._save_debounced()

        return detected

    def get_impression(self, user_id: str) -> Optional[PersonImpression]:
        """获取对某人的印象。"""
        return self._impressions.get(user_id)

    def get_impression_prompt(self, user_id: str) -> str:
        """生成印象提示（注入对话上下文）。"""
        imp = self._impressions.get(user_id)
        if not imp or imp.total_interactions < 5:
            return ""  # 互动太少不注入

        parts = [f"[对{imp.user_name}的印象]"]

        # 温暖度
        if imp.warmth > 3:
            parts.append(f"好感: 很喜欢({imp.warmth:.0f})")
        elif imp.warmth > 1:
            parts.append(f"好感: 有好感({imp.warmth:.0f})")
        elif imp.warmth < -3:
            parts.append(f"好感: 很反感({imp.warmth:.0f})")
        elif imp.warmth < -1:
            parts.append(f"好感: 有点反感({imp.warmth:.0f})")

        # 信任度
        if imp.trust > 3:
            parts.append("信任: 非常信任")
        elif imp.trust < -2:
            parts.append("信任: 不太信任")

        # 有趣度
        if imp.fun > 3:
            parts.append("印象: 很有趣的人")

        # 最近事件
        recent = imp.recent_events[-3:]
        if recent:
            events_str = "、".join(e.get("type_cn", "") for e in recent if e.get("type_cn"))
            if events_str:
                parts.append(f"最近: {events_str}")

        return "\n".join(parts) if len(parts) > 1 else ""

    def get_all_impressions(self) -> dict[str, dict]:
        """获取所有人的印象概要。"""
        result = {}
        for uid, imp in self._impressions.items():
            result[uid] = {
                "name": imp.user_name,
                "warmth": round(imp.warmth, 1),
                "trust": round(imp.trust, 1),
                "fun": round(imp.fun, 1),
                "interactions": imp.total_interactions,
            }
        return result

    # ================================================================
    # 内部
    # ================================================================

    def _get_or_create(self, user_id: str, user_name: str) -> PersonImpression:
        if user_id not in self._impressions:
            self._impressions[user_id] = PersonImpression(
                user_id=user_id,
                user_name=user_name,
                first_met=time.time(),
                last_interact=time.time(),
            )
        imp = self._impressions[user_id]
        if user_name:
            imp.user_name = user_name
        return imp

    def _apply_event(self, imp: PersonImpression, event_type: str, message: str) -> None:
        """应用情感事件到印象。"""
        effects = _EVENT_EFFECTS.get(event_type, {})

        imp.warmth = max(-10, min(10, imp.warmth + effects.get("warmth", 0)))
        imp.trust = max(-10, min(10, imp.trust + effects.get("trust", 0)))
        imp.fun = max(0, min(10, imp.fun + effects.get("fun", 0)))

        # 事件类型中文名
        type_cn_map = {
            "compliment": "夸奖", "comfort": "安慰", "share_secret": "分享秘密",
            "gift": "送礼物", "help": "帮助", "laugh": "逗笑", "agree": "认同",
            "insult": "侮辱", "lie": "说谎", "ignore": "忽视",
            "betray": "背叛", "anger": "发火", "tease": "调侃",
        }

        imp.recent_events.append({
            "type": event_type,
            "type_cn": type_cn_map.get(event_type, event_type),
            "message": message[:50],
            "time": time.time(),
        })
        if len(imp.recent_events) > MAX_EVENTS_PER_PERSON:
            imp.recent_events = imp.recent_events[-MAX_EVENTS_PER_PERSON:]

    # ================================================================
    # 持久化
    # ================================================================

    _save_counter = 0

    def _save_debounced(self) -> None:
        self._save_counter += 1
        if self._save_counter % 10 == 0:
            self._save()

    def _save(self) -> None:
        try:
            data = {uid: asdict(imp) for uid, imp in self._impressions.items()}
            self._data_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as e:
            logger.warning(f"[EmotionMem] 保存失败: {e}")

    def _load(self) -> None:
        if not self._data_path.exists():
            return
        try:
            data = json.loads(self._data_path.read_text(encoding="utf-8"))
            for uid, d in data.items():
                self._impressions[uid] = PersonImpression(**d)
            logger.debug(f"[EmotionMem] 加载: {len(self._impressions)}人的印象")
        except Exception as e:
            logger.warning(f"[EmotionMem] 加载失败: {e}")

    def force_save(self) -> None:
        self._save()

    @property
    def stats(self) -> dict:
        return {
            "total_persons": len(self._impressions),
            "avg_warmth": round(
                sum(i.warmth for i in self._impressions.values()) / max(len(self._impressions), 1), 1
            ),
        }


# ================================================================
# 自动发现接口
# ================================================================

class EmotionMemoryModule(MemoryModule):
    """人际情感印象模块 — 自动发现注册。"""
    name = "emotion_memory_person"

    def init(self, data_dir="data/memory", **kwargs):
        self._impl = EmotionMemoryStore(data_dir=data_dir)

    def get_context_prompt(self, message: str = "", user_id: str = "desktop", is_group: bool = False) -> str:
        if not hasattr(self, '_impl'):
            return ""
        # 用真实用户ID（QQ多用户场景区分每个人；桌面端默认 desktop）
        return self._impl.get_impression_prompt(user_id)

    def on_message(self, user_msg: str = "", ai_reply: str = "", user_id: str = "desktop", is_group: bool = False) -> None:
        if user_msg and hasattr(self, '_impl'):
            self._impl.on_interaction(user_id, "用户", user_msg)

    def on_session_end(self) -> None:
        if hasattr(self, '_impl'):
            self._impl.force_save()


MODULE = EmotionMemoryModule
