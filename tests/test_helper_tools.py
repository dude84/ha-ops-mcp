"""Tests for haops_helper_* — collection-helper WS API wrapper."""

from __future__ import annotations

import json

import pytest

from ha_ops_mcp.connections.websocket import WebSocketError
from ha_ops_mcp.tools.helper import (
    HELPER_DOMAINS,
    _slugify,
    haops_helper_create,
    haops_helper_delete,
    haops_helper_list,
    haops_helper_update,
)


def _ws_router(table: dict[str, object]):
    """Build a side_effect callable for mock_ws.send_command from a {cmd: result} table.

    Lets each test declare per-command stubs without juggling call_args
    inspection. Unknown commands raise so unintended calls fail loudly.
    """
    async def _send(cmd: str, **_: object):
        if cmd not in table:
            raise AssertionError(f"unexpected WS command: {cmd}")
        value = table[cmd]
        if isinstance(value, Exception):
            raise value
        return value
    return _send


def _seed_registry_helper(config_dir, entity_id: str, unique_id: str) -> None:
    """Append a collection-helper entry to .storage/core.entity_registry."""
    path = config_dir / ".storage" / "core.entity_registry"
    data = json.loads(path.read_text())
    domain = entity_id.split(".", 1)[0]
    data["data"]["entities"].append({
        "entity_id": entity_id,
        "name": None,
        "original_name": None,
        "platform": domain,
        "unique_id": unique_id,
        "area_id": None,
        "device_id": None,
        "disabled_by": None,
    })
    path.write_text(json.dumps(data))


def test_slugify_matches_ha_object_id_rules():
    assert _slugify("Foo Bar") == "foo_bar"
    assert _slugify("My-Helper / 2") == "my_helper_2"
    assert _slugify("__edge__") == "edge"


@pytest.mark.asyncio
async def test_helper_list_all_domains(ctx, mock_ws):
    mock_ws.send_command.side_effect = _ws_router({
        f"{d}/list": [] for d in HELPER_DOMAINS
    })
    result = await haops_helper_list(ctx)
    assert "domains" in result
    assert set(result["domains"].keys()) == set(HELPER_DOMAINS)
    assert result["count"] == 0


@pytest.mark.asyncio
async def test_helper_list_single_domain(ctx, mock_ws):
    mock_ws.send_command.side_effect = _ws_router({
        "input_boolean/list": [
            {"id": "abc123", "name": "Foo Bar", "icon": "mdi:flash"}
        ],
    })
    result = await haops_helper_list(ctx, domain="input_boolean")
    assert list(result["domains"].keys()) == ["input_boolean"]
    assert result["count"] == 1


@pytest.mark.asyncio
async def test_helper_list_rejects_unsupported_domain(ctx, mock_ws):
    result = await haops_helper_list(ctx, domain="binary_sensor")
    assert "error" in result
    assert "supported" in result


@pytest.mark.asyncio
async def test_helper_create_preview_returns_token_and_payload(ctx, mock_ws):
    result = await haops_helper_create(
        ctx,
        domain="input_boolean",
        name="My Bool",
        attributes={"icon": "mdi:flash", "initial": False},
    )
    assert "token" in result
    assert result["preview"]["domain"] == "input_boolean"
    assert result["preview"]["payload"]["name"] == "My Bool"
    assert result["preview"]["auto_entity_id"] == "input_boolean.my_bool"


@pytest.mark.asyncio
async def test_helper_create_confirm_calls_ws_and_audits(ctx, mock_ws):
    mock_ws.send_command.side_effect = _ws_router({
        "input_boolean/create": {
            "id": "abc123", "name": "My Bool", "icon": "mdi:flash",
        },
    })
    preview = await haops_helper_create(
        ctx, domain="input_boolean", name="My Bool",
    )
    result = await haops_helper_create(
        ctx,
        domain="input_boolean",
        name="My Bool",
        confirm=True,
        token=preview["token"],
    )
    assert result["success"] is True
    assert result["helper_id"] == "abc123"
    assert result["entity_id"] == "input_boolean.my_bool"


