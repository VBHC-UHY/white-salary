"""
测试设置面板的依赖注入（settings_api.py 的 runtime 容器 + 模块级注册表）。

2026-07-03 审计修复（批5）新增：
- create_settings_router 接受 runtime 容器（desktop_agent / qq_context_manager_getter /
  user_learning / memory_manager，全部可为 None）
- POST /api/settings/chat/reset：注入时真清运行中 Agent 的短期记忆并落盘；
  未注入时回退为真正清文件，并在响应里说明
- POST /api/settings/qq/clear-context：注入/注册表可解析时真清运行中的
  QQ 上下文管理器；未注入时仅清文件并说明
- POST /api/settings/user-learning/trigger：注入时调用 learn(主人统一user_id)
  返回真实 success；未注入时维持原文案
- POST /api/settings/memory/consolidate：优先用注入的运行中 MemoryManager
  （与对话主流程共用同一实例），未注入时才新建一次性实例
- POST /api/settings/upload-temp：multipart 分支可用（python-multipart 已装）

沿用 test_settings_api.py 的做法：直接从 APIRouter 取端点函数调用，
不依赖 starlette TestClient（0.35.1 与 httpx 0.28 不兼容）；
multipart 用例改用 httpx.ASGITransport 直连 ASGI 应用。
"""

import json
from pathlib import Path
from typing import Any, Callable, Optional

import pytest
import yaml

import white_salary.infrastructure.server.settings_api as settings_api_module
from white_salary.infrastructure.server.settings_api import (
    create_settings_router,
    get_runtime_instance,
    register_runtime_instance,
)


def _endpoint(router: Any, path: str, method: str) -> Callable:
    """从 APIRouter 中按路径+方法取出端点函数（避免依赖 TestClient/httpx）。"""
    for route in router.routes:
        if route.path == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"路由未找到: {method} {path}")


def _make_project(tmp_path: Path, family_qq: Optional[list] = None) -> Path:
    """构造一个最小化的临时项目根目录（含 conf.default.yaml 和 conf.yaml）。"""
    (tmp_path / "conf.default.yaml").write_text(
        yaml.dump({"system": {"name": "White Salary"}}, allow_unicode=True),
        encoding="utf-8",
    )
    conf: dict[str, Any] = {"llm": {"provider": "siliconflow"}}
    if family_qq is not None:
        conf["qq"] = {"enabled": True, "family_qq": family_qq}
    (tmp_path / "conf.yaml").write_text(
        yaml.dump(conf, allow_unicode=True), encoding="utf-8"
    )
    return tmp_path


@pytest.fixture(autouse=True)
def _isolated_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """每个用例用独立的模块级注册表，防止注册的 fake 实例泄漏到其他测试。"""
    monkeypatch.setattr(settings_api_module, "_runtime_registry", {})


# ====================================================================
# 测试替身（fake）
# ====================================================================

class _FakeShortTermMemory:
    """模拟 ShortTermMemory：clear() 只清内存、_save_to_file() 落盘（与真实实现一致）。"""

    def __init__(self, persist_path: Path) -> None:
        self._messages: list[str] = ["用户: 你好", "白: 你好呀"]
        self._persist_path = persist_path

    def clear(self) -> None:
        """只清内存，不落盘（复刻 short_term.py 的行为）。"""
        self._messages.clear()

    def _save_to_file(self) -> None:
        """把当前消息列表写入持久化文件。"""
        self._persist_path.parent.mkdir(parents=True, exist_ok=True)
        self._persist_path.write_text(
            json.dumps(list(self._messages), ensure_ascii=False), encoding="utf-8"
        )


class _FakeAgent:
    """模拟 ChatAgent：持有 _memory，reset_conversation() 清记忆。"""

    def __init__(self, memory: _FakeShortTermMemory) -> None:
        self._memory = memory
        self.reset_called: bool = False

    def reset_conversation(self) -> None:
        """复刻 ChatAgent.reset_conversation：调 memory.clear()。"""
        self.reset_called = True
        self._memory.clear()


