"""Helper tools — input_* / counter / timer / schedule via WS collection API.

Home Assistant input helpers (input_boolean, input_number, input_text,
input_select, input_datetime) plus counter, timer, and schedule are NOT
created by inserting rows into the entity registry. They live in HA's
collection-helper subsystem: each domain has its own .storage/<domain>
file plus four WebSocket commands — <domain>/{create,list,update,delete}.
The entity registry only mirrors what the collection subsystem exposes;
poking it directly leaves a dangling row with no integration backing it.

These tools wrap the WS collection API behind the standard ha-ops
two-phase confirmation flow. YAML-defined helpers (those declared in
configuration.yaml under input_boolean:, etc.) are read-only via this
API — HA returns a "not found" error on update/delete. Edit the YAML
file with haops_config_patch instead.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ha_ops_mcp.connections.websocket import WebSocketError
from ha_ops_mcp.safety.rollback import UndoEntry, UndoType
from ha_ops_mcp.server import registry

if TYPE_CHECKING:
    from ha_ops_mcp.server import HaOpsContext


HELPER_DOMAINS: tuple[str, ...] = (
    "input_boolean",
    "input_number",
    "input_text",
    "input_select",
    "input_datetime",
    "counter",
    "timer",
    "schedule",
)


def _domain_id_key(domain: str) -> str:
    """The kwarg name HA expects for update/delete payloads on <domain>."""
    return f"{domain}_id"


async def _list_domain(ctx: HaOpsContext, domain: str) -> list[dict[str, Any]]:
    """Return collection-storage entries for one helper domain.

    Empty list if HA returns a non-list shape (shouldn't happen on a
    healthy install). YAML-defined helpers do not appear here.
    """
    result = await ctx.ws.send_command(f"{domain}/list")
    if isinstance(result, list):
        return result
    return []


async def _resolve_entity(
    ctx: HaOpsContext, entity_id: str
) -> tuple[str, str, dict[str, Any]] | None:
    """Map entity_id → (domain, collection_id, current_payload).

    Cross-references the entity registry (where unique_id == collection
    id for these domains) with the WS collection list (which has the
    full editable payload). Returns None when the entity isn't a
    collection helper or isn't editable (e.g. YAML-defined).
    """
    if "." not in entity_id:
        return None
    domain = entity_id.split(".", 1)[0]
    if domain not in HELPER_DOMAINS:
        return None

    from ha_ops_mcp.tools.entity import _get_entity_registry
    entries = await _get_entity_registry(ctx)
    entry = next((e for e in entries if e.get("entity_id") == entity_id), None)
    unique_id = entry.get("unique_id") if entry else None

    items = await _list_domain(ctx, domain)
    # Match by unique_id when the registry knew about this entity. When
    # the registry lookup failed (fresh helper not yet indexed), fall
    # back to slug-of-name matching so newly-created helpers are still
    # resolvable.
    if isinstance(unique_id, str) and unique_id:
        match = next((it for it in items if it.get("id") == unique_id), None)
        if match is not None:
            return domain, unique_id, match

    object_id = entity_id.split(".", 1)[1]
    match = next(
        (it for it in items if _slugify(it.get("name", "")) == object_id),
        None,
    )
    if match is None:
        return None
    helper_id = match.get("id")
    if not isinstance(helper_id, str):
        return None
    return domain, helper_id, match


def _slugify(name: str) -> str:
    """HA's friendly-name → object_id derivation, simplified.

    HA uses `homeassistant.util.slugify` which lowercases, replaces
    non-alphanumeric runs with `_`, and trims leading/trailing `_`.
    Reproducing the relevant behaviour for collection lookup without
    pulling in the HA codebase.
    """
    out_chars: list[str] = []
    for ch in name.lower():
        if ch.isalnum():
            out_chars.append(ch)
        else:
            out_chars.append("_")
    s = "".join(out_chars)
    while "__" in s:
        s = s.replace("__", "_")
    return s.strip("_")


async def _rename_entity_id(
    ctx: HaOpsContext, current_entity_id: str, new_entity_id: str
) -> None:
    """Rename a registry entity_id via WS. Raises WebSocketError on failure."""
    await ctx.ws.send_command(
        "config/entity_registry/update",
        entity_id=current_entity_id,
        new_entity_id=new_entity_id,
    )


@registry.tool(
    name="haops_helper_list",
    description=(
        "List Home Assistant collection helpers (input_boolean, "
        "input_number, input_text, input_select, input_datetime, counter, "
        "timer, schedule). These are the helpers created via the UI "
        "(Settings → Devices & Services → Helpers) — they live in "
        ".storage/<domain>, NOT in the entity registry alone. "
        "YAML-defined helpers are not returned here; they appear as "
        "regular entities and are read-only via this API. "
        "Parameters: domain (string, optional — restrict to one domain; "
        "omit for all 8 collection-helper domains). "
        "Returns a dict keyed by domain with each helper's collection id, "
        "name, and full payload. Read-only."
    ),
    params={
        "domain": {
            "type": "string",
            "description": (
                "Optional — restrict to one of: input_boolean, "
                "input_number, input_text, input_select, input_datetime, "
                "counter, timer, schedule."
            ),
        },
    },
)
async def haops_helper_list(
    ctx: HaOpsContext, domain: str | None = None
) -> dict[str, Any]:
    if domain is not None and domain not in HELPER_DOMAINS:
        return {
            "error": f"Unsupported domain: {domain!r}",
            "supported": list(HELPER_DOMAINS),
        }

    domains = (domain,) if domain else HELPER_DOMAINS
    results: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for d in domains:
        try:
            results[d] = await _list_domain(ctx, d)
        except WebSocketError as e:
            errors[d] = str(e)

    response: dict[str, Any] = {"domains": results}
    if errors:
        response["errors"] = errors
    response["count"] = sum(len(v) for v in results.values())
    return response


@registry.tool(
    name="haops_helper_create",
    description=(
        "Create a new Home Assistant collection helper. Two-phase: "
        "1) Call without confirm to preview the payload + get a token. "
        "2) Call with confirm=true and the token to create. "
        "Wraps the WebSocket <domain>/create command — this is the "
        "ONLY supported way to create input_*, counter, timer, and "
        "schedule helpers via the API. Inserting into the entity "
        "registry directly does not work. "
        "Parameters: domain (string, required — input_boolean, "
        "input_number, input_text, input_select, input_datetime, "
        "counter, timer, schedule), name (string, required — friendly "
        "name; HA derives entity_id as <domain>.<slug(name)>), "
        "attributes (dict, optional — domain-specific fields, e.g. "
        "{min, max, step, mode, initial, unit_of_measurement} for "
        "input_number, {options, initial} for input_select, "
        "{has_date, has_time, initial} for input_datetime), "
        "entity_id (string, optional — desired final entity_id; if set "
        "and different from the auto-derived value, the helper is "
        "renamed via the entity registry after creation), "
        "confirm (bool, default false), token (string, if confirming). "
        "On success returns the assigned collection id and entity_id. "
        "Error responses preserve HA's WS error text so domain-specific "
        "validation failures (e.g. input_number missing min/max) are "
        "visible to the caller."
    ),
    params={
        "domain": {
            "type": "string",
            "description": (
                "One of: input_boolean, input_number, input_text, "
                "input_select, input_datetime, counter, timer, schedule"
            ),
        },
        "name": {
            "type": "string",
            "description": "Friendly name (entity_id derived from slug)",
        },
        "attributes": {
            "type": "object",
            "description": "Domain-specific helper config",
        },
        "entity_id": {
            "type": "string",
            "description": (
                "Optional — desired final entity_id. Helper is "
                "renamed via entity registry after creation if it "
                "differs from the auto-derived value."
            ),
        },
        "confirm": {
            "type": "boolean", "description": "Execute creation",
            "default": False,
        },
        "token": {
            "type": "string",
            "description": "Confirmation token from preview step",
        },
    },
)
async def haops_helper_create(
    ctx: HaOpsContext,
    domain: str,
    name: str,
    attributes: dict[str, Any] | None = None,
    entity_id: str | None = None,
    confirm: bool = False,
    token: str | None = None,
) -> dict[str, Any]:
    if domain not in HELPER_DOMAINS:
        return {
            "error": f"Unsupported domain: {domain!r}",
            "supported": list(HELPER_DOMAINS),
        }
    if not name:
        return {"error": "name is required"}

    payload: dict[str, Any] = {"name": name}
    if attributes:
        payload.update(attributes)

    derived_entity_id = f"{domain}.{_slugify(name)}"
    will_rename = bool(entity_id) and entity_id != derived_entity_id

    if not confirm:
        tk = ctx.safety.create_token(
            action="helper_create",
            details={
                "domain": domain,
                "payload": payload,
                "rename_to": entity_id if will_rename else None,
            },
        )
        return {
            "preview": {
                "domain": domain,
                "payload": payload,
                "auto_entity_id": derived_entity_id,
                "rename_to": entity_id if will_rename else None,
            },
            "token": tk.id,
            "message": (
                "Review the helper payload above. Call again with "
                "confirm=true and this token to create."
            ),
        }

    if token is None:
        return {"error": "confirm=true requires a token"}
    try:
        token_data = ctx.safety.validate_token(token)
    except Exception as e:
        return {"error": str(e)}

    details = token_data.details
    domain = details["domain"]
    payload = details["payload"]
    rename_to: str | None = details.get("rename_to")

    txn = ctx.rollback.begin("helper_create", token_id=token)
    try:
        result = await ctx.ws.send_command(f"{domain}/create", **payload)
    except WebSocketError as e:
        ctx.rollback.discard(txn.id)
        await ctx.audit.log(
            tool="helper_create",
            details={"domain": domain, "payload": payload},
            success=False,
            token_id=token,
            error=str(e),
        )
        return {"error": f"WS {domain}/create failed: {e}"}

    helper_id = result.get("id") if isinstance(result, dict) else None
    final_entity_id = derived_entity_id

    txn.savepoint(
        name=f"create:{domain}:{helper_id}",
        undo=UndoEntry(
            type=UndoType.ENTITY,
            description=f"Delete {domain} helper {helper_id}",
            data={
                "operation": "helper_delete",
                "domain": domain,
                "helper_id": helper_id,
            },
        ),
    )

    rename_error: str | None = None
    if rename_to and helper_id:
        try:
            await _rename_entity_id(ctx, derived_entity_id, rename_to)
            final_entity_id = rename_to
        except WebSocketError as e:
            rename_error = str(e)

    ctx.safety.consume_token(token)
    ctx.rollback.commit(txn.id)

    audit_details: dict[str, Any] = {
        "domain": domain,
        "helper_id": helper_id,
        "entity_id": final_entity_id,
        "payload": payload,
    }
    if rename_error:
        audit_details["rename_error"] = rename_error
    await ctx.audit.log(
        tool="helper_create",
        details=audit_details,
        token_id=token,
        success=rename_error is None,
    )

    response: dict[str, Any] = {
        "success": rename_error is None,
        "domain": domain,
        "helper_id": helper_id,
        "entity_id": final_entity_id,
        "transaction_id": txn.id,
        "result": result,
    }
    if rename_error:
        response["rename_error"] = rename_error
        response["message"] = (
            f"Helper created as {derived_entity_id} but rename to "
            f"{rename_to} failed: {rename_error}"
        )
    return response


@registry.tool(
    name="haops_helper_update",
    description=(
        "Update a Home Assistant collection helper. Two-phase: "
        "1) Call without confirm to preview old → new diff + get token. "
        "2) Call with confirm=true and the token to apply. "
        "Wraps the WebSocket <domain>/update command. The helper is "
        "identified by entity_id; the tool resolves it to the underlying "
        "collection id via the entity registry. "
        "YAML-defined helpers cannot be updated this way — HA returns "
        "a not-found error. Use haops_config_patch on the YAML file. "
        "Parameters: entity_id (string, required — e.g. "
        "'input_boolean.foo_bar'), attributes (dict, optional — fields "
        "to change; merged onto current values, so unspecified fields "
        "stay as-is), name (string, optional — sets the friendly name; "
        "does NOT change entity_id, use new_entity_id for that), "
        "new_entity_id (string, optional — rename via entity registry "
        "in the same transaction), confirm (bool, default false), "
        "token (string, if confirming)."
    ),
    params={
        "entity_id": {
            "type": "string",
            "description": (
                "Helper entity_id (e.g. 'input_boolean.foo_bar')"
            ),
        },
        "attributes": {
            "type": "object",
            "description": (
                "Domain-specific fields to change; merged with current"
            ),
        },
        "name": {
            "type": "string",
            "description": "New friendly name (does not rename entity_id)",
        },
        "new_entity_id": {
            "type": "string",
            "description": "New entity_id (renames via entity registry)",
        },
        "confirm": {
            "type": "boolean", "description": "Execute update",
            "default": False,
        },
        "token": {
            "type": "string",
            "description": "Confirmation token from preview step",
        },
    },
)
async def haops_helper_update(
    ctx: HaOpsContext,
    entity_id: str,
    attributes: dict[str, Any] | None = None,
    name: str | None = None,
    new_entity_id: str | None = None,
    confirm: bool = False,
    token: str | None = None,
) -> dict[str, Any]:
    resolved = await _resolve_entity(ctx, entity_id)
    if resolved is None:
        return {
            "error": (
                f"Could not resolve {entity_id!r} to a collection "
                "helper. Either it doesn't exist, isn't one of the "
                "supported domains, or is YAML-defined (read-only)."
            ),
        }
    domain, helper_id, current = resolved

    new_payload: dict[str, Any] = {
        k: v for k, v in current.items() if k != "id"
    }
    if attributes:
        new_payload.update(attributes)
    if name is not None:
        new_payload["name"] = name

    will_rename = bool(new_entity_id) and new_entity_id != entity_id

    if new_payload == {k: v for k, v in current.items() if k != "id"} \
            and not will_rename:
        return {
            "message": "Update is a no-op (no fields changed)",
            "current": current,
        }

    if not confirm:
        tk = ctx.safety.create_token(
            action="helper_update",
            details={
                "domain": domain,
                "helper_id": helper_id,
                "entity_id": entity_id,
                "old_payload": current,
                "new_payload": new_payload,
                "rename_to": new_entity_id if will_rename else None,
            },
        )
        return {
            "preview": {
                "domain": domain,
                "entity_id": entity_id,
                "old": {k: v for k, v in current.items() if k != "id"},
                "new": new_payload,
                "rename_to": new_entity_id if will_rename else None,
            },
            "token": tk.id,
            "message": (
                "Review the diff above. Call again with confirm=true "
                "and this token to apply."
            ),
        }

    if token is None:
        return {"error": "confirm=true requires a token"}
    try:
        token_data = ctx.safety.validate_token(token)
    except Exception as e:
        return {"error": str(e)}

    details = token_data.details
    domain = details["domain"]
    helper_id = details["helper_id"]
    old_payload = details["old_payload"]
    new_payload = details["new_payload"]
    rename_to = details.get("rename_to")
    current_entity_id = details["entity_id"]

    txn = ctx.rollback.begin("helper_update", token_id=token)
    txn.savepoint(
        name=f"update:{domain}:{helper_id}",
        undo=UndoEntry(
            type=UndoType.ENTITY,
            description=f"Restore {domain} helper {helper_id} to prior payload",
            data={
                "operation": "helper_update",
                "domain": domain,
                "helper_id": helper_id,
                "payload": {
                    k: v for k, v in old_payload.items() if k != "id"
                },
            },
        ),
    )

    try:
        result = await ctx.ws.send_command(
            f"{domain}/update",
            **{_domain_id_key(domain): helper_id, **new_payload},
        )
    except WebSocketError as e:
        ctx.rollback.discard(txn.id)
        await ctx.audit.log(
            tool="helper_update",
            details={
                "domain": domain,
                "helper_id": helper_id,
                "entity_id": current_entity_id,
                "new_payload": new_payload,
            },
            success=False,
            token_id=token,
            error=str(e),
        )
        return {"error": f"WS {domain}/update failed: {e}"}

    final_entity_id = current_entity_id
    rename_error: str | None = None
    if rename_to:
        try:
            await _rename_entity_id(ctx, current_entity_id, rename_to)
            final_entity_id = rename_to
        except WebSocketError as e:
            rename_error = str(e)

    ctx.safety.consume_token(token)
    ctx.rollback.commit(txn.id)

    audit_details: dict[str, Any] = {
        "domain": domain,
        "helper_id": helper_id,
        "entity_id": final_entity_id,
        "old_payload": {k: v for k, v in old_payload.items() if k != "id"},
        "new_payload": new_payload,
    }
    if rename_error:
        audit_details["rename_error"] = rename_error
    await ctx.audit.log(
        tool="helper_update",
        details=audit_details,
        token_id=token,
        success=rename_error is None,
    )

    response: dict[str, Any] = {
        "success": rename_error is None,
        "domain": domain,
        "helper_id": helper_id,
        "entity_id": final_entity_id,
        "transaction_id": txn.id,
        "result": result,
    }
    if rename_error:
        response["rename_error"] = rename_error
    return response


@registry.tool(
    name="haops_helper_delete",
    description=(
        "Delete one or more Home Assistant collection helpers. Two-phase: "
        "1) Call without confirm to preview the helpers and their full "
        "payloads (so an undo could re-create them) + get a token. "
        "2) Call with confirm=true and the token to delete. "
        "Wraps the WebSocket <domain>/delete command. "
        "YAML-defined helpers cannot be deleted via this API — remove "
        "them from configuration.yaml with haops_config_patch instead. "
        "Parameters: entity_ids (list of strings, required), "
        "confirm (bool, default false), token (string, if confirming). "
        "Each entity_id is resolved to its underlying collection id via "
        "the entity registry; entries that can't be resolved are reported "
        "in not_resolvable and skipped."
    ),
    params={
        "entity_ids": {
            "type": "array",
            "description": "Helper entity_ids to delete",
        },
        "confirm": {
            "type": "boolean", "description": "Execute deletion",
            "default": False,
        },
        "token": {
            "type": "string",
            "description": "Confirmation token from preview step",
        },
    },
)
async def haops_helper_delete(
    ctx: HaOpsContext,
    entity_ids: list[str],
    confirm: bool = False,
    token: str | None = None,
) -> dict[str, Any]:
    if not entity_ids:
        return {"error": "No entity_ids provided"}

    to_delete: list[dict[str, Any]] = []
    not_resolvable: list[str] = []
    for eid in entity_ids:
        resolved = await _resolve_entity(ctx, eid)
        if resolved is None:
            not_resolvable.append(eid)
            continue
        domain, helper_id, current = resolved
        to_delete.append({
            "entity_id": eid,
            "domain": domain,
            "helper_id": helper_id,
            "payload": current,
        })

    if not confirm:
        tk = ctx.safety.create_token(
            action="helper_delete",
            details={"targets": to_delete},
        )
        return {
            "preview": [
                {
                    "entity_id": t["entity_id"],
                    "domain": t["domain"],
                    "helper_id": t["helper_id"],
                    "payload": {
                        k: v for k, v in t["payload"].items() if k != "id"
                    },
                }
                for t in to_delete
            ],
            "not_resolvable": not_resolvable,
            "token": tk.id,
            "message": (
                "Review entities above. Call again with confirm=true "
                "and this token to delete."
            ),
        }

    if token is None:
        return {"error": "confirm=true requires a token"}
    try:
        token_data = ctx.safety.validate_token(token)
    except Exception as e:
        return {"error": str(e)}

    targets: list[dict[str, Any]] = token_data.details.get("targets", [])

    # Persistent backup — payloads on disk so the helper can be hand-
    # restored even after the MCP session ends.
    if targets:
        await ctx.backup.backup_entities(
            [{"entity_id": t["entity_id"], **t["payload"]} for t in targets],
            operation="helper_delete",
        )

    txn = ctx.rollback.begin("helper_delete", token_id=token)
    deleted: list[str] = []
    errors: list[dict[str, str]] = []
    for t in targets:
        domain = t["domain"]
        helper_id = t["helper_id"]
        eid = t["entity_id"]
        txn.savepoint(
            name=f"delete:{domain}:{helper_id}",
            undo=UndoEntry(
                type=UndoType.ENTITY,
                description=f"Recreate {domain} helper from {eid}",
                data={
                    "operation": "helper_create",
                    "domain": domain,
                    "payload": {
                        k: v for k, v in t["payload"].items() if k != "id"
                    },
                },
            ),
        )
        try:
            await ctx.ws.send_command(
                f"{domain}/delete",
                **{_domain_id_key(domain): helper_id},
            )
            deleted.append(eid)
        except WebSocketError as e:
            errors.append({"entity_id": eid, "error": str(e)})

    ctx.safety.consume_token(token)
    ctx.rollback.commit(txn.id)

    await ctx.audit.log(
        tool="helper_delete",
        details={
            "deleted": deleted,
            "errors": errors,
            "not_resolvable": not_resolvable,
        },
        success=not errors,
        token_id=token,
    )

    return {
        "success": not errors,
        "deleted": deleted,
        "errors": errors,
        "not_resolvable": not_resolvable,
        "transaction_id": txn.id,
    }
