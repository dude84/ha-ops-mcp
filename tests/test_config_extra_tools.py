"""Tests for config validate and search tools."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from ha_ops_mcp.tools.config import haops_config_search, haops_config_validate


@pytest.mark.asyncio
async def test_config_validate_valid(ctx):
    ctx.rest.post = AsyncMock(return_value={
        "service_response": {"errors": None, "warnings": None},
    })
    result = await haops_config_validate(ctx)
    assert result["valid"] is True


@pytest.mark.asyncio
async def test_config_validate_invalid(ctx):
    ctx.rest.post = AsyncMock(return_value={
        "service_response": {"errors": "Invalid platform: foo"},
    })
    result = await haops_config_validate(ctx)
    assert result["valid"] is False
    assert result["errors"] == "Invalid platform: foo"


@pytest.mark.asyncio
async def test_config_search_basic(ctx):
    result = await haops_config_search(ctx, pattern="name")
    assert result["count"] > 0
    assert any("configuration.yaml" in m["file"] for m in result["matches"])


@pytest.mark.asyncio
async def test_config_search_regex(ctx):
    result = await haops_config_search(ctx, pattern=r"unit_system:\s+\w+")
    assert result["count"] >= 1


@pytest.mark.asyncio
async def test_config_search_no_matches(ctx):
    result = await haops_config_search(ctx, pattern="ZZZZNOTFOUNDZZZ")
    assert result["count"] == 0


@pytest.mark.asyncio
async def test_config_search_invalid_regex(ctx):
    result = await haops_config_search(ctx, pattern="[invalid")
    assert "error" in result


@pytest.mark.asyncio
async def test_config_search_max_results(ctx):
    result = await haops_config_search(ctx, pattern=".", max_results=2)
    assert result["count"] <= 2


@pytest.mark.asyncio
async def test_config_search_nested_yaml_found_with_default_paths(ctx):
    """Default **/*.yaml should find nested YAML under any subfolder."""
    scripts_dir = ctx.path_guard.config_root / "scripts"
    scripts_dir.mkdir(exist_ok=True)
    (scripts_dir / "livingroom_ac.yaml").write_text(
        "livingroom_ac_default:\n  sequence: []\n"
    )

    result = await haops_config_search(
        ctx, pattern="livingroom_ac_default"
    )
    assert result["count"] >= 1
    assert any("livingroom_ac.yaml" in m["file"] for m in result["matches"])


@pytest.mark.asyncio
async def test_config_search_skips_storage_by_default(ctx):
    """With include_registries=False, .storage JSON files are skipped."""
    # secrets are in secrets.yaml which is NOT in .storage — this should match
    # normally. But a device-registry-only value should be invisible.
    result = await haops_config_search(
        ctx, pattern="Xiaomi"  # only exists in core.device_registry
    )
    # Manufacturer appears nowhere in user-facing YAML; no hits expected
    assert result["count"] == 0


@pytest.mark.asyncio
async def test_config_search_include_registries(ctx):
    """With include_registries=True, .storage/core.* JSON is scanned."""
    result = await haops_config_search(
        ctx, pattern="Xiaomi", include_registries=True
    )
    assert result["count"] >= 1
    assert any(".storage" in m["file"] for m in result["matches"])