class _FakeQQContextManager:
    """模拟带公开 clear_all() 的 QQ 上下文管理器（run_server 注册的子类形态）。"""

    def __init__(self) -> None:
        self._contexts: dict[str, list] = {"group_1": [{"sender": "甲", "text": "hi"}]}
        self.saved: bool = False

    def clear_all(self) -> None:
        """清空全部上下文并"落盘"。"""
        self._contexts.clear()
        self.saved = True


class _FakeQQContextManagerPrivate:
    """模拟没有 clear_all() 的原始 QQContextManager（走私有属性兜底路径）。"""

    def __init__(self) -> None:
        self._contexts: dict[str, list] = {"group_2": [{"sender": "乙", "text": "yo"}]}
        self.save_count: int = 0

    def _save(self) -> None:
        """记录落盘调用次数。"""
        self.save_count += 1


class _FakeUserLearning:
    """模拟 UserLearningService.learn(user_id) -> Optional[dict]。"""

    def __init__(self, result: Optional[dict]) -> None:
        self._result = result
        self.called_with: Optional[str] = None

    async def learn(self, user_id: str) -> Optional[dict]:
        """记录调用参数并返回预设结果。"""
        self.called_with = user_id
        return self._result


class _FakeLongTermStore:
    """
    模拟长期记忆存储：提供 MemoryConsolidationService 用到的三个方法
    （cleanup_expired / search / delete），并记录 delete 的调用以便断言。

    2026-07-03 面板升级（批6）：真实 LongTermMemoryStore 的删除方法叫
    delete(entry_id)，此前整理服务笔误调 store.remove(...) 导致去重从未真正
    删除过（本批已修）——测试替身同步改为 delete，与真实接口对齐。
    """

    def __init__(self) -> None:
        self.removed_ids: list[int] = []

    def cleanup_expired(self) -> int:
        """假装清理了 2 条过期记忆。"""
        return 2

    def search(self, query: str, limit: int = 1000) -> list[dict]:
        """返回固定的三条记忆，其中 id=1 与 id=2 内容重复（id=2 应被去重删除）。"""
        return [
            {"id": 1, "content": "白喜欢打游戏"},
            {"id": 2, "content": "白喜欢打游戏"},
            {"id": 3, "content": "主人的生日在夏天"},
        ]

    def delete(self, mem_id: int) -> bool:
        """记录被删除的记忆 id（与真实 delete 一样返回是否删除成功）。"""
        self.removed_ids.append(mem_id)
        return True


class _FakeMemoryManager:
    """
    模拟运行中的 MemoryManager：只带整理服务实际访问的 _long_term/_core 属性。
    _core 置 None 以跳过核心记忆去重分支（该分支与本测试无关）。
    """

    def __init__(self, long_term: _FakeLongTermStore) -> None:
        self._long_term = long_term
        self._core = None


class _FakeIntegrator:
    """模拟 enhanced 整合器：run_maintenance 返回固定结果、不落盘。"""

    def run_maintenance(self) -> dict:
        """返回可识别的维护结果。"""
        return {"maintained": True}


# ====================================================================
# POST /api/settings/chat/reset
# ====================================================================

