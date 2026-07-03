"""
white_salary/core/memory/enhanced/integrator.py

增强记忆整合器 — 统一调度所有enhanced模块的生命周期。

借鉴v2的integrator.py：
  - 统一管理forgetting/association/temporal/context四个子系统
  - 每个子系统可独立开关
  - on_memory_created: 创建时触发→遗忘注册+关联图入库+分类标签
  - on_memory_accessed: 访问时触发→刷新权重+加强共访问关联
  - get_memory_score: 组合评分 = 遗忘权重 × 场景偏好
  - get_context_for_llm: 汇总所有enhanced模块的上下文

单例模式：全局一个实例，通过get_integrator()获取。

自动发现：导出MODULE供MemoryManager加载。
"""

import json
import time
from pathlib import Path
from typing import Optional

from loguru import logger
from white_salary.core.memory.module_base import MemoryModule


class EnhancedMemoryIntegrator:
    """
    增强记忆整合器 — 统一调度enhanced子系统。

    使用方式:
        integrator = get_integrator()
        meta = integrator.on_memory_created("m1", "今天很开心", category="emotion")
        score = integrator.get_memory_score("m1")
        related = integrator.get_associated_memories("m1")
    """

    def __init__(
        self,
        data_dir: str = "data/memory",
        enable_forgetting: bool = True,
        enable_association: bool = True,
        enable_temporal: bool = True,
        enable_context: bool = True,
    ) -> None:
        self._data_dir = data_dir
        self._flags = {
            "forgetting": enable_forgetting,
            "association": enable_association,
            "temporal": enable_temporal,
            "context": enable_context,
        }

        # 子系统实例（延迟初始化）
        self._forgetting = None
        self._association = None
        self._temporal = None
        self._context = None

        self._initialized = False

    def _ensure_init(self) -> None:
        """延迟初始化子系统（避免循环导入）。"""
        if self._initialized:
            return
        self._initialized = True

        # 读配置
        config = {}
        try:
            cfg_path = Path("config/memory_settings.json")
            if cfg_path.exists():
                config = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception:
            pass

        if self._flags["forgetting"]:
            try:
                from white_salary.core.memory.enhanced.forgetting import ForgettingEngine
                self._forgetting = ForgettingEngine(
                    config=config.get("forgetting", {}),
                    data_dir=self._data_dir,
                )
            except Exception as e:
                logger.warning(f"[Integrator] forgetting初始化失败: {e}")

        if self._flags["association"]:
            try:
                from white_salary.core.memory.enhanced.association import MemoryGraph
                self._association = MemoryGraph(
                    config=config.get("association", {}),
                    data_dir=self._data_dir,
                )
            except Exception as e:
                logger.warning(f"[Integrator] association初始化失败: {e}")

        if self._flags["temporal"]:
            try:
                from white_salary.core.memory.enhanced.temporal import TemporalEngine
                self._temporal = TemporalEngine(
                    config=config.get("temporal", {}),
                    data_dir=self._data_dir,
                )
            except Exception as e:
                logger.warning(f"[Integrator] temporal初始化失败: {e}")

        if self._flags["context"]:
            try:
                from white_salary.core.memory.enhanced.context import SceneEngine
                self._context = SceneEngine(config=config.get("scene", {}))
            except Exception as e:
                logger.warning(f"[Integrator] context初始化失败: {e}")

        active = [k for k, v in self._flags.items() if v]
        logger.debug(f"[Integrator] 初始化完成，活跃子系统: {active}")

    # ================================================================
    # 生命周期 Hook
    # ================================================================

    def on_memory_created(
        self,
        memory_id: str,
        content: str,
        category: str = "",
        is_important: bool = False,
        emotional_intensity: float = 0.0,
        people: list[str] = None,
    ) -> dict:
        """
        记忆创建时调用 — 各子系统注册这条新记忆。

        Returns:
            {"memory_id": str, "weight": float, "category": str, "keywords": list}
        """
        self._ensure_init()
        result = {"memory_id": memory_id, "weight": 1.0, "category": category, "keywords": []}

        # 1. 遗忘引擎注册
        if self._forgetting:
            self._forgetting.record_access(
                key=memory_id,
                emotional_intensity=emotional_intensity,
                is_important=is_important,
                category=category,
            )
            result["weight"] = self._forgetting.get_weight(memory_id)

        # 2. 关联图入库
        if self._association:
            tags = [category] if category else []
            node = self._association.add_node(
                node_id=memory_id,
                content=content,
                tags=tags,
                people=people or [],
                weight=1.0 + emotional_intensity * 0.5,
                auto_extract=True,
            )
            result["keywords"] = node.keywords

        # 3. 时间引擎：检测是否包含周期事件
        if self._temporal:
            detected = self._temporal.detect_from_text(content)
            if detected:
                logger.debug(f"[Integrator] 检测到周期事件: {[e.name for e in detected]}")

        return result

    def on_memory_accessed(
        self,
        memory_id: str,
        accessed_with: list[str] = None,
    ) -> float:
        """
        记忆被访问时调用 — 刷新权重，加强共访问关联。

        Returns:
            当前有效权重
        """
        self._ensure_init()
        weight = 1.0

        # 刷新遗忘权重
        if self._forgetting:
            self._forgetting.record_access(key=memory_id)
            weight = self._forgetting.get_weight(memory_id)

        # 加强共访问的关联边
        if self._association and accessed_with:
            all_ids = [memory_id] + accessed_with
            self._association.record_co_access(all_ids)

        return weight

    # ================================================================
    # 评分
    # ================================================================

    def get_memory_score(self, memory_id: str, category: str = "") -> float:
        """
        组合评分 — 综合遗忘权重和场景偏好。

        公式: score = forgetting_weight × scene_bias
        """
        self._ensure_init()
        score = 1.0

        # 遗忘权重
        if self._forgetting:
            score *= self._forgetting.get_weight(memory_id)

        # 场景偏好
        if self._context and category:
            bias = self._context.get_memory_bias(category)
            score *= bias

        return min(score, 5.0)

    # ================================================================
    # 关联召回
    # ================================================================

    def get_associated_memories(
        self,
        memory_id: str,
        depth: int = 2,
        limit: int = 5,
    ) -> list[tuple[str, float]]:
        """
        获取关联记忆 — 通过关联图BFS遍历。

        Returns:
            [(memory_id, strength), ...]
        """
        self._ensure_init()
        if not self._association:
            return []
        return self._association.get_associated(
            node_id=memory_id,
            depth=depth,
            limit=limit,
        )

    # ================================================================
    # 上下文汇总
    # ================================================================

    def get_context_for_llm(self, message: str = "") -> dict:
        """
        汇总所有enhanced模块的上下文，供LLM注入。

        Returns:
            {"temporal": str, "scene": str, "association": str}
        """
        self._ensure_init()
        context = {}

        # 时间/周期事件提醒
        if self._temporal:
            reminder = self._temporal.get_reminder_prompt()
            if reminder:
                context["temporal"] = reminder

        # 场景氛围
        if self._context:
            if message:
                self._context.update_from_message(message)
            from white_salary.core.memory.enhanced.context import ATMOSPHERE_LABELS
            atm = self._context.current_atmosphere
            if atm != "casual":
                label = ATMOSPHERE_LABELS.get(atm, atm)
                context["scene"] = f"[当前氛围: {label}]"

        # 关联记忆（根据消息搜索）
        if self._association and message:
            matches = self._association.search_by_content(message, limit=3)
            if matches:
                lines = []
                seen = set()
                for nid, _ in matches:
                    node = self._association.get_node(nid)
                    if node and node.content not in seen:
                        seen.add(node.content)
                        lines.append(node.content)
                    # 一层关联
                    for assoc_id, strength in self._association.get_associated(nid, depth=1, limit=2):
                        anode = self._association.get_node(assoc_id)
                        if anode and anode.content not in seen and strength >= 0.3:
                            seen.add(anode.content)
                            lines.append(anode.content)
                if lines:
                    context["association"] = "[联想记忆]\n" + "\n".join(f"  - {l}" for l in lines[:5])

        return context

    def get_context_prompt(self, message: str = "") -> str:
        """汇总为单一prompt字符串。"""
        parts = self.get_context_for_llm(message)
        return "\n".join(parts.values()) if parts else ""

    # ================================================================
    # 场景相关
    # ================================================================

    def update_scene(self, message: str) -> str:
        """更新场景氛围。"""
        self._ensure_init()
        if self._context:
            return self._context.update_from_message(message)
        return "casual"

    # ================================================================
    # 周期事件
    # ================================================================

    def get_auto_chat_hint(self) -> Optional[str]:
        """检查是否有需要触发主动聊天的周期事件。"""
        self._ensure_init()
        if self._temporal:
            return self._temporal.get_auto_chat_hint()
        return None

    # ================================================================
    # 维护
    # ================================================================

    def run_maintenance(self) -> dict:
        """定期维护 — 更新冷存储状态。"""
        self._ensure_init()
        result = {}
        if self._forgetting:
            new_cold = self._forgetting.update_cold_status()
            result["new_cold"] = new_cold
        if self._association:
            self._association.force_save()
            result["association_saved"] = True
        return result

    @property
    def stats(self) -> dict:
        """统计信息。"""
        self._ensure_init()
        s = {"active_subsystems": [k for k, v in self._flags.items() if v]}
        if self._forgetting:
            s["forgetting"] = self._forgetting.stats
        if self._association:
            s["association"] = self._association.stats
        if self._temporal:
            s["temporal"] = self._temporal.stats
        if self._context:
            s["scene"] = self._context.stats
        return s


