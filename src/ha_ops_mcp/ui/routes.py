"""HTTP routes for the sidebar UI.

Mostly read-only. The one exception is `POST /api/ui/backup_prune` —
admin-convenience mutation for pruning persistent backups from the
Backups panel. Audit-logged with `source: "sidebar"` so the Timeline
distinguishes it from MCP-flow prunes. Any other mutation stays in the
MCP flow; the UI is a window into index + safety state.

Auth model:
    - Requests via HA Ingress (X-Ingress-Path header present) are trusted.
    - Otherwise require `Authorization: Bearer <token>` matching either the
      configured HA_OPS_TOKEN env var or the SUPERVISOR_TOKEN provided by
      the addon runtime.
    - Localhost loopback is also trusted (dev convenience).

Caching:
    - `/ui` responds with an ETag computed from the ui.html SHA-256 hash.
      Cache-Control: no-cache, must-revalidate — browsers always revalidate,
      server returns 304 if unchanged.
"""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from starlette.responses import JSONResponse, Response

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP
    from starlette.requests import Request

    from ha_ops_mcp.server import HaOpsContext

logger = logging.getLogger(__name__)


UI_HTML_PATH = Path(__file__).resolve().parent.parent / "static" / "ui.html"


# ── Auth ──────────────────────────────────────────────────────────────


def _is_authorized(request: Request) -> bool:
    """Gate UI routes. Ingress + loopback trusted; otherwise Bearer token."""
    # HA Ingress sets X-Ingress-Path; when present we trust the upstream.
    if request.headers.get("X-Ingress-Path"):
        return True
    client_host = (request.client.host if request.client else "") or ""
    if client_host in {"127.0.0.1", "::1", "localhost", "172.30.32.2"}:
        return True

    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return False
    provided = auth[len("Bearer "):].strip()
    expected = os.environ.get("HA_OPS_TOKEN") or os.environ.get("SUPERVISOR_TOKEN")
    return bool(expected) and provided == expected


def _unauthorized() -> Response:
    return JSONResponse({"error": "Unauthorized"}, status_code=401)


# ── ETag for the SPA ──────────────────────────────────────────────────


def _compute_ui_etag() -> str:
    """SHA-256 of ui.html, computed at server start (not per request).

    Any rebuild of the addon image shifts the hash, so browsers revalidate
    and pick up the new UI on next load.
    """
    if not UI_HTML_PATH.is_file():
        return '"no-ui"'
    h = hashlib.sha256(UI_HTML_PATH.read_bytes()).hexdigest()[:16]
    return f'"{h}"'


_UI_ETAG: str = _compute_ui_etag()
_UI_BODY: bytes | None = None


def _load_ui_body() -> bytes:
    """Cache the body so serving /ui is a memory read, not a disk read."""
    global _UI_BODY
    if _UI_BODY is None and UI_HTML_PATH.is_file():
        _UI_BODY = UI_HTML_PATH.read_bytes()
    return _UI_BODY or b""


# ── Registration ──────────────────────────────────────────────────────


