"""Tests for the sidebar UI HTTP routes.

Uses Starlette's TestClient against an ad-hoc app built from the routes
we register on a FastMCP instance. This exercises the full handler path
(including auth, ETag, JSON shape) without needing a running server.
"""

from __future__ import annotations

import pytest
from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette
from starlette.testclient import TestClient

from ha_ops_mcp.ui.routes import register_ui_routes


@pytest.fixture
def client(ctx):
    """A TestClient wired to a Starlette app carrying only the UI routes.

    All requests carry X-Ingress-Path so auth passes — the loopback-trust
    branch is exercised separately.
    """
    mcp = FastMCP("test")
    register_ui_routes(mcp, ctx)
    app = Starlette(routes=mcp._custom_starlette_routes)
    return TestClient(app, headers={"X-Ingress-Path": "/ha_ops"})


# ── Auth ──────────────────────────────────────────────────────────────


def test_auth_ingress_header_trusted(ctx):
    """Request with X-Ingress-Path header is trusted (HA ingress proxy)."""
    mcp = FastMCP("test")
    register_ui_routes(mcp, ctx)
    app = Starlette(routes=mcp._custom_starlette_routes)
    c = TestClient(app)
    res = c.get("/api/ui/self_check", headers={"X-Ingress-Path": "/ha_ops_mcp"})
    assert res.status_code == 200


def test_auth_unit_is_authorized_rejects_unknown_non_loopback(ctx):
    """Unit-test _is_authorized for the non-loopback / no-bearer path.

    TestClient always reports loopback so the rejection branch isn't
    reachable via HTTP; we invoke the predicate directly.
    """
    from unittest.mock import MagicMock

    from ha_ops_mcp.ui.routes import _is_authorized

    req = MagicMock()
    req.headers = {}
    req.client = MagicMock()
    req.client.host = "8.8.8.8"
    assert _is_authorized(req) is False


# ── /ui HTML + ETag ───────────────────────────────────────────────────


def test_ui_html_returns_503_when_missing(client, tmp_path, monkeypatch):
    from ha_ops_mcp.ui import routes
    monkeypatch.setattr(routes, "UI_HTML_PATH", tmp_path / "nope.html")
    res = client.get("/ui")
    assert res.status_code == 503


def test_ui_html_served_when_present(client, tmp_path, monkeypatch):
    from ha_ops_mcp.ui import routes
    html = tmp_path / "ui.html"
    html.write_text("<!doctype html><html><body>hi</body></html>")
    monkeypatch.setattr(routes, "UI_HTML_PATH", html)
    monkeypatch.setattr(routes, "_UI_ETAG", '"deadbeef"')
    monkeypatch.setattr(routes, "_UI_BODY", None)  # force reload
    res = client.get("/ui")
    assert res.status_code == 200
    assert "hi" in res.text
    assert res.headers.get("etag") == '"deadbeef"'
    assert "no-cache" in res.headers.get("cache-control", "")


def test_ui_html_304_on_matching_etag(client, tmp_path, monkeypatch):
    from ha_ops_mcp.ui import routes
    html = tmp_path / "ui.html"
    html.write_text("x")
    monkeypatch.setattr(routes, "UI_HTML_PATH", html)
    monkeypatch.setattr(routes, "_UI_ETAG", '"abc"')
    monkeypatch.setattr(routes, "_UI_BODY", None)
    res = client.get("/ui", headers={"If-None-Match": '"abc"'})
    assert res.status_code == 304


# ── Removed endpoints (regression guards) ─────────────────────────────


def test_overview_endpoint_removed(client):
    """v0.21.5: Overview tab and its endpoint are gone."""
    assert client.get("/api/ui/overview").status_code == 404


def test_pending_endpoints_removed(client):
    """v0.20.0: Pending tab and its endpoints are gone."""
    assert client.get("/api/ui/pending").status_code == 404
    assert client.get("/api/ui/pending/some_id").status_code == 404


def test_graph_endpoint_removed(client):
    assert client.get("/api/ui/graph").status_code == 404


def test_issues_endpoint_removed(client):
    assert client.get("/api/ui/issues").status_code == 404


def test_references_endpoint_removed(client):
    assert client.get("/api/ui/references/entity:sensor.temperature").status_code == 404


# ── /api/ui/self_check + /api/ui/tools_check ─────────────────────────


def test_self_check_endpoint(client):
    res = client.get("/api/ui/self_check")
    assert res.status_code == 200
    data = res.json()
    # Either {checks: {...}} or flat — Health tab tolerates both
    assert isinstance(data, dict)
    # At least one of the expected keys is present
    keys = data.get("checks", data).keys()
    assert any(k in keys for k in ("rest_api", "filesystem", "backup_dir"))


def test_tools_check_endpoint(client):
    res = client.get("/api/ui/tools_check")
    assert res.status_code == 200
    data = res.json()
    assert "overall" in data or "rest_api" in data  # one of the well-known keys


# ── /api/ui/timeline (replaces old /api/ui/recent) ────────────────────


def test_recent_endpoint_removed(client):
    """Regression: the old /api/ui/recent endpoint is gone.

    The timeline endpoint replaces it. Keeping the old surface as an
    alias would just invite stale consumers; it's better to fail loud.
    """
    assert client.get("/api/ui/recent").status_code == 404


def test_timeline_shape_empty(client):
    res = client.get("/api/ui/timeline")
    assert res.status_code == 200
    data = res.json()
    assert "entries" in data
    assert "count" in data
    assert data["count"] == len(data["entries"])


