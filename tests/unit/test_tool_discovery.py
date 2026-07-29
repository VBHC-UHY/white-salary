"""外部工具自动探测的行为守卫。

这层的产品目标很具体：**新用户装好、点启动，就该能用**，而不是先去翻文档往
conf.yaml 里手写路径。公开版的内置默认路径必须为空（不能夹带作者机器的目录），
所以没有这一层的话，新用户什么都没做错却会看到"路径未配置"然后卡住。

下面每一条断言都对应一个真实踩过的坑，不是补覆盖率：

1. **多份副本要挑能跑的那份。** 首版实测选中了一份没装运行环境的 GPT-SoVITS，
   从那儿启动必然失败，而现象只是"点了没反应"，极难自查。
2. **生成产物不能当作"这份在用"的证据。** 二版实测又选错 ComfyUI：某副本积了
   892 张输出图，仅凭 output 计数就压过了另一份多装 7 个 LoRA 的真实差异。
   用户刻意装进去的模型才代表主力配置。
3. **用户显式配置永远优先。** 探测只在没配时出手，绝不能覆盖用户的选择。
4. **探测失败不许影响主流程。** 找不到就是"未配置"，不能抛异常把启动搞崩。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from white_salary.adapters.tools import tool_discovery


@pytest.fixture(autouse=True)
def _clear_cache():
    tool_discovery.clear_cache()
    yield
    tool_discovery.clear_cache()


@pytest.fixture(autouse=True)
def _enable_autodetect(monkeypatch):
    """本文件必须在探测**开启**的前提下测。

    conftest 为了让单元测试可复现，默认设了 WS_DISABLE_TOOL_AUTODETECT=1。
    若不在这里取消，下面那些"探测应当找到 X"的用例会变成"探测被关了所以返回空"，
    照样绿灯却什么都没验证——正是本项目要极力避免的假绿。
    """
    monkeypatch.delenv("WS_DISABLE_TOOL_AUTODETECT", raising=False)


def _make_gpt_sovits(root: Path, *, runnable: bool, weights: int = 0) -> Path:
    """造一份 GPT-SoVITS 布局。runnable 决定是否具备启动所需的运行环境。"""
    root.mkdir(parents=True, exist_ok=True)
    (root / "api_v2.py").write_text("# fake", encoding="utf-8")
    (root / "GPT_SoVITS").mkdir(exist_ok=True)
    if runnable:
        scripts = root / "venv_new" / "Scripts"
        scripts.mkdir(parents=True, exist_ok=True)
        (scripts / "activate.bat").write_text("@echo off", encoding="utf-8")
    if weights:
        weight_dir = root / "GPT_weights"
        weight_dir.mkdir(exist_ok=True)
        for i in range(weights):
            (weight_dir / f"w{i}.pth").write_text("x", encoding="utf-8")
    return root


def test_finds_tool_by_signature_not_by_folder_name(tmp_path: Path) -> None:
    """目录名随便起也要认得出——判据是内部结构，不是名字。"""
    weird = _make_gpt_sovits(tmp_path / "我的语音合成工具", runnable=True)

    found = tool_discovery.find_tool_dir(
        ("api_v2.py", "GPT_SoVITS"),
        label="GPT-SoVITS",
        search_roots=[tmp_path],
    )

    assert found == weird


def test_prefers_the_copy_that_can_actually_run(tmp_path: Path) -> None:
    """同一工具多份副本时，必须选具备运行环境的那份。

    这是实测踩到的第一个坑：选中没有 venv_new 的副本 → 启动失败，
    而且现象只是"点了没反应"。
    """
    _make_gpt_sovits(tmp_path / "a-broken", runnable=False)
    good = _make_gpt_sovits(tmp_path / "b-usable", runnable=True)

    found = tool_discovery.find_tool_dir(
        ("api_v2.py", "GPT_SoVITS"),
        label="GPT-SoVITS",
        runnable=("venv_new/Scripts/activate.bat",),
        search_roots=[tmp_path],
    )

    assert found == good, "选中了跑不起来的那份副本"


def test_installed_content_breaks_the_tie(tmp_path: Path) -> None:
    """都能跑时，用户装了更多模型的那份才是主力配置。"""
    _make_gpt_sovits(tmp_path / "seldom-used", runnable=True, weights=1)
    main = _make_gpt_sovits(tmp_path / "daily-driver", runnable=True, weights=9)

    found = tool_discovery.find_tool_dir(
        ("api_v2.py", "GPT_SoVITS"),
        label="GPT-SoVITS",
        runnable=("venv_new/Scripts/activate.bat",),
        content_dirs=("GPT_weights",),
        search_roots=[tmp_path],
    )

    assert found == main


def test_runnability_outranks_content(tmp_path: Path) -> None:
    """能跑 > 内容多。装了一堆模型但没运行环境的那份，仍然不能选。"""
    _make_gpt_sovits(tmp_path / "many-models-no-runtime", runnable=False, weights=50)
    runnable = _make_gpt_sovits(tmp_path / "runnable-few-models", runnable=True, weights=1)

    found = tool_discovery.find_tool_dir(
        ("api_v2.py", "GPT_SoVITS"),
        label="GPT-SoVITS",
        runnable=("venv_new/Scripts/activate.bat",),
        content_dirs=("GPT_weights",),
        search_roots=[tmp_path],
    )

    assert found == runnable


def test_returns_none_when_nothing_matches(tmp_path: Path) -> None:
    """找不到就干净地返回 None，不抛异常。"""
    (tmp_path / "unrelated").mkdir()

    found = tool_discovery.find_tool_dir(
        ("api_v2.py", "GPT_SoVITS"),
        label="GPT-SoVITS",
        search_roots=[tmp_path],
    )

    assert found is None


def test_partial_signature_is_not_a_match(tmp_path: Path) -> None:
    """只满足一半特征不算命中，避免把无关目录当成工具。"""
    half = tmp_path / "half"
    half.mkdir()
    (half / "api_v2.py").write_text("# fake", encoding="utf-8")  # 缺 GPT_SoVITS/

    found = tool_discovery.find_tool_dir(
        ("api_v2.py", "GPT_SoVITS"),
        label="GPT-SoVITS",
        search_roots=[tmp_path],
    )

    assert found is None


# ---------------------------------------------------------------------------
# 与 external_paths 的接线：优先级与容错
# ---------------------------------------------------------------------------


def test_explicit_config_wins_over_detection(tmp_path: Path, monkeypatch) -> None:
    """用户在 conf.yaml 里配了什么就用什么，探测不许抢。"""
    from white_salary.adapters.tools import external_paths as ep

    monkeypatch.setattr(ep, "_config_value", lambda field, root=None: "D:/my/explicit/choice")
    # 打桩签名必须与生产调用一致（detect(field, allow_scan=...)）。
    # 早先写成单参 lambda，参数不匹配抛 TypeError 被宽 except 吞掉后同样返回 ""，
    # 于是断言为了错误的原因通过——把开关删掉用例照样绿。
    monkeypatch.setattr(
        tool_discovery,
        "detect",
        lambda field, allow_scan=True: "D:/auto/detected/elsewhere",
    )

    resolved = ep._resolve("WS_NOT_SET_ANYWHERE", "gpt_sovits_dir", "")

    assert resolved == "D:/my/explicit/choice"


def test_environment_variable_wins_over_everything(monkeypatch) -> None:
    """环境变量优先级最高，这是历史行为，不能因为加了探测就变。"""
    from white_salary.adapters.tools import external_paths as ep

    monkeypatch.setenv("WS_TEST_TOOL_PATH", "E:/from/env")
    monkeypatch.setattr(ep, "_config_value", lambda field, root=None: "D:/from/config")
    monkeypatch.setattr(
        tool_discovery, "detect", lambda field, allow_scan=True: "D:/from/detection"
    )

    resolved = ep._resolve("WS_TEST_TOOL_PATH", "gpt_sovits_dir", "")

    assert resolved == "E:/from/env"


def test_detection_failure_never_breaks_resolution(monkeypatch) -> None:
    """探测内部炸了也只能回到"未配置"，绝不能把异常抛给启动流程。"""
    from white_salary.adapters.tools import external_paths as ep

    monkeypatch.setattr(ep, "_config_value", lambda field, root=None: "")

    def _boom(field, allow_scan=True):
        raise RuntimeError("扫描时磁盘出错")

    monkeypatch.setattr(tool_discovery, "detect", _boom)

    assert ep._resolve("WS_NOT_SET_ANYWHERE", "gpt_sovits_dir", "") == ""


def test_unknown_field_detects_to_empty() -> None:
    """未登记的配置字段不该触发任何扫描。"""
    assert tool_discovery.detect("no_such_tool_field") == ""


def test_detection_does_not_hardcode_any_absolute_path() -> None:
    """探测规则里不许出现具体机器路径——否则等于把作者的磁盘布局写进公开源码。

    只检查**真正参与运算的字符串常量**，不检查文档字符串与注释：说明文档里
    需要举例（"用户可能解压成 D:\语音合成"），那是散文不是路径。判据同
    test_no_local_machine_paths 里的散文白名单——能否被程序当作路径使用。
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(tool_discovery))

    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                docstrings.add(doc)

    banned = ("D:/", "D:\\", "C:/Users", "AI_Tools", "cccccccccc")
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value in docstrings:
                continue
            for token in banned:
                if token in node.value:
                    offenders.append((node.lineno, node.value[:60]))
                    break

    assert not offenders, (
        f"tool_discovery 的代码里出现了具体机器路径：{offenders}。"
        "探测必须只靠特征文件与通用目录名。"
    )


