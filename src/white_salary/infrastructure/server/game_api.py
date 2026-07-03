"""
white_salary/infrastructure/server/game_api.py

2026-07-03 功能大项（批11）：游戏对接接口 —— 让 Aurora Forge（体素游戏）
在关键事件（打Boss/升级/结契新伙伴/建营地等）时上报，白在桌面上实时评论/庆祝。

链路（已存在，本模块只做最前一段）：
    游戏 → POST /api/game/event → event_to_hint 翻译成一句给白的中文提示
         → CrossPlatformBridge().push_to_desktop(提示, source="game")
         → websocket_handler 每2秒轮询 pop_desktop_messages 让白开口播报

设计约定：
  - fire-and-forget：接口立刻返回 {ok:true}，绝不因白不在线/异常而让游戏卡住或报错。
    桥是内存队列（deque(maxlen=50)），桌面没连就自然堆积/丢弃，符合
    「游戏不该被白影响」的原则。
  - event_to_hint 是纯函数（模块级映射 + 纯函数），便于单测且与桥解耦。
  - source="game"：⚠️ 当前 websocket_handler 的桥消息分流（_partition_bridge_messages）
    只让 source=="reminder" 穿透「忙碌/静默」模式，其余（含 game）在静默期会被丢弃。
    该文件不在本智能体名下，改不了，故此约定记录在对接说明里，待后续在 handler 放行
    source=="game"。
"""

from typing import Any

from fastapi import APIRouter
from loguru import logger
from pydantic import BaseModel, Field


class GameEvent(BaseModel):
    """
    游戏上报的单个事件。

    字段:
        game: 游戏名（如 "Aurora Forge"），用于提示语里点名。
        event: 事件类型标识（见 _EVENT_TEMPLATES 的键，如 "boss_win"）。
               未知类型走通用兜底，不报错。
        detail: 事件细节字典（如 {"boss": "熔岩魔像", "level": 12}），
                各事件类型按需从中取字段；缺字段用友好占位，不抛异常。
        player: 玩家名（谁触发的），空则在提示里用"玩家"。
    """

    game: str
    event: str
    detail: dict[str, Any] = Field(default_factory=dict)
    player: str = ""


# 2026-07-03 功能大项（批11）：事件类型 → 提示语模板（模块级 dict，便于单测与扩展）。
#
# 模板用 str.format 填充，可用变量：{player}（玩家名，空则已被替换为"玩家"）、
# {game}（游戏名）、以及 detail 里的任意键（缺失时由 _SafeDict 兜底成友好占位，
# 不会 KeyError）。语气要求：口语、简短、有情绪，像朋友在旁边看你玩，别像系统日志。
_EVENT_TEMPLATES: dict[str, str] = {
    # 打赢 Boss —— 夸夸他/一起高兴
    "boss_win": "[游戏] {player}刚在{game}里打赢了Boss「{boss}」！快夸夸他、一起高兴一下～",
    # 打输 Boss / 团灭 —— 安慰、别泄气
    "boss_lose": "[游戏] {player}在{game}里被Boss「{boss}」打败了，安慰安慰他、鼓励他再来一把。",
    # 升级 —— 恭喜
    "level_up": "[游戏] {player}在{game}升到了{level}级！恭喜他一下，替他开心。",
    # 结契新伙伴 —— 替他高兴、问问是谁
    "new_partner": "[游戏] {player}在{game}里结契了新伙伴「{partner}」！替他高兴，好奇地聊聊这个新伙伴。",
    # 建营地 —— 夸他能干
    "build_camp": "[游戏] {player}在{game}建好了营地「{camp}」！夸夸他能干，问问营地怎么样。",
    # 通关章节 —— 庆祝
    "chapter_done": "[游戏] {player}通关了{game}的章节「{chapter}」！好好庆祝一下，为他骄傲。",
    # 进入战斗 —— 加油打气
    "enter_battle": "[游戏] {player}在{game}进入了一场战斗，给他加油打气一句。",
    # 采集/挖矿到稀有物 —— 惊喜
    "rare_drop": "[游戏] {player}在{game}挖到了稀有物「{item}」！替他惊喜一下。",
    # 死亡/掉线 —— 心疼
    "player_die": "[游戏] {player}在{game}里死掉了……心疼他一下，安慰几句。",
}

# 未知事件类型的通用兜底模板（拿到 detail 里的 desc 就用，没有就泛泛播报）。
_FALLBACK_TEMPLATE = "[游戏] {player}在{game}触发了游戏事件「{event}」，自然地回应一句、陪他一起。"
_FALLBACK_WITH_DESC = "[游戏] {player}在{game}：{desc}。自然地回应一句、陪他一起。"


