"""Tests for haops_dashboard_resources."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ha_ops_mcp.connections.websocket import WebSocketError
from ha_ops_mcp.tools.dashboard import haops_dashboard_resources


def _seed_resources(config_root: Path, items: list[dict]) -> None:
    (config_root / ".storage" / "lovelace_resources").write_text(
        json.dumps({"version": 1, "data": {"items": items}})
    )


@pytest.mark.asyncio
async def test_filesystem_tier_returns_resources(ctx):
    config_root = Path(ctx.config.filesystem.config_root)
    _seed_resources(config_root, [
        {
            "id": "res_button",
            "url": "/local/community/button-card/button-card.js",
            "type": "module",
        },
        {
            "id": "res_decluttering",
            "url": "/hacsfiles/decluttering-card/decluttering-card.js",
            "type": "module",
        },
    ])

    result = await haops_dashboard_resources(ctx, include_dashboard_usage=False)
    assert result["source"] == "filesystem"
    assert result["count"] == 2
    urls = {r["url"] for r in result["resources"]}
    assert "/local/community/button-card/button-card.js" in urls
    assert all(r["scope"] == "global" for r in result["resources"])


@pytest.mark.asyncio
async def test_websocket_fallback_when_file_missing(ctx, mock_ws):
    """When the storage file is absent, fall back to WS lovelace/resources."""
    mock_ws.send_command.return_value = [
        {
            "id": "res_x",
            "url": "/local/x.js",
            "type": "module",
        }
    ]

    result = await haops_dashboard_resources(ctx, include_dashboard_usage=False)
    assert result["source"] == "websocket"
    assert result["count"] == 1
    assert result["resources"][0]["url"] == "/local/x.js"


@pytest.mark.asyncio
async def test_both_unavailable_returns_error(ctx, mock_ws):
    """Missing file + WS error → graceful structured error, not a raise."""
    mock_ws.send_command.side_effect = WebSocketError("WS down")
    result = await haops_dashboard_resources(ctx)
    assert result["count"] == 0
    assert result["source"] == "none"
    assert "error" in result


@pytest.mark.asyncio
async def test_dashboard_usage_cross_link(ctx, mock_ws):
    """include_dashboard_usage=True populates used_by_dashboards from
    storage dashboards' per-dashboard resources arrays."""
    config_root = Path(ctx.config.filesystem.config_root)
    _seed_resources(config_root, [
        {
            "id": "res_button",
            "url": "/local/button-card.js",
            "type": "module",
        }
    ])

    # Add resources entry to the default dashboard
    storage = config_root / ".storage"
    lovelace = json.loads((storage / "lovelace").read_text())
    lovelace["data"]["config"]["resources"] = [
        {"url": "/local/button-card.js", "type": "module"},
        {"url": "/local/dashboard-only.js", "type": "module"},
    ]
    (storage / "lovelace").write_text(json.dumps(lovelace))

    # WS dashboards/list returns no extra dashboards (only default exists)
    mock_ws.send_command.return_value = []

    result = await haops_dashboard_resources(ctx, include_dashboard_usage=True)
    assert result["source"] == "filesystem"

    by_url = {r["url"]: r for r in result["resources"]}

    # Global resource cross-linked to default dashboard
    assert "lovelace" in by_url["/local/button-card.js"]["used_by_dashboards"]

    # Dashboard-only resource picked up with scope=dashboard
    assert "/local/dashboard-only.js" in by_url
    assert by_url["/local/dashboard-only.js"]["scope"] == "dashboard"
    assert by_url["/local/dashboard-only.js"]["used_by_dashboards"] == ["lovelace"]


@pytest.mark.asyncio
async def test_include_dashboard_usage_false_skips_scan(ctx, mock_ws):
    """When include_dashboard_usage=False, no WS dashboards/list call."""
    config_root = Path(ctx.config.filesystem.config_root)
    _seed_resources(config_root, [
        {"id": "r1", "url": "/local/x.js", "type": "module"},
    ])

    result = await haops_dashboard_resources(ctx, include_dashboard_usage=False)
    assert result["count"] == 1
    # WS shouldn't have been called at all
    mock_ws.send_command.assert_not_called()
