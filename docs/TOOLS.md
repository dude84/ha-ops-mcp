# Tools (60)

All tools are prefixed `haops_` to avoid collisions with other MCP servers.

## Database tools (`haops_db_*`)

| Tool | Type | Description |
|---|---|---|
| `haops_db_query` | Read | Execute read-only SQL. Enforced at connection level. Reports backend type so the LLM can adjust SQL dialect. |
| `haops_db_health` | Read | Health dashboard — table sizes, schema version, InnoDB buffer pool hit rate (MariaDB), cache ratio (PostgreSQL), file size + integrity (SQLite). |
| `haops_db_execute` | Write | Two-phase SQL execution. Preview shows EXPLAIN plan + token. Confirm executes. No safety net beyond two-phase confirmation. For purging old data, prefer `haops_db_purge`. |
| `haops_db_purge` | Write | Managed purge via HA's `recorder.purge` service (handles the self-referential FK in `states` correctly). Dry-run mode shows estimates. |
| `haops_db_statistics` | Read | Statistics table management — list, find orphans (no matching entity), find stale entries, detailed info per statistic_id. |

## Configuration tools (`haops_config_*`)

| Tool | Type | Description |
|---|---|---|
| `haops_config_read` | Read | Read any file under config root. `secrets.yaml` values redacted by default. Path traversal blocked. Supports byte-range and line-range chunking for large files. |
| `haops_config_patch` | Write | Patch an existing YAML file via unified diff. Two-phase: preview returns diff + token, apply writes. Strict exact-match (no fuzz). Canonicalises YAML to suppress HA's re-wrap churn. Supports `auto_apply` for single-call atomic operations. |
| `haops_config_create` | Write | Create a new file (rejects if path exists). Two-phase with `auto_apply` option. |
| `haops_config_apply` | Write | Apply a change from `config_patch` or `config_create`. Creates rollback savepoint + persistent backup. Preserves YAML comments via ruamel.yaml. |
| `haops_config_validate` | Read | Run HA's config check. Returns valid/invalid with error details. |
| `haops_config_search` | Read | Recursive regex/substring search across `**/*.yaml` under config root. Optional `.storage/core.*` registry scan. |

## Dashboard tools (`haops_dashboard_*`)

| Tool | Type | Description |
|---|---|---|
| `haops_dashboard_list` | Read | List all Lovelace dashboards (storage + YAML mode). |
| `haops_dashboard_get` | Read | Get full config, a specific view, or a lightweight summary. Prefers filesystem, falls back to WebSocket. |
| `haops_dashboard_diff` | Write | Preview changes — full config replace, single-view replace, or view append. Returns confirmation token. For surgical edits (one card/field), prefer `haops_dashboard_patch`. |
| `haops_dashboard_patch` | Write | JSON Patch (RFC 6902) input — `add`/`remove`/`replace`/`move`/`copy`/`test`. Shows only the targeted edit in the approval modal. Supports `auto_apply`. For full view rewrites, use `haops_dashboard_diff`. |
| `haops_dashboard_apply` | Write | Apply dashboard changes via WebSocket. Creates rollback savepoint + persistent backup. |
| `haops_dashboard_validate_yaml` | Read | Pre-paste validator — catches structural errors, missing card types, field-type mismatches against bundled card schemas. |
| `haops_dashboard_resources` | Read | List frontend resources (system-wide — applies to all dashboards). Confirm `custom:<card>` modules are loaded before referencing them. |

## Entity tools (`haops_entity_*`)

| Tool | Type | Description |
|---|---|---|
| `haops_entity_state` | Read | Full state + attributes for one or more entities. |
| `haops_entity_list` | Read | Filter by domain, area, state, integration, staleness. Pagination, projection, count_only. |
| `haops_entity_find` | Read | Fuzzy search across entity_id, friendly_name, device name, area name. |
| `haops_entity_audit` | Read | Health report — unavailable, orphaned, stale, duplicate names, area:device ratio outliers. |
| `haops_entity_remove` | Write | Two-phase entity removal with backup and rollback savepoints. |
| `haops_entity_disable` | Write | Two-phase bulk disable with rollback savepoints. |

## Registry tools

