"""
测试设置面板后端 API（settings_api.py）。

2026-07-02 审计修复（批2）新增：
- POST /api/settings 保存改为「读现有 conf.yaml → 深合并前端子集 → 写回」，不再整体覆盖
- POST /api/settings/prompt 空 prompt 返回 400，防止清空系统提示词
- GET  /api/settings/status 整体结果 10 秒 TTL 缓存
- POST /api/settings/restart 改用 subprocess.Popen（路径含空格安全）
- 模块顶部补 import json（修 github 文件接口的 NameError）

不依赖 httpx/TestClient：直接从 APIRouter 中取出端点函数调用。
"""

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

import pytest
import yaml
from fastapi import HTTPException

from white_salary.infrastructure.server.settings_api import (
    SettingsUpdate,
    create_settings_router,
)


def _endpoint(router: Any, path: str, method: str) -> Callable:
    """从 APIRouter 中按路径+方法取出端点函数（避免依赖 TestClient/httpx）。"""
    for route in router.routes:
        if route.path == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"路由未找到: {method} {path}")


def _write_yaml(path: Path, data: dict) -> None:
    """把字典写成 YAML 文件。"""
    path.write_text(
        yaml.dump(data, default_flow_style=False, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def _make_project(tmp_path: Path) -> Path:
    """构造一个最小化的临时项目根目录（含 conf.default.yaml 和 conf.yaml）。"""
    _write_yaml(tmp_path / "conf.default.yaml", {
        "system": {"name": "White Salary"},
        "llm": {"provider": "openai", "api_key": "", "model": "gpt-4o"},
        "tts": {"provider": "gpt_sovits"},  # 默认模板独有的节，不应被固化进用户配置
    })
    _write_yaml(tmp_path / "conf.yaml", {
        # 面板表单不管理的节——保存后必须保留
        "system": {"debug": True},
        "asr": {"provider": "sensevoice"},
        "llm": {
            "provider": "siliconflow",
            "api_key": "sk-realkey12345678",
            "model": "old-model",
        },
    })
    return tmp_path


class TestSaveSettingsDeepMerge:
    """POST /api/settings：验证深合并写回，不再用表单子集整体覆盖。"""

    async def test_subset_save_preserves_unmanaged_sections(self, tmp_path: Path) -> None:
        """只提交 llm 子集时，面板不管理的 system/asr 节必须原样保留。"""
        root = _make_project(tmp_path)
        router = create_settings_router(root)
        save_settings = _endpoint(router, "/api/settings", "POST")

        resp = await save_settings(SettingsUpdate(settings={"llm": {"model": "new-model"}}))
        assert resp["status"] == "ok"

        saved = yaml.safe_load((root / "conf.yaml").read_text(encoding="utf-8"))
        # 手工配置节未被删除
        assert saved["system"]["debug"] is True
        assert saved["asr"]["provider"] == "sensevoice"
        # 提交的键被更新
        assert saved["llm"]["model"] == "new-model"
        # llm 节内未提交的键（api_key/provider）也保留
        assert saved["llm"]["api_key"] == "sk-realkey12345678"
        assert saved["llm"]["provider"] == "siliconflow"

    async def test_defaults_not_baked_into_user_config(self, tmp_path: Path) -> None:
        """保存时不应把 conf.default.yaml 的默认节（如 tts）固化进 conf.yaml。"""
        root = _make_project(tmp_path)
        router = create_settings_router(root)
        save_settings = _endpoint(router, "/api/settings", "POST")

        await save_settings(SettingsUpdate(settings={"llm": {"model": "new-model"}}))

        saved = yaml.safe_load((root / "conf.yaml").read_text(encoding="utf-8"))
        assert "tts" not in saved  # 默认模板独有的节不应出现在用户配置里

    async def test_masked_api_key_keeps_original(self, tmp_path: Path) -> None:
        """提交带 *** 掩码的 api_key 时，保留原始密钥不被掩码值覆盖。"""
        root = _make_project(tmp_path)
        router = create_settings_router(root)
        save_settings = _endpoint(router, "/api/settings", "POST")

        await save_settings(SettingsUpdate(settings={"llm": {"api_key": "sk-rea***5678"}}))

        saved = yaml.safe_load((root / "conf.yaml").read_text(encoding="utf-8"))
        assert saved["llm"]["api_key"] == "sk-realkey12345678"


class TestSavePromptGuard:
    """POST /api/settings/prompt：空 prompt 必须 400 拒绝，防止清空系统提示词。"""

    async def test_empty_prompt_rejected_and_file_untouched(self, tmp_path: Path) -> None:
        """缺失/空串/纯空白/非字符串的 prompt 均返回 400，且提示词文件内容不变。"""
        root = _make_project(tmp_path)
        prompt_file = root / "prompts" / "system_prompt.txt"
        prompt_file.parent.mkdir(parents=True, exist_ok=True)
        prompt_file.write_text("原有人设内容", encoding="utf-8")

        router = create_settings_router(root)
        save_prompt = _endpoint(router, "/api/settings/prompt", "POST")

        for bad_body in ({}, {"prompt": ""}, {"prompt": "   "}, {"prompt": None}):
            with pytest.raises(HTTPException) as exc_info:
                await save_prompt(bad_body)
            assert exc_info.value.status_code == 400

        assert prompt_file.read_text(encoding="utf-8") == "原有人设内容"

    async def test_valid_prompt_saved(self, tmp_path: Path) -> None:
        """非空 prompt 正常写入。"""
        root = _make_project(tmp_path)
        router = create_settings_router(root)
        save_prompt = _endpoint(router, "/api/settings/prompt", "POST")

        resp = await save_prompt({"prompt": "新的人设"})
        assert resp["status"] == "ok"
        assert (root / "prompts" / "system_prompt.txt").read_text(encoding="utf-8") == "新的人设"


def _set_prompt_file(root: Path, prompt_file: str) -> None:
    cfg = yaml.safe_load((root / "conf.yaml").read_text(encoding="utf-8")) or {}
    cfg["personality"] = {"system_prompt_file": prompt_file}
    _write_yaml(root / "conf.yaml", cfg)


SECTIONED_PROMPT = """【最高优先级 - 输出格式规则】
格式规则
【白 - 角色人设档案】
【基本资料】
基本资料
【外貌特征 - 详细版】
外貌
【居住环境】
居住
【性格特点】
性格
【兴趣爱好】
爱好
【白的自述】
故事
【角色设定详解】
详解
【小白指定的绝对执行规则】
规则
【自主意識與家人互動規則】
家人
【主動搜索規則 - 不懂就上網查】
搜索
【禁止編造記憶 - 不記得就說不記得】
记忆
【理解表情包和圖片的意圖】
图片
【遭受攻擊時的反擊規則】
反击
"""


class TestPromptFileResolution:
    async def test_prompt_read_write_uses_configured_file(self, tmp_path: Path) -> None:
        root = _make_project(tmp_path)
        custom = root / "prompts" / "persona" / "main.txt"
        default_prompt = root / "prompts" / "system_prompt.txt"
        custom.parent.mkdir(parents=True)
        custom.write_text("custom persona", encoding="utf-8")
        default_prompt.parent.mkdir(parents=True, exist_ok=True)
        default_prompt.write_text("default persona", encoding="utf-8")
        _set_prompt_file(root, "prompts/persona/main.txt")

        router = create_settings_router(root)
        get_prompt = _endpoint(router, "/api/settings/prompt", "GET")
        save_prompt = _endpoint(router, "/api/settings/prompt", "POST")

        assert (await get_prompt())["prompt"] == "custom persona"
        resp = await save_prompt({"prompt": "updated persona"})

        assert resp["status"] == "ok"
        assert custom.read_text(encoding="utf-8") == "updated persona"
        assert default_prompt.read_text(encoding="utf-8") == "default persona"

    async def test_prompt_sections_use_configured_file(self, tmp_path: Path) -> None:
        root = _make_project(tmp_path)
        custom = root / "prompts" / "persona" / "main.txt"
        custom.parent.mkdir(parents=True)
        custom.write_text(SECTIONED_PROMPT, encoding="utf-8")
        _set_prompt_file(root, "prompts/persona/main.txt")

        router = create_settings_router(root)
        get_sections = _endpoint(router, "/api/settings/prompt/sections", "GET")
        save_section = _endpoint(router, "/api/settings/prompt/sections/{section_name}", "PUT")

        data = await get_sections()
        assert data["prompt_path"] == str(Path("prompts") / "persona" / "main.txt")
        assert data["sections"]["format_rules"].startswith("【最高优先级")
        assert data["sections"]["basic_info"].startswith("【基本资料】")

        resp = await save_section("personality", {"content": "【性格特点】\n新的性格"})
        assert resp["status"] == "ok"
        assert "新的性格" in custom.read_text(encoding="utf-8")

    async def test_prompt_sections_missing_file_returns_clear_error(self, tmp_path: Path) -> None:
        root = _make_project(tmp_path)
        _set_prompt_file(root, "prompts/persona/missing.txt")

        router = create_settings_router(root)
        get_sections = _endpoint(router, "/api/settings/prompt/sections", "GET")

        with pytest.raises(HTTPException) as exc_info:
            await get_sections()

        assert exc_info.value.status_code == 404
        assert "人设文件不存在" in exc_info.value.detail

    async def test_prompt_file_cannot_escape_project_root(self, tmp_path: Path) -> None:
        root = _make_project(tmp_path)
        _set_prompt_file(root, "../outside.txt")

        router = create_settings_router(root)
        get_prompt = _endpoint(router, "/api/settings/prompt", "GET")

        with pytest.raises(HTTPException) as exc_info:
            await get_prompt()

        assert exc_info.value.status_code == 400
        assert "personality.system_prompt_file" in exc_info.value.detail


class TestGetStatusTTLCache:
    """GET /api/settings/status：10 秒内重复调用应命中同一份缓存结果。"""

    async def test_second_call_hits_cache(self, tmp_path: Path) -> None:
        """连续两次调用返回同一个缓存对象（第一次真实探测，第二次命中 TTL 缓存）。"""
        root = _make_project(tmp_path)
        router = create_settings_router(root)
        get_status = _endpoint(router, "/api/settings/status", "GET")

        result1 = await get_status()
        result2 = await get_status()

        # 命中缓存时返回的是同一个 dict 对象
        assert result2 is result1
        # 结果结构完整
        for key in ("backend", "backend_pid", "tts_local", "qq_connected",
                    "memory_count", "conversation_count", "vision_enabled"):
            assert key in result1


class TestStartTTSEndpoint:
    """POST /api/settings/start-tts: local GPT-SoVITS launcher."""

    async def test_start_tts_uses_configured_quoted_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sovits = tmp_path / "GPT SoVITS"
        (sovits / "venv_new" / "Scripts").mkdir(parents=True)
        (sovits / "venv_new" / "Scripts" / "activate.bat").write_text(
            "@echo off\n", encoding="utf-8"
        )
        (sovits / "api_v2.py").write_text("print('ok')\n", encoding="utf-8")

        from white_salary.adapters.tools import external_paths as ep

        monkeypatch.setattr(ep, "get_gpt_sovits_dir", lambda: sovits)
        calls: dict[str, Any] = {}

        class FakePopen:
            pid = 1234

            def __init__(self, cmd: str, **kwargs: Any) -> None:
                calls["cmd"] = cmd
                calls["kwargs"] = kwargs

            def poll(self) -> None:
                return None

        monkeypatch.setattr(subprocess, "Popen", FakePopen)

        router = create_settings_router(_make_project(tmp_path))
        start_tts = _endpoint(router, "/api/settings/start-tts", "POST")
        resp = await start_tts()

        assert resp["status"] == "ok"
        assert f'cd /d "{os.path.normpath(str(sovits))}"' in calls["cmd"]
        assert 'call "venv_new\\Scripts\\activate.bat"' in calls["cmd"]
        assert calls["kwargs"].get("shell") is True

    async def test_start_tts_missing_install_returns_message(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from white_salary.adapters.tools import external_paths as ep

        monkeypatch.setattr(ep, "get_gpt_sovits_dir", lambda: tmp_path / "missing")
        called = {"popen": False}

        def fake_popen(*args: Any, **kwargs: Any) -> object:
            called["popen"] = True
            return object()

        monkeypatch.setattr(subprocess, "Popen", fake_popen)

        router = create_settings_router(_make_project(tmp_path))
        start_tts = _endpoint(router, "/api/settings/start-tts", "POST")
        resp = await start_tts()

        assert resp["success"] is False
        assert "GPT-SoVITS" in resp["message"]
        assert called["popen"] is False


class TestRestartEndpoint:
    """POST /api/settings/restart：用 subprocess.Popen 拉起新进程后 os._exit(0)。"""

    async def test_restart_uses_popen_and_exit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """重启走 Popen([sys.executable]+sys.argv, cwd=当前目录) 而非 os.execv（路径含空格安全）。"""
        calls: dict[str, Any] = {}

        def fake_popen(args: list, **kwargs: Any) -> object:
            calls["args"] = args
            calls["kwargs"] = kwargs
            return object()

        def fake_exit(code: int) -> None:
            calls["exit_code"] = code

        # 先打补丁再调用，确保后台任务不会真的重启/退出测试进程
        monkeypatch.setattr(subprocess, "Popen", fake_popen)
        monkeypatch.setattr(os, "_exit", fake_exit)

        router = create_settings_router(_make_project(tmp_path))
        restart_backend = _endpoint(router, "/api/settings/restart", "POST")

        resp = await restart_backend()
        assert resp["status"] == "ok"

        # 后台任务 sleep(1) 后执行，等待其完成
        await asyncio.sleep(1.3)

        assert calls["args"] == [sys.executable] + sys.argv
        assert calls["kwargs"].get("cwd") == os.getcwd()
        assert calls["exit_code"] == 0


class TestModuleImports:
    """模块级导入检查。"""

    def test_json_imported_at_module_top(self) -> None:
        """模块顶部已 import json（修 github_read_file/github_write_file 的 NameError）。"""
        import white_salary.infrastructure.server.settings_api as settings_api_module
        assert getattr(settings_api_module, "json", None) is json
