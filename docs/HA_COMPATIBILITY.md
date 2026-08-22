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
| **Built against** | HA Core **2026.8.3** |
| **Recorder DB schema** | **53** (unchanged since 2026.5) |
| **Oldest supported** | **2026.6** |
| **Newest verified** | **2026.8** |
| Verified how | `haops_tools_check` → `all_pass`, 16/16 groups, 0 broken tools |
| Verified on | Singapore **and** Poland HA, 2026-08-22, addon v0.64.1 → re-confirmed on v0.64.3 |

"Newest verified" goes stale by design — HA ships on the first Wednesday of every month. A newer
HA is **not** a known failure; it means nobody has run the suite yet. The startup warning says
exactly that.

## Verification history

| ha-ops-mcp | HA Core | DB schema | Result | Date |
|---|---|---|---|---|
| 0.64.3 | 2026.8.3 | 53 | 16/16 pass, both instances (deployed version) | 2026-08-22 |
| 0.64.1 | 2026.8.3 | 53 | 16/16 pass, both instances (config_flow group) | 2026-08-22 |
| 0.61.1 | 2026.8.2 | 53 | 15/15 pass (docker_prune group) | 2026-08-15 |
| 0.57.1 | 2026.8.1 | 53 | 14/14 pass (Docker group enabled) | 2026-08-12 |
| 0.56.0 | 2026.8.1 | 53 | 13/13 pass | 2026-08-11 |
| 0.55.0 | 2026.7.4 | 53 | 13/13 pass | 2026-08-02 |
| 0.54.0 | 2026.6.3 | 53 | all backends ok | 2026-06-13 |
| 0.53.3 | 2026.6.1 | 53 | all backends ok | 2026-06-07 |
| 0.37.0 | 2026.5.4 | 53 | all backends ok | 2026-06-04 |

## How to re-verify after an HA update

1. `haops_tools_check` — 14 groups, all read-only. This is the whole test.
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
| `config/entity_registry/update` | `haops_entity_toggle`, `haops_entity_customize`, `haops_entity_rename` |
| `config/entity_registry/remove` | `haops_entity_remove` |
| `config/floor_registry/list` | `haops_registry_query` (file is often absent) |
| `config/auth/list`, `/create`, `/update`, `/delete` | `haops_user_*` |
| `config/auth_provider/homeassistant/create` | `haops_user_create` |
| `config/device_registry/remove_config_entry` | `haops_device_remove` |
| `config_entries/get` | `haops_device_remove` (per-entry `supports_remove_device`) |
| `config_entries/reload` | `haops_integration_reload` |
| `config_entries/flow/progress` | `haops_integration_flow_start` (duplicate-flow warning), `haops_tools_check` |
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

**Config-flow endpoints** (v0.64.0+, `haops_integration_flow_*`) — the method
matters here, so they are listed separately:

| Endpoint | Method | Used by |
|---|---|---|
| `/api/config/config_entries/flow` | **POST only** | `haops_integration_flow_start` |
| `/api/config/config_entries/flow/<flow_id>` | GET | `haops_integration_flow_step` (preview) |
| `/api/config/config_entries/flow/<flow_id>` | POST | `haops_integration_flow_step` (apply) |
| `/api/config/config_entries/flow/<flow_id>` | DELETE | `haops_integration_flow_abort` |

`GET` on the **index** (`/api/config/config_entries/flow`) is **not a route** —
it answers `405 Method Not Allowed`. The pending-flow listing is WS-only
(`config_entries/flow/progress`, above). v0.64.0 shipped a `tools_check` probe
that assumed the GET existed and reported a false failure on every healthy
instance; fixed in v0.64.1. Verified against 2026.8.2 and 2026.8.3.

### Supervisor endpoints

`/addons` · `/addons/self/info` · `/addons/<slug>/info|restart|stats` ·
`/addons/<slug>/update` · `/store/reload` ·
`/core/options|restart|start|stop` · `hassio/backup_full` · `/docker/info`

**`/addons/<slug>/update` cannot target this add-on itself.** Supervisor
refuses with `403 {"result":"error","message":"App <slug> can't update
itself!"}`. It is an upstream guard, not a role or token problem —
`hassio_role: manager` plus a valid `SUPERVISOR_TOKEN` make no difference
(verified 2026-08-22, Supervisor 2026.07.5). Updating ha-ops-mcp is therefore
a Home Assistant **UI** action, permanently. `haops_addon_update` reports the
refusal verbatim rather than pretending; do not rebuild a workaround around
it. Updating *other* add-ons works normally.

### Docker Engine socket (v0.57.0+, opt-in)

`/run/docker.sock` — bind-mounted by Supervisor, **not** always present. Engine API
`v1.41` pinned; endpoints used: `GET /containers/json` · `GET /containers/<id>/logs` ·
`POST /containers/<id>/exec` · `POST /exec/<id>/start` · `GET /exec/<id>/json`.

Two facts to re-check if container tools ever break:

1. **Supervisor decides the mount at container-creation time**, in
   `supervisor/docker/app.py`:
   ```python
   if not self.app.protected and self.app.access_docker_api:
       mounts.append(MOUNT_DOCKER)   # /run/docker.sock -> /run/docker.sock, read_only=True
   ```
   So `docker_api: true` in our manifest plus Protection mode OFF is necessary
   **and** the add-on must be restarted afterwards — toggling protection on a
   running container changes nothing. Verified 2026-08-12 on Supervisor 2026.07.5.
