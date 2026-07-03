"""
white_salary/adapters/tools/bili_cookie_reader.py

B站Cookie自动读取 — 从Chrome/Edge浏览器自动获取B站登录cookie。

原理：
  - Chrome/Edge的cookie存在SQLite数据库里
  - Windows路径：%LOCALAPPDATA%/Google/Chrome/User Data/Default/Cookies
  - 读取.bilibili.com域名的SESSDATA/bili_jct/buvid3/DedeUserID
  - 自动写入config/bili.ini

注意：Chrome 80+的cookie是AES加密的，需要解密。
"""

import json
import os
import shutil
import sqlite3
import configparser
from pathlib import Path
from typing import Optional

from loguru import logger


def _get_browser_cookie_paths() -> list[Path]:
    """获取浏览器cookie路径（支持Windows和WSL）。"""
    paths = []
    localappdata = os.environ.get("LOCALAPPDATA", "")

    if localappdata:
        # 原生Windows
        base = Path(localappdata)

        # 便携版Chrome（优先检查）
        portable_chrome = Path("D:/谷歌浏览器/Chrome/Data/Default")
        if portable_chrome.exists():
            paths.extend([
                portable_chrome / "Network/Cookies",
                portable_chrome / "Cookies",
            ])

        # 标准安装的Chrome/Edge
        paths.extend([
            base / "Google/Chrome/User Data/Default/Network/Cookies",
            base / "Google/Chrome/User Data/Default/Cookies",
            base / "Microsoft/Edge/User Data/Default/Network/Cookies",
            base / "Microsoft/Edge/User Data/Default/Cookies",
        ])
    else:
        # WSL环境：通过/mnt/访问Windows文件系统

        # 便携版Chrome（优先检查）
        portable_chrome = Path("/mnt/d/谷歌浏览器/Chrome/Data/Default")
        if portable_chrome.exists():
            paths.extend([
                portable_chrome / "Network/Cookies",
                portable_chrome / "Cookies",
            ])

        # 标准安装的Chrome/Edge
        for user_dir in Path("/mnt/c/Users").iterdir() if Path("/mnt/c/Users").exists() else []:
            if user_dir.name in ("Public", "Default", "All Users", "Default User"):
                continue
            if not user_dir.is_dir():
                continue
            appdata = user_dir / "AppData/Local"
            paths.extend([
                appdata / "Google/Chrome/User Data/Default/Network/Cookies",
                appdata / "Google/Chrome/User Data/Default/Cookies",
                appdata / "Microsoft/Edge/User Data/Default/Network/Cookies",
                appdata / "Microsoft/Edge/User Data/Default/Cookies",
            ])

    return paths


# Chrome/Edge cookie数据库路径
_CHROME_PATHS = []  # 动态生成
_EDGE_PATHS = []    # 不再用，统一在_get_browser_cookie_paths

# 需要的cookie名
_NEEDED_COOKIES = ["SESSDATA", "bili_jct", "buvid3", "DedeUserID"]


def read_bilibili_cookies_from_browser() -> dict[str, str]:
    """
    从Chrome/Edge读取B站cookie（支持Windows原生和WSL）。

    Returns:
        {"SESSDATA": "...", "bili_jct": "...", "buvid3": "...", "DedeUserID": "..."}
    """
    all_paths = _get_browser_cookie_paths()
    for db_path in all_paths:
        if db_path.exists():
            logger.debug(f"[BiliCookie] 尝试读取: {db_path}")
            cookies = _read_cookies_from_db(db_path)
            if cookies.get("SESSDATA"):
                logger.info(f"[BiliCookie] 从 {db_path} 读取成功")
                return cookies
            elif cookies:
                logger.debug(f"[BiliCookie] 读到{len(cookies)}个cookie但没有SESSDATA")
    return {}


