"""Audit log for all confirmed mutations."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class AuditLog:
    """Append-only JSONL audit log for mutating operations.

    Every confirmed mutation gets logged with timestamp, tool name,
    parameters, result, and backup reference.
    """

    def __init__(self, audit_dir: Path) -> None:
        # resolve() canonicalises any `..` segments so subsequent openat()
        # syscalls don't depend on intermediate directories still existing.
        self._dir = Path(audit_dir).resolve()
        self._dir.mkdir(parents=True, exist_ok=True)
        self._log_path = self._dir / "operations.jsonl"

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
    ) -> None:
        """Append an audit entry.

        Args:
            tool: The tool name (e.g. "config_apply").
            details: Operation parameters/details.
            success: Whether the operation succeeded.
            backup_path: Path to backup file if one was created.
            token_id: The confirmation token that authorized this.
            error: Error message if the operation failed.
        """
        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "tool": tool,
            "details": details,
            "success": success,
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

    def clear(self) -> int:
        """Delete the audit log file. Returns the number of entries removed.

        Called by the sidebar "Clear audit log" button. The file is
        recreated on the next ``log()`` call — no manual init needed.
        """
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
            {timestamp, tool, details, success, backup_path?, token_id?, error?}.
        """
        if not self._log_path.exists():
            return []

        entries: list[dict[str, Any]] = []
        try:
            with open(self._log_path) as f:
                lines = f.readlines()
        except OSError as e:
            logger.warning("Could not read audit log: %s", e)
            return []

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
