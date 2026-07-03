"""
测试插件热加载 + on_message/on_reply 钩子接线（2026-07-03 功能大项/批11）。

覆盖：
- PluginManager.process_message：插件返回非空时抢答、返回空时放行、
  坏插件不拖垮主链路、整段超时按不拦截处理、无插件零开销
- PluginManager.process_reply：链式改写、坏插件保留上一版、无插件返回原文
- 热加载：reload_all / load_one / unload_one 的行为（含工具增删）
- 运行实例注册与两个 handler 经 get_runtime_instance('plugins') 取用

沿用现有 fake 风格：不真起服务器/事件循环，异步用例由 asyncio_mode=auto 驱动。
真实的 discover/load 走临时目录里的插件文件（覆盖磁盘发现→加载全链路）。
"""

import asyncio
import textwrap
from pathlib import Path
from typing import Optional

import pytest

import white_salary.infrastructure.server.settings_api as settings_api_module
from white_salary.core.plugins.base import Plugin, PluginMeta, PluginPriority
from white_salary.core.plugins.manager import PluginManager


# ====================================================================
# fixture：隔离运行实例注册表
# ====================================================================

@pytest.fixture(autouse=True)
def _isolated_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """每个用例独立注册表，防止 fake 插件管理器泄漏到其他测试。"""
    monkeypatch.setattr(settings_api_module, "_runtime_registry", {})


# ====================================================================
# 测试替身（fake 插件）
# ====================================================================

class _InterceptPlugin(Plugin):
    """on_message 返回固定串（抢答），on_reply 追加签名。"""

    meta = PluginMeta(name="intercept", priority=PluginPriority.NORMAL)

    def __init__(self, reply: str = "插件抢答了", suffix: str = "[签名]") -> None:
        self._reply = reply
        self._suffix = suffix

    async def on_message(self, text: str, user_id: str = "") -> Optional[str]:
        return self._reply

    async def on_reply(self, text: str) -> str:
        return text + self._suffix


class _PassthroughPlugin(Plugin):
    """on_message 永远返回 None（不拦截），on_reply 原样返回。"""

    meta = PluginMeta(name="passthrough", priority=PluginPriority.NORMAL)

    async def on_message(self, text: str, user_id: str = "") -> Optional[str]:
        return None


class _BrokenPlugin(Plugin):
    """on_message / on_reply 都抛异常（模拟坏插件）。"""

    meta = PluginMeta(name="broken", priority=PluginPriority.HIGHEST)

    async def on_message(self, text: str, user_id: str = "") -> Optional[str]:
        raise RuntimeError("插件内部炸了")

    async def on_reply(self, text: str) -> str:
        raise RuntimeError("插件改写炸了")


class _SlowPlugin(Plugin):
    """on_message 睡很久（模拟卡死插件，触发超时保护）。"""

    meta = PluginMeta(name="slow", priority=PluginPriority.HIGHEST)

    async def on_message(self, text: str, user_id: str = "") -> Optional[str]:
        await asyncio.sleep(10.0)
        return "太慢了不该看到"


def _make_manager_with(*plugins: Plugin) -> PluginManager:
    """构造一个 PluginManager 并直接塞入给定插件实例（跳过磁盘发现）。"""
    pm = PluginManager(plugins_dir="plugins")
    pm._plugins = {p.meta.name: p for p in plugins}
    return pm


# ====================================================================
# process_message：抢答 / 放行 / 坏插件 / 超时 / 无插件
# ====================================================================

async def test_process_message_intercepts() -> None:
    """插件 on_message 返回非空时抢答。"""
    pm = _make_manager_with(_InterceptPlugin(reply="彩蛋！"))
    result = await pm.process_message("触发", user_id="u1")
    assert result == "彩蛋！"


async def test_process_message_passthrough() -> None:
    """所有插件返回 None 时放行（返回 None，主链路正常走 LLM）。"""
    pm = _make_manager_with(_PassthroughPlugin())
    result = await pm.process_message("普通消息", user_id="u1")
    assert result is None


async def test_process_message_broken_plugin_does_not_break_chain() -> None:
    """坏插件不拖垮主链路：坏插件被吞掉，后续放行型插件正常返回 None。"""
    pm = _make_manager_with(_BrokenPlugin(), _PassthroughPlugin())
    result = await pm.process_message("消息", user_id="u1")
    # 坏插件异常被 SafeExecutor 吞掉（返回 None=不拦截），放行插件也 None
    assert result is None