async def _log_audit_entry(
    ctx,
    tool,
    details,
    success=True,
    backup_path=None,
    token_id=None,
    error=None,
):
    """Helper: write a real audit entry via the public API."""
    await ctx.audit.log(
        tool=tool,
        details=details,
        success=success,
        backup_path=backup_path,
        token_id=token_id,
        error=error,
    )


def _fetch_diff(client, entry):
    """Helper: pull the lazy diff for a timeline entry.

    The list endpoint at /api/ui/timeline only ships diff_present; the
    body lives at /api/ui/timeline/diff to keep initial render fast.
    Tests that previously asserted on entry["diff"] go through here.
    """
    res = client.get(
        "/api/ui/timeline/diff",
        params={"ts": entry["timestamp"], "tool": entry["tool"]},
    )
    assert res.status_code == 200, res.text
    return res.json()


@pytest.mark.asyncio
async def test_timeline_config_apply_inlines_diff(client, ctx):
    """config_apply entries get a unified diff reconstructed from details."""
    await _log_audit_entry(
        ctx,
        tool="config_apply",
        details={
            "path": "/config/automations.yaml",
            "old_content": "alpha\nbeta\n",
            "new_content": "alpha\nBETA\ngamma\n",
        },
        backup_path="/config/ha-ops-backups/config/automations.yaml.bak",
        token_id="tok123",
    )
    data = client.get("/api/ui/timeline").json()
    entries = [e for e in data["entries"] if e["tool"] == "haops_config_apply"]
    assert entries, "config_apply entry should surface in timeline"
    e = entries[0]
    assert e["summary"] == "Wrote /config/automations.yaml"
    # List response advertises diff_present but does not inline the body.
    assert e["diff_present"] is True
    assert "diff" not in e
    diff = _fetch_diff(client, e)
    assert diff["diff_present"] is True
    assert "BETA" in diff["diff"]
    assert "-beta" in diff["diff"]
    assert e["backup_path"] == "/config/ha-ops-backups/config/automations.yaml.bak"
    assert e["token_id"] == "tok123"
    assert e["success"] is True


@pytest.mark.asyncio
async def test_timeline_dashboard_apply_has_structured_diff(client, ctx):
    """dashboard_apply renders a json-diff summary via format_json_diff."""
    await _log_audit_entry(
        ctx,
        tool="dashboard_apply",
        details={
            "dashboard_id": "lovelace",
            "old_config": {"title": "Home", "views": []},
            "new_config": {"title": "Home Renamed", "views": []},
        },
        token_id="tok456",
    )
    data = client.get("/api/ui/timeline").json()
    entries = [e for e in data["entries"] if e["tool"] == "haops_dashboard_apply"]
    assert entries
    e = entries[0]
    assert e["summary"] == "Updated dashboard lovelace"
    assert e["diff_present"] is True
    diff = _fetch_diff(client, e)
    assert "Home Renamed" in diff["diff"]  # format_json_diff includes new value


@pytest.mark.asyncio
async def test_timeline_batch_apply_renders_per_item_diffs(client, ctx):
    """batch_apply audit entries surface all per-target diffs stitched together."""
    await _log_audit_entry(
        ctx,
        tool="batch_apply",
        details={
            "items": [
                {
                    "tool": "config_patch",
                    "target": "/config/automations.yaml",
                    "path": "/config/automations.yaml",
                    "old_content": "alias: Foo\n",
                    "new_content": "alias: Foo Renamed\n",
                },
                {
                    "tool": "dashboard_patch",
                    "target": "lovelace",
                    "dashboard_id": "lovelace",
                    "old_config": {"title": "Home", "views": []},
                    "new_config": {"title": "Home Renamed", "views": []},
                },
            ],
        },
        token_id="batch_tok_1",
    )
    data = client.get("/api/ui/timeline").json()
    entries = [e for e in data["entries"] if e["tool"] == "haops_batch_apply"]
    assert entries
    e = entries[0]
    assert "Applied batch: 2 target(s)" in e["summary"]
    assert "config_patch" in e["summary"]
    assert "dashboard_patch" in e["summary"]
    # Both items' diffs are stitched into the single (lazy) diff block.
    assert e["diff_present"] is True
    diff = _fetch_diff(client, e)
    assert "Foo Renamed" in diff["diff"]
    assert "Home Renamed" in diff["diff"]
    # Excerpt summarises the item shape without embedding content
    excerpt = e["details_excerpt"]
    assert excerpt["item_count"] == 2
    assert excerpt["items"][0]["tool"] == "config_patch"


