# Shell-Output Persistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist every `haops_exec_shell` run's full stdout/stderr to a durable file store and surface it inline on the existing Timeline row in the sidebar UI (lazy-loaded on row-expand).

**Architecture:** New `ShellOutputStore` (mirrors `safety/captures.py::CaptureStore`) writes `manifest.jsonl` + `files/<id>.json`. `haops_exec_shell` saves output, stamps `output_id` into its audit row. A new auth-gated route serves the output by id; the Timeline frontend lazy-fetches and renders it, mirroring the existing diff-lazy-load pattern.

**Tech Stack:** Python 3.11 (asyncio, dataclasses), Starlette routes, Alpine.js + Tailwind (single-file `static/ui.html`), pytest.

**Spec:** `docs/superpowers/specs/2026-06-13-shell-output-persistence-design.md`

---

## File Structure

- **Create:** `src/ha_ops_mcp/safety/shell_output.py` — `ShellOutputStore` + `ShellRunEntry`.
- **Modify:** `src/ha_ops_mcp/config.py` — `ShellOutputConfig`, `HaOpsConfig` field, env map, parse call.
- **Modify:** `src/ha_ops_mcp/server.py` — import, instantiate store, `HaOpsContext.shell_output` field, ctor arg.
- **Modify:** `src/ha_ops_mcp/tools/shell.py` — persist output + `output_id` into audit (success + timeout paths).
- **Modify:** `src/ha_ops_mcp/ui/routes.py` — `GET /api/ui/timeline/shell_output`, `exec_shell` excerpt gains `output_id`.
- **Modify:** `src/ha_ops_mcp/static/ui.html` — Timeline expand: lazy-fetch + render stdout/stderr boxes.
- **Modify:** `tests/conftest.py` — add `shell_output` to the `ctx` fixture.
- **Create/Modify tests:** `tests/test_shell_output_store.py` (new), `tests/test_shell_tools.py`, `tests/test_ui_routes.py`.

**Convention:** run all Python via the project venv — prefix commands with `.venv/bin/` (e.g. `.venv/bin/pytest`). Do not call global `python`/`pytest`.

---

## Task 1: `ShellOutputStore` module

**Files:**
- Create: `src/ha_ops_mcp/safety/shell_output.py`
- Test: `tests/test_shell_output_store.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_shell_output_store.py`:

```python
"""Tests for the shell-output store."""

from __future__ import annotations

from pathlib import Path

from ha_ops_mcp.safety.shell_output import ShellOutputStore


def test_save_and_read_roundtrip(tmp_path: Path):
    store = ShellOutputStore(tmp_path / "shell_output")
    entry = store.save(
        command="echo hi",
        cwd="/tmp",
        exit_code=0,
        duration_ms=12.3,
        stdout="hi\n",
        stderr="",
    )
    assert entry.exit_code == 0
    assert entry.command == "echo hi"
    assert entry.stdout_bytes == len("hi\n")
    assert entry.truncated is False

    got = store.read_output(entry.id)
    assert got == {"stdout": "hi\n", "stderr": ""}

    fetched = store.get(entry.id)
    assert fetched is not None
    assert fetched.id == entry.id


def test_read_output_unknown_id_returns_none(tmp_path: Path):
    store = ShellOutputStore(tmp_path / "shell_output")
    assert store.read_output("nope") is None
    assert store.get("nope") is None


def test_stream_cap_truncates_and_flags(tmp_path: Path):
    store = ShellOutputStore(tmp_path / "shell_output")
    big = "x" * (ShellOutputStore._STREAM_CAP + 500)
    entry = store.save(
        command="cat big",
        cwd="/tmp",
        exit_code=0,
        duration_ms=1.0,
        stdout=big,
        stderr="",
    )
    assert entry.truncated is True
    out = store.read_output(entry.id)
    assert out is not None
    assert out["stdout"].endswith("\n... (truncated)")
    assert len(out["stdout"]) <= ShellOutputStore._STREAM_CAP + len("\n... (truncated)")


def test_prune_enforces_max_count(tmp_path: Path):
    store = ShellOutputStore(tmp_path / "shell_output", max_count=3, max_age_days=30)
    ids = []
    for i in range(5):
        e = store.save(
            command=f"echo {i}", cwd="/tmp", exit_code=0,
            duration_ms=1.0, stdout=str(i), stderr="",
        )
        ids.append(e.id)
    remaining = {e.id for e in store.list_entries(limit=100)}
    assert len(remaining) == 3
    # Oldest two pruned, their files gone.
    assert store.read_output(ids[0]) is None
    assert store.read_output(ids[4]) is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_shell_output_store.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ha_ops_mcp.safety.shell_output'`

