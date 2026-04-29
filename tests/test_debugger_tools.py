"""Tests for the v0.7 debugger tools."""

from __future__ import annotations

import pytest

from ha_ops_mcp.tools.debugger import (
    haops_automation_trace,
    haops_entity_history,
    haops_logbook,
    haops_service_list,
    haops_template_render,
)

# ── _format_ts ────────────────────────────────────────────────────────


def test_format_ts_passes_through_iso():
    from ha_ops_mcp.tools.debugger import _format_ts
    assert _format_ts("2026-04-13T10:00:00+00:00") == "2026-04-13T10:00:00+00:00"


def test_format_ts_converts_epoch():
    from ha_ops_mcp.tools.debugger import _format_ts
    out = _format_ts("1712700000")
    assert out is not None
    assert out.startswith("2024-04-09T")


def test_format_ts_empty_returns_none():
    from ha_ops_mcp.tools.debugger import _format_ts
    assert _format_ts("") is None
    assert _format_ts(None) is None


# ── haops_entity_history ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_entity_history_calls_rest_with_entity_filter(ctx, mock_rest):
    mock_rest.get.side_effect = None
    mock_rest.get.return_value = [
        [
            {"entity_id": "sensor.temperature", "state": "22.5"},
            {"entity_id": "sensor.temperature", "state": "22.6"},
        ]
    ]
    res = await haops_entity_history(
        ctx,
        entity_id="sensor.temperature",
        start="2026-04-13T00:00:00+00:00",
    )
    assert "error" not in res
    assert res["entity_count"] == 1
    assert res["series"][0]["count"] == 2
    # The REST path should include filter_entity_id and minimal_response
    call_args = mock_rest.get.await_args
    assert "filter_entity_id=sensor.temperature" in call_args.args[0]
    assert "minimal_response" in call_args.args[0]


@pytest.mark.asyncio
async def test_entity_history_missing_start(ctx):
    res = await haops_entity_history(ctx, entity_id="sensor.x", start="")
    assert "error" in res


@pytest.mark.asyncio
async def test_entity_history_rest_error(ctx, mock_rest):
    from ha_ops_mcp.connections.rest import RestClientError
    mock_rest.get.side_effect = RestClientError(500, "boom")
    res = await haops_entity_history(
        ctx, entity_id="sensor.x", start="2026-04-13T00:00:00+00:00"
    )
    assert "error" in res


# ── haops_logbook ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_logbook_returns_entries(ctx, mock_rest):
    mock_rest.get.side_effect = None
    mock_rest.get.return_value = [
        {"when": "2026-04-13T10:00:00+00:00", "name": "Morning Lights", "domain": "automation"},
    ]
    res = await haops_logbook(ctx, start="2026-04-13T00:00:00+00:00")
    assert res["count"] == 1


@pytest.mark.asyncio
async def test_logbook_with_entity_filter(ctx, mock_rest):
    mock_rest.get.side_effect = None
    mock_rest.get.return_value = []
    await haops_logbook(
        ctx,
        start="2026-04-13T00:00:00+00:00",
        entity_id="light.living_room",
    )
    call_path = mock_rest.get.await_args.args[0]
    assert "entity=light.living_room" in call_path


@pytest.mark.asyncio
async def test_logbook_end_offset_is_url_encoded(ctx, mock_rest):
    """Regression: `+` in ISO offset must be percent-encoded in query string,
    otherwise the web server decodes it to a space and HA rejects as
    'Invalid end_time'. `start` sits in the URL path where `+` is literal."""
    mock_rest.get.side_effect = None
    mock_rest.get.return_value = []
    await haops_logbook(
        ctx,
        start="2026-04-23T10:40:00+00:00",
        end="2026-04-23T15:15:00+00:00",
    )
    call_path = mock_rest.get.await_args.args[0]
    # `+` in offset must be encoded as %2B; colons may be encoded too (both valid per RFC 3986)
    assert "end_time=2026-04-23T15%3A15%3A00%2B00%3A00" in call_path
    # start stays in the path as-is
    assert "/api/logbook/2026-04-23T10:40:00+00:00" in call_path


