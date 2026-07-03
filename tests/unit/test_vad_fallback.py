"""VAD dependency fallback tests."""

import builtins

import pytest

from white_salary.adapters.vad.silero_vad import SileroVAD
from white_salary.core.interfaces.types import AudioData


@pytest.mark.asyncio
async def test_silero_vad_falls_back_when_torch_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Importing/constructing SileroVAD should not hard-require the large torch extra."""
    real_import = builtins.__import__

    def fake_import(name: str, *args, **kwargs):
        if name == "torch":
            raise ImportError("torch intentionally unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    vad = SileroVAD()
    audio = AudioData(samples=(b"\x00\x00" * 160), sample_rate=16000, channels=1)

    assert await vad.get_speech_probability(audio) == 0.0