- [ ] **Step 3: Write the implementation**

Create `src/ha_ops_mcp/safety/shell_output.py`:

```python
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
        (self._files / f"{rid}.json").write_text(
            json.dumps({"stdout": out, "stderr": err})
        )
        entry = ShellRunEntry(
            id=rid,
            timestamp=datetime.now(UTC).isoformat(),
            command=command,
            cwd=cwd,
            exit_code=exit_code,
            duration_ms=duration_ms,
            stdout_bytes=len(out),
            stderr_bytes=len(err),
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
        for line in self._manifest.read_text().splitlines():
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
            data = json.loads(path.read_text())
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
        tmp.write_text("".join(json.dumps(e.to_dict()) + "\n" for e in entries))
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_shell_output_store.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ha_ops_mcp/safety/shell_output.py tests/test_shell_output_store.py
git commit -m "feat(shell): add ShellOutputStore for persisting exec output"
```

---

## Task 2: Config — `ShellOutputConfig`

**Files:**
- Modify: `src/ha_ops_mcp/config.py` (add dataclass after `CaptureConfig` ~line 76; add `HaOpsConfig` field ~line 109; add env map entries ~line 134; add parse call ~line 231)
- Test: `tests/test_config_loader.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_config_loader.py`:

```python
def test_shell_output_config_defaults():
    from ha_ops_mcp.config import HaOpsConfig

    cfg = HaOpsConfig()
    assert cfg.shell_output.dir == ""
    assert cfg.shell_output.max_count == 500
    assert cfg.shell_output.max_age_days == 30


def test_shell_output_env_override(monkeypatch):
    from ha_ops_mcp.config import load_config

    monkeypatch.setenv("HA_OPS_SHELL_OUTPUT_MAX_COUNT", "12")
    monkeypatch.setenv("HA_OPS_SHELL_OUTPUT_DIR", "/tmp/so")
    cfg = load_config(None)
    assert cfg.shell_output.max_count == 12
    assert cfg.shell_output.dir == "/tmp/so"
```

> Note: if `load_config`'s signature differs (e.g. takes a path positionally or
> a keyword), match the existing calls already in `tests/test_config_loader.py`.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_config_loader.py -k shell_output -v`
Expected: FAIL — `AttributeError: 'HaOpsConfig' object has no attribute 'shell_output'`

- [ ] **Step 3: Write the implementation**

In `src/ha_ops_mcp/config.py`, add after the `CaptureConfig` dataclass (after ~line 76):

```python
@dataclass
class ShellOutputConfig:
    # Persisted haops_exec_shell output (surfaced inline on the Timeline).
    # Empty dir = derive <backup.dir>/shell_output in server.py. Retention
    # mirrors captures: newest max_count kept, older than max_age_days pruned.
    dir: str = ""
    max_count: int = 500
    max_age_days: int = 30
```

Add the field to `HaOpsConfig` (after the `captures` line ~109):

```python
    shell_output: ShellOutputConfig = field(default_factory=ShellOutputConfig)
```

Add to `_ENV_MAP` (after the `CAPTURES_*` block ~line 134):

```python
    "SHELL_OUTPUT_DIR": ("shell_output", "dir"),
    "SHELL_OUTPUT_MAX_COUNT": ("shell_output", "max_count"),
    "SHELL_OUTPUT_MAX_AGE_DAYS": ("shell_output", "max_age_days"),
```

Add the parse call in the `HaOpsConfig(...)` constructor (after the `captures=` line ~231):

```python
        shell_output=_build_dataclass(ShellOutputConfig, data.get("shell_output")),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_config_loader.py -k shell_output -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ha_ops_mcp/config.py tests/test_config_loader.py
git commit -m "feat(config): add ShellOutputConfig (dir + retention)"
```

---

## Task 3: Server wiring + conftest fixture

**Files:**
- Modify: `src/ha_ops_mcp/server.py` (import ~line 23; `HaOpsContext` field ~line 84; instantiate ~line 194; ctor arg ~line 225)
- Modify: `tests/conftest.py` (`ctx` fixture ~line 544)

- [ ] **Step 1: Update conftest fixture (this is the failing-test surface)**

In `tests/conftest.py`, add the import near the other safety imports (~line 26):

```python
from ha_ops_mcp.safety.shell_output import ShellOutputStore
```

In the `ctx` fixture's `HaOpsContext(...)` call (after the `captures=` line ~544), add:

```python
        shell_output=ShellOutputStore(backup_dir / "shell_output"),
