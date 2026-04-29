"""Tests for system tools."""

from __future__ import annotations

import pytest

from ha_ops_mcp.tools.system import haops_system_info, haops_system_logs


@pytest.mark.asyncio
async def test_system_info(ctx):
    result = await haops_system_info(ctx)
    assert result["ha_version"] == "2026.4.1"
    assert result["entity_count"] == 3
    assert result["database"]["backend"] == "sqlite"
    assert result["database"]["schema_version"] == 43


@pytest.mark.asyncio
async def test_system_logs_all(ctx):
    result = await haops_system_logs(ctx)
    assert result["count"] == 2  # two log lines in mock


@pytest.mark.asyncio
async def test_system_logs_filter_level(ctx):
    result = await haops_system_logs(ctx, level="error")
    assert result["count"] == 1
    assert "ERROR" in result["lines"][0]


@pytest.mark.asyncio
async def test_system_logs_filter_integration(ctx):
    result = await haops_system_logs(ctx, integration="hacs")
    assert result["count"] == 1
    assert "hacs" in result["lines"][0].lower()


@pytest.mark.asyncio
async def test_system_logs_filter_pattern(ctx):
    result = await haops_system_logs(ctx, pattern=r"Test \w+")
    assert result["count"] == 2


@pytest.mark.asyncio
async def test_system_logs_limit(ctx):
    result = await haops_system_logs(ctx, lines=1)
    assert result["count"] == 1
