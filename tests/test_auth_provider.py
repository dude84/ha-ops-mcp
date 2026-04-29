"""Tests for OAuth provider."""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from pathlib import Path

import pytest
from pydantic import AnyUrl

from ha_ops_mcp.auth.provider import HaOpsOAuthProvider

# ── Helpers ──────────────────────────────────────────────────────────


def _make_client_info() -> dict:
    from mcp.shared.auth import OAuthClientInformationFull
    return OAuthClientInformationFull(
        redirect_uris=["http://localhost:3000/callback"],
        client_name="test-client",
        token_endpoint_auth_method="none",
    )


def _pkce_pair() -> tuple[str, str]:
    """Generate a PKCE code_verifier and code_challenge (S256)."""
    verifier = secrets.token_urlsafe(32)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _make_auth_params(challenge: str, state: str = "test-state") -> object:
    from mcp.server.auth.provider import AuthorizationParams
    return AuthorizationParams(
        state=state,
        scopes=None,
        code_challenge=challenge,
        redirect_uri=AnyUrl("http://localhost:3000/callback"),
        redirect_uri_provided_explicitly=True,
    )


# ── Tests ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_register_and_get_client(tmp_path: Path):
    provider = HaOpsOAuthProvider(data_dir=tmp_path)
    client_info = _make_client_info()

    await provider.register_client(client_info)
    assert client_info.client_id is not None

    loaded = await provider.get_client(client_info.client_id)
    assert loaded is not None
    assert loaded.client_name == "test-client"


@pytest.mark.asyncio
async def test_get_client_not_found(tmp_path: Path):
    provider = HaOpsOAuthProvider(data_dir=tmp_path)
    assert await provider.get_client("nonexistent") is None


@pytest.mark.asyncio
async def test_authorize_returns_redirect_url(tmp_path: Path):
    provider = HaOpsOAuthProvider(data_dir=tmp_path)
    client_info = _make_client_info()
    await provider.register_client(client_info)

    _, challenge = _pkce_pair()
    params = _make_auth_params(challenge)

    redirect = await provider.authorize(client_info, params)
    assert redirect.startswith("http://localhost:3000/callback?")
    assert "code=" in redirect
    assert "state=test-state" in redirect


@pytest.mark.asyncio
async def test_full_authorization_code_flow(tmp_path: Path):
    """Register → authorize → exchange code → get tokens."""
    provider = HaOpsOAuthProvider(data_dir=tmp_path)
    client_info = _make_client_info()
    await provider.register_client(client_info)

    verifier, challenge = _pkce_pair()
    params = _make_auth_params(challenge)

    redirect = await provider.authorize(client_info, params)
    # Extract code from redirect URL
    from urllib.parse import parse_qs, urlparse
    parsed = urlparse(redirect)
    code = parse_qs(parsed.query)["code"][0]

    # Load the code
    auth_code = await provider.load_authorization_code(client_info, code)
    assert auth_code is not None
    assert auth_code.code == code

    # Exchange for tokens — provider doesn't verify PKCE itself,
    # the SDK handler does. Provider just issues tokens.
    token = await provider.exchange_authorization_code(client_info, auth_code)
    assert token.access_token
    assert token.refresh_token
    assert token.token_type == "Bearer"
    assert token.expires_in == 3600

    # Code is now consumed — can't load again
    assert await provider.load_authorization_code(client_info, code) is None


@pytest.mark.asyncio
async def test_access_token_verification(tmp_path: Path):
    """Issued access token can be loaded and verified."""
    provider = HaOpsOAuthProvider(data_dir=tmp_path)
    client_info = _make_client_info()
    await provider.register_client(client_info)

    verifier, challenge = _pkce_pair()
    params = _make_auth_params(challenge)
    redirect = await provider.authorize(client_info, params)

    from urllib.parse import parse_qs, urlparse
    code = parse_qs(urlparse(redirect).query)["code"][0]
    auth_code = await provider.load_authorization_code(client_info, code)
    token = await provider.exchange_authorization_code(client_info, auth_code)

    # Verify the access token
    access = await provider.load_access_token(token.access_token)
    assert access is not None
    assert access.client_id == client_info.client_id


