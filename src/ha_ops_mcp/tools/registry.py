"""Registry tools — haops_registry_query.

Generic filesystem-first access to HA's .storage/core.* registries.
Replaces a number of bespoke list tools with a single primitive.

Reads go through `storage_registry.load_registry`, which reports provenance
and escapes to the live WebSocket registry when the .storage file provably
predates a write this session made (see that module's docstring).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ha_ops_mcp.server import registry
from ha_ops_mcp.storage_registry import REGISTRY_SPECS, load_registry

if TYPE_CHECKING:
    from ha_ops_mcp.server import HaOpsContext

logger = logging.getLogger(__name__)


# Default projection per registry. Where each registry lives (file, data key,
# WS fallback) is declared once in storage_registry.REGISTRY_SPECS.
_SUMMARY_FIELDS: dict[str, list[str]] = {
    "devices": [
        "id", "name", "name_by_user", "manufacturer", "model",
        "sw_version", "hw_version", "area_id", "disabled_by",
        # HA 2026.8 (device registry storage v3.2): the plural `config_entries`
        # list left storage in favour of these. Projecting them by default
        # keeps "which integration owns this device" answerable from a summary.
        "config_entry_id", "primary_config_entry", "composite_device_id",
    ],
    "entities": [
        "entity_id", "name", "original_name", "platform", "device_id",
        "area_id", "disabled_by", "hidden_by",
    ],
    "areas": [
        "id", "name", "floor_id", "icon", "aliases", "labels",
    ],
    "floors": ["floor_id", "name", "level", "icon", "aliases"],
    "config_entries": [
        "entry_id", "domain", "title", "state", "source",
        "disabled_by", "reason",
    ],
}


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
        "count_only (bool, default false — skip records, return just total), "
        "fresh (bool, default false — read HA's live in-memory registry over "
        "WebSocket instead of the .storage file). "
        "Returns: {registry, total, returned, results, truncated, provenance}. "
        "PROVENANCE: HA flushes .storage on a debounce, so the file lags live "
        "state right after a change. provenance reports {source: file|"
        "websocket, file_age_seconds, notes} — and a read is auto-escalated to "
        "WebSocket when the file provably predates a registry write this "
        "session made, so a mutate-then-read-back never reports removed "
        "devices as live. Pass fresh=true when you need live state anyway. "
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
        "fresh": {
            "type": "boolean",
            "description": (
                "Read HA's live registry over WebSocket instead of .storage"
            ),
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
    fresh: bool = False,
) -> dict[str, Any]:
    if registry not in REGISTRY_SPECS:
        return {
            "error": f"Unknown registry '{registry}'",
            "supported": list(REGISTRY_SPECS.keys()),
        }

    read = await load_registry(ctx, registry, fresh=fresh)
    records = read.records

    # Filter
    matched = (
        [r for r in records if _record_matches(r, filter)]
        if filter else list(records)
    )

    total = len(matched)

    if count_only:
        return {
            "registry": registry,
            "total": total,
            "count": total,
            "provenance": read.provenance(),
        }

    # Paginate
    start = max(0, offset)
    end = start + limit
    page = matched[start:end]
    truncated = total > end

    # Project
    results = [
        _project(r, fields, _SUMMARY_FIELDS[registry]) for r in page
    ]

    return {
        "registry": registry,
        "total": total,
        "returned": len(results),
        "results": results,
        "truncated": truncated,
        "provenance": read.provenance(),
    }
