"""Tests for service call tool."""

from __future__ import annotations

from pathlib import Path

import pytest

from ha_ops_mcp.connections.rest import RestClientError
from ha_ops_mcp.tools.service import haops_service_call


@pytest.mark.asyncio
async def test_service_call_basic(ctx):
    result = await haops_service_call(
        ctx, domain="light", service="turn_on"
    )
    assert result["success"] is True
    assert result["service"] == "light.turn_on"


@pytest.mark.asyncio
async def test_service_call_with_target(ctx):
    result = await haops_service_call(
        ctx,
        domain="light",
        service="turn_on",
        target={"entity_id": "light.living_room"},
        data={"brightness": 128},
    )
    assert result["success"] is True


@pytest.mark.asyncio
async def test_service_call_with_data(ctx):
    result = await haops_service_call(
        ctx,
        domain="automation",
        service="trigger",
        data={"skip_condition": True},
    )
    assert result["success"] is True


@pytest.mark.asyncio
async def test_service_call_attaches_log_excerpt_on_failure(ctx, mock_rest):
    """On non-2xx response, recent matching log lines are attached.

    Models _gaps/session_gaps_2026-04-21.md §2: a failing zha.set_*
    service call that leaves the real stack trace in home-assistant.log
    used to require a follow-up haops_system_logs call. The error
    response now carries a log_excerpt field with the matches so
    one round trip is enough to diagnose.
    """
    # Seed the log file under config_root with realistic lines.
    log_file = Path(ctx.config.filesystem.config_root) / "home-assistant.log"
    log_file.write_text(
        "2026-04-22 10:00:00 INFO (MainThread) unrelated log line\n"
        "2026-04-22 10:00:05 ERROR (MainThread) [homeassistant.components.zha] "
        "Failed to set attribute: value: 1 attribute: 65293 cluster_id: 0\n"
        "2026-04-22 10:00:05 ERROR (MainThread) Traceback (most recent call last):\n"
        '  File "zha/zigbee/device.py", line 1288, in write_zigbee_attribute\n'
        "    raise ZHAException('write failed')\n"
        "zha.exceptions.ZHAException: write failed\n"
    )

    mock_rest.post.side_effect = RestClientError(500, "Internal Server Error")

    result = await haops_service_call(
        ctx,
        domain="zha",
        service="set_zigbee_cluster_attribute",
        data={"ieee": "00:00", "endpoint_id": 1, "cluster_id": 0},
    )

    assert "error" in result
    assert "500" in result["error"]
    assert "log_excerpt" in result
    excerpt = result["log_excerpt"]
    assert any("Failed to set attribute" in line for line in excerpt)
    assert any("ZHAException" in line for line in excerpt)
    # Unrelated line should not appear — tokens filter it out.
    assert not any("unrelated log line" in line for line in excerpt)


@pytest.mark.asyncio
async def test_service_call_failure_without_log_source(ctx, mock_rest, monkeypatch):
    """When no log source is reachable, the error still returns cleanly.

    Log enrichment is best-effort: the error payload must still surface
    even if fetch_log_text returns None.
    """
    # Ensure no log file exists; also stub fetch_log_text to return None
    # to cover the Supervisor/REST fallback path.
    log_file = Path(ctx.config.filesystem.config_root) / "home-assistant.log"
    if log_file.exists():
        log_file.unlink()

    async def _no_log(_ctx):
        return None

    monkeypatch.setattr("ha_ops_mcp.utils.logs.fetch_log_text", _no_log)

    mock_rest.post.side_effect = RestClientError(500, "Internal Server Error")
    result = await haops_service_call(ctx, domain="light", service="turn_on")

    assert "error" in result
    assert "log_excerpt" not in result
