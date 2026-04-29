# HA Quirks & Operational Patterns

Living reference for known quirks, gotchas, and field-tested patterns when
working with Home Assistant. Split into two sections: YAML/formatting quirks
(what the controller needs to avoid traps) and operational patterns (what
drives tool design decisions).

Add new entries as we hit them. Keep it terse — lookup table, not essay.

---

## YAML & Formatting Quirks

### Lovelace dashboard YAML — paste-back into HA's raw editor

**Use case:** controller wants to show the user paste-ready YAML for the
HA raw config / section editor.

**Bottom line:** don't, if you can avoid it. Use
`haops_dashboard_diff` + `haops_dashboard_apply` — they go through the
WebSocket save path which round-trips losslessly. Manual paste only as
a fallback when WS is broken.

If you must paste, the format you produce has to byte-match what HA's
own emitter would produce, because HA's editor silently rejects or
mangles drift. Failure modes seen in the field:

#### 1. Folded `>-` vs literal `|-` on `[[[ ... ]]]` templates

button-card's `custom_fields.info` / `custom_fields.room` etc. embed JS
templates as `[[[ return ... ]]]`. HA emits these as **folded** scalars
(`>-`). ruamel's default for multi-line strings with embedded newlines
is **literal** (`|-`). Visually similar, parses as YAML fine, but
breaks button-card's template parser at runtime.

Rule: any string containing `[[[` ... `]]]` -> emit as `>-`.

#### 2. Blank-line count between JS statements

In a `>-` folded scalar, **one** blank line in the YAML source unfolds
to `\n` between statements in the final string. **Two** blank lines
unfold to `\n\n`. HA emits one. `"\n\n".join(stmts)` fed into a
ruamel `FoldedScalarString` emits two and produces the wrong runtime
behaviour.

Rule: separate JS statements by exactly one blank line in the source.

#### 3. ~80-column wrap inside attribute values

HA wraps folded-scalar continuation lines aggressively at space
boundaries — including inside HTML attribute values like
`<ha-icon icon="mdi:bed" style="...">`. ruamel with `width=80`
sometimes leaves long lines intact when it can't find a "natural"
breakpoint. The Lovelace editor refuses single-long-line YAML on save
even though it parses.

Rule: target ~80-col wrap; continuation lines sit at exactly the base
indent (no extra indentation on the continuation).

#### 4. `grid-template-areas` quoting

HA emits `grid-template-areas: "\"room info\""` — double-quoted YAML
with escaped inner double quotes. Single-quoted (`'"room info"'`) is
semantically identical YAML but some Lovelace parse paths are picky
and reject it.

Rule: when a string contains literal double quotes, emit YAML as
double-quoted with `\"` escapes, not single-quoted.

#### 5. Sequence indent

HA's default: list items indented 2 spaces past the parent key
(`sequence=4, offset=2` in ruamel terms). Most other YAML emitters
don't bother and emit `- item` flush with the parent key.

Rule: 2-space sequence indent past parent.

---

## Database Operational Patterns

### The `states` table self-referential FK

The `states` table has `old_state_id` pointing back to itself
(`states.state_id`). Bulk `DELETE FROM states WHERE last_updated_ts < X`
fails with FK constraint violations.

Workarounds:
- **MariaDB/MySQL:** `SET FOREIGN_KEY_CHECKS=0` before delete
- **All backends:** `UPDATE states SET old_state_id = NULL` first, then delete

`haops_db_purge` uses HA's `recorder.purge` service which handles this
correctly. `haops_db_execute` warns when it detects a raw
`DELETE FROM states` without FK handling.

### InnoDB defaults are catastrophic at scale

MariaDB addon ships with `innodb_buffer_pool_size=128M` and
`innodb_log_file_size=48M`. On databases over ~2 GB, this makes every
query touching `states` painfully slow (~100x speedup observed by tuning
to 1536M/256M).

`haops_db_health` reports InnoDB buffer pool size and hit rate. Flag
when buffer pool is <25% of total DB size.

### Table size vs. actual disk usage

After deleting rows from InnoDB tables, the file doesn't shrink. Need
`OPTIMIZE TABLE` (MariaDB) or `VACUUM` (PostgreSQL/SQLite) to reclaim
space. HA recorder's `repack` parameter on `recorder.purge` handles
this, but doesn't always run.

`haops_db_health` reports both logical size (row count) and physical
size (file on disk) to surface fragmentation.

### Statistics orphans

When an entity is removed, its `statistics_meta` and associated
`statistics` / `statistics_short_term` rows remain forever. The
recorder does not clean these up.

`haops_db_statistics orphans` cross-references `statistics_meta` against
the entity registry. High-value tool — every long-running instance
accumulates these.

### phpMyAdmin timeout wall

HA Supervisor ingress proxy has a hardcoded timeout causing phpMyAdmin
to 504 on any query over a few seconds. Not configurable.

`haops_db_query` connects directly to the database, bypassing ingress.

---

## Entity Operational Patterns

### "Unavailable" means different things

