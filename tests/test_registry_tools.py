"""Tests for haops_registry_query."""

from __future__ import annotations

import pytest

from ha_ops_mcp.tools.registry import haops_registry_query


@pytest.mark.asyncio
async def test_registry_query_unknown(ctx):
    result = await haops_registry_query(ctx, registry="bogus")
    assert "error" in result
    assert "devices" in result["supported"]


@pytest.mark.asyncio
async def test_registry_query_devices_all(ctx):
    result = await haops_registry_query(ctx, registry="devices")
    assert result["registry"] == "devices"
    assert result["total"] == 3
    assert result["returned"] == 3
    assert not result["truncated"]
    # Summary projection includes id, name, manufacturer, model, area_id
    assert "id" in result["results"][0]
    assert "manufacturer" in result["results"][0]


@pytest.mark.asyncio
async def test_registry_query_substring_filter(ctx):
    result = await haops_registry_query(
        ctx, registry="devices", filter={"manufacturer": "xiao"}
    )
    assert result["total"] == 1
    assert result["results"][0]["manufacturer"] == "Xiaomi"


@pytest.mark.asyncio
async def test_registry_query_filter_on_name_by_user(ctx):
    # name_by_user is a direct field — substring match
    result = await haops_registry_query(
        ctx, registry="devices", filter={"name_by_user": "living"}
    )
    assert result["total"] == 1
    assert result["results"][0]["name_by_user"] == "Living Room Light"


@pytest.mark.asyncio
async def test_registry_query_filter_on_identifiers_list(ctx):
    # identifiers is a list of lists — substring match must handle this
    result = await haops_registry_query(
        ctx, registry="devices",
        filter={"identifiers": "hue"},
        fields=["id", "name", "identifiers"],
    )
    assert result["total"] == 1
    assert result["results"][0]["id"] == "dev_002"


@pytest.mark.asyncio
async def test_registry_query_projection(ctx):
    result = await haops_registry_query(
        ctx, registry="devices", fields=["id", "manufacturer"]
    )
    for record in result["results"]:
        assert set(record.keys()) == {"id", "manufacturer"}


@pytest.mark.asyncio
async def test_registry_query_pagination(ctx):
    page1 = await haops_registry_query(ctx, registry="devices", limit=2, offset=0)
    page2 = await haops_registry_query(ctx, registry="devices", limit=2, offset=2)
    assert page1["total"] == 3
    assert page1["returned"] == 2
    assert page1["truncated"] is True
    assert page2["returned"] == 1
    assert page2["truncated"] is False
    assert page1["results"][0]["id"] != page2["results"][0]["id"]


@pytest.mark.asyncio
async def test_registry_query_count_only(ctx):
    result = await haops_registry_query(
        ctx, registry="devices", count_only=True
    )
    assert result["total"] == 3
    assert "results" not in result


@pytest.mark.asyncio
async def test_registry_query_entities(ctx):
    result = await haops_registry_query(ctx, registry="entities")
    assert result["total"] == 3  # matches the entity fixture


@pytest.mark.asyncio
async def test_registry_query_areas(ctx):
    result = await haops_registry_query(ctx, registry="areas")
    assert result["total"] == 2
    names = {r["name"] for r in result["results"]}
    assert names == {"Living Room", "Kitchen"}


@pytest.mark.asyncio
async def test_registry_query_floors(ctx):
    result = await haops_registry_query(ctx, registry="floors")
    assert result["total"] == 2
    assert any(r["name"] == "Ground Floor" for r in result["results"])


@pytest.mark.asyncio
async def test_registry_query_config_entries_in_error_state(ctx):
    """The real-world 'which integrations failed to load' question."""
    result = await haops_registry_query(
        ctx, registry="config_entries", filter={"state": "setup_error"}
    )
    assert result["total"] == 1
    assert result["results"][0]["domain"] == "broken"


@pytest.mark.asyncio
async def test_registry_query_case_insensitive(ctx):
    result = await haops_registry_query(
        ctx, registry="devices", filter={"manufacturer": "XIAO"}
    )
    assert result["total"] == 1
