"""OAuth management tools — status and clear."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from ha_ops_mcp.server import registry

if TYPE_CHECKING:
    from ha_ops_mcp.server import HaOpsContext


def _ts_to_iso(ts: float | int | None) -> str | None:
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=UTC).isoformat()


def _mask_token(token: str) -> str:
    """Show first 8 chars + length hint, hide the rest."""
    if len(token) <= 12:
        return token[:4] + "..."
    return token[:8] + f"...({len(token)} chars)"


@registry.tool(
    name="haops_auth_status",
    description=(
        "Show OAuth authentication status and metadata. "
        "Reports whether OAuth is enabled, registered clients (id, name, "
        "registered_at), active access tokens (client_id, scopes, expires_at, "
        "masked token prefix), active refresh tokens (client_id, scopes, "
        "expires_at), and pending authorization codes. "
        "Token values are masked — only the first 8 characters are shown. "
        "Read-only, no parameters."
    ),
)
async def haops_auth_status(ctx: HaOpsContext) -> dict[str, Any]:
    if not ctx.config.auth.enabled or ctx.auth_provider is None:
        return {
            "enabled": False,
            "message": "OAuth is disabled. Set auth.enabled=true to enable.",
        }

    store = ctx.auth_provider._store
    now = time.time()

    clients: list[dict[str, Any]] = []
    for cid, data in store.clients.items():
        clients.append({
            "client_id": _mask_token(cid),
            "client_name": data.get("client_name"),
            "registered_at": _ts_to_iso(data.get("client_id_issued_at")),
            "redirect_uris": data.get("redirect_uris"),
        })

    access_tokens: list[dict[str, Any]] = []
    for tok, data in store.access_tokens.items():
        expires_at = data.get("expires_at")
        access_tokens.append({
            "token_prefix": _mask_token(tok),
            "client_id": _mask_token(data.get("client_id", "")),
            "scopes": data.get("scopes", []),
            "expires_at": _ts_to_iso(expires_at),
            "expired": expires_at is not None and expires_at < now,
            "ttl_seconds": max(0, int(expires_at - now)) if expires_at else None,
        })

    refresh_tokens: list[dict[str, Any]] = []
    for tok, data in store.refresh_tokens.items():
        expires_at = data.get("expires_at")
        refresh_tokens.append({
            "token_prefix": _mask_token(tok),
            "client_id": _mask_token(data.get("client_id", "")),
            "scopes": data.get("scopes", []),
            "expires_at": _ts_to_iso(expires_at),
            "expired": expires_at is not None and expires_at < now,
        })

    pending_codes = len(store.auth_codes)

    return {
        "enabled": True,
        "data_dir": ctx.config.auth.data_dir,
        "access_token_ttl": ctx.config.auth.access_token_ttl,
        "refresh_token_ttl": ctx.config.auth.refresh_token_ttl,
        "clients": clients,
        "client_count": len(clients),
        "access_tokens": access_tokens,
        "access_token_count": len(access_tokens),
        "refresh_tokens": refresh_tokens,
        "refresh_token_count": len(refresh_tokens),
        "pending_auth_codes": pending_codes,
    }


@registry.tool(
    name="haops_auth_clear",
    description=(
        "Clear OAuth state — registered clients, access tokens, refresh "
        "tokens, and authorization codes. Two-phase: call without confirm "
        "to preview what will be cleared. Call with confirm=true and the "
        "token to execute. All connected MCP clients will need to "
        "re-register and re-authenticate after this. "
        "Parameters: confirm (bool, default false), "
        "token (string, required if confirm=true), "
        "clients_only (bool, default false — clear only client "
        "registrations, not tokens)."
    ),
    params={
        "confirm": {
            "type": "boolean",
            "description": "Execute the clear",
            "default": False,
        },
        "token": {
            "type": "string",
            "description": "Confirmation token from preview step",
        },
        "clients_only": {
            "type": "boolean",
            "description": "Clear only client registrations (not tokens)",
            "default": False,
        },
    },
)
async def haops_auth_clear(
    ctx: HaOpsContext,
    confirm: bool = False,
    token: str | None = None,
    clients_only: bool = False,
) -> dict[str, Any]:
    if not ctx.config.auth.enabled or ctx.auth_provider is None:
        return {
            "error": "OAuth is disabled. Nothing to clear.",
        }

    store = ctx.auth_provider._store

    counts = {
        "clients": len(store.clients),
        "access_tokens": len(store.access_tokens),
        "refresh_tokens": len(store.refresh_tokens),
        "auth_codes": len(store.auth_codes),
    }

    if not confirm:
        tk = ctx.safety.create_token(
            action="auth_clear",
            details={"clients_only": clients_only, "counts": counts},
        )
        scope = "client registrations only" if clients_only else "all OAuth state"
        return {
            "preview": counts,
            "scope": scope,
            "token": tk.id,
            "message": f"Will clear {scope}. Call again with confirm=true and this token.",
        }

    if token is None:
        return {"error": "confirm=true requires a token"}

    try:
        ctx.safety.validate_token(token)
    except Exception as e:
        return {"error": str(e)}

    cleared = dict(counts)

    if clients_only:
        store.clients.clear()
    else:
        store.clients.clear()
        store.access_tokens.clear()
        store.refresh_tokens.clear()
        store.auth_codes.clear()
    store.save()

    ctx.safety.consume_token(token)

    await ctx.audit.log(
        tool="auth_clear",
        details={"cleared": cleared, "clients_only": clients_only},
        token_id=token,
    )

    return {
        "success": True,
        "cleared": cleared,
        "clients_only": clients_only,
        "message": "OAuth state cleared. Clients must re-register and re-authenticate.",
    }
