"""Tests for v0.39.0 tools: monitor_entity, ws_command, zigbee_info."""

from __future__ import annotations

import json
import sqlite3

import pytest

from ha_ops_mcp.tools.entity import haops_monitor_entity
from ha_ops_mcp.tools.ws import _looks_read_only, haops_ws_command
from ha_ops_mcp.tools.zigbee import (
    _read_zigbee_db,
    _resolve_ieee,
    _zha_ieee_map,
    haops_zigbee_info,
    haops_zigbee_scan,
)

# --- haops_monitor_entity ---------------------------------------------------


@pytest.mark.asyncio
async def test_monitor_entity_single_sample(ctx, mock_rest):
    """duration_s=0 collects exactly one sample and numeric stats."""
    result = await haops_monitor_entity(ctx, entity_id="sensor.temperature",
                                        duration_s=0, interval_s=1)
    assert result["entity_id"] == "sensor.temperature"
    assert result["stats"]["count"] == 1
    assert result["stats"]["last"] == "22.5"
    assert result["stats"]["numeric"]["mean"] == 22.5


@pytest.mark.asyncio
async def test_monitor_entity_requires_id(ctx):
    result = await haops_monitor_entity(ctx, entity_id="")
    assert "error" in result


@pytest.mark.asyncio
async def test_monitor_entity_caps_duration(ctx, mock_rest):
    """duration above the hard max is capped and flagged."""
    result = await haops_monitor_entity(ctx, entity_id="sensor.temperature",
                                        duration_s=99999, interval_s=600)
    # interval >= duration after cap -> still one sample, no long sleep
    assert result["duration_s"] == 600.0
    assert "note" in result


@pytest.mark.asyncio
async def test_monitor_entity_attribute(ctx, mock_rest):
    """Monitoring an attribute reads attributes[attribute]."""
    result = await haops_monitor_entity(ctx, entity_id="light.living_room",
                                        duration_s=0, attribute="brightness")
    assert result["attribute"] == "brightness"
    assert result["stats"]["last"] == 255


# --- haops_ws_command -------------------------------------------------------


def test_looks_read_only():
    assert _looks_read_only("config/entity_registry/list")
    assert _looks_read_only("config/check_config")
    assert not _looks_read_only("config/entity_registry/update")
    assert not _looks_read_only("zha/devices/reconfigure")


@pytest.mark.asyncio
async def test_ws_command_read_executes_immediately(ctx, mock_ws):
    mock_ws.send_command.return_value = [{"entity_id": "x"}]
    result = await haops_ws_command(ctx, command_type="config/entity_registry/list")
    assert result["success"] is True
    assert result["read_only"] is True
    assert result["result"] == [{"entity_id": "x"}]


@pytest.mark.asyncio
async def test_ws_command_mutation_two_phase(ctx, mock_ws):
    preview = await haops_ws_command(
        ctx, command_type="config/entity_registry/update",
        payload={"entity_id": "sensor.x", "name": "New"},
    )
    assert preview["read_only"] is False
    assert "token" in preview
    # nothing sent yet
    assert mock_ws.send_command.await_count == 0

    result = await haops_ws_command(
        ctx, command_type="config/entity_registry/update",
        payload={"entity_id": "sensor.x", "name": "New"},
        confirm=True, token=preview["token"],
    )
    assert result["success"] is True
    call = mock_ws.send_command.await_args_list[-1]
    assert call.args[0] == "config/entity_registry/update"
    assert call.kwargs.get("entity_id") == "sensor.x"


@pytest.mark.asyncio
async def test_ws_command_requires_type(ctx):
    result = await haops_ws_command(ctx, command_type="")
    assert "error" in result


# --- haops_zigbee_info ------------------------------------------------------


