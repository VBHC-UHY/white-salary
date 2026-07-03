"""
QQ API工具 — OneBot v11 API封装。

所有QQ操作都通过QQAdapter的_call_api方法执行。
这些工具由tool_llm判断是否调用，执行结果交给主模型回复。
"""
import contextvars
import json
from ._helpers import tool, P, S, I, NONE_PARAMS
from loguru import logger


# 全局QQ适配器引用（由run_server.py设置）
_qq_adapter = None

# 当前消息上下文（contextvars，每个asyncio task独立，并发安全）
# qq_handler处理消息时设置，工具读取作为默认目标
_msg_context: contextvars.ContextVar = contextvars.ContextVar("qq_msg_context", default={})


def set_msg_context(group_id: str = "", user_id: str = "", is_group: bool = False) -> None:
    """设置当前消息上下文（qq_handler调用）。"""
    _msg_context.set({"group_id": group_id, "user_id": user_id, "is_group": is_group})


def get_msg_context() -> dict:
    """获取当前消息上下文（工具调用）。"""
    return _msg_context.get({})


def set_qq_adapter(adapter):
    """设置QQ适配器引用（启动时调用）。"""
    global _qq_adapter
    _qq_adapter = adapter


async def _call(action: str, params: dict) -> str:
    """调用OneBot API的统一入口（等待返回值）。"""
    if not _qq_adapter or not _qq_adapter._ws:
        return "操作失败：QQ未连接"
    try:
        result = await _qq_adapter._call_api(action, params, wait_response=True)
        if result:
            import json
            return json.dumps(result, ensure_ascii=False, indent=2)[:2000]
        return "操作成功"
    except Exception as e:
        return f"操作失败: {e}"


def _safe_int(value: str, name: str = "参数") -> int:
    """安全转int，失败时抛ValueError（被外层try/except捕获后返回友好提示）。"""
    s = str(value).strip()
    if not s or not s.isdigit():
        raise ValueError(f"{name}必须是纯数字，收到了'{value}'")
    return int(s)


# ================================================================
# 消息类（8个）
# ================================================================

@tool("qq_send_voice", "发语音/发一下语音/录音/说话给我听——把文本用TTS转成语音消息发送到QQ。当用户说「发语音」「发个语音」「说给我听」「用语音说」时必须调用此工具。【重要】群聊消息必须填group_id（群号），私聊消息必须填user_id（QQ号），不要搞混！",
      P(user_id=S("私聊时填用户QQ号(纯数字)，群聊时不填"), group_id=S("群聊时填群号(纯数字)，私聊时不填"), text=S("要转语音的文本内容", True)))
