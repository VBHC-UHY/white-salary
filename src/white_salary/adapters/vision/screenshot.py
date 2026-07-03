"""
white_salary/adapters/vision/screenshot.py

截屏工具 — 截取用户屏幕并转为base64。

用于让AI"看"到用户屏幕上的内容。
需要 Pillow 库（pip install Pillow）。
"""

import base64
import io
from typing import Optional

from loguru import logger


async def capture_screenshot(monitor: int = 0) -> Optional[str]:
    """
    截取屏幕并返回base64编码的PNG图片。

    Args:
        monitor: 显示器编号（0=主显示器）

    Returns:
        base64编码的PNG图片字符串，失败返回None
    """
    try:
        # 尝试用mss（跨平台截屏）
        try:
            import mss
            with mss.mss() as sct:
                monitors = sct.monitors
                if monitor + 1 < len(monitors):
                    mon = monitors[monitor + 1]  # mss的monitors[0]是全部屏幕
                else:
                    mon = monitors[1]  # 主显示器

                screenshot = sct.grab(mon)

                # 转为PIL Image再转base64
                from PIL import Image
                img = Image.frombytes("RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")

                # 缩小尺寸（节省token）
                max_size = 1024
                if img.width > max_size or img.height > max_size:
                    img.thumbnail((max_size, max_size))

                buffer = io.BytesIO()
                img.save(buffer, format="PNG", optimize=True)
                b64 = base64.b64encode(buffer.getvalue()).decode("ascii")

                logger.debug(f"[Screenshot] Captured {img.width}x{img.height} ({len(b64) // 1024}KB)")
                return b64

        except ImportError:
            # 降级：用PIL的ImageGrab（仅Windows/macOS）
            from PIL import ImageGrab
            img = ImageGrab.grab()

            max_size = 1024
            if img.width > max_size or img.height > max_size:
                img.thumbnail((max_size, max_size))

            buffer = io.BytesIO()
            img.save(buffer, format="PNG", optimize=True)
            b64 = base64.b64encode(buffer.getvalue()).decode("ascii")

            logger.debug(f"[Screenshot] Captured {img.width}x{img.height} ({len(b64) // 1024}KB)")
            return b64

    except Exception as e:
        logger.warning(f"[Screenshot] Capture failed: {e}")
        return None
