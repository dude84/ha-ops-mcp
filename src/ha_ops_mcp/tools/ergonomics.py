"""Ergonomic wrappers — one-line helpers for the most common operations.

These are thin convenience layers over `haops_service_call` and the WS
entity registry endpoints. Each one encapsulates a pattern that the LLM
otherwise has to reconstruct: "domain.service, target.entity_id, etc.".

All wrappers are read-light — they POST a single service call and return
the before/after state. No token dance (firing an automation or reloading
an integration is idempotent and recoverable by re-firing).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ha_ops_mcp.connections.rest import RestClientError
from ha_ops_mcp.connections.websocket import WebSocketError
from ha_ops_mcp.server import registry

if TYPE_CHECKING:
    from ha_ops_mcp.server import HaOpsContext

logger = logging.getLogger(__name__)


async def _fire(
    ctx: HaOpsContext, domain: str, service: str, entity_id: str
) -> dict[str, Any]:
    """Helper: POST /api/services/<domain>/<service> with entity_id target.

    Returns a uniform {success, service, entity_id[, error]} shape.
    """
    try:
        await ctx.rest.post(
            f"/api/services/{domain}/{service}",
            {"entity_id": entity_id},
        )
    except RestClientError as e:
        return {
            "success": False,
            "service": f"{domain}.{service}",
            "entity_id": entity_id,
            "error": str(e),
        }

    await ctx.audit.log(
        tool=f"{domain}_{service}",
        details={"entity_id": entity_id},
    )
    return {
        "success": True,
        "service": f"{domain}.{service}",
        "entity_id": entity_id,
    }


# ── haops_automation_trigger ──────────────────────────────────────────


@registry.tool(
    name="haops_automation_trigger",
    description=(
        "Manually trigger an automation. Equivalent to haops_service_call "
        "domain='automation' service='trigger' target={entity_id}. "
        "Useful for testing whether an automation's actions work without "
        "waiting for its real trigger. Parameter: entity_id (the automation's "
        "entity id, e.g. 'automation.morning_lights')."
    ),
    params={"entity_id": {"type": "string"}},
)
async def haops_automation_trigger(
    ctx: HaOpsContext, entity_id: str
) -> dict[str, Any]:
    if not entity_id.startswith("automation."):
        return {"error": "entity_id must start with 'automation.'"}
    return await _fire(ctx, "automation", "trigger", entity_id)


# ── haops_script_run ──────────────────────────────────────────────────


@registry.tool(
    name="haops_script_run",
    description=(
        "Run a script by its entity id. Equivalent to haops_service_call "
        "domain='script' service='turn_on' target={entity_id}. "
        "Parameter: entity_id (e.g. 'script.bedtime'). "
        "To run a script with variables, use haops_service_call instead."
    ),
    params={"entity_id": {"type": "string"}},
)
async def haops_script_run(
    ctx: HaOpsContext, entity_id: str
) -> dict[str, Any]:
    if not entity_id.startswith("script."):
        return {"error": "entity_id must start with 'script.'"}
    return await _fire(ctx, "script", "turn_on", entity_id)


# ── haops_scene_activate ──────────────────────────────────────────────


@registry.tool(
    name="haops_scene_activate",
    description=(
        "Activate a scene. Equivalent to haops_service_call domain='scene' "
        "service='turn_on' target={entity_id}. "
        "Parameter: entity_id (e.g. 'scene.movie_time')."
    ),
    params={"entity_id": {"type": "string"}},
)
async def haops_scene_activate(
    ctx: HaOpsContext, entity_id: str
) -> dict[str, Any]:
    if not entity_id.startswith("scene."):
        return {"error": "entity_id must start with 'scene.'"}
    return await _fire(ctx, "scene", "turn_on", entity_id)


# ── haops_integration_reload ──────────────────────────────────────────


@registry.tool(
    name="haops_integration_reload",
    description=(
        "Reload a config entry (integration instance) without restarting HA. "
        "Parameter: entry_id (the config entry's id — from haops_registry_query "
        "type='config_entries'). Useful after editing integration options or "
        "when an integration is in setup_retry state."
    ),
    params={"entry_id": {"type": "string"}},
)
async def haops_integration_reload(
    ctx: HaOpsContext, entry_id: str
) -> dict[str, Any]:
    if not entry_id:
        return {"error": "entry_id is required"}
    try:
        await ctx.ws.send_command(
            "config_entries/reload", entry_id=entry_id
        )
    except WebSocketError as e:
        return {"error": f"Reload failed: {e}"}

    await ctx.audit.log(
        tool="integration_reload",
        details={"entry_id": entry_id},
    )
    return {"success": True, "entry_id": entry_id}


# ── haops_entities_assign_area ────────────────────────────────────────


@registry.tool(
    name="haops_entities_assign_area",
    description=(
        "Bulk-assign an area to multiple entities. Two-phase confirm. "
        "Parameters: entity_ids (list, required), area_id (string — empty "
        "string to clear the area assignment), confirm (bool, default false), "
        "token (string, required in phase 2). "
        "Phase 1 previews the impact and returns a token; phase 2 applies "
        "via WS config/entity_registry/update."
    ),
    params={
        "entity_ids": {"type": "array", "items": {"type": "string"}},
        "area_id": {"type": "string", "description": "Area id, or '' to clear"},
        "confirm": {"type": "boolean", "default": False},
        "token": {"type": "string", "default": ""},
    },
)
async def haops_entities_assign_area(
    ctx: HaOpsContext,
    entity_ids: list[str],
    area_id: str = "",
    confirm: bool = False,
    token: str = "",
) -> dict[str, Any]:
    # In phase 2 (confirm=true), entity_ids comes from the token — the caller
    # may pass [] to avoid duplicating the list. Only enforce non-empty in phase 1.
    if not confirm and not entity_ids:
        return {"error": "entity_ids is required and must be non-empty"}

    normalized_area: str | None = area_id if area_id else None

    # Phase 1 — preview
    if not confirm:
        tk = ctx.safety.create_token(
            action="entities_assign_area",
            details={
                "entity_ids": list(entity_ids),
                "area_id": normalized_area,
            },
        )
        return {
            "preview": {
                "entity_ids": list(entity_ids),
                "new_area_id": normalized_area,
                "count": len(entity_ids),
            },
            "token": tk.id,
            "message": (
                "Review the plan above. Call again with confirm=true and this "
                "token to apply."
            ),
        }

    # Phase 2 — apply
    if not token:
        return {"error": "confirm=true requires a token"}
    try:
        token_data = ctx.safety.validate_token(token)
    except Exception as e:
        return {"error": str(e)}

    details = token_data.details
    target_ids = details["entity_ids"]
    target_area = details["area_id"]

    updated: list[str] = []
    errors: list[dict[str, str]] = []
    for eid in target_ids:
        try:
            await ctx.ws.send_command(
                "config/entity_registry/update",
                entity_id=eid,
                area_id=target_area,
            )
            updated.append(eid)
        except WebSocketError as e:
            errors.append({"entity_id": eid, "error": str(e)})

    ctx.safety.consume_token(token)
    await ctx.audit.log(
        tool="entities_assign_area",
        details={
            "updated": updated,
            "errors": errors,
            "area_id": target_area,
        },
        token_id=token,
    )
    return {
        "success": not errors,
        "updated": updated,
        "errors": errors,
        "area_id": target_area,
    }


# ── haops_entity_customize ────────────────────────────────────────────


@registry.tool(
    name="haops_entity_customize",
    description=(
        "Customize entity registry options — friendly name, icon, "
        "unit_of_measurement, etc. Two-phase confirm. Parameters: "
        "entity_id (string, required), name (optional), icon (optional), "
        "unit_of_measurement (optional), device_class (optional), "
        "confirm (bool, default false), token (string, required in phase 2). "
        "Only provided fields are updated; omit a field to leave it unchanged."
    ),
    params={
        "entity_id": {"type": "string"},
        "name": {"type": "string", "default": ""},
        "icon": {"type": "string", "default": ""},
        "unit_of_measurement": {"type": "string", "default": ""},
        "device_class": {"type": "string", "default": ""},
        "confirm": {"type": "boolean", "default": False},
        "token": {"type": "string", "default": ""},
    },
)
async def haops_entity_customize(
    ctx: HaOpsContext,
    entity_id: str,
    name: str = "",
    icon: str = "",
    unit_of_measurement: str = "",
    device_class: str = "",
    confirm: bool = False,
    token: str = "",
) -> dict[str, Any]:
    # entity_id is only required in phase 1 — phase 2 carries it in the token.
    if not confirm and not entity_id:
        return {"error": "entity_id is required"}

    changes: dict[str, Any] = {}
    # Empty string means "not supplied" in our MCP-friendly surface. If a
    # user wants to clear a value, they can pass None (via explicit null in
    # the JSON), but MCP primitives prefer empty strings as absent.
    if name:
        changes["name"] = name
    if icon:
        changes["icon"] = icon
    if unit_of_measurement:
        changes["unit_of_measurement"] = unit_of_measurement
    if device_class:
        changes["device_class"] = device_class

    if not confirm and not changes:
        return {"error": "No fields provided to update"}

    # Phase 1 — preview
    if not confirm:
        tk = ctx.safety.create_token(
            action="entity_customize",
            details={"entity_id": entity_id, "changes": changes},
        )
        return {
            "preview": {"entity_id": entity_id, "changes": changes},
            "token": tk.id,
            "message": (
                "Review the changes above. Call again with confirm=true and "
                "this token to apply."
            ),
        }

    # Phase 2 — apply
    if not token:
        return {"error": "confirm=true requires a token"}
    try:
        token_data = ctx.safety.validate_token(token)
    except Exception as e:
        return {"error": str(e)}

    target_eid = token_data.details["entity_id"]
    target_changes = token_data.details["changes"]

    try:
        await ctx.ws.send_command(
            "config/entity_registry/update",
            entity_id=target_eid,
            **target_changes,
        )
    except WebSocketError as e:
        return {"error": f"Update failed: {e}"}

    ctx.safety.consume_token(token)
    await ctx.audit.log(
        tool="entity_customize",
        details={"entity_id": target_eid, "changes": target_changes},
        token_id=token,
    )
    return {
        "success": True,
        "entity_id": target_eid,
        "changes": target_changes,
    }
