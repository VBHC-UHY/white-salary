"""Print the configured GPT-SoVITS install directory.

Resolution order:
1. WS_GPT_SOVITS_DIR
2. conf.yaml -> external_tools.gpt_sovits_dir

This script is intentionally dependency-light so .bat files and Electron can
call it before the backend is running.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any


def _clean(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        text = text[1:-1].strip()
    return text


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _read_conf_yaml() -> dict[str, Any]:
    conf_path = _project_root() / "conf.yaml"
    if not conf_path.exists():
        return {}
    try:
        import yaml

        data = yaml.safe_load(conf_path.read_text(encoding="utf-8")) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _read_conf_text() -> str:
    conf_path = _project_root() / "conf.yaml"
    if not conf_path.exists():
        return ""
    try:
        return conf_path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _from_conf() -> str:
    data = _read_conf_yaml()
    external_tools = data.get("external_tools") if isinstance(data, dict) else None
    if isinstance(external_tools, dict):
        value = _clean(external_tools.get("gpt_sovits_dir"))
        if value:
            return value

    # Fallback parser for very early installs where PyYAML is not available yet.
    text = _read_conf_text()
    match = re.search(r"(?m)^\s*gpt_sovits_dir\s*:\s*(.*?)\s*$", text)
    if match:
        return _clean(match.group(1).split("#", 1)[0])
    return ""


def _from_detection_cache() -> str:
    """读自动探测的落盘缓存（data/tool_paths.json）。

    保持本脚本"零项目依赖"的设计：只读一个小 JSON，不 import white_salary。
    缓存由后端启动时的后台预热生成。
    """
    cache_path = _project_root() / "data" / "tool_paths.json"
    if not cache_path.exists():
        return ""
    try:
        import json

        data = json.loads(cache_path.read_text(encoding="utf-8"))
        value = _clean((data.get("paths") or {}).get("gpt_sovits_dir"))
        # 缓存可能已过时（用户挪走了目录），用前验证
        if value and Path(value).exists():
            return value
    except Exception:
        pass
    return ""


def _from_live_scan() -> str:
    """兜底：真扫一遍。

    只在"环境变量、conf.yaml、缓存都没有"时才走到这里，也就是全新机器的第一次
    启动。本脚本是独立进程、不在服务的事件循环里，阻塞十几秒是可以接受的——
    比让用户看到"TTS 起不来"然后去翻文档好得多。扫完由 tool_discovery 落盘，
    下次启动就只需读缓存。
    """
    try:
        sys.path.insert(0, str(_project_root() / "src"))
        from white_salary.adapters.tools import tool_discovery

        tool_discovery.configure_cache_path(_project_root() / "data" / "tool_paths.json")
        return tool_discovery.detect("gpt_sovits_dir", allow_scan=True)
    except Exception:
        return ""


def resolve_gpt_sovits_dir() -> Path | None:
    value = (
        _clean(os.environ.get("WS_GPT_SOVITS_DIR"))
        or _from_conf()
        or _from_detection_cache()
        or _from_live_scan()
    )
    return Path(value) if value else None


def main() -> int:
    path = resolve_gpt_sovits_dir()
    if path is not None:
        print(str(path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
