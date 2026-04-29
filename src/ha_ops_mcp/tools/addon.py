"""Add-on tools — Supervisor API access for HA OS/Supervised installs.

The Supervisor API is available at http://supervisor/... with the
HA access token. It's only available in HA OS and Supervised installs,
not in Container or Core installs.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ha_ops_mcp.server import registry

if TYPE_CHECKING:
    from ha_ops_mcp.server import HaOpsContext

logger = logging.getLogger(__name__)

# The Supervisor API endpoint — available inside HA OS / Supervised
_SUPERVISOR_URL = "http://supervisor"


async def _supervisor_get(
    ctx: HaOpsContext, path: str
) -> dict[str, Any] | None:
    """Make a GET request to the Supervisor API.

    Uses the same HA token — the Supervisor trusts it when the request
    comes from within the add-on network.
    """
    import aiohttp

    url = f"{_SUPERVISOR_URL}{path}"
    headers = {
        "Authorization": f"Bearer {ctx.config.ha.resolve_token()}",
    }

    try:
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession() as session, session.get(
            url, headers=headers, timeout=timeout
        ) as resp:
            if resp.status != 200:
                return None
            data = await resp.json()
            return data.get("data", data)  # type: ignore[no-any-return]
    except Exception as e:
        logger.debug("Supervisor API unavailable: %s", e)
        return None


async def _supervisor_post(
    ctx: HaOpsContext, path: str, data: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    """Make a POST request to the Supervisor API."""
    import aiohttp

    url = f"{_SUPERVISOR_URL}{path}"
    headers = {
        "Authorization": f"Bearer {ctx.config.ha.resolve_token()}",
    }

    try:
        async with aiohttp.ClientSession() as session, session.post(
            url, headers=headers, json=data,
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                return {"error": f"HTTP {resp.status}: {text}"}
            result = await resp.json()
            return result.get("data", result)  # type: ignore[no-any-return]
    except Exception as e:
        return {"error": f"Supervisor API unavailable: {e}"}


_self_slug_cache: str | None = None


async def _is_self_addon(ctx: HaOpsContext, slug: str) -> bool:
    """Return True if `slug` resolves to the ha-ops-mcp addon itself.

    Restarting self is special-cased in `haops_addon_restart` because the MCP
    stdio/SSE session does not survive the process restart and the client
    must reconnect. We look up our own slug via Supervisor's `/addons/self`
    alias once per process and cache it.

    Matches on the literal string "self" or the real slug returned by
    `/addons/self/info`. Returns False if self lookup fails (we don't have
    enough info to warn, but the restart still works).
    """
    global _self_slug_cache

    if slug == "self":
        return True

    if _self_slug_cache is None:
        info = await _supervisor_get(ctx, "/addons/self/info")
        # Negative cache on lookup failure — avoids re-asking every call.
        _self_slug_cache = (
            info.get("slug") or "" if info and isinstance(info, dict) else ""
        )

    return bool(_self_slug_cache) and slug == _self_slug_cache


@registry.tool(
    name="haops_addon_list",
    description=(
        "SUPERUSER TOOL: List all installed Home Assistant add-ons. "
        "Only works in HA OS or Supervised installs (requires Supervisor API). "
        "Returns add-on slug, name, version, state (started/stopped), "
        "and update availability. Read-only, no parameters."
    ),
)
async def haops_addon_list(ctx: HaOpsContext) -> dict[str, Any]:
    data = await _supervisor_get(ctx, "/addons")
    if data is None:
        return {
            "error": "Supervisor API not available. "
            "This tool only works in HA OS or Supervised installs."
        }

    addons = data.get("addons", []) if isinstance(data, dict) else data
    if not isinstance(addons, list):
        return {"error": "Unexpected response format from Supervisor API"}

    result = []
    for addon in addons:
        result.append({
            "slug": addon.get("slug"),
            "name": addon.get("name"),
            "version": addon.get("version"),
            "version_latest": addon.get("version_latest"),
            "state": addon.get("state"),
            "update_available": addon.get("update_available", False),
            "repository": addon.get("repository"),
        })

    # Sort: running first, then by name
    result.sort(key=lambda a: (a["state"] != "started", a["name"] or ""))

    return {"addons": result, "count": len(result)}


@registry.tool(
    name="haops_addon_info",
    description=(
        "SUPERUSER TOOL: Get detailed info for a specific add-on. "
        "Returns version, state, resource usage, network config, "
        "options, and available updates. "
        "Parameters: slug (string, required — e.g. 'core_mariadb', "
        "'core_ssh', 'a]0d7b49_esphome'). "
        "Use haops_addon_list to find the slug."
    ),
    params={
        "slug": {
            "type": "string",
            "description": "Add-on slug (from haops_addon_list)",
        },
    },
)
async def haops_addon_info(
    ctx: HaOpsContext, slug: str
) -> dict[str, Any]:
    info = await _supervisor_get(ctx, f"/addons/{slug}/info")
    if info is None:
        return {"error": f"Add-on '{slug}' not found or Supervisor API unavailable"}

    # Also get stats if the addon is running
    stats = None
    if info.get("state") == "started":
        stats = await _supervisor_get(ctx, f"/addons/{slug}/stats")

    result: dict[str, Any] = {
        "slug": slug,
        "name": info.get("name"),
        "version": info.get("version"),
        "version_latest": info.get("version_latest"),
        "state": info.get("state"),
        "description": info.get("description"),
        "url": info.get("url"),
        "auto_update": info.get("auto_update"),
        "boot": info.get("boot"),  # auto / manual
        "options": info.get("options"),
        "network": info.get("network"),
        "host_network": info.get("host_network"),
        "ingress": info.get("ingress"),
        "ingress_url": info.get("ingress_url"),
    }

    if stats:
        result["stats"] = {
            "cpu_percent": stats.get("cpu_percent"),
            "memory_usage": stats.get("memory_usage"),
            "memory_limit": stats.get("memory_limit"),
            "memory_percent": stats.get("memory_percent"),
            "network_rx": stats.get("network_rx"),
            "network_tx": stats.get("network_tx"),
            "blk_read": stats.get("blk_read"),
            "blk_write": stats.get("blk_write"),
        }

    return result


@registry.tool(
    name="haops_addon_logs",
    description=(
        "SUPERUSER TOOL: Get logs from a specific add-on. "
        "Parameters: slug (string, required), "
        "lines (int, default 100 — last N lines). "
        "Returns the add-on's stdout/stderr log output."
    ),
    params={
        "slug": {
            "type": "string",
            "description": "Add-on slug",
        },
        "lines": {
            "type": "integer",
            "description": "Number of log lines",
            "default": 100,
        },
    },
)
async def haops_addon_logs(
    ctx: HaOpsContext, slug: str, lines: int = 100
) -> dict[str, Any]:
    import aiohttp

    url = f"{_SUPERVISOR_URL}/addons/{slug}/logs"
    headers = {
        "Authorization": f"Bearer {ctx.config.ha.resolve_token()}",
    }

    try:
        async with aiohttp.ClientSession() as session, session.get(
            url, headers=headers,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                return {
                    "error": f"Could not fetch logs for '{slug}' "
                    f"(HTTP {resp.status})"
                }
            text = await resp.text()
    except Exception as e:
        return {"error": f"Supervisor API unavailable: {e}"}

    log_lines = text.splitlines()
    log_lines = log_lines[-lines:]

    return {
        "slug": slug,
        "lines": log_lines,
        "count": len(log_lines),
    }


@registry.tool(
    name="haops_addon_restart",
    description=(
        "SUPERUSER TOOL: Restart a specific add-on. Two-phase: "
        "call without confirm to preview, call with confirm=true "
        "and the token to execute. "
        "Parameters: slug (string, required), "
        "confirm (bool, default false), "
        "token (string, required if confirm=true). "
        "WARNING: This interrupts the add-on's service."
    ),
    params={
        "slug": {
            "type": "string",
            "description": "Add-on slug",
        },
        "confirm": {
            "type": "boolean",
            "description": "Execute restart",
            "default": False,
        },
        "token": {
            "type": "string",
            "description": "Confirmation token",
        },
    },
)
async def haops_addon_restart(
    ctx: HaOpsContext,
    slug: str,
    confirm: bool = False,
    token: str | None = None,
) -> dict[str, Any]:
    # Get addon info first for preview
    info = await _supervisor_get(ctx, f"/addons/{slug}/info")
    if info is None:
        return {"error": f"Add-on '{slug}' not found or Supervisor API unavailable"}

    is_self = await _is_self_addon(ctx, slug)

    if not confirm:
        warning = (
            f"This will restart add-on '{info.get('name')}'. "
            "The add-on's service will be temporarily interrupted."
        )
        if is_self:
            warning += (
                " RESTARTING SELF: your MCP session will drop mid-restart — "
                "you will need to reconnect (in Claude Code: run `/mcp` "
                "and reconnect the server) before any further tool calls "
                "will work."
            )
        tk = ctx.safety.create_token(
            action="addon_restart",
            details={"slug": slug, "name": info.get("name"), "self": is_self},
        )
        return {
            "slug": slug,
            "name": info.get("name"),
            "state": info.get("state"),
            "self": is_self,
            "warning": warning,
            "token": tk.id,
            "message": "Call again with confirm=true and this token to restart.",
        }

    if token is None:
        return {"error": "confirm=true requires a token"}

    try:
        ctx.safety.validate_token(token)
    except Exception as e:
        return {"error": str(e)}

    result = await _supervisor_post(ctx, f"/addons/{slug}/restart")

    ctx.safety.consume_token(token)

    await ctx.audit.log(
        tool="addon_restart",
        details={"slug": slug, "name": info.get("name")},
        token_id=token,
    )

    if isinstance(result, dict) and "error" in result:
        return result

    return {
        "success": True,
        "slug": slug,
        "name": info.get("name"),
        "message": f"Add-on '{info.get('name')}' is restarting.",
    }
