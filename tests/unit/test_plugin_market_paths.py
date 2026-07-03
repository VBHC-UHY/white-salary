"""Tests for plugin market path handling."""

import json
from pathlib import Path

from white_salary.core.plugins.market import PluginMarket


def _write_plugin(root: Path, plugin_id: str, source: str = "root") -> Path:
    plugin_dir = root / plugin_id if source == "root" else root / source / plugin_id
    plugin_dir.mkdir(parents=True)
    (plugin_dir / "plugin.py").write_text(
        "from white_salary.core.plugins.base import Plugin, PluginMeta\n"
        "class DemoPlugin(Plugin):\n"
        f"    meta = PluginMeta(name='{plugin_id}')\n",
        encoding="utf-8",
    )
    (plugin_dir / "config.json").write_text(
        json.dumps({"cn_name": f"{plugin_id} 名称"}, ensure_ascii=False),
        encoding="utf-8",
    )
    return plugin_dir


def test_installed_plugins_include_runtime_discovery_paths(tmp_path: Path) -> None:
    plugins_dir = tmp_path / "plugins"
    _write_plugin(plugins_dir, "root_plugin")
    _write_plugin(plugins_dir, "community_plugin", "community")
    _write_plugin(plugins_dir, "builtin_plugin", "builtin")

    market = PluginMarket(plugins_dir=str(plugins_dir), cache_dir=str(tmp_path / "cache"))

    installed = {item["id"]: item for item in market.get_installed()}

    assert set(installed) == {"root_plugin", "community_plugin", "builtin_plugin"}
    assert installed["root_plugin"]["source"] == "root"
    assert installed["community_plugin"]["source"] == "community"
    assert installed["builtin_plugin"]["source"] == "builtin"
    assert installed["community_plugin"]["cn_name"] == "community_plugin 名称"


def test_template_plugins_are_created_under_community(tmp_path: Path) -> None:
    plugins_dir = tmp_path / "plugins"
    market = PluginMarket(plugins_dir=str(plugins_dir), cache_dir=str(tmp_path / "cache"))

    result = market.create_from_template("hello_world", name="Hello World")

    assert result["success"] is True
    assert (plugins_dir / "community" / "hello_world" / "plugin.py").is_file()
    assert market._find_plugin_dir("hello_world") == plugins_dir / "community" / "hello_world"
    assert market.get_installed()[0]["source"] == "community"


def test_uninstall_supports_community_and_protects_builtin_paths(tmp_path: Path) -> None:
    plugins_dir = tmp_path / "plugins"
    community_dir = _write_plugin(plugins_dir, "community_plugin", "community")
    builtin_dir = _write_plugin(plugins_dir, "builtin_plugin", "builtin")
    market = PluginMarket(plugins_dir=str(plugins_dir), cache_dir=str(tmp_path / "cache"))

    assert market.uninstall("community_plugin")["success"] is True
    assert not community_dir.exists()

    builtin_result = market.uninstall("builtin_plugin")
    assert builtin_result["success"] is False
    assert "内置插件" in builtin_result["message"]
    assert builtin_dir.exists()
