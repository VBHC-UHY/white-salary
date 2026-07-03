"""社交管理工具 — 屏蔽/黑名单/忙碌/静默/过滤。"""
from ._helpers import tool, P, S, I, NONE_PARAMS


def _get_filter():
    try:
        from white_salary.core.memory.user_filter import UserFilter
        return UserFilter()
    except Exception:
        return None


@tool("block_user", "屏蔽指定用户", P(user_id=S("用户ID", True), reason=S("原因")))
async def block_user(user_id: str = "", reason: str = "") -> str:
    f = _get_filter()
    if f:
        f.block(user_id, reason or "手动屏蔽")
        return "已屏蔽该用户"
    return "屏蔽功能不可用"

@tool("unblock_user", "解除用户屏蔽", P(user_id=S("用户ID", True)))
async def unblock_user(user_id: str = "") -> str:
    f = _get_filter()
    if f:
        f.unblock(user_id)
        return "已解除屏蔽"
    return "解除屏蔽功能不可用"

@tool("check_blocked_users", "查看当前屏蔽列表")
async def check_blocked_users() -> str:
    f = _get_filter()
    if f:
        blocked = f.get_blocked_list() if hasattr(f, 'get_blocked_list') else []
        if blocked:
            return "屏蔽列表: " + ", ".join(str(u) for u in blocked)
        return "当前没有屏蔽任何人"
    return "屏蔽功能不可用"

# 2026-07-03 工具实现（批9）：以下7个旧「假成功」工具（set_busy_mode/clear_busy_mode/
# global_silent/switch_filter_mode/check_filter_mode/filter_toggle/silent_toggle）
# 已由新的三件套 set_quiet_mode/clear_quiet_mode/get_quiet_status 取代
# （接入 PresenceState 真实状态：QQ回复决策/桌面主动搭话真的会闭嘴）。
# 函数体按批2约定保留，不入 TOOLS。
@tool("set_busy_mode", "开启忙碌模式", P(duration=I("持续分钟"), reason=S("原因")))
async def set_busy_mode(duration: int = 30, reason: str = "") -> str:
    return f"忙碌模式已开启{duration}分钟"

@tool("clear_busy_mode", "关闭忙碌模式")
async def clear_busy_mode() -> str:
    return "忙碌模式已关闭"

@tool("global_silent", "全局静默开关", P(enabled=S("true/false", True)))
async def global_silent(enabled: str = "true") -> str:
    on = enabled.lower() in ("true", "1", "on")
    return f"全局静默已{'开启' if on else '关闭'}"

@tool("manage_blacklist", "管理黑名单（添加/移除/查看）",
      P(action=S("add/remove/list", True), user_id=S("用户ID")))
async def manage_blacklist(action: str = "list", user_id: str = "") -> str:
    f = _get_filter()
    if f:
        if action == "add" and user_id:
            f.block(user_id, "黑名单")
            return "已加入黑名单"
        elif action == "remove" and user_id:
            f.unblock(user_id)
            return "已移出黑名单"
    return await check_blocked_users()

@tool("switch_filter_mode", "切换内容过滤模式", P(mode=S("normal/strict/off", True)))
async def switch_filter_mode(mode: str = "normal") -> str:
    return f"过滤模式已切换为{mode}"

@tool("check_filter_mode", "查看当前过滤模式")
async def check_filter_mode() -> str:
    return "当前过滤模式: normal"

@tool("filter_toggle", "开关内容过滤", P(enabled=S("true/false", True)))
async def filter_toggle(enabled: str = "true") -> str:
    on = enabled.lower() in ('true', '1', 'on')
    return f"内容过滤已{'开启' if on else '关闭'}"

@tool("silent_toggle", "开关静默模式", P(enabled=S("true/false", True), duration=I("持续分钟")))
async def silent_toggle(enabled: str = "true", duration: int = 30) -> str:
    on = enabled.lower() in ("true", "1", "on")
    return f"静默模式已{'开启' if on else '关闭'}" + (f"，{duration}分钟后自动关闭" if on else "")


# ================================================================
# 2026-07-03 工具实现（批9）：忙碌/静默模式三件套（真实现）
# 旧7个假成功工具合并重写为3个，接入 PresenceState：
#   - QQ端：qq_handler 处理消息前查状态（群聊闲聊闭嘴/被@限频简短告知/主人急事仍回）
#   - 桌面端：websocket_handler 的 auto_chat 与桥主动播报静默期间跳过
# ================================================================

def _get_presence():
    """取进程级在场状态单例（QQ线程/桌面主循环/工具层共用同一份）。"""
    from white_salary.core.services.presence_state import PresenceState
    return PresenceState.get_instance()


@tool("set_quiet_mode",
      "进入忙碌/静默模式：期间白不主动搭话、不回QQ群聊闲聊、桌面主动播报暂停"
      "（主人私聊说「紧急/在吗」仍会回）。用户说「别吵我」「我要工作了」"
      "「安静一会」「闭嘴一会」时使用。busy=忙碌（到点自动恢复，默认60分钟），"
      "silent=静默（不给时长就一直静到用户说可以说话了）",
      P(mode=S("busy（忙碌）或 silent（静默）", True),
        duration_minutes=I("持续分钟数；busy不填默认60，silent不填=直到手动解除")))
async def set_quiet_mode(mode: str = "busy", duration_minutes: int = 0) -> str:
    try:
        return _get_presence().set_quiet(mode.strip().lower(), duration_minutes)
    except Exception as e:
        return f"设置安静模式时出了点问题: {e}"


@tool("clear_quiet_mode",
      "解除忙碌/静默模式，恢复正常聊天和主动搭话。"
      "用户说「我忙完了」「可以说话了」「解除静默」时使用")
async def clear_quiet_mode() -> str:
    try:
        return _get_presence().clear()
    except Exception as e:
        return f"解除安静模式时出了点问题: {e}"


@tool("get_quiet_status",
      "查看当前是否处于忙碌/静默模式及剩余时间。"
      "用户问「你现在静音吗」「忙碌模式还有多久」时使用")
async def get_quiet_status() -> str:
    try:
        return _get_presence().describe()
    except Exception as e:
        return f"查安静模式状态时出了点问题: {e}"


# 2026-07-02 审计修复（批2）：下架7个「假成功」工具——set_busy_mode/clear_busy_mode/
# global_silent/switch_filter_mode/check_filter_mode/filter_toggle/silent_toggle
# 直接返回「已开启/已切换」成功文案，但不写任何状态、不接任何调度，消息处理行为完全不变，
# 比「暂不可用」空壳更糟：它们在向用户撒谎（依据 docs/audit-2026-07-02/tools-media.json）。
# 函数体保留，待接入真实状态存储并让消息管线读取后再加回 TOOLS。
# 2026-07-03 工具实现（批9）：7个旧名【不】加回——合并重写为上面的
# set_quiet_mode/clear_quiet_mode/get_quiet_status 三件套（不显著增加工具总数）。
TOOLS = [fn._tool_def for fn in [
    block_user, unblock_user, check_blocked_users, manage_blacklist,
    set_quiet_mode, clear_quiet_mode, get_quiet_status,
]]
