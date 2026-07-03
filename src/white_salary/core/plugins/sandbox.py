"""
white_salary/core/plugins/sandbox.py

插件安全沙箱 — 静态代码分析，防止危险操作。

借鉴v2的plugins/sandbox.py：
  - 禁止os/subprocess/socket等系统模块
  - 禁止eval/exec/compile等动态执行
  - 禁止文件IO操作
  - 允许json/datetime/random/re等安全模块
"""

import re
from typing import Optional

from loguru import logger


# 允许的模块
ALLOWED_MODULES = {
    "json", "datetime", "time", "random", "re", "math",
    "hashlib", "base64", "collections", "dataclasses",
    "enum", "typing", "asyncio", "logging", "pathlib",
    "white_salary.core.plugins.base",
}

# 禁止的模块
BLOCKED_MODULES = {
    "os", "sys", "subprocess", "shutil", "socket",
    "http", "urllib", "requests", "importlib",
    "io", "tempfile", "glob", "multiprocessing",
    "threading", "pickle", "ctypes", "sqlite3",
    "signal", "pty", "fcntl",
}

# 禁止的代码模式
BLOCKED_PATTERNS = [
    (r'\beval\s*\(', "eval()调用"),
    (r'\bexec\s*\(', "exec()调用"),
    (r'\bcompile\s*\(', "compile()调用"),
    (r'__import__\s*\(', "__import__()调用"),
    (r'\bopen\s*\(', "open()文件操作"),
    (r'os\.system', "os.system()系统命令"),
    (r'subprocess', "subprocess模块"),
    (r'__class__', "__class__访问"),
    (r'__globals__', "__globals__访问"),
    (r'__builtins__', "__builtins__访问"),
]


def check_code_safety(code: str) -> tuple[bool, list[str]]:
    """
    检查插件代码是否安全。

    Args:
        code: 插件Python源代码

    Returns:
        (is_safe, issues) — 是否安全，问题列表
    """
    issues = []

    # 检查导入
    import_pattern = re.compile(r'(?:from|import)\s+([\w.]+)')
    for match in import_pattern.finditer(code):
        module = match.group(1).split('.')[0]
        if module in BLOCKED_MODULES:
            issues.append(f"禁止导入模块: {module}")

    # 检查危险模式
    for pattern, desc in BLOCKED_PATTERNS:
        if re.search(pattern, code):
            issues.append(f"禁止的操作: {desc}")

    is_safe = len(issues) == 0
    if not is_safe:
        logger.warning(f"[Sandbox] 插件代码不安全: {issues}")

    return is_safe, issues


def check_file_safety(filepath: str) -> tuple[bool, list[str]]:
    """检查插件文件是否安全。"""
    try:
        with open(filepath, encoding="utf-8") as f:
            code = f.read()
        return check_code_safety(code)
    except Exception as e:
        return False, [f"无法读取文件: {e}"]