@pytest.mark.asyncio
async def test_expired_access_token_returns_none(tmp_path: Path):
    provider = HaOpsOAuthProvider(data_dir=tmp_path, access_token_ttl=0)
    client_info = _make_client_info()
    await provider.register_client(client_info)

    verifier, challenge = _pkce_pair()
    params = _make_auth_params(challenge)
    redirect = await provider.authorize(client_info, params)

    from urllib.parse import parse_qs, urlparse
    code = parse_qs(urlparse(redirect).query)["code"][0]
    auth_code = await provider.load_authorization_code(client_info, code)
    token = await provider.exchange_authorization_code(client_info, auth_code)

    # Token should be expired immediately (TTL=0)
    assert await provider.load_access_token(token.access_token) is None


@pytest.mark.asyncio
async def test_refresh_token_rotation(tmp_path: Path):
    provider = HaOpsOAuthProvider(data_dir=tmp_path)
    client_info = _make_client_info()
    await provider.register_client(client_info)

    verifier, challenge = _pkce_pair()
    params = _make_auth_params(challenge)
    redirect = await provider.authorize(client_info, params)

    from urllib.parse import parse_qs, urlparse
    code = parse_qs(urlparse(redirect).query)["code"][0]
    auth_code = await provider.load_authorization_code(client_info, code)
    token = await provider.exchange_authorization_code(client_info, auth_code)

    # Load refresh token
    refresh = await provider.load_refresh_token(client_info, token.refresh_token)
    assert refresh is not None

    # Exchange for new pair
    new_token = await provider.exchange_refresh_token(client_info, refresh, [])
    assert new_token.access_token != token.access_token
    assert new_token.refresh_token != token.refresh_token

    # Old refresh token is gone
    assert await provider.load_refresh_token(client_info, token.refresh_token) is None
    # Old access token is gone
    assert await provider.load_access_token(token.access_token) is None
    # New access token works
    assert await provider.load_access_token(new_token.access_token) is not None


@pytest.mark.asyncio
async def test_revoke_token(tmp_path: Path):
    provider = HaOpsOAuthProvider(data_dir=tmp_path)
    client_info = _make_client_info()
    await provider.register_client(client_info)

    verifier, challenge = _pkce_pair()
    params = _make_auth_params(challenge)
    redirect = await provider.authorize(client_info, params)

    from urllib.parse import parse_qs, urlparse
    code = parse_qs(urlparse(redirect).query)["code"][0]
    auth_code = await provider.load_authorization_code(client_info, code)
    token = await provider.exchange_authorization_code(client_info, auth_code)

    # Revoke the access token
    access = await provider.load_access_token(token.access_token)
    assert access is not None
    await provider.revoke_token(access)

    # Both access and refresh should be gone
    assert await provider.load_access_token(token.access_token) is None
    assert await provider.load_refresh_token(client_info, token.refresh_token) is None


@pytest.mark.asyncio
async def test_expired_auth_code_returns_none(tmp_path: Path):
    provider = HaOpsOAuthProvider(data_dir=tmp_path)
    client_info = _make_client_info()
    await provider.register_client(client_info)

    verifier, challenge = _pkce_pair()
    params = _make_auth_params(challenge)
    redirect = await provider.authorize(client_info, params)

    from urllib.parse import parse_qs, urlparse
    code = parse_qs(urlparse(redirect).query)["code"][0]

    # Manually expire the code
    provider._store.auth_codes[code]["expires_at"] = time.time() - 10
    provider._store.save()

    assert await provider.load_authorization_code(client_info, code) is None


@pytest.mark.asyncio
async def test_persistence_survives_reload(tmp_path: Path):
    """Tokens survive provider restart (persistence test)."""
    provider = HaOpsOAuthProvider(data_dir=tmp_path)
    client_info = _make_client_info()
    await provider.register_client(client_info)

    verifier, challenge = _pkce_pair()
    params = _make_auth_params(challenge)
    redirect = await provider.authorize(client_info, params)

    from urllib.parse import parse_qs, urlparse
    code = parse_qs(urlparse(redirect).query)["code"][0]
    auth_code = await provider.load_authorization_code(client_info, code)
    token = await provider.exchange_authorization_code(client_info, auth_code)

    # Create a new provider instance (simulates restart)
    provider2 = HaOpsOAuthProvider(data_dir=tmp_path)

    # Client survived
    client = await provider2.get_client(client_info.client_id)
    assert client is not None

    # Access token survived
    access = await provider2.load_access_token(token.access_token)
    assert access is not None
