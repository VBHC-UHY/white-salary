"""
white_salary/core/plugins/sandbox.py

插件代码风险筛查 — 基于静态 AST 分析。

【边界说明，务必先读】
本模块**不是安全沙箱，不能当作安全边界**。Python 运行时高度可自省、可变，
静态分析无法穷尽所有绕过手法（getattr 链、字符串拼接构造属性名、第三方库
内部转发等）。它的定位是"风险筛查"：把绝大多数意外的危险写法和低成本的
恶意代码挡在加载之前，降低误装插件的伤害面。

真正的隔离需要进程/容器级方案（独立子进程 + 能力白名单，或容器 runtime）。
在那之前，安装第三方插件本质上等同于运行任意代码，必须由用户显式信任来源。

设计原则：
  - 导入采用【白名单】：只有 ALLOWED_MODULES、或已声明对应权限的
    PERMISSION_MODULES、或声明了 extra_imports 的第三方模块才能导入。
    历史版本这里是黑名单（未明确禁止即放行），导致 os/sys 等模块畅通无阻。
  - BLOCKED_MODULES 为绝对禁止层，声明任何权限都无法解锁。
  - 高风险能力（网络/文件/数据库/线程）必须在 config.json 的
    permissions 中显式声明，安装时对用户可见。
"""

import ast
from pathlib import Path
from typing import Iterable

from loguru import logger


# 无需声明权限即可导入的纯计算/标准库模块。
# 注意：这个集合现在是**真正生效的白名单**，不再是装饰性的。
ALLOWED_MODULES = {
    "json", "datetime", "time", "random", "re", "math",
    "hashlib", "base64", "collections", "dataclasses",
    "enum", "typing", "asyncio", "logging", "string",
    "itertools", "functools", "decimal", "fractions",
    "statistics", "uuid", "textwrap", "unicodedata",
    "white_salary.core.plugins.base",
}

# 绝对禁止：声明任何权限都不解锁。
# os/sys 曾在历史版本中被移出本集合，且未落入任何权限桶，等价于完全放行——
# 那是一个 RCE 级别的缺口（os.environ 读密钥、os.remove 删文件、
# run = os.popen 别名绕过按名字的调用黑名单）。
BLOCKED_MODULES = {
    "os", "sys", "subprocess", "importlib", "multiprocessing",
    "pickle", "marshal", "shelve", "ctypes", "cffi",
    "signal", "pty", "fcntl", "termios", "resource",
    "builtins", "gc", "inspect", "types", "code", "codeop",
    "runpy", "webbrowser", "platform", "sysconfig",
    "atexit", "traceback", "linecache",
}

# 需要在 config.json 的 permissions 中显式声明才能导入。
PERMISSION_MODULES = {
    "network": {"aiohttp", "httpx", "requests", "urllib", "http", "socket", "ftplib", "smtplib"},
    "filesystem": {"pathlib", "io", "tempfile", "glob", "shutil", "csv", "zipfile", "tarfile"},
    "database": {"sqlite3"},
    "threads": {"threading", "concurrent"},
}

# 声明后可导入白名单之外的第三方模块（如 bs4、pandas）。
# 单独设一个权限而不是默认放行，是为了让"这个插件要引入外部依赖"
# 这件事在安装时对用户可见。
EXTRA_IMPORTS_PERMISSION = "extra_imports"

# 动态执行与自省原语。getattr/vars/globals 是静态分析最主要的绕过入口
# （getattr(mod, "sys"+"tem") 这类构造无法在 AST 层判定），一律拦掉。
ALWAYS_BLOCKED_CALLS = {
    "eval", "exec", "compile", "__import__",
    "getattr", "setattr", "delattr",
    "globals", "locals", "vars",
    "breakpoint",
}

FILESYSTEM_CALLS = {
    "open", "write_text", "write_bytes", "unlink", "rmdir", "rename",
    "replace", "mkdir", "rmtree", "move", "copy", "copy2", "copytree",
}

# 进程/命令执行相关名字：**取值即拦截**，不必等到被调用。
# 历史版本只在 visit_Call 里按名字拦，所以 `run = os.popen` 先取值再调用
# 就能整个绕过去。私有模块别名（random._os 这类转发）同理。
BLOCKED_ATTRIBUTE_NAMES = {
    "system", "popen", "fork", "forkpty",
    "execv", "execve", "execl", "execle", "execlp", "execvp",
    "spawnv", "spawnve", "spawnl", "spawnle",
    "_os", "_sys", "_socket", "_subprocess", "_thread", "_io",
}

BLOCKED_DUNDER_ATTRIBUTES = {
    "__class__", "__globals__", "__builtins__", "__subclasses__",
    "__code__", "__closure__", "__func__", "__self__",
    "__bases__", "__mro__", "__getattribute__",
    "__reduce__", "__reduce_ex__", "__loader__", "__spec__",
    "__import__", "__dict__",
}


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

        # 应用内部模块：只放行插件基类
        if module.startswith("white_salary.") and module != "white_salary.core.plugins.base":
            self.issues.append(f"禁止访问应用内部模块: {module}")
            return

        # 绝对禁止层，优先于一切权限
        if root in BLOCKED_MODULES:
            self.issues.append(f"禁止导入模块: {module}")
            return

        # 权限层：命中则必须已声明
        required = _permission_for_module(root)
        if required:
            if required not in self.permissions:
                self.issues.append(f"导入 {module} 需要声明权限: {required}")
            return

        # 白名单层
        if root in ALLOWED_MODULES or module in ALLOWED_MODULES:
            return

        # 其余一律视为未知第三方依赖，需要显式声明
        if EXTRA_IMPORTS_PERMISSION not in self.permissions:
            self.issues.append(
                f"导入 {module} 不在允许清单中，需要声明权限: {EXTRA_IMPORTS_PERMISSION}"
            )

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
        elif node.attr in BLOCKED_ATTRIBUTE_NAMES:
            self.issues.append(f"禁止的进程/系统操作: {node.attr}")
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
        elif name in BLOCKED_ATTRIBUTE_NAMES:
            self.issues.append(f"禁止的进程/系统操作: {name}()")
        self.generic_visit(node)


def check_code_safety(
    code: str,
    permissions: Iterable[str] | None = None,
) -> tuple[bool, list[str]]:
    """
    对插件源码做静态风险筛查。

    注意：通过检查**不代表代码安全**，只代表没有命中已知的危险写法。
    见模块头部的边界说明。

    Args:
        code: 插件Python源代码
        permissions: 插件在 config.json 中声明的权限

    Returns:
        (is_safe, issues) — 是否通过筛查，问题列表
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
        logger.warning(f"[Sandbox] 插件代码未通过风险筛查: {issues}")

    return is_safe, issues


def check_file_safety(
    filepath: str,
    permissions: Iterable[str] | None = None,
) -> tuple[bool, list[str]]:
    """检查插件文件是否通过风险筛查。"""
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