@pytest.mark.parametrize("entry", ["detect", "detect_all", "warm_up_async", "rescan"])
def test_master_switch_stops_every_scan_entry_point(monkeypatch, tmp_path, entry) -> None:
    """总开关必须挡住**所有**入口，不能只挡请求路径那一条。

    早先开关只写在 external_paths._autodetect 里，于是 run_server 启动时调的
    warm_up_async() 照样起线程扫全盘——用户/CI 明明关掉了探测，代价照付、
    结果又被请求路径的开关挡住用不上，两头亏。

    断言方式是**行为探针**：直接看真正扫盘的 find_tool_dir 有没有被调用。
    不再打桩 detect —— 那样一旦签名或实现变化，用例会为错误的原因通过。
    """
    tool_discovery.configure_cache_path(tmp_path / "tool_paths.json")
    monkeypatch.setenv("WS_DISABLE_TOOL_AUTODETECT", "1")

    scanned: list[str] = []
    monkeypatch.setattr(
        tool_discovery,
        "find_tool_dir",
        lambda *a, **k: scanned.append(k.get("label", "?")) or None,
    )

    if entry == "detect":
        assert tool_discovery.detect("gpt_sovits_dir") == ""
    elif entry == "detect_all":
        assert set(tool_discovery.detect_all().values()) == {""}
    elif entry == "warm_up_async":
        tool_discovery.warm_up_async()
        import time as _t

        _t.sleep(0.3)  # 给后台线程机会（若它真的启动了）
    else:
        assert tool_discovery.rescan() == {}

    assert not scanned, (
        f"入口 {entry} 在探测被显式关闭时仍然扫盘了（{scanned}）"
    )


