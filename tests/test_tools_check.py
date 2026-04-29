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
    }
    assert "haops_registry_query" in registries["tools_affected"]
    # Each registry reports a count when the file exists
    assert registries["tests"]["devices"]["count"] == 3
    assert registries["tests"]["areas"]["count"] == 2
    assert registries["tests"]["floors"]["count"] == 2
    assert registries["tests"]["config_entries"]["count"] == 3