# ================================================================
# 单例
# ================================================================

_integrator: Optional[EnhancedMemoryIntegrator] = None


def get_integrator(**kwargs) -> EnhancedMemoryIntegrator:
    """获取全局整合器实例（单例）。"""
    global _integrator
    if _integrator is None:
        _integrator = EnhancedMemoryIntegrator(**kwargs)
    return _integrator


def init_integrator(**kwargs) -> EnhancedMemoryIntegrator:
    """显式初始化整合器（覆盖已有实例）。"""
    global _integrator
    _integrator = EnhancedMemoryIntegrator(**kwargs)
    return _integrator


# ================================================================
# 自动发现接口
# ================================================================

class IntegratorModule(MemoryModule):
    """增强记忆整合器模块 — 自动发现注册。"""
    name = "enhanced_integrator"

    def init(self, data_dir="data/memory", **kwargs):
        self._impl = init_integrator(data_dir=data_dir)

    def get_context_prompt(self, message: str = "") -> str:
        if not hasattr(self, '_impl'):
            return ""
        return self._impl.get_context_prompt(message)

    def on_message(self, user_msg: str = "", ai_reply: str = "") -> None:
        if not hasattr(self, '_impl') or not user_msg:
            return
        # 更新场景
        self._impl.update_scene(user_msg)

    def on_session_end(self) -> None:
        if hasattr(self, '_impl'):
            self._impl.run_maintenance()


MODULE = IntegratorModule
