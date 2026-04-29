"""Batch tools — atomic multi-surface mutations.

Provides one preview+apply pair that composes over the existing single-item
mutations (haops_config_patch, haops_config_create, haops_dashboard_patch).
The motivation (_gaps/session_gaps_2026-04-16.md §13): a cross-file rename
like `climate.esphome_livingroom_ac_2 → ..._ac` across automations.yaml,
scripts.yaml, and a dashboard is today three separate token round-trips
with no rollback if step 2 or 3 fails. Partial application silently breaks
HA config — the exact failure the preview→apply flow exists to prevent.

Atomicity model: on-disk best-effort. We back up every target before any
write, write serially, and on failure restore each already-written target
from its backup in reverse order. We do NOT try to roll back HA side
effects (automations that fired mid-batch stay fired) — same caveat as the
single-item flow.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import TYPE_CHECKING, Any

import jsonpatch
from ruamel.yaml import YAML

from ha_ops_mcp.safety.rollback import UndoEntry, UndoType
from ha_ops_mcp.server import registry
from ha_ops_mcp.tools.config import _canonicalise_yaml
from ha_ops_mcp.tools.dashboard import _get_dashboard_config
from ha_ops_mcp.utils.diff import (
    PatchApplyError,
    apply_patch,
    format_json_diff,
    json_diff,
    render_diff,
    unified_diff,
)
from ha_ops_mcp.utils.yaml import write_yaml

if TYPE_CHECKING:
    from ha_ops_mcp.server import HaOpsContext


# Combined diff exceeds this → spill to tool-results dir like
# haops_config_patch. Lets large batch previews stay reviewable without
# blowing up the MCP response payload.
LARGE_BATCH_DIFF_BYTES = 50 * 1024

SUPPORTED_TOOLS: frozenset[str] = frozenset({
    "config_patch",
    "config_create",
    "dashboard_patch",
})


def _validate_item_shape(item: dict[str, Any]) -> str | None:
    """Return an error string if the item is malformed, else None."""
    if not isinstance(item, dict):
        return "each item must be an object"
    tool = item.get("tool")
    if tool not in SUPPORTED_TOOLS:
        return (
            f"unknown or missing tool: {tool!r}. "
            f"Supported: {sorted(SUPPORTED_TOOLS)}"
        )
    if tool in ("config_patch", "config_create") and not item.get("path"):
        return f"{tool} item requires 'path'"
    if tool == "config_patch" and "patch" not in item:
        return "config_patch item requires 'patch'"
    if tool == "config_create" and "content" not in item:
        return "config_create item requires 'content'"
    if tool == "dashboard_patch":
        if not item.get("dashboard_id"):
            return "dashboard_patch item requires 'dashboard_id'"
        if "patch" not in item or not isinstance(item["patch"], list):
            return "dashboard_patch item requires 'patch' (JSON Patch array)"
    return None


async def _prepare_config_patch(
    ctx: HaOpsContext, item: dict[str, Any]
) -> dict[str, Any]:
    """Validate + compute new content for a config_patch batch item.

    Mirrors haops_config_patch's preview logic but returns a structured
    result ready to be embedded in the batch token. On error, returns a
    dict with an "error" key and no new content — the caller must refuse
    to issue the batch token.
    """
    path = item["path"]
    patch = item["patch"]
    resolved = ctx.path_guard.validate(path)

    if not resolved.exists():
        return {
            "error": f"File not found: {path}",
            "hint": "Use config_create for new files.",
        }

    old_content = resolved.read_text()
    try:
        new_content = apply_patch(old_content, patch)
    except PatchApplyError as e:
        return {"error": f"Patch for {path} does not apply cleanly: {e}"}

    is_yaml = resolved.suffix in (".yaml", ".yml")
    if is_yaml:
        yaml = YAML()
        try:
            yaml.load(io.StringIO(new_content))
        except Exception as e:
            return {"error": f"Patch for {path} produced invalid YAML: {e}"}

    diff_old = old_content
    diff_new = new_content
    if is_yaml:
        co = _canonicalise_yaml(old_content)
        cn = _canonicalise_yaml(new_content)
        if co is not None and cn is not None:
            diff_old = co
            diff_new = cn

    diff = unified_diff(diff_old, diff_new, path)
    return {
        "tool": "config_patch",
        "target": str(resolved),
        "path": str(resolved),
        "old_content": old_content,
        "new_content": new_content,
        "diff": diff,
    }


async def _prepare_config_create(
    ctx: HaOpsContext, item: dict[str, Any]
) -> dict[str, Any]:
    path = item["path"]
    content = item["content"]
    resolved = ctx.path_guard.validate(path)

    if resolved.exists():
        return {
            "error": (
                f"File already exists: {path}. "
                "config_create is for new files only — use config_patch to edit."
            ),
        }

    if resolved.suffix in (".yaml", ".yml"):
        yaml = YAML()
        try:
            yaml.load(io.StringIO(content))
        except Exception as e:
            return {"error": f"Invalid YAML for {path}: {e}"}

    diff = unified_diff("", content, path)
    return {
        "tool": "config_create",
        "target": str(resolved),
        "path": str(resolved),
        "old_content": "",
        "new_content": content,
        "diff": diff,
    }


async def _prepare_dashboard_patch(
    ctx: HaOpsContext, item: dict[str, Any]
) -> dict[str, Any]:
    dashboard_id = item["dashboard_id"]
    patch = item["patch"]

    old_config = await _get_dashboard_config(ctx, dashboard_id)
    if old_config is None:
        return {"error": f"Dashboard not found: {dashboard_id}"}

    try:
        patch_obj = jsonpatch.JsonPatch(patch)
    except (jsonpatch.InvalidJsonPatch, TypeError) as e:
        return {"error": f"Invalid JSON Patch for {dashboard_id}: {e}"}

    try:
        new_config = patch_obj.apply(old_config)
    except (
        jsonpatch.JsonPatchConflict,
        jsonpatch.JsonPatchTestFailed,
        jsonpatch.JsonPointerException,
    ) as e:
        return {"error": f"Patch for {dashboard_id} does not apply: {e}"}

    if not isinstance(new_config, dict):
        return {
            "error": (
                f"Patch for {dashboard_id} produced a non-object result — "
                "root must remain a dashboard config object."
            ),
        }

    jd = json_diff(old_config, new_config)
    diff_text = format_json_diff(jd) if jd else ""
    return {
        "tool": "dashboard_patch",
        "target": dashboard_id,
        "dashboard_id": dashboard_id,
        "old_config": old_config,
        "new_config": new_config,
        "diff": diff_text,
    }


PREPARE_DISPATCH = {
    "config_patch": _prepare_config_patch,
    "config_create": _prepare_config_create,
    "dashboard_patch": _prepare_dashboard_patch,
}


@registry.tool(
    name="haops_batch_preview",
    description=(
        "Preview a multi-target batch of mutations as ONE atomic unit. "
        "Use this when a single logical change (e.g. an entity rename) "
        "touches multiple files or a mix of config files + a dashboard — "
        "today's single-item tools give you N separate approval modals with "
        "no rollback if step K fails, leaving HA in a half-renamed state. "
        "Same two-phase contract as the single-item tools: returns a combined "
        "diff + one confirmation token; call haops_batch_apply with the "
        "token to commit atomically. "
        "Parameters: items (list, required) — each item is an object of one "
        "of these shapes: "
        "{tool: 'config_patch', path: '...', patch: '<unified diff>'} | "
        "{tool: 'config_create', path: '...', content: '<full content>'} | "
        "{tool: 'dashboard_patch', dashboard_id: '...', patch: [<JSON Patch ops>]}. "
        "Validation runs per-item (path resolution, patch application, YAML "
        "check) — any single failure aborts the whole preview and no token "
        "is issued. "
        "Response fields: token (batch token), targets (list of per-item "
        "{tool, target, diff, diff_rendered, size_bytes}), "
        "combined_diff_rendered (one markdown string with per-target "
        "headers + fenced diffs), total_size_bytes, warnings. "
        "Large combined diffs spill to an auxiliary diff_file on disk."
    ),
    params={
        "items": {
            "type": "array",
            "description": (
                "List of mutation items. See tool description for per-tool "
                "shape. Empty list or unknown tool values are rejected."
            ),
            "items": {"type": "object"},
        },
    },
)
async def haops_batch_preview(
    ctx: HaOpsContext, items: list[dict[str, Any]]
) -> dict[str, Any]:
    if not isinstance(items, list) or not items:
        return {
            "error": (
                "items must be a non-empty list. Each element describes one "
                "mutation (config_patch, config_create, or dashboard_patch)."
            ),
        }

    # Shape validation first — cheap, and gives the LLM a single clear
    # error rather than a misleading downstream failure.
    for idx, item in enumerate(items):
        err = _validate_item_shape(item)
        if err:
            return {"error": f"items[{idx}]: {err}"}

    prepared: list[dict[str, Any]] = []
    for idx, item in enumerate(items):
        tool = item["tool"]
        result = await PREPARE_DISPATCH[tool](ctx, item)
        if "error" in result:
            return {
                "error": f"items[{idx}] ({tool}): {result['error']}",
                "hint": result.get("hint"),
            }
        prepared.append(result)

    # Build combined rendered diff: one markdown document with per-target
    # headers followed by that target's fenced diff. This is the artefact
    # the human reviewer sees at approval time — their one chance to catch
    # a cross-file inconsistency before the whole batch lands.
    sections: list[str] = []
    targets_summary: list[dict[str, Any]] = []
    warnings: list[str] = []

    for p in prepared:
        diff_text = p["diff"] or "(no textual diff)"
        size = len(diff_text)
        language = "diff" if p["tool"] != "dashboard_patch" else "diff"
        fenced = f"```{language}\n{diff_text}\n```"
        header = f"### {p['tool']}: {p['target']}"
        sections.append(f"{header}\n\n{fenced}")
        targets_summary.append({
            "tool": p["tool"],
            "target": p["target"],
            "diff": p["diff"],
            "diff_rendered": render_diff(p["diff"]) if p["diff"] else "",
            "size_bytes": size,
        })
        if not p["diff"]:
            warnings.append(f"{p['tool']} on {p['target']} produced no diff (no-op).")

    combined = "\n\n".join(sections)
    total_size = sum(t["size_bytes"] for t in targets_summary)

    # Token carries the full prepared payload so apply never re-reads the
    # source state. Keeps preview validation binding and avoids a TOCTOU
    # window between preview and apply.
    token = ctx.safety.create_token(
        action="batch_apply",
        details={"items": prepared},
    )

    response: dict[str, Any] = {
        "token": token.id,
        "targets": targets_summary,
        "combined_diff_rendered": combined,
        "total_size_bytes": total_size,
        "warnings": warnings,
        "message": (
            f"Review the {len(prepared)}-target diff above. "
            "Call haops_batch_apply with this token to apply atomically. "
            "On any mid-batch failure, already-applied targets are restored "
            "from backup; HA-side effects (automations that fire during "
            "application) are NOT rolled back."
        ),
    }

    # Large combined diff → spill to disk, same escape hatch as
    # haops_config_patch. Inline fields stay populated for programmatic
    # consumers.
    if total_size > LARGE_BATCH_DIFF_BYTES:
        diff_path = ctx.audit.tool_results_dir() / f"batch-{token.id[:12]}.patch"
        try:
            diff_path.write_text(combined)
            response["diff_file"] = str(diff_path)
        except OSError:
            pass

    return response


async def _write_config(path: Path, content: str) -> None:
    """Write a YAML or plain-text config file.

    Mirrors the write branch of haops_config_apply: YAML files round-trip
    through ruamel so the on-disk form matches what HA itself emits (keeps
    subsequent diffs clean via canonicalise-before-diff).
    """
    if path.suffix in (".yaml", ".yml"):
        yaml = YAML()
        yaml.preserve_quotes = True
        data = yaml.load(io.StringIO(content))
        write_yaml(path, data, yaml)
    else:
        path.write_text(content)


async def _rollback_item(
    ctx: HaOpsContext, executed: dict[str, Any]
) -> dict[str, Any]:
    """Restore a single already-applied item from its backup (or delete if created).

    Called in reverse order during a mid-batch failure. Returns a dict
    describing what was restored so the caller can surface it in the
    failure response.
    """
    tool = executed["tool"]
    target = executed["target"]

    if tool == "config_create":
        # Nothing to restore — just remove the newly-created file.
        p = Path(executed["path"])
        try:
            p.unlink(missing_ok=True)
            return {"target": target, "restored_from": "deleted (was newly created)"}
        except OSError as e:
            return {"target": target, "restore_failed": str(e)}

    if tool == "config_patch":
        backup_path = executed.get("backup_path")
        if not backup_path:
            # No backup recorded — write the original content from the token.
            try:
                await _write_config(
                    Path(executed["path"]), executed["old_content"]
                )
                return {"target": target, "restored_from": "in-memory old_content"}
            except OSError as e:
                return {"target": target, "restore_failed": str(e)}
        try:
            dest = Path(executed["path"])
            src = Path(backup_path).read_text()
            dest.write_text(src)
            return {"target": target, "restored_from": backup_path}
        except OSError as e:
            return {"target": target, "restore_failed": str(e)}

    if tool == "dashboard_patch":
        # Dashboards rollback via WS using the in-memory old_config; the
        # .json backup is kept as an independent durable record, but the
        # faster rollback path is the already-loaded config object.
        from ha_ops_mcp.connections.websocket import WebSocketError
        try:
            kwargs: dict[str, Any] = {"config": executed["old_config"]}
            if executed["dashboard_id"] != "lovelace":
                kwargs["url_path"] = executed["dashboard_id"]
            await ctx.ws.send_command("lovelace/config/save", **kwargs)
            return {"target": target, "restored_from": "WS old_config"}
        except WebSocketError as e:
            return {"target": target, "restore_failed": str(e)}

    return {"target": target, "restore_failed": f"unknown tool: {tool}"}


@registry.tool(
    name="haops_batch_apply",
    description=(
        "Apply a previously previewed batch atomically. Requires a "
        "confirmation token from haops_batch_preview. Writes each target "
        "serially; on ANY failure mid-batch, already-written targets are "
        "restored from their backups (in reverse order) and the token is "
        "consumed as failed. "
        "Parameters: token (string, required). "
        "Response on success: {success: true, transaction_id, results: "
        "[per-target {tool, target, backup_path}]}. The transaction_id "
        "can be passed to haops_rollback to undo the whole batch in one "
        "call (in-memory, no drift). "
        "Response on failure: {success: false, failed_at: {tool, target, "
        "error}, rolled_back: [...], still_dirty: []}. "
        "NOTE: on-disk rollback only — HA side effects (automations that "
        "fired during partial application) are not rolled back. "
        "This is a MUTATING operation."
    ),
    params={
        "token": {"type": "string", "description": "Batch token from haops_batch_preview"},
    },
)
async def haops_batch_apply(
    ctx: HaOpsContext, token: str
) -> dict[str, Any]:
    try:
        token_data = ctx.safety.validate_token(token)
    except Exception as e:
        return {"error": str(e)}

    if token_data.action != "batch_apply":
        return {
            "error": (
                f"Token action mismatch: expected 'batch_apply', "
                f"got {token_data.action!r}. Did you pass a single-item "
                "token? Use the matching single-item apply tool."
            ),
        }

    items: list[dict[str, Any]] = token_data.details["items"]
    executed: list[dict[str, Any]] = []

    # Rollback transaction covering every item in the batch. Each successful
    # item adds a savepoint carrying its pre-write state, so haops_rollback
    # can undo the whole batch in one call without re-reading backup files
    # (avoids the backup-revert drift problem — see _gaps §).
    txn = ctx.rollback.begin("batch_apply", token_id=token)

    for item in items:
        tool = item["tool"]
        try:
            if tool == "config_patch":
                backup_entry = None
                if ctx.config.safety.backup_on_write:
                    backup_entry = await ctx.backup.backup_file(
                        Path(item["path"]), operation="batch_apply"
                    )
                await _write_config(Path(item["path"]), item["new_content"])
                txn.savepoint(
                    name=f"write:{Path(item['path']).name}",
                    undo=UndoEntry(
                        type=UndoType.FILE,
                        description=f"Revert {Path(item['path']).name}",
                        data={
                            "path": item["path"],
                            "content": item["old_content"],
                            "was_created": False,
                        },
                    ),
                )
                executed.append({
                    **item,
                    "backup_path": backup_entry.backup_path if backup_entry else None,
                })

            elif tool == "config_create":
                # No backup — the file didn't exist. Rollback deletes.
                await _write_config(Path(item["path"]), item["new_content"])
                txn.savepoint(
                    name=f"create:{Path(item['path']).name}",
                    undo=UndoEntry(
                        type=UndoType.FILE,
                        description=f"Delete newly-created {Path(item['path']).name}",
                        data={
                            "path": item["path"],
                            "content": "",
                            "was_created": True,
                        },
                    ),
                )
                executed.append({**item, "backup_path": None})

            elif tool == "dashboard_patch":
                backup_entry = None
                if ctx.config.safety.backup_on_write and item["old_config"]:
                    backup_entry = await ctx.backup.backup_dashboard(
                        item["dashboard_id"],
                        item["old_config"],
                        operation="batch_apply",
                    )
                from ha_ops_mcp.connections.websocket import WebSocketError
                try:
                    kwargs: dict[str, Any] = {"config": item["new_config"]}
                    if item["dashboard_id"] != "lovelace":
                        kwargs["url_path"] = item["dashboard_id"]
                    await ctx.ws.send_command("lovelace/config/save", **kwargs)
                except WebSocketError as e:
                    raise RuntimeError(f"dashboard save failed: {e}") from e
                txn.savepoint(
                    name=f"dashboard:{item['dashboard_id']}",
                    undo=UndoEntry(
                        type=UndoType.DASHBOARD,
                        description=f"Revert dashboard '{item['dashboard_id']}'",
                        data={
                            "dashboard_id": item["dashboard_id"],
                            "config": item["old_config"],
                        },
                    ),
                )
                executed.append({
                    **item,
                    "backup_path": backup_entry.backup_path if backup_entry else None,
                })

        except Exception as e:
            # Mid-batch failure — roll back everything already executed from
            # backups (synchronous, in-line). The rollback transaction itself
            # is discarded; we don't want haops_rollback to also try to undo
            # what _rollback_item already restored.
            rolled_back: list[dict[str, Any]] = []
            for prior in reversed(executed):
                rolled_back.append(await _rollback_item(ctx, prior))

            ctx.rollback.discard(txn.id)

            # Consume token regardless — single-use semantics. The failed
            # token must not be replayable.
            ctx.safety.consume_token(token)

            await ctx.audit.log(
                tool="batch_apply",
                details={
                    "items": items,
                    "failed_at": {"tool": tool, "target": item.get(
                        "path", item.get("dashboard_id")
                    ), "error": str(e)},
                    "rolled_back": rolled_back,
                },
                success=False,
                token_id=token,
                error=str(e),
            )

            return {
                "success": False,
                "failed_at": {
                    "tool": tool,
                    "target": item.get("path", item.get("dashboard_id")),
                    "error": str(e),
                },
                "rolled_back": rolled_back,
                "still_dirty": [
                    r for r in rolled_back if "restore_failed" in r
                ],
                "message": (
                    "Batch failed; already-applied targets restored from "
                    "backup. HA side effects (if any) are NOT rolled back."
                ),
            }

    # Success path — one audit entry for the whole batch, carrying each
    # per-item old/new so Phase 3's Timeline can render per-target diffs.
    ctx.safety.consume_token(token)
    ctx.rollback.commit(txn.id)
    await ctx.audit.log(
        tool="batch_apply",
        details={"items": executed, "transaction_id": txn.id},
        token_id=token,
    )

    results = [
        {
            "tool": e["tool"],
            "target": e["target"],
            "backup_path": e.get("backup_path"),
        }
        for e in executed
    ]

    return {
        "success": True,
        "transaction_id": txn.id,
        "results": results,
        "message": (
            f"Batch applied atomically: {len(results)} target(s). "
            f"Pass transaction_id={txn.id!r} to haops_rollback to undo."
        ),
    }
