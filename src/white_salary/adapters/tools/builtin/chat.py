"""聊天管理工具 — 历史/撤回/发消息/未读/群历史/清理/学习。"""
from ._helpers import tool, P, S, I, NONE_PARAMS


@tool("view_chat_history", "查看聊天历史记录",
      P(count=I("条数"), platform=S("平台:all/qq/desktop")))
async def view_chat_history(count: int = 10, platform: str = "all") -> str:
    try:
        from white_salary.core.memory.conversation_log import ConversationLog
        log = ConversationLog.get_instance()
        entries = log.search(platform=platform if platform != "all" else "", limit=count)
        return log.format_results(entries)
    except Exception as e:
        return f"获取失败: {e}"

@tool("group_history", "获取QQ群聊历史", P(group_id=S("群号"), count=I("条数")))
async def group_history(group_id: str = "", count: int = 10) -> str:
    try:
        from white_salary.core.memory.conversation_log import ConversationLog
        log = ConversationLog.get_instance()
        entries = log.search(platform="qq", limit=count)
        return log.format_results(entries)
    except Exception as e:
        return f"获取失败: {e}"

@tool("recall_message", "撤回最近发送的消息", P(target=S("目标会话")))
async def recall_message(target: str = "") -> str:
    return "撤回功能暂时不可用"

@tool("reply_to_user", "发消息给某人/私聊某人——通过QQ给指定用户发私聊消息。当用户说「给XX发消息」「私聊XX」时调用",
      P(user_id=S("用户QQ号", True), content=S("消息内容", True)))
async def reply_to_user(user_id: str = "", content: str = "") -> str:
    """通过QQ给指定用户发私聊消息。"""
    if not user_id or not content:
        return "需要提供用户QQ号和消息内容"
    user_id = user_id.strip()
    if not user_id.isdigit():
        return f"QQ号必须是纯数字，不能是'{user_id}'"
    try:
        from white_salary.adapters.tools.builtin.qq_api import _call
        await _call("send_private_msg", {
            "user_id": int(user_id),
            "message": content,
        })
        return "消息已发送"
    except Exception as e:
        return "消息发送失败了"

@tool("message_send", "发QQ消息（私聊或群聊），target必须是纯数字QQ号",
      P(target=S("目标QQ号或群号(纯数字)", True), content=S("消息内容", True),
        is_group=S("是否群消息(true/false)")))
async def message_send(target: str = "", content: str = "", is_group: str = "false") -> str:
    """发送QQ消息。target必须是纯数字QQ号，不能是用户名。"""
    if not target or not content:
        return "需要提供目标QQ号(纯数字)和消息内容"
    # 验证target是纯数字QQ号
    target = target.strip()
    if not target.isdigit():
        return f"target必须是纯数字QQ号，不能是'{target}'这样的用户名"
    try:
        from white_salary.adapters.tools.builtin.qq_api import _call
        if is_group == "true" or is_group is True:
            await _call("send_group_msg", {
                "group_id": int(target),
                "message": content,
            })
            return "消息已发送"
        else:
            await _call("send_private_msg", {
                "user_id": int(target),
                "message": content,
            })
            return "消息已发送"
    except Exception as e:
        return "消息发送失败了"

@tool("push_to_desktop", "在桌面端说话/桌面端发消息——把消息推送到电脑桌面端。当用户说「桌面端发消息」「电脑上说」时调用",
      P(message=S("要显示的消息内容", True)))
async def push_to_desktop(message: str = "") -> str:
    """把消息推送到桌面端（电脑上的白会说出来）。"""
    if not message:
        return "需要提供消息内容"
    try:
        from white_salary.core.cross_platform import CrossPlatformBridge
        bridge = CrossPlatformBridge()
        bridge.push_to_desktop(message, source="qq")
        return "已推送到桌面端"
    except Exception as e:
        return "推送失败了"

# 2026-07-03 工具实现（批9）：check_unread/dm_cleanup 两个空壳已被 qq_inbox 取代
# （函数体保留备查，不再导出）。
@tool("check_unread", "查看QQ未读消息")
async def check_unread() -> str:
    return "暂时无法查看未读消息"

@tool("dm_cleanup", "清理与某用户的聊天记录", P(user_id=S("用户ID", True)))
async def dm_cleanup(user_id: str = "") -> str:
    return "清理功能暂不可用"


# ================================================================
# 2026-07-03 工具实现（批9）：qq_inbox——QQ收件箱（合并 check_unread/dm_cleanup）
# ================================================================

