"""Tests for the RestClient connection layer.

Focus: the auto-heal behaviour added after gap 2026-04-16 §1, where the
REST client went into ``RestClient not initialized`` mid-session and took
out every REST-backed tool until the addon was restarted.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ha_ops_mcp.connections.rest import RestClient


@pytest.mark.asyncio
async def test_ensure_session_creates_if_missing():
    """Fresh client (never entered) still produces a session on first call."""
    client = RestClient("http://example", "t")
    assert client._session is None

    fake_session = MagicMock()
    fake_session.closed = False
    with patch("aiohttp.ClientSession", return_value=fake_session) as ctor:
        session = await client._ensure_session()
        assert session is fake_session
        assert client._session is fake_session
        ctor.assert_called_once()


@pytest.mark.asyncio
async def test_ensure_session_reopens_if_closed():
    """If the existing session has been closed, _ensure_session replaces it.

    Regression target: aiohttp sessions can die mid-MCP-session (long idle,
    remote reset). Before the fix, the next call raised
    ``RestClient not initialized``. After, it transparently re-creates.
    """
    client = RestClient("http://example", "t")

    dead = MagicMock()
    dead.closed = True
    client._session = dead

    alive = MagicMock()
    alive.closed = False
    with patch("aiohttp.ClientSession", return_value=alive) as ctor:
        session = await client._ensure_session()
        assert session is alive
        assert client._session is alive
        ctor.assert_called_once()


@pytest.mark.asyncio
async def test_ensure_session_reuses_live_session():
    """A live session must not be replaced (otherwise we'd leak connections)."""
    client = RestClient("http://example", "t")

    live = MagicMock()
    live.closed = False
    client._session = live

    with patch("aiohttp.ClientSession") as ctor:
        session = await client._ensure_session()
        assert session is live
        ctor.assert_not_called()


@pytest.mark.asyncio
async def test_get_auto_heals_closed_session():
    """Request methods reopen the session implicitly, no caller change needed."""
    client = RestClient("http://example", "t")

    # Simulate the "mid-session death" state: session exists but is closed.
    dead = MagicMock()
    dead.closed = True
    client._session = dead

    # Build a replacement session whose .get() returns a minimal aiohttp-like
    # context manager that yields a 200/JSON response.
    async def fake_json() -> dict[str, str]:
        return {"ok": "yes"}

    resp = MagicMock()
    resp.status = 200
    resp.json = AsyncMock(return_value={"ok": "yes"})
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=None)

    new_session = MagicMock()
    new_session.closed = False
    new_session.get = MagicMock(return_value=resp)

    with patch("aiohttp.ClientSession", return_value=new_session):
        result = await client.get("/api/config")

    assert result == {"ok": "yes"}
    assert client._session is new_session
