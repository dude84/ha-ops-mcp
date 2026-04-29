"""Tests for dashboard tools."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from ha_ops_mcp.tools.dashboard import (
    haops_dashboard_apply,
    haops_dashboard_diff,
    haops_dashboard_get,
    haops_dashboard_list,
    haops_dashboard_patch,
)


@pytest.mark.asyncio
async def test_dashboard_list(ctx):
    ctx.ws.send_command = AsyncMock(return_value=[
        {"url_path": "energy", "title": "Energy", "mode": "storage", "icon": "mdi:lightning-bolt"},
    ])
    result = await haops_dashboard_list(ctx)
    assert result["count"] == 2
    assert result["dashboards"][0]["url_path"] == "lovelace"
    assert result["dashboards"][1]["url_path"] == "energy"


@pytest.mark.asyncio
async def test_dashboard_get_from_filesystem(ctx, dashboard_storage):
    result = await haops_dashboard_get(ctx, dashboard_id="lovelace")
    assert result["config"]["title"] == "Home"
    assert len(result["config"]["views"]) == 2


@pytest.mark.asyncio
async def test_dashboard_get_specific_view(ctx, dashboard_storage):
    result = await haops_dashboard_get(ctx, dashboard_id="lovelace", view=1)
    assert result["view"]["title"] == "Kitchen"


@pytest.mark.asyncio
async def test_dashboard_get_view_out_of_range(ctx, dashboard_storage):
    result = await haops_dashboard_get(ctx, dashboard_id="lovelace", view=99)
    assert "error" in result


@pytest.mark.asyncio
async def test_dashboard_get_not_found(ctx):
    from ha_ops_mcp.connections.websocket import WebSocketError

    ctx.ws.send_command = AsyncMock(side_effect=WebSocketError("not found"))
    result = await haops_dashboard_get(ctx, dashboard_id="nonexistent")
    assert "error" in result


@pytest.mark.asyncio
async def test_dashboard_diff(ctx, dashboard_storage):
    new_config = {
        "title": "Home Updated",
        "views": [
            {"title": "Overview", "cards": [{"type": "entities"}]},
        ],
    }
    result = await haops_dashboard_diff(
        ctx, dashboard_id="lovelace", new_config=new_config
    )
    assert "diff" in result
    assert "token" in result
    assert "Home Updated" in result["diff"] or "Changed" in result["diff"]


@pytest.mark.asyncio
async def test_dashboard_apply(ctx, dashboard_storage):
    new_config = {"title": "Updated", "views": []}
    diff_result = await haops_dashboard_diff(
        ctx, dashboard_id="lovelace", new_config=new_config
    )

    ctx.ws.send_command = AsyncMock(return_value={})
    apply_result = await haops_dashboard_apply(
        ctx, token=diff_result["token"]
    )
    assert apply_result["success"] is True
    assert apply_result.get("backup_path") is not None


# ── Gap 10: summary mode + view-by-path/title ──


@pytest.mark.asyncio
async def test_dashboard_get_summary(ctx, dashboard_storage):
    """summary=True returns cheap view index without view bodies."""
    result = await haops_dashboard_get(
        ctx, dashboard_id="lovelace", summary=True
    )
    assert result["view_count"] == 2
    view_list = result["views"]
    assert view_list[0]["title"] == "Overview"
    assert view_list[0]["path"] == "overview"
    assert view_list[0]["card_count"] == 1
    # No card bodies in summary — just counts
    assert "cards" not in view_list[0]


@pytest.mark.asyncio
async def test_dashboard_get_view_by_path(ctx, dashboard_storage):
    """View can be looked up by path (string)."""
    result = await haops_dashboard_get(
        ctx, dashboard_id="lovelace", view="kitchen"
    )
    assert result["view_index"] == 1
    assert result["view"]["title"] == "Kitchen"


@pytest.mark.asyncio
async def test_dashboard_get_view_by_title(ctx, dashboard_storage):
    """View can be looked up by title (case-insensitive fallback)."""
    result = await haops_dashboard_get(
        ctx, dashboard_id="lovelace", view="overview"
    )
    # 'overview' is also the path, matches that first
    assert result["view_index"] == 0


@pytest.mark.asyncio
async def test_dashboard_get_view_not_found(ctx, dashboard_storage):
    result = await haops_dashboard_get(
        ctx, dashboard_id="lovelace", view="does_not_exist"
    )
    assert "error" in result


@pytest.mark.asyncio
async def test_dashboard_get_view_by_numeric_string(ctx, dashboard_storage):
    """MCP clients may coerce ints to strings — '1' should resolve to index 1.

    Regression: real-world usage hit `view='3'` returning
    "no view with index/path/title '3'" because the resolver only tried
    int branch when the type was actually int.
    """
    result = await haops_dashboard_get(
        ctx, dashboard_id="lovelace", view="1"
    )
    assert result.get("view_index") == 1
    assert "error" not in result


# ── Gap 11: view-replace mode ──


@pytest.mark.asyncio
async def test_dashboard_diff_view_replace(ctx, dashboard_storage):
    """view-replace mode: pass view + new_view, server composes full config."""
    new_view = {"title": "Kitchen v2", "path": "kitchen", "cards": [{"type": "markdown"}]}
    result = await haops_dashboard_diff(
        ctx, dashboard_id="lovelace", view="kitchen", new_view=new_view
    )
    assert "token" in result
    assert "diff" in result


@pytest.mark.asyncio
async def test_dashboard_diff_view_append(ctx, dashboard_storage):
    """no view + new_view: append a new view to the dashboard."""
    new_view = {"title": "New Tab", "path": "newtab", "cards": []}
    result = await haops_dashboard_diff(
        ctx, dashboard_id="lovelace", new_view=new_view
    )
    assert "token" in result


@pytest.mark.asyncio
async def test_dashboard_diff_requires_one_mode(ctx, dashboard_storage):
    result = await haops_dashboard_diff(ctx, dashboard_id="lovelace")
    assert "error" in result


@pytest.mark.asyncio
async def test_dashboard_diff_rejects_both_modes(ctx, dashboard_storage):
    result = await haops_dashboard_diff(
        ctx,
        dashboard_id="lovelace",
        new_config={},
        new_view={},
    )
    assert "error" in result


@pytest.mark.asyncio
async def test_dashboard_diff_view_replace_not_found(ctx, dashboard_storage):
    result = await haops_dashboard_diff(
        ctx,
        dashboard_id="lovelace",
        view="nonexistent_view",
        new_view={"title": "x", "cards": []},
    )
    assert "error" in result


@pytest.mark.asyncio
async def test_dashboard_diff_view_replace_by_int_index(ctx, dashboard_storage):
    """view=<int> must resolve to views[idx] (the natural int from _get summary).

    Regression: gap 2026-04-16 §2 — dashboard_diff(view=5) returned
    'View 5 not found' because the resolver took the `else` branch and
    searched by path/title (which don't match '5').
    """
    new_view = {"title": "Kitchen v2", "path": "kitchen", "cards": []}
    result = await haops_dashboard_diff(
        ctx, dashboard_id="lovelace", view=1, new_view=new_view
    )
    assert "error" not in result
    assert "token" in result


@pytest.mark.asyncio
async def test_dashboard_diff_view_replace_by_numeric_string(ctx, dashboard_storage):
    """view="1" (MCP stringified int) must resolve the same as view=1."""
    new_view = {"title": "Kitchen v2", "path": "kitchen", "cards": []}
    result = await haops_dashboard_diff(
        ctx, dashboard_id="lovelace", view="1", new_view=new_view
    )
    assert "error" not in result
    assert "token" in result


# ── Regression: storage filename sanitisation + WS kwargs shape ──


@pytest.mark.asyncio
async def test_dashboard_get_resolves_hyphenated_url_path(ctx, config_dir):
    """url_path with `-` must map to storage file with `_`.

    HA sanitises storage keys: url_path "new-dashboard" → file
    ".storage/lovelace.new_dashboard". Tier 1 must apply that rule
    before falling through to WS.
    """
    storage = config_dir / ".storage"
    (storage / "lovelace.new_dashboard").write_text(json.dumps({
        "version": 1,
        "data": {"config": {"title": "New", "views": [{"title": "X", "cards": []}]}},
    }))
    # If tier 1 builds the path correctly, WS is never touched.
    ctx.ws.send_command = AsyncMock(side_effect=AssertionError("WS should not be called"))
    result = await haops_dashboard_get(ctx, dashboard_id="new-dashboard")
    assert result["config"]["title"] == "New"


@pytest.mark.asyncio
async def test_dashboard_apply_passes_msg_type_positionally(ctx, dashboard_storage):
    """Regression for kwargs={'type': ...} bug — send_command must receive
    the WS command name as a positional, not buried in **kwargs.
    """
    new_config = {"title": "x", "views": []}
    diff_result = await haops_dashboard_diff(
        ctx, dashboard_id="lovelace", new_config=new_config
    )

    captured: dict[str, object] = {}

    async def fake_send(msg_type, *args, **kwargs):
        captured["msg_type"] = msg_type
        captured["kwargs"] = kwargs
        return {}

    ctx.ws.send_command = fake_send
    apply_result = await haops_dashboard_apply(ctx, token=diff_result["token"])
    assert apply_result["success"] is True
    assert captured["msg_type"] == "lovelace/config/save"
    assert "type" not in captured["kwargs"]


@pytest.mark.asyncio
async def test_dashboard_diff_full_config_still_works(ctx, dashboard_storage):
    """Backward compat: full new_config mode still works."""
    new_config = {
        "title": "Home v2",
        "views": [{"title": "Single", "cards": []}],
    }
    result = await haops_dashboard_diff(
        ctx, dashboard_id="lovelace", new_config=new_config
    )
    assert "token" in result


# ── haops_dashboard_patch (JSON Patch — Phase 3b Stage 2) ──


@pytest.mark.asyncio
async def test_dashboard_patch_replace_title(ctx, dashboard_storage):
    """Single-op replace produces a token whose apply writes the change."""
    patch = [{"op": "replace", "path": "/title", "value": "Home Renamed"}]
    result = await haops_dashboard_patch(
        ctx, dashboard_id="lovelace", patch=patch
    )
    assert "token" in result
    assert "diff" in result
    assert "diff_rendered" in result

    # The token feeds the existing apply tool unchanged.
    ctx.ws.send_command = AsyncMock(return_value={})
    apply_result = await haops_dashboard_apply(ctx, token=result["token"])
    assert apply_result["success"] is True


@pytest.mark.asyncio
async def test_dashboard_patch_add_card(ctx, dashboard_storage):
    """Append a card to a view via path ending in `/-`."""
    patch = [
        {
            "op": "add",
            "path": "/views/0/cards/-",
            "value": {"type": "markdown", "content": "Hello"},
        }
    ]
    result = await haops_dashboard_patch(
        ctx, dashboard_id="lovelace", patch=patch
    )
    assert "token" in result


@pytest.mark.asyncio
async def test_dashboard_patch_remove_card(ctx, dashboard_storage):
    """Remove a card by index."""
    patch = [{"op": "remove", "path": "/views/0/cards/0"}]
    result = await haops_dashboard_patch(
        ctx, dashboard_id="lovelace", patch=patch
    )
    assert "token" in result


@pytest.mark.asyncio
async def test_dashboard_patch_rejects_empty_patch(ctx, dashboard_storage):
    """An empty patch list is a user error, not a no-op."""
    result = await haops_dashboard_patch(
        ctx, dashboard_id="lovelace", patch=[]
    )
    assert "error" in result
    assert "non-empty" in result["error"].lower() or "empty" in result["error"].lower()


@pytest.mark.asyncio
async def test_dashboard_patch_rejects_invalid_op(ctx, dashboard_storage):
    """Malformed operation (missing op / path) is caught with a clear error."""
    patch = [{"path": "/title", "value": "X"}]  # missing 'op'
    result = await haops_dashboard_patch(
        ctx, dashboard_id="lovelace", patch=patch
    )
    assert "error" in result


@pytest.mark.asyncio
async def test_dashboard_patch_rejects_nonexistent_path(ctx, dashboard_storage):
    """Pointer that doesn't resolve in the current config returns a clean error.

    Common cause in practice: dashboard was edited elsewhere between read
    and patch. Error message must hint at re-reading, not fuzzy-shift.
    """
    patch = [
        {
            "op": "replace",
            "path": "/views/99/cards/0/entity",
            "value": "light.x",
        }
    ]
    result = await haops_dashboard_patch(
        ctx, dashboard_id="lovelace", patch=patch
    )
    assert "error" in result
    assert "hint" in result
    assert "re-read" in result["hint"].lower()


@pytest.mark.asyncio
async def test_dashboard_patch_test_op_failure(ctx, dashboard_storage):
    """JSON Patch 'test' op surfaces as a clean error when assertion fails."""
    patch = [
        {
            "op": "test",
            "path": "/title",
            "value": "Not The Real Title",
        },
        {"op": "replace", "path": "/title", "value": "Home v3"},
    ]
    result = await haops_dashboard_patch(
        ctx, dashboard_id="lovelace", patch=patch
    )
    assert "error" in result


@pytest.mark.asyncio
async def test_dashboard_patch_diff_carries_unified_markers(ctx, dashboard_storage):
    """Patch's ``diff`` field is a real unified diff — per-op anchor + +/-
    line markers from a ``difflib.unified_diff`` of YAML at the patch path.

    Replaces the legacy ``~ replace ... 'old' -> 'new'`` blob (gap report
    2026-04-18 §1) that had no markers, so no chat renderer or sidebar
    JS could colourise it. The diff field is what the controller reads
    and pastes into chat — without +/- prefixes the colourisation is
    impossible regardless of how careful the controller is.
    """
    patch = [
        {
            "op": "replace",
            "path": "/views/0/cards/0",
            "value": {"type": "tile", "entity": "light.kitchen"},
        }
    ]
    result = await haops_dashboard_patch(
        ctx, dashboard_id="lovelace", patch=patch
    )
    diff = result["diff"]

    # Per-op anchor — mechanical lookup, no prose: op kind, JSON Pointer,
    # view-title breadcrumb, before/after value kind.
    assert "Replace" in diff
    assert "/views/0/cards/0" in diff
    assert "Overview" in diff, "anchor must include view title from registry lookup"
    assert "`entities`" in diff and "`tile`" in diff

    # Real unified diff body — +/- markers a chat renderer can colourise.
    assert "\n-type: entities" in diff
    assert "\n+type: tile" in diff

    # diff_rendered is the same content wrapped in a ```diff fence — that's
    # what the controller pastes verbatim into chat per the REVIEW PROTOCOL.
    rendered = result["diff_rendered"]
    assert rendered.startswith("```diff\n")
    assert rendered.rstrip().endswith("```")
    assert diff in rendered


@pytest.mark.asyncio
async def test_dashboard_diff_view_replace_carries_unified_markers(ctx, dashboard_storage):
    """View-replace mode emits a real unified diff of the full dashboard
    config (YAML-serialised) instead of the legacy deepdiff blob."""
    new_view = {
        "title": "Overview Updated",
        "path": "overview",
        "cards": [{"type": "markdown", "content": "hi"}],
    }
    result = await haops_dashboard_diff(
        ctx, dashboard_id="lovelace", view=0, new_view=new_view
    )
    diff = result["diff"]
    # Whole-config YAML unified diff — header + +/- line markers.
    assert diff.startswith("--- a/lovelace") or "--- a/" in diff
    assert "+- title: Overview Updated" in diff
    assert "+  - type: markdown" in diff
    rendered = result["diff_rendered"]
    assert rendered.startswith("```diff\n") and rendered.rstrip().endswith("```")


@pytest.mark.asyncio
async def test_dashboard_patch_rejects_missing_dashboard(ctx, dashboard_storage):
    """Patching a dashboard id that doesn't exist errors with a hint."""
    from ha_ops_mcp.connections.websocket import WebSocketError

    ctx.ws.send_command = AsyncMock(side_effect=WebSocketError("not found"))
    patch = [{"op": "replace", "path": "/title", "value": "X"}]
    result = await haops_dashboard_patch(
        ctx, dashboard_id="does-not-exist", patch=patch
    )
    assert "error" in result
    assert "not found" in result["error"].lower()
