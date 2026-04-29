"""Tests for the reference indexer.

Uses the existing conftest fixtures (`ctx`, `config_dir`) which populate
`.storage/core.*_registry` files with known data. The indexer should
produce a predictable graph from those fixtures.
"""

from __future__ import annotations

import pytest

from ha_ops_mcp.refindex import Edge, NodeMeta, RefIndex, node_id, split_node_id

# ── Pure data-model tests (no ctx needed) ──────────────────────────────


def test_node_id_roundtrip():
    assert node_id("entity", "sensor.kitchen_temp") == "entity:sensor.kitchen_temp"
    assert split_node_id("entity:sensor.kitchen_temp") == ("entity", "sensor.kitchen_temp")


def test_split_node_id_rejects_malformed():
    with pytest.raises(ValueError):
        split_node_id("no_colon_here")


def test_split_node_id_handles_colon_in_local_part():
    # Entity ids can't contain ':' but urls and file paths might — we only
    # split on the FIRST colon.
    assert split_node_id("dashboard_view:lovelace/0") == ("dashboard_view", "lovelace/0")


def test_empty_index_stats():
    index = RefIndex()
    stats = index.stats()
    assert stats["_total_nodes"] == 0
    assert stats["_total_edges"] == 0


def test_add_node_and_read():
    index = RefIndex()
    index.add_node(NodeMeta(
        node_id="entity:sensor.foo",
        node_type="entity",
        display_name="Foo",
    ))
    assert index.node("entity:sensor.foo") is not None
    assert index.node("entity:sensor.foo").display_name == "Foo"
    assert len(index.nodes()) == 1
    assert len(index.nodes("entity")) == 1
    assert len(index.nodes("device")) == 0


def test_add_edge_populates_both_directions():
    index = RefIndex()
    index.add_edge(Edge(source="a", target="b", kind="references"))
    assert len(index.outgoing("a")) == 1
    assert len(index.incoming("b")) == 1
    assert index.outgoing("b") == []
    assert index.incoming("a") == []


def test_neighbors_depth_1():
    index = RefIndex()
    for nid in ["a", "b", "c", "d"]:
        index.add_node(NodeMeta(node_id=nid, node_type="entity"))
    index.add_edge(Edge(source="a", target="b", kind="references"))
    index.add_edge(Edge(source="b", target="c", kind="references"))
    index.add_edge(Edge(source="c", target="d", kind="references"))

    nodes, edges = index.neighbors("a", depth=1)
    assert {n.node_id for n in nodes} == {"a", "b"}
    assert len(edges) == 1


def test_neighbors_depth_2():
    index = RefIndex()
    for nid in ["a", "b", "c", "d"]:
        index.add_node(NodeMeta(node_id=nid, node_type="entity"))
    index.add_edge(Edge(source="a", target="b", kind="references"))
    index.add_edge(Edge(source="b", target="c", kind="references"))
    index.add_edge(Edge(source="c", target="d", kind="references"))

    nodes, edges = index.neighbors("a", depth=2)
    assert {n.node_id for n in nodes} == {"a", "b", "c"}
    assert len(edges) == 2


def test_neighbors_follows_both_directions():
    index = RefIndex()
    for nid in ["a", "b", "c"]:
        index.add_node(NodeMeta(node_id=nid, node_type="entity"))
    index.add_edge(Edge(source="a", target="b", kind="references"))
    index.add_edge(Edge(source="c", target="b", kind="references"))

    # Focused on `b` — should find both a (incoming) and c (incoming)
    nodes, _ = index.neighbors("b", depth=1)
    assert {n.node_id for n in nodes} == {"a", "b", "c"}


def test_neighbors_handles_cycles():
    index = RefIndex()
    for nid in ["a", "b", "c"]:
        index.add_node(NodeMeta(node_id=nid, node_type="entity"))
    index.add_edge(Edge(source="a", target="b", kind="references"))
    index.add_edge(Edge(source="b", target="c", kind="references"))
    index.add_edge(Edge(source="c", target="a", kind="references"))  # cycle

    nodes, edges = index.neighbors("a", depth=5)
    assert {n.node_id for n in nodes} == {"a", "b", "c"}
    assert len(edges) == 3


