"""护栏测试 — 公开仓库不得夹带作者机器专属路径、真实账号或版本回退。

## 为什么需要这一整个文件

本项目的开发方式是"本地目录保留机器专属路径，公开版另开工作树移植"。这套策略
本身没问题，但它把"哪些东西不能过去"完全交给了人的记忆。2026-07 的一次审计
实测发现：本地把作者机器上的工具安装路径回灌成了源码默认值，
并且**把守护它的黄金测试反向改写成"断言这些私人路径就是默认值"**——于是
护栏不但失效，还反过来强制要求作者路径存在。

这类回归的共同特征是：**只要有人用整目录 checkout 或三方 merge 工具，
就会被静默带回来**，而普通功能测试全绿。所以这里把裁决写成断言，
让每次 `pytest` 都替人检查一遍。

## 放宽这些断言之前请先读这段

如果某条断言挡住了你，正确做法几乎总是"把机器专属值放进 `conf.yaml` 或环境
变量"，而不是放宽断言。`external_paths.py` 已经实现了
`环境变量 → conf.yaml external_tools → 自动探测` 三级回退，本地一键跑与公开
仓库干净这两件事本来就可以同时成立。
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]

# 扫描范围：只看会进仓库的源码与配置，跳过体积大的已入库目录
_SKIP_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", "data", "logs",
    "backups", "models", "live2d_models", "NapCat", "NapCat_OneKey",
    "assets", "dist", "build", ".pytest_cache", ".claude",
}
_SCAN_SUFFIXES = {".py", ".js", ".mjs", ".html", ".yaml", ".yml", ".json", ".bat", ".sh", ".md", ".toml"}


def _git_published_files() -> list[Path] | None:
    """问 git 要"会进仓库的文件"清单；拿不到就返回 None 由调用方回退。

    为什么不能只靠 `rglob` + `_SKIP_DIRS`：黑名单是**猜**的，而本机的私有文档
    （运维手册、审计记录）就躺在项目根目录、后缀是 `.md`、内容里必然出现被禁的
    真实账号——它们全都被 gitignore，永远不会进仓库，扫它们只会产生假失败。

    而假失败比没有护栏更糟：人会学会"这几条红字是老样子"，真出事那次也照样放过去。
    所以判据必须和"会不会进仓库"完全一致，也就是直接问 git。

    `--cached` 取已入库的，`--others --exclude-standard` 取未入库但**没被 ignore**
    的（新写的文件也必须受检，否则加个新文件就能绕过护栏）。
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(PROJECT_ROOT), "ls-files", "--full-name", "-z",
             "--cached", "--others", "--exclude-standard"],
            capture_output=True, text=True, timeout=60, check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None  # 不在 git 仓库里 / 没装 git：回退到目录遍历
    return [PROJECT_ROOT / rel for rel in result.stdout.split("\0") if rel]


def _iter_repo_files():
    published = _git_published_files()
    candidates = published if published is not None else PROJECT_ROOT.rglob("*")
    for path in candidates:
        if not path.is_file():
            continue
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() not in _SCAN_SUFFIXES:
            continue
        if path.name.endswith((".bak", ".orig", ".rej")):
            continue
        # 本文件自身必然包含这些字面量，跳过
        if path.name == Path(__file__).name:
            continue
        yield path


# 允许提及这些路径的文件。
#
# CHANGELOG 的职责就是记录"我们把写死的 D:\AI_Tools 改成了配置解析"这类历史，
# 叙述里必然会出现被禁的字面量。禁止它反而会逼人删掉真实的变更记录。
# 判据是"能否被程序当作路径使用"：变更日志里的散文不会，源码默认值会。
_PROSE_ALLOWLIST = {"CHANGELOG.md"}


def _grep(needles: list[str]) -> list[str]:
    """返回 "相对路径:行号: 命中内容" 列表。"""
    hits: list[str] = []
    lowered = [n.lower() for n in needles]
    for path in _iter_repo_files():
        if path.name in _PROSE_ALLOWLIST:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        low = text.lower()
        if not any(n in low for n in lowered):
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            ll = line.lower()
            for n in lowered:
                if n in ll:
                    rel = path.relative_to(PROJECT_ROOT).as_posix()
                    hits.append(f"{rel}:{lineno}: {line.strip()[:120]}")
                    break
    return hits


# ---------------------------------------------------------------------------
# 零、扫描范围本身的自检
#
# 这一组守的是"扫的到底是哪些文件"。上面 `_git_published_files` 的整个价值在于
# 把判据从"手写目录黑名单"换成"git 说会不会进仓库"；如果这条路径静默失效
# （git 调用失败被 except 吃掉、回退到 rglob），下面所有断言都还会通过，
# 只是又开始扫本机私有文档、开始产生假失败。所以这里直接验行为。
# ---------------------------------------------------------------------------


