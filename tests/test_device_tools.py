"""Tests for haops_device_info (device_list was removed; use registry_query)."""

from __future__ import annotations

import pytest

from ha_ops_mcp.tools.device import haops_device_info


@pytest.mark.asyncio
async def test_device_info_by_exact_id(ctx):
    result = await haops_device_info(ctx, device="dev_001")
    assert "error" not in result
    assert result["device"]["id"] == "dev_001"
    assert result["device"]["manufacturer"] == "Xiaomi"
    # Area name is resolved from area_id
    assert result["device"]["area_name"] == "Living Room"


@pytest.mark.asyncio
async def test_device_info_by_name_substring(ctx):
    result = await haops_device_info(ctx, device="philips")
    assert "error" not in result
    assert result["device"]["id"] == "dev_002"


@pytest.mark.asyncio
async def test_device_info_returns_linked_entities(ctx):
    result = await haops_device_info(ctx, device="dev_002")
    assert "entities" in result
    assert result["entity_count"] == 1
    assert result["entities"][0]["entity_id"] == "light.living_room"


@pytest.mark.asyncio
async def test_device_info_not_found(ctx):
    result = await haops_device_info(ctx, device="nonexistent_xyz")
    assert "error" in result


@pytest.mark.asyncio
async def test_device_info_multiple_matches_disambiguates(ctx):
    # "living" matches both dev_001 (Living Room Temp) and dev_002 (Living Room Light)
    result = await haops_device_info(ctx, device="living")
    assert "error" in result
    assert "matches" in result
    assert len(result["matches"]) == 2
