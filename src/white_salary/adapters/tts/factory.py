"""
white_salary/adapters/tts/factory.py

TTS factory - creates the appropriate TTS adapter based on config.

Strategy:
  1. Try local GPT-SoVITS first (best quality)
  2. Fall back to SiliconFlow API (always available)
  3. Last resort: Edge TTS (free, no API key needed)
"""

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
    provider = config.provider.lower()

    if provider == "gpt_sovits":
        from white_salary.adapters.tts.gpt_sovits_adapter import GPTSoVITSAdapter

        logger.info("[TTS] Using local GPT-SoVITS")
        return GPTSoVITSAdapter(
            api_url="http://127.0.0.1:9880",
            ref_audio_path=config.voice,  # reuse voice field for ref audio path
        )

    elif provider == "siliconflow":
        from white_salary.adapters.tts.siliconflow_adapter import SiliconFlowTTSAdapter

        logger.info("[TTS] Using SiliconFlow CosyVoice2")
        # Default SiliconFlow TTS config from WhiteSalary-v2 1.9
        return SiliconFlowTTSAdapter(
            api_key="",
            model="FunAudioLLM/CosyVoice2-0.5B",
            voice=config.voice or "FunAudioLLM/CosyVoice2-0.5B:anna",
        )

    elif provider == "edge_tts":
        # Edge TTS will be implemented later as final fallback
        raise NotImplementedError("Edge TTS adapter not yet implemented")

    else:
        raise ValueError(f"Unknown TTS provider: {provider}")
