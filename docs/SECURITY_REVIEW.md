# Security & Architecture Review (2026-04-17)

Pre-release review of ha-ops-mcp from security, code quality, and
enterprise architecture perspectives. Findings are categorized by
priority; items promoted to the backlog are marked with a link.

---

## 1. Security Findings

### 1.1 No Authentication on MCP Transport

**Severity: CRITICAL** | **Backlog: yes**

The MCP server exposes all 58 tools with zero authentication on the
MCP transport layer. The UI routes (routes.py) check Ingress/Bearer
tokens, but tool handler calls go through FastMCP's native transport
with no auth middleware.

In stdio mode this is acceptable (local process). In SSE/streamable-http
mode (the addon default), port 8901 is exposed on the container network.
Anyone on the Docker bridge network or with port-forwarded access can
call any tool — including `haops_exec_shell`, `haops_db_execute`,
`haops_system_restart`, and `haops_config_apply`.

The HA Ingress proxy provides some protection for browser-based access,
but MCP clients connect directly to the SSE endpoint, not through
Ingress.

**Status (v0.26.0):** Optional OAuth 2.0 implemented. Set
`auth.enabled: true` (or `auth_enabled: true` in addon config) to
require OAuth tokens on SSE/HTTP transports. Disabled by default for
backwards compatibility. stdio transport is never authenticated.

### 1.2 SQL Injection in db_purge and db_statistics

**Severity: MEDIUM** (see note)

`tools/db.py` constructs SQL with f-string interpolation of user-supplied
values:

- `haops_db_purge` (lines 241-251): entity_filter values interpolated
  directly into SQL via `", ".join(f"'{e}'" for e in entity_filter)`.
- `haops_db_statistics` (lines 349-354, 382, 393-414): domain,
  entity_id, cutoff_ts, and meta_id interpolated via f-strings.

**Context note:** This is an admin tool whose purpose is to modify
anything on the HA instance. The SQL injection risk is not through
direct MCP tool calls (the caller already has full SQL access via
`haops_db_execute`), but through the sidebar UI panel — if a
dashboard-injected XSS payload could reach these endpoints, it could
bypass the SQL guard entirely via parameter smuggling.

**Action:** Use parameterized queries via `sqlalchemy.text()` bind
parameters. The database backend already supports `params: dict` — just
not used in these two tools. Low effort, eliminates the class of bug.

### 1.3 Shell Execution with Bypassable Guard

**Severity: HIGH** | **Backlog: yes (removal)**

`haops_exec_shell` accepts `guard=false` to bypass all pattern blocking.
The guard blocklist itself is easily circumvented:

- `rm -rf /` is blocked, but `find / -delete` is not
- `dd if=` is blocked, but `cat /dev/zero > /dev/sda` is not
- `python3 -c "import shutil; shutil.rmtree('/')"` bypasses everything
- Base64 encoding: `echo cm0gLXJmIC8= | base64 -d | sh`

This is inherent to denylist approaches. The guard provides false
confidence without real protection. Decision: remove entirely rather
than maintain theater.

### 1.4 SQL Guard is Theater

**Severity: MEDIUM** | **Backlog: yes (removal)**

The SQL guard (sql_guard.py) blocks 2 patterns (`DROP DATABASE`,
`TRUNCATE`) and warns on 2 more. It does NOT protect against:

- `DELETE FROM states WHERE 1=1` (passes the warning regex because
  WHERE is present)
- `UPDATE schema_changes SET schema_version = 0`
- `INSERT INTO` anything
- `ALTER TABLE`, `CREATE TABLE`, `CREATE INDEX` (unguarded)

The guard is a denylist on a tool (`haops_db_execute`) that explicitly
exists to run arbitrary write SQL. Same false-confidence problem as the
shell guard. Decision: remove rather than maintain.

### 1.5 Timing-Safe Token Comparison Missing

**Severity: LOW**

`routes.py:61`: `provided == expected` — standard string comparison
is vulnerable to timing attacks. Replace with `hmac.compare_digest()`.

### 1.6 eval() in Config Loader

**Severity: LOW** (controlled input)

