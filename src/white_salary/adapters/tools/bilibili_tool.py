"""
white_salary/adapters/tools/bilibili_tool.py

B站集成工具 — 搜索视频、获取视频信息。

借鉴v2的tools/bilibili.py：
  - v2有点赞/评论/Feed功能需要登录态，我们先做不需要登录的搜索和信息查询
  - v2的URL解析（b23.tv短链解析）很实用，保留
  - v2用bilibili_api库，我们直接用HTTP API更轻量
  - 后续可加登录态功能（点赞、评论）

功能：
  - 搜索B站视频（关键词 → 标题+播放量+UP主）
  - 获取视频详情（URL/BV号 → 完整信息）
  - 解析b23.tv短链
"""

import re
from typing import Optional

import aiohttp
from loguru import logger


# B站API
_SEARCH_API = "https://api.bilibili.com/x/web-interface/search/type"
_VIDEO_INFO_API = "https://api.bilibili.com/x/web-interface/view"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.bilibili.com",
}


def _get_cookie_header() -> dict:
    """从bili.ini加载cookie，返回带Cookie的请求头。"""
    import configparser
    from pathlib import Path
    headers = dict(_HEADERS)
    ini_path = Path("config/bili.ini")
    if ini_path.exists():
        cp = configparser.RawConfigParser()
        cp.read(str(ini_path), encoding="utf-8")
        if cp.has_section("bili"):
            parts = []
            for key in ["sessdata", "bili_jct", "buvid3", "dedeuserid"]:
                val = cp.get("bili", key, fallback="")
                if val:
                    # ini里key是小写，cookie名要还原
                    cookie_name = {
                        "sessdata": "SESSDATA", "bili_jct": "bili_jct",
                        "buvid3": "buvid3", "dedeuserid": "DedeUserID",
                    }.get(key, key)
                    parts.append(f"{cookie_name}={val}")
            if parts:
                headers["Cookie"] = "; ".join(parts)
    return headers

# BV号提取
_BVID_PATTERN = re.compile(r"(BV[a-zA-Z0-9]{10})")
_B23_PATTERN = re.compile(r"b23\.tv/[a-zA-Z0-9]+")


async def bilibili_search(query: str, count: int = 5) -> str:
    """
    搜索B站视频。

    Args:
        query: 搜索关键词
        count: 返回数量

    Returns:
        格式化的搜索结果
    """
    if not query:
        return "请输入搜索关键词"

    try:
        headers = _get_cookie_header()
        async with aiohttp.ClientSession() as session:
            params = {
                "search_type": "video",
                "keyword": query,
                "page": 1,
                "pagesize": count,
            }
            async with session.get(
                _SEARCH_API, params=params, headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    return f"B站搜索失败（HTTP {resp.status}）"

                data = await resp.json()
                results = data.get("data", {}).get("result", [])

                if not results:
                    return f"没有找到关于「{query}」的视频"

                lines = [f"B站搜索「{query}」的结果：\n"]
                for i, v in enumerate(results[:count], 1):
                    title = re.sub(r"<[^>]+>", "", v.get("title", ""))  # 去HTML标签
                    author = v.get("author", "")
                    play = v.get("play", 0)
                    bvid = v.get("bvid", "")

                    # 播放量格式化
                    if play >= 10000:
                        play_str = f"{play / 10000:.1f}万"
                    else:
                        play_str = str(play)

                    lines.append(f"{i}. {title}")
                    lines.append(f"   UP: {author} | 播放: {play_str} | https://www.bilibili.com/video/{bvid}")
                    lines.append("")

                return "\n".join(lines)

    except Exception as e:
        logger.warning(f"[Bilibili] 搜索失败: {e}")
        return f"B站搜索出错: {e}"


async def bilibili_video_info(url_or_bvid: str) -> str:
    """
    获取B站视频详细信息。

    Args:
        url_or_bvid: 视频URL或BV号

    Returns:
        格式化的视频信息
    """
    if not url_or_bvid:
        return "请提供视频链接或BV号"

    # 解析BV号
    bvid = await _extract_bvid(url_or_bvid)
    if not bvid:
        return "无法识别视频链接，请提供B站视频URL或BV号"

    try:
        headers = _get_cookie_header()
        async with aiohttp.ClientSession() as session:
            params = {"bvid": bvid}
            async with session.get(
                _VIDEO_INFO_API, params=params, headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    return f"获取视频信息失败（HTTP {resp.status}）"

                data = await resp.json()
                vdata = data.get("data", {})

                if not vdata:
                    return "视频不存在或已删除"

                title = vdata.get("title", "")
                desc = vdata.get("desc", "")[:200]
                author = vdata.get("owner", {}).get("name", "")
                stat = vdata.get("stat", {})
                view = stat.get("view", 0)
                like = stat.get("like", 0)
                danmaku = stat.get("danmaku", 0)
                reply = stat.get("reply", 0)

                # 格式化
                def fmt(n):
                    return f"{n/10000:.1f}万" if n >= 10000 else str(n)

                lines = [
                    f"标题: {title}",
                    f"UP主: {author}",
                    f"播放: {fmt(view)} | 点赞: {fmt(like)} | 弹幕: {fmt(danmaku)} | 评论: {fmt(reply)}",
                    f"链接: https://www.bilibili.com/video/{bvid}",
                ]
                if desc:
                    lines.append(f"简介: {desc}")

                return "\n".join(lines)

    except Exception as e:
        logger.warning(f"[Bilibili] 获取视频信息失败: {e}")
        return f"获取视频信息出错: {e}"


async def _extract_bvid(text: str) -> Optional[str]:
    """从文本中提取BV号（支持b23.tv短链）。"""
    # 直接匹配BV号
    match = _BVID_PATTERN.search(text)
    if match:
        return match.group(1)

    # b23.tv短链 → 跟随重定向
    if "b23.tv" in text:
        match = _B23_PATTERN.search(text)
        if match:
            short_url = f"https://{match.group()}"
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        short_url, allow_redirects=False,
                        timeout=aiohttp.ClientTimeout(total=5),
                    ) as resp:
                        location = resp.headers.get("Location", "")
                        bv_match = _BVID_PATTERN.search(location)
                        if bv_match:
                            return bv_match.group(1)
            except Exception:
                pass

    return None
