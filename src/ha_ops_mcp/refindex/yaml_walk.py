"""Generic YAML tree walker for structured reference extraction.

Home Assistant YAML (automations, scripts, scenes, customize, groups)
has a recursive tree structure where entity/device/area references can
appear at any depth inside triggers, conditions, actions, service calls,
and targets.

Rather than hand-coding the schema for each feature, we walk the tree
generically: at every dict level, check for the well-known reference
keys; recurse into every other value regardless of key. This catches
custom integrations that follow the HA conventions without us having
to know about them in advance.

Layer 1 (v0.6): structured key extraction only. Jinja template references
inside `value_template:`, `{{ ... }}` strings, etc. are deferred to v0.7.

The walker categorises each reference into an edge kind based on the
ancestor key path:

    triggered_by      when the ref is inside a `trigger:` block
    conditioned_on    when the ref is inside a `condition:` block
    targets           when the ref is inside a `target:` block (action target)
    references        everything else (default)

This matches what the refindex edge taxonomy encodes.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

# ── Well-known reference keys ──────────────────────────────────────────


# Keys whose values carry entity references (scalar or list of scalars)
_ENTITY_REF_KEYS = frozenset({
    "entity_id",
    "entity",
    "entities",
})

# Keys whose values carry device references
_DEVICE_REF_KEYS = frozenset({
    "device_id",
    "device",
})

# Keys whose values carry area references
_AREA_REF_KEYS = frozenset({
    "area_id",
    "area",
})

# Ancestor-block keys that categorise the edge kind
_TRIGGER_BLOCK_KEYS = frozenset({"trigger", "triggers"})
_CONDITION_BLOCK_KEYS = frozenset({"condition", "conditions"})
_TARGET_BLOCK_KEYS = frozenset({"target"})


@dataclass(frozen=True)
class YamlRef:
    """A single reference discovered during a walk.

    Attributes:
        ref_type: "entity" | "device" | "area".
        ref_id: The referenced id (e.g. "sensor.kitchen_temp").
        edge_kind: One of "triggered_by", "conditioned_on", "targets",
                   "references" — determined by the ancestor block.
        path: Dotted path from the walk root, e.g.
              "trigger[0].entity_id" or "action[2].target.entity_id".
              Used for edge `location` display.
    """

    ref_type: str
    ref_id: str
    edge_kind: str
    path: str


def walk_yaml_for_refs(
    root: Any,
    path_prefix: str = "",
) -> Iterator[YamlRef]:
    """Yield every reference discovered in the YAML tree.

    Args:
        root: The YAML data (dict, list, or scalar).
        path_prefix: Used for recursive calls; leave empty at top level.

    Yields:
        YamlRef for each discovered reference. Order is deterministic
        (depth-first, then sibling order within dicts/lists).
    """
    yield from _walk(root, path_prefix, edge_context="references")


def _walk(
    node: Any, path: str, edge_context: str
) -> Iterator[YamlRef]:
    """Recursive walker with edge-context tracking.

    `edge_context` is the currently-active edge kind, set when we enter
    a trigger/condition/target block and inherited by nested levels.
    The default is "references" (plain action-level refs).
    """
    if isinstance(node, dict):
        yield from _walk_dict(node, path, edge_context)
    elif isinstance(node, list):
        for idx, item in enumerate(node):
            child_path = f"{path}[{idx}]" if path else f"[{idx}]"
            yield from _walk(item, child_path, edge_context)
    elif isinstance(node, str):
        # Jinja templates embedded in string values — pull entity refs
        # out via the template walker (v0.7 addition).
        yield from _walk_jinja_in_string(node, path, edge_context)
    # Other scalars (int/bool/None) can't carry refs.


def _walk_jinja_in_string(
    text: str, path: str, edge_context: str
) -> Iterator[YamlRef]:
    """Extract Jinja entity refs from a string scalar.

    We import lazily so modules that don't need Jinja support (tests, etc.)
    don't pay the import cost.
    """
    from ha_ops_mcp.refindex.jinja_walk import walk_jinja_for_refs

    seen: set[str] = set()
    for jref in walk_jinja_for_refs(text):
        if jref.entity_id in seen:
            continue
        seen.add(jref.entity_id)
        yield YamlRef(
            ref_type="entity",
            ref_id=jref.entity_id,
            edge_kind=edge_context,
            path=f"{path}@jinja",
        )


def _walk_dict(
    node: dict[Any, Any], path: str, edge_context: str
) -> Iterator[YamlRef]:
    """Process a dict: extract refs at known keys, recurse elsewhere."""
    for key, value in node.items():
        key_str = str(key)
        child_path = f"{path}.{key_str}" if path else key_str

        # If this key switches context (trigger/condition/target),
        # override the edge context for the value and everything below.
        if key_str in _TRIGGER_BLOCK_KEYS:
            next_context = "triggered_by"
        elif key_str in _CONDITION_BLOCK_KEYS:
            next_context = "conditioned_on"
        elif key_str in _TARGET_BLOCK_KEYS:
            next_context = "targets"
        else:
            next_context = edge_context

        # Extract refs where the key names a reference directly
        if key_str in _ENTITY_REF_KEYS:
            yield from _extract_refs(value, "entity", next_context, child_path)
            # Still recurse — an "entities" list may contain dicts with
            # their own `entity:` keys (light-group style).
            if isinstance(value, (dict, list)):
                yield from _walk(value, child_path, next_context)
        elif key_str in _DEVICE_REF_KEYS:
            yield from _extract_refs(value, "device", next_context, child_path)
            if isinstance(value, (dict, list)):
                yield from _walk(value, child_path, next_context)
        elif key_str in _AREA_REF_KEYS:
            yield from _extract_refs(value, "area", next_context, child_path)
            if isinstance(value, (dict, list)):
                yield from _walk(value, child_path, next_context)
        else:
            # Not a ref key — recurse without emitting
            yield from _walk(value, child_path, next_context)


def _extract_refs(
    value: Any, ref_type: str, edge_kind: str, path: str
) -> Iterator[YamlRef]:
    """Extract one or more refs from a value under a known ref-key.

    Values can be:
        - scalar string: one ref
        - list of strings: multiple refs
        - list of dicts with `entity:` keys: handled by the caller's
          recurse-into-value path, not here
        - dict (rare — e.g. `entities: {entity.x: {visible: true}}`):
          keys are the refs
    """
    if isinstance(value, str):
        stripped = value.strip()
        if stripped and _looks_like_ref(stripped, ref_type):
            yield YamlRef(
                ref_type=ref_type,
                ref_id=stripped,
                edge_kind=edge_kind,
                path=path,
            )
    elif isinstance(value, list):
        for idx, item in enumerate(value):
            if isinstance(item, str):
                stripped = item.strip()
                if stripped and _looks_like_ref(stripped, ref_type):
                    yield YamlRef(
                        ref_type=ref_type,
                        ref_id=stripped,
                        edge_kind=edge_kind,
                        path=f"{path}[{idx}]",
                    )
            # Dict items (entities list with per-entity config) are
            # handled by the recursive walker in _walk_dict.
    elif isinstance(value, dict):
        # Used in scenes.yaml: `entities: { switch.x: on, switch.y: off }`
        # The keys are the refs. We only yield keys that look like refs
        # for the current ref_type; per-entity config (the values) is
        # recursed by _walk_dict.
        for sub_key in value:
            k = str(sub_key).strip()
            if k and _looks_like_ref(k, ref_type):
                yield YamlRef(
                    ref_type=ref_type,
                    ref_id=k,
                    edge_kind=edge_kind,
                    path=f"{path}[{k!r}]",
                )


def _looks_like_ref(value: str, ref_type: str) -> bool:
    """Quick sanity check to avoid yielding random strings.

    - entity_id: must contain a `.` and be in `domain.object_id` shape
    - device_id / area_id: opaque ids, accept any non-empty string
    """
    if ref_type == "entity":
        # domain.object_id — at minimum "x.y"
        if "." not in value:
            return False
        # HA domain is lowercase letters/digits/underscores; object_id
        # is lowercase letters/digits/underscores. We're lenient —
        # anything with a dot and two non-empty halves passes.
        domain, _, obj = value.partition(".")
        return bool(domain and obj)
    # device / area ids — opaque
    return bool(value)