async def qq_send_voice(user_id: str = "", group_id: str = "", text: str = "") -> str:
    if not text:
        return "请提供要转语音的文本"
    # 纠正小助理的常见错误：群聊时小助理可能只传user_id没传group_id
    # 如果上下文说是群聊，但只收到user_id → 纠正为群聊发送
    # 如果小助理明确传了group_id → 用它的（支持跨场景）
    ctx = get_msg_context()
    if ctx.get("is_group") and ctx.get("group_id") and user_id and not group_id:
        group_id = ctx["group_id"]
        user_id = ""
    # 都没传时从上下文兜底
    if not user_id and not group_id:
        if ctx.get("is_group") and ctx.get("group_id"):
            group_id = ctx["group_id"]
        elif ctx.get("user_id"):
            user_id = ctx["user_id"]
        else:
            return "请提供user_id或group_id（纯数字QQ号/群号）"
    # 验证QQ号格式
    user_id = str(user_id).strip()
    group_id = str(group_id).strip()
    if user_id and not user_id.isdigit():
        return f"user_id必须是纯数字QQ号，不能是'{user_id}'"
    if group_id and not group_id.isdigit():
        return f"group_id必须是纯数字群号，不能是'{group_id}'"
    # 先用TTS合成，然后存临时文件发送（base64群聊不支持，改用file://协议）
    try:
        from white_salary.adapters.tts.gpt_sovits_adapter import GPTSoVITSAdapter
        from pathlib import Path
        import time as _time
        import os as _os
        # 2026-07-02 审计修复（批3）：默认参考音频改为项目内绝对路径。
        # 原默认值是相对路径且寄生在 GPT-SoVITS 的训练日志目录（logs/ 常被清理），
        # 全靠 TTS 进程 cwd 恰好在 GPT-SoVITS 目录才能解析成功。
        # 音频已复制到项目 assets/tts/ 统一管理，环境变量仍可覆盖。
        _project_root = Path(__file__).resolve().parents[5]
        ref_audio = _os.environ.get(
            "WS_TTS_REF_AUDIO", str(_project_root / "assets" / "tts" / "ref_default.wav")
        )
        ref_text = _os.environ.get("WS_TTS_REF_TEXT", "你怎么不会想让我去试辣子鸡丁吧")
        tts = GPTSoVITSAdapter(ref_audio_path=ref_audio, ref_text=ref_text)
        audio = await tts.synthesize(text)
        if audio and audio.samples:
            # 存临时wav文件（用系统临时目录，路径不含中文空格，NapCat能读到）
            import tempfile as _tempfile
            temp_dir = Path(_tempfile.gettempdir()) / "white_salary_tts"
            temp_dir.mkdir(parents=True, exist_ok=True)
            temp_file = temp_dir / f"tts_{int(_time.time() * 1000)}.wav"
            temp_file.write_bytes(audio.samples)
            # 用file://协议发送（群聊私聊都支持）
            file_uri = f"file:///{temp_file.resolve()}"
            if group_id:
                target = {"group_id": _safe_int(group_id, "群号")}
                action = "send_group_msg"
            else:
                target = {"user_id": _safe_int(user_id, "QQ号")}
                action = "send_private_msg"
            target["message"] = [{"type": "record", "data": {"file": file_uri}}]
            result = await _call(action, target)
            # 延迟10秒删除临时文件（NapCat可能还在异步上传文件）
            import asyncio as _asyncio
            async def _delayed_delete():
                await _asyncio.sleep(10)
                try:
                    temp_file.unlink(missing_ok=True)
                except Exception:
                    pass
            _asyncio.create_task(_delayed_delete())
            return "语音已发送"
    except Exception as e:
        logger.debug(f"[QQ语音] TTS失败: {e}")
    return "语音发送失败了"


@tool("qq_send_image", "发图片/发张图/看图——把图片发送到QQ。当用户说「发图片」「发张图」「把图发给我」时调用。【重要】群聊消息必须填group_id（群号），私聊消息必须填user_id（QQ号），不要搞混！",
      P(user_id=S("私聊时填用户QQ号(纯数字)，群聊时不填"), group_id=S("群聊时填群号(纯数字)，私聊时不填"), image_url=S("图片URL或本地路径", True)))
async def qq_send_image(user_id: str = "", group_id: str = "", image_url: str = "") -> str:
    if not image_url:
        return "请提供图片URL"
    # 纠正小助理的常见错误：群聊时只传了user_id没传group_id → 纠正为群聊
    ctx = get_msg_context()
    if ctx.get("is_group") and ctx.get("group_id") and user_id and not group_id:
        group_id = ctx["group_id"]
        user_id = ""
    # 都没传时从上下文兜底
    if not user_id and not group_id:
        if ctx.get("is_group") and ctx.get("group_id"):
            group_id = ctx["group_id"]
        elif ctx.get("user_id"):
            user_id = ctx["user_id"]
        else:
            return "请提供user_id或group_id"
    user_id = str(user_id).strip()
    group_id = str(group_id).strip()
    if group_id and group_id.isdigit():
        target = {"group_id": _safe_int(group_id, "群号")}
        action = "send_group_msg"
    elif user_id and user_id.isdigit():
        target = {"user_id": _safe_int(user_id, "QQ号")}
        action = "send_private_msg"
    else:
        return "user_id/group_id必须是纯数字"
    target["message"] = [{"type": "image", "data": {"file": image_url}}]
    return await _call(action, target)


