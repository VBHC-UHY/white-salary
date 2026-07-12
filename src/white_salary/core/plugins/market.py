"""
white_salary/core/plugins/market.py

插件市场 — 从GitHub仓库浏览、安装、管理插件。

功能：
  - 从GitHub拉取插件列表（VBHC-UHY/whitesalary-plugins）
  - 一键下载安装到plugins/目录
  - 卸载/启用/禁用
  - 提交插件到市场
  - 同步本地插件到GitHub
  - 本地缓存（GitHub不可用时兜底）

借鉴v2的完整实现但适配我们的插件系统架构。
"""

import json
import hashlib
import time
import shutil
import base64
import uuid
from pathlib import Path, PurePosixPath
from typing import Any, Optional

import aiohttp
from loguru import logger

from white_salary.core.plugins.security import contained_path, validate_plugin_id


# GitHub配置
DEFAULT_GITHUB_REPO = "VBHC-UHY/whitesalary-plugins"
GITHUB_API = "https://api.github.com"
GITHUB_RAW = "https://raw.githubusercontent.com"
MARKET_SCHEMA_VERSION = 2
DEFAULT_PLUGIN_ROLES = ["interceptor", "rewriter", "tool_provider"]
DEFAULT_PLUGIN_PLATFORMS = ["all"]
VALID_PLUGIN_ROLES = {"interceptor", "rewriter", "tool_provider", "observer"}
ROLE_ALIASES = {
    "message": "interceptor",
    "reply": "rewriter",
    "tool": "tool_provider",
    "tools": "tool_provider",
    "observe": "observer",
}


