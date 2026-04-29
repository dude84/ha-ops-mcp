"""Database backends for Home Assistant recorder database.

Provides an abstract DatabaseBackend and concrete implementations for
SQLite, MariaDB, and PostgreSQL. Uses raw SQL via sqlalchemy.text() — no ORM.
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

logger = logging.getLogger(__name__)


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    execution_time_ms: float
    truncated: bool = False


@dataclass
class TableSize:
    name: str
    row_count: int
    size_bytes: int | None = None


@dataclass
class HealthInfo:
    backend: str
    version: str
    schema_version: int | None
    table_sizes: list[TableSize]
    extras: dict[str, Any]


class DatabaseBackend(ABC):
    """Abstract interface for HA database access."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine
        self._session_timezone: str | None = None

    @property
    @abstractmethod
    def backend_type(self) -> str:
        """Return backend identifier: 'sqlite', 'mariadb', or 'postgresql'."""

    async def session_timezone(self) -> str:
        """Return the DB session's effective timezone, cached after first call.

        Surfaces how the backend interprets `FROM_UNIXTIME()` output and
        `UNIX_TIMESTAMP('literal')` input so callers can reason about
        timestamps without guess-and-verify.
        """
        if self._session_timezone is None:
            try:
                self._session_timezone = await self._fetch_session_timezone()
            except Exception as e:
                logger.debug("session_timezone fetch failed: %s", e)
                self._session_timezone = "unknown"
        return self._session_timezone

    @abstractmethod
    async def _fetch_session_timezone(self) -> str:
        """Backend-specific session-timezone lookup."""

    async def query(
        self, sql: str, params: dict[str, Any] | None = None, limit: int = 10000
    ) -> QueryResult:
        """Execute a read-only query and return results."""
        start = time.monotonic()
        async with self._engine.connect() as conn:
            await self._set_read_only(conn)
            result = await conn.execute(text(sql), params or {})
            columns = list(result.keys())
            rows = [
                dict(zip(columns, row, strict=False))
                for row in result.fetchmany(limit + 1)
            ]
            truncated = len(rows) > limit
            if truncated:
                rows = rows[:limit]
        elapsed = (time.monotonic() - start) * 1000
        return QueryResult(
            columns=columns,
            rows=rows,
            row_count=len(rows),
            execution_time_ms=round(elapsed, 2),
            truncated=truncated,
        )

    async def execute(self, sql: str, params: dict[str, Any] | None = None) -> int:
        """Execute a write query and return affected row count."""
        async with self._engine.begin() as conn:
            result = await conn.execute(text(sql), params or {})
            return int(result.rowcount or 0)

    async def explain(self, sql: str, params: dict[str, Any] | None = None) -> list[str]:
        """Run EXPLAIN on a query and return the plan lines."""
        explain_sql = self._explain_prefix() + sql
        async with self._engine.connect() as conn:
            result = await conn.execute(text(explain_sql), params or {})
            return [str(row) for row in result.fetchall()]

    async def health(self) -> HealthInfo:
        """Return health/diagnostic information for this backend."""
        version = await self._get_version()
        schema_version = await self._get_schema_version()
        table_sizes = await self.table_sizes()
        extras = await self._health_extras()
        return HealthInfo(
            backend=self.backend_type,
            version=version,
            schema_version=schema_version,
            table_sizes=table_sizes,
            extras=extras,
        )

    async def table_sizes(self) -> list[TableSize]:
        """Return row counts (and sizes where available) for HA tables."""
        tables = [
            "states", "events", "statistics", "statistics_short_term",
            "statistics_meta", "recorder_runs", "schema_changes",
        ]
        sizes = []
        async with self._engine.connect() as conn:
            for table in tables:
                try:
                    result = await conn.execute(text(f"SELECT COUNT(*) FROM {table}"))  # noqa: S608
                    row = result.fetchone()
                    count = row[0] if row else 0
                    sizes.append(TableSize(name=table, row_count=count))
                except Exception:
                    logger.debug("Table %s not found, skipping", table)
        return sizes

    async def _get_schema_version(self) -> int | None:
        """Read the schema version from the HA recorder.

        Tries multiple strategies because HA's schema tracking has changed
        over time and may differ between SQLite/MariaDB/PostgreSQL.
        """
        candidate_queries = [
            # Standard HA recorder — schema_changes table (most installations)
            "SELECT schema_version FROM schema_changes "
            "ORDER BY change_id DESC LIMIT 1",
            # Some versions use 'id' as primary key instead of 'change_id'
            "SELECT schema_version FROM schema_changes "
            "ORDER BY id DESC LIMIT 1",
            # Fallback: MAX() — works regardless of column naming
            "SELECT MAX(schema_version) FROM schema_changes",
        ]

        for sql in candidate_queries:
            try:
                async with self._engine.connect() as conn:
                    result = await conn.execute(text(sql))
                    row = result.fetchone()
                    if row and row[0] is not None:
                        return int(row[0])
            except Exception as e:
                logger.debug("Schema query failed (%s): %s", sql[:50], e)
                continue

        return None

    @abstractmethod
    async def _set_read_only(self, conn: Any) -> None:
        """Set the connection to read-only mode."""

    @abstractmethod
    async def _get_version(self) -> str:
        """Get the database server version."""

    @abstractmethod
    def _explain_prefix(self) -> str:
        """Return the EXPLAIN prefix for this backend."""

    async def _health_extras(self) -> dict[str, Any]:
        """Backend-specific health metrics. Override in subclasses."""
        return {}

    async def close(self) -> None:
        """Dispose of the engine."""
        await self._engine.dispose()


