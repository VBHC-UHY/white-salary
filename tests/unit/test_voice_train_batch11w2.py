"""
测试声音一键训练端点（2026-07-03 功能大项 批11二波）。

覆盖 settings_api.py 本波新增的三个端点：
- POST /api/settings/voice-clone/train：起训练进程（mock subprocess.Popen 验证起进程
  与防重复；GPT-SoVITS 未安装时返回中文指引且不 Popen；audio_path 复制到脚本输入位）
- GET  /api/settings/voice-clone/train-status：造假日志文件测 tail 与 running 判断
- POST /api/settings/voice-clone/train-stop：安全终止（有/无运行进程两分支）

沿用 test_settings_api.py / test_settings_panel_batch6.py 的做法：直接从 APIRouter
取端点函数调用（不依赖 TestClient），训练状态与 Popen 用 monkeypatch 隔离。
"""

from pathlib import Path
from typing import Any, Callable

import pytest
import yaml

import white_salary.infrastructure.server.settings_api as settings_api_module
from white_salary.infrastructure.server.settings_api import create_settings_router


def _endpoint(router: Any, path: str, method: str) -> Callable:
    """从 APIRouter 中按路径+方法取出端点函数（避免依赖 TestClient/httpx）。"""
    for route in router.routes:
        if route.path == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"路由未找到: {method} {path}")


def _make_project(tmp_path: Path) -> Path:
    """构造最小化临时项目根（含 conf.default.yaml / conf.yaml 与 scripts/train_voice.py）。"""
    (tmp_path / "conf.default.yaml").write_text(
        yaml.dump({"system": {"name": "White Salary"}}, allow_unicode=True),
        encoding="utf-8",
    )
    (tmp_path / "conf.yaml").write_text(
        yaml.dump({"llm": {"provider": "siliconflow"}}, allow_unicode=True),
        encoding="utf-8",
    )
    # 训练脚本占位（端点只检查其存在性，不实际执行）
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    (scripts_dir / "train_voice.py").write_text("# placeholder\n", encoding="utf-8")
    return tmp_path


def _make_sovits_dir(tmp_path: Path) -> Path:
    """构造一个"已安装"的 GPT-SoVITS 目录（含 venv_new/Scripts/activate.bat）。"""
    sovits = tmp_path / "GPT-SoVITS"
    (sovits / "venv_new" / "Scripts").mkdir(parents=True, exist_ok=True)
    (sovits / "venv_new" / "Scripts" / "activate.bat").write_text("@echo on\n", encoding="utf-8")
    return sovits


class _FakePopen:
    """假的 subprocess.Popen：记录构造参数，poll() 可控运行/结束。"""

    last_instance: "Any" = None

    def __init__(self, cmd: Any, **kwargs: Any) -> None:
        self.cmd = cmd
        self.kwargs = kwargs
        self.pid = 4321
        self._returncode: int | None = None  # None=运行中
        self.terminated = False
        _FakePopen.last_instance = self

    def poll(self) -> int | None:
        return self._returncode

    def terminate(self) -> None:
        self.terminated = True
        self._returncode = -15


@pytest.fixture(autouse=True)
def _reset_train_state() -> Any:
    """每个用例前后清空模块级训练状态，避免用例间串扰。"""
    settings_api_module._voice_train_state.update(
        {"process": None, "log_file": None, "started_at": None}
    )
    yield
    settings_api_module._voice_train_state.update(
        {"process": None, "log_file": None, "started_at": None}
    )


def _patch_sovits(monkeypatch: pytest.MonkeyPatch, sovits_dir: Path) -> None:
    """让端点内的 get_gpt_sovits_dir 返回指定目录。"""
    import white_salary.adapters.tools.external_paths as ep

    monkeypatch.setattr(ep, "get_gpt_sovits_dir", lambda **_kwargs: sovits_dir)


