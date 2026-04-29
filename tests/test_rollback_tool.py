"""Tests for haops_rollback and the batch_apply transaction integration.

Covers:
- haops_batch_apply returns a transaction_id on success, no id on failure.
- haops_rollback(transaction_id) reverts a committed batch via in-memory
  undo entries, not via backup files (so drift is preserved).
- haops_rollback of a config_create item deletes the newly-created file.
- Two-phase token flow (preview → apply) and single-use semantics.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from ha_ops_mcp.tools.batch import haops_batch_apply, haops_batch_preview
from ha_ops_mcp.tools.rollback import haops_rollback


@pytest.fixture
def dashboard_storage(config_dir: Path):
    storage = config_dir / ".storage"
    (storage / "lovelace").write_text(json.dumps({
        "version": 1,
        "data": {
            "config": {
                "title": "Home",
                "views": [{"title": "Overview", "cards": []}],
            }
        },
    }))
    return storage


def _patch_for(path: Path, old: str, new: str) -> str:
    """Build a small unified-diff patch (single-hunk) for testing."""
    from ha_ops_mcp.utils.diff import unified_diff
    return unified_diff(old, new, path.name)


# ── batch_apply now returns transaction_id ───────────────────────────────

@pytest.mark.asyncio
async def test_batch_apply_success_returns_transaction_id(ctx):
    automations = ctx.path_guard.config_root / "automations.yaml"
    old = "automation:\n  - id: '1'\n    alias: Foo\n"
    automations.write_text(old)
    new = old.replace("Foo", "Bar")
    patch = _patch_for(automations, old, new)

    preview = await haops_batch_preview(ctx, items=[
        {"tool": "config_patch", "path": "automations.yaml", "patch": patch},
    ])
    result = await haops_batch_apply(ctx, token=preview["token"])
    assert result["success"] is True
    assert "transaction_id" in result
    assert ctx.rollback.get_transaction(result["transaction_id"]).committed


@pytest.mark.asyncio
async def test_batch_apply_failure_discards_transaction(ctx, dashboard_storage):
    automations = ctx.path_guard.config_root / "automations.yaml"
    automations.write_text("automation:\n  - id: '1'\n    alias: Foo\n")
    patch = _patch_for(automations, automations.read_text(),
                       "automation:\n  - id: '1'\n    alias: Bar\n")

    preview = await haops_batch_preview(ctx, items=[
        {"tool": "config_patch", "path": "automations.yaml", "patch": patch},
        {"tool": "dashboard_patch", "dashboard_id": "lovelace",
         "patch": [{"op": "replace", "path": "/title", "value": "X"}]},
    ])

    from ha_ops_mcp.connections.websocket import WebSocketError
    ctx.ws.send_command = AsyncMock(side_effect=WebSocketError("simulated"))

    apply_result = await haops_batch_apply(ctx, token=preview["token"])
    assert apply_result["success"] is False
    # No transaction_id on failure — discarded.
    assert "transaction_id" not in apply_result


# ── haops_rollback — happy path ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_rollback_preview_lists_targets(ctx):
    automations = ctx.path_guard.config_root / "automations.yaml"
    old = "automation:\n  - id: '1'\n    alias: Foo\n"
    automations.write_text(old)
    patch = _patch_for(automations, old, old.replace("Foo", "Bar"))

    preview = await haops_batch_preview(ctx, items=[
        {"tool": "config_patch", "path": "automations.yaml", "patch": patch},
        {"tool": "config_create", "path": "new_after.yaml", "content": "key: 1\n"},
    ])
    applied = await haops_batch_apply(ctx, token=preview["token"])
    txn_id = applied["transaction_id"]

    rb_preview = await haops_rollback(ctx, transaction_id=txn_id)
    assert "token" in rb_preview
    assert rb_preview["count"] == 2
    # Reverse order — config_create (added last) is first to undo.
    assert rb_preview["targets"][0]["action"] == "delete"
    assert rb_preview["targets"][1]["action"] == "restore content"


@pytest.mark.asyncio
async def test_rollback_preview_includes_per_target_diffs(ctx):
    """§ Preview returns a diff per target + a combined markdown block."""
    automations = ctx.path_guard.config_root / "automations.yaml"
    old = "automation:\n  - id: '1'\n    alias: Foo\n"
    automations.write_text(old)
    new = old.replace("Foo", "Bar")
    patch = _patch_for(automations, old, new)

    preview = await haops_batch_preview(ctx, items=[
        {"tool": "config_patch", "path": "automations.yaml", "patch": patch},
        {"tool": "config_create", "path": "new_after.yaml", "content": "key: 1\n"},
    ])
    applied = await haops_batch_apply(ctx, token=preview["token"])

    rb_preview = await haops_rollback(ctx, transaction_id=applied["transaction_id"])
    # First target is the config_create (newest first) — shows "will delete"
    assert "will delete" in rb_preview["targets"][0]["diff"]
    # Second target is the file-restore — unified diff showing Bar → Foo.
    # ruamel re-emits YAML so indentation can shift; just check the alias
    # lines appear on both sides.
    restore_diff = rb_preview["targets"][1]["diff"]
    assert "-" in restore_diff and "alias: Bar" in restore_diff
    assert "+" in restore_diff and "alias: Foo" in restore_diff
    # Combined markdown wraps everything for the approval modal.
    assert "combined_diff_rendered" in rb_preview
    assert "alias: Foo" in rb_preview["combined_diff_rendered"]
    assert "delete" in rb_preview["combined_diff_rendered"]


@pytest.mark.asyncio
async def test_rollback_apply_restores_file_and_deletes_created(ctx):
    automations = ctx.path_guard.config_root / "automations.yaml"
    original = "automation:\n  - id: '1'\n    alias: Foo\n"
    automations.write_text(original)
    patch = _patch_for(automations, original, original.replace("Foo", "Bar"))
    new_file = ctx.path_guard.config_root / "new_after.yaml"

    preview = await haops_batch_preview(ctx, items=[
        {"tool": "config_patch", "path": "automations.yaml", "patch": patch},
        {"tool": "config_create", "path": "new_after.yaml", "content": "key: 1\n"},
    ])
    applied = await haops_batch_apply(ctx, token=preview["token"])
    txn_id = applied["transaction_id"]

    # Mid-state: both mutations landed.
    assert "Bar" in automations.read_text()
    assert new_file.is_file()

    rb_preview = await haops_rollback(ctx, transaction_id=txn_id)
    rb_apply = await haops_rollback(
        ctx, transaction_id=txn_id, confirm=True, token=rb_preview["token"]
    )

    assert rb_apply["success"] is True
    # Original content restored — using in-memory state, not backup file.
    assert "Foo" in automations.read_text()
    assert "Bar" not in automations.read_text()
    # Newly-created file deleted.
    assert not new_file.exists()


@pytest.mark.asyncio
async def test_rollback_of_dashboard_patch_calls_ws_save(ctx, dashboard_storage):
    ctx.ws.send_command = AsyncMock(return_value=None)

    preview = await haops_batch_preview(ctx, items=[
        {"tool": "dashboard_patch", "dashboard_id": "lovelace",
         "patch": [{"op": "replace", "path": "/title", "value": "Renamed"}]},
    ])
    applied = await haops_batch_apply(ctx, token=preview["token"])
    ctx.ws.send_command.reset_mock()

    rb_preview = await haops_rollback(ctx, transaction_id=applied["transaction_id"])
    rb_apply = await haops_rollback(
        ctx, transaction_id=applied["transaction_id"],
        confirm=True, token=rb_preview["token"],
    )
    assert rb_apply["success"] is True
    # Rollback path used WS save with the original config.
    call_kwargs = ctx.ws.send_command.call_args.kwargs
    assert call_kwargs["config"]["title"] == "Home"


# ── haops_rollback — error cases ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_rollback_unknown_transaction(ctx):
    result = await haops_rollback(ctx, transaction_id="does_not_exist")
    assert "error" in result
    assert "not found" in result["error"]


@pytest.mark.asyncio
async def test_rollback_uncommitted_transaction_rejected(ctx):
    """A transaction that was opened but never committed can't be rolled back."""
    pending = ctx.rollback.begin("manual_test_only")
    result = await haops_rollback(ctx, transaction_id=pending.id)
    assert "error" in result
    assert "not committed" in result["error"]


