"""Tests for haops_user_* — HA login-user WS admin API wrapper."""

from __future__ import annotations

import pytest

from ha_ops_mcp.connections.websocket import WebSocketError
from ha_ops_mcp.tools.user import (
    GROUP_ADMIN,
    GROUP_USER,
    _slugify,
    haops_user_create,
    haops_user_delete,
    haops_user_list,
    haops_user_update,
)


def _ws_router(table: dict[str, object]):
    """Build a side_effect callable for mock_ws.send_command from a {cmd: result} table.

    Unknown commands raise so unintended calls fail loudly. Exception
    values in the table are raised to simulate WS failures.
    """
    async def _send(cmd: str, **_: object):
        if cmd not in table:
            raise AssertionError(f"unexpected WS command: {cmd}")
        value = table[cmd]
        if isinstance(value, Exception):
            raise value
        return value
    return _send


# Sample user records as HA's config/auth/list returns them.
_OWNER = {
    "id": "owner_id",
    "name": "Owner",
    "is_owner": True,
    "is_active": True,
    "system_generated": False,
    "local_only": False,
    "group_ids": [GROUP_ADMIN],
}
_REGULAR = {
    "id": "user_id_1",
    "name": "Alice",
    "is_owner": False,
    "is_active": True,
    "system_generated": False,
    "local_only": False,
    "group_ids": [GROUP_USER],
}


def test_slugify_default_username_rules():
    assert _slugify("John Doe") == "john_doe"
    assert _slugify("Mary-Jane / 2") == "mary_jane_2"


# ── list ──

@pytest.mark.asyncio
async def test_user_list_returns_summaries_and_count(ctx, mock_ws):
    mock_ws.send_command.side_effect = _ws_router({
        "config/auth/list": [_OWNER, _REGULAR],
    })
    result = await haops_user_list(ctx)
    assert result["count"] == 2
    assert result["users"][0]["id"] == "owner_id"
    assert result["users"][0]["is_owner"] is True
    assert result["users"][1]["group_ids"] == [GROUP_USER]


@pytest.mark.asyncio
async def test_user_list_ws_failure_returns_error(ctx, mock_ws):
    mock_ws.send_command.side_effect = _ws_router({
        "config/auth/list": WebSocketError("nope"),
    })
    result = await haops_user_list(ctx)
    assert "error" in result


# ── create ──

@pytest.mark.asyncio
async def test_user_create_preview_returns_token(ctx, mock_ws):
    result = await haops_user_create(ctx, name="Bob", admin=True)
    assert "token" in result
    assert result["preview"]["name"] == "Bob"
    assert result["preview"]["group_ids"] == [GROUP_ADMIN]
    # No WS calls during preview.
    mock_ws.send_command.assert_not_awaited()


@pytest.mark.asyncio
async def test_user_create_preview_hides_password(ctx, mock_ws):
    result = await haops_user_create(
        ctx, name="Bob", password="s3cret",
    )
    # Password must never echo back in the preview payload.
    assert "s3cret" not in str(result["preview"])
    assert result["preview"]["password_login"] is True


@pytest.mark.asyncio
async def test_user_create_confirm_calls_ws_and_audits(ctx, mock_ws):
    mock_ws.send_command.side_effect = _ws_router({
        "config/auth/create": {"user": {**_REGULAR, "id": "new_id"}},
    })
    preview = await haops_user_create(ctx, name="Bob", admin=False)
    result = await haops_user_create(
        ctx, name="Bob", admin=False,
        confirm=True, token=preview["token"],
    )
    assert result["success"] is True
    assert result["user_id"] == "new_id"
    assert result["group_ids"] == [GROUP_USER]
    create_calls = [
        c for c in mock_ws.send_command.await_args_list
        if c.args and c.args[0] == "config/auth/create"
    ]
    assert len(create_calls) == 1
    assert create_calls[0].kwargs.get("group_ids") == [GROUP_USER]


@pytest.mark.asyncio
async def test_user_create_with_password_calls_provider(ctx, mock_ws):
    mock_ws.send_command.side_effect = _ws_router({
        "config/auth/create": {"user": {**_REGULAR, "id": "new_id"}},
        "config/auth_provider/homeassistant/create": {},
    })
    preview = await haops_user_create(
        ctx, name="Bob Smith", password="pw123",
    )
    result = await haops_user_create(
        ctx, name="Bob Smith", password="pw123",
        confirm=True, token=preview["token"],
    )
    assert result["success"] is True
    assert result["password_login"] is True
    pw_calls = [
        c for c in mock_ws.send_command.await_args_list
        if c.args
        and c.args[0] == "config/auth_provider/homeassistant/create"
    ]
    assert len(pw_calls) == 1
    # Username defaults to the slug of the display name.
    assert pw_calls[0].kwargs.get("username") == "bob_smith"
    assert pw_calls[0].kwargs.get("user_id") == "new_id"


@pytest.mark.asyncio
async def test_user_create_password_failure_keeps_user(ctx, mock_ws):
    mock_ws.send_command.side_effect = _ws_router({
        "config/auth/create": {"user": {**_REGULAR, "id": "new_id"}},
        "config/auth_provider/homeassistant/create": WebSocketError(
            "weak password"
        ),
    })
    preview = await haops_user_create(ctx, name="Bob", password="pw")
    result = await haops_user_create(
        ctx, name="Bob", password="pw",
        confirm=True, token=preview["token"],
    )
    assert result["success"] is False
    assert result["user_id"] == "new_id"
    assert "password_error" in result