class TestVoiceTrainStart:
    """POST /voice-clone/train。"""

    async def test_missing_gpt_sovits_returns_guidance_no_popen(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GPT-SoVITS 目录不存在：返回中文安装指引，且绝不 Popen。"""
        root = _make_project(tmp_path)
        _patch_sovits(monkeypatch, tmp_path / "nonexistent_sovits")

        called = {"popen": False}

        def _boom(*a: Any, **k: Any) -> None:
            called["popen"] = True
            raise AssertionError("不应该在未安装时启动进程")

        import subprocess

        monkeypatch.setattr(subprocess, "Popen", _boom)

        router = create_settings_router(root)
        train = _endpoint(router, "/api/settings/voice-clone/train", "POST")
        resp = await train({})
        assert resp["ok"] is False
        assert resp["started"] is False
        assert called["popen"] is False
        assert "GPT-SoVITS" in resp["message"]
        assert "LOCAL_ADVANCED" in resp["message"]

    async def test_starts_process_and_records_state(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """安装齐全：Popen 被调用一次，状态与日志文件登记，命令含 cd/activate/python。"""
        root = _make_project(tmp_path)
        sovits = _make_sovits_dir(tmp_path)
        _patch_sovits(monkeypatch, sovits)

        import subprocess

        monkeypatch.setattr(subprocess, "Popen", _FakePopen)

        router = create_settings_router(root)
        train = _endpoint(router, "/api/settings/voice-clone/train", "POST")
        resp = await train({})
        assert resp["ok"] is True
        assert resp["started"] is True
        # 日志文件登记且落盘
        log_file = resp["log_file"]
        assert log_file and Path(log_file).exists()
        assert Path(log_file).parent == (root / "logs")
        # 命令复刻 bat：cd 安装目录 → 激活 venv → python 脚本
        cmd = _FakePopen.last_instance.cmd
        assert "cd /d" in cmd
        assert "activate.bat" in cmd
        assert "train_voice.py" in cmd
        assert "--resume" not in cmd  # 未传 resume 时不带 --resume
        # 状态登记
        assert settings_api_module._voice_train_state["process"] is not None
        assert settings_api_module._voice_train_state["log_file"] == log_file

    async def test_resume_flag_passed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """勾选断点续跑：命令带 --resume。"""
        root = _make_project(tmp_path)
        sovits = _make_sovits_dir(tmp_path)
        _patch_sovits(monkeypatch, sovits)
        import subprocess

        monkeypatch.setattr(subprocess, "Popen", _FakePopen)
        router = create_settings_router(root)
        train = _endpoint(router, "/api/settings/voice-clone/train", "POST")
        resp = await train({"resume": True})
        assert resp["started"] is True
        assert "--resume" in _FakePopen.last_instance.cmd

    async def test_reject_when_already_running(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """已有训练在跑：拒绝并返回"正在训练中"，不起第二个进程。"""
        root = _make_project(tmp_path)
        sovits = _make_sovits_dir(tmp_path)
        _patch_sovits(monkeypatch, sovits)

        import subprocess

        # 预置一个"运行中"的进程
        running = _FakePopen("existing")
        running._returncode = None
        settings_api_module._voice_train_state["process"] = running
        settings_api_module._voice_train_state["log_file"] = str(root / "logs" / "old.log")

        called = {"popen": 0}

        def _count_popen(*a: Any, **k: Any) -> Any:
            called["popen"] += 1
            return _FakePopen(*a, **k)

        monkeypatch.setattr(subprocess, "Popen", _count_popen)

        router = create_settings_router(root)
        train = _endpoint(router, "/api/settings/voice-clone/train", "POST")
        resp = await train({})
        assert resp["ok"] is False
        assert resp["running"] is True
        assert called["popen"] == 0
        assert "训练" in resp["message"]

    async def test_audio_path_copied_to_input(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """指定 audio_path：复制到 <安装目录>/input_audio/jiaran.mp3 供脚本拾取。"""
        root = _make_project(tmp_path)
        sovits = _make_sovits_dir(tmp_path)
        _patch_sovits(monkeypatch, sovits)
        import subprocess

        monkeypatch.setattr(subprocess, "Popen", _FakePopen)

        # 造一个训练音频
        audio = tmp_path / "my_voice.mp3"
        audio.write_bytes(b"FAKEMP3DATA")

        router = create_settings_router(root)
        train = _endpoint(router, "/api/settings/voice-clone/train", "POST")
        resp = await train({"audio_path": str(audio)})
        assert resp["started"] is True
        dst = sovits / "input_audio" / "jiaran.mp3"
        assert dst.exists()
        assert dst.read_bytes() == b"FAKEMP3DATA"

    async def test_missing_audio_path_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """指定了不存在的 audio_path：报错且不启动。"""
        root = _make_project(tmp_path)
        sovits = _make_sovits_dir(tmp_path)
        _patch_sovits(monkeypatch, sovits)
        import subprocess

        called = {"popen": 0}
        monkeypatch.setattr(
            subprocess, "Popen",
            lambda *a, **k: (called.__setitem__("popen", called["popen"] + 1), _FakePopen(*a, **k))[1],
        )

        router = create_settings_router(root)
        train = _endpoint(router, "/api/settings/voice-clone/train", "POST")
        resp = await train({"audio_path": str(tmp_path / "no_such.mp3")})
        assert resp["ok"] is False
        assert resp["started"] is False
        assert called["popen"] == 0


class TestVoiceTrainStatus:
    """GET /voice-clone/train-status。"""

    async def test_tail_and_running_true(
        self, tmp_path: Path
    ) -> None:
        """有运行中进程 + 日志文件：running=True，返回最近30行。"""
        root = _make_project(tmp_path)
        log_dir = root / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "voice_train_test.log"
        log_file.write_text("\n".join(f"line{i}" for i in range(50)), encoding="utf-8")

        running = _FakePopen("x")
        running._returncode = None
        settings_api_module._voice_train_state["process"] = running
        settings_api_module._voice_train_state["log_file"] = str(log_file)

        router = create_settings_router(root)
        status = _endpoint(router, "/api/settings/voice-clone/train-status", "GET")
        resp = await status()
        assert resp["running"] is True
        assert resp["done"] is False
        assert len(resp["last_lines"]) == 30
        assert resp["last_lines"][-1] == "line49"

    async def test_done_when_process_finished(
        self, tmp_path: Path
    ) -> None:
        """进程已结束但有日志文件：running=False、done=True。"""
        root = _make_project(tmp_path)
        log_dir = root / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "voice_train_done.log"
        log_file.write_text("finished\n训练完成\n", encoding="utf-8")

        finished = _FakePopen("x")
        finished._returncode = 0  # 已结束
        settings_api_module._voice_train_state["process"] = finished
        settings_api_module._voice_train_state["log_file"] = str(log_file)

        router = create_settings_router(root)
        status = _endpoint(router, "/api/settings/voice-clone/train-status", "GET")
        resp = await status()
        assert resp["running"] is False
        assert resp["done"] is True
        assert "训练完成" in resp["last_lines"]

    async def test_never_trained(self, tmp_path: Path) -> None:
        """从未训练：running/done 均 False，last_lines 空。"""
        root = _make_project(tmp_path)
        router = create_settings_router(root)
        status = _endpoint(router, "/api/settings/voice-clone/train-status", "GET")
        resp = await status()
        assert resp["running"] is False
        assert resp["done"] is False
        assert resp["last_lines"] == []


class TestVoiceTrainStop:
    """POST /voice-clone/train-stop。"""

    async def test_stop_running_process(self, tmp_path: Path) -> None:
        """有运行进程：terminate 被调用，句柄清空。"""
        root = _make_project(tmp_path)
        running = _FakePopen("x")
        running._returncode = None
        settings_api_module._voice_train_state["process"] = running

        router = create_settings_router(root)
        stop = _endpoint(router, "/api/settings/voice-clone/train-stop", "POST")
        resp = await stop()
        assert resp["ok"] is True
        assert resp["stopped"] is True
        assert running.terminated is True
        assert settings_api_module._voice_train_state["process"] is None

    async def test_stop_when_idle(self, tmp_path: Path) -> None:
        """没有运行进程：ok=True、stopped=False、不报错。"""
        root = _make_project(tmp_path)
        router = create_settings_router(root)
        stop = _endpoint(router, "/api/settings/voice-clone/train-stop", "POST")
        resp = await stop()
        assert resp["ok"] is True
        assert resp["stopped"] is False
