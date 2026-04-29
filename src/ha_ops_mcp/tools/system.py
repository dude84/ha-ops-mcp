"""System tools — info, logs, reload, restart, backup."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ha_ops_mcp.connections.rest import RestClientError
from ha_ops_mcp.server import registry
from ha_ops_mcp.utils.logs import fetch_log_text

# Valid reload targets and their service calls
_RELOAD_TARGETS: dict[str, tuple[str, str]] = {
    "automations": ("automation", "reload"),
    "scripts": ("script", "reload"),
    "scenes": ("scene", "reload"),
    "groups": ("group", "reload"),
    "input_booleans": ("input_boolean", "reload"),
    "input_numbers": ("input_number", "reload"),
    "input_texts": ("input_text", "reload"),
    "input_selects": ("input_select", "reload"),
    "input_datetimes": ("input_datetime", "reload"),
    "shell_commands": ("shell_command", "reload"),
    "core": ("homeassistant", "reload_core_config"),
    "all": ("homeassistant", "reload_all"),
}

if TYPE_CHECKING:
    from ha_ops_mcp.server import HaOpsContext


async def _get_ha_version(ctx: HaOpsContext) -> str | None:
    """Get HA version, preferring filesystem, falling back to API."""
    # Tier 1: .ha_version file
    version_file = Path(ctx.config.filesystem.config_root) / ".HA_VERSION"
    try:
        return version_file.read_text().strip()
    except FileNotFoundError:
        pass

    # Tier 2: REST API
    try:
        config = await ctx.rest.get("/api/config")
        return config.get("version")  # type: ignore[no-any-return]
    except RestClientError:
        return None


@registry.tool(
    name="haops_system_info",
    description=(
        "System overview for the Home Assistant instance. "
        "Returns: HA version, database backend and schema version, "
        "entity/automation/integration counts, and config directory path. "
        "Read-only, no parameters. Good starting point for understanding the instance."
    ),
)
async def haops_system_info(ctx: HaOpsContext) -> dict[str, Any]:
    ha_version = await _get_ha_version(ctx)

    info: dict[str, Any] = {
        "ha_version": ha_version,
        "config_root": ctx.config.filesystem.config_root,
    }

    # DB info
    if ctx.db:
        try:
            health = await ctx.db.health()
            info["database"] = {
                "backend": health.backend,
                "version": health.version,
                "schema_version": health.schema_version,
            }
        except Exception:
            info["database"] = {"error": "Could not connect to database"}

    # Entity/automation/integration counts from API
    try:
        config = await ctx.rest.get("/api/config")
        info["location_name"] = config.get("location_name")
        info["unit_system"] = config.get("unit_system", {}).get("length")
        info["time_zone"] = config.get("time_zone")
    except RestClientError:
        pass

    try:
        states = await ctx.rest.get("/api/states")
        if isinstance(states, list):
            domains: dict[str, int] = {}
            for s in states:
                domain = s.get("entity_id", "").split(".")[0]
                domains[domain] = domains.get(domain, 0) + 1
            info["entity_count"] = len(states)
            info["domain_counts"] = dict(sorted(domains.items(), key=lambda x: -x[1]))
            info["automation_count"] = domains.get("automation", 0)
    except RestClientError:
        pass

    return info


@registry.tool(
    name="haops_self_check",
    description=(
        "Validate that ha-ops-mcp is correctly configured and can reach "
        "all backends. Tests: HA REST API (token validity), WebSocket "
        "connection, database connectivity, filesystem access to config "
        "root, and backup directory. Returns pass/fail for each check. "
        "Run this first to diagnose connection issues. "
        "Read-only, no parameters."
    ),
)
async def haops_self_check(ctx: HaOpsContext) -> dict[str, Any]:
    checks: dict[str, Any] = {}

    # 1. REST API / token
    try:
        config = await ctx.rest.get("/api/config")
        checks["rest_api"] = {
            "status": "ok",
            "ha_version": config.get("version"),
            "time_zone": config.get("time_zone"),
        }
    except Exception as e:
        checks["rest_api"] = {"status": "fail", "error": str(e)}

    # 2. WebSocket — test basic connectivity then dashboard admin access
    ws_url = ctx.config.ha.ws_url or ctx.config.ha.url
    ws_check: dict[str, Any] = {"url": ws_url}
    try:
        await ctx.ws.send_command("ping")
        ws_check["connected"] = True
    except Exception as e:
        ws_check["connected"] = False
        ws_check["error"] = str(e)
        ws_check["hint"] = (
            "WebSocket connection failed. Check: "
            "(1) Is HA running and reachable at the URL above? "
            "(2) Is the token valid (Settings → People → Security → "
            "Long-lived access tokens)? "
            "(3) HA Core logs for WS auth errors."
        )

    if ws_check.get("connected"):
        # Test dashboard listing — indicates whether dashboard tools work.
        # lovelace/dashboards/list requires authenticated access to lovelace.
        try:
            await ctx.ws.send_command("lovelace/dashboards/list")
            ws_check["dashboard_access"] = True
        except Exception as e:
            ws_check["dashboard_access"] = False
            ws_check["dashboard_error"] = str(e)
            ws_check["hint"] = (
                "WS ping succeeded but dashboard list failed — the "
                "connection works but admin-level commands don't. "
                "Check: (1) Does the token have admin access? "
                "(2) Is Lovelace in YAML mode (disables the WS "
                "dashboard API)? (3) HA Core logs for 'lovelace' "
                "errors around this timestamp."
            )

        ws_check["status"] = (
            "ok" if ws_check.get("dashboard_access") else "degraded"
        )
    else:
        ws_check["status"] = "fail"

    checks["websocket"] = ws_check

    # 3. Database
    if ctx.db is not None:
        try:
            health = await ctx.db.health()
            checks["database"] = {
                "status": "ok",
                "backend": health.backend,
                "version": health.version,
                "schema_version": health.schema_version,
            }
        except Exception as e:
            checks["database"] = {"status": "fail", "error": str(e)}
    else:
        checks["database"] = {
            "status": "skip",
            "reason": "No database URL configured",
        }

    # 4. Filesystem — config root
    config_root = Path(ctx.config.filesystem.config_root)
    if config_root.is_dir():
        checks["filesystem"] = {
            "status": "ok",
            "config_root": str(config_root),
            "writable": os.access(config_root, os.W_OK),
        }
    else:
        checks["filesystem"] = {
            "status": "fail",
            "error": f"Config root not found: {config_root}",
        }

    # 5. Backup directory
    backup_dir = Path(ctx.config.backup.dir)
    if backup_dir.is_dir():
        checks["backup_dir"] = {
            "status": "ok",
            "path": str(backup_dir),
            "writable": os.access(backup_dir, os.W_OK),
        }
    else:
        try:
            backup_dir.mkdir(parents=True, exist_ok=True)
            checks["backup_dir"] = {
                "status": "ok",
                "path": str(backup_dir),
                "created": True,
            }
        except Exception as e:
            checks["backup_dir"] = {
                "status": "fail",
                "error": str(e),
            }

    # Summary
    all_ok = all(
        c.get("status") in ("ok", "skip")
        for c in checks.values()
    )
    checks["overall"] = "ok" if all_ok else "issues_found"

    # Version at the end — after the summary loop that iterates checks
    # values expecting dicts; this string would break .get("status").
    from ha_ops_mcp import __version__
    checks["ha_ops_version"] = __version__

    return checks


@registry.tool(
    name="haops_system_logs",
    description=(
        "Retrieve and filter the Home Assistant error log. "
        "Parameters (all optional): level (string: 'error', 'warning', 'info'), "
        "integration (string: filter by integration name in log lines), "
        "lines (int: max lines to return, default 100), "
        "pattern (string: regex pattern to filter log lines). "
        "Returns filtered log entries as a list of strings."
    ),
    params={
        "level": {"type": "string", "description": "Filter by log level (error/warning/info)"},
        "integration": {"type": "string", "description": "Filter by integration name"},
        "lines": {"type": "integer", "description": "Max lines to return", "default": 100},
        "pattern": {"type": "string", "description": "Regex pattern to filter lines"},
    },
)
async def haops_system_logs(
    ctx: HaOpsContext,
    level: str | None = None,
    integration: str | None = None,
    lines: int = 100,
    pattern: str | None = None,
) -> dict[str, Any]:
    # Tier fallback (filesystem → Supervisor → REST) lives in utils/logs so
    # tools/service.py can share it for error-path excerpts without
    # duplicating the retry logic.
    fetched = await fetch_log_text(ctx)
    if fetched is None:
        return {
            "error": "Log unavailable via filesystem, Supervisor, or REST"
        }
    log_text, source = fetched

    log_lines = log_text.splitlines()

    # Filter by level
    if level:
        level_upper = level.upper()
        log_lines = [line for line in log_lines if level_upper in line.upper()]

    # Filter by integration
    if integration:
        log_lines = [line for line in log_lines if integration.lower() in line.lower()]

    # Filter by regex pattern
    if pattern:
        try:
            compiled = re.compile(pattern, re.I)
            log_lines = [line for line in log_lines if compiled.search(line)]
        except re.error as e:
            return {"error": f"Invalid regex pattern: {e}"}

    # Limit
    log_lines = log_lines[-lines:]

    return {
        "lines": log_lines,
        "count": len(log_lines),
        "total_log_lines": len(log_text.splitlines()),
        "source": source,
    }


@registry.tool(
    name="haops_system_reload",
    description=(
        "Targeted reload of specific HA domains. Avoids a full restart. "
        f"Valid targets: {', '.join(_RELOAD_TARGETS.keys())}. "
        "Parameters: target (string, required), "
        "verify (list of entity_id strings, optional — after the reload "
        "completes, checks whether each listed entity exists in /api/states "
        "and reports back. Saves a follow-up haops_entity_state call when "
        "you want to confirm a just-created script/automation registered). "
        "This calls the appropriate domain.reload service."
    ),
    params={
        "target": {
            "type": "string",
            "description": "What to reload",
        },
        "verify": {
            "type": "array",
            "description": (
                "Entity IDs to check after reload. Returns which exist "
                "and which don't."
            ),
            "items": {"type": "string"},
        },
    },
)
async def haops_system_reload(
    ctx: HaOpsContext,
    target: str,
    verify: list[str] | None = None,
) -> dict[str, Any]:
    if target not in _RELOAD_TARGETS:
        return {
            "error": f"Unknown target '{target}'",
            "valid_targets": list(_RELOAD_TARGETS.keys()),
        }

    domain, service = _RELOAD_TARGETS[target]

    try:
        await ctx.rest.post(f"/api/services/{domain}/{service}")
    except RestClientError as e:
        return {"error": f"Reload failed: {e}"}

    await ctx.audit.log(
        tool="system_reload",
        details={"target": target, "domain": domain, "service": service},
    )

    result: dict[str, Any] = {
        "success": True,
        "target": target,
        "service": f"{domain}.{service}",
    }

    if verify:
        # Brief pause — HA needs a moment after reload to register
        # entities. 1 second is enough for scripts/automations; if an
        # entity still doesn't show up it's genuinely missing.
        import asyncio
        await asyncio.sleep(1)
        verified: dict[str, bool] = {}
        for eid in verify:
            try:
                await ctx.rest.get(f"/api/states/{eid}")
                verified[eid] = True
            except Exception:
                verified[eid] = False
        result["verified"] = verified
        missing = [e for e, ok in verified.items() if not ok]
        if missing:
            result["verify_warning"] = (
                f"{len(missing)} entity(s) not found after reload: "
                f"{', '.join(missing)}. Check the entity_id spelling "
                "or the source config (automations.yaml / scripts.yaml)."
            )

    return result


@registry.tool(
    name="haops_system_restart",
    description=(
        "Restart Home Assistant. LAST RESORT — prefer "
        "haops_system_reload for individual domain reloads (automations, "
        "scripts, scenes, core, etc.) which take effect without downtime. "
        "Only use restart when reload is insufficient (e.g. core config "
        "changes, Python dependency updates). Two-phase: call without "
        "confirm to get a confirmation token, then call with confirm=true "
        "and the token. "
        "Parameters: confirm (bool, default false), "
        "token (string, if confirming). "
        "On apply: returns status='initiated' (not an error) when the HA "
        "API drops / 502 / 504s mid-request, because that's the expected "
        "signal that HA is tearing itself down to restart. Use "
        "haops_self_check to poll until the API is back up. "
        "WARNING: This restarts the entire HA instance."
    ),
    params={
        "confirm": {
            "type": "boolean", "description": "Execute restart",
            "default": False,
        },
        "token": {
            "type": "string",
            "description": "Confirmation token from preview step",
        },
    },
)
async def haops_system_restart(
    ctx: HaOpsContext,
    confirm: bool = False,
    token: str | None = None,
) -> dict[str, Any]:
    if not confirm:
        tk = ctx.safety.create_token(
            action="system_restart",
            details={"action": "restart"},
        )
        return {
            "warning": "This will restart Home Assistant. All automations "
            "and connections will be temporarily interrupted.",
            "token": tk.id,
            "message": "Call again with confirm=true and this token to restart.",
        }

    if token is None:
        return {"error": "confirm=true requires a token"}

    try:
        ctx.safety.validate_token(token)
    except Exception as e:
        return {"error": str(e)}

    # HA's REST API goes away *because* HA is restarting — so 502/504 or a
    # connection drop on the restart call IS the success signal, not a
    # failure. Only treat genuinely-unrelated errors (401/403/500 with a
    # response body) as real failures.
    import aiohttp

    restart_initiated = False
    try:
        await ctx.rest.post("/api/services/homeassistant/restart")
    except RestClientError as e:
        if e.status in (502, 503, 504):
            restart_initiated = True
        else:
            return {"error": f"Restart failed: {e}"}
    except (aiohttp.ClientConnectionError, TimeoutError):
        # aiohttp raises a ClientConnectionError / ServerDisconnectedError
        # (both subclasses) when the socket drops mid-request, which is
        # exactly what HA's supervisor does as it tears down the Core
        # container. asyncio.TimeoutError surfaces the same thing when
        # aiohttp's total-timeout fires before the response lands.
        restart_initiated = True

    ctx.safety.consume_token(token)

    await ctx.audit.log(
        tool="system_restart",
        details={"action": "restart"},
        token_id=token,
    )

    if restart_initiated:
        return {
            "status": "initiated",
            "message": (
                "Restart initiated. HA API is unreachable as expected during "
                "restart (typical duration 30-120s). Use haops_self_check to "
                "monitor until it's back up."
            ),
        }

    return {
        "success": True,
        "message": "Home Assistant is restarting. It may take a minute to come back online.",
    }


@registry.tool(
    name="haops_system_backup",
    description=(
        "Trigger a full Home Assistant backup. Prefers the Supervisor "
        "API (/backups/new/full) — fast, non-blocking, returns the new "
        "backup's slug immediately. Falls back to the Core REST "
        "backup.create service when Supervisor isn't reachable. "
        "Parameters: name (string, optional — backup name; supervisor "
        "auto-generates one if omitted), password (string, optional — "
        "encrypts the archive), compressed (bool, default true). "
        "Returns immediately; the backup itself runs in the background."
    ),
    params={
        "name": {
            "type": "string",
            "description": "Backup name (optional)",
        },
        "password": {
            "type": "string",
            "description": "Encrypt the archive (optional)",
        },
        "compressed": {
            "type": "boolean",
            "description": "Compress the archive (default true)",
            "default": True,
        },
    },
)
async def haops_system_backup(
    ctx: HaOpsContext,
    name: str | None = None,
    password: str | None = None,
    compressed: bool = True,
) -> dict[str, Any]:
    # Try Supervisor first — it's the right endpoint and the only one that
    # returns a slug for follow-up status checks.
    sup_body: dict[str, Any] = {"compressed": compressed}
    if name:
        sup_body["name"] = name
    if password:
        sup_body["password"] = password

    from ha_ops_mcp.tools.addon import _supervisor_post
    sup_result = await _supervisor_post(ctx, "/backups/new/full", sup_body)
    if sup_result is not None and "error" not in sup_result:
        await ctx.audit.log(
            tool="system_backup",
            details={"name": name, "via": "supervisor", "compressed": compressed},
            success=True,
        )
        return {
            "success": True,
            "via": "supervisor",
            "slug": sup_result.get("slug"),
            "message": (
                f"Backup initiated (supervisor). Slug: {sup_result.get('slug', 'unknown')}. "
                "Track in HA UI → Settings → System → Backups."
            ),
        }

    # Supervisor unavailable or rejected — fall back to Core REST service.
    # Some HA versions renamed the service; try a couple of known names.
    core_body: dict[str, Any] = {}
    if name:
        core_body["name"] = name
    if password:
        core_body["password"] = password

    last_error: str | None = None
    for service in ("backup/create", "hassio/backup_full"):
        try:
            await ctx.rest.post(f"/api/services/{service}", core_body)
            await ctx.audit.log(
                tool="system_backup",
                details={"name": name, "via": f"core/{service}"},
                success=True,
            )
            return {
                "success": True,
                "via": f"core/{service}",
                "message": (
                    "Backup initiated via Core service. "
                    "Track in HA UI → Settings → System → Backups."
                ),
            }
        except RestClientError as e:
            last_error = str(e)
            continue

    sup_error = sup_result.get("error") if isinstance(sup_result, dict) else "unavailable"
    await ctx.audit.log(
        tool="system_backup",
        details={"name": name, "supervisor_error": sup_error, "core_error": last_error},
        success=False,
        error=last_error or sup_error,
    )
    return {
        "success": False,
        "error": "Backup creation failed via both Supervisor and Core REST",
        "supervisor_error": sup_error,
        "core_error": last_error,
    }