@pytest.mark.asyncio
async def test_helper_create_with_rename_calls_registry_update(
    ctx, mock_ws,
):
    mock_ws.send_command.side_effect = _ws_router({
        "input_boolean/create": {"id": "abc123", "name": "My Bool"},
        "config/entity_registry/update": {},
    })
    preview = await haops_helper_create(
        ctx,
        domain="input_boolean",
        name="My Bool",
        entity_id="input_boolean.custom_id",
    )
    result = await haops_helper_create(
        ctx,
        domain="input_boolean",
        name="My Bool",
        entity_id="input_boolean.custom_id",
        confirm=True,
        token=preview["token"],
    )
    assert result["success"] is True
    assert result["entity_id"] == "input_boolean.custom_id"
    rename_calls = [
        c for c in mock_ws.send_command.await_args_list
        if c.args and c.args[0] == "config/entity_registry/update"
    ]
    assert len(rename_calls) == 1


@pytest.mark.asyncio
async def test_helper_create_rename_failure_reports_but_keeps_helper(
    ctx, mock_ws,
):
    mock_ws.send_command.side_effect = _ws_router({
        "input_boolean/create": {"id": "abc123", "name": "My Bool"},
        "config/entity_registry/update": WebSocketError("not allowed"),
    })
    preview = await haops_helper_create(
        ctx,
        domain="input_boolean",
        name="My Bool",
        entity_id="input_boolean.custom_id",
    )
    result = await haops_helper_create(
        ctx,
        domain="input_boolean",
        name="My Bool",
        entity_id="input_boolean.custom_id",
        confirm=True,
        token=preview["token"],
    )
    assert result["success"] is False
    assert result["helper_id"] == "abc123"
    assert "rename_error" in result
    assert result["entity_id"] == "input_boolean.my_bool"


@pytest.mark.asyncio
async def test_helper_create_rejects_unsupported_domain(ctx, mock_ws):
    result = await haops_helper_create(
        ctx, domain="binary_sensor", name="x",
    )
    assert "error" in result


@pytest.mark.asyncio
async def test_helper_create_requires_name(ctx, mock_ws):
    result = await haops_helper_create(
        ctx, domain="input_boolean", name="",
    )
    assert "error" in result


@pytest.mark.asyncio
async def test_helper_create_confirm_requires_token(ctx, mock_ws):
    result = await haops_helper_create(
        ctx, domain="input_boolean", name="x", confirm=True,
    )
    assert "error" in result


@pytest.mark.asyncio
async def test_helper_create_ws_failure_returns_error(ctx, mock_ws):
    mock_ws.send_command.side_effect = _ws_router({
        "input_boolean/create": WebSocketError("validation failed"),
    })
    preview = await haops_helper_create(
        ctx, domain="input_boolean", name="My Bool",
    )
    result = await haops_helper_create(
        ctx, domain="input_boolean", name="My Bool",
        confirm=True, token=preview["token"],
    )
    assert "error" in result
    assert "validation failed" in result["error"]


@pytest.mark.asyncio
async def test_helper_update_preview_resolves_via_registry(
    ctx, mock_ws, config_dir,
):
    _seed_registry_helper(
        config_dir, "input_number.threshold", "uid_123",
    )
    mock_ws.send_command.side_effect = _ws_router({
        "input_number/list": [{
            "id": "uid_123", "name": "Threshold",
            "min": 0, "max": 100, "step": 1, "mode": "slider",
        }],
    })
    result = await haops_helper_update(
        ctx,
        entity_id="input_number.threshold",
        attributes={"max": 200},
    )
    assert "token" in result
    assert result["preview"]["new"]["max"] == 200
    assert result["preview"]["old"]["max"] == 100


