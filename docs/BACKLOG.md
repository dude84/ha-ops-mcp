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

### Cap the Timeline shell-output served body (DOM weight)

`GET /api/ui/timeline/shell_output` (shipped with shell-output persistence)
serves the **full** stored output; `ShellOutputStore` caps each stream at 1 MB,
so a maximally-verbose run can push ~2 MB of text into a single Alpine
`<pre x-text>` node on row-expand (the `max-h-96 overflow` clips *painting*, not
the DOM text node). This is asymmetric with the diff lazy-load surface, which
caps the wire/DOM payload at 60 KB (`_TIMELINE_INLINE_DIFF_CAP`) and shows a
"truncated — full patch in the audit dir" footer. **Low priority** — real shell
output rarely approaches 1 MB, and `x-text` (not `x-html`) means it's only a
weight concern, not correctness/XSS. Fix when convenient: serve a ~256 KB inline
slice with a "truncated — full output persisted" note, mirroring the diff cap.

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

---

## Gaps found during the PL plug-fleet rename (2026-08-11/12)

All four surfaced in one working session that renamed ~90 entities across 22
plugs, so they're evidenced, not speculative. Ranked by how much damage they do
when absent.

### 1. `haops_entity_rename` cannot rewrite its own references — HIGH

**Approved.** Renaming a registry entry leaves every dashboard card, automation,
script and template pointing at the dead id. It bit twice in one session:

- a Power-tab chart still on `sensor.…active_power_4`
- three Climate-tab device-temperature sensors on `…maeu01_device_temperature{,_2,_3}`

Both were caught only *incidentally*, because `haops_dashboard_patch` validates
entity refs as a side effect and printed `entity_warnings`. Nothing would have
caught a stale ref in an automation.

Shape: `rewrite_references: true` (default false). Preview lists the rename **and**
every reference it would rewrite; apply does registry + `haops_batch_apply` over
dashboards/YAML as one transaction, so a partial rename is impossible. The ref
index (`haops_references` / `haops_refactor_check`) already knows the edges —
this is wiring, not new analysis. Report anything it can't rewrite (e.g. a
templated `states('sensor.x')` built by string concat) rather than silently
skipping.

### 2. `haops_registry_query` served stale cached data — HIGH (correctness bug)

Returned two Tasmota IR ghost devices that the live registry had **already
dropped**. Three removal attempts then failed with `Unknown device`, and a direct
read of `.storage/core.device_registry` confirmed they were gone. The tool caused
a false report of live devices.

Fix: invalidate the cache on any registry write this process performs, and
include cache age (or a `source: cache|fresh` field) in the response so a caller
can tell. A read tool that can be confidently wrong is worse than a slow one.

### 3. No `haops_device_remove` — MEDIUM

Deleting a device is UI-only today. On this build:

- `config_entries/device/remove` → `Unknown command`
- `tasmota/device/remove` → `Unknown command`
- `config/device_registry/remove_config_entry` → only unlinks an entry, and
  returned `Unknown device` for an already-gone device

Blocks a concrete workflow: the Tasmota→ESPHome strip swap needs the old Tasmota
device deleted to free `switch.plug_office_strip_*` for the ESPHome node.
Investigate the per-integration path (MQTT/Tasmota discovery removal vs
`config/device_registry/update` + entry unlink) before designing the tool.

### 4. No ESPHome awareness — MEDIUM

`/config/esphome/*.yaml` is only reachable through the generic config tools. There
is no first-class answer to: which node yaml maps to which HA device, is the node
online, what did the last build produce, **will the firmware fit the target's
free flash**. That last one is a live question for the 1 MB NOUS A5T
(372 KB free under full Tasmota → must go minimal-first).

Scope deliberately **excludes compiling**. Verified 2026-08-12: no `esphome` CLI
in the addon image, and the ESPHome Device Builder addon is unreachable from our
container — port 6052 unpublished (`network: {'6052/tcp': None}`), all container
hostnames refuse connections, ingress returns **401** to a Bearer LLAT (ingress
needs a frontend-minted session token), and there is no docker escape hatch
(`docker_api: False`, `protected: True`, `privileged: []`, no `/var/run/docker.sock`).
Bundling PlatformIO + the xtensa toolchain to duplicate an addon the user already
runs is not worth hundreds of MB on an already-~1.5 GB image — see
`reference_image_size_alpine_dead_end`.

So: `haops_esphome_status` = enumerate node configs, map each to its HA
device/entities and online state, and report the last build's artifacts + firmware
size from `.esphome/build/<node>/.pioenvs/<node>/firmware.bin`. Pure filesystem +
registry, no toolchain.
