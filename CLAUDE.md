# ha-ops-mcp — Implementation Guide

## What this project is

An MCP (Model Context Protocol) server that provides operations tools for Home Assistant power users. It covers database management, configuration file editing, dashboard CRUD, entity hygiene, and system health — the "ops" surface that no existing HA MCP server addresses.

**Read these documents before writing any code:**
1. This file — architecture, patterns, and implementation rules
2. `docs/HA_QUIRKS.md` — field-tested HA quirks and operational patterns
3. `docs/HA_COMPATIBILITY.md` — which HA versions we're built/verified against, and the exact HA
   API surface we depend on. **Read this after every HA update** — it's the list to diff HA's
   breaking changes against. The machine-readable half is `src/ha_ops_mcp/compat.py`; bumping one
   without the other is a bug (there's a test for it).

## Key architectural decisions

### Async everywhere
The entire codebase is async (asyncio). All connection clients, tool handlers, and I/O are async. The MCP server framework (FastMCP or `mcp` SDK) runs the event loop.

### Connection layer is a singleton context
`connections/` provides three clients: REST, WebSocket, Database. They are initialized once at server startup from `config.yaml` and injected into tool handlers (`HaOpsContext`). Do NOT create connections per-tool-call.

### Database abstraction — interface, not ORM
`connections/database.py` defines an abstract `DatabaseBackend` with methods like `query()`, `execute()`, `health()`, `explain()`. Three concrete classes: `SqliteBackend`, `MariaDbBackend`, `PostgresBackend`. Tools call backend methods, never raw SQLAlchemy. The backend auto-detects from the HA recorder `db_url`.

Do NOT use SQLAlchemy ORM models. Use raw SQL via `sqlalchemy.text()` wrapped in the backend interface. HA's schema is well-known and stable — we query it directly.

### Two-phase confirmation — the safety core
`safety/confirmation.py` manages confirmation tokens. Pattern:

```python
# Phase 1: preview (every mutating tool does this by default)
token = safety.create_token(action="config_apply", details={...})
return {"diff": "...", "token": token.id, "message": "Review and call again with confirm=true"}

# Phase 2: apply (only after LLM/user sends back the token)
safety.validate_token(token_id)  # raises if already used or invalid
# ... perform the mutation ...
safety.consume_token(token_id)
```

Tokens are in-memory (dict), single-use, no expiry. Staleness is caught by each tool's own context-match checks at apply time, so a time-based cap adds friction without matching value. This is intentionally simple — no persistence needed since MCP sessions are ephemeral.

### Tool descriptions are LLM-facing documentation
Every tool's `description` parameter in the MCP schema is critical. It must tell an LLM:
1. When to use this tool (and when NOT to)
2. What parameters mean, with examples
3. What the output looks like
4. Whether it's read-only or mutating

Study ha-mcp's tool descriptions for inspiration on what works well for LLM comprehension. Their entity search tool descriptions are particularly good.

## Patterns every new tool follows

- **Tool registry:** tools register themselves via `@registry.tool(name=..., description=..., params=...)` at import time. Never keep a hardcoded list.
- **Do not re-add `recipes_dir`/`scripts_dir`/`checks_dir`** to the config schema. The extensibility model was parked in v0.8.2.
- **Audit every confirmed mutation:** it appends a JSONL entry to `audit/operations.jsonl` via `append_audit_entry()`.
- **Backup is not optional.** Most users run this on production, and there is no staging HA. If your tool writes to the filesystem, DB or a dashboard, call `BackupManager` (`safety/backup.py`) first, inside the confirmation flow. No exceptions: a tool that skips backup is broken. The backup manifest is append-only, so never rewrite or delete it.
- **Safety tests are critical:** SQL injection, path traversal, token reuse.

The HA REST/WS endpoints and DB tables we depend on are inventoried in `docs/HA_COMPATIBILITY.md`; that list is authoritative. Note that `old_state_id` is a self-referential FK on `states`, which complicates bulk DELETE.

## Connection stability hierarchy — CRITICAL

See also `docs/HA_QUIRKS.md` for field-tested operational patterns. The short version:

**Prefer filesystem and DB over APIs. Always.**

When implementing a tool, ask: "Can I get this from a file or a DB query instead of an API call?" If yes, do that. APIs are a convenience layer that breaks between HA releases. Files and database tables are the ground truth.

Concrete rules:
- **Config state** → read the YAML file, don't call an API that reads the same file
- **Entity/device/area registry** → prefer `/config/.storage/core.entity_registry` (JSON file) as the primary source, REST API as fallback. The file is always available; the API requires HA to be running and responsive.
- **Dashboard config** → WebSocket for writes (required), but file read from `.storage/lovelace.*` for reads (faster, works when HA is slow)
- **Database state** → always direct DB query, never go through HA services for data retrieval
- **Service calls** → REST API is the only option here, and that's fine — service calls are Tier 2 stable
- **Config validation** → WebSocket (`config/check_config`) was the only option, and HA 2026.9.2 removed it (see `docs/HA_COMPATIBILITY.md` and the backlog)

When an API call fails (404, 500, timeout), attempt the Tier 1 fallback before reporting failure. Example: if `GET /api/config/entity_registry` times out, read and parse `/config/.storage/core.entity_registry` directly.

### HA version detection
At startup, the connection layer must:
1. Read HA version from `GET /api/config` (or parse `/config/.ha_version` file as fallback)
2. Read DB schema version from `schema_changes` table
3. Store both in `HaOpsContext` so tools can adjust behavior for known version differences
4. Log a warning if HA version is outside the supported window (current + 2 previous monthly releases)

## Code style

- Docstrings on public functions (Google style)
- No classes where a function will do — but use classes for stateful things (connections, backends)
- Error handling: catch specific exceptions, return structured error messages to MCP client (never raw tracebacks)

## HA compatibility window — keep it current (agent routine)

Two files declare which HA Core versions this server works against, and they must move together:

- `src/ha_ops_mcp/compat.py` — `BUILT_AGAINST_HA`, `BUILT_AGAINST_DB_SCHEMA`, `MIN_SUPPORTED_HA`,
  `MAX_TESTED_HA`. Consumed by the startup warning, `haops_system_info`, and `haops_tools_check`.
- `docs/HA_COMPATIBILITY.md` — the prose half, the verification-history table, and the **inventory
  of the HA API surface we depend on**. That inventory is the point: HA's breaking-change lists are
  overwhelmingly integration-level, so diff them against the list instead of reading them whole.

`tests/test_compat.py` asserts `BUILT_AGAINST_HA` satisfies its own window, so bumping one constant
without the other fails. **Also update the README and DOCS.md compatibility tables** — they're
user-facing and go stale silently.

**When to bump (do it automatically, no need to ask):** after `haops_tools_check` returns `all_pass`
on an HA version newer than `MAX_TESTED_HA`. That single tool call *is* the verification — 13
read-only groups covering every backend. Add a row to the history table, and to KNOWN_GOOD_ENV.md.

**When an HA release ships breaking changes**, check them against HA_COMPATIBILITY.md's surface list
before assuming impact, and record anything version-specific there (not in HA_QUIRKS.md, which is for
behaviour that holds across versions).

## Known-good environment baseline — keep it current (agent routine)

`docs/KNOWN_GOOD_ENV.md` holds a version snapshot of the **full client+server stack**
(ha-ops-mcp, HA Core, DB backend+schema, Claude Code, terminal app, macOS, Bun/Node) at which
the MCP connection is verified working end-to-end. It exists so that "it broke for no reason"
breakages are resolved by **diffing the live environment against the last baseline** instead of
guessing. This maintenance is the agent's job, not the user's — do not hand it back to them.

**When to append a new baseline row (do it automatically, no need to ask):**
- After cutting a release / tag (alongside `./scripts/sync-version.sh`), or
- After confirming a clean `haops_self_check` (all backends ok) following any environment change.

**How:** gather the versions (`haops_system_info`, `haops_self_check`, `claude --version`,
`sw_vers`, `bun --version`, `node --version`, `$TERM_PROGRAM_VERSION`, `git describe --tags`),
add a new dated row tied to the current git tag, and **keep the old rows** — the history is the diff.

**When the MCP connection breaks**, consult `docs/CONNECTIVITY_TROUBLESHOOTING.md` FIRST. The
recurring culprit is macOS **Local Network Privacy**: a terminal-app (iTerm/Terminal) update resets
the app's Local Network grant, killing all LAN MCP sockets while `curl` still works (Apple binaries
are exempt). Do not chase HA/Claude-Code updates or swap the MCP URL to an IP (breaks OAuth resource
matching — the URL must stay the mDNS hostname).

## What NOT to do

- Do NOT use SQLAlchemy ORM models or mapped classes — raw SQL via `text()` only
- Do NOT abstract away SQL dialect differences — the LLM writes dialect-appropriate SQL, the tool just reports which backend is active
- Do NOT build a web UI — this is a headless MCP server
- Do NOT depend on ha-mcp or any HA custom component
- Do NOT use PyYAML for writing YAML — it strips comments. Use ruamel.yaml.
- Do NOT store confirmation tokens in a database — in-memory dict is correct
- Do NOT implement device control tools (lights, switches, etc.) — that's out of scope. The generic `haops_service_call` covers it as an escape hatch.
- Do NOT call an API when you can read a file or query the DB directly — see "Connection stability hierarchy" above
- Do NOT assume the latest HA schema version — read `schema_changes` and adapt
- Do NOT hard-fail on API 404s — always attempt a Tier 1 fallback first
