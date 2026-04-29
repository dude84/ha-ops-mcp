"""Tests for haops_entity_find — fuzzy entity search."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ha_ops_mcp.tools.entity import haops_entity_find


@pytest.fixture
def fuzzy_ctx(ctx):
    """Extend the default ctx's storage with extra entities suited for
    fuzzy-search tests (humidifier in Kitchen, a couple of bedroom sensors).

    The default fixture is pinned to 3 entities by other tests, so we can't
    extend it globally — overlay storage files instead.
    """
    config_root = Path(ctx.config.filesystem.config_root)
    storage = config_root / ".storage"

    entity_registry = json.loads((storage / "core.entity_registry").read_text())
    entity_registry["data"]["entities"].extend([
        {
            "entity_id": "humidifier.kitchen_dehumidifier",
            "name": None,
            "original_name": "Smart Dehumidifier 2",
            "platform": "xiaomi_miio",
            "area_id": "kitchen",
            "device_id": "dev_dehum",
            "disabled_by": None,
        },
        {
            "entity_id": "sensor.bedroom_temperature",
            "name": "Bedroom Temperature",
            "original_name": "Temperature",
            "platform": "mqtt",
            "area_id": None,
            "device_id": "dev_bedroom",
            "disabled_by": None,
        },
        {
            "entity_id": "sensor.bedroom_humidity",
            "name": "Bedroom Humidity",
            "original_name": "Humidity",
            "platform": "mqtt",
            "area_id": None,
            "device_id": "dev_bedroom",
            "disabled_by": None,
        },
    ])
    (storage / "core.entity_registry").write_text(json.dumps(entity_registry))

    device_registry = json.loads((storage / "core.device_registry").read_text())
    device_registry["data"]["devices"].extend([
        {
            "id": "dev_dehum",
            "name": "Xiaomi Smart Dehumidifier",
            "name_by_user": None,
            "manufacturer": "Xiaomi",
            "model": "CJXJSQ04ZM",
            "area_id": "kitchen",
            "disabled_by": None,
            "identifiers": [["xiaomi_miio", "dehum_1"]],
            "config_entries": ["xiaomi_entry_1"],
        },
        {
            "id": "dev_bedroom",
            "name": "Bedroom Climate Sensor",
            "name_by_user": None,
            "manufacturer": "Aqara",
            "model": "WSDCGQ11LM",
            "area_id": "bedroom",
            "disabled_by": None,
            "identifiers": [["mqtt", "bedroom_1"]],
            "config_entries": ["mqtt_entry_1"],
        },
    ])
    (storage / "core.device_registry").write_text(json.dumps(device_registry))

    area_registry = json.loads((storage / "core.area_registry").read_text())
    area_registry["data"]["areas"].append(
        {"id": "bedroom", "name": "Bedroom", "floor_id": "upstairs"}
    )
    (storage / "core.area_registry").write_text(json.dumps(area_registry))

    return ctx


@pytest.mark.asyncio
async def test_find_empty_query_returns_error(fuzzy_ctx):
    result = await haops_entity_find(fuzzy_ctx, query="")
    assert result["count"] == 0
    assert "error" in result


@pytest.mark.asyncio
async def test_find_exact_entity_id(fuzzy_ctx):
    result = await haops_entity_find(fuzzy_ctx, query="humidifier.kitchen_dehumidifier")
    assert result["count"] >= 1
    top = result["matches"][0]
    assert top["entity_id"] == "humidifier.kitchen_dehumidifier"
    assert top["score"] >= 90


@pytest.mark.asyncio
async def test_find_friendly_name_partial(fuzzy_ctx):
    """The session repro: 'dehumidifier' should hit the humidifier entity."""
    result = await haops_entity_find(fuzzy_ctx, query="dehumidifier")
    assert result["count"] >= 1
    top = result["matches"][0]
    assert top["entity_id"] == "humidifier.kitchen_dehumidifier"


@pytest.mark.asyncio
async def test_find_area_keyword(fuzzy_ctx):
    """Area name should pull entities effectively in that area."""
    result = await haops_entity_find(fuzzy_ctx, query="kitchen", limit=10)
    eids = [m["entity_id"] for m in result["matches"]]
    assert "humidifier.kitchen_dehumidifier" in eids


@pytest.mark.asyncio
async def test_find_domain_prefilter(fuzzy_ctx):
    """domain pre-filter narrows to that domain only."""
    result = await haops_entity_find(
        fuzzy_ctx, query="bedroom", domain="sensor", limit=10
    )
    for m in result["matches"]:
        assert m["entity_id"].startswith("sensor.")


@pytest.mark.asyncio
async def test_find_threshold_excludes_weak_matches(fuzzy_ctx):
    """High threshold drops weak matches."""
    weak = await haops_entity_find(fuzzy_ctx, query="zzzzzzzzz", threshold=80)
    assert weak["count"] == 0


@pytest.mark.asyncio
async def test_find_limit_truncates(fuzzy_ctx):
    """limit caps returned matches and reports truncated."""
    result = await haops_entity_find(
        fuzzy_ctx, query="bedroom", limit=1, threshold=0
    )
    assert result["count"] <= 1
    assert "total" in result


@pytest.mark.asyncio
async def test_find_returns_matched_field(fuzzy_ctx):
    """Each match reports which field gave the best score."""
    result = await haops_entity_find(fuzzy_ctx, query="dehumidifier")
    assert result["matches"], "expected at least one match"
    assert result["matches"][0]["matched_field"] in {
        "entity_id", "friendly_name", "device_name", "area_name",
    }


@pytest.mark.asyncio
async def test_find_device_name_match(fuzzy_ctx):
    """Matching on device manufacturer/model name surfaces the entity."""
    result = await haops_entity_find(fuzzy_ctx, query="xiaomi smart", limit=5)
    eids = [m["entity_id"] for m in result["matches"]]
    assert "humidifier.kitchen_dehumidifier" in eids
