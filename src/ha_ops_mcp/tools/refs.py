"""Reference-graph MCP tools.

Two read-only tools layered on top of the stateless RefIndex:

    haops_references     list incoming + outgoing refs for a node
    haops_refactor_check "what breaks if I rename/delete X"

The index is built per-request (cached on `ctx.request_index` by the
`get_or_build_index` helper). Callers that invoke multiple tools in one
turn pay the build cost once.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ha_ops_mcp.refindex import (
    Edge,
    NodeMeta,
    get_or_build_index,
    node_id,
)
from ha_ops_mcp.server import registry

if TYPE_CHECKING:
    from ha_ops_mcp.server import HaOpsContext

logger = logging.getLogger(__name__)


# ── Helpers ────────────────────────────────────────────────────────────


def _resolve_node_id(raw: str) -> str:
    """Accept either a typed id (`entity:sensor.x`) or a bare id and guess.

    Bare ids get typed via their shape:
      - `a.b` → entity (domain.object_id)
      - anything else → left as-is; caller has to pass typed id
    """
    if ":" in raw:
        return raw
    if "." in raw:
        return node_id("entity", raw)
    return raw


def _node_to_dict(n: NodeMeta) -> dict[str, Any]:
    return {
        "node_id": n.node_id,
        "node_type": n.node_type,
        "display_name": n.display_name,
        "source_file": n.source_file,
    }


def _edge_to_dict(e: Edge) -> dict[str, Any]:
    return {
        "source": e.source,
        "target": e.target,
        "kind": e.kind,
        "location": e.location,
    }


# ── haops_references ──────────────────────────────────────────────────


@registry.tool(
    name="haops_references",
    description=(
        "List all references to and from a node in the HA config graph. "
        "Accepts a typed id ('entity:sensor.temp', 'device:abc', 'automation:x') "
        "or a bare entity_id ('sensor.temp' — entity assumed). Returns incoming "
        "edges (things that reference this node) and outgoing edges (what this "
        "node references). Read-only."
    ),
    params={
        "node": {
            "type": "string",
            "description": (
                "Typed node id ('entity:sensor.kitchen', 'device:dev_001') "
                "or bare entity_id (treated as entity)."
            ),
        },
    },
)
async def haops_references(ctx: HaOpsContext, node: str) -> dict[str, Any]:
    index = await get_or_build_index(ctx)
    nid = _resolve_node_id(node)
    meta = index.node(nid)
    if meta is None:
        return {
            "error": f"Unknown node '{nid}'",
            "hint": "Use a typed id like 'entity:sensor.foo' or 'device:abc123'.",
        }
    incoming = index.incoming(nid)
    outgoing = index.outgoing(nid)
    return {
        "node": _node_to_dict(meta),
        "incoming": [_edge_to_dict(e) for e in incoming],
        "outgoing": [_edge_to_dict(e) for e in outgoing],
        "total_refs": len(incoming) + len(outgoing),
    }


# ── haops_refactor_check ──────────────────────────────────────────────


_USAGE_EDGE_KINDS = frozenset({
    "references", "targets", "triggered_by",
    "conditioned_on", "renders_on", "customizes",
})


@registry.tool(
    name="haops_refactor_check",
    description=(
        "Preview what breaks if a node is renamed or deleted. For rename, pass "
        "new_id as the replacement; for delete, omit it. Returns the per-file "
        "ref counts plus a 'locations' list to help the caller compose the "
        "actual edits via haops_config_patch / haops_dashboard_patch "
        "(or haops_batch_preview for multi-target refactors). "
        "Read-only (previews only — does not apply)."
    ),
    params={
        "node_id": {"type": "string", "description": "Typed id of the node to rename/delete"},
        "new_id": {
            "type": "string",
            "description": (
                "New typed id if renaming. Omit or pass empty string for deletion."
            ),
            "default": "",
        },
    },
)
async def haops_refactor_check(
    ctx: HaOpsContext,
    node_id: str,
    new_id: str = "",
) -> dict[str, Any]:
    index = await get_or_build_index(ctx)
    nid = _resolve_node_id(node_id)
    if index.node(nid) is None:
        return {"error": f"Unknown node '{nid}'"}

    new_typed = _resolve_node_id(new_id) if new_id else None

    broken = [e for e in index.incoming(nid) if e.kind in _USAGE_EDGE_KINDS]

    files: dict[str, int] = {}
    locations: list[dict[str, Any]] = []
    for e in broken:
        file_ref = (e.location or "").split(":")[0] if e.location else "unknown"
        files[file_ref] = files.get(file_ref, 0) + 1
        source_node = index.node(e.source)
        context = (
            f"{e.source}, {e.location}"
            if e.location else e.source
        )
        if source_node and source_node.display_name:
            context = f"{source_node.display_name} ({context})"
        locations.append({
            "file": file_ref,
            "edge_kind": e.kind,
            "source_node": e.source,
            "context": context,
            "current_value": nid,
            "suggested_value": new_typed,
        })

    affected_files = [
        {"path": path, "ref_count": count}
        for path, count in sorted(files.items(), key=lambda kv: (-kv[1], kv[0]))
    ]

    return {
        "node_id": nid,
        "new_id": new_typed,
        "affected_files": affected_files,
        "locations": locations,
        "jinja_note": (
            "Layer 1 reference extraction only — Jinja template refs "
            "(states('x'), etc.) are NOT yet analyzed. Check templates manually."
        ),
    }
