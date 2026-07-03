"""
white_salary/core/memory/affinity_persona.py

好感度人设语气 — 根据好感度自动切换说话方式。

借鉴v2的features/persona_context.py：
  - 5种语气模式：亲密/友好/礼貌/疏远/冷淡
  - 根据好感度自动切换
  - 家人有专属模式
  - 注入到system prompt影响主模型说话方式

不用主对话LLM，纯数据读取+提示注入。

自动发现：导出MODULE供MemoryManager加载。
"""

from typing import Optional

from loguru import logger
from white_salary.core.memory.module_base import MemoryModule


def _load_prompt_templates():
    """从config/prompt_templates.json加载模板（每次调用实时读取）。"""
    import json
    from pathlib import Path
    try:
        path = Path("config/prompt_templates.json")
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _get_persona_modes() -> dict:
    """从配置文件读取5种语气模式（GUI可编辑）。"""
    templates = _load_prompt_templates()
    persona_cfg = templates.get("affinity_persona", {})

    # 默认值（配置文件没有时用）
    defaults = {
        "family": "对方是你最亲的家人。说话随意自然，可以吐槽、耍赖、发脾气、说心里话。",
        "intimate": "对方是你很亲近的人。说话轻松自然，可以开玩笑、吐槽、分享心事。",
        "friendly": "对方是朋友。说话友好但保持一定分寸，可以开轻松的玩笑。",
        "polite": "对方是不太熟的人。说话正常礼貌，不冷不热。回答问题但不会主动展开话题。",
        "cold": "你不喜欢这个人。说话简短冷淡，不想多聊。回答用最少的字，可以敷衍。",
    }

    labels = {"family": "家人", "intimate": "亲密", "friendly": "友好", "polite": "礼貌", "cold": "冷淡"}
    min_levels = {"family": 99, "intimate": 4, "friendly": 2, "polite": 0, "cold": -99}

    modes = {}
    for key in ["family", "intimate", "friendly", "polite", "cold"]:
        text = persona_cfg.get(key, defaults[key])
        modes[key] = {
            "min_level": min_levels[key],
            "label": labels[key],
            "prompt": f"[语气模式：{labels[key]}]\n{text}",
        }
    return modes


# 5种语气模式（从配置文件读取，GUI可编辑）
PERSONA_MODES = _get_persona_modes()


class AffinityPersona:
    """好感度人设语气切换器。"""

    def __init__(self) -> None:
        self._cache: dict[str, str] = {}  # user_id → mode_name

    def get_persona_prompt(self, user_id: str = "desktop") -> str:
        """
        根据好感度返回语气模式提示。

        Args:
            user_id: 用户ID

        Returns:
            语气模式提示文本
        """
        level_value = self._get_level(user_id)

        # 每次实时读配置（GUI改了马上生效）
        modes = _get_persona_modes()
        mode = self._select_mode(level_value, modes)
        if not mode:
            return ""

        self._cache[user_id] = mode["label"]
        return mode["prompt"]

    def get_current_mode(self, user_id: str = "desktop") -> str:
        """获取当前语气模式名。"""
        return self._cache.get(user_id, "礼貌")

    def _select_mode(self, level_value: int, modes: dict = None) -> Optional[dict]:
        """根据好感度等级选择模式。"""
        m = modes or PERSONA_MODES
        if level_value == 99:
            return m.get("family")
        if level_value >= 4:
            return m.get("intimate")
        elif level_value >= 2:
            return m.get("friendly")
        elif level_value >= 0:
            return m.get("polite")
        else:
            return m.get("cold")

    def _get_level(self, user_id: str) -> int:
        """获取好感度等级。"""
        try:
            from white_salary.core.affinity.manager import AffinityManager
            aff = AffinityManager.get_for_user(user_id)
            stats = aff.get_stats()
            if stats.get("is_family"):
                return 99
            return stats.get("level_value", 0)
        except Exception:
            return 0


# ================================================================
# 自动发现接口
# ================================================================

class AffinityPersonaModule(MemoryModule):
    """好感度人设语气模块 — 自动发现注册。"""
    name = "affinity_persona"

    def init(self, data_dir="data/memory", **kwargs):
        self._impl = AffinityPersona()

    def get_context_prompt(self, message: str = "",
                          user_id: str = "desktop",
                          is_group: bool = False) -> str:
        if not hasattr(self, '_impl'):
            return ""
        return self._impl.get_persona_prompt(user_id)


MODULE = AffinityPersonaModule
