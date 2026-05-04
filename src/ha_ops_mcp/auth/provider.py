"""OAuth 2.0 Authorization Server provider for MCP transport.

Implements the OAuthAuthorizationServerProvider protocol from the MCP SDK.
Single-user/admin server — auto-approves authorization requests (no consent
UI). Client registrations and tokens persisted to a JSON file.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import time
from pathlib import Path
from urllib.parse import urlencode

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from ha_ops_mcp.auth.store import OAuthStore

logger = logging.getLogger(__name__)

# Defaults
AUTH_CODE_TTL = 300  # 5 minutes
DEFAULT_ACCESS_TTL = 86400  # 24 hours — single-user admin tool, sliding TTL extends on use
DEFAULT_REFRESH_TTL = 2592000  # 30 days


def _generate_token() -> str:
    """Generate a URL-safe token with 256 bits of entropy."""
    return secrets.token_urlsafe(32)


def _verify_pkce(code_verifier: str, code_challenge: str) -> bool:
    """Verify PKCE S256: SHA256(code_verifier) == code_challenge."""
    import base64
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return secrets.compare_digest(expected, code_challenge)


class HaOpsOAuthProvider:
    """OAuth 2.0 Authorization Server for ha-ops-mcp.

    Auto-approves all authorization requests — this is a single-user admin
    tool, not a multi-tenant service. The admin configured the server;
    there's no meaningful consent to collect.
    """

    def __init__(
        self,
        data_dir: Path,
        access_token_ttl: int = DEFAULT_ACCESS_TTL,
        refresh_token_ttl: int = DEFAULT_REFRESH_TTL,
    ) -> None:
        self._store = OAuthStore(data_dir / "oauth.json")
        self._access_ttl = access_token_ttl
        self._refresh_ttl = refresh_token_ttl

    # ── Client registration ──────────────────────────────────────────

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        raw = self._store.clients.get(client_id)
        if raw is None:
            logger.debug(
                "Client lookup miss: %s (known: %s)",
                client_id[:12] if client_id else "?",
                [k[:12] for k in self._store.clients],
            )
            return None
        return OAuthClientInformationFull(**raw)

    async def register_client(
        self, client_info: OAuthClientInformationFull
    ) -> None:
        # The MCP SDK's RegistrationHandler assigns client_id, secret, and
        # issued_at before calling us. For direct callers (tests) we fill
        # in client_id and timestamp, but NEVER invent a client_secret —
        # the SDK intentionally sets it to None when the client registers
        # with token_endpoint_auth_method="none", and assigning one would
        # break the ClientAuthenticator (it demands a secret if one exists).
        if not client_info.client_id:
            client_info.client_id = _generate_token()
        if not client_info.client_id_issued_at:
            client_info.client_id_issued_at = int(time.time())

        self._store.clients[client_info.client_id] = client_info.model_dump(
            mode="json", exclude_none=True
        )
        self._store.save()
        logger.info(
            "Registered OAuth client: %s (%s)",
            client_info.client_id[:12],
            client_info.client_name or "unnamed",
        )

    # ── Authorization ────────────────────────────────────────────────

    async def authorize(
        self,
        client: OAuthClientInformationFull,
        params: AuthorizationParams,
    ) -> str:
        """Auto-approve and redirect with authorization code."""
        code = _generate_token()
        now = time.time()

        self._store.auth_codes[code] = {
            "code": code,
            "client_id": client.client_id,
            "scopes": params.scopes or [],
            "code_challenge": params.code_challenge,
            "redirect_uri": str(params.redirect_uri),
            "redirect_uri_provided_explicitly": params.redirect_uri_provided_explicitly,
            "resource": params.resource,
            "expires_at": now + AUTH_CODE_TTL,
        }
        self._store.save()

        # Build redirect URL with code and state
        query: dict[str, str] = {"code": code}
        if params.state:
            query["state"] = params.state
        redirect = f"{params.redirect_uri}?{urlencode(query)}"
        return redirect

    # ── Authorization code exchange ──────────────────────────────────

    async def load_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: str,
    ) -> AuthorizationCode | None:
        raw = self._store.auth_codes.get(authorization_code)
        if raw is None:
            return None
        if raw.get("client_id") != client.client_id:
            return None
        if raw.get("expires_at", 0) < time.time():
            del self._store.auth_codes[authorization_code]
            self._store.save()
            return None
        return AuthorizationCode(**{
            k: v for k, v in raw.items()
            if k in AuthorizationCode.model_fields
        })

    async def exchange_authorization_code(
        self,
        client: OAuthClientInformationFull,
        authorization_code: AuthorizationCode,
    ) -> OAuthToken:
        now = time.time()

        # Delete the auth code (single-use)
        self._store.auth_codes.pop(authorization_code.code, None)

        # Generate tokens
        access = _generate_token()
        refresh = _generate_token()

        self._store.access_tokens[access] = {
            "token": access,
            "client_id": client.client_id,
            "scopes": authorization_code.scopes,
            "expires_at": int(now + self._access_ttl),
            "resource": authorization_code.resource,
        }
        self._store.refresh_tokens[refresh] = {
            "token": refresh,
            "client_id": client.client_id,
            "scopes": authorization_code.scopes,
            "expires_at": int(now + self._refresh_ttl) if self._refresh_ttl else None,
        }
        self._store.save()

        return OAuthToken(
            access_token=access,
            token_type="Bearer",
            expires_in=self._access_ttl,
            refresh_token=refresh,
            scope=" ".join(authorization_code.scopes) if authorization_code.scopes else None,
        )

    # ── Refresh token exchange ───────────────────────────────────────

    async def load_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: str,
    ) -> RefreshToken | None:
        raw = self._store.refresh_tokens.get(refresh_token)
        if raw is None:
            return None
        if raw.get("client_id") != client.client_id:
            return None
        exp = raw.get("expires_at")
        if exp is not None and exp < time.time():
            del self._store.refresh_tokens[refresh_token]
            self._store.save()
            return None
        return RefreshToken(**{
            k: v for k, v in raw.items()
            if k in RefreshToken.model_fields
        })

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        now = time.time()

        # Revoke old tokens
        self._store.refresh_tokens.pop(refresh_token.token, None)
        # Also revoke any access tokens for this client with the same scopes
        for tok, data in list(self._store.access_tokens.items()):
            if data.get("client_id") == client.client_id:
                del self._store.access_tokens[tok]

        # Issue new pair
        new_access = _generate_token()
        new_refresh = _generate_token()
        effective_scopes = scopes or refresh_token.scopes

        self._store.access_tokens[new_access] = {
            "token": new_access,
            "client_id": client.client_id,
            "scopes": effective_scopes,
            "expires_at": int(now + self._access_ttl),
        }
        self._store.refresh_tokens[new_refresh] = {
            "token": new_refresh,
            "client_id": client.client_id,
            "scopes": effective_scopes,
            "expires_at": int(now + self._refresh_ttl) if self._refresh_ttl else None,
        }
        self._store.save()

        logger.info(
            "Refresh-token exchange: client=%s expires_in=%ds",
            (client.client_id or "?")[:12],
            self._access_ttl,
        )

        return OAuthToken(
            access_token=new_access,
            token_type="Bearer",
            expires_in=self._access_ttl,
            refresh_token=new_refresh,
            scope=" ".join(effective_scopes) if effective_scopes else None,
        )

    # ── Token verification ───────────────────────────────────────────

    async def load_access_token(self, token: str) -> AccessToken | None:
        raw = self._store.access_tokens.get(token)
        if raw is None:
            return None
        exp = raw.get("expires_at")
        now = time.time()
        if exp is not None and exp < now:
            del self._store.access_tokens[token]
            self._store.save()
            return None

        # Sliding TTL: extend expires_at on each successful verification so
        # active sessions never time out. Throttle persistence — only rewrite
        # the store when remaining lifetime has dropped below half the window.
        if exp is not None and self._access_ttl > 0:
            remaining = exp - now
            if remaining < (self._access_ttl / 2):
                raw["expires_at"] = int(now + self._access_ttl)
                self._store.save()

        return AccessToken(**{
            k: v for k, v in raw.items()
            if k in AccessToken.model_fields
        })

    # ── Revocation ───────────────────────────────────────────────────

    async def revoke_token(
        self,
        token: AccessToken | RefreshToken,
    ) -> None:
        """Revoke a token and its counterpart."""
        client_id = token.client_id

        if isinstance(token, AccessToken):
            self._store.access_tokens.pop(token.token, None)
            # Also revoke refresh tokens for this client
            for tok, data in list(self._store.refresh_tokens.items()):
                if data.get("client_id") == client_id:
                    del self._store.refresh_tokens[tok]
        else:
            self._store.refresh_tokens.pop(token.token, None)
            # Also revoke access tokens for this client
            for tok, data in list(self._store.access_tokens.items()):
                if data.get("client_id") == client_id:
                    del self._store.access_tokens[tok]

        self._store.save()
