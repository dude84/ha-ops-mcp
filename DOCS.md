# ha-ops-mcp

An MCP server addon that gives AI assistants (and you) operational access to your Home Assistant instance. 86 tools covering database management, YAML config editing (with comment preservation), Lovelace dashboard CRUD via JSON Patch, entity registry hygiene, collection-helper CRUD (input_boolean / input_number / counter / timer / schedule / etc.), cross-surface reference graph, automation debugging, system health monitoring, add-on management, Zigbee/ZHA introspection, ESPHome node inventory + firmware builds, shell access, native user management, and a **headless UI/UX surface** — server-side dashboard screenshots + load-performance capture via Playwright/Chromium (v0.50.0+, Debian-based image) — all with two-phase confirmation, automatic backups, in-session rollback, and a full audit trail.

Built for the maintenance and observability work that comes during and after setup. **Device control** (lights, switches, scenes) is a **secondary objective** — handled by the generic `haops_service_call` escape hatch, not bespoke per-device tools.

## Home Assistant compatibility

| | |
|---|---|
| **Built against** | HA Core **2026.8.2** |
| **Supported window** | **2026.6 – 2026.8** |
| **Recorder DB schema** | **53** |

Each release is verified against a live instance with `haops_tools_check` (15 read-only groups covering every backend). On a newer HA than the window, the addon still starts — it logs a warning and `haops_system_info` reports a `compatibility` block. HA ships monthly, so "newest verified" goes stale by design: outside the window means **untested**, not broken.