@pytest.mark.asyncio
async def test_timeline_rollback_renders_per_target_diffs(client, ctx):
    """rollback audit entries surface proper summary + per-target diffs."""
    await _log_audit_entry(
        ctx,
        tool="rollback",
        details={
            "transaction_id": "txn_abc",
            "operation": "batch_apply",
            "restored": [
                {
                    "target": "/config/scripts.yaml",
                    "action": "restore",
                    "restored": "content",
                    "old_content": "alias: Modified\n",
                    "new_content": "alias: Original\n",
                },
                {
                    "target": "/config/new_after.yaml",
                    "action": "delete",
                    "restored": "deleted (was newly created)",
                    "old_content": "key: 1\n",
                    "new_content": "",
                },
            ],
        },
        token_id="rb_tok_1",
    )
    data = client.get("/api/ui/timeline").json()
    entries = [e for e in data["entries"] if e["tool"] == "haops_rollback"]
    assert entries
    e = entries[0]
    # Summary now has real content — was "rollback (no summary)" before.
    assert "Rolled back batch_apply" in e["summary"]
    assert "2 target" in e["summary"]
    # Diff block stitches both per-target diffs together (lazy-loaded).
    assert e["diff_present"] is True
    diff = _fetch_diff(client, e)
    assert "scripts.yaml" in diff["diff"]
    assert "-alias: Modified" in diff["diff"]
    assert "+alias: Original" in diff["diff"]
    assert "new_after.yaml" in diff["diff"]
    # Compact excerpt instead of dumping the content payload.
    excerpt = e["details_excerpt"]
    assert excerpt["transaction_id"] == "txn_abc"
    assert excerpt["operation"] == "batch_apply"
    assert len(excerpt["restored"]) == 2
    assert excerpt["restored"][0]["action"] == "restore"
    assert excerpt["restored"][1]["action"] == "delete"


@pytest.mark.asyncio
async def test_timeline_batch_apply_failure_entry_flags_rollback(client, ctx):
    """Failed batch entries show FAILED prefix + rolled_back count."""
    await _log_audit_entry(
        ctx,
        tool="batch_apply",
        details={
            "items": [
                {
                    "tool": "config_patch",
                    "target": "/config/scripts.yaml",
                    "path": "/config/scripts.yaml",
                    "old_content": "a: 1\n",
                    "new_content": "a: 2\n",
                },
            ],
            "failed_at": {"tool": "dashboard_patch", "target": "lovelace",
                          "error": "WS dropped"},
            "rolled_back": [{"target": "/config/scripts.yaml",
                             "restored_from": "/backup/ha-ops-mcp/..."}],
        },
        success=False,
        error="WS dropped",
    )
    data = client.get("/api/ui/timeline").json()
    entries = [e for e in data["entries"] if e["tool"] == "haops_batch_apply"]
    assert entries
    e = entries[0]
    assert e["success"] is False
    # Generic failure summary wins via the short-circuit in
    # _summarise_audit_entry; the specific rollback info is in the excerpt
    # and in the per-item diff block.
    assert "failed" in e["summary"].lower()
    assert e["details_excerpt"]["rolled_back_count"] == 1
    assert e["details_excerpt"]["failed_at"]["tool"] == "dashboard_patch"
    # Per-item diff block carries the FAILED header even on failure entries.
    assert e["diff_present"] is True
    diff = _fetch_diff(client, e)
    assert "BATCH FAILED" in diff["diff"]


@pytest.mark.asyncio
async def test_timeline_config_create_summary(client, ctx):
    """config_create routes through config_apply; empty old_content → 'Created' summary."""
    await _log_audit_entry(
        ctx,
        tool="config_apply",
        details={
            "path": "/config/new_file.yaml",
            "old_content": "",
            "new_content": "key: value\n",
        },
        token_id="tok_create",
    )
    data = client.get("/api/ui/timeline").json()
    entries = [e for e in data["entries"]
               if e["tool"] == "haops_config_apply"
               and "new_file.yaml" in e["summary"]]
    assert entries
    assert entries[0]["summary"] == "Created /config/new_file.yaml"


@pytest.mark.asyncio
async def test_timeline_entity_remove_has_details_not_diff(client, ctx):
    """Non-diff tools (entity_remove) emit details_excerpt, no diff field."""
    await _log_audit_entry(
        ctx,
        tool="entity_remove",
        details={"entity_ids": ["sensor.foo", "sensor.bar"]},
        token_id="tok789",
    )
    data = client.get("/api/ui/timeline").json()
    entries = [e for e in data["entries"] if e["tool"] == "haops_entity_remove"]
    assert entries
    e = entries[0]
    assert e["summary"] == "Removed 2 entities"
    assert "diff" not in e
    assert e["diff_present"] is False
    assert e["details_excerpt"]["entity_ids"] == ["sensor.foo", "sensor.bar"]


@pytest.mark.asyncio
async def test_timeline_db_execute_sql_excerpt(client, ctx):
    """db_execute entries show the SQL via details_excerpt."""
    sql = "UPDATE states SET state = 'on' WHERE entity_id = 'light.x';"
    await _log_audit_entry(
        ctx,
        tool="db_execute",
        details={"sql": sql, "rowcount": 1},
    )
    data = client.get("/api/ui/timeline").json()
    entries = [e for e in data["entries"] if e["tool"] == "haops_db_execute"]
    assert entries
    e = entries[0]
    assert "UPDATE states" in e["summary"]
    assert "diff" not in e
    assert e["details_excerpt"]["sql"] == sql
    assert e["details_excerpt"]["rowcount"] == 1


@pytest.mark.asyncio
async def test_timeline_failure_entry_surfaces_error(client, ctx):
    """Failed entries carry the error and a 'failed' summary."""
    await _log_audit_entry(
        ctx,
        tool="config_apply",
        details={"path": "automations.yaml"},
        success=False,
    )
    # The log helper above doesn't pass error — write one directly for this case.
    await ctx.audit.log(
        tool="entity_remove",
        details={"entity_ids": ["sensor.x"]},
        success=False,
        error="HA returned 500 on delete",
    )
    data = client.get("/api/ui/timeline").json()
    failed = [e for e in data["entries"] if not e["success"]]
    assert failed
    # Find the one with the explicit error string
    with_error = [e for e in failed if e.get("error")]
    assert with_error
    assert "500" in with_error[0]["error"]
    assert "failed" in with_error[0]["summary"]


