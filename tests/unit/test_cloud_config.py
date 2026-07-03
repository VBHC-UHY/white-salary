"""Cloud API config fallback rules for public-repo installs."""

from pathlib import Path

import pytest

from white_salary.adapters.tools.cloud_config import (
    load_cloud_config,
    resolve_dmxapi_key,
    resolve_image_generation_keys,
    resolve_siliconflow_api_key,
    resolve_vision_channel,
)


PROJECT_ROOT = Path(__file__).parent.parent.parent


def _copy_default_config(root: Path) -> None:
    (root / "conf.default.yaml").write_text(
        (PROJECT_ROOT / "conf.default.yaml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )


def test_siliconflow_key_in_main_llm_lights_up_cloud_features(tmp_path: Path) -> None:
    """A minimal config with only llm.api_key should still enable SF cloud tools."""
    _copy_default_config(tmp_path)
    (tmp_path / "conf.yaml").write_text(
        "llm:\n"
        "  provider: siliconflow\n"
        "  api_key: sk-main\n"
        "  model: deepseek-ai/DeepSeek-V3.2\n"
        "  base_url: https://api.siliconflow.cn/v1\n",
        encoding="utf-8",
    )

    config = load_cloud_config(tmp_path)
    vision = resolve_vision_channel(config)
    sf_key, dmx_key = resolve_image_generation_keys(config)

    assert resolve_siliconflow_api_key(config) == "sk-main"
    assert vision.api_key == "sk-main"
    assert vision.model == "Qwen/Qwen3-VL-8B-Instruct"
    assert vision.base_url == "https://api.siliconflow.cn/v1"
    assert sf_key == "sk-main"
    assert dmx_key == ""


def test_dmxapi_key_is_not_confused_with_siliconflow(tmp_path: Path) -> None:
    """DMXAPI and SiliconFlow keys are resolved from matching providers only."""
    _copy_default_config(tmp_path)
    (tmp_path / "conf.yaml").write_text(
        "llm:\n"
        "  provider: dmxapi\n"
        "  api_key: sk-dmx\n"
        "  model: gpt-4o\n"
        "  base_url: https://www.dmxapi.cn/v1\n"
        "llm_vision:\n"
        "  api_key: sk-sf\n",
        encoding="utf-8",
    )

    config = load_cloud_config(tmp_path)

    assert resolve_dmxapi_key(config) == "sk-dmx"
    assert resolve_siliconflow_api_key(config) == "sk-sf"
    assert resolve_image_generation_keys(config) == ("sk-sf", "sk-dmx")


def test_tts_factory_consumes_current_tts_config_fields(monkeypatch) -> None:
    """create_tts should use fallback_* fields, not removed provider/voice fields."""
    from white_salary.adapters.tts import factory as tts_factory
    from white_salary.infrastructure.config.models import TTSConfig

    def no_local_tts(*args, **kwargs):
        raise OSError("closed")

    monkeypatch.setattr(tts_factory.socket, "create_connection", no_local_tts)

    adapter = tts_factory.create_tts(
        TTSConfig(
            fallback_api_key="sk-tts",
            fallback_model="FunAudioLLM/CosyVoice2-0.5B",
            fallback_voice="FunAudioLLM/CosyVoice2-0.5B:bella",
        )
    )

    assert adapter._api_key == "sk-tts"
    assert adapter._model == "FunAudioLLM/CosyVoice2-0.5B"
    assert adapter._voice == "FunAudioLLM/CosyVoice2-0.5B:bella"


def test_tts_factory_reports_missing_cloud_key(monkeypatch) -> None:
    """If neither local TTS nor a SF key exists, the failure is explicit."""
    from white_salary.adapters.tts import factory as tts_factory
    from white_salary.infrastructure.config.models import TTSConfig

    def no_local_tts(*args, **kwargs):
        raise OSError("closed")

    monkeypatch.setattr(tts_factory.socket, "create_connection", no_local_tts)

    with pytest.raises(ValueError, match="TTS fallback API key"):
        tts_factory.create_tts(TTSConfig())
