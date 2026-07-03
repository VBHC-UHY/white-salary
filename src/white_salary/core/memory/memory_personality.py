"""
white_salary/core/memory/memory_personality.py

基于记忆的人格一致性 — 确保白的回复与之前记住的信息一致。

功能：
  - 提取核心记忆中关于白自身的记录
  - 提取关于用户的关键记录
  - 防止自相矛盾（比如之前说喜欢猫，这次说不喜欢）
"""

from typing import Optional
from loguru import logger


from white_salary.core.memory.module_base import MemoryModule


class MemoryPersonalityModule(MemoryModule):
    name = "memory_personality"

    def init(self, data_dir: str = "data/memory", **kwargs) -> None:
        core = kwargs.get("core_store")
        self._impl = MemoryPersonality(core)

    def get_context_prompt(self, message: str = "",
                           user_id: str = "desktop",
                           is_group: bool = False) -> str:
        """
        2026-07-02 审计修复（批4）：改新签名接收user_id。本模块修复后开始
        真正产出"人格一致性"注入，其中包含核心档案里的用户事实——只对主人
        注入，避免主人隐私进入QQ陌生人的system prompt（隐私优先）。
        """
        if not hasattr(self, '_impl'):
            return ""
        try:
            from white_salary.core.memory.manager import is_owner_user
            if not is_owner_user(user_id):
                return ""
        except Exception as e:
            logger.warning(f"[MemPersona] 主人身份判定失败，按非主人处理不注入: {e}")
            return ""
        return self._impl.get_consistency_prompt()


MODULE = MemoryPersonalityModule


class MemoryPersonality:
    """
    基于记忆的人格一致性。

    使用方式:
        mp = MemoryPersonality(core_store)
        prompt = mp.get_consistency_prompt()
        # → "[人格一致性] 你喜欢编程; 你讨厌无聊; 用户叫小白..."
    """

    # 关于白自身的记忆key前缀
    SELF_PREFIXES = ["self_", "white_", "my_"]
    # 关于用户的关键类别
    USER_CATEGORIES = ["basic_info", "preference", "relationship", "important"]

    def __init__(self, core_store=None) -> None:
        self._core = core_store

    def get_consistency_prompt(self, max_items: int = 10) -> str:
        """
        生成人格一致性提示。

        Returns:
            注入system prompt的提示文本
        """
        if not self._core or not hasattr(self._core, '_cache'):
            return ""

        self_facts = []
        user_facts = []

        for key, entry in self._core._cache.items():
            # 2026-07-02 审计修复（批4）：_cache的值是CoreMemoryEntry(dataclass)，
            # 原代码当dict调.get()必然AttributeError（异常被manager的回退分支吞掉，
            # 本模块从未成功产出过注入）。改用getattr按属性读取。
            value = str(getattr(entry, "value", "") or "")
            category = str(getattr(entry, "category", "") or "")

            if not value:
                continue

            # 白自身的记忆
            if any(key.startswith(p) for p in self.SELF_PREFIXES):
                self_facts.append(value)
                continue

            # 用户相关的重要记忆
            if category in self.USER_CATEGORIES:
                user_facts.append(f"{key}: {value}")

        parts = []
        if self_facts:
            parts.append("[关于自己]\n" + "\n".join(f"- {f}" for f in self_facts[:max_items // 2]))
        if user_facts:
            parts.append("[关于用户]\n" + "\n".join(f"- {f}" for f in user_facts[:max_items // 2]))

        if not parts:
            return ""
        return "[人格记忆]\n" + "\n".join(parts)
