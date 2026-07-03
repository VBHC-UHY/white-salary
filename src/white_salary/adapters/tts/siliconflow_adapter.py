"""
white_salary/adapters/tts/siliconflow_adapter.py

SiliconFlow CosyVoice2 TTS adapter.

Uses the SiliconFlow API (OpenAI-compatible) to synthesize speech.
This is the temporary/fallback TTS - local GPT-SoVITS will be
the primary TTS once the model is properly trained.

API docs: https://docs.siliconflow.cn/api-reference/audio/create-speech
"""

import io

import aiohttp
from loguru import logger

from white_salary.core.exceptions import TTSError, TTSSynthesisError
from white_salary.core.interfaces.tts import TTSInterface
from white_salary.core.interfaces.types import AudioData


class SiliconFlowTTSAdapter(TTSInterface):
    """
    SiliconFlow CosyVoice2 TTS adapter.

    Calls the SiliconFlow cloud API to convert text to speech.
    Returns raw audio bytes (mp3 format).
    """

    def __init__(
        self,
        api_key: str,
        model: str = "FunAudioLLM/CosyVoice2-0.5B",
        voice: str = "FunAudioLLM/CosyVoice2-0.5B:anna",
        base_url: str = "https://api.siliconflow.cn/v1",
        speed: float = 1.0,
    ) -> None:
        """
        Initialize the SiliconFlow TTS adapter.

        Args:
            api_key:  SiliconFlow API key
            model:    TTS model name
            voice:    Voice ID (custom cloned voice or preset)
            base_url: API endpoint
            speed:    Playback speed (1.0 = normal)
        """
        self._api_key = api_key
        self._model = model
        self._voice = voice
        self._base_url = base_url.rstrip("/")
        self._speed = speed

    async def synthesize(self, text: str) -> AudioData:
        """
        Convert text to speech audio.

        Args:
            text: Text to synthesize

        Returns:
            AudioData containing the mp3 audio bytes
        """
        if not text or not text.strip():
            logger.warning("[TTS] Empty text, skipping synthesis")
            return AudioData(samples=b"", sample_rate=24000, dtype="mp3")

        # Normalize text: add ending punctuation if missing
        normalized = text.strip()
        if normalized and normalized[-1] not in ".,!?;:...~":
            normalized += "."

        url = f"{self._base_url}/audio/speech"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self._model,
            "input": normalized,
            "voice": self._voice,
            "response_format": "mp3",
            "speed": self._speed,
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status == 200:
                        audio_bytes = await resp.read()
                        logger.debug(
                            f"[TTS] Synthesized {len(normalized)} chars -> "
                            f"{len(audio_bytes)} bytes"
                        )
                        return AudioData(
                            samples=audio_bytes,
                            sample_rate=24000,
                            dtype="mp3",
                        )
                    else:
                        body = await resp.text()
                        raise TTSSynthesisError(
                            f"SiliconFlow TTS failed (HTTP {resp.status}): {body[:200]}",
                            details={"status": resp.status, "body": body[:500]},
                        )

        except aiohttp.ClientError as e:
            raise TTSSynthesisError(
                f"SiliconFlow TTS network error: {e}",
                details={"error": str(e)},
            ) from e

    async def synthesize_stream(self, text: str):
        """
        Stream synthesis - for SiliconFlow, we just return the full audio
        as a single chunk since the API doesn't support true streaming.
        """
        audio = await self.synthesize(text)
        yield audio

    async def is_available(self) -> bool:
        """Check if the TTS service is reachable."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self._base_url}/models",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    return resp.status == 200
        except Exception:
            return False
