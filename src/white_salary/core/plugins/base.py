"""
white_salary/core/plugins/base.py

插件基类 — 定义插件接口和生命周期。

合并了旧plugin_manager.py的消息钩子和新系统的工具注册。
借鉴v2的安全机制（权限/沙箱/超时）。

每个插件是一个Python文件或目录，放在 plugins/ 下：
  plugins/
    weather/
      __init__.py    ← 包含 Plugin 子类
      config.json    ← 可选配置
    dice.py          ← 单文件插件

插件能做的事：
  1. 注册工具给AI用（get_tools）
  2. 拦截用户消息（on_message → 返回str替代回复）
  3. 修改AI回复（on_reply → 返回修改后的文字）
  4. 加载/卸载时执行操作（on_load/on_unload）
"""

from abc import ABC
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional, Any


class PluginPriority(IntEnum):
    HIGHEST = 0
    HIGH = 25
    NORMAL = 50
    LOW = 75
    LOWEST = 100


@dataclass
class PluginMeta:
    """插件元数据。"""
    name: str
    version: str = "1.0.0"
    description: str = ""
    author: str = ""
    priority: PluginPriority = PluginPriority.NORMAL
    tools: list[dict] = field(default_factory=list)
    roles: list[str] = field(
        default_factory=lambda: ["interceptor", "rewriter", "tool_provider"]
    )


class Plugin(ABC):
    """
    插件基类。所有插件必须继承这个类。

    示例（最简单的插件）:
        class MyPlugin(Plugin):
            meta = PluginMeta(name="my_plugin", description="我的插件")

            async def on_message(self, text, user_id=""):
                if "骰子" in text:
                    import random
                    return f"🎲 掷出了 {random.randint(1,6)}"
                return None  # 不拦截

    示例（带工具的插件）:
        class WeatherPlugin(Plugin):
            meta = PluginMeta(name="weather", description="查天气")

            def get_tools(self):
                return [{
                    "name": "get_weather",
                    "description": "查询天气",
                    "parameters": {"type":"object","properties":{"city":{"type":"string"}}},
                    "handler": self.handle_weather,
                }]

            async def handle_weather(self, city=""):
                return f"{city}的天气是晴天"
    """

    meta: PluginMeta = PluginMeta(name="unnamed")

    # ================================================================
    # 生命周期
    # ================================================================

    async def on_load(self) -> None:
        """插件加载时调用。"""
        pass

    async def on_unload(self) -> None:
        """插件卸载时调用。"""
        pass

    # ================================================================
    # 消息钩子
    # ================================================================

    async def on_message(self, text: str, user_id: str = "") -> Optional[str]:
        """
        用户消息钩子。

        Args:
            text: 用户发送的消息
            user_id: 发送者ID

        Returns:
            None = 不拦截，继续正常处理
            str = 拦截消息，直接返回这个字符串作为回复
        """
        return None

    async def on_observe(
        self,
        text: str,
        user_id: str = "",
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        """
        只观察消息，不抢答。

        observer 插件可用它记录/学习所有经过授权的消息；要启用该角色，在
        PluginMeta.roles 里加入 "observer"。
        """
        return None

    async def on_reply(self, text: str) -> str:
        """
        AI回复钩子（可修改回复内容）。

        Args:
            text: AI的原始回复

        Returns:
            修改后的回复（或原样返回）
        """
        return text

    # ================================================================
    # 工具注册
    # ================================================================

    def get_tools(self) -> list[dict]:
        """
        返回这个插件提供的工具列表。

        每个工具是一个dict:
            {
                "name": "tool_name",
                "description": "工具描述",
                "parameters": {"type": "object", "properties": {...}},
                "handler": async_callable,
            }
        """
        return []

    # ================================================================
    # 配置
    # ================================================================

    def get_config(self, key: str = "", default: Any = None) -> Any:
        """获取插件配置。"""
        config = getattr(self, '_config', {})
        if key:
            return config.get(key, default)
        return config

    def set_config(self, config: dict) -> None:
        """设置插件配置。"""
        self._config = config


# ================================================================
# 权限装饰器（插件可以用这些控制命令权限）
# ================================================================

def admin_only(func):
    """标记这个方法只有管理员能触发。"""
    func._admin_only = True
    return func


def owner_only(func):
    """标记这个方法只有主人能触发。"""
    func._owner_only = True
    return func


def cooldown(seconds: float = 5.0, per_user: bool = True):
    """给方法加冷却时间。"""
    import time as _time
    _last_call: dict[str, float] = {}

    def decorator(func):
        async def wrapper(*args, **kwargs):
            key = kwargs.get("user_id", "global") if per_user else "global"
            now = _time.time()
            if key in _last_call and now - _last_call[key] < seconds:
                remaining = seconds - (now - _last_call[key])
                return f"冷却中，{remaining:.0f}秒后再试"
            _last_call[key] = now
            return await func(*args, **kwargs)
        wrapper._cooldown = seconds
        return wrapper
    return decorator
