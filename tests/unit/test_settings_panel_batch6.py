"""
测试设置面板后端端点补齐（2026-07-03 面板升级 批6）。

覆盖 settings_api.py 本批新增/修缮的端点主路径与守卫：
- POST /api/settings/llm/test：LLM 通道 1-token 探活（role/直传参数/掩码Key回退）
- GET/PUT /api/settings/expression-map：情绪→表情映射存取（键合法性校验）
- GET /api/settings/live2d/expressions：枚举模型表情
- POST /api/settings/users/affinity/{user_id}/set：按用户改好感度/家人标记
- GET /api/settings/users/filter + 黑名单增删：优先运行实例（注册表键 user_filter）
- GET /api/settings/voice-clone/status：读 tts_infer.yaml（候选路径可注入）
- GET /api/settings/memory/search：长期记忆检索
- POST /api/settings/modules/toggle + GET /modules：模块开关写 modules.disabled
- POST /api/settings/qzone/test-post：发说说测试（Cookie 未配置明确报错）
- GET /api/settings/about：版本/工具数/模块数动态统计
- POST /api/settings/prompt、/prompt/apply_template：覆盖前自动备份（保留10份）+
  人格热更新（注册表键 personality，hasattr 守卫）
- POST /api/settings/qq/clear-context：清运行实例抛异常仍清文件
- UserFilter.list_blacklist 公开方法
- developer.py 安全项：GitHub 同步公开视图、默认密码登录提示、
  GET /developers/list 要求有效 token

沿用 test_settings_api.py / test_settings_di.py 的做法：直接从 APIRouter 取
端点函数调用（不依赖 TestClient），运行实例注册表用 monkeypatch 隔离。
"""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Optional

import pytest
import yaml
from fastapi import HTTPException

import white_salary.infrastructure.server.settings_api as settings_api_module
from white_salary.infrastructure.server.settings_api import (
    create_settings_router,
    register_runtime_instance,
    _module_description,
)


def _endpoint(router: Any, path: str, method: str) -> Callable:
    """从 APIRouter 中按路径+方法取出端点函数（避免依赖 TestClient/httpx）。"""
    for route in router.routes:
        if route.path == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"路由未找到: {method} {path}")


def _make_project(tmp_path: Path, conf_extra: Optional[dict] = None) -> Path:
    """构造一个最小化的临时项目根目录（含 conf.default.yaml 和 conf.yaml）。"""
    (tmp_path / "conf.default.yaml").write_text(
        yaml.dump({"system": {"name": "White Salary"}}, allow_unicode=True),
        encoding="utf-8",
    )
    conf: dict[str, Any] = {"llm": {"provider": "siliconflow"}}
    if conf_extra:
        conf.update(conf_extra)
    (tmp_path / "conf.yaml").write_text(
        yaml.dump(conf, allow_unicode=True), encoding="utf-8"
    )
    return tmp_path


@pytest.fixture(autouse=True)
def _isolated_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """每个用例用独立的模块级注册表，防止注册的 fake 实例泄漏到其他测试。"""
    monkeypatch.setattr(settings_api_module, "_runtime_registry", {})


# ====================================================================
# POST /api/settings/llm/test
# ====================================================================

class _FakeLLMAdapter:
    """模拟 OpenAICompatibleAdapter：记录构造参数，chat_completion 返回固定值。"""

    last_init: dict = {}

    def __init__(self, api_key: str, base_url: str, model: str, timeout: float = 60.0) -> None:
        type(self).last_init = {
            "api_key": api_key, "base_url": base_url, "model": model,
        }

    async def chat_completion(self, messages: list, temperature: float = 0.7,
                              max_tokens: int = 2048) -> str:
        """探活成功路径。"""
        return "x"


class _FailingLLMAdapter(_FakeLLMAdapter):
    """模拟坏通道：chat_completion 直接抛异常。"""

    async def chat_completion(self, messages: list, temperature: float = 0.7,
                              max_tokens: int = 2048) -> str:
        raise RuntimeError("401 Unauthorized")


