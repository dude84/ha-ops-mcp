"""MCP server setup, tool registry, and HaOpsContext."""

from __future__ import annotations

import contextlib
import functools
import inspect
import logging
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from ha_ops_mcp.config import HaOpsConfig, load_config
from ha_ops_mcp.connections.database import DatabaseBackend, create_backend
from ha_ops_mcp.connections.rest import RestClient
from ha_ops_mcp.connections.websocket import WebSocketClient
from ha_ops_mcp.safety.audit import AuditLog
from ha_ops_mcp.safety.backup import BackupManager
from ha_ops_mcp.safety.classification import classify
from ha_ops_mcp.safety.confirmation import SafetyManager
from ha_ops_mcp.safety.path_guard import PathGuard
from ha_ops_mcp.safety.rollback import RollbackManager

logger = logging.getLogger(__name__)

ToolHandler = Callable[..., Coroutine[Any, Any, Any]]


@dataclass
class ToolSchema:
    name: str
    description: str
    params: dict[str, Any]


class ToolRegistry:
    """Dynamic tool registry. Built-in tools register via decorator."""

    def __init__(self) -> None:
        self._tools: dict[str, tuple[ToolHandler, ToolSchema]] = {}

    def register(self, name: str, handler: ToolHandler, schema: ToolSchema) -> None:
        self._tools[name] = (handler, schema)

    def tool(
        self, name: str, description: str, params: dict[str, Any] | None = None
    ) -> Callable[[ToolHandler], ToolHandler]:
        """Decorator for registering built-in tools."""
        def decorator(fn: ToolHandler) -> ToolHandler:
            self.register(name, fn, ToolSchema(name, description, params or {}))
            return fn
        return decorator

    def all_tools(self) -> list[tuple[str, ToolHandler, ToolSchema]]:
        return [(name, handler, schema) for name, (handler, schema) in self._tools.items()]

    def get(self, name: str) -> tuple[ToolHandler, ToolSchema] | None:
        return self._tools.get(name)

    def __len__(self) -> int:
        return len(self._tools)


# Global registry — tools register at import time
registry = ToolRegistry()


@dataclass
class HaOpsContext:
    """Shared context injected into all tool handlers."""

    config: HaOpsConfig
    rest: RestClient
    ws: WebSocketClient
    db: DatabaseBackend | None
    safety: SafetyManager
    rollback: RollbackManager
    backup: BackupManager
    audit: AuditLog
    path_guard: PathGuard
    auth_provider: Any | None = None  # HaOpsOAuthProvider when auth enabled
    ha_version: str | None = None
    db_schema_version: int | None = None
    # Per-request cache for the reference index — set by tool/route
    # entrypoints, consumed by downstream helpers via
    # `ha_ops_mcp.refindex.get_or_build_index(ctx)`.
    request_index: Any = None


def _auto_detect_db_url(config_root: Path) -> str:
    """Auto-detect the recorder database URL from HA config.

    Checks:
    1. configuration.yaml for recorder.db_url
    2. Resolves !secret references from secrets.yaml
    3. Falls back to default SQLite path
    """
    default_sqlite = f"sqlite:///{config_root / 'home-assistant_v2.db'}"

    config_file = config_root / "configuration.yaml"
    if not config_file.exists():
        logger.info("No configuration.yaml found, using default SQLite: %s", default_sqlite)
        return default_sqlite

    try:
        from ruamel.yaml import YAML
        yaml = YAML()
        with open(config_file) as f:
            ha_config = yaml.load(f)

        if ha_config and "recorder" in ha_config:
            recorder = ha_config["recorder"]
            if isinstance(recorder, dict) and "db_url" in recorder:
                raw_value = recorder["db_url"]
                # ruamel.yaml represents !secret as a tagged scalar
                # Check if it's a tagged value (has .tag attribute)
                if hasattr(raw_value, "tag") and raw_value.tag == "!secret":
                    secret_key = str(raw_value)
                    db_url = _resolve_secret(config_root, secret_key)
                elif isinstance(raw_value, str) and raw_value.startswith("!secret"):
                    secret_key = raw_value.split(None, 1)[1] if " " in raw_value else ""
                    db_url = _resolve_secret(config_root, secret_key)
                else:
                    db_url = str(raw_value)
                if db_url:
                    logger.info("Auto-detected recorder DB URL from configuration.yaml")
                    return db_url
    except Exception as e:
        logger.warning("Failed to parse configuration.yaml for DB URL: %s", e)

    logger.info("No recorder db_url found, using default SQLite: %s", default_sqlite)
    return default_sqlite


