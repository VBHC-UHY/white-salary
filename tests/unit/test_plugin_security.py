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
