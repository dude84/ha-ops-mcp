"""Tests for entity tools."""

from __future__ import annotations

import pytest

from ha_ops_mcp.tools.entity import haops_entity_audit, haops_entity_list


@pytest.mark.asyncio
async def test_entity_list_all(ctx):
    result = await haops_entity_list(ctx)
    assert result["count"] == 3
    ids = [e["entity_id"] for e in result["entities"]]
    assert "sensor.temperature" in ids
    assert "sensor.orphan" in ids


@pytest.mark.asyncio
async def test_entity_list_default_is_tight_3_field_summary(ctx):
    """Regression: default response had 8 fields per entity, blowing the MCP
    result-size limit on large-area queries (116 KB on 419 sensors). Default
    is now a 3-field summary; verbose payload requires full=true."""
    result = await haops_entity_list(ctx)
    e = result["entities"][0]
    assert set(e.keys()) == {"entity_id", "friendly_name", "state"}


@pytest.mark.asyncio
async def test_entity_list_full_returns_verbose_summary(ctx):
    """`full=true` returns the previous 8-field summary."""
    result = await haops_entity_list(ctx, full=True)
    e = result["entities"][0]
    assert set(e.keys()) >= {
        "entity_id", "friendly_name", "state", "last_changed",
        "area_id", "platform", "device_id", "disabled_by",
    }


@pytest.mark.asyncio
async def test_entity_list_explicit_fields_overrides_default(ctx):
    """Explicit fields=[] projection overrides both the tight default and full mode."""
    result = await haops_entity_list(
        ctx, fields=["entity_id", "platform"], full=True  # full ignored when fields set
    )
    e = result["entities"][0]
    assert set(e.keys()) == {"entity_id", "platform"}


@pytest.mark.asyncio
async def test_entity_list_filter_domain(ctx):
    result = await haops_entity_list(ctx, domain="light")
    assert result["count"] == 1
    assert result["entities"][0]["entity_id"] == "light.living_room"


@pytest.mark.asyncio
async def test_entity_list_filter_state(ctx):
    result = await haops_entity_list(ctx, state="unavailable")
    assert result["count"] == 1
    assert result["entities"][0]["entity_id"] == "sensor.orphan"


@pytest.mark.asyncio
async def test_entity_list_filter_integration(ctx):
    result = await haops_entity_list(ctx, integration="hue")
    assert result["count"] == 1


@pytest.mark.asyncio
async def test_entity_audit(ctx):
    result = await haops_entity_audit(ctx)

    assert result["summary"]["total_entities"] == 3
    assert result["summary"]["unavailable"] == 1
    assert result["summary"]["orphaned"] == 1  # sensor.orphan has no device and no area

    # sensor.orphan should be unavailable
    unavail_ids = [e["entity_id"] for e in result["unavailable"]]
    assert "sensor.orphan" in unavail_ids

    # sensor.orphan has no name
    assert "sensor.orphan" in result["no_friendly_name"]

    # New surface (added v0.8.7) — area_ratio_outliers should always be present
    # (empty on this small fixture; the actual outlier logic needs ≥3 areas
    # with devices + entities to compute a median).
    assert "area_ratio_outliers" in result["summary"]
    assert result["area_ratio_outliers"] == []