def test_switch_removal_would_be_caught(monkeypatch, tmp_path) -> None:
    """反向自检：开关判定函数必须真的读环境变量。

    这条守的是"开关被改成恒返回 False"这类回归——上面那些用例都建立在
    autodetect_disabled() 可信之上。
    """
    monkeypatch.delenv("WS_DISABLE_TOOL_AUTODETECT", raising=False)
    assert tool_discovery.autodetect_disabled() is False

    for truthy in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv("WS_DISABLE_TOOL_AUTODETECT", truthy)
        assert tool_discovery.autodetect_disabled() is True, truthy

    monkeypatch.setenv("WS_DISABLE_TOOL_AUTODETECT", "0")
    assert tool_discovery.autodetect_disabled() is False


# ---------------------------------------------------------------------------
# 磁盘缓存与"不许在请求路径同步扫盘"
#
# 这一组守的是一个实测出来的严重问题：冷缓存下单次探测要 **15 秒**（热缓存只要
# 0.8 秒，所以开发时极易被"看起来很快"骗过去）。而路径解析会被请求处理路径调用，
# 同步扫十几秒会把事件循环整个卡死——用户看到的就是"点了没反应"。
# ---------------------------------------------------------------------------


def test_request_path_never_triggers_a_scan(monkeypatch, tmp_path) -> None:
    """external_paths 解析绝不能同步扫盘。

    这是防"点了没反应"的关键约束：真正的扫描交给启动时的后台预热。
    """
    from white_salary.adapters.tools import external_paths as ep

    tool_discovery.configure_cache_path(tmp_path / "tool_paths.json")
    monkeypatch.setattr(ep, "_config_value", lambda field, root=None: "")

    scanned = []

    def _tripwire(*args, **kwargs):
        scanned.append(kwargs.get("label", "?"))
        return None

    monkeypatch.setattr(tool_discovery, "find_tool_dir", _tripwire)

    ep._resolve("WS_NOT_SET_ANYWHERE", "gpt_sovits_dir", "")

    assert not scanned, (
        f"路径解析触发了同步扫盘（{scanned}）。冷缓存下这会阻塞十几秒，"
        "必须走 allow_scan=False + 启动时后台预热。"
    )


