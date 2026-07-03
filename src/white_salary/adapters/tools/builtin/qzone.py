"""QQ空间工具 — 发说说/发图/回复评论/获取说说/逛空间/社交/监控。

Phase 25-B 完整版：
  - 原有7个工具保留
  - 新增：逛空间并评论、检查新评论、QQ空间状态、发带图说说(自动补白的形象)
  - 频率控制集成
  - QQ空间记忆集成
"""
from ._helpers import tool, P, S


def _get_qzone():
    from white_salary.adapters.platform.qzone_api import get_client
    return get_client()


def _get_memory():
    try:
        from white_salary.adapters.platform.qzone_memory import get_qzone_memory
        return get_qzone_memory()
    except Exception:
        return None


# ================================================================
# 原有工具（保留）
# ================================================================

@tool("qzone_post", "发QQ空间说说——当用户说「发说说」「发QQ空间」「发个动态」时必须调用。如果用户说要@某人，把QQ号填到at_uins里",
      P(content=S("说说内容", True), at_uins=S("要@的人的QQ号，多个用逗号隔开，如'123,456'")))
async def qzone_post(content: str = "", at_uins: str = "") -> str:
    if not content:
        return "说说内容不能为空"
    client = _get_qzone()
    if not client.is_configured:
        return "QQ空间未配置Cookie，请在控制面板设置"
    if client.is_cookie_expired:
        return "QQ空间Cookie已过期，请重新登录"

    # 频率检查（只检查，不记录——成功后才记录）
    try:
        from white_salary.core.qzone.rate_limiter import get_rate_limiter
        if not get_rate_limiter().can_do("post"):
            return "发说说太频繁了，稍后再试"
    except Exception:
        pass

    # 手动@（用户指定的QQ号）
    at_parts = []
    if at_uins:
        for uin in at_uins.replace(" ", "").split(","):
            uin = uin.strip()
            if uin and uin.isdigit():
                at_parts.append(f"@{{uin:{uin},nick:{uin},who:1}}")

    # 自动@（兴趣匹配推荐的人，手动@过的不重复@）
    manual_uins = {u.strip() for u in at_uins.split(",") if u.strip()} if at_uins else set()
    try:
        from white_salary.core.qzone.social_manager import get_social_manager
        mgr = get_social_manager()
        at_targets = mgr.get_at_targets(content, exclude_uins=manual_uins)
        for t in at_targets:
            t_uin = t.get("uin", "")
            t_nick = t.get("nick", "")
            if t_uin and t_uin not in manual_uins:
                at_parts.append(f"@{{uin:{t_uin},nick:{t_nick},who:1,auto:1}}")
    except Exception:
        pass

    if at_parts:
        content = content + " " + " ".join(at_parts)

    result = await client.post_emotion(content)
    if result["success"]:
        # API成功后才记录频率
        try:
            get_rate_limiter().record("post")
        except Exception:
            pass
        mem = _get_memory()
        if mem:
            mem.add_post(content, tid=result.get("tid", ""))
        return "说说已发布"
    return "说说发布失败了"


@tool("qzone_post_image", "发带图片的QQ空间说说——当用户说「发带图的说说」「发图到空间」时调用",
      P(content=S("说说内容", True), image_path=S("图片路径（本地路径或先用generate_image生成）")))
async def qzone_post_image(content: str = "", image_path: str = "") -> str:
    if not content:
        return "说说内容不能为空"
    client = _get_qzone()
    if not client.is_configured:
        return "QQ空间未配置Cookie"
    if client.is_cookie_expired:
        return "QQ空间Cookie已过期，请重新登录"

    # B12: 检测是否需要自动补白的形象（发图说说含人物描述时自动加外观提示词）
    pic_info = None
    if image_path:
        from pathlib import Path
        img_file = Path(image_path)
        if img_file.exists():
            pic_info = await client.upload_image(img_file.read_bytes(), img_file.name)
            if not pic_info.get("success"):
                return "图片上传失败了"
    elif not image_path:
        # 没有图片路径时，尝试用AI生成图片（自动补白的形象）
        try:
            from white_salary.adapters.tools.image_gen import generate_image, _is_self_portrait, _get_appearance
            # 如果内容提到了人物相关，自动加白的外观
            prompt = content
            if _is_self_portrait(content) or any(kw in content for kw in ["白的照片", "我的照片", "自拍", "画个我", "发个我"]):
                prompt = f"{_get_appearance()}, {content}"
            img_path = await generate_image(prompt)
            if img_path:
                img_file = Path(img_path)
                if img_file.exists():
                    pic_info = await client.upload_image(img_file.read_bytes(), img_file.name)
        except Exception as _e:
            from loguru import logger
            logger.debug(f"[QZone] 图片生成/上传失败，改为纯文字: {_e}")

    # 频率检查（只检查，不记录——成功后才记录）
    try:
        from white_salary.core.qzone.rate_limiter import get_rate_limiter
        if not get_rate_limiter().can_do("post"):
            return "发说说太频繁了，稍后再试"
    except Exception:
        pass

    result = await client.post_emotion(content, pic_info)
    if result["success"]:
        try:
            get_rate_limiter().record("post")
        except Exception:
            pass
        mem = _get_memory()
        if mem:
            mem.add_post(content, tid=result.get("tid", ""), has_image=bool(pic_info))
        return "带图说说已发布" if pic_info else "说说已发布（图片生成/上传失败，改为纯文字）"
    return "说说发布失败了"


