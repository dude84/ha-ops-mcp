"""Passive integration test — validate each tool group works against real HA.

Unlike haops_self_check (which validates configuration/connectivity),
haops_tools_check exercises each tool group with real operations to
confirm that the haops_* tools can actually do their job.

All operations are READ-ONLY. Nothing is mutated.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ha_ops_mcp.server import registry

if TYPE_CHECKING:
    from ha_ops_mcp.server import HaOpsContext


async def _check_rest_api(ctx: HaOpsContext) -> dict[str, Any]:
    """Exercise REST endpoints used by entity/system/service tools."""
    checks: dict[str, Any] = {}

    try:
        config = await ctx.rest.get("/api/config")
        checks["api_config"] = {
            "ok": True,
            "ha_version": config.get("version"),
        }
    except Exception as e:
        checks["api_config"] = {"ok": False, "error": str(e)[:200]}

    try:
        states = await ctx.rest.get("/api/states")
        checks["api_states"] = {
            "ok": True,
            "entity_count": len(states) if isinstance(states, list) else 0,
        }
    except Exception as e:
        checks["api_states"] = {"ok": False, "error": str(e)[:200]}

    # Single-entity state lookup — powers haops_entity_state
    if checks["api_states"].get("ok") and isinstance(states, list) and states:
        try:
            probe_id = states[0].get("entity_id")
            if probe_id:
                raw = await ctx.rest.get(f"/api/states/{probe_id}")
                checks["api_single_state"] = {
                    "ok": isinstance(raw, dict) and "state" in raw,
                    "entity_id": probe_id,
                }
        except Exception as e:
            checks["api_single_state"] = {"ok": False, "error": str(e)[:200]}

    # Note: /api/config/entity_registry was removed from REST (WS-only now).
    # Entity registry is read from filesystem with WS fallback — tested in
    # the filesystem/websocket groups. Not tested here.

    # Note: /api/error_log may 404 via Supervisor proxy (permission-scoped).
    # haops_system_logs reads /config/home-assistant.log first, REST as fallback.
    # That fallback is tested by the filesystem group.

    all_ok = all(c.get("ok") for c in checks.values())
    return {
        "status": "pass" if all_ok else "fail",
        "tools_affected": [
            "haops_entity_list", "haops_entity_audit", "haops_entity_state",
            "haops_system_info", "haops_system_logs", "haops_service_call",
        ],
        "tests": checks,
    }


async def _check_websocket(ctx: HaOpsContext) -> dict[str, Any]:
    """Exercise WS commands used by dashboard/config tools."""
    checks: dict[str, Any] = {}

    try:
        await ctx.ws.send_command("ping")
        checks["ping"] = {"ok": True}
    except Exception as e:
        checks["ping"] = {"ok": False, "error": str(e)[:200]}

    # If ping didn't work, don't bother with further commands
    if checks["ping"]["ok"]:
        try:
            result = await ctx.ws.send_command("get_config")
            checks["get_config"] = {
                "ok": True,
                "ha_version": (
                    result.get("version") if isinstance(result, dict) else None
                ),
            }
        except Exception as e:
            checks["get_config"] = {"ok": False, "error": str(e)[:200]}

        dashboards: list[Any] | None = None
        try:
            result = await ctx.ws.send_command("lovelace/dashboards/list")
            dashboards = result if isinstance(result, list) else None
            checks["dashboards_list"] = {
                "ok": True,
                "count": len(dashboards) if dashboards is not None else 0,
            }
        except Exception as e:
            checks["dashboards_list"] = {"ok": False, "error": str(e)[:200]}

        # Round-trip a real dashboard read through _get_dashboard_config so a
        # regression in either tier (filesystem path build, WS kwargs) is
        # caught here instead of in the middle of a user session.
        try:
            from ha_ops_mcp.tools.dashboard import _get_dashboard_config

            target_id = "lovelace"
            if dashboards:
                first = next(
                    (d for d in dashboards if isinstance(d, dict) and d.get("url_path")),
                    None,
                )
                if first is not None:
                    target_id = first["url_path"]
            cfg = await _get_dashboard_config(ctx, target_id)
            checks["dashboard_get"] = {
                "ok": cfg is not None,
                "url_path": target_id,
                **({"error": "no config returned"} if cfg is None else {}),
            }
        except Exception as e:
            checks["dashboard_get"] = {"ok": False, "error": str(e)[:200]}

    all_ok = all(c.get("ok") for c in checks.values())
    return {
        "status": "pass" if all_ok else ("partial" if checks["ping"].get("ok") else "fail"),
        "tools_affected": [
            "haops_dashboard_list", "haops_dashboard_get",
            "haops_dashboard_diff", "haops_dashboard_patch",
            "haops_dashboard_apply",
            "haops_batch_preview", "haops_batch_apply",
            "haops_dashboard_resources",
            "haops_config_validate",
        ],
        "tests": checks,
    }


async def _check_database(ctx: HaOpsContext) -> dict[str, Any]:
    """Exercise DB queries used by db tools."""
    if ctx.db is None:
        return {
            "status": "skip",
            "reason": "Database not configured",
            "tools_affected": [
                "haops_db_query", "haops_db_health", "haops_db_execute",
                "haops_db_purge", "haops_db_statistics",
            ],
        }

    checks: dict[str, Any] = {}

    # Basic query
    try:
        result = await ctx.db.query("SELECT 1 AS one", limit=1)
        checks["basic_query"] = {
            "ok": len(result.rows) == 1,
            "backend": ctx.db.backend_type,
        }
    except Exception as e:
        checks["basic_query"] = {"ok": False, "error": str(e)[:200]}

    # HA schema tables — required for db_health, db_statistics
    try:
        result = await ctx.db.query("SELECT COUNT(*) AS c FROM states", limit=1)
        checks["states_table"] = {
            "ok": True,
            "row_count": result.rows[0].get("c") if result.rows else 0,
        }
    except Exception as e:
        checks["states_table"] = {"ok": False, "error": str(e)[:200]}

    try:
        result = await ctx.db.query(
            "SELECT COUNT(*) AS c FROM statistics_meta", limit=1
        )
        checks["statistics_meta"] = {
            "ok": True,
            "row_count": result.rows[0].get("c") if result.rows else 0,
        }
    except Exception as e:
        checks["statistics_meta"] = {"ok": False, "error": str(e)[:200]}

    # Health metrics
    try:
        health = await ctx.db.health()
        checks["health"] = {
            "ok": True,
            "schema_version": health.schema_version,
            "tables_found": len(health.table_sizes),
        }
    except Exception as e:
        checks["health"] = {"ok": False, "error": str(e)[:200]}

    all_ok = all(c.get("ok") for c in checks.values())
    return {
        "status": "pass" if all_ok else "fail",
        "tools_affected": [
            "haops_db_query", "haops_db_health", "haops_db_execute",
            "haops_db_purge", "haops_db_statistics",
        ],
        "tests": checks,
    }


async def _check_filesystem(ctx: HaOpsContext) -> dict[str, Any]:
    """Exercise filesystem reads used by config/entity tools."""
    checks: dict[str, Any] = {}
    config_root = Path(ctx.config.filesystem.config_root)

    checks["config_root"] = {
        "ok": config_root.is_dir(),
        "path": str(config_root),
    }

    ha_version_file = config_root / ".HA_VERSION"
    if ha_version_file.exists():
        try:
            checks["ha_version_file"] = {
                "ok": True,
                "version": ha_version_file.read_text().strip(),
            }
        except Exception as e:
            checks["ha_version_file"] = {"ok": False, "error": str(e)[:200]}
    else:
        checks["ha_version_file"] = {
            "ok": False,
            "note": ".HA_VERSION not found (non-critical)",
        }

    configuration_yaml = config_root / "configuration.yaml"
    checks["configuration_yaml"] = {
        "ok": configuration_yaml.exists(),
        "readable": configuration_yaml.is_file() if configuration_yaml.exists() else False,
    }

    # home-assistant.log — optional source for haops_system_logs
    # HA OS uses journald instead (Supervisor /core/logs endpoint),
    # so absence of this file is NOT a failure.
    ha_log = config_root / "home-assistant.log"
    checks["home_assistant_log"] = {
        "ok": True,
        "exists": ha_log.is_file(),
        "note": (
            "Primary log source for haops_system_logs" if ha_log.is_file()
            else "Not present (HA uses journald — logs read via Supervisor)"
        ),
    }

    # Critical checks: config_root must work; other file reads are best-effort
    critical_ok = checks["config_root"]["ok"]
    return {
        "status": "pass" if critical_ok else "fail",
        "tools_affected": [
            "haops_config_read", "haops_config_patch",
            "haops_config_create", "haops_config_apply", "haops_config_search",
            "haops_batch_preview", "haops_batch_apply",
            "haops_system_logs",
        ],
        "tests": checks,
    }


async def _check_registries(ctx: HaOpsContext) -> dict[str, Any]:
    """Exercise each .storage/core.* registry used by haops_registry_query.

    Probes filesystem access first, then reports count. Failure on a given
    registry means haops_registry_query for that registry won't work
    (though WS fallback may still succeed at runtime for some types).
    """
    from ha_ops_mcp.tools.registry import _REGISTRIES

    checks: dict[str, Any] = {}
    for name, spec in _REGISTRIES.items():
        path = Path(ctx.config.filesystem.config_root) / spec["file"]
        if not path.is_file():
            checks[name] = {
                "ok": bool(spec.get("ws_command")),
                "file": str(path),
                "exists": False,
                "note": (
                    f"File missing — will try WS fallback ({spec['ws_command']})"
                    if spec.get("ws_command")
                    else "File missing and no WS fallback — registry unavailable"
                ),
            }
            continue
        try:
            data = json.loads(path.read_text())
            records = data.get("data", {}).get(spec["data_key"], [])
            checks[name] = {
                "ok": isinstance(records, list),
                "file": str(path),
                "count": len(records) if isinstance(records, list) else 0,
            }
        except Exception as e:
            checks[name] = {
                "ok": False,
                "file": str(path),
                "error": str(e)[:200],
            }

    all_ok = all(c.get("ok") for c in checks.values())
    return {
        "status": "pass" if all_ok else "partial",
        "tools_affected": [
            "haops_registry_query",
            "haops_device_info",
            "haops_entity_list",
            "haops_entity_audit",
            "haops_entity_find",
        ],
        "tests": checks,
    }


async def _check_supervisor(ctx: HaOpsContext) -> dict[str, Any]:
    """Exercise Supervisor API for addon tools."""
    import aiohttp

    checks: dict[str, Any] = {}

    try:
        headers = {"Authorization": f"Bearer {ctx.config.ha.resolve_token()}"}
        timeout = aiohttp.ClientTimeout(total=5)
        async with aiohttp.ClientSession() as session, session.get(
            "http://supervisor/info", headers=headers, timeout=timeout
        ) as resp:
            if resp.status == 200:
                data = await resp.json()
                result = data.get("data", {})
                checks["supervisor_info"] = {
                    "ok": True,
                    "supervisor_version": result.get("supervisor"),
                    "homeassistant_version": result.get("homeassistant"),
                    "hassos_version": result.get("hassos"),
                    "arch": result.get("arch"),
                }
            else:
                checks["supervisor_info"] = {
                    "ok": False,
                    "error": f"HTTP {resp.status}",
                }
    except Exception as e:
        checks["supervisor_info"] = {
            "ok": False,
            "error": str(e)[:200],
            "note": "Not running in addon context (expected for standalone)",
        }

    if checks["supervisor_info"].get("ok"):
        try:
            async with aiohttp.ClientSession() as session, session.get(
                "http://supervisor/addons",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=5),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    addons = data.get("data", {}).get("addons", [])
                    checks["addons_list"] = {"ok": True, "count": len(addons)}
                else:
                    checks["addons_list"] = {
                        "ok": False,
                        "error": f"HTTP {resp.status}",
                    }
        except Exception as e:
            checks["addons_list"] = {"ok": False, "error": str(e)[:200]}

    status = "skip" if not checks["supervisor_info"].get("ok") else (
        "pass" if all(c.get("ok") for c in checks.values()) else "fail"
    )
    return {
        "status": status,
        "tools_affected": [
            "haops_addon_list", "haops_addon_info",
            "haops_addon_logs", "haops_addon_restart",
        ],
        "tests": checks,
    }


async def _check_shell(ctx: HaOpsContext) -> dict[str, Any]:
    """Exercise shell subprocess execution."""
    import asyncio

    checks: dict[str, Any] = {}

    try:
        proc = await asyncio.create_subprocess_shell(
            "echo ha-ops-tools-check",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        output = stdout.decode().strip()
        checks["echo"] = {
            "ok": proc.returncode == 0 and output == "ha-ops-tools-check",
            "output": output,
            "exit_code": proc.returncode,
        }
    except Exception as e:
        checks["echo"] = {"ok": False, "error": str(e)[:200]}

    try:
        config_root = ctx.config.filesystem.config_root
        proc = await asyncio.create_subprocess_shell(
            f"ls -la {config_root} | head -3",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        checks["config_access"] = {
            "ok": proc.returncode == 0,
            "exit_code": proc.returncode,
        }
    except Exception as e:
        checks["config_access"] = {"ok": False, "error": str(e)[:200]}

    all_ok = all(c.get("ok") for c in checks.values())
    return {
        "status": "pass" if all_ok else "fail",
        "tools_affected": ["haops_exec_shell"],
        "tests": checks,
    }


async def _check_debugger(ctx: HaOpsContext) -> dict[str, Any]:
    """Probe the endpoints behind the v0.7 debugger tools."""
    checks: dict[str, Any] = {}
    ha_url = ctx.config.ha.url

    # /api/template — used by haops_template_render. The endpoint returns
    # text/plain (not JSON), so use post_text() instead of post().
    try:
        rendered = await ctx.rest.post_text(
            "/api/template", {"template": "{{ 1 + 1 }}"}
        )
        checks["template_render"] = {
            "ok": rendered.strip() == "2",
            "sample": rendered[:40],
        }
    except Exception as e:
        checks["template_render"] = {
            "ok": False,
            "error": str(e)[:200],
            "url_tried": f"{ha_url}/api/template",
            "hint": (
                "POST /api/template failed. Check: "
                "(1) Is the REST API reachable at the URL above? "
                "(2) Does the token have access to the template "
                "service? (3) HA Core logs for REST API errors."
            ),
        }

    # WS get_services — used by haops_service_list
    try:
        data = await ctx.ws.send_command("get_services")
        ok = isinstance(data, dict) and len(data) > 0
        checks["services_schema"] = {
            "ok": ok,
            "domain_count": len(data) if isinstance(data, dict) else 0,
        }
    except Exception as e:
        checks["services_schema"] = {
            "ok": False,
            "error": str(e)[:200],
            "hint": (
                "WS get_services failed. If the WebSocket self-check "
                "also shows degraded/fail, fix the WS connection "
                "first — this probe depends on it."
            ),
        }

    all_ok = all(c.get("ok") for c in checks.values())
    return {
        "status": "pass" if all_ok else "partial",
        "tools_affected": [
            "haops_entity_history", "haops_logbook", "haops_template_render",
            "haops_service_list", "haops_automation_trace",
        ],
        "tests": checks,
    }


async def _check_refs(ctx: HaOpsContext) -> dict[str, Any]:
    """Build the reference index and confirm the ref tools work.

    The index draws from registries + YAML + dashboards that earlier groups
    already cover; this check just confirms the graph assembles without
    crashing and the ref-tool surface is alive.
    """
    from ha_ops_mcp.refindex import RefIndex

    tools_affected = ["haops_references", "haops_refactor_check"]

    checks: dict[str, Any] = {}
    try:
        ctx.request_index = None  # force fresh build
        index = RefIndex()
        await index.build(ctx)
        stats = index.stats()
        checks["index_build"] = {
            "ok": True,
            "total_nodes": stats.get("_total_nodes", 0),
            "total_edges": stats.get("_total_edges", 0),
        }
    except Exception as e:
        return {
            "status": "fail",
            "tools_affected": tools_affected,
            "tests": {"index_build": {"ok": False, "error": str(e)[:200]}},
        }

    all_ok = all(c.get("ok") for c in checks.values())
    return {
        "status": "pass" if all_ok else "fail",
        "tools_affected": tools_affected,
        "tests": checks,
    }


@registry.tool(
    name="haops_tools_check",
    description=(
        "Passive integration test — validate each tool group works against "
        "your real HA instance. No mutations. Useful after HA upgrades or "
        "when developing/testing ha-ops-mcp. "
        "Tests each group with real READ-ONLY operations and reports which "
        "haops_* tools are functional. "
        "Groups tested: REST API, WebSocket, Database, Filesystem, "
        "Supervisor API, Shell execution. "
        "Read-only, no parameters. For configuration/connectivity issues, "
        "use haops_self_check instead."
    ),
)
async def haops_tools_check(ctx: HaOpsContext) -> dict[str, Any]:
    results: dict[str, Any] = {}

    results["rest_api"] = await _check_rest_api(ctx)
    results["websocket"] = await _check_websocket(ctx)
    results["database"] = await _check_database(ctx)
    results["filesystem"] = await _check_filesystem(ctx)
    results["registries"] = await _check_registries(ctx)
    results["supervisor"] = await _check_supervisor(ctx)
    results["shell"] = await _check_shell(ctx)
    results["refs"] = await _check_refs(ctx)
    results["debugger"] = await _check_debugger(ctx)

    # Summary
    statuses = [r.get("status") for r in results.values()]
    pass_count = sum(1 for s in statuses if s == "pass")
    fail_count = sum(1 for s in statuses if s == "fail")
    partial_count = sum(1 for s in statuses if s == "partial")
    skip_count = sum(1 for s in statuses if s == "skip")

    if fail_count == 0 and partial_count == 0:
        overall = "all_pass"
    elif fail_count == 0:
        overall = "pass_with_degradation"
    elif pass_count > 0:
        overall = "partial_failure"
    else:
        overall = "all_fail"

    # Collect broken tools
    broken_tools: list[str] = []
    for group in results.values():
        if group.get("status") in ("fail", "partial"):
            broken_tools.extend(group.get("tools_affected", []))

    results["summary"] = {
        "overall": overall,
        "groups_passing": pass_count,
        "groups_failing": fail_count,
        "groups_partial": partial_count,
        "groups_skipped": skip_count,
        "broken_tools": broken_tools,
    }

    return results