def register_ui_routes(mcp: FastMCP, ctx: HaOpsContext) -> None:
    """Mount all UI routes onto the FastMCP server."""

    @mcp.custom_route("/ui", methods=["GET"])  # type: ignore[untyped-decorator]
    async def ui_html(request: Request) -> Response:
        if not _is_authorized(request):
            return _unauthorized()
        if not UI_HTML_PATH.is_file():
            return JSONResponse(
                {"error": "UI HTML not built yet — static/ui.html missing"},
                status_code=503,
            )
        # Conditional GET — honor If-None-Match
        if request.headers.get("If-None-Match") == _UI_ETAG:
            return Response(status_code=304, headers={"ETag": _UI_ETAG})
        body = _load_ui_body()
        return Response(
            content=body,
            media_type="text/html; charset=utf-8",
            headers={
                "ETag": _UI_ETAG,
                "Cache-Control": "no-cache, must-revalidate",
            },
        )

    @mcp.custom_route("/api/ui/self_check", methods=["GET"])  # type: ignore[untyped-decorator]
    async def api_self_check(request: Request) -> Response:
        if not _is_authorized(request):
            return _unauthorized()
        from ha_ops_mcp.tools.system import haops_self_check
        try:
            result = await haops_self_check(ctx)
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)
        return JSONResponse(result)

    @mcp.custom_route("/api/ui/tools_check", methods=["GET"])  # type: ignore[untyped-decorator]
    async def api_tools_check(request: Request) -> Response:
        if not _is_authorized(request):
            return _unauthorized()
        from ha_ops_mcp.tools.tools_check import haops_tools_check
        try:
            result = await haops_tools_check(ctx)
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)
        return JSONResponse(result)

    @mcp.custom_route("/api/ui/timeline", methods=["GET"])  # type: ignore[untyped-decorator]
    async def api_timeline(request: Request) -> Response:
        """Visual-inspection timeline of recent mutations.

        Returns audit entries augmented with a one-line summary and (for
        diff-capable tools) an inline unified/structured diff computed
        from the stored details. The Timeline itself is read-only —
        revert/re-apply buttons on entries route through the
        admin-convenience endpoints (`POST /api/ui/rollback`) which share
        the haops_rollback code path — see routes.py module docstring.
        Revert is NOT wired in this handler;
        see the parked plan for why this is an explicit non-goal.
        """
        if not _is_authorized(request):
            return _unauthorized()
        limit_raw = request.query_params.get("limit", "50")
        try:
            limit = max(int(limit_raw), 1)
        except ValueError:
            limit = 50
        offset_raw = request.query_params.get("offset", "0")
        try:
            offset = max(int(offset_raw), 0)
        except ValueError:
            offset = 0

        # Read one extra entry past the requested window so we can answer
        # has_more without a second pass. read_recent is tail-bounded
        # by line count; pulling offset+limit+1 stays cheap up to tens
        # of thousands of audit lines.
        audit = ctx.audit.read_recent(limit=offset + limit + 1)
        has_more = len(audit) > offset + limit
        prior_pages = audit[:offset]
        page = audit[offset:offset + limit]

        entries = [_render_audit_entry(e) for e in page]
        # Only the most recent successful apply gets a Revert button.
        # Older applies may have stale old_content — HA re-serializes
        # files on reload, or the user edits outside ha-ops — so
        # rolling them back would clobber later state. Strip
        # transaction_id from everything except the first qualifying
        # entry (entries are newest-first). With pagination, "first"
        # means first across the whole log: if a successful apply with
        # a transaction_id exists on a more-recent page, every apply on
        # this page is older and must be stripped.
        _revertable = {
            "haops_config_apply", "haops_dashboard_apply",
            "haops_batch_apply",
        }
        _revertable_raw = {
            "config_apply", "dashboard_apply", "batch_apply",
        }
        _seen_first = any(
            e.get("tool") in _revertable_raw
            and e.get("success", True)
            and isinstance((e.get("details") or {}).get("transaction_id"), str)
            for e in prior_pages
        )
        for e in entries:
            if e["tool"] in _revertable and e.get("transaction_id"):
                if _seen_first:
                    del e["transaction_id"]
                _seen_first = True
        # Pair apply ↔ rollback within the page only — pairs that
        # straddle a page boundary lose their cross-link, which is an
        # acceptable trade for keeping the index references local.
        _annotate_rollback_pairs(page, entries)
        return JSONResponse({
            "count": len(entries),
            "offset": offset,
            "limit": limit,
            "has_more": has_more,
            "entries": entries,
        })

    @mcp.custom_route("/api/ui/timeline/diff", methods=["GET"])  # type: ignore[untyped-decorator]
    async def api_timeline_diff(request: Request) -> Response:
        """Lazy-load a single timeline entry's diff.

        Query params:
            ts:   ISO timestamp of the audit entry (exact match required).
            tool: optional disambiguator if two entries share a timestamp;
                  accepts the bare audit tool name (``config_apply``) or
                  the display-prefixed form (``haops_config_apply``).

        Response shape:
            {diff_present: true, diff: str, diff_truncated: bool}
            {diff_present: false}              # entry has no diff surface
            {error: "..."} with 400/404 status on lookup failure.

        The list endpoint at ``GET /api/ui/timeline`` deliberately omits
        the diff body — for 50 entries × ~60 KB caps that would put MBs
        on the wire and re-shipping that on every 5 s poll. The frontend
        fetches diffs on-demand when the user expands a row, and caches
        the response on the entry so subsequent expand/collapse is free.
        """
        if not _is_authorized(request):
            return _unauthorized()

        ts = request.query_params.get("ts", "").strip()
        if not ts:
            return JSONResponse(
                {"error": "ts query param required"}, status_code=400
            )
        tool_filter_raw = request.query_params.get("tool", "").strip()
        # Accept both the bare audit form and the display-prefixed form;
        # callers will typically send what the list endpoint returned
        # (display-prefixed), so strip the prefix back to bare for the match.
        tool_filter = tool_filter_raw
        if tool_filter.startswith("haops_"):
            tool_filter = tool_filter[len("haops_"):]

        # Walk the recent audit window. 500 covers the normal "scroll
        # back through today" case without forcing us to load the whole
        # log; deeper history is a rare ask and would still work via
        # haops_timeline + raw audit reads.
        audit = ctx.audit.read_recent(limit=500)
        match: dict[str, Any] | None = None
        for raw in audit:
            if raw.get("timestamp") != ts:
                continue
            if tool_filter and raw.get("tool") != tool_filter:
                continue
            match = raw
            break

        if match is None:
            return JSONResponse(
                {"error": f"Audit entry not found for ts={ts!r}"},
                status_code=404,
            )

        tool = match.get("tool", "")
        details = match.get("details") or {}
        diff_text = _recompute_audit_diff(tool, details)
        if diff_text is None:
            return JSONResponse({"diff_present": False})
        if len(diff_text) > _TIMELINE_INLINE_DIFF_CAP:
            return JSONResponse({
                "diff_present": True,
                "diff_truncated": True,
                "diff": diff_text[:_TIMELINE_INLINE_DIFF_CAP],
            })
        return JSONResponse({
            "diff_present": True,
            "diff_truncated": False,
            "diff": diff_text,
        })

    @mcp.custom_route("/api/ui/backups", methods=["GET"])  # type: ignore[untyped-decorator]
    async def api_backups(request: Request) -> Response:
        """Summary of persistent backups — per-type counts/bytes, oldest/
        newest timestamps, effective retention settings, last prune entry.
        Read-only; no delete affordance. The admin hits haops_backup_prune
        via the MCP flow for any mutation.
        """
        if not _is_authorized(request):
            return _unauthorized()

        # A very high limit — list_backups is bounded by manifest size
        # (tens to low-hundreds of entries in normal use), so reading all
        # of them for a summary is cheap and avoids "N+more" footnotes.
        entries = await ctx.backup.list_backups(type_filter="all", limit=1_000_000)

        per_type: dict[str, dict[str, Any]] = {
            t: {"count": 0, "bytes": 0, "oldest_ts": None, "newest_ts": None}
            for t in ("config", "dashboard", "entity", "db")
        }
        total_count = 0
        total_bytes = 0
        for e in entries:
            bucket = per_type.get(e.type)
            if bucket is None:
                # Unknown backup type — shouldn't happen; skip so the
                # summary never crashes on schema drift.
                continue
            bucket["count"] += 1
            bucket["bytes"] += e.size_bytes
            if bucket["oldest_ts"] is None or e.timestamp < bucket["oldest_ts"]:
                bucket["oldest_ts"] = e.timestamp
            if bucket["newest_ts"] is None or e.timestamp > bucket["newest_ts"]:
                bucket["newest_ts"] = e.timestamp
            total_count += 1
            total_bytes += e.size_bytes

        # Walk recent audit entries for the most recent successful prune;
        # front-end shows "last prune" so the admin knows retention is
        # active (or hasn't run yet).
        last_prune: dict[str, Any] | None = None
        for audit_entry in ctx.audit.read_recent(limit=200):
            if (
                audit_entry.get("tool") == "backup_prune"
                and audit_entry.get("success", True)
            ):
                d = audit_entry.get("details") or {}
                last_prune = {
                    "ts": audit_entry.get("timestamp"),
                    "pruned_count": d.get("deleted_count", 0),
                    "bytes_freed": d.get("bytes_freed", 0),
                    "type": d.get("type", "all"),
                    "clear_all": bool(d.get("clear_all")),
                }
                break

        return JSONResponse({
            "summary": {
                "total_count": total_count,
                "total_bytes": total_bytes,
                "per_type": per_type,
            },
            "retention": {
                "max_age_days": ctx.config.backup.max_age_days,
                "max_per_type": ctx.config.backup.max_per_type,
            },
            "last_prune": last_prune,
            "backup_dir": ctx.config.backup.dir,
        })

    @mcp.custom_route("/api/ui/backup_prune", methods=["POST"])  # type: ignore[untyped-decorator]
    async def api_backup_prune(request: Request) -> Response:
        """Admin-convenience prune triggered from the Backups panel.

        Shape:
            POST body (JSON):
              {
                "execute": bool,               # default false → dry-run preview
                "older_than_days": int | null, # override, optional
                "type": str,                   # filter, default "all"
                "clear_all": bool,             # escape hatch, default false
              }
            Response shape mirrors BackupManager.prune — count, bytes_freed,
            plus `would_delete` on preview / `deleted` on execute. Execute
            path writes an audit entry with `source: "sidebar"` so Timeline
            can distinguish it from MCP-flow prunes.

        The UI does the two phases as two calls: first with execute=false
        to populate the confirm modal, then with execute=true on confirm.
        """
        if not _is_authorized(request):
            return _unauthorized()

        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            return JSONResponse({"error": "JSON body required"}, status_code=400)

        execute = bool(body.get("execute", False))
        older_than_days = body.get("older_than_days")
        if older_than_days is not None and not isinstance(older_than_days, int):
            return JSONResponse(
                {"error": "older_than_days must be an integer or null"},
                status_code=400,
            )
        type_filter = body.get("type", "all")
        if type_filter not in ("config", "dashboard", "entity", "db", "all"):
            return JSONResponse(
                {"error": f"Invalid type: {type_filter!r}"},
                status_code=400,
            )
        clear_all = bool(body.get("clear_all", False))

        result = await ctx.backup.prune(
            dry_run=not execute,
            older_than_days=older_than_days,
            type_filter=type_filter,
            clear_all=clear_all,
        )

        if execute:
            # Audit the admin action. Same tool name as the MCP-flow prune
            # so Timeline rendering is uniform; `source` field tells the
            # reader where it originated.
            await ctx.audit.log(
                tool="backup_prune",
                details={
                    "older_than_days": older_than_days,
                    "type": type_filter,
                    "clear_all": clear_all,
                    "deleted_count": result["count"],
                    "bytes_freed": result["bytes_freed"],
                    "deleted": [
                        {"id": e["id"], "source": e["source"], "type": e["type"]}
                        for e in result.get("deleted", [])
                    ],
                    "source": "sidebar",
                },
            )

        return JSONResponse(result)

    @mcp.custom_route("/api/ui/rollback", methods=["POST"])  # type: ignore[untyped-decorator]
    async def api_rollback(request: Request) -> Response:
        """Admin-convenience rollback from the Timeline Revert button.

        Shares the exact code path haops_rollback uses — _preview_undo
        for the preview phase, _execute_undo + `rollback_transaction`
        for the execute phase. No MCP token is issued (browser confirm
        is the second phase); audit entry carries `source: "sidebar"`
        so Timeline rendering distinguishes UI-triggered rollbacks.

        Body: {transaction_id: str, execute: bool}.
        Preview (execute=false): {transaction_id, operation, count,
        targets: [{type, target, action, diff}]}.
        Execute (execute=true): {success, restored, still_dirty}.
        """
        if not _is_authorized(request):
            return _unauthorized()

        # Lazy import — rollback.py depends on server.registry so we
        # can't pull it in at module load without a cycle.
        from ha_ops_mcp.tools.rollback import _execute_undo, _preview_undo

        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            return JSONResponse(
                {"error": "JSON body required"}, status_code=400
            )

        transaction_id = body.get("transaction_id")
        if not isinstance(transaction_id, str) or not transaction_id:
            return JSONResponse(
                {"error": "transaction_id (non-empty string) required"},
                status_code=400,
            )
        execute = bool(body.get("execute", False))

        txn = ctx.rollback.get_transaction(transaction_id)
        if txn is None:
            return JSONResponse(
                {
                    "error": (
                        f"Transaction {transaction_id!r} not found. The "
                        "rollback system is in-memory per session — addon "
                        "restarts clear it. For older changes use "
                        "haops_backup_revert."
                    ),
                },
                status_code=404,
            )
        if not txn.committed:
            return JSONResponse(
                {
                    "error": (
                        f"Transaction {transaction_id!r} is not committed."
                    ),
                },
                status_code=409,
            )
        active = txn.active_savepoints
        if not active:
            return JSONResponse(
                {
                    "error": (
                        f"Transaction {transaction_id!r} has no active "
                        "savepoints — already rolled back."
                    ),
                },
                status_code=409,
            )

        if not execute:
            preview_targets = []
            for sp in reversed(active):
                preview_targets.append(await _preview_undo(ctx, sp.undo))
            # Slim the preview payload before returning — diff_rendered
            # can be large and the browser just needs the summary fields.
            slim_targets = [
                {
                    "type": t.get("type"),
                    "target": t.get("target"),
                    "action": t.get("action"),
                    "description": t.get("description"),
                    "diff": t.get("diff"),
                }
                for t in preview_targets
            ]
            return JSONResponse({
                "transaction_id": transaction_id,
                "operation": txn.operation,
                "count": len(active),
                "targets": slim_targets,
            })

        # Execute path — mirror haops_rollback's apply branch.
        undos = ctx.rollback.rollback_transaction(transaction_id)
        restored: list[dict[str, Any]] = []
        for undo in undos:
            restored.append(await _execute_undo(ctx, undo))
        still_dirty = [r for r in restored if "restore_failed" in r]

        await ctx.audit.log(
            tool="rollback",
            details={
                "transaction_id": transaction_id,
                "operation": txn.operation,
                "restored": restored,
                "source": "sidebar",
            },
            success=not still_dirty,
        )

        return JSONResponse({
            "success": not still_dirty,
            "transaction_id": transaction_id,
            "operation": txn.operation,
            "restored": restored,
            "still_dirty": still_dirty,
        })

    @mcp.custom_route("/api/ui/audit_clear", methods=["POST"])  # type: ignore[untyped-decorator]
    async def api_audit_clear(request: Request) -> Response:
        """Clear the audit log (operations.jsonl). Wipes the Timeline.

        The audit log is append-only during normal operation; this is the
        one-shot admin escape hatch for a fresh start. The file is
        recreated automatically on the next mutation.
        """
        if not _is_authorized(request):
            return _unauthorized()
        count = ctx.audit.clear()
        return JSONResponse({
            "cleared": count,
            "message": f"Audit log cleared ({count} entries removed).",
        })


