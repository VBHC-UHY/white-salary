"""端到端测试设施：把真实后端连同假上游一起拉起来。

设计约束（都是踩过的坑）：
  - 端口必须动态分配。写死端口会和用户正在运行的实例（默认 12400）撞车。
  - 数据目录必须隔离到 tmp。真跑一遍会写 data/ 下的记忆、好感度、任务账本，
    绝不能污染工作区或用户数据。
  - 后端以子进程启动（而不是 in-process TestClient），因为要验证的正是
    真实事件循环下的时序行为：流式回复、打断、后台桥循环。
  - 一切等待都基于"真实就绪信号"（/health 有响应、端口可连），不用 sleep 猜。
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional

import urllib.error
import urllib.request

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def free_port() -> int:
    """要一个当前空闲的端口。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_for_http(url: str, timeout: float = 60.0, interval: float = 0.25) -> bool:
    """轮询直到 HTTP 有响应。返回是否成功，不抛异常。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as resp:
                if 200 <= resp.status < 500:
                    return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(interval)
    return False


def post_json(url: str, payload: dict[str, Any], timeout: float = 5.0) -> dict[str, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
    return json.loads(body) if body else {}


def get_json(url: str, timeout: float = 5.0) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


class ManagedProcess:
    """带日志收集的子进程包装，退出时能说清为什么。"""

    def __init__(self, name: str, cmd: list[str], cwd: Path, env: dict[str, str], log_path: Path):
        self.name = name
        self.cmd = cmd
        self.cwd = cwd
        self.env = env
        self.log_path = log_path
        self._log = open(log_path, "w", encoding="utf-8", errors="replace")
        self.proc = subprocess.Popen(
            cmd, cwd=str(cwd), env=env, stdout=self._log, stderr=subprocess.STDOUT
        )

    def is_alive(self) -> bool:
        return self.proc.poll() is None

    def read_log(self, tail_chars: int = 4000) -> str:
        try:
            self._log.flush()
        except Exception:
            pass
        try:
            text = self.log_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return "(日志不可读)"
        return text[-tail_chars:]

    def stop(self, timeout: float = 10.0) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=timeout)
        try:
            self._log.close()
        except Exception:
            pass


class E2EStack:
    """假上游 + 真后端。用作上下文管理器。"""

    def __init__(self, tmp_dir: Path, *, qq_enabled: bool = False, napcat_port: int = 0):
        self.tmp_dir = tmp_dir
        self.upstream_port = free_port()
        self.backend_port = free_port()
        self.qq_enabled = qq_enabled
        self.napcat_port = napcat_port
        self.upstream: Optional[ManagedProcess] = None
        self.backend: Optional[ManagedProcess] = None
        self._conf_backup: Optional[bytes] = None
        self._conf_written = False

    # ---- 地址 ----
    @property
    def upstream_base(self) -> str:
        return f"http://127.0.0.1:{self.upstream_port}"

    @property
    def backend_base(self) -> str:
        return f"http://127.0.0.1:{self.backend_port}"

    @property
    def ws_url(self) -> str:
        # 真实路由是 /ws/chat（run_server.py 的 @fastapi_app.websocket）。
        # 路径写错时 Starlette 返回 HTTP 403 而不是 404，很容易误判成鉴权问题。
        return f"ws://127.0.0.1:{self.backend_port}/ws/chat"

    # ---- 假上游控制 ----
    def configure_upstream(self, **kwargs: Any) -> None:
        post_json(f"{self.upstream_base}/__control__", kwargs)

    def upstream_stats(self) -> dict[str, Any]:
        return get_json(f"{self.upstream_base}/__stats__")

    def reset_upstream(self) -> None:
        post_json(f"{self.upstream_base}/__reset__", {})

    # ---- 生命周期 ----
    def _write_conf(self) -> Path:
        """写一份只指向本地假上游的测试配置。

        注意：`load_config` 固定从 project_root 读 `conf.yaml`，`run_server.py`
        的 project_root 又是它自己所在目录，所以测试配置只能落在项目根。
        conf.yaml 与 data/ 都在 .gitignore 里，不会污染仓库；但如果本机已有
        conf.yaml（开发者自己的配置），必须先备份、结束时原样还回去。
        """
        conf = {
            "system": {"debug": True},
            "server": {"host": "127.0.0.1", "port": self.backend_port},
            "llm": {
                "provider": "openai",
                "api_key": "fake-key-for-e2e",
                "model": "fake-model",
                "base_url": f"{self.upstream_base}/v1",
                "temperature": 0.7,
                "max_tokens": 256,
            },
            "asr": {
                "provider": "siliconflow",
                "api_key": "fake-key-for-e2e",
                "model": "fake-asr",
                "base_url": f"{self.upstream_base}/v1",
            },
            "tts": {
                # 指向假上游，避免去连本机 9880 而长时间等待
                "local_api_url": f"{self.upstream_base}/__no_local_tts__",
                "fallback_provider": "siliconflow",
                "fallback_api_key": "fake-key-for-e2e",
            },
            "qq": {
                "enabled": self.qq_enabled,
                "ws_url": f"ws://127.0.0.1:{self.napcat_port}" if self.napcat_port else "",
                "bot_name": "白",
                "wake_words": ["白"],
                "family_qq": ["10001"],
                "owner_name": "小白",
            },
            # 后台定时任务在 e2e 里只会制造噪声
            "features": {
                "memory_consolidation": False,
                "user_learning": False,
                "rest_system": False,
            },
            "auto_chat": {"enabled": False},
        }
        import yaml

        conf_path = PROJECT_ROOT / "conf.yaml"
        if conf_path.exists() and self._conf_backup is None:
            self._conf_backup = conf_path.read_bytes()
        conf_path.write_text(
            yaml.safe_dump(conf, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
        self._conf_written = True
        return conf_path

    def _restore_conf(self) -> None:
        """还原 conf.yaml：原本有就写回原内容，原本没有就删掉。"""
        if not self._conf_written:
            return
        conf_path = PROJECT_ROOT / "conf.yaml"
        try:
            if self._conf_backup is not None:
                conf_path.write_bytes(self._conf_backup)
            elif conf_path.exists():
                conf_path.unlink()
        except Exception:
            pass
        self._conf_written = False

    def _base_env(self) -> dict[str, str]:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(PROJECT_ROOT / "src")
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"
        # tests/conftest.py 为了让单元测试可复现，在 pytest 进程里设了
        # WS_DISABLE_TOOL_AUTODETECT=1。这里的 env 是 dict(os.environ) 全量继承，
        # 若不显式清掉，**后端子进程里的自动探测也是关着的**——
        # 于是"新用户点启动能自动找到外部工具"这条产品承诺在端到端层面
        # 根本没被验证过，而测试照样全绿。这正是本项目要极力避免的假绿。
        env.pop("WS_DISABLE_TOOL_AUTODETECT", None)
        return env

    def start(self) -> None:
        env = self._base_env()

        self.upstream = ManagedProcess(
            "upstream",
            [
                sys.executable, "-m", "uvicorn",
                "tests.e2e.fake_upstream:app",
                "--host", "127.0.0.1",
                "--port", str(self.upstream_port),
                "--log-level", "warning",
            ],
            cwd=PROJECT_ROOT,
            env=env,
            log_path=self.tmp_dir / "upstream.log",
        )
        if not wait_for_http(f"{self.upstream_base}/health", timeout=45):
            raise RuntimeError(
                f"假上游未能启动。日志：\n{self.upstream.read_log()}"
            )

        self._write_conf()

        self.backend = ManagedProcess(
            "backend",
            [sys.executable, "run_server.py", "--host", "127.0.0.1", "--port", str(self.backend_port)],
            cwd=PROJECT_ROOT,
            env=env,
            log_path=self.tmp_dir / "backend.log",
        )
        if not wait_for_http(f"{self.backend_base}/health", timeout=90):
            raise RuntimeError(
                f"后端未能启动。日志：\n{self.backend.read_log()}"
            )

    def stop(self) -> None:
        for proc in (self.backend, self.upstream):
            if proc is not None:
                proc.stop()
        self._restore_conf()

    def backend_log(self, tail_chars: int = 6000) -> str:
        return self.backend.read_log(tail_chars) if self.backend else "(后端未启动)"

    def __enter__(self) -> "E2EStack":
        try:
            self.start()
        except Exception:
            self.stop()
            raise
        return self

    def __exit__(self, *exc_info) -> None:
        self.stop()
