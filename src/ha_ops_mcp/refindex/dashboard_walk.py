"""Dashboard card-tree walker.

HA's Lovelace config is an arbitrary JSON tree: `views → cards → (nested cards
or entities)`. Custom card types (mushroom, button-card, stack-in-card, etc.)
follow the same conventions but can nest in novel ways.

Strategy — "don't assume, don't template":
    - Treat the card tree as arbitrary JSON.
    - At every dict encountered, extract refs from the well-known keys
      (`entity`, `entities`, `entity_id`) and recurse into every other value.
    - No allowlist of known card types — custom cards get indexed for free
      as long as they use the conventional ref keys.

We deliberately reuse the generic YAML walker — same key conventions apply
to JSON dashboards. The only difference is the edge kind (`renders_on` here;
`references` / `triggered_by` / etc. in automation YAML).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from ha_ops_mcp.refindex.yaml_walk import _looks_like_ref


@dataclass(frozen=True)
class DashboardRef:
    """A single reference discovered inside a dashboard card tree."""

    ref_id: str
    path: str


def walk_dashboard_for_refs(
    root: Any, path_prefix: str = ""
) -> Iterator[DashboardRef]:
    """Yield every entity reference found in a dashboard view's card tree.

    Unlike the YAML walker, we don't care about edge-kind categorisation
    (triggers / conditions / targets aren't a dashboard concept). Everything
    a view renders is a `renders_on` edge; the caller assigns that.

    Deduplication across the tree is the caller's concern — we emit each
    hit at its path so the UI can show "3 places in view X".
    """
    yield from _walk(root, path_prefix)


def _walk(node: Any, path: str) -> Iterator[DashboardRef]:
    if isinstance(node, dict):
        yield from _walk_dict(node, path)
    elif isinstance(node, list):
        for idx, item in enumerate(node):
            child_path = f"{path}[{idx}]" if path else f"[{idx}]"
            yield from _walk(item, child_path)


def _walk_dict(node: dict[Any, Any], path: str) -> Iterator[DashboardRef]:
    for key, value in node.items():
        key_str = str(key)
        child_path = f"{path}.{key_str}" if path else key_str

        if key_str in {"entity", "entity_id"} or key_str == "entities":
            yield from _extract(value, child_path)
            if isinstance(value, (dict, list)):
                yield from _walk(value, child_path)
        else:
            yield from _walk(value, child_path)


def _extract(value: Any, path: str) -> Iterator[DashboardRef]:
    """Extract one or more entity refs from a value under a known ref-key."""
    if isinstance(value, str):
        stripped = value.strip()
        if stripped and _looks_like_ref(stripped, "entity"):
            yield DashboardRef(ref_id=stripped, path=path)
    elif isinstance(value, list):
        for idx, item in enumerate(value):
            if isinstance(item, str):
                stripped = item.strip()
                if stripped and _looks_like_ref(stripped, "entity"):
                    yield DashboardRef(ref_id=stripped, path=f"{path}[{idx}]")
            # Dict items (entities list with per-entity config) are handled
            # via the recursive walk in _walk_dict (the outer loop recurses
            # into lists as well).
