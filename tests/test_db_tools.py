"""Tests for database tools."""

from __future__ import annotations

import pytest

from ha_ops_mcp.tools.db import haops_db_health, haops_db_query


@pytest.mark.asyncio
async def test_db_query_basic(ctx):
    result = await haops_db_query(ctx, sql="SELECT entity_id, state FROM states ORDER BY state_id")
    assert result["backend"] == "sqlite"
    assert result["row_count"] == 3
    assert result["rows"][0]["entity_id"] == "sensor.temperature"
    assert result["rows"][0]["state"] == "22.5"


@pytest.mark.asyncio
async def test_db_query_reports_session_timezone(ctx):
    result = await haops_db_query(ctx, sql="SELECT 1 AS one")
    assert "session_timezone" in result
    assert "SQLite" in result["session_timezone"]


@pytest.mark.asyncio
async def test_db_query_with_limit(ctx):
    result = await haops_db_query(ctx, sql="SELECT * FROM states", limit=1)
    assert result["row_count"] == 1
    assert result.get("truncated") is True


@pytest.mark.asyncio
async def test_db_query_no_db(ctx):
    ctx.db = None
    result = await haops_db_query(ctx, sql="SELECT 1")
    assert "error" in result
    assert "not configured" in result["error"]


@pytest.mark.asyncio
async def test_db_health(ctx):
    result = await haops_db_health(ctx)
    assert result["backend"] == "sqlite"
    assert "SQLite" in result["version"]
    assert result["schema_version"] == 43
    assert any(t["name"] == "states" for t in result["tables"])
    states_table = next(t for t in result["tables"] if t["name"] == "states")
    assert states_table["row_count"] == 3