class TestLLMTest:
    """LLM 通道连通性测试端点。"""

    async def test_invalid_role_rejected(self, tmp_path: Path) -> None:
        """非法 role 返回 400。"""
        router = create_settings_router(_make_project(tmp_path))
        test_llm = _endpoint(router, "/api/settings/llm/test", "POST")
        with pytest.raises(HTTPException) as exc:
            await test_llm({"role": "llm_hacker"})
        assert exc.value.status_code == 400

    async def test_incomplete_config_reports_missing(self, tmp_path: Path) -> None:
        """通道未配置时返回 ok=False 并列出缺失字段，不发任何请求。"""
        router = create_settings_router(_make_project(tmp_path))
        test_llm = _endpoint(router, "/api/settings/llm/test", "POST")
        resp = await test_llm({"role": "llm_tool"})
        assert resp["ok"] is False
        assert "api_key" in resp["error"]

    async def test_direct_params_success(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """直传 provider/api_key/base_url/model：探活成功返回 ok=True 与耗时。"""
        import white_salary.adapters.llm.openai_compatible as oa_module
        monkeypatch.setattr(oa_module, "OpenAICompatibleAdapter", _FakeLLMAdapter)

        router = create_settings_router(_make_project(tmp_path))
        test_llm = _endpoint(router, "/api/settings/llm/test", "POST")
        resp = await test_llm({
            "role": "llm",
            "api_key": "sk-form-key",
            "base_url": "https://example.com/v1",
            "model": "test-model",
        })
        assert resp["ok"] is True
        assert resp["error"] == ""
        assert resp["model"] == "test-model"
        assert resp["elapsed_ms"] >= 0
        assert _FakeLLMAdapter.last_init["api_key"] == "sk-form-key"

    async def test_masked_key_falls_back_to_saved(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """前端回传掩码 Key（含***）时，回退用 conf.yaml 里保存的真实 Key。"""
        import white_salary.adapters.llm.openai_compatible as oa_module
        monkeypatch.setattr(oa_module, "OpenAICompatibleAdapter", _FakeLLMAdapter)

        root = _make_project(tmp_path, conf_extra={
            "llm": {"provider": "siliconflow", "api_key": "sk-saved-real-key",
                    "model": "saved-model"},
        })
        router = create_settings_router(root)
        test_llm = _endpoint(router, "/api/settings/llm/test", "POST")
        resp = await test_llm({"role": "llm", "api_key": "sk-sav***-key"})
        assert resp["ok"] is True
        assert _FakeLLMAdapter.last_init["api_key"] == "sk-saved-real-key"
        # base_url 从预设供应商表补齐
        assert "siliconflow" in _FakeLLMAdapter.last_init["base_url"]

    async def test_channel_failure_reports_reason(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """通道请求异常时 ok=False，error 带异常类型与原因。"""
        import white_salary.adapters.llm.openai_compatible as oa_module
        monkeypatch.setattr(oa_module, "OpenAICompatibleAdapter", _FailingLLMAdapter)

        router = create_settings_router(_make_project(tmp_path))
        test_llm = _endpoint(router, "/api/settings/llm/test", "POST")
        resp = await test_llm({
            "role": "llm", "api_key": "sk-x",
            "base_url": "https://example.com/v1", "model": "m",
        })
        assert resp["ok"] is False
        assert "401" in resp["error"]


# ====================================================================
# GET/PUT /api/settings/expression-map
# ====================================================================

class TestExpressionMap:
    """情绪→表情映射存取端点。"""

    async def test_get_without_file_falls_back_to_default_16(self, tmp_path: Path) -> None:
        """文件缺失：回退硬编码表，16种情绪齐全，source=default。"""
        router = create_settings_router(_make_project(tmp_path))
        get_map = _endpoint(router, "/api/settings/expression-map", "GET")
        resp = await get_map()
        assert resp["source"] == "default"
        assert len(resp["map"]) == 16
        assert len(resp["emotions"]) == 16
        assert resp["map"]["happy"]["expression"] == "happy"

    async def test_put_string_value_merges_and_keeps_16_keys(self, tmp_path: Path) -> None:
        """PUT 表情名字符串：归一化为对象、未提交的情绪保留默认，文件始终16键。"""
        root = _make_project(tmp_path)
        router = create_settings_router(root)
        put_map = _endpoint(router, "/api/settings/expression-map", "PUT")
        resp = await put_map({"map": {"happy": "XD"}})
        assert resp["status"] == "ok"

        saved = json.loads(
            (root / "config" / "expression_map.json").read_text(encoding="utf-8")
        )
        assert len(saved) == 16
        assert saved["happy"]["expression"] == "XD"
        # 未提交的情绪保留默认值
        assert saved["sad"]["expression"] == "sad"

        # GET 现在读文件
        get_map = _endpoint(router, "/api/settings/expression-map", "GET")
        resp2 = await get_map()
        assert resp2["source"] == "file"
        assert resp2["map"]["happy"]["expression"] == "XD"

    async def test_put_unknown_emotion_rejected(self, tmp_path: Path) -> None:
        """非法情绪键返回 400，文件不落盘。"""
        root = _make_project(tmp_path)
        router = create_settings_router(root)
        put_map = _endpoint(router, "/api/settings/expression-map", "PUT")
        with pytest.raises(HTTPException) as exc:
            await put_map({"map": {"rage_quit": "angry"}})
        assert exc.value.status_code == 400
        assert not (root / "config" / "expression_map.json").exists()

    async def test_put_empty_rejected(self, tmp_path: Path) -> None:
        """空 map 返回 400。"""
        router = create_settings_router(_make_project(tmp_path))
        put_map = _endpoint(router, "/api/settings/expression-map", "PUT")
        with pytest.raises(HTTPException) as exc:
            await put_map({"map": {}})
        assert exc.value.status_code == 400


# ====================================================================
# GET /api/settings/live2d/expressions
# ====================================================================

class TestLive2DExpressions:
    """Live2D 表情枚举端点。"""

    async def test_reads_expression_names_from_model3(self, tmp_path: Path) -> None:
        """从 model3.json 的 FileReferences.Expressions 读 Name 列表。"""
        root = _make_project(tmp_path)
        model_dir = root / "live2d_models" / "default"
        model_dir.mkdir(parents=True, exist_ok=True)
        (model_dir / "ulvm2_0001.model3.json").write_text(json.dumps({
            "FileReferences": {"Expressions": [
                {"Name": "happy", "File": "expressions/happy.exp3.json"},
                {"Name": "XD", "File": "expressions/XD.exp3.json"},
            ]},
        }), encoding="utf-8")

        router = create_settings_router(root)
        get_exprs = _endpoint(router, "/api/settings/live2d/expressions", "GET")
        resp = await get_exprs()
        assert resp["expressions"] == ["happy", "XD"]
        assert resp["count"] == 2

    async def test_missing_model_returns_empty_with_error(self, tmp_path: Path) -> None:
        """模型文件缺失：空列表+错误说明，不抛异常。"""
        router = create_settings_router(_make_project(tmp_path))
        get_exprs = _endpoint(router, "/api/settings/live2d/expressions", "GET")
        resp = await get_exprs()
        assert resp["expressions"] == []
        assert "error" in resp


# ====================================================================
# 用户管理：好感度修改 + 黑名单运行实例
# ====================================================================

class _FakePerUserAffinity:
    """模拟单用户 AffinityManager。"""

    def __init__(self) -> None:
        self.points: float = 0.0
        self.is_family: bool = False

    def set_points(self, points: float) -> None:
        self.points = points

    def set_family(self, is_family: bool) -> None:
        self.is_family = is_family

    def get_stats(self) -> dict:
        return {"points": self.points, "is_family": self.is_family,
                "level_name": "测试"}


class _FakeAffinityManagerClass:
    """模拟 AffinityManager 类（只提供 get_for_user 类方法）。"""

    instances: dict[str, _FakePerUserAffinity] = {}
    last_data_dir: str = ""

    @classmethod
    def get_for_user(cls, user_id: str, data_dir: str = "data/affinity") -> _FakePerUserAffinity:
        cls.last_data_dir = data_dir
        if user_id not in cls.instances:
            cls.instances[user_id] = _FakePerUserAffinity()
        return cls.instances[user_id]


class _FakeUserFilter:
    """模拟运行中的 UserFilter（含 list_blacklist 公开方法）。"""

    def __init__(self) -> None:
        self.added: list[tuple] = []
        self.removed: list[str] = []

    @property
    def stats(self) -> dict:
        return {"mode": "blacklist", "hard_blacklist": 1, "soft_blacklist": 0}

    def add_to_blacklist(self, user_id: str, nickname: str = "",
                         reason: str = "", permanent: bool = False) -> None:
        self.added.append((user_id, nickname, reason, permanent))

    def remove_from_blacklist(self, user_id: str) -> bool:
        self.removed.append(user_id)
        return True

    def list_blacklist(self) -> list[dict]:
        return [{"user_id": "666", "nickname": "捣蛋鬼", "type": "hard"}]


class TestUsersManagement:
    """用户管理端点。"""

    async def test_set_affinity_points_and_family(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """按 user_id 改分+设家人：走 get_for_user 共享实例路径。"""
        import white_salary.core.affinity.manager as aff_module
        _FakeAffinityManagerClass.instances = {}
        monkeypatch.setattr(aff_module, "AffinityManager", _FakeAffinityManagerClass)

        root = _make_project(tmp_path)
        router = create_settings_router(root)
        set_aff = _endpoint(router, "/api/settings/users/affinity/{user_id}/set", "POST")
        resp = await set_aff("12345", {"points": 88.5, "is_family": True})
        assert resp["status"] == "ok"
        assert resp["points"] == 88.5
        assert resp["is_family"] is True
        # data_dir 用 project_root 拼接（与QQ运行时共享实例一致）
        assert str(root) in _FakeAffinityManagerClass.last_data_dir

    async def test_set_affinity_requires_at_least_one_field(self, tmp_path: Path) -> None:
        """body 无 points 也无 is_family：400。"""
        router = create_settings_router(_make_project(tmp_path))
        set_aff = _endpoint(router, "/api/settings/users/affinity/{user_id}/set", "POST")
        with pytest.raises(HTTPException) as exc:
            await set_aff("12345", {})
        assert exc.value.status_code == 400

    async def test_set_affinity_invalid_points_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """points 非数字：400。"""
        import white_salary.core.affinity.manager as aff_module
        monkeypatch.setattr(aff_module, "AffinityManager", _FakeAffinityManagerClass)
        router = create_settings_router(_make_project(tmp_path))
        set_aff = _endpoint(router, "/api/settings/users/affinity/{user_id}/set", "POST")
        with pytest.raises(HTTPException) as exc:
            await set_aff("12345", {"points": "很多"})
        assert exc.value.status_code == 400

    async def test_filter_status_prefers_runtime_and_returns_blacklist(
        self, tmp_path: Path
    ) -> None:
        """注册表有 user_filter 实例：GET /users/filter 用它并返回黑名单明细。"""
        fake_uf = _FakeUserFilter()
        register_runtime_instance("user_filter", fake_uf)
        router = create_settings_router(_make_project(tmp_path))
        get_filter = _endpoint(router, "/api/settings/users/filter", "GET")
        resp = await get_filter()
        assert resp["status"] == "ok"
        assert resp["runtime"] is True
        assert resp["blacklist"][0]["user_id"] == "666"

    async def test_blacklist_add_remove_via_runtime(self, tmp_path: Path) -> None:
        """拉黑/解除优先操作运行实例（即时生效）。"""
        fake_uf = _FakeUserFilter()
        register_runtime_instance("user_filter", fake_uf)
        router = create_settings_router(_make_project(tmp_path))

        add_bl = _endpoint(router, "/api/settings/users/filter/blacklist", "POST")
        resp = await add_bl({"user_id": "666", "nickname": "捣蛋鬼", "reason": "刷屏"})
        assert resp["status"] == "ok"
        assert resp["runtime"] is True
        assert fake_uf.added[0][0] == "666"

        del_bl = _endpoint(
            router, "/api/settings/users/filter/blacklist/{user_id}", "DELETE"
        )
        resp2 = await del_bl("666")
        assert resp2["status"] == "ok"
        assert resp2["removed"] is True
        assert fake_uf.removed == ["666"]

    async def test_filter_fallback_without_runtime_uses_project_root(
        self, tmp_path: Path
    ) -> None:
        """无运行实例：回退新建文件实例（data目录在临时项目根下，不碰真实数据）。"""
        root = _make_project(tmp_path)
        router = create_settings_router(root)
        get_filter = _endpoint(router, "/api/settings/users/filter", "GET")
        resp = await get_filter()
        assert resp["status"] == "ok"
        assert resp["runtime"] is False
        assert resp["blacklist"] == []


# ====================================================================
# GET /api/settings/voice-clone/status
# ====================================================================

class TestVoiceCloneStatus:
    """声音克隆状态端点。"""

    async def test_reads_weights_from_tts_infer_yaml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """候选路径可读：返回 custom 节的权重路径与版本。"""
        infer = tmp_path / "tts_infer.yaml"
        infer.write_text(yaml.dump({
            "custom": {
                "t2s_weights_path": "GPT_weights_v2/white_v1-e20.ckpt",
                "vits_weights_path": "SoVITS_weights_v2/white_v1_e16.pth",
                "version": "v2",
                "device": "cuda",
            },
        }), encoding="utf-8")
        monkeypatch.setattr(
            settings_api_module, "TTS_INFER_CONFIG_CANDIDATES", (str(infer),)
        )
        router = create_settings_router(_make_project(tmp_path))
        get_status = _endpoint(router, "/api/settings/voice-clone/status", "GET")
        resp = await get_status()
        assert resp["available"] is True
        assert resp["gpt_weights"].endswith("white_v1-e20.ckpt")
        assert resp["sovits_weights"].endswith("white_v1_e16.pth")
        assert resp["version"] == "v2"

    async def test_missing_config_reports_unavailable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """所有候选路径都不存在：available=false + 错误说明。"""
        monkeypatch.setattr(
            settings_api_module, "TTS_INFER_CONFIG_CANDIDATES",
            (str(tmp_path / "nonexistent.yaml"),),
        )
        router = create_settings_router(_make_project(tmp_path))
        get_status = _endpoint(router, "/api/settings/voice-clone/status", "GET")
        resp = await get_status()
        assert resp["available"] is False
        assert "error" in resp


# ====================================================================
# GET /api/settings/memory/search
# ====================================================================

class _FakeLongTermSearchStore:
    """模拟 LongTermMemoryStore：search 返回固定条目。"""

    def __init__(self, data_dir: str = "") -> None:
        pass

    def search(self, query: str, limit: int = 10) -> list:
        return [SimpleNamespace(
            id=7, content=f"命中:{query}", layer="fact",
            importance=8, is_highlight=True, created_at=1234.5,
        )][:limit]


class TestMemorySearch:
    """长期记忆检索端点。"""

    async def test_search_returns_entries(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """带关键词检索：返回条目字段完整。"""
        import white_salary.core.memory.long_term_store as lt_module
        monkeypatch.setattr(lt_module, "LongTermMemoryStore", _FakeLongTermSearchStore)
        router = create_settings_router(_make_project(tmp_path))
        search = _endpoint(router, "/api/settings/memory/search", "GET")
        resp = await search(q="游戏", limit=20)
        assert resp["count"] == 1
        assert resp["results"][0]["content"] == "命中:游戏"
        assert resp["results"][0]["layer"] == "fact"

    async def test_empty_query_returns_empty(self, tmp_path: Path) -> None:
        """空关键词：直接返回空结果，不触存储。"""
        router = create_settings_router(_make_project(tmp_path))
        search = _endpoint(router, "/api/settings/memory/search", "GET")
        resp = await search(q="   ")
        assert resp["results"] == []
        assert resp["count"] == 0


# ====================================================================
# 模块管理：/modules 与 /modules/toggle
# ====================================================================

def _make_module_files(root: Path) -> None:
    """在临时项目根下伪造两个记忆模块文件（含 MODULE 导出标记）。"""
    base = root / "src" / "white_salary" / "core" / "memory"
    (base / "enhanced").mkdir(parents=True, exist_ok=True)
    (base / "foo_mod.py").write_text(
        '"""\nwhite_salary/core/memory/foo_mod.py\n\n甲模块 — 测试用。\n"""\n'
        "MODULE = object\n",
        encoding="utf-8",
    )
    (base / "enhanced" / "bar_mod.py").write_text(
        '"""乙模块 — 测试用。"""\nMODULE = object\n', encoding="utf-8",
    )


class TestModulesToggle:
    """模块开关端点。"""

    async def test_disable_then_enable_updates_disabled_list(self, tmp_path: Path) -> None:
        """禁用写入 modules.disabled，启用移除；提示重启生效。"""
        root = _make_project(tmp_path)
        _make_module_files(root)
        # 预置含其它配置节的 memory_settings.json，确认不被抹掉
        cfg = root / "config" / "memory_settings.json"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(json.dumps({
            "forgetting": {"retention_days": 30},
            "modules": {"disabled": []},
        }), encoding="utf-8")

        router = create_settings_router(root)
        toggle = _endpoint(router, "/api/settings/modules/toggle", "POST")

        resp = await toggle({"stem": "foo_mod", "enabled": False})
        assert resp["status"] == "ok"
        assert "重启" in resp["message"]
        saved = json.loads(cfg.read_text(encoding="utf-8"))
        assert "foo_mod" in saved["modules"]["disabled"]
        assert saved["forgetting"]["retention_days"] == 30  # 其它节保留

        resp2 = await toggle({"stem": "foo_mod", "enabled": True})
        assert resp2["status"] == "ok"
        saved2 = json.loads(cfg.read_text(encoding="utf-8"))
        assert "foo_mod" not in saved2["modules"]["disabled"]

    async def test_toggle_unknown_stem_404(self, tmp_path: Path) -> None:
        """stem 不是真实模块文件：404，防止任意字符串写进配置。"""
        root = _make_project(tmp_path)
        _make_module_files(root)
        router = create_settings_router(root)
        toggle = _endpoint(router, "/api/settings/modules/toggle", "POST")
        with pytest.raises(HTTPException) as exc:
            await toggle({"stem": "not_a_module", "enabled": False})
        assert exc.value.status_code == 404

    async def test_toggle_missing_fields_400(self, tmp_path: Path) -> None:
        """缺 stem/enabled 字段：400。"""
        router = create_settings_router(_make_project(tmp_path))
        toggle = _endpoint(router, "/api/settings/modules/toggle", "POST")
        with pytest.raises(HTTPException) as exc:
            await toggle({"stem": "foo_mod"})
        assert exc.value.status_code == 400

    def test_module_description_skips_path_line(self) -> None:
        """docstring 首行是文件路径时跳过，取第一条说明文字。"""
        doc = "\nwhite_salary/core/memory/user_filter.py\n\n用户过滤器 — 黑白名单。\n"
        assert _module_description(doc) == "用户过滤器 — 黑白名单。"
        assert _module_description(None) == ""
        assert _module_description("直接说明") == "直接说明"


# ====================================================================
# POST /api/settings/qzone/test-post
# ====================================================================

class _FakeQZoneClient:
    """模拟 QZoneClient。"""

    def __init__(self, configured: bool = True, expired: bool = False,
                 post_result: Optional[dict] = None) -> None:
        self.is_configured = configured
        self.is_cookie_expired = expired
        self._post_result = post_result or {"success": True, "tid": "t123"}
        self.posted: list[str] = []

    async def post_emotion(self, content: str, pic_info: dict = None) -> dict:
        self.posted.append(content)
        return self._post_result


class TestQZoneTestPost:
    """发说说测试端点。"""

    async def test_post_success(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Cookie 有效：调 post_emotion 并返回 tid。"""
        import white_salary.adapters.platform.qzone_api as qz_module
        fake = _FakeQZoneClient()
        monkeypatch.setattr(qz_module, "get_client", lambda: fake)
        router = create_settings_router(_make_project(tmp_path))
        test_post = _endpoint(router, "/api/settings/qzone/test-post", "POST")
        resp = await test_post({"content": "测试说说～"})
        assert resp["success"] is True
        assert resp["tid"] == "t123"
        assert fake.posted == ["测试说说～"]

    async def test_unconfigured_cookie_clear_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """未配置 Cookie：明确报错，不调发布。"""
        import white_salary.adapters.platform.qzone_api as qz_module
        fake = _FakeQZoneClient(configured=False)
        monkeypatch.setattr(qz_module, "get_client", lambda: fake)
        router = create_settings_router(_make_project(tmp_path))
        test_post = _endpoint(router, "/api/settings/qzone/test-post", "POST")
        resp = await test_post({"content": "hi"})
        assert resp["success"] is False
        assert "Cookie" in resp["message"]
        assert fake.posted == []

    async def test_empty_content_rejected(self, tmp_path: Path) -> None:
        """空内容：直接拒绝。"""
        router = create_settings_router(_make_project(tmp_path))
        test_post = _endpoint(router, "/api/settings/qzone/test-post", "POST")
        resp = await test_post({"content": "  "})
        assert resp["success"] is False

    async def test_post_failure_surfaces_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """发布失败：错误原样透出并提示 Cookie 可能失效。"""
        import white_salary.adapters.platform.qzone_api as qz_module
        fake = _FakeQZoneClient(post_result={"success": False, "error": "登录态失效"})
        monkeypatch.setattr(qz_module, "get_client", lambda: fake)
        router = create_settings_router(_make_project(tmp_path))
        test_post = _endpoint(router, "/api/settings/qzone/test-post", "POST")
        resp = await test_post({"content": "hi"})
        assert resp["success"] is False
        assert "登录态失效" in resp["message"]


# ====================================================================
# GET /api/settings/about
# ====================================================================

class _FakeToolRegistry:
    """模拟 ToolRegistry：不导入任何真实工具。"""

    @property
    def count(self) -> int:
        return 12


class TestAbout:
    """关于页动态信息端点。"""

    async def test_about_returns_dynamic_stats(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """版本来自 pyproject、模块数来自轻量扫描、工具数来自注册中心（缓存）。"""
        import white_salary.adapters.tools.registry as registry_module
        monkeypatch.setattr(registry_module, "ToolRegistry", _FakeToolRegistry)

        root = _make_project(tmp_path)
        (root / "pyproject.toml").write_text(
            '[project]\nname = "white-salary"\nversion = "9.9.9"\n',
            encoding="utf-8",
        )
        _make_module_files(root)
        cfg = root / "config" / "memory_settings.json"
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.write_text(json.dumps({"modules": {"disabled": ["bar_mod"]}}), encoding="utf-8")

        router = create_settings_router(root)
        about = _endpoint(router, "/api/settings/about", "GET")
        resp = await about()
        assert resp["version"] == "9.9.9"
        assert resp["tool_count"] == 12
        assert resp["module_total"] == 2
        assert resp["module_disabled"] == 1
        assert resp["module_enabled"] == 1
        assert resp["python_version"].count(".") == 2


# ====================================================================
# 提示词备份 + 人格热更新
# ====================================================================

class _FakePersonality:
    """模拟带 reload() 的 PersonalityManager。"""

    def __init__(self) -> None:
        self.reload_count = 0

    def reload(self) -> None:
        self.reload_count += 1


class TestPromptBackupAndReload:
    """save_prompt / apply_template 备份与热更新。"""

    async def test_save_prompt_creates_backup_and_keeps_10(self, tmp_path: Path) -> None:
        """每次保存前备份旧文件到 prompts/backups/，最多保留10份。"""
        root = _make_project(tmp_path)
        prompt_file = root / "prompts" / "system_prompt.txt"
        prompt_file.parent.mkdir(parents=True, exist_ok=True)
        prompt_file.write_text("初版人设", encoding="utf-8")

        router = create_settings_router(root)
        save_prompt = _endpoint(router, "/api/settings/prompt", "POST")

        resp = await save_prompt({"prompt": "第2版人设"})
        assert resp["status"] == "ok"
        assert resp["backup"] is not None
        backup_file = Path(resp["backup"])
        assert backup_file.exists()
        assert backup_file.read_text(encoding="utf-8") == "初版人设"
        assert prompt_file.read_text(encoding="utf-8") == "第2版人设"
        # 未注册 personality：提示需重启
        assert resp["hot_reloaded"] is False
        assert "重启" in resp["message"]

        # 连续保存12次：备份目录只留最近10份
        for i in range(12):
            await save_prompt({"prompt": f"第{i + 3}版人设"})
        backups = list((root / "prompts" / "backups").glob("system_prompt_*.txt"))
        assert len(backups) == 10

    async def test_save_prompt_hot_reloads_registered_personality(
        self, tmp_path: Path
    ) -> None:
        """注册表有 personality（带 reload）：保存后热更新并注明立即生效。"""
        root = _make_project(tmp_path)
        fake_p = _FakePersonality()
        register_runtime_instance("personality", fake_p)
        router = create_settings_router(root)
        save_prompt = _endpoint(router, "/api/settings/prompt", "POST")
        resp = await save_prompt({"prompt": "新人设"})
        assert resp["hot_reloaded"] is True
        assert fake_p.reload_count == 1
        assert "热更新" in resp["message"]

    async def test_personality_without_reload_method_is_safe(self, tmp_path: Path) -> None:
        """注册的 personality 没有 reload 方法（hasattr 守卫）：不炸，提示重启。"""
        root = _make_project(tmp_path)
        register_runtime_instance("personality", object())
        router = create_settings_router(root)
        save_prompt = _endpoint(router, "/api/settings/prompt", "POST")
        resp = await save_prompt({"prompt": "新人设"})
        assert resp["hot_reloaded"] is False

    async def test_apply_template_backs_up_before_overwrite(self, tmp_path: Path) -> None:
        """应用模板前自动备份现有人设，返回体带备份路径。"""
        root = _make_project(tmp_path)
        prompt_file = root / "prompts" / "system_prompt.txt"
        prompt_file.parent.mkdir(parents=True, exist_ok=True)
        prompt_file.write_text("38KB精调人设", encoding="utf-8")
        tpl_dir = root / "prompts" / "templates"
        tpl_dir.mkdir(parents=True, exist_ok=True)
        (tpl_dir / "tsundere.txt").write_text("傲娇模板", encoding="utf-8")

        router = create_settings_router(root)
        apply_tpl = _endpoint(router, "/api/settings/prompt/apply_template", "POST")
        resp = await apply_tpl({"file": "tsundere.txt"})
        assert resp["status"] == "ok"
        assert prompt_file.read_text(encoding="utf-8") == "傲娇模板"
        backup_file = Path(resp["backup"])
        assert backup_file.exists()
        assert backup_file.read_text(encoding="utf-8") == "38KB精调人设"

    async def test_update_section_hot_reloads(self, tmp_path: Path) -> None:
        """分区保存成功后同样尝试热更新。"""
        root = _make_project(tmp_path)
        prompt_file = root / "prompts" / "system_prompt.txt"
        prompt_file.parent.mkdir(parents=True, exist_ok=True)
        prompt_file.write_text(
            "【基本资料】\n旧资料\n【外貌特征】\n银发\n", encoding="utf-8"
        )
        fake_p = _FakePersonality()
        register_runtime_instance("personality", fake_p)

        router = create_settings_router(root)
        update_section = _endpoint(
            router, "/api/settings/prompt/sections/{section_name}", "PUT"
        )
        resp = await update_section("basic_info", {"content": "【基本资料】\n新资料"})
        assert resp["status"] == "ok"
        assert resp["hot_reloaded"] is True
        assert fake_p.reload_count == 1
        assert "新资料" in prompt_file.read_text(encoding="utf-8")


# ====================================================================
# clear_qq_context 健壮性
# ====================================================================

class _ExplodingQQContextManager:
    """模拟清空即抛异常的QQ上下文管理器。"""

    def clear_all(self) -> None:
        raise RuntimeError("运行实例内部错误")


class TestClearQQContextRobust:
    """清运行实例抛异常时仍执行文件清空。"""

    async def test_runtime_clear_failure_still_clears_file(self, tmp_path: Path) -> None:
        """clear_all 抛异常：状态仍 ok，contexts.json 被清空。"""
        root = _make_project(tmp_path)
        ctx_file = root / "data" / "qq" / "contexts.json"
        ctx_file.parent.mkdir(parents=True, exist_ok=True)
        ctx_file.write_text('{"group_1": [{"sender": "甲"}]}', encoding="utf-8")

        router = create_settings_router(
            root,
            runtime={"qq_context_manager_getter": lambda: _ExplodingQQContextManager()},
        )
        clear_qq = _endpoint(router, "/api/settings/qq/clear-context", "POST")
        resp = await clear_qq()
        assert resp["status"] == "ok"
        assert ctx_file.read_text(encoding="utf-8") == "{}"
        assert "失败" in resp["message"]


# ====================================================================
# UserFilter.list_blacklist
# ====================================================================

class TestUserFilterListBlacklist:
    """黑名单明细公开方法。"""

    def test_list_blacklist_returns_active_entries(self, tmp_path: Path) -> None:
        """硬拉黑+未过期软拉黑返回，已过期软拉黑被过滤。"""
        import time as _time
        from white_salary.core.memory.user_filter import BlacklistEntry, UserFilter

        uf = UserFilter(data_dir=str(tmp_path))
        uf.add_to_blacklist("111", nickname="坏蛋", reason="骚扰", permanent=True)
        uf.add_to_blacklist("222", nickname="捣蛋", reason="刷屏", permanent=False)
        # 手工塞一条已过期的软拉黑
        uf._soft_blacklist["333"] = BlacklistEntry(
            user_id="333", nickname="过期用户",
            added_time=_time.time() - 7200, expires_at=_time.time() - 3600,
        )

        entries = uf.list_blacklist()
        ids = {e["user_id"] for e in entries}
        assert ids == {"111", "222"}
        types = {e["user_id"]: e["type"] for e in entries}
        assert types["111"] == "hard"
        assert types["222"] == "soft"
        # 字段完整可供前端渲染
        assert all("reason" in e and "nickname" in e for e in entries)


# ====================================================================
# developer.py 安全项
# ====================================================================

class TestDeveloperSecurity:
    """GitHub 同步公开视图 + 默认密码提示 + 名单接口鉴权。"""

    def test_public_sync_payload_strips_secrets(self) -> None:
        """同步载荷剔除 tokens 整节与 password_hash。"""
        from white_salary.core.plugins.developer import DeveloperManager

        data = {
            "developers": {
                "admin": {
                    "username": "admin", "password_hash": "abc123",
                    "role": "super_admin", "status": "approved",
                    "created_at": "2026-07-01 00:00", "plugins_submitted": ["p1"],
                },
            },
            "tokens": {"tok_secret": {"username": "admin", "expires_at": 9999999999}},
        }
        payload = DeveloperManager._build_public_sync_payload(data)
        assert "tokens" not in payload
        admin = payload["developers"]["admin"]
        assert "password_hash" not in admin
        assert admin["role"] == "super_admin"
        assert admin["status"] == "approved"
        assert admin["plugins_submitted"] == ["p1"]

    def test_login_with_default_password_prompts_change(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """命中默认密码：返回 must_change_password=true 与提示。"""
        from white_salary.core.plugins.developer import (
            DEFAULT_SUPER_ADMIN_PASSWORD,
            DeveloperManager,
        )
        # 隔离：不读真实 conf.yaml / 不碰 GitHub
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            DeveloperManager, "_load_github_config", staticmethod(lambda: {})
        )
        dm = DeveloperManager(config_dir=str(tmp_path / "config"))
        resp = dm.login("admin", DEFAULT_SUPER_ADMIN_PASSWORD)
        assert resp["success"] is True
        assert resp["must_change_password"] is True
        assert "默认密码" in resp["message"]

    def test_login_with_custom_password_no_flag(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """改过密码后登录：不带 must_change_password。"""
        from white_salary.core.plugins.developer import DeveloperManager
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            DeveloperManager, "_load_github_config", staticmethod(lambda: {})
        )
        dm = DeveloperManager(config_dir=str(tmp_path / "config"))
        dm._developers["admin"]["password_hash"] = dm._hash_password("我的新密码123")
        resp = dm.login("admin", "我的新密码123")
        assert resp["success"] is True
        assert "must_change_password" not in resp

    def test_login_missing_hash_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """账号缺失 password_hash（远端公开视图）：任何密码都不放行。"""
        from white_salary.core.plugins.developer import DeveloperManager
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            DeveloperManager, "_load_github_config", staticmethod(lambda: {})
        )
        dm = DeveloperManager(config_dir=str(tmp_path / "config"))
        dm._developers["ghost"] = {
            "username": "ghost", "role": "developer", "status": "approved",
        }
        resp = dm.login("ghost", "")
        assert resp["success"] is False

    async def test_developers_list_requires_token(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """GET /developers/list：无 token 401，有效 token 放行。"""
        import white_salary.core.plugins.developer as dev_module

        class _FakeDevManager:
            """模拟 DeveloperManager：只认 goodtoken。"""

            def __init__(self, *args: Any, **kwargs: Any) -> None:
                pass

            def verify_token(self, token: str) -> Optional[dict]:
                if token == "goodtoken":
                    return {"username": "admin", "role": "super_admin"}
                return None

            def list_developers(self) -> list[dict]:
                return [{"username": "admin", "role": "super_admin"}]

            @property
            def stats(self) -> dict:
                return {"total": 1}

        monkeypatch.setattr(dev_module, "DeveloperManager", _FakeDevManager)
        router = create_settings_router(_make_project(tmp_path))
        list_devs = _endpoint(router, "/api/settings/developers/list", "GET")

        with pytest.raises(HTTPException) as exc:
            await list_devs(token="")
        assert exc.value.status_code == 401

        resp = await list_devs(token="goodtoken")
        assert resp["developers"][0]["username"] == "admin"
