# Approved backlog

Non-critical items that have been discussed, scoped, and approved for
implementation — just not scheduled yet. New items land here after they
survive a triage pass; truly speculative ideas stay in `_gaps/`.

When you pick one up: move it into a change plan, implement, then delete
the entry (not strike-through) on the merge commit.

---

## HA 2026.8.3 — confirm compatibility alignment

**Filed 2026-08-22.** Both instances (SG and PL) were upgraded to HA Core
**2026.8.3** while we were built and verified against **2026.8.2**. Nothing is
known to be broken; this is the standing post-upgrade routine from
`CLAUDE.md`, which is the agent's job, not the user's.

No startup warning fired and `in_window` reports `true` — but only because
`MAX_TESTED_HA` is minor-granular (`2026.8`), so a patch bump can't fall out
of the window. That is exactly the case where a silent break hides: the
2026.8 device-registry storage split (v3.2) broke us while the API kept a
deprecated shim and every API-level test stayed green. Do not treat
`in_window: true` as evidence of anything.

**Verify on a settled instance.** Both were still booting when this was filed
— SG read `automation_count: 0` and 1604 entities (against 50 / 1969 before
the upgrade), PL 924 (against 1094). Those numbers are integrations still
loading, not losses, but `haops_tools_check` and any count comparison will
lie until they settle. Re-read `haops_system_info` first and only proceed
when the counts are back to roughly pre-upgrade levels.

Steps:

1. `haops_tools_check` on **both** instances. That single call is the
   verification — 16 read-only groups covering every backend.
2. Diff HA's 2026.8.3 release notes against the **API-surface inventory** in
   `docs/HA_COMPATIBILITY.md` — not against the breaking-change list whole,
   which is overwhelmingly integration-level noise. Pay attention to
   anything touching `.storage` schemas, the WS command set, or the recorder
   schema (currently 53).
3. On `all_pass` (16/16): bump `BUILT_AGAINST_HA` to `2026.8.3` in
   `src/ha_ops_mcp/compat.py` **and** `docs/HA_COMPATIBILITY.md` together —
   `tests/test_compat.py` fails if they drift. Add a verification-history
   row. Also update the compatibility tables in `README.md` and `DOCS.md`;
   they are user-facing and go stale silently. `MAX_TESTED_HA` needs no
   change for a patch release.
4. Append the pending **`KNOWN_GOOD_ENV.md` baseline row** in the same pass —
   it is owed for v0.64.1 anyway (v0.64.0 never got one: its `config_flow`
   check group was broken, so no instance ever reached `all_pass` on it).
   Gather the full stack per that file's convention: `haops_system_info`,
   `haops_self_check`, `claude --version`, `sw_vers`, `bun --version`,
   `node --version`, `$TERM_PROGRAM_VERSION`, `git describe --tags`.

Blocked on: both addons being updated to **v0.64.1** first, otherwise the
`config_flow` group still reports the 405 false failure and `all_pass` is
unreachable. Related: [[project_ha_compat_window]],
[[reference_ha_storage_schema_vs_api_shim]], [[feedback_env_baseline_routine]].

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

## Zigbee radio ops (first-class tools for coordinator-level work)

Moved here 2026-08-22 from `_gaps/session_gaps_2026-06-05.md`, where it sat as
a trailing FEATURE note. Not a gap — the user explicitly parked it ("wants this
in the haops implementation stream, NOT now"), so it is approved-in-principle
work awaiting a scope decision.

Three radio-level ops that ZHA and zha_toolkit do not cover for a **TI CC2652 /
zigpy-znp** coordinator, all done by hand in the 2026-06-05 session via
`exec_shell` plus a pip-installed `zigpy-znp` — **which is wiped on every addon
rebuild**, so none of it survives an update:

- coordinator firmware flash (`cc2538-bsl`) — kit vendored at
  `/config/zigbee_fw_flash/`
- energy scan (`zigpy_znp.tools.energy_scan`) — channel selection
- **channel change** (`ControllerApplication.move_network_to_channel`) —
  zha_toolkit's only channel service is `ezsp_set_channel`, which is
  EZSP/Silabs **only** and useless on znp

Proposed shape: `haops_zigbee_energy_scan`, `haops_zigbee_change_channel(target)`,
`haops_zigbee_network_backup` / `_restore`, each orchestrating the existing
`haops_system_core` stop → op → start (+ watchdog) flow.

**Open questions to resolve before building:**

- **Optional dependency, size-gated.** `zigpy-znp` pulls `zigpy` plus a few
  pure-Python deps (modest, no heavy C), but it must be an extra rather than a
  base-image dependency, and only after the image-size cost is measured. The
  image is already ~1.5 GB.
- **Radio-specific.** znp assumes TI; a Silabs stick needs `bellows`. Either
  abstract the radio layer or declare the assumption and pick the library at
  build/config time.
- **Scope call pending** — how much radio management belongs in an *ops* MCP
  server at all, versus staying as the `/config/zigbee_fw_flash/` kit.

**Two hard-won facts to keep** (both cost a wasted attempt in 2026-06-05):

- A standalone `move_network_to_channel(25)` **does** retune a running znp
  coordinator. The failure mode is *persistence*: the last line of that
  function is `await self.backups.create_backup()`, so an app with no
  `database_path` writes the new-channel backup to a throwaway store and ZHA
  reverts to its old `network_backups_v15` row on next start. Point
  `database_path` at `<config>/zigbee.db` and it persists. This is **not** a
  "needs a live-app tool" problem — that was a misdiagnosis.
- `zigpy.config` double-validation: pass a **raw dict** to
  `ControllerApplication.new(...)`. Pre-running `.SCHEMA()` makes the
  OTA-provider validator throw `'ZigpyOtaProvider' object has no attribute
  'get'` on the second pass.

Related: [[project_zigbee_coordinator_flash]], [[project_zigbee_channel_migration]].

---

