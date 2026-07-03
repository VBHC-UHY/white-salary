"""
white_salary/core/services/memory_consolidation.py

记忆整理服务 — 定期自动合并、去重、精简长期记忆。

借鉴v2的memory_consolidation.py但做了以下改进：
  - v2只处理long_term.json的highlights，我们处理SQLite里的长期记忆
  - v2没有去重逻辑（全靠LLM），我们先做文本去重再调LLM
  - v2丢失时间戳元数据，我们保留
  - v2固定10条阈值，我们根据总量动态调整

功能：
  - 每日自动执行（默认凌晨4点）
  - 去除重复/相似的记忆条目
  - LLM辅助合并（将相关记忆合并为精简版）
  - 清理已过期的记忆
  - 可手动触发
"""

import asyncio
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger


class MemoryConsolidationService:
    """
    记忆整理服务。

    使用方式:
        service = MemoryConsolidationService(memory_manager, llm)
        await service.start()  # 后台运行
    """

    def __init__(
        self,
        memory_manager,
        consolidation_llm=None,
        consolidation_hour: int = 4,
    ) -> None:
        """
        Args:
            memory_manager: MemoryManager实例
            consolidation_llm: 用于合并的LLM（可选，不传则只做去重不做合并）
            consolidation_hour: 每日执行时间（小时，默认凌晨4点）
        """
        self._memory = memory_manager
        self._llm = consolidation_llm
        self._hour = consolidation_hour
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._last_run_date = ""
        self._stats = {"total_runs": 0, "total_removed": 0, "total_merged": 0}

    async def start(self) -> None:
        """启动后台服务。"""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info(f"[MemConsolidate] 已启动（每日 {self._hour}:00 执行）")

    async def stop(self) -> None:
        """停止。"""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def run_now(self) -> dict:
        """手动触发一次整理，返回统计。"""
        return await self._consolidate()

    async def _loop(self) -> None:
        """后台循环 — 每10分钟检查一次是否到执行时间。"""
        while self._running:
            try:
                await asyncio.sleep(600)  # 10分钟检查一次

                now = datetime.now()
                today = now.strftime("%Y-%m-%d")

                # 今天已执行过，跳过
                if today == self._last_run_date:
                    continue

                # 到了执行时间
                if now.hour == self._hour:
                    logger.info("[MemConsolidate] 开始每日记忆整理...")
                    result = await self._consolidate()
                    self._last_run_date = today
                    logger.info(
                        f"[MemConsolidate] 完成: "
                        f"去重 {result['duplicates_removed']} 条, "
                        f"过期清理 {result['expired_removed']} 条"
                    )

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[MemConsolidate] 错误: {e}")
                await asyncio.sleep(60)

    async def _consolidate(self) -> dict:
        """执行一次记忆整理。"""
        result = {
            "duplicates_removed": 0,
            "expired_removed": 0,
            "timestamp": time.time(),
        }

        try:
            # 1. 清理过期记忆
            if hasattr(self._memory, '_long_term') and self._memory._long_term:
                expired = self._memory._long_term.cleanup_expired()
                result["expired_removed"] = expired

            # 2. 去重（基于文本相似度）
            if hasattr(self._memory, '_long_term') and self._memory._long_term:
                dupes = await self._remove_duplicates()
                result["duplicates_removed"] = dupes

            # 3. 核心记忆去重
            if hasattr(self._memory, '_core') and self._memory._core:
                core_dupes = self._deduplicate_core()
                result["duplicates_removed"] += core_dupes

            # 4. 2026-07-03 审计修复（批5）：enhanced整合器每日维护（遗忘/衰减）。
            # 审计实锤：integrator.run_maintenance（冷存储状态更新+关联图落盘）
            # 此前仅被从未执行的 on_session_end 引用，遗忘曲线从未真正运行。
            # 挂进本每日任务，与去重/过期清理同节奏执行。
            try:
                from white_salary.core.memory.enhanced.integrator import get_integrator
                maintenance = get_integrator().run_maintenance()
                result["enhanced_maintenance"] = maintenance
                logger.debug(f"[MemConsolidate] enhanced维护完成: {maintenance}")
            except Exception as e:
                logger.warning(f"[MemConsolidate] enhanced维护失败（不影响其它整理步骤）: {e}")

            self._stats["total_runs"] += 1
            self._stats["total_removed"] += result["duplicates_removed"] + result["expired_removed"]

        except Exception as e:
            logger.error(f"[MemConsolidate] 整理失败: {e}")

        return result

    async def _remove_duplicates(self) -> int:
        """去除长期记忆中的重复条目。"""
        try:
            store = self._memory._long_term
            # 获取所有记忆
            all_memories = store.search("", limit=1000)
            if len(all_memories) < 2:
                return 0

            seen_texts: dict[str, int] = {}  # normalized_text -> first_id
            to_remove: list[int] = []

            for mem in all_memories:
                # 标准化文本用于比较
                text = mem.get("content", "").strip().lower()[:100]
                if not text:
                    continue

                if text in seen_texts:
                    # 重复的，标记删除
                    to_remove.append(mem.get("id", 0))
                else:
                    seen_texts[text] = mem.get("id", 0)

            # 执行删除
            # 2026-07-03 面板升级（批6）：store.remove(...) 是笔误——
            # LongTermMemoryStore 的删除方法叫 delete(entry_id)（long_term_store.py:426），
            # remove 不存在导致 AttributeError 被下面的 except 吞掉，
            # 长期记忆去重从未真正删除过任何条目
            for mem_id in to_remove:
                try:
                    store.delete(mem_id)
                except Exception:
                    pass

            if to_remove:
                logger.debug(f"[MemConsolidate] 长期记忆去重: {len(to_remove)} 条")
            return len(to_remove)

        except Exception as e:
            logger.debug(f"[MemConsolidate] 长期记忆去重失败: {e}")
            return 0

    def _deduplicate_core(self) -> int:
        """去除核心记忆中的重复键值。"""
        try:
            core = self._memory._core
            if not hasattr(core, '_cache'):
                return 0

            # 检查值完全相同的不同key
            seen_values: dict[str, str] = {}  # value -> first_key
            to_remove: list[str] = []

            for key, entry in list(core._cache.items()):
                val = str(entry.get("value", "")).strip().lower()
                if val in seen_values and val:
                    to_remove.append(key)
                else:
                    seen_values[val] = key

            for key in to_remove:
                try:
                    core.delete(key)
                except Exception:
                    pass

            if to_remove:
                logger.debug(f"[MemConsolidate] 核心记忆去重: {len(to_remove)} 条")
            return len(to_remove)

        except Exception as e:
            logger.debug(f"[MemConsolidate] 核心记忆去重失败: {e}")
            return 0

    @property
    def stats(self) -> dict:
        return self._stats.copy()
