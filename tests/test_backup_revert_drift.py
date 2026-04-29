"""Regression test for backup_revert drift annotation.

Scenario: user applies a config change at T0, HA re-writes the same file
at T1 (injecting drift), then user tries to revert via haops_backup_revert.
Before this annotation, the preview diff lumped intended-revert + drift
together. After: the preview returns both separately so the user can
tell what else the restore will touch.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ha_ops_mcp.tools.backup import haops_backup_revert
from ha_ops_mcp.tools.config import haops_config_apply, haops_config_patch
from ha_ops_mcp.utils.diff import unified_diff


def _patch_for(path: Path, old: str, new: str) -> str:
    return unified_diff(old, new, path.name)


@pytest.mark.asyncio
async def test_backup_revert_annotates_drift_when_audit_has_original(ctx):
    """When the apply audit entry is still in the recent window, preview
    exposes intended_revert + drift_since_apply in addition to the full diff."""
    automations = ctx.path_guard.config_root / "automations.yaml"
    t0 = "automation:\n  - id: '1'\n    alias: Original\n"
    automations.write_text(t0)

    # Apply: Original → Changed. audit entry stores old/new.
    t1 = t0.replace("Original", "Changed")
    patched = await haops_config_patch(
        ctx, path="automations.yaml", patch=_patch_for(automations, t0, t1)
    )
    applied = await haops_config_apply(ctx, token=patched["token"])
    backup_path = applied["backup_path"]
    # Normalise backup_path to what backup_list returns (string).
    assert backup_path

    # Simulate HA drift: post-apply, an extra line appears (a comment HA
    # inserted on reload). This is what makes full-file restore dangerous.
    t2 = t1 + "# HA wrote this at T1\n"
    automations.write_text(t2)

    # Find the backup_id for the just-taken backup.
    from ha_ops_mcp.tools.backup import haops_backup_list
    backups = await haops_backup_list(ctx, type="config")
    target_backup = next(
        b for b in backups["backups"] if b["backup_path"] == backup_path
    )

    preview = await haops_backup_revert(ctx, backup_id=target_backup["id"])
    assert "token" in preview
    # Full restore diff includes both the Original→Changed reversal AND
    # the drift removal.
    assert "Original" in preview["diff"]
    assert "HA wrote this" in preview["diff"]

    # Annotation splits the two:
    assert "intended_revert" in preview
    assert "drift_since_apply" in preview
    # intended_revert is Changed → Original (reverse of apply)
    assert "-    alias: Changed" in preview["intended_revert"]
    assert "+    alias: Original" in preview["intended_revert"]
    # drift_since_apply only contains the HA-inserted comment
    assert "HA wrote this" in preview["drift_since_apply"]
    assert "Original" not in preview["drift_since_apply"]
    # Warning surfaces because drift is non-empty.
    assert "warning" in preview


@pytest.mark.asyncio
async def test_backup_revert_no_drift_annotation_when_no_audit(ctx):
    """If no matching audit entry exists (e.g., backup predates session),
    preview keeps working but without drift annotation."""
    automations = ctx.path_guard.config_root / "automations.yaml"
    automations.write_text("original\n")

    # Manually create a backup without a corresponding audit entry.
    entry = await ctx.backup.backup_file(automations, operation="test_seed")
    automations.write_text("drifted\n")

    preview = await haops_backup_revert(ctx, backup_id=entry.id)
    assert "token" in preview
    assert "diff" in preview
    # No annotation fields when the audit record is missing.
    assert "intended_revert" not in preview
    assert "drift_since_apply" not in preview