```

- [ ] **Step 2: Run a ctx-using test to verify it fails**

Run: `.venv/bin/pytest tests/test_shell_tools.py -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'shell_output'` (the dataclass field doesn't exist yet).

- [ ] **Step 3: Wire the server**

In `src/ha_ops_mcp/server.py`, add the import (near line 23, after the `CaptureStore` import):

```python
from ha_ops_mcp.safety.shell_output import ShellOutputStore
```

Add the field to the `HaOpsContext` dataclass (after the `captures: CaptureStore` line ~84):

```python
    shell_output: ShellOutputStore
```

> Place it among the other required (no-default) fields — before any field that
> has a default (e.g. `auth_provider`) so dataclass field ordering stays valid.

Instantiate the store (after the `captures = CaptureStore(...)` block ~line 194):

```python
    # Persisted shell output (haops_exec_shell) — default <backup_dir>/shell_output,
    # a sibling of captures/ on the survival /backup volume. Retention mirrors
    # captures (newest max_count kept, older than max_age_days pruned on init).
    shell_output_dir_str = (
        config.shell_output.dir or str(Path(config.backup.dir) / "shell_output")
    )
    shell_output = ShellOutputStore(
        Path(shell_output_dir_str).resolve(),
        max_count=config.shell_output.max_count,
        max_age_days=config.shell_output.max_age_days,
    )
```

Pass it into the `HaOpsContext(...)` return (after the `captures=captures,` line ~225):

```python
        shell_output=shell_output,
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_shell_tools.py -v`
Expected: PASS (5 existing tests)

- [ ] **Step 5: Commit**

```bash
git add src/ha_ops_mcp/server.py tests/conftest.py
git commit -m "feat(server): wire ShellOutputStore into HaOpsContext"
```

---

## Task 4: Persist output in `haops_exec_shell`

**Files:**
- Modify: `src/ha_ops_mcp/tools/shell.py` (lines ~96–142: wrap exec timing, save output, add `output_id` to audit; timeout branch ~106–112)
- Test: `tests/test_shell_tools.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_shell_tools.py`:

```python
@pytest.mark.asyncio
async def test_exec_shell_persists_output(ctx):
    preview = await haops_exec_shell(ctx, command="echo persistme")
    await haops_exec_shell(
        ctx, command="echo persistme", confirm=True,
        token=preview["token"], cwd="/tmp",
    )
    # The most recent run is retrievable from the store with the output.
    entries = ctx.shell_output.list_entries(limit=5)
    assert entries, "expected a persisted shell run"
    latest = entries[0]
    assert latest.command == "echo persistme"
    out = ctx.shell_output.read_output(latest.id)
    assert out is not None
    assert "persistme" in out["stdout"]


