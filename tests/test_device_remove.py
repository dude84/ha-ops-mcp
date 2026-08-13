"""haops_device_remove — the config-entry unlink path."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ha_ops_mcp.storage_registry import reset_write_clock
from ha_ops_mcp.tools.device import haops_device_remove


@pytest.fixture(autouse=True)
def _clean_clock():
    reset_write_clock()
    yield
    reset_write_clock()


def _seed(
    config_dir: Path,
    *,
    devices: list[dict] | None = None,
    entities: list[dict] | None = None,
) -> None:
    storage = config_dir / ".storage"
    storage.mkdir(exist_ok=True)
    (storage / "core.device_registry").write_text(
        json.dumps({"version": 1, "data": {"devices": devices or []}})
    )
    (storage / "core.entity_registry").write_text(
        json.dumps({"version": 1, "data": {"entities": entities or []}})
    )


DEVICE = {
    "id": "dev_strip",
    "name": "Office Strip",
    "manufacturer": "NOUS",
    "model": "A5T",
    "config_entries": ["entry_tasmota"],
}

ENTITIES = [
    {"entity_id": "switch.plug_office_strip_1", "device_id": "dev_strip"},
    {"entity_id": "switch.plug_office_strip_2", "device_id": "dev_strip"},
    {"entity_id": "sensor.other", "device_id": "dev_other"},
]

REMOVABLE_ENTRY = {
    "entry_id": "entry_tasmota",
    "domain": "tasmota",
    "title": "Tasmota",
    "state": "loaded",
    "supports_remove_device": True,
}


def _ws_router(entries: list[dict], devices_after: list[dict] | None = None):
    """Route WS commands the tool uses; default device list = removed."""

    async def _send(command: str, **kwargs):
        if command == "config_entries/get":
            return entries
        if command == "config/device_registry/list":
            return devices_after if devices_after is not None else []
        if command == "config/entity_registry/list":
            return ENTITIES
        if command == "config/device_registry/remove_config_entry":
            return {}
        raise AssertionError(f"unexpected WS command {command}")

    return _send


@pytest.mark.asyncio
async def test_preview_lists_entities_and_removability(
    ctx, config_dir, mock_ws
):
    _seed(config_dir, devices=[DEVICE], entities=ENTITIES)
    mock_ws.send_command.side_effect = _ws_router(
        [REMOVABLE_ENTRY], devices_after=[DEVICE]
    )

    result = await haops_device_remove(ctx, device="dev_strip")

    assert result["device"]["id"] == "dev_strip"
    assert set(result["entities_to_remove"]) == {
        "switch.plug_office_strip_1",
        "switch.plug_office_strip_2",
    }
    assert result["entity_count"] == 2
    assert result["will_unlink_entries"] == ["entry_tasmota"]
    assert "IRREVERSIBLE" in result["warning"]
    assert result["token"]
    # Discovery caveat must be stated — Tasmota comes back on the next announce.
    assert any("discovery" in c for c in result["caveats"])


@pytest.mark.asyncio
async def test_preview_refuses_when_no_entry_supports_removal(
    ctx, config_dir, mock_ws
):
    """ZHA: the config-entry path cannot delete the device — name the one that can."""
    device = {**DEVICE, "config_entries": ["entry_zha"]}
    _seed(config_dir, devices=[device], entities=[])
    mock_ws.send_command.side_effect = _ws_router(
        [{
            "entry_id": "entry_zha",
            "domain": "zha",
            "title": "ZHA",
            "state": "loaded",
            "supports_remove_device": False,
        }],
        devices_after=[device],
    )

    result = await haops_device_remove(ctx, device="dev_strip")

    assert "error" in result
    assert "token" not in result
    assert any("zha.remove" in alt for alt in result["alternatives"])


@pytest.mark.asyncio
async def test_apply_unlinks_every_entry_and_verifies_live(
    ctx, config_dir, mock_ws
):
    _seed(config_dir, devices=[DEVICE], entities=ENTITIES)
    mock_ws.send_command.side_effect = _ws_router(
        [REMOVABLE_ENTRY], devices_after=[DEVICE]
    )
    preview = await haops_device_remove(ctx, device="dev_strip")

    # After removal the live registry no longer has the device.
    calls: list[tuple[str, dict]] = []

    async def _send(command: str, **kwargs):
        calls.append((command, kwargs))
        if command == "config_entries/get":
            return [REMOVABLE_ENTRY]
        if command == "config/device_registry/list":
            # First call resolves the device, later calls verify removal.
            removes = sum(
                1
                for c, _ in calls
                if c == "config/device_registry/remove_config_entry"
            )
            return [] if removes else [DEVICE]
        return {}

    mock_ws.send_command.side_effect = _send

    result = await haops_device_remove(
        ctx, device="dev_strip", confirm=True, token=preview["token"]
    )

    assert result["success"] is True
    assert result["device_gone"] is True
    assert result["removed_from_entries"] == ["entry_tasmota"]
    assert result["entities_removed"] == [
        "switch.plug_office_strip_1",
        "switch.plug_office_strip_2",
    ]
    assert "Not available" in result["rollback"]
    removals = [
        kw
        for c, kw in calls
        if c == "config/device_registry/remove_config_entry"
    ]
    assert removals == [
        {"device_id": "dev_strip", "config_entry_id": "entry_tasmota"}
    ]


@pytest.mark.asyncio
async def test_apply_reports_device_that_survived(ctx, config_dir, mock_ws):
    """Unlinking one entry when another (unremovable) entry remains."""
    device = {**DEVICE, "config_entries": ["entry_tasmota", "entry_zha"]}
    entries = [
        REMOVABLE_ENTRY,
        {
            "entry_id": "entry_zha",
            "domain": "zha",
            "supports_remove_device": False,
        },
    ]
    _seed(config_dir, devices=[device], entities=[])
    mock_ws.send_command.side_effect = _ws_router(entries, devices_after=[device])

    preview = await haops_device_remove(ctx, device="dev_strip")
    survivor = {**device, "config_entries": ["entry_zha"]}
    mock_ws.send_command.side_effect = _ws_router(
        entries, devices_after=[survivor]
    )

    result = await haops_device_remove(
        ctx, device="dev_strip", confirm=True, token=preview["token"]
    )

    assert result["success"] is False
    assert result["device_gone"] is False
    assert result["remaining_entries"] == ["entry_zha"]
    assert "do not support device removal" in result["note"]


@pytest.mark.asyncio
async def test_token_is_bound_to_the_previewed_device(ctx, config_dir, mock_ws):
    """A token from one device must not remove a different one."""
    other = {
        "id": "dev_other",
        "name": "Office Strip Two",
        "config_entries": ["entry_tasmota"],
    }
    _seed(config_dir, devices=[DEVICE, other], entities=[])
    mock_ws.send_command.side_effect = _ws_router(
        [REMOVABLE_ENTRY], devices_after=[DEVICE, other]
    )
    preview = await haops_device_remove(ctx, device="dev_strip")

    result = await haops_device_remove(
        ctx, device="dev_other", confirm=True, token=preview["token"]
    )

    assert "error" in result
    assert "token was issued for dev_strip" in result["error"]


@pytest.mark.asyncio
async def test_ambiguous_name_asks_for_disambiguation(ctx, config_dir, mock_ws):
    devices = [DEVICE, {**DEVICE, "id": "dev_strip2"}]
    _seed(config_dir, devices=devices, entities=[])
    mock_ws.send_command.side_effect = _ws_router([], devices_after=devices)

    result = await haops_device_remove(ctx, device="Office Strip")

    assert "Multiple devices match" in result["error"]
    assert len(result["matches"]) == 2


@pytest.mark.asyncio
async def test_confirm_requires_a_token(ctx, config_dir, mock_ws):
    _seed(config_dir, devices=[DEVICE], entities=[])

    result = await haops_device_remove(ctx, device="dev_strip", confirm=True)

    assert result["error"] == "confirm=true requires a token"


@pytest.mark.asyncio
async def test_foreign_token_action_is_rejected(ctx, config_dir, mock_ws):
    _seed(config_dir, devices=[DEVICE], entities=[])
    token = ctx.safety.create_token(action="config_apply", details={})

    result = await haops_device_remove(
        ctx, device="dev_strip", confirm=True, token=token.id
    )

    assert "Token action mismatch" in result["error"]


@pytest.mark.asyncio
async def test_token_cannot_be_replayed(ctx, config_dir, mock_ws):
    _seed(config_dir, devices=[DEVICE], entities=[])
    mock_ws.send_command.side_effect = _ws_router(
        [REMOVABLE_ENTRY], devices_after=[DEVICE]
    )
    preview = await haops_device_remove(ctx, device="dev_strip")
    await haops_device_remove(
        ctx, device="dev_strip", confirm=True, token=preview["token"]
    )

    again = await haops_device_remove(
        ctx, device="dev_strip", confirm=True, token=preview["token"]
    )

    assert "error" in again
    assert "success" not in again


@pytest.mark.asyncio
async def test_removal_is_audited(ctx, config_dir, mock_ws):
    _seed(config_dir, devices=[DEVICE], entities=ENTITIES)
    mock_ws.send_command.side_effect = _ws_router(
        [REMOVABLE_ENTRY], devices_after=[DEVICE]
    )
    preview = await haops_device_remove(ctx, device="dev_strip")

    await haops_device_remove(
        ctx, device="dev_strip", confirm=True, token=preview["token"]
    )

    logged = ctx.audit.read_recent(limit=10)
    entry = next(e for e in logged if e["tool"] == "device_remove")
    assert entry["details"]["device_id"] == "dev_strip"
    assert entry["details"]["entities_removed"] == [
        "switch.plug_office_strip_1",
        "switch.plug_office_strip_2",
    ]
