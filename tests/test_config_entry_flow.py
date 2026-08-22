"""Tests for the config-entry flow tools (haops_integration_flow_*).

The gating split is the thing worth pinning: `start` and `abort` run
immediately, `step` is two-phase. If someone "tidies" that into uniform
confirm-everywhere, or drops the confirm on `step`, these fail.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from ha_ops_mcp.connections.rest import RestClientError
from ha_ops_mcp.tools.config_entry import (
    _FLOW_BASE,
    _normalise_step,
    haops_integration_flow_abort,
    haops_integration_flow_start,
    haops_integration_flow_step,
)

FORM_STEP: dict[str, Any] = {
    "type": "form",
    "flow_id": "abc123",
    "handler": "ac_controller",
    "step_id": "user",
    "data_schema": [
        {"name": "wrapped_entity", "required": True, "type": "string"},
        {"name": "slug", "required": True, "type": "string"},
        {"name": "comfort_offset", "required": False, "type": "float"},
    ],
    "errors": {},
    "last_step": True,
}

CREATED_STEP: dict[str, Any] = {
    "type": "create_entry",
    "flow_id": "abc123",
    "handler": "ac_controller",
    "title": "AC Bedroom",
    "result": "01JQ8ZK3F4XYZ",
}


# ── normalisation ─────────────────────────────────────────────────────


def test_normalise_form_lifts_schema_and_errors() -> None:
    out = _normalise_step(FORM_STEP)
    assert out["type"] == "form"
    assert out["step_id"] == "user"
    assert out["errors"] == {}
    assert out["data_schema"][0]["name"] == "wrapped_entity"


def test_normalise_create_entry_exposes_entry_id() -> None:
    """HA replaces `result` with the entry_id string — surface it as such."""
    out = _normalise_step(CREATED_STEP)
    assert out["type"] == "create_entry"
    assert out["entry_id"] == "01JQ8ZK3F4XYZ"
    assert out["title"] == "AC Bedroom"


def test_normalise_abort_exposes_reason() -> None:
    out = _normalise_step(
        {"type": "abort", "flow_id": "x", "reason": "already_configured"}
    )
    assert out["reason"] == "already_configured"


def test_normalise_unknown_type_keeps_raw_payload() -> None:
    """A future flow type must degrade to 'here is everything', not drop it."""
    out = _normalise_step({"type": "quantum_step", "flow_id": "x", "odd": 1})
    assert out["raw"]["odd"] == 1


# ── start ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_start_returns_schema_without_confirm(ctx: Any) -> None:
    ctx.ws.send_command = AsyncMock(return_value=[])
    ctx.rest.post = AsyncMock(return_value=FORM_STEP)

    result = await haops_integration_flow_start(ctx, domain="ac_controller")

    assert result["type"] == "form"
    assert result["flow_id"] == "abc123"
    assert [f["name"] for f in result["data_schema"]] == [
        "wrapped_entity", "slug", "comfort_offset",
    ]
    # No token: opening a flow creates nothing.
    assert "token" not in result
    ctx.rest.post.assert_awaited_once_with(
        _FLOW_BASE,
        {"handler": "ac_controller", "show_advanced_options": False},
    )


@pytest.mark.asyncio
async def test_start_reports_existing_pending_flows(ctx: Any) -> None:
    """The listing is WS (`config_entries/flow/progress`), NOT REST.

    `GET /api/config/config_entries/flow` answers 405 on HA 2026.8 — the
    REST index is POST-only. v0.64.0 used the GET and the feature was dead.
    """
    ctx.ws.send_command = AsyncMock(return_value=[
        {
            "flow_id": "old1",
            "handler": "ac_controller",
            "step_id": "user",
            "context": {"source": "user"},
        },
        {"flow_id": "other", "handler": "mqtt", "step_id": "user"},
    ])
    ctx.rest.post = AsyncMock(return_value=FORM_STEP)

    result = await haops_integration_flow_start(ctx, domain="ac_controller")

    # Filtered to this handler only.
    assert [f["flow_id"] for f in result["in_progress"]] == ["old1"]
    assert result["in_progress"][0]["source"] == "user"
    assert "haops_integration_flow_abort" in result["note"]
    ctx.ws.send_command.assert_awaited_once_with("config_entries/flow/progress")


@pytest.mark.asyncio
async def test_start_names_discovered_flows(ctx: Any) -> None:
    """A zeroconf/dhcp flow is only identifiable by its title placeholder."""
    ctx.ws.send_command = AsyncMock(return_value=[{
        "flow_id": "disco1",
        "handler": "espsomfy_rts",
        "step_id": "zeroconf_confirm",
        "context": {
            "source": "zeroconf",
            "title_placeholders": {"name": "ESPSomfyRTS.local.", "host": "10.0.30.202"},
        },
    }])
    ctx.rest.post = AsyncMock(return_value=FORM_STEP)

    result = await haops_integration_flow_start(ctx, domain="espsomfy_rts")

    assert result["in_progress"][0]["name"] == "ESPSomfyRTS.local."
    assert result["in_progress"][0]["source"] == "zeroconf"


@pytest.mark.asyncio
async def test_start_flags_entry_created_on_init(ctx: Any) -> None:
    """A no-input flow commits inside start — say so loudly, audit as mutate."""
    ctx.ws.send_command = AsyncMock(return_value=[])
    ctx.rest.post = AsyncMock(return_value=CREATED_STEP)

    result = await haops_integration_flow_start(ctx, domain="ac_controller")

    assert result["created_on_start"] is True
    assert result["entry_id"] == "01JQ8ZK3F4XYZ"
    assert "no confirm step to gate" in result["warning"]


@pytest.mark.asyncio
async def test_start_unknown_domain_explains_yaml_only(ctx: Any) -> None:
    ctx.ws.send_command = AsyncMock(return_value=[])
    ctx.rest.post = AsyncMock(side_effect=RestClientError(400, "unknown handler"))

    result = await haops_integration_flow_start(ctx, domain="nope")

    assert "error" in result
    assert "YAML-only" in result["hint"]


@pytest.mark.asyncio
async def test_start_requires_domain(ctx: Any) -> None:
    assert "error" in await haops_integration_flow_start(ctx, domain="")


@pytest.mark.asyncio
async def test_start_survives_unreadable_flow_index(ctx: Any) -> None:
    """Can't list pending flows → still start, just without the extra info."""
    from ha_ops_mcp.connections.websocket import WebSocketError

    ctx.ws.send_command = AsyncMock(side_effect=WebSocketError("Unknown command"))
    ctx.rest.post = AsyncMock(return_value=FORM_STEP)

    result = await haops_integration_flow_start(ctx, domain="ac_controller")

    assert result["type"] == "form"
    assert "in_progress" not in result


