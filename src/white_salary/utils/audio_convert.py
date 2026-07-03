"""
white_salary/utils/audio_convert.py

音频格式探测与转码工具。

2026-07-02 审计修复（批2）新增：
背景：桌面端前端用 MediaRecorder 录音，产出的是 WebM/Opus 字节流，
但后端此前把它硬标成 wav 上传 SiliconFlow ASR，导致语音识别大面积 HTTP 500
（2026-05-31 日志 68 次 500，语音输入事实上从未可用）。

本模块提供两个能力：
  1. detect_audio_format() — 按文件头（magic bytes）探测真实容器格式；
  2. convert_to_wav()      — 用 ffmpeg 子进程把 webm/ogg 等格式转成
                             16kHz 单声道 WAV 字节（stdin 进 stdout 出，不落盘）。

ffmpeg 查找顺序：显式配置（WS_FFMPEG_PATH / external_tools.ffmpeg_path）→
PATH 中的 ffmpeg → 都找不到返回 None，
由调用方降级处理（按真实容器格式直接上传）。
"""

import asyncio
import shutil
from pathlib import Path
from typing import Optional

from loguru import logger

# 已移除作者机器的固定安装路径；保留空列表只兼容旧测试/调用结构。
_KNOWN_FFMPEG_PATHS: list[Path] = []

# ffmpeg 转码超时（秒）——语音消息一般几秒到几十秒，30 秒足够
_FFMPEG_TIMEOUT_SECONDS: float = 30.0


def detect_audio_format(data: bytes) -> str:
    """
    按文件头探测音频容器格式。

    Args:
        data: 音频字节流（只看开头几个字节，不解码）

    Returns:
        "webm"    — EBML 头 0x1A45DFA3（MediaRecorder 的 audio/webm 输出）
        "wav"     — RIFF....WAVE
        "ogg"     — OggS（Opus/Vorbis 的 Ogg 容器）
        "mp3"     — ID3 标签头或 MPEG 帧同步头
        "unknown" — 无法识别
    """
    if not data or len(data) < 4:
        return "unknown"
    if data[:4] == b"\x1a\x45\xdf\xa3":
        return "webm"
    if data[:4] == b"RIFF" and len(data) >= 12 and data[8:12] == b"WAVE":
        return "wav"
    if data[:4] == b"OggS":
        return "ogg"
    if data[:3] == b"ID3" or (data[0] == 0xFF and (data[1] & 0xE0) == 0xE0):
        return "mp3"
    return "unknown"


def find_ffmpeg() -> Optional[str]:
    """
    查找可用的 ffmpeg 可执行文件。

    2026-07-03 外部依赖优化（批8）：在原有查找顺序之前先看"环境变量/配置显式指定"，
    使换机器时无需改源码即可指定 ffmpeg（云端为主、本地进阶可选的方向）：
        环境变量 WS_FFMPEG_PATH → conf.yaml external_tools.ffmpeg_path
        → PATH 中的 ffmpeg

    `_KNOWN_FFMPEG_PATHS` 现在默认为空，不再携带作者机器的固定路径。

    Returns:
        ffmpeg 的完整路径；找不到返回 None
    """
    # 环境变量 / 配置显式指定（新增能力，指向存在的文件才采用，否则继续向下回退）
    explicit = _explicit_ffmpeg_path()
    if explicit:
        return explicit
    ff = shutil.which("ffmpeg")
    if ff:
        return ff
    for p in _KNOWN_FFMPEG_PATHS:
        if p.exists():
            return str(p)
    return None


def _explicit_ffmpeg_path() -> Optional[str]:
    """
    2026-07-03 外部依赖优化（批8）：取环境变量或配置里显式指定的 ffmpeg 路径。

    仅当指向的文件确实存在时才返回（避免误配一个不存在的路径把探测卡死）；
    读取配置失败（脱离项目根等）时静默返回 None，回退到 PATH / 内置候选。

    Returns:
        存在的 ffmpeg 路径；无显式配置或配置路径不存在时返回 None
    """
    import os

    env_ff = os.environ.get("WS_FFMPEG_PATH", "").strip()
    if env_ff and Path(env_ff).exists():
        return env_ff
    try:
        from white_salary.adapters.tools.external_paths import _config_value

        cfg_ff = _config_value("ffmpeg_path")
        if cfg_ff and Path(cfg_ff).exists():
            return cfg_ff
    except Exception:
        pass
    return None


async def convert_to_wav(
    data: bytes,
    sample_rate: int = 16000,
    channels: int = 1,
) -> Optional[bytes]:
    """
    用 ffmpeg 子进程把任意容器格式的音频字节转成 WAV 字节（PCM s16le）。

    全程管道操作：stdin 喂原始字节，stdout 收 WAV 字节，不写临时文件。
    注意：ffmpeg 输出到管道时无法回填 WAV 头里的长度字段（会写成 0xFFFFFFFF），
    主流解码器（含云端 ASR）都能正常处理这种流式 WAV。

    Args:
        data:        原始音频字节（webm/ogg/mp3/wav 等 ffmpeg 认识的格式）
        sample_rate: 目标采样率，默认 16000（ASR 标准）
        channels:    目标声道数，默认 1（单声道）

    Returns:
        转码后的 WAV 字节；ffmpeg 不可用或转码失败返回 None（调用方降级）
    """
    if not data:
        return None

    ffmpeg = find_ffmpeg()
    if ffmpeg is None:
        logger.warning("[AudioConvert] 找不到 ffmpeg（未配置且 PATH 中也没有），无法转码")
        return None

    try:
        proc = await asyncio.create_subprocess_exec(
            ffmpeg,
            "-hide_banner",
            "-loglevel", "error",
            "-i", "pipe:0",          # 从 stdin 读入
            "-f", "wav",             # 输出 WAV 容器
            "-acodec", "pcm_s16le",  # 16bit PCM
            "-ar", str(sample_rate),
            "-ac", str(channels),
            "pipe:1",                # 写到 stdout
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=data),
                timeout=_FFMPEG_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            # 超时必须杀掉子进程，防止 ffmpeg 僵尸进程堆积
            proc.kill()
            await proc.wait()
            logger.warning(
                f"[AudioConvert] ffmpeg 转码超时（>{_FFMPEG_TIMEOUT_SECONDS}秒），已放弃"
            )
            return None

        if proc.returncode != 0 or not stdout:
            err_text = (stderr or b"").decode("utf-8", errors="replace")[:200]
            logger.warning(
                f"[AudioConvert] ffmpeg 转码失败 (exit={proc.returncode}): {err_text}"
            )
            return None

        logger.debug(
            f"[AudioConvert] 转码成功: {len(data)} bytes -> {len(stdout)} bytes WAV "
            f"({sample_rate}Hz {channels}ch)"
        )
        return stdout

    except Exception as e:
        logger.warning(f"[AudioConvert] ffmpeg 调用异常: {e}")
        return None