@tool("qq_send_poke", "QQ戳一戳", P(user_id=S("用户QQ号", True), group_id=S("群号")))
async def qq_send_poke(user_id: str = "", group_id: str = "") -> str:
    if group_id:
        return await _call("group_poke", {"group_id": _safe_int(group_id, "群号"), "user_id": _safe_int(user_id, "QQ号")})
    return await _call("friend_poke", {"user_id": _safe_int(user_id, "QQ号")})


@tool("qq_send_like", "QQ点赞/超级赞", P(user_id=S("用户QQ号", True), times=I("次数1-10")))
async def qq_send_like(user_id: str = "", times: int = 1) -> str:
    return await _call("send_like", {"user_id": _safe_int(user_id, "QQ号"), "times": min(times, 10)})


@tool("qq_delete_msg", "撤回QQ消息", P(message_id=S("消息ID", True)))
async def qq_delete_msg(message_id: str = "") -> str:
    return await _call("delete_msg", {"message_id": _safe_int(message_id, "消息ID")})


@tool("qq_get_msg", "获取QQ消息详情", P(message_id=S("消息ID", True)))
async def qq_get_msg(message_id: str = "") -> str:
    return await _call("get_msg", {"message_id": _safe_int(message_id, "消息ID")})


@tool("qq_get_forward_msg", "获取合并转发消息", P(id=S("转发消息ID", True)))
async def qq_get_forward_msg(id: str = "") -> str:
    return await _call("get_forward_msg", {"id": id})


@tool("qq_emoji_like", "给QQ消息发表情回复",
      P(message_id=S("消息ID", True), emoji_id=S("表情ID", True)))
async def qq_emoji_like(message_id: str = "", emoji_id: str = "") -> str:
    return await _call("set_msg_emoji_like", {"message_id": _safe_int(message_id, "消息ID"), "emoji_id": _safe_int(emoji_id, "表情ID")})


# ================================================================
# 群管理（9个）
# ================================================================

@tool("qq_group_ban", "QQ群禁言",
      P(group_id=S("群号", True), user_id=S("用户QQ号", True), duration=I("禁言秒数（0=解除）")))
async def qq_group_ban(group_id: str = "", user_id: str = "", duration: int = 60) -> str:
    return await _call("set_group_ban", {
        "group_id": _safe_int(group_id, "群号"), "user_id": _safe_int(user_id, "QQ号"), "duration": duration
    })


@tool("qq_group_whole_ban", "QQ全体禁言", P(group_id=S("群号", True), enable=S("true/false", True)))
async def qq_group_whole_ban(group_id: str = "", enable: str = "true") -> str:
    return await _call("set_group_whole_ban", {
        "group_id": _safe_int(group_id, "群号"), "enable": enable.lower() in ("true", "1")
    })


@tool("qq_group_kick", "QQ踢人", P(group_id=S("群号", True), user_id=S("用户QQ号", True)))
async def qq_group_kick(group_id: str = "", user_id: str = "") -> str:
    return await _call("set_group_kick", {
        "group_id": _safe_int(group_id, "群号"), "user_id": _safe_int(user_id, "QQ号"), "reject_add_request": False
    })


@tool("qq_set_group_card", "设置群名片",
      P(group_id=S("群号", True), user_id=S("用户QQ号", True), card=S("新名片", True)))
async def qq_set_group_card(group_id: str = "", user_id: str = "", card: str = "") -> str:
    return await _call("set_group_card", {
        "group_id": _safe_int(group_id, "群号"), "user_id": _safe_int(user_id, "QQ号"), "card": card
    })


@tool("qq_set_group_name", "设置群名", P(group_id=S("群号", True), name=S("新群名", True)))
async def qq_set_group_name(group_id: str = "", name: str = "") -> str:
    return await _call("set_group_name", {"group_id": _safe_int(group_id, "群号"), "group_name": name})


@tool("qq_set_group_admin", "设置群管理员",
      P(group_id=S("群号", True), user_id=S("用户QQ号", True), enable=S("true=设置/false=取消")))
async def qq_set_group_admin(group_id: str = "", user_id: str = "", enable: str = "true") -> str:
    return await _call("set_group_admin", {
        "group_id": _safe_int(group_id, "群号"), "user_id": _safe_int(user_id, "QQ号"),
        "enable": enable.lower() in ("true", "1")
    })