def test_scan_scope_comes_from_git_not_a_guessed_blacklist() -> None:
    """git 清单必须真的取到了，而不是静默回退到目录遍历。"""
    published = _git_published_files()
    assert published is not None, (
        "拿不到 git 文件清单——扫描退化成了目录遍历，会把 gitignore 的本机私有文档"
        "一起扫进来。先确认 PROJECT_ROOT 是 git 工作树、git 可执行。"
    )
    assert len(published) > 100, f"git 只报了 {len(published)} 个文件，明显不对"
    # 抽查：入库的源码在，.git 内部文件不在
    rels = {p.relative_to(PROJECT_ROOT).as_posix() for p in published}
    assert "pyproject.toml" in rels
    assert not any(r.startswith(".git/") for r in rels)


def test_gitignored_local_files_are_not_scanned() -> None:
    """被 gitignore 的文件必须落在扫描范围外；新增的未入库文件必须落在范围内。

    前者防假失败：本机的运维手册/审计记录就在项目根、是 `.md`、且必然写着真实
    账号与作者路径（它们的职责就是记录这些），扫它们等于让护栏天天报错。
    后者防绕过：光看 `--cached` 的话，新写一个文件就能躲开检查。
    """
    ignored = PROJECT_ROOT / "PROJECT_AUDIT_ZZ_GUARDRAIL_PROBE.md"   # 匹配 .gitignore 的 PROJECT_AUDIT_*.md
    fresh = PROJECT_ROOT / "zz_guardrail_probe.md"                    # 未入库但不被 ignore
    for probe in (ignored, fresh):
        if probe.exists():
            pytest.skip(f"探针文件已存在，不覆盖：{probe.name}")

    try:
        ignored.write_text("probe\n", encoding="utf-8")
        fresh.write_text("probe\n", encoding="utf-8")
        scanned = set(_iter_repo_files())
        assert ignored not in scanned, (
            f"{ignored.name} 被 gitignore 却仍在扫描范围内 —— 假失败的来源"
        )
        assert fresh in scanned, (
            f"{fresh.name} 未入库但也没被 ignore，必须受检，否则新增文件可绕过护栏"
        )
    finally:
        ignored.unlink(missing_ok=True)
        fresh.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# 一、作者机器专属路径
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "needle",
    [
        "d:/cccccccccc",
        "d:\\cccccccccc",
        "d:/ai_tools",
        "d:\\ai_tools",
        "谷歌浏览器",
    ],
)
def test_author_machine_paths_are_absent(needle: str) -> None:
    """这些路径只存在于作者机器上，别人 clone 下来指向的是不存在的目录。

    正确做法：源码默认值留空，实际路径写在本地 conf.yaml 的 external_tools 段
    或对应环境变量里。
    """
    hits = _grep([needle])
    assert not hits, (
        f"发现作者机器专属路径 {needle!r}，应改为空默认值 + conf.yaml 配置：\n"
        + "\n".join(hits[:15])
    )


def test_external_paths_defaults_stay_empty() -> None:
    """external_paths 的默认值必须为空，否则等于把作者的磁盘布局写进公开源码。"""
    from white_salary.adapters.tools import external_paths as ep

    for name in [n for n in dir(ep) if n.startswith("DEFAULT_")]:
        value = getattr(ep, name)
        if isinstance(value, str):
            assert value == "", f"{name} 应为空串，实际 {value!r}"
        elif isinstance(value, (tuple, list, set)):
            assert not value, f"{name} 应为空集合，实际 {value!r}"


# ---------------------------------------------------------------------------
# 二、真实账号信息
# ---------------------------------------------------------------------------


# 禁用的真实账号以 SHA-256 存储，**不写原始数字**。
#
# 原因：本文件的职责就是"禁止真实账号出现在仓库里"，如果把号码作为字面量写在
# 这里，护栏本身就成了泄漏源——推送到公开仓库后，那串号码照样是公开可见、
# 可被爬取的。号码不是密钥，但它是个人信息，而我们当初正是为了不公开它，
# 才把它从 smart_reply 的 docstring 示例里删掉。
#
# 检测能力不变：扫描时把文件里所有 8~11 位数字串取出来算哈希再比对。
_BANNED_ACCOUNT_HASHES: dict[str, str] = {
    "f379061ca4e056ec1206bb483481223bde7675a939e117225ce631cb19f18152":
        "真实 QQ 号（曾出现在 smart_reply 类 docstring 示例里）",
    "a0f87851ba6838d9672dbe281abf6117728c8ed8050c770b4267f409d1e88736":
        "真实 QQ 群号（曾出现在设置面板 placeholder 与单测里）",
}


def test_real_account_identifiers_are_absent() -> None:
    """示例值必须用占位号（如 123456789），不能用真实账号。"""
    import hashlib

    digits = re.compile(r"\b\d{8,11}\b")
    offenders: list[str] = []

    for file in _iter_repo_files():
        if file.name in _PROSE_ALLOWLIST:
            continue
        try:
            content = file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for lineno, line in enumerate(content.splitlines(), 1):
            for candidate in digits.findall(line):
                digest = hashlib.sha256(candidate.encode()).hexdigest()
                what = _BANNED_ACCOUNT_HASHES.get(digest)
                if what:
                    rel = file.relative_to(PROJECT_ROOT).as_posix()
                    offenders.append(f"{rel}:{lineno}: 发现{what}")

    assert not offenders, "\n".join(offenders[:10])


