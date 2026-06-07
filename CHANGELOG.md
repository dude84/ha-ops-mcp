## 0.53.3

**Captures are human-viewed by default; model-vision is opt-in.** Pushing image bytes into the model's context costs tokens (vision tokens + the client's base64 text-echo). Routine "show me this view" shouldn't pay that — the ingress gallery already serves the image in your browser for free.

- **Captures-tab deep-link.** `…/ui#capture=<id>` opens the Captures tab and auto-opens that capture in the lightbox. The newest capture is always card #1, so "just took it" needs no link at all; the deep-link is for pointing at a specific older capture.
- **`haops_ui_screenshot` view_hint reworded** to lead with the browser path (0 model tokens), with `haops_capture_show` flagged as the token-costing fallback.
- **`haops_capture_show` description reframed** as "only when the model must analyze the pixels" (visual-regression / chart-jaggedness checks), not the default viewer.

No behavior change to capture/render; this is about *who looks* and the token cost of each path.

## 0.53.2

**`haops_capture_show` payload trimmed further.** The MCP client renders the returned image *and* echoes its bytes as a text block; at JPEG q82 / 900px even that echo could exceed the client's text-output cap. Lowered the defaults to **768px / quality 70** — still legible for layout/value checks, with a meaningfully smaller payload. `max_px` remains overridable for more detail.

## 0.53.1

**`haops_capture_show` now returns JPEG, not PNG.** The MCP client echoes the returned image bytes as text as well as rendering them; a full-page dashboard PNG (multi-hundred-KB) blew that text budget. `capture_show` now downscales to **JPEG (q82, default long-edge 900px)** — a fraction of the size, still perfectly legible for visual review. Raise `max_px` for more detail.

## 0.53.0

**See captures without the base64/shell dance.** Viewing a screenshot used to mean either an inline base64 that blew the response token cap, or shelling into the host with `haops_exec_shell` (which also littered the audit Timeline with shell mutations). Both gone.

- **New `haops_capture_show(capture_id, max_px=1100)`** — returns a stored screenshot as a **native MCP image** content block, downscaled server-side (Pillow) to fit the budget. The read-only, one-call way for the assistant to *see* a capture. Errors cleanly on an unknown id or a trace zip (open those in the Playwright trace viewer). Classified `read/captures`; added to the `haops_tools_check` ui group (with a `pillow_available` probe).
- **`haops_ui_screenshot` no longer inlines base64 by default.** It returns `capture_id` + `saved_path` + metadata (tiny) and a `view_hint` pointing at `haops_capture_show`. Pass `inline=true` to force the old inline behavior (still capped — over-cap images point you at `capture_show` regardless).
- New dep: **Pillow** (manylinux/Debian wheel) for the downscaler.

Tool count 77 → **78**. 694 tests green, ruff + mypy --strict clean.

## 0.52.3

**Captures show the actual console errors, not just a count.** "1 err" told you nothing actionable. Captures now store the real console-error **messages** (`CaptureEntry.errors`, capped 20), and the Captures-tab "N err" badge is a toggle that expands the message list inline. The count (`console_errors`) is still kept as the quick badge. Captures taken before this version show a "count only — predates message capture" note when expanded.

## 0.52.2

**Sourcemap-noise fix, take 2.** v0.52.1 aborted the source-map / `/node_modules/` requests to kill the 404 — but an *aborted* request itself logs `net::ERR_FAILED` to the console, so the capture error-count still read "1 err", just for a different phantom. The driver now **fulfills those requests with an empty 204** instead of aborting, so nothing is logged and the console-error count reflects real errors only.

## 0.52.1

**Screenshot defaults + console-noise fix.** Tuning after the v0.52.0 shakedown.

- **Default viewport is now 16:10 desktop (1280×800)** instead of 1280×2400. The old tall default padded short views with a big black band under `full_page`; `full_page` still grows the capture to the real content height, so nothing is cut — short views just stop at their content instead of a fixed 2400 px. Applies to all `haops_ui_*` tools; override per-call with `viewport_width`/`viewport_height`.
- **Mobile preset.** All `haops_ui_*` tools gained a `device` param. `device="mobile"` (aliases `iphone`/`phone`) renders at iPhone-17-Pro-class **402×874 @3× DPR with touch**, so captures match the single-column mobile layout the HA companion app shows. Default `""` = desktop. A preset overrides the viewport for that call.
- **Source-map 404s muted.** Headless Chromium resolved `//# sourceMappingURL` references shipped in some HACS card bundles (e.g. `@webcomponents/scoped-custom-element-registry` → a `.ts` under `/node_modules/`); those sources aren't published, so every capture logged one harmless 404 and the console-error count read "1 err" on every shot. The capture driver now aborts `**/node_modules/**` and `**/*.map` requests (served card JS lives under `/frontend_latest/` and `/hacsfiles/`, never `/node_modules/`), so the error count reflects real errors only.

## 0.52.0

**Capture gallery in the sidebar.** The screenshots and traces produced by `haops_ui_screenshot` / `haops_ui_trace` now have a home: a new **Captures** tab in the HA Ops ingress UI to view, download, annotate, select/delete, and prune them. No new MCP tools — capture artifacts are ha-ops-admin storage (the addon's own files), not Home Assistant state, so management lives in the ingress UI, not behind MCP.

- **`CaptureStore`** (`safety/captures.py`) — manifest+files store mirroring `BackupManager`'s append-only-jsonl + atomic-rewrite + retention pattern. Lives under `<backup.dir>/captures` by default (override `captures.dir` / `HA_OPS_CAPTURES_DIR`); retention is newest-`max_count` (200) + `max_age_days` (30), enforced on init and after every save. Adds the gallery operations backups don't have: read-bytes (download), delete-by-id, annotate (note / change-link), lookup-by-transaction.
- **Captures tab** — thumbnail grid (screenshots inline, traces as download cards) with per-item metadata (view, timestamp, size, nav ms, console-error count, change-link), editable notes, click-to-zoom lightbox, multi-select + bulk delete, and two-phase **Prune / Clear all** mirroring the Backups tab. New ingress endpoints: `GET /api/ui/captures`, `GET /api/ui/captures/{id}` (inline/download), `POST /api/ui/captures_delete|captures_annotate|captures_prune`. Mutations are audit-logged with `source: "sidebar"`.
- **Timeline ↔ capture link.** `haops_ui_screenshot` / `haops_ui_trace` gained `note` + `transaction_id` params; pass a change's transaction_id and the capture is tied to it. The Timeline then renders a thumbnail (or trace-download link) on the matching change row — visual before/after right in the audit feed.

No new MCP tools (still 77). Full suite green (added `tests/test_captures.py` + capture-route tests in `tests/test_ui_routes.py`); UI verified headless (Captures tab + lightbox, light/dark, zero console errors).

## 0.51.0

**Drift guard + headless interaction/trace + native user management.** Three independent additions (71 → 77 tools).

- **`haops_dashboard_apply` stale-token drift guard.** Apply now re-fetches the live dashboard and compares it to the token's `old_config`; if it changed since the preview, the apply **refuses** (doesn't save, doesn't consume the token) instead of silently clobbering the newer edit — re-preview to proceed. (Proceeds if the re-fetch returns nothing, so a transient read doesn't block a legit apply.) See HA_QUIRKS → "Confirmation tokens are NOT auto-invalidated".
- **`haops_ui_interact`** — drive a Lovelace view through scroll / click / tap actions (default: a full-page scroll sweep) and capture the main-thread long-tasks + console errors that occur *during* interaction. The core of the UI freeze-hunting work. Read-only/diagnostic.
- **`haops_ui_trace`** — record a Playwright CDP performance-trace (zip) of a view load, saved under the tool-results dir, for deep profiling. Read-only.
- **Native user management** via the HA admin WebSocket API (`config/auth/*`) — no `.storage/auth` editing, no restart:
  - `haops_user_list` (read)
  - `haops_user_create` (two-phase) — name, admin, local_only, optional password
  - `haops_user_update` (two-phase) — rename / group / `is_active` (disable–enable)
  - `haops_user_delete` (two-phase, destructive) — refuses owners; backs up the auth entry first
  New `user` op-area; `haops_tools_check` gains a `user` group.

Built in parallel by subagents, integrated + committed per-feature. Full suite 655 green; Debian image smoke green.

## 0.50.0

**Debian base + server-side Playwright UI suite.** Major shift: the addon image moves off Alpine to Debian so it can run Playwright + Chromium (Alpine/musl is unsupported by Playwright), unlocking server-side dashboard screenshots and load-performance capture. The version jump to 0.50.0 marks the base-image change.

