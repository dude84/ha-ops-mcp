"""Service tools — haops_service_call (generic escape hatch)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ha_ops_mcp.connections.rest import RestClientError
from ha_ops_mcp.server import registry
from ha_ops_mcp.utils.logs import recent_log_matches

if TYPE_CHECKING:
    from ha_ops_mcp.server import HaOpsContext


@registry.tool(
    name="haops_service_call",
    description=(
        "Generic Home Assistant service call — the 'everything else' "
        "escape hatch for operations not covered by specific tools. "
        "Parameters: domain (string, required — e.g. 'light', 'switch'), "
        "service (string, required — e.g. 'turn_on', 'toggle'), "
        "data (dict, optional — service data), "
        "target (dict, optional — entity_id, device_id, or area_id). "
        "Returns the service call result and state changes for "
        "targeted entities (before/after). "
        "On failure, the response includes a `log_excerpt` field with "
        "recent matching lines from homeassistant.log (service tag, "
        "domain, exception tokens) so the caller doesn't need a "
        "follow-up haops_system_logs round trip to diagnose. "
        "Note: this is NOT for database, config, or entity registry "
        "operations — use the specific haops_* tools for those."
    ),
    params={
        "domain": {
            "type": "string",
            "description": "Service domain (e.g. 'light', 'automation')",
        },
        "service": {
            "type": "string",
            "description": "Service name (e.g. 'turn_on', 'reload')",
        },
        "data": {
            "type": "object",
            "description": "Service data (optional)",
        },
        "target": {
            "type": "object",
            "description": "Target: {entity_id, device_id, area_id}",
        },
    },
)
async def haops_service_call(
    ctx: HaOpsContext,
    domain: str,
    service: str,
    data: dict[str, Any] | None = None,
    target: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # Capture before-states for targeted entities
    before_states: dict[str, str] = {}
    target_entity_ids: list[str] = []
    if target and "entity_id" in target:
        ids = target["entity_id"]
        if isinstance(ids, str):
            target_entity_ids = [ids]
        elif isinstance(ids, list):
            target_entity_ids = ids

    for eid in target_entity_ids:
        try:
            state = await ctx.rest.get(f"/api/states/{eid}")
            before_states[eid] = state.get("state", "unknown")
        except RestClientError:
            before_states[eid] = "unknown"

    # Build request body
    body: dict[str, Any] = {}
    if data:
        body.update(data)
    if target:
        body.update(target)

    # Call the service
    try:
        result = await ctx.rest.post(
            f"/api/services/{domain}/{service}", body
        )
    except RestClientError as e:
        # On any non-2xx, enrich the error with recent log excerpts that
        # mention the service or a common exception token. Saves the caller
        # a follow-up haops_system_logs round trip for the 50% of failures
        # (ZHA, template, integration-specific) where the real error lives
        # in homeassistant.log, not in the HTTP response body.
        error_payload: dict[str, Any] = {"error": f"Service call failed: {e}"}
        excerpt = await recent_log_matches(
            ctx,
            tokens=[
                f"{domain}.{service}",
                domain,
                "Exception",
                "Traceback",
            ],
        )
        if excerpt:
            error_payload["log_excerpt"] = excerpt
        return error_payload

    # Capture after-states
    state_changes: list[dict[str, Any]] = []
    for eid in target_entity_ids:
        try:
            state = await ctx.rest.get(f"/api/states/{eid}")
            after = state.get("state", "unknown")
            if before_states.get(eid) != after:
                state_changes.append({
                    "entity_id": eid,
                    "before": before_states.get(eid),
                    "after": after,
                })
        except RestClientError:
            pass

    await ctx.audit.log(
        tool="service_call",
        details={
            "domain": domain,
            "service": service,
            "data": data,
            "target": target,
        },
    )

    response: dict[str, Any] = {
        "success": True,
        "service": f"{domain}.{service}",
    }
    if state_changes:
        response["state_changes"] = state_changes
    if isinstance(result, list) and result:
        response["result"] = result

    return response
