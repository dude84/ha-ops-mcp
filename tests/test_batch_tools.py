"""Tests for batch tools (haops_batch_preview, haops_batch_apply).

Regression: _gaps/session_gaps_2026-04-16.md §13 — cross-file consistency
was unprotected because each single-item patch+apply is its own token.
Mid-batch failure left HA in a half-renamed state; these tests pin the
atomic semantics: one token for the whole batch, rollback from backup on
any failure, single audit entry.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from ha_ops_mcp.tools.batch import haops_batch_apply, haops_batch_preview

# ── fixtures ──────────────────────────────────────────────────────────────

@pytest.fixture
def dashboard_storage(config_dir: Path):
    storage = config_dir / ".storage"
    (storage / "lovelace").write_text(json.dumps({
        "version": 1,
        "data": {
            "config": {
                "title": "Home",
                "views": [
                    {
                        "title": "Overview",
                        "path": "overview",
                        "cards": [{"type": "entities", "entity": "light.old"}],
                    },
                ],
            }
        },
    }))
    return storage


# ── shape validation ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_batch_preview_rejects_empty_list(ctx):
    result = await haops_batch_preview(ctx, items=[])
    assert "error" in result
    assert "non-empty" in result["error"]


@pytest.mark.asyncio
async def test_batch_preview_rejects_unknown_tool(ctx):
    result = await haops_batch_preview(
        ctx, items=[{"tool": "db_execute", "sql": "DELETE FROM x"}]
    )
    assert "error" in result
    assert "unknown" in result["error"].lower()


@pytest.mark.asyncio
async def test_batch_preview_rejects_malformed_config_patch(ctx):
    result = await haops_batch_preview(
        ctx, items=[{"tool": "config_patch", "path": "automations.yaml"}]
    )
    assert "error" in result
    assert "patch" in result["error"]


# ── happy path ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_batch_preview_and_apply_mixed_items(ctx, dashboard_storage):
    """3-item batch: config_patch + config_create + dashboard_patch.

    Preview produces one token + combined diff. Apply writes all three,
    logs one audit entry, and returns per-target backup paths.
    """
    # Seed an existing config file the patch can target
    (ctx.path_guard.config_root / "automations.yaml").write_text(
        "automation:\n  - id: '1'\n    alias: Foo\n"
    )

    # Unified diff against current automations.yaml: rename alias
    config_patch_text = (
        "--- a/automations.yaml\n"
        "+++ b/automations.yaml\n"
        "@@ -1,3 +1,3 @@\n"
        " automation:\n"
        "   - id: '1'\n"
        "-    alias: Foo\n"
        "+    alias: Foo Renamed\n"
    )

    preview = await haops_batch_preview(ctx, items=[
        {"tool": "config_patch", "path": "automations.yaml",
         "patch": config_patch_text},
        {"tool": "config_create", "path": "new_file.yaml",
         "content": "key: value\n"},
        {"tool": "dashboard_patch", "dashboard_id": "lovelace",
         "patch": [{"op": "replace", "path": "/title", "value": "Renamed"}]},
    ])

    assert "token" in preview
    assert len(preview["targets"]) == 3
    assert preview["total_size_bytes"] > 0
    assert "config_patch" in preview["combined_diff_rendered"]
    assert "config_create" in preview["combined_diff_rendered"]
    assert "dashboard_patch" in preview["combined_diff_rendered"]

    # Mock WS save for the dashboard apply path
    ctx.ws.send_command = AsyncMock(return_value=None)

    apply_result = await haops_batch_apply(ctx, token=preview["token"])
    assert apply_result["success"] is True
    assert len(apply_result["results"]) == 3

    # config_patch should have a backup; config_create should not
    results_by_tool = {r["tool"]: r for r in apply_result["results"]}
    assert results_by_tool["config_patch"]["backup_path"] is not None
    assert results_by_tool["config_create"]["backup_path"] is None

    # Writes landed on disk
    automations = (ctx.path_guard.config_root / "automations.yaml").read_text()
    assert "Foo Renamed" in automations

    new_file = ctx.path_guard.config_root / "new_file.yaml"
    assert new_file.is_file()

    # Dashboard save was invoked via WS
    ctx.ws.send_command.assert_awaited()


# ── partial failure → rollback ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_batch_apply_rollback_on_mid_batch_failure(ctx, dashboard_storage):
    """Item 2 (dashboard) raises during apply → item 1 (config) restored from backup.

    This is the §13 correctness claim: no half-applied state.
    """
    original = "automation:\n  - id: '1'\n    alias: Original\n"
    automations_path = ctx.path_guard.config_root / "automations.yaml"
    automations_path.write_text(original)

    patch_text = (
        "--- a/automations.yaml\n"
        "+++ b/automations.yaml\n"
        "@@ -1,3 +1,3 @@\n"
        " automation:\n"
        "   - id: '1'\n"
        "-    alias: Original\n"
        "+    alias: Modified\n"
    )

    preview = await haops_batch_preview(ctx, items=[
        {"tool": "config_patch", "path": "automations.yaml", "patch": patch_text},
        {"tool": "dashboard_patch", "dashboard_id": "lovelace",
         "patch": [{"op": "replace", "path": "/title", "value": "Fails"}]},
    ])
    assert "token" in preview

    # Force the dashboard save to fail
    from ha_ops_mcp.connections.websocket import WebSocketError
    ctx.ws.send_command = AsyncMock(side_effect=WebSocketError("simulated WS drop"))

    apply_result = await haops_batch_apply(ctx, token=preview["token"])

    assert apply_result["success"] is False
    assert apply_result["failed_at"]["tool"] == "dashboard_patch"
    assert len(apply_result["rolled_back"]) == 1
    assert apply_result["rolled_back"][0]["target"] == str(automations_path)
    assert apply_result["still_dirty"] == []

    # Automations file restored to pre-batch state
    assert automations_path.read_text() == original


@pytest.mark.asyncio
async def test_batch_apply_rollback_deletes_created_file(ctx, dashboard_storage):
    """On rollback, config_create items are DELETED (no backup to restore from)."""
    new_path = ctx.path_guard.config_root / "new_from_batch.yaml"
    assert not new_path.exists()

    preview = await haops_batch_preview(ctx, items=[
        {"tool": "config_create", "path": "new_from_batch.yaml",
         "content": "key: value\n"},
        {"tool": "dashboard_patch", "dashboard_id": "lovelace",
         "patch": [{"op": "replace", "path": "/title", "value": "Fails"}]},
    ])

    from ha_ops_mcp.connections.websocket import WebSocketError
    ctx.ws.send_command = AsyncMock(side_effect=WebSocketError("boom"))

    apply_result = await haops_batch_apply(ctx, token=preview["token"])
    assert apply_result["success"] is False
    assert not new_path.exists()


# ── token semantics ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_batch_apply_rejects_single_item_token(ctx):
    """A token from haops_config_patch must NOT be consumable by batch_apply."""
    (ctx.path_guard.config_root / "automations.yaml").write_text(
        "automation:\n  - id: '1'\n    alias: Foo\n"
    )
    from ha_ops_mcp.tools.config import haops_config_patch
    single = await haops_config_patch(
        ctx, path="automations.yaml",
        patch=(
            "--- a/automations.yaml\n"
            "+++ b/automations.yaml\n"
            "@@ -1,3 +1,3 @@\n"
            " automation:\n"
            "   - id: '1'\n"
            "-    alias: Foo\n"
            "+    alias: Bar\n"
        ),
    )
    assert "token" in single

    result = await haops_batch_apply(ctx, token=single["token"])
    assert "error" in result
    assert "batch_apply" in result["error"]


@pytest.mark.asyncio
async def test_batch_apply_single_use_token(ctx):
    """Batch tokens are single-use like every other confirmation token."""
    (ctx.path_guard.config_root / "automations.yaml").write_text(
        "automation:\n  - id: '1'\n    alias: Foo\n"
    )
    patch_text = (
        "--- a/automations.yaml\n"
        "+++ b/automations.yaml\n"
        "@@ -1,3 +1,3 @@\n"
        " automation:\n"
        "   - id: '1'\n"
        "-    alias: Foo\n"
        "+    alias: Bar\n"
    )

    preview = await haops_batch_preview(ctx, items=[
        {"tool": "config_patch", "path": "automations.yaml", "patch": patch_text},
    ])
    token = preview["token"]

    first = await haops_batch_apply(ctx, token=token)
    assert first["success"] is True

    second = await haops_batch_apply(ctx, token=token)
    assert "error" in second


# ── preview refuses invalid items cleanly ────────────────────────────────

@pytest.mark.asyncio
async def test_batch_preview_refuses_on_bad_patch_context(ctx):
    """If item 2's patch doesn't apply, NO token is issued for the whole batch."""
    (ctx.path_guard.config_root / "automations.yaml").write_text(
        "automation:\n  - id: '1'\n    alias: Foo\n"
    )

    good_patch = (
        "--- a/automations.yaml\n"
        "+++ b/automations.yaml\n"
        "@@ -1,3 +1,3 @@\n"
        " automation:\n"
        "   - id: '1'\n"
        "-    alias: Foo\n"
        "+    alias: Good\n"
    )
    bad_patch = (
        "--- a/scripts.yaml\n"
        "+++ b/scripts.yaml\n"
        "@@ -1,1 +1,1 @@\n"
        "-does not match anything\n"
        "+new\n"
    )
    (ctx.path_guard.config_root / "scripts.yaml").write_text(
        "script:\n  test: {}\n"
    )

    result = await haops_batch_preview(ctx, items=[
        {"tool": "config_patch", "path": "automations.yaml", "patch": good_patch},
        {"tool": "config_patch", "path": "scripts.yaml", "patch": bad_patch},
    ])
    assert "error" in result
    assert "items[1]" in result["error"]
    assert "token" not in result