def _make_zigbee_db(path: str, coord_ieee: str, child_ieee: str) -> None:
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE devices_v15 (ieee TEXT, nwk INT, last_seen REAL)")
    conn.execute("CREATE TABLE neighbors_v15 (device_ieee TEXT, ieee TEXT, "
                 "lqi INT, relationship INT, depth INT)")
    conn.execute("CREATE TABLE network_backups_v15 (id INTEGER PRIMARY KEY, "
                 "backup_time TEXT, backup_json TEXT)")
    now = 1_700_000_000.0
    conn.execute("INSERT INTO devices_v15 VALUES (?,?,?)", (coord_ieee, 0, now))
    conn.execute("INSERT INTO devices_v15 VALUES (?,?,?)",
                 (child_ieee, 0x1234, now - 100_000))  # stale
    conn.execute("INSERT INTO neighbors_v15 VALUES (?,?,?,?,?)",
                 (coord_ieee, child_ieee, 180, 1, 1))
    conn.execute("INSERT INTO network_backups_v15 VALUES (?,?,?)",
                 (1, "2026-06-01", json.dumps({
                     "network_info": {"metadata": {"version": "20250321"},
                                      "source": "bellows"}})))
    conn.commit()
    conn.close()


def test_read_zigbee_db_discovers_suffix(tmp_path):
    db = tmp_path / "zigbee.db"
    _make_zigbee_db(str(db), "00:11:22:33:44:55:66:77", "aa:bb:cc:dd:ee:ff:00:11")
    data = _read_zigbee_db(str(db))
    assert data["tables"]["devices"] == "devices_v15"
    assert len(data["devices"]) == 2
    assert data["backup_meta"]["metadata"]["version"] == "20250321"


def test_zha_ieee_map():
    reg = [
        {"id": "dev1", "name": "Coordinator",
         "connections": [["zigbee", "00:11:22:33:44:55:66:77"]],
         "identifiers": []},
        {"id": "dev2", "name_by_user": "Office Motion", "name": "FP1",
         "connections": [], "identifiers": [["zha", "AA:BB:CC:DD:EE:FF:00:11"]]},
    ]
    m = _zha_ieee_map(reg)
    assert m["00:11:22:33:44:55:66:77"]["name"] == "Coordinator"
    # name_by_user wins; ieee lowercased
    assert m["aa:bb:cc:dd:ee:ff:00:11"]["name"] == "Office Motion"


def test_zha_ieee_map_tolerates_non_2tuple_identifiers():
    """Regression (v0.39.0 live bug): HomeKit stores 3-element identifiers,
    e.g. ['homekit', '<id>', 'homekit.bridge']. A strict `for k, v in ids`
    unpack raised 'too many values to unpack (expected 2)' and took down
    haops_zigbee_info AND haops_zha_reconfigure_device for the whole registry.
    The mapper must skip odd-length tuples, not crash."""
    reg = [
        {"id": "hk", "name": "HASS Bridge",
         "connections": [],
         "identifiers": [["homekit", "8427912b", "homekit.bridge"]]},
        {"id": "z", "name": "Coordinator",
         "connections": [["zigbee", "00:12:4b:00:24:c8:68:76"]],
         "identifiers": [["zha", "00:12:4b:00:24:c8:68:76"]]},
    ]
    m = _zha_ieee_map(reg)  # must not raise
    assert "00:12:4b:00:24:c8:68:76" in m
    assert m["00:12:4b:00:24:c8:68:76"]["name"] == "Coordinator"
    # the homekit device contributes nothing (not a ZHA device)
    assert len(m) == 1


@pytest.mark.asyncio
async def test_zigbee_scan_timeout_means_initiated(ctx, mock_ws):
    """Regression (v0.39.0 live bug): zha/topology/update is valid but
    long-running — HA only replies after the full scan, so the default await
    timed out and the tool reported failure even though the scan WAS started.
    A timeout must now read as success/initiated."""
    from ha_ops_mcp.connections.websocket import WebSocketError
    mock_ws.send_command.side_effect = WebSocketError(
        "Timeout waiting for response to zha/topology/update")
    result = await haops_zigbee_scan(ctx)
    assert result["success"] is True
    assert result["status"] == "initiated"


@pytest.mark.asyncio
async def test_zigbee_scan_real_error_surfaces(ctx, mock_ws):
    """A genuine fast error (e.g. unknown_command on a future ZHA) must NOT be
    masked as success — only the timeout case is treated as initiated."""
    from ha_ops_mcp.connections.websocket import WebSocketError
    mock_ws.send_command.side_effect = WebSocketError(
        "Command zha/topology/update failed: unknown_command")
    result = await haops_zigbee_scan(ctx)
    assert "error" in result


