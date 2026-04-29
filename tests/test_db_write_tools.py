"""Tests for DB write tools — execute, purge, statistics."""

from __future__ import annotations

import pytest

from ha_ops_mcp.tools.db import haops_db_execute, haops_db_purge, haops_db_statistics


@pytest.mark.asyncio
async def test_db_execute_preview(ctx):
    result = await haops_db_execute(
        ctx, sql="DELETE FROM states WHERE state_id = 1"
    )
    assert "explain" in result
    assert "token" in result


@pytest.mark.asyncio
async def test_db_execute_preview_invalid_sql(ctx):
    """Invalid SQL still gets a preview with a failed EXPLAIN."""
    result = await haops_db_execute(ctx, sql="DROP DATABASE homeassistant")
    assert "token" in result
    assert "EXPLAIN failed" in result["explain"][0]


@pytest.mark.asyncio
async def test_db_execute_confirm(ctx):
    # Preview first
    preview = await haops_db_execute(
        ctx,
        sql="DELETE FROM states WHERE state_id = 999",
    )
    assert "token" in preview

    # Confirm
    result = await haops_db_execute(
        ctx,
        sql="DELETE FROM states WHERE state_id = 999",
        confirm=True,
        token=preview["token"],
    )
    assert result["success"] is True
    assert "affected_rows" in result


@pytest.mark.asyncio
async def test_db_execute_sql_mismatch(ctx):
    preview = await haops_db_execute(ctx, sql="DELETE FROM states WHERE state_id = 1")
    result = await haops_db_execute(
        ctx,
        sql="DELETE FROM states WHERE state_id = 2",
        confirm=True,
        token=preview["token"],
    )
    assert "error" in result
    assert "does not match" in result["error"]


@pytest.mark.asyncio
async def test_db_execute_no_db(ctx):
    ctx.db = None
    result = await haops_db_execute(ctx, sql="SELECT 1")
    assert "error" in result


@pytest.mark.asyncio
async def test_db_purge_dry_run(ctx):
    result = await haops_db_purge(ctx, keep_days=7, dry_run=True)
    assert result["dry_run"] is True
    assert "estimated_states_rows" in result


@pytest.mark.asyncio
async def test_db_purge_with_entity_filter(ctx):
    result = await haops_db_purge(
        ctx,
        keep_days=7,
        entity_filter=["sensor.temperature"],
        dry_run=True,
    )
    assert result["dry_run"] is True
    assert result["entity_filter"] == ["sensor.temperature"]


@pytest.mark.asyncio
async def test_db_statistics_list(ctx):
    result = await haops_db_statistics(ctx, command="list")
    assert "statistics" in result


@pytest.mark.asyncio
async def test_db_statistics_orphans(ctx):
    result = await haops_db_statistics(ctx, command="orphans")
    assert "orphans" in result


@pytest.mark.asyncio
async def test_db_statistics_stale(ctx):
    result = await haops_db_statistics(ctx, command="stale")
    assert "stale" in result


@pytest.mark.asyncio
async def test_db_statistics_info_missing(ctx):
    result = await haops_db_statistics(
        ctx, command="info", entity_id="sensor.nonexistent"
    )
    assert "error" in result


@pytest.mark.asyncio
async def test_db_statistics_info_no_entity_id(ctx):
    result = await haops_db_statistics(ctx, command="info")
    assert "error" in result


@pytest.mark.asyncio
async def test_db_statistics_unknown_command(ctx):
    result = await haops_db_statistics(ctx, command="bogus")
    assert "error" in result