def test_disk_cache_survives_process_restart(tmp_path, monkeypatch) -> None:
    """探测结果要落盘，下次启动直接读，不必再付扫盘代价。"""
    cache_file = tmp_path / "tool_paths.json"
    tool_discovery.configure_cache_path(cache_file)

    real = _make_gpt_sovits(tmp_path / "tools" / "sovits", runnable=True)
    monkeypatch.setitem(
        tool_discovery._DETECTORS, "gpt_sovits_dir", lambda: real
    )

    assert tool_discovery.detect("gpt_sovits_dir") == str(real)
    assert cache_file.exists(), "探测结果没有落盘"

    # 模拟新进程：清空内存缓存，且让扫描函数一旦被调用就失败
    tool_discovery.configure_cache_path(cache_file)
    monkeypatch.setitem(
        tool_discovery._DETECTORS,
        "gpt_sovits_dir",
        lambda: (_ for _ in ()).throw(AssertionError("不该再扫盘")),
    )

    assert tool_discovery.detect("gpt_sovits_dir") == str(real)


def test_stale_cache_entry_is_discarded(tmp_path, monkeypatch) -> None:
    """缓存里的路径已经不存在时必须丢弃并重新探测。

    用户挪走或删掉工具后，不能让陈旧路径把启动带进坑里。
    """
    cache_file = tmp_path / "tool_paths.json"
    tool_discovery.configure_cache_path(cache_file)

    gone = tmp_path / "moved-away"
    cache_file.write_text(
        '{"version": 1, "paths": {"gpt_sovits_dir": "%s"}}' % gone.as_posix(),
        encoding="utf-8",
    )

    fresh = _make_gpt_sovits(tmp_path / "tools" / "new-location", runnable=True)
    monkeypatch.setitem(tool_discovery._DETECTORS, "gpt_sovits_dir", lambda: fresh)

    assert tool_discovery.detect("gpt_sovits_dir") == str(fresh)


def test_corrupt_cache_file_does_not_break_detection(tmp_path, monkeypatch) -> None:
    """缓存文件损坏（半截 JSON / 手改坏了）时当作没有缓存，而不是崩掉。"""
    cache_file = tmp_path / "tool_paths.json"
    cache_file.write_text("{这不是合法 JSON", encoding="utf-8")
    tool_discovery.configure_cache_path(cache_file)

    real = _make_gpt_sovits(tmp_path / "tools" / "sovits", runnable=True)
    monkeypatch.setitem(tool_discovery._DETECTORS, "gpt_sovits_dir", lambda: real)

    assert tool_discovery.detect("gpt_sovits_dir") == str(real)


def test_cache_version_mismatch_is_ignored(tmp_path, monkeypatch) -> None:
    """缓存格式版本不匹配时整体作废，避免旧格式被误读。"""
    cache_file = tmp_path / "tool_paths.json"
    cache_file.write_text(
        '{"version": 999, "paths": {"gpt_sovits_dir": "D:/whatever"}}', encoding="utf-8"
    )
    tool_discovery.configure_cache_path(cache_file)

    real = _make_gpt_sovits(tmp_path / "tools" / "sovits", runnable=True)
    monkeypatch.setitem(tool_discovery._DETECTORS, "gpt_sovits_dir", lambda: real)

    assert tool_discovery.detect("gpt_sovits_dir") == str(real)


def test_allow_scan_false_returns_empty_on_cold_cache(tmp_path, monkeypatch) -> None:
    """缓存未就绪且禁止扫盘时，返回空而不是阻塞——调用方按"未配置"处理。"""
    tool_discovery.configure_cache_path(tmp_path / "tool_paths.json")
    monkeypatch.setitem(
        tool_discovery._DETECTORS,
        "gpt_sovits_dir",
        lambda: (_ for _ in ()).throw(AssertionError("禁扫模式下不该扫盘")),
    )

    assert tool_discovery.detect("gpt_sovits_dir", allow_scan=False) == ""


# ---------------------------------------------------------------------------
# 安全边界
#
# 这一组守的是自动探测引入的一个真实安全回归（已实测复现后修掉）：
#
# 改动前，被 subprocess 启动的路径**只来自用户显式配置**，风险由用户自己掌握。
# 加了磁盘扫描之后，任何符合特征的目录都可能被自动启动。而我最初把
# 下载/桌面/文档目录也纳入了扫描范围——那正是未受信任内容的落地区。
#
# 完整攻击链（已实测）：攻击者提供一个 zip，内含
# run_nvidia_gpu.bat + ComfyUI/ + python_embeded/python.exe；
# 用户只要解压到下载目录，它就会被认成"可运行的 ComfyUI"，
# 并在下次出图时被 subprocess 拉起 —— 用户没做任何其它操作。
# ---------------------------------------------------------------------------


