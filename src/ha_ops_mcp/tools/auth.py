"""OAuth management tool — status (read-only).

The previous `haops_auth_clear` write tool was removed in v0.33.7 because it
self-DoS'd: wiping the OAuth store kills the very session calling the tool, and
the only realistic use case (recover from a wedged auth state where MCP
dispatch is dead) can't be served from the MCP surface at all. Clearing the
OAuth store is now an admin action via the addon Configuration tab's
`auth_reset_marker` field — see run.sh.
"""

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


