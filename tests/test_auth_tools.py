"""Tests for OAuth management tools — status and clear."""

from __future__ import annotations

from pathlib import Path

import pytest

from ha_ops_mcp.auth.provider import HaOpsOAuthProvider
from ha_ops_mcp.tools.auth import haops_auth_clear, haops_auth_status


@pytest.mark.asyncio
async def test_auth_status_disabled(ctx):
    """When auth is disabled, status reports that."""
    result = await haops_auth_status(ctx)
    assert result["enabled"] is False


@pytest.mark.asyncio
async def test_auth_status_enabled(ctx, tmp_path: Path):
    """When auth is enabled, status reports counts and metadata."""
    ctx.config.auth.enabled = True
    provider = HaOpsOAuthProvider(data_dir=tmp_path)
    ctx.auth_provider = provider

    # Register a client
    from mcp.shared.auth import OAuthClientInformationFull
    client = OAuthClientInformationFull(
        redirect_uris=["http://localhost:3000/callback"],
        client_name="test-client",
        token_endpoint_auth_method="none",
    )
    await provider.register_client(client)

    result = await haops_auth_status(ctx)
    assert result["enabled"] is True
    assert result["client_count"] == 1
    assert result["clients"][0]["client_name"] == "test-client"
    # Client ID should be masked
    assert "..." in result["clients"][0]["client_id"]


@pytest.mark.asyncio
async def test_auth_status_shows_tokens(ctx, tmp_path: Path):
    """Status shows token metadata (masked prefixes, TTLs, scopes)."""
    import base64
    import hashlib
    import secrets as _secrets

    from mcp.server.auth.provider import AuthorizationParams
    from mcp.shared.auth import OAuthClientInformationFull
    from pydantic import AnyUrl

    ctx.config.auth.enabled = True
    provider = HaOpsOAuthProvider(data_dir=tmp_path)
    ctx.auth_provider = provider

    client = OAuthClientInformationFull(
        redirect_uris=["http://localhost:3000/callback"],
        client_name="test",
        token_endpoint_auth_method="none",
    )
    await provider.register_client(client)

    verifier = _secrets.token_urlsafe(32)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()

    params = AuthorizationParams(
        state="s", scopes=None, code_challenge=challenge,
        redirect_uri=AnyUrl("http://localhost:3000/callback"),
        redirect_uri_provided_explicitly=True,
    )
    redirect = await provider.authorize(client, params)
    from urllib.parse import parse_qs, urlparse
    code = parse_qs(urlparse(redirect).query)["code"][0]
    auth_code = await provider.load_authorization_code(client, code)
    await provider.exchange_authorization_code(client, auth_code)

    result = await haops_auth_status(ctx)
    assert result["access_token_count"] == 1
    assert result["refresh_token_count"] == 1
    # Token prefix visible, not the full token
    assert "..." in result["access_tokens"][0]["token_prefix"]
    assert result["access_tokens"][0]["ttl_seconds"] is not None


@pytest.mark.asyncio
async def test_auth_clear_disabled(ctx):
    """Clear on disabled auth reports error."""
    result = await haops_auth_clear(ctx)
    assert "error" in result


@pytest.mark.asyncio
async def test_auth_clear_preview_and_execute(ctx, tmp_path: Path):
    """Two-phase clear: preview shows counts, confirm wipes state."""
    from mcp.shared.auth import OAuthClientInformationFull

    ctx.config.auth.enabled = True
    provider = HaOpsOAuthProvider(data_dir=tmp_path)
    ctx.auth_provider = provider

    client = OAuthClientInformationFull(
        redirect_uris=["http://localhost:3000/callback"],
        client_name="test",
        token_endpoint_auth_method="none",
    )
    await provider.register_client(client)

    # Preview
    preview = await haops_auth_clear(ctx)
    assert "token" in preview
    assert preview["preview"]["clients"] == 1

    # Confirm
    result = await haops_auth_clear(ctx, confirm=True, token=preview["token"])
    assert result["success"] is True
    assert result["cleared"]["clients"] == 1

    # Verify cleared
    status = await haops_auth_status(ctx)
    assert status["client_count"] == 0


@pytest.mark.asyncio
async def test_auth_clear_clients_only(ctx, tmp_path: Path):
    """clients_only=True clears registrations but keeps tokens."""
    import base64
    import hashlib
    import secrets as _secrets

    from mcp.server.auth.provider import AuthorizationParams
    from mcp.shared.auth import OAuthClientInformationFull
    from pydantic import AnyUrl

    ctx.config.auth.enabled = True
    provider = HaOpsOAuthProvider(data_dir=tmp_path)
    ctx.auth_provider = provider

    client = OAuthClientInformationFull(
        redirect_uris=["http://localhost:3000/callback"],
        client_name="test",
        token_endpoint_auth_method="none",
    )
    await provider.register_client(client)

    # Issue tokens
    verifier = _secrets.token_urlsafe(32)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    params = AuthorizationParams(
        state="s", scopes=None, code_challenge=challenge,
        redirect_uri=AnyUrl("http://localhost:3000/callback"),
        redirect_uri_provided_explicitly=True,
    )
    redirect = await provider.authorize(client, params)
    from urllib.parse import parse_qs, urlparse
    code = parse_qs(urlparse(redirect).query)["code"][0]
    auth_code = await provider.load_authorization_code(client, code)
    await provider.exchange_authorization_code(client, auth_code)

    # Clear clients only
    preview = await haops_auth_clear(ctx, clients_only=True)
    result = await haops_auth_clear(
        ctx, confirm=True, token=preview["token"], clients_only=True
    )
    assert result["success"] is True

    status = await haops_auth_status(ctx)
    assert status["client_count"] == 0
    # Tokens survive
    assert status["access_token_count"] == 1
