"""
white_salary/adapters/tts/factory.py

TTS factory - creates the appropriate TTS adapter based on config.

Strategy:
  1. Try local GPT-SoVITS first (best quality)
  2. Fall back to SiliconFlow API if a key is available
"""

import socket
from urllib.parse import urlparse

from loguru import logger

from white_salary.core.interfaces.tts import TTSInterface
from white_salary.infrastructure.config.models import TTSConfig


def create_tts(config: TTSConfig) -> TTSInterface:
    """
    Create a TTS adapter based on config.

    Args:
        config: TTS configuration

    Returns:
        A TTSInterface implementation
    """
    parsed = urlparse(config.local_api_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 9880

    local_available = False
    try:
        sock = socket.create_connection((host, port), timeout=2)
        sock.close()
        local_available = True
    except (ConnectionRefusedError, OSError, socket.timeout):
        local_available = False

    if local_available:
        from white_salary.adapters.tts.gpt_sovits_adapter import GPTSoVITSAdapter

        logger.info("[TTS] Using local GPT-SoVITS")
        return GPTSoVITSAdapter(
            api_url=config.local_api_url,
            ref_audio_path=config.ref_audio,
            ref_text=config.ref_text,
            speed=config.speed,
        )

    provider = config.fallback_provider.lower()
    if provider == "siliconflow":
        from white_salary.adapters.tts.siliconflow_adapter import SiliconFlowTTSAdapter
        from white_salary.adapters.tools.cloud_config import resolve_siliconflow_api_key

        logger.info("[TTS] Using SiliconFlow CosyVoice2")
        api_key = resolve_siliconflow_api_key(explicit=config.fallback_api_key)
        if not api_key:
            raise ValueError(
                "TTS fallback API key is missing. Configure tts.fallback_api_key "
                "or a SiliconFlow key in llm/llm_vision."
            )
        return SiliconFlowTTSAdapter(
            api_key=api_key,
            model=config.fallback_model,
            voice=config.fallback_voice,
        )

    raise ValueError(f"Unknown TTS fallback provider: {provider}")