def test_neighbors_unknown_node_returns_empty():
    index = RefIndex()
    nodes, edges = index.neighbors("nonexistent", depth=1)
    assert nodes == []
    assert edges == []


def test_stats_counts_by_type():
    index = RefIndex()
    index.add_node(NodeMeta(node_id="entity:a", node_type="entity"))
    index.add_node(NodeMeta(node_id="entity:b", node_type="entity"))
    index.add_node(NodeMeta(node_id="device:x", node_type="device"))
    index.add_edge(Edge(source="entity:a", target="device:x", kind="belongs_to"))
    stats = index.stats()
    assert stats["entity"] == 2
    assert stats["device"] == 1
    assert stats["_total_edges"] == 1


# ── Registry-pass integration tests (use conftest's ctx fixture) ───────


@pytest.mark.asyncio
async def test_build_populates_registry_nodes(ctx):
    """With the conftest fixtures (3 entities, 3 devices, 2 areas, 2 floors,
    3 config entries), the built index should contain all of them as nodes."""
    index = RefIndex()
    await index.build(ctx)
    stats = index.stats()
    assert stats.get("entity", 0) == 3
    assert stats.get("device", 0) == 3
    assert stats.get("area", 0) == 2
    assert stats.get("floor", 0) == 2
    assert stats.get("config_entry", 0) == 3


@pytest.mark.asyncio
async def test_build_connects_entity_to_device(ctx):
    index = RefIndex()
    await index.build(ctx)
    # sensor.temperature is linked to dev_001 in the fixture
    out = index.outgoing("entity:sensor.temperature")
    assert any(
        e.kind == "belongs_to" and e.target == "device:dev_001"
        for e in out
    )


@pytest.mark.asyncio
async def test_build_connects_device_to_area(ctx):
    index = RefIndex()
    await index.build(ctx)
    out = index.outgoing("device:dev_001")
    assert any(
        e.kind == "located_in" and e.target == "area:living_room"
        for e in out
    )


@pytest.mark.asyncio
async def test_build_connects_area_to_floor(ctx):
    index = RefIndex()
    await index.build(ctx)
    out = index.outgoing("area:living_room")
    assert any(
        e.kind == "belongs_to" and e.target == "floor:ground"
        for e in out
    )


@pytest.mark.asyncio
async def test_build_connects_config_entry_to_device(ctx):
    index = RefIndex()
    await index.build(ctx)
    out = index.outgoing("config_entry:mqtt_entry_1")
    # dev_001 is provided by mqtt_entry_1 in the fixture
    assert any(
        e.kind == "provides" and e.target == "device:dev_001"
        for e in out
    )


@pytest.mark.asyncio
async def test_build_effective_area_inherited_from_device(ctx):
    """Entity with no direct area_id should inherit from its device."""
    import json
    # Add an entity with no area_id but device in living_room
    registry_path = ctx.path_guard.config_root / ".storage" / "core.entity_registry"
    data = json.loads(registry_path.read_text())
    data["data"]["entities"].append({
        "entity_id": "climate.ac_inherited",
        "name": "Inherited AC",
        "platform": "esphome",
        "area_id": None,
        "device_id": "dev_001",  # dev_001 is in living_room
        "disabled_by": None,
    })
    registry_path.write_text(json.dumps(data))

    index = RefIndex()
    await index.build(ctx)
    out = index.outgoing("entity:climate.ac_inherited")
    area_edges = [e for e in out if e.kind == "located_in"]
    assert len(area_edges) == 1
    assert area_edges[0].target == "area:living_room"
    # And location notes it's inherited
    assert "inherited" in (area_edges[0].location or "")