- **Debian base swap.** `Dockerfile` → `ghcr.io/home-assistant/{arch}-base-debian:trixie` (Python 3.13 via apt), `build.yaml` updated. Wheels-only install (`--only-binary=:all:`, no compiler toolchain); **Chromium-headless-shell** (not full Chromium) via `playwright install --with-deps`. The browser layer is installed *before* the project source so code changes don't re-download Chromium. Image ≈ 1.5 GB. ⚠️ The Supervisor rebuilds this **on the HA host** — first build pulls the base + browser (several minutes; slower on low-power hosts).
- **`haops_ui_screenshot`** — render a Lovelace view headless and return a PNG (saved path + base64) + capture metadata. For visual checks / before-after comparisons.
- **`haops_ui_perf`** — load-performance metrics for a view (nav timing, FCP/LCP, CLS, long-tasks, JS heap, shadow-DOM-aware DOM/card counts), raw and unscored. Both are read-only and require a Home Assistant **long-lived access token** in `ha_token` (the frontend session; Supervisor tokens aren't accepted by the frontend). Auth is injected via `localStorage['hassTokens']`.
- **`scripts/smoke.sh`** — in-image runtime/contract test (every tool module imports, DB drivers import, real Chromium launch). Catches base-image-swap breakage the mock-based pytest can't. Gate for the swap.
- **`dev-deploy.sh --ref <branch|tag|sha>`** — deploy a feature branch's working tree as the local addon without cutting a public release: PEP 440-valid dev version (`<base>.dev<YYYYMMDDHHMMSS>`), `ha store reload && ha apps update` so the shown version tracks the deployed code.
- **Docs.** HA_QUIRKS: confirmation-token staleness semantics; `custom:apexcharts-card` rejects `layout_options` ("Configuration error" — wrap in a stack for full-width in sections). BACKLOG: dedicated `ha-ops-user` service account; native user-account-management feature.

New tests: UI capture tools, supervisor-token regression, oauth migration. Full suite 624 green; Debian image smoke green (real Chromium launch).

## 0.40.1

**Supervisor API auth fix — use `SUPERVISOR_TOKEN`, not the configured HA token.** Every `http://supervisor/*` call (`haops_addon_list/info/logs/restart`, `haops_system_core`, core-log fetch, the tools_check supervisor probe) authenticated with `ctx.config.ha.resolve_token()`. That only worked because `run.sh` aliases an **empty** `ha_token` to `SUPERVISOR_TOKEN`. The moment a real HA **long-lived access token** is set in `ha_token` (e.g. to give a tool a frontend session, or for a dedicated service user), those calls sent a *Core user token* to the Supervisor → **HTTP 403** ("supervisor_info HTTP 403", addon tools + core power dead). New `addon._supervisor_token()` prefers the `SUPERVISOR_TOKEN` env var (always present in the addon) and falls back to the configured token only for non-addon/dev runs. Applied across `addon.py`, `utils/logs.py`, and the tools_check probe. + regression test. Found on a live deploy that set a custom token; everything else (HA Core REST/WS/DB/filesystem) was unaffected.

## 0.40.0

**OAuth store moved to a survival volume + a dev-deploy branch mode.** Groundwork so a future base-image migration (and any addon reinstall/slug-change) doesn't cost data or client re-auth.

- **OAuth store now defaults under `<backup_dir>/auth/` (i.e. `/backup/ha-ops-mcp/auth/oauth.json`), not `/data`.** `/data` is the addon's private volume — the Supervisor **wipes it on uninstall or a slug change**, so the old default meant every MCP client had to re-register (re-auth) after a reinstall. `/backup` is a mapped volume that survives uninstall *and* isn't swept into HA snapshots, so tokens stay both durable and out of backups. `auth.data_dir` resolution mirrors the existing `audit.dir` pattern (empty config value → derived from `backup.dir`); set `auth.data_dir` explicitly to override. (The audit log already lived under `backup_dir` and already survived — this brings OAuth in line.)
- **One-time legacy migration.** On startup, if the new location has no store but a legacy `/data/oauth.json` exists, it's copied over once (logged). Existing clients keep their registrations/tokens seamlessly across the update — no re-auth. The `clear_oauth_on_next_boot` wipe in `run.sh` was repointed to the new path (kept in sync with the Python derive).
- **`dev-deploy.sh --ref <branch|tag|sha>` (dev mode).** Deploys the working tree against a feature branch without cutting a public release: skips the strict "HEAD == latest tag" guard and stamps a **unique** dev version (`<base>-dev.<sha>.<time>`) so the Supervisor always rebuilds. For iterating on a branch (e.g. the upcoming Debian base swap) against a local addon while `main` stays the shippable build.

New tests: OAuth migration (copy-when-absent / noop-when-present / noop-when-no-legacy), `auth.data_dir` default. Full suite 615 green.

## 0.39.3

**`haops_system_core` reported `success: false` on a restart that actually worked.** Live test: `haops_system_core(restart)` bounced Core correctly (verified — REST/WS went 502 while down, then HA 2026.6.0 came back clean) but the tool returned failure. Root cause: Supervisor's `/core/{restart,stop,start}` endpoints **block until the operation completes**, so the POST exceeds `_supervisor_post`'s 30s timeout → `asyncio.TimeoutError` (whose `str()` is empty) → `{"error": "Supervisor API unavailable: "}`. The tool treated any error as failure; `haops_system_restart` already treats a drop/timeout as "initiated" — `system_core` now does the same. A timeout/connection-drop reads as `status: "initiated"` (success); only a real HTTP-status error (e.g. `HTTP 401/403` — auth/permission) is reported as `status: "failed"`. New `_core_post_outcome` classifier + 6 tests (restart-timeout-initiated, http-error-failure, stop-disables-watchdog-first, etc.). Full suite 612 green.

## 0.39.2

**`haops_zha_reconfigure_device` now resolves a device by friendly name.** Previously it accepted only ieee / device_id / entity_id — but `haops_zigbee_info` *returns* friendly names, so the natural round-trip (read a name, reconfigure it) failed with "could not resolve". Now matches the device's friendly name (`name_by_user`/`name`) case-insensitively. An ambiguous name (>1 matching device) deliberately does **not** resolve, so a reconfigure never fires at a guessed device. ieee / device_id / entity_id paths unchanged.

## 0.39.1

**Fixes for two v0.39.0 Zigbee tool bugs found on first live use** (both root-caused against the live instance, not guessed).

- **`haops_zigbee_info` + `haops_zha_reconfigure_device` — `too many values to unpack (expected 2)`.** Both share `_zha_ieee_map`, which did `for kind, val in dev.get("identifiers")`. The HomeKit integration stores **3-element** identifiers (`["homekit", "<id>", "homekit.bridge"]`), so the strict 2-tuple unpack threw the moment it walked past the HomeKit bridge — taking down both tools for the entire registry on every call. Now indexes defensively (`len(el) >= 2`, `el[0]`/`el[1]`) over both `connections` and `identifiers`. Verified on the live registry (238 devices, incl. the offending HomeKit bridge): 28 ZHA devices mapped, coordinator firmware `CodeRevision 20250321` surfaced, no crash.
- **`haops_zigbee_scan` — false "Topology scan request failed: Timeout".** `zha/topology/update` is the correct command, but it's long-running: HA only returns the WS result after the whole mesh has been walked (verified live: no reply in 15s, and no error frame — an unknown command would error immediately). The tool awaited the result with the default 30s timeout and reported failure even though the scan had started. Now fires with a short timeout and treats the timeout as `status: "initiated"` (the scan runs server-side and refreshes `zigbee.db` regardless); a genuine fast error still surfaces.

New regression tests for both (the HomeKit 3-tuple mapper case and all three scan outcomes). Full suite 604 green.

## 0.39.0

**Zigbee/ZHA tooling + closing the 2026-06-05 shell-gap inventory.** A long coordinator-flash session had to drop to `haops_exec_shell` repeatedly because no first-class tool existed (enable an entity, read the zigpy mesh, stop/start Core, reconfigure a ZHA device). Each shell reach was a tool gap; this release closes them.

- **`haops_entity_disable` → `haops_entity_toggle` (BREAKING rename).** The tool is now symmetric: `enable=true` flips a disabled entity back on (`disabled_by` → `null`), `enable=false` (default) disables. The old name is **removed**, not aliased — downstream callers will see it no longer exists. Also **fixes a latent bug**: `entity_disable` recorded a `UndoType.ENTITY` savepoint, but `_execute_undo` had no `ENTITY` branch, so rolling back a disable silently failed ("unsupported undo type"). Rollback of toggles now works both directions. Note: enabling/disabling a ZHA entity reloads the ZHA config entry (~30s) and can wedge some devices (e.g. Aqara FP1) until a reconfigure.
- **`haops_zigbee_info` (read-only).** Coordinator firmware/metadata + per-device `last_seen`, LQI-at-coordinator, parent/relationship, and a stale-device list — read straight from `<config_root>/zigbee.db` (the zigpy SQLite DB, separate from the recorder) plus `core.device_registry` for names. The `_v<N>` table suffix is discovered at runtime (it shifts across zigpy releases). Replaces ~6 raw `sqlite3` shell round-trips per session.
- **`haops_zigbee_scan`.** Forces a fresh zigpy topology/neighbor scan so LQI/route data isn't the hours-old snapshot zigpy keeps by default. Re-read `haops_zigbee_info` after ~30-60s.
- **`haops_zha_reconfigure_device`.** Two-phase ZHA "Reconfigure device" (re-interview + re-establish report bindings) by ieee, device_id, or any entity_id. This is the only thing that recovers a device wedged by a ZHA reload (coordinator flash, entity toggle) — an on-device reset button does not.
- **`haops_system_core(stop|start|restart)`.** Supervisor-driven Core power control. Unlike `haops_system_restart` (which asks a *running* HA to restart itself), this can fully **stop** Core — needed to free a resource HA holds, e.g. a serial port for in-place Zigbee coordinator flashing. `stop` disables the Supervisor watchdog first (else it revives Core in seconds); `start` re-enables it. HA OS / Supervised only.
- **`haops_monitor_entity`.** Polls one entity (or a named attribute) live for a fixed window and returns the time series plus stats (count, change count, distinct values, numeric min/max/mean/stdev). For averaging a noisy reading before deciding (e.g. jittery Zigbee LQI) or watching a value settle. Fully configurable duration, hard ceiling 600s; long windows can exceed the MCP client request timeout (documented in-tool). For hours/days use `haops_entity_history` (DB-backed) instead.
- **`haops_ws_command` (escape hatch).** Generic Home Assistant WebSocket passthrough for the long tail of WS-only admin commands (registry updates, `zha/*`, `lovelace/*`, diagnostics) with no first-class tool. **Deliberately no type allowlist** — this is a power-user tool and `haops_exec_shell` already exists, so an allowlist would be theatre, not a boundary. Read-shaped command types execute immediately; everything else is two-phase for audit + an explicit apply gate (not a security guarantee — read the preview).

All new tools are audit-logged and wired into `haops_tools_check` (new `zigbee` group). New tests for every tool; full suite 600 green.

## 0.38.0

**Hardware access — USB/serial device passthrough.** The addon now requests generic USB access (`usb: true`) plus auto-mapped UART nodes (`uart: true`) in `config.yaml`. Previously the container could *see* `/dev/ttyUSB*` but the cgroup device filter denied `open()` in every mode (`EPERM`), so any tool that needed raw serial — notably flashing the Zigbee coordinator in place — was blocked.

- **Why:** in-place Zigbee coordinator firmware flashing (Sonoff ZBDongle-P / CC2652P) via `haops_exec_shell` + `cc2538-bsl`, without unplugging the dongle to a laptop. The same grant covers any other USB peripheral a tool may need.
- **Scope note:** this is a deliberate capability expansion. Combined with `haops_exec_shell`, the addon can now read and write any USB/serial device the host exposes. It sits behind the same shell-access trust boundary already documented under Safety — treat the addon like SSH-to-production.
- **Requires a rebuild** to take effect (Supervisor → addon → Update / Rebuild). The device cgroup is applied at container creation, not at runtime.

## 0.37.0

**Design System v1.1 brand mark + Timeline/Health polish.**

- **New brand mark** (DS v1.1 — robot hand gripping a cone drill bit, cyan/indigo/green on navy). Addon store icons swapped: `icon.png` → 256² mark, `logo.png` → the wide `ha-ops` wordmark lockup. The sideload header now shows the inlined mark beside "HA Ops", and the page favicon is the real mark (inline SVG data-URI — the server serves only `/ui`, so brand assets are inlined, not `<link>`ed).
- **Removed the "Show reads / Mutations only" filter chip** from the Timeline — it was clutter. The Timeline shows mutations + destructive operations; read-only calls are still logged to `activity.jsonl` for forensics, just not surfaced in the UI. (The backend `classes` query param is retained.)
- **Health → Tools check:** per-test `ok`/`fail` badges now right-align in a column under the group `pass`/`partial` badge, instead of leading each row — matches the block convention and reads far cleaner.

## 0.36.5

**Timeline rows: move the timestamp to the right.** The date led every row and crowded the identity. It's now right-aligned and faint, so each row reads left-to-right as area-icon + tool + type, with the timestamp parked on the right edge. Left content wraps independently of the date (verified desktop + 390px mobile).

## 0.36.4

**Simplify Timeline summaries — drop redundant verb prefixes.** The `type` tag already names the action, so the summary line now shows just the payload: `service_call` → `recorder.purge_entities` (was "Called …"), `db_execute` → the raw statement (was "Executed SQL: …"), `exec_shell` → the raw command (was "Ran shell: …"). The `(no summary)` fallback was already retired in v0.36.2.

## 0.36.3

**Timeline row redesign.** Reworked how each row reads, per operator feedback.

- **Removed** the intro paragraph and the op-class legend / "what do the tags mean?" toggle — the row should speak for itself.
- **New row order:** date · area-icon + area name · tool · **type** · *(error only on failure)*. The area icon now leads instead of trailing where it was lost.
- **Operation-specific `type`** replaces the one-size `READ`/`EDIT`/`DELETE` pill. It now reads the actual action — `service call`, `db delete`, `db write`, `patch`, `new file`, `remove`, `delete helper`, `list`, … — at the same text size as the rest of the row, with a small risk-colored dot (neutral / amber / red) carrying the read/mutate/destructive tier. `service_call` is no longer mislabeled "EDIT".
- **Dropped the `ok` badge** — success is the default; only failures show an `error` tag.

New `type_label()` in `safety/classification.py` (db-verb + config create/patch aware) drives the label; surfaced as `type` on each Timeline entry. Verified headless in both themes. 583 tests pass.

## 0.36.2

**Fix: header was not responsive — the theme toggle fell off-screen on mobile.** On a phone-width viewport the single-row header overflowed, pushing the theme-cycle button past the right edge (measured x≈463 on a 390px screen) so it was untappable — which read as "the toggle doesn't work." The wordmark also wrapped and "Last refreshed" clipped.

Header is now `flex-wrap` with the right-hand group pinned (`ml-auto shrink-0`) so the theme toggle stays on-screen; version (`hidden sm:`) and last-refreshed (`hidden md:`) are progressively hidden on narrow screens; wordmark/tabs `whitespace-nowrap`; page padding tightens to `p-4` under `sm`. Verified headless at 390px (toggle right edge 374 < 390) and on desktop (both labels return).

**Also:** retired the `<tool> (no summary)` Timeline fallback. Now that read-only calls are logged, many tools hit it; added bespoke summaries for `service_call`, `scene_activate`, `script_run`, `automation_trigger`, `integration_reload`, `system_reload`, `system_backup`, `db_purge`, `helper_*`, and a clean humanised generic (`Entity list: light`) for everything else.

## 0.36.1

**Fix: light/dark toggle did nothing after the v0.36.0 DS adoption.** The token color utilities (`bg-surface`, `bg-op-mutate-bg`, …) were never generated, so surfaces never switched and pills had no fill — only the CSS `--vars` flipped, with nothing consuming them.

Root cause: `tailwind.config` was assigned **before** the Tailwind Play CDN `<script>` loaded. The Play CDN ignores `theme.extend` when config is set ahead of it (the old `{darkMode:'class'}` survived by luck; the new token colors did not). Moved the config assignment to a `<script>` **after** the CDN tag — the documented pattern. Verified headless in both themes: body background now switches `#f9fafb` ⇄ `#111827` and all op-class pills/area icons render.

## 0.36.0

**Adopt Design System v1.0 in the sideload UI + rename the mutate pill to `EDIT`.** Wires the design team's token system (`docs/design_system/`) into `static/ui.html`, retiring the four hand-rolled `sev-*` classes.

- **Tokens inlined.** `colors_and_type.css`'s two-tier token set (primitive ramps → semantic `--surface/--text/--border/--op-*/--state-*/--diff-*` …, light on `:root`, dark under `.dark`) is inlined into the `<style>` block — inlined rather than `<link>`ed because the server serves only `/ui`, keeping it buildless + offline.
- **Tailwind config** extended to expose the semantic tokens as utilities (`bg-surface`, `text-text-muted`, `bg-op-mutate-bg`, `text-diff-add`, …) that resolve through the CSS vars, so both themes work without `dark:` pairs.
- **`sev-*` gone.** Every call site migrated to op-class / state tokens: Health + tools_check status badges, the Timeline op-class pills and `ok`/`fail` badge, the diff viewer, the Revert/danger buttons, the paired-row link chip, and inline result banners. Surfaces/text/borders swept to `surface-*`/`text-*`/`border-*` tokens (also fixes the dead `dark:hover:bg-gray-750`).
- **Area icons.** Self-hosted Lucide sprite (`area-icons.svg`, ISC) inlined into the document; each `·area·` tag now renders its subsystem glyph (`currentColor`, never icon-only — text label retained for a11y).
- **Pill rename**: the `mutate` op-class now reads `EDIT` (was `MUTATE`); internal `op_class` value unchanged.

Buildless, offline, dark-parity, and "color is never the sole signal" constraints all held. 572 tests pass (UI tests unaffected). Visual check in both themes recommended after deploy.

## 0.35.0

**Timeline op-class + area classification, read-only activity logging, and a Design System brief.** Every Timeline row now carries an **op-class** pill (`READ` / `MUTATE` / `DELETE`) and an **area** tag (`·database·`, `·automation·`, …) so an operator can see at a glance whether a row observed state, changed it recoverably, or did something irreversible — and which subsystem it touched. `db_execute` is refined by SQL verb (SELECT→read, UPDATE→mutate, DELETE/DROP/TRUNCATE→destructive); `config_*` writes derive a sub-area (automation/script/scene/config) from the file basename.

New `safety/classification.py` is the single source of truth mapping every registered tool to `(op_class, area)`; `AuditLog.log()` stamps both onto each entry, and the UI timeline endpoint derives them for legacy rows via the same function so old and new entries render identically.

**Reads are now logged too.** Read-only tool calls land in a separate `activity.jsonl` stream (rotated at 5 MB, one backup) via the central tool wrapper in `server.py` — no per-tool edits, no double-logging of mutations. The Timeline defaults to mutations-only; a "Show reads" toggle merges the activity stream in (persisted in `localStorage`). New `audit.log_reads` config option (default on; addon option `audit_log_reads`, env `HA_OPS_AUDIT_LOG_READS`) disables it for operators who don't want the volume. Clearing the audit log now also clears the activity streams.

UI adds the op-class pills, area tags, a reads filter, and a dismissible legend. The op-class colors reuse the existing `sev-*` classes as a stopgap — see the new `docs/DESIGN_SYSTEM_BRIEF.md`, which hands the design team a two-tier token system to replace them.

32 new tests (classification incl. SQL/path refinement, registry-coverage guard, audit stamping, activity stream, rotation, clear); 572 pass. ruff + mypy clean.

## 0.34.3

**Fix `haops_config_validate`: it called a service that returns nothing.** The tool POSTed `/api/services/homeassistant/check_config?return_response`, which HA rejects with `HTTP 400: "Service does not support responses. Remove return_response from request."` — `homeassistant.check_config` has no response object, so the tool always errored out.

Switched to the WebSocket `config/check_config` command, which returns the verdict directly (`{"result": "valid"|"invalid", "errors": <str|null>, "warnings": <str|null>}`) — also the path `CLAUDE.md` prescribes for config validation ("WebSocket config/check_config is the only option"). The dead REST call and its broken `call_service` + `return_response` WS fallback are removed. `_format_check_result` now also honours the explicit `result` field. Tests updated to the WS path; 540 pass. No tool signature change; `tools_check` already groups this tool under its WS self-check.

## 0.34.2

**Fix `haops_db_execute` write path: read-only state leaked across the connection pool.** Any write (INSERT/UPDATE/DELETE) issued *after* a prior `haops_db_query` failed with `(1792, 'Cannot execute statement in a READ ONLY transaction')` on MariaDB — and `attempt to write a readonly database` on SQLite. `EXPLAIN` of a write statement in the two-phase preview failed the same way.

Root cause: `query()` marks its connection read-only with a **session/connection-scoped** setting — MariaDB `SET SESSION TRANSACTION READ ONLY`, SQLite `PRAGMA query_only = ON` — which persists on the connection after it returns to the pool. A later `execute()` / `explain()` checking out that same pooled connection inherited the read-only state. No test caught it because none ran a read query before a write; a live MCP session does so constantly (every `haops_db_query` before a `haops_db_execute`).

Fix: added a `_set_writable()` backend counterpart to `_set_read_only()` (SQLite `PRAGMA query_only = OFF`, MariaDB `SET SESSION TRANSACTION READ WRITE`, Postgres `SET SESSION CHARACTERISTICS AS TRANSACTION READ WRITE`). `execute()` and `explain()` now reset writability on the connection, end the autobegun transaction, then open a fresh read-write transaction — so a connection poisoned by an earlier read can no longer block a write.

3 regression tests added (read-query-then-write at both backend and tool level). 540 tests pass. No tool signatures or descriptions changed; `tools_check` lock count unchanged at 63.

## 0.34.1

**OAuth provider hardening: cap client registrations + record `issued_at` on tokens.** Two defensive changes targeting the LAN-anonymous DCR surface and forensic gaps in the token store.

1. **`MAX_CLIENTS = 100` cap on `oauth.json` client registrations.** DCR is anonymous — anyone reachable on the LAN can POST to `/register`. Without a cap, a buggy client looping registration or a hostile peer could grow `oauth.json` unboundedly. On overflow, `register_client` evicts the oldest entries by `client_id_issued_at` (LRU-by-age) and also revokes any access/refresh tokens owned by the dropped client so a leaked dump can't smuggle live tokens past the cap. Eviction logs at WARNING. Cap is well above the steady-state count (production currently runs at 2 clients).

2. **`issued_at` field added to access + refresh token records and surfaced via `haops_auth_status`.** Previously only `expires_at` was stored; with the 30-day sliding TTL on access tokens, `expires_at` reveals nothing about when a token was minted. `issued_at` is stamped on both `exchange_authorization_code` and `exchange_refresh_token`, persisted in `oauth.json`, and rendered as ISO-8601 in `haops_auth_status` output. Pure forensic value — no behaviour change.

Drop on `tools_check` — no auth tool reference exists there. Lock count unchanged at 63. 537 tests pass.

## 0.34.0

**Switch addon default transport from `sse` to `streamable-http`.** Field-observed reliability problem on the SSE transport: long-lived `GET /sse` streams get torn down by the Supervisor IPv6 proxy after periods of idleness, and the MCP Python SDK's SSE handler (`mcp/server/sse.py:249`) raises `anyio.ClosedResourceError` on the next POST to that session. Client sees the POST 202-accept but never receives the JSON-RPC reply, surfaces as `MCP error -32602` and forces a manual `/mcp` reconnect every session.

Reproduced in production logs:

```
INFO:     fd0c:ac1e:2100::1:59808 - "POST /messages/?session_id=... HTTP/1.1" 202 Accepted
ERROR:    Exception in ASGI application
  File ".../mcp/server/sse.py", line 249, in handle_post_message
    await writer.send(session_message)
  File ".../anyio/streams/memory.py", line 218, in send_nowait
    raise ClosedResourceError
anyio.ClosedResourceError
```

`streamable-http` (single-endpoint POST/GET at `/mcp`, no `session_id` URL coupling, reconnect-safe by design) does not exhibit this pattern. Server already supported it via `_runner.py::serve_http` — only the addon option default needed flipping plus a schema-order cosmetic swap. Existing installs keep their saved `transport` value (Supervisor preserves saved options); only fresh installs pick up the new default.

`sse` transport remains available for clients that need it. Both code paths are still in `_runner.py` and both are tested. Connect snippets in `README.md` and `docs/INSTALL.md` now lead with the `--transport http ... /mcp` form and keep the SSE form as a legacy fallback. Endpoint description for port 8901 in the addon manifest updated to "streamable-HTTP / SSE transport" (order reflects which is the default).

No code change; no test change; tool count unchanged at 63. 533 tests still pass.

## 0.33.9

**Always export `HA_OPS_AUTH_ENABLED` from `run.sh` and guard issuer host derivation.** Two bugs surfaced when toggling `auth_enabled: false` on the addon — (1) bashio's `bashio::config 'auth_enabled'` only exports the env var when the option is *true-y*, so flipping to `false` left the env var unset and the Python default (`true`) won. The shell now reads the raw option and always exports the explicit boolean string. (2) IPv6 literal hosts (`fd00::1`-shaped Supervisor addresses) crashed the issuer-URL builder; now wrapped in `[ ]` per RFC 3986.

Tracking docs updated: `docs/SECURITY_REVIEW.md` and `README.md` retarget DCR re-auth references from the now-closed-as-duplicate #58607 to upstream root issue [anthropics/claude-code#43000](https://github.com/anthropics/claude-code/issues/43000), with SSE-transport addendum cross-posted.

## 0.33.8

**Addon Configuration: replace `auth_reset_marker` string with `clear_oauth_on_next_boot` boolean that self-resets after firing.** Marker pattern (set any new value to trigger a wipe) worked but felt indirect — the user described the desired UX as "a button" and a checkbox is the closest the addon Configuration form gets to one. Bool default `false`. When toggled on and saved, the next addon boot wipes `/data/oauth.json` and then writes the option back to `false` via Supervisor's `POST /addons/self/info` → `POST /addons/self/options` (GET-modify-POST because the Supervisor API replaces the whole options object rather than merging). On the boot after that, the flag is already false and the wipe is a no-op — same shape as ESPHome's "factory reset on next boot" pattern.

Failure mode: if the Supervisor self-write call fails (Supervisor unreachable, token rejected, network), the wipe has already happened but the flag stays `true`. The startup log emits a clear warning telling the user to toggle the flag off manually, otherwise every restart will wipe tokens. The wipe itself is the operation that has to succeed; the self-reset is convenience.

The previous v0.33.6 `auth_reset_marker` field was only live for ~one release before this replaced it; no migration path is needed because nobody had it set in saved options yet.

## 0.33.7

**Remove `haops_auth_clear` MCP tool — self-DoS by design, admin action moved to addon config.** The tool wiped the OAuth store (clients, access tokens, refresh tokens, auth codes) via the standard two-phase preview/confirm flow. Problem: the only client that could call it is the MCP client itself, and the wipe invalidates that very client's token. Calling `haops_auth_clear` always terminated the session that called it. Worse, the realistic use case — "auth is wedged, recover from it" — can't be served from the MCP surface at all because MCP dispatch is what's broken in that scenario. We had a tool that worked when you didn't need it and didn't work when you did.

v0.33.6 already shipped the correct path for this: addon Configuration → `auth_reset_marker` field. The shell-level marker wipes `/data/oauth.json` before the MCP server starts, which is exactly the layering this operation needs.

Removed: `src/ha_ops_mcp/tools/auth.py::haops_auth_clear` (registration + function), `tests/test_auth_tools.py::test_auth_clear_*` (3 tests). `haops_auth_status` stays — read-only, never had this problem. Docs (`docs/TOOLS.md`, `docs/HA_API_CAPABILITIES.md`, `README.md` tool count) updated. `CLAUDE.md` rewritten to point at the addon-config recovery flow.

Tools: 64 → 63. Tests: 534 → 531.

## 0.33.6

**Addon Configuration: `auth_reset_marker` field — wipe the OAuth store from the HA UI when MCP dispatch is dead.** The pain case: a client gets stuck on an expired or otherwise unusable access token, surfaces as `MCP error -32602`, and the natural recovery path (`haops_auth_clear` via the MCP server) is unreachable because dispatch itself is what's broken. Previous recovery required SSH into the addon container and `rm /data/oauth.json`, which is fine for the project author but unfit for any user who reaches "auth is wedged" via the panel.

`auth_reset_marker` is a free-form string option on the addon Configuration tab. The startup script (`run.sh`) compares the saved value against `/data/.auth_reset_marker`; if they differ and the new value is non-empty, it deletes `/data/oauth.json` and writes the new marker. Empty value = no-op. Same value as last run = no-op. Any *change* triggers exactly one wipe. Logged at warning level so the operations log shows when/why the store was reset.

Implementation is entirely shell + addon manifest — no Python changes, no new env var, no new `AuthConfig` field. The wipe runs before the MCP server process starts, which is the whole point: we don't want a wedged server to be responsible for unwedging itself. Schema entry is `auth_reset_marker: "str?"`, option default is `""`.

User flow: HA → Settings → Add-ons → HA Ops → Configuration → set `auth_reset_marker` to any new value (a timestamp works) → Save. Addon restarts, log shows the wipe, next `/mcp` reissues a fresh token against the now-empty store. The existing `haops_auth_clear` MCP tool remains the preferred path when the server is healthy.

## 0.33.5

**Raise `auth.access_token_ttl` default from 72h → 30d.** Field reality: ha-ops runs on a private LAN against a single HA instance, single admin user, single MCP client. The "minimise idle window for stolen tokens" argument from generic OAuth deployments doesn't load-bear here — anyone on the LAN with token-stealing capability already has 30 d via the refresh token, and revocation remains a one-call `haops_auth_clear`. With the access TTL now matching the refresh TTL (2592000 s) and sliding extension on use, idle expiry stops being a thing the user notices in practice. The config knob still wins for anyone running this in a less closed environment.

One-line change. No new tests.

## 0.33.4

**Raise `auth.access_token_ttl` default from 24h → 72h.** 0.33.3 corrected the regression where the dataclass default still said 1h, but 24h still surfaces `MCP error -32602` on long idle gaps — weekend pauses, multi-day review windows, anything that lets a session sit untouched past Friday afternoon. Single-user admin tool, refresh tokens already last 30 days, revocation is one `haops_auth_clear` call. Bumping the idle ceiling to 72h covers the realistic gap between active sessions without weakening anything an attacker who steals a token can already do for the next 30 d via refresh.

Sliding TTL still extends on use, so an actively-used session never expires; this only changes how long an idle session survives before forcing a fresh OAuth round-trip. `auth.access_token_ttl` in `config.yaml` continues to override.

One-line change. No new tests.

## 0.33.3

**Fix: `auth.access_token_ttl` default dropped back to 1h after 0.32.4.** 0.32.4 raised `DEFAULT_ACCESS_TTL` in `auth/provider.py` to 86400 s and added sliding TTL — but `AuthConfig.access_token_ttl` in `config.py` was still `3600`, and `server.py` passes the dataclass value into the provider, so the field default won. Net effect: every fresh deploy that didn't explicitly set `auth.access_token_ttl:` in `config.yaml` reverted to a 1h ceiling, and users kept hitting `MCP error -32602` on idle gaps > 1h despite the changelog claiming otherwise.

Two-line change: dataclass default → 86400, with a comment pointing at the sliding-TTL behaviour in `provider.py` so the next person doesn't drift them apart again. `config.example.local.yaml` documents both `access_token_ttl` and `refresh_token_ttl` knobs so the next deploy doesn't have to grep source to find them.

No code-path changes, no new tests — purely a default that should have moved with 0.32.4.

## 0.33.2

**Fix: addon ingress UI returned 502 / MCP-over-IPv6 returned RST — pre-bind the listener so it accepts both stacks simultaneously.** Confirmed working on HA OS production after `Update` from the addon panel. Releases 0.33.0 and 0.33.1 were withdrawn — both shipped one half of the regression.

Two-step regression that 0.33.2 closes for good:

- ≤ 0.32.x bound `0.0.0.0`. That broke MCP clients whose HTTP stack prefers IPv6 (Bun's `fetch`, used by Claude Code's MCP loader). HA OS's Avahi advertises `homeassistant.local` as both an A and a link-local AAAA record; RFC 6724 ranks v6 above v4, so the client tried v6 first, the kernel had no v6 socket, and replied with a TCP RST. Surfaced as "socket connection was closed unexpectedly" on `GET /sse` within ~30 ms, with no entry in the addon log.
- 0.33.1 (b065d91) changed the default to `::` on the assumption that the kernel sysctl `net.ipv6.bindv6only=0` would yield a dual-stack listener. In the HA addon container that assumption did not hold — the actual uvicorn socket still rejected v4-mapped peers (`curl http://127.0.0.1:8901/ui` and `curl http://[::ffff:127.0.0.1]:8901/ui` both got `Connection refused`), even though a freshly-created `socket.socket(AF_INET6)` reports `IPV6_V6ONLY=0`. HA Supervisor's ingress proxy connects to add-ons by IPv4 hostname, so opening the "HA Ops" panel surfaced as `502 Bad Gateway` / "The app seems to not be ready, it might still be starting" while MCP traffic over IPv6 kept working — which is why the regression was invisible in the MCP path that I'd been testing on.

Fix routes the SSE and streamable-http transports through a small runner (`src/ha_ops_mcp/_runner.py`) that pre-binds the listening socket itself: `setsockopt(IPV6_V6ONLY, 0)` *before* `bind`, then `Server.serve(sockets=[sock])` instead of letting uvicorn create its own socket. stdio transport keeps `mcp.run("stdio")` unchanged. Default host stays at `::` so both IPv4 (Supervisor ingress, `0.0.0.0:8901` host port-forward, IPv4-only MCP clients) and IPv6 (mDNS-AAAA-preferring MCP clients) work simultaneously. Users who set `HA_OPS_HOST` / `host:` in `config.yaml` to a literal IPv4 still get a plain `AF_INET` socket with no IPv6 logic.

## 0.33.1

**Fix: bind dual-stack (`::`) so IPv6 clients no longer get a kernel RST.** Default server host changed from `0.0.0.0` to `::`. The symptom was a Claude Code MCP "socket connection was closed unexpectedly" failure within ~30 ms of `GET /sse`, with no entry in the addon log. Root cause: HA OS's Avahi advertises `homeassistant.local` as both an IPv4 (e.g. `10.0.0.150`) and a link-local IPv6 (`fe80::…`) address. RFC 6724 ranks v6 above v4, so Bun's `fetch` (Claude Code) tries v6 first; with the listener on `0.0.0.0` only, the kernel has no v6 socket and replies with a TCP RST. The client surfaces that as a closed connection, never reaches the 401 / OAuth discovery path, and stays stuck.

Binding `::` makes Uvicorn accept on both families on Linux/macOS (default `IPV6_V6ONLY=0`). The HA addon's `ports: 8901/tcp: 8901` mapping forwards both stacks. Existing users who set `HA_OPS_HOST` or `host:` in `config.yaml` keep their override.

This also unblocks the same failure mode for any other MCP client whose HTTP stack prefers v6 — the pattern was specific to dual-stack mDNS, not to Claude Code.

## 0.33.0

**Collection-helper tools: `haops_helper_{list,create,update,delete}`.** Closes a gap that bit during real use — previously, an LLM asked to "add an input_boolean" had no working path through this MCP. The visible tool surface (`haops_registry_query`, `haops_entity_*`) implied the entity registry was the way, but inserting a row there is a no-op: HA's input helpers are owned by the per-domain collection subsystem in `.storage/<domain>`, not the registry, and the only API that creates them is the WebSocket `<domain>/{create,update,delete,list}` family. `haops_service_call` couldn't reach those either (REST `/api/services` → service calls only; collection commands are WS-native).

The new module wraps the WS collection API for all eight collection-helper domains: `input_boolean`, `input_number`, `input_text`, `input_select`, `input_datetime`, `counter`, `timer`, `schedule`. Same two-phase confirmation flow as the rest of the surface — preview returns a token, apply consumes it. `haops_helper_update` and `haops_helper_delete` accept `entity_id` (e.g. `input_boolean.foo`) and resolve to the collection id via the entity registry's `unique_id` mapping, with a slug-of-name fallback so freshly-created helpers are still resolvable before the registry catches up. `haops_helper_create` optionally accepts a target `entity_id` and renames via `config/entity_registry/update` in the same transaction; rename failure leaves the helper at its auto-derived id and surfaces `rename_error` instead of silently mis-reporting success.

YAML-defined helpers are intentionally out of scope for these tools — HA returns "not found" on collection-API mutations against them. Edit `configuration.yaml` with `haops_config_patch` and reload `input_<domain>` instead.

`haops_tools_check` gains a `helpers` group that probes `input_boolean/list` and `counter/list` to surface a broken WS collection API before a user session hits it. Config-entry helpers (`template`, `utility_meter`, `derivative`, etc.) are NOT covered here — those go through `config_entries/flow/init`, a multi-step state machine, and warrant a separate tool.

Tools: 60 → 64. Tests: 514 → 534 (+20: list/create/update/delete preview & confirm paths, slug fallback, rename-failure path, no-op detection, unresolvable handling, unsupported-domain rejection).

## 0.32.4

**OAuth access tokens: 24h default + sliding TTL on use + refresh-exchange logging.** Field telemetry (across multi-day Claude Code sessions on streamable-http) showed the 1h `access_token_ttl` default was too short for ha-ops's session shape — review-and-fix loops routinely span hours with multi-minute think pauses, and every TTL crossing surfaces as `MCP error -32602` (the client-side rewrite of the server's spec-correct 401, see `docs/HA_QUIRKS.md`) and forces a manual `/mcp` reconnect. Three layered changes in `auth/provider.py`:

