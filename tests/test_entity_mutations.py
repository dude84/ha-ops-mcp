"""Tests for entity mutation tools — remove, toggle (enable/disable)."""

from __future__ import annotations

import pytest

from ha_ops_mcp.tools import entity as entity_mod
from ha_ops_mcp.tools.entity import haops_entity_remove, haops_entity_toggle


@pytest.fixture
def with_disabled_entity(monkeypatch):
    """Inject a disabled entity into the registry view without perturbing the
    shared fixture's entity count (many other tests assert exactly 3)."""
    disabled = {
        "entity_id": "sensor.disabled_diag",
        "name": "Disabled Diagnostic",
        "original_name": "Disabled Diagnostic",
        "platform": "zha",
        "area_id": None,
        "device_id": None,
        "disabled_by": "user",
    }

    async def _patched(ctx):
        base = [
            {"entity_id": "sensor.temperature", "name": "Temperature",
             "platform": "mqtt", "disabled_by": None},
            disabled,
        ]
        return base

    monkeypatch.setattr(entity_mod, "_get_entity_registry", _patched)
    return disabled


@pytest.mark.asyncio
async def test_entity_remove_preview(ctx):
    result = await haops_entity_remove(
        ctx, entity_ids=["sensor.temperature"]
    )
    assert "preview" in result
    assert "token" in result
    assert len(result["preview"]) == 1
    assert result["preview"][0]["entity_id"] == "sensor.temperature"


@pytest.mark.asyncio
async def test_entity_remove_not_found(ctx):
    result = await haops_entity_remove(
        ctx, entity_ids=["sensor.nonexistent"]
    )
    assert result["not_found"] == ["sensor.nonexistent"]
    assert len(result["preview"]) == 0


@pytest.mark.asyncio
async def test_entity_remove_confirm(ctx):
    preview = await haops_entity_remove(
        ctx, entity_ids=["sensor.temperature"]
    )
    result = await haops_entity_remove(
        ctx,
        entity_ids=["sensor.temperature"],
        confirm=True,
        token=preview["token"],
    )
    assert result["success"] is True
    assert "sensor.temperature" in result["removed"]


@pytest.mark.asyncio
async def test_entity_remove_empty(ctx):
    result = await haops_entity_remove(ctx, entity_ids=[])
    assert "error" in result


@pytest.mark.asyncio
async def test_entity_toggle_disable_preview(ctx):
    result = await haops_entity_toggle(
        ctx, entity_ids=["sensor.temperature"]
    )
    assert "preview" in result
    assert "token" in result
    assert len(result["preview"]) == 1


@pytest.mark.asyncio
async def test_entity_toggle_disable_confirm(ctx):
    preview = await haops_entity_toggle(
        ctx, entity_ids=["sensor.temperature"]
    )
    result = await haops_entity_toggle(
        ctx,
        entity_ids=["sensor.temperature"],
        confirm=True,
        token=preview["token"],
    )
    assert result["success"] is True
    assert "sensor.temperature" in result["disabled"]


@pytest.mark.asyncio
async def test_entity_toggle_already_disabled_skipped(ctx, with_disabled_entity):
    """Disabling an already-disabled entity lands in already_disabled, not preview."""
    result = await haops_entity_toggle(
        ctx, entity_ids=["sensor.disabled_diag"]
    )
    assert result["preview"] == []
    assert "sensor.disabled_diag" in result["already_disabled"]


@pytest.mark.asyncio
async def test_entity_toggle_enable_preview(ctx, with_disabled_entity):
    result = await haops_entity_toggle(
        ctx, entity_ids=["sensor.disabled_diag"], enable=True
    )
    assert len(result["preview"]) == 1
    assert result["preview"][0]["entity_id"] == "sensor.disabled_diag"
    assert "token" in result


@pytest.mark.asyncio
async def test_entity_toggle_enable_already_enabled_skipped(ctx, with_disabled_entity):
    """Enabling an already-enabled entity lands in already_enabled."""
    result = await haops_entity_toggle(
        ctx, entity_ids=["sensor.temperature"], enable=True
    )
    assert result["preview"] == []
    assert "sensor.temperature" in result["already_enabled"]