def test_download_and_desktop_dirs_are_not_scanned() -> None:
    """下载/桌面/文档目录绝不能进扫描范围。

    它们是未受信任内容的落地区，而探测结果会被 subprocess 启动。
    真实的工具安装也不会待在下载目录里。
    """
    roots = [str(r).lower() for r in tool_discovery._candidate_roots()]
    for banned in ("downloads", "desktop", "documents", "下载", "桌面", "文档"):
        offenders = [r for r in roots if banned in r]
        assert not offenders, (
            f"候选起点包含 {banned!r}：{offenders}。"
            "用户解压一个 zip 就可能让里面的批处理被自动执行。"
        )


# 只列 Windows 文件系统真能创建的字符。`|` `"` `<` `>` 等是 Windows 的非法
# 文件名字符（实测 mkdir 直接报 WinError 123），攻击者在 Windows 上构造不出来；
# 但 Linux/macOS 允许，所以 _is_shell_safe 仍然要拦它们——只是没法在本用例里造。
@pytest.mark.parametrize(
    "evil_name",
    [
        "ComfyUI_portable & calc",
        "ComfyUI^escape",
        "ComfyUI%PATH%expand",
        "ComfyUI;semicolon",
        "ComfyUI$dollar",
    ],
)
def test_shell_unsafe_paths_are_rejected(tmp_path: Path, evil_name: str) -> None:
    """路径含 shell 语法字符时必须拒绝，而不是想办法把它跑起来。

    这些路径最终会进 subprocess；`&` 在 cmd 下会被当命令分隔符
    （实测 `echo A & echo INJECTED` 会真的执行两条命令）。
    正规安装目录不会带这些字符，所以直接退回"未配置"让用户显式指定更安全。
    """
    evil = tmp_path / evil_name
    (evil / "ComfyUI").mkdir(parents=True)
    (evil / "run_nvidia_gpu.bat").write_text("x", encoding="utf-8")
    (evil / "python_embeded").mkdir()
    (evil / "python_embeded" / "python.exe").write_text("x", encoding="utf-8")

    found = tool_discovery.find_tool_dir(
        ("run_nvidia_gpu.bat", "ComfyUI"),
        label="ComfyUI",
        runnable=("python_embeded/python.exe",),
        search_roots=[tmp_path],
    )

    assert found is None, f"含 shell 特殊字符的路径被接受了: {found}"


def test_safe_path_still_wins_when_an_unsafe_sibling_exists(tmp_path: Path) -> None:
    """有可疑候选时不能整体放弃——正常那份仍要被选中。"""
    for name in ("ComfyUI_evil & calc", "ComfyUI_normal"):
        target = tmp_path / name
        (target / "ComfyUI").mkdir(parents=True)
        (target / "run_nvidia_gpu.bat").write_text("x", encoding="utf-8")
        (target / "python_embeded").mkdir()
        (target / "python_embeded" / "python.exe").write_text("x", encoding="utf-8")

    found = tool_discovery.find_tool_dir(
        ("run_nvidia_gpu.bat", "ComfyUI"),
        label="ComfyUI",
        runnable=("python_embeded/python.exe",),
        search_roots=[tmp_path],
    )

    assert found is not None and found.name == "ComfyUI_normal"


@pytest.mark.parametrize("module_name", ["comfyui_client", "cosyvoice_client"])
def test_external_tools_are_not_launched_through_a_shell(module_name: str) -> None:
    """启动外部工具不得用 shell=True。

    路径可能来自磁盘扫描，必须按不可信输入对待。改用 cmd /c + 参数列表：
    路径作为独立参数由 Windows 转义，不再经过 shell 语法解析。
    （.bat 不是可执行映像，所以 cmd /c 是必要的，不能直接 CreateProcess。）
    """
    import importlib
    import inspect

    module = importlib.import_module(f"white_salary.adapters.tools.{module_name}")
    source = inspect.getsource(module)

    launch_block = source.split("subprocess.Popen", 1)
    assert len(launch_block) > 1, f"{module_name} 里找不到 subprocess.Popen"
    assert "shell=True" not in launch_block[1][:400], (
        f"{module_name} 仍用 shell=True 启动外部工具，路径里的 & | % 会被当命令执行"
    )


@pytest.mark.parametrize(
    "unsafe",
    ["/tools/ComfyUI|whoami", '/tools/ComfyUI"quote', "/tools/ComfyUI`cmd`", "/tools/a" + chr(10) + "b"],
)
def test_shell_safety_check_rejects_posix_only_characters(unsafe: str) -> None:
    """`|` `"` 反引号 换行 在 Windows 上是非法文件名，造不出目录来测；
    但 Linux/macOS 允许，所以直接对判定函数做字符串级校验。"""
    assert tool_discovery._is_shell_safe(Path(unsafe)) is False