# ── Serialization helpers ─────────────────────────────────────────────


# Above this size (characters of the rendered diff string), the timeline
# entry points the reader at the persisted patch file instead of inlining
# the content. Same spirit as the LARGE_DIFF_BYTES threshold in
# haops_config_patch, but computed after rendering so the Alpine front-end
# doesn't choke on any single massive row.
_TIMELINE_INLINE_DIFF_CAP = 60 * 1024


# Audit log stores tool names without the `haops_` prefix for brevity,
# but the Timeline should match what the MCP client actually calls so
# the operator can grep one against the other without a mental mapping.
# Keep this list in sync with tool registrations; anything not here
# falls through as-is (safe default — the short form is still readable).
_HAOPS_PREFIXED_TOOLS: frozenset[str] = frozenset({
    "config_apply",
    "dashboard_apply",
    "batch_apply",
    "backup_revert",
    "backup_prune",
    "rollback",
    "entity_remove",
    "entity_disable",
    "entity_customize",
    "entities_assign_area",
    "integration_reload",
    "db_execute",
    "db_purge",
    "service_call",
    "system_reload",
    "system_restart",
    "system_backup",
    "addon_restart",
    "exec_shell",
})


def _annotate_rollback_pairs(
    audit: list[dict[str, Any]],
    entries: list[dict[str, Any]],
) -> None:
    """Link rollback rows to the apply rows they reversed (and vice versa).

    Mutates ``entries`` in place: each paired entry gains a ``paired_with``
    field with ``{index, timestamp, tool, relation}``. Relation is
    "rolled_back_by" on apply rows, "reverts" on rollback rows. Rows
    whose transaction is gone (rollback with no matching apply, or an
    apply whose rollback is outside the fetched window) get no
    annotation — frontend renders them as-is.

    Reads from the raw audit list so the un-prefixed tool names match
    the canonical dispatch; `entries` carries the display-prefixed form
    that the frontend sees.
    """
    apply_tools = {"config_apply", "dashboard_apply", "batch_apply"}
    pairs: dict[str, dict[str, int]] = {}
    for idx, raw in enumerate(audit):
        tool = raw.get("tool", "")
        details = raw.get("details") or {}
        txn_id = details.get("transaction_id")
        if not isinstance(txn_id, str) or not txn_id:
            continue
        if tool in apply_tools and raw.get("success", True):
            pairs.setdefault(txn_id, {})["apply"] = idx
        elif tool == "rollback":
            pairs.setdefault(txn_id, {})["rollback"] = idx

    for pair in pairs.values():
        apply_idx = pair.get("apply")
        rollback_idx = pair.get("rollback")
        if apply_idx is None or rollback_idx is None:
            # Lone apply (not yet rolled back) or lone rollback (apply
            # pre-dates this window / pre-v0.18.2 entries without
            # transaction_id). Leave un-paired.
            continue
        entries[apply_idx]["paired_with"] = {
            "index": rollback_idx,
            "timestamp": entries[rollback_idx]["timestamp"],
            "tool": entries[rollback_idx]["tool"],
            "relation": "rolled_back_by",
        }
        entries[rollback_idx]["paired_with"] = {
            "index": apply_idx,
            "timestamp": entries[apply_idx]["timestamp"],
            "tool": entries[apply_idx]["tool"],
            "relation": "reverts",
        }


