"""黄金测试 — ComfyUI 自动启动的 _starting 标志必须在取消路径下也复位。

缺陷形状：`_starting` 原本只在 return / `except Exception` 分支里复位。而
`asyncio.CancelledError` 自 Python 3.8 起继承 **BaseException**，`except Exception`
接不住它（见 asyncio 官方文档"Task Cancellation"）。用户中途打断、或上层
`asyncio.timeout` 取消这次调用时，那几处复位全被跳过 → 标志位永久为 True →
之后每次 ensure_comfyui_running 一进来就因"正在启动中"直接返回，
**ComfyUI 再也不会被自动拉起，而且没有任何报错**。

修复用 try/finally + started_here。这条测试直接构造取消场景验证。
"""

from __future__ import annotations

import asyncio
import os

import pytest

from white_salary.adapters.tools import comfyui_client


@pytest.mark.skipif(
    os.name != "nt",
    reason=(
        "ensure_comfyui_running 对非 Windows 直接 return False（.bat 自动启动只支持 "
        "Windows），任务在 cancel() 之前就已结束，构造不出取消场景。"
        "本用例守的是 Windows 上的 _starting 标志复位。"
    ),
)
@pytest.mark.asyncio
async def test_starting_flag_is_released_when_cancelled(monkeypatch, tmp_path) -> None:
    """被取消时 _starting 必须复位，否则自动启动能力永久失效。"""
    bat = tmp_path / "run.bat"
    bat.write_text("echo fake", encoding="utf-8")

    monkeypatch.setattr(comfyui_client, "_starting", False, raising=False)
    monkeypatch.setattr(comfyui_client, "is_comfyui_online", lambda: _never_online())
    monkeypatch.setattr(
        "white_salary.adapters.tools.external_paths.get_comfyui_bat",
        lambda **kwargs: bat,
    )
    monkeypatch.setattr(comfyui_client.subprocess, "Popen", lambda *a, **k: None)

    async def _never_online():
        return False

    task = asyncio.create_task(comfyui_client.ensure_comfyui_running(timeout=30))
    await asyncio.sleep(0.05)  # 让它进入启动分支并置上标志位
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert comfyui_client._starting is False, (
        "取消后 _starting 仍为 True —— ComfyUI 自动启动将永久失效且无任何报错。"
        "复位必须放在 finally，因为 CancelledError 是 BaseException，"
        "except Exception 接不住它。"
    )


def test_generate_image_timeout_default_stays_responsive() -> None:
    """生成超时默认值不得被拉长到分钟级。

    桌宠场景下，本地失败要能快速降级到云端。本地分支曾把它连同启动等待一起
    提到 600 秒，结果本地不可用时用户要干等 10 分钟才看到云端结果。
    需要更久的调用方应显式传参，而不是改全局默认。
    """
    import inspect

    sig = inspect.signature(comfyui_client.generate_image)
    assert sig.parameters["timeout"].default <= 180, (
        f"generate_image 的 timeout 默认值 {sig.parameters['timeout'].default} 过长"
    )

    from white_salary.adapters.tools import image_gen

    sig2 = inspect.signature(image_gen._try_comfyui)
    assert sig2.parameters["generation_timeout"].default <= 180
    assert sig2.parameters["startup_timeout"].default <= 120


def test_cloud_fallback_shortens_local_startup_budget() -> None:
    """配了云端时本地冷启动预算应当更短（快速降级），这条自适应逻辑不能丢。"""
    import inspect

    source = inspect.getsource(__import__(
        "white_salary.adapters.tools.image_gen", fromlist=["*"]
    ))
    assert "startup_timeout = 15 if" in source, (
        "有云端可降级时的短冷启动预算被删除了——本地没装 ComfyUI 的用户"
        "每次出图都要先干等满额超时"
    )
