"""Rollback tool — undo a committed transaction by id.

Undo stack is kept in-memory by RollbackManager alongside every mutating
tool (config_apply, dashboard_apply, batch_apply, backup_revert). This
tool lets the caller undo any one of them in a single two-phase call,
without touching persistent backups. That matters because full-file
backup restore also reverts any HA-side drift that happened since the
backup was taken (see haops_backup_revert docs). In-memory rollback
replays the exact pre-write state, so drift is preserved.

Limits, by design:
- Transactions are ephemeral (MCP-session lifetime). Addon restart loses
  them. Persistent reverts go through haops_backup_revert.
- HA side effects (an automation that fired during apply) are NOT rolled
  back — same caveat as every other mutation here.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from ha_ops_mcp.safety.rollback import UndoEntry, UndoType
from ha_ops_mcp.server import registry
from ha_ops_mcp.utils.diff import (
    render_diff,
    unified_diff,
    yaml_unified_diff,
)
from ha_ops_mcp.utils.yaml import write_yaml

if TYPE_CHECKING:
    from ha_ops_mcp.server import HaOpsContext


async def _read_current_file(path: Path) -> str:
    """Best-effort read of the current file content for the diff baseline.

    Empty string when the file is missing (e.g. if the user deleted it
    between the batch apply and the rollback — rare but possible). The
    diff will show the full restored content as pure additions.
    """
    try:
        return path.read_text()
    except OSError:
        return ""


async def _preview_undo(
    ctx: HaOpsContext, undo: UndoEntry
) -> dict[str, Any]:
    """Describe what a single rollback will do, including a real diff.

    The diff is between *current* state (not post-apply) and what the
    rollback will write, so the caller sees exactly what's about to
    happen — drift included.
    """
    if undo.type == UndoType.FILE:
        path_str = undo.data.get("path", "?")
        path = Path(path_str)
        name = path.name or path_str

        if undo.data.get("was_created"):
            # No textual diff — the file will be deleted.
            current = await _read_current_file(path)
            size = len(current)
            return {
                "type": "file",
                "target": path_str,
                "action": "delete",
                "description": undo.description,
                "diff": f"(will delete {name} — currently {size:,} bytes)",
                "diff_rendered": None,
            }

        current = await _read_current_file(path)
        target_content = undo.data.get("content", "")
        diff = unified_diff(current, target_content, name)
        return {
            "type": "file",
            "target": path_str,
            "action": "restore content",
            "description": undo.description,
            "diff": diff or "(no differences — target already matches savepoint)",
            "diff_rendered": render_diff(diff) if diff else None,
        }

    if undo.type == UndoType.DASHBOARD:
        from ha_ops_mcp.tools.dashboard import _get_dashboard_config

        did = undo.data.get("dashboard_id", "?")
        target_config = undo.data.get("config", {})
        current_config = await _get_dashboard_config(ctx, did) or {}
        # Real unified diff between current dashboard state and the savepoint
        # we're about to restore — line-marked output a markdown ``diff``
        # fence (or the sidebar's renderDiffHtml) actually colourises.
        # Replaces the prior format_json_diff blob (gap 2026-04-18 §4 + the
        # `~ replace ... 'old' -> 'new'` style that had no +/- markers).
        diff_text = yaml_unified_diff(
            current_config, target_config, label=f"dashboard:{did}"
        )
        return {
            "type": "dashboard",
            "target": did,
            "action": "restore config",
            "description": undo.description,
            "diff": diff_text or "(no differences — dashboard already matches savepoint)",
            "diff_rendered": render_diff(diff_text) if diff_text else None,
        }

    return {
        "type": undo.type.value,
        "target": undo.description,
        "action": "undo",
        "description": undo.description,
        "diff": "(no preview available for this undo type)",
        "diff_rendered": None,
    }


async def _execute_undo(
    ctx: HaOpsContext, undo: UndoEntry
) -> dict[str, Any]:
    """Execute a single UndoEntry, return a result dict that includes the
    before/after payload so the Timeline can recompute the diff later."""
    if undo.type == UndoType.FILE:
        path = Path(undo.data["path"])
        if undo.data.get("was_created"):
            pre = await _read_current_file(path)
            try:
                path.unlink(missing_ok=True)
                return {
                    "target": str(path),
                    "action": "delete",
                    "restored": "deleted (was newly created)",
                    "old_content": pre,
                    "new_content": "",
                }
            except OSError as e:
                return {"target": str(path), "restore_failed": str(e)}
        try:
            pre = await _read_current_file(path)
            content = undo.data["content"]
            if path.suffix in (".yaml", ".yml"):
                from io import StringIO

                from ruamel.yaml import YAML
                yaml = YAML()
                yaml.preserve_quotes = True
                data = yaml.load(StringIO(content)) if content else None
                if data is None:
                    path.write_text(content)
                else:
                    write_yaml(path, data, yaml)
            else:
                path.write_text(content)
            return {
                "target": str(path),
                "action": "restore",
                "restored": "content",
                "old_content": pre,
                "new_content": content,
            }
        except OSError as e:
            return {"target": str(path), "restore_failed": str(e)}

    if undo.type == UndoType.DASHBOARD:
        from ha_ops_mcp.connections.websocket import WebSocketError
        from ha_ops_mcp.tools.dashboard import _get_dashboard_config

        did = undo.data["dashboard_id"]
        try:
            pre_config = await _get_dashboard_config(ctx, did) or {}
            kwargs: dict[str, Any] = {"config": undo.data["config"]}
            if did != "lovelace":
                kwargs["url_path"] = did
            await ctx.ws.send_command("lovelace/config/save", **kwargs)
            return {
                "target": did,
                "action": "restore",
                "restored": "dashboard config",
                "old_config": pre_config,
                "new_config": undo.data["config"],
            }
        except WebSocketError as e:
            return {"target": did, "restore_failed": str(e)}

    return {
        "target": undo.description,
        "restore_failed": f"unsupported undo type: {undo.type.value}",
    }


@registry.tool(
    name="haops_rollback",
    description=(
        "Undo a recent mutation by transaction_id (two-phase). PREFERRED "
        "over haops_backup_revert for changes made in this session — replays "
        "the exact pre-write state captured in memory, so you don't pick up "
        "any HA-side drift that happened since. Use this to undo a "
        "haops_batch_apply / haops_config_apply / haops_dashboard_apply. "
        "Parameters: transaction_id (string, required), "
        "confirm (bool, default false), token (string, required if "
        "confirm=true). "
        "Preview response: {transaction_id, operation, targets: "
        "[per-target {type, target, action, diff, diff_rendered}], "
        "combined_diff_rendered, token}. The diff is computed against the "
        "CURRENT state of each target, so you see exactly what the rollback "
        "will change — HA-side drift included. "
        "Apply response: {success, restored: [per-target {target, action, "
        "restored|restore_failed}], still_dirty}. "
        "LIMITS: transactions are in-memory for the MCP-session lifetime "
        "only — an addon restart loses them. For older changes use "
        "haops_backup_revert. HA side effects fired during the original "
        "apply are NOT rolled back. "
        "Also accepts any committed transaction id, including the one "
        "haops_backup_revert returns — rolling back a revert re-applies "
        "the change that was reverted, if that's what you need. "
        "This is a MUTATING operation. "
        "REVIEW PROTOCOL — TWO non-negotiable parts: "
        "(1) RENDER, ALWAYS. After the preview call (confirm=false) you "
        "MUST paste `combined_diff_rendered` verbatim (entire markdown "
        "```diff fenced block, not a paraphrase) as your next chat "
        "message. The chat surface colourises +/- lines — this is the "
        "ONLY visual review the human gets of what's about to be "
        "restored, because Claude Code's tool-result panel only shows "
        "escaped JSON. Render every time, even for tiny rollbacks. "
        "(2) STOP for approval. After rendering, wait for explicit user "
        "approval before calling again with confirm=true. EXCEPTION "
        "applies ONLY to the stop, NEVER to the render: if the user "
        "already explicitly asked for this specific rollback in the "
        "current turn (e.g. 'rollback transaction X' as a direct "
        "instruction), you may chain to confirm=true — but the diff "
        "render still happens first."
    ),
    params={
        "transaction_id": {
            "type": "string",
            "description": "ID returned by a previous mutating tool (e.g., haops_batch_apply)",
        },
        "confirm": {
            "type": "boolean",
            "description": "Execute the rollback",
            "default": False,
        },
        "token": {
            "type": "string",
            "description": "Confirmation token from the preview step",
        },
    },
)
async def haops_rollback(
    ctx: HaOpsContext,
    transaction_id: str,
    confirm: bool = False,
    token: str | None = None,
) -> dict[str, Any]:
    txn = ctx.rollback.get_transaction(transaction_id)
    if txn is None:
        return {
            "error": (
                f"Transaction '{transaction_id}' not found. The rollback "
                "system is in-memory per session, so addon restarts clear "
                "it — use haops_backup_revert for older changes."
            ),
        }

    if not txn.committed:
        return {
            "error": (
                f"Transaction '{transaction_id}' is not committed — it "
                "either failed mid-flight or is still in progress, so "
                "there's nothing to roll back."
            ),
        }

    active = txn.active_savepoints
    if not active:
        return {
            "error": (
                f"Transaction '{transaction_id}' has no active savepoints "
                "— already rolled back."
            ),
        }

    # Newest first — reverse of the order in which savepoints were added.
    preview_targets: list[dict[str, Any]] = []
    for sp in reversed(active):
        preview_targets.append(await _preview_undo(ctx, sp.undo))

    if not confirm:
        # Stitch per-target diffs into one markdown document, mirroring
        # haops_batch_preview's combined_diff_rendered shape. Makes the
        # approval modal self-sufficient — no follow-up read calls needed.
        sections: list[str] = []
        for t in preview_targets:
            header = f"### {t['type']}: {t['target']} — {t['action']}"
            if t.get("diff_rendered"):
                sections.append(f"{header}\n\n{t['diff_rendered']}")
            else:
                sections.append(f"{header}\n\n{t['diff']}")
        combined = "\n\n".join(sections)

        tk = ctx.safety.create_token(
            action="rollback",
            details={"transaction_id": transaction_id},
        )
        return {
            "transaction_id": transaction_id,
            "operation": txn.operation,
            "targets": preview_targets,
            "combined_diff_rendered": combined,
            "count": len(preview_targets),
            "token": tk.id,
            "message": (
                f"Review the {len(preview_targets)} target(s) above. "
                "Call again with confirm=true and this token to roll back. "
                "HA side effects fired during the original apply will NOT "
                "be un-fired."
            ),
        }

    if token is None:
        return {"error": "confirm=true requires a token"}

    try:
        token_data = ctx.safety.validate_token(token)
    except Exception as e:
        return {"error": str(e)}

    if token_data.details.get("transaction_id") != transaction_id:
        return {
            "error": (
                "Token was issued for a different transaction. Run the "
                "preview again to obtain a matching token."
            ),
        }

    # Mark all savepoints rolled back up front (returns the UndoEntry list
    # in reverse order). Execution below drives each.
    undos = ctx.rollback.rollback_transaction(transaction_id)

    restored: list[dict[str, Any]] = []
    for undo in undos:
        restored.append(await _execute_undo(ctx, undo))

    ctx.safety.consume_token(token)

    still_dirty = [r for r in restored if "restore_failed" in r]

    # Audit log keeps the full payload — old_/new_content + old_/new_config
    # — so the Timeline can recompute diffs after the fact.
    await ctx.audit.log(
        tool="rollback",
        details={
            "transaction_id": transaction_id,
            "operation": txn.operation,
            "restored": restored,
        },
        success=not still_dirty,
        token_id=token,
    )

    # Wire response strips the bulky pre/post payload — for a real
    # dashboard (15 views, ~150 cards) the embedded config was ~140 KB,
    # blowing past the MCP client's output cap and forcing the response to
    # disk (gap 2026-04-18 §4). Caller still gets target + action +
    # outcome; full pre/post state lives in the audit log.
    bulky_keys = {"old_content", "new_content", "old_config", "new_config"}
    slim_restored = [
        {k: v for k, v in r.items() if k not in bulky_keys}
        for r in restored
    ]

    return {
        "success": not still_dirty,
        "transaction_id": transaction_id,
        "operation": txn.operation,
        "restored": slim_restored,
        "still_dirty": [
            {k: v for k, v in r.items() if k not in bulky_keys}
            for r in still_dirty
        ],
        "message": (
            f"Rolled back {len(restored)} target(s)."
            if not still_dirty
            else f"Rolled back with {len(still_dirty)} failure(s); see still_dirty."
        ),
    }
