"""
audio_convert（音频格式探测 + ffmpeg 转码）的单元测试。

覆盖：
  - detect_audio_format 对 webm/wav/ogg/mp3/未知/空输入的探测
  - find_ffmpeg 查找顺序（which优先 → 硬编码路径 → None）
  - convert_to_wav 在 ffmpeg 不可用时返回 None（降级路径）
  - 本机 ffmpeg 可用时的真实转码冒烟测试（wav→wav、webm→wav、坏数据→None）

（2026-07-02 审计修复批2新增）
"""

import asyncio
import io
import wave

import pytest

from white_salary.utils import audio_convert
from white_salary.utils.audio_convert import (
    convert_to_wav,
    detect_audio_format,
    find_ffmpeg,
)

# ================================================================
# 格式探测
# ================================================================


def test_detect_webm_header():
    """WebM 的 EBML 头 0x1A45DFA3 应被识别为 webm。"""
    data = b"\x1a\x45\xdf\xa3" + b"\x00" * 32
    assert detect_audio_format(data) == "webm"


def test_detect_wav_header():
    """RIFF....WAVE 应被识别为 wav。"""
    data = b"RIFF" + b"\x24\x00\x00\x00" + b"WAVE" + b"fmt " + b"\x00" * 16
    assert detect_audio_format(data) == "wav"


def test_detect_riff_but_not_wave_is_unknown():
    """RIFF 开头但不是 WAVE 子类型（如 AVI）不应误判为 wav。"""
    data = b"RIFF" + b"\x24\x00\x00\x00" + b"AVI " + b"\x00" * 16
    assert detect_audio_format(data) == "unknown"


def test_detect_ogg_header():
    """OggS 头应被识别为 ogg。"""
    data = b"OggS" + b"\x00" * 32
    assert detect_audio_format(data) == "ogg"


def test_detect_mp3_id3_header():
    """ID3 标签头应被识别为 mp3。"""
    data = b"ID3" + b"\x00" * 32
    assert detect_audio_format(data) == "mp3"


def test_detect_mp3_frame_sync():
    """MPEG 帧同步头（0xFFEx）应被识别为 mp3。"""
    data = b"\xff\xfb\x90\x00" + b"\x00" * 32
    assert detect_audio_format(data) == "mp3"


def test_detect_unknown_bytes():
    """随机字节应返回 unknown。"""
    assert detect_audio_format(b"hello world, not audio") == "unknown"


def test_detect_empty_and_short_input():
    """空输入和不足4字节的输入应返回 unknown（不抛异常）。"""
    assert detect_audio_format(b"") == "unknown"
    assert detect_audio_format(b"\x1a\x45") == "unknown"


# ================================================================
# ffmpeg 查找与降级路径
# ================================================================


def test_find_ffmpeg_prefers_path(monkeypatch):
    """PATH 里有 ffmpeg 时应优先返回它（不碰硬编码路径）。"""
    monkeypatch.setattr(
        audio_convert.shutil, "which", lambda name: "C:/fake/ffmpeg.exe"
    )
    assert find_ffmpeg() == "C:/fake/ffmpeg.exe"


def test_find_ffmpeg_none_when_nothing_available(monkeypatch):
    """PATH 和硬编码路径都没有时应返回 None。"""
    monkeypatch.setattr(audio_convert.shutil, "which", lambda name: None)
    monkeypatch.setattr(audio_convert, "_KNOWN_FFMPEG_PATHS", [])
    assert find_ffmpeg() is None


async def test_convert_returns_none_without_ffmpeg(monkeypatch):
    """ffmpeg 完全不可用时 convert_to_wav 应返回 None（降级，不抛异常）。"""
    monkeypatch.setattr(audio_convert, "find_ffmpeg", lambda: None)
    result = await convert_to_wav(b"\x1a\x45\xdf\xa3" + b"\x00" * 100)
    assert result is None


async def test_convert_empty_input_returns_none():
    """空字节输入应直接返回 None，不启动子进程。"""
    result = await convert_to_wav(b"")
    assert result is None


# ================================================================
# 真实转码冒烟测试（本机 ffmpeg 可用才跑，不可用自动跳过）
# ================================================================

_FFMPEG_AVAILABLE = find_ffmpeg() is not None
_skip_no_ffmpeg = pytest.mark.skipif(
    not _FFMPEG_AVAILABLE, reason="本机没有可用的 ffmpeg，跳过真实转码测试"
)


def _make_test_wav(duration_seconds: float = 0.2, rate: int = 8000) -> bytes:
    """用标准库 wave 生成一段极短的静音 WAV（8kHz 单声道 16bit）。"""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"\x00\x00" * int(rate * duration_seconds))
    return buf.getvalue()


@_skip_no_ffmpeg
async def test_convert_wav_to_wav_smoke():
    """真实转码：8kHz WAV → 16kHz 单声道 WAV，输出应是合法 RIFF/WAVE。"""
    src = _make_test_wav()
    out = await convert_to_wav(src, sample_rate=16000, channels=1)
    assert out is not None
    assert detect_audio_format(out) == "wav"


@_skip_no_ffmpeg
async def test_convert_webm_to_wav_smoke():
    """
    真实转码冒烟：先用 ffmpeg 现场生成一段 WebM/Opus（与前端 MediaRecorder
    产物同容器同编码），再走 convert_to_wav，验证审计修复的核心路径。
    """
    ffmpeg = find_ffmpeg()
    # 生成 0.3 秒静音的 webm/opus 到 stdout
    proc = await asyncio.create_subprocess_exec(
        ffmpeg,
        "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
        "-t", "0.3",
        "-c:a", "libopus",
        "-f", "webm",
        "pipe:1",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    webm_bytes, _err = await asyncio.wait_for(proc.communicate(), timeout=30)
    if proc.returncode != 0 or not webm_bytes:
        pytest.skip("本机 ffmpeg 无法生成 webm/opus 测试样本（可能缺 libopus）")

    assert detect_audio_format(webm_bytes) == "webm"

    out = await convert_to_wav(webm_bytes, sample_rate=16000, channels=1)
    assert out is not None
    assert detect_audio_format(out) == "wav"


@_skip_no_ffmpeg
async def test_convert_garbage_returns_none():
    """喂无法解码的垃圾字节，ffmpeg 失败时应返回 None 而不是抛异常。"""
    out = await convert_to_wav(b"this is definitely not audio data" * 10)
    assert out is None
