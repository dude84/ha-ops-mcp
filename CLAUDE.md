# ha-ops-mcp — Implementation Guide

## What this project is

An MCP (Model Context Protocol) server that provides operations tools for Home Assistant power users. It covers database management, configuration file editing, dashboard CRUD, entity hygiene, and system health — the "ops" surface that no existing HA MCP server addresses.

**Read these documents before writing any code:**
1. This file — architecture, patterns, and implementation rules
2. `docs/HA_QUIRKS.md` — field-tested HA quirks and operational patterns

## Key architectural decisions

### Async everywhere
The entire codebase is async (asyncio). All connection clients, tool handlers, and I/O are async. The MCP server framework (FastMCP or `mcp` SDK) runs the event loop.

### Connection layer is a singleton context
`connections/` provides three clients: REST, WebSocket, Database. They are initialized once at server startup from `config.yaml` and injected into tool handlers. Do NOT create connections per-tool-call.

```python
# Pattern: server.py initializes connections, tools receive them
class HaOpsContext:
    rest: RestClient
    ws: WebSocketClient  
    db: DatabaseBackend
    config: HaOpsConfig
    safety: SafetyManager
```

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

## Implementation order

Phases 1 and 2 are shipped; see `CHANGELOG.md` for release history. The remaining implementation notes below describe the patterns every new tool should follow:

### Step 1: Skeleton server with dynamic tool registration
- `server.py`: Initialize FastMCP/mcp server, load config, create context
- **Tool registry pattern** — tools register via decorator or explicit call, NOT hardcoded list. Every built-in tool registers itself via `@registry.tool(name=..., description=..., params=...)` and the server discovers them at import time.
- `config.py`: Load `config.yaml` with env var overrides, validate. (Note: the original spec called for `recipes_dir`/`scripts_dir`/`checks_dir` in the schema; the extensibility model was **parked** in v0.8.2. Do not re-add those fields.)
- `__main__.py`: Entry point that runs the server
- **Audit log from day one** — every confirmed mutation appends a JSONL entry to `audit/operations.jsonl`. Implement this in Step 1, not later. It's one function (`append_audit_entry()`) and it makes every subsequent tool auditable for free.
- Get `ha-ops-mcp` running as a valid MCP server with zero tools (just the handshake)

```python
# Tool registry pattern — server.py
class ToolRegistry:
    """Dynamic tool registry. Built-in tools register at import time."""
    
    def __init__(self):
        self._tools: dict[str, ToolHandler] = {}
    
    def register(self, name: str, handler: ToolHandler, schema: ToolSchema) -> None:
        self._tools[name] = (handler, schema)
    
    def tool(self, name: str, description: str, params: dict):
        """Decorator for built-in tools."""
        def decorator(fn):
            self.register(name, fn, ToolSchema(name, description, params))
            return fn
        return decorator
    
    def all_tools(self) -> list[tuple[str, ToolHandler, ToolSchema]]:
        return [(name, *val) for name, val in self._tools.items()]
```

### Step 2: Connection layer
- `connections/rest.py`: aiohttp session, auth header, methods for GET/POST to HA API
- `connections/websocket.py`: websockets client, auth flow, command/response pattern
- `connections/database.py`: Abstract backend + SQLite implementation first (easiest to test without a real HA instance), then MariaDB, then PostgreSQL