async def test_process_message_broken_then_intercept() -> None:
    """坏插件不影响其后插件抢答。"""
    # _BrokenPlugin 优先级 HIGHEST（先跑），_InterceptPlugin NORMAL（后跑）
    pm = _make_manager_with(_BrokenPlugin(), _InterceptPlugin(reply="我抢答"))
    result = await pm.process_message("消息", user_id="u1")
    assert result == "我抢答"


async def test_process_message_timeout_skips(monkeypatch: pytest.MonkeyPatch) -> None:
    """整段超时按"不拦截"处理（返回 None，消息正常走 LLM）。"""
    pm = _make_manager_with(_SlowPlugin())
    # 把钩子总超时压到极短，避免用例真的等 3 秒
    pm._hook_timeout = 0.05
    result = await pm.process_message("消息", user_id="u1")
    assert result is None


async def test_process_message_no_plugins_zero_cost() -> None:
    """无插件时立即返回 None（零开销）。"""
    pm = PluginManager(plugins_dir="plugins")
    pm._plugins = {}
    result = await pm.process_message("消息", user_id="u1")
    assert result is None


# ====================================================================
# process_reply：链式改写 / 坏插件保留 / 无插件原文
# ====================================================================

async def test_process_reply_rewrites() -> None:
    """on_reply 型插件改写回复。"""
    pm = _make_manager_with(_InterceptPlugin(suffix="[尾巴]"))
    result = await pm.process_reply("原始回复")
    assert result == "原始回复[尾巴]"


async def test_process_reply_broken_keeps_previous() -> None:
    """坏插件的 on_reply 异常时保留上一版回复（SafeExecutor default=上一版）。"""
    pm = _make_manager_with(_BrokenPlugin())
    result = await pm.process_reply("原始回复")
    assert result == "原始回复"


async def test_process_reply_no_plugins_returns_original() -> None:
    """无插件时返回原文。"""
    pm = PluginManager(plugins_dir="plugins")
    pm._plugins = {}
    result = await pm.process_reply("原始回复")
    assert result == "原始回复"


# ====================================================================
# 热加载：真实磁盘发现 + load_one / unload_one / reload_all
# ====================================================================

_MSG_PLUGIN_SRC = textwrap.dedent(
    '''
    from white_salary.core.plugins.base import Plugin, PluginMeta


    class DicePlugin(Plugin):
        meta = PluginMeta(name="dice_msg")

        async def on_message(self, text, user_id=""):
            if "骰子" in text:
                return "掷出6"
            return None
    '''
).strip()

_TOOL_PLUGIN_SRC = textwrap.dedent(
    '''
    from white_salary.core.plugins.base import Plugin, PluginMeta


    class ToolPlugin(Plugin):
        meta = PluginMeta(name="tool_plug")

        def get_tools(self):
            async def _handler(**kwargs):
                return "ok"
            return [{
                "name": "plug_tool",
                "description": "测试工具",
                "parameters": {"type": "object", "properties": {}},
                "handler": _handler,
            }]
    '''
).strip()


def _write_dir_plugin(plugins_dir: Path, plugin_id: str, src: str) -> None:
    """在 plugins_dir 下写一个目录插件（plugin.py + __init__.py）。"""
    pdir = plugins_dir / plugin_id
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "plugin.py").write_text(src, encoding="utf-8")
    (pdir / "__init__.py").write_text("from .plugin import *\n", encoding="utf-8")


class _FakeRegistry:
    """模拟 ToolRegistry：只记录 register/unregister 的工具名。"""

    def __init__(self) -> None:
        self.tools: dict[str, object] = {}

    def register(self, tool) -> None:
        self.tools[tool.name] = tool

    def unregister(self, name: str) -> bool:
        if name in self.tools:
            del self.tools[name]
            return True
        return False


async def test_load_one_and_unload_one(tmp_path: Path) -> None:
    """load_one 加载单个磁盘插件、unload_one 卸载它。"""
    plugins_dir = tmp_path / "plugins"
    _write_dir_plugin(plugins_dir, "dice_pkg", _MSG_PLUGIN_SRC)

    pm = PluginManager(plugins_dir=str(plugins_dir))
    # 初始未加载
    assert pm.count == 0

    ok = await pm.load_one("dice_pkg")
    assert ok is True
    # 插件按 meta.name 存（dice_msg），plugin_id（dice_pkg）经映射定位
    assert "dice_msg" in pm._plugins
    assert pm._plugin_id_to_name["dice_pkg"] == "dice_msg"
    # 钩子真触发
    assert await pm.process_message("给我骰子") == "掷出6"

    # 卸载
    ok2 = await pm.unload_one("dice_pkg")
    assert ok2 is True
    assert "dice_msg" not in pm._plugins
    assert await pm.process_message("给我骰子") is None