@pytest.mark.asyncio
async def test_exec_shell_audit_carries_output_id(ctx):
    preview = await haops_exec_shell(ctx, command="echo audit")
    await haops_exec_shell(
        ctx, command="echo audit", confirm=True,
        token=preview["token"], cwd="/tmp",
    )
    recent = ctx.audit.read_recent(limit=10)
    shell_rows = [e for e in recent if e.get("tool") == "exec_shell"]
    assert shell_rows, "expected an exec_shell audit row"
    details = shell_rows[0].get("details") or {}
    assert isinstance(details.get("output_id"), str) and details["output_id"]
    # The output_id resolves to a real stored run.
    assert ctx.shell_output.get(details["output_id"]) is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_shell_tools.py -k "persists_output or output_id" -v`
Expected: FAIL — store is empty / audit details lack `output_id`.

- [ ] **Step 3: Edit the tool**

In `src/ha_ops_mcp/tools/shell.py`:

Add `import time` near the top imports (after `import asyncio`):

```python
import time
```

Replace the execute block (current lines ~96–142, from `try:` through the final `return response`) with the timed + persisted version:

```python
    t0 = time.monotonic()
    timed_out = False
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
    except TimeoutError:
        proc.kill()
        # Best-effort: drain whatever the killed process buffered so the
        # timeout case still persists partial output.
        try:
            stdout, stderr = await proc.communicate()
        except Exception:
            stdout, stderr = b"", b""
        timed_out = True
    except FileNotFoundError:
        return {"error": f"Working directory not found: {cwd}"}
    except Exception as e:
        return {"error": f"Execution failed: {e}"}

    ctx.safety.consume_token(token)
    duration_ms = round((time.monotonic() - t0) * 1000, 1)

    stdout_text = stdout.decode(errors="replace").rstrip()
    stderr_text = stderr.decode(errors="replace").rstrip()
    exit_code = None if timed_out else proc.returncode

    # Persist the full output durably (Timeline surfaces it inline). Never let
    # a store failure break the tool's return — swallow + log.
    output_id: str | None = None
    try:
        entry = ctx.shell_output.save(
            command=command,
            cwd=cwd,
            exit_code=exit_code,
            duration_ms=duration_ms,
            stdout=stdout_text,
            stderr=stderr_text,
        )
        output_id = entry.id
    except Exception:  # noqa: BLE001 — persistence is best-effort
        logging.getLogger(__name__).exception("shell-output persist failed")

    await ctx.audit.log(
        tool="exec_shell",
        details={
            "command": command,
            "cwd": cwd,
            "exit_code": exit_code,
            "output_id": output_id,
        },
        token_id=token,
    )

    if timed_out:
        return {
            "error": f"Command timed out after {timeout}s",
            "command": command,
            "stdout": stdout_text[:50000],
            "stderr": stderr_text[:50000],
        }

    response: dict[str, Any] = {
        "exit_code": proc.returncode,
        "stdout": stdout_text,
        "stderr": stderr_text,
    }

    # Truncate very large output (model-response cap; the store keeps more).
    for key in ("stdout", "stderr"):
        if len(response[key]) > 50000:
            response[key] = response[key][:50000] + "\n... (truncated)"
            response[f"{key}_truncated"] = True

    return response
```

Add `import logging` to the top imports if not already present (after `import time`):

```python
import logging
```

> Behaviour change note: the timeout branch previously consumed the token and
> returned only an error. It now also persists partial output and the audit row,
> and the token is consumed once on the shared path below. Verify the existing
> `test_exec_shell_timeout_capped` still passes — it only exercises the preview
> phase (timeout cap), so it is unaffected.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_shell_tools.py -v`
Expected: PASS (7 tests — 5 existing + 2 new)

- [ ] **Step 5: Commit**

```bash
git add src/ha_ops_mcp/tools/shell.py tests/test_shell_tools.py
git commit -m "feat(shell): persist exec output + stamp output_id into audit"
```

---

## Task 5: Route + excerpt surface

**Files:**
- Modify: `src/ha_ops_mcp/ui/routes.py` (new route inside `register_ui_routes`, near the other `/api/ui/timeline/*` routes ~line 258; add `output_id` to the `exec_shell` `keep` list in `_audit_details_excerpt` ~line 1385)
- Test: `tests/test_ui_routes.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ui_routes.py`:

```python
def test_shell_output_endpoint_returns_output(client, ctx):
    entry = ctx.shell_output.save(
        command="echo route", cwd="/tmp", exit_code=0,
        duration_ms=2.0, stdout="route-out\n", stderr="",
    )
    res = client.get(f"/api/ui/timeline/shell_output?id={entry.id}")
    assert res.status_code == 200
    body = res.json()
    assert body["command"] == "echo route"
    assert body["exit_code"] == 0
    assert "route-out" in body["stdout"]
    assert body["stderr"] == ""
    assert body["truncated"] is False


def test_shell_output_endpoint_404_unknown(client):
    res = client.get("/api/ui/timeline/shell_output?id=doesnotexist")
    assert res.status_code == 404


def test_shell_output_endpoint_400_missing_id(client):
    res = client.get("/api/ui/timeline/shell_output")
    assert res.status_code == 400
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_ui_routes.py -k shell_output -v`
Expected: FAIL — route returns 404 for all (not registered).

- [ ] **Step 3: Add the route**

In `src/ha_ops_mcp/ui/routes.py`, inside `register_ui_routes`, after the
`api_timeline_diff` handler (it ends ~line 331, just before
`@mcp.custom_route("/api/ui/backups", ...)`), add:

```python
    @mcp.custom_route("/api/ui/timeline/shell_output", methods=["GET"])  # type: ignore[untyped-decorator]
    async def api_shell_output(request: Request) -> Response:
        """Lazy-load a persisted haops_exec_shell run's stdout/stderr.

        The Timeline list payload carries only `output_id` (in the entry's
        details_excerpt); the frontend fetches the body on row-expand and
        caches it on the entry, mirroring the diff lazy-load.

        Response: {command, cwd, exit_code, duration_ms, stdout, stderr,
        truncated}. 400 if `id` missing, 404 if the run / file is gone.
        """
        if not _is_authorized(request):
            return _unauthorized()
        run_id = request.query_params.get("id", "").strip()
        if not run_id:
            return JSONResponse(
                {"error": "id query param required"}, status_code=400
            )
        entry = ctx.shell_output.get(run_id)
        output = ctx.shell_output.read_output(run_id)
        if entry is None or output is None:
            return JSONResponse(
                {"error": f"Shell output {run_id!r} not found"},
                status_code=404,
            )
        return JSONResponse({
            "command": entry.command,
            "cwd": entry.cwd,
            "exit_code": entry.exit_code,
            "duration_ms": entry.duration_ms,
            "stdout": output["stdout"],
            "stderr": output["stderr"],
            "truncated": entry.truncated,
        })
```

In `_audit_details_excerpt` (~line 1385), change the `exec_shell` `keep` entry from:

```python
        "exec_shell": ["command", "cwd", "timeout"],
```

to:

```python
        "exec_shell": ["command", "cwd", "timeout", "output_id"],
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/test_ui_routes.py -k shell_output -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ha_ops_mcp/ui/routes.py tests/test_ui_routes.py
git commit -m "feat(ui): serve persisted shell output by id + excerpt output_id"
```

---

## Task 6: Frontend — render shell output on Timeline expand

**Files:**
- Modify: `src/ha_ops_mcp/static/ui.html` (template block in the Timeline expand `<div>` ~line 463–514; `toggleTimelineEntry` ~line 1179; add `_maybeFetchShellOutput` after `_maybeFetchDiff` ~line 1216)

> This task has no unit test (single-file Alpine SPA). Verify via the headless
> screenshot routine after the change — see Task 7. Keep the diff minimal and
> mirror the existing diff-lazy-load shape exactly.

- [ ] **Step 1: Add the fetch method**

In `src/ha_ops_mcp/static/ui.html`, after the `_maybeFetchDiff(idx)` method
(ends ~line 1216), add a sibling method:

```javascript
    async _maybeFetchShellOutput(idx) {
      // Lazy-load persisted exec_shell output on row-expand. The list
      // payload carries only output_id (in details_excerpt); pull the body
      // once and cache it on the entry as e._shell.
      var e = this.timeline?.entries?.[idx];
      if (!e) return;
      var oid = e.details_excerpt?.output_id;
      if (!oid) return;
      if (e._shell) return;                 // already loaded
      if (e._shell_loading) return;         // in-flight
      e._shell_loading = true;
      e._shell_error = '';
      try {
        var data = await this.fetchJson(
          '/timeline/shell_output?id=' + encodeURIComponent(oid)
        );
        e._shell = data;
      } catch (err) {
        e._shell_error = (err && err.message) || String(err);
      } finally {
        e._shell_loading = false;
      }
    },
```

- [ ] **Step 2: Call it on expand**

Edit `toggleTimelineEntry` (~line 1179) to also kick the shell fetch:

```javascript
    toggleTimelineEntry(idx) {
      this.expandedTimeline = this.expandedTimeline === idx ? -1 : idx;
      if (this.expandedTimeline === idx) {
        this._maybeFetchDiff(idx);
        this._maybeFetchShellOutput(idx);
      }
    },
```

- [ ] **Step 3: Render the output boxes**

In the Timeline expand `<div x-show="expandedTimeline === eidx" ...>` (opens
~line 463), insert these templates immediately after the `e.error` template
block (after line 467, before the `e.diff_present` template):