An entity showing "unavailable" can mean:
- **Device offline** — transient, will recover (Zigbee/WiFi)
- **Integration removed** — permanent ghost, safe to remove
- **Integration misconfigured** — needs investigation, not removal
- **Entity disabled** — deliberately disabled by user
- **Entity orphaned** — device removed, registry entry remains

`haops_entity_audit` categorizes by probable cause. Do NOT recommend
bulk removal without categorization.

### Integration removal leaves ghosts everywhere

Removing an integration (e.g., UniFi) leaves: unavailable entities,
device registry entries, statistics entries, and broken automation
references. A "clean removal" workflow: `haops_entity_audit` +
`haops_db_statistics orphans` + `haops_refactor_check` + targeted
cleanup.

### Bulk operations need care

Deleting/disabling hundreds of entities rapidly can overwhelm HA's
event bus. `haops_entity_remove` and `haops_entity_disable` batch
operations.

---

## Configuration Operational Patterns

### YAML comment preservation is non-negotiable

PyYAML silently strips all comments on write. This has caused real data
loss in community tools. ha-ops-mcp uses `ruamel.yaml` exclusively.

### `secrets.yaml` is the keystore

`haops_config_read` redacts values by default. Other YAML files note
`!secret` references but don't resolve them.

### Storage-mode dashboards are NOT files

Storage-mode dashboards (default in modern HA) live in
`/config/.storage/lovelace.*` as JSON, managed via WebSocket.
Direct file editing works but HA won't pick up changes until restart.
Dashboard tools use WebSocket for writes, filesystem for reads.

### HACS integration configs live in unusual places

HACS integrations store config in config flows, not `configuration.yaml`.
Tuning parameters are only accessible via HA UI or REST API config
entries. `haops_registry_query(registry='config_entries')` surfaces these.

---

## System Operational Patterns

### Targeted reloads vs. full restart

HA supports reloading specific domains without restart. Full restart
takes 30-60+ seconds and disrupts everything. `haops_system_reload` is
always preferred; `haops_system_restart` is last resort.

### Error log is noisy

The HA error log mixes critical errors, flaky-integration warnings,
and noise. `haops_system_logs` supports filtering by integration and
severity. Raw dump is useless without filters.

### `MCP error -32602: Invalid request parameters` on every tool call → suspect auth expiry

When *every* tool call returns `-32602`, including no-arg ones like
`haops_system_info` or `haops_self_check`, the cause is almost
certainly an expired/lost OAuth Bearer token, NOT a params problem.
Confirm with curl:

```
curl -i http://homeassistant.local:8901/sse
# expect: HTTP/1.1 401 Unauthorized
#         www-authenticate: Bearer error="invalid_token", ...
```

The 401 is what the server actually sends. The `-32602` you see is
the Claude Code MCP client rewriting the 401 into a JSON-RPC error
on its way back. **This rewrite is client-side and not fixable in
ha-ops-mcp** — see the rejected-fix note below.

Fix in the moment: re-run the auth flow (`/mcp` in Claude Code, or
the equivalent in your MCP client). Common triggers: long sessions
crossing the token TTL (default 1 h), `/compact` re-mounting SSE
without carrying the token, addon restart invalidating in-memory
state. `haops_auth_status` reports `expires_at` + `ttl_seconds` per
access token if you want to refresh proactively.

**Rejected fix (do not propose again):** mapping the auth failure
to JSON-RPC `-32001 Authentication required` server-side. The 401
+ `WWW-Authenticate` is what spec-compliant MCP clients use to
discover the auth server and trigger refresh; replacing it with a
JSON-RPC body would break OAuth discovery. The misleading `-32602`
surface is generated by Claude Code's client, not by us — fix has
to land upstream there.

### MCP client says "Unable to connect" after a fresh addon install

Symptom is a connection-level failure from the MCP client, not a 401.
Confirm by inspecting the OAuth discovery doc the addon advertises:

```
curl -s http://homeassistant.local:8901/.well-known/oauth-authorization-server | jq .issuer
```

If `issuer` resolves to something the client can't dial (`http://none:8901/`,
`http://localhost:8901/`, etc.), the addon's auto-detection of HA's
`internal_url` returned a value that parses but isn't routable. Common
trigger: HA's **Settings → System → Network → Internal URL** is unset
or set to a placeholder; the supervisor returns `null` or a junk URL
and `urlparse(...).hostname` happily produces an unusable string.

Fix: set `auth_issuer_url` in the addon Configuration tab to a hostname
your MCP client can actually reach (e.g. `http://homeassistant.local:8901`
or your HA's LAN IP). Since v0.32.3 the addon falls back to
`homeassistant.local:8901` when auto-detection produces null/empty/
"none"/loopback values, so a fresh install on a default LAN works
without any manual config.

---

## Adding new entries

Template:

```
### <symptom>

<what broke, why, the rule — 2-5 lines max>
```

Keep entries scoped to "what you need to know to avoid the trap."
Implementation details belong in the relevant tool's source.