@pytest.mark.asyncio
async def test_zigbee_scan_fast_success(ctx, mock_ws):
    """Small mesh that returns before the timeout still succeeds."""
    mock_ws.send_command.return_value = {}
    result = await haops_zigbee_scan(ctx)
    assert result["success"] is True
    assert result["status"] == "completed"


@pytest.mark.asyncio
async def test_resolve_ieee_by_name(ctx, monkeypatch):
    """Friendly name (as shown by zigbee_info) resolves case-insensitively."""
    from ha_ops_mcp.tools import zigbee as zmod

    async def _reg(ctx):
        return [
            {"id": "d1", "name": "Aqara Motion Office",
             "connections": [["zigbee", "54:ef:44:10:00:58:48:be"]],
             "identifiers": []},
            {"id": "d2", "name_by_user": "Kitchen Plug",
             "connections": [["zigbee", "a4:c1:38:00:00:00:00:01"]],
             "identifiers": []},
        ]
    monkeypatch.setattr(zmod, "_get_device_registry", _reg)

    assert await _resolve_ieee(ctx, "aqara motion office") == "54:ef:44:10:00:58:48:be"
    assert await _resolve_ieee(ctx, "Kitchen Plug") == "a4:c1:38:00:00:00:00:01"
    # raw ieee still passes through untouched
    assert await _resolve_ieee(ctx, "00:15:8D:00:07:F2:A7:00") == "00:15:8d:00:07:f2:a7:00"


@pytest.mark.asyncio
async def test_resolve_ieee_ambiguous_name_returns_none(ctx, monkeypatch):
    """A name matching >1 device must NOT resolve — never reconfigure a guess."""
    from ha_ops_mcp.tools import zigbee as zmod

    async def _reg(ctx):
        return [
            {"id": "d1", "name": "Plug",
             "connections": [["zigbee", "aa:aa:aa:aa:aa:aa:aa:01"]], "identifiers": []},
            {"id": "d2", "name": "plug",
             "connections": [["zigbee", "bb:bb:bb:bb:bb:bb:bb:02"]], "identifiers": []},
        ]
    monkeypatch.setattr(zmod, "_get_device_registry", _reg)

    async def _ents(ctx):
        return []
    monkeypatch.setattr("ha_ops_mcp.tools.entity._get_entity_registry", _ents)

    assert await _resolve_ieee(ctx, "plug") is None


@pytest.mark.asyncio
async def test_zigbee_info_missing_db(ctx):
    """No zigbee.db -> structured error, not an exception."""
    result = await haops_zigbee_info(ctx)
    assert "error" in result
    assert "zigbee.db" in result["error"]


@pytest.mark.asyncio
async def test_zigbee_info_reads_mesh(ctx, monkeypatch, tmp_path):
    db = tmp_path / "zigbee.db"
    coord, child = "00:11:22:33:44:55:66:77", "aa:bb:cc:dd:ee:ff:00:11"
    _make_zigbee_db(str(db), coord, child)

    from ha_ops_mcp.tools import zigbee as zmod
    monkeypatch.setattr(zmod, "_zigbee_db_path", lambda ctx: db)

    async def _reg(ctx):
        return [{"id": "d1", "name": "Coord",
                 "connections": [["zigbee", coord]], "identifiers": []},
                {"id": "d2", "name": "Child",
                 "connections": [["zigbee", child]], "identifiers": []}]
    monkeypatch.setattr(zmod, "_get_device_registry", _reg)

    result = await haops_zigbee_info(ctx, stale_hours=1)
    assert result["coordinator"]["ieee"] == coord
    assert result["coordinator"]["firmware_metadata"]["metadata"]["version"] == "20250321"
    assert result["device_count"] == 2
    # child's last_seen is a synthetic past timestamp -> always far past
    # the 1h threshold; coordinator is excluded from stale.
    assert result["stale_count"] == 1
    child_rec = next(d for d in result["devices"] if d["ieee"] == child)
    assert child_rec["lqi_at_coordinator"] == 180
    assert child_rec["relationship"] == "child"
