"""Tests for the v0.8 ergonomic wrappers."""

from __future__ import annotations

import pytest

from ha_ops_mcp.tools.ergonomics import (
    haops_automation_trigger,
    haops_entities_assign_area,
    haops_entity_customize,
    haops_integration_reload,
    haops_scene_activate,
    haops_script_run,
)

# ── haops_automation_trigger ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_automation_trigger_rejects_wrong_domain(ctx):
    res = await haops_automation_trigger(ctx, entity_id="light.living_room")
    assert "error" in res


@pytest.mark.asyncio
async def test_automation_trigger_calls_service(ctx, mock_rest):
    mock_rest.post.return_value = {}
    res = await haops_automation_trigger(ctx, entity_id="automation.morning")
    assert res["success"] is True
    call = mock_rest.post.await_args
    assert call.args[0] == "/api/services/automation/trigger"
    assert call.args[1] == {"entity_id": "automation.morning"}


# ── haops_script_run ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_script_run_calls_turn_on(ctx, mock_rest):
    mock_rest.post.return_value = {}
    res = await haops_script_run(ctx, entity_id="script.bedtime")
    assert res["success"] is True
    call = mock_rest.post.await_args
    assert call.args[0] == "/api/services/script/turn_on"


@pytest.mark.asyncio
async def test_script_run_rejects_wrong_domain(ctx):
    res = await haops_script_run(ctx, entity_id="automation.x")
    assert "error" in res


# ── haops_scene_activate ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scene_activate_calls_turn_on(ctx, mock_rest):
    mock_rest.post.return_value = {}
    res = await haops_scene_activate(ctx, entity_id="scene.movie")
    assert res["success"] is True
    call = mock_rest.post.await_args
    assert call.args[0] == "/api/services/scene/turn_on"


# ── haops_integration_reload ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_integration_reload_calls_ws(ctx, mock_ws):
    mock_ws.send_command.return_value = {}
    res = await haops_integration_reload(ctx, entry_id="mqtt_entry_1")
    assert res["success"] is True
    call = mock_ws.send_command.await_args
    assert call.args[0] == "config_entries/reload"
    assert call.kwargs["entry_id"] == "mqtt_entry_1"


@pytest.mark.asyncio
async def test_integration_reload_missing_entry(ctx):
    res = await haops_integration_reload(ctx, entry_id="")
    assert "error" in res


# ── haops_entities_assign_area ────────────────────────────────────────


@pytest.mark.asyncio
async def test_entities_assign_area_phase1_returns_token(ctx):
    res = await haops_entities_assign_area(
        ctx, entity_ids=["sensor.a", "sensor.b"], area_id="kitchen"
    )
    assert "token" in res
    assert res["preview"]["count"] == 2
    assert res["preview"]["new_area_id"] == "kitchen"


@pytest.mark.asyncio
async def test_entities_assign_area_phase2_applies(ctx, mock_ws):
    phase1 = await haops_entities_assign_area(
        ctx, entity_ids=["sensor.a", "sensor.b"], area_id="kitchen"
    )
    token = phase1["token"]
    mock_ws.send_command.return_value = {}
    res = await haops_entities_assign_area(
        ctx,
        entity_ids=[], area_id="", confirm=True, token=token,
    )
    assert res["success"] is True
    assert set(res["updated"]) == {"sensor.a", "sensor.b"}
    assert res["area_id"] == "kitchen"


@pytest.mark.asyncio
async def test_entities_assign_area_clear_with_empty_string(ctx):
    res = await haops_entities_assign_area(
        ctx, entity_ids=["sensor.a"], area_id=""
    )
    assert res["preview"]["new_area_id"] is None


@pytest.mark.asyncio
async def test_entities_assign_area_rejects_empty_list(ctx):
    res = await haops_entities_assign_area(ctx, entity_ids=[], area_id="x")
    assert "error" in res


# ── haops_entity_customize ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_entity_customize_phase1_with_changes(ctx):
    res = await haops_entity_customize(
        ctx,
        entity_id="sensor.temperature",
        name="Kitchen Temp",
        icon="mdi:thermometer",
    )
    assert "token" in res
    assert res["preview"]["changes"] == {
        "name": "Kitchen Temp",
        "icon": "mdi:thermometer",
    }


@pytest.mark.asyncio
async def test_entity_customize_no_changes_error(ctx):
    res = await haops_entity_customize(ctx, entity_id="sensor.x")
    assert "error" in res


@pytest.mark.asyncio
async def test_entity_customize_phase2_applies(ctx, mock_ws):
    phase1 = await haops_entity_customize(
        ctx, entity_id="sensor.temperature", name="Foo"
    )
    mock_ws.send_command.return_value = {}
    res = await haops_entity_customize(
        ctx,
        entity_id="",  # ignored in phase 2; token carries real value
        confirm=True,
        token=phase1["token"],
    )
    assert res["success"] is True
    assert res["changes"] == {"name": "Foo"}
    call = mock_ws.send_command.await_args
    assert call.args[0] == "config/entity_registry/update"
    assert call.kwargs["entity_id"] == "sensor.temperature"
    assert call.kwargs["name"] == "Foo"


@pytest.mark.asyncio
async def test_entity_customize_phase2_without_token(ctx):
    res = await haops_entity_customize(
        ctx, entity_id="sensor.x", name="Foo", confirm=True, token=""
    )
    # Hits the "No fields" check before the confirm branch when entity_id is
    # set but changes is empty — adjust test to provide fields
    # Actually "Foo" is truthy so changes is set; confirm=true + no token
    # should error.
    assert "error" in res
