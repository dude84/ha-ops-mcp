"""User-management tools — HA person/login users via the WS admin API.

Home Assistant's user accounts (the login accounts that show up under
Settings → People → Users) are managed exclusively through the
authenticated admin WebSocket API. There is no REST surface and no
storage file we should hand-edit: the auth subsystem owns
``.storage/auth`` and ``.storage/auth_provider.homeassistant`` and
hashes passwords with bcrypt — poking those files directly corrupts the
login state. So unlike most ha-ops tools, these are WS-only by design.

The relevant WS commands:
  * ``config/auth/list``     → all users
  * ``config/auth/create``   → create a user (returns {user: {...}})
  * ``config/auth/update``   → update name / group_ids / is_active / local_only
  * ``config/auth/delete``   → delete a user
  * ``config/auth_provider/homeassistant/create`` → attach a
        username+password login to a freshly-created user

Group membership is HA's permission model: the built-in group
``system-admin`` grants admin rights, ``system-users`` is a regular
(non-admin) user. The ``admin`` boolean on create/update is a
convenience that maps to one of those two group ids.

All mutating tools follow the standard ha-ops two-phase confirmation
flow (preview → token → confirm). Auth mutations are NOT rolled back
automatically: re-creating a deleted user gives it a new id and drops
its password, so an "undo" would be misleading. The persistent audit
log is the recovery surface here, not the rollback transaction.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ha_ops_mcp.connections.websocket import WebSocketError
from ha_ops_mcp.server import registry

if TYPE_CHECKING:
    from ha_ops_mcp.server import HaOpsContext


GROUP_ADMIN = "system-admin"
GROUP_USER = "system-users"

# Fields surfaced in list/preview output. The raw WS payload carries a
# few more (credentials, etc.) we deliberately drop.
_USER_SUMMARY_FIELDS: tuple[str, ...] = (
    "id",
    "name",
    "username",
    "is_owner",
    "is_active",
    "system_generated",
    "local_only",
    "group_ids",
)


def _summarize_user(user: dict[str, Any]) -> dict[str, Any]:
    """Project a WS user record down to the fields callers care about."""
    return {k: user.get(k) for k in _USER_SUMMARY_FIELDS if k in user}


def _group_ids_for(admin: bool) -> list[str]:
    """Map the ``admin`` convenience flag to HA's built-in group ids."""
    return [GROUP_ADMIN] if admin else [GROUP_USER]


async def _list_users(ctx: HaOpsContext) -> list[dict[str, Any]]:
    """Return the raw user records from the WS admin API.

    Empty list if HA returns a non-list shape (shouldn't happen on a
    healthy install).
    """
    result = await ctx.ws.send_command("config/auth/list")
    if isinstance(result, list):
        return result
    return []


async def _find_user(
    ctx: HaOpsContext, user_id: str
) -> dict[str, Any] | None:
    """Look up a single user by id, or None if not present."""
    for user in await _list_users(ctx):
        if user.get("id") == user_id:
            return user
    return None


@registry.tool(
    name="haops_user_list",
    description=(
        "List Home Assistant login users (Settings → People → Users). "
        "These are the accounts that can log into HA — distinct from "
        "'person' entities. Wraps the admin WebSocket command "
        "config/auth/list (WS-only; there is no REST or filesystem "
        "source for this — the auth store is bcrypt-hashed and must not "
        "be hand-edited). "
        "No parameters. "
        "Returns {users: [{id, name, username, is_owner, is_active, "
        "system_generated, local_only, group_ids}], count}. "
        "group_ids of ['system-admin'] means an admin user; "
        "['system-users'] means a regular user. is_owner marks the "
        "single owner account (cannot be deleted). system_generated "
        "users are HA-internal (e.g. Supervisor) — leave them alone. "
        "Read-only."
    ),
    params={},
)
async def haops_user_list(ctx: HaOpsContext) -> dict[str, Any]:
    try:
        users = await _list_users(ctx)
    except WebSocketError as e:
        return {"error": f"WS config/auth/list failed: {e}"}

    summaries = [_summarize_user(u) for u in users]
    return {"users": summaries, "count": len(summaries)}


