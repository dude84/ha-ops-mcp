"""Tests for system mutation tools — reload, restart, backup."""

from __future__ import annotations

import pytest

from ha_ops_mcp.tools.system import (
    haops_system_backup,
    haops_system_reload,
    haops_system_restart,
)


@pytest.mark.asyncio
async def test_system_reload_automations(ctx):
    result = await haops_system_reload(ctx, target="automations")
    assert result["success"] is True
    assert result["service"] == "automation.reload"


@pytest.mark.asyncio
async def test_system_reload_core(ctx):
    result = await haops_system_reload(ctx, target="core")
    assert result["success"] is True
    assert result["service"] == "homeassistant.reload_core_config"


@pytest.mark.asyncio
async def test_system_reload_invalid_target(ctx):
    result = await haops_system_reload(ctx, target="nonexistent")
    assert "error" in result
    assert "valid_targets" in result


@pytest.mark.asyncio
async def test_system_restart_preview(ctx):
    result = await haops_system_restart(ctx)
    assert "token" in result
    assert "warning" in result


@pytest.mark.asyncio
async def test_system_restart_confirm(ctx):
    preview = await haops_system_restart(ctx)
    result = await haops_system_restart(
        ctx, confirm=True, token=preview["token"]
    )
    assert result["success"] is True


@pytest.mark.asyncio
async def test_system_restart_no_token(ctx):
    result = await haops_system_restart(ctx, confirm=True)
    assert "error" in result


@pytest.mark.asyncio
async def test_system_restart_treats_504_as_initiated(ctx, mock_rest):
    """A 504 on the restart call means HA started restarting — not a failure.

    Per _gaps/session_gaps_2026-04-21.md §3: HA's API goes unreachable
    because HA is shutting down. Returning an error misleads agents into
    retry-storming or aborting valid flows.
    """
    from ha_ops_mcp.connections.rest import RestClientError

    preview = await haops_system_restart(ctx)
    mock_rest.post.side_effect = RestClientError(504, "Gateway Timeout")

    result = await haops_system_restart(
        ctx, confirm=True, token=preview["token"]
    )
    assert result.get("status") == "initiated"
    assert "unreachable" in result["message"].lower()
    assert "error" not in result


@pytest.mark.asyncio
async def test_system_restart_treats_connection_drop_as_initiated(ctx, mock_rest):
    """Connection reset / server-disconnected during restart is also success."""
    import aiohttp

    preview = await haops_system_restart(ctx)
    mock_rest.post.side_effect = aiohttp.ServerDisconnectedError(
        "Server disconnected"
    )

    result = await haops_system_restart(
        ctx, confirm=True, token=preview["token"]
    )
    assert result.get("status") == "initiated"
    assert "error" not in result


@pytest.mark.asyncio
async def test_system_restart_401_is_real_failure(ctx, mock_rest):
    """Only 502/503/504 and connection drops are "restart in progress".

    A 401 means the token is wrong — not that HA is restarting. The tool
    must still surface that as a real error.
    """
    from ha_ops_mcp.connections.rest import RestClientError

    preview = await haops_system_restart(ctx)
    mock_rest.post.side_effect = RestClientError(401, "Unauthorized")

    result = await haops_system_restart(
        ctx, confirm=True, token=preview["token"]
    )
    assert "error" in result
    assert "401" in result["error"]


@pytest.mark.asyncio
async def test_system_backup_falls_back_to_core_when_supervisor_unavailable(ctx, mock_rest):
    """In tests, http://supervisor isn't reachable — _supervisor_post returns
    an error dict, and we should fall back to the Core REST service."""
    mock_rest.post.return_value = {"context": {"id": "abc"}}
    result = await haops_system_backup(ctx, name="test-backup")
    assert result["success"] is True
    assert result["via"].startswith("core/")


@pytest.mark.asyncio
async def test_system_backup_returns_failure_when_all_paths_fail(ctx, mock_rest):
    """Regression v0.8.8: tool previously returned success=true even when
    backup creation failed."""
    from ha_ops_mcp.connections.rest import RestClientError
    mock_rest.post.side_effect = RestClientError(400, "bad request")
    result = await haops_system_backup(ctx, name="test-backup")
    assert result["success"] is False
    assert "error" in result
    assert "core_error" in result
