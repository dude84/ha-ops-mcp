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