class _SafeDict(dict):
    """
    str.format_map 用的容错字典：模板里引用了 detail 缺失的键时，
    不抛 KeyError，而是回填一个友好占位（"那个东西"），保证提示语始终能生成。
    """

    def __missing__(self, key: str) -> str:
        """缺失键的占位（避免 boss/partner 等字段没传时报错）。"""
        return "那个东西"


def event_to_hint(event: str, detail: dict[str, Any], player: str, game: str) -> str:
    """
    2026-07-03 功能大项（批11）：把结构化游戏事件翻译成一句给白的自然中文提示（纯函数）。

    参数:
        event: 事件类型（_EVENT_TEMPLATES 的键；未知则走兜底）。
        detail: 事件细节字典（模板按需取字段，缺字段容错）。
        player: 玩家名（空字符串时用"玩家"）。
        game: 游戏名（空时用"游戏"）。

    返回:
        一句口语化、带情绪的中文提示（白会据此在桌面开口播报）。

    说明:
        纯函数、无副作用，便于单测。所有取值都容错（缺字段/空值不抛异常）。
    """
    safe_player = player.strip() if player and player.strip() else "玩家"
    safe_game = game.strip() if game and game.strip() else "游戏"

    # format 变量池：先放 player/game/event，再铺开 detail（detail 同名键可覆盖，
    # 但 player/game 是我们规范化后的值，放最后确保不被 detail 里的同名脏字段顶掉）
    values = _SafeDict(detail if isinstance(detail, dict) else {})
    values["event"] = event
    values["player"] = safe_player
    values["game"] = safe_game

    template = _EVENT_TEMPLATES.get(event)
    if template is not None:
        return template.format_map(values)

    # 未知事件：detail 里带了 desc 就用它拼一句更具体的，否则泛泛兜底
    desc = ""
    if isinstance(detail, dict):
        raw_desc = detail.get("desc") or detail.get("description") or ""
        desc = str(raw_desc).strip()
    if desc:
        values["desc"] = desc
        return _FALLBACK_WITH_DESC.format_map(values)
    return _FALLBACK_TEMPLATE.format_map(values)


def create_game_router() -> APIRouter:
    """
    2026-07-03 功能大项（批11）：创建游戏对接 API 路由。

    端点:
        POST /api/game/event  接收游戏事件 → 翻译 → 推送到桌面桥（fire-and-forget）
        GET  /api/game/ping   游戏侧探测白在不在（返回 desktop_online）

    返回:
        FastAPI APIRouter（在 create_app 里 include_router 挂载）。
    """
    router = APIRouter(prefix="/api/game", tags=["game"])

    @router.post("/event")
    async def report_event(evt: GameEvent) -> dict:
        """
        接收游戏事件并转成一句提示推给白（fire-and-forget，永远返回 {ok:true}）。

        无论白是否在线、桥是否异常，都立刻返回成功——游戏侧不该被白的状态阻塞。
        任何异常都被吞进日志（这是本项目少数「按需求必须静默」的点：需求 §1 明确
        要求「白不在线也不报错」「失败要静默」，故此处刻意 catch-all 并留日志）。
        """
        try:
            hint = event_to_hint(evt.event, evt.detail, evt.player, evt.game)
            # 延迟导入，避免测试/装配期强依赖 core（且与 settings_api 的延迟导入风格一致）
            from white_salary.core.cross_platform import CrossPlatformBridge

            from_user = evt.player.strip() if evt.player and evt.player.strip() else "玩家"
            CrossPlatformBridge().push_to_desktop(
                hint,
                from_user=from_user,
                source="game",
            )
            logger.debug(f"[GameAPI] 事件已入桥: {evt.game}/{evt.event}")
        except Exception as e:
            # 需求约定：失败要静默、游戏不该被白影响；但至少留日志便于排查
            logger.warning(f"[GameAPI] 处理游戏事件异常（已静默返回成功）: {e}")
        return {"ok": True}

    @router.get("/ping")
    async def ping() -> dict:
        """
        供游戏侧探测白在不在（是否值得上报事件）。

        desktop_online：桌面端是否可能收到播报。桥是内存队列、并不持有「消费者是否
        存在」的确切信息，无法可靠判断桌面 WebSocket 是否连着，故这里保守返回 True
        （游戏照常上报即可；白不在线时桥自然堆积/丢弃，不会出错）。
        后续若 CrossPlatformBridge 暴露活跃消费者计数，可在此改成真实在线判断。
        """
        return {"ok": True, "desktop_online": True}

    return router