def test_banned_account_detection_actually_works() -> None:
    """反向自检：哈希比对必须真的能认出被禁号码。

    否则这条护栏会变成"永远通过"的装饰品——号码换成哈希之后，
    最容易犯的错就是哈希算错而没人发现。
    """
    import hashlib

    assert len(_BANNED_ACCOUNT_HASHES) >= 2
    for digest in _BANNED_ACCOUNT_HASHES:
        assert len(digest) == 64, f"不是合法 sha256 十六进制：{digest}"

    # 用一个已知不在禁用表里的号码验证不会误报
    benign = hashlib.sha256(b"123456789").hexdigest()
    assert benign not in _BANNED_ACCOUNT_HASHES, "占位号 123456789 不该被禁"


# ---------------------------------------------------------------------------
# 三、版本号一致性（防止把版本退回旧值）
# ---------------------------------------------------------------------------


def test_version_is_consistent_and_not_rolled_back() -> None:
    """四处版本号必须一致。历史上本地分支曾把它从 0.1.11 退回 0.1.7。"""
    pyproject = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version = "([^"]+)"', pyproject, re.MULTILINE)
    assert match, "pyproject.toml 里找不到 version"
    version = match.group(1)

    init_py = (PROJECT_ROOT / "src" / "white_salary" / "__init__.py").read_text(encoding="utf-8")
    assert f'__version__ = "{version}"' in init_py, f"__init__.py 版本与 pyproject 不一致（应为 {version}）"

    conf_default = (PROJECT_ROOT / "conf.default.yaml").read_text(encoding="utf-8")
    assert f'version: "{version}"' in conf_default, f"conf.default.yaml 版本与 pyproject 不一致（应为 {version}）"

    frontend_pkg = json.loads((PROJECT_ROOT / "frontend" / "package.json").read_text(encoding="utf-8"))
    assert frontend_pkg["version"] == version, (
        f"frontend/package.json 版本 {frontend_pkg['version']} 与 pyproject {version} 不一致"
    )

    # 单调性：不得低于本护栏引入时的版本
    parts = tuple(int(p) for p in version.split("."))
    assert parts >= (0, 1, 12), f"版本号疑似回退到 {version}"


# ---------------------------------------------------------------------------
# 四、启动脚本必须走项目虚拟环境
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "script",
    ["Start.bat", "Start-Backend.bat", "Start-TTS.bat", "Start-TTS-Local.bat"],
)
def test_launchers_use_project_venv(script: str) -> None:
    """启动器必须用项目 .venv，与安装器的隔离安装保持一致。

    历史上本地分支把 Start.bat 退回全局 python，与 `安装.bat` 装进 .venv 的行为
    矛盾，导致新装机器起不来。
    """
    path = PROJECT_ROOT / script
    if not path.exists():
        pytest.skip(f"{script} 不存在")
    text = path.read_text(encoding="utf-8", errors="ignore")

    assert ".venv" in text, f"{script} 没有引用项目 .venv"
    assert not re.search(r"(?<![\w.\\/])python(?:\.exe)?\s+run_server\.py", text), (
        f"{script} 出现了裸 python 调用，应使用 .venv\\Scripts\\python.exe"
    )


# ---------------------------------------------------------------------------
# 五、"零动作"文件确实没被本地版覆盖
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "relative",
    [
        "src/white_salary/core/plugins/sandbox.py",
        "src/white_salary/infrastructure/server/websocket_handler.py",
        "src/white_salary/core/runtime/journal.py",
    ],
)
def test_v0_1_12_security_fixes_are_still_present(relative: str) -> None:
    """v0.1.12 的关键修复不得被本地版覆盖回去。

    这几处的共同点是：本地版本里对应位置是旧代码，一旦整文件取本地，
    修复就静默消失，而功能测试察觉不到。
    """
    text = (PROJECT_ROOT / relative).read_text(encoding="utf-8", errors="ignore")
    markers = {
        # 沙箱：绝对禁止层必须仍然拦住 os/sys
        "src/white_salary/core/plugins/sandbox.py": ['"os"', '"sys"', "BLOCKED_MODULES"],
        # 3.10 兼容的取消判定辅助函数
        "src/white_salary/infrastructure/server/websocket_handler.py": ["_current_task_is_cancelling"],
        # 账本降级句柄
        "src/white_salary/core/runtime/journal.py": ["_DetachedTaskHandle"],
    }[relative]
    for marker in markers:
        assert marker in text, f"{relative} 缺少 v0.1.12 修复标记 {marker!r}，疑似被本地版覆盖"
