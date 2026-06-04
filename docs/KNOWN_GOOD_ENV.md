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