def test_shell_safety_check_accepts_normal_paths() -> None:
    """正常路径（含空格、中文、括号、连字符）不得被误拦。"""
    for ok in [
        "D:/AI Tools/GPT-SoVITS",
        "E:/我的工具/ComfyUI_windows_portable",
        "C:/Program Files (x86)/tool",
        "/opt/ai-tools/comfyui",
    ]:
        assert tool_discovery._is_shell_safe(Path(ok)) is True, ok


# ---------------------------------------------------------------------------
# 对抗审查抓出的三条：可用性下限 / 硬件中立 marker / ffmpeg 探测有消费者
# ---------------------------------------------------------------------------


def _make_comfyui(root: Path, *, runnable: bool, bats: tuple[str, ...] = ()) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "ComfyUI").mkdir(exist_ok=True)
    (root / "ComfyUI" / "main.py").write_text("# fake", encoding="utf-8")
    if runnable:
        (root / "python_embeded").mkdir(exist_ok=True)
        (root / "python_embeded" / "python.exe").write_text("x", encoding="utf-8")
    for name in bats:
        (root / name).write_text("@echo off", encoding="utf-8")
    return root


def test_lone_unusable_candidate_is_rejected(tmp_path: Path) -> None:
    """只找到一份、而且这份跑不起来时，必须当作"没找到"。

    没有这条下限的话，那个路径会被返回并落盘，接着 comfyui_client 真的去
    Popen 一个必然失败的 .bat，轮询到超时才放弃——用户每次出图干等一分钟，
    还留下游离进程。用户 clone 了源码仓库或留了备份副本正好落在这条路径上。
    """
    _make_comfyui(tmp_path / "source-only", runnable=False)

    found = tool_discovery.find_tool_dir(
        ("ComfyUI/main.py",),
        label="ComfyUI",
        runnable=("python_embeded/python.exe",),
        search_roots=[tmp_path],
    )

    assert found is None, f"返回了跑不起来的 {found}"


def test_usable_candidate_is_still_accepted(tmp_path: Path) -> None:
    """下限不能把正常安装也挡掉（反向守卫）。"""
    good = _make_comfyui(tmp_path / "portable", runnable=True)

    found = tool_discovery.find_tool_dir(
        ("ComfyUI/main.py",),
        label="ComfyUI",
        runnable=("python_embeded/python.exe",),
        search_roots=[tmp_path],
    )

    assert found == good


def test_comfyui_marker_is_hardware_neutral(tmp_path: Path) -> None:
    """AMD 的 ZLUDA / DirectML 分发版（没有 run_nvidia_gpu.bat）也必须能被找到。

    marker 曾写死 run_nvidia_gpu.bat，后果双向：这些分发版整份被漏掉，
    而且让 detect_comfyui_bat 里的"退回 CPU 版"分支永远不可达
    （marker 保证了 NVIDIA 版必然存在）。
    """
    assert "run_nvidia_gpu.bat" not in tool_discovery._SIGNATURES["comfyui_dir"]["markers"]

    amd = _make_comfyui(tmp_path / "ComfyUI-Zluda", runnable=True, bats=("run_zluda.bat",))

    spec = tool_discovery._SIGNATURES["comfyui_dir"]
    found = tool_discovery.find_tool_dir(
        spec["markers"],
        label="ComfyUI",
        runnable=spec.get("runnable", ()),
        search_roots=[tmp_path],
    )

    assert found == amd


def test_comfyui_bat_choice_avoids_cuda_without_nvidia(tmp_path: Path, monkeypatch) -> None:
    """没有 NVIDIA 时绝不能先试 CUDA 版——那会让每次出图卡满超时。"""
    directory = _make_comfyui(
        tmp_path / "portable",
        runnable=True,
        bats=("run_nvidia_gpu.bat", "run_cpu.bat"),
    )
    monkeypatch.setattr(tool_discovery, "_discover", lambda key, label: directory)
    monkeypatch.setattr(tool_discovery, "_has_nvidia_gpu", lambda: False)

    chosen = tool_discovery.detect_comfyui_bat()

    assert chosen is not None
    assert "nvidia" not in chosen.name.lower(), f"无 NVIDIA 却选了 {chosen.name}"


def test_comfyui_bat_choice_uses_cuda_when_available(tmp_path: Path, monkeypatch) -> None:
    """有 NVIDIA 时应当用 CUDA 版（反向守卫，别修成永远走 CPU）。"""
    directory = _make_comfyui(
        tmp_path / "portable",
        runnable=True,
        bats=("run_nvidia_gpu.bat", "run_cpu.bat"),
    )
    monkeypatch.setattr(tool_discovery, "_discover", lambda key, label: directory)
    monkeypatch.setattr(tool_discovery, "_has_nvidia_gpu", lambda: True)

    chosen = tool_discovery.detect_comfyui_bat()

    assert chosen is not None and chosen.name == "run_nvidia_gpu.bat"


