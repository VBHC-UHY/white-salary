"""
white_salary/utils/async_helpers.py

异步编程辅助工具。

Python的异步编程（async/await）有时候会比较麻烦，
这个文件提供一些常用的辅助函数来简化异步操作。
"""

import asyncio
from typing import Any, Awaitable, TypeVar

T = TypeVar("T")


async def run_with_timeout(
    coroutine: Awaitable[T],
    timeout: float,
    default: T | None = None,
) -> T | None:
    """
    运行一个异步任务，如果超时就返回默认值。

    有些操作可能会卡住（比如网络请求），设置超时可以避免一直等下去。

    参数:
        coroutine: 要运行的异步任务
        timeout:   超时时间（秒）
        default:   超时后返回的默认值

    返回:
        任务的返回值，或者超时后返回 default

    示例:
        >>> result = await run_with_timeout(slow_api_call(), timeout=5.0, default="超时了")
    """
    try:
        return await asyncio.wait_for(coroutine, timeout=timeout)
    except asyncio.TimeoutError:
        return default


async def gather_with_errors(
    *tasks: Awaitable[Any],
) -> list[Any | Exception]:
    """
    并行运行多个异步任务，即使某个任务失败也不影响其他任务。

    和 asyncio.gather 不同的是：
      - asyncio.gather 默认一个失败全部取消
      - 这个函数一个失败，其他照常运行，失败的返回异常对象

    参数:
        *tasks: 要并行运行的异步任务

    返回:
        结果列表（成功的是返回值，失败的是Exception对象）

    示例:
        >>> results = await gather_with_errors(task1(), task2(), task3())
        >>> for r in results:
        ...     if isinstance(r, Exception):
        ...         print(f"失败了: {r}")
        ...     else:
        ...         print(f"成功: {r}")
    """
    return list(await asyncio.gather(*tasks, return_exceptions=True))


class AsyncEventEmitter:
    """
    异步事件发射器。

    用于模块间的解耦通信。一个模块触发事件，其他模块监听并响应。
    比如：情感模块检测到"开心"→ 触发事件 → 虚拟形象模块收到后切换笑脸。

    用法:
        emitter = AsyncEventEmitter()

        # 注册监听器
        async def on_emotion_change(emotion):
            print(f"情绪变了: {emotion}")

        emitter.on("emotion_changed", on_emotion_change)

        # 触发事件
        await emitter.emit("emotion_changed", "happy")
    """

    def __init__(self) -> None:
        """初始化事件发射器，创建空的监听器字典。"""
        # 键=事件名, 值=监听器函数列表
        self._listeners: dict[str, list[Any]] = {}

    def on(self, event: str, callback: Any) -> None:
        """
        注册一个事件监听器。

        参数:
            event:    事件名称
            callback: 事件触发时要调用的异步函数
        """
        if event not in self._listeners:
            self._listeners[event] = []
        self._listeners[event].append(callback)

    def off(self, event: str, callback: Any) -> None:
        """
        移除一个事件监听器。

        参数:
            event:    事件名称
            callback: 要移除的函数
        """
        if event in self._listeners:
            self._listeners[event] = [
                cb for cb in self._listeners[event] if cb != callback
            ]

    async def emit(self, event: str, *args: Any, **kwargs: Any) -> None:
        """
        触发一个事件，通知所有监听器。

        参数:
            event:   事件名称
            *args:   传给监听器的位置参数
            **kwargs: 传给监听器的关键字参数
        """
        if event in self._listeners:
            for callback in self._listeners[event]:
                await callback(*args, **kwargs)

    def listener_count(self, event: str) -> int:
        """
        获取某个事件的监听器数量。

        参数:
            event: 事件名称

        返回:
            监听器数量
        """
        return len(self._listeners.get(event, []))
