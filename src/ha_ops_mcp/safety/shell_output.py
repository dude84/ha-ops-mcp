"""Shell-output store — persists haops_exec_shell stdout/stderr.

The `haops_exec_shell` tool returns output to the model inline (capped) but
otherwise persists nothing, so once context rolls over the output is gone.
This store is the durable record: a managed directory + append-only manifest,
surfaced inline on the Timeline row in the sidebar UI (lazy-loaded on expand).

It mirrors `safety/captures.py::CaptureStore`'s manifest+retention shape
(append-only jsonl, atomic rewrite on prune), trimmed to what an inline-only
surface needs — no delete / annotate / by-transaction. This is ha-ops-admin
storage (the addon's own artifacts), NOT Home Assistant state, so it has no MCP
tools; the writing tool audit-logs the run for traceability.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass
class ShellRunEntry:
    """One persisted shell run (manifest line; output lives in files/)."""

    id: str
    timestamp: str  # ISO-8601 UTC
    command: str
    cwd: str
    exit_code: int | None  # None on timeout / kill
    duration_ms: float | None
    stdout_bytes: int  # bytes stored (post-cap)
    stderr_bytes: int  # bytes stored (post-cap)
    truncated: bool  # either stream hit the store cap

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class ShellOutputStore:
    """JSON-manifest store for shell-run output.

    Layout under ``shell_output_dir``::

        manifest.jsonl       # append-only, one ShellRunEntry per line
        files/<id>.json      # {"stdout": "...", "stderr": "..."}

    Retention (``max_count`` newest, ``max_age_days``) is enforced on init and
    after every ``save``.
    """

    # Per-stream cap in the store. Independent of the tool's 50k model-response
    # cap — the human reading the Timeline can see far more than the model.
    _STREAM_CAP = 1024 * 1024  # 1 MB
    _TRUNC_MARKER = "\n... (truncated)"

    def __init__(
        self,
        shell_output_dir: Path,
        *,
        max_count: int = 500,
        max_age_days: int = 30,
    ) -> None:
        self._dir = Path(shell_output_dir).resolve()
        self._files = self._dir / "files"
        self._manifest = self._dir / "manifest.jsonl"
        self._files.mkdir(parents=True, exist_ok=True)
        self.max_count = max_count
        self.max_age_days = max_age_days
        self._prune()

    # ---- write ---------------------------------------------------------------

    def _cap(self, s: str) -> tuple[str, bool]:
        if len(s) <= self._STREAM_CAP:
            return s, False
        return s[: self._STREAM_CAP] + self._TRUNC_MARKER, True

    def save(
        self,
        *,
        command: str,
        cwd: str,
        exit_code: int | None,
        duration_ms: float | None,
        stdout: str,
        stderr: str,
    ) -> ShellRunEntry:
        """Persist output + append a manifest entry. Prunes afterward."""
        rid = uuid.uuid4().hex[:12]
        out, out_trunc = self._cap(stdout)
        err, err_trunc = self._cap(stderr)
        # File is written before the manifest line; a crash between the two
        # leaves an orphan file (bounded wasted disk, no data loss) — same
        # accepted trade-off as CaptureStore. _prune only cleans manifested ids.
        (self._files / f"{rid}.json").write_text(
            json.dumps({"stdout": out, "stderr": err}), encoding="utf-8"
        )
        entry = ShellRunEntry(
            id=rid,
            timestamp=datetime.now(UTC).isoformat(),
            command=command,
            cwd=cwd,
            exit_code=exit_code,
            duration_ms=duration_ms,
            stdout_bytes=len(out.encode("utf-8")),
            stderr_bytes=len(err.encode("utf-8")),
            truncated=out_trunc or err_trunc,
        )
        with open(self._manifest, "a") as f:
            f.write(json.dumps(entry.to_dict()) + "\n")
        self._prune()
        return entry

    # ---- read ----------------------------------------------------------------

    def _read_all(self) -> list[ShellRunEntry]:
        if not self._manifest.exists():
            return []
        out: list[ShellRunEntry] = []
        for line in self._manifest.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(ShellRunEntry(**json.loads(line)))
            except (json.JSONDecodeError, TypeError):
                continue  # skip malformed / schema-drifted lines
        return out

    def list_entries(self, *, limit: int = 200) -> list[ShellRunEntry]:
        """Newest-first run entries."""
        entries = self._read_all()
        entries.sort(key=lambda e: e.timestamp, reverse=True)
        return entries[:limit]

    def get(self, run_id: str) -> ShellRunEntry | None:
        for e in self._read_all():
            if e.id == run_id:
                return e
        return None

    def read_output(self, run_id: str) -> dict[str, str] | None:
        """Return {"stdout", "stderr"} from the file, or None if absent."""
        entry = self.get(run_id)
        if entry is None:
            return None
        path = self._files / f"{run_id}.json"
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return None
        return {
            "stdout": str(data.get("stdout", "")),
            "stderr": str(data.get("stderr", "")),
        }

    # ---- internals -----------------------------------------------------------

    @staticmethod
    def _ts(entry: ShellRunEntry) -> float:
        try:
            return datetime.fromisoformat(entry.timestamp).timestamp()
        except ValueError:
            return 0.0

    def _rewrite(self, entries: list[ShellRunEntry]) -> None:
        tmp = self._manifest.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(e.to_dict()) + "\n" for e in entries), encoding="utf-8")
        os.replace(tmp, self._manifest)

    def _prune(self) -> None:
        """Enforce max_age_days + max_count (keep newest). Drop orphan files."""
        entries = self._read_all()
        entries.sort(key=lambda e: e.timestamp, reverse=True)
        cutoff = datetime.now(UTC).timestamp() - self.max_age_days * 86400
        keep: list[ShellRunEntry] = []
        for i, e in enumerate(entries):
            if i < self.max_count and self._ts(e) >= cutoff:
                keep.append(e)
        if len(keep) != len(entries):
            drop = {e.id for e in entries} - {e.id for e in keep}
            for e in entries:
                if e.id in drop:
                    p = self._files / f"{e.id}.json"
                    if p.is_file():
                        p.unlink()
            self._rewrite(keep)
