"""Access-control tests for the local settings and plugin-management API."""

from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from white_salary.infrastructure.server.settings_api import create_settings_router


def _app(root: Path, token: str = "") -> FastAPI:
    root.mkdir(parents=True, exist_ok=True)
    (root / "conf.default.yaml").write_text(
        f"server:\n  management_token: {token!r}\n",
        encoding="utf-8",
    )
    app = FastAPI()
    app.include_router(create_settings_router(root))
    return app


@pytest.mark.asyncio
async def test_loopback_settings_access_needs_no_token(tmp_path: Path) -> None:
    transport = httpx.ASGITransport(
        app=_app(tmp_path / "project"),
        client=("127.0.0.1", 12345),
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://local") as client:
        response = await client.get("/api/settings/providers")

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_remote_settings_access_is_closed_without_token(tmp_path: Path) -> None:
    transport = httpx.ASGITransport(
        app=_app(tmp_path / "project"),
        client=("10.1.2.3", 12345),
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://server") as client:
        response = await client.get("/api/settings/providers")

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_remote_settings_access_accepts_configured_token(tmp_path: Path) -> None:
    transport = httpx.ASGITransport(
        app=_app(tmp_path / "project", token="correct-token"),
        client=("10.1.2.3", 12345),
    )
    async with httpx.AsyncClient(transport=transport, base_url="http://server") as client:
        denied = await client.get(
            "/api/settings/providers",
            headers={"X-White-Salary-Token": "wrong-token"},
        )
        allowed = await client.get(
            "/api/settings/providers",
            headers={"X-White-Salary-Token": "correct-token"},
        )

    assert denied.status_code == 403
    assert allowed.status_code == 200