@tool("qzone_reply_comment", "回复QQ空间评论——必须提供评论ID才能让对方看到回复",
      P(tid=S("说说ID", True), content=S("回复内容", True),
        commentid=S("要回复的评论ID", True), reply_uin=S("被回复者QQ号")))
async def qzone_reply_comment(tid: str = "", content: str = "", commentid: str = "", reply_uin: str = "") -> str:
    if not tid or not content or not commentid:
        return "需要说说ID、回复内容和评论ID"
    client = _get_qzone()
    if not client.is_configured:
        return "QQ空间未配置Cookie"

    # 频率检查
    try:
        from white_salary.core.qzone.rate_limiter import get_rate_limiter
        if not get_rate_limiter().can_do("reply"):
            return "回复太频繁了，稍后再试"
    except Exception:
        pass

    result = await client.reply_comment(tid, content, commentid, reply_uin)
    if result["success"]:
        try:
            from white_salary.core.qzone.rate_limiter import get_rate_limiter
            get_rate_limiter().record("reply")
        except Exception:
            pass
        # 记录到记忆
        mem = _get_memory()
        if mem:
            mem.add_comment("", "", content, tid=tid, comment_id=commentid)
        return "评论已回复"
    return "回复评论失败了"


@tool("qzone_get_feeds", "获取QQ空间说说列表——查看自己或别人最近发的说说",
      P(count=S("获取数量，默认5"), target_uin=S("目标QQ号（空=自己）")))
async def qzone_get_feeds(count: str = "5", target_uin: str = "") -> str:
    client = _get_qzone()
    if not client.is_configured:
        return "QQ空间未配置Cookie"
    num = int(count) if count.isdigit() else 5
    feeds = await client.get_feeds(num, target_uin)
    if not feeds:
        return "没有获取到说说"
    lines = []
    for i, f in enumerate(feeds, 1):
        text = f["content"][:50] if f["content"] else "(无文字)"
        lines.append(f"{i}. {f.get('name', '')} ({f['uin']}): {text} [ID:{f['tid']}]")
    return "\n".join(lines)


@tool("qzone_get_comments", "获取QQ空间说说的评论列表",
      P(tid=S("说说ID", True)))
async def qzone_get_comments(tid: str = "") -> str:
    if not tid:
        return "需要提供说说ID"
    client = _get_qzone()
    if not client.is_configured:
        return "QQ空间未配置Cookie"
    comments = await client.get_comments(tid)
    if not comments:
        return "没有评论"
    lines = []
    for c in comments:
        lines.append(f"{c['name']}({c['uin']}): {c['content'][:40]} [评论ID:{c['commentid']}]")
    return "\n".join(lines)


@tool("qzone_visit_space", "逛别人的QQ空间——查看别人最近发的说说",
      P(target_uin=S("要逛的人的QQ号", True), count=S("查看数量，默认3")))
async def qzone_visit_space(target_uin: str = "", count: str = "3") -> str:
    if not target_uin:
        return "需要提供QQ号"
    client = _get_qzone()
    if not client.is_configured:
        return "QQ空间未配置Cookie"
    num = int(count) if count.isdigit() else 3
    feeds = await client.get_feeds(num, target_uin)
    if not feeds:
        return f"没有获取到{target_uin}的说说（可能设了权限）"
    lines = [f"{target_uin}的最近说说:"]
    for i, f in enumerate(feeds, 1):
        text = f["content"][:80] if f["content"] else "(无文字)"
        lines.append(f"{i}. {text}")
    return "\n".join(lines)


@tool("qzone_share_mood", "在QQ空间分享心情——根据当前心情自主发一条说说",
      P(mood=S("当前心情"), content=S("说说内容")))