@pytest.mark.asyncio
async def test_build_does_not_inherit_when_entity_has_direct_area(ctx):
    """Entity with its own area_id should NOT get an inherited edge too."""
    index = RefIndex()
    await index.build(ctx)
    # sensor.temperature has area_id=living_room directly AND device dev_001
    # which is also in living_room. We should see exactly ONE located_in edge.
    out = index.outgoing("entity:sensor.temperature")
    area_edges = [e for e in out if e.kind == "located_in"]
    assert len(area_edges) == 1
    # And it should not be marked as inherited
    assert "inherited" not in (area_edges[0].location or "")


# ── get_or_build_index helper ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_or_build_index_reuses_cached(ctx):
    """When ctx.request_index is already set, get_or_build_index returns it
    without rebuilding."""
    from ha_ops_mcp.refindex import get_or_build_index

    # Simulate an already-populated per-request index
    cached = RefIndex()
    cached.add_node(NodeMeta(node_id="test:sentinel", node_type="entity"))
    ctx.request_index = cached

    result = await get_or_build_index(ctx)
    assert result is cached
    assert result.node("test:sentinel") is not None


@pytest.mark.asyncio
async def test_get_or_build_index_builds_and_caches(ctx):
    """When ctx.request_index is None, get_or_build_index builds fresh and
    stashes it on ctx."""
    from ha_ops_mcp.refindex import get_or_build_index

    ctx.request_index = None
    result = await get_or_build_index(ctx)
    # Should be a fresh index with registry data populated
    assert result.node("entity:sensor.temperature") is not None
    # And should now be cached on ctx
    assert ctx.request_index is result


# ── YAML layer tests (automations / scripts / scenes / groups / customize) ──


@pytest.mark.asyncio
async def test_build_indexes_automations(ctx):
    index = RefIndex()
    await index.build(ctx)
    node = index.node("automation:auto_lights")
    assert node is not None
    assert node.display_name == "Morning Lights"
    assert node.source_file == "automations.yaml"


@pytest.mark.asyncio
async def test_build_automation_edge_kinds(ctx):
    """trigger → triggered_by, condition → conditioned_on, target → targets."""
    index = RefIndex()
    await index.build(ctx)
    out = index.outgoing("automation:auto_lights")
    kinds = {(e.kind, e.target) for e in out}
    assert ("triggered_by", "entity:sensor.temperature") in kinds
    assert ("conditioned_on", "entity:light.living_room") in kinds
    assert ("targets", "entity:light.living_room") in kinds
    assert ("targets", "area:living_room") in kinds


@pytest.mark.asyncio
async def test_build_automation_without_id_slugifies_alias_or_is_skipped(ctx):
    """The second automation has alias but no id; should still be indexed."""
    import json
    # Overwrite automations to test alias-slug fallback more cleanly.
    (ctx.path_guard.config_root / "automations.yaml").write_text(
        "- alias: Good Morning!\n  trigger: []\n  action: []\n"
    )
    index = RefIndex()
    await index.build(ctx)
    assert index.node("automation:good_morning") is not None
    # Ensure no JSON pollution
    _ = json  # silence unused


@pytest.mark.asyncio
async def test_build_indexes_scripts(ctx):
    index = RefIndex()
    await index.build(ctx)
    assert index.node("script:bedtime") is not None
    out = index.outgoing("script:bedtime")
    assert any(
        e.kind == "targets" and e.target == "entity:light.living_room"
        for e in out
    )


@pytest.mark.asyncio
async def test_build_indexes_scenes_with_entities_dict(ctx):
    """scene `entities:` dict keys are the refs."""
    index = RefIndex()
    await index.build(ctx)
    assert index.node("scene:movie_time") is not None
    out = index.outgoing("scene:movie_time")
    targets = {e.target for e in out}
    assert "entity:light.living_room" in targets
    assert "entity:sensor.temperature" in targets