| Tool | Type | Description |
|---|---|---|
| `haops_registry_query` | Read | Generic access to `.storage/core.*` registries: devices, entities, areas, floors, config_entries. Filter, project, paginate. |
| `haops_device_info` | Read | Device lookup by ID or name — full record + linked entities with state + area resolution. |

## System tools (`haops_system_*`)

| Tool | Type | Description |
|---|---|---|
| `haops_system_info` | Read | HA version, DB backend/schema, entity counts, timezone. |
| `haops_system_logs` | Read | Filtered error log — by level, integration, regex, line count. |
| `haops_system_reload` | Write | Targeted domain reload (automations, scripts, scenes, core, all) without restart. Optional post-reload entity verification. |
| `haops_system_restart` | Write | Two-phase HA restart. Last resort — prefer `haops_system_reload` for individual domains. |
| `haops_system_backup` | Write | Trigger a full HA backup via Supervisor or Core API. |
| `haops_self_check` | Read | Validate all connections — REST, WebSocket, database, filesystem, backup directory. Run first to diagnose connectivity. |
| `haops_tools_check` | Read | Passive integration test — exercises each tool group with real read-only operations. Run after HA upgrades. |

## Service tools

| Tool | Type | Description |
|---|---|---|
| `haops_service_call` | Write | Generic HA service call — the "everything else" escape hatch. Captures before/after state. |

## Backup & rollback tools

| Tool | Type | Description |
|---|---|---|
| `haops_backup_list` | Read | List persistent backups with type, timestamp, size, originating operation. |
| `haops_backup_revert` | Write | Two-phase revert from backup. Shows diff, annotates intended_revert vs drift. For in-session changes, prefer `haops_rollback`. |
| `haops_backup_prune` | Write | Two-phase prune per retention policy. |
| `haops_rollback` | Write | Two-phase undo of any committed transaction from the current session. Per-target diffs in preview. Preferred over `haops_backup_revert` for recent changes. |

## Batch tools

| Tool | Type | Description |
|---|---|---|
| `haops_batch_preview` | Write | Atomic multi-target preview — compose config patches and dashboard patches into a single diff + token. |
| `haops_batch_apply` | Write | Apply a batch with per-item savepoints. Rolls back on mid-batch failure. |

## Reference graph tools

Stateless typed graph rebuilt per query — registries, structured YAML, dashboards (storage + YAML mode), loose YAML scan, and Jinja template refs.

| Tool | Type | Description |
|---|---|---|
| `haops_references` | Read | Incoming + outgoing refs for a node. Accepts typed ids or bare entity_ids. |
| `haops_refactor_check` | Read | "What breaks if I rename/delete X?" — per-file ref counts + edit pointers. |

## Debugger tools

| Tool | Type | Description |
|---|---|---|
| `haops_entity_history` | Read | Recorder history over a time window. |
| `haops_logbook` | Read | Narrative event stream — automation triggers, script runs, status changes. |
| `haops_template_render` | Read | Preview Jinja template output against live HA state. |
| `haops_service_list` | Read | Service schemas — field names, descriptions, required flags. |
| `haops_automation_trace` | Read | Per-step execution data for debugging automations. |

## Ergonomic wrappers

| Tool | Type | Description |
|---|---|---|
| `haops_automation_trigger` | Write | Fire an automation. |
| `haops_script_run` | Write | Run a script. |
| `haops_scene_activate` | Write | Activate a scene. |
| `haops_integration_reload` | Write | Reload a config entry. |
| `haops_entities_assign_area` | Write | Bulk area reassignment. Two-phase. |
| `haops_entity_customize` | Write | Update entity registry options (name, icon, unit, device_class). Two-phase. |

## Superuser tools

| Tool | Type | Description |
|---|---|---|
| `haops_exec_shell` | Write | Two-phase shell execution. No safety net beyond two-phase confirmation. |
| `haops_addon_list` | Read | List installed add-ons. Requires Supervisor API. |
| `haops_addon_info` | Read | Add-on details + live resource stats. |
| `haops_addon_logs` | Read | Add-on log output. |
| `haops_addon_restart` | Write | Two-phase add-on restart. |

## OAuth management tools

| Tool | Type | Description |
|---|---|---|
| `haops_auth_status` | Read | OAuth status — enabled/disabled, registered clients, active tokens (masked), TTLs. |
| `haops_auth_clear` | Write | Two-phase clear of OAuth state (clients, tokens, codes). |
