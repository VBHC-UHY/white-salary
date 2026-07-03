"""B站工具 — 搜索/详情/点赞/评论/推荐。"""
from ._helpers import tool, P, S, NONE_PARAMS


@tool("bilibili_search", "搜索B站视频", P(query=S("搜索关键词", True)))
async def bilibili_search(query: str = "") -> str:
    from white_salary.adapters.tools.bilibili_tool import bilibili_search as _bs
    return await _bs(query)


@tool("bilibili_video_info", "获取B站视频详细信息", P(url=S("视频URL或BV号", True)))
async def bilibili_video_info(url: str = "") -> str:
    from white_salary.adapters.tools.bilibili_tool import bilibili_video_info as _bi
    return await _bi(url)


def _load_bili_cookies() -> dict:
    """从config/bili.ini加载B站cookie。"""
    import configparser
    from pathlib import Path
    cookies = {}
    # 2026-07-03 审计修复（批5）：config/bili.ini 改为从模块位置推导项目根的
    # 绝对路径，不再依赖 CWD（依据 docs/audit-2026-07-02/config-audit.json）
    # 本文件位于 src/white_salary/adapters/tools/builtin/，项目根 = parents[5]
    ini_path = Path(__file__).resolve().parents[5] / "config" / "bili.ini"
    if ini_path.exists():
        cp = configparser.RawConfigParser()
        cp.read(str(ini_path), encoding="utf-8")
        if cp.has_section("bili"):
            cookies = {
                "SESSDATA": cp.get("bili", "sessdata", fallback=""),
                "bili_jct": cp.get("bili", "bili_jct", fallback=""),
                "buvid3": cp.get("bili", "buvid3", fallback=""),
                "DedeUserID": cp.get("bili", "dedeuserid", fallback=""),
            }
    return cookies


@tool("like_bilibili_video", "给B站视频点赞", P(url=S("视频链接或BV号", True)))
async def like_bilibili_video(url: str = "") -> str:
    """给B站视频点赞（需要config/bili.ini配置）。"""
    import aiohttp
    cookies = _load_bili_cookies()
    if not cookies.get("SESSDATA"):
        return "[B站] 未配置登录凭证，请在config/bili.ini填写SESSDATA"
    try:
        from white_salary.adapters.tools.bilibili_tool import _extract_bvid
        bvid = await _extract_bvid(url)
        if not bvid:
            return "无法识别视频链接"
        # 先获取aid
        async with aiohttp.ClientSession(cookies=cookies) as session:
            async with session.get(
                f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}",
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36", "Referer": "https://www.bilibili.com"},
            ) as resp:
                data = await resp.json()
                aid = data.get("data", {}).get("aid")
                title = data.get("data", {}).get("title", "")
            if not aid:
                return "获取视频信息失败"
            # 点赞
            async with session.post(
                "https://api.bilibili.com/x/web-interface/archive/like",
                data={"aid": aid, "like": 1, "csrf": cookies.get("bili_jct", "")},
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36", "Referer": "https://www.bilibili.com"},
            ) as resp:
                result = await resp.json()
                if result.get("code") == 0:
                    return f"已给「{title}」点赞 👍"
                else:
                    return f"点赞失败: {result.get('message', '未知错误')}"
    except Exception as e:
        return f"点赞出错: {e}"


@tool("comment_bilibili_video", "评论B站视频",
      P(url=S("视频链接或BV号", True), content=S("评论内容", True)))
async def comment_bilibili_video(url: str = "", content: str = "") -> str:
    """给B站视频发评论（需要config/bili.ini配置）。"""
    import aiohttp
    cookies = _load_bili_cookies()
    if not cookies.get("SESSDATA"):
        return "[B站] 未配置登录凭证"
    if not content:
        return "请提供评论内容"
    try:
        from white_salary.adapters.tools.bilibili_tool import _extract_bvid
        bvid = await _extract_bvid(url)
        if not bvid:
            return "无法识别视频链接"
        async with aiohttp.ClientSession(cookies=cookies) as session:
            # 获取aid
            async with session.get(
                f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}",
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36", "Referer": "https://www.bilibili.com"},
            ) as resp:
                data = await resp.json()
                aid = data.get("data", {}).get("aid")
                title = data.get("data", {}).get("title", "")
            if not aid:
                return "获取视频信息失败"
            # 发评论
            async with session.post(
                "https://api.bilibili.com/x/v2/reply/add",
                data={
                    "type": 1, "oid": aid, "message": content,
                    "csrf": cookies.get("bili_jct", ""),
                },
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36", "Referer": f"https://www.bilibili.com/video/{bvid}"},
            ) as resp:
                result = await resp.json()
                if result.get("code") == 0:
                    return f"已在「{title}」下评论: {content[:30]}"
                else:
                    return f"评论失败: {result.get('message', '未知错误')}"
    except Exception as e:
        return f"评论出错: {e}"