@pytest.mark.asyncio
async def test_build_indexes_groups(ctx):
    index = RefIndex()
    await index.build(ctx)
    node = index.node("group:downstairs")
    assert node is not None
    out = index.outgoing("group:downstairs")
    targets = {e.target for e in out}
    assert "entity:light.living_room" in targets
    assert "entity:sensor.temperature" in targets


@pytest.mark.asyncio
async def test_build_indexes_customize_standalone(ctx):
    index = RefIndex()
    await index.build(ctx)
    node = index.node("customize:sensor.temperature")
    assert node is not None
    out = index.outgoing("customize:sensor.temperature")
    assert any(
        e.kind == "customizes" and e.target == "entity:sensor.temperature"
        for e in out
    )


# ── Jinja integration tests ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_build_extracts_jinja_refs_from_automation_value_template(ctx):
    """Entity refs inside {{ }} in automation values should surface as edges."""
    (ctx.path_guard.config_root / "automations.yaml").write_text(
        "- id: 'templated_auto'\n"
        "  alias: Templated\n"
        "  trigger:\n"
        "    - platform: template\n"
        "      value_template: \"{{ states('sensor.temperature') | float > 20 }}\"\n"
        "  condition:\n"
        "    - condition: template\n"
        "      value_template: \"{{ is_state('light.living_room', 'off') }}\"\n"
        "  action: []\n"
    )
    index = RefIndex()
    await index.build(ctx)
    out = index.outgoing("automation:templated_auto")
    kinds = {(e.kind, e.target) for e in out}
    # Jinja ref inside a trigger block → triggered_by
    assert ("triggered_by", "entity:sensor.temperature") in kinds
    # Jinja ref inside a condition block → conditioned_on
    assert ("conditioned_on", "entity:light.living_room") in kinds


# ── Dashboard tests ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_build_indexes_default_dashboard(ctx):
    index = RefIndex()
    await index.build(ctx)
    assert index.node("dashboard:lovelace") is not None
    assert index.node("dashboard_view:lovelace/overview") is not None


@pytest.mark.asyncio
async def test_dashboard_contains_view(ctx):
    index = RefIndex()
    await index.build(ctx)
    out = index.outgoing("dashboard:lovelace")
    assert any(
        e.kind == "contains" and e.target == "dashboard_view:lovelace/overview"
        for e in out
    )


@pytest.mark.asyncio
async def test_dashboard_renders_on_refs(ctx):
    index = RefIndex()
    await index.build(ctx)
    out = index.outgoing("dashboard_view:lovelace/overview")
    targets = {(e.kind, e.target) for e in out}
    # Direct entity: card
    assert ("renders_on", "entity:sensor.temperature") in targets
    # entities list — scalar form
    assert ("renders_on", "entity:light.living_room") in targets
    # entities list — dict form with `entity:` key
    assert ("renders_on", "entity:sensor.orphan") in targets


@pytest.mark.asyncio
async def test_dashboard_walks_custom_cards(ctx):
    """Custom card types (mushroom, button-card, etc.) should work as long
    as they use the conventional `entity:` / `entities:` keys."""
    index = RefIndex()
    await index.build(ctx)
    # The mushroom card inside vertical-stack should be walked
    out = index.outgoing("dashboard_view:lovelace/overview")
    mushroom_edges = [
        e for e in out
        if e.target == "entity:sensor.temperature" and "vertical-stack" not in (e.location or "")
    ]
    assert mushroom_edges  # at least one edge to sensor.temperature


@pytest.mark.asyncio
async def test_yaml_default_dashboard_indexed(ctx):
    """`lovelace.mode: yaml` with `ui-lovelace.yaml` is walked."""
    cfg = ctx.path_guard.config_root
    (cfg / "configuration.yaml").write_text(
        "homeassistant:\n  name: Test\n"
        "lovelace:\n  mode: yaml\n"
    )
    (cfg / "ui-lovelace.yaml").write_text(
        "title: My Home\n"
        "views:\n"
        "  - title: Main\n"
        "    path: main\n"
        "    cards:\n"
        "      - type: entity\n"
        "        entity: sensor.temperature\n"
    )
    index = RefIndex()
    await index.build(ctx)
    # Default dashboard now also has a YAML representation; the YAML pass
    # writes last so the node reflects YAML mode.
    out = index.outgoing("dashboard_view:lovelace/main")
    assert any(
        e.kind == "renders_on" and e.target == "entity:sensor.temperature"
        for e in out
    )