If a tool misbehaves after an HA update, open the **Health** tab (or call `haops_tools_check`) — each failing group names the tools it affects. Details and the full HA API surface this addon depends on: [docs/HA_COMPATIBILITY.md](https://github.com/dude84/ha-ops-mcp/blob/main/docs/HA_COMPATIBILITY.md).

## Sidebar panel

The addon adds an **HA Ops** panel to your HA sidebar (via ingress). Four tabs:

- **Timeline** — chronological feed of every mutation with expandable inline diffs (unified for config, structured for dashboards). The most recent apply carries a one-click **Revert** button while the session is active. Rollback and apply entries are visually linked, and a change with a linked UI capture shows its thumbnail inline. Paginated 50 per page; auto-refreshes every 5 seconds on page 1 (paused on deeper pages so the offset window doesn't shift under you).
- **Captures** — gallery of screenshots and traces from `haops_ui_screenshot` / `haops_ui_trace`: thumbnail grid, click-to-zoom, download, editable notes, expandable per-capture console errors, multi-select delete, and prune/clear. These are the addon's own artifacts (not HA state), so *management* (delete/annotate/prune) lives here rather than via MCP tools; the assistant can *view* a capture inline with the read-only `haops_capture_show`.
- **Backups** — per-type backup counts, retention settings, prune and clear actions.
- **Health** — `self_check` (connectivity) and `tools_check` (functional) results, per-group with per-test breakdown and actionable diagnostics (URLs, error details, hints).

Admin-convenience mutations (prune, clear, revert) share the exact code path of their MCP tool counterparts and audit with `source: "sidebar"`.

## Safety

Every mutating operation creates backups and logs to an append-only audit trail. Rollback is built in — in-session (`haops_rollback`) for precise undo without drift, persistent (`haops_backup_revert`) for older changes. But HA side effects (automations triggered, history logged during an inconsistency window) cannot be reversed. Treat this like SSH access to production.

- **Two-phase confirmation** — preview returns a diff + token, apply consumes the token. Or use `auto_apply=true` for single-call atomic operations (default).
- **SQL guard** — `DROP DATABASE`, `TRUNCATE`, `DROP TABLE` on core tables blocked. `DELETE`/`UPDATE` without `WHERE` triggers warnings.
- **Shell guard** — `rm -rf /`, `dd`, `mkfs`, etc. blocked by default. Bypassable with `guard=false`.
- **Path guard** — all file operations resolved against config root. Path traversal rejected.
- **Secrets redaction** — `secrets.yaml` values masked by default.

## Configuration

### Token

Leave blank to use the auto-provisioned Supervisor token (recommended). Or paste a long-lived access token if you need specific permissions.

### Authentication (`auth_mode`, `auth_token`)

- **`token`** (default): static pre-shared Bearer token. Set `auth_token` yourself, or leave it
  blank to auto-generate — the generated token is **prefilled back into this Configuration tab**
  (`auth_token`) on the next start, and also persisted under `<backup_dir>/auth/`. Clients send it
  as an `Authorization: Bearer` header. Multiple MCP clients can share it and connect
  concurrently.
- **`oauth`** (experimental): the pre-v0.62.0 default — OAuth 2.0 with Dynamic Client
  Registration. Only viable when clients reach the addon over HTTPS or from localhost (Claude
  Code refuses plain-HTTP OAuth since ~v2.1.234).
- **`none`**: no MCP auth. Anyone reachable on port 8901 can call every tool, including shell and
  DB writes — only for strictly trusted networks.

### Transport

- **streamable-http** — the only HTTP transport, port 8901, endpoint `/mcp`.

The legacy **sse** transport was **removed in v0.63.0** (its long-lived streams dropped on
proxy idle, causing the pre-v0.34.0 re-auth symptom). If your addon options still say
`transport: sse` from an older version, it starts on streamable-http and logs a warning — set
`streamable-http` here and point your MCP client at the `/mcp` endpoint.

### Database URL

Leave blank to auto-detect from HA's recorder config. Or specify explicitly:

- SQLite: `sqlite:////config/home-assistant_v2.db`
- MariaDB: `mysql://homeassistant:password@core-mariadb/homeassistant`
- PostgreSQL: `postgresql://homeassistant:password@localhost/homeassistant`

### Backup

- **Backup directory**: default `/backup/ha-ops-mcp` (HA's persistent `/backup` volume).
- **Max age days**: default 30 — backups older than this are pruned automatically.
- **Max per type**: default 100 — cap per backup type (config, dashboard, entity, db).

### Hardware access

The addon requests generic USB access (`usb: true`) and auto-mapped UART/serial nodes (`uart: true`). This lets tools reach USB peripherals directly — most notably **flashing the Zigbee coordinator firmware in place** (e.g. Sonoff ZBDongle-P / CC2652P at `/dev/ttyUSB*`) via `haops_exec_shell`, without moving the dongle to another machine.

This is a deliberate capability expansion: combined with shell access, the addon can read and write any USB/serial device the host exposes. It sits behind the same trust boundary as shell access — treat the addon like SSH to production. Changing these flags requires an addon **rebuild** (the device cgroup is applied at container creation, not at runtime).

### Container access (Docker socket) — opt-in

`haops_container_list`, `haops_container_logs` and `haops_container_exec` reach **other containers on the HA host** — other add-ons, HA Core, the Supervisor. The point is borrowing tools this image doesn't ship: compiling an ESPHome firmware with the ESPHome add-on's own toolchain, for example, rather than shipping a second copy of the compiler.

**Two things are required, and the manifest is only the first:**

1. `docker_api: true` in the add-on manifest — already declared since v0.57.0.
2. **Protection mode OFF** — Settings → Add-ons → HA Ops MCP → Info tab, then restart the add-on.

Supervisor silently strips `docker_api` while Protection mode is on, and it is on by default, so step 2 is what actually grants access. Until you do it the three tools are inert and return instructions instead of an opaque error. `haops_tools_check` reports the `docker` group as `skip` (not `fail`) in that state, so an install that never opts in still reaches `all_pass`.

Before switching protection off, read the trade-off in [SECURITY.md](SECURITY.md#docker-socket-access-v0570--opt-in-and-a-genuine-step-up): the socket reaches every container on the machine, which widens the add-on's reach from "all of Home Assistant" to "all containers".

Note `haops_container_exec` **abandons** a command that hits its timeout — the Docker API has no cancel, so the process keeps running inside the target container. The response says so. Don't start unbounded work with it. For add-on logs, prefer `haops_addon_logs`: it goes through Supervisor and needs none of this.

## Connecting an MCP client

### Claude Code

```bash
claude mcp add --transport http ha-ops http://<your-ha-address>:8901/mcp \
  --header "Authorization: Bearer <your token>"
```

The token is in the addon Configuration (`auth_token`) — if you left it blank, the addon generated
one and prefilled it there on first start (also persisted at `<backup_dir>/auth/static_token`).
A raw-IP URL works in token mode.

Then start Claude Code — the `haops_*` tools will be available.

> **Why a token and not OAuth?** OAuth was the default until Aug 2026, when Claude Code
> ~v2.1.234+ began enforcing OAuth 2.0's TLS requirement for token endpoints
> (`Refusing to send credentials to non-https token endpoint`; only localhost exempt; intended
> behavior upstream, [claude-code#3320](https://github.com/anthropics/claude-code/issues/3320)).
> New OAuth setups on a plain-HTTP LAN — the typical deployment of this addon — can't meet that
> requirement, so v0.62.0 switched the default to a static Bearer token, which conforms (a
> pre-supplied header involves no OAuth credential exchange). OAuth remains available as an
> **experimental** mode (`auth_mode: oauth`) for HTTPS/localhost deployments.

## Tools

See [docs/TOOLS.md](https://github.com/dude84/ha-ops-mcp/blob/main/docs/TOOLS.md) for the full tool reference and [docs/HA_API_CAPABILITIES.md](https://github.com/dude84/ha-ops-mcp/blob/main/docs/HA_API_CAPABILITIES.md) for per-tool backend dependencies.

## More information

- [README](https://github.com/dude84/ha-ops-mcp) — overview, installation, examples
- [HA_COMPATIBILITY](https://github.com/dude84/ha-ops-mcp/blob/main/docs/HA_COMPATIBILITY.md) — supported HA versions and the HA API surface this addon depends on
- [CHANGELOG](https://github.com/dude84/ha-ops-mcp/blob/main/CHANGELOG.md) — release history
- [Issues](https://github.com/dude84/ha-ops-mcp/issues) — bug reports and feature requests