def _infer_unreplied(entries: list) -> list[dict]:
    """
    2026-07-03 工具实现（批9）：从对话日志推断「可能没回的人」。

    OneBot/NapCat 没有真正的「未读消息」接口（get_recent_contact 只有最近
    联系人，不带已读状态），所以用本地 ConversationLog 推断：
    取近期 QQ 私聊记录，按用户分组取最后一条——如果最后一条的 ai_reply
    为空（对方发了消息、白没有回），就认为「可能没回」。

    Args:
        entries: ConversationEntry 列表（按时间倒序）

    Returns:
        [{"user_name", "user_id", "last_msg", "time_str"}]，按时间倒序
    """
    seen_users: set = set()
    unreplied: list[dict] = []
    for e in entries:
        # 只看私聊（群聊没有「必须回」的语义）
        if getattr(e, "group_id", ""):
            continue
        uid = getattr(e, "user_id", "")
        if not uid or uid in seen_users:
            continue
        seen_users.add(uid)  # entries 按时间倒序，第一条就是该用户最后一条
        if not (getattr(e, "ai_reply", "") or "").strip():
            unreplied.append({
                "user_name": getattr(e, "user_name", "") or uid,
                "user_id": uid,
                "last_msg": (getattr(e, "user_msg", "") or "")[:80],
                "time_str": getattr(e, "time_str", ""),
            })
    return unreplied


@tool("qq_inbox", "查看QQ消息动态——action=unread列出「可能还没回消息的人」（基于本地聊天记录推断），action=recent列最近联系人。当用户问「有没有人找我」「有没有没回的消息」「最近谁给我发消息了」时调用",
      P(action=S("unread=可能没回的人（默认）/recent=最近联系人")))
async def qq_inbox(action: str = "unread") -> str:
    action = (action or "unread").strip().lower()

    if action == "recent":
        # NapCat 有 get_recent_contact（qq_api.qq_recent_contact 同款 action），
        # QQ在线时直接问 NapCat；失败/未连接时退回本地日志推断并如实说明
        try:
            from white_salary.adapters.tools.builtin.qq_api import _call
            result = await _call("get_recent_contact", {"count": 10})
            if result and "操作失败" not in result:
                return f"QQ最近联系人（NapCat返回）：\n{result}"
        except Exception:
            pass
        # 兜底：本地对话日志统计活跃用户（如实说明是本地记录，不是QQ实时数据）
        try:
            from white_salary.core.memory.conversation_log import ConversationLog
            users = ConversationLog.get_instance().get_active_users(days=7, limit=10)
            if not users:
                return "QQ未连接，本地聊天记录里最近7天也没有联系人"
            lines = ["QQ未连接，以下是本地聊天记录里最近7天联系过的人（不是QQ实时数据）："]
            for u in users:
                lines.append(f"- {u['user_name'] or u['user_id']}（{u['count']}条消息）")
            return "\n".join(lines)
        except Exception as e:
            return f"获取最近联系人失败: {e}"

    # action == "unread"（默认）：推断「可能没回的人」
    try:
        from white_salary.core.memory.conversation_log import ConversationLog
        log = ConversationLog.get_instance()
        entries = log.search(platform="qq", limit=200, days=3)
        unreplied = _infer_unreplied(entries)
        if not unreplied:
            return "最近3天的QQ私聊都回过了，没有发现可能没回的人（基于本地聊天记录推断）"
        lines = [
            "以下是「可能还没回」的人（QQ没有真正的未读接口，"
            "这是根据本地聊天记录推断的，仅供参考）："
        ]
        for u in unreplied[:10]:
            lines.append(f"- [{u['time_str']}] {u['user_name']}: {u['last_msg']}")
        return "\n".join(lines)
    except Exception as e:
        return f"检查失败: {e}"


# ================================================================
# 2026-07-03 工具实现（批9）：view_learned_style——查看学到的说话风格/网络用语
# （合并 view_learned_phrases/view_learned_slang 两个空壳，旧名不再导出）
# ================================================================

def _project_root_path():
    """返回项目根目录绝对路径（从模块位置推导，不依赖 CWD）。"""
    from pathlib import Path
    return Path(__file__).resolve().parents[5]


def _learning_disabled_modules() -> set:
    """读 config/memory_settings.json 的 modules.disabled（与 manager.py 消费口径一致）。"""
    import json
    try:
        cfg = _project_root_path() / "config" / "memory_settings.json"
        data = json.loads(cfg.read_text(encoding="utf-8"))
        return {str(n) for n in ((data.get("modules") or {}).get("disabled") or [])}
    except Exception:
        return set()