@pytest.mark.asyncio
async def test_entity_audit_flags_area_ratio_outliers(ctx):
    """Inject a 'pfsense-style' area: 1 device, 30 entities — should appear
    as an outlier when there are ≥3 areas to compute a median against."""
    import json
    cfg = ctx.path_guard.config_root

    # Add a third area (the fixture has 'living_room' and 'kitchen' already)
    area_path = cfg / ".storage" / "core.area_registry"
    area_data = json.loads(area_path.read_text())
    area_data["data"]["areas"].append(
        {"id": "office", "name": "Office", "floor_id": "ground"}
    )
    area_path.write_text(json.dumps(area_data))

    # Add the pfSense-style device under 'office'
    dev_path = cfg / ".storage" / "core.device_registry"
    dev_data = json.loads(dev_path.read_text())
    dev_data["data"]["devices"].append({
        "id": "dev_pfsense",
        "name": "pfSense",
        "manufacturer": "Netgate",
        "model": "SG-2100",
        "area_id": "office",
        "disabled_by": None,
        "identifiers": [["pfsense", "router_1"]],
        "config_entries": [],
    })
    # Also add a balancing device in living_room/kitchen so the median
    # ratio stays low (1:1) and the outlier stands out.
    for did, aid in [("dev_lr", "living_room"), ("dev_kt", "kitchen")]:
        dev_data["data"]["devices"].append({
            "id": did,
            "name": did,
            "area_id": aid,
            "disabled_by": None,
            "identifiers": [],
            "config_entries": [],
        })
    dev_path.write_text(json.dumps(dev_data))

    # Add 50 entities under the pfSense device (all in office via device area).
    # Need a clear outlier: with median ~10 and threshold = max(median*3, 20),
    # ratio 50 is comfortably above 30.
    ent_path = cfg / ".storage" / "core.entity_registry"
    ent_data = json.loads(ent_path.read_text())
    for i in range(50):
        ent_data["data"]["entities"].append({
            "entity_id": f"sensor.pfsense_metric_{i}",
            "name": f"pfSense Metric {i}",
            "platform": "pfsense",
            "area_id": None,
            "device_id": "dev_pfsense",
            "disabled_by": None,
        })
    # Plus one entity in each of the other devices so those areas have refs
    for eid, did in [("sensor.lr_x", "dev_lr"), ("sensor.kt_x", "dev_kt")]:
        ent_data["data"]["entities"].append({
            "entity_id": eid,
            "name": eid,
            "platform": "test",
            "area_id": None,
            "device_id": did,
            "disabled_by": None,
        })
    # Boost living_room and kitchen entity counts so ratios are low (~1:1)
    # and the outlier check has enough to compute a median against
    for area, did in [("living_room", "dev_lr"), ("kitchen", "dev_kt")]:
        for i in range(9):
            ent_data["data"]["entities"].append({
                "entity_id": f"sensor.{area}_filler_{i}",
                "name": "filler",
                "platform": "test",
                "area_id": None,
                "device_id": did,
                "disabled_by": None,
            })
    ent_path.write_text(json.dumps(ent_data))

    result = await haops_entity_audit(ctx)
    outliers = result["area_ratio_outliers"]
    office_outlier = next(
        (o for o in outliers if o["area_id"] == "office"), None
    )
    assert office_outlier is not None
    assert office_outlier["entities"] == 50
    assert office_outlier["devices"] == 1
    assert office_outlier["ratio"] == 50.0
    assert office_outlier["area_name"] == "Office"


@pytest.mark.asyncio
async def test_entity_list_count_only(ctx):
    result = await haops_entity_list(ctx, count_only=True)
    assert result["total"] == 3
    assert result["count"] == 3
    assert "entities" not in result


@pytest.mark.asyncio
async def test_entity_list_limit_and_offset(ctx):
    page1 = await haops_entity_list(ctx, limit=2, offset=0)
    page2 = await haops_entity_list(ctx, limit=2, offset=2)
    assert page1["total"] == 3
    assert page1["returned"] == 2
    assert page1["truncated"] is True
    assert page2["returned"] == 1
    assert page2["truncated"] is False
    # No overlap between pages
    page1_ids = {e["entity_id"] for e in page1["entities"]}
    page2_ids = {e["entity_id"] for e in page2["entities"]}
    assert page1_ids.isdisjoint(page2_ids)


@pytest.mark.asyncio
async def test_entity_list_fields_projection(ctx):
    result = await haops_entity_list(
        ctx, fields=["entity_id", "state"]
    )
    for entity in result["entities"]:
        assert set(entity.keys()) == {"entity_id", "state"}


@pytest.mark.asyncio
async def test_entity_list_no_default_limit(ctx):
    """Unbounded output preserves backward compat."""
    result = await haops_entity_list(ctx)
    # All 3 entities returned, not truncated
    assert result["returned"] == 3
    assert result["truncated"] is False


