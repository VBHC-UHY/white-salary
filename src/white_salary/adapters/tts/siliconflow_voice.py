"""SiliconFlow custom voice management for cloud TTS fallback."""

from __future__ import annotations

import mimetypes
from pathlib import Path
import re
import wave
from typing import Any

import aiohttp


class SiliconFlowVoiceError(RuntimeError):
    """A user-actionable custom-voice API failure."""


def validate_custom_name(value: str) -> str:
    """Validate the stable name embedded in a SiliconFlow custom voice URI."""
    name = str(value or "").strip()
    if not name:
        raise SiliconFlowVoiceError("请填写云端音色名称")
    if len(name) > 64:
        raise SiliconFlowVoiceError("云端音色名称不能超过64个字符")
    if not re.fullmatch(r"[\w.-]+", name, flags=re.UNICODE):
        raise SiliconFlowVoiceError("云端音色名称只能包含文字、字母、数字、下划线、点或短横线")
    return name


def validate_reference_audio(path: str | Path) -> Path:
    """Validate a local reference clip before sending it to the cloud provider."""
    audio_path = Path(path).expanduser().resolve()
    if not audio_path.exists() or not audio_path.is_file():
        raise SiliconFlowVoiceError(f"参考音频不存在: {audio_path}")
    if audio_path.suffix.lower() not in {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac"}:
        raise SiliconFlowVoiceError("参考音频格式不支持，请使用 MP3、WAV、M4A、FLAC、OGG 或 AAC")
    if audio_path.stat().st_size > 20 * 1024 * 1024:
        raise SiliconFlowVoiceError("参考音频文件过大，请裁剪为30秒以内的清晰片段")
    if audio_path.suffix.lower() == ".wav":
        try:
            with wave.open(str(audio_path), "rb") as wav_file:
                duration = wav_file.getnframes() / max(wav_file.getframerate(), 1)
        except (wave.Error, OSError) as exc:
            raise SiliconFlowVoiceError(f"WAV参考音频无法读取: {exc}") from exc
        if duration > 30.0:
            raise SiliconFlowVoiceError(f"云端参考音频必须在30秒以内，当前约{duration:.1f}秒")
    return audio_path


class SiliconFlowVoiceClient:
    """Upload, list and delete user-defined SiliconFlow voice styles."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.siliconflow.cn/v1",
    ) -> None:
        self._api_key = str(api_key or "").strip()
        self._base_url = base_url.rstrip("/")
        if not self._api_key:
            raise SiliconFlowVoiceError("没有可用的硅基流动 API key")

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    async def _error(self, response: aiohttp.ClientResponse, operation: str) -> SiliconFlowVoiceError:
        body = await response.text()
        trace_id = response.headers.get("x-siliconcloud-trace-id", "")
        suffix = f"，trace={trace_id}" if trace_id else ""
        return SiliconFlowVoiceError(
            f"{operation}失败（HTTP {response.status}{suffix}）：{body[:500]}"
        )

    async def upload(
        self,
        *,
        audio_path: str | Path,
        text: str,
        custom_name: str,
        model: str = "FunAudioLLM/CosyVoice2-0.5B",
    ) -> dict[str, Any]:
        clip = validate_reference_audio(audio_path)
        reference_text = str(text or "").strip()
        if not reference_text:
            raise SiliconFlowVoiceError("请填写参考音频中实际说出的文字")
        name = validate_custom_name(custom_name)

        form = aiohttp.FormData()
        form.add_field("model", str(model or "FunAudioLLM/CosyVoice2-0.5B"))
        form.add_field("customName", name)
        form.add_field("text", reference_text)
        content_type = mimetypes.guess_type(clip.name)[0] or "application/octet-stream"
        with clip.open("rb") as audio_file:
            form.add_field(
                "file",
                audio_file,
                filename=clip.name,
                content_type=content_type,
            )
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self._base_url}/uploads/audio/voice",
                    headers=self._headers,
                    data=form,
                    timeout=aiohttp.ClientTimeout(total=90),
                ) as response:
                    if response.status != 200:
                        raise await self._error(response, "上传云端音色")
                    payload = await response.json(content_type=None)

        uri = str(payload.get("uri") or "").strip() if isinstance(payload, dict) else ""
        if not uri.startswith("speech:"):
            raise SiliconFlowVoiceError("云端音色上传成功但返回了无效音色ID")
        return {"uri": uri, "customName": name, "model": model}

    async def list(self) -> list[dict[str, Any]]:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                f"{self._base_url}/audio/voice/list",
                headers=self._headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                if response.status != 200:
                    raise await self._error(response, "读取云端音色列表")
                payload = await response.json(content_type=None)
        results = payload.get("results", []) if isinstance(payload, dict) else []
        return [item for item in results if isinstance(item, dict) and item.get("uri")]

    async def delete(self, uri: str) -> None:
        voice_uri = str(uri or "").strip()
        if not voice_uri.startswith("speech:"):
            raise SiliconFlowVoiceError("要删除的云端音色ID无效")
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{self._base_url}/audio/voice/deletions",
                headers={**self._headers, "Content-Type": "application/json"},
                json={"uri": voice_uri},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                if response.status not in (200, 204):
                    raise await self._error(response, "删除云端音色")
