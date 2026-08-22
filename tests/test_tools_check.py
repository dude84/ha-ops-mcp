"""Tests for haops_tools_check — passive integration validator."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from ha_ops_mcp.tools.tools_check import haops_tools_check


@pytest.mark.asyncio
async def test_tools_check_reports_all_groups(ctx):
    """Returns status for each tool group + summary."""
    # ws mock needs to respond to commands
    ctx.ws.send_command = AsyncMock(return_value=[])

    result = await haops_tools_check(ctx)

    assert "rest_api" in result
    assert "websocket" in result
    assert "database" in result
    assert "filesystem" in result
    assert "registries" in result
    assert "config_flow" in result
    assert "supervisor" in result
    assert "shell" in result
    assert "summary" in result


@pytest.mark.asyncio
async def test_tools_check_database_skip_without_db(ctx):
    """Database check reports skip when no DB configured."""
    ctx.db = None
    ctx.ws.send_command = AsyncMock(return_value=[])

    result = await haops_tools_check(ctx)

    assert result["database"]["status"] == "skip"
    assert "haops_db_query" in result["database"]["tools_affected"]


@pytest.mark.asyncio
async def test_tools_check_supervisor_skip_standalone(ctx):
    """Supervisor check reports skip when not running as addon."""
    ctx.ws.send_command = AsyncMock(return_value=[])

    result = await haops_tools_check(ctx)

    # Standalone mode: supervisor API unreachable
    assert result["supervisor"]["status"] in ("skip", "fail")


@pytest.mark.asyncio
async def test_tools_check_summary_structure(ctx):
    """Summary includes overall verdict and broken tools list."""
    ctx.ws.send_command = AsyncMock(return_value=[])

    result = await haops_tools_check(ctx)

    summary = result["summary"]
    assert "overall" in summary
    assert summary["overall"] in (
        "all_pass", "pass_with_degradation", "partial_failure", "all_fail",
    )
    assert "broken_tools" in summary
    assert "groups_passing" in summary


@pytest.mark.asyncio
async def test_tools_check_rest_api_calls_real_endpoints(ctx):
    """REST API check tries /api/config, /api/states, etc."""
    ctx.ws.send_command = AsyncMock(return_value=[])

    result = await haops_tools_check(ctx)

    rest = result["rest_api"]
    assert "api_config" in rest["tests"]
    assert "api_states" in rest["tests"]
    # api_entity_registry and api_error_log removed — their endpoints are
    # handled via filesystem/WS instead (see tools_check filesystem group)


@pytest.mark.asyncio
async def test_tools_check_shell_runs_echo(ctx):
    """Shell check runs a real echo command."""
    ctx.ws.send_command = AsyncMock(return_value=[])

    result = await haops_tools_check(ctx)

    shell = result["shell"]
    assert shell["tests"]["echo"]["ok"] is True
    assert shell["tests"]["echo"]["output"] == "ha-ops-tools-check"


@pytest.mark.asyncio
async def test_tools_check_registries_group(ctx):
    """Registries group probes each .storage/core.* file."""
    ctx.ws.send_command = AsyncMock(return_value=[])

    result = await haops_tools_check(ctx)

    registries = result["registries"]
    assert registries["status"] == "pass"  # all fixtures present
    assert set(registries["tests"].keys()) == {
        "devices", "entities", "areas", "floors", "config_entries",
        # Live entry list — haops_device_remove needs supports_remove_device,
        # which only the running integration reports.
        "config_entries_live",
    }
    assert "haops_registry_query" in registries["tools_affected"]
    # Each registry reports a count when the file exists
    assert registries["tests"]["devices"]["count"] == 3
    assert registries["tests"]["areas"]["count"] == 2
    assert registries["tests"]["floors"]["count"] == 2
    assert registries["tests"]["config_entries"]["count"] == 3


@pytest.mark.asyncio
async def test_tools_check_config_flow_probes_without_creating_a_flow(ctx):
    """Proves both flow surfaces answer — without parking pending state.

    The creation route is probed with a handler that cannot exist; HA
    rejects it with a 404 before touching any integration. Starting a real
    flow would leave state in the user's HA, which a read-only check must
    never do.
    """
    from ha_ops_mcp.connections.rest import RestClientError
    from ha_ops_mcp.tools.config_entry import _PROBE_HANDLER

    posts: list[tuple] = []

    async def _post(path: str, data=None):
        posts.append((path, data))
        if path == "/api/config/config_entries/flow":
            raise RestClientError(404, '{"message": "Invalid handler specified"}')
        return {}

    async def _ws(command: str, **kwargs):
        if command == "config_entries/flow/progress":
            return []
        return []

    ctx.rest.post = AsyncMock(side_effect=_post)
    ctx.ws.send_command = AsyncMock(side_effect=_ws)

    result = await haops_tools_check(ctx)
    group = result["config_flow"]

    assert group["status"] == "pass"
    assert group["tests"]["flow_create_route"]["probe"] == "rejected_bogus_handler"
    assert group["tests"]["flow_progress"]["pending_flows"] == 0
    # Exactly one POST, and it used the un-creatable handler.
    flow_posts = [p for p in posts if p[0] == "/api/config/config_entries/flow"]
    assert len(flow_posts) == 1
    assert flow_posts[0][1] == {"handler": _PROBE_HANDLER}


@pytest.mark.asyncio
async def test_tools_check_config_flow_fails_if_create_route_gone(ctx):
    """A moved/removed creation endpoint must be visible, not silent.

    v0.64.0 shipped a probe that did GET on the POST-only index and reported
    a false failure on every healthy instance (405). Pin the real shape.
    """
    from ha_ops_mcp.connections.rest import RestClientError

    async def _post(path: str, data=None):
        if path == "/api/config/config_entries/flow":
            raise RestClientError(405, "405: Method Not Allowed")
        return {}

    ctx.rest.post = AsyncMock(side_effect=_post)
    ctx.ws.send_command = AsyncMock(return_value=[])

    result = await haops_tools_check(ctx)

    assert result["config_flow"]["status"] == "fail"
    assert "cannot be created" in (
        result["config_flow"]["tests"]["flow_create_route"]["impact"]
    )
    assert "haops_integration_flow_step" in result["summary"]["broken_tools"]


@pytest.mark.asyncio
async def test_tools_check_config_flow_partial_when_only_listing_is_down(ctx):
    """Losing the WS listing costs a warning, not the ability to create."""
    from ha_ops_mcp.connections.rest import RestClientError
    from ha_ops_mcp.connections.websocket import WebSocketError

    async def _post(path: str, data=None):
        if path == "/api/config/config_entries/flow":
            raise RestClientError(404, "Invalid handler specified")
        return {}

    async def _ws(command: str, **kwargs):
        if command == "config_entries/flow/progress":
            raise WebSocketError("Unknown command")
        return []

    ctx.rest.post = AsyncMock(side_effect=_post)
    ctx.ws.send_command = AsyncMock(side_effect=_ws)

    result = await haops_tools_check(ctx)
    group = result["config_flow"]

    assert group["status"] == "partial"
    assert group["tests"]["flow_create_route"]["ok"] is True
    assert "Creation itself still works" in group["tests"]["flow_progress"]["impact"]


@pytest.mark.asyncio
async def test_tools_check_config_flow_fails_if_bogus_handler_accepted(ctx):
    """If HA accepts an impossible handler, the probe is no longer honest."""
    ctx.rest.post = AsyncMock(return_value={"type": "form", "flow_id": "x"})
    ctx.ws.send_command = AsyncMock(return_value=[])

    result = await haops_tools_check(ctx)

    assert result["config_flow"]["status"] == "fail"
    assert "accepted the bogus handler" in (
        result["config_flow"]["tests"]["flow_create_route"]["error"]
    )
