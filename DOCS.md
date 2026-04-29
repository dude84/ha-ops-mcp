# ha-ops-mcp

An MCP server addon that gives AI assistants operational access to your Home Assistant instance. 60 tools covering database management, YAML config editing (with comment preservation), Lovelace dashboard CRUD via JSON Patch, entity registry hygiene, cross-surface reference graph, automation debugging, system health monitoring, add-on management, and shell access — with two-phase confirmation, automatic backups, in-session rollback, and a full audit trail.

Not for device control. For the maintenance work that comes after setup.

## Sidebar panel

The addon adds an **HA Ops** panel to your HA sidebar (via ingress). Three tabs:

- **Timeline** — chronological feed of every mutation with expandable inline diffs (unified for config, structured for dashboards). The most recent apply carries a one-click **Revert** button while the session is active. Rollback and apply entries are visually linked. Paginated 50 per page; auto-refreshes every 5 seconds on page 1 (paused on deeper pages so the offset window doesn't shift under you).
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

### Transport

- **sse** (default): Server-Sent Events on port 8901. Recommended for the addon.
- **streamable-http**: Alternative HTTP transport, also on port 8901.

### Database URL

Leave blank to auto-detect from HA's recorder config. Or specify explicitly:

- SQLite: `sqlite:////config/home-assistant_v2.db`
- MariaDB: `mysql://homeassistant:password@core-mariadb/homeassistant`
- PostgreSQL: `postgresql://homeassistant:password@localhost/homeassistant`

### Backup

- **Backup directory**: default `/backup/ha-ops-mcp` (HA's persistent `/backup` volume).
- **Max age days**: default 30 — backups older than this are pruned automatically.
- **Max per type**: default 100 — cap per backup type (config, dashboard, entity, db).

## Connecting an MCP client

### Claude Code

```bash
claude mcp add --transport sse ha-ops http://<your-ha-address>:8901/sse
```

Then start Claude Code — the 58 `haops_*` tools will be available.

## Tools

See [docs/TOOLS.md](https://github.com/dude84/ha-ops-mcp/blob/main/docs/TOOLS.md) for the full tool reference and [docs/HA_API_CAPABILITIES.md](https://github.com/dude84/ha-ops-mcp/blob/main/docs/HA_API_CAPABILITIES.md) for per-tool backend dependencies.

## More information

- [README](https://github.com/dude84/ha-ops-mcp) — overview, installation, examples
- [CHANGELOG](https://github.com/dude84/ha-ops-mcp/blob/main/CHANGELOG.md) — release history
- [Issues](https://github.com/dude84/ha-ops-mcp/issues) — bug reports and feature requests
