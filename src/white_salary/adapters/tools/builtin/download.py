"""
下载工具 — 用 yt_dlp 下载在线视频到本地。

2026-07-03 工具实现（批9）：download_video 真实现——
原 video.py 里的同名空壳（批2下架）被本文件取代：
  - yt_dlp（已装 2026.02.04）下载到 data/downloads/
  - 限制：单文件≤500MB、时长≤30分钟，超限直接拒绝并说明
  - 下载过程写日志（logger），完成返回保存路径与标题
  - 非视频站直链交给 yt_dlp 报错，把报错转译成中文
超时预算见 registry.TOOL_TIMEOUTS["download_video"]=600 秒。
"""

import asyncio
from pathlib import Path
from typing import Any

from loguru import logger

from ._helpers import tool, P, S

# 下载限制（超限拒绝，防止磁盘被塞爆/工具超时）
MAX_FILESIZE_BYTES: int = 500 * 1024 * 1024   # 单文件≤500MB
MAX_DURATION_SECONDS: int = 30 * 60           # 时长≤30分钟


def _project_root_path() -> Path:
    """返回项目根目录绝对路径（从模块位置推导，不依赖 CWD）。"""
    return Path(__file__).resolve().parents[5]


def _downloads_dir() -> Path:
    """返回下载目录 data/downloads/（不存在则创建）。"""
    d = _project_root_path() / "data" / "downloads"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _translate_ytdlp_error(err: Exception) -> str:
    """把 yt_dlp 的英文报错转译成用户能看懂的中文。"""
    text = str(err)
    lower = text.lower()
    if "unsupported url" in lower or "no video" in lower:
        return "这个链接不是受支持的视频页面（请给B站/YouTube等视频网站的视频页链接，不要给普通网页或文件直链）"
    if "video unavailable" in lower or "not available" in lower:
        return "视频不存在或已被删除/设为私密"
    if "http error 404" in lower:
        return "链接打不开（404），请检查链接是否正确"
    if "http error 403" in lower or "sign in" in lower or "login" in lower:
        return "视频需要登录或没有访问权限，下载不了"
    if "max-filesize" in lower or "file is larger" in lower:
        return f"视频文件超过{MAX_FILESIZE_BYTES // (1024 * 1024)}MB上限，已中止下载"
    if "timed out" in lower or "timeout" in lower or "connection" in lower:
        return "网络连接超时，请稍后再试"
    return f"下载器报错：{text[:200]}"


def _progress_hook(status: dict) -> None:
    """yt_dlp 进度回调：下载中写日志，让后台可观测（不打扰对话）。"""
    try:
        if status.get("status") == "downloading":
            logger.debug(
                "[Download] 下载中 {} {}",
                status.get("_percent_str", "?"),
                status.get("_speed_str", ""),
            )
        elif status.get("status") == "finished":
            logger.info("[Download] 下载完成: {}", status.get("filename", ""))
    except Exception:
        pass  # 进度日志绝不影响下载本身


def _download_sync(url: str, dest_dir: Path) -> tuple[str, str]:
    """
    同步下载（在线程池里跑，避免阻塞事件循环）。

    Returns:
        (保存路径, 视频标题)

    Raises:
        ValueError: 超出时长/大小限制（信息已是中文，直接展示给用户）
        yt_dlp 的各种异常: 由调用方转译成中文
    """
    import yt_dlp

    common_opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,  # 只下单个视频，不下整个播放列表
    }

    # 第一步：只解析信息不下载，先做时长/大小预检（超限拒绝，不浪费流量）
    with yt_dlp.YoutubeDL(common_opts) as ydl:
        info = ydl.extract_info(url, download=False)
    if info is None:
        raise ValueError("链接解析失败：没有拿到视频信息，请确认是视频页面链接")

    title = str(info.get("title") or "未知标题")
    duration = info.get("duration") or 0
    if duration and duration > MAX_DURATION_SECONDS:
        raise ValueError(
            f"视频《{title}》时长{int(duration) // 60}分钟，"
            f"超过{MAX_DURATION_SECONDS // 60}分钟的下载上限，已拒绝下载"
        )
    approx_size = info.get("filesize") or info.get("filesize_approx") or 0
    if approx_size and approx_size > MAX_FILESIZE_BYTES:
        raise ValueError(
            f"视频《{title}》约{approx_size // (1024 * 1024)}MB，"
            f"超过{MAX_FILESIZE_BYTES // (1024 * 1024)}MB的下载上限，已拒绝下载"
        )

    # 第二步：真正下载（max_filesize 双保险：预检估不准时下载中途也会中止）
    dl_opts: dict[str, Any] = {
        **common_opts,
        "outtmpl": str(dest_dir / "%(title).80s-%(id)s.%(ext)s"),
        "max_filesize": MAX_FILESIZE_BYTES,
        "progress_hooks": [_progress_hook],
    }
    with yt_dlp.YoutubeDL(dl_opts) as ydl:
        result = ydl.extract_info(url, download=True)
        saved_path = ydl.prepare_filename(result)

    if not saved_path or not Path(saved_path).exists():
        # max_filesize 中止时 yt_dlp 不抛异常只是跳过文件——如实报告
        raise ValueError(
            f"视频《{title}》下载中止（很可能超过"
            f"{MAX_FILESIZE_BYTES // (1024 * 1024)}MB大小上限），本地没有保存文件"
        )
    return saved_path, title


@tool("download_video", "下载视频——给一个视频链接（B站/YouTube等视频网站的视频页），把视频下载到电脑上。当用户说「下载这个视频」「把这个视频存下来」时调用。限制：≤30分钟且≤500MB",
      P(url=S("视频页面链接", True)))
async def download_video(url: str = "") -> str:
    url = str(url).strip()
    if not url:
        return "请提供要下载的视频链接"
    if not (url.startswith("http://") or url.startswith("https://")):
        return f"下载失败：'{url[:50]}'不是有效的网址链接"

    logger.info("[Download] 开始下载视频: {}", url)
    try:
        # yt_dlp 是同步阻塞库，放线程池跑，不占对话路径
        saved_path, title = await asyncio.to_thread(_download_sync, url, _downloads_dir())
    except ValueError as e:
        # 预检拒绝/中止，信息已是中文
        return f"下载失败：{e}"
    except Exception as e:
        return f"下载失败：{_translate_ytdlp_error(e)}"

    return f"视频《{title}》已下载，保存在 {saved_path}"


TOOLS = [fn._tool_def for fn in [download_video]]