def test_amd_distribution_bat_is_preferred_over_cpu(tmp_path: Path, monkeypatch) -> None:
    """无 NVIDIA 且存在 ZLUDA 版时，应当用 ZLUDA 而不是退到 CPU。

    官方文档明确 run_cpu.bat 只用于排障，不是 AMD 的正常选择。
    """
    directory = _make_comfyui(
        tmp_path / "portable",
        runnable=True,
        bats=("run_zluda.bat", "run_cpu.bat"),
    )
    monkeypatch.setattr(tool_discovery, "_discover", lambda key, label: directory)
    monkeypatch.setattr(tool_discovery, "_has_nvidia_gpu", lambda: False)

    chosen = tool_discovery.detect_comfyui_bat()

    assert chosen is not None and chosen.name == "run_zluda.bat"


def test_ffmpeg_detection_has_a_consumer(tmp_path: Path, monkeypatch) -> None:
    """探测到的 ffmpeg 必须真的被 find_ffmpeg 使用。

    此前两处 find_ffmpeg 都不走自动探测，于是扫了一遍磁盘找 ffmpeg，
    结果没有任何消费者——"装了 ffmpeg 但没加进 PATH"这个恰好是本功能
    要解决的场景，却完全没生效。
    """
    from white_salary.adapters.tools import external_paths as ep

    fake = tmp_path / "ffmpeg" / "bin" / "ffmpeg.exe"
    fake.parent.mkdir(parents=True)
    fake.write_text("x", encoding="utf-8")

    monkeypatch.delenv("WS_FFMPEG_PATH", raising=False)
    monkeypatch.setattr(ep, "_config_value", lambda field, root=None: "")
    # find_ffmpeg 内部是局部 import shutil，所以要打桩全局 shutil.which
    import shutil as _shutil
    monkeypatch.setattr(_shutil, "which", lambda name: None)  # PATH 里没有
    monkeypatch.setattr(
        tool_discovery, "detect", lambda field, allow_scan=True: str(fake)
    )

    assert ep.find_ffmpeg(prefer_path_first=True) == str(fake)


def test_ffmpeg_explicit_config_still_wins(tmp_path: Path, monkeypatch) -> None:
    """接了探测之后，显式配置仍必须优先（反向守卫）。"""
    from white_salary.adapters.tools import external_paths as ep

    explicit = tmp_path / "my-ffmpeg.exe"
    explicit.write_text("x", encoding="utf-8")

    monkeypatch.delenv("WS_FFMPEG_PATH", raising=False)
    monkeypatch.setattr(ep, "_config_value", lambda field, root=None: str(explicit))
    monkeypatch.setattr(
        tool_discovery, "detect", lambda field, allow_scan=True: "D:/detected/elsewhere.exe"
    )

    assert ep.find_ffmpeg(prefer_path_first=True) == str(explicit)


def test_cosyvoice_runnable_marker_matches_real_layout(tmp_path: Path) -> None:
    """CosyVoice 的"可运行"判据必须是启动脚本，而不是目录内的 venv。

    它与另外几个工具不同：**不自带 python 运行环境**，启动脚本里自己指定用哪个
    解释器（实测真实安装是借用别处的 python_embeded）。早先这里按另外几个工具
    的样子推测成 venv/Scripts/activate.bat 与 runtime/python.exe，
    加上可用性下限后真实安装立刻被判为不可运行而整个消失——
    是我自己的真机复验抓到的回归。

    这条守的不只是 CosyVoice，而是那条原则：**指纹要拿真实安装验证，
    不能靠类比其它工具推断。**
    """
    spec = tool_discovery._SIGNATURES["cosyvoice_dir"]
    runnable = spec.get("runnable", ())

    assert runnable, "CosyVoice 缺少可运行判据"
    assert not any("venv" in marker for marker in runnable), (
        f"CosyVoice 的可运行判据又回到了 venv 形态：{runnable}。"
        "它不自带运行环境，判据应当是启动脚本。"
    )

    # 造一份真实形态：有 cosyvoice/ 与 api_server.py 与 start_cosyvoice.bat，但没有 venv
    root = tmp_path / "CosyVoice"
    (root / "cosyvoice").mkdir(parents=True)
    (root / "api_server.py").write_text("# fake", encoding="utf-8")
    (root / "start_cosyvoice.bat").write_text("@echo off", encoding="utf-8")

    found = tool_discovery.find_tool_dir(
        spec["markers"],
        label="CosyVoice",
        runnable=runnable,
        content_dirs=spec.get("content", ()),
        search_roots=[tmp_path],
    )

    assert found == root, "真实形态的 CosyVoice 安装被判为不可运行"