def _display_tool_name(tool: str) -> str:
    """Prefix known audit tool names with `haops_` for Timeline display.

    The audit log stores bare action names (`config_apply`); the Timeline
    surface users know is `haops_config_apply`. This function bridges the
    two without changing anything in the audit log itself (programmatic
    consumers still get the short form from the raw log).
    """
    if tool in _HAOPS_PREFIXED_TOOLS:
        return f"haops_{tool}"
    return tool


def _sanitize_details(details: dict[str, Any]) -> dict[str, Any]:
    """Trim large content fields for UI display — full content is available
    through the MCP flow if needed, but the UI doesn't need to render it.
    """
    out = dict(details)
    for k in ("new_content", "old_content", "backup_content", "current_content"):
        if k in out and isinstance(out[k], str):
            s = out[k]
            if len(s) > 500:
                out[k] = s[:500] + f"... ({len(s)} chars total)"
    return out


def _render_audit_entry(
    entry: dict[str, Any], *, include_diff: bool = False
) -> dict[str, Any]:
    """Turn a raw audit log entry into a timeline-ready view.

    The audit log stores the full operation details (including large
    content fields) so rollback and forensics work. For the timeline,
    we recompute a compact, scannable representation:

    - ``summary`` — one-line human description of what happened.
    - ``diff_present`` — bool indicating whether a diff can be rendered.
      The diff body itself is NOT inlined in list responses (would balloon
      the payload to MBs for 50 entries × 60 KB caps); the frontend fetches
      it on row expand from ``GET /api/ui/timeline/diff``.
    - ``details_excerpt`` — compact structured data for tools without
      a diff (entity_remove, db_execute, exec_shell, etc.).
    - ``backup_path`` — plain-text path reference; the UI renders this
      as inspection-only copy, NOT as a clickable revert action.

    When ``include_diff=True`` the full diff is computed and inlined —
    used by the lazy-load endpoint, never by the list endpoint.

    Unknown tool names fall through with a generic summary and a
    truncated details excerpt so the timeline never silently drops
    events.
    """
    tool = entry.get("tool", "")
    details = entry.get("details") or {}
    success = bool(entry.get("success", True))

    out: dict[str, Any] = {
        "timestamp": entry.get("timestamp", ""),
        # Display-prefixed name so Timeline matches the MCP tool name the
        # client actually calls. The raw audit log keeps the bare form.
        "tool": _display_tool_name(tool),
        "success": success,
        "token_id": entry.get("token_id"),
        "backup_path": entry.get("backup_path"),
        "summary": _summarise_audit_entry(tool, details, success, entry.get("error")),
    }

    # Surface transaction_id for apply-type rows so the Timeline Revert
    # button has something to send to POST /api/ui/rollback. Absent when
    # the apply predates v0.17.0 (batch) / v0.18.2 (config + dashboard);
    # frontend hides the button in that case.
    if tool in ("config_apply", "dashboard_apply", "batch_apply") and success:
        txn_id = details.get("transaction_id")
        if isinstance(txn_id, str) and txn_id:
            out["transaction_id"] = txn_id

    if include_diff:
        diff_text = _recompute_audit_diff(tool, details)
        if diff_text is not None:
            out["diff_present"] = True
            if len(diff_text) > _TIMELINE_INLINE_DIFF_CAP:
                out["diff_truncated"] = True
                out["diff"] = diff_text[:_TIMELINE_INLINE_DIFF_CAP]
            else:
                out["diff_truncated"] = False
                out["diff"] = diff_text
        else:
            out["diff_present"] = False
    else:
        # Cheap presence check — skip the actual diff computation.
        out["diff_present"] = _has_diff_surface(tool, details)

    # Always attach a compact structured excerpt — useful for non-diff
    # tools and for cross-checking diff-capable tools when the diff
    # didn't render (e.g. legacy entries missing old_content).
    out["details_excerpt"] = _audit_details_excerpt(tool, details)

    if entry.get("error"):
        out["error"] = entry["error"]
    return out


