"""Capture store — manages headless-browser artifacts (screenshots, traces).

The `haops_ui_screenshot` / `haops_ui_trace` tools produce PNGs and trace zips.
This store is where they live: a managed directory + append-only manifest, with
list / read / delete / annotate / purge — surfaced in the HA Ops sidebar's
"Captures" tab. It deliberately mirrors `BackupManager`'s manifest+retention
pattern (append-only jsonl, atomic rewrite on prune), and adds the operations a
gallery needs that backups don't: read-bytes (download), delete-by-id, annotate
(note / change-link), and lookup-by-transaction (for Timeline thumbnails).

This is ha-ops-admin storage (the addon's own artifacts) — NOT Home Assistant
state — so it has no MCP tools; management happens directly in the ingress UI.
Mutations are still audit-logged by the caller for traceability.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class CaptureEntry:
    """One captured artifact (screenshot PNG or trace zip)."""

    id: str
    timestamp: str  # ISO-8601 UTC
    kind: str  # "screenshot" | "trace"
    view: str  # the dashboard path / url captured
    filename: str  # basename under <dir>/files/
    size_bytes: int
    nav_ms: float | None = None
    console_errors: int = 0  # count (kept for back-compat / quick badge)
    errors: list[str] = field(default_factory=list)  # the actual messages
    note: str = ""
    transaction_id: str = ""  # links the capture to a change/audit entry
    viewport: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class CaptureStore:
    """JSON-manifest store for UI capture artifacts.

    Layout under ``captures_dir``::

        manifest.jsonl   # append-only, one CaptureEntry per line
        files/<id>.<ext> # the PNG / zip artifacts

    Retention (``max_count`` newest, ``max_age_days``) is enforced on init and
    after every ``save``. Deletes/annotations rewrite the manifest atomically.
    """

    def __init__(
        self,
        captures_dir: Path,
        *,
        max_count: int = 200,
        max_age_days: int = 30,
    ) -> None:
        self._dir = Path(captures_dir).resolve()
        self._files = self._dir / "files"
        self._manifest = self._dir / "manifest.jsonl"
        self._files.mkdir(parents=True, exist_ok=True)
        self.max_count = max_count
        self.max_age_days = max_age_days
        self._prune()

    # ---- write ---------------------------------------------------------------

    def save(
        self,
        *,
        content: bytes,
        kind: str,
        view: str,
        ext: str,
        nav_ms: float | None = None,
        errors: list[str] | None = None,
        note: str = "",
        transaction_id: str = "",
        viewport: dict[str, int] | None = None,
    ) -> CaptureEntry:
        """Persist an artifact + append a manifest entry. Prunes afterward."""
        cid = uuid.uuid4().hex[:12]
        filename = f"{cid}.{ext.lstrip('.')}"
        (self._files / filename).write_bytes(content)
        errs = errors or []
        entry = CaptureEntry(
            id=cid,
            timestamp=datetime.now(UTC).isoformat(),
            kind=kind,
            view=view,
            filename=filename,
            size_bytes=len(content),
            nav_ms=nav_ms,
            console_errors=len(errs),
            errors=errs,
            note=note,
            transaction_id=transaction_id,
            viewport=viewport or {},
        )
        with open(self._manifest, "a") as f:
            f.write(json.dumps(entry.to_dict()) + "\n")
        self._prune()
        return entry

    # ---- read ----------------------------------------------------------------

    def _read_all(self) -> list[CaptureEntry]:
        if not self._manifest.exists():
            return []
        out: list[CaptureEntry] = []
        for line in self._manifest.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(CaptureEntry(**json.loads(line)))
            except (json.JSONDecodeError, TypeError):
                continue  # skip malformed / schema-drifted lines
        return out

    def list_entries(self, *, limit: int = 200) -> list[CaptureEntry]:
        """Newest-first capture entries."""
        entries = self._read_all()
        entries.sort(key=lambda e: e.timestamp, reverse=True)
        return entries[:limit]

    def get(self, capture_id: str) -> CaptureEntry | None:
        for e in self._read_all():
            if e.id == capture_id:
                return e
        return None

    def artifact_path(self, entry: CaptureEntry) -> Path:
        """Absolute path to an entry's artifact file."""
        return self._files / entry.filename

    def read_bytes(self, capture_id: str) -> tuple[CaptureEntry, bytes] | None:
        """Return (entry, file bytes) for download/serve, or None."""
        entry = self.get(capture_id)
        if entry is None:
            return None
        path = self._files / entry.filename
        if not path.is_file():
            # Manifest entry survives but its artifact file is gone (manual
            # delete / volume hiccup). Soft-fail to None — callers surface a
            # 404 — but warn so the inconsistency is visible, not silent.
            logger.warning(
                "capture %s in manifest but file missing: %s", capture_id, path
            )
            return None
        return entry, path.read_bytes()

    def by_transaction(self, transaction_id: str) -> CaptureEntry | None:
        """Newest capture linked to a change (for Timeline thumbnails)."""
        if not transaction_id:
            return None
        linked = [e for e in self._read_all() if e.transaction_id == transaction_id]
        linked.sort(key=lambda e: e.timestamp, reverse=True)
        return linked[0] if linked else None

    def stats(self) -> dict[str, object]:
        entries = self._read_all()
        per_kind: dict[str, int] = {}
        for e in entries:
            per_kind[e.kind] = per_kind.get(e.kind, 0) + 1
        return {
            "count": len(entries),
            "total_bytes": sum(e.size_bytes for e in entries),
            "per_kind": per_kind,
            "retention": {"max_count": self.max_count, "max_age_days": self.max_age_days},
            "dir": str(self._dir),
        }

    # ---- mutate --------------------------------------------------------------

    def delete(self, ids: list[str]) -> dict[str, object]:
        """Delete artifacts + manifest entries by id. Atomic manifest rewrite."""
        idset: set[str] = set(ids)
        kept: list[CaptureEntry] = []
        deleted = 0
        bytes_freed = 0
        for e in self._read_all():
            if e.id in idset:
                p = self._files / e.filename
                if p.is_file():
                    bytes_freed += e.size_bytes
                    p.unlink()
                deleted += 1
            else:
                kept.append(e)
        self._rewrite(kept)
        return {"deleted": deleted, "bytes_freed": bytes_freed}

    def annotate(
        self,
        capture_id: str,
        *,
        note: str | None = None,
        transaction_id: str | None = None,
    ) -> CaptureEntry | None:
        """Set the note and/or change-link on a capture."""
        entries = self._read_all()
        found: CaptureEntry | None = None
        for e in entries:
            if e.id == capture_id:
                if note is not None:
                    e.note = note
                if transaction_id is not None:
                    e.transaction_id = transaction_id
                found = e
                break
        if found is not None:
            self._rewrite(entries)
        return found

    def purge(
        self,
        *,
        older_than_days: int | None = None,
        clear_all: bool = False,
        dry_run: bool = False,
    ) -> dict[str, object]:
        """Retention sweep. Returns {deleted|would_delete, count, bytes_freed}."""
        entries = self._read_all()
        if clear_all:
            doomed = entries
        else:
            days = older_than_days if older_than_days is not None else self.max_age_days
            cutoff = datetime.now(UTC).timestamp() - days * 86400
            doomed = [e for e in entries if self._ts(e) < cutoff]
        count = len(doomed)
        bytes_freed = sum(e.size_bytes for e in doomed)
        if not dry_run and doomed:
            self.delete([e.id for e in doomed])
        key = "would_delete" if dry_run else "deleted"
        return {key: True, "count": count, "bytes_freed": bytes_freed}

    # ---- internals -----------------------------------------------------------

    @staticmethod
    def _ts(entry: CaptureEntry) -> float:
        try:
            return datetime.fromisoformat(entry.timestamp).timestamp()
        except ValueError:
            return 0.0

    def _rewrite(self, entries: list[CaptureEntry]) -> None:
        tmp = self._manifest.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(e.to_dict()) + "\n" for e in entries))
        os.replace(tmp, self._manifest)

    def _prune(self) -> None:
        """Enforce max_age_days + max_count (keep newest). Drops orphan files."""
        entries = self._read_all()
        entries.sort(key=lambda e: e.timestamp, reverse=True)
        cutoff = datetime.now(UTC).timestamp() - self.max_age_days * 86400
        keep: list[CaptureEntry] = []
        for i, e in enumerate(entries):
            if i < self.max_count and self._ts(e) >= cutoff:
                keep.append(e)
        if len(keep) != len(entries):
            drop = {e.id for e in entries} - {e.id for e in keep}
            for e in entries:
                if e.id in drop:
                    p = self._files / e.filename
                    if p.is_file():
                        p.unlink()
            self._rewrite(keep)
        self._sweep_orphan_files(keep)

    def _sweep_orphan_files(self, known: list[CaptureEntry]) -> None:
        """Delete files in files/ not referenced by any manifest entry.

        Reaps crash leftovers: save() writes the artifact THEN appends the
        manifest line, so a crash in that window leaves a blob no entry
        points at — invisible to list_entries and never otherwise cleaned.
        Runs on init + after every save (via _prune). Save is synchronous
        and single-threaded, so a just-written file is always already in the
        manifest by the time this runs — no in-flight file gets nuked.
        """
        known_names = {e.filename for e in known}
        for f in self._files.iterdir():
            if f.is_file() and f.name not in known_names:
                logger.warning(
                    "orphan capture file (no manifest entry), removing: %s", f
                )
                f.unlink()