class SqliteBackend(DatabaseBackend):
    @property
    def backend_type(self) -> str:
        return "sqlite"

    async def _set_read_only(self, conn: Any) -> None:
        await conn.execute(text("PRAGMA query_only = ON"))

    async def _get_version(self) -> str:
        async with self._engine.connect() as conn:
            result = await conn.execute(text("SELECT sqlite_version()"))
            row = result.fetchone()
            return f"SQLite {row[0]}" if row else "SQLite unknown"

    def _explain_prefix(self) -> str:
        return "EXPLAIN QUERY PLAN "

    async def _fetch_session_timezone(self) -> str:
        return "naive (SQLite stores whatever HA wrote — typically UTC epoch seconds)"

    async def _health_extras(self) -> dict[str, Any]:
        extras: dict[str, Any] = {}
        async with self._engine.connect() as conn:
            result = await conn.execute(text("PRAGMA page_count"))
            row = result.fetchone()
            if row:
                page_count = row[0]
                result2 = await conn.execute(text("PRAGMA page_size"))
                row2 = result2.fetchone()
                page_size = row2[0] if row2 else 4096
                extras["db_size_bytes"] = page_count * page_size

            result = await conn.execute(text("PRAGMA integrity_check(1)"))
            row = result.fetchone()
            extras["integrity"] = row[0] if row else "unknown"
        return extras


class MariaDbBackend(DatabaseBackend):
    @property
    def backend_type(self) -> str:
        return "mariadb"

    async def _set_read_only(self, conn: Any) -> None:
        await conn.execute(text("SET SESSION TRANSACTION READ ONLY"))

    async def _get_version(self) -> str:
        async with self._engine.connect() as conn:
            result = await conn.execute(text("SELECT VERSION()"))
            row = result.fetchone()
            return f"MariaDB {row[0]}" if row else "MariaDB unknown"

    def _explain_prefix(self) -> str:
        return "EXPLAIN "

    async def _fetch_session_timezone(self) -> str:
        async with self._engine.connect() as conn:
            result = await conn.execute(text("SELECT @@session.time_zone"))
            row = result.fetchone()
            session_tz = row[0] if row else "unknown"
            if session_tz == "SYSTEM":
                result = await conn.execute(text("SELECT @@system_time_zone"))
                row = result.fetchone()
                system_tz = row[0] if row else "unknown"
                return f"SYSTEM ({system_tz})"
            return str(session_tz)

    async def _health_extras(self) -> dict[str, Any]:
        extras: dict[str, Any] = {}
        async with self._engine.connect() as conn:
            try:
                result = await conn.execute(
                    text("SHOW GLOBAL STATUS LIKE 'Innodb_buffer_pool_read_requests'")
                )
                row = result.fetchone()
                read_requests = int(row[1]) if row else 0

                result = await conn.execute(
                    text("SHOW GLOBAL STATUS LIKE 'Innodb_buffer_pool_reads'")
                )
                row = result.fetchone()
                disk_reads = int(row[1]) if row else 0

                if read_requests > 0:
                    extras["buffer_pool_hit_rate"] = round(
                        (1 - disk_reads / read_requests) * 100, 2
                    )
            except Exception:
                logger.debug("Could not read InnoDB stats")
        return extras

    async def table_sizes(self) -> list[TableSize]:
        """MariaDB can also report data_length."""
        sizes = []
        async with self._engine.connect() as conn:
            result = await conn.execute(text(
                "SELECT table_name, table_rows, data_length "
                "FROM information_schema.tables "
                "WHERE table_schema = DATABASE() "
                "AND table_name IN ("
                "'states','events','statistics','statistics_short_term',"
                "'statistics_meta','recorder_runs','schema_changes')"
            ))
            for row in result.fetchall():
                sizes.append(TableSize(
                    name=row[0], row_count=row[1] or 0, size_bytes=row[2]
                ))
        return sizes


