"""Tests for add-on tools (mocked Supervisor API)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from ha_ops_mcp.tools.addon import (
    haops_addon_info,
    haops_addon_list,
    haops_addon_restart,
)

MOCK_ADDONS = {
    "addons": [
        {
            "slug": "core_mariadb",
            "name": "MariaDB",
            "version": "2.7.1",
            "version_latest": "2.7.2",
            "state": "started",
            "update_available": True,
            "repository": "core",
        },
        {
            "slug": "core_ssh",
            "name": "SSH & Web Terminal",
            "version": "9.14.0",
            "version_latest": "9.14.0",
            "state": "stopped",
            "update_available": False,
            "repository": "core",
        },
    ]
}

MOCK_ADDON_INFO = {
    "slug": "core_mariadb",
    "name": "MariaDB",
    "version": "2.7.1",
    "version_latest": "2.7.2",
    "state": "started",
    "description": "MariaDB database server",
    "url": "https://github.com/home-assistant/addons",
    "auto_update": False,
    "boot": "auto",
    "options": {"databases": ["homeassistant"]},
    "network": {"3306/tcp": 3306},
    "host_network": False,
    "ingress": True,
    "ingress_url": "/api/hassio_ingress/abc123",
}

MOCK_STATS = {
    "cpu_percent": 2.5,
    "memory_usage": 256000000,
    "memory_limit": 1073741824,
    "memory_percent": 23.8,
    "network_rx": 1024000,
    "network_tx": 512000,
    "blk_read": 2048000,
    "blk_write": 1024000,
}


def _make_supervisor_get_mock(responses: dict):
    """Create a mock for _supervisor_get that returns by path."""
    async def mock_get(ctx, path):
        for key, value in responses.items():
            if key in path:
                return value
        return None
    return mock_get


@pytest.mark.asyncio
async def test_addon_list(ctx):
    with patch(
        "ha_ops_mcp.tools.addon._supervisor_get",
        new=_make_supervisor_get_mock({"/addons": MOCK_ADDONS}),
    ):
        result = await haops_addon_list(ctx)

    assert result["count"] == 2
    # Running addons should sort first
    assert result["addons"][0]["slug"] == "core_mariadb"
    assert result["addons"][0]["state"] == "started"


@pytest.mark.asyncio
async def test_addon_list_unavailable(ctx):
    with patch(
        "ha_ops_mcp.tools.addon._supervisor_get",
        new=_make_supervisor_get_mock({}),
    ):
        result = await haops_addon_list(ctx)
    assert "error" in result


@pytest.mark.asyncio
async def test_addon_info(ctx):
    with patch(
        "ha_ops_mcp.tools.addon._supervisor_get",
        new=_make_supervisor_get_mock({
            "/info": MOCK_ADDON_INFO,
            "/stats": MOCK_STATS,
        }),
    ):
        result = await haops_addon_info(ctx, slug="core_mariadb")

    assert result["name"] == "MariaDB"
    assert result["state"] == "started"
    assert result["stats"]["cpu_percent"] == 2.5
    assert result["options"] == {"databases": ["homeassistant"]}


@pytest.mark.asyncio
async def test_addon_info_not_found(ctx):
    with patch(
        "ha_ops_mcp.tools.addon._supervisor_get",
        new=_make_supervisor_get_mock({}),
    ):
        result = await haops_addon_info(ctx, slug="nonexistent")
    assert "error" in result


@pytest.mark.asyncio
async def test_addon_restart_preview(ctx):
    with patch(
        "ha_ops_mcp.tools.addon._supervisor_get",
        new=_make_supervisor_get_mock({"/info": MOCK_ADDON_INFO}),
    ):
        result = await haops_addon_restart(ctx, slug="core_mariadb")

    assert "token" in result
    assert "warning" in result
    assert "MariaDB" in result["name"]


@pytest.mark.asyncio
async def test_addon_restart_confirm(ctx):
    with patch(
        "ha_ops_mcp.tools.addon._supervisor_get",
        new=_make_supervisor_get_mock({"/info": MOCK_ADDON_INFO}),
    ):
        preview = await haops_addon_restart(ctx, slug="core_mariadb")

    with patch(
        "ha_ops_mcp.tools.addon._supervisor_get",
        new=_make_supervisor_get_mock({"/info": MOCK_ADDON_INFO}),
    ), patch(
        "ha_ops_mcp.tools.addon._supervisor_post",
        new=AsyncMock(return_value={}),
    ):
        result = await haops_addon_restart(
            ctx, slug="core_mariadb", confirm=True, token=preview["token"]
        )

    assert result["success"] is True


@pytest.mark.asyncio
async def test_addon_restart_not_found(ctx):
    with patch(
        "ha_ops_mcp.tools.addon._supervisor_get",
        new=_make_supervisor_get_mock({}),
    ):
        result = await haops_addon_restart(ctx, slug="nonexistent")
    assert "error" in result


def test_supervisor_token_prefers_env_over_config(monkeypatch):
    """Supervisor calls must use SUPERVISOR_TOKEN, not the HA-Core token.

    Regression: with a user LLAT in ha_token, reusing it for the Supervisor API
    returns 403 (a Core token is not a Supervisor token). v0.40.x fix.
    """
    from types import SimpleNamespace

    from ha_ops_mcp.tools.addon import _supervisor_token

    ctx = SimpleNamespace(
        config=SimpleNamespace(ha=SimpleNamespace(resolve_token=lambda: "LLAT"))
    )
    monkeypatch.setenv("SUPERVISOR_TOKEN", "SUPTOK")
    assert _supervisor_token(ctx) == "SUPTOK"  # env wins
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    assert _supervisor_token(ctx) == "LLAT"  # fallback for non-addon/dev


@pytest.mark.asyncio
async def test_addon_info_masks_credentials(ctx):
    info = {
        **MOCK_ADDON_INFO,
        "slug": "ha_ops_mcp",
        "options": {
            "ha_token": "eyJhbGciOiJIUzI1NiJ9.secret",
            "auth_token": "hunter2-long-token",
            "pin": "1234",
            "db_url": "mysql://homeassistant:s3cret@core-mariadb/homeassistant",
            "auth_issuer_url": "http://10.0.0.2:8901",
            "log_level": "info",
            "auth_mode": "token",
            "backup_max_age_days": 30,
            "mqtt": {"password": "nested-pw", "user": "ha"},
            "logins": [{"username": "me", "password": "list-pw"}],
            "api_key": "",
        },
        "schema": [
            {"name": "ha_token", "type": "string", "optional": True},
            {"name": "auth_token", "type": "string", "format": "password"},
            {"name": "pin", "type": "string", "format": "password"},
        ],
    }
    with patch(
        "ha_ops_mcp.tools.addon._supervisor_get",
        new=_make_supervisor_get_mock({"/info": info, "/stats": MOCK_STATS}),
    ):
        result = await haops_addon_info(ctx, slug="ha_ops_mcp")

    opts = result["options"]
    assert opts["ha_token"] == "***masked***"
    assert opts["auth_token"] == "***masked***"
    assert opts["pin"] == "***masked***"  # schema says password, name doesn't
    assert opts["db_url"] == (
        "mysql://homeassistant:***masked***@core-mariadb/homeassistant"
    )
    assert opts["mqtt"] == {"password": "***masked***", "user": "ha"}
    assert opts["logins"] == [{"username": "me", "password": "***masked***"}]
    # Not credentials: left alone
    assert opts["auth_issuer_url"] == "http://10.0.0.2:8901"
    assert opts["log_level"] == "info"
    assert opts["auth_mode"] == "token"
    assert opts["backup_max_age_days"] == 30
    assert opts["api_key"] == ""  # empty stays visible: "not set"
    # The raw secret appears nowhere in the response
    assert "s3cret" not in str(result)
    assert "hunter2" not in str(result)
