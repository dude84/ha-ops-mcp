# Project notes — state & in-flight context

> Working checkpoint of where the project stands and what is in flight.
> Last updated as a migration checkpoint (see git log). Keep dev-focused;
> personal HA-instance operational detail belongs in HA, not this public repo.

## Current state

- **Released:** `v0.54.2` (HEAD on `main`). Version is synced across
  `pyproject.toml`, `config.yaml`, `src/ha_ops_mcp/__init__.py` via
  `./scripts/sync-version.sh`.
- **Working tree:** clean, fully pushed to `origin/main`. No uncommitted work.
- **Architecture / conventions:** see `CLAUDE.md` (authoritative). Release flow,
  patterns, and "what not to do" all live there. Release history in
  `CHANGELOG.md`.

### Recently shipped (this development arc)

- **`v0.54.0` — `haops_exec_shell` output persistence.** New `ShellOutputStore`
  (`src/ha_ops_mcp/safety/shell_output.py`): managed `shell_output/` dir
  (manifest + per-run `files/<id>.json`), retention prune, 1 MB/stream cap.
  Each run persists stdout/stderr + stamps `output_id` into the audit entry;
  Timeline lazy-loads and renders it inline. Timeout path persists partial
  output and bounds the drain so a grandchild holding the pipes can't hang.
- **`v0.54.1`** — store manifest↔file reconciliation: dangling entry (file gone)
  now logs a `WARNING` instead of silent soft-fail.
- **`v0.54.2`** — completes it: orphan files (blob with no manifest entry) are
  swept + warned on init and after every save. Applies to both the shell-output
  store and the capture store.

## In-flight / next up

Authoritative backlog: **`docs/BACKLOG.md`** (scoped + approved, unscheduled).
Speculative ideas: `_gaps/` (gitignored scratch — see "Outside git" below).

Active program — **UI suite** (chart de-jag = task 1, done):

- **Task 2 — standing UI perf / freeze-hunting suite.** Build the harness around
  the shipped capture primitives (`haops_ui_screenshot`, `haops_ui_perf` v0.50.0;
  `haops_ui_interact`, `haops_ui_trace` v0.51.0). Goal: catch the intermittent
  companion-app freeze + give every view a load-cost baseline. Live signal: Home
  dashboard measured **16 long-tasks / 1845 ms / CLS 0.19** — ApexCharts now the
  heaviest suspect (heavier than the old history-graphs). See
  `docs/UI_PERF_BASELINE.md`.
- **Task 3 — design-system dashboard rebuild.** Maintainability project (not
  perf). Centralize the copy-pasted tokens (room colour map, chart recipe across
  all 11 ApexCharts), de-dupe forked components, add the task-2 screenshot-diff +
  perf baselines as the visual-regression gate. Open substrate decision in
  BACKLOG (token-via-variables vs native card library vs mushroom).

Smaller queued item: cap the Timeline shell-output served body (DOM weight) —
mirror the diff lazy-load's 60 KB cap. Low priority. (Full detail in BACKLOG.)

## Outside git (will NOT migrate with a repo clone — handle manually)

These hold context this repo does not, and are not in version control:

- **`~/.claude/projects/<this-project>/memory/`** — persistent memory files +
  `MEMORY.md` index. Holds dev preferences, HA-instance operational knowledge,
  and decision history. Account/machine-local; review before switching accounts.
- **`~/.claude/projects/<this-project>/*.jsonl`** — Claude Code session
  transcripts. Not in git, not moved by a clone.
- **`config.local.yaml`** — local config incl. HA token (gitignored; not in repo).
  The live token is in the HA addon options, not here.
- **`_gaps/`** — gitignored session-scratch / gap docs by convention. Intentionally
  ephemeral; promote anything worth keeping into `docs/BACKLOG.md` first.
- **Sibling repo `~/_dev/camera_360_unwrap/`** — the FE360 fisheye dewarp tuner
  (private GitHub repo, separately versioned; clean + pushed). Not part of this
  project; clone separately if needed on the new machine.
- **Remote Frigate / live-HA dashboard work** — operational, not repo source.
  Lives on the Frigate host (`config.yml`) and HA `.storage`, not here.

## Local branches

Both local-only feature branches are **fully merged into `origin/main`** (no
unique commits) — kept as refs only, safe to delete:

- `feat/ops-tools`
- `feat/debian-playwright-ui-suite`
