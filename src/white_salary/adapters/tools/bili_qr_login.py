"""
white_salary/adapters/tools/bili_qr_login.py

B站扫码登录 — 生成二维码让用户用B站APP扫码获取cookie。

流程：
  1. 调B站API获取二维码URL和key
  2. 生成二维码图片（base64）
  3. 轮询扫码状态（用户扫码后获取cookie）
  4. 写入config/bili.ini

B站扫码API：
  - 获取二维码: https://passport.bilibili.com/x/passport-login/web/qrcode/generate
  - 轮询状态: https://passport.bilibili.com/x/passport-login/web/qrcode/poll
"""

import asyncio
import configparser
import json
import time
from pathlib import Path
from typing import Optional

import aiohttp
from loguru import logger


async def generate_qr_code() -> dict:
    """
    生成B站登录二维码。

    Returns:
        {
            "success": bool,
            "qr_url": str,       # 二维码内容URL（用来生成二维码图片）
            "qr_key": str,       # 轮询用的key
            "qr_image": str,     # 二维码图片base64（如果有qrcode库）
        }
    """
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://passport.bilibili.com/x/passport-login/web/qrcode/generate",
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                data = await resp.json()
                if data.get("code") != 0:
                    return {"success": False, "message": f"获取二维码失败: {data.get('message')}"}

                qr_url = data["data"]["url"]
                qr_key = data["data"]["qrcode_key"]

                # 尝试生成二维码图片
                qr_image = ""
                try:
                    import qrcode
                    import io
                    import base64
                    qr = qrcode.QRCode(version=1, box_size=6, border=2)
                    qr.add_data(qr_url)
                    qr.make(fit=True)
                    img = qr.make_image(fill_color="black", back_color="white")
                    buf = io.BytesIO()
                    img.save(buf, format="PNG")
                    qr_image = base64.b64encode(buf.getvalue()).decode()
                except ImportError:
                    logger.debug("[BiliQR] qrcode库未安装，无法生成图片")

                return {
                    "success": True,
                    "qr_url": qr_url,
                    "qr_key": qr_key,
                    "qr_image": qr_image,
                    "message": "请用B站APP扫描二维码",
                }

    except Exception as e:
        return {"success": False, "message": f"生成二维码失败: {e}"}


async def poll_qr_status(qr_key: str) -> dict:
    """
    轮询扫码状态。

    Returns:
        {
            "success": bool,
            "status": "waiting" | "scanned" | "confirmed" | "expired",
            "message": str,
            "cookies": dict,  # 扫码成功后返回
        }
    """
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://passport.bilibili.com/x/passport-login/web/qrcode/poll",
                params={"qrcode_key": qr_key},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                data = await resp.json()
                code = data.get("data", {}).get("code", -1)

                if code == 86101:
                    return {"success": False, "status": "waiting", "message": "等待扫码..."}
                elif code == 86090:
                    return {"success": False, "status": "scanned", "message": "已扫码，等待确认..."}
                elif code == 86038:
                    return {"success": False, "status": "expired", "message": "二维码已过期，请重新获取"}
                elif code == 0:
                    # 扫码成功！从URL提取cookie
                    url = data["data"].get("url", "")
                    cookies = _extract_cookies_from_url(url)

                    # 也从response header获取set-cookie
                    for cookie_header in resp.headers.getall("Set-Cookie", []):
                        _parse_set_cookie(cookie_header, cookies)

                    if cookies.get("SESSDATA"):
                        # 保存到bili.ini
                        _save_cookies(cookies)
                        return {
                            "success": True,
                            "status": "confirmed",
                            "message": "登录成功！Cookie已保存",
                            "cookies": cookies,
                        }
                    else:
                        return {"success": False, "status": "confirmed",
                                "message": "登录成功但未获取到SESSDATA"}

                return {"success": False, "status": "unknown", "message": f"未知状态: {code}"}

    except Exception as e:
        return {"success": False, "status": "error", "message": f"轮询失败: {e}"}


async def qr_login_flow(timeout_seconds: int = 180) -> dict:
    """
    完整的扫码登录流程（生成+轮询）。

    Args:
        timeout_seconds: 超时时间（默认3分钟）

    Returns:
        {"success": bool, "message": str, "cookies": dict}
    """
    # 1. 生成二维码
    qr = await generate_qr_code()
    if not qr["success"]:
        return qr

    logger.info(f"[BiliQR] 二维码已生成，等待扫码...")
    qr_key = qr["qr_key"]

    # 2. 轮询（每2秒查一次）
    start = time.time()
    while time.time() - start < timeout_seconds:
        result = await poll_qr_status(qr_key)

        if result["status"] == "confirmed":
            if result["success"]:
                logger.info("[BiliQR] 扫码登录成功！")
            return result
        elif result["status"] == "expired":
            return result

        await asyncio.sleep(2)

    return {"success": False, "status": "timeout", "message": "扫码超时（3分钟）"}


def _extract_cookies_from_url(url: str) -> dict:
    """从B站回调URL提取cookie。"""
    cookies = {}
    try:
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(url)
        params = parse_qs(parsed.query)
        for key in ["SESSDATA", "bili_jct", "DedeUserID"]:
            if key in params:
                cookies[key] = params[key][0]
    except Exception:
        pass
    return cookies


def _parse_set_cookie(header: str, cookies: dict) -> None:
    """解析Set-Cookie头。"""
    try:
        parts = header.split(";")[0].split("=", 1)
        if len(parts) == 2:
            name, value = parts
            if name.strip() in ("SESSDATA", "bili_jct", "DedeUserID", "buvid3"):
                cookies[name.strip()] = value.strip()
    except Exception:
        pass


def _save_cookies(cookies: dict) -> None:
    """保存cookie到config/bili.ini。"""
    ini_path = Path("config/bili.ini")
    ini_path.parent.mkdir(parents=True, exist_ok=True)

    cp = configparser.RawConfigParser()
    cp.add_section("bili")
    cp.set("bili", "sessdata", cookies.get("SESSDATA", ""))
    cp.set("bili", "bili_jct", cookies.get("bili_jct", ""))
    cp.set("bili", "buvid3", cookies.get("buvid3", ""))
    cp.set("bili", "dedeuserid", cookies.get("DedeUserID", ""))
    cp.set("bili", "ac_time_value", "")

    with open(ini_path, "w", encoding="utf-8") as f:
        cp.write(f)

    logger.info("[BiliQR] Cookie已保存到config/bili.ini")


async def check_login_status() -> dict:
    """检查当前B站登录状态。"""
    try:
        import configparser
        cp = configparser.RawConfigParser()
        cp.read("config/bili.ini", encoding="utf-8")
        sessdata = cp.get("bili", "sessdata", fallback="")
        if not sessdata:
            return {"logged_in": False, "message": "未配置Cookie"}

        async with aiohttp.ClientSession(
            cookies={"SESSDATA": sessdata}
        ) as session:
            async with session.get(
                "https://api.bilibili.com/x/web-interface/nav",
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                data = await resp.json()
                if data.get("code") == 0:
                    uname = data["data"].get("uname", "")
                    mid = data["data"].get("mid", "")
                    return {
                        "logged_in": True,
                        "username": uname,
                        "uid": mid,
                        "message": f"已登录: {uname} (UID: {mid})",
                    }
                else:
                    return {
                        "logged_in": False,
                        "message": f"Cookie已过期: {data.get('message', '')}",
                    }
    except Exception as e:
        return {"logged_in": False, "message": f"检查失败: {e}"}
