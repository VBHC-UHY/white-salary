"""
测试游戏对接接口（game_api.py）。

2026-07-03 功能大项（批11）新增：
- event_to_hint 各类型映射：boss_win / level_up / new_partner / build_camp /
  chapter_done / enter_battle 等按模板填充，含玩家名/游戏名/detail 字段。
- 未知 event 走通用兜底（有 desc 用 desc，无 desc 泛泛播报），不抛异常。
- 缺字段容错：detail 里没有模板引用的键时用友好占位，不 KeyError。
- POST /api/game/event 调了 CrossPlatformBridge().push_to_desktop（monkeypatch
  验证参数：提示文本非空、source="game"、from_user 为玩家名）。
- fire-and-forget：push 抛异常时端点仍返回 {ok:true}（不把异常透给游戏）。
- GET /api/game/ping 返回 {ok:true, desktop_online:...}。

沿用 test_settings_api 风格：直接从 APIRouter 取端点函数调用，不依赖 TestClient/httpx。
"""

from typing import Any, Callable

import pytest

from white_salary.infrastructure.server.game_api import (
    GameEvent,
    create_game_router,
    event_to_hint,
)


def _endpoint(router: Any, path: str, method: str) -> Callable:
    """从 APIRouter 中按路径+方法取出端点函数（避免依赖 TestClient/httpx）。"""
    for route in router.routes:
        if route.path == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"路由未找到: {method} {path}")


# ---------------------------------------------------------------------------
# event_to_hint 纯函数：各类型映射
# ---------------------------------------------------------------------------

def test_boss_win_hint_includes_player_game_and_boss():
    """boss_win：提示含玩家名、游戏名、Boss名，且带情绪（"夸/高兴"）。"""
    hint = event_to_hint("boss_win", {"boss": "熔岩魔像"}, "阿白", "Aurora Forge")
    assert "阿白" in hint
    assert "Aurora Forge" in hint
    assert "熔岩魔像" in hint
    assert "游戏" in hint  # 带 [游戏] 前缀标识
    # 口语/情绪：夸夸或高兴其一在场即可
    assert ("夸" in hint) or ("高兴" in hint)


def test_level_up_hint_includes_level():
    """level_up：提示含等级。"""
    hint = event_to_hint("level_up", {"level": 12}, "阿白", "Aurora Forge")
    assert "12" in hint
    assert "恭喜" in hint or "开心" in hint


def test_new_partner_hint_includes_partner_name():
    """new_partner：提示含新伙伴名。"""
    hint = event_to_hint("new_partner", {"partner": "小火龙"}, "阿白", "Aurora Forge")
    assert "小火龙" in hint


def test_build_camp_hint_includes_camp_name():
    """build_camp：提示含营地名。"""
    hint = event_to_hint("build_camp", {"camp": "北岭营地"}, "阿白", "Aurora Forge")
    assert "北岭营地" in hint


def test_chapter_done_hint_includes_chapter():
    """chapter_done：提示含章节名。"""
    hint = event_to_hint("chapter_done", {"chapter": "第一章"}, "阿白", "Aurora Forge")
    assert "第一章" in hint


def test_enter_battle_hint_is_generated():
    """enter_battle：提示能生成且含游戏名（无需 detail 字段）。"""
    hint = event_to_hint("enter_battle", {}, "阿白", "Aurora Forge")
    assert "阿白" in hint
    assert "Aurora Forge" in hint
    assert hint  # 非空


# ---------------------------------------------------------------------------
# event_to_hint 兜底与容错
# ---------------------------------------------------------------------------

def test_unknown_event_falls_back_without_error():
    """未知 event 走通用兜底，不抛异常，提示里带上原事件名。"""
    hint = event_to_hint("some_weird_event", {}, "阿白", "Aurora Forge")
    assert hint
    assert "some_weird_event" in hint
    assert "阿白" in hint


def test_unknown_event_uses_desc_when_present():
    """未知 event 且 detail 带 desc 时，兜底提示用上 desc 内容。"""
    hint = event_to_hint(
        "custom_thing", {"desc": "捡到了一颗星星"}, "阿白", "Aurora Forge"
    )
    assert "捡到了一颗星星" in hint


def test_missing_detail_field_does_not_raise():
    """已知 event 但 detail 缺模板字段（如 boss_win 没传 boss）时不 KeyError。"""
    hint = event_to_hint("boss_win", {}, "阿白", "Aurora Forge")
    assert hint  # 用占位生成，不抛异常
    assert "阿白" in hint


def test_empty_player_and_game_use_defaults():
    """player/game 为空时用默认「玩家」「游戏」，不出现空白。"""
    hint = event_to_hint("boss_win", {"boss": "史莱姆"}, "", "")
    assert "玩家" in hint
    # game 为空回退成"游戏"；提示里 [游戏] 前缀本就含"游戏"，此处确认不崩即可
    assert "史莱姆" in hint


