"""Device tools — haops_device_info, haops_device_remove."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ha_ops_mcp.server import registry
from ha_ops_mcp.storage_registry import device_config_entry_ids, load_registry

if TYPE_CHECKING:
    from ha_ops_mcp.server import HaOpsContext

logger = logging.getLogger(__name__)


async def _get_device_registry(
    ctx: HaOpsContext, *, fresh: bool = False
) -> list[dict[str, Any]]:
    """Read device registry, filesystem-first with WebSocket fallback.

    Mirrors the entity registry access pattern via the shared freshness-aware
    loader. HA's REST API does not expose devices, so the fallback is
    WebSocket (`config/device_registry/list`).
    """
    read = await load_registry(ctx, "devices", fresh=fresh)
    return read.records


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


def _resolve_device(
    devices: list[dict[str, Any]],
    query: str,
    areas: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Resolve a device by id, else by substring across its name-like fields.

    Returns:
        (device, None) on a unique match, or (None, error_dict) when nothing
        or more than one thing matched — the error carries the candidate list
        so the caller can disambiguate.
    """
    matches = [d for d in devices if d.get("id") == query]
    if not matches:
        q = query.lower()
        # Substring match across all name-like fields, not just the primary
        # display name — a device may be labeled by user while still matching
        # on manufacturer/model.
        matches = [
            d
            for d in devices
            if q in (d.get("name_by_user") or "").lower()
            or q in (d.get("name") or "").lower()
            or q in (d.get("model") or "").lower()
            or q in (d.get("manufacturer") or "").lower()
        ]

    if not matches:
        return None, {"error": f"No device matches '{query}'"}
    if len(matches) > 1:
        return None, {
            "error": f"Multiple devices match '{query}' ({len(matches)} found). "
            "Use the exact device id or a more specific name.",
            "matches": [_summarize_device(d, areas) for d in matches],
        }
    return matches[0], None


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

    dev, err = _resolve_device(devices, device, areas)
    if err is not None:
        return err
    assert dev is not None
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
            "config_entries": device_config_entry_ids(dev),
            # HA 2026.8 splits one physical device into one record per config
            # entry; these tie a split record back to its composite.
            "composite_device_id": dev.get("composite_device_id"),
            "split_at": dev.get("split_at"),
            "configuration_url": dev.get("configuration_url"),
        },
        "entities": linked,
        "entity_count": len(linked),
    }


# ── haops_device_remove ───────────────────────────────────────────────

# Integrations whose devices cannot be removed through the config-entry path,
# with the route that does work. Keyed by config-entry domain.
_REMOVAL_ALTERNATIVES: dict[str, str] = {
    "zha": (
        "ZHA does not support per-device removal over the config-entry path "
        "(and its own WS remove commands were dropped). Call the `zha.remove` "
        "service with the device's IEEE address via haops_service_call."
    ),
    "mqtt": (
        "This MQTT device was created by discovery. Removing the registry "
        "entry does not stop discovery — clear the retained discovery topic "
        "at the broker, or the device reappears on the next announce."
    ),
    "tasmota": (
        "Tasmota devices arrive via MQTT discovery. Removing the registry "
        "entry does not stop discovery — the device reappears on the next "
        "announce unless the retained discovery topic is cleared, or the "
        "hardware is off the network for good."
    ),
    "esphome": (
        "ESPHome models one node as one config entry, so there is no "
        "per-device removal — delete the config entry for the node instead "
        "(Settings → Devices & services → ESPHome → node → Delete)."
    ),
}


async def _config_entries_by_id(ctx: HaOpsContext) -> dict[str, dict[str, Any]]:
    """Live config entries keyed by entry_id.

    WebSocket only, deliberately: `supports_remove_device` is a runtime
    capability of the loaded integration, not something persisted in
    .storage/core.config_entries, so the filesystem copy cannot answer the
    question this tool has to ask.
    """
    from ha_ops_mcp.connections.websocket import WebSocketError

    try:
        result: Any = await ctx.ws.send_command("config_entries/get")
    except WebSocketError as e:
        raise RuntimeError(f"Could not read config entries: {e}") from e
    if not isinstance(result, list):
        raise RuntimeError(
            "config_entries/get returned "
            f"{type(result).__name__}, expected a list of entries"
        )
    return {
        e["entry_id"]: e
        for e in result
        if isinstance(e, dict) and e.get("entry_id")
    }


