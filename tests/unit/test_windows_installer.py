"""Windows installer interpreter discovery regression tests."""

from __future__ import annotations

import ctypes
import locale
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _cmd_safe_batch_path(path: Path) -> str:
    """返回一个 cmd.exe 一定能解析的批处理文件路径。

    ``安装.bat`` 是中文文件名。cmd.exe 解析 ``/c`` 后面的命令行时使用系统
    ANSI 代码页，当区域设置无法表示这些汉字时（例如英文/西欧环境 ACP=1252），
    文件名会被降级成 ``??.bat``，于是报 "'??.bat' is not recognized"。
    这与安装器本身完全无关，纯粹是测试调用方式的问题——中文 Windows
    （ACP=936）上双击或命令行运行都正常。

    无法用 ANSI 表示时改用 8.3 短路径（纯 ASCII），它指向的仍是同一个真实
    文件，``%~dp0`` 等路径语义不受影响。注意必须传**完整短路径**而不是短
    文件名：裸文件名会走 cwd/PATH 查找，而目录项里记录的是长名，查不到别名。

    若系统禁用了 8.3 短名生成，则跳过该用例并说明原因，而不是长期红着——
    长期失败的测试会掩盖安装器真正的回归。
    """
    try:
        str(path).encode(locale.getpreferredencoding(False))
        return str(path)
    except (UnicodeEncodeError, LookupError):
        pass

    buffer = ctypes.create_unicode_buffer(1024)
    length = ctypes.windll.kernel32.GetShortPathNameW(str(path), buffer, 1024)
    short_path = buffer.value if length else ""
    if short_path and short_path != str(path):
        return short_path

    pytest.skip(
        "当前区域设置无法用 ANSI 代码页表示 '安装.bat'，且系统禁用了 8.3 短名，"
        "无法通过 cmd.exe 调用该安装器"
    )


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
        [
            os.environ.get("COMSPEC", "cmd.exe"),
            "/d",
            "/c",
            _cmd_safe_batch_path(PROJECT_ROOT / "安装.bat"),
            "/check",
        ],
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
        [
            os.environ.get("COMSPEC", "cmd.exe"),
            "/d",
            "/c",
            _cmd_safe_batch_path(tmp_path / "安装.bat"),
        ],
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
