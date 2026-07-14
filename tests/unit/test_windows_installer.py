"""Windows installer interpreter discovery regression tests."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.skipif(os.name != "nt", reason="Windows batch installer")
def test_installer_discovers_uv_managed_python_without_path_python(tmp_path: Path) -> None:
    """A uv-managed interpreter must work even when ``python`` is absent from PATH."""
    fake_uv = tmp_path / "uv.cmd"
    fake_uv.write_text(
        "@echo off\r\n"
        "if /i \"%~1\"==\"python\" if /i \"%~2\"==\"find\" "
        f"echo {sys.executable}\r\n",
        encoding="utf-8",
    )

    env = os.environ.copy()
    env.pop("WS_PYTHON", None)
    env["PATH"] = os.pathsep.join([str(tmp_path), str(Path(os.environ["SystemRoot"]) / "System32")])
    result = subprocess.run(
        [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", "安装.bat", "/check"],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "uv-managed Python 3.12" in result.stdout
    assert "--no-python-downloads --no-cache" in (PROJECT_ROOT / "安装.bat").read_text(
        encoding="utf-8"
    )
    assert "Refusing to remove .venv" in (PROJECT_ROOT / "安装.bat").read_text(
        encoding="utf-8"
    )
    assert "import white_salary" in (PROJECT_ROOT / "安装.bat").read_text(
        encoding="utf-8"
    )
    assert "frontend\\node_modules\\electron\\package.json" in (
        PROJECT_ROOT / "安装.bat"
    ).read_text(encoding="utf-8")
    assert "[CHECK] Done" in result.stdout


@pytest.mark.skipif(os.name != "nt", reason="Windows batch installer")
def test_installer_refuses_to_delete_unidentified_nonempty_venv(
    tmp_path: Path,
) -> None:
    shutil.copy2(PROJECT_ROOT / "安装.bat", tmp_path / "安装.bat")
    sentinel = tmp_path / ".venv" / "keep-me.txt"
    sentinel.parent.mkdir()
    sentinel.write_text("not a virtualenv\n", encoding="utf-8")

    env = os.environ.copy()
    env["WS_PYTHON"] = sys.executable
    result = subprocess.run(
        [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", "安装.bat"],
        cwd=tmp_path,
        env=env,
        input="\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )

    assert result.returncode != 0
    assert "Refusing to remove .venv" in result.stdout
    assert sentinel.read_text(encoding="utf-8") == "not a virtualenv\n"
