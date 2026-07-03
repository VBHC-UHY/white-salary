"""Cloud API configuration helpers used by optional media tools.

The desktop app allows users to paste a single SiliconFlow key during setup.
Several optional tools used to read only their own section from ``conf.yaml``;
when users kept a minimal config, chat worked but vision/TTS/image/video looked
unconfigured.  This module centralizes the "reuse a configured cloud key"
rules and always reads the merged project configuration when possible.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger


SILICONFLOW_BASE_URL = "https://api.siliconflow.cn/v1"
DMXAPI_BASE_URL = "https://www.dmxapi.cn/v1"


@dataclass(frozen=True)
class CloudChannel:
    provider: str = ""
    api_key: str = ""
    model: str = ""
    base_url: str = ""

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.model and self.base_url)


def project_root_path() -> Path:
    """Return the project root from this module's src-layout location."""
    return Path(__file__).resolve().parents[4]


def load_cloud_config(project_root: Path | None = None) -> Any:
    """
    Load merged app config, falling back to a raw YAML merge for lightweight tests.

    ``load_config`` is the real runtime path.  The raw fallback keeps small unit
    tests and partially installed environments from failing just because
    ``conf.default.yaml`` or optional dependencies are unavailable.
    """
    root = project_root or project_root_path()
    try:
        from white_salary.infrastructure.config import load_config

        return load_config(project_root=root)
    except Exception as exc:
        logger.debug(f"[CloudConfig] load_config fallback: {exc}")

    try:
        import yaml
        from white_salary.infrastructure.config.loader import _deep_merge

        data: dict[str, Any] = {}
        default_path = root / "conf.default.yaml"
        user_path = root / "conf.yaml"
        if default_path.exists():
            data = yaml.safe_load(default_path.read_text(encoding="utf-8")) or {}
        if user_path.exists():
            user_data = yaml.safe_load(user_path.read_text(encoding="utf-8")) or {}
            data = _deep_merge(data, user_data)
        return data
    except Exception as exc:
        logger.debug(f"[CloudConfig] raw YAML fallback failed: {exc}")
        return {}


def _section(config: Any, name: str) -> Any:
    if isinstance(config, dict):
        return config.get(name) or {}
    return getattr(config, name, None)


def _field(section: Any, name: str) -> str:
    if section is None:
        return ""
    if isinstance(section, dict):
        return str(section.get(name) or "")
    return str(getattr(section, name, "") or "")


def _provider_preset(provider: str) -> dict[str, str]:
    try:
        from white_salary.adapters.llm.factory import PRESET_PROVIDERS

        return PRESET_PROVIDERS.get(provider.lower(), {})
    except Exception:
        return {}


def _channel(config: Any, name: str) -> CloudChannel:
    section = _section(config, name)
    provider = _field(section, "provider")
    base_url = _field(section, "base_url")
    if not base_url and provider:
        base_url = _provider_preset(provider).get("base_url", "")
    model = _field(section, "model")
    if not model and provider:
        model = _provider_preset(provider).get("default_model", "")
    return CloudChannel(
        provider=provider,
        api_key=_field(section, "api_key"),
        model=model,
        base_url=base_url,
    )


def _is_siliconflow(channel: CloudChannel) -> bool:
    return (
        "siliconflow" in channel.provider.lower()
        or "siliconflow.cn" in channel.base_url.lower()
    )


def _is_dmxapi(channel: CloudChannel) -> bool:
    return "dmxapi" in channel.provider.lower() or "dmxapi.cn" in channel.base_url.lower()


def resolve_siliconflow_api_key(config: Any | None = None, explicit: str = "") -> str:
    """
    Resolve a SiliconFlow key from explicit config or any configured LLM channel.

    This is the "one key lights up chat/vision/speech/media" rule.  The main
    ``llm`` channel is included so users who only filled the setup wizard's main
    section still get cloud vision/TTS/ASR/media fallbacks.
    """
    explicit = (explicit or "").strip()
    if explicit:
        return explicit

    cfg = config if config is not None else load_cloud_config()
    for name in (
        "llm_vision",
        "llm_postprocess",
        "llm_memory",
        "llm_emotion",
        "llm_background",
        "llm",
    ):
        channel = _channel(cfg, name)
        if _is_siliconflow(channel) and channel.api_key:
            return channel.api_key
    return ""


def resolve_dmxapi_key(config: Any | None = None, explicit: str = "") -> str:
    """Resolve a DMXAPI key only from channels that actually point to DMXAPI."""
    explicit = (explicit or "").strip()
    if explicit:
        return explicit

    cfg = config if config is not None else load_cloud_config()
    for name in ("llm", "llm_postprocess", "llm_background"):
        channel = _channel(cfg, name)
        if _is_dmxapi(channel) and channel.api_key:
            return channel.api_key
    return ""


def resolve_vision_channel(config: Any | None = None) -> CloudChannel:
    """
    Return the effective vision channel, reusing a SiliconFlow key when safe.

    The model/base_url still come from ``llm_vision`` (or its defaults), so the
    main chat model is never accidentally used as a vision model.
    """
    cfg = config if config is not None else load_cloud_config()
    vision = _channel(cfg, "llm_vision")
    api_key = vision.api_key
    if not api_key and _is_siliconflow(vision):
        api_key = resolve_siliconflow_api_key(cfg)
    return CloudChannel(
        provider=vision.provider or "siliconflow",
        api_key=api_key,
        model=vision.model,
        base_url=vision.base_url or SILICONFLOW_BASE_URL,
    )


def resolve_image_generation_keys(config: Any | None = None) -> tuple[str, str]:
    """Return ``(siliconflow_key, dmxapi_key)`` for image/video cloud fallbacks."""
    cfg = config if config is not None else load_cloud_config()
    return resolve_siliconflow_api_key(cfg), resolve_dmxapi_key(cfg)
