"""
white_salary/core/plugins/safe_executor.py

安全执行器 — 带超时+异常捕获执行插件代码。

借鉴v2的plugins/safe_executor.py：
  - 默认5秒超时
  - 异常不会传播到主系统
  - 执行统计（成功/失败/超时次数）
"""

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from loguru import logger


@dataclass
class ExecutionStats:
    """执行统计。"""
    total: int = 0
    success: int = 0
    failed: int = 0
    timeout: int = 0


class SafeExecutor:
    """
    安全执行器 — 保护主系统不被插件崩溃影响。

    使用方式:
        executor = SafeExecutor(timeout=5.0)
        result = await executor.run(plugin.on_message, text)
    """

    def __init__(self, timeout: float = 5.0) -> None:
        self._timeout = timeout
        self._stats: dict[str, ExecutionStats] = {}  # plugin_name → stats

    async def run(self, coro, plugin_name: str = "",
                  default: Any = None) -> Any:
        """
        安全执行异步函数。

        Args:
            coro: 要执行的协程
            plugin_name: 插件名（用于统计）
            default: 异常时的默认返回值

        Returns:
            执行结果 或 default
        """
        stats = self._get_stats(plugin_name)
        stats.total += 1

        try:
            result = await asyncio.wait_for(coro, timeout=self._timeout)
            stats.success += 1
            return result
        except asyncio.TimeoutError:
            stats.timeout += 1
            logger.warning(f"[Plugin] {plugin_name} 执行超时({self._timeout}秒)")
            return default
        except Exception as e:
            stats.failed += 1
            logger.warning(f"[Plugin] {plugin_name} 执行异常: {e}")
            return default

    def _get_stats(self, name: str) -> ExecutionStats:
        if name not in self._stats:
            self._stats[name] = ExecutionStats()
        return self._stats[name]

    def get_stats(self, name: str = "") -> dict:
        if name:
            s = self._stats.get(name, ExecutionStats())
            return {"total": s.total, "success": s.success,
                    "failed": s.failed, "timeout": s.timeout}
        return {n: {"total": s.total, "success": s.success,
                     "failed": s.failed, "timeout": s.timeout}
                for n, s in self._stats.items()}