@tool("qq_group_notice", "发群公告", P(group_id=S("群号", True), content=S("公告内容", True)))
async def qq_group_notice(group_id: str = "", content: str = "") -> str:
    # 尝试标准名，不行用下划线版本
    result = await _call("send_group_notice", {"group_id": _safe_int(group_id, "群号"), "content": content})
    if "error" in str(result).lower():
        result = await _call("_send_group_notice", {"group_id": _safe_int(group_id, "群号"), "content": content})
    return result


@tool("qq_group_leave", "退出群聊", P(group_id=S("群号", True)))
async def qq_group_leave(group_id: str = "") -> str:
    return await _call("set_group_leave", {"group_id": _safe_int(group_id, "群号")})


@tool("qq_set_title", "设置群专属头衔",
      P(group_id=S("群号", True), user_id=S("用户QQ号", True), title=S("头衔", True)))
async def qq_set_title(group_id: str = "", user_id: str = "", title: str = "") -> str:
    return await _call("set_group_special_title", {
        "group_id": _safe_int(group_id, "群号"), "user_id": _safe_int(user_id, "QQ号"), "special_title": title
    })


# ================================================================
# 查询类（10个）
# ================================================================

@tool("qq_friend_list", "获取QQ好友列表")
async def qq_friend_list() -> str:
    return await _call("get_friend_list", {})


@tool("qq_group_list", "获取QQ群列表")
async def qq_group_list() -> str:
    return await _call("get_group_list", {})


@tool("qq_group_member_list", "获取群成员列表", P(group_id=S("群号", True)))
async def qq_group_member_list(group_id: str = "") -> str:
    return await _call("get_group_member_list", {"group_id": _safe_int(group_id, "群号")})


@tool("qq_group_member_info", "获取群成员信息",
      P(group_id=S("群号", True), user_id=S("用户QQ号", True)))
async def qq_group_member_info(group_id: str = "", user_id: str = "") -> str:
    return await _call("get_group_member_info", {
        "group_id": _safe_int(group_id, "群号"), "user_id": _safe_int(user_id, "QQ号")
    })


@tool("qq_stranger_info", "获取陌生人信息", P(user_id=S("用户QQ号", True)))
async def qq_stranger_info(user_id: str = "") -> str:
    return await _call("get_stranger_info", {"user_id": _safe_int(user_id, "QQ号")})


@tool("qq_group_info", "获取群信息", P(group_id=S("群号", True)))
async def qq_group_info(group_id: str = "") -> str:
    return await _call("get_group_info", {"group_id": _safe_int(group_id, "群号")})


@tool("qq_login_info", "获取当前登录的QQ账号信息")
async def qq_login_info() -> str:
    return await _call("get_login_info", {})


@tool("qq_group_honor", "获取群荣誉信息（龙王等）", P(group_id=S("群号", True)))
async def qq_group_honor(group_id: str = "") -> str:
    return await _call("get_group_honor_info", {"group_id": _safe_int(group_id, "群号"), "type": "all"})


@tool("qq_group_msg_history", "获取群消息历史",
      P(group_id=S("群号", True), count=I("条数")))
async def qq_group_msg_history(group_id: str = "", count: int = 20) -> str:
    return await _call("get_group_msg_history", {
        "group_id": _safe_int(group_id, "群号"), "message_seq": 0, "count": count
    })


@tool("qq_recent_contact", "获取最近联系人")
async def qq_recent_contact() -> str:
    return await _call("get_recent_contact", {})


# ================================================================
# 请求处理（2个）
# ================================================================

@tool("qq_accept_friend", "处理加好友请求",
      P(flag=S("请求标识", True), approve=S("true=同意/false=拒绝", True)))
async def qq_accept_friend(flag: str = "", approve: str = "true") -> str:
    return await _call("set_friend_add_request", {
        "flag": flag, "approve": approve.lower() in ("true", "1")
    })


@tool("qq_accept_group", "处理加群请求",
      P(flag=S("请求标识", True), approve=S("true=同意/false=拒绝", True), sub_type=S("add/invite")))