2. **`docker_api` is NOT a read-only API**, despite Supervisor's own docstring
   ("Return if the app need read-only Docker API access") and HA's docs. `read_only=True`
   is a bind-mount flag on the socket *inode*; `connect()` still yields the full
   bidirectional Engine API. `exec` works — verified 2026-08-12 against the ESPHome
   add-on container. There is no filtering proxy in the path, and `full_access: true`
   is **not** required. Supervisor's `/docker/info` endpoint (metadata only, gated by the
   same flag) is probably what gave "read-only" its reputation.

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

### `.storage` registry writes are debounced — all versions

HA persists `core.*_registry` through a delayed `Store` save, so the file lags live state after
any change and the delay restarts on each further change: a burst of renames can hold the flush
off well past the nominal delay. A filesystem-first read taken right after a mutation therefore
reports pre-mutation records — the origin of a real bug where `haops_registry_query` listed two
devices HA had already dropped, and three removal attempts answered `Unknown device`.

Handled in `storage_registry.py`: any successful `config/*_registry/<write>` command stamps a
process write clock (detected in the WS client, so no tool can forget), and a read whose file
mtime predates that stamp is served from the live WebSocket registry instead. Reads report
`provenance` either way. Nothing here is version-specific in the sense of "might be fixed" — the
debounce is by design; only the *nominal* delay could change, and the mtime comparison does not
depend on its value.


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

### Device registry splits per integration — 2026.8 (VERIFIED 2026-08-13)

A physical device delivered by more than one integration becomes **one device entry per
integration** instead of a merged entry; entities migrate between them and HA raises a "replaced
device" repair. Devices become restricted to a single config entry and at most one subentry.

Nothing in ha-ops-mcp persists a `device_id`, so there was no stale state to migrate — every tool
resolves devices at call time. What **did** break is the record shape.

Verified against the live PL instance (HA 2026.8.1, migration ran 2026-08-05, leaving a
`core.device_registry.<ts>.migration_backup` beside the file):

- `.storage/core.device_registry` is now **version 3, minor 2**. The plural **`config_entries`
  list is gone from storage**, replaced by singular `config_entry_id` + `config_subentry_id`, plus
  `primary_config_entry`, `composite_device_id`, `composite_primary_config_entry`, `split_at` and
  `has_composite_identifiers`. On that instance: 142/142 devices carry `config_entry_id`, 3 are
  splits (`split_at` + `composite_device_id` set).
- **The WebSocket payload still carries `config_entries`.** `DeviceEntry.dict_repr` emits it and
  `config_entries_subentries` with a comment marking both deprecated-and-kept. So the *same device*
  reads differently depending on which tier answered — and since this server is filesystem-first by
  design, the default path is the one that changed.

That asymmetry is a trap worth naming: a compat shim on the API can hide a storage-schema break
from anyone testing through the API, while a filesystem-first reader takes it head-on. Handled by
`storage_registry.device_config_entry_ids()`, which accepts both shapes; every caller goes through
it (`haops_device_info`, `haops_device_remove`, the refindex device→config-entry edges). It
deliberately ignores `composite_primary_config_entry`, which names the pre-split composite's
former primary rather than an entry the record belongs to.

Also expect `haops_device_info`, `haops_registry_query`, `haops_entity_audit` and
`haops_references` to report **more devices than before**, with duplicate-looking names.

### ESPHome add-on container (used by `haops_esphome_*`)

Not an HA API, but an external surface with the same "silently moves" risk, so it belongs on this
list. Verified 2026-08-13 against `ghcr.io/esphome/esphome-hassio:2026.7.4`:

| Fact | Value |
|---|---|
| Container name | `app_<slug>_esphome` (matched on name/image containing `esphome`) |
| CLI | `/usr/local/bin/esphome` |
| Node configs | `/config/esphome/*.yaml` — the same path we see |
| Build output (current) | **`/config/esphome/.esphome/build/<node>/`** — under the config root, so readable with no Docker |
| Build output (legacy) | `/data/build/<node>/` — the add-on's private volume, Docker-only; survives an upgrade |
| Artifacts (Arduino/ESP8266) | `.pioenvs/<node>/firmware{,.ota,.factory}.bin` |
| Artifacts (ESP-IDF/ESP32) | `build/firmware{,.ota,.factory}.bin` |
| Size report | PlatformIO's `Flash: [== ] xx.x% (used A bytes from B bytes)` on stdout |

The build path is the one that matters, and it **moved**. Older add-on versions built into the
add-on's private `/data/build`, unreachable from our filesystem; current versions build under the
config root, where a plain file read gets the firmware size. Both trees can hold a copy of the same
artifact — the old one is not cleaned up on upgrade — so `haops_esphome_status` checks both and lets
the newest mtime win. Reporting a stale artifact right after a fresh compile is worse than
reporting none, and that is exactly what a `/data`-only reader did.

Consequence: only *compiling* needs the Protection-mode opt-in. Firmware sizes for a
recently-built node do not.