@registry.tool(
    name="haops_user_create",
    description=(
        "Create a new Home Assistant login user. Two-phase: "
        "1) Call without confirm to preview the user + get a token. "
        "2) Call with confirm=true and the token to create. "
        "Wraps config/auth/create (and, when a password is given, "
        "config/auth_provider/homeassistant/create to attach a "
        "username+password login). WS-only. "
        "Parameters: name (string, required — display name), "
        "admin (bool, default false — true puts the user in the "
        "'system-admin' group with full admin rights, false uses "
        "'system-users' for a regular account), "
        "local_only (bool, default false — restrict login to the local "
        "network), "
        "password (string, optional — if set, a username+password login "
        "is created; the username defaults to the slug of name, override "
        "with the username param), "
        "username (string, optional — login username when a password is "
        "given; defaults to slugified name), "
        "confirm (bool, default false), token (string, if confirming). "
        "On success returns the new user's id, group_ids, and whether a "
        "password login was attached. NOTE: without a password the user "
        "exists but cannot log in until a login is attached in the UI. "
        "Mutating."
    ),
    params={
        "name": {
            "type": "string",
            "description": "Display name for the new user",
        },
        "admin": {
            "type": "boolean",
            "description": (
                "True → system-admin group (admin); "
                "false → system-users (regular)"
            ),
            "default": False,
        },
        "local_only": {
            "type": "boolean",
            "description": "Restrict login to the local network",
            "default": False,
        },
        "password": {
            "type": "string",
            "description": (
                "Optional — attach a username+password login on create"
            ),
        },
        "username": {
            "type": "string",
            "description": (
                "Login username when a password is set "
                "(defaults to slug of name)"
            ),
        },
        "confirm": {
            "type": "boolean",
            "description": "Execute creation",
            "default": False,
        },
        "token": {
            "type": "string",
            "description": "Confirmation token from preview step",
        },
    },
)
async def haops_user_create(
    ctx: HaOpsContext,
    name: str,
    admin: bool = False,
    local_only: bool = False,
    password: str | None = None,
    username: str | None = None,
    confirm: bool = False,
    token: str | None = None,
) -> dict[str, Any]:
    if not name:
        return {"error": "name is required"}

    group_ids = _group_ids_for(admin)
    has_password = bool(password)

    if not confirm:
        tk = ctx.safety.create_token(
            action="user_create",
            details={
                "name": name,
                "group_ids": group_ids,
                "local_only": local_only,
                "password": password,
                "username": username,
            },
        )
        return {
            "preview": {
                "name": name,
                "admin": admin,
                "group_ids": group_ids,
                "local_only": local_only,
                "password_login": has_password,
                # Never echo the password back in the preview.
                "username": username if has_password else None,
            },
            "token": tk.id,
            "message": (
                "Review the new user above. Call again with confirm=true "
                "and this token to create."
            ),
        }

    if token is None:
        return {"error": "confirm=true requires a token"}
    try:
        token_data = ctx.safety.validate_token(token)
    except Exception as e:
        return {"error": str(e)}

    details = token_data.details
    name = details["name"]
    group_ids = details["group_ids"]
    local_only = details["local_only"]
    password = details.get("password")
    username = details.get("username")
    has_password = bool(password)

    try:
        result = await ctx.ws.send_command(
            "config/auth/create",
            name=name,
            group_ids=group_ids,
            local_only=local_only,
        )
    except WebSocketError as e:
        await ctx.audit.log(
            tool="user_create",
            details={"name": name, "group_ids": group_ids},
            success=False,
            token_id=token,
            error=str(e),
        )
        return {"error": f"WS config/auth/create failed: {e}"}

    user = result.get("user") if isinstance(result, dict) else None
    user_id = user.get("id") if isinstance(user, dict) else None

    password_error: str | None = None
    if has_password and user_id:
        login_username = username or _slugify(name)
        try:
            await ctx.ws.send_command(
                "config/auth_provider/homeassistant/create",
                user_id=user_id,
                username=login_username,
                password=password,
            )
        except WebSocketError as e:
            password_error = str(e)

    ctx.safety.consume_token(token)

    audit_details: dict[str, Any] = {
        "user_id": user_id,
        "name": name,
        "group_ids": group_ids,
        "local_only": local_only,
        "password_login": has_password and password_error is None,
    }
    if password_error:
        audit_details["password_error"] = password_error
    await ctx.audit.log(
        tool="user_create",
        details=audit_details,
        token_id=token,
        success=password_error is None,
    )

    response: dict[str, Any] = {
        "success": password_error is None,
        "user_id": user_id,
        "name": name,
        "group_ids": group_ids,
        "local_only": local_only,
        "password_login": has_password and password_error is None,
    }
    if user:
        response["user"] = _summarize_user(user)
    if password_error:
        response["password_error"] = password_error
        response["message"] = (
            f"User {name!r} created (id={user_id}) but attaching the "
            f"password login failed: {password_error}"
        )
    return response