def _has_diff_surface(tool: str, details: dict[str, Any]) -> bool:
    """Return True if this entry has the inputs needed to render a diff.

    Mirrors ``_recompute_audit_diff``'s presence checks without paying
    the unified-diff CPU + the JSON-serialisation cost. Used by the
    timeline list endpoint so the frontend knows whether to surface a
    "Loading diff…" affordance on row expand.
    """
    if tool == "config_apply":
        return isinstance(details.get("old_content"), str) and isinstance(
            details.get("new_content"), str
        )
    if tool == "dashboard_apply":
        return isinstance(details.get("old_config"), dict) and isinstance(
            details.get("new_config"), dict
        )
    if tool == "batch_apply":
        items = details.get("items") or []
        return any(
            isinstance(it, dict)
            and (
                (
                    it.get("tool") in ("config_patch", "config_create")
                    and isinstance(it.get("old_content"), str)
                    and isinstance(it.get("new_content"), str)
                )
                or (
                    it.get("tool") == "dashboard_patch"
                    and isinstance(it.get("old_config"), dict)
                    and isinstance(it.get("new_config"), dict)
                )
            )
            for it in items
        )
    if tool == "rollback":
        restored = details.get("restored") or []
        return any(
            isinstance(r, dict)
            and (
                ("old_content" in r and "new_content" in r)
                or ("old_config" in r and "new_config" in r)
            )
            for r in restored
        )
    if tool == "backup_revert":
        typ = details.get("type") or _infer_backup_type(details)
        if typ == "config":
            return isinstance(details.get("current_content"), str) and isinstance(
                details.get("backup_content"), str
            )
        if typ == "dashboard":
            return isinstance(details.get("current_config"), dict) and isinstance(
                details.get("backup_config"), dict
            )
        return False
    return False