def _format_learned_slang() -> str:
    """读 slang_learner 的持久化文件 data/memory/learned_slang.json 并格式化。"""
    import json
    if "slang_learner" in _learning_disabled_modules():
        return "网络用语学习模块已关闭，可在控制面板-模块管理开启"
    path = _project_root_path() / "data" / "memory" / "learned_slang.json"
    if not path.exists():
        return "还没学到网络用语（学习是后台自动进行的，聊得多了就会积累）"
    try:
        learned: dict = json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        return f"网络用语数据读取失败: {e}"
    if not learned:
        return "还没学到网络用语（学习是后台自动进行的，聊得多了就会积累）"
    # 按见过次数排序，最多展示20个
    items = sorted(learned.items(), key=lambda kv: kv[1].get("seen_count", 0), reverse=True)
    lines = [f"已学到 {len(learned)} 个网络用语（按出现频率排序，最多显示20个）："]
    for word, info in items[:20]:
        meaning = info.get("meaning", "")
        lines.append(f"- {word}: {meaning}")
    return "\n".join(lines)


def _format_learned_phrases() -> str:
    """读 expression_learner 的风格画像 data/memory/expression_styles/*.json 并格式化。"""
    import json
    if "expression_learner" in _learning_disabled_modules():
        return "说话风格学习模块已关闭，可在控制面板-模块管理开启"
    styles_dir = _project_root_path() / "data" / "memory" / "expression_styles"
    if not styles_dir.exists():
        return "还没学到谁的说话风格（学习是后台自动进行的，对方消息多了才会分析）"
    styles: list[dict] = []
    for f in sorted(styles_dir.glob("*.json")):
        try:
            styles.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            continue  # 单个坏文件不影响其它画像
    if not styles:
        return "还没学到谁的说话风格（学习是后台自动进行的，对方消息多了才会分析）"
    lines = [f"已学到 {len(styles)} 个人的说话风格："]
    for s in styles[:10]:
        name = s.get("user_name") or s.get("user_id") or "未知"
        parts = []
        if s.get("tone"):
            parts.append(f"语气{ s['tone'] }")
        vocab = s.get("vocabulary") or []
        if vocab:
            parts.append("常用词: " + "、".join(str(v) for v in vocab[:5]))
        habits = s.get("habits") or []
        if habits:
            parts.append("习惯: " + "、".join(str(h) for h in habits[:3]))
        lines.append(f"- {name}: " + ("；".join(parts) if parts else "（画像为空）"))
    return "\n".join(lines)


@tool("view_learned_style", "查看白学到的说话风格和网络用语——kind=phrases看学到的各用户说话风格画像，kind=slang看学到的网络用语，kind=all都看。当用户问「你学到了什么」「你学会了哪些梗」「你了解我的说话风格吗」时调用",
      P(kind=S("phrases=说话风格/slang=网络用语/all=全部（默认all）")))
async def view_learned_style(kind: str = "all") -> str:
    kind = (kind or "all").strip().lower()
    if kind == "phrases":
        return _format_learned_phrases()
    if kind == "slang":
        return _format_learned_slang()
    return "【说话风格】\n" + _format_learned_phrases() + "\n\n【网络用语】\n" + _format_learned_slang()


# 2026-07-03 工具实现（批9）：view_learned_phrases/view_learned_slang 两个空壳
# 已被 view_learned_style 取代（函数体保留备查，不再导出）。
@tool("view_learned_phrases", "查看AI学到的表情和用语")
async def view_learned_phrases() -> str:
    return "这个功能还在开发中"

@tool("view_learned_slang", "查看AI学到的网络用语")
async def view_learned_slang() -> str:
    return "这个功能还在开发中"

@tool("ntfy_push", "发送手机推送通知（ntfy.sh）",
      P(message=S("通知内容", True), topic=S("推送主题")))
async def ntfy_push(message: str = "", topic: str = "white-salary") -> str:
    if not message:
        return "请提供通知内容"
    try:
        import aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.post(f"https://ntfy.sh/{topic}", data=message.encode()) as resp:
                return f"推送{'成功' if resp.status == 200 else '失败'}: {message[:30]}"
    except Exception as e:
        return f"推送失败: {e}"


# 2026-07-02 审计修复（批2）：下架5个空壳工具——
# recall_message（「撤回功能暂时不可用」空壳；真实撤回是 qq_api.py 的 qq_recall_last，
# 空壳与真实现并存会让模型选中空壳导致撤回假失败）、check_unread/dm_cleanup/
# view_learned_phrases/view_learned_slang（均为固定文案「暂不可用/开发中」）。
# 2026-07-03 工具实现（批9）：check_unread/dm_cleanup 合并实现为 qq_inbox，
# view_learned_phrases/view_learned_slang 合并实现为 view_learned_style；
# 旧4个名字保持移除（函数体保留备查），recall_message 继续由 qq_recall_last 承担。
TOOLS = [fn._tool_def for fn in [
    view_chat_history, group_history, reply_to_user,
    message_send, push_to_desktop, ntfy_push,
    qq_inbox, view_learned_style,
]]
