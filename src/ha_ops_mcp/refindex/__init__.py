"""Reference index for Home Assistant configuration.

The indexer walks HA's heterogeneous config layers (registries, YAML,
dashboards) and assembles a typed graph of nodes (entities, devices,
areas, automations, dashboards, ...) and edges (belongs_to, references,
triggered_by, renders_on, ...).

Design: stateless per query. Builds fresh against current filesystem
state; no cache invalidation, no stale-read bugs. Caching is an
optimisation we may add later if performance demands it.

Node IDs are typed — `entity:sensor.kitchen_temp`, `device:abc123`,
`automation:morning_lights`, etc. — so the graph is unambiguous even
when an entity and an automation share a slug.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ── Public node/edge/issue shapes ──────────────────────────────────────


@dataclass
class NodeMeta:
    """A node in the reference graph.

    Attributes:
        node_id: Typed identifier, e.g. "entity:sensor.kitchen_temp".
        node_type: One of the known types — entity, device, area, floor,
                   config_entry, automation, script, scene, dashboard,
                   dashboard_view, template_sensor, group, customize, file.
        display_name: Human-friendly name for UI labels. May be None.
        properties: Raw data from the source (registry record, YAML fragment).
                    Kept for downstream tools that need more than the summary.
        source_file: Where this node was discovered (relative to config_root).
                     For registry nodes, the .storage file. For YAML-sourced
                     nodes, the file that contained the definition.
    """

    node_id: str
    node_type: str
    display_name: str | None = None
    properties: dict[str, Any] = field(default_factory=dict)
    source_file: str | None = None


@dataclass
class Edge:
    """A directed relationship between two nodes.

    Attributes:
        source: Node id.
        target: Node id.
        kind: One of the known edge kinds — contains, located_in, belongs_to,
              provides, references, targets, triggered_by, conditioned_on,
              renders_on, customizes.
        location: Where in the source material the edge was found, e.g.
                  "automations.yaml:42" or "dashboard:lovelace/0/cards/2".
                  Useful for the sidebar "jump to" action.
    """

    source: str
    target: str
    kind: str
    location: str | None = None


# ── Known types/kinds (sets used by consumers for filtering) ──────────


NODE_TYPES = frozenset([
    "entity",
    "device",
    "area",
    "floor",
    "config_entry",
    "automation",
    "script",
    "scene",
    "dashboard",
    "dashboard_view",
    "template_sensor",
    "group",
    "customize",
    "file",
    "yaml_file",  # synthetic source for refs found by the loose YAML scan
])


EDGE_KINDS = frozenset([
    "contains",
    "located_in",
    "belongs_to",
    "provides",
    "references",
    "targets",
    "triggered_by",
    "conditioned_on",
    "renders_on",
    "customizes",
])


# ── Node id helpers ────────────────────────────────────────────────────


def node_id(node_type: str, local_id: str) -> str:
    """Build a typed node id — `entity:sensor.kitchen_temp`.

    We don't validate node_type here (checked at RefIndex boundaries),
    because the slug is whatever the source material contains; we pass
    it through unchanged so the caller can look it up by the exact id
    HA uses.
    """
    return f"{node_type}:{local_id}"


def split_node_id(nid: str) -> tuple[str, str]:
    """Split `entity:sensor.kitchen_temp` into ('entity', 'sensor.kitchen_temp').

    Raises ValueError if the id is malformed (no `:`).
    """
    if ":" not in nid:
        raise ValueError(f"Malformed node id (missing ':'): {nid!r}")
    kind, _, rest = nid.partition(":")
    return kind, rest


# ── RefIndex ───────────────────────────────────────────────────────────


class RefIndex:
    """In-memory reference graph.

    Usage:
        index = RefIndex()
        await index.build(ctx)
        node = index.node("entity:sensor.kitchen_temp")
        incoming = index.incoming("entity:sensor.kitchen_temp")

    The builder (src/ha_ops_mcp/refindex/builder.py) populates this by
    calling `add_node` and `add_edge`. Consumers use the read-side
    accessors below.

    Rebuild on every query — do not cache across calls. One index per
    request is the boundary we enforce (see docstring at module top).
    """

    def __init__(self) -> None:
        self._nodes: dict[str, NodeMeta] = {}
        self._out: dict[str, list[Edge]] = {}
        self._in: dict[str, list[Edge]] = {}

    # ── Write side (used by builder) ──────────────────────────────────

    def add_node(self, meta: NodeMeta) -> None:
        """Insert or update a node. Last-write-wins on duplicate ids.

        Duplicates are rare but legitimate (e.g. a registry entry overlaid
        by a YAML customization). We keep the most recently added record.
        """
        if meta.node_id in self._nodes:
            logger.debug("Duplicate node id %r — overwriting", meta.node_id)
        self._nodes[meta.node_id] = meta
        # Ensure the adjacency buckets exist even for leaf nodes
        self._out.setdefault(meta.node_id, [])
        self._in.setdefault(meta.node_id, [])

    def add_edge(self, edge: Edge) -> None:
        """Record an edge. Duplicate edges (same source/target/kind) are kept —
        they may have different `location` values and the UI shows them all.
        """
        self._out.setdefault(edge.source, []).append(edge)
        self._in.setdefault(edge.target, []).append(edge)

    # ── Read side ─────────────────────────────────────────────────────

    def node(self, node_id: str) -> NodeMeta | None:
        return self._nodes.get(node_id)

    def nodes(self, type: str | None = None) -> list[NodeMeta]:
        """All nodes, optionally filtered by `node_type`."""
        if type is None:
            return list(self._nodes.values())
        return [n for n in self._nodes.values() if n.node_type == type]

    def incoming(self, node_id: str) -> list[Edge]:
        """Edges pointing TO this node."""
        return list(self._in.get(node_id, []))

    def outgoing(self, node_id: str) -> list[Edge]:
        """Edges starting FROM this node."""
        return list(self._out.get(node_id, []))

    def neighbors(
        self, node_id: str, depth: int = 1
    ) -> tuple[list[NodeMeta], list[Edge]]:
        """Return the subgraph within `depth` hops of `node_id`.

        Follows edges in both directions (incoming + outgoing) up to the
        given depth. Safe against cycles via a visited-set.

        Args:
            node_id: Focal node.
            depth: How many edges away to explore. depth=1 includes the
                   focal node and all directly connected nodes.

        Returns:
            (nodes, edges) — both de-duplicated.
        """
        if node_id not in self._nodes:
            return ([], [])

        visited_nodes: set[str] = {node_id}
        frontier: set[str] = {node_id}
        collected_edges: list[Edge] = []
        seen_edge_keys: set[tuple[str, str, str]] = set()

        for _ in range(max(depth, 0)):
            next_frontier: set[str] = set()
            for nid in frontier:
                for edge in self._out.get(nid, []) + self._in.get(nid, []):
                    key = (edge.source, edge.target, edge.kind)
                    if key in seen_edge_keys:
                        continue
                    seen_edge_keys.add(key)
                    collected_edges.append(edge)
                    for other in (edge.source, edge.target):
                        if other not in visited_nodes:
                            visited_nodes.add(other)
                            next_frontier.add(other)
            frontier = next_frontier
            if not frontier:
                break

        nodes = [self._nodes[nid] for nid in visited_nodes if nid in self._nodes]
        return (nodes, collected_edges)

    def stats(self) -> dict[str, int]:
        """Counts by node_type + totals. Used by the Overview tab."""
        counts_by_type: dict[str, int] = {}
        for n in self._nodes.values():
            counts_by_type[n.node_type] = counts_by_type.get(n.node_type, 0) + 1
        edge_total = sum(len(v) for v in self._out.values())
        return {
            **counts_by_type,
            "_total_nodes": len(self._nodes),
            "_total_edges": edge_total,
        }

    # ── Builder entrypoint ────────────────────────────────────────────

    async def build(self, ctx: Any) -> None:  # ctx: HaOpsContext
        """Populate this index from ctx's filesystem + registries.

        Implementation lives in `src/ha_ops_mcp/refindex/builder.py` to
        keep this module focused on the data model and read API.
        """
        from ha_ops_mcp.refindex.builder import build_index
        await build_index(self, ctx)


# ── Per-request cache ─────────────────────────────────────────────────


async def get_or_build_index(ctx: Any) -> RefIndex:
    """Return `ctx.request_index` if present, else build and cache for this
    request.

    Callers that enter a request scope (MCP tool handlers, HTTP routes)
    should set `ctx.request_index = None` on entry and clear it on exit.
    Downstream consumers call this helper instead of building directly.

    If `ctx.request_index` is not an attribute at all, falls back to a
    fresh build (safe default for legacy callers).
    """
    existing = getattr(ctx, "request_index", None)
    if isinstance(existing, RefIndex):
        return existing
    index = RefIndex()
    await index.build(ctx)
    # Stash on context if the attribute exists (and is currently None)
    if hasattr(ctx, "request_index"):
        ctx.request_index = index
    return index
