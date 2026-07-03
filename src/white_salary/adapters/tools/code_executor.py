"""
white_salary/adapters/tools/code_executor.py

代码执行器 — 执行AI生成的Python代码，支持PC控制。

两种模式：
  1. 普通模式：安全沙箱，限制危险操作
  2. PC控制模式：放开限制，注入PC控制函数库

PC控制函数自动注入：
  click(), type_text(), open_app(), smart_click() 等30+函数
  AI直接调用函数名，不需要写import

执行后自动截屏：
  PC控制操作执行后，自动截屏让主模型看到结果
"""

import asyncio
import os
import sys
import tempfile
import textwrap
from pathlib import Path
from typing import Optional

from loguru import logger


MAX_OUTPUT_LENGTH = 3000
DEFAULT_TIMEOUT = 30  # PC操作可能需要更长时间


# PC控制关键词 — 检测到这些说明是PC控制代码，放开限制
_PC_KEYWORDS = [
    "click(", "double_click(", "right_click(", "move_to(", "drag_to(",
    "scroll(", "type_text(", "hotkey(", "press(", "open_app(", "open_url(",
    "open_file(", "open_folder(", "smart_click(", "screenshot(",
    "get_mouse_pos(", "get_screen_size(", "find_on_screen(",
    "wait(", "run_command(", "get_window_list(",
    "pyautogui", "pyperclip",
]

# 始终禁止的危险操作（即使是PC控制模式也不行）
_ALWAYS_BLOCKED = [
    "shutil.rmtree", "os.rmdir", "os.unlink",  # 删除目录/文件
    "format(", "fdisk",                          # 格式化
    "shutdown", "os.system('shutdown",           # 关机
    "__import__('ctypes",                        # 底层调用
]


async def execute_python(code: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    """
    执行Python代码。

    自动检测是否是PC控制代码：
      - 是：注入PC函数库，放开os/subprocess限制
      - 不是：安全沙箱模式

    Args:
        code: Python代码
        timeout: 超时秒数

    Returns:
        执行结果 + （如果是PC控制）自动截屏提示
    """
    if not code or not code.strip():
        return "请提供要执行的Python代码"

    # 始终禁止的操作
    for blocked in _ALWAYS_BLOCKED:
        if blocked in code:
            return f"[安全拦截] 禁止使用: {blocked}"

    # 检测是否是PC控制代码
    is_pc_control = any(kw in code for kw in _PC_KEYWORDS)

    if is_pc_control:
        return await _execute_pc_code(code, timeout)
    else:
        return await _execute_sandbox(code, timeout)


async def _execute_pc_code(code: str, timeout: int) -> str:
    """PC控制模式 — 注入函数库，放开限制。"""
    # 构建完整脚本：导入PC函数库 + 用户代码
    script = textwrap.dedent(f"""\
        import sys, os, time
        sys.path.insert(0, r"{Path(__file__).parent.parent.parent.parent}")
        os.environ["PYTHONPATH"] = r"{Path(__file__).parent.parent.parent.parent}" + os.sep + "src"

        # 注入PC控制函数
        from white_salary.adapters.tools.pc_helpers import PC_FUNCTIONS
        globals().update(PC_FUNCTIONS)

        # 用户代码
        try:
    """)

    # 缩进用户代码
    for line in code.strip().split("\n"):
        script += f"        {line}\n"

    script += textwrap.dedent("""\
        except Exception as _e:
            print(f"[执行错误] {_e}")
    """)

    # 写临时文件执行（避免命令行参数长度限制）
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8",
        dir=str(Path(__file__).parent),
    )
    try:
        tmp.write(script)
        tmp.close()

        proc = await asyncio.create_subprocess_exec(
            sys.executable, tmp.name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(Path(__file__).parent.parent.parent.parent),
        )

        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return f"[超时] 代码执行超过{timeout}秒"

        output = ""
        if stdout:
            output += stdout.decode("utf-8", errors="replace").strip()
        if stderr:
            err = stderr.decode("utf-8", errors="replace").strip()
            # 过滤pyautogui的警告
            err_lines = [l for l in err.split("\n") if "UserWarning" not in l and "FutureWarning" not in l]
            if err_lines:
                output += "\n[stderr] " + "\n".join(err_lines[:5])

        if not output:
            output = "[执行成功]"

        # PC控制操作执行后提示：可以截屏确认结果
        output += "\n[提示：如需确认操作结果，可以截屏查看]"

        if len(output) > MAX_OUTPUT_LENGTH:
            output = output[:MAX_OUTPUT_LENGTH] + "...[截断]"

        logger.info(f"[PC Control] 执行完成: {output[:80]}")
        return output

    finally:
        try:
            os.unlink(tmp.name)
        except Exception:
            pass


async def _execute_sandbox(code: str, timeout: int = 10) -> str:
    """安全沙箱模式 — 限制危险操作。"""
    # 安全检查
    blocked_imports = ["subprocess", "shutil", "ctypes", "socket", "http.server"]
    blocked_calls = ["os.system", "os.popen", "os.exec", "os.spawn", "os.remove",
                     "eval(", "exec(", "__import__", "compile("]

    for mod in blocked_imports:
        if f"import {mod}" in code or f"from {mod}" in code:
            return f"[安全拦截] 禁止导入: {mod}"
    for call in blocked_calls:
        if call.lower() in code.lower():
            return f"[安全拦截] 禁止使用: {call}"

    # 包装代码
    wrapped = _wrap_code(code)

    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", wrapped,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return f"[超时] 超过{timeout}秒"

        parts = []
        if stdout:
            out = stdout.decode("utf-8", errors="replace").strip()
            if out:
                parts.append(out)
        if stderr:
            err = stderr.decode("utf-8", errors="replace").strip()
            if err:
                parts.append(f"[错误] {err}")
        if not parts:
            parts.append("[执行成功，无输出]")

        result = "\n".join(parts)
        if len(result) > MAX_OUTPUT_LENGTH:
            result = result[:MAX_OUTPUT_LENGTH] + "...[截断]"
        return result

    except Exception as e:
        return f"[执行错误] {e}"


def _wrap_code(code: str) -> str:
    """包装代码，自动打印最后一个表达式。"""
    lines = code.strip().split("\n")
    last = lines[-1].strip()
    is_expr = (
        last and not last.startswith(("import ", "from ", "def ", "class ", "if ", "for ", "while ", "try:", "with "))
        and "=" not in last.split("#")[0] and not last.endswith(":")
    )
    if is_expr:
        lines[-1] = f"__r__ = {last}"
        lines.append("if __r__ is not None: print(__r__)")
    return "\n".join(lines)