```html
          <template x-if="e._shell_loading">
            <div class="mt-3 text-xs text-text-muted italic">Loading output…</div>
          </template>
          <template x-if="e._shell_error">
            <div class="mt-3 text-xs text-banner-fail-fg bg-banner-fail-bg p-2 rounded font-mono"
                 x-text="'Failed to load output: ' + e._shell_error"></div>
          </template>
          <template x-if="e._shell">
            <div class="mt-3">
              <div class="text-xs text-text-muted mb-1">
                Output
                <span class="ml-1 font-mono"
                      x-text="'exit ' + (e._shell.exit_code === null ? '— (timeout)' : e._shell.exit_code)"></span>
                <template x-if="e._shell.truncated">
                  <span class="ml-2 text-orange-700 dark:text-orange-400">(truncated)</span>
                </template>
              </div>
              <template x-if="e._shell.stdout">
                <div class="mb-2">
                  <div class="text-[10px] uppercase tracking-wide text-text-muted mb-0.5">stdout</div>
                  <pre class="text-xs font-mono bg-surface-sunken p-3 rounded overflow-x-auto max-h-96 border border-border-subtle"
                       x-text="e._shell.stdout"></pre>
                </div>
              </template>
              <template x-if="!e._shell.stdout">
                <div class="mb-2 text-xs text-text-muted italic">stdout: (empty)</div>
              </template>
              <template x-if="e._shell.stderr">
                <div>
                  <div class="text-[10px] uppercase tracking-wide text-text-muted mb-0.5">stderr</div>
                  <pre class="text-xs font-mono bg-surface-sunken p-3 rounded overflow-x-auto max-h-96 border border-border-subtle text-red-700 dark:text-red-300"
                       x-text="e._shell.stderr"></pre>
                </div>
              </template>
            </div>
          </template>
```

- [ ] **Step 4: Verify the file still parses (smoke)**

Run: `.venv/bin/python -c "import pathlib; s=pathlib.Path('src/ha_ops_mcp/static/ui.html').read_text(); assert '_maybeFetchShellOutput' in s and 'shell_output?id=' in s; print('ok')"`
Expected: `ok`

- [ ] **Step 5: Commit**

```bash
git add src/ha_ops_mcp/static/ui.html
git commit -m "feat(ui): render persisted shell output inline on Timeline row"
```

---

## Task 7: Full suite, headless UI verify, version bump

**Files:**
- Modify: version files via `./scripts/sync-version.sh` (run, don't hand-edit)
- Verify: `haops_tools_check` parity (exec_shell surface unchanged — confirm green)

- [ ] **Step 1: Run the full test suite**

Run: `.venv/bin/pytest -x -q`
Expected: PASS (all green, including the new store / shell / route tests).

- [ ] **Step 2: Headless UI smoke (render check)**

Per the project's local-UI-screenshot routine (`reference_local_ui_screenshot`):
inject a fake timeline entry with `details_excerpt.output_id` + a stubbed
`_shell` payload and confirm the stdout/stderr boxes render (manual/headless —
no assertion harness required). If the routine script exists under `scripts/`,
run it; otherwise eyeball in a local browser against `/ui`.

- [ ] **Step 3: Bump version**

Run: `./scripts/sync-version.sh`
Expected: version files updated to the current tag (per `feedback_version_bump_required`). If HEAD is untagged, tag first per the release flow before running.

- [ ] **Step 4: Confirm tools_check parity**

The exec_shell tool params/description are unchanged, so `haops_tools_check`
should still pass. Confirm:

Run: `.venv/bin/pytest tests/test_tools_check.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "chore(shell): sync version for shell-output persistence"
```

---

## Self-Review Notes

**Spec coverage:**
- ShellOutputStore (store, cap, retention) → Task 1. ✓
- ShellOutputConfig + env → Task 2. ✓
- Server wiring + ctx field → Task 3. ✓
- Persist on exec + output_id in audit + timeout best-effort + swallow-on-failure → Task 4. ✓
- Route + excerpt output_id → Task 5. ✓
- Frontend inline render, lazy + cached → Task 6. ✓
- Tests (store / tool / route) → Tasks 1, 4, 5. ✓
- Graceful degradation (legacy rows, missing file 404) → Task 5 route (404) + Task 6 (no output_id → no box). ✓
- Version/tools_check → Task 7. ✓

**Type consistency:** `ShellOutputStore.save(...)` kwargs (`command, cwd, exit_code, duration_ms, stdout, stderr`) match the call in Task 4. `read_output` returns `{"stdout","stderr"}`, consumed by the route in Task 5 and rendered as `e._shell.stdout/stderr` in Task 6. `output_id` written in Task 4 = read in Task 5 excerpt = `details_excerpt.output_id` in Task 6. Endpoint path `/api/ui/timeline/shell_output` consistent across Tasks 5–6 (frontend uses `fetchJson('/timeline/shell_output...')` since `fetchJson` prepends `/api/ui`).

**Caching parity:** unlike diffs, shell output is cached on `e._shell` but NOT carried across timeline polls (no `_mergeCachedShell`). Acceptable — a poll re-collapses rows; on next expand the body re-fetches. If flicker is observed, add a merge mirroring `_mergeCachedDiffs` keyed on `timestamp|tool`. Noted as a known minor, not a v1 requirement.
