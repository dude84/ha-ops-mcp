# Shell-output persistence → Timeline (design)

**Date:** 2026-06-13
**Tool affected:** `haops_exec_shell`
**Status:** approved, pending implementation plan

## Problem

`haops_exec_shell` captures stdout/stderr and returns them in the tool response
to the model, but persists nothing. The audit row records only
`{command, cwd, exit_code}`. Once the model's context rolls over — or whenever a
human wants to look back at what a command actually printed — the output is gone.
There is no durable record and no human-facing surface for it.

## Goal

Persist every `haops_exec_shell` run's full output durably, and surface it
**inline on the existing Timeline row** in the sidebar UI (lazy-loaded on
row-expand). No new tab.

## Non-goals

- No dedicated "Console" tab, no gallery, no manual prune UI, no delete button.
- No backfill of historical runs — new runs only.
- No change to the tool's MCP response shape (the model still gets stdout/stderr
  inline, capped at the existing 50k).
- No change to the two-phase confirmation flow.

## Design

### Flow

```
exec runs → full stdout/stderr saved to ShellOutputStore (file)
          → output_id stamped into the audit row details
Timeline row expand → GET /api/ui/timeline/shell_output?id=<run_id>
          → render stdout / stderr boxes inline (cached on the entry)
```

### Components

#### 1. `safety/shell_output.py` — `ShellOutputStore`

Mirrors `safety/captures.py::CaptureStore`, trimmed to what an inline-only
surface needs. No delete / annotate / by-transaction.

**Layout** under `shell_output_dir`:

```
manifest.jsonl       # append-only, one ShellRunEntry per line
files/<run_id>.json  # {"stdout": "...", "stderr": "..."}
```

**`ShellRunEntry`** (dataclass, manifest line):

| field | type | note |
|-------|------|------|
| `id` | str | 12-hex run id (uuid4) |
| `timestamp` | str | ISO-8601 UTC |
| `command` | str | the executed command |
| `cwd` | str | working directory |
| `exit_code` | int \| null | null on timeout/kill |
| `duration_ms` | float \| null | wall-clock of the exec |
| `stdout_bytes` | int | size stored (post-cap) |
| `stderr_bytes` | int | size stored (post-cap) |
| `truncated` | bool | true if either stream hit the store cap |

**Methods:**

- `save(*, command, cwd, exit_code, duration_ms, stdout, stderr) → ShellRunEntry`
  — caps each stream at the store cap, writes `files/<id>.json`, appends the
  manifest line, prunes.
- `get(run_id) → ShellRunEntry | None` — manifest lookup.
- `read_output(run_id) → dict | None` — returns
  `{stdout, stderr}` from the file (None if missing).
- `list_entries(*, limit=200) → list[ShellRunEntry]` — newest-first (kept for
  symmetry / future use; not wired to a tab in v1).
- `_prune()` — enforce `max_count` (keep newest) + `max_age_days`, drop orphan
  files. Runs on init and after every `save`. Atomic manifest rewrite via
  `os.replace`, same as `CaptureStore`.

**Store cap:** 1 MB per stream (`_STREAM_CAP = 1024 * 1024`). On exceed, keep the
first `_STREAM_CAP` bytes + a `\n... (truncated)` marker and set
`truncated=True`. This is independent of the tool's 50k model-response cap.

#### 2. `config.py` — `ShellOutputConfig`

```python
@dataclass
class ShellOutputConfig:
    dir: str = ""           # empty → <backup.dir>/shell_output in server.py
    max_count: int = 500
    max_age_days: int = 30
```

- Add `shell_output: ShellOutputConfig = field(default_factory=ShellOutputConfig)`
  to `HaOpsConfig`, wire into `_build_dataclass` parsing.
- Env overrides: `SHELL_OUTPUT_DIR`, `SHELL_OUTPUT_MAX_COUNT`,
  `SHELL_OUTPUT_MAX_AGE_DAYS`.

#### 3. `server.py`

- Import `ShellOutputStore`.
- Derive dir: `config.shell_output.dir or <backup.dir>/shell_output`.
- Instantiate with `max_count` / `max_age_days`.
- Add `shell_output: ShellOutputStore` field to `HaOpsContext`; pass in the ctor.

#### 4. `tools/shell.py`

In the phase-2 execute path, after `communicate()` returns (success **and**
timeout paths where output exists):

- Measure `duration_ms` (wrap the exec in a `time.monotonic()` span).
- `entry = ctx.shell_output.save(command=..., cwd=..., exit_code=proc.returncode,
  duration_ms=..., stdout=<decoded>, stderr=<decoded>)`.
- Add `output_id=entry.id` to the existing `ctx.audit.log(...)` details (alongside
  `command`, `cwd`, `exit_code`).
- On the **timeout** branch, still save what was captured (best-effort) with
  `exit_code=None`, and include `output_id` in that audit path too. If save fails
  for any reason, swallow the error — persistence must never break the tool's
  return.

The tool's returned dict is unchanged (model still gets `stdout`/`stderr`
inline, capped at 50k).

#### 5. `ui/routes.py`

- New route `GET /api/ui/timeline/shell_output?id=<run_id>`, auth-gated via
  `_is_authorized` like its siblings. Returns:
  ```json
  {"command": "...", "cwd": "...", "exit_code": 0, "duration_ms": 12.3,
   "stdout": "...", "stderr": "...", "truncated": false}
  ```
  `404` `{"error": "..."}` when the run id is unknown / file missing.
- In `_audit_details_excerpt`, surface `output_id` in the `exec_shell` excerpt
  (add `"output_id"` to that tool's `keep` list) so the frontend knows the
  expand affordance exists. Rows without `output_id` (legacy) render as today.

#### 6. `static/ui.html`

Timeline exec_shell row, on expand:

- If `e.details_excerpt?.output_id` present and not already cached, fetch
  `/api/ui/timeline/shell_output?id=<output_id>`, cache the result on the entry
  object (mirror the existing diff-caching pattern), render:
  - `exit N` chip,
  - **stdout** box — monospace, scroll-capped height, `(empty)` when blank,
  - **stderr** box — same, only shown when non-empty,
  - a "(truncated)" note when `truncated`.
- Rows without `output_id` keep current rendering (command + excerpt only).

#### 7. Tests

- `ShellOutputStore` unit: save → read round-trip; per-stream cap + `truncated`
  flag; `_prune` honors `max_count` and `max_age_days`; orphan-file cleanup.
- `tools/shell.py`: phase-2 execute writes a file + stamps `output_id` into the
  audit details; timeout path persists best-effort with `exit_code=None`; a save
  failure does not break the tool return.
- `ui/routes.py`: `GET .../shell_output?id=<id>` returns the stored output; `404`
  on unknown id; auth gate rejects unauthorized.

### Graceful degradation

- Legacy exec_shell audit rows (no `output_id`) → Timeline shows the command as
  today, no output box. No migration.
- Store-save failure → logged + swallowed; the command result still returns.
- Missing output file (pruned but manifest race / manual delete) → endpoint
  `404`; the frontend shows "output no longer available".

### Version / release

- Touches `src/` → run `./scripts/sync-version.sh` and bump per the project's
  release flow (commit → tag → push → `gh release create`).
- Keep `haops_tools_check` in sync (exec_shell tool surface unchanged, but verify
  the check still passes after the audit-detail addition).