@pytest.mark.asyncio
async def test_yaml_named_dashboard_indexed(ctx):
    """A named YAML dashboard with `filename:` is walked."""
    cfg = ctx.path_guard.config_root
    (cfg / "configuration.yaml").write_text(
        "homeassistant:\n  name: Test\n"
        "lovelace:\n"
        "  dashboards:\n"
        "    energy-yaml:\n"
        "      mode: yaml\n"
        "      filename: dashboards/energy.yaml\n"
        "      title: Energy YAML\n"
    )
    (cfg / "dashboards").mkdir()
    (cfg / "dashboards" / "energy.yaml").write_text(
        "title: Energy\n"
        "views:\n"
        "  - title: Solar\n"
        "    path: solar\n"
        "    cards:\n"
        "      - type: entities\n"
        "        entities:\n"
        "          - sensor.temperature\n"
        "          - light.living_room\n"
    )
    index = RefIndex()
    await index.build(ctx)
    dash = index.node("dashboard:energy-yaml")
    assert dash is not None
    assert dash.display_name == "Energy YAML"
    out = index.outgoing("dashboard_view:energy-yaml/solar")
    targets = {e.target for e in out if e.kind == "renders_on"}
    assert "entity:sensor.temperature" in targets
    assert "entity:light.living_room" in targets


@pytest.mark.asyncio
async def test_loose_yaml_scan_finds_refs_in_uncovered_files(ctx):
    """Power-user case: YAML files under a custom dir (e.g. ULM theme) that
    aren't formally registered as `lovelace.dashboards.*` should still get
    their entity refs indexed by the loose-scan pass.
    """
    cfg = ctx.path_guard.config_root
    # Mimic ULM directory layout — config not registered anywhere
    (cfg / "ui_lovelace_minimalist" / "dashboard" / "views").mkdir(parents=True)
    (cfg / "ui_lovelace_minimalist" / "dashboard" / "views" / "office.yaml").write_text(
        "title: Office\n"
        "cards:\n"
        "  - type: custom:button-card\n"
        "    entity: sensor.temperature\n"
        "  - type: entities\n"
        "    entities:\n"
        "      - light.living_room\n"
    )
    index = RefIndex()
    await index.build(ctx)
    # The synthetic yaml_file node should exist
    nid = "yaml_file:ui_lovelace_minimalist/dashboard/views/office.yaml"
    assert index.node(nid) is not None
    # Both refs should appear as incoming on the entities
    incoming_temp = {(e.source, e.kind) for e in index.incoming("entity:sensor.temperature")}
    assert (nid, "references") in incoming_temp
    incoming_light = {(e.source, e.kind) for e in index.incoming("entity:light.living_room")}
    assert (nid, "references") in incoming_light


@pytest.mark.asyncio
async def test_loose_yaml_scan_skips_already_covered_files(ctx):
    """A file walked by the structured pass (automations.yaml) should NOT
    also be indexed by the loose scan — that would create duplicate edges
    with different source nodes."""
    index = RefIndex()
    await index.build(ctx)
    # automations.yaml is structurally indexed → no yaml_file node should exist for it
    assert index.node("yaml_file:automations.yaml") is None


