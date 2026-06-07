# Approved backlog

Non-critical items that have been discussed, scoped, and approved for
implementation — just not scheduled yet. New items land here after they
survive a triage pass; truly speculative ideas stay in `_gaps/`.

When you pick one up: move it into a change plan, implement, then delete
the entry (not strike-through) on the merge commit.

---

## UI program (follows the chart de-jag, task 1 — done)

### Task 2 — standing UI performance / freeze-hunting suite

Build on the Playwright capture tools shipped in v0.50.0 (`haops_ui_screenshot`,
`haops_ui_perf`). Goal: catch the **intermittent companion-app freeze** (unknown
which screen/control) and give every view a load-cost baseline.

- **Find-the-freeze first:** load each view, synthetic scroll + tap each control,
  flag main-thread long-tasks, correlate to card type. Suspects: live-stream
  camera cards (frigate/advanced-camera), wallpanel, many-entity history-graph,
  button-card template loops — and now ApexCharts (live signal: Home is **16
  long-tasks / 1845 ms / CLS 0.19**, heavier than the old history-graphs).
- **Capture primitives all shipped** — `haops_ui_screenshot` + `haops_ui_perf`
  (v0.50.0), `haops_ui_interact` (scroll/tap/click + during-interaction jank
  capture) + `haops_ui_trace` (CDP trace) (v0.51.0). The building blocks exist;
  this task is the *harness* + analysis around them.
- **Standing suite:** per-view load-time baselines, jank/FPS, screenshot diffs, a
  results/baseline store for regression.
- Keep tools as eyes/hands only (raw metrics + image; scoring stays in the
  controller). Related: [[project_ui_suite_program]].

### Task 3 — design-system dashboard rebuild

Rebuild the dashboard look on a proper design system. Reuse the task-1 chart
recipe (ApexCharts avg-30min + smooth + CO2 bands, legends off) and the room
colour map (Office #2196F3, Bedroom #FFB300, Living #EF5350, Walkin #26A69A,
Kitchen #AB47BC, Roof #42A5F5, Open #FFA726) as design tokens. The task-2
screenshot-diff + perf baselines become the **visual-regression gate** for the
rebuild — build task 2 component-aware with this in mind.

### Capture gallery in the HA Ops sidebar

A **Captures** tab in the ingress UI to view / download / manage the PNGs that
`haops_ui_screenshot` produces — instead of the current round-trip (HA-host file
→ base64 overflow → manual decode → Downloads). Also the natural home for the
Task-2 perf-suite screenshots + regression baselines.

**Storage (refactor):** move captures out of `audit/tool-results/` into a managed
`<backup_dir>/captures/` with an append-only manifest (`captures.jsonl`) — mirror
the `BackupManager` pattern. Per-entry metadata: id, path, view/url, timestamp,
size, viewport, `nav_ms`, console-error count, optional **note**, and optional
**`transaction_id` / token** linking the shot to the change it documents (so a
before/after pair cross-links to the dashboard edit + audit entry).

**NO MCP tools — this is ha-ops-admin, not HA management.** Managing the addon's
own capture artifacts is the same class as the audit Timeline + backup views the
sidebar already owns; it touches no HA state, so it does **not** belong on the
controller-facing MCP surface. Only the *producer* stays a tool:
`haops_ui_screenshot` writes into the store. Everything else is handled directly
in the ingress UI via a `CaptureStore` service + `/api/ui/captures/*` routes.
(This is the exception side of [[feedback_sidebar_read_mostly]]: the "mirror an
MCP tool" rule is for sidebar actions that mutate *HA*; addon-internal artifact
management doesn't.) Deletes/purges are still **audit-logged** (like backup
prune) for traceability — just not exposed to the controller.

**Ingress UI (Captures tab in `static/ui.html`) + `/api/ui/captures/*`:**
thumbnail grid; click → full view; **direct download** (Content-Disposition);
**select / select-all + delete selected**; per-item editable note + link to the
referenced change; "Purge" action. Headless-verify via Playwright before release
([[reference_local_ui_screenshot]]); mind the Tailwind-CDN config order
([[reference_tailwind_cdn_config_order]]).

**Timeline integration:** when a capture is linked to a change
(`transaction_id`/token), show its thumbnail + link **inline on that audit /
Timeline entry** — so a dashboard edit's before/after is visible right where the
operation is logged. Only for linked captures (not every shot).

**Purge / retention:** configurable `captures_max_age_days` / `captures_max`,
auto-pruned on write (like backups) + manual purge — so it can't grow unbounded
on the survival volume.

Priority: medium — strong UX win, and it makes Task 2's output reviewable.
Approved 2026-06-07. Related: [[project_ui_suite_program]].

## Auth & users

### Dedicated `ha-ops-user` service account for addon auth

Instead of the addon authenticating as the owner (LLAT in `ha_token`), use a
dedicated **admin** HA user `ha-ops-user`. Same visibility (admin sees all), but
cleaner: actions/UI sessions attribute to `ha-ops-user` in the logbook (separable
from the owner); one-switch revoke; no owner-profile clutter.

- One-time manual setup (create admin user → one LLAT → `ha_token`), unless UAM
  (below) lands first and bootstraps it via the WS admin API. Addon must NOT
  self-create by editing `.storage/auth` (lockout risk).
- ⚠️ **Profile must mirror the owner** — headless UI capture renders as this
  user, so its theme / dark-light / default dashboard / locale must match the
  owner's, else screenshots show a different UI. Copy
  `.storage/frontend.user_data.<user_id>` + theme, or add a per-call theme
  override on the UI tools. Resolve before relying on it for visual work.

**Priority: LOW — deprioritized 2026-06-07.** Discussed: it's a modest
convenience, not important. The only solid win is HA-logbook attribution, which
is partly redundant with the addon's own `operations.jsonl` audit; "security /
least-privilege" is illusory (addon is already root-on-HA via exec_shell +
config:rw, regardless of which HA user the token names); and naive adoption
**degrades UI capture** (screenshots render as this user → must mirror the
owner's theme/locale/default-dashboard forever). If ever revisited, do the
**split**: `ha_token` = `ha-ops-user` for actions, pass the **owner** LLAT to the
UI tools via their `access_token` param (add `ui.access_token` defaulting to
`ha.token`) — clean attribution without the profile-mirror tax. Not naive #2.
Approved-but-parked. Related: [[project_ui_suite_program]].

_(Native user-account-management — `haops_user_*` — **shipped v0.51.0**. The
`ha-ops-user` bootstrap it enabled is now mechanically possible; revisit only if
that account is ever pursued.)_
