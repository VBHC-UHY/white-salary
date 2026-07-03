"""
white_salary/core/plugins/manager.py

插件管理器 — 自动发现、安全加载、管理插件。

合并了旧plugin_manager.py的消息钩子和新系统的工具注册。
加入v2的安全机制：沙箱检查+超时执行+上下文隔离。

功能：
  - 扫描plugins/目录，自动发现插件
  - 沙箱检查代码安全性
  - 安全执行（5秒超时+异常捕获）
  - 消息拦截+回复修改
  - 工具自动注册到ToolRegistry
  - 热重载
"""

import importlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Optional

from loguru import logger

from white_salary.core.plugins.base import Plugin, PluginMeta
from white_salary.core.plugins.safe_executor import SafeExecutor
from white_salary.core.plugins.context import PluginContext


class PluginManager:
    """
    插件管理器。

    使用方式:
        pm = PluginManager(plugins_dir="plugins")
        pm.discover()
        await pm.load_all()
        # 消息处理
        intercept = await pm.process_message("你好", user_id="123")
        # 回复修改
        reply = await pm.process_reply("AI的回复")
        # 获取工具
        tools = pm.get_all_tools()
    """

    def __init__(self, plugins_dir: str = "plugins") -> None:
        self._dir = Path(plugins_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._plugins: dict[str, Plugin] = {}
        self._discovered: list[str] = []
        self._executor = SafeExecutor(timeout=5.0)
        self._context = PluginContext()

    def discover(self) -> list[str]:
        """扫描插件目录，返回发现的插件名列表。"""
        self._discovered.clear()

        if not self._dir.exists():
            return []

        # 扫描builtin/和community/子目录
        for category in ["builtin", "community"]:
            cat_dir = self._dir / category
            if not cat_dir.exists():
                continue
            for d in cat_dir.iterdir():
                if d.is_dir():
                    # 目录插件：找plugin.py或__init__.py
                    if (d / "plugin.py").exists():
                        self._discovered.append(str(d / "plugin.py"))
                    elif (d / "__init__.py").exists():
                        self._discovered.append(str(d))

        # 根目录的单文件插件
        for f in self._dir.glob("*.py"):
            if f.name.startswith("_"):
                continue
            self._discovered.append(str(f))

        # 根目录的目录插件
        for d in self._dir.iterdir():
            if d.is_dir() and d.name not in ("builtin", "community", "__pycache__"):
                if (d / "__init__.py").exists():
                    self._discovered.append(str(d))
                elif (d / "plugin.py").exists():
                    self._discovered.append(str(d / "plugin.py"))

        logger.info(f"[Plugins] 发现 {len(self._discovered)} 个插件")
        return [Path(p).stem for p in self._discovered]

    async def load_all(self) -> int:
        """加载所有发现的插件。"""
        loaded = 0
        for path in self._discovered:
            try:
                if await self.load(path):
                    loaded += 1
            except Exception as e:
                logger.error(f"[Plugins] 加载失败 {path}: {e}")
        logger.info(f"[Plugins] 已加载 {loaded}/{len(self._discovered)} 个插件")
        return loaded

    async def load(self, path: str) -> bool:
        """加载单个插件（带沙箱检查）。"""
        p = Path(path)

        # 沙箱检查
        code_file = p if p.is_file() else (p / "plugin.py" if (p / "plugin.py").exists() else p / "__init__.py")
        if code_file.exists():
            from white_salary.core.plugins.sandbox import check_file_safety
            is_safe, issues = check_file_safety(str(code_file))
            if not is_safe:
                logger.warning(f"[Plugins] {p.name} 代码不安全: {issues}")
                return False

            # 自动修复import路径（兼容v2插件）
            self._fix_imports(code_file)

        try:
            # 导入模块
            if p.is_dir():
                entry = p / "plugin.py" if (p / "plugin.py").exists() else p / "__init__.py"
                module_name = f"plugin_{p.name}"
                spec = importlib.util.spec_from_file_location(
                    module_name, str(entry),
                    submodule_search_locations=[str(p)],
                )
            elif p.suffix == ".py":
                module_name = f"plugin_{p.stem}"
                spec = importlib.util.spec_from_file_location(module_name, str(p))
            else:
                return False

            if not spec or not spec.loader:
                return False

            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)

            # 找Plugin子类
            plugin_cls = None
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (isinstance(attr, type) and issubclass(attr, Plugin)
                        and attr is not Plugin):
                    plugin_cls = attr
                    break

            if not plugin_cls:
                logger.debug(f"[Plugins] {p.name}: 没有找到Plugin子类")
                return False

            # 实例化
            instance = plugin_cls()

            # 注入上下文
            instance.context = self._context

            # 加载配置
            config_path = None
            if p.is_dir():
                config_path = p / "config.json"
            elif p.is_file():
                config_path = p.with_suffix(".json")
            if config_path and config_path.exists():
                try:
                    config = json.loads(config_path.read_text(encoding="utf-8"))
                    instance.set_config(config)
                except Exception:
                    pass

            # 安全调用on_load
            await self._executor.run(
                instance.on_load(),
                plugin_name=instance.meta.name,
            )

            self._plugins[instance.meta.name] = instance
            logger.info(f"[Plugins] 已加载: {instance.meta.name} v{instance.meta.version}")
            return True

        except Exception as e:
            logger.error(f"[Plugins] 加载 {p.name} 失败: {e}")
            return False

    async def unload(self, name: str) -> bool:
        """卸载插件。"""
        if name not in self._plugins:
            return False
        plugin = self._plugins[name]
        await self._executor.run(plugin.on_unload(), plugin_name=name)
        del self._plugins[name]
        logger.info(f"[Plugins] 已卸载: {name}")
        return True

    async def reload(self, name: str) -> bool:
        """热重载插件。"""
        plugin_path = None
        for path in self._discovered:
            p = Path(path)
            if p.stem == name or p.name == name:
                plugin_path = path
                break
        if not plugin_path:
            return False

        await self.unload(name)
        mod_name = f"plugin_{name}"
        if mod_name in sys.modules:
            del sys.modules[mod_name]
        return await self.load(plugin_path)

    # ================================================================
    # 消息钩子
    # ================================================================

    async def process_message(self, text: str, user_id: str = "") -> Optional[str]:
        """
        让所有插件处理用户消息。

        如果任何插件返回非None，表示拦截消息。
        """
        for plugin in sorted(
            self._plugins.values(),
            key=lambda p: p.meta.priority,
        ):
            result = await self._executor.run(
                plugin.on_message(text, user_id),
                plugin_name=plugin.meta.name,
            )
            if result is not None:
                logger.debug(f"[Plugins] {plugin.meta.name} 拦截了消息")
                return result
        return None

    async def process_reply(self, text: str) -> str:
        """让所有插件处理AI回复（可修改内容）。"""
        result = text
        for plugin in sorted(
            self._plugins.values(),
            key=lambda p: p.meta.priority,
        ):
            modified = await self._executor.run(
                plugin.on_reply(result),
                plugin_name=plugin.meta.name,
                default=result,
            )
            if modified is not None:
                result = modified
        return result

    # ================================================================
    # 工具管理
    # ================================================================

    def get_all_tools(self) -> list[dict]:
        """获取所有插件提供的工具。"""
        tools = []
        for plugin in sorted(
            self._plugins.values(),
            key=lambda p: p.meta.priority,
        ):
            try:
                plugin_tools = plugin.get_tools()
                for t in plugin_tools:
                    t["_plugin"] = plugin.meta.name
                tools.extend(plugin_tools)
            except Exception as e:
                logger.warning(f"[Plugins] {plugin.meta.name} get_tools失败: {e}")
        return tools

    def register_tools_to_registry(self, registry) -> int:
        """把所有插件工具注册到ToolRegistry。"""
        count = 0
        for tool_def in self.get_all_tools():
            try:
                from white_salary.adapters.tools.registry import ToolDefinition
                td = ToolDefinition(
                    name=tool_def["name"],
                    description=tool_def.get("description", ""),
                    parameters=tool_def.get("parameters", {}),
                    handler=tool_def["handler"],
                )
                registry.register(td)
                count += 1
            except Exception as e:
                logger.warning(f"[Plugins] 工具注册失败 {tool_def.get('name')}: {e}")
        if count:
            logger.info(f"[Plugins] 注册了 {count} 个插件工具")
        return count

    # ================================================================
    # 查询
    # ================================================================

    @property
    def loaded_plugins(self) -> dict[str, PluginMeta]:
        return {name: p.meta for name, p in self._plugins.items()}

    @property
    def count(self) -> int:
        return len(self._plugins)

    @staticmethod
    def _fix_imports(code_file: Path) -> None:
        """自动修复v2插件的import路径。"""
        import re
        try:
            code = code_file.read_text(encoding="utf-8")
            original = code

            replacements = [
                (r'from src\.core\.plugins\.base import', 'from white_salary.core.plugins.base import'),
                (r'from src\.core\.plugins import', 'from white_salary.core.plugins import'),
                (r'from src\.core\.plugin_manager import PluginBase', 'from white_salary.core.plugins.base import Plugin'),
                (r'class (\w+)\(PluginBase\)', r'class \1(Plugin)'),
                (r'from src\.', 'from white_salary.'),
            ]
            for pattern, replacement in replacements:
                code = re.sub(pattern, replacement, code)

            if code != original:
                code_file.write_text(code, encoding="utf-8")
                logger.info(f"[Plugins] 自动修复了 {code_file.name} 的import路径")
        except Exception:
            pass

    def get_stats(self) -> dict:
        return {
            "loaded": self.count,
            "discovered": len(self._discovered),
            "executor": self._executor.get_stats(),
        }
