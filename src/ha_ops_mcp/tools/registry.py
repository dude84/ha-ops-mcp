"""Registry tools — haops_registry_query.

Generic filesystem-first access to HA's .storage/core.* registries.
Replaces a number of bespoke list tools with a single primitive.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ha_ops_mcp.server import registry

if TYPE_CHECKING:
    from ha_ops_mcp.server import HaOpsContext

logger = logging.getLogger(__name__)


# Registry spec: file path (relative to config root), data key, WS fallback command
# WS fallback is None for registries that have no WS list endpoint.
_REGISTRIES: dict[str, dict[str, Any]] = {
    "devices": {
        "file": ".storage/core.device_registry",
        "data_key": "devices",
        "ws_command": "config/device_registry/list",
        "summary_fields": [
            "id", "name", "name_by_user", "manufacturer", "model",
            "sw_version", "hw_version", "area_id", "disabled_by",
        ],
    },
    "entities": {
        "file": ".storage/core.entity_registry",
        "data_key": "entities",
        "ws_command": "config/entity_registry/list",
        "summary_fields": [
            "entity_id", "name", "original_name", "platform", "device_id",
            "area_id", "disabled_by", "hidden_by",
        ],
    },
    "areas": {
        "file": ".storage/core.area_registry",
        "data_key": "areas",
        "ws_command": "config/area_registry/list",
        "summary_fields": [
            "id", "name", "floor_id", "icon", "aliases", "labels",
        ],
    },
    "floors": {
        "file": ".storage/core.floor_registry",
        "data_key": "floors",
        "ws_command": "config/floor_registry/list",
        "summary_fields": ["floor_id", "name", "level", "icon", "aliases"],
    },
    "config_entries": {
        "file": ".storage/core.config_entries",
        "data_key": "entries",
        "ws_command": None,
        "summary_fields": [
            "entry_id", "domain", "title", "state", "source",
            "disabled_by", "reason",
        ],
    },
}


async def _load_registry(
    ctx: HaOpsContext, name: str
) -> list[dict[str, Any]]:
    """Load a registry, filesystem-first with optional WebSocket fallback."""
    spec = _REGISTRIES[name]
    storage_path = Path(ctx.config.filesystem.config_root) / spec["file"]

    try:
        content = storage_path.read_text()
        data = json.loads(content)
        records = data.get("data", {}).get(spec["data_key"], [])
        if isinstance(records, list):
            return records
    except (FileNotFoundError, KeyError, json.JSONDecodeError):
        pass

    # Fall back to WebSocket if available
    if spec["ws_command"]:
        try:
            from ha_ops_mcp.connections.websocket import WebSocketError
            result: Any = await ctx.ws.send_command(spec["ws_command"])
            if isinstance(result, list):
                return result
        except WebSocketError as e:
            raise RuntimeError(
                f"Registry '{name}' unavailable via filesystem or WebSocket: {e}"
            ) from e

    raise RuntimeError(
        f"Registry '{name}' unavailable — file {spec['file']} not found "
        "and no WebSocket fallback for this registry"
    )


def _record_matches(
    record: dict[str, Any], filter_: dict[str, Any]
) -> bool:
    """Case-insensitive substring match on every filter field.

    For list values (identifiers, aliases, labels, connections), match if
    ANY element's string form contains the query. For scalars, stringify
    and substring-match.
    """
    for key, query in filter_.items():
        value = record.get(key)
        q = str(query).lower()

        if value is None:
            return False

        if isinstance(value, (list, tuple)):
            if not any(q in str(item).lower() for item in value):
                return False
        elif isinstance(value, dict):
            if q not in str(value).lower():
                return False
        else:
            if q not in str(value).lower():
                return False

    return True


def _project(
    record: dict[str, Any],
    fields: list[str] | None,
    summary_fields: list[str],
) -> dict[str, Any]:
    """Pick only the requested fields from a record."""
    selected = fields if fields else summary_fields
    return {k: record.get(k) for k in selected}


@registry.tool(
    name="haops_registry_query",
    description=(
        "Generic access to HA's .storage/core.* registries. "
        "Filesystem-first, WebSocket fallback where available. "
        "Supported registries: 'devices', 'entities', 'areas', 'floors', "
        "'config_entries'. "
        "Parameters: registry (string, required), "
        "filter (dict, optional — case-insensitive substring match per field, "
        "e.g. {'name': 'blaster', 'manufacturer': 'xiaomi'}), "
        "fields (list of strings — projection, default returns summary), "
        "limit (int, default 100 — max records returned), "
        "offset (int, default 0), "
        "count_only (bool, default false — skip records, return just total). "
        "Returns: {registry, total, returned, results, truncated}. "
        "Use this to answer 'what devices/entities/areas/floors exist' and "
        "'which integrations are in setup_error state' without shell fallback."
    ),
    params={
        "registry": {
            "type": "string",
            "description": "Which registry: devices, entities, areas, floors, config_entries",
        },
        "filter": {
            "type": "object",
            "description": "Field→query pairs (case-insensitive substring match)",
        },
        "fields": {
            "type": "array",
            "description": "Keys to include in each record (projection)",
        },
        "limit": {
            "type": "integer",
            "description": "Max records to return",
            "default": 100,
        },
        "offset": {
            "type": "integer",
            "description": "Skip the first N matches",
            "default": 0,
        },
        "count_only": {
            "type": "boolean",
            "description": "Return only the count",
            "default": False,
        },
    },
)
async def haops_registry_query(
    ctx: HaOpsContext,
    registry: str,
    filter: dict[str, Any] | None = None,
    fields: list[str] | None = None,
    limit: int = 100,
    offset: int = 0,
    count_only: bool = False,
) -> dict[str, Any]:
    if registry not in _REGISTRIES:
        return {
            "error": f"Unknown registry '{registry}'",
            "supported": list(_REGISTRIES.keys()),
        }

    spec = _REGISTRIES[registry]
    records = await _load_registry(ctx, registry)

    # Filter
    matched = (
        [r for r in records if _record_matches(r, filter)]
        if filter else list(records)
    )

    total = len(matched)

    if count_only:
        return {"registry": registry, "total": total, "count": total}

    # Paginate
    start = max(0, offset)
    end = start + limit
    page = matched[start:end]
    truncated = total > end

    # Project
    results = [_project(r, fields, spec["summary_fields"]) for r in page]

    return {
        "registry": registry,
        "total": total,
        "returned": len(results),
        "results": results,
        "truncated": truncated,
    }
