"""Backup tools — haops_backup_list, haops_backup_revert, haops_backup_prune."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ha_ops_mcp.safety.rollback import UndoEntry, UndoType
from ha_ops_mcp.server import registry

if TYPE_CHECKING:
    from ha_ops_mcp.server import HaOpsContext


@registry.tool(
    name="haops_backup_list",
    description=(
        "List persistent backups created by ha-ops-mcp. Shows config file backups, "
        "dashboard snapshots, entity registry snapshots, and DB row dumps. "
        "Parameters (all optional): type (string: 'config', 'dashboard', 'entity', 'db', 'all'), "
        "since (string: ISO datetime), limit (int, default 50). "
        "Read-only. For in-session rollback of recent changes, the rollback system handles that "
        "automatically — this tool shows persistent on-disk backups."
    ),
    params={
        "type": {"type": "string", "description": "Filter by backup type", "default": "all"},
        "since": {"type": "string", "description": "Only show backups after this ISO datetime"},
        "limit": {"type": "integer", "description": "Max entries to return", "default": 50},
    },
)
async def haops_backup_list(
    ctx: HaOpsContext,
    type: str = "all",
    since: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    since_dt = None
    if since:
        try:
            since_dt = datetime.fromisoformat(since)
            if since_dt.tzinfo is None:
                since_dt = since_dt.replace(tzinfo=UTC)
        except ValueError:
            return {"error": f"Invalid datetime format: {since}"}

    entries = await ctx.backup.list_backups(
        type_filter=type,
        since=since_dt,
        limit=limit,
    )

    return {
        "backups": [e.to_dict() for e in entries],
        "count": len(entries),
    }


@registry.tool(
    name="haops_backup_revert",
    description=(
        "Revert a previous change using a persistent backup. For changes "
        "made in this session, prefer haops_rollback (more precise, no "
        "drift). Use this tool for older changes or after addon restart "
        "when in-memory transactions are lost. Two-phase: "
        "1) Call with backup_id to preview what will be restored (diff). "
        "2) Call with confirm=true and token to apply the revert. "
        "Dispatches to the appropriate restore mechanism based on backup "
        "type (config file, dashboard, entity registry). "
        "CAVEAT: full-file restore undoes EVERYTHING that has changed "
        "since the backup was taken, not just the change you applied — "
        "HA's own rewrites (re-wrapped descriptions, re-serialised "
        ".storage/*) will also revert. For config files the preview "
        "annotates `intended_revert` (the diff of your original apply, "
        "reversed) vs `drift_since_apply` (everything else) when an audit "
        "record for the original apply is still available. When you only "
        "want to undo a recent change without drift, prefer "
        "haops_rollback(transaction_id) — that replays the in-memory "
        "pre-write state and ignores drift entirely. "
        "Parameters: backup_id (string, required), "
        "confirm (bool, default false), token (string, if confirming). "
        "Preview response includes diff (raw) and diff_rendered (markdown "
        "```diff fence for UI rendering)."
    ),
    params={
        "backup_id": {
            "type": "string",
            "description": "Backup ID from haops_backup_list",
        },
        "confirm": {
            "type": "boolean", "description": "Execute revert",
            "default": False,
        },
        "token": {
            "type": "string",
            "description": "Confirmation token from preview step",
        },
    },
)
async def haops_backup_revert(
    ctx: HaOpsContext,
    backup_id: str,
    confirm: bool = False,
    token: str | None = None,
) -> dict[str, Any]:

    # Find the backup entry
    all_backups = await ctx.backup.list_backups(limit=500)
    entry = next((b for b in all_backups if b.id == backup_id), None)
    if entry is None:
        return {"error": f"Backup '{backup_id}' not found"}

    backup_path = Path(entry.backup_path)
    if not backup_path.exists():
        return {"error": f"Backup file missing: {entry.backup_path}"}

    if entry.type == "config":
        return await _revert_config(
            ctx, entry, backup_path, confirm, token
        )
    if entry.type == "dashboard":
        return await _revert_dashboard(
            ctx, entry, backup_path, confirm, token
        )

    return {
        "error": f"Revert not supported for backup type '{entry.type}'. "
        "DB and entity backups require manual restoration."
    }


def _find_apply_entry_for_backup(
    ctx: HaOpsContext, backup_path: Path
) -> dict[str, Any] | None:
    """Look up the config_apply audit entry that created this backup.

    Backups are 1:1 with a config_apply audit entry (same backup_path).
    Finding it lets us reconstruct the original new_content, which is
    what's needed to separate intended_revert from drift_since_apply in
    the preview.
    """
    want = str(backup_path)
    for entry in ctx.audit.read_recent(limit=500):
        if (
            entry.get("tool") == "config_apply"
            and entry.get("backup_path") == want
        ):
            return entry
    return None


async def _revert_config(
    ctx: HaOpsContext,
    entry: Any,
    backup_path: Path,
    confirm: bool,
    token: str | None,
) -> dict[str, Any]:
    from ha_ops_mcp.utils.diff import render_diff, unified_diff

    source_path = Path(entry.source)
    backup_content = backup_path.read_text()
    current_content = source_path.read_text() if source_path.exists() else ""

    # The primary diff the user will review: what this restore will
    # actually do to the file on disk.
    diff = unified_diff(current_content, backup_content, source_path.name)

    # Best-effort drift annotation — only possible when the original
    # config_apply audit entry is still within the recent window and
    # carries the new_content (old+new stored since v0.15.0).
    intended_revert: str | None = None
    drift_since_apply: str | None = None
    applied_entry = _find_apply_entry_for_backup(ctx, backup_path)
    if applied_entry:
        details = applied_entry.get("details") or {}
        applied_new = details.get("new_content")
        applied_old = details.get("old_content")
        if isinstance(applied_new, str) and isinstance(applied_old, str):
            # intended_revert: reverse of the original apply
            intended_revert = unified_diff(
                applied_new, applied_old, source_path.name
            )
            # drift: everything that has changed since the apply, i.e.
            # current vs. the exact post-apply content. Empty drift means
            # the full-file revert is identical to the surgical one.
            drift_since_apply = unified_diff(
                applied_new, current_content, source_path.name
            )

    if not confirm:
        tk = ctx.safety.create_token(
            action="backup_revert",
            details={
                "backup_id": entry.id,
                "source_path": str(source_path),
                "backup_path": str(backup_path),
                "backup_content": backup_content,
                "current_content": current_content,
            },
        )
        response: dict[str, Any] = {
            "type": "config",
            "source": str(source_path),
            "backup_timestamp": entry.timestamp,
            "diff": diff if diff else "(no differences)",
            "token": tk.id,
            "message": "Review the diff. Call again with confirm=true "
            "and this token to restore.",
        }
        if diff:
            response["diff_rendered"] = render_diff(diff)
        if intended_revert is not None:
            response["intended_revert"] = intended_revert
            response["drift_since_apply"] = drift_since_apply or ""
            if drift_since_apply:
                response["warning"] = (
                    "This full-file restore includes drift (HA or another "
                    "tool wrote to this file after your apply). To undo "
                    "just the original change without reverting drift, "
                    "prefer haops_rollback(transaction_id) if the session "
                    "transaction is still in memory."
                )
        return response

    if token is None:
        return {"error": "confirm=true requires a token"}

    try:
        token_data = ctx.safety.validate_token(token)
    except Exception as e:
        return {"error": str(e)}

    details = token_data.details
    revert_content = details["backup_content"]
    current = details["current_content"]
    src = Path(details["source_path"])

    txn = ctx.rollback.begin("backup_revert")
    txn.savepoint(
        name=f"revert:{src.name}",
        undo=UndoEntry(
            type=UndoType.FILE,
            description=f"Undo revert of {src.name}",
            data={"path": str(src), "content": current},
        ),
    )

    src.write_text(revert_content)

    ctx.safety.consume_token(token)
    ctx.rollback.commit(txn.id)

    await ctx.audit.log(
        tool="backup_revert",
        details={"backup_id": entry.id, "source": str(src)},
        token_id=token,
    )

    return {
        "success": True,
        "source": str(src),
        "restored_from": str(backup_path),
        "transaction_id": txn.id,
    }


async def _revert_dashboard(
    ctx: HaOpsContext,
    entry: Any,
    backup_path: Path,
    confirm: bool,
    token: str | None,
) -> dict[str, Any]:
    from ha_ops_mcp.connections.websocket import WebSocketError
    from ha_ops_mcp.utils.diff import format_json_diff, json_diff, render_diff

    dashboard_id = entry.source
    backup_config = json.loads(backup_path.read_text())

    # Get current config for diff
    from ha_ops_mcp.tools.dashboard import _get_dashboard_config
    current_config = await _get_dashboard_config(ctx, dashboard_id) or {}

    diff = json_diff(current_config, backup_config)
    readable = format_json_diff(diff)

    if not confirm:
        tk = ctx.safety.create_token(
            action="backup_revert",
            details={
                "backup_id": entry.id,
                "dashboard_id": dashboard_id,
                "backup_config": backup_config,
                "current_config": current_config,
            },
        )
        response: dict[str, Any] = {
            "type": "dashboard",
            "dashboard_id": dashboard_id,
            "backup_timestamp": entry.timestamp,
            "diff": readable,
            "token": tk.id,
            "message": "Review the diff. Call again with confirm=true "
            "and this token to restore.",
        }
        if diff:
            response["diff_rendered"] = render_diff(readable)
        return response

    if token is None:
        return {"error": "confirm=true requires a token"}

    try:
        token_data = ctx.safety.validate_token(token)
    except Exception as e:
        return {"error": str(e)}

    details = token_data.details
    restore_config = details["backup_config"]
    current = details["current_config"]
    did = details["dashboard_id"]

    txn = ctx.rollback.begin("backup_revert")
    txn.savepoint(
        name=f"revert:dashboard:{did}",
        undo=UndoEntry(
            type=UndoType.DASHBOARD,
            description=f"Undo revert of dashboard '{did}'",
            data={"dashboard_id": did, "config": current},
        ),
    )

    try:
        kwargs: dict[str, Any] = {"config": restore_config}
        if did != "lovelace":
            kwargs["url_path"] = did
        await ctx.ws.send_command("lovelace/config/save", **kwargs)
    except WebSocketError as e:
        return {"error": f"Failed to restore dashboard: {e}"}

    ctx.safety.consume_token(token)
    ctx.rollback.commit(txn.id)

    await ctx.audit.log(
        tool="backup_revert",
        details={"backup_id": entry.id, "dashboard_id": did},
        token_id=token,
    )

    return {
        "success": True,
        "dashboard_id": did,
        "restored_from": str(backup_path),
        "transaction_id": txn.id,
    }


@registry.tool(
    name="haops_backup_prune",
    description=(
        "Prune persistent backups — either per the configured retention "
        "policy (max_age_days, max_per_type) or with a one-shot override. "
        "Two-phase: call without confirm to preview a dry-run; call with "
        "confirm=true and the returned token to actually delete. "
        "Parameters (all optional on the preview call): "
        "older_than_days (int — override backup.max_age_days for this call), "
        "type (string: 'config' | 'dashboard' | 'entity' | 'db' | 'all' — "
        "default 'all'), "
        "clear_all (bool — WIPE EVERY backup in scope; use with a type "
        "filter unless you really mean everything), "
        "confirm (bool, default false), "
        "token (string, required if confirm=true). "
        "Preview response: {would_delete: [backup entries], count, "
        "bytes_freed, token}. Apply response: {success, deleted_count, "
        "bytes_freed}. When called without older_than_days and with "
        "clear_all=False, uses the same policy BackupManager runs "
        "automatically on every backup write — useful for seeing what the "
        "retention pass is removing."
    ),
    params={
        "older_than_days": {
            "type": "integer",
            "description": "Override backup.max_age_days for this call",
        },
        "type": {
            "type": "string",
            "description": "Type filter: config|dashboard|entity|db|all",
            "default": "all",
        },
        "clear_all": {
            "type": "boolean",
            "description": "Wipe every backup in scope (dangerous)",
            "default": False,
        },
        "confirm": {
            "type": "boolean",
            "description": "Execute the prune (otherwise preview only)",
            "default": False,
        },
        "token": {
            "type": "string",
            "description": "Confirmation token from preview step",
        },
    },
)
async def haops_backup_prune(
    ctx: HaOpsContext,
    older_than_days: int | None = None,
    type: str = "all",
    clear_all: bool = False,
    confirm: bool = False,
    token: str | None = None,
) -> dict[str, Any]:
    if not confirm:
        # Preview — dry-run shows exactly what the apply call will delete.
        preview = await ctx.backup.prune(
            dry_run=True,
            older_than_days=older_than_days,
            type_filter=type,
            clear_all=clear_all,
        )
        if preview["count"] == 0:
            return {
                "would_delete": [],
                "count": 0,
                "bytes_freed": 0,
                "message": (
                    "No backups match the retention criteria; nothing to prune."
                ),
            }
        tk = ctx.safety.create_token(
            action="backup_prune",
            details={
                "older_than_days": older_than_days,
                "type": type,
                "clear_all": clear_all,
            },
        )
        return {
            "would_delete": preview["would_delete"],
            "count": preview["count"],
            "bytes_freed": preview["bytes_freed"],
            "token": tk.id,
            "message": (
                f"Would delete {preview['count']} backup(s), freeing "
                f"{preview['bytes_freed']:,} bytes. Call again with "
                "confirm=true and this token to apply."
            ),
        }

    if token is None:
        return {"error": "confirm=true requires a token"}
    try:
        token_data = ctx.safety.validate_token(token)
    except Exception as e:
        return {"error": str(e)}

    # Use the params stored in the token — not re-passed kwargs — so the
    # apply executes exactly what the preview showed, even if the caller
    # messes up the second call.
    d = token_data.details
    result = await ctx.backup.prune(
        dry_run=False,
        older_than_days=d.get("older_than_days"),
        type_filter=d.get("type", "all"),
        clear_all=d.get("clear_all", False),
    )
    ctx.safety.consume_token(token)

    await ctx.audit.log(
        tool="backup_prune",
        details={
            "older_than_days": d.get("older_than_days"),
            "type": d.get("type", "all"),
            "clear_all": d.get("clear_all", False),
            "deleted_count": result["count"],
            "bytes_freed": result["bytes_freed"],
            # Only compact metadata per entry — full backup_path would
            # inflate the audit log; callers who want detail have the
            # preview response or the prune return value itself.
            "deleted": [
                {"id": e["id"], "source": e["source"], "type": e["type"]}
                for e in result["deleted"]
            ],
        },
        token_id=token,
    )

    return {
        "success": True,
        "deleted_count": result["count"],
        "bytes_freed": result["bytes_freed"],
    }