def _resolve_secret(config_root: Path, key: str) -> str:
    """Resolve a secret key from secrets.yaml."""
    secrets_file = config_root / "secrets.yaml"
    if not secrets_file.exists():
        return ""
    try:
        from ruamel.yaml import YAML
        yaml = YAML()
        with open(secrets_file) as f:
            secrets = yaml.load(f)
        return str(secrets.get(key, "")) if secrets else ""
    except Exception:
        return ""


def create_context(config: HaOpsConfig) -> HaOpsContext:
    """Create the shared context from config. Connections are not yet open."""
    token = config.ha.resolve_token()

    rest = RestClient(config.ha.url, token)
    ws_url = config.ha.ws_url or config.ha.url
    ws = WebSocketClient(ws_url, token)

    db: DatabaseBackend | None = None
    db_url = config.database.url
    if not db_url and config.database.auto_detect:
        db_url = _auto_detect_db_url(Path(config.filesystem.config_root))
    if db_url:
        db = create_backend(db_url)

    safety = SafetyManager()
    rollback = RollbackManager()
    backup = BackupManager(
        Path(config.backup.dir),
        max_age_days=config.backup.max_age_days,
        max_per_type=config.backup.max_per_type,
    )
    # Audit dir is its own config option. Default lives INSIDE backup_dir
    # so we don't pollute HA's filesystem with stray directories. The
    # original fallback used `.parent / "audit"` which placed the audit
    # log as a SIBLING of backup_dir — e.g. /backup/audit/ instead of
    # /backup/ha-ops-mcp/audit/. Fixed in v0.22.0.
    audit_dir_str = config.audit.dir or str(Path(config.backup.dir) / "audit")
    audit = AuditLog(Path(audit_dir_str).resolve())

    # Legacy detection: the pre-v0.22.0 fallback placed the audit log at
    # <backup_dir>/../audit (a sibling, not inside backup_dir). Warn if
    # that stray directory still has data so the admin knows to move it.
    _legacy_audit = (Path(config.backup.dir).parent / "audit").resolve()
    if (
        _legacy_audit != audit._dir
        and _legacy_audit.is_dir()
        and any(_legacy_audit.iterdir())
    ):
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "Legacy audit directory detected at %s (outside backup_dir). "
            "The audit log now lives at %s. Move operations.jsonl from "
            "the old location to keep your history, or delete the stray "
            "directory.",
            _legacy_audit,
            audit._dir,
        )

    path_guard = PathGuard(Path(config.filesystem.config_root))

    return HaOpsContext(
        config=config,
        rest=rest,
        ws=ws,
        db=db,
        safety=safety,
        rollback=rollback,
        backup=backup,
        audit=audit,
        path_guard=path_guard,
    )


def _migrate_legacy_oauth(
    new_dir: Path, legacy: Path = Path("/data/oauth.json")
) -> None:
    """Copy a pre-v0.40.0 /data/oauth.json into the survival data dir once.

    The OAuth store used to live in /data, which the Supervisor wipes when the
    addon is uninstalled (or installed under a new slug). It now defaults under
    backup_dir — a mapped /backup volume that survives. If the new location has
    no store yet but the legacy one exists, migrate it so MCP clients keep
    their registrations/tokens across the move (no re-auth).
    """
    import logging as _logging

    target = new_dir / "oauth.json"
    if target.exists() or not legacy.is_file():
        return
    try:
        new_dir.mkdir(parents=True, exist_ok=True)
        target.write_bytes(legacy.read_bytes())
        _logging.getLogger(__name__).info(
            "Migrated legacy OAuth store %s -> %s", legacy, target
        )
    except OSError as e:
        _logging.getLogger(__name__).warning(
            "Could not migrate legacy OAuth store from %s: %s", legacy, e
        )