`config.py:156`: `eval(target_type)` where target_type comes from
dataclass annotations (always `"bool"` or `"int"`). Not exploitable
in practice but gets flagged in audits. Replace with a lookup dict.

---

## 2. Sensitive Data in the Repository

### 2.1 Personal Email in repository.yaml

`repository.yaml:3` contains maintainer email. Standard for open-source
but requires a conscious decision. Consider GitHub noreply address.

### 2.2 _gaps/ Directory

Properly gitignored, won't appear in public repo. Verify with
`git ls-files _gaps/` that nothing was committed historically.

### 2.3 .gitignore Gaps

Missing entries to add before public release:

- `.env` / `.env.*`
- `secrets.yaml`
- `*.pem`, `*.key`, `*.cert`
- `/audit/`, `/backup/`
- `config.*.yaml` (only `config.local.yaml` is covered)

---

## 3. Code Quality

### 3.1 routes.py is Overcomplicated

960 lines mixing HTTP handlers, audit rendering, and diff recomputation.
The rendering logic (`_render_audit_entry`, `_recompute_audit_diff`,
`_summarise_audit_entry`, `_audit_details_excerpt` — lines 586-959) is
pure data transformation that should be in `utils/audit_render.py`.
Would make routes ~300 lines and rendering independently testable.

### 3.2 Entity List Loads Full State on Every Call

`haops_entity_list` calls both `_get_entity_registry()` and
`_get_states()` even for `count_only=True`. On large instances (1000+
entities), this fetches all states from the REST API just to count.
The `count_only` path should short-circuit.

### 3.3 No Connection Pooling / Timeout Config

- `RestClient`: hardcoded 30s timeout, not configurable.
- `WebSocketClient`: no connection timeout, no ping/pong keepalive,
  no max reconnect attempts. HA hangs → WS blocks forever.
- `DatabaseBackend`: no pool size, pool timeout, or pool recycle.

### 3.4 Config Loader Silently Drops Unknown Keys

`_build_dataclass` ignores unknown keys with no warning. A typo in
config.yaml (`trasnport` instead of `transport`) silently uses the
default. Should log a warning for unknown keys.

---

## 4. Enterprise / Open-Source Release Gaps

### 4.1 Missing LICENSE File

`pyproject.toml` declares Apache-2.0 but no LICENSE file in repo root.
Many organizations require the full license text.

### 4.2 Missing SECURITY.md

Need: vulnerability reporting process (email, not public issues), threat
model documentation, explicit statement that MCP transport has no auth
and relies on network isolation.

### 4.3 No Rate Limiting

No rate limiting on tool calls or UI endpoints. A misconfigured LLM loop
could hammer HA's API, flood the DB, or fill the audit log.

### 4.4 No RBAC or Tool Scoping

All tools available to all clients. No way to restrict a client to
read-only. At minimum: document the threat model and recommend scoped
HA access tokens.

### 4.5 No Dependency Pinning

`pyproject.toml` uses `>=` with no upper bounds and no lock file.
Breaking dependency changes will break the addon.

### 4.6 No Health Check Endpoint

No `/healthz` or `/readyz` for Docker/Kubernetes health probes.
Supervisor can't distinguish "running but broken" from "healthy."

### 4.7 Audit Log Has No Integrity Protection

Append-only by convention, no cryptographic integrity. Filesystem access
allows silent tampering. For compliance: consider a hash chain per entry.
At minimum: document the limitation.

### 4.8 Confirmation Tokens Have No Expiry

Documented as intentional. Tokens are in-memory and die on restart, so
practical risk is low. A 1-hour TTL would be reasonable defense-in-depth.

---

## 5. What's Done Well

- Two-phase confirmation pattern — right call for production infra
- Tier 1/Tier 2 fallback (filesystem before API) — genuine resilience
- Path guard for config operations — clean and correct
- Secrets redaction in config_read — thoughtful
- Rollback/savepoint model — well-designed for ephemeral sessions
- Test coverage at 487 tests — strong
- YAML comment preservation via ruamel.yaml — production-aware
- Audit trail from day one — good practice
- Tool descriptions — excellent, detailed, with examples
