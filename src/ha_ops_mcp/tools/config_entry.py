"""Config-entry creation — drive Home Assistant's config flows over REST.

The one part of HA state ha-ops could not touch. We could *read* config
entries (`haops_registry_query type='config_entries'`, `config_entries/get`),
*reload* one (`haops_integration_reload`) and *remove* a device
(`haops_device_remove`) — but nothing could **create** an entry, so any
multi-entry integration was untestable end-to-end from here.

`haops_ws_command` is not the escape hatch for this: config flows are a REST
surface (`/api/config/config_entries/flow`), not a WS one, so the WS
passthrough cannot reach them. The alternatives were worse — `curl` with a
Bearer token through `haops_exec_shell` (correctly blocked by client-side
permission classifiers, since it is shaped exactly like credential
exfiltration), or hand-editing `.storage/core.config_entries` with a
hand-minted ULID and a full Core restart, which bypasses every validation the
flow itself performs.

Three tools cover the whole lifecycle:

    start → step (repeat until create_entry/abort) → [abort to discard]

Gating follows `haops_ws_command`'s shape: the phases that only read or park
state run immediately, the phase that commits is two-phase confirmed.
`start` opens a flow (a pending, discardable thing) and `abort` throws one
away; `step` is what actually creates the entry, so `step` is the gate.

One wrinkle that gating cannot prevent: a flow with no user input can
`create_entry` on the *init* call, i.e. inside `start`. HA does that
server-side before we ever see a response, so `start` detects it, audits it as
the mutation it was, and says so loudly in the result.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ha_ops_mcp.connections.rest import RestClientError
from ha_ops_mcp.server import registry

if TYPE_CHECKING:
    from ha_ops_mcp.server import HaOpsContext

logger = logging.getLogger(__name__)

_FLOW_BASE = "/api/config/config_entries/flow"

#: Flow-result types that end the flow — no further `step` call is possible.
_TERMINAL_TYPES = frozenset({"create_entry", "abort"})


def _normalise_step(step: Any) -> dict[str, Any]:
    """Flatten HA's flow-step payload into a stable, LLM-readable shape.

    HA returns a different key set per ``type`` (form / create_entry / abort /
    menu / external / progress). Rather than make every caller learn that
    matrix, lift the fields that matter per type and keep the untouched
    payload under ``raw`` only for types we don't model — so a future HA
    flow type degrades to "here is everything" instead of silently dropping
    the half the caller needed.
    """
    if not isinstance(step, dict):
        return {"type": "unknown", "raw": step}

    flow_type = step.get("type")
    out: dict[str, Any] = {
        "type": flow_type,
        "flow_id": step.get("flow_id"),
        "handler": step.get("handler"),
    }

    if flow_type == "form":
        out["step_id"] = step.get("step_id")
        # voluptuous-serialize output: a list of {name, type, required, ...}
        # describing exactly what user_input this step wants.
        out["data_schema"] = step.get("data_schema")
        out["errors"] = step.get("errors") or {}
        out["last_step"] = step.get("last_step")
        if step.get("description_placeholders"):
            out["description_placeholders"] = step["description_placeholders"]
    elif flow_type == "create_entry":
        # HA replaces `result` with the new entry's id and drops `data`
        # before serialising, so `result` IS the entry_id string.
        out["entry_id"] = step.get("result")
        out["title"] = step.get("title")
    elif flow_type == "abort":
        out["reason"] = step.get("reason")
        if step.get("description_placeholders"):
            out["description_placeholders"] = step["description_placeholders"]
    elif flow_type == "menu":
        out["step_id"] = step.get("step_id")
        out["menu_options"] = step.get("menu_options")
    else:
        # progress / external_step / anything HA adds later.
        out["raw"] = step

    return out


async def _in_progress(ctx: HaOpsContext, domain: str | None = None) -> list[dict[str, Any]]:
    """List pending flows, optionally filtered to one handler.

    Best-effort: a failure here must not stop a flow from being started, so
    callers treat an empty list as "couldn't tell" rather than "none exist".
    """
    try:
        flows = await ctx.rest.get(_FLOW_BASE)
    except RestClientError:
        return []
    if not isinstance(flows, list):
        return []
    rows = [
        {
            "flow_id": f.get("flow_id"),
            "handler": f.get("handler"),
            "step_id": f.get("step_id"),
            "context": (f.get("context") or {}).get("source"),
        }
        for f in flows
        if isinstance(f, dict)
    ]
    if domain:
        rows = [r for r in rows if r["handler"] == domain]
    return rows


# ── haops_integration_flow_start ──────────────────────────────────────


@registry.tool(
    name="haops_integration_flow_start",
    description=(
        "Start a config flow to ADD a new integration instance (config "
        "entry) — the 'Add integration' button, over MCP. This is the only "
        "way to create a config entry; haops_ws_command cannot reach config "
        "flows (they are REST, not WebSocket), and hand-editing "
        ".storage/core.config_entries bypasses the flow's own validation. "
        "Runs immediately (no confirm): it opens a PENDING flow, which "
        "creates nothing until you answer a step. "
        "Parameters: domain (string, required — the integration's domain, "
        "e.g. 'ac_controller', 'mqtt', 'shelly'), show_advanced_options "
        "(bool, default false — surfaces fields HA hides behind the "
        "'advanced mode' user setting). "
        "Returns the flow's first step: type ('form' normally), flow_id, "
        "step_id, and data_schema — the exact list of fields the step wants. "
        "Feed flow_id + your answers to haops_integration_flow_step. "
        "Also returns in_progress: other pending flows for this domain, so "
        "you don't stack duplicates — abandon them with "
        "haops_integration_flow_abort. "
        "NOTE: a flow that needs no input can create the entry on this very "
        "call; the result then carries type='create_entry' and "
        "created_on_start=true."
    ),
    params={
        "domain": {
            "type": "string",
            "description": "Integration domain to add (the flow 'handler')",
        },
        "show_advanced_options": {
            "type": "boolean",
            "description": "Include fields gated behind HA's advanced mode",
            "default": False,
        },
    },
)
async def haops_integration_flow_start(
    ctx: HaOpsContext,
    domain: str,
    show_advanced_options: bool = False,
) -> dict[str, Any]:
    if not domain:
        return {"error": "domain is required"}

    # Surface pre-existing pending flows BEFORE starting another one.
    existing = await _in_progress(ctx, domain)

    try:
        step = await ctx.rest.post(
            _FLOW_BASE,
            {"handler": domain, "show_advanced_options": show_advanced_options},
        )
    except RestClientError as e:
        # 400/404 here almost always means "no such domain" or "this
        # integration has no config flow" (YAML-only integrations).
        return {
            "error": f"Could not start a config flow for '{domain}': {e}",
            "hint": (
                "Check the domain spelling. Integrations that are YAML-only "
                "(no UI 'Add integration' entry) have no config flow — those "
                "are configured with haops_config_patch instead."
            ),
        }

    result = _normalise_step(step)
    created_on_start = result.get("type") == "create_entry"

    # A no-input flow commits inside this call. We cannot gate that (HA has
    # already created the entry by the time we see the response), so record
    # it as the mutation it was rather than logging it as a read.
    await ctx.audit.log(
        tool="integration_flow_start",
        details={
            "domain": domain,
            "flow_id": result.get("flow_id"),
            "result_type": result.get("type"),
            "entry_id": result.get("entry_id"),
            "created_on_start": created_on_start,
        },
        op_class="mutate" if created_on_start else "read",
    )

    out: dict[str, Any] = {"domain": domain, **result}
    if existing:
        out["in_progress"] = existing
        out["note"] = (
            f"{len(existing)} other pending flow(s) already exist for "
            f"'{domain}'. Discard stale ones with "
            "haops_integration_flow_abort so they stop showing as pending "
            "in Settings > Devices & Services."
        )
    if created_on_start:
        out["created_on_start"] = True
        out["warning"] = (
            "This flow required no input, so Home Assistant created the "
            f"config entry ({result.get('entry_id')}) during this call — "
            "there was no confirm step to gate. Remove it via the HA UI if "
            "that was not intended."
        )
    elif result.get("type") == "form":
        out["message"] = (
            "Flow open. Answer the fields in data_schema by calling "
            f"haops_integration_flow_step(flow_id='{result.get('flow_id')}', "
            "user_input={...}) — that call is two-phase confirmed because it "
            "is what creates the entry."
        )
    return out


# ── haops_integration_flow_step ───────────────────────────────────────


@registry.tool(
    name="haops_integration_flow_step",
    description=(
        "Answer one step of an open config flow — the call that actually "
        "CREATES the config entry. Two-phase: call without confirm to "
        "preview (shows the step's own data_schema next to the user_input "
        "you are about to submit, so a wrong or missing field is caught "
        "before you spend the token), then again with confirm=true and the "
        "token. "
        "Parameters: flow_id (string, required — from "
        "haops_integration_flow_start), user_input (object, required — "
        "field name to value, matching the step's data_schema; pass {} for a "
        "confirm-only step), confirm (bool, default false), token (string, "
        "required in phase 2). "
        "Returns the next step. type='form' with a non-empty errors object "
        "means the flow REJECTED the input (e.g. already_configured, "
        "invalid_slug) and nothing was created — fix user_input and call "
        "again. type='create_entry' means the entry exists and carries its "
        "entry_id. type='abort' means the flow ended without creating "
        "anything (see reason). A multi-step flow returns another form — "
        "keep calling with the same flow_id."
    ),
    params={
        "flow_id": {
            "type": "string",
            "description": "Open flow's id (from haops_integration_flow_start)",
        },
        "user_input": {
            "type": "object",
            "description": "Field values for this step ({} for confirm-only steps)",
        },
        "confirm": {"type": "boolean", "default": False},
        "token": {"type": "string", "default": ""},
    },
)
async def haops_integration_flow_step(
    ctx: HaOpsContext,
    flow_id: str = "",
    user_input: dict[str, Any] | None = None,
    confirm: bool = False,
    token: str = "",
) -> dict[str, Any]:
    # flow_id / user_input are only required in phase 1 — phase 2 reads them
    # back off the token, so the caller need not repeat them.
    if not confirm and not flow_id:
        return {"error": "flow_id is required"}
    if user_input is not None and not isinstance(user_input, dict):
        return {"error": "user_input must be an object of field values"}

    # Phase 1 — preview. Fetch the pending step so the preview can show what
    # the flow is asking for against what we're about to answer.
    if not confirm:
        pending: dict[str, Any] = {}
        try:
            pending = _normalise_step(await ctx.rest.get(f"{_FLOW_BASE}/{flow_id}"))
        except RestClientError as e:
            return {
                "error": f"No open flow '{flow_id}': {e}",
                "hint": (
                    "Flows are lost on a Core restart and after they "
                    "complete. Start a fresh one with "
                    "haops_integration_flow_start."
                ),
            }

        tk = ctx.safety.create_token(
            action="integration_flow_step",
            details={"flow_id": flow_id, "user_input": user_input or {}},
        )
        preview: dict[str, Any] = {
            "flow_id": flow_id,
            "handler": pending.get("handler"),
            "step_id": pending.get("step_id"),
            "submitting": user_input or {},
            "step_wants": pending.get("data_schema"),
        }
        out: dict[str, Any] = {
            "preview": preview,
            "token": tk.id,
            "message": (
                "Review the fields above — 'step_wants' is what the flow "
                "asked for, 'submitting' is what will be sent. Call again "
                "with confirm=true and this token to submit. This may create "
                "the config entry."
            ),
        }
        # Cheap arity check. Purely advisory: the flow itself is the
        # authority, and some schemas are conditional.
        schema = pending.get("data_schema")
        if isinstance(schema, list):
            wanted = {
                f.get("name") for f in schema
                if isinstance(f, dict) and f.get("required")
            }
            missing = sorted(n for n in wanted if n and n not in (user_input or {}))
            if missing:
                out["missing_required"] = missing
        return out

    # Phase 2 — apply
    if not token:
        return {"error": "confirm=true requires a token"}
    try:
        token_data = ctx.safety.claim_token(token)
    except Exception as e:
        return {"error": str(e)}

    target_flow = token_data.details["flow_id"]
    target_input = token_data.details["user_input"]

    try:
        step = await ctx.rest.post(f"{_FLOW_BASE}/{target_flow}", target_input)
    except RestClientError as e:
        await ctx.audit.log(
            tool="integration_flow_step",
            details={"flow_id": target_flow, "user_input": target_input},
            success=False,
            error=str(e),
            token_id=token,
        )
        return {"error": f"Flow step failed: {e}", "flow_id": target_flow}

    result = _normalise_step(step)
    created = result.get("type") == "create_entry"
    rejected = bool(result.get("errors"))

    await ctx.audit.log(
        tool="integration_flow_step",
        details={
            "flow_id": target_flow,
            "user_input": target_input,
            "result_type": result.get("type"),
            "entry_id": result.get("entry_id"),
            "errors": result.get("errors") or None,
        },
        success=not rejected,
        token_id=token,
    )

    out = {"success": not rejected, **result}
    if created:
        out["message"] = (
            f"Config entry created: {result.get('entry_id')} "
            f"({result.get('title')}). It is loaded and its entities are "
            "live — check with haops_registry_query or haops_entity_find."
        )
    elif rejected:
        out["message"] = (
            "The flow rejected this input — nothing was created. See "
            "'errors' (keyed by field, or 'base' for step-wide errors), fix "
            "user_input, and submit again against the same flow_id."
        )
    elif result.get("type") == "form":
        out["message"] = (
            "Next step returned. Answer it with another "
            "haops_integration_flow_step call on the same flow_id."
        )
    elif result.get("type") == "abort":
        out["message"] = (
            f"Flow ended without creating an entry (reason: "
            f"{result.get('reason')})."
        )
    return out


# ── haops_integration_flow_abort ──────────────────────────────────────


@registry.tool(
    name="haops_integration_flow_abort",
    description=(
        "Discard an open config flow. Use it to clean up a flow you started "
        "and are not going to finish — pending flows keep showing up in "
        "Settings > Devices & Services until they are answered or dropped. "
        "Runs immediately: it destroys a pending flow only, and can never "
        "affect an already-created config entry (to remove one of those, use "
        "the HA UI). "
        "Parameter: flow_id (string, required — from "
        "haops_integration_flow_start, or the in_progress list it returns). "
        "Already-gone flows report aborted=false rather than erroring."
    ),
    params={
        "flow_id": {"type": "string", "description": "Open flow's id"},
    },
)
async def haops_integration_flow_abort(
    ctx: HaOpsContext, flow_id: str
) -> dict[str, Any]:
    if not flow_id:
        return {"error": "flow_id is required"}

    try:
        await ctx.rest.delete(f"{_FLOW_BASE}/{flow_id}")
    except RestClientError as e:
        # A flow that is already finished/gone is not a failure worth
        # raising — the caller's goal ("this flow should not be pending")
        # is already true.
        if e.status == 404:
            return {
                "aborted": False,
                "flow_id": flow_id,
                "message": "No such open flow — nothing to discard.",
            }
        return {"error": f"Could not abort flow: {e}", "flow_id": flow_id}

    await ctx.audit.log(
        tool="integration_flow_abort",
        details={"flow_id": flow_id},
    )
    return {
        "aborted": True,
        "flow_id": flow_id,
        "message": "Pending flow discarded. No config entry was created.",
    }
