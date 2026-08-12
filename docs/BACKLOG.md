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

Compiling **in our own image** stays out of scope: bundling PlatformIO + the
xtensa toolchain to duplicate an addon the user already runs is not worth hundreds
of MB on an already-~1.5 GB image (see `reference_image_size_alpine_dead_end`).
Verified 2026-08-12 that the builder addon is also unreachable over the network:
no `esphome` CLI in our image, port 6052 unpublished (`network: {'6052/tcp': None}`),
all container hostnames refuse connections, and ingress returns **401** to a Bearer
LLAT (it wants a frontend-minted session token).

**UNBLOCKED 2026-08-12 (gap 5 shipped in v0.57.0).** Compiling is now reachable by
`docker exec` into the ESPHome container — borrow the toolchain instead of shipping
it — so `haops_esphome_build` is viable as a thin wrapper over
`haops_container_exec` (find the esphome container, `esphome compile <node>.yaml`,
report the artifact path + firmware size). Gated on the same Protection-mode
opt-in, and on confirming Supervisor actually permits exec.

So: `haops_esphome_status` = enumerate node configs, map each to its HA
device/entities and online state, and report the last build's artifacts + firmware
size from `.esphome/build/<node>/.pioenvs/<node>/firmware.bin`. Pure filesystem +
registry, no toolchain.

### 5. Addon never got the Docker escalation path it was designed for — ✅ DONE in v0.57.0

**Shipped 2026-08-12.** `docker_api: true` is in the manifest and
`haops_container_list` / `_logs` / `_exec` drive the Engine API over the unix
socket (no docker CLI in the image, aiohttp `UnixConnector`, API pinned to
`v1.41`). Two-phase confirm on exec with the token bound to **both** command and
container; `tools_check` gained a `docker` group that reports `skip` when the
socket is absent so `all_pass` stays reachable for non-opted-in installs.
**VERIFIED LIVE 2026-08-12** on PL (HA 2026.8.1, Supervisor 2026.07.5, Docker
29.6.2): socket at `/run/docker.sock`, 19/19 containers listed, `exec` returning
`exit_code: 0` against the ESPHome add-on (ESPHome 2026.7.4 / Python 3.14.6).
`haops_tools_check` → `all_pass` 14/14. **`docker_api` is NOT read-only** —
`read_only=True` is a bind-mount flag on the socket inode, not an API
restriction, so `full_access: true` was never needed. Gotcha worth remembering:
Supervisor evaluates the mount at *container creation*, so turning Protection
mode off does nothing until the add-on is restarted. Details in
HA_COMPATIBILITY.md. Docker access is also surfaced in `haops_self_check`
(reports `skip` when not enabled) and therefore in the sidebar Health tab. Original entry below for context.

**Regression against intent, found 2026-08-12.** The design assumed
`haops_exec_shell` could escalate to other containers via `docker exec` (e.g. to
borrow the ESPHome toolchain, inspect the recorder, or debug another addon).
It cannot, and not by policy — the capability is simply **absent from the
manifest**. `config.yaml` declares `map`, `hassio_role: manager`,
`host_network: false` and nothing else; Supervisor accordingly reports
`docker_api: False`, `full_access: False`, `privileged: []`, and there is no
`/var/run/docker.sock` in the container.

To enable:

1. Add `docker_api: true` to `config.yaml`.
2. The user must then switch **Protection mode OFF** on the addon — Supervisor
   strips `docker_api`/`full_access`/`privileged` while protection is on, so the
   manifest change alone does nothing.
3. No docker CLI needed in the image: the socket can be driven directly, e.g.
   `curl --unix-socket /var/run/docker.sock -X POST http://localhost/containers/<id>/exec`.
   Adding the CLI is optional sugar.

Verify before promising `exec`: HA's docs describe `docker_api` as *read-only*
access, yet Portainer-class addons clearly start/stop/exec with it. Confirm which
operations Supervisor actually permits before designing the tool — if exec is
genuinely blocked, the fallback is `full_access: true`, which is a much bigger
hammer.

**State the security trade-off explicitly when proposing this to the user:**
protection-off + docker_api means the addon can reach every container on the
host. The addon is already effectively root-on-HA (`config:rw` + `exec_shell`), so
this widens blast radius from "all of HA" to "all containers" — a real step, worth
one deliberate decision rather than a silent manifest bump.

Natural follow-on once granted: a first-class `haops_container_exec`
(list containers, exec a command, stream output) so callers stop hand-rolling
socket curls in `exec_shell`.
