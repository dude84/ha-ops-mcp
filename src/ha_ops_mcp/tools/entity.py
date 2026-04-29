"""Entity tools — list, audit, remove, disable."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ha_ops_mcp.connections.rest import RestClientError
from ha_ops_mcp.safety.rollback import UndoEntry, UndoType
from ha_ops_mcp.server import registry

if TYPE_CHECKING:
    from ha_ops_mcp.server import HaOpsContext

logger = logging.getLogger(__name__)


async def _get_entity_registry(ctx: HaOpsContext) -> list[dict[str, Any]]:
    """Get entity registry, preferring filesystem, falling back to WebSocket.

    Note: HA removed /api/config/entity_registry from the REST API — it's
    WS-only now (`config/entity_registry/list`).
    """
    # Tier 1: direct file read (fastest, no HA involvement)
    storage_path = Path(ctx.config.filesystem.config_root) / ".storage" / "core.entity_registry"
    try:
        content = storage_path.read_text()
        data = json.loads(content)
        return data["data"]["entities"]  # type: ignore[no-any-return]
    except (FileNotFoundError, KeyError, json.JSONDecodeError):
        pass

    # Tier 2: WebSocket fallback (REST endpoint was removed in recent HA versions)
    try:
        from ha_ops_mcp.connections.websocket import WebSocketError
        result: Any = await ctx.ws.send_command("config/entity_registry/list")
        if isinstance(result, list):
            return result
        return []
    except WebSocketError as e:
        raise RuntimeError(
            f"Entity registry unavailable via filesystem or WebSocket: {e}"
        ) from e


async def _get_states(ctx: HaOpsContext) -> dict[str, dict[str, Any]]:
    """Get current entity states, keyed by entity_id."""
    try:
        states = await ctx.rest.get("/api/states")
        return {s["entity_id"]: s for s in states}
    except RestClientError:
        return {}


@registry.tool(
    name="haops_entity_state",
    description=(
        "Get full state + attributes for one or more entities. "
        "This is the primary diagnostic tool: for climate, media_player, "
        "light, sensor, weather, etc., the useful data lives in attributes "
        "(current_temperature, target_temperature, hvac_modes, brightness, "
        "unit_of_measurement, etc.) — not in the state string alone. "
        "Parameters: entity_id (string or list of strings — single id or batch), "
        "attributes (list of strings — projection: only include these "
        "attribute keys; omit all attributes with attributes=[]; default "
        "returns all attributes). "
        "Returns per entity: {entity_id, state, attributes, last_changed, "
        "last_updated} or an error field if the entity doesn't exist. "
        "For many entities, use the batch form to avoid N round-trips. "
        "For large entities (weather queues, media_player playlists), use "
        "the attributes projection to cap payload size."
    ),
    params={
        "entity_id": {
            "type": ["string", "array"],
            "description": "Single entity_id or a list of entity_ids",
        },
        "attributes": {
            "type": "array",
            "description": (
                "Attribute keys to include (projection; [] = none; omit = all)"
            ),
        },
    },
)
async def haops_entity_state(
    ctx: HaOpsContext,
    entity_id: str | list[str],
    attributes: list[str] | None = None,
) -> dict[str, Any]:
    from ha_ops_mcp.connections.rest import RestClientError

    ids = [entity_id] if isinstance(entity_id, str) else list(entity_id)
    if not ids:
        return {"error": "No entity_id provided"}

    results: list[dict[str, Any]] = []
    for eid in ids:
        try:
            raw = await ctx.rest.get(f"/api/states/{eid}")
        except RestClientError as e:
            results.append({"entity_id": eid, "error": str(e)[:200]})
            continue

        raw_attrs = raw.get("attributes", {}) if isinstance(raw, dict) else {}
        if attributes is not None:
            projected = {k: raw_attrs.get(k) for k in attributes}
        else:
            projected = raw_attrs

        results.append({
            "entity_id": raw.get("entity_id", eid),
            "state": raw.get("state"),
            "attributes": projected,
            "last_changed": raw.get("last_changed"),
            "last_updated": raw.get("last_updated"),
        })

    # Unwrap single-entity form for convenience — matches caller intent
    if isinstance(entity_id, str):
        return results[0]
    return {"entities": results, "count": len(results)}


@registry.tool(
    name="haops_entity_list",
    description=(
        "List Home Assistant entities with rich filtering. "
        "Prefers reading the entity registry from filesystem "
        "(.storage/core.entity_registry), WebSocket fallback. "
        "Cross-references with current states. "
        "Parameters (all optional): domain (string, e.g. 'sensor', 'light'), "
        "area (string — area_id or area name), "
        "area_mode (string: 'effective' (default) | 'entity' | 'device') — "
        "'effective' uses entity.area_id OR device.area_id (matches HA's UI "
        "behavior); 'entity' is strict entity-level; 'device' matches when "
        "the linked device's area matches, "
        "state (string, e.g. 'unavailable', 'unknown'), "
        "integration (string, e.g. 'hue', 'mqtt'), "
        "stale_days (int — entities with no state change in N+ days), "
        "limit (int — cap output; WARNING: unbounded output can exceed LLM "
        "tool-result size limits on large instances, recommend limit=100), "
        "offset (int, default 0), "
        "count_only (bool, default false — return just the count), "
        "fields (list of strings — projection: keys to include in each "
        "entity record; overrides the default summary). "
        "full (bool, default false) — when true, returns the verbose 8-field "
        "summary (entity_id, friendly_name, state, last_changed, area_id, "
        "platform, device_id, disabled_by) per entity. Default returns a "
        "tight 3-field summary (entity_id, friendly_name, state) so large "
        "queries (hundreds of entities) stay under MCP result-size limits. "
        "For device-filtered queries, use haops_registry_query or "
        "haops_device_info (which returns linked entities in one call). "
        "Returns: {entities, count, total, returned, truncated}."
    ),
    params={
        "domain": {"type": "string", "description": "Filter by domain (e.g. 'sensor')"},
        "area": {"type": "string", "description": "Filter by area name/id"},
        "area_mode": {
            "type": "string",
            "description": "effective (default) | entity | device",
            "default": "effective",
        },
        "state": {"type": "string", "description": "Filter by current state value"},
        "integration": {
            "type": "string", "description": "Filter by integration/platform",
        },
        "stale_days": {
            "type": "integer", "description": "Only entities unchanged for N+ days",
        },
        "limit": {
            "type": "integer",
            "description": "Max entities to return (recommend 100 on large instances)",
        },
        "offset": {
            "type": "integer", "description": "Skip the first N results", "default": 0,
        },
        "count_only": {
            "type": "boolean",
            "description": "Return only the count (no entity records)",
            "default": False,
        },
        "fields": {
            "type": "array",
            "description": "Keys to include in each entity record (projection)",
        },
        "full": {
            "type": "boolean",
            "description": "Return verbose 8-field summary (default 3-field)",
            "default": False,
        },
    },
)
async def haops_entity_list(
    ctx: HaOpsContext,
    domain: str | None = None,
    area: str | None = None,
    area_mode: str = "effective",
    state: str | None = None,
    integration: str | None = None,
    stale_days: int | None = None,
    limit: int | None = None,
    offset: int = 0,
    count_only: bool = False,
    fields: list[str] | None = None,
    full: bool = False,
) -> dict[str, Any]:
    entities = await _get_entity_registry(ctx)
    states = await _get_states(ctx)

    # Resolve area filter: accept name or id, and optionally inherit from device
    area_id_filter: str | None = None
    device_area_map: dict[str, str | None] = {}
    if area:
        from ha_ops_mcp.tools.device import _get_area_registry, _get_device_registry
        areas = await _get_area_registry(ctx)

        # Resolve name → id
        area_id_filter = area
        if area not in areas:
            for aid, a in areas.items():
                if a.get("name", "").lower() == area.lower():
                    area_id_filter = aid
                    break

        # Device-area map for effective/device modes
        if area_mode in ("effective", "device"):
            devices = await _get_device_registry(ctx)
            device_area_map = {d["id"]: d.get("area_id") for d in devices}

    results: list[dict[str, Any]] = []
    import time

    now = time.time()

    for entity in entities:
        eid = entity.get("entity_id", "")

        # Domain filter
        if domain and not eid.startswith(f"{domain}."):
            continue

        # Area filter (effective/entity/device)
        if area_id_filter is not None:
            entity_area = entity.get("area_id")
            device_id = entity.get("device_id")
            device_area = device_area_map.get(device_id) if device_id else None

            if area_mode == "entity":
                match = entity_area == area_id_filter
            elif area_mode == "device":
                match = device_area == area_id_filter
            else:  # effective
                effective = entity_area or device_area
                match = effective == area_id_filter
            if not match:
                continue

        # Integration filter
        if integration and entity.get("platform") != integration:
            continue

        entity_state = states.get(eid, {})
        current_state = entity_state.get("state")

        # State filter
        if state and current_state != state:
            continue

        last_changed = entity_state.get("last_changed")

        # Stale filter
        if stale_days is not None and last_changed:
            from datetime import datetime
            try:
                changed_dt = datetime.fromisoformat(last_changed.replace("Z", "+00:00"))
                age_days = (now - changed_dt.timestamp()) / 86400
                if age_days < stale_days:
                    continue
            except (ValueError, TypeError):
                pass

        friendly = (
            entity_state.get("attributes", {}).get("friendly_name")
            or entity.get("name")
            or entity.get("original_name")
        )
        if fields:
            # Explicit projection — caller knows what they want
            full_record = {
                "entity_id": eid,
                "friendly_name": friendly,
                "state": current_state,
                "last_changed": last_changed,
                "area_id": entity.get("area_id"),
                "platform": entity.get("platform"),
                "device_id": entity.get("device_id"),
                "disabled_by": entity.get("disabled_by"),
            }
            summary = {k: v for k, v in full_record.items() if k in fields}
        elif full:
            # Verbose summary — opt-in for callers who can handle the size
            summary = {
                "entity_id": eid,
                "friendly_name": friendly,
                "state": current_state,
                "last_changed": last_changed,
                "area_id": entity.get("area_id"),
                "platform": entity.get("platform"),
                "device_id": entity.get("device_id"),
                "disabled_by": entity.get("disabled_by"),
            }
        else:
            # Default tight summary — keeps large-area queries under MCP result limits
            summary = {
                "entity_id": eid,
                "friendly_name": friendly,
                "state": current_state,
            }
        results.append(summary)

    total = len(results)

    if count_only:
        return {"count": total, "total": total}

    # Apply offset + limit
    start = max(0, offset)
    end = start + limit if limit is not None else None
    page = results[start:end]
    truncated = limit is not None and total > start + limit

    return {
        "entities": page,
        "count": len(page),
        "returned": len(page),
        "total": total,
        "truncated": truncated,
    }


@registry.tool(
    name="haops_entity_find",
    description=(
        "Fuzzy search for entities by keyword across entity_id, friendly_name, "
        "device name, and area name. Use this when you only know a partial "
        "name or a keyword (e.g. 'kitchen dehumidifier') and don't know which "
        "domain/area filter would hit. Collapses the typical 'try domain, "
        "try integration, try area' loop into one call. "
        "Prefers filesystem reads (.storage/core.entity_registry, "
        "core.device_registry, core.area_registry); REST is queried for live "
        "friendly_name overrides (best-effort). "
        "When you already know the exact axis (domain, area, integration), "
        "haops_entity_list is faster and exact. When you have a precise "
        "entity_id, use haops_entity_state directly. "
        "Parameters: query (string, required), "
        "limit (int, default 20 — top-N by score), "
        "threshold (int 0-100, default 50 — minimum score), "
        "domain (string, optional — pre-filter by domain). "
        "Returns: {matches: [{entity_id, friendly_name, score, "
        "matched_field, area_id, area_name, device_id, device_name, "
        "platform}], count, query, threshold}."
    ),
    params={
        "query": {
            "type": "string",
            "description": "Search keyword(s)",
        },
        "limit": {
            "type": "integer",
            "description": "Max matches to return (top-N by score)",
            "default": 20,
        },
        "threshold": {
            "type": "integer",
            "description": "Minimum match score (0-100, default 50)",
            "default": 50,
        },
        "domain": {
            "type": "string",
            "description": "Optional domain pre-filter (e.g. 'sensor')",
        },
    },
)
async def haops_entity_find(
    ctx: HaOpsContext,
    query: str,
    limit: int = 20,
    threshold: int = 50,
    domain: str | None = None,
) -> dict[str, Any]:
    if not query or not query.strip():
        return {
            "matches": [],
            "count": 0,
            "query": query,
            "threshold": threshold,
            "error": "query is required and must be non-empty",
        }

    from rapidfuzz import fuzz

    from ha_ops_mcp.tools.device import _get_area_registry, _get_device_registry

    entities = await _get_entity_registry(ctx)
    devices = await _get_device_registry(ctx)
    areas = await _get_area_registry(ctx)
    states = await _get_states(ctx)

    device_by_id: dict[str, dict[str, Any]] = {
        d["id"]: d for d in devices if d.get("id")
    }

    def _normalize(s: str) -> str:
        # Replace separators with space so token-based matchers see
        # `kitchen_dehumidifier` and `kitchen dehumidifier` the same.
        return s.replace("_", " ").replace(".", " ").replace("-", " ").lower().strip()

    q_norm = _normalize(query)

    # Per-field weights — friendly_name boosted because it's what users type.
    field_weights = {
        "entity_id": 1.0,
        "friendly_name": 1.2,
        "device_name": 0.9,
        "area_name": 0.7,
    }

    results: list[dict[str, Any]] = []

    for entity in entities:
        eid = entity.get("entity_id", "")
        if not eid:
            continue
        if domain and not eid.startswith(f"{domain}."):
            continue

        device_id = entity.get("device_id")
        device = device_by_id.get(device_id) if device_id else None
        device_name = (
            (device.get("name_by_user") or device.get("name")) if device else None
        )

        # Effective area (entity-level wins, falls back to linked device)
        area_id = entity.get("area_id") or (device.get("area_id") if device else None)
        area_name = None
        if area_id and area_id in areas:
            area_name = areas[area_id].get("name")

        state_info = states.get(eid, {})
        friendly = (
            state_info.get("attributes", {}).get("friendly_name")
            or entity.get("name")
            or entity.get("original_name")
        )

        candidates: list[tuple[str, str | None]] = [
            ("entity_id", eid),
            ("friendly_name", friendly),
            ("device_name", device_name),
            ("area_name", area_name),
        ]

        best_score = 0.0
        best_field = ""
        for field, value in candidates:
            if not value:
                continue
            raw = fuzz.WRatio(q_norm, _normalize(str(value)))
            weighted = raw * field_weights[field]
            if weighted > best_score:
                best_score = weighted
                best_field = field

        # Cap reported score at 100 — the weight boost for friendly_name
        # can exceed 100 internally, but the LLM expects a 0-100 scale.
        reported_score = min(round(best_score, 1), 100.0)
        if reported_score < threshold:
            continue

        results.append({
            "entity_id": eid,
            "friendly_name": friendly,
            "score": reported_score,
            "matched_field": best_field,
            "area_id": area_id,
            "area_name": area_name,
            "device_id": device_id,
            "device_name": device_name,
            "platform": entity.get("platform"),
        })

    results.sort(key=lambda r: -r["score"])
    truncated = len(results) > limit
    page = results[:limit]

    return {
        "matches": page,
        "count": len(page),
        "total": len(results),
        "truncated": truncated,
        "query": query,
        "threshold": threshold,
    }


@registry.tool(
    name="haops_entity_audit",
    description=(
        "Comprehensive entity health report. Categorizes entities into: "
        "unavailable (with last_changed if available), orphaned (no device AND no area), "
        "stale (no state change in 30+ days), duplicate friendly names, "
        "entities with no friendly_name set, and area_ratio_outliers (areas "
        "where entities-per-device is unusually high — e.g. an integration "
        "like pfSense or UPS that registers hundreds of sensors against a "
        "single device assigned to that area, distorting the area's apparent "
        "scale). "
        "Read-only, no parameters. Use this to identify cleanup opportunities."
    ),
)
async def haops_entity_audit(ctx: HaOpsContext) -> dict[str, Any]:
    from ha_ops_mcp.tools.device import _get_area_registry, _get_device_registry

    entities = await _get_entity_registry(ctx)
    states = await _get_states(ctx)
    devices = await _get_device_registry(ctx)
    areas = await _get_area_registry(ctx)

    import time
    from collections import Counter, defaultdict

    now = time.time()

    # device_id → area_id, used for effective-area resolution and the
    # area_ratio_outliers signal.
    device_area_map: dict[str, str | None] = {
        d["id"]: d.get("area_id") for d in devices if d.get("id")
    }
    area_device_counts: dict[str, int] = defaultdict(int)
    for d in devices:
        if d.get("disabled_by"):
            continue
        aid = d.get("area_id")
        if aid:
            area_device_counts[aid] += 1

    unavailable: list[dict[str, Any]] = []
    orphaned: list[dict[str, Any]] = []
    stale: list[dict[str, Any]] = []
    no_name: list[str] = []
    name_counts: Counter[str] = Counter()
    area_entity_counts: dict[str, int] = defaultdict(int)

    for entity in entities:
        eid = entity.get("entity_id", "")
        if entity.get("disabled_by"):
            continue  # Skip disabled entities

        entity_state = states.get(eid, {})
        current_state = entity_state.get("state")
        friendly = (
            entity_state.get("attributes", {}).get("friendly_name")
            or entity.get("name")
            or entity.get("original_name")
        )

        # Unavailable
        if current_state == "unavailable":
            unavailable.append({
                "entity_id": eid,
                "friendly_name": friendly,
                "last_changed": entity_state.get("last_changed"),
                "platform": entity.get("platform"),
            })

        # Orphaned (no device AND no area)
        if not entity.get("device_id") and not entity.get("area_id"):
            orphaned.append({
                "entity_id": eid,
                "friendly_name": friendly,
                "platform": entity.get("platform"),
            })

        # Stale (30+ days without change)
        last_changed = entity_state.get("last_changed")
        if last_changed:
            from datetime import datetime
            try:
                changed_dt = datetime.fromisoformat(last_changed.replace("Z", "+00:00"))
                age_days = (now - changed_dt.timestamp()) / 86400
                if age_days >= 30:
                    stale.append({
                        "entity_id": eid,
                        "friendly_name": friendly,
                        "days_since_change": round(age_days),
                        "last_state": current_state,
                    })
            except (ValueError, TypeError):
                pass

        # Name tracking
        if friendly:
            name_counts[friendly] += 1
        else:
            no_name.append(eid)

        # Per-area entity tally (effective area: entity OR linked device)
        eaid = entity.get("area_id")
        device_id = entity.get("device_id")
        daid = device_area_map.get(device_id) if isinstance(device_id, str) else None
        effective_area = eaid or daid
        if effective_area:
            area_entity_counts[effective_area] += 1

    # Duplicate names
    duplicates: dict[str, int] = {
        name: count for name, count in name_counts.items() if count > 1
    }

    # Area entity:device ratio outliers — flag areas where a small device
    # count maps to a large entity count (typical of integrations like
    # pfSense, UPS monitors, or weather services that register hundreds
    # of sensors against one device assigned to that area).
    #
    # Threshold: ratio > max(3 × cross-area median, 20:1) AND ≥10 entities.
    # The absolute floor (20) and entity floor (10) keep small/sparse
    # areas from false-positiving when one well-instrumented device gives
    # an area an "infinite" ratio.
    ratios: list[tuple[str, int, int, float]] = []
    for aid in set(area_device_counts) | set(area_entity_counts):
        de = area_device_counts.get(aid, 0)
        en = area_entity_counts.get(aid, 0)
        if de > 0 and en >= 10:
            ratios.append((aid, en, de, en / de))

    area_ratio_outliers: list[dict[str, Any]] = []
    if len(ratios) >= 3:
        sorted_ratios = sorted(r[3] for r in ratios)
        median = sorted_ratios[len(sorted_ratios) // 2]
        threshold = max(median * 3, 20.0)
        for aid, en, de, ratio in sorted(ratios, key=lambda r: -r[3]):
            if ratio > threshold:
                area_ratio_outliers.append({
                    "area_id": aid,
                    "area_name": (areas.get(aid) or {}).get("name") or aid,
                    "entities": en,
                    "devices": de,
                    "ratio": round(ratio, 1),
                })

    return {
        "summary": {
            "total_entities": len(entities),
            "unavailable": len(unavailable),
            "orphaned": len(orphaned),
            "stale_30d": len(stale),
            "duplicate_names": len(duplicates),
            "no_friendly_name": len(no_name),
            "area_ratio_outliers": len(area_ratio_outliers),
        },
        "unavailable": unavailable,
        "orphaned": orphaned,
        "stale": stale,
        "duplicate_names": duplicates,
        "no_friendly_name": no_name,
        "area_ratio_outliers": area_ratio_outliers,
    }


@registry.tool(
    name="haops_entity_remove",
    description=(
        "Remove entities from the HA entity registry. Two-phase: "
        "1) Call without confirm to preview what will be removed. "
        "2) Call with confirm=true and the token to execute. "
        "Creates rollback savepoints so removals can be undone within "
        "the session (best-effort — integration must still exist). "
        "Parameters: entity_ids (list of strings, required), "
        "confirm (bool, default false), token (string, if confirming)."
    ),
    params={
        "entity_ids": {
            "type": "array",
            "description": "Entity IDs to remove",
        },
        "confirm": {
            "type": "boolean", "description": "Execute removal",
            "default": False,
        },
        "token": {
            "type": "string",
            "description": "Confirmation token from preview step",
        },
    },
)
async def haops_entity_remove(
    ctx: HaOpsContext,
    entity_ids: list[str],
    confirm: bool = False,
    token: str | None = None,
) -> dict[str, Any]:
    if not entity_ids:
        return {"error": "No entity_ids provided"}

    entities = await _get_entity_registry(ctx)
    states = await _get_states(ctx)

    # Find matching entities
    to_remove: list[dict[str, Any]] = []
    not_found: list[str] = []
    for eid in entity_ids:
        entry = next((e for e in entities if e.get("entity_id") == eid), None)
        if entry:
            state_info = states.get(eid, {})
            to_remove.append({
                "entity_id": eid,
                "friendly_name": (
                    state_info.get("attributes", {}).get("friendly_name")
                    or entry.get("name")
                ),
                "platform": entry.get("platform"),
                "device_id": entry.get("device_id"),
                "area_id": entry.get("area_id"),
                "registry_entry": entry,
            })
        else:
            not_found.append(eid)

    if not confirm:
        tk = ctx.safety.create_token(
            action="entity_remove",
            details={
                "entity_ids": entity_ids,
                "entries": [r["registry_entry"] for r in to_remove],
            },
        )
        return {
            "preview": [
                {k: v for k, v in r.items() if k != "registry_entry"}
                for r in to_remove
            ],
            "not_found": not_found,
            "token": tk.id,
            "message": "Review entities above. Call again with "
            "confirm=true and this token to remove.",
        }

    # Phase 2: execute
    if token is None:
        return {"error": "confirm=true requires a token"}

    try:
        token_data = ctx.safety.validate_token(token)
    except Exception as e:
        return {"error": str(e)}

    entries = token_data.details.get("entries", [])

    # Backup entities before removal
    if entries:
        await ctx.backup.backup_entities(entries, operation="entity_remove")

    from ha_ops_mcp.connections.websocket import WebSocketError

    txn = ctx.rollback.begin("entity_remove")

    removed: list[str] = []
    errors: list[dict[str, str]] = []
    for entry in entries:
        eid = entry.get("entity_id", "")
        txn.savepoint(
            name=f"remove:{eid}",
            undo=UndoEntry(
                type=UndoType.ENTITY,
                description=f"Restore entity {eid}",
                data={"entity_id": eid, "registry_entry": entry},
            ),
        )
        # HA removed DELETE /api/config/entity_registry/<id> from the REST
        # API; WS config/entity_registry/remove is the only working path.
        try:
            await ctx.ws.send_command(
                "config/entity_registry/remove",
                entity_id=eid,
            )
            removed.append(eid)
        except WebSocketError as e:
            errors.append({"entity_id": eid, "error": str(e)})

    ctx.safety.consume_token(token)
    ctx.rollback.commit(txn.id)

    await ctx.audit.log(
        tool="entity_remove",
        details={"removed": removed, "errors": errors},
        success=not errors,
        token_id=token,
    )

    return {
        "success": not errors,
        "removed": removed,
        "errors": errors,
        "transaction_id": txn.id,
    }


@registry.tool(
    name="haops_entity_disable",
    description=(
        "Bulk disable entities in the HA entity registry. Two-phase: "
        "1) Call without confirm to preview. "
        "2) Call with confirm=true and the token to execute. "
        "Disabled entities stop updating and free resources. "
        "Parameters: entity_ids (list of strings, required), "
        "confirm (bool, default false), token (string, if confirming)."
    ),
    params={
        "entity_ids": {
            "type": "array",
            "description": "Entity IDs to disable",
        },
        "confirm": {
            "type": "boolean", "description": "Execute disable",
            "default": False,
        },
        "token": {
            "type": "string",
            "description": "Confirmation token from preview step",
        },
    },
)
async def haops_entity_disable(
    ctx: HaOpsContext,
    entity_ids: list[str],
    confirm: bool = False,
    token: str | None = None,
) -> dict[str, Any]:
    if not entity_ids:
        return {"error": "No entity_ids provided"}

    entities = await _get_entity_registry(ctx)
    states = await _get_states(ctx)

    to_disable: list[dict[str, Any]] = []
    already_disabled: list[str] = []
    not_found: list[str] = []

    for eid in entity_ids:
        entry = next((e for e in entities if e.get("entity_id") == eid), None)
        if not entry:
            not_found.append(eid)
        elif entry.get("disabled_by"):
            already_disabled.append(eid)
        else:
            state_info = states.get(eid, {})
            to_disable.append({
                "entity_id": eid,
                "friendly_name": (
                    state_info.get("attributes", {}).get("friendly_name")
                    or entry.get("name")
                ),
                "platform": entry.get("platform"),
                "current_state": state_info.get("state"),
            })

    if not confirm:
        tk = ctx.safety.create_token(
            action="entity_disable",
            details={"entity_ids": [d["entity_id"] for d in to_disable]},
        )
        return {
            "preview": to_disable,
            "already_disabled": already_disabled,
            "not_found": not_found,
            "token": tk.id,
            "message": "Review entities above. Call again with "
            "confirm=true and this token to disable.",
        }

    # Phase 2: execute
    if token is None:
        return {"error": "confirm=true requires a token"}

    try:
        token_data = ctx.safety.validate_token(token)
    except Exception as e:
        return {"error": str(e)}

    target_ids = token_data.details.get("entity_ids", [])

    from ha_ops_mcp.connections.websocket import WebSocketError

    txn = ctx.rollback.begin("entity_disable")

    disabled: list[str] = []
    errors: list[dict[str, str]] = []
    for eid in target_ids:
        txn.savepoint(
            name=f"disable:{eid}",
            undo=UndoEntry(
                type=UndoType.ENTITY,
                description=f"Re-enable entity {eid}",
                data={"entity_id": eid, "action": "enable"},
            ),
        )
        # HA removed POST /api/config/entity_registry/<id> from the REST API;
        # the only working path is WS config/entity_registry/update.
        try:
            await ctx.ws.send_command(
                "config/entity_registry/update",
                entity_id=eid,
                disabled_by="user",
            )
            disabled.append(eid)
        except WebSocketError as e:
            errors.append({"entity_id": eid, "error": str(e)})

    ctx.safety.consume_token(token)
    ctx.rollback.commit(txn.id)

    await ctx.audit.log(
        tool="entity_disable",
        details={"disabled": disabled, "errors": errors},
        success=not errors,
        token_id=token,
    )

    return {
        # `success` reflects whether every per-entity call succeeded — a
        # 100%-failure run no longer reports success: true.
        "success": not errors,
        "disabled": disabled,
        "errors": errors,
        "transaction_id": txn.id,
    }
