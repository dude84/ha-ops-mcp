# ha-ops-mcp

**This is a power-user tool. It can break your Home Assistant as easily as you can, and will do that much faster.**

Mutating operations create backups and log to an audit trail. Rollback is built in. But HA side effects (automations triggered, history logged during an inconsistency window) cannot be reversed. Treat this like SSH access to production — because that's what it is.

---

An [MCP server](https://modelcontextprotocol.io/) that gives AI assistants (and you) operational access to Home Assistant. Database queries, YAML config editing, Lovelace dashboard management, entity hygiene, system health, add-on control, and a cross-surface reference graph — the maintenance surface that HA's own UI doesn't expose well and that no other MCP server covers.

Other HA MCP tools ([HA's built-in MCP integration](https://www.home-assistant.io/integrations/mcp_server/), [ha-mcp](https://github.com/homeassistant-ai/ha-mcp), [hass-mcp](https://github.com/voska/hass-mcp)) focus on device control — "turn on the lights", query states, trigger automations via natural language. ha-ops-mcp is for the work that comes *during and after* setup: cleaning up 200 orphaned entities, reorganising dashboards across views, purging a bloated recorder database, editing YAML without losing comments, understanding what references `sensor.energy_grid` before renaming it, **seeing your dashboards** (server-side headless screenshots + load-performance capture), and doing all of that with diffs you can review and rollback if something goes wrong (most of the time...). Device control here is a *secondary* objective — the generic `haops_service_call` covers it; there are no bespoke per-device tools.

**87 tools. 878 tests. Mypy strict. Debian image with Playwright/Chromium (v0.50.0+).**

## Home Assistant compatibility

| | |
|---|---|
| **Built against** | HA Core **2026.8.2** |
| **Supported window** | **2026.6 – 2026.8** |
| **Recorder DB schema** | **53** |

Every release is verified against a live instance with `haops_tools_check` — 14 read-only groups exercising REST, WebSocket, database, filesystem, registries, Supervisor, shell, Docker, reference graph, debugger, helpers, Zigbee, UI and user tools. A release ships when that returns `all_pass`. (The Docker group reports `skip` unless you opt into container access, and `skip` does not block `all_pass`.)

**Running a newer HA than the window?** Nothing will refuse to start. The server logs a warning, and `haops_system_info` reports a `compatibility` block telling you the same. HA ships on the first Wednesday of every month, so the newest verified version goes stale by design — outside the window means *untested*, not *known-broken*. If something misbehaves, run `haops_tools_check` first: each failing group names the tools it affects.

### Verified against

| ha-ops-mcp | HA Core | DB schema | Result |
|---|---|---|---|
| 0.61.1 | 2026.8.2 | 53 | 15/15 groups pass (docker_prune group) |
| 0.57.1 | 2026.8.1 | 53 | 14/14 groups pass (Docker enabled) |
| 0.56.0 | 2026.8.1 | 53 | 13/13 groups pass |
| 0.55.0 | 2026.7.4 | 53 | 13/13 groups pass |
| 0.54.0 | 2026.6.3 | 53 | all backends ok |
| 0.53.3 | 2026.6.1 | 53 | all backends ok |
| 0.37.0 | 2026.5.4 | 53 | all backends ok |

[**HA_COMPATIBILITY.md**](https://github.com/dude84/ha-ops-mcp/blob/main/docs/HA_COMPATIBILITY.md) has the full picture: the exact HA API surface this server depends on (WebSocket commands, REST and Supervisor endpoints, `.storage` files, recorder tables) and the version-specific HA behaviour we've hit. Most HA breaking changes are integration-level and touch none of it — that document is the list to check them against.

Requires Python **3.11+** (the addon image ships its own). Databases: SQLite, MariaDB/MySQL, PostgreSQL.

## Installation

See [INSTALL.md](https://github.com/dude84/ha-ops-mcp/blob/main/docs/INSTALL.md) for addon, dev-deploy, and standalone setup.

**Quick start (addon):** add `https://github.com/dude84/ha-ops-mcp` as a repository in **Settings > Apps > App Store**, install, start. Default config works — Supervisor token and DB auto-detection, no manual setup needed.

> **Use v0.55.1 or later.** Every earlier release fails to start on a fresh build with `ModuleNotFoundError: No module named 'mcp.server.fastmcp'` — the MCP SDK published 2.0.0 on 2026-07-28 and removed that module, and dependencies were previously uncapped. Rolling back to an older tag does not help; v0.55.1 caps every dependency below its next major.

### Protection mode must be OFF for the Docker-backed tools

Home Assistant add-ons run with **Protection mode ON by default**, and Supervisor silently strips the
manifest's `docker_api: true` while it is on. The declaration alone grants nothing — the checkbox is
the opt-in gate.

To enable: **Settings > Add-ons > HA Ops MCP > Info**, switch **Protection mode** off, then
**restart the add-on**. The restart is required, not cosmetic: Supervisor decides whether to mount
`/run/docker.sock` when it *creates* the container, so toggling protection on a running add-on
changes nothing until it is recreated.

**If you leave Protection mode on** (a perfectly valid choice — see the trade-off in
[SECURITY.md](SECURITY.md#docker-socket-access-v0570--opt-in-and-a-genuine-step-up), since the socket
reaches every container on the host, not just this add-on):

| Tool | Behaviour with Protection mode ON |
|------|-----------------------------------|
| `haops_container_list` / `_logs` / `_exec` | **Unavailable.** Return instructions on how to enable, not an opaque error. |
| `haops_esphome_build` | **Cannot compile.** Compiling borrows the ESPHome add-on's toolchain through the socket. |
| `haops_esphome_status` | **Works.** Firmware sizes come from `<config>/esphome/.esphome/build` — a plain file read — with a scoped `impact` note. |
| `haops_docker_prune` | **Unavailable**, and the startup auto-prune (`docker_prune_on_start`) skips. Dangling images and build cache from every add-on rebuild accumulate; reclaim them from the host instead. |
| `haops_self_check` | `docker` reports `skip` with `tools_unavailable: 3`. `overall` stays `ok`. |
| `haops_tools_check` | The `docker` group reports `skip`, never `fail` — `all_pass` is still reachable. |

Every other tool is unaffected. For add-on logs specifically, prefer `haops_addon_logs`: it goes
through Supervisor and needs no socket at all.

### Connecting an MCP client

The addon exposes a streamable-HTTP endpoint on port 8901 (default). To connect Claude Code:

```bash
claude mcp add --transport http ha-ops http://<your-ha-address>:8901/mcp \
  --header "Authorization: Bearer <your token>"
```

The token comes from the addon Configuration (`auth_token`) or the addon log — see
[Authentication](#authentication). In token mode a raw-IP URL is fine (useful over VPN, where mDNS
doesn't resolve).

**Transport is not configurable.** streamable-http on `/mcp` is the only one; the addon option was
removed in v0.63.1 (single valid value) and the legacy `sse` transport in v0.63.0 — its long-lived
streams dropped on Supervisor-proxy idle. A stored `transport` value from an older install is
ignored with a log line; point your client at `/mcp`.

For standalone (stdio):

```bash
claude mcp add ha-ops -- /path/to/.venv/bin/ha-ops-mcp --config /path/to/config.local.yaml
```

### Authentication

**Default since v0.62.0: static Bearer token.** OAuth was the default until Claude Code
(~v2.1.234+, Aug 2026) began enforcing OAuth 2.0's TLS requirement for token endpoints
(RFC 6749 §3.2): its MCP client no longer sends OAuth token requests to plain-`http` endpoints
(only `localhost`/`127.0.0.1`/`::1` are exempt). A fresh OAuth flow against
`http://<your-ha-address>:8901` fails with `Refusing to send credentials to non-https token
endpoint`; already-authorized clients keep working on cached tokens until a full re-auth. This is
intended behavior upstream
([claude-code#3320](https://github.com/anthropics/claude-code/issues/3320)), with no exemption for
trusted LANs — and since the typical home-lab deployment of this addon is exactly "plain HTTP on a
trusted LAN", we adapted to conform: a static Bearer token supplied as a header involves no OAuth
credential exchange, so it meets the requirement and became the default.

**How token auth works:** set `auth_token` in the addon Configuration (masked password field), or
leave it blank — the addon generates one and **prefills it back into the Configuration tab**
(`auth_token`) via the Supervisor API, so you read it from the addon Configuration, not from a log
line that scrolls away. (It's also persisted to `<backup_dir>/auth/static_token`, 0600.) Connect
with:

```bash
claude mcp add --transport http ha-ops http://<your-ha-address>:8901/mcp \
  --header "Authorization: Bearer <your token>"
```

Sending the header suppresses the client's OAuth discovery entirely, which also lifts the
hostname/resource-matching restriction — raw-IP URLs (VPN use) work fine in token mode. Tokens are
compared constant-time; the sidebar panel (`/ui`, `/api/ui/*`) stays on HA ingress auth, everything
else on port 8901 requires the Bearer token. Multiple Claude Code instances can share the token and
connect concurrently.

#### OAuth 2.0 (experimental since v0.62.0 — previously the default)

OAuth is still fully implemented: Dynamic Client Registration, auto-approved authorization
(single-user admin server, no consent UI), clients + tokens persisted to `<data_dir>/oauth.json`,
30-day access token with a sliding TTL (extends on every successful verification) and 30-day
refresh token. What changed is that **modern MCP clients will only speak OAuth to an HTTPS
endpoint**, so it now takes real infrastructure. Use it if you want per-client credentials,
revocation, and expiry rather than one shared secret.

**Requirements — all of them, or the flow fails client-side before it reaches the addon:**

1. **TLS in front of port 8901.** A reverse proxy (HAProxy, nginx, Caddy, NPM add-on) terminating
   HTTPS and forwarding to the addon. Self-signed will be rejected by most clients — use a real
   certificate (Let's Encrypt / DuckDNS add-on, or your own CA properly trusted on the client).
   *Alternative:* no proxy, but reach the addon over `localhost` — e.g.
   `ssh -L 8901:localhost:8901 root@<ha-host>` and connect to `http://localhost:8901/mcp`. Loopback
   is the one exemption from the TLS requirement.
2. **A stable hostname that matches the certificate**, resolvable from every client machine
   (split-DNS or `/etc/hosts` if the name is internal-only).
3. **`auth_issuer_url` set to that exact HTTPS URL** in the addon Configuration — e.g.
   `https://ha-ops.example.com`. Auto-detection derives an `http://` issuer from HA's
   `internal_url`, which will not satisfy the client. No trailing path.
4. **`auth_mode: oauth`** in the addon Configuration.
5. **The client URL must match the issuer's host exactly.** OAuth resource metadata is pinned to
   the issuer (RFC 8707), so `https://ha-ops.example.com/mcp` works and a raw-IP or alternate
   hostname pointing at the same server does not. (Token mode has no such constraint — this is a
   real cost of choosing OAuth.)

Then, on the client:

```bash
claude mcp add --transport http ha-ops https://ha-ops.example.com/mcp
```

No `--header` — the client discovers the OAuth endpoints and runs the flow itself, opening a
browser once. Verify with `haops_auth_status`: it should report `mode: oauth` plus registered
clients and token TTLs.

**Operating notes.** To clear all stored OAuth state (client mismatch, revocation, wedged auth),
tick `clear_oauth_on_next_boot` in the addon Configuration and restart — the flag self-resets after
firing. There is no MCP tool for this on purpose: clearing the store kills the session making the
call. Defensive caps (v0.34.1): `MAX_CLIENTS = 100` persisted DCR registrations with
LRU-by-`client_id_issued_at` eviction (tokens for dropped clients are revoked too), and `issued_at`
stamped on every access + refresh token for auditing via `haops_auth_status`.

**Historical note — "re-auth on every launch" (resolved in v0.34.0).** Reports of Claude Code
forcing a fresh DCR + authorization-code flow on every launch were tracked against
[anthropics/claude-code#43000](https://github.com/anthropics/claude-code/issues/43000). The cause
was the old SSE transport: long-lived `GET /sse` streams dropped on Supervisor-proxy idle, which
surfaced client-side as forced re-auth. Switching the default to streamable-HTTP fixed it (same
`client_id` and tokens persist across restarts), and SSE was removed entirely in v0.63.0.

`auth_mode: none` (or the legacy `auth_enabled: false`) remains available for trusted single-host LAN deployments where you want zero auth overhead. Disabling it means anyone reachable on `:8901/mcp` can call every tool including `haops_exec_shell` and DB writes — only acceptable if the LAN trust boundary is strict.

Defensive caps added in v0.34.1: `MAX_CLIENTS = 100` on persisted DCR registrations with LRU-by-`client_id_issued_at` eviction (revokes tokens for dropped clients too), and `issued_at` stamped on every access + refresh token for forensic auditing via `haops_auth_status`.

### Troubleshooting connectivity

If your MCP client suddenly **"Failed to connect"** but `curl http://homeassistant.local:8901/mcp` returns `401` from the same machine, the server is fine — the block is client-side. The common cause on macOS is **Local Network Privacy**: a terminal-app update (iTerm, Terminal, etc.) resets that app's Local Network permission, so every process it launches — including the MCP client — loses LAN access, while `curl` keeps working because Apple system binaries are exempt. Fix: System Settings → Privacy & Security → **Local Network** → toggle your terminal off/on, then **fully quit and relaunch** it.

In **OAuth mode only**: keep the MCP URL as the mDNS hostname (`http://homeassistant.local:8901/mcp`), not an IP — the OAuth resource metadata is pinned to the hostname and an IP URL fails resource matching. Token mode has no such restriction.

Full triage steps and a known-good version baseline (diff against it to spot which component moved) are in [`docs/CONNECTIVITY_TROUBLESHOOTING.md`](https://github.com/dude84/ha-ops-mcp/blob/main/docs/CONNECTIVITY_TROUBLESHOOTING.md) and [`docs/KNOWN_GOOD_ENV.md`](https://github.com/dude84/ha-ops-mcp/blob/main/docs/KNOWN_GOOD_ENV.md).

## Usage

Mutating tools support two modes: **two-phase confirmation** (preview returns a diff + token, a second call applies it) and **auto-apply** (`auto_apply=true` — preview + apply in a single call). Both modes create backups and rollback savepoints automatically. The AI assistant can use either mode autonomously, or you can require manual review — it depends on your MCP client's permission settings, not the server.

**A note on diff visibility.** The colourised diff a reviewer actually sees in chat is rendered by the controller LLM (Claude Code, etc.) when it pastes the tool's `diff_rendered` field as a fenced markdown block — not by the server, and not by the tool-result panel (which only shows escaped JSON). Each preview tool's description embeds a REVIEW PROTOCOL asking the controller to paste before applying, but tool descriptions are *advisory*: today's Claude Opus 4.7 obeys, but if your controller drifts (paraphrases the diff, summarises in prose, or chains preview→apply silently) you'll need to nudge it. See [INSTALL.md → Recommended: client-side review mode](https://github.com/dude84/ha-ops-mcp/blob/main/docs/INSTALL.md#recommended-client-side-review-mode-for-mutations) for the per-message / per-session / per-project nudge patterns and Claude Code's `permissions.ask` snippet for mechanical enforcement of the apply step.

Changes can be rolled back via the MCP client (`haops_rollback` for the current session, `haops_backup_revert` for persistent backups) or directly from the **HA Ops** sidebar panel in the HA UI.

### Examples

**Reorganise a dashboard and roll back if it looks wrong:**
> "Move all energy cards from the Overview to a new Energy view on the climate dashboard"

The assistant reads the dashboard, builds a JSON Patch, shows you the diff, applies it. If the result isn't right — roll back from the sidebar or ask the assistant to revert.

**Entity cleanup across registries and config:**
> "Find all unavailable entities, check what references them, and disable the ones from removed devices"

Runs `haops_entity_audit` to find problems, `haops_refactor_check` to map references, then `haops_entity_toggle` with a preview of what changes. Cross-references YAML config, dashboards, and registries.

**Edit config YAML with validation:**
> "Add a template sensor for daily energy cost, validate the config, and reload"

Reads `configuration.yaml`, patches in the new sensor (preserving comments), shows the unified diff, applies after confirmation, runs `haops_config_validate`, then `haops_system_reload` for template sensors.

**Multi-file atomic batch:**
> "Rename `sensor.power_meter` to `sensor.grid_power` across automations.yaml, scripts.yaml, and the energy dashboard"

Uses `haops_refactor_check` to find all references, then `haops_batch_preview` to compose patches across config files and dashboards in one atomic preview. Single confirm, single rollback point.

**Database maintenance:**
> "How big is the recorder database? Purge everything older than 14 days, but show me what will be removed first"

`haops_db_health` for stats, `haops_db_purge` in dry-run mode for estimates, then confirm to purge.

**Debug an automation that isn't firing:**
> "Why didn't the morning lights automation trigger today?"

`haops_automation_trace` for per-step execution data, `haops_entity_history` for the trigger entity's state changes, `haops_logbook` for the event timeline, `haops_template_render` to test the condition template against live state.

## Reporting Issues

This is an open-source project maintained in spare time. Bug reports and feature requests are welcome at [github.com/dude84/ha-ops-mcp/issues](https://github.com/dude84/ha-ops-mcp/issues), but not all changes will be implemented, accepted, or prioritised.

When reporting a bug, include: HA version, install method (addon/pip), transport, the tool name that failed, the full error message, and steps to reproduce.

## Tools & Capabilities

**86 tools** across database, config, dashboard, entity, registry, system, service, backup, rollback, batch, reference graph, debugger, helper (input_boolean / input_number / counter / timer / schedule etc.), Zigbee/ZHA, ESPHome (`haops_esphome_status` / `_build`), the headless UI/UX surface (`haops_ui_screenshot` / `_perf` / `_interact` / `_trace` / `haops_capture_show`), user management (`haops_user_*`), container access (`haops_container_list` / `_logs` / `_exec` — opt-in, needs Protection mode off), ergonomic wrappers, and superuser categories. All prefixed `haops_`.

- **[Tool reference](https://github.com/dude84/ha-ops-mcp/blob/main/docs/TOOLS.md)** — full list with descriptions and types
- **[Capability matrix](https://github.com/dude84/ha-ops-mcp/blob/main/docs/HA_API_CAPABILITIES.md)** — per-tool backend dependencies (REST, WS, DB, FS, Supervisor) and token requirements

## Sidebar UI

The addon registers an **HA Ops** panel in the HA sidebar via ingress. Four tabs:

- **Timeline** — chronological feed of mutations with expandable inline diffs (unified for config, structured for dashboards). Apply rows carry a **Revert** button for the most recent change while the session is active. Rollback and apply entries are visually linked, and a change with a linked UI capture shows its thumbnail inline. Paginated 50 per page; auto-refreshes every 5 seconds on page 1 (paused on deeper pages so the offset window doesn't shift under you).
- **Captures** — gallery of screenshots/traces from the UI tools: thumbnail grid, click-to-zoom, download, notes, multi-select delete, prune/clear. Addon-owned artifacts, managed here rather than via MCP.
- **Backups** — per-type counts, retention settings, prune/clear actions.
- **Health** — `self_check` + `tools_check` results, rendered per-group with per-test breakdown and actionable diagnostics.

Admin-convenience mutations (prune, clear, revert) share the exact code path of their MCP tool counterparts and audit with `source: "sidebar"`.

## License

Apache 2.0