@pytest.mark.asyncio
async def test_loose_yaml_scan_skips_custom_components_and_backup(ctx):
    """v0.8.10 — vendored custom_components/ and backup dirs should not be
    walked. They're the biggest source of noise on real instances."""
    cfg = ctx.path_guard.config_root
    (cfg / "custom_components" / "ulm" / "cards").mkdir(parents=True)
    (cfg / "custom_components" / "ulm" / "cards" / "ghost.yaml").write_text(
        "entity: sensor.ghost_from_vendored\n"
    )
    (cfg / "_backup_").mkdir()
    (cfg / "_backup_" / "automations.yaml").write_text(
        "- id: x\n"
        "  trigger:\n"
        "    - platform: state\n"
        "      entity_id: sensor.ghost_from_backup\n"
        "  action: []\n"
    )
    (cfg / "home_backup.bak").write_text(
        "entity: sensor.ghost_from_bak\n"
    )

    index = RefIndex()
    await index.build(ctx)
    yaml_file_nodes = {n.node_id for n in index.nodes("yaml_file")}
    assert not any("custom_components" in nid for nid in yaml_file_nodes)
    assert not any("_backup_" in nid for nid in yaml_file_nodes)
    assert not any(".bak" in nid for nid in yaml_file_nodes)


@pytest.mark.asyncio
async def test_loose_yaml_scan_skips_secrets_and_storage(ctx):
    """secrets.yaml and .storage/* should never appear as yaml_file nodes."""
    cfg = ctx.path_guard.config_root
    # secrets.yaml exists in the conftest fixture
    (cfg / ".storage" / "irrelevant.yaml").write_text("entity: sensor.x\n")
    index = RefIndex()
    await index.build(ctx)
    yaml_file_nodes = {n.node_id for n in index.nodes("yaml_file")}
    assert "yaml_file:secrets.yaml" not in yaml_file_nodes
    assert not any(".storage" in nid for nid in yaml_file_nodes)


@pytest.mark.asyncio
async def test_yaml_dashboard_with_include_chain_indexed(ctx):
    """!include in a YAML dashboard resolves and refs from nested files surface."""
    cfg = ctx.path_guard.config_root
    (cfg / "configuration.yaml").write_text(
        "lovelace:\n"
        "  dashboards:\n"
        "    main:\n"
        "      mode: yaml\n"
        "      filename: dashboards/main.yaml\n"
    )
    (cfg / "dashboards").mkdir()
    (cfg / "dashboards" / "main.yaml").write_text(
        "title: Main\n"
        "views:\n"
        "  - !include views/overview.yaml\n"
    )
    (cfg / "dashboards" / "views").mkdir()
    (cfg / "dashboards" / "views" / "overview.yaml").write_text(
        "title: Overview\n"
        "path: overview\n"
        "cards:\n"
        "  - type: entity\n"
        "    entity: sensor.temperature\n"
    )
    index = RefIndex()
    await index.build(ctx)
    out = index.outgoing("dashboard_view:main/overview")
    assert any(
        e.kind == "renders_on" and e.target == "entity:sensor.temperature"
        for e in out
    )


@pytest.mark.asyncio
async def test_dashboard_missing_storage_does_not_crash(ctx):
    (ctx.path_guard.config_root / ".storage" / "lovelace").unlink()
    index = RefIndex()
    await index.build(ctx)
    assert index.node("dashboard:lovelace") is None
    # Registry nodes still present
    assert index.node("entity:sensor.temperature") is not None


@pytest.mark.asyncio
async def test_build_missing_yaml_files_does_not_crash(ctx):
    for filename in ("automations.yaml", "scripts.yaml", "scenes.yaml",
                     "groups.yaml", "customize.yaml"):
        (ctx.path_guard.config_root / filename).unlink(missing_ok=True)
    index = RefIndex()
    await index.build(ctx)
    # Registry nodes still present
    assert index.node("entity:sensor.temperature") is not None
    # No YAML nodes
    assert index.nodes("automation") == []
    assert index.nodes("script") == []


@pytest.mark.asyncio
async def test_get_or_build_index_returns_same_instance_on_subsequent_calls(ctx):
    """Within a request, repeated calls return the same index without rebuild."""
    from ha_ops_mcp.refindex import get_or_build_index

    ctx.request_index = None
    first = await get_or_build_index(ctx)
    second = await get_or_build_index(ctx)
    assert first is second
