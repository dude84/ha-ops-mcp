"""Tests for static Bearer token auth (auth_mode: token, default since v0.62.0)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ha_ops_mcp.auth.static_token import (
    StaticTokenMiddleware,
    resolve_static_token,
)
from ha_ops_mcp.config import load_config

# ---------------------------------------------------------------- resolve

def test_configured_token_wins_and_is_not_persisted(tmp_path: Path) -> None:
    token, source = resolve_static_token(tmp_path, "my-secret-token")
    assert token == "my-secret-token"
    assert source == "configured"
    assert not (tmp_path / "static_token").exists()


def test_generated_token_persists_and_is_reused(tmp_path: Path) -> None:
    token1, source1 = resolve_static_token(tmp_path, "")
    assert source1 == "generated"
    assert len(token1) >= 32
    assert (tmp_path / "static_token").read_text().strip() == token1
    # 0600 perms
    assert (tmp_path / "static_token").stat().st_mode & 0o777 == 0o600

    token2, source2 = resolve_static_token(tmp_path, "")
    assert token2 == token1
    assert source2 == "persisted"


def test_configured_token_is_stripped(tmp_path: Path) -> None:
    token, _ = resolve_static_token(tmp_path, "  spaced-token \n")
    assert token == "spaced-token"


# ---------------------------------------------------------------- middleware

class _App:
    """Terminal ASGI app recording whether it was reached."""

    def __init__(self) -> None:
        self.called = False

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        self.called = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


async def _run(
    mw: StaticTokenMiddleware,
    path: str,
    headers: list[tuple[bytes, bytes]] | None = None,
    scope_type: str = "http",
) -> tuple[int | None, bytes]:
    sent: list[dict[str, Any]] = []

    async def send(msg: dict[str, Any]) -> None:
        sent.append(msg)

    async def receive() -> dict[str, Any]:  # pragma: no cover - never awaited
        return {"type": "http.request"}

    scope = {"type": scope_type, "path": path, "headers": headers or []}
    await mw(scope, receive, send)
    status = next(
        (m["status"] for m in sent if m["type"] == "http.response.start"), None
    )
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, body


@pytest.mark.asyncio
async def test_missing_header_gets_401_with_hint() -> None:
    app = _App()
    mw = StaticTokenMiddleware(app, "sekrit")
    status, body = await _run(mw, "/mcp")
    assert status == 401
    assert not app.called
    payload = json.loads(body)
    assert payload["error"] == "unauthorized"
    assert "Bearer" in payload["hint"]


@pytest.mark.asyncio
async def test_wrong_token_gets_401() -> None:
    app = _App()
    mw = StaticTokenMiddleware(app, "sekrit")
    status, _ = await _run(
        mw, "/mcp", headers=[(b"authorization", b"Bearer wrong")]
    )
    assert status == 401
    assert not app.called


@pytest.mark.asyncio
async def test_correct_token_passes() -> None:
    app = _App()
    mw = StaticTokenMiddleware(app, "sekrit")
    status, body = await _run(
        mw, "/mcp", headers=[(b"authorization", b"Bearer sekrit")]
    )
    assert status == 200
    assert app.called
    assert body == b"ok"


@pytest.mark.asyncio
async def test_bearer_scheme_is_case_insensitive_and_token_stripped() -> None:
    app = _App()
    mw = StaticTokenMiddleware(app, "sekrit")
    status, _ = await _run(
        mw, "/mcp", headers=[(b"authorization", b"bearer sekrit ")]
    )
    assert status == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/ui", "/api/ui/timeline", "/api/ui/captures/abc"])
async def test_ui_paths_are_exempt(path: str) -> None:
    app = _App()
    mw = StaticTokenMiddleware(app, "sekrit")
    status, _ = await _run(mw, path)
    assert status == 200
    assert app.called


@pytest.mark.asyncio
async def test_ui_prefix_lookalike_is_not_exempt() -> None:
    """/api/ui-ish paths must not slip through the prefix check."""
    app = _App()
    mw = StaticTokenMiddleware(app, "sekrit")
    status, _ = await _run(mw, "/api/uix/steal")
    assert status == 401
    assert not app.called


@pytest.mark.asyncio
async def test_sse_and_messages_paths_are_protected() -> None:
    app = _App()
    mw = StaticTokenMiddleware(app, "sekrit")
    for path in ("/sse", "/messages/", "/mcp"):
        status, _ = await _run(mw, path)
        assert status == 401, path


@pytest.mark.asyncio
async def test_non_http_scopes_pass_through() -> None:
    app = _App()
    mw = StaticTokenMiddleware(app, "sekrit")
    await _run(mw, "/mcp", scope_type="lifespan")
    assert app.called


# ---------------------------------------------------------------- config

def test_auth_mode_defaults_to_token(tmp_path: Path) -> None:
    cfg = load_config(None)
    assert cfg.auth.mode == "token"
    assert cfg.auth.static_token == ""


def test_auth_mode_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HA_OPS_AUTH_MODE", "oauth")
    monkeypatch.setenv("HA_OPS_AUTH_TOKEN", "envtoken")
    cfg = load_config(None)
    assert cfg.auth.mode == "oauth"
    assert cfg.auth.static_token == "envtoken"