class TestChatResetDI:
    """清空桌面端对话：注入时真清运行实例；未注入时回退真清文件。"""

    async def test_reset_clears_running_agent_and_persists(self, tmp_path: Path) -> None:
        """注入 desktop_agent 后：内存被清 + 落盘被调用 + 响应说明运行实例已清。"""
        root = _make_project(tmp_path)
        persist = root / "data" / "chat_history" / "current.json"
        fake_mem = _FakeShortTermMemory(persist)
        fake_agent = _FakeAgent(fake_mem)

        router = create_settings_router(root, runtime={"desktop_agent": fake_agent})
        reset_chat = _endpoint(router, "/api/settings/chat/reset", "POST")

        resp = await reset_chat()

        assert resp["status"] == "ok"
        assert fake_agent.reset_called is True
        assert fake_mem._messages == []          # 运行实例内存真被清空
        assert json.loads(persist.read_text(encoding="utf-8")) == []  # 且已落盘
        assert "运行中对话记忆" in resp["message"]

    async def test_reset_without_runtime_falls_back_and_clears_file(
        self, tmp_path: Path
    ) -> None:
        """未注入 runtime：回退路径必须真正清空历史文件，并在响应里说明仅清文件。"""
        root = _make_project(tmp_path)
        persist = root / "data" / "chat_history" / "current.json"
        persist.parent.mkdir(parents=True, exist_ok=True)
        persist.write_text(
            json.dumps([{"role": "user", "content": "hi", "name": None}]),
            encoding="utf-8",
        )

        router = create_settings_router(root)  # 不传 runtime
        reset_chat = _endpoint(router, "/api/settings/chat/reset", "POST")

        resp = await reset_chat()

        assert resp["status"] == "ok"
        # 原实现是完全 no-op（clear() 不落盘）；现在文件必须真被清空
        assert json.loads(persist.read_text(encoding="utf-8")) == []
        assert "未注入运行实例" in resp["message"]


# ====================================================================
# POST /api/settings/qq/clear-context
# ====================================================================

class TestQQClearContextDI:
    """清空QQ上下文：getter/注册表可解析时清运行实例；否则仅清文件并说明。"""

    async def test_clear_via_getter_uses_clear_all(self, tmp_path: Path) -> None:
        """runtime 里的 getter 返回带 clear_all 的实例：内存清空 + 文件清空。"""
        root = _make_project(tmp_path)
        ctx_file = root / "data" / "qq" / "contexts.json"
        ctx_file.parent.mkdir(parents=True, exist_ok=True)
        ctx_file.write_text('{"group_1": [{"sender": "甲", "text": "hi"}]}', encoding="utf-8")

        fake_mgr = _FakeQQContextManager()
        router = create_settings_router(
            root, runtime={"qq_context_manager_getter": lambda: fake_mgr}
        )
        clear_qq = _endpoint(router, "/api/settings/qq/clear-context", "POST")

        resp = await clear_qq()

        assert resp["status"] == "ok"
        assert fake_mgr._contexts == {}          # 运行实例内存真被清空
        assert fake_mgr.saved is True            # 且通过 clear_all 落盘
        assert ctx_file.read_text(encoding="utf-8") == "{}"
        assert "运行实例" in resp["message"]

    async def test_clear_via_registry_private_fallback(self, tmp_path: Path) -> None:
        """无 runtime 但注册表里有实例（无 clear_all）：走私有属性兜底路径清空。"""
        root = _make_project(tmp_path)
        fake_mgr = _FakeQQContextManagerPrivate()
        register_runtime_instance("qq_context_manager", fake_mgr)
        assert get_runtime_instance("qq_context_manager") is fake_mgr

        router = create_settings_router(root)  # 不传 runtime，靠注册表解析
        clear_qq = _endpoint(router, "/api/settings/qq/clear-context", "POST")

        resp = await clear_qq()

        assert resp["status"] == "ok"
        assert fake_mgr._contexts == {}
        assert fake_mgr.save_count == 1          # 兜底路径调用了 _save()
        assert "运行实例" in resp["message"]

    async def test_clear_without_runtime_only_clears_file(self, tmp_path: Path) -> None:
        """既无 runtime 又无注册表实例：仅清文件，响应说明未注入。"""
        root = _make_project(tmp_path)
        ctx_file = root / "data" / "qq" / "contexts.json"
        ctx_file.parent.mkdir(parents=True, exist_ok=True)
        ctx_file.write_text('{"g": []}', encoding="utf-8")

        router = create_settings_router(root)
        clear_qq = _endpoint(router, "/api/settings/qq/clear-context", "POST")

        resp = await clear_qq()

        assert resp["status"] == "ok"
        assert ctx_file.read_text(encoding="utf-8") == "{}"
        assert "未注入运行实例" in resp["message"]

    async def test_getter_raising_still_clears_file(self, tmp_path: Path) -> None:
        """getter 抛异常时不炸端点：回退为仅清文件。"""
        root = _make_project(tmp_path)
        ctx_file = root / "data" / "qq" / "contexts.json"
        ctx_file.parent.mkdir(parents=True, exist_ok=True)
        ctx_file.write_text('{"g": []}', encoding="utf-8")

        def _bad_getter() -> Any:
            raise RuntimeError("QQ服务还没起来")

        router = create_settings_router(
            root, runtime={"qq_context_manager_getter": _bad_getter}
        )
        clear_qq = _endpoint(router, "/api/settings/qq/clear-context", "POST")

        resp = await clear_qq()

        assert resp["status"] == "ok"
        assert ctx_file.read_text(encoding="utf-8") == "{}"
        assert "未注入运行实例" in resp["message"]