# ── step (two-phase) ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_step_preview_shows_schema_beside_input_and_creates_nothing(
    ctx: Any,
) -> None:
    ctx.rest.get = AsyncMock(return_value=FORM_STEP)
    ctx.rest.post = AsyncMock()

    result = await haops_integration_flow_step(
        ctx,
        flow_id="abc123",
        user_input={"wrapped_entity": "climate.x", "slug": "bedroom"},
    )

    assert "token" in result
    assert result["preview"]["submitting"]["slug"] == "bedroom"
    assert result["preview"]["step_wants"][0]["name"] == "wrapped_entity"
    # Phase 1 must never POST.
    ctx.rest.post.assert_not_awaited()


@pytest.mark.asyncio
async def test_step_preview_flags_missing_required_fields(ctx: Any) -> None:
    ctx.rest.get = AsyncMock(return_value=FORM_STEP)

    result = await haops_integration_flow_step(
        ctx, flow_id="abc123", user_input={"wrapped_entity": "climate.x"}
    )

    assert result["missing_required"] == ["slug"]


@pytest.mark.asyncio
async def test_step_apply_creates_entry(ctx: Any) -> None:
    ctx.rest.get = AsyncMock(return_value=FORM_STEP)
    preview = await haops_integration_flow_step(
        ctx,
        flow_id="abc123",
        user_input={"wrapped_entity": "climate.x", "slug": "bedroom"},
    )

    ctx.rest.post = AsyncMock(return_value=CREATED_STEP)
    result = await haops_integration_flow_step(
        ctx, confirm=True, token=preview["token"]
    )

    assert result["success"] is True
    assert result["entry_id"] == "01JQ8ZK3F4XYZ"
    # flow_id and user_input came off the token, not the second call.
    ctx.rest.post.assert_awaited_once_with(
        f"{_FLOW_BASE}/abc123",
        {"wrapped_entity": "climate.x", "slug": "bedroom"},
    )


