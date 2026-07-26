"""Security and runtime-policy regression tests for third-party plugins."""

import json
from pathlib import Path

import pytest

from white_salary.adapters.tools.registry import ToolDefinition, ToolRegistry
from white_salary.core.plugins.manager import PluginManager
from white_salary.core.plugins.sandbox import check_code_safety, check_plugin_tree_safety


def _tool(name: str, category: str = "builtin") -> ToolDefinition:
    async def handler() -> str:
        return "ok"

    return ToolDefinition(
        name=name,
        description=name,
        parameters={"type": "object", "properties": {}},
        handler=handler,
        category=category,
    )


def test_ast_scanner_rejects_aliases_and_undeclared_file_writes() -> None:
    safe, issues = check_code_safety(
        "from pathlib import Path as P\nP('x').write_text('bad')\n"
    )

    assert safe is False
    assert any("filesystem" in issue for issue in issues)


def test_plugin_tree_scans_imported_helper_files(tmp_path: Path) -> None:
    package = tmp_path / "sample"
    package.mkdir()
    (package / "plugin.py").write_text("from . import helper\n", encoding="utf-8")
    (package / "helper.py").write_text("eval('1 + 1')\n", encoding="utf-8")

    safe, issues = check_plugin_tree_safety(package)

    assert safe is False
    assert any("helper.py" in issue and "eval" in issue for issue in issues)


@pytest.mark.asyncio
async def test_disabled_plugin_is_not_imported(tmp_path: Path) -> None:
    plugins = tmp_path / "plugins"
    package = plugins / "community" / "disabled_demo"
    package.mkdir(parents=True)
    marker = tmp_path / "executed.txt"
    (package / "plugin.py").write_text(
        "from pathlib import Path\n"
        "from white_salary.core.plugins.base import Plugin, PluginMeta\n"
        f"Path({str(marker)!r}).write_text('executed', encoding='utf-8')\n"
        "class DisabledPlugin(Plugin):\n"
        "    meta = PluginMeta(name='disabled_demo')\n",
        encoding="utf-8",
    )
    (package / "config.json").write_text(
        json.dumps({"enabled": False, "permissions": ["filesystem"]}),
        encoding="utf-8",
    )
    manager = PluginManager(str(plugins))

    manager.discover()
    loaded = await manager.load_all()

    assert loaded == 0
    assert not marker.exists()


def test_plugin_tool_cannot_replace_builtin_tool() -> None:
    registry = ToolRegistry.__new__(ToolRegistry)
    registry._tools = {}
    builtin = _tool("same_name", "builtin")
    registry.register(builtin)

    with pytest.raises(ValueError, match="工具名冲突"):
        registry.register(_tool("same_name", "plugin"))

    assert registry.get_tool("same_name") is builtin


# ---------------------------------------------------------------------------
# 黄金测试 — 锁住 v0.1.12 修复的 RCE 缺口
#
# 背景：历史版本把 os/sys 从 BLOCKED_MODULES 里移走，且未落入任何权限桶。
# 由于导入检查当时是黑名单语义（未明确禁止即放行），插件不声明任何权限
# 就能读环境变量里的全部密钥、删文件、并用 `run = os.popen` 绕过按名字的
# 调用黑名单执行任意命令。以下断言是防止该缺口再次出现的硬约束，
# 任何"为了让某个插件跑起来"而放宽它们的改动都应被视为安全回归。
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "module",
    ["os", "sys", "subprocess", "importlib", "ctypes", "pickle", "inspect", "builtins"],
)
def test_dangerous_modules_are_rejected_even_with_all_permissions(module: str) -> None:
    """绝对禁止层：声明任何权限都不得解锁这些模块。"""
    safe, issues = check_code_safety(
        f"import {module}\n",
        permissions=["network", "filesystem", "database", "threads", "extra_imports"],
    )

    assert safe is False
    assert any(f"禁止导入模块: {module}" in issue for issue in issues)


def test_process_call_cannot_be_bypassed_by_aliasing() -> None:
    """`run = os.popen` 这类先取值再调用的写法必须在取值时就被拦住。"""
    safe, issues = check_code_safety("import os\nrun = os.popen\nrun('whoami')\n")

    assert safe is False
    assert any("popen" in issue or "os" in issue for issue in issues)


def test_private_module_forwarding_is_rejected() -> None:
    """经白名单模块转发访问系统能力（random._os）必须被拦。"""
    safe, issues = check_code_safety("import random\nrandom._os.system('calc')\n")

    assert safe is False


@pytest.mark.parametrize("primitive", ["getattr", "setattr", "globals", "locals", "vars", "eval"])
def test_introspection_primitives_are_rejected(primitive: str) -> None:
    """getattr 链是静态分析的主要绕过入口，必须整体拦掉。"""
    safe, issues = check_code_safety(f"import json\nx = {primitive}(json, 'x')\n")

    assert safe is False
    assert any(primitive in issue for issue in issues)


def test_unknown_third_party_import_requires_extra_imports_permission() -> None:
    """未知第三方依赖默认拒绝，声明 extra_imports 后放行（安装时对用户可见）。"""
    denied, issues = check_code_safety("import bs4\n")
    assert denied is False
    assert any("extra_imports" in issue for issue in issues)

    allowed, _ = check_code_safety("import bs4\n", permissions=["extra_imports"])
    assert allowed is True


@pytest.mark.parametrize(
    ("code", "permissions"),
    [
        ("import requests\n", ["network"]),
        ("from pathlib import Path\nPath('a').write_text('x')\n", ["filesystem"]),
        ("import sqlite3\n", ["database"]),
        ("import threading\n", ["threads"]),
    ],
)
def test_declared_permissions_unlock_their_modules(code: str, permissions: list[str]) -> None:
    """声明权限后必须真的能用，否则筛查就成了功能阻断。"""
    safe, issues = check_code_safety(code, permissions=permissions)

    assert safe is True, issues


def test_ordinary_plugin_code_is_not_flagged() -> None:
    """内置/社区插件的常见写法不得误杀。"""
    safe, issues = check_code_safety(
        "from white_salary.core.plugins.base import Plugin, PluginMeta\n"
        "import re\n"
        "import json\n"
        "import random\n"
        "from typing import Optional, Dict, Any\n"
        "class Demo(Plugin):\n"
        "    meta = PluginMeta(name='demo')\n"
        "    async def on_message(self, ctx):\n"
        "        return random.choice(['a', 'b'])\n"
    )

    assert safe is True, issues
