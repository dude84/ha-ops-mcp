"""Freshness-aware registry reads (storage_registry).

The bug being pinned down: `.storage/core.*` lags live state after a write, so
a read that follows a mutation can report records HA has already dropped.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from ha_ops_mcp.storage_registry import (
    load_registry,
    mark_registry_write,
    reset_write_clock,
)
from ha_ops_mcp.tools.registry import haops_registry_query


@pytest.fixture(autouse=True)
def _clean_clock():
    reset_write_clock()
    yield
    reset_write_clock()


def _write_devices(config_dir: Path, devices: list[dict]) -> Path:
    path = config_dir / ".storage" / "core.device_registry"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({"version": 1, "data": {"devices": devices}}))
    return path


@pytest.mark.asyncio
async def test_file_read_reports_source_and_age(ctx, config_dir):
    _write_devices(config_dir, [{"id": "d1", "name": "Lamp"}])

    read = await load_registry(ctx, "devices")

    assert read.source == "file"
    assert read.records == [{"id": "d1", "name": "Lamp"}]
    assert read.file_age_seconds is not None
    assert read.file_predates_our_write is False
    assert not read.notes


@pytest.mark.asyncio
async def test_stale_file_after_our_write_escalates_to_websocket(
    ctx, config_dir, mock_ws
):
    """The reported bug: file still lists a device HA already removed."""
    path = _write_devices(config_dir, [{"id": "ghost", "name": "Tasmota IR"}])
    # File written before our registry write, and not flushed since.
    old = time.time() - 60
    os.utime(path, (old, old))
    mark_registry_write("config/device_registry/remove_config_entry")

    mock_ws.send_command.return_value = [{"id": "d1", "name": "Lamp"}]

    read = await load_registry(ctx, "devices")

    assert read.source == "websocket"
    assert [r["id"] for r in read.records] == ["d1"]
    assert read.file_predates_our_write is True
    assert "has not been flushed" in read.notes[0]
    mock_ws.send_command.assert_awaited_with("config/device_registry/list")


@pytest.mark.asyncio
async def test_flushed_file_after_our_write_is_trusted(
    ctx, config_dir, mock_ws
):
    """HA flushed after our write → the file is current, no WS round-trip."""
    mark_registry_write("config/entity_registry/update")
    time.sleep(0.01)
    _write_devices(config_dir, [{"id": "d1"}])

    read = await load_registry(ctx, "devices")

    assert read.source == "file"
    assert read.file_predates_our_write is False
    mock_ws.send_command.assert_not_awaited()


@pytest.mark.asyncio
async def test_fresh_flag_forces_websocket(ctx, config_dir, mock_ws):
    _write_devices(config_dir, [{"id": "from_file"}])
    mock_ws.send_command.return_value = [{"id": "from_ws"}]

    read = await load_registry(ctx, "devices", fresh=True)

    assert read.source == "websocket"
    assert read.records[0]["id"] == "from_ws"


@pytest.mark.asyncio
async def test_failed_websocket_falls_back_to_file_with_a_warning(
    ctx, config_dir, mock_ws
):
    """A stale file beats no data — but the caller is told it may be stale."""
    from ha_ops_mcp.connections.websocket import WebSocketError

    path = _write_devices(config_dir, [{"id": "maybe_ghost"}])
    old = time.time() - 60
    os.utime(path, (old, old))
    mark_registry_write("config/device_registry/remove_config_entry")
    mock_ws.send_command.side_effect = WebSocketError("connection closed")

    read = await load_registry(ctx, "devices")

    assert read.source == "file"
    assert read.file_predates_our_write is True
    assert "may be stale" in read.notes[0]


@pytest.mark.asyncio
async def test_missing_file_uses_websocket(ctx, config_dir, mock_ws):
    path = config_dir / ".storage" / "core.floor_registry"
    if path.exists():
        path.unlink()
    mock_ws.send_command.return_value = [{"floor_id": "ground"}]

    read = await load_registry(ctx, "floors")

    assert read.source == "websocket"
    assert "unreadable" in read.notes[0]


@pytest.mark.asyncio
async def test_registry_without_ws_fallback_raises_when_file_is_gone(
    ctx, config_dir
):
    path = config_dir / ".storage" / "core.config_entries"
    if path.exists():
        path.unlink()

    with pytest.raises(RuntimeError, match="no WebSocket fallback"):
        await load_registry(ctx, "config_entries")


@pytest.mark.asyncio
async def test_query_tool_surfaces_provenance(ctx, config_dir):
    _write_devices(config_dir, [{"id": "d1", "name": "Lamp"}])

    result = await haops_registry_query(ctx, registry="devices")

    assert result["provenance"]["source"] == "file"
    assert "file_age_seconds" in result["provenance"]


@pytest.mark.asyncio
async def test_query_tool_count_only_still_reports_provenance(ctx, config_dir):
    _write_devices(config_dir, [{"id": "d1"}, {"id": "d2"}])

    result = await haops_registry_query(
        ctx, registry="devices", count_only=True
    )

    assert result["count"] == 2
    assert result["provenance"]["source"] == "file"


@pytest.mark.asyncio
async def test_unknown_registry_is_rejected(ctx):
    result = await haops_registry_query(ctx, registry="nope")

    assert "error" in result
    assert "devices" in result["supported"]


# ── The write clock is stamped by the WS layer, not by each tool ───────


@pytest.mark.asyncio
async def test_ws_registry_mutation_stamps_the_clock():
    from ha_ops_mcp.connections.websocket import (  # noqa: I001 - local import
        _note_if_registry_write,
    )
    from ha_ops_mcp.storage_registry import last_registry_write

    _note_if_registry_write("config/entity_registry/update")
    ts, what = last_registry_write()

    assert ts > 0
    assert what == "config/entity_registry/update"


@pytest.mark.parametrize(
    "command",
    [
        "config/entity_registry/list",
        "config/device_registry/get",
        "lovelace/config/save",
        "config/check_config",
        "config_entries/get",
    ],
)
def test_non_registry_writes_do_not_stamp_the_clock(command):
    from ha_ops_mcp.connections.websocket import _note_if_registry_write
    from ha_ops_mcp.storage_registry import last_registry_write

    _note_if_registry_write(command)

    assert last_registry_write() == (0.0, None)


@pytest.mark.parametrize(
    "command",
    [
        "config/entity_registry/update",
        "config/entity_registry/remove",
        "config/device_registry/remove_config_entry",
        "config/area_registry/create",
        "config/label_registry/delete",
    ],
)
def test_every_registry_write_shape_stamps_the_clock(command):
    from ha_ops_mcp.connections.websocket import _note_if_registry_write
    from ha_ops_mcp.storage_registry import last_registry_write

    _note_if_registry_write(command)

    assert last_registry_write()[1] == command