def test_none_detail_is_tolerated():
    """detail 传成非 dict（防御性）时不崩，走兜底不抛异常。"""
    # event_to_hint 内部对非 dict 的 detail 做容错
    hint = event_to_hint("boss_win", None, "阿白", "Aurora Forge")  # type: ignore[arg-type]
    assert hint


# ---------------------------------------------------------------------------
# POST /api/game/event：调用桥 + fire-and-forget
# ---------------------------------------------------------------------------

class _FakeBridge:
    """假的跨平台桥：记录 push_to_desktop 的调用参数，供断言。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def push_to_desktop(self, message: str, from_user: str = "", source: str = "qq") -> None:
        self.calls.append(
            {"message": message, "from_user": from_user, "source": source}
        )


@pytest.mark.asyncio
async def test_post_event_pushes_to_bridge(monkeypatch):
    """POST /event：把翻译后的提示以 source='game' 推给桥，返回 {ok:True}。"""
    fake = _FakeBridge()
    # game_api 里用的是延迟导入 `from white_salary.core.cross_platform import CrossPlatformBridge`，
    # 因此 patch 源模块的类，让 CrossPlatformBridge() 返回我们的假桥。
    monkeypatch.setattr(
        "white_salary.core.cross_platform.CrossPlatformBridge",
        lambda: fake,
    )

    router = create_game_router()
    handler = _endpoint(router, "/api/game/event", "POST")

    evt = GameEvent(
        game="Aurora Forge", event="boss_win", detail={"boss": "熔岩魔像"}, player="阿白"
    )
    result = await handler(evt)

    assert result == {"ok": True}
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["source"] == "game"
    assert call["from_user"] == "阿白"
    assert "熔岩魔像" in call["message"]
    assert call["message"]  # 提示非空


@pytest.mark.asyncio
async def test_post_event_empty_player_uses_default_from_user(monkeypatch):
    """POST /event：player 为空时 from_user 回退为「玩家」。"""
    fake = _FakeBridge()
    monkeypatch.setattr(
        "white_salary.core.cross_platform.CrossPlatformBridge",
        lambda: fake,
    )

    router = create_game_router()
    handler = _endpoint(router, "/api/game/event", "POST")

    evt = GameEvent(game="Aurora Forge", event="level_up", detail={"level": 5}, player="")
    result = await handler(evt)

    assert result == {"ok": True}
    assert fake.calls[0]["from_user"] == "玩家"


@pytest.mark.asyncio
async def test_post_event_is_fire_and_forget_on_bridge_error(monkeypatch):
    """POST /event：桥 push 抛异常时仍返回 {ok:True}（不把异常透给游戏）。"""

    def _boom():
        raise RuntimeError("bridge down")

    # 让 CrossPlatformBridge() 本身抛异常，模拟桥不可用
    monkeypatch.setattr(
        "white_salary.core.cross_platform.CrossPlatformBridge",
        _boom,
    )

    router = create_game_router()
    handler = _endpoint(router, "/api/game/event", "POST")

    evt = GameEvent(game="Aurora Forge", event="boss_win", detail={}, player="阿白")
    result = await handler(evt)

    assert result == {"ok": True}  # 静默吞异常，游戏侧收到成功


@pytest.mark.asyncio
async def test_post_event_unknown_event_still_pushes(monkeypatch):
    """POST /event：未知 event 也能推送（走兜底提示），返回 {ok:True}。"""
    fake = _FakeBridge()
    monkeypatch.setattr(
        "white_salary.core.cross_platform.CrossPlatformBridge",
        lambda: fake,
    )

    router = create_game_router()
    handler = _endpoint(router, "/api/game/event", "POST")

    evt = GameEvent(game="Aurora Forge", event="mystery", detail={}, player="阿白")
    result = await handler(evt)

    assert result == {"ok": True}
    assert len(fake.calls) == 1
    assert "mystery" in fake.calls[0]["message"]


# ---------------------------------------------------------------------------
# GET /api/game/ping
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ping_returns_ok_and_desktop_online():
    """GET /ping：返回 {ok:True, desktop_online:...}。"""
    router = create_game_router()
    handler = _endpoint(router, "/api/game/ping", "GET")
    result = await handler()
    assert result["ok"] is True
    assert "desktop_online" in result


# ---------------------------------------------------------------------------
# GameEvent 默认值
# ---------------------------------------------------------------------------

def test_game_event_defaults():
    """GameEvent：detail 默认空 dict、player 默认空串。"""
    evt = GameEvent(game="Aurora Forge", event="boss_win")
    assert evt.detail == {}
    assert evt.player == ""
