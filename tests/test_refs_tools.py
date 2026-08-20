"""Tests for the ref-graph MCP tools."""

from __future__ import annotations

import pytest

# Importing triggers registration with the global tool registry.
from ha_ops_mcp.tools.refs import (  # noqa: F401
    haops_refactor_check,
    haops_references,
)

# ── haops_references ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_references_bare_entity_id_resolves(ctx):
    res = await haops_references(ctx, node="sensor.temperature")
    assert "error" not in res
    assert res["node"]["node_id"] == "entity:sensor.temperature"
    assert res["total_refs"] > 0


@pytest.mark.asyncio
async def test_references_typed_device_id(ctx):
    res = await haops_references(ctx, node="device:dev_001")
    assert "error" not in res
    assert res["node"]["node_type"] == "device"


@pytest.mark.asyncio
async def test_references_unknown_node_error(ctx):
    res = await haops_references(ctx, node="entity:does.not_exist")
    assert "error" in res


@pytest.mark.asyncio
async def test_references_shows_incoming_from_dashboard(ctx):
    """sensor.temperature is rendered by the default dashboard in fixtures."""
    res = await haops_references(ctx, node="sensor.temperature")
    incoming_kinds = {e["kind"] for e in res["incoming"]}
    assert "renders_on" in incoming_kinds


# ── haops_refactor_check ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_refactor_check_delete_entity_surfaces_breakage(ctx):
    res = await haops_refactor_check(ctx, node_id="sensor.temperature")
    assert res["affected_files"]
    # sensor.temperature is used in automation, dashboard, customize
    affected_paths = {f["path"] for f in res["affected_files"]}
    assert any(
        "lovelace" in p or "automations.yaml" in p or "customize" in p
        for p in affected_paths
    )


@pytest.mark.asyncio
async def test_refactor_check_rename(ctx):
    res = await haops_refactor_check(
        ctx, node_id="sensor.temperature", new_id="sensor.renamed"
    )
    assert res["new_id"] == "entity:sensor.renamed"
    if res["locations"]:
        assert res["locations"][0]["suggested_value"] == "entity:sensor.renamed"


@pytest.mark.asyncio
async def test_refactor_check_unknown_node(ctx):
    res = await haops_refactor_check(ctx, node_id="entity:ghost")
    assert "error" in res


@pytest.mark.asyncio
async def test_refactor_check_mentions_jinja_limitation(ctx):
    res = await haops_refactor_check(ctx, node_id="sensor.temperature")
    assert "jinja_note" in res


@pytest.mark.asyncio
async def test_refactor_check_no_impact_key(ctx):
    """v0.10: impact analyzer removed; response no longer carries `impact`."""
    res = await haops_refactor_check(ctx, node_id="sensor.temperature")
    assert "impact" not in res


# ── Cross-call cache invalidation ─────────────────────────────────────


@pytest.mark.asyncio
async def test_index_rebuilt_after_config_change(ctx, config_dir):
    """Regression: ctx is a global singleton, so an index cached on
    ctx.request_index by one tool call must not be served stale to the
    next — config edits between calls have to show up."""
    res = await haops_references(ctx, node="sensor.temperature")
    before = {e["source"] for e in res["incoming"]}
    assert "automation:auto_second" not in before

    automations = config_dir / "automations.yaml"
    automations.write_text(
        automations.read_text()
        + "- id: 'auto_second'\n"
        "  alias: Second Automation\n"
        "  trigger:\n"
        "    - platform: state\n"
        "      entity_id: sensor.temperature\n"
        "  action: []\n"
    )

    res = await haops_references(ctx, node="sensor.temperature")
    after = {e["source"] for e in res["incoming"]}
    assert "automation:auto_second" in after


@pytest.mark.asyncio
async def test_refactor_check_sees_config_change(ctx, config_dir):
    """Same invalidation guarantee for haops_refactor_check."""
    await haops_references(ctx, node="sensor.temperature")  # warms the cache

    (config_dir / "scripts.yaml").write_text(
        "night_report:\n"
        "  alias: Night Report\n"
        "  sequence:\n"
        "    - service: notify.notify\n"
        "      data:\n"
        "        message: hello\n"
        "      target:\n"
        "        entity_id: sensor.temperature\n"
    )

    res = await haops_refactor_check(ctx, node_id="sensor.temperature")
    sources = {loc["source_node"] for loc in res["locations"]}
    assert "script:night_report" in sources