@registry.tool(
    name="haops_device_remove",
    description=(
        "Delete a device from HA's device registry, along with every entity "
        "it owns. This is the tool-equivalent of the UI's device Delete "
        "button: it unlinks the device from each of its config entries, and "
        "HA drops the device once its last entry is gone. "
        "WHEN TO USE: hardware that is gone for good, or freeing an entity_id "
        "namespace (e.g. a dead Tasmota device holding "
        "switch.plug_x_1..4 that a replacement node needs). "
        "NOT FOR: temporarily offline devices — use haops_entity_toggle to "
        "disable entities instead. "
        "NOT EVERY INTEGRATION SUPPORTS THIS. The preview reports "
        "supports_remove_device per config entry and, when the answer is no, "
        "names the route that does work (ZHA → the zha.remove service; "
        "ESPHome → delete the node's config entry). Discovery-based "
        "integrations (MQTT/Tasmota) re-create the device on the next "
        "announce unless the retained discovery topic is cleared — the "
        "preview says so. "
        "Two-phase: 1) call without confirm for a preview listing the device, "
        "every entity that will disappear, and the per-entry removability. "
        "2) call with confirm=true and the token. "
        "NOT ROLLBACKABLE — haops_rollback cannot recreate a device; the "
        "integration has to rediscover or you re-pair it. Entity "
        "customisations and the device's area/name overrides are lost. "
        "Parameters: device (string, required — device id or name substring), "
        "confirm (bool, default false), token (string, if confirming). "
        "Returns (apply): {success, device_id, removed_from_entries, "
        "remaining_entries, device_gone, entities_removed}. "
        "This is a MUTATING, IRREVERSIBLE operation."
    ),
    params={
        "device": {
            "type": "string",
            "description": "Device id or substring of display name",
        },
        "confirm": {
            "type": "boolean",
            "description": "Execute the removal",
            "default": False,
        },
        "token": {
            "type": "string",
            "description": "Confirmation token from the preview step",
        },
    },
)
async def haops_device_remove(
    ctx: HaOpsContext,
    device: str,
    confirm: bool = False,
    token: str | None = None,
) -> dict[str, Any]:
    from ha_ops_mcp.connections.websocket import WebSocketError
    from ha_ops_mcp.tools.entity import _get_entity_registry

    if not confirm:
        # Read live: a device removed moments ago is still in the .storage
        # file, and previewing the removal of a ghost is exactly the failure
        # this tool must not reproduce.
        devices = await _get_device_registry(ctx, fresh=True)
        areas = await _get_area_registry(ctx)
        dev, err = _resolve_device(devices, device, areas)
        if err is not None:
            return err
        assert dev is not None

        dev_id = str(dev.get("id"))
        entities = await _get_entity_registry(ctx, fresh=True)
        owned = [
            e.get("entity_id")
            for e in entities
            if e.get("device_id") == dev_id and e.get("entity_id")
        ]

        try:
            all_entries = await _config_entries_by_id(ctx)
        except RuntimeError as e:
            return {"error": str(e)}

        entry_rows: list[dict[str, Any]] = []
        removable: list[str] = []
        caveats: list[str] = []
        for entry_id in device_config_entry_ids(dev):
            entry = all_entries.get(entry_id, {})
            domain = entry.get("domain")
            supported = bool(entry.get("supports_remove_device"))
            row = {
                "entry_id": entry_id,
                "domain": domain,
                "title": entry.get("title"),
                "state": entry.get("state"),
                "supports_remove_device": supported,
            }
            if not entry:
                row["note"] = (
                    "Entry not found among live config entries — it may have "
                    "been deleted already; removal will be attempted anyway."
                )
            if supported:
                removable.append(entry_id)
            alt = _REMOVAL_ALTERNATIVES.get(domain or "")
            if alt and (not supported or domain in {"mqtt", "tasmota"}):
                caveats.append(f"{domain}: {alt}")
            entry_rows.append(row)

        if not removable:
            return {
                "error": (
                    "None of this device's config entries supports device "
                    "removal, so the registry path cannot delete it."
                ),
                "device": _summarize_device(dev, areas),
                "config_entries": entry_rows,
                "alternatives": caveats
                or [
                    "Delete the whole config entry for this integration, or "
                    "remove the device from the integration's own UI."
                ],
            }

        tk = ctx.safety.create_token(
            action="device_remove",
            details={
                "device_id": dev_id,
                "name": _device_display_name(dev),
                "entry_ids": removable,
                "entity_ids": owned,
            },
        )
        return {
            "device": _summarize_device(dev, areas),
            "entities_to_remove": owned,
            "entity_count": len(owned),
            "config_entries": entry_rows,
            "will_unlink_entries": removable,
            "caveats": caveats,
            "warning": (
                "IRREVERSIBLE — haops_rollback cannot recreate a device. "
                f"{len(owned)} entities disappear with it, and their history "
                "becomes orphaned (states rows keep the old entity_id)."
            ),
            "token": tk.id,
            "message": "Review the above. Call again with confirm=true and "
            "this token to remove the device.",
        }

    # Phase 2: execute
    if token is None:
        return {"error": "confirm=true requires a token"}
    try:
        token_data = ctx.safety.claim_token(token)
    except Exception as e:
        return {"error": str(e)}

    if token_data.action != "device_remove":
        return {
            "error": (
                f"Token action mismatch: expected 'device_remove', got "
                f"{token_data.action!r}."
            )
        }

    dev_id = str(token_data.details.get("device_id"))
    # Bind the token to the device it previewed: re-resolving `device` here
    # could land on a different match if the registry changed in between.
    devices = await _get_device_registry(ctx, fresh=True)
    match, err = _resolve_device(devices, device, {})
    if err is not None:
        return err
    assert match is not None
    if match.get("id") != dev_id:
        return {
            "error": (
                f"'{device}' now resolves to device {match.get('id')} but the "
                f"token was issued for {dev_id}. Re-run the preview."
            )
        }

    entry_ids: list[str] = list(token_data.details.get("entry_ids") or [])
    entity_ids: list[str] = list(token_data.details.get("entity_ids") or [])

    unlinked: list[str] = []
    errors: list[dict[str, str]] = []
    for entry_id in entry_ids:
        try:
            await ctx.ws.send_command(
                "config/device_registry/remove_config_entry",
                device_id=dev_id,
                config_entry_id=entry_id,
            )
            unlinked.append(entry_id)
        except WebSocketError as e:
            errors.append({"entry_id": entry_id, "error": str(e)})

    # Verify against live state, not the .storage file — HA has not flushed it
    # yet at this point by definition.
    after = await _get_device_registry(ctx, fresh=True)
    still_there = next((d for d in after if d.get("id") == dev_id), None)
    remaining = device_config_entry_ids(still_there) if still_there else []

    await ctx.audit.log(
        tool="device_remove",
        details={
            "device_id": dev_id,
            "name": token_data.details.get("name"),
            "unlinked_entries": unlinked,
            "entities_removed": entity_ids,
            "device_gone": still_there is None,
            "errors": errors,
        },
        success=not errors and still_there is None,
        token_id=token,
    )

    result: dict[str, Any] = {
        "success": not errors and still_there is None,
        "device_id": dev_id,
        "name": token_data.details.get("name"),
        "removed_from_entries": unlinked,
        "remaining_entries": remaining,
        "device_gone": still_there is None,
        "entities_removed": entity_ids,
        "rollback": (
            "Not available — device removal cannot be undone by haops_rollback."
        ),
    }
    if errors:
        result["errors"] = errors
    if still_there is not None and not errors:
        result["note"] = (
            "Device still present: it retains config entries that do not "
            "support device removal. Remaining entries listed above."
        )
    return result