@pytest.mark.asyncio
async def test_user_create_requires_name(ctx):
    result = await haops_user_create(ctx, name="")
    assert "error" in result


@pytest.mark.asyncio
async def test_user_create_confirm_requires_token(ctx):
    result = await haops_user_create(ctx, name="Bob", confirm=True)
    assert "error" in result


@pytest.mark.asyncio
async def test_user_create_ws_failure_returns_error(ctx, mock_ws):
    mock_ws.send_command.side_effect = _ws_router({
        "config/auth/create": WebSocketError("create failed"),
    })
    preview = await haops_user_create(ctx, name="Bob")
    result = await haops_user_create(
        ctx, name="Bob", confirm=True, token=preview["token"],
    )
    assert "error" in result
    assert "create failed" in result["error"]


# ── update ──

@pytest.mark.asyncio
async def test_user_update_preview_diff(ctx, mock_ws):
    mock_ws.send_command.side_effect = _ws_router({
        "config/auth/list": [_OWNER, _REGULAR],
    })
    result = await haops_user_update(
        ctx, user_id="user_id_1", is_active=False,
    )
    assert "token" in result
    assert result["preview"]["old"]["is_active"] is True
    assert result["preview"]["new"]["is_active"] is False


@pytest.mark.asyncio
async def test_user_update_admin_flag_maps_to_group(ctx, mock_ws):
    mock_ws.send_command.side_effect = _ws_router({
        "config/auth/list": [_OWNER, _REGULAR],
    })
    result = await haops_user_update(
        ctx, user_id="user_id_1", admin=True,
    )
    assert result["preview"]["new"]["group_ids"] == [GROUP_ADMIN]


@pytest.mark.asyncio
async def test_user_update_confirm_calls_ws(ctx, mock_ws):
    mock_ws.send_command.side_effect = _ws_router({
        "config/auth/list": [_OWNER, _REGULAR],
        "config/auth/update": {"user": {**_REGULAR, "is_active": False}},
    })
    preview = await haops_user_update(
        ctx, user_id="user_id_1", is_active=False,
    )
    result = await haops_user_update(
        ctx, user_id="user_id_1", is_active=False,
        confirm=True, token=preview["token"],
    )
    assert result["success"] is True
    update_calls = [
        c for c in mock_ws.send_command.await_args_list
        if c.args and c.args[0] == "config/auth/update"
    ]
    assert len(update_calls) == 1
    assert update_calls[0].kwargs.get("user_id") == "user_id_1"
    assert update_calls[0].kwargs.get("is_active") is False


@pytest.mark.asyncio
async def test_user_update_unknown_user_returns_error(ctx, mock_ws):
    mock_ws.send_command.side_effect = _ws_router({
        "config/auth/list": [_OWNER, _REGULAR],
    })
    result = await haops_user_update(
        ctx, user_id="ghost", name="x",
    )
    assert "error" in result


@pytest.mark.asyncio
async def test_user_update_noop_skips_token(ctx, mock_ws):
    mock_ws.send_command.side_effect = _ws_router({
        "config/auth/list": [_OWNER, _REGULAR],
    })
    result = await haops_user_update(
        ctx, user_id="user_id_1", name="Alice",  # unchanged
    )
    assert "token" not in result
    assert "no-op" in result["message"].lower()


# ── delete ──

@pytest.mark.asyncio
async def test_user_delete_preview_returns_token(ctx, mock_ws):
    mock_ws.send_command.side_effect = _ws_router({
        "config/auth/list": [_OWNER, _REGULAR],
    })
    result = await haops_user_delete(ctx, user_id="user_id_1")
    assert "token" in result
    assert result["preview"]["user"]["id"] == "user_id_1"


@pytest.mark.asyncio
async def test_user_delete_confirm_calls_ws(ctx, mock_ws):
    mock_ws.send_command.side_effect = _ws_router({
        "config/auth/list": [_OWNER, _REGULAR],
        "config/auth/delete": {},
    })
    preview = await haops_user_delete(ctx, user_id="user_id_1")
    result = await haops_user_delete(
        ctx, user_id="user_id_1",
        confirm=True, token=preview["token"],
    )
    assert result["success"] is True
    assert result["user_id"] == "user_id_1"
    delete_calls = [
        c for c in mock_ws.send_command.await_args_list
        if c.args and c.args[0] == "config/auth/delete"
    ]
    assert len(delete_calls) == 1
    assert delete_calls[0].kwargs.get("user_id") == "user_id_1"


@pytest.mark.asyncio
async def test_user_delete_refuses_owner(ctx, mock_ws):
    mock_ws.send_command.side_effect = _ws_router({
        "config/auth/list": [_OWNER, _REGULAR],
    })
    result = await haops_user_delete(ctx, user_id="owner_id")
    assert "error" in result
    assert "owner" in result["error"].lower()
    # No token issued, no delete attempted.
    assert "token" not in result


@pytest.mark.asyncio
async def test_user_delete_unknown_user_returns_error(ctx, mock_ws):
    mock_ws.send_command.side_effect = _ws_router({
        "config/auth/list": [_OWNER, _REGULAR],
    })
    result = await haops_user_delete(ctx, user_id="ghost")
    assert "error" in result


@pytest.mark.asyncio
async def test_user_delete_confirm_requires_token(ctx, mock_ws):
    mock_ws.send_command.side_effect = _ws_router({
        "config/auth/list": [_OWNER, _REGULAR],
    })
    result = await haops_user_delete(
        ctx, user_id="user_id_1", confirm=True,
    )
    assert "error" in result