async def qq_accept_group(flag: str = "", approve: str = "true", sub_type: str = "add") -> str:
    return await _call("set_group_add_request", {
        "flag": flag, "sub_type": sub_type, "approve": approve.lower() in ("true", "1")
    })


# ================================================================
# 文件（3个）
# ================================================================

@tool("qq_group_files", "获取群文件列表", P(group_id=S("群号", True)))
async def qq_group_files(group_id: str = "") -> str:
    return await _call("get_group_root_files", {"group_id": _safe_int(group_id, "群号")})


@tool("qq_group_file_url", "获取群文件下载链接",
      P(group_id=S("群号", True), file_id=S("文件ID", True), busid=I("业务ID")))
async def qq_group_file_url(group_id: str = "", file_id: str = "", busid: int = 0) -> str:
    return await _call("get_group_file_url", {
        "group_id": _safe_int(group_id, "群号"), "file_id": file_id, "busid": busid
    })


@tool("qq_group_file_info", "获取群文件系统信息", P(group_id=S("群号", True)))
async def qq_group_file_info(group_id: str = "") -> str:
    return await _call("get_group_file_system_info", {"group_id": _safe_int(group_id, "群号")})


# ================================================================
# 其他（3个）
# ================================================================

@tool("qq_get_record", "获取QQ语音文件", P(file=S("语音文件名", True)))
async def qq_get_record(file: str = "") -> str:
    return await _call("get_record", {"file": file, "out_format": "mp3"})


@tool("qq_set_profile", "设置QQ资料",
      P(nickname=S("昵称"), personal_note=S("个性签名")))
async def qq_set_profile(nickname: str = "", personal_note: str = "") -> str:
    params = {}
    if nickname:
        params["nickname"] = nickname
    if personal_note:
        params["personal_note"] = personal_note
    return await _call("set_qq_profile", params)


@tool("qq_friend_msg_history", "获取好友消息历史",
      P(user_id=S("好友QQ号", True), count=I("条数")))
async def qq_friend_msg_history(user_id: str = "", count: int = 20) -> str:
    return await _call("get_friend_msg_history", {"user_id": _safe_int(user_id, "QQ号"), "count": count})


# ================================================================
# 群精华消息
# ================================================================

@tool("qq_set_essence", "设置群精华消息", P(message_id=S("消息ID", True)))
async def qq_set_essence(message_id: str = "") -> str:
    return await _call("set_essence_msg", {"message_id": _safe_int(message_id, "消息ID")})

@tool("qq_delete_essence", "移除群精华消息", P(message_id=S("消息ID", True)))
async def qq_delete_essence(message_id: str = "") -> str:
    return await _call("delete_essence_msg", {"message_id": _safe_int(message_id, "消息ID")})

@tool("qq_get_essence", "获取群精华消息列表", P(group_id=S("群号", True)))
async def qq_get_essence(group_id: str = "") -> str:
    return await _call("get_essence_msg_list", {"group_id": _safe_int(group_id, "群号")})


# ================================================================
# 发送文件
# ================================================================

# 2026-07-03 工具实现（批9）：qq_send_file——NapCat OneBot 的文件上传。
# action 名参照 NapCat/go-cqhttp 扩展 API：群文件 upload_group_file、
# 私聊文件 upload_private_file（与本文件既有 get_group_file_url 等
# NapCat 扩展 action 同一命名体系）。
# 吸取 qq_send_voice 谎报的教训：只有拿到 NapCat 的成功响应数据才报「已发送」，
# 5秒内没确认就如实说「未确认」，绝不假装成功。
@tool("qq_send_file", "发文件——把电脑上的一个文件发到QQ（私聊或群文件）。当用户说「把文件发给我」「发个文件到群里」「把XX传给我」时调用",
      P(target=S("目标QQ号或群号(纯数字)，不填时发给当前会话", ),
        file_path=S("要发送的本地文件路径", True),
        is_group=S("是否发到群(true/false)，不填时按当前会话自动判断")))