# ====================================================================
# POST /api/settings/user-learning/trigger
# ====================================================================

class TestUserLearningTriggerDI:
    """手动触发画像学习：注入时调 learn(主人user_id) 返回真实结果。"""

    async def test_trigger_calls_learn_with_master_user_id(self, tmp_path: Path) -> None:
        """注入实例后：learn 被调用且 user_id 是 conf.yaml qq.family_qq[0]。"""
        root = _make_project(tmp_path, family_qq=[12345, 67890])
        fake_ul = _FakeUserLearning(result={"user_name": "小白", "interests": ["游戏"]})

        router = create_settings_router(root, runtime={"user_learning": fake_ul})
        trigger = _endpoint(router, "/api/settings/user-learning/trigger", "POST")

        resp = await trigger()

        assert fake_ul.called_with == "12345"    # 主人统一 user_id = family_qq[0]
        assert resp["success"] is True
        assert resp["profile"]["user_name"] == "小白"

    async def test_trigger_learn_returns_none_reports_failure(self, tmp_path: Path) -> None:
        """learn() 返回 None（消息不足/LLM未配置）：success 必须为 False。"""
        root = _make_project(tmp_path, family_qq=[12345])
        fake_ul = _FakeUserLearning(result=None)

        router = create_settings_router(root, runtime={"user_learning": fake_ul})
        trigger = _endpoint(router, "/api/settings/user-learning/trigger", "POST")

        resp = await trigger()

        assert fake_ul.called_with == "12345"
        assert resp["success"] is False
        assert "未产出画像" in resp["message"]

    async def test_trigger_without_family_qq_falls_back_to_desktop(
        self, tmp_path: Path
    ) -> None:
        """conf.yaml 无 family_qq：user_id 回退旧值 desktop（与 websocket_handler 口径一致）。"""
        root = _make_project(tmp_path)  # 不写 qq 节
        fake_ul = _FakeUserLearning(result={"user_name": "小白"})

        router = create_settings_router(root, runtime={"user_learning": fake_ul})
        trigger = _endpoint(router, "/api/settings/user-learning/trigger", "POST")

        resp = await trigger()

        assert fake_ul.called_with == "desktop"
        assert resp["success"] is True

    async def test_trigger_without_runtime_keeps_original_message(
        self, tmp_path: Path
    ) -> None:
        """未注入实例：维持原有文案（行为不变）。"""
        root = _make_project(tmp_path, family_qq=[12345])
        router = create_settings_router(root)
        trigger = _endpoint(router, "/api/settings/user-learning/trigger", "POST")

        resp = await trigger()

        assert resp["success"] is False
        assert resp["message"] == "用户学习会在对话积累足够后自动触发，无需手动操作"


# ====================================================================
# POST /api/settings/memory/consolidate
# ====================================================================