- **Default `access_token_ttl` raised from 3600 s → 86400 s (24h).** Single-user admin tool, blast-radius argument for short access tokens is weak when revocation is one `haops_auth_clear` call away. Existing override via `auth.access_token_ttl` in YAML still wins.
- **Sliding TTL on use.** `load_access_token` now extends `expires_at` whenever a valid token is verified, so an actively-used session never times out. Throttled persistence — only re-saves the store when remaining lifetime has dropped below half the configured window. Idle tokens still expire on schedule; expired tokens are still rejected (sliding cannot resurrect a token that already crossed `expires_at`).
- **INFO log on `exchange_refresh_token`.** Diagnostic — until now we couldn't tell server-side whether an MCP client was exercising the refresh-token grant or just doing a fresh dynamic-client-registration on every 401. One log line per attempt is enough to validate which clients refresh proactively without instrumenting them.

Tests: 510 → 514 (+4: sliding extends past half-window, sliding is a no-op when fresh, sliding does not resurrect expired tokens, refresh-exchange emits INFO log).

## 0.32.3

**Fix: OAuth issuer auto-detection on fresh installs with no `internal_url`.** When HA's `internal_url` is unset, the supervisor returns `null` (or a junk placeholder); the previous parser stringified that into the literal hostname `none`, producing an issuer of `http://none:8901/` that no MCP client can dial — the symptom is "Unable to connect" on the client, not a 401. `run.sh` now treats null/empty/`none`/`localhost`/loopback values as "not detected" and falls back to `http://homeassistant.local:8901` (HA's mDNS default), which works on a default LAN with zero manual config. The existing `auth_issuer_url` override still wins when set. New `docs/HA_QUIRKS.md` entry documents the diagnosis path.

## 0.32.2

**CI / release pipeline ahead of public flip.** Adds `.github/workflows/release.yml`: tests + ruff + mypy strict on every push/PR; on `release: published`, builds sdist + wheel via `python -m build`, attests provenance with `actions/attest-build-provenance@v2`, and uploads the artifacts to the GitHub release. The first public release will ship with a verifiable build attestation (`gh attestation verify`) instead of a bare tag.

Also fixes the long-standing mypy strict error at `server.py:282` (auth provider HTTP-issuer monkeypatch). Replaced the `lambda _url: None` with a typed inner function so `mypy --strict` passes — required for the CI step to be green from day one.

## 0.32.1

