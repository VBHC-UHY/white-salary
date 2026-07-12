"""Cloud custom-voice management and control-panel endpoint tests."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable
import wave

import pytest
import yaml

from white_salary.adapters.tts.siliconflow_voice import (
    SiliconFlowVoiceError,
    validate_custom_name,
    validate_reference_audio,
)
from white_salary.infrastructure.server.settings_api import create_settings_router


def _endpoint(router: Any, path: str, method: str) -> Callable:
    for route in router.routes:
        if route.path == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise AssertionError(f"route missing: {method} {path}")


def _make_project(tmp_path: Path) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "conf.default.yaml").write_text(
        yaml.safe_dump(
            {
                "tts": {
                    "fallback_provider": "siliconflow",
                    "fallback_model": "FunAudioLLM/CosyVoice2-0.5B",
                    "fallback_voice": "FunAudioLLM/CosyVoice2-0.5B:anna",
                }
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    (tmp_path / "conf.yaml").write_text(
        yaml.safe_dump(
            {
                "llm": {
                    "provider": "siliconflow",
                    "api_key": "sk-test",
                    "base_url": "https://api.siliconflow.cn/v1",
                }
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    return tmp_path


class FakeVoiceClient:
    instances: list["FakeVoiceClient"] = []

    def __init__(self, api_key: str, base_url: str = "") -> None:
        self.api_key = api_key
        self.upload_kwargs: dict[str, Any] = {}
        self.deleted: list[str] = []
        type(self).instances.append(self)

    async def upload(self, **kwargs):
        self.upload_kwargs = kwargs
        return {
            "uri": "speech:white_salary:voice-id",
            "customName": kwargs["custom_name"],
            "model": kwargs["model"],
        }

    async def list(self):
        return [{
            "uri": "speech:white_salary:voice-id",
            "customName": "white_salary",
            "model": "FunAudioLLM/CosyVoice2-0.5B",
        }]

    async def delete(self, uri: str):
        self.deleted.append(uri)


@pytest.fixture
def fake_client(monkeypatch):
    FakeVoiceClient.instances = []
    monkeypatch.setattr(
        "white_salary.adapters.tts.siliconflow_voice.SiliconFlowVoiceClient",
        FakeVoiceClient,
    )
    return FakeVoiceClient


async def test_upload_selects_returned_cloud_voice(
    tmp_path: Path,
    fake_client,
) -> None:
    project = _make_project(tmp_path)
    audio = project / "data" / "temp" / "reference.mp3"
    audio.parent.mkdir(parents=True)
    audio.write_bytes(b"mp3")
    router = create_settings_router(project)
    upload = _endpoint(router, "/api/settings/voice-clone/cloud/upload", "POST")

    result = await upload({
        "audio_path": str(audio),
        "text": "你好，我是白。",
        "custom_name": "white_salary",
    })

    assert result["ok"] is True
    assert fake_client.instances[0].api_key == "sk-test"
    saved = yaml.safe_load((project / "conf.yaml").read_text(encoding="utf-8"))
    assert saved["tts"]["fallback_voice"] == "speech:white_salary:voice-id"
    assert saved["tts"]["fallback_provider"] == "siliconflow"


async def test_cloud_list_and_use_endpoints(tmp_path: Path, fake_client) -> None:
    project = _make_project(tmp_path)
    router = create_settings_router(project)
    get_voices = _endpoint(router, "/api/settings/voice-clone/cloud", "GET")
    use_voice = _endpoint(router, "/api/settings/voice-clone/cloud/use", "POST")

    listing = await get_voices()
    selected = await use_voice({"uri": "speech:white_salary:voice-id"})

    assert listing["ok"] is True and len(listing["voices"]) == 1
    assert selected["ok"] is True
    saved = yaml.safe_load((project / "conf.yaml").read_text(encoding="utf-8"))
    assert saved["tts"]["fallback_voice"] == "speech:white_salary:voice-id"


async def test_upload_rejects_audio_outside_project(tmp_path: Path, fake_client) -> None:
    project = _make_project(tmp_path / "project")
    outside = tmp_path / "outside.mp3"
    outside.write_bytes(b"mp3")
    upload = _endpoint(
        create_settings_router(project),
        "/api/settings/voice-clone/cloud/upload",
        "POST",
    )

    result = await upload({
        "audio_path": str(outside),
        "text": "你好",
        "custom_name": "white_salary",
    })

    assert result["ok"] is False
    assert "项目目录内" in result["message"]
    assert fake_client.instances == []


def test_reference_wav_over_30_seconds_is_rejected(tmp_path: Path) -> None:
    audio = tmp_path / "too-long.wav"
    with wave.open(str(audio), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(8000)
        wav_file.writeframes(b"\0\0" * 8000 * 31)

    with pytest.raises(SiliconFlowVoiceError, match="30秒"):
        validate_reference_audio(audio)


def test_cloud_voice_name_rejects_shell_or_path_characters() -> None:
    assert validate_custom_name("white_salary-01") == "white_salary-01"
    with pytest.raises(SiliconFlowVoiceError):
        validate_custom_name("../../voice")


def test_control_panel_exposes_cloud_voice_workflow() -> None:
    root = Path(__file__).resolve().parents[2]
    html = (root / "frontend" / "settings.html").read_text(encoding="utf-8")
    js = (root / "frontend" / "js" / "settings.js").read_text(encoding="utf-8")

    assert 'id="vc-cloud-voice-list"' in html
    assert "uploadCloudVoice()" in html
    assert "/voice-clone/cloud/upload" in js
    assert "/voice-clone/cloud/use" in js
    assert "refreshCloudVoices" in js
