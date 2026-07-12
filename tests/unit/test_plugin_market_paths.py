"""Tests for plugin market path handling."""

import json
from pathlib import Path

import pytest

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


def test_observer_template_marks_runtime_role_and_market_metadata(tmp_path: Path) -> None:
    plugins_dir = tmp_path / "plugins"
    market = PluginMarket(plugins_dir=str(plugins_dir), cache_dir=str(tmp_path / "cache"))

    result = market.create_from_template(
        "watcher",
        name="Watcher",
        plugin_type="observer",
    )

    plugin_dir = plugins_dir / "community" / "watcher"
    code = (plugin_dir / "plugin.py").read_text(encoding="utf-8")
    config = json.loads((plugin_dir / "config.json").read_text(encoding="utf-8"))

    assert result["success"] is True
    assert "roles=['observer']" in code
    assert config["roles"] == ["observer"]
    assert config["platforms"] == ["all"]


def test_market_metadata_defaults_keep_legacy_plugins_usable(tmp_path: Path) -> None:
    market = PluginMarket(plugins_dir=str(tmp_path / "plugins"), cache_dir=str(tmp_path / "cache"))

    config = market._build_market_config("legacy", {"name": "Legacy"})

    assert config["schema_version"] == 2
    assert config["roles"] == ["interceptor", "rewriter", "tool_provider"]
    assert config["platforms"] == ["all"]
    assert config["permissions"] == []


def test_market_entry_normalizes_string_fields(tmp_path: Path) -> None:
    market = PluginMarket(plugins_dir=str(tmp_path / "plugins"), cache_dir=str(tmp_path / "cache"))

    entry = market._normalize_market_entry({
        "id": "stringy",
        "roles": "observer",
        "platforms": "qq,desktop",
        "requires_services": "napcat",
        "assets": "assets/icon.png",
    })

    assert entry["roles"] == ["observer"]
    assert entry["platforms"] == ["qq", "desktop"]
    assert entry["requires_service"] == ["napcat"]
    assert entry["assets"] == ["assets/icon.png"]


def test_market_metadata_rejects_unknown_runtime_role(tmp_path: Path) -> None:
    market = PluginMarket(plugins_dir=str(tmp_path / "plugins"), cache_dir=str(tmp_path / "cache"))

    with pytest.raises(ValueError, match="roles不支持"):
        market._build_market_config("bad", {"roles": ["unknown_role"]})


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


def test_market_rejects_traversal_ids_without_touching_outside_files(tmp_path: Path) -> None:
    plugins_dir = tmp_path / "plugins"
    plugins_dir.mkdir()
    victim = tmp_path / "victim.py"
    victim.write_text("keep", encoding="utf-8")
    market = PluginMarket(plugins_dir=str(plugins_dir), cache_dir=str(tmp_path / "cache"))

    result = market.uninstall("../victim")

    assert result["success"] is False
    assert victim.read_text(encoding="utf-8") == "keep"
    assert market.get_plugin_code("..\\victim")["success"] is False


@pytest.mark.asyncio
async def test_market_install_is_atomic_and_disabled_by_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin_code = (
        "from white_salary.core.plugins.base import Plugin, PluginMeta\n"
        "class RemotePlugin(Plugin):\n"
        "    meta = PluginMeta(name='remote_demo')\n"
    )

    class _Response:
        def __init__(self, *, status: int = 200, text: str = "", data=None) -> None:
            self.status = status
            self._text = text
            self._data = data

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def text(self):
            return self._text

        async def json(self):
            return self._data

        async def read(self):
            return self._text.encode("utf-8")

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def get(self, url: str, **kwargs):
            if url.endswith("/plugin.py"):
                return _Response(text=plugin_code)
            if url.endswith("/config.json"):
                return _Response(data={"id": "remote_demo", "roles": ["tool_provider"]})
            return _Response(status=404)

    monkeypatch.setattr("white_salary.core.plugins.market.aiohttp.ClientSession", _Session)
    market = PluginMarket(
        plugins_dir=str(tmp_path / "plugins"),
        cache_dir=str(tmp_path / "cache"),
    )

    result = await market.install("remote_demo")

    plugin_dir = tmp_path / "plugins" / "community" / "remote_demo"
    config = json.loads((plugin_dir / "config.json").read_text(encoding="utf-8"))
    assert result["success"] is True
    assert result["enabled"] is False
    assert config["enabled"] is False
    assert config["trust_level"] == "community"
    assert not list((tmp_path / "plugins" / "community").glob(".install-*"))


@pytest.mark.asyncio
async def test_sync_to_github_uploads_declared_assets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugins_dir = tmp_path / "plugins"
    plugin_dir = _write_plugin(plugins_dir, "asset_plugin", "community")
    (plugin_dir / "assets").mkdir()
    (plugin_dir / "assets" / "icon.txt").write_text("icon", encoding="utf-8")
    (plugin_dir / "prompts").mkdir()
    (plugin_dir / "prompts" / "system.md").write_text("prompt", encoding="utf-8")
    config = {
        "id": "asset_plugin",
        "name": "Asset Plugin",
        "assets": ["assets/icon.txt", "prompts/system.md"],
        "roles": ["tool_provider"],
        "dependencies": {"python": ["httpx"]},
    }
    (plugin_dir / "config.json").write_text(
        json.dumps(config, ensure_ascii=False),
        encoding="utf-8",
    )

    market = PluginMarket(
        plugins_dir=str(plugins_dir),
        cache_dir=str(tmp_path / "cache"),
        github_token="token",
    )
    uploaded: list[str] = []
    indexed: list[tuple[str, dict]] = []

    async def fake_put(session, path: str, content_b64: str, message: str) -> None:
        uploaded.append(path)

    async def fake_update_index(session, plugin_id: str, config: dict) -> None:
        indexed.append((plugin_id, config))

    monkeypatch.setattr(market, "_github_put", fake_put)
    monkeypatch.setattr(market, "_update_plugins_index", fake_update_index)

    result = await market.sync_to_github()

    assert result["success"] is True
    assert "plugins/asset_plugin/plugin.py" in uploaded
    assert "plugins/asset_plugin/config.json" in uploaded
    assert "plugins/asset_plugin/assets/icon.txt" in uploaded
    assert "plugins/asset_plugin/prompts/system.md" in uploaded
    assert indexed[0][0] == "asset_plugin"
    assert indexed[0][1]["roles"] == ["tool_provider"]
