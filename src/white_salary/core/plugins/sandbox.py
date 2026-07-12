"""
white_salary/core/plugins/sandbox.py

插件安全沙箱 — 静态代码分析，防止危险操作。

借鉴v2的plugins/sandbox.py：
  - 禁止os/subprocess/socket等系统模块
  - 禁止eval/exec/compile等动态执行
  - 禁止文件IO操作
  - 允许json/datetime/random/re等安全模块
"""

import ast
from pathlib import Path
from typing import Iterable

from loguru import logger


# 默认允许的纯计算/标准库模块。第三方依赖仍可使用，但高风险能力必须在
# config.json 的 permissions 中显式声明。
ALLOWED_MODULES = {
    "json", "datetime", "time", "random", "re", "math",
    "hashlib", "base64", "collections", "dataclasses",
    "enum", "typing", "asyncio", "logging", "pathlib",
    "white_salary.core.plugins.base",
}

# 禁止的模块
BLOCKED_MODULES = {
    "subprocess", "importlib", "multiprocessing", "pickle", "ctypes",
    "signal", "pty", "fcntl",
}

PERMISSION_MODULES = {
    "network": {"aiohttp", "httpx", "requests", "urllib", "http", "socket"},
    "filesystem": {"pathlib", "io", "tempfile", "glob", "shutil"},
    "database": {"sqlite3"},
    "threads": {"threading"},
}

ALWAYS_BLOCKED_CALLS = {"eval", "exec", "compile", "__import__"}
FILESYSTEM_CALLS = {
    "open", "write_text", "write_bytes", "unlink", "rmdir", "rename",
    "replace", "mkdir", "rmtree", "move", "copy", "copy2", "copytree",
}
BLOCKED_DUNDER_ATTRIBUTES = {"__class__", "__globals__", "__builtins__", "__subclasses__"}


def _permission_for_module(root: str) -> str | None:
    for permission, modules in PERMISSION_MODULES.items():
        if root in modules:
            return permission
    return None


class _SafetyVisitor(ast.NodeVisitor):
    def __init__(self, permissions: frozenset[str]) -> None:
        self.permissions = permissions
        self.issues: list[str] = []

    def _check_module(self, module: str) -> None:
        root = module.split(".", 1)[0]
        if root in BLOCKED_MODULES:
            self.issues.append(f"禁止导入模块: {module}")
            return
        if module.startswith("white_salary.") and module != "white_salary.core.plugins.base":
            self.issues.append(f"禁止访问应用内部模块: {module}")
            return
        required = _permission_for_module(root)
        if required and required not in self.permissions:
            self.issues.append(f"导入 {module} 需要声明权限: {required}")

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._check_module(alias.name)
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level == 0 and node.module:
            self._check_module(node.module)
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in BLOCKED_DUNDER_ATTRIBUTES:
            self.issues.append(f"禁止访问属性: {node.attr}")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        name = ""
        if isinstance(node.func, ast.Name):
            name = node.func.id
        elif isinstance(node.func, ast.Attribute):
            name = node.func.attr
        if name in ALWAYS_BLOCKED_CALLS:
            self.issues.append(f"禁止的动态执行: {name}()")
        elif name in FILESYSTEM_CALLS and "filesystem" not in self.permissions:
            self.issues.append(f"调用 {name}() 需要声明权限: filesystem")
        elif name in {"system", "popen", "spawn", "fork"}:
            self.issues.append(f"禁止的进程操作: {name}()")
        self.generic_visit(node)


def check_code_safety(
    code: str,
    permissions: Iterable[str] | None = None,
) -> tuple[bool, list[str]]:
    """
    检查插件代码是否安全。

    Args:
        code: 插件Python源代码

    Returns:
        (is_safe, issues) — 是否安全，问题列表
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return False, [f"Python语法错误: {exc.msg}（第{exc.lineno}行）"]

    visitor = _SafetyVisitor(frozenset(str(p).strip().lower() for p in permissions or ()))
    visitor.visit(tree)
    issues = list(dict.fromkeys(visitor.issues))

    is_safe = len(issues) == 0
    if not is_safe:
        logger.warning(f"[Sandbox] 插件代码不安全: {issues}")

    return is_safe, issues


def check_file_safety(
    filepath: str,
    permissions: Iterable[str] | None = None,
) -> tuple[bool, list[str]]:
    """检查插件文件是否安全。"""
    try:
        with open(filepath, encoding="utf-8") as f:
            code = f.read()
        return check_code_safety(code, permissions=permissions)
    except Exception as e:
        return False, [f"无法读取文件: {e}"]


def check_plugin_tree_safety(
    plugin_path: str | Path,
    permissions: Iterable[str] | None = None,
) -> tuple[bool, list[str]]:
    """Scan every Python source file in a plugin package.

    Scanning only ``plugin.py`` lets a harmless-looking relative import hide
    dangerous code in ``helper.py``. This function closes that gap.
    """

    root = Path(plugin_path)
    files = [root] if root.is_file() else sorted(root.rglob("*.py"))
    issues: list[str] = []
    for file in files:
        if "__pycache__" in file.parts:
            continue
        safe, file_issues = check_file_safety(str(file), permissions=permissions)
        if not safe:
            try:
                label = file.relative_to(root).as_posix() if root.is_dir() else file.name
            except ValueError:
                label = file.name
            issues.extend(f"{label}: {issue}" for issue in file_issues)
    return not issues, issues