@registry.tool(
    name="haops_user_update",
    description=(
        "Update a Home Assistant login user. Two-phase: "
        "1) Call without confirm to preview old → new diff + get token. "
        "2) Call with confirm=true and the token to apply. "
        "Wraps config/auth/update. WS-only. This is the disable/enable "
        "lever (is_active) as well as the rename and admin/regular "
        "toggle. "
        "Parameters: user_id (string, required — from haops_user_list), "
        "name (string, optional — new display name), "
        "admin (bool, optional — true sets group to 'system-admin', "
        "false to 'system-users'; mutually exclusive with group_ids), "
        "group_ids (list of strings, optional — explicit group ids; "
        "overrides admin if both given), "
        "is_active (bool, optional — false disables the account "
        "(blocks login without deleting it), true re-enables it), "
        "local_only (bool, optional — restrict login to local network), "
        "confirm (bool, default false), token (string, if confirming). "
        "Only the fields you pass are changed; everything else is left "
        "as-is. Returns the updated user summary. "
        "Mutating."
    ),
    params={
        "user_id": {
            "type": "string",
            "description": "Target user id (from haops_user_list)",
        },
        "name": {
            "type": "string",
            "description": "New display name",
        },
        "admin": {
            "type": "boolean",
            "description": (
                "True → system-admin, false → system-users "
                "(ignored if group_ids is given)"
            ),
        },
        "group_ids": {
            "type": "array",
            "description": "Explicit group ids (overrides admin)",
        },
        "is_active": {
            "type": "boolean",
            "description": "False disables the account, true re-enables",
        },
        "local_only": {
            "type": "boolean",
            "description": "Restrict login to the local network",
        },
        "confirm": {
            "type": "boolean",
            "description": "Execute update",
            "default": False,
        },
        "token": {
            "type": "string",
            "description": "Confirmation token from preview step",
        },
    },
)
async def haops_user_update(
    ctx: HaOpsContext,
    user_id: str,
    name: str | None = None,
    admin: bool | None = None,
    group_ids: list[str] | None = None,
    is_active: bool | None = None,
    local_only: bool | None = None,
    confirm: bool = False,
    token: str | None = None,
) -> dict[str, Any]:
    if not user_id:
        return {"error": "user_id is required"}

    try:
        current = await _find_user(ctx, user_id)
    except WebSocketError as e:
        return {"error": f"WS config/auth/list failed: {e}"}
    if current is None:
        return {"error": f"User {user_id!r} not found"}

    # Resolve the effective group_ids: explicit group_ids wins, else the
    # admin flag, else leave unchanged.
    resolved_group_ids: list[str] | None
    if group_ids is not None:
        resolved_group_ids = group_ids
    elif admin is not None:
        resolved_group_ids = _group_ids_for(admin)
    else:
        resolved_group_ids = None

    # Build the change set — only fields the caller actually supplied.
    changes: dict[str, Any] = {}
    if name is not None and name != current.get("name"):
        changes["name"] = name
    if resolved_group_ids is not None \
            and resolved_group_ids != current.get("group_ids"):
        changes["group_ids"] = resolved_group_ids
    if is_active is not None and is_active != current.get("is_active"):
        changes["is_active"] = is_active
    if local_only is not None and local_only != current.get("local_only"):
        changes["local_only"] = local_only

    if not changes:
        return {
            "message": "Update is a no-op (no fields changed)",
            "current": _summarize_user(current),
        }

    if not confirm:
        tk = ctx.safety.create_token(
            action="user_update",
            details={"user_id": user_id, "changes": changes},
        )
        old = {k: current.get(k) for k in changes}
        return {
            "preview": {
                "user_id": user_id,
                "old": old,
                "new": changes,
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
    user_id = details["user_id"]
    changes = details["changes"]

    try:
        result = await ctx.ws.send_command(
            "config/auth/update",
            user_id=user_id,
            **changes,
        )
    except WebSocketError as e:
        await ctx.audit.log(
            tool="user_update",
            details={"user_id": user_id, "changes": changes},
            success=False,
            token_id=token,
            error=str(e),
        )
        return {"error": f"WS config/auth/update failed: {e}"}

    ctx.safety.consume_token(token)

    await ctx.audit.log(
        tool="user_update",
        details={"user_id": user_id, "changes": changes},
        token_id=token,
        success=True,
    )

    updated = result.get("user") if isinstance(result, dict) else None
    response: dict[str, Any] = {
        "success": True,
        "user_id": user_id,
        "changes": changes,
    }
    if isinstance(updated, dict):
        response["user"] = _summarize_user(updated)
    return response


@registry.tool(
    name="haops_user_delete",
    description=(
        "Delete a Home Assistant login user. DESTRUCTIVE. Two-phase: "
        "1) Call without confirm to preview the user + get a token. "
        "2) Call with confirm=true and the token to delete. "
        "Wraps config/auth/delete. WS-only. There is no automatic "
        "rollback — recreating a deleted user assigns a new id and "
        "drops its password; the audit log is the recovery record. "
        "The owner account cannot be deleted (the tool refuses). "
        "Consider haops_user_update with is_active=false to disable a "
        "user instead of deleting it. "
        "Parameters: user_id (string, required — from haops_user_list), "
        "confirm (bool, default false), token (string, if confirming). "
        "Destructive."
    ),
    params={
        "user_id": {
            "type": "string",
            "description": "Target user id (from haops_user_list)",
        },
        "confirm": {
            "type": "boolean",
            "description": "Execute deletion",
            "default": False,
        },
        "token": {
            "type": "string",
            "description": "Confirmation token from preview step",
        },
    },
)
async def haops_user_delete(
    ctx: HaOpsContext,
    user_id: str,
    confirm: bool = False,
    token: str | None = None,
) -> dict[str, Any]:
    if not user_id:
        return {"error": "user_id is required"}

    try:
        current = await _find_user(ctx, user_id)
    except WebSocketError as e:
        return {"error": f"WS config/auth/list failed: {e}"}
    if current is None:
        return {"error": f"User {user_id!r} not found"}

    if current.get("is_owner"):
        return {
            "error": (
                f"Refusing to delete user {user_id!r}: it is the owner "
                "account. The owner cannot be deleted."
            ),
        }

    if not confirm:
        tk = ctx.safety.create_token(
            action="user_delete",
            details={
                "user_id": user_id,
                "user": _summarize_user(current),
            },
        )
        return {
            "preview": {"user": _summarize_user(current)},
            "token": tk.id,
            "message": (
                "Review the user above. Call again with confirm=true "
                "and this token to delete. This cannot be undone."
            ),
        }

    if token is None:
        return {"error": "confirm=true requires a token"}
    try:
        token_data = ctx.safety.validate_token(token)
    except Exception as e:
        return {"error": str(e)}

    details = token_data.details
    user_id = details["user_id"]
    user_summary = details.get("user", {})

    # Persistent record on disk so the account details survive past the
    # MCP session even though the user itself can't be auto-restored.
    await ctx.backup.backup_entities(
        [{"entity_id": f"auth.user.{user_id}", **user_summary}],
        operation="user_delete",
    )

    try:
        await ctx.ws.send_command("config/auth/delete", user_id=user_id)
    except WebSocketError as e:
        await ctx.audit.log(
            tool="user_delete",
            details={"user_id": user_id, "user": user_summary},
            success=False,
            token_id=token,
            error=str(e),
        )
        return {"error": f"WS config/auth/delete failed: {e}"}

    ctx.safety.consume_token(token)

    await ctx.audit.log(
        tool="user_delete",
        details={"user_id": user_id, "user": user_summary},
        token_id=token,
        success=True,
    )

    return {
        "success": True,
        "user_id": user_id,
        "deleted": user_summary,
    }


def _slugify(name: str) -> str:
    """HA's friendly-name → slug derivation, simplified.

    Lowercases, replaces non-alphanumeric runs with ``_``, collapses
    repeats, and trims. Used to derive a default login username from a
    display name. Mirrors the helper in tools/helper.py.
    """
    out_chars: list[str] = []
    for ch in name.lower():
        out_chars.append(ch if ch.isalnum() else "_")
    s = "".join(out_chars)
    while "__" in s:
        s = s.replace("__", "_")
    return s.strip("_")