async def qzone_share_mood(mood: str = "", content: str = "") -> str:
    if not content:
        return "内容不能为空"
    return await qzone_post(content=content)


# ================================================================
# 新增工具（Phase 25-B）
# ================================================================

@tool("qzone_visit_and_comment", "逛别人空间并自动评论——查看说说然后留言评论",
      P(target_uin=S("要逛的人的QQ号", True)))
async def qzone_visit_and_comment(target_uin: str = "") -> str:
    """逛别人空间，看说说，自动生成评论。"""
    if not target_uin:
        return "需要提供QQ号"
    try:
        from white_salary.core.qzone.social_manager import get_social_manager
        manager = get_social_manager()
        await manager.trigger_visit_async(target_uin)
        return f"已逛{target_uin}的空间并尝试评论"
    except Exception as e:
        return f"逛空间失败: {e}"


@tool("qzone_check_new_comments", "检查QQ空间新评论——看看有没有人评论了我的说说",)
async def qzone_check_new_comments() -> str:
    """检查并回复新评论。"""
    try:
        from white_salary.core.services.qzone_monitor import get_qzone_monitor
        monitor = get_qzone_monitor()
        count = await monitor.check_and_reply()
        if count > 0:
            return f"发现并回复了{count}条新评论"
        return "没有新评论"
    except Exception as e:
        return f"检查评论失败: {e}"


@tool("qzone_status", "查看QQ空间状态——Cookie是否有效、发了多少说说、频率统计",)
async def qzone_status() -> str:
    """查看QQ空间整体状态。"""
    client = _get_qzone()
    parts = []

    # Cookie状态
    if not client.is_configured:
        parts.append("Cookie: 未配置")
    elif client.is_cookie_expired:
        parts.append("Cookie: 已过期，需要重新登录")
    else:
        parts.append(f"Cookie: 正常 (QQ号: {client.uin})")

    # 记忆统计
    mem = _get_memory()
    if mem:
        stats = mem.stats
        parts.append(f"记录: {stats['posts']}条说说, {stats['comments']}条评论")

    # 频率统计
    try:
        from white_salary.core.qzone.rate_limiter import get_rate_limiter
        limiter_stats = get_rate_limiter().get_stats()
        for op, s in limiter_stats.items():
            if isinstance(s, dict):
                parts.append(f"{op}: 本小时{s['hour']}/{s['hour_limit']}, 今日{s['day']}/{s['day_limit']}")
    except Exception:
        pass

    return "\n".join(parts) if parts else "QQ空间状态未知"


@tool("qzone_delete_post", "删除QQ空间说说——当用户说「删掉那条说说」「把说说删了」时调用",
      P(tid=S("要删除的说说ID", True)))
async def qzone_delete_post(tid: str = "") -> str:
    if not tid:
        return "需要提供说说ID"
    client = _get_qzone()
    if not client.is_configured:
        return "QQ空间未配置Cookie"
    result = await client.delete_emotion(tid)
    if result["success"]:
        return "说说已删除"
    return f"删除失败: {result.get('error', '未知错误')}"


@tool("qzone_delete_comment", "删除QQ空间评论——删除自己发的评论",
      P(tid=S("说说ID", True), commentid=S("要删除的评论ID", True)))
async def qzone_delete_comment(tid: str = "", commentid: str = "") -> str:
    if not tid or not commentid:
        return "需要提供说说ID和评论ID"
    client = _get_qzone()
    if not client.is_configured:
        return "QQ空间未配置Cookie"
    result = await client.delete_comment(tid, commentid)
    if result["success"]:
        return "评论已删除"
    return f"删除失败: {result.get('error', '未知错误')}"


@tool("qzone_like", "给QQ空间说说点赞——当用户说「给XX点个赞」「赞一下」时调用",
      P(tid=S("说说ID", True), target_uin=S("说说发布者QQ号（空=自己）")))
async def qzone_like(tid: str = "", target_uin: str = "") -> str:
    if not tid:
        return "需要提供说说ID"
    client = _get_qzone()
    if not client.is_configured:
        return "QQ空间未配置Cookie"
    result = await client.like_emotion(tid, host_uin=target_uin)
    if result["success"]:
        return "已点赞"
    return "点赞失败了"


# 导出
TOOLS = [fn._tool_def for fn in [
    qzone_post, qzone_post_image, qzone_reply_comment,
    qzone_get_feeds, qzone_get_comments, qzone_visit_space, qzone_share_mood,
    qzone_visit_and_comment, qzone_check_new_comments, qzone_status,
    qzone_delete_post, qzone_delete_comment, qzone_like,
]]
