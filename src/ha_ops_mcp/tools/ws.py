"""Generic Home Assistant WebSocket passthrough — the WS escape hatch.

haops_service_call is the documented escape hatch for REST services. A large
slice of HA's admin surface is WS-ONLY though: config/*/update, *_registry
ops, zha/*, lovelace/*, topology scans, etc. This tool is the generic answer
for "there's no first-class tool for this WS command yet".

Deliberately NO type allowlist. This is a power-user tool, and the server
already exposes haops_exec_shell — an allowlist here would be security
theatre, not a boundary. The two-phase confirm on non-read commands exists
for AUDIT + an explicit apply gate, not as a security control: read-shaped
commands run immediately, everything else previews first.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ha_ops_mcp.server import registry

if TYPE_CHECKING:
    from ha_ops_mcp.server import HaOpsContext

logger = logging.getLogger(__name__)

# Heuristic for "this is a read" — skips the confirm step. NOT a security
# gate: it only decides whether to preview, and a power user can still send
# any command via the two-phase path.
_READ_SUFFIXES = ("/list", "/get", "/info", "/render")
_READ_TYPES = {
    "config/check_config",
    "lovelace/config",
    "lovelace/resources",
    "zha/devices",
    "zha/groups",
}


def _looks_read_only(command_type: str) -> bool:
    if command_type in _READ_TYPES:
        return True
    return any(command_type.endswith(s) for s in _READ_SUFFIXES)


@registry.tool(
    name="haops_ws_command",
    description=(
        "ESCAPE HATCH: send an arbitrary Home Assistant WebSocket command. "
        "Use when no first-class haops_* tool covers a WS-only operation "
        "(e.g. config/device_registry/update, zha/* commands, lovelace/* "
        "operations, topology/diagnostic commands). For REST service calls "
        "use haops_service_call instead; for entity enable/disable use "
        "haops_entity_toggle; for ZHA reconfigure use "
        "haops_zha_reconfigure_device — those are safer and self-documenting. "
        "Reach for this only for the long tail they don't cover. "
        "BEHAVIOUR: read-shaped commands (types ending /list, /get, /info, "
        "/render, or known read types) execute immediately. Any other command "
        "is two-phase: call without confirm to preview the exact message, "
        "then confirm=true + token to send. The confirm step is for audit + "
        "an explicit gate, NOT a safety guarantee — this can do anything the "
        "HA WS API can, so read the preview. "
        "Parameters: command_type (string, e.g. 'config/entity_registry/"
        "update'), payload (object — extra fields merged into the WS message, "
        "e.g. {\"entity_id\": \"sensor.x\", \"name\": \"New\"}), "
        "confirm (bool, default false), token (string, if confirming). "
        "Returns the raw WS result dict under 'result'."
    ),
    params={
        "command_type": {
            "type": "string",
            "description": "The HA WS message 'type' (e.g. 'config/entity_registry/list')",
        },
        "payload": {
            "type": "object",
            "description": "Extra fields merged into the WS message (besides id/type)",
        },
        "confirm": {
            "type": "boolean", "description": "Execute a non-read command",
            "default": False,
        },
        "token": {
            "type": "string",
            "description": "Confirmation token from preview step",
        },
    },
)
async def haops_ws_command(
    ctx: HaOpsContext,
    command_type: str,
    payload: dict[str, Any] | None = None,
    confirm: bool = False,
    token: str | None = None,
) -> dict[str, Any]:
    if not command_type:
        return {"error": "command_type is required"}
    payload = payload or {}
    if not isinstance(payload, dict):
        return {"error": "payload must be an object/dict of WS message fields"}

    from ha_ops_mcp.connections.websocket import WebSocketError

    read_only = _looks_read_only(command_type)

    # Non-read commands require a two-phase confirm (audit + explicit apply).
    if not read_only and not confirm:
        tk = ctx.safety.create_token(
            action="ws_command",
            details={"command_type": command_type, "payload": payload},
        )
        return {
            "preview": {"type": command_type, **payload},
            "token": tk.id,
            "read_only": False,
            "message": "This is a non-read WS command. Review the message "
            "above, then call again with confirm=true and this token to send.",
        }

    if not read_only:
        if token is None:
            return {"error": "confirm=true requires a token"}
        try:
            token_data = ctx.safety.validate_token(token)
        except Exception as e:
            return {"error": str(e)}
        command_type = token_data.details.get("command_type", command_type)
        payload = token_data.details.get("payload", payload)

    try:
        result = await ctx.ws.send_command(command_type, **payload)
    except WebSocketError as e:
        if not read_only and token is not None:
            await ctx.audit.log(
                tool="ws_command",
                details={"command_type": command_type, "payload": payload},
                success=False, error=str(e), token_id=token,
            )
        return {"error": f"WS command failed: {e}", "command_type": command_type}
    except TypeError as e:
        # bad payload keys (e.g. reserved 'id'/'type' collision)
        return {"error": f"Invalid payload for {command_type}: {e}"}

    if not read_only and token is not None:
        ctx.safety.consume_token(token)
    await ctx.audit.log(
        tool="ws_command",
        details={"command_type": command_type, "payload": payload},
        op_class="read" if read_only else "mutate",
        token_id=None if read_only else token,
    )

    return {
        "success": True,
        "command_type": command_type,
        "read_only": read_only,
        "result": result,
    }
