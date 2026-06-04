# ha-ops-mcp

**This is a power-user tool. It can break your Home Assistant as easily as you can — possibly faster.**

Mutating operations create backups and log to an audit trail. Rollback is built in. But HA side effects (automations triggered, history logged during an inconsistency window) cannot be reversed. Treat this like SSH access to production — because that's what it is.

---

An [MCP server](https://modelcontextprotocol.io/) that gives AI assistants (and you) operational access to Home Assistant. Database queries, YAML config editing, Lovelace dashboard management, entity hygiene, system health, add-on control, and a cross-surface reference graph — the maintenance surface that HA's own UI doesn't expose well and that no other MCP server covers.

Other HA MCP tools ([HA's built-in MCP integration](https://www.home-assistant.io/integrations/mcp_server/), [ha-mcp](https://github.com/homeassistant-ai/ha-mcp), [hass-mcp](https://github.com/voska/hass-mcp)) focus on device control — "turn on the lights", query states, trigger automations via natural language. ha-ops-mcp is for the work that comes *dduring and after* setup: cleaning up 200 orphaned entities, reorganising dashboards across views, purging a bloated recorder database, editing YAML without losing comments, understanding what references `sensor.energy_grid` before renaming it, and doing all of that with diffs you can review and rollback if something goes wrong (most of the time...).

**63 tools. 531 tests. Mypy strict.**

## Installation

See [INSTALL.md](https://github.com/dude84/ha-ops-mcp/blob/main/docs/INSTALL.md) for addon, dev-deploy, and standalone setup.

**Quick start (addon):** add `https://github.com/dude84/ha-ops-mcp` as a repository in **Settings > Apps > App Store**, install, start. Default config works — Supervisor token and DB auto-detection, no manual setup needed.

### Connecting an MCP client

The addon exposes a streamable-HTTP endpoint on port 8901 (default). To connect Claude Code:

```bash
claude mcp add --transport http ha-ops http://<your-ha-address>:8901/mcp
```

For SSE transport (legacy, set `transport: sse` in addon Configuration):

```bash
claude mcp add --transport sse ha-ops http://<your-ha-address>:8901/sse
```

For standalone (stdio):

```bash
claude mcp add ha-ops -- /path/to/.venv/bin/ha-ops-mcp --config /path/to/config.local.yaml
```

### Authentication

OAuth 2.0 with Dynamic Client Registration is enabled by default for SSE / streamable-HTTP transports. The provider auto-approves authorization requests (single-user admin server, no consent UI) and persists clients + tokens to `<data_dir>/oauth.json`. Default token TTLs: 30-day access token with a sliding window (extends on every successful verification), 30-day refresh token.

To clear all stored OAuth state (after a client mismatch or revocation), tick `clear_oauth_on_next_boot` in the addon Configuration and restart — the flag self-resets after firing.

**Re-auth-every-launch — resolved by switching transport to streamable-HTTP (v0.34.0).** Earlier reports of "Claude Code forces a fresh DCR + authorization-code flow on every launch" were tracked against [anthropics/claude-code#43000](https://github.com/anthropics/claude-code/issues/43000). After flipping the addon default from SSE to streamable-HTTP in v0.34.0, the symptom no longer reproduces: same `client_id` reused, same access + refresh tokens persisted across `/clear` and Claude Code restart cycles (`haops_auth_status` confirms TTL decrements at wall-clock rate, no fresh registrations piling up). The root cause was SSE-transport fragility — long-lived `GET /sse` streams dropping on Supervisor-proxy idle, surfacing client-side as forced re-auth — not the DCR-keying theory. If you are still on SSE and seeing this, switch to `transport: streamable-http` in the addon Configuration.

`auth_enabled: false` remains available for trusted single-host LAN deployments where you want zero auth overhead. Disabling it means anyone reachable on `:8901/mcp` can call every tool including `haops_exec_shell` and DB writes — only acceptable if the LAN trust boundary is strict.

Defensive caps added in v0.34.1: `MAX_CLIENTS = 100` on persisted DCR registrations with LRU-by-`client_id_issued_at` eviction (revokes tokens for dropped clients too), and `issued_at` stamped on every access + refresh token for forensic auditing via `haops_auth_status`.

### Troubleshooting connectivity

If your MCP client suddenly **"Failed to connect"** but `curl http://homeassistant.local:8901/mcp` returns `401` from the same machine, the server is fine — the block is client-side. The common cause on macOS is **Local Network Privacy**: a terminal-app update (iTerm, Terminal, etc.) resets that app's Local Network permission, so every process it launches — including the MCP client — loses LAN access, while `curl` keeps working because Apple system binaries are exempt. Fix: System Settings → Privacy & Security → **Local Network** → toggle your terminal off/on, then **fully quit and relaunch** it.

Keep the MCP URL as the mDNS hostname (`http://homeassistant.local:8901/mcp`), not an IP — the OAuth resource metadata is pinned to the hostname and an IP URL fails resource matching.

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

Runs `haops_entity_audit` to find problems, `haops_refactor_check` to map references, then `haops_entity_disable` with a preview of what changes. Cross-references YAML config, dashboards, and registries.

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

**63 tools** across database, config, dashboard, entity, registry, system, service, backup, rollback, batch, reference graph, debugger, helper (input_boolean / input_number / counter / timer / schedule etc.), ergonomic wrappers, and superuser categories. All prefixed `haops_`.

- **[Tool reference](https://github.com/dude84/ha-ops-mcp/blob/main/docs/TOOLS.md)** — full list with descriptions and types
- **[Capability matrix](https://github.com/dude84/ha-ops-mcp/blob/main/docs/HA_API_CAPABILITIES.md)** — per-tool backend dependencies (REST, WS, DB, FS, Supervisor) and token requirements

## Sidebar UI

The addon registers an **HA Ops** panel in the HA sidebar via ingress. Three tabs:

- **Timeline** — chronological feed of mutations with expandable inline diffs (unified for config, structured for dashboards). Apply rows carry a **Revert** button for the most recent change while the session is active. Rollback and apply entries are visually linked. Paginated 50 per page; auto-refreshes every 5 seconds on page 1 (paused on deeper pages so the offset window doesn't shift under you).
- **Backups** — per-type counts, retention settings, prune/clear actions.
- **Health** — `self_check` + `tools_check` results, rendered per-group with per-test breakdown and actionable diagnostics.

Admin-convenience mutations (prune, clear, revert) share the exact code path of their MCP tool counterparts and audit with `source: "sidebar"`.

## License

Apache 2.0