def _summarise_audit_entry(
    tool: str, details: dict[str, Any], success: bool, error: str | None
) -> str:
    """One-line humanisation of what this audit entry represents."""
    if not success and error:
        return f"{tool} failed: {error[:80]}"

    if tool == "config_apply":
        path = details.get("path", "?")
        # config_create routes through config_apply with empty old_content;
        # surface the distinction in the timeline summary.
        if details.get("old_content") == "" and details.get("new_content"):
            return f"Created {path}"
        return f"Wrote {path}"
    if tool == "dashboard_apply":
        dashboard_id = details.get("dashboard_id", "?")
        return f"Updated dashboard {dashboard_id}"
    if tool == "batch_apply":
        items = details.get("items") or []
        by_tool: dict[str, int] = {}
        for it in items:
            t = it.get("tool", "?") if isinstance(it, dict) else "?"
            by_tool[t] = by_tool.get(t, 0) + 1
        if details.get("failed_at"):
            failed = details["failed_at"]
            rb = len(details.get("rolled_back", []))
            return (
                f"Batch FAILED at {failed.get('tool')} on "
                f"{failed.get('target')} — rolled back {rb} item(s)"
            )
        parts = [f"{n} {t}" for t, n in sorted(by_tool.items())]
        return f"Applied batch: {len(items)} target(s) ({', '.join(parts)})"
    if tool == "rollback":
        op = details.get("operation", "?")
        restored = details.get("restored") or []
        return f"Rolled back {op} ({len(restored)} target(s))"
    if tool == "backup_prune":
        count = details.get("deleted_count", 0)
        freed = details.get("bytes_freed", 0)
        size_str = (
            f"{freed / (1024 * 1024):.1f} MB" if freed >= 100 * 1024
            else f"{freed:,} bytes"
        )
        scope = details.get("type", "all")
        if details.get("clear_all"):
            return (
                f"Pruned {count} backup(s) [clear_all, type={scope}], "
                f"freed {size_str}"
            )
        return (
            f"Pruned {count} backup(s) (scope={scope}), freed {size_str}"
        )
    if tool == "backup_revert":
        typ = details.get("type") or _infer_backup_type(details)
        target = (
            details.get("source_path")
            or details.get("dashboard_id")
            or details.get("backup_id")
            or "?"
        )
        return f"Reverted {typ or 'backup'} to {target}"
    if tool == "entity_remove":
        ids = details.get("entity_ids") or []
        return f"Removed {len(ids)} entit{'y' if len(ids) == 1 else 'ies'}"
    if tool == "entity_disable":
        ids = details.get("entity_ids") or []
        return f"Disabled {len(ids)} entit{'y' if len(ids) == 1 else 'ies'}"
    if tool == "entity_customize":
        ids = details.get("entity_ids") or []
        return f"Customised {len(ids)} entit{'y' if len(ids) == 1 else 'ies'}"
    if tool == "entities_assign_area":
        area = details.get("area_id") or details.get("area") or "?"
        ids = details.get("entity_ids") or []
        return f"Assigned {len(ids)} entities to area {area}"
    if tool == "db_execute":
        sql = (details.get("sql") or "").strip().splitlines()
        first = sql[0] if sql else ""
        return f"Executed SQL: {first[:80]}"
    if tool == "exec_shell":
        cmd = (details.get("command") or "").strip()
        return f"Ran shell: {cmd[:80]}"
    if tool == "addon_restart":
        return f"Restarted addon {details.get('addon_slug', details.get('slug', '?'))}"
    if tool == "system_restart":
        return "Restarted Home Assistant"
    return f"{tool or 'unknown'} (no summary)"


