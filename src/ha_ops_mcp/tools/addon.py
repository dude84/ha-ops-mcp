"""Add-on tools — Supervisor API access for HA OS/Supervised installs.

The Supervisor API is available at http://supervisor/... with the
HA access token. It's only available in HA OS and Supervised installs,
not in Container or Core installs.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ha_ops_mcp.server import registry

if TYPE_CHECKING:
    from ha_ops_mcp.server import HaOpsContext

logger = logging.getLogger(__name__)

# The Supervisor API endpoint — available inside HA OS / Supervised
_SUPERVISOR_URL = "http://supervisor"


def _supervisor_token(ctx: HaOpsContext) -> str:
    """Token for Supervisor API calls.

    MUST be the addon's SUPERVISOR_TOKEN (always in the addon env), NOT the
    configured HA-Core token. They coincide only when `ha_token` is empty (run.sh
    aliases the empty token to SUPERVISOR_TOKEN). The moment a real HA user token
    (LLAT) is set in `ha_token` — e.g. for the UI tools or a dedicated service
    user — reusing it here gets a 403: a Core user token is not a Supervisor
    token. Prefer the env var; fall back to the configured token only for
    non-addon/dev runs where SUPERVISOR_TOKEN isn't present.
    """
    import os

    return os.environ.get("SUPERVISOR_TOKEN") or ctx.config.ha.resolve_token()


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
        "Authorization": f"Bearer {_supervisor_token(ctx)}",
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
        "Authorization": f"Bearer {_supervisor_token(ctx)}",
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
        "Authorization": f"Bearer {_supervisor_token(ctx)}",
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
        ctx.safety.claim_token(token)
    except Exception as e:
        return {"error": str(e)}

    result = await _supervisor_post(ctx, f"/addons/{slug}/restart")

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


#: Supervisor's own refusal when an add-on POSTs its own update endpoint.
#: Matched loosely (substring) so a wording change still classifies.
_SELF_UPDATE_REFUSAL = "can't update itself"


async def _attempt_self_update(ctx: HaOpsContext, slug: str) -> dict[str, Any]:
    """POST our own update endpoint and report what Supervisor actually says.

    **Supervisor forbids this.** `POST /addons/<slug>/update` from inside that
    same add-on answers:

        HTTP 403 {"result": "error",
                  "message": "App <slug> can't update itself!"}

    Verified 2026-08-22 on Supervisor 2026.07.5. It is a deliberate guard, not
    a role or token problem — `hassio_role: manager` and a valid
    SUPERVISOR_TOKEN make no difference.

    History worth keeping, because it cost three releases: this used to fire
    the POST from a `setsid`-detached child writing to /dev/null, on the theory
    that Supervisor would tear our container down mid-request and the caller
    would never receive the response. That theory was wrong in both halves —
    Supervisor refuses instantly and nothing is torn down — and discarding the
    child's output hid the 403 completely. The tool reported `triggered: true`,
    nothing restarted, and there was no way to find out why (0.64.1 → 0.64.2,
    twice; then 0.64.3 → 0.65.0 once more, which is the attempt whose log
    finally named the cause).

    So: POST inline, no detach. If some future Supervisor lifts the guard and
    *does* start tearing us down, a dropped socket or timeout is treated as
    "initiated" the way `haops_system_restart` treats its own teardown.

    Returns the tool's response dict — either a clean refusal or, if the guard
    is ever lifted, an initiated/success shape.
    """
    import contextlib

    import aiohttp

    # Best-effort log of the raw outcome, for the same reason the log was
    # added in 0.64.3: whatever Supervisor says here should survive the call.
    log = _self_update_log_path(ctx)
    with contextlib.suppress(OSError):
        log.parent.mkdir(parents=True, exist_ok=True)

    url = f"{_SUPERVISOR_URL}/addons/{slug}/update"
    headers = {"Authorization": f"Bearer {_supervisor_token(ctx)}"}
    status: int | None = None
    body = ""
    try:
        async with aiohttp.ClientSession() as session, session.post(
            url, headers=headers, timeout=aiohttp.ClientTimeout(total=30)
        ) as resp:
            status = resp.status
            body = (await resp.text())[:500]
    except (aiohttp.ClientConnectionError, TimeoutError):
        # Only reachable if Supervisor ever starts accepting self-update and
        # tears the container down mid-request.
        with contextlib.suppress(OSError):
            log.write_text(f"--- self-update {slug} ---\nconnection dropped\n")
        return {
            "status": "initiated",
            "slug": slug,
            "message": (
                "Supervisor accepted the request and dropped the connection "
                "while stopping this add-on. Reconnect the MCP server once it "
                "is back (`/mcp` in Claude Code)."
            ),
        }
    except Exception as e:  # noqa: BLE001 - report, never raise, from a tool
        return {"error": f"Self-update request failed: {e}", "slug": slug}
    finally:
        with contextlib.suppress(OSError):
            log.write_text(
                f"--- self-update {slug} ---\nHTTP {status}\n{body}\n"
            )

    if status == 403 and _SELF_UPDATE_REFUSAL in body:
        return {
            "error": "Supervisor refuses to let an add-on update itself.",
            "slug": slug,
            "http_status": 403,
            "supervisor_message": body,
            "how_to_update": (
                "Home Assistant UI: Settings > Add-ons > this add-on > "
                "Update. There is no MCP path — the restriction is enforced "
                "by Supervisor, not by this server."
            ),
        }
    if status is not None and status >= 400:
        return {
            "error": f"Supervisor rejected the self-update (HTTP {status}).",
            "slug": slug,
            "http_status": status,
            "supervisor_message": body,
        }
    return {
        "status": "initiated",
        "slug": slug,
        "http_status": status,
        "message": (
            "Supervisor accepted the request — unexpected, it normally "
            "refuses self-update. Expect this session to drop; reconnect "
            "with `/mcp`."
        ),
    }


def _self_update_log_path(ctx: HaOpsContext) -> Path:
    """Where a self-update attempt records what Supervisor answered."""
    return Path(ctx.config.backup.dir) / "self_update.log"


@registry.tool(
    name="haops_addon_update",
    description=(
        "SUPERUSER TOOL: Update an add-on to its latest version. "
        "Reloads the add-on store index first, so a release published moments "
        "ago is seen (this is the 'Check for updates' click in the UI). "
        "Two-phase: call without confirm to preview the version change, then "
        "again with confirm=true and the token. "
        "Reports 'already_latest' and does nothing when no update exists. "
        "UPDATING THIS ADD-ON ITSELF IS IMPOSSIBLE — do not try. Supervisor "
        "forbids it outright: an add-on POSTing its own update endpoint gets "
        "HTTP 403 \"App <slug> can't update itself!\". That is a Supervisor "
        "guard, not a permission this server can be granted (verified on "
        "Supervisor 2026.07.5); no role, token or flag changes it. Updating "
        "ha-ops-mcp is a Home Assistant UI action: Settings > Add-ons > "
        "HA Ops MCP > Update. allow_self=true only makes the tool attempt the "
        "POST anyway and hand you Supervisor's exact refusal (kept so a future "
        "Supervisor that lifts the guard is detected rather than assumed); "
        "without it, self-update is refused up front. Either way nothing is "
        "updated and the session does NOT drop. "
        "Parameters: slug (string, required — from haops_addon_list), "
        "allow_self (bool, default false), "
        "confirm (bool, default false), "
        "token (string, required if confirm=true)."
    ),
    params={
        "slug": {
            "type": "string",
            "description": "Add-on slug",
        },
        "allow_self": {
            "type": "boolean",
            "description": (
                "Attempt the (Supervisor-forbidden) self-update anyway and "
                "return its 403. Cannot succeed; use the HA UI instead."
            ),
            "default": False,
        },
        "confirm": {
            "type": "boolean",
            "description": "Execute the update",
            "default": False,
        },
        "token": {
            "type": "string",
            "description": "Confirmation token from the preview step",
        },
    },
)
async def haops_addon_update(
    ctx: HaOpsContext,
    slug: str,
    allow_self: bool = False,
    confirm: bool = False,
    token: str | None = None,
) -> dict[str, Any]:
    # Refresh the store index before reading versions — Supervisor caches it,
    # so without this a release published minutes ago reports as "latest".
    # Best-effort: a failure here only means we might miss a brand-new version.
    await _supervisor_post(ctx, "/store/reload")

    info = await _supervisor_get(ctx, f"/addons/{slug}/info")
    if info is None:
        return {"error": f"Add-on '{slug}' not found or Supervisor API unavailable"}

    current = info.get("version")
    latest = info.get("version_latest")
    is_self = await _is_self_addon(ctx, slug)

    if not info.get("update_available"):
        return {
            "already_latest": True,
            "slug": slug,
            "name": info.get("name"),
            "version": current,
            "version_latest": latest,
            "message": f"'{info.get('name')}' is already at {current}.",
        }

    if is_self and not allow_self:
        return {
            "error": (
                f"Supervisor does not allow '{info.get('name')}' to update "
                "itself — an add-on POSTing its own update endpoint gets "
                "HTTP 403 \"can't update itself!\". No flag or role changes "
                "that; it is enforced upstream."
            ),
            "how_to_update": (
                f"Home Assistant UI: Settings > Add-ons > {info.get('name')} "
                f"> Update ({current} -> {latest})."
            ),
            "slug": slug,
            "self": True,
            "version": current,
            "version_latest": latest,
            "note": (
                "allow_self=true attempts the POST anyway and returns "
                "Supervisor's refusal verbatim — useful only to confirm the "
                "guard is still in place."
            ),
        }

    if not confirm:
        warning = (
            f"This will update '{info.get('name')}' from {current} to {latest}. "
            "The add-on's service is interrupted while it rebuilds."
        )
        if is_self:
            warning += (
                " UPDATING SELF: this response is returned first, then the "
                "update fires ~2s later and your MCP session drops. The "
                "ha-ops-mcp image builds on the host, so expect several "
                "minutes before it answers again (in Claude Code: `/mcp` to "
                "reconnect). If the new version fails to start, recovery is "
                "via the Home Assistant UI only."
            )
        tk = ctx.safety.create_token(
            action="addon_update",
            details={
                "slug": slug,
                "name": info.get("name"),
                "version": current,
                "version_latest": latest,
                "self": is_self,
            },
        )
        return {
            "slug": slug,
            "name": info.get("name"),
            "version": current,
            "version_latest": latest,
            "self": is_self,
            "warning": warning,
            "token": tk.id,
            "message": "Call again with confirm=true and this token to update.",
        }

    if token is None:
        return {"error": "confirm=true requires a token"}

    try:
        token_data = ctx.safety.claim_token(token)
    except Exception as e:  # noqa: BLE001 — returned to the caller as a message
        return {"error": str(e)}

    # A token minted for one add-on must not update a different one.
    if token_data.details.get("slug") != slug:
        return {"error": "Add-on slug does not match the token. Re-run the preview."}

    await ctx.audit.log(
        tool="addon_update",
        details={
            "slug": slug,
            "name": info.get("name"),
            "version": current,
            "version_latest": latest,
            "self": is_self,
        },
        token_id=token,
    )

    if is_self:
        # Audit is written BEFORE the attempt: if Supervisor ever does start
        # accepting self-update, it stops us mid-request and there would be no
        # opportunity to log anything afterwards.
        outcome = await _attempt_self_update(ctx, slug)
        return {
            "slug": slug,
            "name": info.get("name"),
            "version": current,
            "version_latest": latest,
            "self": True,
            "outcome_log": str(_self_update_log_path(ctx)),
            **outcome,
        }

    result = await _supervisor_post(ctx, f"/addons/{slug}/update")
    if isinstance(result, dict) and "error" in result:
        return result

    return {
        "success": True,
        "slug": slug,
        "name": info.get("name"),
        "version_before": current,
        "version": latest,
        "message": f"'{info.get('name')}' updated {current} -> {latest}.",
    }
