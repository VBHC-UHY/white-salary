"""Windows installer interpreter discovery regression tests."""

from __future__ import annotations

import os
from pathlib import Path
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
    assert "[CHECK] Done" in result.stdout