def _recompute_audit_diff(tool: str, details: dict[str, Any]) -> str | None:
    """Recompute a displayable diff from stored operation details.

    Returns None when the tool has no diff surface (entity bulk ops, SQL
    execute, shell, system actions) OR when the entry is from an old
    version that didn't store the pieces needed. Callers fall back to
    ``details_excerpt`` in that case.
    """
    from ha_ops_mcp.utils.diff import (
        unified_diff,
        yaml_unified_diff,
    )

    if tool == "config_apply":
        old = details.get("old_content")
        new = details.get("new_content")
        if isinstance(old, str) and isinstance(new, str):
            path = details.get("path", "file")
            # path may be absolute — use basename for the diff header so the
            # rendering doesn't leak /config/... boilerplate.
            from pathlib import Path as _Path
            return unified_diff(old, new, filename=_Path(path).name or path)
        return None

    if tool == "dashboard_apply":
        old = details.get("old_config")
        new = details.get("new_config")
        if isinstance(old, dict) and isinstance(new, dict):
            did = details.get("dashboard_id", "dashboard")
            return yaml_unified_diff(old, new, label=did)
        return None

    if tool == "batch_apply":
        # Render each item's diff with its own header so the Timeline
        # entry for a batch looks like the preview did — one scrollable
        # document answering "what did this token change across all
        # targets?" Works for both success and failure entries; failure
        # entries include a leading header noting what failed.
        items = details.get("items") or []
        if not items:
            return None
        from pathlib import Path as _Path
        sections: list[str] = []
        if details.get("failed_at"):
            fa = details["failed_at"]
            sections.append(
                f"=== BATCH FAILED at {fa.get('tool')} on "
                f"{fa.get('target')}: {fa.get('error', '?')} ===\n"
            )
        for it in items:
            if not isinstance(it, dict):
                continue
            t = it.get("tool")
            if t in ("config_patch", "config_create"):
                old = it.get("old_content", "")
                new = it.get("new_content", "")
                if isinstance(old, str) and isinstance(new, str):
                    name = _Path(it.get("path", "file")).name or "file"
                    sections.append(
                        f"--- {t}: {name} ---\n"
                        f"{unified_diff(old, new, filename=name)}"
                    )
            elif t == "dashboard_patch":
                old_cfg = it.get("old_config")
                new_cfg = it.get("new_config")
                if isinstance(old_cfg, dict) and isinstance(new_cfg, dict):
                    did = it.get("dashboard_id", "?")
                    body = yaml_unified_diff(old_cfg, new_cfg, label=did)
                    sections.append(
                        f"--- dashboard_patch: {did} ---\n"
                        f"{body or '(no diff)'}"
                    )
        return "\n\n".join(sections) if sections else None

    if tool == "rollback":
        # haops_rollback writes old+new per target into details.restored[*]
        # at apply time (see tools/rollback.py::_execute_undo). Rebuild the
        # same per-target diff block the preview showed, so the Timeline
        # row answers "what did this rollback change?" in one expand.
        restored = details.get("restored") or []
        if not restored:
            return None
        from pathlib import Path as _Path
        rb_sections: list[str] = []
        for r in restored:
            if not isinstance(r, dict):
                continue
            target = r.get("target", "?")
            if "old_content" in r and "new_content" in r:
                name = _Path(target).name or target
                old = r.get("old_content", "")
                new = r.get("new_content", "")
                if isinstance(old, str) and isinstance(new, str):
                    diff = unified_diff(old, new, filename=name)
                    rb_sections.append(
                        f"--- rollback file: {name} ---\n"
                        f"{diff or '(no textual change)'}"
                    )
            elif "old_config" in r and "new_config" in r:
                old_cfg = r.get("old_config")
                new_cfg = r.get("new_config")
                if isinstance(old_cfg, dict) and isinstance(new_cfg, dict):
                    body = yaml_unified_diff(old_cfg, new_cfg, label=target)
                    rb_sections.append(
                        f"--- rollback dashboard: {target} ---\n"
                        f"{body or '(no diff)'}"
                    )
        return "\n\n".join(rb_sections) if rb_sections else None

    if tool == "backup_revert":
        typ = details.get("type") or _infer_backup_type(details)
        if typ == "config":
            current = details.get("current_content")
            backup = details.get("backup_content")
            if isinstance(current, str) and isinstance(backup, str):
                name = details.get("source_path") or "file"
                from pathlib import Path as _Path
                # Backup revert overwrites current with backup, so the diff
                # direction shows what the restore will change.
                return unified_diff(current, backup, filename=_Path(name).name or name)
        elif typ == "dashboard":
            current = details.get("current_config")
            backup = details.get("backup_config")
            if isinstance(current, dict) and isinstance(backup, dict):
                did = details.get("dashboard_id", "dashboard")
                # Backup revert overwrites current with backup, so the diff
                # direction shows what the restore will change.
                return yaml_unified_diff(current, backup, label=did)
        return None

    return None