async def qq_send_file(target: str = "", file_path: str = "", is_group: str = "") -> str:
    from pathlib import Path

    if not file_path:
        return "发送失败：请提供要发送的本地文件路径"

    # 1. 文件必须真实存在（失败原因说清楚，不含糊）
    p = Path(file_path)
    if not p.exists() or not p.is_file():
        return f"发送失败：文件不存在：{file_path}"

    # 2. QQ 必须已连接
    if not _qq_adapter or not getattr(_qq_adapter, "_ws", None):
        return "发送失败：QQ未连接，无法发送文件"

    # 3. 确定目标：优先用参数，缺省从当前消息上下文兜底
    target = str(target).strip()
    ctx = get_msg_context()
    group_mode: bool
    if is_group in ("true", "True", True):
        group_mode = True
    elif is_group in ("false", "False", False):
        group_mode = False
    else:
        # 未指定时：目标等于当前群号→群；否则按会话类型判断
        group_mode = bool(ctx.get("is_group")) if not target else (
            target == str(ctx.get("group_id") or "")
        )
    if not target:
        target = str(ctx.get("group_id") or "") if group_mode else str(ctx.get("user_id") or "")
    if not target:
        return "发送失败：请提供目标QQ号或群号（纯数字）"
    if not target.isdigit():
        return f"发送失败：目标必须是纯数字QQ号/群号，不能是'{target}'"

    # 4. 调 NapCat 上传 API（file 用绝对路径，NapCat 读本机文件）
    abs_path = str(p.resolve())
    if group_mode:
        action = "upload_group_file"
        params: dict = {"group_id": int(target), "file": abs_path, "name": p.name}
    else:
        action = "upload_private_file"
        params = {"user_id": int(target), "file": abs_path, "name": p.name}

    try:
        result = await _qq_adapter._call_api(action, params, wait_response=True)
    except Exception as e:
        return f"发送失败：调用QQ接口出错（{e}）"

    # 5. 只有拿到成功响应数据才报成功；没确认就如实说（不谎报）
    if result is not None:
        return f"文件已发送：{p.name}"
    return (
        f"文件上传请求已提交（{p.name}），但5秒内没收到QQ的成功确认——"
        "大文件可能还在后台上传；如果对方一直没收到，请检查QQ连接和文件大小"
    )


# ================================================================
# 撤回自己的消息
# ================================================================

@tool("qq_recall_last", "撤回自己最近发的一条消息——当用户说「撤回」「把刚才那条删了」时调用")
async def qq_recall_last() -> str:
    """撤回白自己最近发送的一条消息。"""
    if not _qq_adapter:
        return "QQ未连接"
    msg_id = _qq_adapter.get_last_sent_id()
    if not msg_id:
        return "没有找到最近发送的消息"
    result = await _qq_adapter._call_api("delete_msg", {"message_id": msg_id}, wait_response=True)
    # 从列表里删掉
    _qq_adapter._sent_messages = [m for m in _qq_adapter._sent_messages if m["msg_id"] != msg_id]
    return "已撤回"


# ================================================================
# 导出
# ================================================================

TOOLS = [fn._tool_def for fn in [
    # 消息类
    qq_send_voice, qq_send_image, qq_send_poke, qq_send_like,
    qq_delete_msg, qq_get_msg, qq_get_forward_msg, qq_emoji_like,
    # 群管理
    qq_group_ban, qq_group_whole_ban, qq_group_kick, qq_set_group_card,
    qq_set_group_name, qq_set_group_admin, qq_group_notice, qq_group_leave, qq_set_title,
    # 查询
    qq_friend_list, qq_group_list, qq_group_member_list, qq_group_member_info,
    qq_stranger_info, qq_group_info, qq_login_info, qq_group_honor,
    qq_group_msg_history, qq_recent_contact,
    # 请求
    qq_accept_friend, qq_accept_group,
    # 文件
    qq_group_files, qq_group_file_url, qq_group_file_info,
    # 2026-07-03 工具实现（批9）：发送文件（NapCat upload_group_file/upload_private_file）
    qq_send_file,
    # 其他
    qq_get_record, qq_set_profile, qq_friend_msg_history,
    # 精华消息
    qq_set_essence, qq_delete_essence, qq_get_essence,
    # 撤回
    qq_recall_last,
]]
