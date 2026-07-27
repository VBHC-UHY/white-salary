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
    monkeypatch.setattr(
        tool_discovery, "detect", lambda field: "D:/auto/detected/elsewhere"
    )

    resolved = ep._resolve("WS_NOT_SET_ANYWHERE", "gpt_sovits_dir", "")

    assert resolved == "D:/my/explicit/choice"


def test_environment_variable_wins_over_everything(monkeypatch) -> None:
    """环境变量优先级最高，这是历史行为，不能因为加了探测就变。"""
    from white_salary.adapters.tools import external_paths as ep

    monkeypatch.setenv("WS_TEST_TOOL_PATH", "E:/from/env")
    monkeypatch.setattr(ep, "_config_value", lambda field, root=None: "D:/from/config")
    monkeypatch.setattr(tool_discovery, "detect", lambda field: "D:/from/detection")

    resolved = ep._resolve("WS_TEST_TOOL_PATH", "gpt_sovits_dir", "")

    assert resolved == "E:/from/env"


def test_detection_failure_never_breaks_resolution(monkeypatch) -> None:
    """探测内部炸了也只能回到"未配置"，绝不能把异常抛给启动流程。"""
    from white_salary.adapters.tools import external_paths as ep

    monkeypatch.setattr(ep, "_config_value", lambda field, root=None: "")

    def _boom(field):
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


def test_autodetect_can_be_disabled_by_env(monkeypatch) -> None:
    """开关必须真的能关掉探测——测试与 CI 的可复现性依赖它。"""
    from white_salary.adapters.tools import external_paths as ep

    monkeypatch.setattr(ep, "_config_value", lambda field, root=None: "")
    monkeypatch.setattr(tool_discovery, "detect", lambda field: "D:/should/not/be/used")
    monkeypatch.setenv("WS_DISABLE_TOOL_AUTODETECT", "1")

    assert ep._resolve("WS_NOT_SET_ANYWHERE", "gpt_sovits_dir", "") == ""
