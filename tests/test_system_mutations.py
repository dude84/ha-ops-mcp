"""Tests for system mutation tools — reload, restart, backup."""

from __future__ import annotations

import pytest

from ha_ops_mcp.tools.system import (
    _core_post_outcome,
    haops_system_backup,
    haops_system_core,
    haops_system_reload,
    haops_system_restart,
)


def test_core_post_outcome():
    """Classifier behind haops_system_core's success reporting."""
    # clean responses -> ok, not async
    assert _core_post_outcome(None) == (True, False)
    assert _core_post_outcome({}) == (True, False)
    assert _core_post_outcome({"slug": "x"}) == (True, False)
    # timeout (empty str) / connection drop -> ok + initiated async
    assert _core_post_outcome({"error": "Supervisor API unavailable: "}) == (True, True)
    assert _core_post_outcome(
        {"error": "Supervisor API unavailable: Server disconnected"}) == (True, True)
    # real HTTP status error -> failure
    assert _core_post_outcome({"error": "HTTP 401: forbidden"}) == (False, False)


@pytest.mark.asyncio
async def test_system_core_invalid_action(ctx):
    result = await haops_system_core(ctx, action="reboot")
    assert "error" in result


@pytest.mark.asyncio
async def test_system_core_preview(ctx):
    result = await haops_system_core(ctx, action="restart")
    assert "token" in result
    assert "warning" in result


@pytest.mark.asyncio
async def test_system_core_restart_timeout_is_initiated(ctx, monkeypatch):
    """Regression (v0.39.2 live test): Supervisor /core/restart BLOCKS until
    Core is back, so the POST times out (TimeoutError -> empty str ->
    'Supervisor API unavailable: '). That must read as success/initiated, not
    failure — the restart DID fire (verified live: Core 502'd then recovered)."""
    async def _post(ctx, path, data=None):
        return {"error": "Supervisor API unavailable: "}
    monkeypatch.setattr("ha_ops_mcp.tools.addon._supervisor_post", _post)

    preview = await haops_system_core(ctx, action="restart")
    result = await haops_system_core(
        ctx, action="restart", confirm=True, token=preview["token"])
    assert result["success"] is True
    assert result["status"] == "initiated"


@pytest.mark.asyncio
async def test_system_core_restart_http_error_is_failure(ctx, monkeypatch):
    """A genuine HTTP status error (auth/permission) must NOT be masked."""
    async def _post(ctx, path, data=None):
        return {"error": "HTTP 403: forbidden"}
    monkeypatch.setattr("ha_ops_mcp.tools.addon._supervisor_post", _post)

    preview = await haops_system_core(ctx, action="restart")
    result = await haops_system_core(
        ctx, action="restart", confirm=True, token=preview["token"])
    assert result["success"] is False
    assert result["status"] == "failed"


@pytest.mark.asyncio
async def test_system_core_stop_disables_watchdog_first(ctx, monkeypatch):
    """stop must POST watchdog=false before /core/stop."""
    calls: list[tuple[str, object]] = []

    async def _post(ctx, path, data=None):
        calls.append((path, data))
        return {}
    monkeypatch.setattr("ha_ops_mcp.tools.addon._supervisor_post", _post)

    preview = await haops_system_core(ctx, action="stop")
    await haops_system_core(
        ctx, action="stop", confirm=True, token=preview["token"])
    assert calls[0] == ("/core/options", {"watchdog": False})
    assert calls[1] == ("/core/stop", None)


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
