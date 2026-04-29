"""Database tools — query, health, execute, purge, statistics."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ha_ops_mcp.safety.rollback import UndoEntry, UndoType
from ha_ops_mcp.server import registry

if TYPE_CHECKING:
    from ha_ops_mcp.server import HaOpsContext


def _no_db() -> dict[str, Any]:
    return {"error": "Database not configured. Set database.url in config.yaml."}


@registry.tool(
    name="haops_db_query",
    description=(
        "Execute a read-only SQL query against the Home Assistant recorder database. "
        "The query runs in a READ ONLY transaction — writes are impossible. "
        "Returns columns, rows (as dicts), row count, execution time, backend type "
        "(sqlite/mariadb/postgresql) so you can adjust SQL dialect, and "
        "session_timezone — the effective TZ the backend uses for FROM_UNIXTIME()/"
        "UNIX_TIMESTAMP() so you don't have to guess whether to add/subtract an offset. "
        "Use haops_db_health first to understand the schema. "
        "Parameters: sql (string, required), limit (int, default 100). "
        "Example: sql='SELECT entity_id, COUNT(*) as cnt "
        "FROM states GROUP BY entity_id ORDER BY cnt DESC LIMIT 10'"
    ),
    params={
        "sql": {"type": "string", "description": "SQL query (read-only enforced)"},
        "limit": {"type": "integer", "description": "Max rows", "default": 100},
    },
)
async def haops_db_query(
    ctx: HaOpsContext, sql: str, limit: int = 100
) -> dict[str, Any]:
    if ctx.db is None:
        return _no_db()

    result = await ctx.db.query(
        sql, limit=min(limit, ctx.config.safety.max_query_rows)
    )

    response: dict[str, Any] = {
        "backend": ctx.db.backend_type,
        "session_timezone": await ctx.db.session_timezone(),
        "columns": result.columns,
        "rows": result.rows,
        "row_count": result.row_count,
        "execution_time_ms": result.execution_time_ms,
    }
    if result.truncated:
        response["truncated"] = True
        response["message"] = f"Results truncated at {limit} rows"

    return response


@registry.tool(
    name="haops_db_health",
    description=(
        "Database health dashboard for the Home Assistant recorder. "
        "Returns: backend type, version, schema version, table sizes "
        "(rows + disk bytes where available), and backend-specific metrics "
        "(InnoDB buffer pool hit rate for MariaDB, cache hit ratio for "
        "PostgreSQL, DB file size + integrity for SQLite). "
        "Read-only, no parameters."
    ),
)
async def haops_db_health(ctx: HaOpsContext) -> dict[str, Any]:
    if ctx.db is None:
        return _no_db()

    health = await ctx.db.health()

    return {
        "backend": health.backend,
        "version": health.version,
        "schema_version": health.schema_version,
        "tables": [
            {
                "name": t.name,
                "row_count": t.row_count,
                "size_bytes": t.size_bytes,
            }
            for t in health.table_sizes
        ],
        **health.extras,
    }


@registry.tool(
    name="haops_db_execute",
    description=(
        "Execute a write SQL statement (INSERT, UPDATE, DELETE, ALTER) "
        "against the HA recorder database. Two-phase operation: "
        "1) Call without confirm to get EXPLAIN plan + confirmation token. "
        "2) Call with confirm=true and the token to execute. "
        "For purging old recorder data, prefer haops_db_purge — it uses "
        "HA's recorder.purge service which handles the self-referential FK "
        "in the states table correctly. Use db_execute for non-purge writes. "
        "This tool has no SQL safety net beyond two-phase confirmation — "
        "the caller is responsible for understanding the statement. "
        "Parameters: sql (string, required), confirm (bool, default false), "
        "token (string, required if confirm=true)."
    ),
    params={
        "sql": {"type": "string", "description": "SQL statement to execute"},
        "confirm": {"type": "boolean", "description": "Execute the statement", "default": False},
        "token": {"type": "string", "description": "Confirmation token from preview step"},
    },
)
async def haops_db_execute(
    ctx: HaOpsContext,
    sql: str,
    confirm: bool = False,
    token: str | None = None,
) -> dict[str, Any]:
    if ctx.db is None:
        return _no_db()

    if not confirm:
        # Phase 1: preview
        try:
            plan = await ctx.db.explain(sql)
        except Exception as e:
            plan = [f"EXPLAIN failed: {e}"]

        tk = ctx.safety.create_token(
            action="db_execute",
            details={"sql": sql},
        )

        return {
            "explain": plan,
            "token": tk.id,
            "message": "Review the EXPLAIN plan. Call again with "
            "confirm=true and this token to execute.",
        }

    # Phase 2: execute
    if token is None:
        return {"error": "confirm=true requires a token from the preview step"}

    try:
        token_data = ctx.safety.validate_token(token)
    except Exception as e:
        return {"error": str(e)}

    if token_data.details.get("sql") != sql:
        return {"error": "SQL does not match the token. Re-run the preview."}

    txn = ctx.rollback.begin("db_execute")
    txn.savepoint(
        name="db_execute",
        undo=UndoEntry(
            type=UndoType.DB_ROWS,
            description=f"Undo: {sql[:80]}",
            data={"sql": sql, "note": "Manual reversal may be needed"},
        ),
    )

    try:
        affected = await ctx.db.execute(sql)
    except Exception as e:
        return {"error": f"Execution failed: {e}"}

    ctx.safety.consume_token(token)
    ctx.rollback.commit(txn.id)

    await ctx.audit.log(
        tool="db_execute",
        details={"sql": sql, "affected_rows": affected},
        token_id=token,
    )

    return {
        "success": True,
        "affected_rows": affected,
        "backend": ctx.db.backend_type,
        "transaction_id": txn.id,
    }


@registry.tool(
    name="haops_db_purge",
    description=(
        "Managed database purge using HA's recorder.purge service. "
        "This is safer than raw SQL — the recorder handles the "
        "self-referential FK in the states table correctly. "
        "Parameters: keep_days (int, required), "
        "entity_filter (list of entity_ids, optional — purge only these), "
        "dry_run (bool, default true — preview what would be purged). "
        "In dry_run mode, reports estimated row counts. "
        "When executed, calls recorder.purge via the REST API."
    ),
    params={
        "keep_days": {
            "type": "integer",
            "description": "Keep history newer than N days",
        },
        "entity_filter": {
            "type": "array",
            "description": "Only purge these entity_ids (optional)",
        },
        "dry_run": {
            "type": "boolean",
            "description": "Preview only, don't purge",
            "default": True,
        },
    },
)
async def haops_db_purge(
    ctx: HaOpsContext,
    keep_days: int,
    entity_filter: list[str] | None = None,
    dry_run: bool = True,
) -> dict[str, Any]:
    if ctx.db is None:
        return _no_db()

    import time

    cutoff_ts = time.time() - (keep_days * 86400)

    # Estimate affected rows
    if entity_filter:
        placeholders = ", ".join(f":e{i}" for i in range(len(entity_filter)))
        count_sql = (
            f"SELECT COUNT(*) FROM states "
            f"WHERE last_updated_ts < :cutoff "
            f"AND entity_id IN ({placeholders})"
        )
        params: dict[str, Any] = {"cutoff": cutoff_ts}
        params.update({f"e{i}": eid for i, eid in enumerate(entity_filter)})
    else:
        count_sql = (
            "SELECT COUNT(*) FROM states "
            "WHERE last_updated_ts < :cutoff"
        )
        params = {"cutoff": cutoff_ts}

    try:
        result = await ctx.db.query(count_sql, params=params, limit=1)
        estimated_rows = result.rows[0].get("COUNT(*)", 0) if result.rows else 0
    except Exception:
        estimated_rows = "unknown"

    if dry_run:
        response: dict[str, Any] = {
            "dry_run": True,
            "keep_days": keep_days,
            "estimated_states_rows": estimated_rows,
        }
        if entity_filter:
            response["entity_filter"] = entity_filter
        return response

    # Execute purge via HA service call
    service_data: dict[str, Any] = {"keep_days": keep_days}
    if entity_filter:
        # Use purge_entities for targeted purge
        try:
            await ctx.rest.post(
                "/api/services/recorder/purge_entities",
                {"entity_id": entity_filter, "keep_days": keep_days},
            )
        except Exception as e:
            return {"error": f"recorder.purge_entities failed: {e}"}
    else:
        try:
            await ctx.rest.post(
                "/api/services/recorder/purge",
                service_data,
            )
        except Exception as e:
            return {"error": f"recorder.purge failed: {e}"}

    await ctx.audit.log(
        tool="db_purge",
        details={
            "keep_days": keep_days,
            "entity_filter": entity_filter,
            "estimated_rows": estimated_rows,
        },
    )

    return {
        "success": True,
        "keep_days": keep_days,
        "estimated_rows_purged": estimated_rows,
        "message": "Purge initiated. The recorder processes this in the background.",
    }


@registry.tool(
    name="haops_db_statistics",
    description=(
        "Statistics table management for the HA recorder. "
        "Sub-commands: "
        "'list' — list statistics_meta entries (filterable by domain/entity). "
        "'orphans' — find statistics with no corresponding entity in the registry. "
        "'stale' — find entities with no new statistics in N days. "
        "'info' — detailed stats for a specific statistic_id. "
        "Parameters: command (string, required: list/orphans/stale/info), "
        "domain (string, optional), entity_id (string, optional), "
        "stale_days (int, default 30)."
    ),
    params={
        "command": {
            "type": "string",
            "description": "Sub-command: list, orphans, stale, info",
        },
        "domain": {"type": "string", "description": "Filter by domain"},
        "entity_id": {
            "type": "string",
            "description": "Specific entity/statistic ID (for 'info')",
        },
        "stale_days": {
            "type": "integer",
            "description": "Days threshold for 'stale'",
            "default": 30,
        },
    },
)
async def haops_db_statistics(
    ctx: HaOpsContext,
    command: str,
    domain: str | None = None,
    entity_id: str | None = None,
    stale_days: int = 30,
) -> dict[str, Any]:
    if ctx.db is None:
        return _no_db()

    if command == "list":
        sql = "SELECT id, statistic_id, source, unit_of_measurement, name FROM statistics_meta"
        conditions: list[str] = []
        params: dict[str, Any] = {}
        if domain:
            conditions.append("statistic_id LIKE :domain_prefix")
            params["domain_prefix"] = f"{domain}.%"
        if entity_id:
            conditions.append("statistic_id = :entity_id")
            params["entity_id"] = entity_id
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY statistic_id"

        result = await ctx.db.query(sql, params=params or None, limit=1000)
        return {"statistics": result.rows, "count": result.row_count}

    if command == "orphans":
        # Find statistics_meta entries that don't match any entity in states
        sql = (
            "SELECT sm.id, sm.statistic_id, sm.source, sm.name "
            "FROM statistics_meta sm "
            "LEFT JOIN (SELECT DISTINCT entity_id FROM states) s "
            "ON sm.statistic_id = s.entity_id "
            "WHERE s.entity_id IS NULL "
            "ORDER BY sm.statistic_id"
        )
        result = await ctx.db.query(sql, limit=1000)
        return {"orphans": result.rows, "count": result.row_count}

    if command == "stale":
        import time

        cutoff_ts = time.time() - (stale_days * 86400)
        sql = (
            "SELECT sm.statistic_id, sm.name, MAX(st.start_ts) as last_stat "
            "FROM statistics_meta sm "
            "LEFT JOIN statistics st ON sm.id = st.metadata_id "
            "GROUP BY sm.id, sm.statistic_id, sm.name "
            "HAVING MAX(st.start_ts) < :cutoff OR MAX(st.start_ts) IS NULL "
            "ORDER BY last_stat"
        )
        result = await ctx.db.query(sql, params={"cutoff": cutoff_ts}, limit=1000)
        return {"stale": result.rows, "count": result.row_count}

    if command == "info":
        if not entity_id:
            return {"error": "'info' command requires entity_id parameter"}

        meta_sql = (
            "SELECT * FROM statistics_meta "
            "WHERE statistic_id = :entity_id"
        )
        meta = await ctx.db.query(meta_sql, params={"entity_id": entity_id}, limit=1)
        if not meta.rows:
            return {"error": f"No statistics found for '{entity_id}'"}

        meta_id = meta.rows[0]["id"]

        # LTS count and range
        lts_sql = (
            "SELECT COUNT(*) as count, MIN(start_ts) as first, "
            "MAX(start_ts) as last FROM statistics "
            "WHERE metadata_id = :meta_id"
        )
        lts = await ctx.db.query(lts_sql, params={"meta_id": meta_id}, limit=1)

        # Short-term count and range
        st_sql = (
            "SELECT COUNT(*) as count, MIN(start_ts) as first, "
            "MAX(start_ts) as last FROM statistics_short_term "
            "WHERE metadata_id = :meta_id"
        )
        short_term = await ctx.db.query(st_sql, params={"meta_id": meta_id}, limit=1)

        return {
            "statistic_id": entity_id,
            "meta": meta.rows[0],
            "long_term": lts.rows[0] if lts.rows else {},
            "short_term": short_term.rows[0] if short_term.rows else {},
        }

    return {"error": f"Unknown command '{command}'. Use: list, orphans, stale, info"}