@pytest.mark.asyncio
async def test_step_surfaces_flow_validation_errors_as_not_success(
    ctx: Any,
) -> None:
    """A form with errors means the flow rejected it — nothing was created."""
    ctx.rest.get = AsyncMock(return_value=FORM_STEP)
    preview = await haops_integration_flow_step(
        ctx, flow_id="abc123", user_input={"slug": "dupe"}
    )

    rejected = {**FORM_STEP, "errors": {"slug": "already_configured"}}
    ctx.rest.post = AsyncMock(return_value=rejected)
    result = await haops_integration_flow_step(
        ctx, confirm=True, token=preview["token"]
    )

    assert result["success"] is False
    assert result["errors"] == {"slug": "already_configured"}
    assert "nothing was created" in result["message"]


@pytest.mark.asyncio
async def test_step_confirm_requires_token(ctx: Any) -> None:
    result = await haops_integration_flow_step(ctx, flow_id="abc123", confirm=True)
    assert "error" in result


@pytest.mark.asyncio
async def test_step_token_is_single_use(ctx: Any) -> None:
    ctx.rest.get = AsyncMock(return_value=FORM_STEP)
    preview = await haops_integration_flow_step(
        ctx, flow_id="abc123", user_input={"slug": "x"}
    )
    ctx.rest.post = AsyncMock(return_value=CREATED_STEP)

    first = await haops_integration_flow_step(
        ctx, confirm=True, token=preview["token"]
    )
    second = await haops_integration_flow_step(
        ctx, confirm=True, token=preview["token"]
    )

    assert first["success"] is True
    assert "error" in second


@pytest.mark.asyncio
async def test_step_preview_on_dead_flow_explains_restart(ctx: Any) -> None:
    ctx.rest.get = AsyncMock(side_effect=RestClientError(404, "not found"))

    result = await haops_integration_flow_step(
        ctx, flow_id="gone", user_input={}
    )

    assert "error" in result
    assert "Core restart" in result["hint"]


@pytest.mark.asyncio
async def test_step_rejects_non_dict_user_input(ctx: Any) -> None:
    result = await haops_integration_flow_step(
        ctx, flow_id="abc123", user_input=["nope"]  # type: ignore[arg-type]
    )
    assert "error" in result


# ── abort ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_abort_discards_pending_flow(ctx: Any) -> None:
    ctx.rest.delete = AsyncMock(return_value={})

    result = await haops_integration_flow_abort(ctx, flow_id="abc123")

    assert result["aborted"] is True
    ctx.rest.delete.assert_awaited_once_with(f"{_FLOW_BASE}/abc123")


@pytest.mark.asyncio
async def test_abort_of_missing_flow_is_not_an_error(ctx: Any) -> None:
    """The caller's goal (flow not pending) is already true — don't raise."""
    ctx.rest.delete = AsyncMock(side_effect=RestClientError(404, "no flow"))

    result = await haops_integration_flow_abort(ctx, flow_id="gone")

    assert result["aborted"] is False
    assert "error" not in result


@pytest.mark.asyncio
async def test_abort_reports_real_failures(ctx: Any) -> None:
    ctx.rest.delete = AsyncMock(side_effect=RestClientError(500, "boom"))
    result = await haops_integration_flow_abort(ctx, flow_id="abc123")
    assert "error" in result


@pytest.mark.asyncio
async def test_abort_requires_flow_id(ctx: Any) -> None:
    assert "error" in await haops_integration_flow_abort(ctx, flow_id="")
