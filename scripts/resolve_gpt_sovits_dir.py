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


def resolve_gpt_sovits_dir() -> Path | None:
    value = _clean(os.environ.get("WS_GPT_SOVITS_DIR")) or _from_conf()
    return Path(value) if value else None


def main() -> int:
    path = resolve_gpt_sovits_dir()
    if path is not None:
        print(str(path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
