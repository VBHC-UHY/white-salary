"""
white_salary/adapters/tts/gpt_sovits_adapter.py

Local GPT-SoVITS TTS adapter.

Calls the local GPT-SoVITS API (api_v2.py running on port 9880)
for highest quality voice synthesis with a custom-trained voice model.

This adapter requires:
  1. GPT-SoVITS installed locally. The default install directory is
     D:/AI_Tools/GPT-SoVITS, but it is configurable — see
     conf.yaml `external_tools.gpt_sovits_dir` (resolved via
     adapters/tools/external_paths.get_gpt_sovits_dir()). This adapter itself
     only needs the API URL; the directory is used by settings_api's
     start-tts / voice-clone endpoints.
  2. API server running (api_v2.py on port 9880)
  3. A properly trained voice model loaded

The voice model needs to be RETRAINED (the old jiaran model is broken).

2026-07-03 外部依赖优化（批8）：安装目录说明改为指向可配置项
（external_tools.gpt_sovits_dir），不再把 D:/AI_Tools/GPT-SoVITS 当作唯一硬编码路径。
"""

import aiohttp
from loguru import logger

from white_salary.core.exceptions import TTSError, TTSSynthesisError
from white_salary.core.interfaces.tts import TTSInterface
from white_salary.core.interfaces.types import AudioData


class GPTSoVITSAdapter(TTSInterface):
    """
    Local GPT-SoVITS TTS adapter.

    Connects to a locally running GPT-SoVITS API for natural-sounding
    voice synthesis using a custom-trained model.
    """

    def __init__(
        self,
        api_url: str = "http://127.0.0.1:9880",
        ref_audio_path: str = "",
        ref_text: str = "",
        ref_lang: str = "zh",
        speed: float = 1.0,
    ) -> None:
        """
        Initialize the GPT-SoVITS adapter.

        Args:
            api_url:        Local GPT-SoVITS API URL
            ref_audio_path: Path to reference audio file (for voice cloning)
            ref_text:       Text content of the reference audio
            ref_lang:       Language of the reference audio (zh/en/ja)
            speed:          Speech speed (1.0 = normal)
        """
        self._api_url = api_url.rstrip("/")
        self._ref_audio_path = ref_audio_path
        self._ref_text = ref_text
        self._ref_lang = ref_lang
        self._speed = speed

    def _build_payload(self, text: str, speed_multiplier: float = 1.0) -> dict:
        """
        2026-07-03 面板升级（批6）：请求体构建抽成独立方法（便于单测校验语速传递）。

        最终语速 = 配置基准语速(self._speed) × 情绪倍率(speed_multiplier)，
        并夹紧到 0.25~4.0 的安全区间（GPT-SoVITS api_v2 的合理取值范围）。

        Args:
            text:             要合成的文字（调用方保证非空）
            speed_multiplier: 情绪语速倍率（来自 emotion_tracker.get_tts_modifiers，
                              1.0=不调速，行为与旧版完全一致）

        Returns:
            POST /tts 的 JSON 请求体
        """
        effective_speed: float = max(0.25, min(4.0, self._speed * speed_multiplier))
        return {
            "text": text.strip(),
            "text_lang": "zh",
            "ref_audio_path": self._ref_audio_path,
            "prompt_text": self._ref_text,
            "prompt_lang": self._ref_lang,
            "text_split_method": "cut5",
            "batch_size": 1,
            "speed_factor": round(effective_speed, 3),
            "streaming_mode": False,
            "media_type": "wav",
        }

    async def synthesize(self, text: str) -> AudioData:
        """
        Convert text to speech using local GPT-SoVITS.

        Args:
            text: Text to synthesize

        Returns:
            AudioData containing the wav audio bytes
        """
        # 2026-07-03 面板升级（批6）：委托给带语速倍率的实现（倍率1.0=行为不变）
        return await self.synthesize_with_speed(text, speed_multiplier=1.0)

    async def synthesize_with_speed(
        self, text: str, speed_multiplier: float = 1.0,
    ) -> AudioData:
        """
        2026-07-03 面板升级（批6）：按情绪语速倍率合成语音。

        websocket_handler 合成每句语音时把 emotion_tracker.get_tts_modifiers()
        的 speed_factor 传进来，与配置基准语速（config.tts.speed）相乘后
        写入请求体的 speed_factor 字段——"表情动作"页情绪调速表从此真实生效
        （依据 docs/panel-audit-2026-07-03/panel-expressions.json）。

        Args:
            text:             要合成的文字
            speed_multiplier: 情绪语速倍率（1.0=正常语速）

        Returns:
            AudioData containing the wav audio bytes
        """
        if not text or not text.strip():
            logger.warning("[TTS-Local] Empty text, skipping")
            return AudioData(samples=b"", sample_rate=32000, dtype="wav")

        payload = self._build_payload(text, speed_multiplier)

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self._api_url}/tts",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=60),
                ) as resp:
                    if resp.status == 200:
                        audio_bytes = await resp.read()
                        logger.debug(
                            f"[TTS-Local] Synthesized {len(text)} chars -> "
                            f"{len(audio_bytes)} bytes"
                        )
                        return AudioData(
                            samples=audio_bytes,
                            sample_rate=32000,
                            dtype="wav",
                        )
                    else:
                        body = await resp.text()
                        raise TTSSynthesisError(
                            f"GPT-SoVITS TTS failed (HTTP {resp.status}): {body[:200]}"
                        )

        except aiohttp.ClientError as e:
            raise TTSSynthesisError(
                f"GPT-SoVITS connection error (is the server running?): {e}"
            ) from e

    async def synthesize_stream(self, text: str):
        """Stream mode - return full audio as one chunk."""
        audio = await self.synthesize(text)
        yield audio

    async def is_available(self) -> bool:
        """Check if GPT-SoVITS API is running."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self._api_url}/",
                    timeout=aiohttp.ClientTimeout(total=3),
                ) as resp:
                    # API returns 404 on root, but that means it's running
                    return resp.status in (200, 404)
        except Exception:
            return False

    async def switch_model(
        self,
        gpt_weights_path: str,
        sovits_weights_path: str,
    ) -> bool:
        """
        Switch the loaded voice model at runtime.

        Args:
            gpt_weights_path:    Path to GPT .ckpt file
            sovits_weights_path: Path to SoVITS .pth file

        Returns:
            True if both models loaded successfully
        """
        try:
            async with aiohttp.ClientSession() as session:
                # Switch GPT weights
                async with session.get(
                    f"{self._api_url}/set_gpt_weights",
                    params={"weights_path": gpt_weights_path},
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status != 200:
                        logger.error(f"[TTS-Local] Failed to load GPT weights: {await resp.text()}")
                        return False

                # Switch SoVITS weights
                async with session.get(
                    f"{self._api_url}/set_sovits_weights",
                    params={"weights_path": sovits_weights_path},
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status != 200:
                        logger.error(f"[TTS-Local] Failed to load SoVITS weights: {await resp.text()}")
                        return False

            logger.info("[TTS-Local] Voice model switched successfully")
            return True

        except Exception as e:
            logger.error(f"[TTS-Local] Model switch failed: {e}")
            return False