class PluginMarket:
    """
    插件市场管理器。

    使用方式:
        market = PluginMarket(plugins_dir="plugins", github_token="ghp_xxx")
        plugins = await market.fetch_list()
        await market.install("plugin_id")
    """

    def __init__(
        self,
        plugins_dir: str = "plugins",
        cache_dir: str = "data/cache",
        github_token: str = "",
        github_repo: str = DEFAULT_GITHUB_REPO,
    ) -> None:
        self._plugins_dir = Path(plugins_dir)
        self._plugins_dir.mkdir(parents=True, exist_ok=True)
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._cache_path = self._cache_dir / "plugin_market_cache.json"
        self._token = github_token
        self._repo = github_repo

    @staticmethod
    def _validated_id(plugin_id: object) -> tuple[str | None, dict[str, Any] | None]:
        try:
            return validate_plugin_id(plugin_id), None
        except ValueError as exc:
            return None, {"success": False, "message": str(exc)}

    @staticmethod
    def _is_plugin_dir(path: Path) -> bool:
        return path.is_dir() and ((path / "plugin.py").exists() or (path / "__init__.py").exists())

    def _iter_plugin_dirs(self) -> list[tuple[str, Path, str]]:
        """Return installed plugin dirs using the same layout PluginManager discovers.

        Supported layouts:
          - plugins/<id>/plugin.py
          - plugins/community/<id>/plugin.py
          - plugins/builtin/<id>/plugin.py
        """
        entries: list[tuple[str, Path, str]] = []
        seen: set[str] = set()

        def add(plugin_id: str, path: Path, source: str) -> None:
            if plugin_id in seen or not self._is_plugin_dir(path):
                return
            try:
                plugin_id = validate_plugin_id(plugin_id)
                path = contained_path(self._plugins_dir, *path.relative_to(self._plugins_dir).parts)
            except (ValueError, OSError):
                logger.warning(f"[Market] 跳过不安全插件目录: {path}")
                return
            seen.add(plugin_id)
            entries.append((plugin_id, path, source))

        # Match runtime override order: root plugins first, then user/community,
        # then bundled/builtin fallbacks.
        for d in sorted(self._plugins_dir.iterdir()):
            if d.name in {"builtin", "community", "__pycache__"}:
                continue
            add(d.name, d, "root")

        for source in ["community", "builtin"]:
            cat_dir = self._plugins_dir / source
            if not cat_dir.exists():
                continue
            for d in sorted(cat_dir.iterdir()):
                add(d.name, d, source)

        return entries

    def _headers(self) -> dict:
        h = {"Accept": "application/vnd.github.v3+json"}
        if self._token:
            h["Authorization"] = f"token {self._token}"
        return h

    @staticmethod
    def _as_list(value: Any) -> list[Any]:
        if value is None or value == "":
            return []
        if isinstance(value, str):
            return [x.strip() for x in value.replace("，", ",").split(",") if x.strip()]
        if isinstance(value, (list, tuple, set)):
            return list(value)
        return [value]

    @classmethod
    def _normalize_string_list(
        cls,
        value: Any,
        *,
        default: list[str] | tuple[str, ...] = (),
        aliases: dict[str, str] | None = None,
        allowed: set[str] | None = None,
        field: str = "字段",
    ) -> list[str]:
        raw_items = cls._as_list(value)
        if not raw_items:
            return list(default)

        result: list[str] = []
        invalid: list[str] = []
        for item in raw_items:
            text = str(item).strip().lower()
            if not text:
                continue
            text = (aliases or {}).get(text, text)
            if allowed is not None and text not in allowed:
                invalid.append(text)
                continue
            if text not in result:
                result.append(text)

        if invalid:
            allowed_text = ", ".join(sorted(allowed or ()))
            raise ValueError(f"{field}不支持: {', '.join(invalid)}；可用值: {allowed_text}")
        return result or list(default)

    @classmethod
    def _normalize_asset_paths(cls, value: Any) -> list[str]:
        result: list[str] = []
        for item in cls._as_list(value):
            rel = str(item).strip().replace("\\", "/").lstrip("/")
            if not rel:
                continue
            posix = PurePosixPath(rel)
            if posix.is_absolute() or ".." in posix.parts:
                raise ValueError(f"资源路径不安全: {item}")
            if rel in {"plugin.py", "config.json", "__init__.py"}:
                continue
            if rel not in result:
                result.append(rel)
        return result

    @classmethod
    def _normalize_dependencies(cls, value: Any) -> dict[str, Any]:
        if value in (None, ""):
            return {}
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            return {"python": cls._normalize_string_list(value)}
        if isinstance(value, (list, tuple, set)):
            return {"python": [str(x).strip() for x in value if str(x).strip()]}
        raise ValueError("dependencies 必须是对象、字符串或列表")

    @classmethod
    def _build_market_config(
        cls,
        plugin_id: str,
        metadata: dict[str, Any] | None,
        author_key: str | None = None,
    ) -> dict[str, Any]:
        metadata = metadata or {}
        cn_name = metadata.get("cn_name", metadata.get("name", plugin_id))
        config: dict[str, Any] = {
            "schema_version": MARKET_SCHEMA_VERSION,
            "id": plugin_id,
            "name": metadata.get("name", plugin_id),
            "cn_name": cn_name,
            "version": metadata.get("version", "1.0.0"),
            "author": metadata.get("author", "anonymous"),
            "description": metadata.get("description", ""),
            "full_description": metadata.get("full_description", ""),
            "category": metadata.get("category", "其他"),
            "features": cls._as_list(metadata.get("features", [])),
            "usage": metadata.get("usage", ""),
            "commands": cls._as_list(metadata.get("commands", [])),
            "changelog": metadata.get("changelog", "v1.0.0 - 初始版本"),
            "notes": metadata.get("notes", ""),
            "roles": cls._normalize_string_list(
                metadata.get("roles"),
                default=DEFAULT_PLUGIN_ROLES,
                aliases=ROLE_ALIASES,
                allowed=VALID_PLUGIN_ROLES,
                field="roles",
            ),
            "platforms": cls._normalize_string_list(
                metadata.get("platforms"),
                default=DEFAULT_PLUGIN_PLATFORMS,
                field="platforms",
            ),
            "permissions": cls._normalize_string_list(metadata.get("permissions", [])),
            "requires_service": cls._normalize_string_list(
                metadata.get("requires_service", metadata.get("requires_services", []))
            ),
            "assets": cls._normalize_asset_paths(metadata.get("assets", [])),
            "dependencies": cls._normalize_dependencies(metadata.get("dependencies", {})),
        }
        if author_key:
            config["author_key"] = author_key
        return config

    @classmethod
    def _normalize_market_entry(cls, entry: dict[str, Any]) -> dict[str, Any]:
        try:
            normalized = cls._build_market_config(
                str(entry.get("id") or entry.get("name") or ""),
                entry,
            )
        except ValueError:
            normalized = {
                "schema_version": MARKET_SCHEMA_VERSION,
                "roles": DEFAULT_PLUGIN_ROLES[:],
                "platforms": DEFAULT_PLUGIN_PLATFORMS[:],
                "permissions": [],
                "requires_service": [],
                "assets": [],
                "dependencies": {},
            }
        merged = dict(entry)
        normalized_fields = {
            "schema_version",
            "features",
            "commands",
            "roles",
            "platforms",
            "permissions",
            "requires_service",
            "assets",
            "dependencies",
        }
        for key, value in normalized.items():
            if key in normalized_fields:
                merged[key] = value
            else:
                merged.setdefault(key, value)
        return merged

    @classmethod
    def _plugin_index_entry(
        cls,
        config: dict[str, Any],
        *,
        repo: str,
        plugin_id: str,
    ) -> dict[str, Any]:
        return {
            "schema_version": MARKET_SCHEMA_VERSION,
            "id": plugin_id,
            "name": config.get("name", plugin_id),
            "cn_name": config.get("cn_name", config.get("name", plugin_id)),
            "version": config.get("version", "1.0.0"),
            "author": config.get("author", "anonymous"),
            "description": config.get("description", ""),
            "category": config.get("category", "其他"),
            "roles": config.get("roles", DEFAULT_PLUGIN_ROLES),
            "platforms": config.get("platforms", DEFAULT_PLUGIN_PLATFORMS),
            "permissions": config.get("permissions", []),
            "requires_service": config.get("requires_service", []),
            "assets": config.get("assets", []),
            "dependencies": config.get("dependencies", {}),
            "downloads": int(config.get("downloads", 0) or 0),
            "rating": float(config.get("rating", 5.0) or 5.0),
            "featured": bool(config.get("featured", False)),
            "download_url": f"{GITHUB_RAW}/{repo}/main/plugins/{plugin_id}",
        }

    @classmethod
    def _local_path_for_asset(cls, plugin_dir: Path, rel_path: str) -> Path:
        safe = cls._normalize_asset_paths([rel_path])[0]
        return plugin_dir.joinpath(*PurePosixPath(safe).parts)

    @classmethod
    def _iter_extra_upload_files(
        cls,
        plugin_dir: Path,
        config: dict[str, Any],
    ) -> list[tuple[str, Path]]:
        candidates: set[str] = set()
        for name in ("README.md", "readme.md"):
            if (plugin_dir / name).is_file():
                candidates.add(name)
        for folder in ("assets", "prompts", "docs"):
            root = plugin_dir / folder
            if root.exists():
                for file in root.rglob("*"):
                    if file.is_file() and "__pycache__" not in file.parts:
                        candidates.add(file.relative_to(plugin_dir).as_posix())
        for rel in config.get("assets", []) or []:
            candidates.add(rel)

        files: list[tuple[str, Path]] = []
        for rel in sorted(candidates):
            try:
                local = cls._local_path_for_asset(plugin_dir, rel)
            except (IndexError, ValueError):
                continue
            if local.is_file() and local.suffix not in {".pyc", ".pyo"}:
                files.append((rel.replace("\\", "/"), local))
        return files

    # ================================================================
    # 获取插件列表
    # ================================================================

    async def fetch_list(self) -> list[dict]:
        """
        从GitHub获取插件列表。失败时用本地缓存兜底。

        Returns:
            插件列表，每个插件包含 id/name/version/author/description/installed 等
        """
        plugins = await self._fetch_from_github()
        if not plugins:
            plugins = self._load_cache()

        plugins = [self._normalize_market_entry(p) for p in plugins if isinstance(p, dict)]

        # 标记已安装的
        installed = self._get_installed_ids()
        for p in plugins:
            p["installed"] = p.get("id", "") in installed

        return plugins

    async def _fetch_from_github(self) -> list[dict]:
        """从GitHub拉取plugins.json。"""
        url = f"{GITHUB_API}/repos/{self._repo}/contents/plugins.json"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, headers=self._headers(),
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status != 200:
                        logger.debug(f"[Market] GitHub返回 {resp.status}")
                        return []
                    data = await resp.json()
                    content = base64.b64decode(data.get("content", "")).decode("utf-8")
                    raw = json.loads(content)
                    # 支持两种格式：直接数组 或 {"plugins": [...]}
                    if isinstance(raw, list):
                        plugins = raw
                    elif isinstance(raw, dict):
                        plugins = raw.get("plugins", [])
                    else:
                        plugins = []
                    plugins = [self._normalize_market_entry(p) for p in plugins if isinstance(p, dict)]
                    if plugins:
                        self._save_cache(plugins)
                        logger.debug(f"[Market] 从GitHub拉取 {len(plugins)} 个插件")
                        return plugins
        except Exception as e:
            logger.debug(f"[Market] GitHub获取失败: {e}")
        return []

    def _load_cache(self) -> list[dict]:
        if self._cache_path.exists():
            try:
                data = json.loads(self._cache_path.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    logger.debug(f"[Market] 使用缓存 {len(data)} 个插件")
                    return data
            except Exception:
                pass
        return []

    def _save_cache(self, plugins: list[dict]) -> None:
        try:
            self._cache_path.write_text(
                json.dumps(plugins, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass

    def _get_installed_ids(self) -> set[str]:
        """获取已安装的插件ID列表。"""
        installed = {plugin_id for plugin_id, _, _ in self._iter_plugin_dirs()}
        for f in self._plugins_dir.glob("*.py"):
            if not f.name.startswith("_"):
                installed.add(f.stem)
        return installed

    # ================================================================
    # 安装插件
    # ================================================================

    async def install(self, plugin_id: str) -> dict:
        """
        从市场安装插件。

        Returns:
            {"success": bool, "message": str}
        """
        plugin_id, error = self._validated_id(plugin_id)
        if error:
            return error
        assert plugin_id is not None

        if self._find_plugin_dir(plugin_id):
            return {"success": False, "message": f"{plugin_id} 已安装"}
        community_dir = contained_path(self._plugins_dir, "community")
        community_dir.mkdir(parents=True, exist_ok=True)
        plugin_dir = contained_path(community_dir, plugin_id)

        # 从GitHub下载
        try:
            download_url = f"{GITHUB_RAW}/{self._repo}/main/plugins/{plugin_id}"

            async with aiohttp.ClientSession() as session:
                # 下载plugin.py
                async with session.get(
                    f"{download_url}/plugin.py",
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status != 200:
                        return {"success": False, "message": f"下载失败: HTTP {resp.status}"}
                    plugin_code = await resp.text()

                # 下载config.json（可选）
                config_data = {}
                try:
                    async with session.get(
                        f"{download_url}/config.json",
                        timeout=aiohttp.ClientTimeout(total=5),
                    ) as resp2:
                        if resp2.status == 200:
                            config_data = await resp2.json()
                except Exception:
                    pass
                if isinstance(config_data, dict) and config_data:
                    config_data = self._normalize_market_entry(
                        {"id": plugin_id, **config_data}
                    )
                else:
                    config_data = self._build_market_config(
                        plugin_id,
                        {"name": plugin_id, "cn_name": plugin_id},
                    )
                # Installing files is not consent to execute third-party code.
                # The user must explicitly enable it after reviewing permissions.
                config_data["enabled"] = False
                config_data["source"] = "market"
                config_data["trust_level"] = "community"
                config_data["safety_model"] = "static_analysis"

                extra_files: dict[str, bytes] = {}
                for rel_path in config_data.get("assets", []) or []:
                    try:
                        safe_rel = self._normalize_asset_paths([rel_path])[0]
                    except (IndexError, ValueError) as e:
                        logger.warning(f"[Market] 跳过不安全资源路径 {rel_path}: {e}")
                        continue
                    try:
                        async with session.get(
                            f"{download_url}/{safe_rel}",
                            timeout=aiohttp.ClientTimeout(total=10),
                        ) as asset_resp:
                            if asset_resp.status == 200:
                                extra_files[safe_rel] = await asset_resp.read()
                            else:
                                logger.warning(
                                    f"[Market] 资源下载失败 {plugin_id}/{safe_rel}: "
                                    f"HTTP {asset_resp.status}"
                                )
                    except Exception as e:
                        logger.warning(f"[Market] 资源下载失败 {plugin_id}/{safe_rel}: {e}")

            # 自动替换import路径（兼容v2插件）
            import re as _re
            # v2的路径 → 我们的路径
            _import_replacements = [
                (r'from src\.core\.plugins\.base import', 'from white_salary.core.plugins.base import'),
                (r'from src\.core\.plugins import', 'from white_salary.core.plugins import'),
                (r'from src\.core\.plugin_manager import PluginBase', 'from white_salary.core.plugins.base import Plugin'),
                (r'class (\w+)\(PluginBase\)', r'class \1(Plugin)'),
                (r'from src\.', 'from white_salary.'),
            ]
            for pattern, replacement in _import_replacements:
                plugin_code = _re.sub(pattern, replacement, plugin_code)

            from white_salary.core.plugins.sandbox import check_code_safety, check_plugin_tree_safety

            is_safe, issues = check_code_safety(
                plugin_code,
                permissions=config_data.get("permissions", []),
            )
            if not is_safe:
                return {
                    "success": False,
                    "message": "插件未通过安全检查",
                    "issues": issues,
                }

            # Build the complete package in a hidden staging directory. Discover
            # ignores hidden directories; only a fully validated package is
            # atomically moved to its final id.
            stage = contained_path(
                community_dir,
                f".install-{plugin_id}-{uuid.uuid4().hex}",
            )
            try:
                stage.mkdir(parents=False, exist_ok=False)
                (stage / "plugin.py").write_text(plugin_code, encoding="utf-8")
                (stage / "__init__.py").write_text(
                    "from .plugin import *\n", encoding="utf-8"
                )
                (stage / "config.json").write_text(
                    json.dumps(config_data, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                for rel_path, content in extra_files.items():
                    asset_path = self._local_path_for_asset(stage, rel_path)
                    asset_path.parent.mkdir(parents=True, exist_ok=True)
                    asset_path.write_bytes(content)

                tree_safe, tree_issues = check_plugin_tree_safety(
                    stage,
                    permissions=config_data.get("permissions", []),
                )
                if not tree_safe:
                    return {
                        "success": False,
                        "message": "插件包未通过安全检查",
                        "issues": tree_issues,
                    }
                if plugin_dir.exists():
                    return {"success": False, "message": f"{plugin_id} 已安装"}
                stage.replace(plugin_dir)
            finally:
                if stage.exists():
                    shutil.rmtree(stage, ignore_errors=True)

            logger.info(f"[Market] 已安装: {plugin_id}")

            # 异步更新下载计数
            import asyncio
            asyncio.ensure_future(self._increment_download_count(plugin_id))

            return {
                "success": True,
                "enabled": False,
                "message": f"已安装 {plugin_id}（默认禁用，请确认权限后手动启用）",
            }

        except Exception as e:
            return {"success": False, "message": f"安装失败: {e}"}

    # ================================================================
    # 卸载插件
    # ================================================================

    def uninstall(self, plugin_id: str) -> dict:
        """卸载插件（删除文件）。"""
        plugin_id, error = self._validated_id(plugin_id)
        if error:
            return error
        assert plugin_id is not None
        plugin_dir = self._find_plugin_dir(plugin_id)
        plugin_file = contained_path(self._plugins_dir, f"{plugin_id}.py")

        if plugin_dir and plugin_dir.parent == self._plugins_dir / "builtin":
            return {"success": False, "message": f"{plugin_id} 是内置插件，可禁用但不能卸载"}
        if plugin_dir and plugin_dir.exists():
            shutil.rmtree(str(plugin_dir))
            logger.info(f"[Market] 已卸载: {plugin_id}")
            return {"success": True, "message": f"已卸载 {plugin_id}"}
        elif plugin_file.exists():
            plugin_file.unlink()
            return {"success": True, "message": f"已卸载 {plugin_id}"}
        else:
            return {"success": False, "message": f"{plugin_id} 未安装"}

    # ================================================================
    # 获取已安装插件信息
    # ================================================================

    def get_installed(self) -> list[dict]:
        """获取已安装的插件列表（含本地信息）。"""
        result = []
        for plugin_id, d, source in self._iter_plugin_dirs():
            info = {
                "id": plugin_id,
                "name": plugin_id,
                "installed": True,
                "source": source,
                "path": str(d),
            }
            config_path = d / "config.json"
            if config_path.exists():
                try:
                    config = json.loads(config_path.read_text(encoding="utf-8"))
                    info.update(self._normalize_market_entry({"id": plugin_id, **config}))
                except Exception:
                    info.update(self._build_market_config(plugin_id, {"name": plugin_id}))
            else:
                info.update(self._build_market_config(plugin_id, {"name": plugin_id}))
            result.append(info)
        for f in self._plugins_dir.glob("*.py"):
            if not f.name.startswith("_"):
                info = {
                    "id": f.stem,
                    "name": f.stem,
                    "installed": True,
                    "source": "root_file",
                    "path": str(f),
                }
                info.update(self._build_market_config(f.stem, {"name": f.stem}))
                result.append(info)
        return result

    # ================================================================
    # 提交插件到市场
    # ================================================================

    async def submit(self, plugin_id: str, plugin_code: str,
                     metadata: dict) -> dict:
        """提交插件到GitHub市场。"""
        if not self._token:
            return {"success": False, "message": "未配置GitHub Token"}

        plugin_id, error = self._validated_id(plugin_id)
        if error:
            return error
        assert plugin_id is not None

        from white_salary.core.plugins.sandbox import check_code_safety

        metadata = metadata or {}
        is_safe, issues = check_code_safety(
            plugin_code,
            permissions=metadata.get("permissions", []),
        )
        if not is_safe:
            return {
                "success": False,
                "message": "插件未通过安全检查",
                "issues": issues,
            }

        try:
            # 生成作者密钥
            author_key = hashlib.md5(
                f"{metadata.get('author', '')}{plugin_id}{time.time()}".encode()
            ).hexdigest()[:16]
            config = self._build_market_config(plugin_id, metadata, author_key)

            async with aiohttp.ClientSession() as session:
                # 1. 上传plugin.py
                encoded = base64.b64encode(plugin_code.encode("utf-8")).decode()
                await self._github_put(
                    session,
                    f"plugins/{plugin_id}/plugin.py",
                    encoded,
                    f"Add plugin: {metadata.get('cn_name', plugin_id)}",
                )

                # 2. 上传config.json（含author_key）
                cn_name = config.get("cn_name", plugin_id)
                config_encoded = base64.b64encode(
                    json.dumps(config, ensure_ascii=False, indent=2).encode("utf-8")
                ).decode()
                await self._github_put(
                    session,
                    f"plugins/{plugin_id}/config.json",
                    config_encoded,
                    f"Add config for: {cn_name}",
                )

                # 3. 更新plugins.json索引（关键！不更新的话新插件不显示在列表里）
                await self._update_plugins_index(session, plugin_id, config)

            logger.info(f"[Market] 已提交: {plugin_id}")
            return {
                "success": True,
                "message": f"已提交 {plugin_id}，请保存作者密钥用于后续删除",
                "author_key": author_key,
            }

        except ValueError as e:
            return {"success": False, "message": f"元数据错误: {e}"}
        except Exception as e:
            return {"success": False, "message": f"提交失败: {e}"}

    async def _update_plugins_index(self, session, plugin_id: str,
                                    config: dict) -> None:
        """更新GitHub上的plugins.json索引。"""
        url = f"{GITHUB_API}/repos/{self._repo}/contents/plugins.json"
        sha = None
        plugins_data = {"version": "1.0.0", "last_updated": "", "plugins": []}

        # 读取当前plugins.json
        async with session.get(url, headers=self._headers()) as resp:
            if resp.status == 200:
                data = await resp.json()
                sha = data.get("sha")
                content = base64.b64decode(data["content"]).decode("utf-8")
                plugins_data = json.loads(content)

        # 确保plugins是列表
        if isinstance(plugins_data, list):
            plugins_data = {"version": "1.0.0", "plugins": plugins_data}
        plugins_list = plugins_data.get("plugins", [])

        # 删除旧的同名条目，同时保留下载量/评分/精选等市场统计。
        old_entry = next((p for p in plugins_list if p.get("id") == plugin_id), {})
        plugins_list = [p for p in plugins_list if p.get("id") != plugin_id]

        # 加入新条目
        cn_name = config.get("cn_name", config.get("name", plugin_id))
        new_entry = self._plugin_index_entry(
            {**old_entry, **config},
            repo=self._repo,
            plugin_id=plugin_id,
        )
        plugins_list.append(new_entry)

        plugins_data["plugins"] = plugins_list
        plugins_data["last_updated"] = time.strftime("%Y-%m-%d")

        # 写回
        new_content = base64.b64encode(
            json.dumps(plugins_data, ensure_ascii=False, indent=2).encode("utf-8")
        ).decode()
        body = {"message": f"Add plugin to list: {cn_name}", "content": new_content}
        if sha:
            body["sha"] = sha
        async with session.put(url, headers=self._headers(), json=body) as resp:
            if resp.status not in (200, 201):
                logger.warning(f"[Market] plugins.json更新失败: {resp.status}")

    async def _increment_download_count(self, plugin_id: str) -> None:
        """异步更新下载计数。"""
        try:
            async with aiohttp.ClientSession() as session:
                url = f"{GITHUB_API}/repos/{self._repo}/contents/plugins.json"
                async with session.get(url, headers=self._headers()) as resp:
                    if resp.status != 200:
                        return
                    data = await resp.json()
                    sha = data.get("sha")
                    content = base64.b64decode(data["content"]).decode("utf-8")
                    plugins_data = json.loads(content)

                if isinstance(plugins_data, list):
                    plugins_data = {"plugins": plugins_data}
                for p in plugins_data.get("plugins", []):
                    if p.get("id") == plugin_id:
                        p["downloads"] = p.get("downloads", 0) + 1
                        break

                new_content = base64.b64encode(
                    json.dumps(plugins_data, ensure_ascii=False, indent=2).encode("utf-8")
                ).decode()
                body = {"message": f"Update download count: {plugin_id}",
                        "content": new_content, "sha": sha}
                await session.put(url, headers=self._headers(), json=body)
        except Exception as e:
            logger.debug(f"[Market] 下载计数更新失败: {e}")

    async def _github_put(self, session, path: str, content_b64: str, message: str) -> None:
        """上传文件到GitHub。"""
        url = f"{GITHUB_API}/repos/{self._repo}/contents/{path}"

        # 检查是否已存在（需要sha来更新）
        sha = None
        async with session.get(url, headers=self._headers()) as resp:
            if resp.status == 200:
                data = await resp.json()
                sha = data.get("sha")

        body = {
            "message": message,
            "content": content_b64,
        }
        if sha:
            body["sha"] = sha

        async with session.put(url, headers=self._headers(), json=body) as resp:
            if resp.status not in (200, 201):
                raise Exception(f"GitHub API {resp.status}: {await resp.text()}")

    # ================================================================
    # 删除市场插件
    # ================================================================

    async def delete_from_market(self, plugin_id: str, auth: str,
                                 auth_type: str = "admin") -> dict:
        """从GitHub市场删除插件（管理员密码或作者密钥）。"""
        plugin_id, error = self._validated_id(plugin_id)
        if error:
            return error
        assert plugin_id is not None
        if not self._token:
            return {"success": False, "message": "未配置GitHub Token"}

        # 验证权限：两条路径
        is_admin = False
        is_author = False

        if auth_type == "admin":
            try:
                config_path = Path("config/github_config.json")
                if config_path.exists():
                    gc = json.loads(config_path.read_text(encoding="utf-8"))
                    if auth == gc.get("admin_password", ""):
                        is_admin = True
            except Exception:
                pass
            if not is_admin:
                return {"success": False, "message": "管理员密码错误"}

        elif auth_type == "author":
            # 从GitHub读取config.json验证author_key
            try:
                async with aiohttp.ClientSession() as session:
                    url = f"{GITHUB_API}/repos/{self._repo}/contents/plugins/{plugin_id}/config.json"
                    async with session.get(url, headers=self._headers()) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            content = base64.b64decode(data["content"]).decode("utf-8")
                            config = json.loads(content)
                            if auth == config.get("author_key", ""):
                                is_author = True
            except Exception:
                pass
            if not is_author:
                return {"success": False, "message": "作者密钥错误"}

        else:
            return {"success": False, "message": "未知验证类型"}

        try:
            async with aiohttp.ClientSession() as session:
                for filename in ["plugin.py", "config.json"]:
                    url = f"{GITHUB_API}/repos/{self._repo}/contents/plugins/{plugin_id}/{filename}"
                    async with session.get(url, headers=self._headers()) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            sha = data["sha"]
                            await session.delete(url, headers=self._headers(), json={
                                "message": f"Delete {plugin_id}/{filename}",
                                "sha": sha,
                            })

                # 从plugins.json索引中移除
                try:
                    idx_url = f"{GITHUB_API}/repos/{self._repo}/contents/plugins.json"
                    async with session.get(idx_url, headers=self._headers()) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            sha = data.get("sha")
                            content = base64.b64decode(data["content"]).decode("utf-8")
                            plugins_data = json.loads(content)
                            if isinstance(plugins_data, list):
                                plugins_data = {"plugins": plugins_data}
                            plugins_data["plugins"] = [
                                p for p in plugins_data.get("plugins", [])
                                if p.get("id") != plugin_id
                            ]
                            new_content = base64.b64encode(
                                json.dumps(plugins_data, ensure_ascii=False, indent=2).encode()
                            ).decode()
                            await session.put(idx_url, headers=self._headers(), json={
                                "message": f"Remove from list: {plugin_id}",
                                "content": new_content, "sha": sha,
                            })
                except Exception:
                    pass

            return {"success": True, "message": f"已从市场删除 {plugin_id}"}
        except Exception as e:
            return {"success": False, "message": f"删除失败: {e}"}

    # ================================================================
    # 同步到GitHub
    # ================================================================

    # ================================================================
    # 启用/禁用
    # ================================================================

    def _find_plugin_dir(self, plugin_id: str) -> Optional[Path]:
        """查找插件目录（支持builtin/community子目录）。"""
        try:
            plugin_id = validate_plugin_id(plugin_id)
        except ValueError:
            return None
        # 直接查找
        d = contained_path(self._plugins_dir, plugin_id)
        if self._is_plugin_dir(d):
            return d
        # 在子目录里查找
        for sub in ["community", "builtin"]:
            d = contained_path(self._plugins_dir, sub, plugin_id)
            if self._is_plugin_dir(d):
                return d
        return None

    def toggle_plugin(self, plugin_id: str, enabled: bool) -> dict:
        """启用/禁用插件。"""
        plugin_id, error = self._validated_id(plugin_id)
        if error:
            return error
        assert plugin_id is not None
        plugin_dir = self._find_plugin_dir(plugin_id)
        if not plugin_dir:
            return {"success": False, "message": f"{plugin_id} 未安装"}
        config_path = plugin_dir / "config.json"
        config = {}
        if config_path.exists():
            try:
                config = json.loads(config_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        config["enabled"] = enabled
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"success": True, "enabled": enabled}

    # ================================================================
    # 代码编辑
    # ================================================================

    def get_plugin_code(self, plugin_id: str) -> dict:
        """获取插件源代码。"""
        plugin_id, error = self._validated_id(plugin_id)
        if error:
            return error
        assert plugin_id is not None
        plugin_dir = self._find_plugin_dir(plugin_id)
        if not plugin_dir:
            return {"success": False, "message": "插件不存在"}
        plugin_file = plugin_dir / "plugin.py"
        if not plugin_file.exists():
            return {"success": False, "message": "plugin.py不存在"}
        code = plugin_file.read_text(encoding="utf-8")
        config = {}
        config_path = plugin_dir / "config.json"
        if config_path.exists():
            try:
                config = json.loads(config_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"success": True, "code": code, "config": config}

    def save_plugin_code(self, plugin_id: str, code: str,
                         metadata: dict = None) -> dict:
        """保存插件代码。"""
        plugin_id, error = self._validated_id(plugin_id)
        if error:
            return error
        assert plugin_id is not None
        plugin_dir = self._find_plugin_dir(plugin_id)
        if not plugin_dir:
            return {"success": False, "message": "插件不存在"}
        plugin_file = plugin_dir / "plugin.py"
        if not plugin_file.exists():
            return {"success": False, "message": "plugin.py不存在"}
        config_path = plugin_dir / "config.json"
        config = {}
        if config_path.exists():
            try:
                config = json.loads(config_path.read_text(encoding="utf-8"))
            except Exception:
                config = {}
        if metadata:
            config.update(metadata)

        from white_salary.core.plugins.sandbox import check_code_safety

        is_safe, issues = check_code_safety(
            code,
            permissions=config.get("permissions", []),
        )
        if not is_safe:
            return {
                "success": False,
                "message": "代码未通过安全检查，未保存",
                "issues": issues,
            }
        plugin_file.write_text(code, encoding="utf-8")
        if metadata:
            config = self._normalize_market_entry({"id": plugin_id, **config})
            config["updated_at"] = time.strftime("%Y-%m-%d %H:%M")
            config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"success": True, "message": "已保存"}

    # ================================================================
    # 插件模板生成
    # ================================================================

    def create_from_template(self, plugin_id: str, name: str = "",
                             description: str = "",
                             plugin_type: str = "classic") -> dict:
        """从模板创建新插件。"""
        plugin_id, error = self._validated_id(plugin_id)
        if error:
            return error
        assert plugin_id is not None

        if self._find_plugin_dir(plugin_id):
            return {"success": False, "message": f"{plugin_id} 已存在"}
        plugin_dir = contained_path(self._plugins_dir, "community", plugin_id)

        # 生成类名
        class_name = ''.join(w.capitalize() for w in plugin_id.split('_')) + 'Plugin'
        display_name = name or plugin_id
        template_type = (plugin_type or "classic").strip().lower()
        role_map = {
            "classic": DEFAULT_PLUGIN_ROLES,
            "interceptor": ["interceptor"],
            "observer": ["observer"],
            "rewriter": ["rewriter"],
            "tool": ["tool_provider"],
            "tool_provider": ["tool_provider"],
        }
        roles = role_map.get(template_type, DEFAULT_PLUGIN_ROLES)
        role_line = "" if template_type == "classic" else f"        roles={roles!r},\n"

        template = f'''"""
{display_name} 插件
{description}
"""

from white_salary.core.plugins.base import Plugin, PluginMeta


class {class_name}(Plugin):
    meta = PluginMeta(
        name="{plugin_id}",
        description="{description or display_name}",
        version="1.0.0",
        author="",
{role_line.rstrip()}
    )

    async def on_load(self):
        print(f"[Plugin:{{self.meta.name}}] 加载完成")

    async def on_observe(self, text, user_id="", metadata=None):
        # observer 插件在这里记录/学习消息，不抢答
        return None

    async def on_message(self, text, user_id=""):
        # interceptor 插件在这里写消息处理逻辑
        # 返回 str = 拦截消息，返回 None = 不拦截
        return None

    async def on_reply(self, text):
        # rewriter 插件可以在这里改写 AI 最终回复
        return text

    def get_tools(self):
        # tool_provider 插件在这里注册工具
        return []
'''
        plugin_dir.mkdir(parents=True, exist_ok=True)
        (plugin_dir / "plugin.py").write_text(template, encoding="utf-8")
        (plugin_dir / "__init__.py").write_text("from .plugin import *\n", encoding="utf-8")
        config = self._build_market_config(
            plugin_id,
            {
                "name": display_name,
                "cn_name": display_name,
                "description": description,
                "version": "1.0.0",
                "roles": roles,
                "platforms": ["all"],
            },
        )
        config["enabled"] = True
        config["plugin_type"] = template_type
        (plugin_dir / "config.json").write_text(
            json.dumps(config, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        return {"success": True, "message": f"已创建 {plugin_id}", "path": str(plugin_dir)}

    async def sync_to_github(self) -> dict:
        """同步本地plugins/到GitHub。"""
        if not self._token:
            return {"success": False, "message": "未配置GitHub Token"}

        synced = 0
        errors = []

        async with aiohttp.ClientSession() as session:
            for _, d, source in self._iter_plugin_dirs():
                if source == "builtin":
                    continue
                plugin_py = d / "plugin.py"
                if not plugin_py.exists():
                    continue

                try:
                    code = plugin_py.read_text(encoding="utf-8")
                    encoded = base64.b64encode(code.encode("utf-8")).decode()
                    await self._github_put(
                        session,
                        f"plugins/{d.name}/plugin.py",
                        encoded,
                        f"Sync plugin: {d.name}",
                    )

                    config_path = d / "config.json"
                    config: dict[str, Any]
                    if config_path.exists():
                        config_code = config_path.read_text(encoding="utf-8")
                        try:
                            config = self._normalize_market_entry(
                                {"id": d.name, **json.loads(config_code)}
                            )
                            config_code = json.dumps(
                                config,
                                ensure_ascii=False,
                                indent=2,
                            )
                        except Exception:
                            config = self._build_market_config(d.name, {"name": d.name})
                            config_code = json.dumps(
                                config,
                                ensure_ascii=False,
                                indent=2,
                            )
                        config_encoded = base64.b64encode(config_code.encode("utf-8")).decode()
                        await self._github_put(
                            session,
                            f"plugins/{d.name}/config.json",
                            config_encoded,
                            f"Sync config: {d.name}",
                        )
                    else:
                        config = self._build_market_config(d.name, {"name": d.name})

                    for rel_path, file_path in self._iter_extra_upload_files(d, config):
                        file_encoded = base64.b64encode(file_path.read_bytes()).decode()
                        await self._github_put(
                            session,
                            f"plugins/{d.name}/{rel_path}",
                            file_encoded,
                            f"Sync plugin asset: {d.name}/{rel_path}",
                        )

                    await self._update_plugins_index(session, d.name, config)

                    synced += 1
                except Exception as e:
                    errors.append(f"{d.name}: {e}")

        msg = f"同步完成: {synced}个插件"
        if errors:
            msg += f", {len(errors)}个失败"
        return {"success": True, "message": msg, "synced": synced, "errors": errors}
