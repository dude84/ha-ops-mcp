"""Tests for haops_backup_prune — two-phase preview/apply, token safety,
clear_all escape hatch, audit entry shape.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ha_ops_mcp.tools.backup import haops_backup_prune


async def _seed(ctx, source: Path, n: int) -> None:
    for i in range(n):
        source.write_text(f"content {i}\n")
        await ctx.backup.backup_file(source, operation=f"seed_{i}")


def _forge_old_entry(
    ctx, backup_dir: Path, source: Path, days_ago: int
) -> str:
    """Write a manifest entry dated days_ago into the past."""
    ts = (datetime.now(UTC) - timedelta(days=days_ago)).isoformat()
    entry_id = f"config_forged_{days_ago}"
    forged_path = backup_dir / "config" / f"{entry_id}.bak"
    forged_path.write_text("x")
    entry = {
        "id": entry_id,
        "timestamp": ts,
        "type": "config",
        "source": str(source),
        "backup_path": str(forged_path),
        "operation": "forged",
        "size_bytes": 1,
    }
    with open(backup_dir / "manifest.jsonl", "a") as f:
        f.write(json.dumps(entry) + "\n")
    return entry_id


@pytest.mark.asyncio
async def test_prune_preview_and_apply(ctx):
    """Two-phase: preview returns token + would_delete; apply actually removes."""
    src = ctx.path_guard.config_root / "automations.yaml"
    src.write_text("base\n")
    await _seed(ctx, src, 1)
    backup_dir = Path(ctx.config.backup.dir)
    forged_id = _forge_old_entry(ctx, backup_dir, src, days_ago=60)

    # Preview with a tight override → the forged entry lands in scope.
    preview = await haops_backup_prune(ctx, older_than_days=30)
    assert "token" in preview
    assert preview["count"] == 1
    assert any(e["id"] == forged_id for e in preview["would_delete"])

    # Apply.
    applied = await haops_backup_prune(
        ctx, confirm=True, token=preview["token"]
    )
    assert applied["success"] is True
    assert applied["deleted_count"] == 1
    assert applied["bytes_freed"] >= 1

    # Forged entry is gone from manifest and disk.
    remaining_ids = {
        json.loads(line)["id"]
        for line in (backup_dir / "manifest.jsonl").read_text().splitlines()
        if line.strip()
    }
    assert forged_id not in remaining_ids


@pytest.mark.asyncio
async def test_prune_preview_no_op_returns_zero(ctx):
    """When nothing matches, no token is issued — caller can't apply a
    no-op that would consume retry budget accidentally."""
    result = await haops_backup_prune(ctx, older_than_days=365)
    assert "token" not in result
    assert result["count"] == 0


@pytest.mark.asyncio
async def test_prune_apply_uses_token_details_not_kwargs(ctx):
    """Apply ignores fresh kwargs; uses token's preview scope."""
    src = ctx.path_guard.config_root / "automations.yaml"
    src.write_text("base\n")
    await _seed(ctx, src, 1)
    backup_dir = Path(ctx.config.backup.dir)
    forged_id = _forge_old_entry(ctx, backup_dir, src, days_ago=60)

    preview = await haops_backup_prune(ctx, older_than_days=30)
    token = preview["token"]

    # Apply with conflicting kwargs — should be IGNORED; token wins.
    applied = await haops_backup_prune(
        ctx, older_than_days=999, type="all", confirm=True, token=token
    )
    assert applied["deleted_count"] == 1
    remaining = {
        json.loads(line)["id"]
        for line in (backup_dir / "manifest.jsonl").read_text().splitlines()
        if line.strip()
    }
    assert forged_id not in remaining


@pytest.mark.asyncio
async def test_prune_clear_all_with_type_filter(ctx):
    """clear_all + type=config wipes config backups only."""
    src = ctx.path_guard.config_root / "automations.yaml"
    src.write_text("base\n")
    await _seed(ctx, src, 2)
    await ctx.backup.backup_dashboard(
        "dash_a", {"title": "A"}, operation="seed_dash"
    )

    preview = await haops_backup_prune(
        ctx, type="config", clear_all=True
    )
    assert preview["count"] == 2

    applied = await haops_backup_prune(
        ctx, confirm=True, token=preview["token"]
    )
    assert applied["deleted_count"] == 2

    backup_dir = Path(ctx.config.backup.dir)
    types = [
        json.loads(line)["type"]
        for line in (backup_dir / "manifest.jsonl").read_text().splitlines()
        if line.strip()
    ]
    # Only the dashboard entry remains.
    assert types == ["dashboard"]


@pytest.mark.asyncio
async def test_prune_token_single_use(ctx):
    src = ctx.path_guard.config_root / "automations.yaml"
    src.write_text("base\n")
    await _seed(ctx, src, 1)
    backup_dir = Path(ctx.config.backup.dir)
    _forge_old_entry(ctx, backup_dir, src, days_ago=60)

    preview = await haops_backup_prune(ctx, older_than_days=30)
    token = preview["token"]

    first = await haops_backup_prune(ctx, confirm=True, token=token)
    assert first["success"] is True

    second = await haops_backup_prune(ctx, confirm=True, token=token)
    assert "error" in second


@pytest.mark.asyncio
async def test_prune_apply_writes_audit_entry(ctx):
    src = ctx.path_guard.config_root / "automations.yaml"
    src.write_text("base\n")
    await _seed(ctx, src, 1)
    backup_dir = Path(ctx.config.backup.dir)
    forged_id = _forge_old_entry(ctx, backup_dir, src, days_ago=60)

    preview = await haops_backup_prune(ctx, older_than_days=30)
    await haops_backup_prune(ctx, confirm=True, token=preview["token"])

    # Find the audit entry.
    recent = ctx.audit.read_recent(limit=20)
    prune_entries = [e for e in recent if e["tool"] == "backup_prune"]
    assert prune_entries
    entry = prune_entries[0]
    assert entry["success"] is True
    d = entry["details"]
    assert d["deleted_count"] == 1
    assert d["older_than_days"] == 30
    assert d["type"] == "all"
    assert d["clear_all"] is False
    # Compact deleted list — id/source/type only, no full backup_path.
    assert len(d["deleted"]) == 1
    assert d["deleted"][0]["id"] == forged_id
    assert "backup_path" not in d["deleted"][0]