def create_server(config_path: Path | None = None) -> tuple[FastMCP, HaOpsContext]:
    """Create the MCP server and context.

    Returns:
        Tuple of (FastMCP server, HaOpsContext).
    """
    config = load_config(config_path)
    ctx = create_context(config)

    @asynccontextmanager
    async def lifespan(app: FastMCP) -> AsyncIterator[None]:
        """Initialize connections on startup, clean up on shutdown."""
        # Enter REST client session
        await ctx.rest.__aenter__()
        # Enter WebSocket client (best-effort — HA may not be reachable yet)
        try:
            await ctx.ws.__aenter__()
            logger.info("WebSocket connected successfully at startup")
        except Exception as e:
            logger.warning(
                "WebSocket connection failed at startup: %s — will retry on first use",
                e,
            )
        try:
            yield
        finally:
            # Cleanup
            with contextlib.suppress(Exception):
                await ctx.ws.__aexit__(None, None, None)
            await ctx.rest.__aexit__(None, None, None)
            if ctx.db:
                await ctx.db.close()

    # OAuth auth — opt-in, disabled by default. When enabled on SSE/HTTP
    # transports the SDK wires Bearer token validation + OAuth endpoints.
    auth_provider = None
    auth_settings = None

    if config.auth.enabled and config.server.transport != "stdio":
        from mcp.server.auth.settings import (
            AuthSettings,
            ClientRegistrationOptions,
            RevocationOptions,
        )

        from ha_ops_mcp.auth.provider import HaOpsOAuthProvider

        # OAuth store dir. Empty (the default) derives <backup_dir>/auth — a
        # mapped /backup volume that survives addon uninstall/slug-change,
        # unlike the old /data default. Migrate a legacy /data/oauth.json into
        # the new home once so updates stay seamless.
        auth_data_dir = Path(
            config.auth.data_dir or str(Path(config.backup.dir) / "auth")
        ).resolve()
        _migrate_legacy_oauth(auth_data_dir)
        auth_provider = HaOpsOAuthProvider(
            data_dir=auth_data_dir,
            access_token_ttl=config.auth.access_token_ttl,
            refresh_token_ttl=config.auth.refresh_token_ttl,
        )
        from pydantic import AnyHttpUrl

        # `config.server.host` may be a bind address (`::` for IPv6 dual-stack,
        # `0.0.0.0` for IPv4 any-interface, or empty) that isn't a valid URL
        # host. Substitute a reachable hostname for the issuer fallback so
        # AnyHttpUrl doesn't reject `http://:::8901`.
        host = config.server.host
        if host in ("::", "0.0.0.0", ""):
            host = "homeassistant.local"
        issuer = config.auth.issuer_url or f"http://{host}:{config.server.port}"
        server_url = AnyHttpUrl(issuer)

        # The MCP SDK enforces HTTPS per OAuth 2.0 spec, but HA addons
        # run on a local Docker network where TLS isn't available.
        # Patch the validation so HTTP works for local deployments.
        if not issuer.startswith("https://"):
            import mcp.server.auth.routes as _auth_routes

            def _allow_http_issuer(url: Any) -> None:  # noqa: ARG001
                """Bypass MCP SDK's HTTPS-only validate_issuer_url for local nets."""

            _auth_routes.validate_issuer_url = _allow_http_issuer
            logger.warning(
                "OAuth issuer URL is not HTTPS — acceptable for local "
                "network, not suitable for public exposure"
            )

        auth_settings = AuthSettings(
            issuer_url=server_url,
            resource_server_url=server_url,
            client_registration_options=ClientRegistrationOptions(enabled=True),
            revocation_options=RevocationOptions(enabled=True),
        )
        ctx.auth_provider = auth_provider
        logger.info("OAuth authentication enabled")

    mcp = FastMCP(
        "ha-ops-mcp",
        instructions=(
            "Home Assistant operations server. Provides database queries, "
            "config file management, entity auditing, and system health tools. "
            "All mutating operations require two-phase confirmation."
        ),
        host=config.server.host,
        port=config.server.port,
        lifespan=lifespan,
        auth_server_provider=auth_provider,
        auth=auth_settings,
    )

    # Import tools to trigger registration
    import ha_ops_mcp.tools.addon  # noqa: F401
    import ha_ops_mcp.tools.auth  # noqa: F401
    import ha_ops_mcp.tools.backup  # noqa: F401
    import ha_ops_mcp.tools.batch  # noqa: F401
    import ha_ops_mcp.tools.config  # noqa: F401
    import ha_ops_mcp.tools.dashboard  # noqa: F401
    import ha_ops_mcp.tools.db  # noqa: F401
    import ha_ops_mcp.tools.debugger  # noqa: F401
    import ha_ops_mcp.tools.device  # noqa: F401
    import ha_ops_mcp.tools.entity  # noqa: F401
    import ha_ops_mcp.tools.ergonomics  # noqa: F401
    import ha_ops_mcp.tools.helper  # noqa: F401
    import ha_ops_mcp.tools.refs  # noqa: F401
    import ha_ops_mcp.tools.registry  # noqa: F401
    import ha_ops_mcp.tools.rollback  # noqa: F401
    import ha_ops_mcp.tools.service  # noqa: F401
    import ha_ops_mcp.tools.shell  # noqa: F401
    import ha_ops_mcp.tools.system  # noqa: F401
    import ha_ops_mcp.tools.tools_check  # noqa: F401
    import ha_ops_mcp.tools.ws  # noqa: F401
    import ha_ops_mcp.tools.zigbee  # noqa: F401

    # Register all tools with FastMCP
    for name, handler, schema in registry.all_tools():
        _register_tool(mcp, name, schema.description, handler, ctx)

    # Mount the read-only sidebar UI + /api/ui/* endpoints
    from ha_ops_mcp.ui.routes import register_ui_routes
    register_ui_routes(mcp, ctx)

    logger.info("ha-ops-mcp server created with %d tools", len(registry))
    return mcp, ctx