@pytest.mark.asyncio
async def test_entity_toggle_enable_confirm_sends_null_disabled_by(
    ctx, mock_ws, with_disabled_entity
):
    """Enable apply must send disabled_by=None over WS and report `enabled`."""
    preview = await haops_entity_toggle(
        ctx, entity_ids=["sensor.disabled_diag"], enable=True
    )
    mock_ws.send_command.reset_mock()
    result = await haops_entity_toggle(
        ctx,
        entity_ids=["sensor.disabled_diag"],
        enable=True,
        confirm=True,
        token=preview["token"],
    )
    assert result["success"] is True
    assert "sensor.disabled_diag" in result["enabled"]
    ws_calls = [c for c in mock_ws.send_command.await_args_list
                if c.args and c.args[0] == "config/entity_registry/update"]
    assert ws_calls
    assert ws_calls[0].kwargs.get("entity_id") == "sensor.disabled_diag"
    assert ws_calls[0].kwargs.get("disabled_by") is None


@pytest.mark.asyncio
async def test_entity_toggle_token_carries_intent(
    ctx, mock_ws, with_disabled_entity
):
    """A token minted by an enable preview must enable even if the apply
    call omits enable=true — intent is read from the token, not the default."""
    preview = await haops_entity_toggle(
        ctx, entity_ids=["sensor.disabled_diag"], enable=True
    )
    mock_ws.send_command.reset_mock()
    result = await haops_entity_toggle(
        ctx,
        entity_ids=["sensor.disabled_diag"],
        confirm=True,  # note: enable defaulted to False here
        token=preview["token"],
    )
    assert "enabled" in result
    ws_calls = [c for c in mock_ws.send_command.await_args_list
                if c.args and c.args[0] == "config/entity_registry/update"]
    assert ws_calls[0].kwargs.get("disabled_by") is None


@pytest.mark.asyncio
async def test_entity_toggle_empty(ctx):
    result = await haops_entity_toggle(ctx, entity_ids=[])
    assert "error" in result


@pytest.mark.asyncio
async def test_entity_toggle_uses_websocket_not_rest(ctx, mock_ws, mock_rest):
    """Regression v0.8.8: the disable apply step used POST
    /api/config/entity_registry/<id> which HA removed from the REST API.
    It now uses WS config/entity_registry/update."""
    preview = await haops_entity_toggle(
        ctx, entity_ids=["sensor.temperature"]
    )
    mock_ws.send_command.reset_mock()
    mock_rest.post.reset_mock()
    await haops_entity_toggle(
        ctx,
        entity_ids=["sensor.temperature"],
        confirm=True,
        token=preview["token"],
    )
    # WS should have been hit with the right shape
    ws_calls = [c for c in mock_ws.send_command.await_args_list
                if c.args and c.args[0] == "config/entity_registry/update"]
    assert ws_calls, "Expected WS config/entity_registry/update call"
    assert ws_calls[0].kwargs.get("entity_id") == "sensor.temperature"
    assert ws_calls[0].kwargs.get("disabled_by") == "user"
    # REST entity_registry endpoint must NOT have been called
    rest_post_calls = [c for c in mock_rest.post.await_args_list
                       if "/api/config/entity_registry/" in c.args[0]]
    assert not rest_post_calls


@pytest.mark.asyncio
async def test_entity_toggle_success_false_when_ws_fails(ctx, mock_ws):
    """Regression v0.8.8: the apply step previously returned `success: true`
    even when every per-entity call failed. Now reflects errors."""
    from ha_ops_mcp.connections.websocket import WebSocketError

    preview = await haops_entity_toggle(
        ctx, entity_ids=["sensor.temperature"]
    )
    mock_ws.send_command.side_effect = WebSocketError("HTTP 404")
    result = await haops_entity_toggle(
        ctx,
        entity_ids=["sensor.temperature"],
        confirm=True,
        token=preview["token"],
    )
    assert result["success"] is False
    assert result["disabled"] == []
    assert len(result["errors"]) == 1


@pytest.mark.asyncio
async def test_entity_remove_uses_websocket_not_rest(ctx, mock_ws, mock_rest):
    """Regression v0.8.8: the remove apply step used DELETE /api/config/
    entity_registry/<id> which HA removed. It now uses WS
    config/entity_registry/remove."""
    preview = await haops_entity_remove(
        ctx, entity_ids=["sensor.temperature"]
    )
    mock_ws.send_command.reset_mock()
    mock_rest.delete.reset_mock()
    await haops_entity_remove(
        ctx,
        entity_ids=["sensor.temperature"],
        confirm=True,
        token=preview["token"],
    )
    ws_calls = [c for c in mock_ws.send_command.await_args_list
                if c.args and c.args[0] == "config/entity_registry/remove"]
    assert ws_calls
    assert ws_calls[0].kwargs.get("entity_id") == "sensor.temperature"
    assert mock_rest.delete.await_count == 0