class PostgresBackend(DatabaseBackend):
    @property
    def backend_type(self) -> str:
        return "postgresql"

    async def _set_read_only(self, conn: Any) -> None:
        await conn.execute(text("SET TRANSACTION READ ONLY"))

    async def _get_version(self) -> str:
        async with self._engine.connect() as conn:
            result = await conn.execute(text("SELECT version()"))
            row = result.fetchone()
            return row[0] if row else "PostgreSQL unknown"

    def _explain_prefix(self) -> str:
        return "EXPLAIN "

    async def _fetch_session_timezone(self) -> str:
        async with self._engine.connect() as conn:
            result = await conn.execute(text("SHOW TIME ZONE"))
            row = result.fetchone()
            return str(row[0]) if row else "unknown"

    async def _health_extras(self) -> dict[str, Any]:
        extras: dict[str, Any] = {}
        async with self._engine.connect() as conn:
            try:
                result = await conn.execute(text(
                    "SELECT "
                    "sum(blks_hit) AS hits, "
                    "sum(blks_read) AS reads "
                    "FROM pg_stat_database"
                ))
                row = result.fetchone()
                if row and row[0]:
                    total = row[0] + row[1]
                    if total > 0:
                        extras["cache_hit_ratio"] = round(row[0] / total * 100, 2)
            except Exception:
                logger.debug("Could not read PG stats")
        return extras

    async def table_sizes(self) -> list[TableSize]:
        """PostgreSQL can report pg_total_relation_size."""
        sizes = []
        tables = [
            "states", "events", "statistics", "statistics_short_term",
            "statistics_meta", "recorder_runs", "schema_changes",
        ]
        async with self._engine.connect() as conn:
            for table in tables:
                try:
                    result = await conn.execute(text(
                        f"SELECT COUNT(*), pg_total_relation_size('{table}')"  # noqa: S608
                    ))
                    row = result.fetchone()
                    if row:
                        sizes.append(TableSize(name=table, row_count=row[0], size_bytes=row[1]))
                except Exception:
                    logger.debug("Table %s not found, skipping", table)
        return sizes


def _normalize_db_url(url: str) -> str:
    """Convert a HA-style DB URL to an async SQLAlchemy URL."""
    if url.startswith("sqlite:///"):
        return url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
    if url.startswith("mysql://") or url.startswith("mysql+pymysql://"):
        # Normalize to aiomysql
        url = url.split("://", 1)[1]
        return f"mysql+aiomysql://{url}"
    if url.startswith("postgresql://") or url.startswith("postgres://"):
        url = url.split("://", 1)[1]
        return f"postgresql+asyncpg://{url}"
    return url


def create_backend(db_url: str) -> DatabaseBackend:
    """Factory: create the appropriate backend from a database URL.

    Args:
        db_url: Database URL in HA recorder format (sqlite:///..., mysql://..., etc.)

    Returns:
        A DatabaseBackend instance with an async engine.
    """
    async_url = _normalize_db_url(db_url)
    engine = create_async_engine(async_url, echo=False)

    if "sqlite" in async_url:
        return SqliteBackend(engine)
    if "mysql" in async_url or "mariadb" in async_url:
        return MariaDbBackend(engine)
    if "postgresql" in async_url or "asyncpg" in async_url:
        return PostgresBackend(engine)

    raise ValueError(f"Unsupported database URL scheme: {db_url}")
