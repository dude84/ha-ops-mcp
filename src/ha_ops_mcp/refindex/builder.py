"""Reference index builder.

Walks HA's configuration layers and populates a RefIndex with typed
nodes and edges. This module is the load-bearing piece of v0.6 —
everything the impact analyzer, sidebar UI, and refs tools rely on
comes out of here.

Layer 1 (v0.6): structured references from registries + YAML + dashboards.
Layer 2 (v0.7): Jinja template reference extraction.

Call `build_index(index, ctx)` as the single entrypoint. The builder
never crashes on malformed config — bad YAML, missing files, broken
includes are logged and skipped.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ha_ops_mcp.refindex import (
    Edge,
    NodeMeta,
    RefIndex,
    node_id,
)
from ha_ops_mcp.refindex.dashboard_walk import walk_dashboard_for_refs
from ha_ops_mcp.refindex.yaml_walk import walk_yaml_for_refs
from ha_ops_mcp.utils.ha_yaml import HaYamlLoader, merge_packages

if TYPE_CHECKING:
    from ha_ops_mcp.server import HaOpsContext

logger = logging.getLogger(__name__)


def _slugify(name: str) -> str:
    """HA-style slug: lowercase, non-alphanumeric runs → single underscore.

    Matches what HA uses to derive entity_ids from aliases (close enough for
    indexing purposes — the real slug function lives in `homeassistant.util`
    but we don't want to import that).
    """
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_")


# ── Main entrypoint ────────────────────────────────────────────────────


async def build_index(index: RefIndex, ctx: HaOpsContext) -> None:
    """Populate `index` with nodes/edges/issues from `ctx`'s filesystem.

    Passes, in order:
      1. Registries — entities, devices, areas, floors, config_entries
      2. Effective-area resolution — connect entities to areas via their
         devices when entity.area_id is None
      3. YAML layer — automations, scripts, scenes, groups, customize,
         template sensors (with HA-compatible include/package resolution
         and Jinja-aware reference extraction inside string scalars)
      4. Dashboards — .storage/lovelace* AND YAML-mode dashboards
         (configured via `lovelace:` in configuration.yaml) walked as
         generic card trees
      5. Loose YAML scan — every other *.yaml/*.yml under config_root
         that wasn't covered by passes 3-4 (catches community-themed
         dashboards split into many files like ULM)
    """
    covered_files: set[str] = set()
    await _index_registries(index, ctx)
    _index_yaml_layer(index, ctx, covered_files)
    _index_dashboards(index, ctx)
    _index_yaml_dashboards(index, ctx, covered_files)
    _index_loose_yaml(index, ctx, covered_files)


# ── Registry pass ──────────────────────────────────────────────────────


async def _index_registries(index: RefIndex, ctx: HaOpsContext) -> None:
    """Build entity/device/area/floor/config_entry nodes + their edges.

    Data source: the existing `_load_registry` helper in
    `src/ha_ops_mcp/tools/registry.py` which already handles filesystem-first
    loading with WS fallback. We reuse it unchanged.
    """
    from ha_ops_mcp.tools.registry import _load_registry

    try:
        devices = await _load_registry(ctx, "devices")
    except Exception as e:
        logger.warning("Registry load failed (devices): %s", e)
        devices = []
    try:
        entities = await _load_registry(ctx, "entities")
    except Exception as e:
        logger.warning("Registry load failed (entities): %s", e)
        entities = []
    try:
        areas = await _load_registry(ctx, "areas")
    except Exception as e:
        logger.warning("Registry load failed (areas): %s", e)
        areas = []
    try:
        floors = await _load_registry(ctx, "floors")
    except Exception as e:
        logger.warning("Registry load failed (floors): %s", e)
        floors = []
    try:
        config_entries = await _load_registry(ctx, "config_entries")
    except Exception as e:
        logger.warning("Registry load failed (config_entries): %s", e)
        config_entries = []

    # ── Floors ──
    for floor in floors:
        fid = floor.get("floor_id")
        if not fid:
            continue
        index.add_node(NodeMeta(
            node_id=node_id("floor", fid),
            node_type="floor",
            display_name=floor.get("name"),
            properties=dict(floor),
            source_file=".storage/core.floor_registry",
        ))

    # ── Areas ──
    for area in areas:
        aid = area.get("id") or area.get("area_id")
        if not aid:
            continue
        index.add_node(NodeMeta(
            node_id=node_id("area", aid),
            node_type="area",
            display_name=area.get("name"),
            properties=dict(area),
            source_file=".storage/core.area_registry",
        ))
        floor_id = area.get("floor_id")
        if floor_id:
            index.add_edge(Edge(
                source=node_id("area", aid),
                target=node_id("floor", floor_id),
                kind="belongs_to",
                location=".storage/core.area_registry",
            ))

    # ── Config entries ──
    for entry in config_entries:
        eid = entry.get("entry_id")
        if not eid:
            continue
        title = entry.get("title") or entry.get("domain")
        index.add_node(NodeMeta(
            node_id=node_id("config_entry", eid),
            node_type="config_entry",
            display_name=title,
            properties=dict(entry),
            source_file=".storage/core.config_entries",
        ))

    # ── Devices ──
    for device in devices:
        did = device.get("id")
        if not did:
            continue
        # Pick the best display name (matches _device_display_name in tools/device.py)
        name = (
            device.get("name_by_user")
            or device.get("name")
            or device.get("model")
            or did
        )
        index.add_node(NodeMeta(
            node_id=node_id("device", did),
            node_type="device",
            display_name=name,
            properties=dict(device),
            source_file=".storage/core.device_registry",
        ))
        # device → area
        area_id = device.get("area_id")
        if area_id:
            index.add_edge(Edge(
                source=node_id("device", did),
                target=node_id("area", area_id),
                kind="located_in",
                location=".storage/core.device_registry",
            ))
        # config_entry → device (one device can be provided by multiple entries)
        entry_ids = device.get("config_entries") or []
        if isinstance(entry_ids, list):
            for entry_id in entry_ids:
                if not entry_id:
                    continue
                index.add_edge(Edge(
                    source=node_id("config_entry", entry_id),
                    target=node_id("device", did),
                    kind="provides",
                    location=".storage/core.device_registry",
                ))

    # ── Entities ──
    for entity in entities:
        eid = entity.get("entity_id")
        if not eid:
            continue
        # Display name: friendly name from registry if present, else the entity_id.
        name = entity.get("name") or entity.get("original_name") or eid
        index.add_node(NodeMeta(
            node_id=node_id("entity", eid),
            node_type="entity",
            display_name=name,
            properties=dict(entity),
            source_file=".storage/core.entity_registry",
        ))
        # entity → device (belongs_to)
        device_id = entity.get("device_id")
        if device_id:
            index.add_edge(Edge(
                source=node_id("entity", eid),
                target=node_id("device", device_id),
                kind="belongs_to",
                location=".storage/core.entity_registry",
            ))
        # entity → area (located_in, direct). Device-inherited area is
        # handled in a separate pass below so we can correctly prefer
        # the entity-level area when set.
        area_id = entity.get("area_id")
        if area_id:
            index.add_edge(Edge(
                source=node_id("entity", eid),
                target=node_id("area", area_id),
                kind="located_in",
                location=".storage/core.entity_registry",
            ))

    # ── Effective-area pass ──
    # For entities with no direct area_id but whose device has one, add
    # a located_in edge via the device's area. This matches HA's own
    # "effective area" resolution (same logic as haops_entity_list
    # area_mode="effective").
    _connect_effective_areas(index)

    logger.debug(
        "Registry pass complete: %d entities, %d devices, %d areas, "
        "%d floors, %d config_entries",
        len(entities), len(devices), len(areas), len(floors), len(config_entries),
    )


def _connect_effective_areas(index: RefIndex) -> None:
    """For entities without direct area_id, inherit from their device.

    Walks entities looking for ones whose outgoing edges contain a
    `belongs_to` (to a device) but no `located_in` (to an area). If the
    device has a `located_in`, copy it onto the entity as a located_in
    edge with kind suffix indicating inherited.

    We add a plain `located_in` edge (not `located_in_via_device`) so
    downstream callers don't need to special-case. The `location`
    attribute notes the inheritance path.
    """
    entity_nodes = index.nodes("entity")
    for entity in entity_nodes:
        out = index.outgoing(entity.node_id)
        has_direct_area = any(e.kind == "located_in" for e in out)
        if has_direct_area:
            continue
        # Find the device this entity belongs to
        device_edge = next((e for e in out if e.kind == "belongs_to"), None)
        if device_edge is None:
            continue
        device_out = index.outgoing(device_edge.target)
        device_area_edge = next(
            (e for e in device_out if e.kind == "located_in"), None
        )
        if device_area_edge is None:
            continue
        index.add_edge(Edge(
            source=entity.node_id,
            target=device_area_edge.target,
            kind="located_in",
            location=(
                f"inherited from {device_edge.target} "
                f"(.storage/core.device_registry)"
            ),
        ))


# ── YAML layer ─────────────────────────────────────────────────────────


def _index_yaml_layer(
    index: RefIndex, ctx: HaOpsContext, covered_files: set[str]
) -> None:
    """Load configuration.yaml + packages, then walk each known section.

    `covered_files` (mutated) accumulates every relative path the loader
    visits, so later passes (loose YAML scan) can skip files we've already
    indexed structurally.

    Every section is optional; missing files are silently skipped. Broken
    includes / missing secrets are logged and skipped.
    """
    config_root = ctx.path_guard.config_root
    loader = HaYamlLoader(config_root, path_guard=ctx.path_guard.validate)

    merged: dict[str, Any] = {}

    # Root configuration.yaml (with package merge)
    root = loader.load(Path("configuration.yaml"))
    if isinstance(root.data, dict):
        merged_data, _pkg_issues = merge_packages(root.data, loader)
        merged.update(merged_data)
    covered_files.update(root.included_files)

    # Standalone files that may or may not already be included from
    # configuration.yaml. If not already covered, load them directly and
    # splice into the merged view under their canonical key.
    _load_standalone_if_missing(
        loader, config_root, merged, covered_files, index,
        filename="automations.yaml", key="automation",
    )
    _load_standalone_if_missing(
        loader, config_root, merged, covered_files, index,
        filename="scripts.yaml", key="script",
    )
    _load_standalone_if_missing(
        loader, config_root, merged, covered_files, index,
        filename="scenes.yaml", key="scene",
    )
    _load_standalone_if_missing(
        loader, config_root, merged, covered_files, index,
        filename="groups.yaml", key="group",
    )
    _load_standalone_if_missing(
        loader, config_root, merged, covered_files, index,
        filename="customize.yaml", key="_customize_standalone",
    )

    # Walks — each guards against None/missing key internally.
    _index_automations(index, merged.get("automation"), source="automations.yaml")
    _index_scripts(index, merged.get("script"), source="scripts.yaml")
    _index_scenes(index, merged.get("scene"), source="scenes.yaml")
    _index_groups(index, merged.get("group"), source="groups.yaml")
    _index_template_sensors(index, merged.get("template"), source="configuration.yaml")
    _index_customize(
        index,
        primary=_get_ha_customize(merged),
        standalone=merged.get("_customize_standalone"),
    )

    # Capture every file the loader touched (configuration.yaml + packages
    # + standalone reloads + their !include chains) so the loose-scan pass
    # knows what's already been indexed structurally and won't double-emit.
    covered_files.update(loader._included)  # noqa: SLF001 — internal but stable


def _load_standalone_if_missing(
    loader: HaYamlLoader,
    config_root: Path,
    merged: dict[str, Any],
    covered_files: set[str],
    index: RefIndex,
    *,
    filename: str,
    key: str,
) -> None:
    """If `filename` exists and wasn't already pulled in via !include, load it
    into `merged[key]`. Concatenate lists, merge dicts; scalars replace.
    """
    if filename in covered_files:
        return
    path = config_root / filename
    if not path.is_file():
        return
    result = loader.load(Path(filename))
    if result.data is None:
        return
    existing = merged.get(key)
    if existing is None:
        merged[key] = result.data
    elif isinstance(existing, list) and isinstance(result.data, list):
        merged[key] = existing + result.data
    elif isinstance(existing, dict) and isinstance(result.data, dict):
        merged_dict = dict(existing)
        merged_dict.update(result.data)
        merged[key] = merged_dict
    # Type mismatch — keep existing, skip silently (rare edge case).


# ── Automations ───────────────────────────────────────────────────────


def _index_automations(
    index: RefIndex, data: Any, *, source: str
) -> None:
    """Walk the merged `automation:` list, emitting automation nodes + edges.

    HA accepts a list at the top of automations.yaml. Each entry has:
        id:          optional, persistent identifier (preferred for node_id)
        alias:       human name (fallback — slugify for node_id)
        trigger(s):  list
        condition(s): list
        action(s):   list

    An entry with neither `id:` nor `alias:` gets a `missing_identifier` issue
    and is skipped (not indexable — we'd lose it on file edits).
    """
    if not isinstance(data, list):
        return
    for _idx, entry in enumerate(data):
        if not isinstance(entry, dict):
            continue
        local_id = _automation_local_id(entry)
        if local_id is None:
            continue
        nid = node_id("automation", local_id)
        index.add_node(NodeMeta(
            node_id=nid,
            node_type="automation",
            display_name=entry.get("alias") or local_id,
            properties=dict(entry),
            source_file=source,
        ))
        _emit_refs_from_walk(index, nid, entry, source, root_path="")


def _automation_local_id(entry: dict[str, Any]) -> str | None:
    raw_id = entry.get("id")
    if raw_id is not None:
        return str(raw_id).strip() or None
    alias = entry.get("alias")
    if alias:
        slug = _slugify(str(alias))
        return slug or None
    return None


# ── Scripts ───────────────────────────────────────────────────────────


def _index_scripts(index: RefIndex, data: Any, *, source: str) -> None:
    """`script:` is a dict: `{script_id: {alias?, sequence: [...]}}`."""
    if not isinstance(data, dict):
        return
    for script_id, entry in data.items():
        if not isinstance(entry, dict):
            continue
        sid = str(script_id).strip()
        if not sid:
            continue
        nid = node_id("script", sid)
        index.add_node(NodeMeta(
            node_id=nid,
            node_type="script",
            display_name=entry.get("alias") or sid,
            properties=dict(entry),
            source_file=source,
        ))
        _emit_refs_from_walk(index, nid, entry, source, root_path="")


# ── Scenes ────────────────────────────────────────────────────────────


def _index_scenes(index: RefIndex, data: Any, *, source: str) -> None:
    """`scene:` is a list. Each scene has `id:` (preferred), `name:`, `entities:`.

    The `entities:` dict holds the actual entity refs — keys are entity_ids,
    values are per-entity state/config. The generic walker handles this via
    the dict-with-entity-keys path.
    """
    if not isinstance(data, list):
        return
    for _idx, entry in enumerate(data):
        if not isinstance(entry, dict):
            continue
        raw_id = entry.get("id")
        if raw_id is not None:
            local_id = str(raw_id).strip() or None
        else:
            name = entry.get("name")
            local_id = _slugify(str(name)) if name else None
        if not local_id:
            continue
        nid = node_id("scene", local_id)
        index.add_node(NodeMeta(
            node_id=nid,
            node_type="scene",
            display_name=entry.get("name") or local_id,
            properties=dict(entry),
            source_file=source,
        ))
        _emit_refs_from_walk(index, nid, entry, source, root_path="")


# ── Groups ────────────────────────────────────────────────────────────


def _index_groups(index: RefIndex, data: Any, *, source: str) -> None:
    """`group:` is a dict keyed by group_id. Each group has `entities:` list."""
    if not isinstance(data, dict):
        return
    for group_id, entry in data.items():
        gid = str(group_id).strip()
        if not gid:
            continue
        # Group can be either a dict `{name, entities}` or a bare list of entities.
        if isinstance(entry, dict):
            name = entry.get("name") or gid
            props = dict(entry)
        elif isinstance(entry, list):
            name = gid
            props = {"entities": list(entry)}
            entry = {"entities": list(entry)}
        else:
            continue
        nid = node_id("group", gid)
        index.add_node(NodeMeta(
            node_id=nid,
            node_type="group",
            display_name=name,
            properties=props,
            source_file=source,
        ))
        _emit_refs_from_walk(index, nid, entry, source, root_path="")


# ── Template sensors (structural refs only; Jinja deferred to v0.7) ───


def _index_template_sensors(index: RefIndex, data: Any, *, source: str) -> None:
    """Modern `template:` block — a list of trigger-based template platforms.

    Only structural entity refs (`trigger.entity_id`, `availability.entity_id`)
    are caught here. References embedded inside `{{ states('x') }}` strings
    are v0.7 work.

    Legacy `sensor: - platform: template` under the `sensor:` block is not
    indexed as template_sensor nodes here — those get the generic `sensor.*`
    entity_id at runtime and are covered by the registry pass.
    """
    if not isinstance(data, list):
        return
    for idx, entry in enumerate(data):
        if not isinstance(entry, dict):
            continue
        # A template entry doesn't necessarily have a stable id. We synthesize
        # one from index position — sufficient for Layer 1; v0.7 can upgrade
        # to unique_id when we introduce full template support.
        nid = node_id("template_sensor", f"{source}#{idx}")
        index.add_node(NodeMeta(
            node_id=nid,
            node_type="template_sensor",
            display_name=f"template[{idx}]",
            properties=dict(entry),
            source_file=source,
        ))
        _emit_refs_from_walk(index, nid, entry, source, root_path="")


# ── Customize ─────────────────────────────────────────────────────────


def _get_ha_customize(merged: dict[str, Any]) -> Any:
    """Extract `homeassistant.customize` from the merged view, if present."""
    ha = merged.get("homeassistant")
    if isinstance(ha, dict):
        return ha.get("customize")
    return None


def _index_customize(
    index: RefIndex, primary: Any, standalone: Any
) -> None:
    """Customize entries: keys are entity_ids (or globs). Values are attribute
    overrides. Each key becomes a `customize:*` node with a `customizes` edge
    to the `entity:*` node.

    Globs (`light.kitchen_*`) are kept as-is; resolving them against live
    entity_ids is a query-time concern (done in `haops_references`).
    """
    sources = []
    if isinstance(primary, dict):
        sources.append(("configuration.yaml", primary))
    if isinstance(standalone, dict):
        sources.append(("customize.yaml", standalone))

    for source, data in sources:
        for key, value in data.items():
            k = str(key).strip()
            if not k:
                continue
            nid = node_id("customize", k)
            index.add_node(NodeMeta(
                node_id=nid,
                node_type="customize",
                display_name=k,
                properties=value if isinstance(value, dict) else {"value": value},
                source_file=source,
            ))
            # Only connect non-glob keys to concrete entity nodes. Glob keys
            # (containing `*`) stay as standalone customize nodes until a
            # resolver walks them.
            if "*" not in k and "." in k:
                index.add_edge(Edge(
                    source=nid,
                    target=node_id("entity", k),
                    kind="customizes",
                    location=source,
                ))


# ── Dashboards ────────────────────────────────────────────────────────


def _index_dashboards(index: RefIndex, ctx: HaOpsContext) -> None:
    """Walk Lovelace dashboards stored under `.storage/lovelace*`.

    Each file is JSON with shape `{"data": {"config": {"views": [...]}}, ...}`.
    We create:
        - `dashboard:<url_path>` node per file
        - `dashboard_view:<url_path>/<view_index>` per view
        - `renders_on` edges from each view to every entity it references

    YAML-mode dashboards (configured via `lovelace.mode: yaml`) live in
    user-defined .yaml files and aren't covered here — they're a Layer 2
    concern and deferred.
    """
    storage_dir = ctx.path_guard.config_root / ".storage"
    if not storage_dir.is_dir():
        return

    # Match `lovelace` (default) and `lovelace.<url_path>` files.
    for path in sorted(storage_dir.iterdir()):
        name = path.name
        if not path.is_file():
            continue
        if name == "lovelace":
            url_path = "lovelace"
        elif name.startswith("lovelace."):
            url_path = name[len("lovelace."):]
        else:
            continue

        try:
            import json
            data = json.loads(path.read_text())
        except (OSError, ValueError) as e:
            logger.debug("Skipping dashboard %s: %s", name, e)
            continue

        config = data.get("data", {}).get("config") if isinstance(data, dict) else None
        if not isinstance(config, dict):
            continue

        dash_nid = node_id("dashboard", url_path)
        index.add_node(NodeMeta(
            node_id=dash_nid,
            node_type="dashboard",
            display_name=config.get("title") or url_path,
            properties={
                "title": config.get("title"),
                "views_count": len(config.get("views") or []),
            },
            source_file=f".storage/{name}",
        ))

        views_raw = config.get("views")
        views: list[Any] = views_raw if isinstance(views_raw, list) else []
        for vidx, view in enumerate(views):
            if not isinstance(view, dict):
                continue
            view_path = str(view.get("path") or vidx)
            view_local = f"{url_path}/{view_path}"
            view_nid = node_id("dashboard_view", view_local)
            index.add_node(NodeMeta(
                node_id=view_nid,
                node_type="dashboard_view",
                display_name=view.get("title") or view.get("path") or f"view[{vidx}]",
                properties={"title": view.get("title"), "path": view.get("path")},
                source_file=f".storage/{name}",
            ))
            # dashboard → dashboard_view: "contains"
            index.add_edge(Edge(
                source=dash_nid,
                target=view_nid,
                kind="contains",
                location=f".storage/{name}:views[{vidx}]",
            ))

            # Walk cards → renders_on edges
            seen: set[tuple[str, str]] = set()
            for ref in walk_dashboard_for_refs(view, path_prefix=f"views[{vidx}]"):
                target = node_id("entity", ref.ref_id)
                key = (target, ref.path)
                if key in seen:
                    continue
                seen.add(key)
                index.add_edge(Edge(
                    source=view_nid,
                    target=target,
                    kind="renders_on",
                    location=f".storage/{name}:{ref.path}",
                ))


# ── YAML-mode dashboards ──────────────────────────────────────────────


def _index_yaml_dashboards(
    index: RefIndex, ctx: HaOpsContext, covered_files: set[str]
) -> None:
    """Walk dashboards configured in YAML mode (lovelace: in configuration.yaml).

    Two cases:

    1. Default dashboard YAML override:
           lovelace:
             mode: yaml
       → load `ui-lovelace.yaml` from config root.

    2. Named YAML dashboards:
           lovelace:
             dashboards:
               my-dash:
                 mode: yaml
                 filename: dashboards/my-dash.yaml
                 title: My Dash
       → load each `filename` (with `!include` chains).

    Each loaded dashboard registers as `dashboard:<url_path>` (not prefixed
    with `yaml:` — same node namespace as storage dashboards). Views become
    `dashboard_view:<url_path>/<view_index_or_path>`. Refs use the same
    `renders_on` edge kind. Custom-card walking via `walk_dashboard_for_refs`.
    """
    config_root = ctx.path_guard.config_root
    loader = HaYamlLoader(config_root, path_guard=ctx.path_guard.validate)

    root = loader.load(Path("configuration.yaml"))
    if not isinstance(root.data, dict):
        return
    merged_data, _pkg_issues = merge_packages(root.data, loader)

    lovelace = merged_data.get("lovelace")
    if not isinstance(lovelace, dict):
        return

    # Case 1: default dashboard via ui-lovelace.yaml
    if str(lovelace.get("mode", "")).lower() == "yaml":
        # If a duplicate dashboard:lovelace already exists from .storage,
        # the YAML one overrides — last-write-wins per RefIndex.add_node.
        ui_lovelace = config_root / "ui-lovelace.yaml"
        if ui_lovelace.is_file():
            _walk_yaml_dashboard(
                index, loader, ui_lovelace,
                url_path="lovelace",
                title=None,
                source_rel="ui-lovelace.yaml",
            )

    # Case 2: named YAML dashboards
    dashboards = lovelace.get("dashboards") or {}
    if isinstance(dashboards, dict):
        for url_path, entry in dashboards.items():
            if not isinstance(entry, dict):
                continue
            if str(entry.get("mode", "")).lower() != "yaml":
                continue
            filename = entry.get("filename")
            if not filename:
                continue
            target = config_root / filename
            if not target.is_file():
                logger.debug(
                    "YAML dashboard %r references missing file %s",
                    url_path, filename,
                )
                continue
            _walk_yaml_dashboard(
                index, loader, target,
                url_path=str(url_path),
                title=entry.get("title"),
                source_rel=str(filename),
            )

    # Tell the loose-scan pass to skip every file we touched (the loader
    # records each via include resolution).
    covered_files.update(loader._included)  # noqa: SLF001 — internal but stable


def _walk_yaml_dashboard(
    index: RefIndex,
    loader: HaYamlLoader,
    abs_path: Path,
    *,
    url_path: str,
    title: str | None,
    source_rel: str,
) -> None:
    """Load one YAML dashboard file (with !include resolution) and emit nodes/edges."""
    rel = abs_path.resolve().relative_to(loader._config_root)  # noqa: SLF001 — internal but stable
    result = loader.load(Path(rel))
    config = result.data
    if not isinstance(config, dict):
        return

    dash_nid = node_id("dashboard", url_path)
    views_raw = config.get("views")
    views: list[Any] = views_raw if isinstance(views_raw, list) else []
    index.add_node(NodeMeta(
        node_id=dash_nid,
        node_type="dashboard",
        display_name=title or config.get("title") or url_path,
        properties={
            "title": title or config.get("title"),
            "views_count": len(views),
            "mode": "yaml",
        },
        source_file=source_rel,
    ))

    for vidx, view in enumerate(views):
        if not isinstance(view, dict):
            continue
        view_path = str(view.get("path") or vidx)
        view_local = f"{url_path}/{view_path}"
        view_nid = node_id("dashboard_view", view_local)
        index.add_node(NodeMeta(
            node_id=view_nid,
            node_type="dashboard_view",
            display_name=view.get("title") or view.get("path") or f"view[{vidx}]",
            properties={"title": view.get("title"), "path": view.get("path")},
            source_file=source_rel,
        ))
        index.add_edge(Edge(
            source=dash_nid,
            target=view_nid,
            kind="contains",
            location=f"{source_rel}:views[{vidx}]",
        ))

        seen: set[tuple[str, str]] = set()
        for ref in walk_dashboard_for_refs(view, path_prefix=f"views[{vidx}]"):
            target = node_id("entity", ref.ref_id)
            key = (target, ref.path)
            if key in seen:
                continue
            seen.add(key)
            index.add_edge(Edge(
                source=view_nid,
                target=target,
                kind="renders_on",
                location=f"{source_rel}:{ref.path}",
            ))


# ── Shared ref-emission helper ────────────────────────────────────────


def _emit_refs_from_walk(
    index: RefIndex,
    source_nid: str,
    data: Any,
    source_file: str,
    root_path: str,
) -> None:
    """Run the generic YAML ref walker on `data` and emit edges from
    `source_nid` to each discovered reference target.
    """
    for ref in walk_yaml_for_refs(data, path_prefix=root_path):
        target = node_id(ref.ref_type, ref.ref_id)
        location = f"{source_file}:{ref.path}" if ref.path else source_file
        index.add_edge(Edge(
            source=source_nid,
            target=target,
            kind=ref.edge_kind,
            location=location,
        ))


# ── Loose YAML scan ────────────────────────────────────────────────────


def _path_is_excluded(
    rel_path: str,
    skip_dirs: frozenset[str],
    skip_globs: tuple[str, ...],
) -> bool:
    """Return True if `rel_path` falls under any skipped directory name OR
    matches any glob pattern.

    Path-matching uses `fnmatch` against both the full relative path and
    the basename, so patterns like `*.bak` match `foo/bar.bak` and
    `secrets.yaml` matches `my/deep/secrets.yaml`.
    """
    import fnmatch
    from pathlib import PurePosixPath
    p = PurePosixPath(rel_path)

    # Directory skip — any segment of the path matches a skip dir name
    for part in p.parts:
        if part in skip_dirs:
            return True

    # Glob skip — match against the name AND the full path
    name = p.name
    for pattern in skip_globs:
        if fnmatch.fnmatch(name, pattern):
            return True
        if fnmatch.fnmatch(rel_path, pattern):
            return True

    return False


# Loose-scan exclusions. Previously exposed as addon options — that
# surface was removed in v0.10 (the refindex is an internal tool, not a
# configurable linter). Inline here so the builder has no config
# dependency. Edit in place if a new dir/glob needs skipping.
_LOOSE_SCAN_SKIP_DIRS: frozenset[str] = frozenset({
    ".storage",            # covered by the structured dashboard + registry passes
    ".cloud",              # HA Cloud internal state
    ".git",                # VCS metadata
    "deps",                # HA's pip cache
    "tts",                 # generated TTS audio clips
    "image",               # image entity thumbnails (binary, no YAML)
    "blueprints",          # !input placeholders would false-positive
    "ha-ops-backups",      # this addon's own backup dir
    "node_modules",        # JS deps
    "__pycache__",         # Python bytecode
    "esphome",             # device source; scoped out
    "custom_components",   # vendored third-party code
    "_backup_",            # user-snapshot convention
    "backup",
    "backups",
})

_LOOSE_SCAN_SKIP_GLOBS: tuple[str, ...] = (
    "*.bak",
    "*.disabled",
    "*.old",
    "*.orig",
    "*.backup",
    "secrets.yaml",        # `key: value` pairs only, never entity refs
)


def _index_loose_yaml(
    index: RefIndex, ctx: HaOpsContext, covered_files: set[str]
) -> None:
    """Catch-all pass: walk every *.yaml/*.yml under config_root that wasn't
    already indexed by the structured passes, and emit entity refs.

    Why this exists: power users (e.g. ULM theme users) split dashboards
    across many YAML files in custom directories that aren't formally
    registered as `lovelace.dashboards.*` in configuration.yaml. Without
    this pass, refs in those files are invisible to the graph — and any
    rename built on graph data alone would silently leave them broken.

    Scope:
        - Walks everything under `config_root` recursively
        - Skips `_LOOSE_SCAN_SKIP_DIRS` and `_LOOSE_SCAN_SKIP_FILES`
        - Skips files in `covered_files` (already structurally indexed)
        - Reads each file with `HaYamlLoader` (so HA tags + includes resolve)
        - Walks parsed data with `walk_dashboard_for_refs` (most permissive
          entity-ref extractor)
        - Emits `references` edges from a synthetic `yaml_file:<rel_path>`
          source node to each `entity:X` it finds

    Errors are swallowed per file: a malformed YAML file becomes an issue,
    not a crashed build.
    """
    config_root = ctx.path_guard.config_root
    skip_dirs = _LOOSE_SCAN_SKIP_DIRS
    skip_globs = _LOOSE_SCAN_SKIP_GLOBS
    loader = HaYamlLoader(config_root, path_guard=ctx.path_guard.validate)

    for path in _iter_yaml_files(config_root, skip_dirs):
        try:
            rel = str(path.resolve().relative_to(config_root))
        except ValueError:
            continue
        if rel in covered_files:
            continue
        if _path_is_excluded(rel, skip_dirs, skip_globs):
            continue

        try:
            result = loader.load(Path(rel))
        except Exception as e:
            logger.debug("Loose YAML scan: skipping %s (%s)", rel, e)
            continue
        if not isinstance(result.data, (dict, list)):
            continue

        # Use the dashboard walker — it's the most permissive ref extractor
        # (handles entity:, entity_id:, entities: at any depth without
        # requiring a known card schema).
        refs = list(walk_dashboard_for_refs(result.data))
        if not refs:
            continue

        src_nid = node_id("yaml_file", rel)
        # Register the file as a node so refactor_check can group locations
        # by file. Display name is just the relative path.
        index.add_node(NodeMeta(
            node_id=src_nid,
            node_type="yaml_file",
            display_name=rel,
            properties={"loose_scan": True},
            source_file=rel,
        ))

        seen: set[tuple[str, str]] = set()
        for ref in refs:
            target = node_id("entity", ref.ref_id)
            key = (target, ref.path)
            if key in seen:
                continue
            seen.add(key)
            index.add_edge(Edge(
                source=src_nid,
                target=target,
                kind="references",
                location=f"{rel}:{ref.path}",
            ))


def _iter_yaml_files(
    root: Path, skip_dirs: frozenset[str] = frozenset()
) -> Iterator[Path]:
    """Recursive walk of *.yaml/*.yml under root, skipping named dirs.

    Hidden directories (`.foo`) are also skipped except for `.storage`
    (which usually appears in `skip_dirs` anyway — kept for safety). The
    caller supplies the full skip set (config-driven).
    """
    stack: list[Path] = [root]
    while stack:
        d = stack.pop()
        try:
            entries = list(d.iterdir())
        except OSError:
            continue
        for entry in entries:
            name = entry.name
            if entry.is_dir():
                if name in skip_dirs:
                    continue
                if name.startswith(".") and name != ".storage":
                    continue
                stack.append(entry)
            elif entry.is_file() and entry.suffix.lower() in {".yaml", ".yml"}:
                yield entry