@pytest.mark.asyncio
async def test_timeline_backup_revert_config_renders_diff(client, ctx):
    """backup_revert for config type rebuilds the diff (current → backup)."""
    await _log_audit_entry(
        ctx,
        tool="backup_revert",
        details={
            "type": "config",
            "source_path": "/config/automations.yaml",
            "current_content": "current\nstate\n",
            "backup_content": "backup\nstate\n",
        },
    )
    data = client.get("/api/ui/timeline").json()
    entries = [e for e in data["entries"] if e["tool"] == "haops_backup_revert"]
    assert entries
    e = entries[0]
    assert "Reverted config" in e["summary"]
    assert e["diff_present"] is True
    diff = _fetch_diff(client, e)
    assert "-current" in diff["diff"]
    assert "+backup" in diff["diff"]


@pytest.mark.asyncio
async def test_timeline_limit_param(client, ctx):
    """?limit=N controls how many entries are returned."""
    for i in range(5):
        await _log_audit_entry(
            ctx,
            tool="config_apply",
            details={"path": f"file{i}.yaml", "old_content": "", "new_content": "x"},
        )
    res = client.get("/api/ui/timeline?limit=3")
    data = res.json()
    assert data["count"] <= 3


@pytest.mark.asyncio
async def test_timeline_pagination_offset_returns_older_slice(client, ctx):
    """offset=N skips the N newest entries; the page is the next limit slice."""
    # Log 25 entries; the audit log is newest-first so the LAST one
    # logged is at index 0.
    for i in range(25):
        await _log_audit_entry(
            ctx,
            tool="config_apply",
            details={
                "path": f"/c/{i}.yaml", "old_content": "", "new_content": "x",
            },
        )
    # Page 1: offset=0, limit=10 → newest 10. Last logged was i=24, so
    # entries[0] points at i=24.
    page1 = client.get("/api/ui/timeline?offset=0&limit=10").json()
    assert page1["count"] == 10
    assert page1["offset"] == 0
    assert page1["limit"] == 10
    assert page1["has_more"] is True
    # Page 2: offset=10, limit=10 → next 10 (i=14..i=5).
    page2 = client.get("/api/ui/timeline?offset=10&limit=10").json()
    assert page2["count"] == 10
    assert page2["offset"] == 10
    assert page2["has_more"] is True
    # Page 3: offset=20 → only 5 entries left, has_more=False.
    page3 = client.get("/api/ui/timeline?offset=20&limit=10").json()
    assert page3["count"] == 5
    assert page3["has_more"] is False
    # No overlap between pages (paths are unique per entry).
    p1_paths = {e["details_excerpt"]["path"] for e in page1["entries"]}
    p2_paths = {e["details_excerpt"]["path"] for e in page2["entries"]}
    assert p1_paths.isdisjoint(p2_paths)


@pytest.mark.asyncio
async def test_timeline_pagination_strips_txn_on_deeper_pages(client, ctx):
    """Only the most-recent successful apply across the WHOLE log gets a
    Revert button. If that entry is on page 1, every apply on page 2
    must have its transaction_id stripped — even though page 2's first
    apply would otherwise look like "the first" to the per-page loop.
    """
    # Three applies, each with a transaction_id. Logged oldest → newest.
    for txn_suffix, content in [("oldest", "a"), ("middle", "b"), ("newest", "c")]:
        await _log_audit_entry(
            ctx, tool="config_apply",
            details={
                "path": f"/c/{txn_suffix}.yaml",
                "old_content": "",
                "new_content": content,
                "transaction_id": f"txn_{txn_suffix}",
            },
        )
    # Page 1 with limit=1 holds only the newest. Page 2 holds the middle.
    page1 = client.get("/api/ui/timeline?offset=0&limit=1").json()
    assert page1["entries"][0].get("transaction_id") == "txn_newest"
    page2 = client.get("/api/ui/timeline?offset=1&limit=1").json()
    # The middle apply would normally be "the first qualifying" of its
    # page, but pagination must see across pages: a more-recent apply
    # exists, so this entry's txn_id is stripped.
    assert "transaction_id" not in page2["entries"][0]


@pytest.mark.asyncio
async def test_timeline_pagination_default_limit_is_50(client, ctx):
    """No query params → 50/page (matches header note + pagination UI)."""
    res = client.get("/api/ui/timeline").json()
    assert res["limit"] == 50
    assert res["offset"] == 0


@pytest.mark.asyncio
async def test_timeline_pagination_invalid_offset_clamps_to_zero(client, ctx):
    """Garbage offset values fall through to 0 instead of returning 400."""
    await _log_audit_entry(
        ctx, tool="config_apply",
        details={"path": "/c.yaml", "old_content": "", "new_content": "x"},
    )
    res = client.get("/api/ui/timeline?offset=junk").json()
    assert res["offset"] == 0
    assert res["count"] == 1