def _infer_backup_type(details: dict[str, Any]) -> str | None:
    """backup_revert entries pre-date the explicit ``type`` field; infer."""
    if "source_path" in details or "backup_content" in details:
        return "config"
    if "dashboard_id" in details or "backup_config" in details:
        return "dashboard"
    if "table" in details:
        return "db"
    return None


def _audit_details_excerpt(tool: str, details: dict[str, Any]) -> dict[str, Any]:
    """Compact view of the operation params for tools without a diff.

    Large content fields are truncated; only the keys that actually
    describe what happened are kept. For diff-capable tools this is a
    small supplement (path + token_id preview); for non-diff tools it's
    the primary payload the timeline renders.
    """
    excerpt: dict[str, Any] = {}

    # rollback entries embed per-target old+new content. Show a compact
    # summary instead of dumping the content — the diff block already
    # renders that.
    if tool == "rollback":
        restored = details.get("restored") or []
        excerpt["transaction_id"] = details.get("transaction_id")
        excerpt["operation"] = details.get("operation")
        excerpt["restored"] = [
            {"target": r.get("target"), "action": r.get("action", "restore")}
            for r in restored if isinstance(r, dict)
        ][:20]
        still_dirty = [r for r in restored if isinstance(r, dict) and "restore_failed" in r]
        if still_dirty:
            excerpt["still_dirty_count"] = len(still_dirty)
        return excerpt

    # backup_prune entries embed a per-item `deleted` list; show it
    # compactly in the excerpt without the full source paths.
    if tool == "backup_prune":
        excerpt["older_than_days"] = details.get("older_than_days")
        excerpt["type"] = details.get("type", "all")
        excerpt["clear_all"] = bool(details.get("clear_all"))
        excerpt["deleted_count"] = details.get("deleted_count", 0)
        excerpt["bytes_freed"] = details.get("bytes_freed", 0)
        # Don't dump the full deleted list — it's already in the audit
        # entry for audit-log readers; the timeline row just needs the
        # totals.
        return excerpt

    # batch_apply items embed full content per target. Render a compact
    # summary with per-item target + tool, not the raw payload — the big
    # data lives in the recomputed diff block.
    if tool == "batch_apply":
        items = details.get("items") or []
        excerpt["item_count"] = len(items)
        excerpt["items"] = [
            {"tool": it.get("tool"), "target": it.get("target")}
            for it in items if isinstance(it, dict)
        ][:20]
        if details.get("failed_at"):
            excerpt["failed_at"] = details["failed_at"]
        if details.get("rolled_back"):
            excerpt["rolled_back_count"] = len(details["rolled_back"])
        return excerpt

    keep = {
        "config_apply": ["path"],
        "dashboard_apply": ["dashboard_id"],
        "backup_revert": ["type", "source_path", "dashboard_id", "backup_id"],
        "entity_remove": ["entity_ids"],
        "entity_disable": ["entity_ids"],
        "entity_customize": ["entity_ids", "customizations", "overrides"],
        "entities_assign_area": ["area_id", "area", "entity_ids"],
        "db_execute": ["sql", "rowcount"],
        "exec_shell": ["command", "cwd", "timeout"],
        "addon_restart": ["slug", "addon_slug"],
        "system_restart": [],
    }.get(tool, list(details.keys())[:6])

    for k in keep:
        if k not in details:
            continue
        v = details[k]
        if isinstance(v, str) and len(v) > 400:
            excerpt[k] = v[:400] + f"... ({len(v)} chars total)"
        elif isinstance(v, list) and len(v) > 20:
            excerpt[k] = list(v[:20]) + [f"... ({len(v)} items total)"]
        else:
            excerpt[k] = v
    return excerpt