@pytest.mark.asyncio
async def test_entity_history_end_offset_is_url_encoded(ctx, mock_rest):
    mock_rest.get.side_effect = None
    mock_rest.get.return_value = []
    await haops_entity_history(
        ctx,
        entity_id="sensor.temperature",
        start="2026-04-23T10:40:00+00:00",
        end="2026-04-23T15:15:00+00:00",
    )
    call_path = mock_rest.get.await_args.args[0]
    # `+` in offset must be encoded as %2B; colons may be encoded too (both valid per RFC 3986)
    assert "end_time=2026-04-23T15%3A15%3A00%2B00%3A00" in call_path


# ── haops_template_render ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_template_render_success(ctx, mock_rest):
    # HA returns text/plain — must use post_text, NOT post (which calls .json())
    mock_rest.post_text = type(mock_rest.post)()  # AsyncMock for the new method
    mock_rest.post_text.return_value = "22.5"
    res = await haops_template_render(
        ctx, template="{{ states('sensor.temperature') }}"
    )
    assert res["rendered"] == "22.5"
    # Regression: never call .json() on /api/template
    mock_rest.post_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_template_render_empty(ctx):
    res = await haops_template_render(ctx, template="")
    assert "error" in res


@pytest.mark.asyncio
async def test_template_render_with_variables(ctx, mock_rest):
    mock_rest.post_text = type(mock_rest.post)()
    mock_rest.post_text.return_value = "3"
    await haops_template_render(
        ctx, template="{{ x + 1 }}", variables={"x": 2}
    )
    call = mock_rest.post_text.await_args
    assert call.args[0] == "/api/template"
    assert call.args[1]["variables"] == {"x": 2}


# ── haops_service_list ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_service_list_ws_dict_shape(ctx, mock_ws):
    mock_ws.send_command.return_value = {
        "light": {
            "turn_on": {"name": "Turn on", "description": "...", "fields": {}},
            "turn_off": {"name": "Turn off", "description": "...", "fields": {}},
        },
        "switch": {"toggle": {"fields": {}}},
    }
    res = await haops_service_list(ctx)
    assert "light" in res["domains"]
    assert res["domain_count"] == 2


@pytest.mark.asyncio
async def test_service_list_domain_filter(ctx, mock_ws):
    mock_ws.send_command.return_value = {
        "light": {"turn_on": {}, "turn_off": {}},
        "switch": {"toggle": {}},
    }
    res = await haops_service_list(ctx, domain="light")
    assert res["domain"] == "light"
    assert set(res["services"].keys()) == {"turn_on", "turn_off"}


@pytest.mark.asyncio
async def test_service_list_ws_fails_rest_fallback(ctx, mock_ws, mock_rest):
    from ha_ops_mcp.connections.websocket import WebSocketError
    mock_ws.send_command.side_effect = WebSocketError("nope")
    mock_rest.get.side_effect = None
    mock_rest.get.return_value = [
        {"domain": "light", "services": {"turn_on": {}}},
    ]
    res = await haops_service_list(ctx, domain="light")
    assert res["domain"] == "light"
    assert "turn_on" in res["services"]


# ── haops_automation_trace ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_automation_trace_lists_runs(ctx, mock_ws):
    mock_ws.send_command.return_value = [
        {"run_id": "r1", "timestamp": {"start": "t1"}},
        {"run_id": "r2", "timestamp": {"start": "t2"}},
    ]
    res = await haops_automation_trace(ctx, automation_id="auto_lights")
    assert res["count"] == 2
    # Verify trace/list was called with correct args
    call = mock_ws.send_command.await_args
    assert call.args[0] == "trace/list"
    assert call.kwargs["domain"] == "automation"
    assert call.kwargs["item_id"] == "auto_lights"


@pytest.mark.asyncio
async def test_automation_trace_get_specific_run(ctx, mock_ws):
    mock_ws.send_command.return_value = {"trace": {"actions": []}}
    res = await haops_automation_trace(
        ctx, automation_id="auto_lights", run_id="r1"
    )
    assert "trace" in res
    assert res["run_id"] == "r1"
    call = mock_ws.send_command.await_args
    assert call.args[0] == "trace/get"


@pytest.mark.asyncio
async def test_automation_trace_missing_id(ctx):
    res = await haops_automation_trace(ctx, automation_id="")
    assert "error" in res
