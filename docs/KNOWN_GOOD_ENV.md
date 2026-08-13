# Known-Good Environment Baseline

A snapshot of the **full client+server stack** versions at which the ha-ops-mcp ↔ Claude Code
connection is verified working end-to-end (connectivity + OAuth + all backends).

When the MCP connection breaks "for no reason", **diff the live environment against the most
recent baseline below** before chasing anything. Most "mystery" breakages are an environment
component updating underneath us — the classic being a **terminal-app update resetting macOS
Local Network permission** (see `CONNECTIVITY_TROUBLESHOOTING.md`).

Maintaining this file is an **agent routine, not a user chore** (see CLAUDE.md → "Known-good
environment baseline"). The agent appends a new row automatically — after cutting a release/tag,
or after confirming a clean `haops_self_check` following any environment change — by gathering the
versions and tying the row to the current git tag. **Keep old rows** — the history is the diff.

## Baselines

### `v0.60.2` — verified 2026-08-13 (**Poland HA** — first baseline with the ESPHome tools)

Stack unchanged from the `v0.57.0` row below except macOS and Node: HA **2026.8.1**, Supervisor
**2026.07.5**, HAOS **18.2**, Docker **29.6.2**, MariaDB schema **53**, Claude Code **2.1.226**,
iTerm2 **3.6.11**, macOS **26.5.2** (build 25F84), Bun **1.3.14**, Node **v26.5.0**.

- `haops_self_check` → `overall: ok`, `ha_ops_version: 0.60.2`, `docker: ok` (19/19 containers).
- `haops_tools_check` → **`all_pass`, 15/15 groups, 0 broken tools** — first run including the new
  `esphome` group (8 node configs parsed, builder container found). Live scale for reference:
  1089 states, 1352 registry entities, 142 devices, 50 config entries (11 with
  `supports_remove_device`), 2,088,704 `states` rows, refindex 1617 nodes / 2745 edges.
- **Device registry is storage v3.2** on this instance (migrated 2026-08-05). Anything reading a
  device's config entries must go through `storage_registry.device_config_entry_ids()` — see
  HA_COMPATIBILITY.md. This is the first baseline verified against the post-split schema.
- ESPHome toolchain reachable at `app_5c53de3b_esphome` (ESPHome **2026.7.4**). A live cold compile
  of `pl-office-powerstrip` took **251 s** — hence the 110 s default timeout on
  `haops_esphome_build`, which sits under the ~120 s MCP client limit so the call returns
  "still compiling" instead of dying. Build artifacts land in
  `/config/esphome/.esphome/build/<node>/` on this version, not the add-on's `/data/build`.

### `v0.58.0` — verified 2026-08-12 (**Poland HA**)

Whole stack **identical to the `v0.57.0` row below** — HA 2026.8.1, Supervisor 2026.07.5, HAOS 18.2,
Docker 29.6.2, MariaDB schema 53, Claude Code 2.1.226, iTerm2 3.6.11, macOS 26.5.2, Bun 1.3.14,
Node v26.5.0. Only the add-on version moved. Deliberately kept as its own row rather than folded
into the one below, because a row per shipped tag is what makes the history diffable.

- `haops_self_check` → `overall: ok`, and it now includes
  **`docker: ok — /run/docker.sock, 19 containers, 19 running`** (new in v0.57.1).
- `haops_addon_update` exercised live on both a third-party add-on and our own slug; both correctly
  reported `already_latest` with no rebuild fired. The self-refusal branch can't be exercised while
  we're current — `already_latest` short-circuits ahead of it by design.

### `v0.57.0` — verified 2026-08-12 (**Poland HA** — first baseline with Docker socket access)

| Component | Version | How to check |
|---|---|---|
| ha-ops-mcp (addon) | **0.57.0** (tag `v0.57.0`) | `haops_self_check` → `ha_ops_version` |
| Home Assistant Core | **2026.8.1** | `haops_self_check` → `rest_api.ha_version` |
| Supervisor / HAOS | **2026.07.5** / **18.2** | `haops_tools_check` → `supervisor` |
| Docker Engine (host) | **29.6.2** | `haops_container_list`, or Supervisor `/docker/info` |
| HA DB backend | **MariaDB 11.4.10-MariaDB**, schema **53** | `haops_system_info` → `database` |
| Add-on protection mode | **OFF** (required for container tools) | `/addons/self/info` → `protected: false` |
| Claude Code CLI | **2.1.226** | `claude --version` |
| Terminal host | **iTerm2 3.6.11** | iTerm → About / `$TERM_PROGRAM_VERSION` |
| macOS | **26.5.2** (25F84, Darwin 25.5.0) | `sw_vers` |
| Bun (CC runtime) | **1.3.14** | `bun --version` |
| Node (local) | **v26.5.0** | `node --version` |
| MCP server name | `ha-ops-pl` (Singapore instance is `ha-ops`) | `claude mcp list` |

Notes:
- `haops_tools_check` → **`all_pass`, 14/14 groups** (the new `docker` group included), 0 broken tools.
- **Container access verified working**: socket at `/run/docker.sock`, 19/19 containers listed, and
  `exec` returning `exit_code: 0`. Confirmed against the ESPHome add-on
  (`app_5c53de3b_esphome`, ESPHome **2026.7.4**, Python **3.14.6**), which is the toolchain this
  capability exists to borrow.
- **`docker_api` is NOT read-only, despite HA's wording** — see HA_COMPATIBILITY.md. Full Engine API
  including exec. `full_access: true` is not needed.
- **Two gates, and the second one bites:** Supervisor evaluates
  `if not protected and access_docker_api` when it *creates* the container, so turning Protection mode
  off does nothing until the add-on is restarted. On this baseline the update recreated the container
  while still protected, and the socket only appeared after an explicit restart.
- HA Core, Supervisor, HAOS, DB and all client-side versions unchanged from the `v0.56.0` row — the
  only moving parts were the add-on and its protection setting.

### `v0.56.0` — verified 2026-08-11 (**Poland HA** — first baseline on HA 2026.8)

| Component | Version | How to check |
|---|---|---|
| ha-ops-mcp (addon) | **0.56.0** (tag `v0.56.0`) | `haops_self_check` → `ha_ops_version` |
| Home Assistant Core | **2026.8.1** | `haops_self_check` → `rest_api.ha_version` |
| Supervisor / HAOS | **2026.07.5** / **18.2** | `haops_tools_check` → `supervisor` |
| HA DB backend | **MariaDB 11.4.10-MariaDB**, schema **53** | `haops_system_info` → `database` |
| Claude Code CLI | **2.1.226** | `claude --version` |
| Terminal host | **iTerm2 3.6.11** | iTerm → About / `$TERM_PROGRAM_VERSION` |
| macOS | **26.5.2** (Darwin 25.5.0) | `sw_vers` |
| Bun (CC runtime) | **1.3.14** | `bun --version` |
| Node (local) | **v26.5.0** | `node --version` |
| MCP server name | `ha-ops-pl` (Singapore instance is `ha-ops`) | `claude mcp list` |

Notes:
- **HA Core jumped 2026.7.4 → 2026.8.1** since the v0.55.1 row. `haops_tools_check` → `all_pass`,
  **13/13 groups, 0 broken tools**, so the compatibility window was bumped to **2026.6 – 2026.8**
  (`compat.py` + README + DOCS + HA_COMPATIBILITY together).
- Recorder schema **still 53** — unchanged across four HA releases now.
- New in this build: `haops_entity_rename` (bulk two-phase renames). Exercised immediately on the
  PL plug fleet: 84 entities renamed in 4 calls, 0 errors.

### `v0.55.1` — verified 2026-08-02 (**Poland HA** — first PL baseline)

| Component | Version | How to check |
|---|---|---|
| ha-ops-mcp (addon) | **0.55.1** (tag `v0.55.1`) | `haops_self_check` → `ha_ops_version` |
| Home Assistant Core | **2026.7.4** | `haops_self_check` → `rest_api.ha_version` |
| HA DB backend | **MariaDB 11.4.10-MariaDB**, schema **53** | `haops_system_info` → `database` |
| Claude Code CLI | **2.1.220** | `claude --version` |
| Terminal host | **iTerm2 3.6.11** | iTerm → About / `$TERM_PROGRAM_VERSION` |
| macOS | **26.5.2** (Darwin 25.5.0) | `sw_vers` |
| Bun (CC runtime) | **1.3.14** | `bun --version` |
| Node (local) | **v26.5.0** | `node --version` |
| MCP server name | `ha-ops-pl` (Singapore instance is `ha-ops`) | `claude mcp list` |
| MCP transport | streamable-http, OAuth on | `claude mcp list` |
| PL HA host | HAOS 18.1 OVA in a KVM VM, 2 vCPU `kvm64`, 8 GB RAM | `haops_exec_shell` / Supervisor |

**Notes for this baseline:**
- **This is the Poland instance**, not Singapore — separate HA, separate addon install, separate
  OAuth store. All other rows in this file are Singapore. Same client stack, so client drift is shared.
- **v0.55.1 is the fix for the mcp 2.0 dependency break** — every release before it is marked broken
  (see `962dd9a`). Verified here after the user updated the addon: `haops_self_check` → `overall: ok`,
  all five backends (REST, WebSocket, MariaDB, filesystem, backup dir) green on the first call.
- PL instance shape at verification: **875 entities**, 30 automations, `PL Home`, `Europe/Warsaw`.
- Compatibility window satisfied: live 2026.7.4, window 2026.5 → 2026.7, `in_window: true`.

### `v0.55.0` — verified 2026-08-02 (Singapore HA)

| Component | Version | How to check |
|---|---|---|
| ha-ops-mcp (addon) | **0.55.0** (tag `v0.55.0`) | `haops_self_check` → `ha_ops_version` |
| Home Assistant Core | **2026.7.4** | `haops_self_check` → `rest_api.ha_version` |
| HA Supervisor | **2026.07.5** | `haops_tools_check` → `supervisor.tests.supervisor_info` |
| HA OS | **18.2** (amd64) | `haops_tools_check` → `supervisor.tests.supervisor_info` |
| HA DB backend | **MariaDB 11.4.10-MariaDB**, schema **53** | `haops_system_info` → `database` |
| Claude Code CLI | **2.1.220** | `claude --version` |
| Terminal host | **iTerm2 3.6.11** | iTerm → About / `$TERM_PROGRAM_VERSION` |
| macOS | **26.5.2** (build 25F84, Darwin 25.5.0) | `sw_vers` |
| Bun (CC runtime) | **1.3.14** | `bun --version` |
| Node (local) | **v26.5.0** | `node --version` |
| Addon base image | **Debian trixie** + Playwright chromium-headless-shell (~1.5 GB) — unchanged since v0.53.3 | `haops_exec_shell "chromium --version"` |
| MCP transport | streamable-http, OAuth on | `claude mcp list` |
| MCP URL | `http://homeassistant.local:8901/mcp` | must stay mDNS — OAuth resource is pinned to this host |
| HA host LAN IP | `10.0.0.150` (stable) | `dscacheutil -q host -a name homeassistant.local` |

**Notes for this baseline:**
- **First baseline taken on HA 2026.7.x.** `haops_tools_check` → `all_pass`, **13/13 groups, 0 broken
  tools**. HA Core jumped **2026.6.3 → 2026.7.4** since the v0.54.0 row; nothing in 2026.7's breaking
  changes touches our API surface (all integration-level, plus automation trigger-key renames).
- **v0.55.0 adds `src/ha_ops_mcp/compat.py`** — the HA compatibility window is now declared in code,
  logged at startup when the live instance falls outside it, and reported by `haops_system_info` and
  `haops_tools_check`. The prose version with the full API-surface inventory is `docs/HA_COMPATIBILITY.md`;
  **that file is the one to read after an HA update**, this one is for client/connectivity drift.
- **ZHA WebSocket commands verified live** on 2026.7.4: `zha/devices/reconfigure` and
  `zha/topology/update` both still exist (the `zha/*/remove` commands do not — see HA_COMPATIBILITY.md).
  `haops_tools_check`'s zigbee group now probes the reconfigure command every run, so a future removal
  fails a check instead of silently breaking a tool.
- **Client drift since v0.54.0:** Claude Code **2.1.177 → 2.1.220**, node **v26.0.0 → v26.5.0**,
  macOS **26.5.1 → 26.5.2** (build 25F80 → 25F84). Connection stayed healthy across all of it.
- **Forward-looking:** HA **2026.8 lands 2026-08-05** with a device-registry change (one device entry
  per integration) that will change device counts. Re-verify after updating; don't take it on day one.

### `v0.54.0` — verified 2026-06-13 (Singapore HA)

| Component | Version | How to check |
|---|---|---|
| ha-ops-mcp (addon) | **0.54.0** (tag `v0.54.0`, `e9577b6`) | `haops_system_info` / `git describe --tags` |
| Home Assistant Core | **2026.6.3** | `haops_self_check` → `rest_api.ha_version` |
| HA DB backend | **MariaDB 11.4.10-MariaDB**, schema **53** | `haops_system_info` → `database` |
| Claude Code CLI | **2.1.177** | `claude --version` |
| Terminal host | **iTerm2 3.6.11** | iTerm → About / `$TERM_PROGRAM_VERSION` |
| macOS | **26.5.1** (build 25F80, Darwin 25.5.0) | `sw_vers` |
| Bun (CC runtime) | **1.3.14** | `bun --version` |
| Node (local) | **v26.0.0** | `node --version` |
| Addon base image | **Debian trixie** + Playwright chromium-headless-shell (~1.5 GB) — unchanged from v0.53.3 (no Dockerfile/base change in v0.54.0) | `haops_exec_shell "chromium --version"` |
| MCP transport | streamable-http, OAuth on | `claude mcp list` |
| MCP URL | `http://homeassistant.local:8901/mcp` | must stay mDNS — OAuth resource is pinned to this host |
| HA host LAN IP | `10.0.0.150` (stable) | `dscacheutil -q host -a name homeassistant.local` |

**Notes for this baseline:**
- **v0.54.0 ships shell-output persistence** — `haops_exec_shell` now saves full stdout/stderr to
  a `ShellOutputStore` (`<backup.dir>/shell_output`, manifest + per-run JSON, retention prune, 1 MB/stream
  cap) and stamps an `output_id` into its audit row; the sidebar Timeline lazy-loads + renders it inline
  on row-expand. **No new MCP tool** (store is ha-ops-admin data, not HA state — still **78 tools**).
- **Verified live end-to-end this session:** ran `dmesg` (full buffer, ~50k+ chars). The MCP client
  capped/offloaded the *model-facing* result, but the **full output persisted to the store and rendered
  untruncated in the Timeline sidebar** — the exact "model view ↔ human view decoupled, output durable
  either way" behavior the feature exists for. `haops_self_check` `overall: ok` (all backends).
- **HA Core updated** this session: **2026.6.1 → 2026.6.3**. Stack otherwise unchanged from v0.53.3
  (MariaDB 11.4.10 schema 53, Debian+Playwright image, same client host / mDNS URL / LAN IP).
- **Client drift since v0.53.3:** Claude Code **2.1.166 → 2.1.177** (connection stayed healthy across it).

### `v0.53.3` — verified 2026-06-07 (Singapore HA)

| Component | Version | How to check |
|---|---|---|
| ha-ops-mcp (addon) | **0.53.3** (tag `v0.53.3`, `d33c0fd`) | `haops_system_info` / `git describe --tags` |
| Home Assistant Core | **2026.6.1** | `haops_self_check` → `rest_api.ha_version` |
| HA DB backend | **MariaDB 11.4.10-MariaDB**, schema **53** | `haops_system_info` → `database` |
| Claude Code CLI | **2.1.166** | `claude --version` |
| Terminal host | **iTerm2 3.6.11** | iTerm → About / `$TERM_PROGRAM_VERSION` |
| macOS | **26.5.1** (Darwin 25.5.0) | `sw_vers` |
| Bun (CC runtime) | **1.3.14** | `bun --version` |
| Node (local) | **v26.0.0** | `node --version` |
| Addon base image | **Debian trixie** + Playwright chromium-headless-shell (~1.5 GB) | `haops_exec_shell "chromium --version"` |
| MCP transport | streamable-http, OAuth on | `claude mcp list` |
| MCP URL | `http://homeassistant.local:8901/mcp` | must stay mDNS — OAuth resource is pinned to this host |
| HA host LAN IP | `10.0.0.150` (stable) | `dscacheutil -q host -a name homeassistant.local` |

**Notes for this baseline:**
- First baseline on the **Debian + Playwright** stack (the v0.50.0 base-swap line) with the full UI
  suite live: `haops_ui_screenshot`/`perf`/`interact`/`trace` + `haops_capture_show` + the Captures
  sidebar gallery (v0.52.0–v0.53.3). 78 tools.
- UI capture defaults settled here: viewport **1280×800** (16:10, `full_page` grows to content),
  `device="mobile"` preset = iPhone-17-Pro-class **402×874 @3× touch**. Source-map/`/node_modules/`
  requests are stubbed with an empty 204 so headless renders log **0 console errors** (verified live:
  walkin desktop `console_errors: []`).
- **Capture view model** (v0.53.3): screenshots are human-viewed in-browser by default (Captures tab /
  `…/ui#capture=<id>` deep-link, 0 model tokens); `haops_ui_screenshot` no longer inlines base64.
  `haops_capture_show` (pixels→model, JPEG 768px/q70) is opt-in for model-side visual analysis only.
  See [[feedback_capture_view_model]]. Verified live: screenshot returns tiny JSON + view_hint, no inline image.
- `haops_self_check` `overall: ok` (all backends) on 0.52.3. Stack otherwise unchanged from v0.40.0
  (HA 2026.6.1, MariaDB 11.4.10 schema 53, same client host).

### `v0.40.0` — verified 2026-06-06 (Singapore HA)

| Component | Version | How to check |
|---|---|---|
| ha-ops-mcp (addon) | **0.40.0** (tag `v0.40.0`, `cf6070d`) | `haops_system_info` / `git describe --tags` |
| Home Assistant Core | **2026.6.1** | `haops_self_check` → `rest_api.ha_version` |
| HA DB backend | **MariaDB 11.4.10-MariaDB**, schema **53** | `haops_system_info` → `database` |
| Claude Code CLI | **2.1.166** | `claude --version` |
| Terminal host | **iTerm2 3.6.11** | iTerm → About / `$TERM_PROGRAM_VERSION` |
| macOS | **26.5.1** (build 25F80, Darwin 25.5.0) | `sw_vers` |
| Bun (CC runtime) | **1.3.14** | `bun --version` |
| Node (local) | **v26.0.0** | `node --version` |
| MCP transport | streamable-http, OAuth on | `claude mcp list` |
| MCP URL | `http://homeassistant.local:8901/mcp` | must stay mDNS — OAuth resource is pinned to this host |
| HA host LAN IP | `10.0.0.150` (stable) | `dscacheutil -q host -a name homeassistant.local` |

**Notes for this baseline:**
- v0.40.0 relocated the OAuth store from `/data` → `/backup/ha-ops-mcp/auth/` (survives addon
  uninstall/slug-change). Migration verified live: 6 clients / 3 tokens carried over, **no re-auth**;
  legacy `/data/oauth.json` left in place. Audit log already lived under `/backup` (intact, 501 ops).
- HA OS update landed this session: Core **2026.6.0 → 2026.6.1**. `haops_self_check` `overall: ok`
  (all backends) after the update + addon update.
- Captured as the clean reference point immediately before the Debian base-swap / Playwright work
  (branch `feat/debian-playwright-ui-suite`).

### `v0.38.0` — verified 2026-06-05 (Singapore HA)

| Component | Version | How to check |
|---|---|---|
| ha-ops-mcp (addon) | **0.38.0** (tag `v0.38.0`, `3e2c57c`) | `haops_system_info` / `git describe --tags` |
| Home Assistant Core | **2026.6.0** | `haops_self_check` → `rest_api.ha_version` |
| HA DB backend | **MariaDB 11.4.10-MariaDB**, schema **53** | `haops_system_info` → `database` |
| Claude Code CLI | **2.1.162** | `claude --version` |
| Terminal host | **iTerm2 3.6.11** | iTerm → About / `$TERM_PROGRAM_VERSION` |
| macOS | **26.5.1** (build 25F80, Darwin 25.5.0) | `sw_vers` |
| Bun (CC runtime) | **1.3.14** | `bun --version` |
| Node (local) | **v26.0.0** | `node --version` |
| MCP transport | streamable-http, OAuth on | `claude mcp list` |
| MCP URL | `http://homeassistant.local:8901/mcp` | must stay mDNS — OAuth resource is pinned to this host |
| HA host LAN IP | `10.0.0.150` (stable) | `dscacheutil -q host -a name homeassistant.local` |

**Notes for this baseline:**
- v0.38.0 added `usb: true` + `uart: true` to the addon manifest — the addon can now reach USB/serial
  devices (used to flash the Zigbee coordinator in place). Required an addon **rebuild** to take effect.
- Zigbee coordinator (Sonoff ZBDongle-P / CC2652P) flashed **Z-Stack 20240710 → 20250321** this session;
  ZHA auto-restored the network after the mass-erase (no re-pair).
- `haops_self_check` returned `overall: ok` (all backends) right after the flash + core restart.

### `v0.37.0` — verified 2026-06-04 (Singapore HA)

| Component | Version | How to check |
|---|---|---|
| ha-ops-mcp (addon) | **0.37.0** (tag `v0.37.0`, `41eaa63`) | `haops_system_info` / `git describe --tags` |
| Home Assistant Core | **2026.5.4** | `haops_self_check` → `rest_api.ha_version` |
| HA DB backend | **MariaDB 11.4.10-MariaDB**, schema **53** | `haops_system_info` → `database` |
| Claude Code CLI | **2.1.162** | `claude --version` |
| Terminal host | **iTerm2 3.6.11** | iTerm → About / `$TERM_PROGRAM_VERSION` |
| macOS | **26.5.1** (build 25F80, Darwin 25.5.0) | `sw_vers` |
| Bun (CC runtime) | **1.3.14** | `bun --version` |
| Node (local) | **v26.0.0** | `node --version` |
| MCP transport | streamable-http, OAuth on | `claude mcp list` |
| MCP URL | `http://homeassistant.local:8901/mcp` | must stay mDNS — OAuth resource is pinned to this host |
| HA host LAN IP | `10.0.0.150` (stable) | `dscacheutil -q host -a name homeassistant.local` |

**Notes for this baseline:**
- `haops_self_check` database check may transiently fail with `Lost connection ... Connection reset by peer`
  on the first call after a long idle/disconnect (stale pooled MariaDB connection). It recovers on retry;
  `haops_system_info` reading the DB confirms it's healthy.
- The MCP URL **must** use the mDNS hostname `homeassistant.local`, not the IP — HA's OAuth protected-resource
  metadata is `http://homeassistant.local:8901/` and an IP URL fails RFC-8707 resource matching.
