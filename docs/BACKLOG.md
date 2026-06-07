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
- **Needs `haops_ui_interact`** (scroll/tap, capture jank/long-tasks/console
  errors) — not yet built; the screenshot + perf primitives exist.
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

### Native user account management (UAM)

First-class **create / update / delete / disable** HA-user tools via the admin
**WebSocket API** (no `.storage/auth` edits, no restart): `config/auth/*`
(`update.is_active` = disable/enable), `config/auth_provider/homeassistant/*` for
credentials. Reachable today via `haops_ws_command`; promote to audited,
two-phase tools:

- `haops_user_list` (read)
- `haops_user_create` (mutate, two-phase) — name, admin, local_only, optional password
- `haops_user_update` (mutate, two-phase) — rename / group / active (disable)
- `haops_user_delete` (mutate, two-phase) — back up the auth entry first (irreversible)

Bonus: lets the addon bootstrap `ha-ops-user` (create + password + mirror
profile) from a one-time owner token, closing the chicken-egg above.
Approved 2026-06-06.

## Safety / tooling

### `dashboard_apply` stale-token drift guard

`haops_dashboard_apply` overwrites with the token's stored `new_config` without
re-checking the live dashboard, so a stale token (target edited since preview)
silently clobbers the intervening change. Guard: on apply, re-fetch current
config and compare to the token's `old_config`; refuse (or warn + require
re-preview) on drift. See docs/HA_QUIRKS.md → "Confirmation tokens are NOT
auto-invalidated". Approved 2026-06-07.