def _read_cookies_from_db(db_path: Path) -> dict[str, str]:
    """从SQLite cookie数据库读取B站cookie。"""
    cookies = {}
    tmp_path = db_path.parent / "Cookies_tmp"

    try:
        # 复制数据库（Chrome运行时锁定原文件）
        shutil.copy2(str(db_path), str(tmp_path))

        conn = sqlite3.connect(str(tmp_path))
        cursor = conn.cursor()

        for name in _NEEDED_COOKIES:
            try:
                cursor.execute(
                    "SELECT value, encrypted_value FROM cookies "
                    "WHERE host_key LIKE '%bilibili.com' AND name = ?",
                    (name,)
                )
                row = cursor.fetchone()
                if row:
                    value = row[0]
                    if value:
                        cookies[name] = value
                    else:
                        # Chrome 80+ 加密的cookie
                        encrypted = row[1]
                        decrypted = _decrypt_chrome_cookie(encrypted)
                        if decrypted:
                            cookies[name] = decrypted
            except Exception:
                pass

        conn.close()
    except Exception as e:
        logger.debug(f"[BiliCookie] 读取失败: {e}")
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass

    return cookies


def _decrypt_chrome_cookie(encrypted_value: bytes) -> str:
    """解密Chrome加密的cookie（Windows DPAPI）。"""
    if not encrypted_value:
        return ""

    try:
        # Chrome 80+ 用 AES-256-GCM 加密，前缀 v10/v20
        if encrypted_value[:3] in (b'v10', b'v20'):
            # 需要从Chrome Local State获取key
            local_state_path = Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/User Data/Local State"
            if not local_state_path.exists():
                # 试Edge
                local_state_path = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/Edge/User Data/Local State"

            if local_state_path.exists():
                import base64
                with open(local_state_path, encoding="utf-8") as f:
                    local_state = json.load(f)
                encrypted_key = base64.b64decode(local_state["os_crypt"]["encrypted_key"])
                encrypted_key = encrypted_key[5:]  # 去掉DPAPI前缀

                import ctypes
                import ctypes.wintypes

                class DATA_BLOB(ctypes.Structure):
                    _fields_ = [("cbData", ctypes.wintypes.DWORD),
                                ("pbData", ctypes.POINTER(ctypes.c_char))]

                p = ctypes.create_string_buffer(encrypted_key)
                blob_in = DATA_BLOB(len(encrypted_key), p)
                blob_out = DATA_BLOB()

                if ctypes.windll.crypt32.CryptUnprotectData(
                    ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
                ):
                    key = ctypes.string_at(blob_out.pbData, blob_out.cbData)

                    # AES-GCM解密
                    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
                    nonce = encrypted_value[3:15]
                    cipher_text = encrypted_value[15:]
                    aesgcm = AESGCM(key)
                    return aesgcm.decrypt(nonce, cipher_text, None).decode("utf-8")

        # 旧版Chrome用DPAPI直接加密
        import ctypes
        import ctypes.wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", ctypes.wintypes.DWORD),
                        ("pbData", ctypes.POINTER(ctypes.c_char))]

        p = ctypes.create_string_buffer(encrypted_value)
        blob_in = DATA_BLOB(len(encrypted_value), p)
        blob_out = DATA_BLOB()

        if ctypes.windll.crypt32.CryptUnprotectData(
            ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
        ):
            return ctypes.string_at(blob_out.pbData, blob_out.cbData).decode("utf-8")

    except ImportError:
        logger.debug("[BiliCookie] cryptography库未安装，无法解密Chrome 80+ cookie")
    except Exception as e:
        logger.debug(f"[BiliCookie] 解密失败: {e}")

    return ""


def auto_update_bili_ini() -> dict:
    """
    自动从浏览器读取B站cookie并更新config/bili.ini。

    Returns:
        {"success": bool, "message": str, "cookies": dict}
    """
    cookies = read_bilibili_cookies_from_browser()
    if not cookies.get("SESSDATA"):
        return {"success": False, "message": "未找到B站登录cookie，请先在浏览器登录bilibili.com"}

    # 写入bili.ini
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

    logger.info(f"[BiliCookie] 已自动更新config/bili.ini")
    return {"success": True, "message": "B站cookie已自动获取并保存", "cookies": cookies}
