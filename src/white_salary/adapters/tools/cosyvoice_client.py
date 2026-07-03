"""
white_salary/adapters/tools/cosyvoice_client.py

本地CosyVoice2 TTS客户端 — 调用本地API生成语音（无安全过滤）。

流程：
  1. 检测CosyVoice2是否在线（127.0.0.1:9881）
  2. 没在线就自动启动（start_cosyvoice.bat）
  3. 发送文本 → 接收WAV音频 → 保存到本地
"""

import subprocess
import time
from pathlib import Path
from typing import Optional

import aiohttp
from loguru import logger


COSYVOICE_URL = "http://127.0.0.1:9881"
# 2026-07-03 外部依赖优化（批8）：启动脚本路径改由 external_paths 统一解析
# （环境变量 WS_COSYVOICE_BAT → conf.yaml external_tools.cosyvoice_bat）。
# 不再在此写死；实际取用见 ensure_running()。
COSYVOICE_BAT: Path | None = None

_starting = False


async def is_online() -> bool:
    """检查CosyVoice2是否在线。"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{COSYVOICE_URL}/health",
                timeout=aiohttp.ClientTimeout(total=3),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("model_loaded", False)
    except Exception:
        pass
    return False


async def ensure_running(timeout: int = 120) -> bool:
    """确保CosyVoice2在运行。没运行就自动启动。"""
    global _starting
    import asyncio

    if await is_online():
        return True

    # 2026-07-03 外部依赖优化（批8）：启动脚本路径统一解析（环境变量→配置→内置默认）
    from white_salary.adapters.tools.external_paths import get_cosyvoice_bat
    try:
        cosyvoice_bat = get_cosyvoice_bat()
    except FileNotFoundError as exc:
        logger.debug(f"[CosyVoice2] {exc}")
        return False

    if not cosyvoice_bat.exists():
        logger.debug(f"[CosyVoice2] 启动脚本不存在: {cosyvoice_bat}")
        return False

    if _starting:
        start = time.time()
        while time.time() - start < timeout:
            if await is_online():
                return True
            await asyncio.sleep(3)
        return False

    try:
        _starting = True
        logger.info("[CosyVoice2] 自动启动中...")

        subprocess.Popen(
            str(cosyvoice_bat),
            cwd=str(cosyvoice_bat.parent),
            shell=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0)
                        | getattr(subprocess, 'DETACHED_PROCESS', 0),
        )

        start = time.time()
        while time.time() - start < timeout:
            if await is_online():
                logger.info(f"[CosyVoice2] 启动成功（{int(time.time()-start)}秒）")
                _starting = False
                return True
            await asyncio.sleep(3)

        logger.warning(f"[CosyVoice2] 启动超时（{timeout}秒）")
        _starting = False
        return False
    except Exception as e:
        logger.warning(f"[CosyVoice2] 启动失败: {e}")
        _starting = False
        return False


async def generate_speech(
    text: str,
    voice: str = "中文女",
    output_path: str = "",
) -> Optional[str]:
    """
    生成语音（无安全过滤）。

    Args:
        text: 要说的文本
        voice: 音色名（中文女/中文男/日语男等）
        output_path: 保存路径（空=自动生成）

    Returns:
        WAV文件路径
    """
    if not await is_online():
        if not await ensure_running(timeout=120):
            logger.warning("[CosyVoice2] 不可用")
            return None

    try:
        async with aiohttp.ClientSession() as session:
            payload = {"text": text, "voice": voice}
            async with session.post(
                f"{COSYVOICE_URL}/tts",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status == 200:
                    audio_bytes = await resp.read()
                    if not output_path:
                        save_dir = Path("data/audio")
                        save_dir.mkdir(parents=True, exist_ok=True)
                        output_path = str(save_dir / f"tts_{int(time.time())}.wav")

                    Path(output_path).write_bytes(audio_bytes)
                    logger.info(f"[CosyVoice2] 语音生成: {output_path} ({len(audio_bytes)//1024}KB)")
                    return output_path
                else:
                    body = await resp.text()
                    logger.warning(f"[CosyVoice2] 生成失败({resp.status}): {body[:100]}")
    except Exception as e:
        logger.warning(f"[CosyVoice2] 异常: {e}")
    return None


async def list_voices() -> list[str]:
    """获取可用音色列表。"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{COSYVOICE_URL}/voices",
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("voices", [])
    except Exception:
        pass
    return []
