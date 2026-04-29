"""Debugger tools — history, logbook, template render, service schemas, traces.

These are the v0.7 "what is / was happening?" tools. They wrap HA's
introspection endpoints so the LLM can debug automations, preview template
output, and inspect service schemas without shelling out.

All tools are read-only.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from ha_ops_mcp.connections.rest import RestClientError
from ha_ops_mcp.connections.websocket import WebSocketError
from ha_ops_mcp.server import registry

if TYPE_CHECKING:
    from ha_ops_mcp.server import HaOpsContext

logger = logging.getLogger(__name__)


def _format_ts(ts: str | None) -> str | None:
    """Accept ISO-8601 timestamps or raw numeric epoch; return ISO-8601.

    HA's history/logbook endpoints expect `YYYY-MM-DDTHH:MM:SS+00:00` style
    timestamps in the URL path. We normalize whatever the caller gave us.
    """
    if ts is None or ts == "":
        return None
    try:
        # Numeric epoch (seconds)
        epoch = float(ts)
        return datetime.fromtimestamp(epoch, tz=UTC).isoformat()
    except (TypeError, ValueError):
        pass
    # Assume it's already ISO-8601 — pass through after a sanity parse.
    try:
        datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return ts
    except ValueError:
        return ts  # let HA reject it if malformed; keep the error visible


# ── haops_entity_history ──────────────────────────────────────────────


@registry.tool(
    name="haops_entity_history",
    description=(
        "Fetch state history for one or more entities from the recorder. "
        "Wraps REST /api/history/period/<start>. "
        "Parameters: entity_id (string or list, required), start (ISO-8601 or "
        "epoch seconds, required), end (optional ISO-8601 or epoch), "
        "minimal_response (bool, default true — omit attributes for smaller "
        "payloads), significant_changes_only (bool, default false). "
        "Returns a list of state-change arrays, one per entity. Read-only."
    ),
    params={
        "entity_id": {"type": "string", "description": "Entity id or comma-separated list"},
        "start": {"type": "string", "description": "ISO-8601 timestamp or epoch seconds"},
        "end": {"type": "string", "description": "Optional end timestamp", "default": ""},
        "minimal_response": {"type": "boolean", "default": True},
        "significant_changes_only": {"type": "boolean", "default": False},
    },
)
async def haops_entity_history(
    ctx: HaOpsContext,
    entity_id: str,
    start: str,
    end: str = "",
    minimal_response: bool = True,
    significant_changes_only: bool = False,
) -> dict[str, Any]:
    start_iso = _format_ts(start)
    end_iso = _format_ts(end)
    if not start_iso:
        return {"error": "start is required"}

    path = f"/api/history/period/{start_iso}"
    params = []
    params.append(f"filter_entity_id={quote(entity_id, safe=',')}")
    if end_iso:
        params.append(f"end_time={quote(end_iso, safe='')}")
    if minimal_response:
        params.append("minimal_response")
    if significant_changes_only:
        params.append("significant_changes_only")
    path = f"{path}?{'&'.join(params)}"

    try:
        data = await ctx.rest.get(path)
    except RestClientError as e:
        return {"error": str(e)}

    # HA returns list[list[state]] — one inner list per entity.
    if not isinstance(data, list):
        return {"error": "Unexpected response shape", "raw": data}

    series = [
        {
            "entity_id": group[0].get("entity_id") if group else None,
            "points": group,
            "count": len(group),
        }
        for group in data
    ]
    return {
        "start": start_iso,
        "end": end_iso,
        "series": series,
        "entity_count": len(series),
    }


# ── haops_logbook ─────────────────────────────────────────────────────


@registry.tool(
    name="haops_logbook",
    description=(
        "Fetch the logbook (human-readable event stream) from HA. Wraps "
        "REST /api/logbook/<start>. Unlike entity_history (which returns "
        "state snapshots) logbook returns narrative events: automation "
        "triggers, script runs, device status changes. "
        "Parameters: start (ISO-8601 or epoch, required), end (optional), "
        "entity_id (optional — filter to a single entity). Read-only."
    ),
    params={
        "start": {"type": "string", "description": "ISO-8601 timestamp or epoch seconds"},
        "end": {"type": "string", "default": ""},
        "entity_id": {"type": "string", "default": ""},
    },
)
async def haops_logbook(
    ctx: HaOpsContext,
    start: str,
    end: str = "",
    entity_id: str = "",
) -> dict[str, Any]:
    start_iso = _format_ts(start)
    end_iso = _format_ts(end)
    if not start_iso:
        return {"error": "start is required"}

    path = f"/api/logbook/{start_iso}"
    params = []
    if end_iso:
        params.append(f"end_time={quote(end_iso, safe='')}")
    if entity_id:
        params.append(f"entity={quote(entity_id, safe='')}")
    if params:
        path = f"{path}?{'&'.join(params)}"

    try:
        data = await ctx.rest.get(path)
    except RestClientError as e:
        return {"error": str(e)}

    if not isinstance(data, list):
        return {"error": "Unexpected response shape", "raw": data}

    return {
        "start": start_iso,
        "end": end_iso,
        "entries": data,
        "count": len(data),
    }


# ── haops_template_render ─────────────────────────────────────────────


@registry.tool(
    name="haops_template_render",
    description=(
        "Render a Jinja template against live HA state without writing "
        "anything. Wraps POST /api/template. Use this to preview what a "
        "`value_template:` will produce before committing it to an automation "
        "or sensor. Parameters: template (string, required), variables "
        "(optional dict of template locals). Read-only."
    ),
    params={
        "template": {
            "type": "string",
            "description": "Jinja template, e.g. \"{{ states('sensor.temperature') }}\"",
        },
        "variables": {
            "type": "object",
            "description": "Optional variables available inside the template",
        },
    },
)
async def haops_template_render(
    ctx: HaOpsContext,
    template: str,
    variables: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not template:
        return {"error": "template is required"}
    payload: dict[str, Any] = {"template": template}
    if variables:
        payload["variables"] = variables
    try:
        # /api/template returns the rendered string as text/plain, not JSON.
        raw = await ctx.rest.post_text("/api/template", payload)
    except RestClientError as e:
        return {"error": str(e), "template": template}
    return {
        "template": template,
        "rendered": raw,
    }


# ── haops_service_list ────────────────────────────────────────────────


@registry.tool(
    name="haops_service_list",
    description=(
        "List service schemas (field names + descriptions + required flags) "
        "for HA domains. Essential for composing safe service calls. Uses WS "
        "get_services, falls back to REST /api/services. "
        "Parameters: domain (optional — filter to one domain). Read-only."
    ),
    params={
        "domain": {
            "type": "string",
            "description": "Filter to one domain, e.g. 'light'. Omit for all.",
            "default": "",
        },
    },
)
async def haops_service_list(
    ctx: HaOpsContext, domain: str = ""
) -> dict[str, Any]:
    # Prefer WS (richer field descriptions); fall back to REST.
    data: Any = None
    try:
        data = await ctx.ws.send_command("get_services")
    except WebSocketError as e:
        logger.debug("WS get_services failed: %s — falling back to REST", e)
        try:
            data = await ctx.rest.get("/api/services")
        except RestClientError as e2:
            return {"error": f"Could not fetch services: {e2}"}

    if isinstance(data, list):
        # REST shape: [{domain, services: {name: {...}}}, ...]
        by_domain = {entry.get("domain"): entry.get("services", {}) for entry in data}
    elif isinstance(data, dict):
        # WS shape: {domain: {service: {name, description, fields, ...}}}
        by_domain = data
    else:
        return {"error": "Unexpected services response", "raw": data}

    if domain:
        services = by_domain.get(domain) or {}
        return {
            "domain": domain,
            "services": services,
            "count": len(services),
        }
    return {
        "domains": sorted(by_domain.keys()),
        "domain_count": len(by_domain),
        "services_by_domain": by_domain,
    }


# ── haops_automation_trace ────────────────────────────────────────────


@registry.tool(
    name="haops_automation_trace",
    description=(
        "Fetch automation traces — per-step execution data for debugging "
        "automations that didn't do what you expected. Wraps WS trace/list "
        "(when run_id is empty) and trace/get (when run_id is provided). "
        "Parameters: automation_id (string, required — the automation's "
        "unique id), run_id (optional — a specific run to inspect; omit to "
        "list available runs). Read-only."
    ),
    params={
        "automation_id": {"type": "string"},
        "run_id": {"type": "string", "default": ""},
    },
)
async def haops_automation_trace(
    ctx: HaOpsContext,
    automation_id: str,
    run_id: str = "",
) -> dict[str, Any]:
    if not automation_id:
        return {"error": "automation_id is required"}

    try:
        if not run_id:
            runs = await ctx.ws.send_command(
                "trace/list", domain="automation", item_id=automation_id
            )
            return {
                "automation_id": automation_id,
                "runs": runs or [],
                "count": len(runs) if isinstance(runs, list) else 0,
                "hint": "Call again with run_id to get full trace data.",
            }
        trace = await ctx.ws.send_command(
            "trace/get",
            domain="automation",
            item_id=automation_id,
            run_id=run_id,
        )
        return {
            "automation_id": automation_id,
            "run_id": run_id,
            "trace": trace,
        }
    except WebSocketError as e:
        return {"error": f"trace fetch failed: {e}"}