@pytest.mark.asyncio
async def test_rollback_token_bound_to_transaction(ctx):
    """A token issued for transaction A must not apply a rollback of transaction B."""
    automations = ctx.path_guard.config_root / "automations.yaml"
    automations.write_text("automation:\n  - id: '1'\n    alias: Foo\n")
    patch_a = _patch_for(automations, automations.read_text(),
                         "automation:\n  - id: '1'\n    alias: A\n")

    p_a = await haops_batch_preview(ctx, items=[
        {"tool": "config_patch", "path": "automations.yaml", "patch": patch_a},
    ])
    applied_a = await haops_batch_apply(ctx, token=p_a["token"])

    # Apply the rollback for A, then try to reuse its token against a
    # fresh transaction B — should be rejected on the transaction_id check.
    # Build B.
    patch_b = _patch_for(automations, automations.read_text(),
                         automations.read_text().replace("A", "B"))
    p_b = await haops_batch_preview(ctx, items=[
        {"tool": "config_patch", "path": "automations.yaml", "patch": patch_b},
    ])
    applied_b = await haops_batch_apply(ctx, token=p_b["token"])

    # Preview rollback for A, but pass its token to rollback of B.
    rb_a_preview = await haops_rollback(ctx, transaction_id=applied_a["transaction_id"])
    wrong = await haops_rollback(
        ctx, transaction_id=applied_b["transaction_id"],
        confirm=True, token=rb_a_preview["token"],
    )
    assert "error" in wrong
    assert "different transaction" in wrong["error"]


@pytest.mark.asyncio
async def test_rollback_single_use_token(ctx):
    automations = ctx.path_guard.config_root / "automations.yaml"
    automations.write_text("automation:\n  - id: '1'\n    alias: Foo\n")
    patch = _patch_for(automations, automations.read_text(),
                       "automation:\n  - id: '1'\n    alias: Bar\n")
    preview = await haops_batch_preview(ctx, items=[
        {"tool": "config_patch", "path": "automations.yaml", "patch": patch},
    ])
    applied = await haops_batch_apply(ctx, token=preview["token"])

    rb_preview = await haops_rollback(ctx, transaction_id=applied["transaction_id"])
    first = await haops_rollback(
        ctx, transaction_id=applied["transaction_id"],
        confirm=True, token=rb_preview["token"],
    )
    assert first["success"] is True

    second = await haops_rollback(
        ctx, transaction_id=applied["transaction_id"],
        confirm=True, token=rb_preview["token"],
    )
    assert "error" in second