@pytest.mark.asyncio
async def test_timeline_truncates_giant_diff(client, ctx):
    """Huge diffs get the diff_truncated flag + a cap on inline size."""
    huge_new = "line\n" * 30_000  # well over the 60 KB cap
    await _log_audit_entry(
        ctx,
        tool="config_apply",
        details={
            "path": "huge.yaml",
            "old_content": "",
            "new_content": huge_new,
        },
    )
    data = client.get("/api/ui/timeline").json()
    entries = [e for e in data["entries"] if e["details_excerpt"].get("path") == "huge.yaml"]
    assert entries
    e = entries[0]
    # List response is small — no diff body, just diff_present flag.
    assert e["diff_present"] is True
    assert "diff" not in e
    assert "diff_truncated" not in e
    # Truncation surfaces only when the diff body is fetched.
    diff = _fetch_diff(client, e)
    assert diff["diff_truncated"] is True
    assert len(diff["diff"]) <= 60 * 1024 + 1


# ── /api/ui/timeline/diff (lazy diff) ─────────────────────────────────


def test_timeline_diff_rejects_missing_ts(client):
    res = client.get("/api/ui/timeline/diff")
    assert res.status_code == 400
    assert "ts" in res.json()["error"]


def test_timeline_diff_returns_404_for_unknown_entry(client):
    res = client.get(
        "/api/ui/timeline/diff", params={"ts": "2099-01-01T00:00:00Z"}
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_timeline_diff_returns_diff_body_for_config_apply(client, ctx):
    await _log_audit_entry(
        ctx,
        tool="config_apply",
        details={
            "path": "/config/x.yaml",
            "old_content": "alpha\n",
            "new_content": "beta\n",
        },
    )
    list_entry = client.get("/api/ui/timeline").json()["entries"][0]
    res = client.get(
        "/api/ui/timeline/diff",
        params={"ts": list_entry["timestamp"], "tool": list_entry["tool"]},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["diff_present"] is True
    assert body["diff_truncated"] is False
    assert "-alpha" in body["diff"]
    assert "+beta" in body["diff"]


@pytest.mark.asyncio
async def test_timeline_diff_accepts_bare_tool_name(client, ctx):
    """tool=config_apply (bare) and tool=haops_config_apply (prefixed) both work."""
    await _log_audit_entry(
        ctx,
        tool="config_apply",
        details={
            "path": "/c.yaml", "old_content": "a\n", "new_content": "b\n",
        },
    )
    ts = client.get("/api/ui/timeline").json()["entries"][0]["timestamp"]
    bare = client.get(
        "/api/ui/timeline/diff", params={"ts": ts, "tool": "config_apply"}
    )
    prefixed = client.get(
        "/api/ui/timeline/diff",
        params={"ts": ts, "tool": "haops_config_apply"},
    )
    assert bare.status_code == 200
    assert prefixed.status_code == 200
    assert bare.json() == prefixed.json()


@pytest.mark.asyncio
async def test_timeline_diff_returns_present_false_for_non_diff_tool(client, ctx):
    """entity_remove has no diff surface — endpoint returns diff_present=false."""
    await _log_audit_entry(
        ctx,
        tool="entity_remove",
        details={"entity_ids": ["sensor.x"]},
    )
    ts = client.get("/api/ui/timeline").json()["entries"][0]["timestamp"]
    res = client.get(
        "/api/ui/timeline/diff",
        params={"ts": ts, "tool": "entity_remove"},
    )
    assert res.status_code == 200
    assert res.json() == {"diff_present": False}


@pytest.mark.asyncio
async def test_timeline_list_excludes_diff_body(client, ctx):
    """Regression: the list response must never inline the diff body —
    that's the whole point of the lazy split. A diff_present flag is OK;
    a diff field is not.
    """
    await _log_audit_entry(
        ctx,
        tool="config_apply",
        details={
            "path": "/c.yaml", "old_content": "a\n", "new_content": "b\n",
        },
    )
    entries = client.get("/api/ui/timeline").json()["entries"]
    assert entries
    for e in entries:
        assert "diff" not in e
        assert "diff_truncated" not in e
        assert "diff_present" in e


# ── /api/ui/backups and backup_prune Timeline ────────────────────────────

@pytest.mark.asyncio
async def test_backups_endpoint_empty(client, ctx):
    res = client.get("/api/ui/backups")
    assert res.status_code == 200
    data = res.json()
    assert data["summary"]["total_count"] == 0
    assert data["summary"]["total_bytes"] == 0
    for t in ("config", "dashboard", "entity", "db"):
        assert data["summary"]["per_type"][t]["count"] == 0
    assert data["retention"]["max_age_days"] == ctx.config.backup.max_age_days
    assert data["retention"]["max_per_type"] == ctx.config.backup.max_per_type
    assert data["last_prune"] is None
    assert data["backup_dir"] == ctx.config.backup.dir


@pytest.mark.asyncio
async def test_backups_endpoint_aggregates_per_type(client, ctx):
    src = ctx.path_guard.config_root / "automations.yaml"
    src.write_text("foo\n")
    await ctx.backup.backup_file(src, operation="unit_test")
    await ctx.backup.backup_dashboard(
        "dash", {"title": "X"}, operation="unit_test"
    )

    data = client.get("/api/ui/backups").json()
    assert data["summary"]["total_count"] == 2
    assert data["summary"]["per_type"]["config"]["count"] == 1
    assert data["summary"]["per_type"]["dashboard"]["count"] == 1
    assert data["summary"]["per_type"]["config"]["bytes"] > 0
    assert data["summary"]["per_type"]["config"]["oldest_ts"] is not None
    assert data["summary"]["per_type"]["config"]["newest_ts"] is not None


@pytest.mark.asyncio
async def test_backups_endpoint_last_prune(client, ctx):
    await _log_audit_entry(
        ctx,
        tool="backup_prune",
        details={
            "older_than_days": 30,
            "type": "config",
            "clear_all": False,
            "deleted_count": 3,
            "bytes_freed": 12_345,
            "deleted": [
                {"id": "config_a", "source": "/config/a.yaml", "type": "config"},
                {"id": "config_b", "source": "/config/b.yaml", "type": "config"},
                {"id": "config_c", "source": "/config/c.yaml", "type": "config"},
            ],
        },
    )
    data = client.get("/api/ui/backups").json()
    assert data["last_prune"] is not None
    assert data["last_prune"]["pruned_count"] == 3
    assert data["last_prune"]["bytes_freed"] == 12_345
    assert data["last_prune"]["type"] == "config"
    assert data["last_prune"]["clear_all"] is False


@pytest.mark.asyncio
async def test_timeline_backup_prune_summary_and_excerpt(client, ctx):
    await _log_audit_entry(
        ctx,
        tool="backup_prune",
        details={
            "older_than_days": None,
            "type": "all",
            "clear_all": False,
            "deleted_count": 5,
            "bytes_freed": 2 * 1024 * 1024,
            "deleted": [
                {"id": f"config_{i}", "source": "/x.yaml", "type": "config"}
                for i in range(5)
            ],
        },
        token_id="tok_prune",
    )
    entries = [
        e for e in client.get("/api/ui/timeline").json()["entries"]
        if e["tool"] == "haops_backup_prune"
    ]
    assert entries
    e = entries[0]
    # Summary surfaces count + size.
    assert "Pruned 5 backup(s)" in e["summary"]
    assert "2.0 MB" in e["summary"]
    # Excerpt is compact — totals only, no deleted list.
    excerpt = e["details_excerpt"]
    assert excerpt["deleted_count"] == 5
    assert excerpt["bytes_freed"] == 2 * 1024 * 1024
    assert "deleted" not in excerpt
    # No diff field — prune has no content diff to show.
    assert "diff" not in e or not e["diff"]


@pytest.mark.asyncio
async def test_backup_prune_post_preview(client, ctx):
    """POST with execute=false returns dry-run preview + count/bytes.

    Mirrors the UI's Phase 1 call — never mutates the manifest.
    """
    src = ctx.path_guard.config_root / "automations.yaml"
    src.write_text("x\n")
    await ctx.backup.backup_file(src, operation="unit")
    # Force every entry into scope by demanding an impossibly tight age.
    res = client.post(
        "/api/ui/backup_prune",
        json={"execute": False, "older_than_days": 0},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["count"] >= 0
    assert "would_delete" in data
    # Manifest NOT rewritten during preview. Raw audit reads the bare
    # tool name (no haops_ prefix); only the Timeline endpoint prefixes.
    audit_entries_for_prune = [
        e for e in ctx.audit.read_recent(limit=10)
        if e["tool"] == "backup_prune"
    ]
    assert audit_entries_for_prune == []


@pytest.mark.asyncio
async def test_backup_prune_post_execute_audits_with_sidebar_source(client, ctx):
    """POST with execute=true actually prunes AND logs source='sidebar'."""
    src = ctx.path_guard.config_root / "automations.yaml"
    src.write_text("x\n")
    await ctx.backup.backup_file(src, operation="unit")

    res = client.post(
        "/api/ui/backup_prune",
        json={
            "execute": True,
            "clear_all": True,
            "type": "config",
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["count"] >= 1

    # Audit entry source marker. Raw audit reads the bare tool name
    # (no haops_ prefix); only the Timeline endpoint prefixes.
    entries = [
        e for e in ctx.audit.read_recent(limit=10)
        if e["tool"] == "backup_prune"
    ]
    assert entries
    assert entries[0]["details"]["source"] == "sidebar"
    assert entries[0]["details"]["clear_all"] is True
    assert entries[0]["details"]["type"] == "config"


@pytest.mark.asyncio
async def test_backup_prune_post_rejects_unknown_type(client, ctx):
    res = client.post(
        "/api/ui/backup_prune",
        json={"execute": False, "type": "nonsense"},
    )
    assert res.status_code == 400
    assert "Invalid type" in res.json()["error"]


@pytest.mark.asyncio
async def test_backup_prune_post_rejects_bad_older_than_days(client, ctx):
    res = client.post(
        "/api/ui/backup_prune",
        json={"execute": False, "older_than_days": "30"},  # string not int
    )
    assert res.status_code == 400


@pytest.mark.asyncio
async def test_backup_prune_post_unauthorized_non_loopback(ctx):
    """Non-loopback POST without bearer is rejected (auth is symmetric
    across read and write endpoints)."""
    from unittest.mock import patch

    mcp = FastMCP("test")
    register_ui_routes(mcp, ctx)
    app = Starlette(routes=mcp._custom_starlette_routes)
    # TestClient reports loopback, so monkeypatch the auth predicate.
    with patch("ha_ops_mcp.ui.routes._is_authorized", return_value=False):
        c = TestClient(app)
        res = c.post("/api/ui/backup_prune", json={"execute": False})
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_timeline_pairs_apply_and_rollback(client, ctx):
    """Apply + its rollback cross-link via paired_with (v0.19.0)."""
    # Log the rollback FIRST because audit log is newest-first; in the
    # timeline response the rollback ends up at an earlier index than
    # the apply it reverses. Mirrors real-world sequencing (apply
    # happens first in time; rollback later).
    await _log_audit_entry(
        ctx, tool="config_apply",
        details={
            "path": "/x.yaml",
            "old_content": "old\n",
            "new_content": "new\n",
            "transaction_id": "txn_pair_1",
        },
    )
    await _log_audit_entry(
        ctx, tool="rollback",
        details={
            "transaction_id": "txn_pair_1",
            "operation": "config_apply",
            "restored": [],
        },
    )

    data = client.get("/api/ui/timeline").json()
    # Rollback is newest → index 0; apply is older → index 1.
    rollback = data["entries"][0]
    apply_row = data["entries"][1]
    assert rollback["tool"] == "haops_rollback"
    assert apply_row["tool"] == "haops_config_apply"

    assert apply_row["paired_with"]["index"] == 0
    assert apply_row["paired_with"]["tool"] == "haops_rollback"
    assert apply_row["paired_with"]["relation"] == "rolled_back_by"

    assert rollback["paired_with"]["index"] == 1
    assert rollback["paired_with"]["tool"] == "haops_config_apply"
    assert rollback["paired_with"]["relation"] == "reverts"


@pytest.mark.asyncio
async def test_timeline_leaves_lone_apply_unpaired(client, ctx):
    """Apply without a matching rollback carries no paired_with."""
    await _log_audit_entry(
        ctx, tool="config_apply",
        details={
            "path": "/x.yaml",
            "old_content": "a\n",
            "new_content": "b\n",
            "transaction_id": "txn_lone",
        },
    )
    data = client.get("/api/ui/timeline").json()
    apply_row = data["entries"][0]
    assert "paired_with" not in apply_row


@pytest.mark.asyncio
async def test_timeline_prefixes_known_tools_with_haops(client, ctx):
    """Display names on Timeline rows carry haops_ prefix (v0.19.0)."""
    await _log_audit_entry(
        ctx, tool="config_apply",
        details={"path": "/c.yaml", "old_content": "a\n", "new_content": "b\n"},
    )
    await _log_audit_entry(
        ctx, tool="exec_shell",
        details={"command": "ls /config"},
    )
    await _log_audit_entry(
        ctx, tool="weirdo_not_in_allowlist",
        details={"whatever": 1},
    )

    data = client.get("/api/ui/timeline").json()
    display_tools = {e["tool"] for e in data["entries"]}
    assert "haops_config_apply" in display_tools
    assert "haops_exec_shell" in display_tools
    # Unknown tools fall through unchanged — safer than guessing.
    assert "weirdo_not_in_allowlist" in display_tools
    # The bare forms aren't used in Timeline display.
    assert "config_apply" not in display_tools
    assert "exec_shell" not in display_tools


@pytest.mark.asyncio
async def test_timeline_revert_only_on_most_recent_apply(client, ctx):
    """v0.19.1: only the most recent successful apply exposes transaction_id
    (Revert button). Older applies have it stripped because HA may have
    re-serialized the file or the user edited outside ha-ops, making the
    savepoint's old_content stale.
    """
    # Log three applies in order (oldest → newest).
    await _log_audit_entry(
        ctx, tool="config_apply",
        details={
            "path": "/config/x.yaml",
            "old_content": "a\n",
            "new_content": "b\n",
            "transaction_id": "txn_oldest",
        },
    )
    await _log_audit_entry(
        ctx, tool="dashboard_apply",
        details={
            "dashboard_id": "lovelace",
            "old_config": {"title": "A"},
            "new_config": {"title": "B"},
            "transaction_id": "txn_middle",
        },
    )
    await _log_audit_entry(
        ctx, tool="batch_apply",
        details={"items": [], "transaction_id": "txn_newest"},
    )
    # Failed apply — never gets transaction_id regardless.
    await _log_audit_entry(
        ctx, tool="config_apply",
        details={
            "path": "/config/y.yaml",
            "old_content": "",
            "new_content": "",
            "transaction_id": "txn_failed",
        },
        success=False,
    )
    # Rollback row — not an apply, no Revert button.
    await _log_audit_entry(
        ctx, tool="rollback",
        details={
            "transaction_id": "txn_rb",
            "operation": "config_apply",
            "restored": [],
        },
    )

    data = client.get("/api/ui/timeline").json()

    # Entries are newest-first. The most recent *successful* apply is
    # batch_apply (txn_newest) — only IT gets transaction_id.
    apply_rows = [
        e for e in data["entries"]
        if e["tool"] in (
            "haops_config_apply", "haops_dashboard_apply",
            "haops_batch_apply",
        ) and e["success"]
    ]
    assert len(apply_rows) == 3
    # First (newest) keeps its transaction_id.
    assert apply_rows[0].get("transaction_id") == "txn_newest"
    # Older applies have it stripped.
    assert "transaction_id" not in apply_rows[1]
    assert "transaction_id" not in apply_rows[2]

    # Failed apply row never gets it.
    failed = [e for e in data["entries"] if not e["success"]]
    assert not any(r.get("transaction_id") for r in failed)

    # Rollback row doesn't get a Revert button either.
    rollback = [e for e in data["entries"] if e["tool"] == "haops_rollback"]
    assert not any(r.get("transaction_id") for r in rollback)


@pytest.mark.asyncio
async def test_rollback_post_preview_shape(client, ctx):
    """Preview returns per-target action/diff summary and transaction metadata."""
    from unittest.mock import AsyncMock

    from ha_ops_mcp.tools.batch import haops_batch_apply, haops_batch_preview
    from ha_ops_mcp.utils.diff import unified_diff

    automations = ctx.path_guard.config_root / "automations.yaml"
    original = "automation:\n  - id: '1'\n    alias: Foo\n"
    automations.write_text(original)
    patch = unified_diff(original, original.replace("Foo", "Bar"), "automations.yaml")

    preview = await haops_batch_preview(ctx, items=[
        {"tool": "config_patch", "path": "automations.yaml", "patch": patch},
    ])
    ctx.ws.send_command = AsyncMock(return_value=None)
    applied = await haops_batch_apply(ctx, token=preview["token"])
    txn_id = applied["transaction_id"]

    res = client.post(
        "/api/ui/rollback",
        json={"transaction_id": txn_id, "execute": False},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["transaction_id"] == txn_id
    assert data["operation"] == "batch_apply"
    assert data["count"] == 1
    assert data["targets"][0]["action"] == "restore content"
    assert "Foo" in data["targets"][0]["diff"]


@pytest.mark.asyncio
async def test_rollback_post_execute_restores_and_audits_sidebar(client, ctx):
    from unittest.mock import AsyncMock

    from ha_ops_mcp.tools.batch import haops_batch_apply, haops_batch_preview
    from ha_ops_mcp.utils.diff import unified_diff

    automations = ctx.path_guard.config_root / "automations.yaml"
    original = "automation:\n  - id: '1'\n    alias: Foo\n"
    automations.write_text(original)
    patch = unified_diff(original, original.replace("Foo", "Bar"), "automations.yaml")

    preview = await haops_batch_preview(ctx, items=[
        {"tool": "config_patch", "path": "automations.yaml", "patch": patch},
    ])
    ctx.ws.send_command = AsyncMock(return_value=None)
    applied = await haops_batch_apply(ctx, token=preview["token"])
    # Intermediate state: Bar.
    assert "Bar" in automations.read_text()

    res = client.post(
        "/api/ui/rollback",
        json={"transaction_id": applied["transaction_id"], "execute": True},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    # File is back to Foo.
    assert "Foo" in automations.read_text()

    # Audit entry tagged source=sidebar.
    # Raw audit — bare tool name (no haops_ prefix).
    entries = [
        e for e in ctx.audit.read_recent(limit=10)
        if e["tool"] == "rollback"
    ]
    assert entries
    assert entries[0]["details"]["source"] == "sidebar"


@pytest.mark.asyncio
async def test_rollback_post_unknown_txn(client, ctx):
    res = client.post(
        "/api/ui/rollback",
        json={"transaction_id": "does_not_exist", "execute": False},
    )
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_rollback_post_uncommitted_txn(client, ctx):
    pending = ctx.rollback.begin("manual_test_only")
    res = client.post(
        "/api/ui/rollback",
        json={"transaction_id": pending.id, "execute": False},
    )
    assert res.status_code == 409
    assert "not committed" in res.json()["error"]


@pytest.mark.asyncio
async def test_rollback_post_already_rolled_back(client, ctx):
    from unittest.mock import AsyncMock

    from ha_ops_mcp.tools.batch import haops_batch_apply, haops_batch_preview
    from ha_ops_mcp.utils.diff import unified_diff

    automations = ctx.path_guard.config_root / "automations.yaml"
    automations.write_text("automation:\n  - id: '1'\n    alias: Foo\n")
    patch = unified_diff(
        automations.read_text(),
        "automation:\n  - id: '1'\n    alias: Bar\n",
        "automations.yaml",
    )
    preview = await haops_batch_preview(ctx, items=[
        {"tool": "config_patch", "path": "automations.yaml", "patch": patch},
    ])
    ctx.ws.send_command = AsyncMock(return_value=None)
    applied = await haops_batch_apply(ctx, token=preview["token"])
    txn_id = applied["transaction_id"]

    # Consume the savepoints once.
    r1 = client.post(
        "/api/ui/rollback",
        json={"transaction_id": txn_id, "execute": True},
    )
    assert r1.status_code == 200
    # Second call: savepoints already rolled back.
    r2 = client.post(
        "/api/ui/rollback",
        json={"transaction_id": txn_id, "execute": False},
    )
    assert r2.status_code == 409
    assert "already rolled back" in r2.json()["error"]


@pytest.mark.asyncio
async def test_rollback_post_rejects_bad_payload(client, ctx):
    # No transaction_id.
    r1 = client.post("/api/ui/rollback", json={"execute": False})
    assert r1.status_code == 400
    # Non-JSON body.
    r2 = client.post("/api/ui/rollback", content="not json")
    assert r2.status_code == 400


@pytest.mark.asyncio
async def test_timeline_backup_prune_clear_all_summary(client, ctx):
    await _log_audit_entry(
        ctx,
        tool="backup_prune",
        details={
            "older_than_days": None,
            "type": "dashboard",
            "clear_all": True,
            "deleted_count": 12,
            "bytes_freed": 500,
            "deleted": [],
        },
    )
    entries = [
        e for e in client.get("/api/ui/timeline").json()["entries"]
        if e["tool"] == "haops_backup_prune"
    ]
    assert entries
    summary = entries[0]["summary"]
    assert "clear_all" in summary
    assert "type=dashboard" in summary
    assert "Pruned 12 backup(s)" in summary