# ── haops_entity_state ──


@pytest.mark.asyncio
async def test_entity_state_single(ctx):
    from ha_ops_mcp.tools.entity import haops_entity_state

    result = await haops_entity_state(ctx, entity_id="sensor.temperature")
    assert result["entity_id"] == "sensor.temperature"
    assert result["state"] == "22.5"
    # Full attributes by default
    assert result["attributes"]["unit_of_measurement"] == "°C"


@pytest.mark.asyncio
async def test_entity_state_batch(ctx):
    from ha_ops_mcp.tools.entity import haops_entity_state

    result = await haops_entity_state(
        ctx, entity_id=["sensor.temperature", "light.living_room"]
    )
    assert result["count"] == 2
    assert result["entities"][0]["state"] == "22.5"
    assert result["entities"][1]["state"] == "on"


@pytest.mark.asyncio
async def test_entity_state_attribute_projection(ctx):
    from ha_ops_mcp.tools.entity import haops_entity_state

    result = await haops_entity_state(
        ctx,
        entity_id="sensor.temperature",
        attributes=["friendly_name"],
    )
    assert set(result["attributes"].keys()) == {"friendly_name"}


@pytest.mark.asyncio
async def test_entity_state_attribute_projection_empty(ctx):
    """attributes=[] returns no attributes (useful for size control)."""
    from ha_ops_mcp.tools.entity import haops_entity_state

    result = await haops_entity_state(
        ctx, entity_id="sensor.temperature", attributes=[]
    )
    assert result["attributes"] == {}


@pytest.mark.asyncio
async def test_entity_state_missing_entity(ctx):
    from ha_ops_mcp.tools.entity import haops_entity_state

    result = await haops_entity_state(
        ctx, entity_id="sensor.does_not_exist"
    )
    assert "error" in result


# ── Effective area filter (Gap 9) ──


@pytest.mark.asyncio
async def test_entity_list_effective_area_inherits_from_device(ctx):
    """Entity with null area_id should match if its device's area matches."""
    import json

    # Add an entity with NO area_id but linked to a device in living_room
    registry_path = ctx.path_guard.config_root / ".storage" / "core.entity_registry"
    data = json.loads(registry_path.read_text())
    data["data"]["entities"].append({
        "entity_id": "climate.living_room_ac",
        "name": "Living Room AC",
        "platform": "esphome",
        "area_id": None,  # No direct area
        "device_id": "dev_002",  # dev_002 has area_id=living_room
        "disabled_by": None,
    })
    registry_path.write_text(json.dumps(data))

    # effective mode (default): should find the AC because device is in living_room
    result = await haops_entity_list(ctx, area="living_room")
    ids = {e["entity_id"] for e in result["entities"]}
    assert "climate.living_room_ac" in ids


@pytest.mark.asyncio
async def test_entity_list_entity_area_mode_is_strict(ctx):
    """area_mode='entity' matches only on entity.area_id, not device inheritance."""
    import json

    registry_path = ctx.path_guard.config_root / ".storage" / "core.entity_registry"
    data = json.loads(registry_path.read_text())
    data["data"]["entities"].append({
        "entity_id": "climate.living_room_ac",
        "name": "Living Room AC",
        "platform": "esphome",
        "area_id": None,
        "device_id": "dev_002",
        "disabled_by": None,
    })
    registry_path.write_text(json.dumps(data))

    result = await haops_entity_list(
        ctx, area="living_room", area_mode="entity"
    )
    ids = {e["entity_id"] for e in result["entities"]}
    assert "climate.living_room_ac" not in ids
    # sensor.temperature has area_id='living_room' directly
    assert "sensor.temperature" in ids


@pytest.mark.asyncio
async def test_entity_list_area_by_name(ctx):
    """Area filter accepts area names too, not just ids."""
    result = await haops_entity_list(ctx, area="Living Room")
    # Our fixture has area id 'living_room' with name 'Living Room'
    assert result["returned"] >= 1
