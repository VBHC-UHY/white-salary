"""Shared validation helpers for plugin identifiers and filesystem paths."""

from __future__ import annotations

import re
from pathlib import Path


PLUGIN_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def validate_plugin_id(value: object) -> str:
    """Return a canonical plugin id or raise ``ValueError``.

    Market ids are deliberately narrower than arbitrary filenames. Keeping one
    grammar for URLs, local directories and GitHub paths prevents traversal and
    ambiguous aliases across the three plugin-market repositories.
    """

    if not isinstance(value, str):
        raise ValueError("插件ID必须是字符串")
    plugin_id = value.strip()
    if plugin_id != value or not PLUGIN_ID_PATTERN.fullmatch(plugin_id):
        raise ValueError("插件ID格式错误（小写字母开头，仅含小写字母、数字和下划线，最长64位）")
    return plugin_id


def contained_path(root: Path, *parts: str) -> Path:
    """Resolve a child path and prove it remains under ``root``.

    ``Path.resolve`` also follows existing symlinks, so a symlink inside the
    plugin tree cannot redirect market delete/edit operations outside it.
    """

    base = root.resolve()
    candidate = base.joinpath(*parts).resolve()
    if not candidate.is_relative_to(base):
        raise ValueError("插件路径越出允许目录")
    return candidate