### OAuth authentication (enabled by default, v0.27.0+)
`auth/provider.py` implements the MCP SDK's `OAuthAuthorizationServerProvider`. When `auth.enabled: true` in config AND the transport is SSE or streamable-http, the provider is passed to the FastMCP constructor. The SDK then automatically mounts OAuth endpoints and enforces Bearer tokens on all MCP tool calls. OAuth is enabled by default since v0.27.0; set `auth.enabled: false` to disable. The issuer URL is auto-derived from HA's `internal_url` via the Supervisor API; override with `auth.issuer_url` if auto-detection picks the wrong hostname. HTTP issuer URLs are accepted for local networks (the MCP SDK's HTTPS requirement is patched at startup with a logged warning).

The provider is a single-user/admin server — it auto-approves all authorization requests (no consent UI). Client registrations and tokens are persisted to `<data_dir>/oauth.json`. The store is loaded into memory on startup, flushed to disk on every write, and cleaned of expired entries on load.

`haops_auth_status` and `haops_auth_clear` provide visibility and management. Token values are masked in status output (first 8 chars only).

### Step 3: Safety layer (includes backup — this is Phase 1, not Phase 2)
- `safety/confirmation.py`: Token create/validate/consume
- `safety/path_guard.py`: Resolve path, assert under config_root
- `safety/backup.py`: Backup manager — the most important safety component

**Backup is not optional.** Most users run this on production. There is no staging HA. The backup manager is a shared internal service used by all mutating tools:

```python
class BackupManager:
    """Manages automatic backups before destructive operations.
    
    Every mutating tool calls this BEFORE writing. This is not a suggestion —
    tools that skip backup are broken tools.
    """
    
    def __init__(self, backup_dir: Path, config: BackupConfig):
        self.backup_dir = backup_dir
        self.manifest_path = backup_dir / "manifest.jsonl"
        # Create subdirs on init: config/, dashboards/, entities/, db/
    
    async def backup_file(self, source_path: Path, operation: str) -> BackupEntry:
        """Copy a config/YAML file to backups/config/<name>.<timestamp>.bak.
        Returns the backup entry (used in audit log and revert)."""
    
    async def backup_dashboard(self, dashboard_id: str, config: dict, operation: str) -> BackupEntry:
        """Snapshot a dashboard JSON to backups/dashboards/."""
    
    async def backup_entities(self, entities: list[dict], operation: str) -> BackupEntry:
        """Save entity registry entries to backups/entities/ as JSONL."""
    
    async def backup_db_rows(self, table: str, rows: list[dict], operation: str) -> BackupEntry:
        """Dump rows as INSERT statements to backups/db/.
        For small operations only — large ops should use haops_system_backup."""
    
    async def list_backups(self, type_filter: str = "all", since: datetime = None) -> list[BackupEntry]:
        """Read manifest and return matching entries."""
    
    async def _append_manifest(self, entry: BackupEntry) -> None:
        """Append to manifest.jsonl — never rewrite, never delete."""
```

**The rule for tools:** if your tool writes to filesystem, DB, or dashboard — call `BackupManager` first. No exceptions. Wire it into the confirmation flow:

```python
# Pattern: config_apply tool
async def haops_config_apply(ctx, token_id, backup=True):
    ctx.safety.validate_token(token_id)
    token = ctx.safety.get_token(token_id)
    
    if backup:
        await ctx.backup.backup_file(token.details["path"], operation="config_apply")
    
    # ... write the file ...
    
    ctx.safety.consume_token(token_id)
    await ctx.audit.log(tool="config_apply", details=token.details, backup=backup)
```

### Step 4: Tools — one group at a time
Implement in this order (each group should be a complete, tested unit before moving on):
1. `tools/db.py` — start with `haops_db_query` and `haops_db_health`
2. `tools/config.py` — `haops_config_read`, `haops_config_patch`, `haops_config_create`, `haops_config_apply`
3. `tools/entity.py` — `haops_entity_list`, `haops_entity_audit`
4. `tools/system.py` — `haops_system_info`, `haops_system_logs`
5. `tools/dashboard.py` — full CRUD
6. `tools/service.py` — generic service call

### Step 5: Tests
- `conftest.py`: Fixtures that create in-memory SQLite with HA schema, mock HA API responses
- One test file per tool group
- Safety tests are critical — test SQL injection, path traversal, token expiry, token reuse

## HA API reference (what you need to know)

### REST API patterns
```
GET  /api/states                          → all entity states
GET  /api/states/<entity_id>              → single entity state
POST /api/services/<domain>/<service>     → call a service
GET  /api/config/entity_registry          → entity registry (all entities)
GET  /api/config/device_registry          → device registry
GET  /api/config/area_registry            → area registry  
GET  /api/error_log                       → error log (plain text)
GET  /api/config                          → HA config (units, location, etc)
DELETE /api/config/entity_registry/<id>   → remove entity
POST /api/config/entity_registry/<id>     → update entity (e.g., disable)
```

### WebSocket API patterns
```json
// Auth
{"type": "auth", "access_token": "..."}

// Config check
{"id": 1, "type": "config/check_config"}

// Dashboard get
{"id": 2, "type": "lovelace/config", "url_path": "new-dashboard"}

// Dashboard save
{"id": 3, "type": "lovelace/config/save", "url_path": "new-dashboard", "config": {...}}

// List dashboards
{"id": 4, "type": "lovelace/dashboards/list"}
```

### HA database schema (core tables)
```sql
-- states: current + historical entity states
states (state_id, entity_id, state, attributes, last_changed_ts, last_updated_ts, old_state_id)
-- Note: old_state_id is a self-referential FK — complicates bulk DELETE

-- events: event log  
events (event_id, event_type, event_data, time_fired_ts)

-- statistics: long-term statistics (hourly)
statistics (id, metadata_id, start_ts, mean, min, max, last_reset_ts, state, sum)

-- statistics_short_term: 5-minute statistics
statistics_short_term (id, metadata_id, start_ts, mean, min, max, last_reset_ts, state, sum)

-- statistics_meta: maps statistic_id to entity_id
statistics_meta (id, statistic_id, source, unit_of_measurement, has_mean, has_sum, name)

-- recorder_runs: tracks HA recorder sessions
recorder_runs (run_id, start, end, created)

-- schema_changes: tracks DB schema migrations
schema_changes (id, schema_version, changed)
```

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
- **Config validation** → WebSocket (`config/check_config`) is the only option, acceptable Tier 2

When an API call fails (404, 500, timeout), attempt the Tier 1 fallback before reporting failure. Example: if `GET /api/config/entity_registry` times out, read and parse `/config/.storage/core.entity_registry` directly.

### Graceful degradation pattern
```python
async def get_entity_registry(ctx: HaOpsContext) -> list[dict]:
    """Get entity registry, preferring filesystem, falling back to API."""
    # Tier 1: direct file read (always available if filesystem access exists)
    try:
        registry = await ctx.filesystem.read_json(".storage/core.entity_registry")
        return registry["data"]["entities"]
    except (FileNotFoundError, KeyError):
        pass
    
    # Tier 2: REST API fallback
    try:
        return await ctx.rest.get("/api/config/entity_registry")
    except ApiError as e:
        raise HaOpsError(f"Entity registry unavailable via both filesystem and API: {e}")
```

### HA version detection
At startup, the connection layer must:
1. Read HA version from `GET /api/config` (or parse `/config/.ha_version` file as fallback)
2. Read DB schema version from `schema_changes` table
3. Store both in `HaOpsContext` so tools can adjust behavior for known version differences
4. Log a warning if HA version is outside the supported window (current + 2 previous monthly releases)

## Code style

- Python 3.11+, type hints everywhere
- Ruff for linting (config in pyproject.toml)
- Mypy strict mode
- Docstrings on public functions (Google style)
- No classes where a function will do — but use classes for stateful things (connections, backends)
- Error handling: catch specific exceptions, return structured error messages to MCP client (never raw tracebacks)

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
