"""
white_salary/core/memory/injection_control.py

注入控制 — 统一管理哪些信息注入到LLM的system prompt。

借鉴v2的injection_control.py：
  - 每个模块的注入可单独开关
  - 优先级排序（核心记忆 > 重要记忆 > 情感 > 长期 > 关联）
  - token预算控制（总注入量不超过上限）
  - 防止过多注入导致对话质量下降

配置从 config/memory_settings.json 的 smart_injection 节读取。

自动发现：导出MODULE供MemoryManager加载。
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from loguru import logger
from white_salary.core.memory.module_base import MemoryModule


# 注入源的优先级（数字越小越优先）
INJECTION_PRIORITY = {
    "core_memory": 1,           # 核心记忆（永远注入）
    "important_memory": 2,      # 重要记忆（承诺/约定）
    "personality": 3,           # 人格一致性
    "emotion": 4,               # 当前情绪
    "scene": 5,                 # 场景氛围
    "temporal": 6,              # 周期事件提醒
    "knowledge_graph": 7,       # 知识图谱关系
    "context_memory": 8,        # 情境记忆
    "association": 9,           # 联想记忆
    "topic_history": 10,        # 话题历史
    "proactive_recall": 11,     # 主动回忆
    "cross_session": 12,        # 跨会话
    "emotion_trigger": 13,      # 情感触发
    "user_profile": 14,         # 用户画像
}

# 默认开关（全部启用）
DEFAULT_SWITCHES = {name: True for name in INJECTION_PRIORITY}


@dataclass
class InjectionItem:
    """一条待注入的内容。"""
    source: str          # 来源模块名
    content: str         # 注入内容
    priority: int        # 优先级
    char_count: int      # 字符数（估算token）


class InjectionController:
    """
    注入控制器。

    使用方式:
        ctrl = InjectionController(config)
        ctrl.add("core_memory", "[核心记忆]\\n用户叫小明")
        ctrl.add("emotion", "[情绪: 开心]")
        prompt = ctrl.build_injection()  # 按优先级+预算合并
    """

    def __init__(self, config: dict = None) -> None:
        cfg = config or {}
        self._max_chars = cfg.get("max_injection_chars", 3000)
        self._must_include_count = cfg.get("must_include_count", 3)
        self._switches: dict[str, bool] = dict(DEFAULT_SWITCHES)

        # 从配置加载开关
        switches_cfg = cfg.get("switches", {})
        for name, enabled in switches_cfg.items():
            if name in self._switches:
                self._switches[name] = enabled

        # 待注入项
        self._items: list[InjectionItem] = []

    def add(self, source: str, content: str) -> None:
        """添加一条待注入内容。"""
        if not content or not content.strip():
            return

        # 检查开关
        if not self._switches.get(source, True):
            return

        priority = INJECTION_PRIORITY.get(source, 99)
        self._items.append(InjectionItem(
            source=source,
            content=content.strip(),
            priority=priority,
            char_count=len(content),
        ))

    def build_injection(self) -> str:
        """
        按优先级+预算合并所有注入内容。

        Returns:
            合并后的注入文本
        """
        if not self._items:
            return ""

        # 按优先级排序
        sorted_items = sorted(self._items, key=lambda x: x.priority)

        # 按预算选择
        selected = []
        total_chars = 0

        for i, item in enumerate(sorted_items):
            # 前N个必须包含（无论预算）
            if i < self._must_include_count:
                selected.append(item)
                total_chars += item.char_count
                continue

            # 预算检查
            if total_chars + item.char_count <= self._max_chars:
                selected.append(item)
                total_chars += item.char_count
            else:
                # 预算用完了
                break

        # 合并
        parts = [item.content for item in selected]

        # 清空（一次性使用）
        self._items.clear()

        return "\n\n".join(parts)

    def set_switch(self, source: str, enabled: bool) -> None:
        """设置模块注入开关。"""
        self._switches[source] = enabled

    def get_switch(self, source: str) -> bool:
        """获取模块注入开关状态。"""
        return self._switches.get(source, True)

    def get_all_switches(self) -> dict[str, bool]:
        """获取所有开关状态。"""
        return dict(self._switches)

    @property
    def stats(self) -> dict:
        enabled = sum(1 for v in self._switches.values() if v)
        disabled = sum(1 for v in self._switches.values() if not v)
        return {
            "max_chars": self._max_chars,
            "enabled_sources": enabled,
            "disabled_sources": disabled,
            "pending_items": len(self._items),
        }


# ================================================================
# 自动发现接口
# ================================================================

class InjectionControlModule(MemoryModule):
    """注入控制模块 — 自动发现注册。"""
    name = "injection_control"

    def init(self, data_dir="data/memory", **kwargs):
        config = {}
        try:
            cfg_path = Path("config/memory_settings.json")
            if cfg_path.exists():
                all_cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
                config = all_cfg.get("smart_injection", {})
        except Exception:
            pass
        self._impl = InjectionController(config=config)


MODULE = InjectionControlModule
