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

**This is a maintainability/consistency project, NOT a perf one** (the row
controls are not a measured bottleneck — see `docs/UI_PERF_BASELINE.md`; the perf
lever is the ApexCharts, separate work).

**Investigation 2026-06-08 — `new-dashboard` already ~30% a proto-DS:**
- Component layer = decluttering templates: `ac_room_row` (×11), `ac_mini_card`
  (×4) **+ forked `ac_mini_card_livingroom` variant** (×1), `plug_control` (×4).
  Informal but real reusable components.
- Card vocabulary: 7 custom types — button-card (~50), decluttering (~25),
  apexcharts (11), advanced-camera (11), mini-climate (2), scheduler, weather-radar
  — mixed visual languages.
- De-facto tokens copy-pasted, not centralized: room colour map hardcoded in all
  11 ApexCharts series; chart recipe duplicated across all 11; spacing/typography
  inline via button-card `styles` + `grid-template-*`.

**What the rebuild does:** (1) centralize tokens (colours/spacing/type/chart
recipe) — today a colour change = editing 11 charts; (2) de-dupe forked
components (parameterize, don't fork); (3) unify card chrome; (4) kill JS-in-JSON
button-card templates; (5) add the visual-regression gate.

**Open decisions:** substrate — formalize decluttering+button-card with
token-via-variables (cheapest) vs **native card library** (cleanest; the room row
+ chart wrapper as Lit components — note: no off-the-shelf HACS card cleanly does
the multi-entity + column-aligned room row, flex-table is one-entity-per-row,
multiple-entity-row is 2yr stale; source-checked 2026-06-08) vs migrate to
mushroom (maintained, themable). Also: tokens as HA theme CSS-vars? Scope:
`new-dashboard` only or also the older `lovelace` overview (17 views)?
Increment (tokenize-in-place) vs big-bang.

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
