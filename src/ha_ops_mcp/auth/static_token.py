"""Static pre-shared Bearer token auth for the HTTP transports.

Default auth mode since v0.62.0. Claude Code ~v2.1.234 (Aug 2026) enforces
OAuth 2.0's TLS requirement for token endpoints (RFC 6749 §3.2; localhost
exempt; claude-code#3320) — fresh OAuth setups against the addon's
plain-HTTP LAN endpoint can't meet it. A static token sent as
``Authorization: Bearer <token>`` conforms (no OAuth credential exchange
happens), suppresses the client's OAuth discovery entirely, works over plain
HTTP and raw-IP URLs, and needs no per-project re-authorization — so it
replaced OAuth as the default; OAuth remains available as an experimental
mode for HTTPS/localhost deployments.

The middleware protects every route EXCEPT the sidebar-panel surface
(``/ui`` and ``/api/ui/*``): the panel is served through HA ingress on the
same port, and ingress requests carry HA's own admin authentication. This
mirrors the OAuth mode's behaviour, where the SDK guarded only the MCP
endpoints and custom routes were ingress-gated.
"""

from __future__ import annotations

import hmac
import json
import logging
import secrets
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Path prefixes exempt from Bearer auth (HA-ingress-authenticated panel).
_EXEMPT_PREFIXES = ("/ui", "/api/ui/")

_TOKEN_FILENAME = "static_token"


def resolve_static_token(data_dir: Path, configured: str) -> tuple[str, str]:
    """Return (token, source) where source is "configured" | "persisted" | "generated".

    An explicitly configured token always wins and is not persisted (the
    addon Configuration is its source of truth). Otherwise reuse the token
    persisted from a previous boot, or generate one, persist it with 0600
    perms, and log it ONCE so the admin can copy it.
    """
    if configured:
        return configured.strip(), "configured"

    data_dir.mkdir(parents=True, exist_ok=True)
    token_path = data_dir / _TOKEN_FILENAME
    if token_path.exists():
        persisted = token_path.read_text(encoding="utf-8").strip()
        if persisted:
            return persisted, "persisted"

    token = secrets.token_urlsafe(32)
    token_path.write_text(token + "\n", encoding="utf-8")
    token_path.chmod(0o600)
    # Deliberate full-token log line: this is the one-time handshake with the
    # admin (addon logs are Supervisor-admin-gated). Subsequent boots reuse
    # the persisted token and log only its masked prefix.
    logger.warning(
        "Generated MCP Bearer token (copy it now — shown only on generation):\n"
        "    %s\n"
        "Connect with:\n"
        '    claude mcp add --transport http ha-ops http://<ha-address>:8901/mcp '
        '--header "Authorization: Bearer %s"\n'
        "Persisted to %s; set 'auth_token' in the addon Configuration to "
        "choose your own instead.",
        token,
        token,
        token_path,
    )
    return token, "generated"


def _unauthorized() -> tuple[list[tuple[bytes, bytes]], bytes]:
    body = json.dumps(
        {
            "error": "unauthorized",
            "hint": (
                "This server uses static Bearer token auth. Reconnect with: "
                "claude mcp add --transport http <name> <url> "
                '--header "Authorization: Bearer <token>". The token is in the '
                "addon Configuration (auth_token) or the addon log / "
                "<backup_dir>/auth/static_token."
            ),
        }
    ).encode()
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode()),
        # Advertise Bearer without OAuth resource metadata so compliant
        # clients don't attempt an OAuth discovery flow against us.
        (b"www-authenticate", b"Bearer"),
    ]
    return headers, body


class StaticTokenMiddleware:
    """Pure ASGI middleware: constant-time Bearer check on non-exempt paths."""

    def __init__(self, app: Any, token: str) -> None:
        self.app = app
        self._token = token.encode()

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path: str = scope.get("path", "")
        if path == "/ui" or any(path.startswith(p) for p in _EXEMPT_PREFIXES):
            await self.app(scope, receive, send)
            return

        provided = b""
        for name, value in scope.get("headers", []):
            if name == b"authorization":
                if value[:7].lower() == b"bearer ":
                    provided = value[7:].strip()
                break

        if provided and hmac.compare_digest(provided, self._token):
            await self.app(scope, receive, send)
            return

        headers, body = _unauthorized()
        await send(
            {"type": "http.response.start", "status": 401, "headers": headers}
        )
        await send({"type": "http.response.body", "body": body})