# ---------------------------------------------------------------------------
# 扫描代价与指纹精度（对抗审查的中低危项）
# ---------------------------------------------------------------------------


def test_only_fixed_drives_are_scanned(monkeypatch) -> None:
    """可移动盘/光驱/网络映射盘不得被深扫。

    此前函数名叫 _fixed_drive_roots 却从不检查盘类型，后果是可感知的骚扰：
    光驱被唤醒转起来（听得见）、断开的网络盘要等超时（把扫描拖成几十秒）、
    U 盘内容被当成本机安装（插拔后路径就失效）。
    """
    if os.name != "nt":
        pytest.skip("盘类型判定是 Windows 专属")

    types = {"A:\\": 2, "C:\\": 3, "D:\\": 3, "E:\\": 5, "Z:\\": 4}  # 可移动/固定/固定/光驱/网络

    class _FakeKernel32:
        @staticmethod
        def GetDriveTypeW(root):
            return types.get(str(root), 0)

    import ctypes

    monkeypatch.setattr(ctypes, "windll", type("W", (), {"kernel32": _FakeKernel32})())
    monkeypatch.setattr(Path, "exists", lambda self: str(self) in types)

    roots = [str(p) for p in tool_discovery._fixed_drive_roots()]

    assert roots == ["C:\\", "D:\\"], f"扫描盘列表不对：{roots}"


def test_unknown_drive_type_is_scanned_anyway(monkeypatch) -> None:
    """盘类型判断不出来时保守放行——宁可多扫也不要漏掉用户真正的安装位置。"""
    if os.name != "nt":
        pytest.skip("盘类型判定是 Windows 专属")

    import ctypes

    def _boom(root):
        raise OSError("API 不可用")

    monkeypatch.setattr(
        ctypes, "windll", type("W", (), {"kernel32": type("K", (), {"GetDriveTypeW": staticmethod(_boom)})})()
    )

    assert tool_discovery._is_fixed_drive(Path("D:/")) is True


def test_wav2lip_signature_is_not_a_generic_ml_layout(tmp_path: Path) -> None:
    """Wav2Lip 指纹不能是 ML 项目的通用布局。

    ('hparams.py','checkpoints') 是 Tacotron / so-vits-svc / RVC 一整个家族都有的
    形状，会把无关仓库误认成 Wav2Lip，之后视频口型功能以看不懂的方式失败。
    """
    markers = tool_discovery._SIGNATURES["wav2lip_dir"]["markers"]
    assert "hparams.py" not in markers, f"指纹又退回通用布局：{markers}"

    # 造一个"通用 ML 仓库"：有 hparams.py 与 checkpoints/，但不是 Wav2Lip
    generic = tmp_path / "some-tts-project"
    (generic / "checkpoints").mkdir(parents=True)
    (generic / "hparams.py").write_text("# generic", encoding="utf-8")

    spec = tool_discovery._SIGNATURES["wav2lip_dir"]
    found = tool_discovery.find_tool_dir(
        spec["markers"],
        label="Wav2Lip",
        runnable=spec.get("runnable", ()),
        search_roots=[tmp_path],
    )
    assert found is None, f"把无关 ML 仓库认成了 Wav2Lip：{found}"


def test_real_wav2lip_layout_is_still_recognised(tmp_path: Path) -> None:
    """收紧指纹不能把真实 Wav2Lip 安装弄丢（反向守卫）。"""
    real = tmp_path / "Wav2Lip"
    (real / "face_detection").mkdir(parents=True)
    (real / "checkpoints").mkdir()
    (real / "wav2lip_train.py").write_text("# train", encoding="utf-8")
    (real / "checkpoints" / "wav2lip_gan.pth").write_text("x", encoding="utf-8")

    spec = tool_discovery._SIGNATURES["wav2lip_dir"]
    found = tool_discovery.find_tool_dir(
        spec["markers"],
        label="Wav2Lip",
        runnable=spec.get("runnable", ()),
        content_dirs=spec.get("content", ()),
        search_roots=[tmp_path],
    )
    assert found == real


def test_gpt_sovits_content_covers_current_weight_dirs() -> None:
    """权重目录要覆盖现行发布的命名，否则模型放新目录的用户会被算成 0 分。"""
    content = tool_discovery._SIGNATURES["gpt_sovits_dir"]["content"]
    for suffix in ("", "_v2", "_v2Pro", "_v3", "_v4"):
        assert f"GPT_weights{suffix}" in content, f"缺 GPT_weights{suffix}"