class TestMemoryConsolidateDI:
    """手动触发记忆整理：优先用注入的运行中 MemoryManager，而不是新建一次性实例。"""

    @staticmethod
    def _patch_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
        """
        隔离两处副作用（端点在函数体内按名 import，patch 模块属性即可生效）：
        1. 把 MemoryManager 换成"一实例化就报错"的哨兵类——若端点无视注入实例
           走了新建路径，AssertionError 会被端点 except 捕获、返回体带 error，
           下面的断言随即失败，从而证明"优先用注入实例"；
        2. enhanced 整合器是全局单例且默认写真实 data/memory，换成无副作用的
           假实现，防止单元测试污染项目数据目录。
        """
        import white_salary.core.memory.enhanced.integrator as integrator_module
        import white_salary.core.memory.manager as manager_module

        class _ForbiddenMemoryManager:
            """哨兵：新建即失败，证明端点没有绕开注入实例自行新建。"""

            def __init__(self, *args: Any, **kwargs: Any) -> None:
                raise AssertionError("不应新建 MemoryManager：必须优先使用注入实例")

        monkeypatch.setattr(manager_module, "MemoryManager", _ForbiddenMemoryManager)
        monkeypatch.setattr(
            integrator_module, "get_integrator", lambda **kwargs: _FakeIntegrator()
        )

    async def test_consolidate_prefers_injected_memory_manager(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """注入 memory_manager 后：整理作用于注入实例，且绝不新建 MemoryManager。"""
        root = _make_project(tmp_path)
        long_term = _FakeLongTermStore()
        fake_mm = _FakeMemoryManager(long_term)
        self._patch_side_effects(monkeypatch)

        router = create_settings_router(root, runtime={"memory_manager": fake_mm})
        consolidate = _endpoint(router, "/api/settings/memory/consolidate", "POST")

        result = await consolidate()

        assert "error" not in result, f"端点报错（可能走了新建路径）: {result}"
        assert result["expired_removed"] == 2        # 来自注入实例的 cleanup_expired
        assert result["duplicates_removed"] == 1     # id=2 与 id=1 内容重复
        assert long_term.removed_ids == [2]          # 去重删除真的作用在注入实例上
        assert result.get("enhanced_maintenance") == {"maintained": True}

    async def test_consolidate_resolves_memory_manager_from_registry(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """不传 runtime、但注册表里有 memory_manager：同样优先用注册实例。"""
        root = _make_project(tmp_path)
        long_term = _FakeLongTermStore()
        register_runtime_instance("memory_manager", _FakeMemoryManager(long_term))
        self._patch_side_effects(monkeypatch)

        router = create_settings_router(root)  # 不传 runtime，靠模块级注册表解析
        consolidate = _endpoint(router, "/api/settings/memory/consolidate", "POST")

        result = await consolidate()

        assert "error" not in result, f"端点报错（可能走了新建路径）: {result}"
        assert result["expired_removed"] == 2
        assert result["duplicates_removed"] == 1
        assert long_term.removed_ids == [2]


# ====================================================================
# POST /api/settings/upload-temp（multipart 分支）
# ====================================================================

class TestUploadTempMultipart:
    """multipart 上传分支：python-multipart 已装，starlette 解析应可用。"""

    async def test_multipart_upload_saves_file(self, tmp_path: Path) -> None:
        """multipart 表单上传：解析成功、文件按内容原样落盘到 data/temp。"""
        httpx = pytest.importorskip("httpx")
        from fastapi import FastAPI

        root = _make_project(tmp_path)
        app = FastAPI()
        app.include_router(create_settings_router(root))

        payload: bytes = b"\x89PNG-fake-image-bytes"
        # starlette 0.35.1 的 TestClient 与 httpx 0.28 不兼容（app 参数被移除），
        # 用 httpx.ASGITransport 直连 ASGI 应用
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            resp = await client.post(
                "/api/settings/upload-temp",
                files={"file": ("pic.png", payload, "image/png")},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True, f"multipart 解析失败: {data}"
        saved = Path(data["path"])
        assert saved.exists()
        assert saved.read_bytes() == payload
        assert saved.parent == root / "data" / "temp"


# ====================================================================
# 注册表本身
# ====================================================================

class TestRuntimeRegistry:
    """模块级运行实例注册表的注册/读取。"""

    def test_register_and_get(self) -> None:
        """注册后能按名取回；未注册的名字返回 None。"""
        sentinel = object()
        register_runtime_instance("some_instance", sentinel)
        assert get_runtime_instance("some_instance") is sentinel
        assert get_runtime_instance("missing") is None
