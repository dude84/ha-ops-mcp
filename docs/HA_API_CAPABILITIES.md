# Capability Matrix

Each tool depends on one or more backend **capabilities**. If a capability isn't available (e.g., WebSocket broken, no DB connection, not running as an addon), the affected tools fail or degrade gracefully.

## Capabilities

- **REST** — HA REST API
- **WS** — HA WebSocket API
- **DB** — Direct database connection (SQLite/MariaDB/PostgreSQL)
- **FS** — Filesystem access to `/config`
- **Shell** — Subprocess execution in the server environment
- **Sup** — Supervisor API (HA OS / Supervised only)

## Token types

- **Any** — both Supervisor token and long-lived access token work
- **Sup only** — requires the auto-injected `SUPERVISOR_TOKEN` (addon context). Long-lived user tokens are rejected by the Supervisor API.

The addon auto-detects token type and routes requests accordingly — Supervisor token routes through `http://supervisor/core`, long-lived tokens route directly to `http://homeassistant:8123`. No manual URL configuration needed.

## Per-tool matrix

Legend: ● = required, ◐ = preferred (falls back to another capability), ○ = optional enhancement

| Tool | REST | WS | DB | FS | Shell | Sup | Token | Notes |
|---|---|---|---|---|---|---|---|---|
| **Database** |
| `haops_db_query` | | | ● | | | | Any | |
| `haops_db_health` | | | ● | | | | Any | |
| `haops_db_execute` | | | ● | | | | Any | |
| `haops_db_purge` | ● | | ● | | | | Any | Uses `recorder.purge` service via REST |
| `haops_db_statistics` | | | ● | | | | Any | |
| **Configuration** |
| `haops_config_read` | | | | ● | | | Any | |
| `haops_config_patch` | | | | ● | | | Any | |
| `haops_config_create` | | | | ● | | | Any | |
| `haops_config_apply` | | | | ● | | | Any | |
| `haops_config_validate` | ● | ○ | | | | | Any | REST with WS fallback |
| `haops_config_search` | | | | ● | | | Any | |
| **Dashboard** |
| `haops_dashboard_list` | | ● | | | | | Any | Fails without WS |
| `haops_dashboard_get` | | ◐ | | ◐ | | | Any | FS preferred, WS fallback |
| `haops_dashboard_diff` | | ◐ | | ◐ | | | Any | Same as `get` |
| `haops_dashboard_patch` | | ◐ | | ◐ | | | Any | Same read tiers as `get` |
| `haops_dashboard_apply` | | ● | | | | | Any | Writes require WS |
| `haops_dashboard_validate_yaml` | | | | | | | Any | Pure-local: bundled card schemas |
| `haops_dashboard_resources` | | ◐ | | ◐ | | | Any | FS preferred, WS fallback for YAML-mode |
| **Entity** |
| `haops_entity_state` | ● | | | | | | Any | |
| `haops_entity_list` | | ◐ | | ◐ | | | Any | FS preferred, WS fallback |
| `haops_entity_find` | ○ | ◐ | | ◐ | | | Any | FS preferred; REST best-effort for live names |
| `haops_entity_audit` | | ◐ | | ◐ | | | Any | Same as list |
| `haops_entity_remove` | | ● | | | | | Any | WS only |
| `haops_entity_disable` | | ● | | | | | Any | WS only |
| **Registry** |
| `haops_registry_query` | | ◐ | | ◐ | | | Any | FS preferred, WS fallback |
| `haops_device_info` | ○ | ◐ | | ◐ | | | Any | FS preferred |
| **Helper** (input_*, counter, timer, schedule) |
| `haops_helper_list` | | ● | | | | | Any | WS `<domain>/list` per requested domain |
| `haops_helper_create` | | ● | | ◐ | | | Any | WS `<domain>/create`; FS read of entity registry for optional rename |
| `haops_helper_update` | | ● | | ◐ | | | Any | WS `<domain>/update`; FS read of entity registry to resolve entity_id → collection id |
| `haops_helper_delete` | | ● | | ◐ | | | Any | WS `<domain>/delete`; FS read for entity_id resolution |
| **System** |
| `haops_system_info` | ● | | ○ | ○ | | | Any | Works without DB/FS but with less detail |
| `haops_system_logs` | ○ | | | ◐ | | | Any | FS preferred, REST fallback |
| `haops_system_reload` | ● | | | | | | Any | |
| `haops_system_restart` | ● | | | | | | Any | |
| `haops_system_backup` | ◐ | | | | | ◐ | Any | Supervisor preferred; REST fallback |
| **Service** |
| `haops_service_call` | ● | | | | | | Any | |
| **Backup & Rollback** |
| `haops_backup_list` | | | | ● | | | Any | Reads manifest from disk |
| `haops_backup_revert` | | ○ | | ● | | | Any | Dashboard restore requires WS |
| `haops_backup_prune` | | | | ● | | | Any | |
| `haops_rollback` | | ○ | | ○ | | | Any | Depends on target type |
| **Batch** |
| `haops_batch_preview` | | ◐ | | ◐ | | | Any | Depends on item types |
| `haops_batch_apply` | | ◐ | | ◐ | | | Any | Same |
| **Reference graph** |
| `haops_references` | | ◐ | | ◐ | | | Any | Rebuilds index from FS; WS as fallback |
| `haops_refactor_check` | | ◐ | | ◐ | | | Any | Same |
| **Debugger** |
| `haops_entity_history` | ● | | | | | | Any | |
| `haops_logbook` | ● | | | | | | Any | |
| `haops_template_render` | ● | | | | | | Any | |
| `haops_service_list` | ◐ | ◐ | | | | | Any | WS preferred (richer schemas) |
| `haops_automation_trace` | | ● | | | | | Any | WS-only |
| **Ergonomic wrappers** |
| `haops_automation_trigger` | ● | | | | | | Any | |
| `haops_script_run` | ● | | | | | | Any | |
| `haops_scene_activate` | ● | | | | | | Any | |
| `haops_integration_reload` | | ● | | | | | Any | |
| `haops_entities_assign_area` | | ● | | | | | Any | |
| `haops_entity_customize` | | ● | | | | | Any | |
| **Superuser** |
| `haops_exec_shell` | | | | | ● | | Any | |
| `haops_addon_list` | | | | | | ● | **Sup only** | |
| `haops_addon_info` | | | | | | ● | **Sup only** | |
| `haops_addon_logs` | | | | | | ● | **Sup only** | |
| `haops_addon_restart` | | | | | | ● | **Sup only** | |
| **OAuth management** |
| `haops_auth_status` | | | | | | | Any | Pure-local: reads OAuth store |
| `haops_auth_clear` | | | | | | | Any | Pure-local: clears OAuth store |
| **Diagnostic** |
| `haops_self_check` | — | — | — | — | — | — | Any | Always runs |
| `haops_tools_check` | — | — | — | — | — | — | Any | Always runs |
