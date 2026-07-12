"""Image generation provider fallback and model compatibility tests."""

from __future__ import annotations

from white_salary.adapters.tools import image_gen


async def test_cloud_fallback_runs_after_local_and_dmx_fail(monkeypatch) -> None:
    calls: list[tuple] = []

    monkeypatch.setattr(
        image_gen,
        "_load_style_config",
        lambda: {
            "providers": {
                "comfyui": {"enabled": True},
                "dmxapi": {"enabled": True},
                "siliconflow": {"enabled": True, "model": "Qwen/Qwen-Image"},
            }
        },
    )

    async def fake_local(prompt, size, is_portrait=False, startup_timeout=60):
        calls.append(("local", startup_timeout))
        return None

    async def fake_dmx(prompt, key, size):
        calls.append(("dmx", key, size))
        return None

    async def fake_sf(prompt, key, size, model=""):
        calls.append(("siliconflow", key, size, model))
        return "https://example.test/generated.png"

    async def fake_save(value):
        calls.append(("save", value))
        return "data/images/generated.png"

    monkeypatch.setattr(image_gen, "_try_comfyui", fake_local)
    monkeypatch.setattr(image_gen, "_try_dmxapi", fake_dmx)
    monkeypatch.setattr(image_gen, "_try_siliconflow", fake_sf)
    monkeypatch.setattr(image_gen, "_download_and_save", fake_save)

    result = await image_gen.generate_image(
        "一只猫",
        siliconflow_key="sk-sf",
        dmxapi_key="sk-dmx",
    )

    assert result == "data/images/generated.png"
    assert calls == [
        ("local", 15),
        ("dmx", "sk-dmx", "1024x1024"),
        ("siliconflow", "sk-sf", "1024x1024", "Qwen/Qwen-Image"),
        ("save", "https://example.test/generated.png"),
    ]


async def test_local_only_mode_keeps_full_startup_budget(monkeypatch) -> None:
    seen: list[int] = []
    monkeypatch.setattr(
        image_gen,
        "_load_style_config",
        lambda: {"providers": {"comfyui": {"enabled": True}}},
    )

    async def fake_local(prompt, size, is_portrait=False, startup_timeout=60):
        seen.append(startup_timeout)
        return None

    monkeypatch.setattr(image_gen, "_try_comfyui", fake_local)

    assert await image_gen.generate_image("一只猫") is None
    assert seen == [60]


def test_qwen_image_uses_supported_aspect_preserving_sizes() -> None:
    assert image_gen._siliconflow_image_size("Qwen/Qwen-Image", "1024x1024") == "1328x1328"
    assert image_gen._siliconflow_image_size("Qwen/Qwen-Image", "1024x576") == "1664x928"
    assert image_gen._siliconflow_image_size("Qwen/Qwen-Image", "576x1024") == "928x1664"
    assert image_gen._siliconflow_image_size("Kwai-Kolors/Kolors", "1024x1024") == "1024x1024"
