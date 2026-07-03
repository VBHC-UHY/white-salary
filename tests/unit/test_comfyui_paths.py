"""
测试 Bug3 修复：写死的 ComfyUI 路径改为环境变量可覆盖（并保留原默认值）。

修复点：comfyui_client.COMFYUI_BAT 由 os.environ.get("WS_COMFYUI_BAT", <原默认>) 决定，
换机器时设环境变量即可，不设则回退到原来的默认路径，不破坏现状。
"""

import importlib


class TestComfyUIPathEnvOverride:
    """COMFYUI_BAT 路径可被环境变量覆盖。"""

    def test_env_override(self, monkeypatch) -> None:
        """设了 WS_COMFYUI_BAT 后，重新加载模块应采用该路径。"""
        from white_salary.adapters.tools import comfyui_client

        monkeypatch.setenv("WS_COMFYUI_BAT", "/tmp/custom/run.bat")
        importlib.reload(comfyui_client)
        try:
            # 2026-07-02 修复：用 Path 对象比较而不是字符串比较——
            # Windows 上 str(WindowsPath) 输出反斜杠，原断言在 Windows 必失败（测试不可移植）
            from pathlib import Path

            assert comfyui_client.COMFYUI_BAT == Path("/tmp/custom/run.bat")
        finally:
            # 还原模块状态，避免影响其它测试
            monkeypatch.delenv("WS_COMFYUI_BAT", raising=False)
            importlib.reload(comfyui_client)

    def test_default_unconfigured(self, monkeypatch) -> None:
        """No env override means ComfyUI auto-start remains unconfigured."""
        from white_salary.adapters.tools import comfyui_client

        monkeypatch.delenv("WS_COMFYUI_BAT", raising=False)
        importlib.reload(comfyui_client)
        assert comfyui_client.COMFYUI_BAT is None