def _read_summary(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Compact a read tool's call args for the activity log.

    Keep scalars (entity_id, path, limit, sql first line) and drop bulky
    payloads — the activity feed wants "what was looked at", not full data.
    """
    out: dict[str, Any] = {}
    for k, v in kwargs.items():
        if isinstance(v, (str, int, float, bool)) or v is None:
            s = v
            if isinstance(v, str) and len(v) > 200:
                s = v[:200] + f"… ({len(v)} chars)"
            out[k] = s
        elif isinstance(v, (list, tuple)):
            out[k] = f"[{len(v)} items]"
        elif isinstance(v, dict):
            out[k] = f"{{{len(v)} keys}}"
    return out


def _register_tool(
    mcp: FastMCP,
    name: str,
    description: str,
    handler: ToolHandler,
    ctx: HaOpsContext,
) -> None:
    """Register a tool handler with FastMCP, injecting context.

    FastMCP introspects the wrapper's signature to build the tool schema.
    We must expose the original handler's parameters (minus ctx) so that
    FastMCP generates the correct parameter schema for MCP clients.
    """
    # Build a wrapper with the same signature as handler, minus 'ctx'
    orig_sig = inspect.signature(handler)
    params = [p for pname, p in orig_sig.parameters.items() if pname != "ctx"]
    new_sig = orig_sig.replace(parameters=params)

    async def tool_wrapper(**kwargs: Any) -> Any:
        result = await handler(ctx, **kwargs)
        # Read-only calls don't self-log; the central wrapper records them
        # to the activity stream so the Timeline can show a full feed.
        # Mutating/destructive tools call ctx.audit.log() themselves — skip
        # them here to avoid double-logging.
        if ctx.config.audit.log_reads:
            bare = name[len("haops_"):] if name.startswith("haops_") else name
            op_class, area = classify(bare, kwargs)
            if op_class == "read":
                try:
                    await ctx.audit.log_activity(
                        tool=bare,
                        details=_read_summary(kwargs),
                        op_class=op_class,
                        area=area,
                    )
                except Exception:  # noqa: BLE001 - logging must never break a tool
                    logger.debug("activity log failed for %s", bare, exc_info=True)
        return result

    # Set signature BEFORE FastMCP's @mcp.tool decorator introspects it
    functools.update_wrapper(tool_wrapper, handler)
    tool_wrapper.__signature__ = new_sig  # type: ignore[attr-defined]

    mcp.tool(name=name, description=description)(tool_wrapper)