async def test_unload_one_unknown_returns_false(tmp_path: Path) -> None:
    """unload_one 未加载的插件返回 False（不报错）。"""
    pm = PluginManager(plugins_dir=str(tmp_path / "plugins"))
    assert await pm.unload_one("不存在") is False


async def test_load_one_registers_and_unload_unregisters_tools(tmp_path: Path) -> None:
    """load_one 把插件工具注册进 registry，unload_one 反注册。"""
    plugins_dir = tmp_path / "plugins"
    _write_dir_plugin(plugins_dir, "tool_pkg", _TOOL_PLUGIN_SRC)

    pm = PluginManager(plugins_dir=str(plugins_dir))
    registry = _FakeRegistry()
    # 先接入 registry（模拟 run_server 的 register_tools_to_registry）
    pm.register_tools_to_registry(registry)

    ok = await pm.load_one("tool_pkg")
    assert ok is True
    assert "plug_tool" in registry.tools  # 工具已注册

    await pm.unload_one("tool_pkg")
    assert "plug_tool" not in registry.tools  # 工具已反注册


async def test_reload_all(tmp_path: Path) -> None:
    """reload_all 重新发现并加载全部插件。"""
    plugins_dir = tmp_path / "plugins"
    _write_dir_plugin(plugins_dir, "dice_pkg", _MSG_PLUGIN_SRC)

    pm = PluginManager(plugins_dir=str(plugins_dir))
    pm.discover()
    await pm.load_all()
    assert pm.count == 1

    # 新增一个插件文件后 reload_all 应发现它
    _write_dir_plugin(plugins_dir, "tool_pkg", _TOOL_PLUGIN_SRC)
    loaded = await pm.reload_all()
    assert loaded == 2
    assert "dice_msg" in pm._plugins
    assert "tool_plug" in pm._plugins


async def test_reload_all_reregisters_tools(tmp_path: Path) -> None:
    """reload_all 后插件工具重新注册进 registry。"""
    plugins_dir = tmp_path / "plugins"
    _write_dir_plugin(plugins_dir, "tool_pkg", _TOOL_PLUGIN_SRC)

    pm = PluginManager(plugins_dir=str(plugins_dir))
    registry = _FakeRegistry()
    pm.discover()
    await pm.load_all()
    pm.register_tools_to_registry(registry)
    assert "plug_tool" in registry.tools

    # 重载后工具仍在（重新注册），且不重复堆叠映射
    await pm.reload_all()
    assert "plug_tool" in registry.tools
    assert pm._plugin_tools.get("tool_plug") == ["plug_tool"]


# ====================================================================
# 运行实例注册 + handler 取用
# ====================================================================

def test_runtime_register_and_get() -> None:
    """register_runtime_instance('plugins', pm) 后 get 能取回同一实例。"""
    from white_salary.infrastructure.server.settings_api import (
        get_runtime_instance,
        register_runtime_instance,
    )

    pm = PluginManager(plugins_dir="plugins")
    register_runtime_instance("plugins", pm)
    assert get_runtime_instance("plugins") is pm


def test_qq_handler_get_plugin_manager() -> None:
    """qq_handler._get_plugin_manager 从注册表取到运行实例。"""
    from white_salary.infrastructure.server.settings_api import register_runtime_instance
    from white_salary.infrastructure.server.qq_handler import _get_plugin_manager

    pm = PluginManager(plugins_dir="plugins")
    register_runtime_instance("plugins", pm)
    assert _get_plugin_manager() is pm


def test_qq_handler_get_plugin_manager_none_when_unregistered() -> None:
    """未注册时 qq_handler._get_plugin_manager 返回 None（不报错）。"""
    from white_salary.infrastructure.server.qq_handler import _get_plugin_manager
    assert _get_plugin_manager() is None


def test_ws_handler_get_plugin_manager() -> None:
    """websocket_handler._get_plugin_manager 从注册表取到运行实例。"""
    from white_salary.infrastructure.server.settings_api import register_runtime_instance
    from white_salary.infrastructure.server.websocket_handler import _get_plugin_manager

    pm = PluginManager(plugins_dir="plugins")
    register_runtime_instance("plugins", pm)
    assert _get_plugin_manager() is pm


def test_ws_handler_get_plugin_manager_none_when_unregistered() -> None:
    """未注册时 websocket_handler._get_plugin_manager 返回 None。"""
    from white_salary.infrastructure.server.websocket_handler import _get_plugin_manager
    assert _get_plugin_manager() is None
