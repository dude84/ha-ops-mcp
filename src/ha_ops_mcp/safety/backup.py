"""Persistent backup manager for heavy/destructive operations.

This is for operations that warrant on-disk backups — DB purges, system-level
changes, explicit user request. Routine mutations use the ephemeral rollback
system (safety/rollback.py) instead.

Retention is enforced in-process: `BackupManager` prunes at startup and after
every write, dropping entries older than ``max_age_days`` or exceeding
``max_per_type``. The atomic manifest-rewrite path in `_prune_sync` is the
ONE place `manifest.jsonl` is rewritten — everywhere else it is strictly
append-only. Manual prune is exposed via `haops_backup_prune`.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# Historical default used before v0.18.0. If the configured backup_dir has
# been moved off this path but the legacy tree still contains data, we warn
# the admin at startup so it isn't silently orphaned.
_LEGACY_BACKUP_DIR = Path("/config/ha-ops-backups")


@dataclass
class BackupEntry:
    id: str
    timestamp: str
    type: str  # "config", "dashboard", "entity", "db"
    source: str
    backup_path: str
    operation: str
    size_bytes: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class BackupManager:
    """Manages persistent on-disk backups for destructive operations.

    Used for:
    - DB purges affecting many rows
    - System-level backups (explicit user request)
    - Operations where in-memory rollback is insufficient

    Routine config edits and small mutations use RollbackManager instead.
    """

    def __init__(
        self,
        backup_dir: Path,
        *,
        max_age_days: int = 30,
        max_per_type: int = 100,
    ) -> None:
        self._dir = backup_dir
        self._manifest = backup_dir / "manifest.jsonl"
        self._max_age_days = max_age_days
        self._max_per_type = max_per_type
        # Create directory structure
        for subdir in ("config", "dashboards", "entities", "db"):
            (backup_dir / subdir).mkdir(parents=True, exist_ok=True)
        # §6 — warn if data lives in the old default location after the
        # admin has moved backup_dir elsewhere. Purely informational; we
        # never touch files outside the configured dir.
        self._maybe_warn_legacy_dir()
        # Catch-up retention pass at startup. Non-fatal on failure — the
        # server should come up even if prune bombs on a malformed entry,
        # and we log enough to diagnose later.
        try:
            self._prune_sync()
        except Exception as exc:
            logger.warning("Startup retention pass failed: %s", exc)

    async def backup_file(self, source_path: Path, operation: str) -> BackupEntry:
        """Copy a file to backups/config/ with timestamp."""
        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        dest = self._dir / "config" / f"{source_path.name}.{ts}.bak"
        shutil.copy2(source_path, dest)
        entry = self._make_entry(
            type="config",
            source=str(source_path),
            backup_path=str(dest),
            operation=operation,
            size_bytes=dest.stat().st_size,
        )
        await self._append_manifest(entry)
        self._prune_after_write()
        return entry

    async def backup_dashboard(
        self, dashboard_id: str, config: dict[str, Any], operation: str
    ) -> BackupEntry:
        """Snapshot a dashboard config as JSON."""
        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        dest = self._dir / "dashboards" / f"{dashboard_id}.{ts}.json"
        content = json.dumps(config, indent=2)
        dest.write_text(content)
        entry = self._make_entry(
            type="dashboard",
            source=dashboard_id,
            backup_path=str(dest),
            operation=operation,
            size_bytes=len(content.encode()),
        )
        await self._append_manifest(entry)
        self._prune_after_write()
        return entry

    async def backup_entities(
        self, entities: list[dict[str, Any]], operation: str
    ) -> BackupEntry:
        """Save entity registry entries as JSONL."""
        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        dest = self._dir / "entities" / f"entities.{ts}.jsonl"
        lines = [json.dumps(e) for e in entities]
        content = "\n".join(lines) + "\n"
        dest.write_text(content)
        entry = self._make_entry(
            type="entity",
            source=f"{len(entities)} entities",
            backup_path=str(dest),
            operation=operation,
            size_bytes=len(content.encode()),
        )
        await self._append_manifest(entry)
        self._prune_after_write()
        return entry

    async def backup_db_rows(
        self, table: str, rows: list[dict[str, Any]], operation: str
    ) -> BackupEntry:
        """Dump rows as JSON for small DB operations."""
        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        dest = self._dir / "db" / f"{table}.{ts}.jsonl"
        lines = [json.dumps(r) for r in rows]
        content = "\n".join(lines) + "\n"
        dest.write_text(content)
        entry = self._make_entry(
            type="db",
            source=f"{table} ({len(rows)} rows)",
            backup_path=str(dest),
            operation=operation,
            size_bytes=len(content.encode()),
        )
        await self._append_manifest(entry)
        self._prune_after_write()
        return entry

    async def list_backups(
        self,
        type_filter: str = "all",
        since: datetime | None = None,
        limit: int = 50,
    ) -> list[BackupEntry]:
        """Read manifest and return matching entries."""
        entries: list[BackupEntry] = []
        if not self._manifest.exists():
            return entries

        for line in self._manifest.read_text().splitlines():
            if not line.strip():
                continue
            data = json.loads(line)
            entry = BackupEntry(**data)
            if type_filter != "all" and entry.type != type_filter:
                continue
            if since:
                entry_time = datetime.fromisoformat(entry.timestamp)
                if entry_time < since:
                    continue
            entries.append(entry)

        entries.sort(key=lambda e: e.timestamp, reverse=True)
        return entries[:limit]

    async def prune(
        self,
        *,
        dry_run: bool = False,
        older_than_days: int | None = None,
        type_filter: str = "all",
        clear_all: bool = False,
    ) -> dict[str, Any]:
        """Async wrapper around ``_prune_sync`` for tool-path callers.

        Retention logic is pure file I/O (no network), so the heavy lifting
        lives in the sync method — which lets ``__init__`` and the
        post-write hook reuse the same code without async gymnastics.
        """
        return self._prune_sync(
            dry_run=dry_run,
            older_than_days=older_than_days,
            type_filter=type_filter,
            clear_all=clear_all,
        )

    def _prune_after_write(self) -> None:
        """Called after every successful backup write. Non-fatal on error."""
        try:
            self._prune_sync()
        except Exception as exc:
            logger.warning("Post-write retention pass failed: %s", exc)

    def _prune_sync(
        self,
        *,
        dry_run: bool = False,
        older_than_days: int | None = None,
        type_filter: str = "all",
        clear_all: bool = False,
    ) -> dict[str, Any]:
        """Drop manifest entries + on-disk files per retention policy.

        Returns ``{would_delete|deleted, count, bytes_freed}``. When not
        dry-running, manifest.jsonl is rewritten via tmp+rename — this is
        the sole place manifest is rewritten; append-only everywhere else.
        """
        empty_result_key = "would_delete" if dry_run else "deleted"
        if not self._manifest.exists():
            return {empty_result_key: [], "count": 0, "bytes_freed": 0}

        all_entries: list[BackupEntry] = []
        for line in self._manifest.read_text().splitlines():
            if not line.strip():
                continue
            try:
                data = json.loads(line)
                all_entries.append(BackupEntry(**data))
            except Exception:
                # Malformed lines keep their place — don't drop them
                # silently during prune, just skip them for the scan.
                logger.debug("Malformed manifest line skipped during prune")
                continue

        if type_filter != "all":
            scope = [e for e in all_entries if e.type == type_filter]
        else:
            scope = all_entries

        to_prune: list[BackupEntry] = []
        if clear_all:
            to_prune = list(scope)
        else:
            age_days = (
                older_than_days
                if older_than_days is not None
                else self._max_age_days
            )
            cutoff = datetime.now(UTC) - timedelta(days=age_days)

            # Age pass
            for e in scope:
                try:
                    entry_time = datetime.fromisoformat(e.timestamp)
                except ValueError:
                    continue
                if entry_time < cutoff:
                    to_prune.append(e)

            # Per-type count cap on whatever the age pass didn't catch.
            pruned_ids = {e.id for e in to_prune}
            remaining = [e for e in scope if e.id not in pruned_ids]
            by_type: dict[str, list[BackupEntry]] = {}
            for e in remaining:
                by_type.setdefault(e.type, []).append(e)
            for entries in by_type.values():
                entries.sort(key=lambda e: e.timestamp, reverse=True)
                if len(entries) > self._max_per_type:
                    to_prune.extend(entries[self._max_per_type:])

        bytes_freed = sum(e.size_bytes for e in to_prune)

        if dry_run:
            return {
                "would_delete": [e.to_dict() for e in to_prune],
                "count": len(to_prune),
                "bytes_freed": bytes_freed,
            }

        for e in to_prune:
            try:
                Path(e.backup_path).unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("Failed to unlink %s: %s", e.backup_path, exc)

        # Atomic manifest rewrite — the ONE place manifest.jsonl is not
        # strictly append-only. Writing tmp + os.replace() means any
        # concurrent reader either sees the pre-prune file in full or the
        # post-prune file in full, never a half-written mix.
        pruned_ids = {e.id for e in to_prune}
        survivors = [e for e in all_entries if e.id not in pruned_ids]
        tmp = self._manifest.with_suffix(".jsonl.tmp")
        with open(tmp, "w") as f:
            for e in survivors:
                f.write(json.dumps(e.to_dict()) + "\n")
        os.replace(tmp, self._manifest)

        if to_prune:
            logger.debug(
                "Pruned %d backup(s), freed %d bytes",
                len(to_prune), bytes_freed,
            )

        return {
            "deleted": [e.to_dict() for e in to_prune],
            "count": len(to_prune),
            "bytes_freed": bytes_freed,
        }

    def _maybe_warn_legacy_dir(self) -> None:
        """§6 — admin warning when legacy backup_dir still has data.

        Silent when:
        - backup_dir IS the legacy path (still in use, no migration needed).
        - legacy path doesn't exist (fresh deploy).
        - legacy path is empty.
        """
        if self._dir == _LEGACY_BACKUP_DIR:
            return
        if not _LEGACY_BACKUP_DIR.exists():
            return
        try:
            has_content = any(_LEGACY_BACKUP_DIR.iterdir())
        except OSError:
            return
        if not has_content:
            return

        entry_count = 0
        legacy_manifest = _LEGACY_BACKUP_DIR / "manifest.jsonl"
        try:
            if legacy_manifest.is_file():
                entry_count = sum(
                    1 for line in legacy_manifest.read_text().splitlines()
                    if line.strip()
                )
        except OSError:
            pass

        logger.warning(
            "Legacy backup directory detected at %s (%d manifest entries). "
            "Your configured backup_dir is %s, which is different. "
            "The legacy tree is OUTSIDE the configured location so "
            "retention will not touch it. To adopt the new default: move "
            "the files (mv or scp), then rewrite manifest.jsonl paths to "
            "match the new location; OR set backup_dir: %s in addon "
            "options to keep using the legacy path.",
            _LEGACY_BACKUP_DIR,
            entry_count,
            self._dir,
            _LEGACY_BACKUP_DIR,
        )

    def _make_entry(self, **kwargs: Any) -> BackupEntry:
        ts = datetime.now(UTC).isoformat()
        entry_id = f"{kwargs['type']}_{int(time.time() * 1000)}"
        return BackupEntry(id=entry_id, timestamp=ts, **kwargs)

    async def _append_manifest(self, entry: BackupEntry) -> None:
        """Append to manifest.jsonl — never rewrite, never delete here.

        (Rewrites happen only in `_prune_sync`.)
        """
        with open(self._manifest, "a") as f:
            f.write(json.dumps(entry.to_dict()) + "\n")