**Docs refresh.** No code change. README + DOCS Sidebar Timeline bullets now mention pagination (50 per page, auto-refresh paused on deeper pages). Tool/test counts updated across README/DOCS/INSTALL (60 tools, 510 tests). New entry in `docs/HA_QUIRKS.md` documenting the `MCP error -32602` → auth-expiry diagnosis (server-side fix is rejected — the rewrite happens in Claude Code's MCP client; recovery hint and curl probe captured for future debug sessions).

## 0.32.0

**Timeline now paginates — 50 entries per page, Prev/Older controls.** The audit log accumulates indefinitely and the previous all-or-nothing `?limit=50` cap meant older history was reachable only via raw audit reads. Sidebar gains offset-based pagination: `GET /api/ui/timeline?offset=N&limit=50` returns the slice plus `has_more: bool` so the frontend can hide "Older →" at the tail without a separate count call. Auto-refresh polling fires only on page 1 — paging deeper pauses the 5 s tick (otherwise the offset window would shift under the user as new entries arrive at the head). Header label confirms this state: "auto-refreshing" on page 1, "auto-refresh paused" on page 2+.

The cross-page-aware Revert-button stripping was the subtle bit: only the most-recent successful apply gets a `transaction_id` in the response, and "most recent" must mean across the whole log, not within the current page. The endpoint reads `offset+limit+1` audit entries, checks the prior-pages slice for a qualifying apply, and seeds the per-page strip loop accordingly. Without this, a user on page 2 would have seen a Revert button on a stale apply whose savepoint is no longer the freshest.

Apply ↔ rollback pairing (`paired_with` cross-link) stays within-page only — pairs that straddle a page boundary lose their badge. Acceptable trade for keeping the index references local; the user can flip pages to inspect.

Tests: 506 → 510 (+4: offset slicing, has_more flag, cross-page txn_id strip, default limit=50).

## 0.31.2

**Header layout: tabs on the left, version/last-refreshed on the right.** The previous layout put the metadata strip (version + last-refreshed) next to the brand on the left and tabs on the right, which buried the navigation past the eye's first stop. Swapped: tabs sit immediately after the "HA Ops" brand on the left; version, last-refreshed, and the theme toggle group on the right.

## 0.31.1

**Title-bar version now loads on every page render.** Previously the addon version (`v0.31.x` in the header) only populated when the user visited the Health tab, because that's where the `selfCheck` fetch lived. Page loads that stayed on Timeline showed a blank header, which read as "did the page even load?". Prefetched in `init()` via a shared `_loadSelfCheck()` helper that's idempotent — if Health is opened while the prefetch is in flight, both callers piggyback on the same promise instead of double-fetching.

## 0.31.0

**Sidebar Timeline + Health no longer block on the slowest payload.** The sidebar UI on Home Assistant felt frozen on initial load when the audit log had accumulated a few dozen entries: the `/api/ui/timeline` response inlined the full unified diff for every row (capped at 60 KB each), so 50 entries × diff-heavy applies put MBs on the wire — and re-shipped them every 5 s on auto-refresh. The Health tab compounded the wait by `Promise.all`-blocking the fast `self_check` on the slow `tools_check`. Both fixes are independent UI plumbing — no tool surface touched.

- **Timeline list ships `diff_present: bool` instead of the diff body.** `_render_audit_entry` now skips the `unified_diff` / `yaml_unified_diff` recompute on the list path and uses a cheap `_has_diff_surface` presence check. Initial render becomes near-instant regardless of how many diff-capable entries are in scope. The 5 s auto-refresh poll drops the same payload weight.

- **New `GET /api/ui/timeline/diff?ts=<iso>&tool=<name>` endpoint** lazy-loads a single entry's diff. The frontend hits it on row expand, caches the body onto the entry, and re-uses the cached body across collapse/expand cycles. Polling preserves cached diffs across list refreshes by matching on `timestamp + tool`. Both bare (`config_apply`) and prefixed (`haops_config_apply`) tool names are accepted.

- **Health tab renders `selfCheck` and `toolsCheck` independently.** Replaced the single `healthLoaded` gate with per-section `selfCheckLoading` / `toolsCheckLoading` flags and dropped the `Promise.all` wait. The fast probe (config + connectivity) appears first; the slower per-group capability probe fills in when ready. Errors surface per-section instead of suppressing the whole tab.

Tests: 500 → 506 (+6, all on the lazy diff endpoint and the regression that the list never re-inlines the body). Ruff + mypy clean on touched files.

## 0.30.0

**Three operability gaps from 2026-04-24 closed as one release.** All three surfaced from a single dehumidifier-controller calibration session during heavy time-series analysis; each was eroding trust in timestamp arithmetic or wasting a round trip on a confusing REST error. Repros in `_gaps/session_gaps_2026-04-24.md`.

- **`haops_db_query` response now includes `session_timezone`.** MariaDB, Postgres, and SQLite all surface the effective timezone the backend uses for `FROM_UNIXTIME()` / `UNIX_TIMESTAMP()` so callers don't have to guess-and-verify whether to add or subtract an offset. MariaDB: `SELECT @@session.time_zone` (resolves `SYSTEM` to the underlying `@@system_time_zone`). Postgres: `SHOW TIME ZONE`. SQLite: a descriptive string noting it stores whatever HA wrote (typically UTC epoch). Cached on first call per backend. Repro was 3× in one session, costing ~5 minutes of double-counted `+8h` on SGT timestamps.

- **`haops_self_check` now reports HA's configured timezone.** The `rest_api` check block already calls `/api/config`; now surfaces `time_zone` (e.g. `"Asia/Singapore"`) alongside `ha_version`. Combined with the DB `session_timezone` above, every timestamp in a recorder query becomes self-describing — no more back-solving TZ by comparing `last_changed` against a known SGT wall-clock moment. `haops_system_info` already exposed this; parity added to self_check.

- **`haops_logbook` / `haops_entity_history` URL-encode query-string values.** Root cause was not what the gap doc initially assumed (start/end pipeline asymmetry): both go through the same `_format_ts()`. The real bug was that `start` sits in the URL path (where `+` is literal) while `end_time` sits in the query string (where `+` decodes to space per application/x-www-form-urlencoded). HA saw `end_time=2026-04-23T15:15:00 00:00` and rejected as "Invalid end_time". Wrapped `end_time`, `entity`, and `filter_entity_id` query values in `urllib.parse.quote()`. `start` in the path is untouched.

Tests: 497 → 500 (+3). Ruff clean. (Preexisting mypy error in `server.py:282` auth provider typing is unaffected and unrelated.)

## 0.29.0

**Three operability gaps from 2026-04-21 closed as one release.** All three came from a single long session building the dryer-detection + Clothes Drying lock; each was costing minutes of re-round-tripping per incident. Implementation notes and repros in `_gaps/session_gaps_2026-04-21.md`.

- **`haops_config_patch` absorbs ±5-line hunk-header drift.** Mimics GNU `patch -F`: on exact-line context mismatch, searches ±5 lines for the hunk's context block and relocates when the match is unique. Ambiguous matches (the context block appears at the same distance on both sides) still raise so the caller regenerates against the current state. The existing expected-vs-actual diagnostic is preserved and reports the first diverging line at the declared position. `apply_patch(..., fuzz=0)` reinstates strict mode for callers that want it. Repro was 3× in one session (template.yaml, sensor.yaml, automations.yaml) — LLMs counting lines against a partial read routinely produced off-by-2/3 headers.

- **`haops_service_call` attaches `log_excerpt` to non-2xx responses.** Scans the last ~200 lines of `homeassistant.log` for entries mentioning `{domain}.{service}`, the bare domain, or common exception tokens (`Exception`, `Traceback`), and embeds up to 10 matches in the error payload. Saves the second round trip (previously: failed service call → `haops_system_logs` call → paste-and-read) for the ~50% of ZHA/template/integration failures where the real error lives in the log, not the HTTP body. Log-fetch fallback (filesystem → Supervisor → REST) extracted to `utils/logs.py` so `haops_system_logs` and the service-call error path share one code path.

- **`haops_system_restart` no longer reports its own success as a failure.** On the confirm call, 502/503/504 and aiohttp connection drops are recognised as the expected "HA API went down because HA is restarting" signal. Response switches to `{"status": "initiated", "message": "...use haops_self_check to monitor..."}` instead of a red "Restart failed" error. Only genuinely-unrelated errors (401/403, actual 500 with a body, etc.) still surface as failures. Restart flows stop looking broken to agents and humans alike.

Tests: 489 → 497 (+8). Ruff + mypy clean.

## 0.28.2

**Sharper REVIEW PROTOCOL in preview tool descriptions.** Restructured the protocol text into two explicitly non-negotiable parts: **(1) RENDER, ALWAYS** — controller MUST paste `diff_rendered` verbatim as a chat message every time, even for trivial changes, even when the user pre-approved; **(2) STOP for approval**. The pre-approval EXCEPTION now applies only to the stop, never to the render — the render is the receipt of what's about to land. Hits `haops_dashboard_diff`, `haops_dashboard_patch`, `haops_config_patch`, `haops_config_create`, `haops_rollback`.

**Backlog cleanup informed by empirical findings.** Spun up `~/_dev/claude-code-diff-render-test` (separate private repo) — a standalone MCP server with one tool per rendering hypothesis. First run on Claude Code (Claude Opus 4.7, 2026-04-19) settled the question: Claude Code's tool-result panel renders ONLY `structuredContent`. TextContent (single, multi-block, fenced, JSON-bodied), EmbeddedResource — all invisible to the panel. Elicitation prompts surface as real Approve/Decline UI; the message body renders as plain text (no markdown colourisation) but line breaks and `+`/`-` markers are preserved, so a raw unified diff in the body is fully readable.

Backlog edits driven by those findings:
- **Deleted** the "Return preview diffs as MCP text content" entry — proven non-functional in Claude Code (T2/T3/T7/T8/T9 all produce empty panels).
- **Upgraded** the `safety.review_mode` entry: default `prompt` (on by default per the "guards optional but enabled by default" rule), strip the markdown fence from the elicit message body (renders literally), drop the raw unified diff text directly. Empirical confirmation that `ctx.elicit()` round-trips correctly noted in the entry.

No code-shape changes beyond the description text. Tests: 489 (unchanged). Ruff clean.

## 0.28.1

**Renamed `haops_lovelace_resources` → `haops_dashboard_resources`.** The old name implied per-dashboard scope ("which Lovelace? the default one?"); the tool actually lists frontend resources system-wide — registered once, applied to the default dashboard and every custom dashboard. New name matches the `haops_dashboard_*` family. No deprecation shim — tool name updated everywhere (registry, `tools_check`, `lovelace_validate`, docs, test file). HA's underlying storage path (`.storage/lovelace_resources`) and WS endpoint (`lovelace/resources`) keep their HA-native names. Description updated to make the system-wide scope explicit.

Backlog cleanup: removed a stray `.mcpb` reference from the `safety.review_mode` entry — speculative future-packaging mention with no real architectural basis behind it.

## 0.28.0

**Chapter close on the diff-readability + two-phase confirmation arc.** No new code beyond v0.27.7 — this release rolls v0.27.4 → v0.27.7 + the docs work into a single round-numbered tag so the next session opens against a clean baseline.

What this chapter shipped, in one place:

- **Diff field is a real unified diff** for every preview tool (`haops_dashboard_patch`, `haops_dashboard_diff`, `haops_config_patch`, `haops_config_create`, `haops_rollback`). Per-op anchor (op + JSON Pointer + view/section title lookup + before/after kind) followed by `difflib.unified_diff` of YAML at the patch path. Replaces the markerless `~ replace ... 'old' -> 'new'` blob and the deepdiff `Changed values: root[...]` blob — both unreadable in any diff-aware renderer because they had no `+`/`-` line markers.
- **Sidebar Timeline matches.** `_recompute_audit_diff` in `ui/routes.py` switched all four dashboard-type callsites (`dashboard_apply`, `batch_apply` dashboard items, `rollback` dashboard targets, `backup_revert` dashboard) to `yaml_unified_diff`. The sidebar's `renderDiffHtml` JS already keys on `+`/`-` markers, so colourisation works without UI-side changes.
- **REVIEW PROTOCOL embedded in tool descriptions.** Every preview tool now tells the controller LLM to paste `diff_rendered` verbatim to chat as a fenced markdown block, then STOP for explicit user approval before calling `*_apply`. Caveat: skip the stop if the user already approved this specific change in the current turn (no double-confirm on direct instructions).
- **`haops_rollback` apply response slimmed down.** Stripped bulky `old_/new_content` + `old_/new_config` payload from `restored[]` entries — a real dashboard rollback was hitting 143 KB and forcing the MCP client to spool to disk. Audit log keeps the full payload for Timeline diff recomputation.
- **Schema-validation regression net.** Parametrised test in `tests/test_mcp_result_shape.py` registers each diff-emitting handler against a real FastMCP instance and calls it through `mcp.call_tool` — exercises the same schema validator the MCP transport runs, asserts `diff` carries `+`/`-` line markers. v0.27.4 shipped a schema-mismatch ship-blocker that no in-process unit test caught; this closes that gap.
- **Recommended client-side review mode (Claude Code) documented.** `docs/INSTALL.md` now has the `~/.claude/settings.json` `permissions.ask` snippet that triggers Claude Code's native approval modal on every `*_apply`. Mechanical, not bypassable, no server change. The server-side equivalent (`safety.review_mode` via MCP elicitation) for non-Claude-Code clients is on the backlog.
- **Removed the `_to_mcp_result` / `_mcp_content` multi-block wire path.** v0.27.4's first attempt was the wrong surface — Claude Code reads `structuredContent` and bypasses content blocks. Engineering effort wasted; ripped it out in v0.27.6 in favour of putting the real diff into the `diff` field everyone reads.

Tests: 482 → 489 (net + 7 since v0.27.0). Ruff + mypy clean (one pre-existing mypy error unrelated to this work).

## 0.27.7

**Sidebar diff format matches the chat / wire format.** v0.27.6 fixed the diff that the controller LLM and chat surface see, but the sidebar Timeline kept showing the legacy `Changed values: root['views'][14]['sections'][0]['cards'][0]['name']: 'Desk' -> 'Lab Light'` blob — because it doesn't read the live tool response, it recomputes the diff at view-time from audit-log entries. Switched all four dashboard-type callsites in `_recompute_audit_diff` (`dashboard_apply`, `batch_apply` dashboard items, `rollback` dashboard targets, `backup_revert` dashboard) from `format_json_diff(json_diff(...))` to `yaml_unified_diff`. The sidebar's existing `renderDiffHtml` JS already keys on `+`/`-` line markers, so colourisation works without any UI-side change.

## 0.27.6

**Diff field is finally a real unified diff.** v0.27.4/.5 chased the wrong surface — multi-block MCP `content` doesn't reach the user in Claude Code (the panel renders `structuredContent` as JSON, the LLM reads `structuredContent`, content blocks are bypassed). The fix is much simpler: put the real `difflib.unified_diff` directly into the `diff` and `diff_rendered` fields the LLM and panel actually read.

- **`haops_dashboard_patch`** — `_render_patch_aware_diff` now emits per-op anchor (op + JSON Pointer + view/section title lookup + before/after value kind) followed by a real unified diff of the YAML serialisation at the patch path. Replaces the `~ replace ... 'old' -> 'new'` blob that had no `+`/`-` line markers.
- **`haops_dashboard_diff`** — full-config / view-replace / view-append modes now emit a YAML unified diff of old vs new dashboard config. Replaces the `format_json_diff(json_diff(...))` deepdiff blob.
- **`haops_rollback`** — DASHBOARD-type undo preview now emits a YAML unified diff between current state and the savepoint to be restored. Apply response **drops** the bulky `old_/new_content` + `old_/new_config` payload from `restored[]` entries (gap report 2026-04-18 §4: 143 KB rollback response was forcing the client to spool to disk). Audit log retains the full payload for Timeline diff recomputation.
- **`haops_config_patch` / `haops_config_create`** — already used real unified diffs; only stripped the `_mcp_content` field added in v0.27.4.

**Tool descriptions: REVIEW PROTOCOL.** Every preview tool's description now includes an explicit instruction to the controller LLM: paste `diff_rendered` verbatim as a chat message before calling `*_apply`, then STOP for explicit user approval. Caveat: skip the stop if the user has already explicitly approved this specific change in the current turn (no double-confirm on direct instructions).

**Removed: `_to_mcp_result` + `_mcp_content` opt-in.** Per user feedback, the multi-block MCP wire shape is bypassed by the way Claude Code renders tool results — engineering effort wasted. Stripped from `server.py`, `tools/dashboard.py`, `tools/config.py`. Test file repurposed as a generic FastMCP schema-validation regression net (4 parametrised cases) that also asserts `diff` carries `+`/`-` line markers.

Tests: 494 → 489 (5 obsolete `_mcp_content` tests removed; 0 net new failures).

## 0.27.5

**Hotfix for v0.27.4 ship-blocker.** The opt-in multi-block return shape produced a `CallToolResult` whose `structuredContent` was missing the `{"result": ...}` wrap that FastMCP's auto-derived output schema requires for handlers annotated `-> dict[str, Any]`. Result: every `haops_dashboard_patch` / `haops_dashboard_diff` / `haops_config_patch` / `haops_config_create` call failed at the MCP boundary with `Field required [type=missing] result` even though the underlying logic worked. Fixed in `_to_mcp_result` by wrapping the structured payload to match the schema sibling tools get for free via FastMCP's `convert_result`.

**Schema-validation regression net.** Added `tests/test_mcp_result_shape.py::test_diff_tools_satisfy_fastmcp_output_schema` — a parametrised test that registers each real diff-emitting handler against a live `FastMCP` instance and calls it through `mcp.call_tool`, exercising the same schema validator the MCP transport runs. The v0.27.4 bug slipped through because no test exercised a real handler past the `_to_mcp_result` boundary; this closes that gap. Tests: 489 → 494.

## 0.27.4

**Diff previews are now reviewable.** Dashboard and config preview tools (`haops_dashboard_diff`, `haops_dashboard_patch`, `haops_config_patch`, `haops_config_create`) now emit their response as **multiple MCP `content` blocks** plus `structuredContent`, instead of one JSON-stringified blob. Diff-aware MCP clients render the unified diff as a syntax-highlighted ` ```diff ` fence; the confirmation token gets its own block instead of being buried in escaped JSON.

**Real unified diffs for dashboard patches.** `haops_dashboard_patch` previously emitted a `before -> after` blob with no `+`/`-` line markers — even diff-aware renderers had nothing to colourise. Each JSON Patch op now produces a per-op anchor line (mechanical: op + path + view-title lookup + before/after kind) plus a real `difflib.unified_diff` body of the YAML serialisation at the patch path. The same diff text feeds the sidebar's `renderDiffHtml` JS, so its colourisation finally works too.

**Backward compatible.** Handlers opt in via a `_mcp_content` sentinel intercepted at the registration boundary (`_to_mcp_result` in `server.py`); existing in-process callers, tests, and the audit log continue to see the dict shape unchanged. Tests: 482 → 489.

## 0.27.0

**OAuth enabled by default.** After introduction in v0.26.0 as opt-in, OAuth is now **enabled by default** for SSE and streamable-http transports. Anyone who can reach port 8901 must authenticate before calling tools. Set `auth_enabled: false` to disable on fully-trusted LANs.

**Issuer URL auto-detection.** The addon queries HA's internal URL via the Supervisor API and constructs the issuer URL automatically — no manual `auth_issuer_url` needed. Override only if auto-detection picks the wrong hostname.

**HTTP issuer support for local networks.** The MCP SDK enforces HTTPS per OAuth 2.0 spec, but HA addons run on local Docker networks without TLS. The SDK's issuer validation is patched at startup to allow HTTP, with a logged warning.

**Fixed token exchange with Claude Code.** Claude Code registers with `token_endpoint_auth_method="none"`. The provider was incorrectly assigning a `client_secret` to these clients, causing the SDK's `ClientAuthenticator` to reject token exchange requests. Fixed — clients that register without a secret no longer get one forced on them.

**Config UI descriptions.** Added help text for `auth_enabled` and `auth_issuer_url` in the addon Configuration tab.

No tool changes. Tests: 482 (unchanged).

## 0.26.2

**Makefile consolidation.** Reduced from 10 targets to 7. Removed `refresh`, `reinstall`, `dev-update`, `dev-deploy`, `rebuild` — one-off admin actions accessible via HA UI or SSH. Kept `deploy` (SCP sync), `update` (sync + store reload + `ha apps update`), `logs`, `check`, `test`, `lint`, `typecheck`.

## 0.26.1

Documentation pass — changelog entries for v0.24–v0.26, OAuth architecture section in CLAUDE.md, auth tools in HA_API_CAPABILITIES.md capability matrix.

## 0.26.0

**Optional OAuth 2.0 authentication for MCP transport.** Set `auth.enabled: true` (addon: `auth_enabled: true`) to require OAuth tokens on SSE/HTTP transports. Disabled by default — no auth, identical to previous behavior. stdio is never authenticated.

Implements `OAuthAuthorizationServerProvider` from the MCP SDK. When enabled, the SDK automatically mounts `/authorize`, `/token`, `/register`, `/revoke`, and `/.well-known/oauth-authorization-server` endpoints plus Bearer token validation middleware. Auto-approves authorization requests (single-user admin tool). Client registrations and tokens persisted to `/data/oauth.json` (addon persistent storage, survives restarts).

**New tools:**
- `haops_auth_status` — show OAuth status: registered clients (masked IDs, names, registration dates), active access tokens (masked prefix, scopes, TTL, expiry), active refresh tokens, pending auth codes.
- `haops_auth_clear` — two-phase clear of OAuth state (all, or clients-only). Connected MCP clients must re-register after clear.

**Docs:** `docs/INSTALL.md` — full "Enabling OAuth" section covering addon, standalone, how clients connect, token lifetimes, Claude Code/Desktop instructions.

**Tests:** 476 → 482. Ruff + mypy clean.

## 0.25.0

**Removed shell and SQL guardrails.** The shell guard (`safety/shell_guard.py`) and SQL guard (`safety/sql_guard.py`) were denylist-based pattern blockers trivially bypassable via alternate commands, encoding, or `guard=false`. They gave users false confidence that dangerous operations were blocked when they weren't. Two-phase confirmation is the real safety mechanism — pattern denylists are theater for a superuser admin tool. Both modules deleted along with the `guard` parameter from `haops_exec_shell` and `blocked_sql_patterns` from config.

**Parameterized SQL in `haops_db_purge` and `haops_db_statistics`.** All f-string SQL interpolation converted to `sqlalchemy.text()` bind parameters. Closes the parameter-smuggling vector through the sidebar panel.

**Security review.** `docs/SECURITY_REVIEW.md` — full pre-release audit covering security, sensitive data, code quality, and enterprise architecture gaps.

**Tests:** 487 → 476 (guard tests removed, net reduction). Ruff + mypy clean.

## 0.24.0

Documentation overhaul. Rewrote README, split tools/capabilities/install into separate docs, added tool cross-references to descriptions, renamed docs for clarity.

## 0.23.0

Three backlog items from the dashboard-extension session, shipped together. Backlog is empty again.

**`haops_dashboard_patch` — patch-aware diff rendering.** Instead of delegating to deepdiff (which shows 28+ "Changed values" when inserting one card because every subsequent array position shifts), the diff is now rendered from the JSON Patch ops themselves. Each op is summarised in terms of what the caller asked for — `+ add /views/0/cards/0: {type, entity, ...}`, `- remove /views/1/cards/3: {...}`, `~ replace /title: 'Old' -> 'New'`. Array-position shifts are noted once as a parenthetical ("array at /views/0/cards: 4 -> 5 items") instead of drowning real changes.

**Soft entity-existence validation in `haops_dashboard_patch`.** After computing the diff, the tool walks the new config for entity refs (reusing `walk_dashboard_for_refs` from the refindex) and cross-checks against `/api/states`. Unresolved entity IDs surface as `entity_warnings` in the preview response — a list of per-entity warning strings with the path where each was found. Warnings, not blocks: templated entities (`{{ }}`), `input_*` helpers, and dynamically-registered entities are skipped. REST failure (no API access) silently skips validation rather than blocking the preview.

**`haops_system_reload` — `verify` param.** Optional `verify=["script.foo", "automation.bar"]` that, after the reload service call completes, checks each listed entity against `/api/states` and reports `verified: {entity_id: true|false}` + a `verify_warning` string listing any missing ones. Saves a follow-up `haops_entity_state` call when you want to confirm a just-created script/automation registered. 1-second post-reload pause built in so HA has time to register the entity.

**Backlog:** empty. All three items shipped.

**Tests:** 487 (unchanged — these are runtime features that need live HA for full validation; unit tests verify the code compiles and existing flows are unaffected). Ruff + mypy clean.

## 0.22.0

Two fixes + one new param, triaged from the dashboard-extension session and the audit-path discovery.

**Audit log path moved inside `backup_dir`.** Was resolving to `/backup/audit/` (a sibling of backup_dir, stray directory on HA's filesystem). Now resolves to `/backup/ha-ops-mcp/audit/` (inside the configured backup_dir). One-line fix in `server.py`. Existing deploys that have data at the old path get a startup WARNING with migration instructions — no auto-migration.

**`haops_config_read` — `lines=[start, end]` param.** 1-based, half-open line range for YAML inspection and patch authoring. Returns content + `line_start`, `line_end`, `lines_returned`, `total_lines`, and a `more` + paginate hint when there's more after the range. Preferred over the byte-range `chunk` param when building unified-diff hunk headers — no manual byte counting or off-by-one risk.

**Backlog triaged.** Five raw improvement requests from the dashboard session evaluated:
- #1 (dashboard_patch diff misleading for array inserts) → backlog, medium.
- #2 (config_read line-range reads) → **shipped in this release**.
- #3 (soft entity-existence validation in dashboard_patch) → backlog, low.
- #4 (system_reload post-reload verify param) → backlog, low.
- #5 (dedicated script_create/patch) → **killed** — scope creep.

**Tests:** 484 → 487. Ruff + mypy clean.

## 0.21.0

Closes the last backlog item — §5 `auto_apply` parameter.

**`auto_apply=True` on `haops_config_patch`, `haops_config_create`, and `haops_dashboard_patch`.** When set, the tool previews AND applies atomically in one call — returns `{diff, diff_rendered, success, transaction_id, backup_path}`. No separate `_apply` call needed. Default is `False` (unchanged two-call flow with preview + token + apply).

Internally: creates a token and immediately calls the matching `_apply` function, so the audit entry, backup, rollback transaction, and Timeline Revert button all work identically to the two-call flow. Zero new code paths — just a shortcut that skips the round-trip.

**Default is `auto_apply=False` for all three tools.** If specific tools start showing excessive two-call friction in practice, the default can be flipped per-tool without a schema or behaviour change — callers that don't pass the parameter keep getting the current two-call flow.

**Backlog is empty.** All items shipped, killed, or resolved.

**Tests:** 481 → 484. Ruff + mypy clean.

## 0.20.0

Partial resolution of §5 (token-flow backlog item). Two changes: token expiry removed, Pending tab dropped.

**Tokens no longer expire.** The 5-minute expiry was belt-and-suspenders over protection each tool already provides: `config_patch` has context-match checking, `dashboard_patch` has structural JSON Patch matching, `config_create` checks file-exists. The expiry caught nothing these don't already catch, and it created real friction when the user paused to check something in the HA UI ("token expired, re-preview"). Tokens are now single-use with no time cap — they live until consumed or the session ends (addon restart clears the dict).

`TokenExpiredError` removed from the codebase. `cleanup_expired()` removed. `expires_in` parameter removed from `create_token()`. `expires_at` field removed from `ConfirmationToken`.

**Pending tab removed from the sidebar.** The tab showed outstanding confirmation tokens. In practice tokens were consumed within seconds (LLM drives the flow) and with no expiry they'd just accumulate until session end. The tab showed nothing actionable. The Overview card still reports `pending_tokens` count as a number — the dedicated tab surface is gone. `/api/ui/pending` and `/api/ui/pending/{token_id}` endpoints deleted; both return 404.

**Backlog:** §5 entry updated to "partially resolved" / low priority. Expiry removed and preview stays default (both decided). Residual open question (`auto_apply=True` opt-in) deferred until it comes up in real usage.

**Tests:** 486 → 481. Ruff + mypy clean.

## 0.19.1

Backlog cleanup + safety refinement.

**Killed `backup.db_row_threshold`.** Removed the field from `BackupConfig`, dropped the `HA_OPS_BACKUP_DB_ROW_THRESHOLD` env var mapping, stripped the single use site in `haops_db_purge` dry-run. The warning fired on every realistic purge (any active HA instance exceeds 1000 state rows/day) and didn't gate anything — just noise. User's call: "the backup is an option for the user anyway, and if they are using this to configure HA they should do it under their own volition."

**Timeline Revert button restricted to most-recent apply only.** Previously every successful `config_apply` / `dashboard_apply` / `batch_apply` row with an in-session transaction showed a Revert button. Now only the most recent one does. Rationale: HA re-serializes config files on reload and the user can edit outside the ha-ops flow, so the RollbackManager's saved `old_content` for older applies may be stale — rolling them back would clobber later state. For anything older, fall back to `haops_backup_revert` via the MCP flow (which has drift annotation since v0.17.0).

**Backlog reduced to 1 item.** Removed "Out-of-memory revert from backup by audit entry" (existing paths cover the use case) and "`backup.db_row_threshold` rework or remove" (killed outright). Only §5 (token-flow apply-first default) remains — design proposal, user decision still pending.

**Tests:** 486 (unchanged count, one test rewritten). Ruff + mypy clean.

## 0.19.0

Four backlog items shipped in one release, all low-priority UX/ergonomics. Also a permanent drop of `haops_addon_options` from the backlog (user's explicit call — "we will not do this at all").

**Large-file content input hardening.** `haops_config_create` and `haops_config_patch` now accept `content_from_file` / `patch_from_file` — a path under `config_root` containing the value verbatim. Lets callers stage big payloads (50 KB+ patches, whole-file rewrites) via `haops_exec_shell` or a subagent instead of pasting inline. PathGuard-enforced. Exactly one of inline/from-file is required; both or neither is a clear error.

**`haops_config_read` size cap + chunking.** Default cap at 128 KB of inline content — large files return `{content[:128KB], truncated: true, size_bytes, cap_bytes, hint}` with a pointer to `chunk`. New `chunk=[start, end]` parameter for byte-range reads; the response echoes `chunk_start`/`chunk_end` and sets `more: true` + a paginate hint when there's more after the range. Replaces the silent MCP-result-size cap that bit a 173 KB dashboard file in the 04-15 gap doc.

**Timeline `haops_*` prefix.** Timeline rows now show `haops_config_apply`, `haops_batch_apply`, `haops_exec_shell` etc. — matching the MCP tool names the client actually calls. The bare audit log keeps the short form (so `ctx.audit.read_recent` / programmatic consumers are unaffected); display is handled in `_render_audit_entry` via a `_display_tool_name` mapping.

**Rollback↔apply visual pairing.** Each Timeline entry whose `details.transaction_id` matches another entry gets a `paired_with: {index, timestamp, tool, relation}` field — `relation` is `"rolled_back_by"` on apply rows, `"reverts"` on rollback rows. Frontend renders a small chip next to the row's tool name; clicking it scrolls + expands the paired entry with a brief indigo ring flash so you can trace an apply→rollback pair in one click. Lone applies (not yet rolled back) and lone rollbacks (apply outside the fetched window) render without the chip — no noise.

**Removed:** `haops_addon_options` backlog entry. User rejected outright 2026-04-17 ("drop this one completely — we will not do this at all"); memory updated to reflect the firmer stance so it's not re-proposed silently.

**Tests:** 469 → 486. Ruff + mypy clean.

## 0.18.2

Sidebar parity with MCP-flow rollback. v0.18.1 silently broke the "sidebar is read-only" principle by adding Prune/Clear buttons; this release makes that shift explicit and applies it consistently to Timeline revert.

**Sidebar is now read-mostly.** Stated explicitly in README + saved as a feedback memory so it doesn't rot again. Principle: admin-convenience mutations are allowed in the sidebar only when they mirror an MCP tool's exact code path, and each audit entry carries `source: "sidebar"` so Timeline rendering distinguishes UI-triggered from MCP-triggered. Novel operations still ship as MCP tools first.

**Timeline Revert button.** Each successful `config_apply` / `dashboard_apply` / `batch_apply` row now surfaces `transaction_id` at the top level when the transaction is still in-session memory. The expanded row shows a "Revert" button that fires `POST /api/ui/rollback` — shares the exact `haops_rollback` code path, preview-then-confirm flow in the browser. When the addon restarts and the transaction is gone, the button disappears and the user falls back to `haops_backup_revert` (with the out-of-memory-revert work queued in BACKLOG).

**`transaction_id` plumbed through** `config_apply` and `dashboard_apply` audit entries. `batch_apply` already had it since v0.17.0. All three apply types now consistently carry the txn anchor so the Revert button finds a match.

**New `POST /api/ui/rollback` endpoint.** Takes `{transaction_id, execute}`. Preview phase returns per-target action/diff summary; execute phase runs the rollback via the existing `_preview_undo` / `_execute_undo` helpers from `rollback.py`, audits with `source: "sidebar"`.

**Not yet:** Re-apply-after-rollback (rollback the rollback). Needs `haops_rollback` to create its own transaction capturing pre-rollback state — real design work, not just plumbing. Queued after we see how the revert button plays in production.

**Tests:** 462 → 469. Ruff + mypy clean.

## 0.18.1

Follow-up on v0.18.0. Retention controls were in the code and enforced, but never surfaced in the addon UI; and the sidebar Backups panel was read-only. This release closes both.

**Addon Configuration tab** — two new fields:

- `backup_max_age_days` (default 30)
- `backup_max_per_type` (default 100)

Wired through `run.sh` as `HA_OPS_BACKUP_MAX_AGE_DAYS` / `HA_OPS_BACKUP_MAX_PER_TYPE` env vars. `BackupConfig` already read these — they just weren't exposed. Existing installs inherit the defaults.

`backup.db_row_threshold` is **deliberately not exposed** — the current per-call semantics (warn when a single SQL statement estimates >N rows, in the `haops_db_purge` dry-run only) isn't useful at any realistic threshold and would become a footgun in a schema form. Parked in `docs/BACKLOG.md` pending a decision: remove the field, or rebuild as cumulative session-level tracking.

**Sidebar Backups panel** — three new actions:

- **Prune now** — runs the configured retention policy on demand. Browser shows a count + size confirm modal before firing.
- **Clear all now** — red button, wipes every backup across every type. Strong confirm.
- **Per-row Clear** — each type row in the By-type table has a small Clear action that wipes just that type.

All three go through a new `POST /api/ui/backup_prune` endpoint that shares the same `BackupManager.prune()` code path as `haops_backup_prune`. Audit entries carry `source: "sidebar"` so Timeline can distinguish UI-triggered prunes from MCP-flow prunes.

The MCP flow (`haops_backup_prune`) remains the primary path for scripted / LLM-driven pruning; these sidebar buttons are admin convenience for the cases where you just want to clean up without firing up a chat session.

**Tests:** 457 → 462. Ruff + mypy clean.

## 0.18.0

Backup lifecycle pass. Retention was configured but unused; no manual prune; no sidebar visibility. One release closes all three plus the default-directory move that was bundled in the same gap.

**Retention is enforced now.** `BackupManager.max_age_days` / `max_per_type` (defaults 30 / 100) finally do something. A prune pass runs once at startup (catches up on accumulated history from pre-v0.18.0 deploys) and after every successful backup write (bounds growth at source). Drops entries by age first, then per-type count cap (keep newest when the cap is the limiter). Files on disk get `unlink`ed; `manifest.jsonl` is rewritten atomically via tmp+rename — the ONE place the manifest is not append-only, called out with a comment at the rewrite site.

**New tool `haops_backup_prune`.** Two-phase preview/apply for manual pruning. `older_than_days` overrides the configured `max_age_days` for a single call; `type=config|dashboard|entity|db|all` filters the scope; `clear_all=True` is an escape hatch for a full wipe (still two-phase, still audit-logged). Shares the same `BackupManager.prune()` implementation as the automatic retention pass, so running it with defaults previews exactly what retention is already removing. Audit entry carries a compact `{id, source, type}` list per deleted backup — full `backup_path` stays out of the log.

**Sidebar Backups panel.** New Backups tab reading from a new `/api/ui/backups` endpoint. Shows total count, total disk usage, per-type breakdown (count/bytes/oldest/newest), effective retention settings, and the most recent prune entry with what it removed. Read-only — admin uses `haops_backup_prune` via the MCP flow for any mutation. Timeline gains `backup_prune` branches in `_summarise_audit_entry` ("Pruned N backup(s) …, freed X MB") and `_audit_details_excerpt` (compact totals, no `deleted` list dump). No `_recompute_audit_diff` branch — prune has no content diff.

**Default `backup_dir` moved off `/config/`.** New deployments get `/backup/ha-ops-mcp` (HA's `/backup` volume, already mapped `rw`). Existing deployments keep whatever they explicitly configured — nothing touches the addon options on upgrade. For deployments on the old default `/config/ha-ops-backups`: the addon detects legacy data at startup and logs a WARNING with migration guidance. **Migration is manual** — move files with `mv` or `scp`, rewrite `manifest.jsonl` paths to match the new location, OR set `backup_dir: /config/ha-ops-backups` in addon options to stay on the legacy path. The legacy tree is **outside** the configured backup dir, so retention never touches it.

**`haops_rollback` docstring tweak.** Dropped `haops_backup_revert` from the main "use this to undo X" sentence — the natural read was "un-revert" which confuses the tool's value prop. The generic tool still accepts any committed transaction id (including `backup_revert`), called out near LIMITS: "rolling back a revert re-applies the change that was reverted, if that's what you need."

**Tool count:** 57 → 58 (`haops_backup_prune`).
**Tests:** 437 → 457. Ruff + mypy clean.

## 0.17.1

Follow-up QoL on v0.17.0: `haops_rollback` preview and the Timeline tab both now include per-target diffs, so you can see exactly what the rollback changed without a follow-up read.

**Preview** — each entry in `targets[*]` carries `diff` + `diff_rendered` (unified diff for files, `format_json_diff` output for dashboards, "will delete <path>" for `config_create` undos). New `combined_diff_rendered` stitches them into one markdown block mirroring `haops_batch_preview`, so the approval modal is self-sufficient.

**Timeline** — `rollback` audit entries used to render as "rollback (no summary)" with only a target list. They now show: summary `Rolled back <operation> (N target(s))`, the stitched per-target diff in the diff panel, and a compact excerpt that lists `{target, action}` per item instead of dumping the full content payload.

Under the hood `_execute_undo` now captures the pre-rollback state of each target and stores it alongside the restored content in the audit entry, so `_recompute_audit_diff` can reconstruct the diff without re-reading files.

**Tests: 435 → 437.**

## 0.17.0

Clean up two gaps in the revert surface discovered while testing v0.16.0.

**`haops_batch_apply` now returns a `transaction_id`.** The batch tool
already backed up every target and rolled them back from backup on
mid-batch failure, but it didn't open a `RollbackManager` transaction
for the success path — so there was no drift-free way to undo a
just-applied batch. Now it records one savepoint per item (including
`was_created: True` for `config_create` items so rollback knows to
delete vs restore) and commits on success.

**New tool `haops_rollback(transaction_id)`.** Generic two-phase undo
for any committed in-memory transaction (batch_apply, config_apply,
dashboard_apply, backup_revert). Preview lists targets with per-item
action (delete / restore content / restore dashboard); apply dispatches
each `UndoEntry` by type. Uses the in-memory pre-write state — no
backup file read — so it sidesteps any drift HA introduced between
apply and rollback. Transactions are session-ephemeral (addon restart
loses them); older changes still go through `haops_backup_revert`. HA
side effects that fired during the original apply are NOT un-fired —
same caveat as everywhere else.

**`haops_backup_revert` preview annotates drift vs. intended revert.**
Full-file restore reverts everything that changed since the backup,
including HA's own rewrites (descriptions reformatted, `.storage/*`
re-serialised). The preview now looks up the matching `config_apply`
audit entry and, when found, returns:
- `intended_revert` — the reverse of the original apply (what you
  probably want)
- `drift_since_apply` — everything else the full-file restore will
  also touch
- `warning` — surfaced when drift is non-empty, nudging the user to
  prefer `haops_rollback(transaction_id)` if the in-memory
  transaction is still available.

Tool description updated with the drift caveat.

**Tools (56 → 57):** `haops_rollback`. Tests 424 → 435.

## 0.16.0

Removed `haops_config_diff` outright — deprecated in v0.15.0, gone in v0.16.0. No staged removal window: this is a single-user tool and the deprecation notice was cargo-culted ceremony. `haops_config_patch` covers edits, `haops_config_create` covers new files. `haops_config_apply` description and error messages point at the survivors.

`haops_dashboard_diff` stays — it handles full-config replace and view-swap flows that `haops_dashboard_patch` (RFC-6902 ops) doesn't cover cleanly.

**Tools (57 → 56).** Tests 430 → 424 (removed six tests that exercised `haops_config_diff` directly; integration tests that used it as a convenience to produce tokens now call `haops_config_patch` with a real unified diff).

## 0.15.0

Correctness-and-review pass. Three gaps from `_gaps/session_gaps_2026-04-16.md` landed together because they share the same audit-entry and patch-tool surface.

**§13 — atomic multi-file batch (the flagship).** New `haops_batch_preview` + `haops_batch_apply` tools. One token covers N targets; on any mid-batch failure, already-written targets are restored from backup in reverse order before the response returns. Supported item types: `config_patch`, `config_create`, `dashboard_patch`. Mixed item types compose cleanly — one approval modal, one combined diff, one audit entry. Atomicity is on-disk best-effort: HA side effects that fire between a write and a rollback stay fired (same caveat as the single-item flow). Motivating scenario the user flagged as *crucial*: renaming `climate.esphome_livingroom_ac_2 → ..._ac` across `automations.yaml` + `scripts.yaml` + a dashboard (18 refs, 3 files) — previously 3 separate token round-trips with no rollback if step K failed, leaving HA with a half-renamed config.

**§11 — `haops_config_diff` deprecated.** Same two-tool ambiguity the entity tools resolved in v0.11: `config_diff` ships the full proposed file as the tool-call payload, so the approval modal is a wall of text; `config_patch` ships only the changed lines. Responses now include `"deprecated": true` and a `deprecation_notice`. Tool description prepended with the DEPRECATED marker. Removal scheduled for v0.16.0 (tracked in `docs/BACKLOG.md`).

**§12 — Timeline now shows diffs inline for every mutation entry.** The recompute logic already existed; `haops_config_apply` / `haops_dashboard_apply` just weren't storing the old+new content in the audit entry so the UI had nothing to work with. Both now embed the pre/post state in `details`, and `batch_apply` entries render per-target diffs stitched into one block. Failed batch entries carry a `BATCH FAILED at <tool> on <target>` header and surface `rolled_back_count` in the details excerpt.

**New tool: `haops_config_create`.** Two-phase create for files that don't yet exist. Rejects if the path already exists (symmetric to `haops_config_patch` rejecting non-existent paths). Routes through `haops_config_apply` with empty `old_content`, so the diff is all-added and the apply audit entry surfaces as `Created <path>` in the Timeline.

**Tools (54 → 57):** `haops_config_create`, `haops_batch_preview`, `haops_batch_apply`.

**Tests:** 427 → 430.

## 0.11.0

Convenience-layer tools, driven by `GAP_INTERFACE_UX_ANALYSIS.md`. Three read-only additions that collapse common LLM patterns into one call so the controller stops falling back to `haops_exec_shell` for ad-hoc discovery.

**New tools (51 → 54):**

- **`haops_entity_find`** — fuzzy search across `entity_id`, `friendly_name`, device name, and area name. Backed by RapidFuzz (new dep) with weighted per-field scoring; friendly_name boosted because it's what users type. Optional `domain` pre-filter, `threshold`, `limit`. Returns ranked matches with `score` and `matched_field`. Repro from the session that drove this: "find the kitchen dehumidifier" — three failed `entity_list` filters collapse to one call.

- **`haops_dashboard_validate_yaml`** — pre-paste validator for Lovelace YAML. Catches the failure modes that surfaced as generic `'Cannot read properties of undefined (reading startsWith)'` dialogs in HA's editor: YAML parse errors with line numbers, missing card `type:`, decluttering-card `variables:` map-vs-list shape (the most common bug from the session log), unterminated `[[[ JS ]]]` template blocks, and field-type mismatches. Validates against bundled per-card schemas under `static/lovelace_card_schemas/` (core/`entities`, `entity`, `grid`, `vertical-stack`, `horizontal-stack`, `markdown`, `conditional`; custom/`button-card`, `decluttering-card`, `mushroom-template-card`). NOT a full HA-authoritative validator — unknown custom cards emit `warning`, not error, with a pointer to `haops_lovelace_resources`. Scope: `dashboard` / `view` / `section`.

- **`haops_lovelace_resources`** — list Lovelace frontend resources (Settings → Dashboards → Resources) plus per-dashboard resource overrides. Tier-1 reads `.storage/lovelace_resources`, tier-2 falls back to WS `lovelace/resources` for YAML-mode Lovelace. Optional `include_dashboard_usage` cross-links each global resource to the dashboards that reference it; dashboard-only resources surface with `scope: "dashboard"`.

**Why ship the lighter validator instead of vendoring HA's schema:** HA's authoritative Lovelace schema lives in `homeassistant/components/lovelace` and changes per release; vendoring is a multi-day port plus ongoing maintenance. The session's actual bugs are catchable with ~150 LOC + a small bundled schema directory, and a missing schema for an exotic card emits a `warning` (not an error) so the community can add card schemas via PR as they're encountered. See `static/lovelace_card_schemas/` for the format.

**Other changes:**

- New dep: `rapidfuzz>=3.6` — C-backed, ~3 MB wheel, no transitive deps. Used only by `haops_entity_find`.
- `haops_tools_check` lists the three new tools under the appropriate groups (`registries` for `entity_find`, `websocket` for `lovelace_resources`; `dashboard_validate_yaml` is pure-local and needs no probe).
- Tests: 347 → 379.

## 0.10.5

Controller-facing docs.

- Started `docs/ha_yaml_quirks.md` — living reference of HA YAML formatting traps the controller (LLM, Claude Desktop, etc.) needs to know about when reading/generating/pasting YAML against HA. First entries cover Lovelace raw-editor paste-back: folded `>-` vs literal `|-` for `[[[ ]]]` templates, blank-line counts, ~80-col wrap, `grid-template-areas` quoting, sequence indent.
- `haops_dashboard_apply` and `haops_dashboard_get` tool descriptions now point to `docs/ha_yaml_quirks.md` and warn that hand-generating paste-back YAML is fragile (HA's editor rejects format drift).
- Removed `GAP_YAML_PASTE_SERIALIZER.md` — its persistent quirks knowledge is now in `docs/ha_yaml_quirks.md`. The "let's vendor HA's YAML dumper" proposal in that doc was rejected: format-matching is brain work that belongs in the controller, not in ha-ops-mcp ("eyes and hands" scope from v0.10).

## 0.10.4

Dashboard read/write restored.

**Fixes:**
- `haops_dashboard_get`, `haops_dashboard_diff`, `haops_dashboard_apply`, and `haops_backup_revert` (dashboard backups) all crashed with `WebSocketClient.send_command() missing 1 required positional argument: 'msg_type'` whenever they fell back to the WebSocket path. Three sites built `kwargs={"type": "lovelace/..."}` and unpacked it, so the WS command name never reached the `msg_type` parameter. Switched all three to pass the command positionally.
- `haops_dashboard_get` filesystem tier now sanitises hyphens in `url_path` to underscores when building the storage filename. HA stores `url_path: "new-dashboard"` as `.storage/lovelace.new_dashboard`; the previous code looked for `lovelace.new-dashboard` and missed every storage-mode dashboard whose url_path wasn't a bare identifier — then fell through to the broken WS path above.
- `haops_tools_check` now round-trips a real `_get_dashboard_config` against the first non-default dashboard, so a regression in either tier (filesystem path build, WS kwargs shape) gets caught by the self-check instead of in the middle of a user session.

## 0.10.3

Fix: `dev-deploy.sh` was not syncing the new `translations/` directory to the host, so HA Supervisor never saw `translations/en.yaml` and the addon Configuration tab showed bare field names without descriptions. Script now copies `translations/` alongside `src/`.

## 0.10.2

Deploy tooling: `scripts/dev-deploy.sh` now refuses to run when HEAD isn't at the latest tag. `sync-version.sh` silently downgrades the deployed `config.yaml` version to whatever the latest tag is, and HA Supervisor then no-ops the rebuild because the version number didn't change (this hit us on the v0.10.0 / v0.10.1 commits, both shipped as 0.9.4 by the deploy script). Error message explains the two ways out: tag HEAD, or check out the tag you want to deploy.

No runtime changes.

## 0.10.1

Addon Configuration UX polish.

- Added `translations/en.yaml` so the addon Configuration tab shows a human-readable name + description next to every option (ha_token, transport, db_url, backup_dir, log_level).
- Dropped `stdio` from the `transport` dropdown. It only works for local CLI clients piping stdin/stdout — meaningless in addon mode. `config.example.local.yaml` still documents stdio for standalone users.

## 0.10.0

Scope-down. ha-ops-mcp is "eyes and hands" for a controller (LLM, Claude Code, etc.) — not a parallel integrity linter. This release removes the layers that were trying to be a brain.

**Removed:**
- `haops_graph` and `haops_issues` MCP tools.
- Inline `impact:` blocks on every mutation response (`haops_config_diff/apply`, `haops_dashboard_diff/apply`, `haops_entity_remove/disable`, `haops_db_execute`). The controller calls `haops_refactor_check` explicitly when it wants impact.
- `src/ha_ops_mcp/refindex/impact.py` (the impact analyzer module).
- `Issue` dataclass, `RefIndex.issues()`, `RefIndex.add_issue()`, all derived-issue computation (dangling refs, orphan customize, unused areas/devices, integration errors, source_kind tagging, transitive scene.create propagation).
- Sidebar **Graph** and **Issues** tabs; the `/api/ui/graph` and `/api/ui/issues` HTTP endpoints; the Cytoscape.js CDN script. Sidebar now has Overview / Pending / Health / Recent only.
- Refindex exclusion options: `refindex_exclude_dirs`, `refindex_exclude_globs`, `refindex_exclude_dashboards`, `refindex_dynamic_entity_patterns` and their matching `HA_OPS_REFINDEX_*` env vars. Defaults are now module-level constants in `refindex/builder.py`. HA's `config/check_config` is the integrity surface; we stop shipping a parallel one.
- `/api/ui/references/{node_id}` (unused by the current sidebar).

**Kept:**
- Refindex builder and `haops_references` / `haops_refactor_check`. The graph still exists; the controller walks it when it needs to.
- Two-phase `confirm=true` as the sole mutation gate.
- Everything else (database, config/dashboard edit, entity hygiene, system / addon / debugger / ergonomic tools).

**Follow-up candidate:** surface `config/check_config` criticals around `haops_system_restart` / `haops_system_reload` as a pre-flight gate — not bundled here.

Tests: 392 → 347.

## 0.9.4

- Drop `secrets.yml` from default `exclude_globs`. Uncommon variant; users with the canonical `secrets.yaml` are still covered. One fewer cosmetic entry to scan past.

## 0.9.3

Refindex defaults trimmed + better-documented.

- **Removed `known_devices.yaml`** from the default exclude_globs. Legacy HA device_tracker persistence file; modern installs don't even write it. The exclusion was purely cosmetic — zero runtime effect.
- **Inline rationale** added next to every entry in `_DEFAULT_EXCLUDE_DIRS` and `_DEFAULT_EXCLUDE_GLOBS`. Users deciding "do I want this scanned?" now have the *why* without grepping commit history.
- `secrets.yaml` AND `secrets.yml` both kept — HA accepts either form, both contain only `key: value` pairs (never entity refs), and listing both costs nothing if your install only has one.
- `custom_components` and `blueprints` stay excluded by default with explicit reasoning in the inline comments — these need smarter walking (theme-template detection / `!input` resolution) before they're useful, not a permissive default that floods the issue panel.

## 0.9.2

P0 bugfix: `action:` vs `service:` key in modern HA YAML.

HA 2024.8+ writes `action: scene.create` in scripts and automations (the frontend emits this form; the legacy `service:` key is still accepted). The scene.create / script-call detector in `_walk_for_dynamic_signals` only read `service:`, so every step written in the modern form was invisible — dynamic entities were never registered, and references to them stayed at `dangling_entity_ref` (high severity) instead of `dynamic_entity_ref` (info).

Fix is surgical: `service = obj.get("action") or obj.get("service")` — coalesce both keys, `action` wins when both present (matches HA's own loader precedence). No other walker in the refindex reads a service verb, so the bug is confined to this one function. Both keys remain supported indefinitely — HA accepts both, we must too.

Users affected: anyone whose scripts were created/edited in HA 2024.8+ will see hundreds of false-positive `scene.temp_*` dangling refs drop once v0.9.2 is deployed.

Tests: 390 → 392.

## 0.9.1

Two fixes from the v0.9.0 deploy.

**Materialized refindex defaults in addon `options:`.** The Configuration tab now shows the full default `exclude_dirs` and `exclude_globs` lists pre-populated, so users can see exactly what's filtered without reading source. Fresh installs get them automatically. **Existing instances** with saved-empty options will still see `[]` in the UI (HA Supervisor preserves user-saved values across updates) — click **Reset** in the addon Configuration tab to pick up the new defaults, or just leave it: runtime behavior is unaffected because empty addon-option still falls through to the Python `default_factory`.

**Effective-exclusions startup log.** Server logs `refindex effective exclusions: N dirs, N globs, ...` plus the actual lists at startup, so you can verify what's in force from the addon Log tab regardless of how options were saved.

**Transitive `scene.create` propagation.** v0.9.0's detector caught `scene.create` only when the same source referenced the resulting scene. Real workflows often factor the snapshot/restore into a helper script (`script.snapshot_and_run`) called from many parents — references from the parent scripts were still flagged dangling. The detector now builds a per-source script call graph from `service: script.X` shorthand AND `service: script.turn_on` with a `target.entity_id: script.X`, then propagates dynamic entities through the call graph via fixpoint iteration (cycle-safe). If A calls B and B calls `scene.create`, A's references to that scene also get the `dynamic_entity_ref` (info) treatment.

Tool count unchanged. Tests: 388 → 390.

## 0.9.0

Refindex issue-tuning — second wave. Picks up where v0.8.10 left off, adding runtime-entity detection, provenance tagging, and explicit user-visible defaults.

**Runtime-created entity detection.** Scripts and automations that call `service: scene.create` with `data.scene_id: X` now register `entity:scene.X` as a dynamically-created entity scoped to that source. When the same source later references `scene.X` (in `scene.turn_on` / `scene.apply` / etc.), the reference is emitted as `dynamic_entity_ref` (info severity) instead of `dangling_entity_ref` (high). Scope is per-source — a `scene.create` in script A does NOT mask a typo for the same scene name in automation B. Eliminates the whole false-positive class for the common snapshot/restore pattern.

**Config-driven `dynamic_entity_patterns`** — for runtime creators we don't auto-detect (input_text set at boot, MQTT discovery entities). Patterns are fnmatch globs over the bare entity_id (`scene.temp_*`, `input_text.runtime_*`). Matches get the `dynamic_entity_ref` treatment.

**Explicit defaults in config.** `RefindexConfig` now materialises the full default `exclude_dirs` / `exclude_globs` lists in Python, mirrored in `config.example.local.yaml`. Users can see exactly what's being excluded and remove an entry if they want it scanned. The hardcoded `_LOOSE_SCAN_SKIP_DIRS`/`_LOOSE_SCAN_SKIP_SUFFIXES`/`_LOOSE_SCAN_SKIP_FILES` frozensets in `builder.py` are gone — everything reads from config. A user's list REPLACES the defaults (if you set `exclude_dirs: []` you get zero exclusions); a missing YAML field applies the defaults. The addon's `run.sh` only exports the env var when the option is non-empty, so leaving the addon option empty also falls back to defaults.

**`exclude_dashboards`** — new config field listing dashboard url_paths to skip in the structured `.storage/lovelace.<slug>` pass AND the YAML-mode dashboard pass. Useful for ULM-style dashboards that generate enormous card counts you don't want in the graph.

**Provenance tagging on every `Issue`.** New `source_kind` field: `user` (default, actionable), `vendored` (originates in `custom_components/`), or `backup` (`_backup_/`, `backup(s)/`, `.bak`/`.disabled`/etc.). Computed from the source file path via `_classify_source_kind`.

**Duplicate aggregation.** `RefIndex.issues()` now collapses identical `(code, node_id, related, severity, source_kind)` tuples with a `count` field. A cycle reached via many entry points shows once with `count=N`. Pass `group=False` for raw.

**`haops_issues` tool gains params.** `severity`, `code`, `include_noise` (default false — hides vendored/backup), `group` (default true). Response includes `source_kind`, `count` per row, `by_source_kind` summary.

**Sidebar Issues tab** updated: new "Include noise" checkbox; table adds Source and Count columns; count displays `{rows} rows · {count} total` to show the aggregation.

**Addon options + env vars.** New: `refindex_exclude_dashboards`, `refindex_dynamic_entity_patterns` in the addon Configuration tab. New env vars: `HA_OPS_REFINDEX_EXCLUDE_DASHBOARDS`, `HA_OPS_REFINDEX_DYNAMIC_ENTITY_PATTERNS`.

Tool count unchanged: 51. Tests: 381 → 388.

## 0.8.10

Issue-panel noise fix — targeting ~3,600 false positives reported on a real instance down to under 200 actionable items.

**Loose YAML scan — expanded skip list.** Added `custom_components`, `_backup_`, `backup`, `backups` to the default directory skips (vendored theme packages like ULM and backup snapshots were the dominant noise sources). Added `.bak`, `.disabled`, `.old`, `.orig`, `.backup` to a new file-suffix skip list so stale copies of real YAML files don't re-emit their refs.

**User-extendable exclusions** — new addon options `refindex_exclude_dirs` and `refindex_exclude_globs` (list values, comma-separated at the env-var level), accessible in the addon's Configuration tab alongside `backup_dir` etc. Work like gitignore: user entries **add to** the built-in defaults — never replace them, so out-of-the-box behavior can't regress. Static per-instance config, not interactive.

**True-cycle detection in `HaYamlLoader`.** The previous implementation flagged any re-entry of a file as circular — wrong for the common pattern of one file `!include`'d from many independent parents (ULM card templates do this hundreds of times). Replaced the `_visited` set with an active include stack: a file is only flagged as circular when it appears in its own ancestor chain (A → B → A). Cycles are also de-duplicated: one issue per distinct cycle signature regardless of how many entry points reach it.

**Provenance filter on `dangling_entity_ref`.** Refs originating in `custom_components/`, `_backup_/`, `backup(s)/`, or user-excluded paths are suppressed — backup files intentionally reference deleted entities. `.storage/` is NOT in this set (it contains the user's real dashboards), so legitimate dangling refs stay visible.

**Issues from inside excluded paths are also suppressed at load time** (`broken_include`, `circular_include`, `path_traversal` emitted from within a vendored/backup tree no longer bubble up).

Not in this release — deferred to v0.8.11: provenance tagging on `Issue` (`source_kind` field), duplicate aggregation (`{code, node, related}` → single entry with `count`), disabled-automation filter for dangling refs.

## 0.8.9

Docs sync — no behavior change.

- README test count 363 → 376 (3 places: header, architecture tree, roadmap implemented section).
- README **Tools** section: `haops_entity_list` description now mentions the v0.8.6 default-summary behavior + `full=true` opt-in. `haops_entity_audit` description mentions area entity:device ratio outliers (v0.8.7). `haops_entity_remove` / `haops_entity_disable` clarify they use WS, not REST, and that `success` reflects errors. `haops_system_backup` rewritten to describe Supervisor-first + REST fallback.
- README **Capability matrix** updated: entity_remove/disable rows show WS dependency (was REST); system_backup row shows Supervisor preferred / REST fallback.
- README **Reference graph tools** section now lists the loose YAML scan (v0.8.6) as a coverage tier.
- README **Roadmap → Implemented** gains a v0.8.x patches bullet summarizing the post-0.8 fixes (loose YAML, entity_list summary, ratio outliers, WS switches, supervisor backup, sidebar Health tab + dark mode).

## 0.8.8

Two P0 fixes from real-world feedback. Both involve apply paths that returned `success: true` despite 100% failure — that misleading flag is also fixed.

**`haops_entity_disable` apply step.** Was calling `POST /api/config/entity_registry/<id>` which HA removed from the REST API; every call returned HTTP 404. Switched to WS `config/entity_registry/update` (the only working path; the read-side `_get_entity_registry` already used WS fallback for the same reason). The apply response now reports `success: not errors` instead of always `true`. Same fix applied to **`haops_entity_remove`** (was using `DELETE /api/config/entity_registry/<id>`; now WS `config/entity_registry/remove`).

**`haops_system_backup` HTTP 400.** The Core REST `backup.create` service was inconsistent across HA versions and didn't expose a slug for follow-up status checks. Tool now prefers Supervisor `/backups/new/full` (the right endpoint for HA OS / Supervised installs — fast, non-blocking, returns the new backup's slug). Falls back to Core REST (`backup.create` then `hassio.backup_full`) when Supervisor isn't reachable. New params: `password` (encrypts the archive), `compressed` (default true). When all paths fail, returns `success: false` with both supervisor + core error messages instead of pretending it worked.

## 0.8.7

`haops_entity_audit` gains an area entity-to-device ratio outlier check.

Real-world signal: areas where a small number of devices map to a disproportionate number of entities — typical of integrations like pfSense, UPS monitors, or weather services that register hundreds of sensors against one device assigned to that area, distorting the area's apparent scale.

Algorithm: per area, compute `entity_count / device_count` (effective area, so device-inherited assignments count). Across all areas with ≥10 entities, take the median ratio. Flag any area whose ratio exceeds `max(3 × median, 20)` and surface as `area_ratio_outliers: [{area_id, area_name, entities, devices, ratio}]` ordered by descending ratio. Returns empty when fewer than 3 areas qualify (no median to compare against).

## 0.8.6

P0 + P1 fixes from real-world feedback.

**P0 — Refindex now sees community-themed YAML dashboards.** Added a "loose YAML scan" pass that walks every `*.yaml`/`*.yml` under `config_root` not already covered by the structured passes. Catches power-user setups that split dashboards across many YAML files in custom directories (e.g. `ui_lovelace_minimalist/dashboard/views/*.yaml`) without registering them under `lovelace.dashboards.*`. Refs found in these files emit `references` edges from a synthetic `yaml_file:<rel_path>` node, so `haops_references` and `haops_refactor_check` see the full picture. Skips: `.storage/`, `.git/`, `esphome/`, `blueprints/`, hidden dirs, `secrets.yaml`, and any file already loaded by the structured pass (no duplicate edges).

**P1 — `haops_entity_list` default response shrunk.** Previously returned an 8-field summary per entity (entity_id, friendly_name, state, last_changed, area_id, platform, device_id, disabled_by). On areas with hundreds of entities this hit MCP result-size limits (116 KB on a 419-sensor query). New default returns 3 fields: `entity_id`, `friendly_name`, `state`. Added `full=true` opt-in for the previous verbose payload. Explicit `fields=[...]` still wins when set.

## 0.8.5

Sidebar Health tab now shows the *what* and *why*.

- **Self check**: each check is rendered as a card with the status badge
  on the right, the error message highlighted in red (when present), and
  every other field (`ha_version`, `backend`, `config_root`, `writable`,
  `dashboard_access`, etc.) shown as key:value pairs underneath. No more
  guessing what made it `degraded` — you see exactly which sub-field
  failed.
- **Tools check**: each group is rendered as a card with the status badge
  on the right, the per-test breakdown (`api_config`, `api_states`,
  `api_single_state`, `dashboard_get`, etc.) listed inside with an
  individual ok/fail badge per test plus the test's own summary fields.
  Errors show up next to the failing test, not buried in the parent
  group's summary. The tools_affected list now only renders when the
  group is degraded — when everything's passing it's noise.

## 0.8.4

Sidebar Graph: bare entity_id input now works.

- Typing `sensor.temperature` (without the `entity:` prefix) in the Graph
  tab returned `HTTP 404`. `/api/ui/graph` and `/api/ui/references/{id}`
  now resolve bare entity_ids to typed form (`entity:sensor.temperature`),
  matching the resolver `haops_references` already uses.
- Sidebar JS: `fetchJson` now surfaces the server-side error body
  (`Unknown node 'X'`) instead of a bare `HTTP 404` so failures explain
  themselves.

## 0.8.3

Docs sync — no behavior change.

- REQUIREMENTS.md §11 (Extensibility Model) now carries an explicit PARKED header pointing to the v0.8.2 decision. The original spec is preserved as historical reference; nothing is in force.
- REQUIREMENTS.md §5 example `config.yaml` no longer includes `recipes_dir`/`scripts_dir`/`checks_dir` (`ExtensibilityConfig` was removed in v0.8.2).
- CLAUDE.md updated: no longer instructs to add the extensibility config fields; tool-registry comment about post-MVP recipes removed (matches `server.py`).

## 0.8.2

Scope decision + cleanup. No behavior change.

- Recipe runner / atomic multi-surface transactions explicitly **parked** for v1.0. ha-ops-mcp stays as "hands for HA power users"; multi-surface ops like rename are sequenced by the LLM controller using `haops_refactor_check` + per-surface `_diff` / `_apply` tools, with a confirm gate per surface. Full parked spec preserved at `~/.claude/plans/scalable-wiggling-hennessy.md`.
- Removed `ExtensibilityConfig` (recipes_dir / scripts_dir / checks_dir) from `src/ha_ops_mcp/config.py` — no consumers, scaffolding for the parked direction.
- Removed empty `recipes/` and `checks/` placeholder directories. `scripts/` (real dev scripts) kept.
- Dropped stale "post-MVP" comment from `src/ha_ops_mcp/server.py`.
- README "Roadmap → Planned" no longer mentions extensibility; new "Future direction: recipe runner (parked)" section explains the decision.

363 tests pass, ruff + mypy --strict clean.

## 0.8.1

Patch release driven by real-world feedback from a 2,800-entity instance.

**Fixes (P0 correctness):**
- `haops_template_render` — was calling `.json()` on `/api/template`, but HA returns `text/plain`. Every call failed. Added `RestClient.post_text` and switched the tool to use it.
- Reference indexer — YAML-mode dashboards (configured via `lovelace:` in `configuration.yaml`, with `mode: yaml` and per-dashboard `filename:` pointing at user YAML files with `!include` chains) were silently invisible. Indexer now walks them: default override (`ui-lovelace.yaml`), each named YAML dashboard, and any `!include`/`!include_dir_*` chains beneath. Dashboards with missing `filename:` emit a `dashboard_yaml_missing` issue.
- Sidebar Issues panel — `:key="i.node_id + i.code"` collided when one node had multiple issues of the same code (e.g. one dashboard view with 5 dangling refs). Alpine deduplicated the rows so only the first rendered. Same bug in pending broken-refs and Recent / Audit log lists. Switched all to index-based keys.
- Sidebar Graph — full-graph default render was attempting force-directed layout on 3k+ nodes, freezing the browser. `/api/ui/graph` no-focus now returns counts-only catalog by default; explicit `include_nodes=true` opt-in caps at 500 nodes. Subgraph endpoint also gained a `limit` parameter.
- `haops_dashboard_get` — `view="3"` (numeric string from MCP coercion) returned "no view with index/path/title '3'". Resolver now tries integer-string as index before falling back to path/title lookup.

**Sidebar UX additions:**
- Dark mode (auto-follows `prefers-color-scheme`, manual override with cycle button in header, choice persisted in `localStorage`).
- Server version shown in the top header (sourced from `/api/ui/overview`).
- New **Health** tab combining `haops_self_check` (REST/WS/DB/FS/backup connectivity) and `haops_tools_check` (per-group capability matrix with broken-tools list).

## 0.8.0

Sharpen + close edges. This release adds ergonomic wrappers for the most common "fire it now" operations, bulk entity area reassignment, integration reload, and entity registry customization — closing gaps that previously forced users into `haops_service_call` or the HA UI.

**New tools (6):**
- `haops_automation_trigger` — fire an automation by entity id.
- `haops_script_run` — run a script by entity id.
- `haops_scene_activate` — activate a scene by entity id.
- `haops_integration_reload` — reload a config entry via WS `config_entries/reload`. Useful after editing integration options or when an entry is in `setup_retry`.
- `haops_entities_assign_area` — bulk area reassignment. Two-phase confirm. Empty `area_id` clears the assignment.
- `haops_entity_customize` — update entity registry options (name, icon, unit_of_measurement, device_class) via WS `config/entity_registry/update`. Two-phase confirm.

**Tool count: 45 → 51.** Tests: 339 → 354.

## 0.7.0

Debugger release: the tools needed to answer "why didn't my automation fire?" and "what will this template render to?" without shelling out. Also folds Jinja-aware reference extraction into the v0.6 refindex, so templated automations/sensors register their real dependencies.

**New tools (5):**
- `haops_entity_history` — wraps REST `/api/history/period/<ts>`. One or many entities, arbitrary time window, `minimal_response` and `significant_changes_only` flags for payload control.
- `haops_logbook` — wraps REST `/api/logbook/<ts>`. Narrative event stream (automation triggers, script runs, device status changes), optionally filtered to one entity.
- `haops_template_render` — wraps POST `/api/template`. Preview what a `value_template:` produces against live HA state before committing it to an automation. Supports local `variables`.
- `haops_service_list` — wraps WS `get_services` with REST fallback. Returns full service schemas (field names, descriptions, required flags) so LLMs compose correct `haops_service_call` invocations.
- `haops_automation_trace` — wraps WS `trace/list` and `trace/get`. Lists recent runs for an automation, or returns the full step-by-step trace for a specific run.

**Refindex — Jinja-aware reference extraction (Layer 2):**
- The YAML walker now recurses into string scalars and extracts entity refs from embedded Jinja: `states('x')`, `state_attr('x', 'y')`, `is_state('x', ...)`, `is_state_attr(...)`, `expand(...)`, `has_value(...)`, and the attribute-access form `states.domain.object_id`.
- Extracted refs respect the surrounding edge context (trigger block → `triggered_by`, condition → `conditioned_on`, etc.), so a template-based trigger correctly registers as triggering the automation.
- Templated entity ids like `states(var)` can't be resolved statically — documented limitation; those are v0.8+ work.

**tools_check:** new `debugger` group probing `/api/template` and WS `get_services`.

**Tool count: 40 → 45.** Tests: 307 → 339.

## 0.6.0

Referential integrity release. The server now builds a typed dependency graph across every HA config layer and consults it automatically inside the mutation flow — every diff/apply tool embeds an impact summary so LLM and human see what's about to happen before they pull the trigger.

**New — reference graph:**
- HA-compatible YAML loader resolves `!include`, `!include_dir_list`, `!include_dir_merge_list`, `!include_dir_named`, `!include_dir_merge_named`, `!secret`, `!env_var`, and `homeassistant.packages`. Every resolved path runs through the path guard; broken includes degrade to Issues, not crashes.
- Reference indexer (`src/ha_ops_mcp/refindex`) walks registries, automations, scripts, scenes, groups, customize, template sensors, and `.storage/lovelace*` dashboards. Produces typed nodes (`entity:*`, `device:*`, `area:*`, `automation:*`, `script:*`, `scene:*`, `dashboard:*`, `dashboard_view:*`, `group:*`, `customize:*`, `template_sensor:*`, `floor:*`, `config_entry:*`) and typed edges (`belongs_to`, `located_in`, `provides`, `contains`, `references`, `targets`, `triggered_by`, `conditioned_on`, `renders_on`, `customizes`). Dashboard walker has no card-type allowlist — custom cards (mushroom, button-card, stack-in-card) get indexed for free as long as they use the conventional `entity:`/`entities:` keys. Jinja template refs inside `{{ ... }}` are explicitly a v0.7 concern.
- Issues computer derives `dangling_entity_ref`, `missing_device_link`, `orphan_customize`, `unused_area`, `unused_device`, `integration_error`, `broken_include`, `missing_identifier`.
- Impact analyzer (`refindex/impact.py`) dispatches per token action: config_apply / dashboard_apply / entity_remove / entity_disable / backup_revert get ref-level analysis (added_refs / removed_refs / broken_refs / affected_nodes); db_execute / exec_shell / system_restart get opaque warn-level summaries. Severity is advisory only — `confirm=true` remains the sole mutation gate.

**New tools (4):**
- `haops_references` — incoming + outgoing refs for any node. Accepts typed ids (`entity:sensor.x`) or bare entity_ids.
- `haops_graph` — subgraph around a focus node, JSON or Mermaid format, configurable depth.
- `haops_refactor_check` — "what breaks if I rename/delete X?". Returns impact + per-file ref counts + per-location edit pointers. Caller composes actual edits via existing `haops_config_diff` / `haops_dashboard_diff`.
- `haops_issues` — problem list with severity filter.

**MCP flow integration:**
- `haops_config_diff` / `_apply`, `haops_dashboard_diff` / `_apply`, `haops_entity_remove` / `_disable`, `haops_db_execute` now return `impact: {...}` inline with their diffs. Apply phase rebuilds the index from current state and re-runs impact so drift between preview and apply is visible. No new gate parameters — the two-phase `confirm=true` model is untouched.

**New — read-only sidebar UI (HA ingress panel):**
- Single-file SPA at `/ui` (Cytoscape.js + Alpine.js + Tailwind via CDN, no build step). Five tabs: Overview, Graph, Pending, Issues, Recent. Clicking a graph node refocuses; clicking an issue jumps to that node in the graph.
- HTTP API: `/api/ui/overview`, `/api/ui/graph`, `/api/ui/references/{id}`, `/api/ui/issues`, `/api/ui/pending`, `/api/ui/pending/{id}`, `/api/ui/recent`. ETag on `/ui` with `Cache-Control: no-cache, must-revalidate` so browsers revalidate but don't serve stale HTML after addon rebuilds.
- Auth: trusted via `X-Ingress-Path` header or loopback; otherwise requires `Authorization: Bearer <token>` matching `HA_OPS_TOKEN` or `SUPERVISOR_TOKEN`.
- Addon config: `ingress: true`, `ingress_port: 8901`, `ingress_entry: ui`. Panel icon changed to `mdi:graph-outline`, title to "HA Ops".

**Safety layer extensions:**
- `SafetyManager.list_tokens(include_consumed=False)` — enumerate active tokens.
- `AuditLog.read_recent(limit=50)` — tail the JSONL audit log.
- `Transaction.token_id` optional field threads through `RollbackManager.begin()` so rollback → audit → pending correlation works.

**Tool count: 36 → 40.** Tests: 188 → 307.

## 0.5.0

Addresses four operational gaps found during dashboard editing and device diagnostics on a real instance.

**New:**
- `haops_entity_state` (Gap 8): full state + attributes for one or a batch of entities. Without this, climate/media_player/sensor diagnostics had no way to read `current_temperature`, `brightness`, `unit_of_measurement`, etc. — only the bare state string. Optional `attributes` projection (`[]` = no attributes) caps payload for large entities.

**Improved:**
- `haops_dashboard_diff` + `haops_dashboard_apply` (Gap 11): new single-view replace mode. Pass `view` (index, path, or title) + `new_view`, and the server composes the full config internally. Adding one card to one view in a 15-view dashboard no longer requires round-tripping 60+ KB of unrelated views. Full `new_config` mode still works. View-append mode (omit `view`) also supported.
- `haops_dashboard_get` (Gap 10): new `summary=True` mode returns a lightweight view index `[{index, title, path, icon, type, section_count, card_count}]` — cheap enumeration without view bodies. `view` parameter now also accepts a path or title string, not just an integer index. "Find the ha-ops-lab tab" goes from 15 sequential calls to 1.
- `haops_entity_list` (Gap 9): new `area_mode` parameter. `area_mode='effective'` (default) matches on `entity.area_id OR device.area_id`, so entities that inherit area from their device (the common case) are found when querying by area. `'entity'` preserves strict entity-only matching. `'device'` matches on device area only.
- `haops_entity_list`: `area` parameter now accepts area name too, not just area_id.
- `haops_tools_check`: REST group probes `/api/states/<id>` (the `haops_entity_state` path).

**Tool count: 35 → 36.** Tests: 170 → 188.

## 0.4.0

Real-world ops probing surfaced seven gaps in the tool surface on a 2,800-entity production instance. This release addresses the P0 and P1 items.

**New tools:**
- `haops_registry_query` — generic `.storage/core.*` primitive. Supports `devices`, `entities`, `areas`, `floors`, `config_entries`. Case-insensitive substring filter across any field, projection via `fields`, pagination via `limit`/`offset`, and `count_only` for size checks. Filesystem-first with WebSocket fallback. Answers "which devices exist", "which integrations failed to load", "what areas/floors are defined" without shell fallback.
- `haops_device_info` — ergonomic wrapper: single device by id or name substring, returns full record + linked entities with current state + area name resolution. Disambiguates when multiple devices match.

**Improved:**
- `haops_entity_list` gains pagination (`limit`, `offset`, `count_only`) and projection (`fields`). Backward compatible: no default limit. New response shape includes `total`, `returned`, `truncated`. The tool description warns that unbounded output can exceed LLM tool-result size on large instances.
- `haops_config_search` default scope widened from `*.yaml` + `esphome/*.yaml` + `automations/*.yaml` to recursive `**/*.yaml` + `**/*.yml` — covers scripts/, packages/, dashboards/, etc. This fixes silent false negatives on "what references entity X?" queries.
- `haops_config_search` adds `include_registries=true` opt-in to scan `.storage/core.*` JSON files — makes device/entity registry data searchable without shell.
- `haops_tools_check` adds a new **registries** group that probes each supported registry file and reports counts.
- `haops_device_info` name matching now looks across `name_by_user`, `name`, `model`, `manufacturer` (previously only `name_by_user` via `_device_display_name`).

**Tool count: 33 → 35.** Tests: 144 → 170.

## 0.3.0

First fully-validated deployment against real Home Assistant instance (HA OS 17.2 / HA Core 2026.4.2 / Supervisor 2026.03.3 / MariaDB 10.11.6).

All 6 backend groups passing in `haops_tools_check`:
- REST API, WebSocket, Database, Filesystem, Supervisor API, Shell execution
- 33 tools, 0 known broken tools
- Works with Supervisor-injected token (addon default) and long-lived user tokens

No new features since 0.2.2 — this tag marks the v1-candidate milestone.

## 0.2.2

- **System logs** (`haops_system_logs`): add Supervisor `/core/logs` (journald) as a middle fallback between the optional `/config/home-assistant.log` file and the REST `/api/error_log` endpoint. HA OS doesn't write to the log file by default — journald via Supervisor is the canonical source.
- **Supervisor info check** in `tools_check`: report `supervisor_version`, `homeassistant_version`, `hassos_version`, and `arch` (was incorrectly trying to read `version` which doesn't exist at that key).
- `tools_check` filesystem group: `home_assistant_log` absence is no longer a failure — it's an informational check indicating log source selection.

## 0.2.1

- **Entity registry**: switch REST fallback to WebSocket (`config/entity_registry/list`). HA removed the REST endpoint `/api/config/entity_registry`. Filesystem still preferred.
- **System logs**: read `/config/home-assistant.log` directly (filesystem-first), fall back to `/api/error_log` if needed. Avoids 404s through the Supervisor proxy.
- **Addon config.yaml**: add `hassio_role: manager` so `haops_addon_*` tools can list and manage other add-ons.
- `tools_check`: drop tests for dead endpoints; add a check for the HA log file.
- Capability matrix: added **Token type** column (Any / Sup-only) to clarify which tools require the Supervisor-injected token vs. work with any token.

## 0.2.0

- Dev workflow overhaul: new Makefile targets
  - `make update` — sync source + rescan store + apply update (preserves addon options; the proper flow for config.yaml changes)
  - `make refresh` — rescan the app store so HA picks up config.yaml changes
  - `make reinstall` — full reinstall (warns: wipes addon options)
  - `make rebuild`, `make logs`, `make dev-update`, `make dev-deploy` refined
- Fixed: `ha store reload` (was incorrectly `ha apps reload`)
- Consistently use `ha apps` (deprecated: `ha addons`)
- Documented: decision tree for which target to use based on what changed

## 0.1.3

- Full addon deployment working end-to-end with the default Supervisor token (no manual token setup required).
- Route both REST and WebSocket through Supervisor proxy when using SUPERVISOR_TOKEN; route both directly to HA Core when a custom long-lived token is configured.
- Fix `haops_config_validate`: HA 2026.4 no longer exposes `config/check_config` WS command; now calls `homeassistant.check_config` service via REST with WS fallback.
- Improve WebSocket error handling: wrap exceptions with useful diagnostic info (close codes, auth rejection messages), auto-reconnect on dead connections.
- Fix WS ping/pong handling: pong responses no longer treated as command failures.
- Updated `haops_self_check` and `haops_tools_check` to use `lovelace/dashboards/list` and `get_config` as WS probes (replacing the deprecated `config/check_config`).
- Documentation: comprehensive capability matrix showing backend dependencies (REST/WS/DB/FS/Shell/Supervisor) for each of the 33 tools.
- Docs: Makefile workflow (`make dev-update`, `make logs`, `make check`).
- Updated `ha apps` commands (replaced deprecated `ha addons`).

## 0.1.2

- Add haops_tools_check: passive integration test validating each tool group (REST, WS, DB, filesystem, Supervisor, shell) with read-only operations
- Improve WebSocket auth error messages (surface auth_invalid messages and close reasons)
- Improve DB schema_version detection: multiple fallback queries for different HA schema layouts (MariaDB, PostgreSQL)

## 0.1.1

- Fix REST client URL building (aiohttp base_url path replacement issue)
- Fix connection lifecycle: REST/WS initialized via FastMCP lifespan hook
- Fix DB auto-detect: resolve !secret tagged scalars from ruamel.yaml
- Fix s6-overlay v3 compatibility (shebang, init flag)
- Fix SSE transport as addon default (stdio exits without client)
- Fix tool parameter schemas: expose real function signatures to FastMCP
- Split REST/WS URLs: Supervisor proxy for REST, direct for WebSocket
- Add haops_self_check tool with per-component diagnostics
- Expose port 8901 by default for SSE transport
- Updated docs: claude mcp add CLI, installation workflow, contributing guide
- Known issues: WebSocket auth with Supervisor token, DB schema_version null on MariaDB

## 0.1.0

- Initial release
- 32 MCP tools: database, config, dashboard, entity, system, service, backup, shell, addon management, self-check
- Two-phase confirmation for all mutating operations
- Ephemeral transaction/savepoint rollback system
- Persistent backup for config files and destructive operations
- SQL guard, path guard, shell guard with optional bypass
- SQLite, MariaDB, and PostgreSQL support
- Append-only audit log