@tool("bilibili_feed", "获取B站首页推荐视频")
async def bilibili_feed() -> str:
    """获取B站个性化推荐视频（需要config/bili.ini配置）。"""
    import aiohttp
    cookies = _load_bili_cookies()
    if not cookies.get("SESSDATA"):
        return "[B站] 未配置登录凭证，请在config/bili.ini填写SESSDATA"
    try:
        async with aiohttp.ClientSession(cookies=cookies) as session:
            async with session.get(
                "https://api.bilibili.com/x/web-interface/wbi/index/top/feed/rcmd",
                params={"fresh_type": 4, "ps": 5},
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36", "Referer": "https://www.bilibili.com"},
            ) as resp:
                data = await resp.json()
                items = data.get("data", {}).get("item", [])
                if not items:
                    return "暂无推荐视频"
                lines = ["B站推荐视频：\n"]
                for i, v in enumerate(items[:5], 1):
                    title = v.get("title", "")
                    author = v.get("owner", {}).get("name", "")
                    bvid = v.get("bvid", "")
                    play = v.get("stat", {}).get("view", 0)
                    play_str = f"{play/10000:.1f}万" if play >= 10000 else str(play)
                    lines.append(f"{i}. {title}")
                    lines.append(f"   UP: {author} | 播放: {play_str} | https://www.bilibili.com/video/{bvid}")
                    lines.append("")
                return "\n".join(lines)
    except Exception as e:
        return f"获取推荐失败: {e}"


@tool("watch_video", "一起看视频（打开浏览器+截屏+识别+点评）",
      P(url=S("视频链接或搜索关键词", True)))
async def watch_video(url: str = "") -> str:
    """
    打开B站视频和用户一起看。

    流程：搜索/解析链接 → 打开浏览器 → 等待加载 → 截屏 → 识别画面 → 返回点评。
    """
    import asyncio

    # 1. 如果是关键词而不是链接，先搜索
    video_url = url
    if not url.startswith("http") and "bilibili" not in url and "BV" not in url:
        from white_salary.adapters.tools.bilibili_tool import bilibili_search
        search_result = await bilibili_search(url, count=1)
        import re
        urls = re.findall(r'https://www\.bilibili\.com/video/BV\w+', search_result)
        if urls:
            video_url = urls[0]
        else:
            return f"没找到关于「{url}」的视频"
    elif "BV" in url and "bilibili.com" not in url:
        import re
        bv = re.search(r'(BV\w{10})', url)
        if bv:
            video_url = f"https://www.bilibili.com/video/{bv.group(1)}"

    # 2. 获取视频信息
    video_info = ""
    try:
        from white_salary.adapters.tools.bilibili_tool import bilibili_video_info
        video_info = await bilibili_video_info(video_url)
    except Exception:
        pass

    # 3. 打开浏览器
    try:
        from white_salary.adapters.tools.pc_helpers import open_url
        open_url(video_url)
    except Exception as e:
        return f"打开浏览器失败: {e}"

    # 4. 等待页面加载
    await asyncio.sleep(5)

    # 5. 截屏
    screen_desc = ""
    try:
        from white_salary.adapters.vision.screenshot import capture_screenshot
        img_b64 = await capture_screenshot()
        if img_b64:
            # 6. 用vision_llm识别画面
            try:
                from white_salary.adapters.vision.multimodal_adapter import MultimodalVisionAdapter
                from white_salary.adapters.tools.cloud_config import resolve_vision_channel

                vision_cfg = resolve_vision_channel()
                if vision_cfg.configured:
                    adapter = MultimodalVisionAdapter(
                        api_key=vision_cfg.api_key,
                        model=vision_cfg.model,
                        base_url=vision_cfg.base_url,
                    )
                    screen_desc = await adapter.describe_image(
                        img_b64,
                        prompt="这是一个B站视频页面的截图。描述你看到的视频内容、画面和弹幕。用轻松的语气。",
                    )
            except Exception:
                screen_desc = "（视觉识别不可用，但视频已打开）"
    except Exception:
        screen_desc = "（截屏不可用，但视频已打开）"

    # 7. 组装结果
    result_parts = [
        f"已经帮你打开了视频：{video_url}",
    ]
    if video_info:
        result_parts.append(f"\n视频信息：\n{video_info}")
    if screen_desc:
        result_parts.append(f"\n我看到的画面：{screen_desc}")
    result_parts.append("\n视频已经在播放了，你可以随时跟我聊这个视频的内容！")

    return "\n".join(result_parts)


TOOLS = [fn._tool_def for fn in [
    bilibili_search, bilibili_video_info, like_bilibili_video,
    comment_bilibili_video, bilibili_feed, watch_video,
]]
