"""Device tools — haops_device_list, haops_device_info."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ha_ops_mcp.server import registry

if TYPE_CHECKING:
    from ha_ops_mcp.server import HaOpsContext

logger = logging.getLogger(__name__)


async def _get_device_registry(ctx: HaOpsContext) -> list[dict[str, Any]]:
    """Read device registry, filesystem-first with WebSocket fallback.

    Mirrors the entity registry access pattern. HA's REST API does not
    expose devices, so the fallback is WebSocket (`config/device_registry/list`).
    """
    storage_path = (
        Path(ctx.config.filesystem.config_root) / ".storage" / "core.device_registry"
    )
    try:
        content = storage_path.read_text()
        data = json.loads(content)
        return data["data"]["devices"]  # type: ignore[no-any-return]
    except (FileNotFoundError, KeyError, json.JSONDecodeError):
        pass

    try:
        from ha_ops_mcp.connections.websocket import WebSocketError
        result: Any = await ctx.ws.send_command("config/device_registry/list")
        if isinstance(result, list):
            return result
        return []
    except WebSocketError as e:
        raise RuntimeError(
            f"Device registry unavailable via filesystem or WebSocket: {e}"
        ) from e


async def _get_area_registry(ctx: HaOpsContext) -> dict[str, dict[str, Any]]:
    """Read area registry as a dict keyed by area_id."""
    storage_path = (
        Path(ctx.config.filesystem.config_root) / ".storage" / "core.area_registry"
    )
    try:
        content = storage_path.read_text()
        data = json.loads(content)
        areas = data.get("data", {}).get("areas", [])
        return {a["id"]: a for a in areas}
    except (FileNotFoundError, KeyError, json.JSONDecodeError):
        pass

    try:
        from ha_ops_mcp.connections.websocket import WebSocketError
        result: Any = await ctx.ws.send_command("config/area_registry/list")
        if isinstance(result, list):
            return {a.get("area_id") or a.get("id"): a for a in result}
        return {}
    except WebSocketError:
        return {}


def _device_display_name(device: dict[str, Any]) -> str | None:
    """Pick the best display name for a device."""
    return (
        device.get("name_by_user")
        or device.get("name")
        or device.get("model")
        or device.get("id")
    )


def _summarize_device(
    device: dict[str, Any],
    areas: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    area_id = device.get("area_id")
    area_name = None
    if area_id and area_id in areas:
        area_name = areas[area_id].get("name")
    return {
        "id": device.get("id"),
        "name": _device_display_name(device),
        "name_by_user": device.get("name_by_user"),
        "manufacturer": device.get("manufacturer"),
        "model": device.get("model"),
        "sw_version": device.get("sw_version"),
        "hw_version": device.get("hw_version"),
        "area_id": area_id,
        "area_name": area_name,
        "disabled_by": device.get("disabled_by"),
        "entry_type": device.get("entry_type"),
    }


@registry.tool(
    name="haops_device_info",
    description=(
        "Get detailed info for a single device by ID or name (substring match). "
        "Returns the full device record (manufacturer, model, sw/hw version, "
        "area, identifiers, connections, config_entries, disabled state) plus "
        "all linked entities with their current states. "
        "Parameter: device (string, required — device id or substring of name). "
        "If multiple devices match by name, returns the match list to disambiguate."
    ),
    params={
        "device": {
            "type": "string",
            "description": "Device id or substring of display name",
        },
    },
)
async def haops_device_info(
    ctx: HaOpsContext, device: str
) -> dict[str, Any]:
    from ha_ops_mcp.tools.entity import _get_entity_registry, _get_states

    devices = await _get_device_registry(ctx)
    areas = await _get_area_registry(ctx)

    # Try exact id match first
    matches = [d for d in devices if d.get("id") == device]
    if not matches:
        q = device.lower()
        # Substring match across all name-like fields, not just the
        # primary display name — a device may be labeled by user while
        # still matching on manufacturer model, etc.
        matches = [
            d
            for d in devices
            if q in (d.get("name_by_user") or "").lower()
            or q in (d.get("name") or "").lower()
            or q in (d.get("model") or "").lower()
            or q in (d.get("manufacturer") or "").lower()
        ]

    if not matches:
        return {"error": f"No device matches '{device}'"}

    if len(matches) > 1:
        return {
            "error": f"Multiple devices match '{device}' ({len(matches)} found). "
            "Use the exact device id or a more specific name.",
            "matches": [_summarize_device(d, areas) for d in matches],
        }

    dev = matches[0]
    dev_id = dev.get("id")

    # Collect linked entities
    entities = await _get_entity_registry(ctx)
    states = await _get_states(ctx)
    linked: list[dict[str, Any]] = []
    for e in entities:
        if e.get("device_id") != dev_id:
            continue
        eid = e.get("entity_id", "")
        state_info = states.get(eid, {})
        linked.append({
            "entity_id": eid,
            "name": (
                state_info.get("attributes", {}).get("friendly_name")
                or e.get("name")
                or e.get("original_name")
            ),
            "state": state_info.get("state"),
            "disabled_by": e.get("disabled_by"),
            "platform": e.get("platform"),
        })

    return {
        "device": {
            **_summarize_device(dev, areas),
            "identifiers": dev.get("identifiers"),
            "connections": dev.get("connections"),
            "config_entries": dev.get("config_entries"),
            "configuration_url": dev.get("configuration_url"),
        },
        "entities": linked,
        "entity_count": len(linked),
    }
