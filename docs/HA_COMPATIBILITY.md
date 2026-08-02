# Home Assistant Compatibility

What HA Core versions this server is **built against**, **verified against**, and **expected to
work against** — plus the exact HA API surface it depends on, so a future HA release can be
diffed against a list instead of guessed at.

The machine-readable version of the top table lives in `src/ha_ops_mcp/compat.py`. **Keep the two
in sync** — `haops_system_info` and `haops_tools_check` both report from that module, and the
server logs a warning at startup when the live instance falls outside the window.

> Not to be confused with `KNOWN_GOOD_ENV.md`, which snapshots the *whole client+server stack*
> (Claude Code, macOS, terminal, Bun) for connectivity debugging. This file is only about the
> **HA-side API contract**.

## Current window

| | |
|---|---|
| **Built against** | HA Core **2026.7.4** |
| **Recorder DB schema** | **53** (unchanged since 2026.5) |
| **Oldest supported** | **2026.5** |
| **Newest verified** | **2026.7** |
| Verified how | `haops_tools_check` → `all_pass`, 13/13 groups, 0 broken tools |
| Verified on | Singapore HA, 2026-08-02, addon v0.55.0 |

"Newest verified" goes stale by design — HA ships on the first Wednesday of every month. A newer
HA is **not** a known failure; it means nobody has run the suite yet. The startup warning says
exactly that.

## Verification history

| ha-ops-mcp | HA Core | DB schema | Result | Date |
|---|---|---|---|---|
| 0.55.0 | 2026.7.4 | 53 | 13/13 pass | 2026-08-02 |
| 0.54.0 | 2026.6.3 | 53 | all backends ok | 2026-06-13 |
| 0.53.3 | 2026.6.1 | 53 | all backends ok | 2026-06-07 |
| 0.37.0 | 2026.5.4 | 53 | all backends ok | 2026-06-04 |

## How to re-verify after an HA update

1. `haops_tools_check` — 13 groups, all read-only. This is the whole test.
2. If a group fails, its `tools_affected` list names the broken tools directly.
3. Bump `BUILT_AGAINST_HA` / `MAX_TESTED_HA` in `src/ha_ops_mcp/compat.py`, add a row above, and
   add a `KNOWN_GOOD_ENV.md` baseline row.

## HA API surface we depend on

This is the blast radius. When HA publishes breaking changes, check them against **this list** —
almost all HA breakage is integration-level and touches none of it.

### WebSocket commands

| Command | Used by |
|---|---|
| `config/check_config` | `haops_config_validate` |
| `config/area_registry/list` | registry tools (fallback) |
| `config/device_registry/list` | registry tools (fallback) |
| `config/entity_registry/list` | registry tools (fallback) |
| `config/entity_registry/update` | `haops_entity_toggle`, `haops_entity_customize` |
| `config/entity_registry/remove` | `haops_entity_remove` |
| `config/floor_registry/list` | `haops_registry_query` (file is often absent) |
| `config/auth/list`, `/create`, `/update`, `/delete` | `haops_user_*` |
| `config/auth_provider/homeassistant/create` | `haops_user_create` |
| `config_entries/reload` | `haops_integration_reload` |
| `lovelace/config`, `lovelace/config/save` | dashboard read/write |
| `lovelace/dashboards/list`, `lovelace/resources` | `haops_dashboard_list`, `_resources` |
| `trace/get`, `trace/list` | `haops_automation_trace` |
| `zha/devices`, `zha/groups` | `haops_ws_command` allowlist |
| `zha/devices/reconfigure` | `haops_zha_reconfigure_device` |
| `zha/topology/update` | `haops_zigbee_scan` |
| `backup/create` | `haops_system_backup` |
| `ping`, `get_config`, `get_services` | connection health, `haops_service_list` |

### REST endpoints

`/api/config` · `/api/states` · `/api/states/<entity_id>` · `/api/services` ·
`/api/services/<domain>/<service>` · `/api/template` · `/api/error_log` ·
`/api/history/period/<ts>` · `/api/logbook/<ts>`

### Supervisor endpoints

`/addons` · `/addons/self/info` · `/addons/<slug>/info|restart|stats` ·
`/core/options|restart|start|stop` · `hassio/backup_full`

### `.storage` files (Tier 1 — preferred over the API)

`core.entity_registry` · `core.device_registry` · `core.area_registry` ·
`core.floor_registry` · `core.config_entries` · `lovelace` · `lovelace.<id>` ·
`lovelace_resources` · `auth` · `auth_provider.homeassistant`

### Recorder tables

`states` · `events` · `statistics` · `statistics_short_term` · `statistics_meta` ·
`recorder_runs` · `schema_changes`

### Other

`zigbee.db` (zigpy — separate from the recorder DB; table suffix `_v15` as of 2026.7) ·
`/config/.HA_VERSION`

## Known HA-version-specific behaviour

### ZHA device removal — 2026.7

`zha/devices/remove` and `zha/device/remove` both answer **"Unknown command"**. The generic
`config/device_registry/remove_config_entry` also fails — the ZHA config entry reports
`supports_remove_device: false`. The working path is the **`zha.remove` service** with
`{"ieee": "<colon-separated>"}`. For an offline device the leave request can't be ACKed, but
zigpy purges it anyway.

`zha/devices/reconfigure` and `zha/topology/update` **are** still live on 2026.7.4 (probed
directly). `haops_tools_check` now probes `zha/devices/reconfigure` on every run, so a future
removal surfaces as a failing check instead of a silently broken tool.

### HA Core moved to Python 3.14 — 2026.7

Broke an `IntEnum.__str__` assumption in the WebSocket alive-check, causing a reconnect storm.
Fixed in v0.54.3. Mentioned here because "HA bumped its bundled Python" is a category of
breakage that has nothing to do with HA's own API and won't appear in any HA changelog.

### Automation trigger/condition key renames — 2026.7

`battery.low` → `battery.became_low`, `vacuum.docked` → `vacuum.returned_to_dock`, and others.
We don't parse trigger keys, so no tool is affected — but `haops_config_validate` will surface
these against a user's own automations, which is correct behaviour, not a bug.

### HA Core port is not fixed — 2026.8

New HA OS installs default to **port 80** instead of 8123; existing installs are unchanged.
`run.sh` probes 8123 then 80 on the long-lived-token path. The Supervisor-token path
(`http://supervisor/core`) is unaffected either way.

### Device registry splits per integration — 2026.8 (not yet verified)

A physical device delivered by more than one integration becomes **one device entry per
integration** instead of a merged entry; entities migrate between them and HA raises a "replaced
device" repair. Devices become restricted to a single config entry and at most one subentry.

Nothing in ha-ops-mcp persists a `device_id`, so there is no stale state to migrate — every tool
resolves devices at call time. But expect `haops_device_info`, `haops_registry_query`,
`haops_entity_audit` and `haops_references` to report **more devices than before**, and expect
duplicate-looking names. Re-verify these four after updating to 2026.8.
