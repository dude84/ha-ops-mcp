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

### `v0.52.3` — verified 2026-06-07 (Singapore HA)

| Component | Version | How to check |
|---|---|---|
| ha-ops-mcp (addon) | **0.52.3** (tag `v0.52.3`, `b1aa377`) | `haops_system_info` / `git describe --tags` |
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
  suite live: `haops_ui_screenshot`/`perf`/`interact`/`trace` + the Captures sidebar gallery (v0.52.0).
- UI capture defaults settled here: viewport **1280×800** (16:10, `full_page` grows to content),
  `device="mobile"` preset = iPhone-17-Pro-class **402×874 @3× touch**. Source-map/`/node_modules/`
  requests are stubbed with an empty 204 so headless renders log **0 console errors** (verified live:
  walkin desktop `console_errors: []`).
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
