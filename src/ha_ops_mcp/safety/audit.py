"""Audit log for all confirmed mutations."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ha_ops_mcp.safety.classification import classify

logger = logging.getLogger(__name__)


def _resolve_class_area(
    tool: str,
    details: dict[str, Any],
    op_class: str | None,
    area: str | None,
) -> tuple[str, str]:
    """Fill in op_class/area from classify() when either is missing."""
    if op_class is not None and area is not None:
        return op_class, area
    oc, ar = classify(tool, details)
    return op_class or oc, area or ar


class AuditLog:
    """Append-only JSONL audit log for mutating operations.

    Every confirmed mutation gets logged with timestamp, tool name,
    parameters, result, and backup reference.
    """

    # Rotate activity.jsonl past this size, keeping one .1 backup. Reads
    # dominate call volume, so this file is bounded; operations.jsonl (low
    # volume, forensically valuable) stays unbounded.
    _ACTIVITY_MAX_BYTES = 5 * 1024 * 1024

    def __init__(self, audit_dir: Path) -> None:
        # resolve() canonicalises any `..` segments so subsequent openat()
        # syscalls don't depend on intermediate directories still existing.
        self._dir = Path(audit_dir).resolve()
        self._dir.mkdir(parents=True, exist_ok=True)
        self._log_path = self._dir / "operations.jsonl"
        # Read-only tool calls land in a separate, rotated stream so they
        # don't crowd mutations out of the tail-read window or bloat the
        # rollback/forensics log.
        self._activity_path = self._dir / "activity.jsonl"

    def tool_results_dir(self) -> Path:
        """Path for large tool-result artifacts (diffs, captured outputs).

        Tools that produce results too big to embed inline in an MCP response
        can persist them here and hand the caller a filesystem path. Lives
        next to the audit log so "what did that operation say?" and "what
        was the big diff it showed me?" stay colocated.
        """
        d = self._dir / "tool-results"
        d.mkdir(parents=True, exist_ok=True)
        return d

    async def log(
        self,
        tool: str,
        details: dict[str, Any],
        success: bool = True,
        backup_path: str | None = None,
        token_id: str | None = None,
        error: str | None = None,
        op_class: str | None = None,
        area: str | None = None,
    ) -> None:
        """Append an audit entry to the mutations log (operations.jsonl).

        Args:
            tool: The tool name (e.g. "config_apply").
            details: Operation parameters/details.
            success: Whether the operation succeeded.
            backup_path: Path to backup file if one was created.
            token_id: The confirmation token that authorized this.
            error: Error message if the operation failed.
            op_class: "read" | "mutate" | "destructive". Derived from
                ``classify(tool, details)`` when omitted, so existing call
                sites need no changes.
            area: subsystem touched. Derived alongside op_class when omitted.
        """
        op_class, area = _resolve_class_area(tool, details, op_class, area)
        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "tool": tool,
            "details": details,
            "success": success,
            "op_class": op_class,
            "area": area,
        }
        if backup_path:
            entry["backup_path"] = backup_path
        if token_id:
            entry["token_id"] = token_id
        if error:
            entry["error"] = error

        line = json.dumps(entry)
        with open(self._log_path, "a") as f:
            f.write(line + "\n")

        logger.debug("Audit: %s %s", tool, "OK" if success else "FAILED")

    async def log_activity(
        self,
        tool: str,
        details: dict[str, Any],
        op_class: str | None = None,
        area: str | None = None,
    ) -> None:
        """Append a read-only tool call to the activity stream.

        Separate from ``log()`` so high-volume reads stay out of the
        mutation log. Best-effort: a write failure here must never break
        the tool call that triggered it, so errors are swallowed.
        """
        op_class, area = _resolve_class_area(tool, details, op_class, area)
        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "tool": tool,
            "details": details,
            "success": True,
            "op_class": op_class,
            "area": area,
        }
        try:
            self._maybe_rotate()
            with open(self._activity_path, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError as e:
            logger.debug("Could not write activity entry: %s", e)

    def _maybe_rotate(self) -> None:
        """Rotate activity.jsonl to activity.1.jsonl past the size cap."""
        try:
            if self._activity_path.stat().st_size < self._ACTIVITY_MAX_BYTES:
                return
        except OSError:
            return
        backup = self._dir / "activity.1.jsonl"
        try:
            self._activity_path.replace(backup)  # atomic, overwrites old .1
        except OSError as e:
            logger.debug("Could not rotate activity log: %s", e)

    def clear(self) -> int:
        """Delete the audit log file. Returns the number of entries removed.

        Called by the sidebar "Clear audit log" button. The file is
        recreated on the next ``log()`` call — no manual init needed.
        """
        # Also drop the read-activity streams — "clear" means clear the
        # whole Timeline, not just the mutation half.
        for extra in (self._activity_path, self._dir / "activity.1.jsonl"):
            try:
                extra.unlink(missing_ok=True)
            except OSError as e:
                logger.debug("Could not clear %s: %s", extra.name, e)

        if not self._log_path.exists():
            return 0
        try:
            lines = self._log_path.read_text().splitlines()
            count = sum(1 for line in lines if line.strip())
            self._log_path.unlink()
            logger.info("Audit log cleared (%d entries)", count)
            return count
        except OSError as e:
            logger.warning("Could not clear audit log: %s", e)
            return 0

    def read_recent(self, limit: int = 50) -> list[dict[str, Any]]:
        """Read the last `limit` audit entries.

        The log is append-only JSONL; we read the tail. For small logs
        this walks the whole file; for large logs we still tail-read the
        last N lines which is fine up to hundreds of thousands of entries.
        Malformed lines are skipped with a debug log, not surfaced.

        Args:
            limit: Max entries to return.

        Returns:
            List of entries (newest first), each a dict with keys
            {timestamp, tool, details, success, op_class, area,
            backup_path?, token_id?, error?}.
        """
        return self._tail_read(self._log_path, limit)

    def read_recent_merged(self, limit: int = 50) -> list[dict[str, Any]]:
        """Read the last `limit` entries across mutations + activity streams.

        Merges operations.jsonl (mutations) and activity.jsonl (reads),
        sorted newest-first by timestamp. Used by the Timeline when the
        operator opts in to seeing read-only calls. Each stream is
        tail-bounded independently, so this stays cheap even when the
        activity log is large.
        """
        mutations = self._tail_read(self._log_path, limit)
        reads = self._tail_read(self._activity_path, limit)
        merged = mutations + reads
        # Newest-first by ISO timestamp (lexicographic == chronological).
        merged.sort(key=lambda e: e.get("timestamp", ""), reverse=True)
        return merged[:limit]

    def _tail_read(self, path: Path, limit: int) -> list[dict[str, Any]]:
        """Tail-read a JSONL stream, newest-first, skipping malformed lines."""
        if not path.exists():
            return []
        try:
            with open(path) as f:
                lines = f.readlines()
        except OSError as e:
            logger.warning("Could not read %s: %s", path.name, e)
            return []

        entries: list[dict[str, Any]] = []
        # Tail: parse last 2*limit lines, filter valid JSON, take last `limit`.
        # The 2x margin is a cheap guard against recent malformed lines.
        for raw in lines[-max(limit * 2, limit):]:
            raw = raw.strip()
            if not raw:
                continue
            try:
                entries.append(json.loads(raw))
            except json.JSONDecodeError:
                logger.debug("Skipping malformed audit line")
                continue

        entries.reverse()  # newest first
        return entries[:limit]