@pytest.mark.asyncio
async def test_helper_update_confirm_calls_ws_with_domain_id_key(
    ctx, mock_ws, config_dir,
):
    _seed_registry_helper(
        config_dir, "input_boolean.foo", "uid_foo",
    )
    mock_ws.send_command.side_effect = _ws_router({
        "input_boolean/list": [{"id": "uid_foo", "name": "Foo"}],
        "input_boolean/update": {},
    })
    preview = await haops_helper_update(
        ctx, entity_id="input_boolean.foo", name="Foo Bar",
    )
    await haops_helper_update(
        ctx,
        entity_id="input_boolean.foo",
        name="Foo Bar",
        confirm=True,
        token=preview["token"],
    )
    update_calls = [
        c for c in mock_ws.send_command.await_args_list
        if c.args and c.args[0] == "input_boolean/update"
    ]
    assert len(update_calls) == 1
    kwargs = update_calls[0].kwargs
    assert kwargs.get("input_boolean_id") == "uid_foo"
    assert kwargs.get("name") == "Foo Bar"


@pytest.mark.asyncio
async def test_helper_update_unresolvable_returns_error(ctx, mock_ws):
    mock_ws.send_command.side_effect = _ws_router({
        "input_boolean/list": [],
    })
    result = await haops_helper_update(
        ctx, entity_id="input_boolean.unknown", name="x",
    )
    assert "error" in result


@pytest.mark.asyncio
async def test_helper_update_noop_skips_token(ctx, mock_ws, config_dir):
    _seed_registry_helper(
        config_dir, "input_boolean.foo", "uid_foo",
    )
    mock_ws.send_command.side_effect = _ws_router({
        "input_boolean/list": [{"id": "uid_foo", "name": "Foo"}],
    })
    result = await haops_helper_update(
        ctx, entity_id="input_boolean.foo",
    )
    assert "token" not in result
    assert "no-op" in result["message"].lower()


@pytest.mark.asyncio
async def test_helper_delete_preview_lists_targets(
    ctx, mock_ws, config_dir,
):
    _seed_registry_helper(
        config_dir, "input_boolean.foo", "uid_foo",
    )
    mock_ws.send_command.side_effect = _ws_router({
        "input_boolean/list": [
            {"id": "uid_foo", "name": "Foo", "icon": "mdi:flash"},
        ],
    })
    result = await haops_helper_delete(
        ctx, entity_ids=["input_boolean.foo"],
    )
    assert "token" in result
    assert len(result["preview"]) == 1
    assert result["preview"][0]["domain"] == "input_boolean"
    assert result["preview"][0]["helper_id"] == "uid_foo"


@pytest.mark.asyncio
async def test_helper_delete_confirm_calls_ws_delete(
    ctx, mock_ws, config_dir,
):
    _seed_registry_helper(
        config_dir, "input_boolean.foo", "uid_foo",
    )
    mock_ws.send_command.side_effect = _ws_router({
        "input_boolean/list": [{"id": "uid_foo", "name": "Foo"}],
        "input_boolean/delete": {},
    })
    preview = await haops_helper_delete(
        ctx, entity_ids=["input_boolean.foo"],
    )
    result = await haops_helper_delete(
        ctx,
        entity_ids=["input_boolean.foo"],
        confirm=True,
        token=preview["token"],
    )
    assert result["success"] is True
    assert result["deleted"] == ["input_boolean.foo"]
    delete_calls = [
        c for c in mock_ws.send_command.await_args_list
        if c.args and c.args[0] == "input_boolean/delete"
    ]
    assert len(delete_calls) == 1
    assert delete_calls[0].kwargs.get("input_boolean_id") == "uid_foo"


@pytest.mark.asyncio
async def test_helper_delete_reports_unresolvable(ctx, mock_ws):
    mock_ws.send_command.side_effect = _ws_router({
        "input_boolean/list": [],
    })
    result = await haops_helper_delete(
        ctx, entity_ids=["input_boolean.ghost"],
    )
    assert "input_boolean.ghost" in result["not_resolvable"]


@pytest.mark.asyncio
async def test_helper_delete_empty_returns_error(ctx):
    result = await haops_helper_delete(ctx, entity_ids=[])
    assert "error" in result
