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

import asyncio
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
        # 2026-07-03 功能大项（批11）：热加载与钩子接线新增的三份状态。
        # _registry：注册工具用的 ToolRegistry（reload/load_one 时把插件工具
        #   重新注册进去；未接入注册表时为 None，热加载仅刷新插件实例不动工具）。
        # _plugin_id_to_name：磁盘插件标识（目录名/单文件stem，= 市场 plugin_id）
        #   → 运行中 meta.name 的映射。插件按 meta.name 存进 _plugins，但市场
        #   install/uninstall 与设置面板用的是 plugin_id（目录名），二者可能不同，
        #   load_one/unload_one 需要这份映射把 plugin_id 定位到已加载实例。
        # _plugin_tools：meta.name → 该插件注册进 registry 的工具名列表，
        #   卸载时据此反注册，避免坏插件卸载后残留幽灵工具。
        self._registry = None
        self._plugin_id_to_name: dict[str, str] = {}
        self._plugin_tools: dict[str, list[str]] = {}
        # 2026-07-03 功能大项（批11）：消息钩子专用超时（3秒）——比工具执行的
        # 5秒更短，因为它挡在每条用户消息前，绝不能拖慢主链路（任务要求）。
        self._hook_timeout: float = 3.0

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

    @staticmethod
    def _path_id(path: str) -> str:
        """
        2026-07-03 功能大项（批11）：从发现的插件路径提取磁盘标识（= plugin_id）。

        目录插件取目录名，单文件插件取去后缀的文件名，与市场
        install/uninstall、设置面板传入的 plugin_id 口径一致。

        Args:
            path: discover() 记录在 _discovered 里的路径

        Returns:
            plugin_id（目录名或文件stem）
        """
        p = Path(path)
        # 单文件插件 discover 记的是 .py 文件路径；目录插件记的是目录或
        # 目录下的 plugin.py。取所在包目录名（plugin.py 的父目录名）或文件 stem。
        if p.is_file() and p.suffix == ".py":
            if p.name in ("plugin.py", "__init__.py"):
                return p.parent.name
            return p.stem
        return p.name

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
            # 2026-07-03 功能大项（批11）：登记 plugin_id → meta.name 映射，
            # 供 load_one/unload_one 用磁盘标识定位运行中实例
            self._plugin_id_to_name[self._path_id(path)] = instance.meta.name
            logger.info(f"[Plugins] 已加载: {instance.meta.name} v{instance.meta.version}")
            return True

        except Exception as e:
            logger.error(f"[Plugins] 加载 {p.name} 失败: {e}")
            return False

    async def unload(self, name: str) -> bool:
        """卸载插件（按 meta.name）。"""
        if name not in self._plugins:
            return False
        plugin = self._plugins[name]
        await self._executor.run(plugin.on_unload(), plugin_name=name)
        del self._plugins[name]
        # 2026-07-03 功能大项（批11）：反注册该插件的工具 + 清理映射，
        # 避免卸载后 registry 里残留幽灵工具、映射表悬挂过期 meta.name
        self._unregister_plugin_tools(name)
        for pid, mapped in list(self._plugin_id_to_name.items()):
            if mapped == name:
                del self._plugin_id_to_name[pid]
        logger.info(f"[Plugins] 已卸载: {name}")
        return True

    def _unregister_plugin_tools(self, name: str) -> None:
        """
        2026-07-03 功能大项（批11）：把某插件注册进 registry 的工具全部反注册。

        卸载/重载插件时调用，据 _plugin_tools 记录的工具名逐个 unregister，
        防止插件卸载后其工具仍留在 ToolRegistry 里被 LLM 误选。
        registry 未接入或反注册出错都不抛（清理是尽力而为）。

        Args:
            name: 插件 meta.name
        """
        tool_names = self._plugin_tools.pop(name, [])
        if not self._registry or not tool_names:
            return
        for tool_name in tool_names:
            try:
                self._registry.unregister(tool_name)
            except Exception as e:
                logger.warning(f"[Plugins] 反注册工具 {tool_name} 失败: {e}")

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
    # 热加载（批11）
    # ================================================================

    async def reload_all(self) -> int:
        """
        2026-07-03 功能大项（批11）：全量热重载所有插件（装完/改完即生效不重启）。

        流程：先安全卸载全部已加载插件（含反注册其工具、执行 on_unload），
        清掉 sys.modules 里的插件模块缓存（保证重新 exec 的是磁盘最新代码），
        再重新 discover + load_all，并把工具重新注册进已接入的 registry。
        单个插件加载失败不影响其它插件（load 内部已 try/except）。

        Returns:
            重载后成功加载的插件数
        """
        # 1. 卸载所有已加载插件（unload 内部会反注册工具、清映射）
        for name in list(self._plugins.keys()):
            try:
                await self.unload(name)
            except Exception as e:
                logger.warning(f"[Plugins] 重载时卸载 {name} 失败: {e}")
        # 2. 清插件模块缓存，确保重新加载的是磁盘最新代码
        for mod_name in [m for m in sys.modules if m.startswith("plugin_")]:
            try:
                del sys.modules[mod_name]
            except Exception as e:
                logger.debug(f"[Plugins] 清理模块缓存 {mod_name} 失败: {e}")
        # 3. 重新发现并加载
        self._plugin_id_to_name.clear()
        self._plugin_tools.clear()
        self.discover()
        loaded = await self.load_all()
        # 4. 工具重新注册进 registry（若已接入）
        if self._registry is not None:
            self.register_tools_to_registry(self._registry)
        logger.info(f"[Plugins] 热重载完成，当前 {loaded} 个插件")
        return loaded

    async def load_one(self, plugin_id: str) -> bool:
        """
        2026-07-03 功能大项（批11）：热加载单个插件（市场安装成功后即时生效）。

        plugin_id 是磁盘标识（目录名/单文件stem，= 市场 plugin_id）。
        先重新 discover 让新装的插件进入发现列表，再定位并加载它，
        成功后把它的工具注册进 registry。已加载则先卸载再加载（等价刷新）。

        Args:
            plugin_id: 磁盘插件标识（目录名或单文件 stem）

        Returns:
            是否加载成功
        """
        # 重新发现（新装的插件此前不在 _discovered 里）
        self.discover()
        # 若已加载（同名刷新场景），先卸载
        if plugin_id in self._plugin_id_to_name:
            await self.unload_one(plugin_id)
        # 在发现列表里找匹配 plugin_id 的路径
        target_path = None
        for path in self._discovered:
            if self._path_id(path) == plugin_id:
                target_path = path
                break
        if target_path is None:
            logger.warning(f"[Plugins] load_one 未找到插件: {plugin_id}")
            return False
        # 清该插件的模块缓存，保证加载磁盘最新代码
        for mod_name in (f"plugin_{plugin_id}", f"plugin_{Path(target_path).stem}"):
            if mod_name in sys.modules:
                try:
                    del sys.modules[mod_name]
                except Exception:
                    pass
        ok = await self.load(target_path)
        if ok:
            # 把新加载插件的工具注册进 registry
            name = self._plugin_id_to_name.get(plugin_id)
            if name:
                self._register_one_plugin_tools(name)
        return ok

    async def unload_one(self, plugin_id: str) -> bool:
        """
        2026-07-03 功能大项（批11）：热卸载单个插件（市场卸载成功后即时下线）。

        plugin_id 是磁盘标识；经映射表定位运行中实例的 meta.name 后
        走 unload（反注册工具、执行 on_unload、清理映射）。

        Args:
            plugin_id: 磁盘插件标识

        Returns:
            是否卸载成功（未加载返回 False）
        """
        name = self._plugin_id_to_name.get(plugin_id)
        if name is None:
            logger.debug(f"[Plugins] unload_one: {plugin_id} 未加载，跳过")
            return False
        return await self.unload(name)

    # ================================================================
    # 消息钩子
    # ================================================================

    async def process_message(self, text: str, user_id: str = "") -> Optional[str]:
        """
        让所有插件处理用户消息（按优先级）。

        如果任何插件返回非None，表示拦截消息（"抢答"）。

        2026-07-03 功能大项（批11）：整段加超时+异常兜底。
        - 无插件时立刻返回 None（挡在每条消息前，零开销）。
        - 整个遍历用 asyncio.wait_for(_hook_timeout=3秒) 保护：即便某插件
          on_message 卡死（SafeExecutor 单个5秒超时之外的极端情况，或插件
          总数多导致累计超时），也不拖慢主消息链路——超时按"不拦截"处理。
        - 顶层 try/except 兜底：钩子链路任何异常都不上抛，记 warning 后
          返回 None 让消息正常走 LLM（单个坏插件不能拖垮整条链路）。
        """
        if not self._plugins:
            return None
        try:
            return await asyncio.wait_for(
                self._process_message_inner(text, user_id),
                timeout=self._hook_timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(
                f"[Plugins] process_message 超时({self._hook_timeout}秒)，"
                f"按不拦截处理，消息正常走LLM"
            )
            return None
        except Exception as e:
            logger.warning(f"[Plugins] process_message 异常，按不拦截处理: {e}")
            return None

    async def _process_message_inner(self, text: str, user_id: str) -> Optional[str]:
        """process_message 的实际遍历逻辑（被超时包裹，见 process_message）。"""
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
        """
        让所有插件处理AI回复（可修改内容）。

        2026-07-03 功能大项（批11）：顶层异常兜底——钩子链路出任何岔子
        都返回原始回复，绝不因插件问题把 AI 回复吞掉。每个插件调用本就由
        SafeExecutor 兜底（异常/超时返回上一版回复），这里再加一层保险。
        """
        if not self._plugins:
            return text
        try:
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
        except Exception as e:
            logger.warning(f"[Plugins] process_reply 异常，返回原始回复: {e}")
            return text

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
        """
        把所有插件工具注册到ToolRegistry。

        2026-07-03 功能大项（批11）：注册时记住 registry 引用（供热加载/卸载
        时增删工具）并按插件记录其工具名（_plugin_tools[meta.name]=[工具名...]），
        卸载/重载该插件时据此精确反注册，不误伤其它插件的工具。
        """
        self._registry = registry
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
                # 记录该工具属于哪个插件（"_plugin" 由 get_all_tools 注入）
                owner = tool_def.get("_plugin", "")
                if owner:
                    self._plugin_tools.setdefault(owner, []).append(td.name)
                count += 1
            except Exception as e:
                logger.warning(f"[Plugins] 工具注册失败 {tool_def.get('name')}: {e}")
        if count:
            logger.info(f"[Plugins] 注册了 {count} 个插件工具")
        return count

    def _register_one_plugin_tools(self, name: str) -> int:
        """
        2026-07-03 功能大项（批11）：只把某一个插件的工具注册进 registry。

        load_one 时用——重新全量注册会重复登记其它插件工具，这里只处理
        目标插件。registry 未接入时直接返回 0（热加载仅刷新插件实例）。

        Args:
            name: 插件 meta.name

        Returns:
            成功注册的工具数
        """
        if not self._registry or name not in self._plugins:
            return 0
        plugin = self._plugins[name]
        count = 0
        try:
            plugin_tools = plugin.get_tools()
        except Exception as e:
            logger.warning(f"[Plugins] {name} get_tools失败: {e}")
            return 0
        for tool_def in plugin_tools:
            try:
                from white_salary.adapters.tools.registry import ToolDefinition
                td = ToolDefinition(
                    name=tool_def["name"],
                    description=tool_def.get("description", ""),
                    parameters=tool_def.get("parameters", {}),
                    handler=tool_def["handler"],
                )
                self._registry.register(td)
                self._plugin_tools.setdefault(name, []).append(td.name)
                count += 1
            except Exception as e:
                logger.warning(f"[Plugins] 工具注册失败 {tool_def.get('name')}: {e}")
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
