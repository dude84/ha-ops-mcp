"""Integration tests — end-to-end tool flows using the full HaOpsContext.

These tests exercise complete user workflows: query DB, edit config with
patch/apply/rollback, audit entities, search configs, manage backups, etc.
All using the real safety/rollback/audit infrastructure against mocked
HA connections and an in-memory SQLite DB.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# ── Full config edit flow: read → patch → apply → verify → revert ──


@pytest.mark.asyncio
async def test_config_edit_full_flow(ctx):
    """End-to-end: read config, patch it, apply, verify, then revert via backup."""
    from ha_ops_mcp.tools.backup import haops_backup_list
    from ha_ops_mcp.tools.config import (
        haops_config_apply,
        haops_config_patch,
        haops_config_read,
    )
    from ha_ops_mcp.utils.diff import unified_diff

    # 1. Read current config
    read_result = await haops_config_read(ctx, path="configuration.yaml")
    assert "homeassistant" in read_result["content"]
    assert read_result["size_bytes"] > 0

    # 2. Patch a change
    original = read_result["content"]
    target = original.replace("name: Test Home", "name: Integration Test Home")
    patch = unified_diff(original, target, "configuration.yaml")
    patch_result = await haops_config_patch(
        ctx, path="configuration.yaml", patch=patch
    )
    assert "token" in patch_result
    assert "Integration Test Home" in patch_result["diff"]

    # 3. Apply the change
    apply_result = await haops_config_apply(ctx, token=patch_result["token"])
    assert apply_result["success"] is True
    assert apply_result.get("backup_path") is not None
    assert apply_result.get("transaction_id") is not None

    # 4. Verify the file changed
    verify = await haops_config_read(ctx, path="configuration.yaml")
    assert "Integration Test Home" in verify["content"]

    # 5. Check backup was created
    backups = await haops_backup_list(ctx, type="config")
    assert backups["count"] >= 1
    assert any("configuration.yaml" in b["source"] for b in backups["backups"])

    # 6. Verify audit log was written
    audit_path = ctx.audit._log_path
    assert audit_path.exists()
    audit_lines = audit_path.read_text().splitlines()
    assert any("config_apply" in line for line in audit_lines)


# ── DB query + health flow ──


@pytest.mark.asyncio
async def test_db_query_and_health_flow(ctx):
    """Query the DB, check health, run statistics commands."""
    from ha_ops_mcp.tools.db import haops_db_health, haops_db_query, haops_db_statistics

    # 1. Health check
    health = await haops_db_health(ctx)
    assert health["backend"] == "sqlite"
    assert health["schema_version"] == 43
    states_table = next(t for t in health["tables"] if t["name"] == "states")
    assert states_table["row_count"] == 3

    # 2. Query
    query = await haops_db_query(
        ctx, sql="SELECT entity_id, state FROM states ORDER BY state_id"
    )
    assert query["row_count"] == 3
    assert query["rows"][0]["entity_id"] == "sensor.temperature"
    assert query["rows"][0]["state"] == "22.5"

    # 3. Statistics list (empty, but should work)
    stats = await haops_db_statistics(ctx, command="list")
    assert "statistics" in stats

    # 4. Statistics orphans
    orphans = await haops_db_statistics(ctx, command="orphans")
    assert "orphans" in orphans


# ── DB execute two-phase flow ──


@pytest.mark.asyncio
async def test_db_execute_two_phase(ctx):
    """Preview → confirm → verify a DB write."""
    from ha_ops_mcp.tools.db import haops_db_execute, haops_db_query

    # Insert a row
    sql = "INSERT INTO states (entity_id, state, last_updated_ts) VALUES ('sensor.test', '99', 0)"

    # Preview
    preview = await haops_db_execute(ctx, sql=sql)
    assert "token" in preview
    assert "explain" in preview

    # Confirm
    result = await haops_db_execute(ctx, sql=sql, confirm=True, token=preview["token"])
    assert result["success"] is True
    assert result["affected_rows"] == 1

    # Verify
    query = await haops_db_query(ctx, sql="SELECT * FROM states WHERE entity_id = 'sensor.test'")
    assert query["row_count"] == 1
    assert query["rows"][0]["state"] == "99"


# ── Entity audit + filtering flow ──


@pytest.mark.asyncio
async def test_entity_audit_and_filter_flow(ctx):
    """Audit entities, then filter to find specific problems."""
    from ha_ops_mcp.tools.entity import haops_entity_audit, haops_entity_list

    # 1. Full audit
    audit = await haops_entity_audit(ctx)
    assert audit["summary"]["total_entities"] == 3
    assert audit["summary"]["unavailable"] >= 1
    assert audit["summary"]["orphaned"] >= 1

    # 2. Filter to unavailable only
    unavail = await haops_entity_list(ctx, state="unavailable")
    assert unavail["count"] >= 1
    assert all(
        e["state"] == "unavailable" for e in unavail["entities"]
    )

    # 3. Filter by domain
    sensors = await haops_entity_list(ctx, domain="sensor")
    assert all(
        e["entity_id"].startswith("sensor.") for e in sensors["entities"]
    )

    # 4. Filter by integration
    hue = await haops_entity_list(ctx, integration="hue")
    assert hue["count"] == 1
    assert hue["entities"][0]["entity_id"] == "light.living_room"


# ── Config search flow ──


@pytest.mark.asyncio
async def test_config_search_flow(ctx):
    """Search across config files for patterns."""
    from ha_ops_mcp.tools.config import haops_config_search

    # Search for a known string
    result = await haops_config_search(ctx, pattern="Test Home")
    assert result["count"] >= 1
    assert any(
        m["file"] == "configuration.yaml" for m in result["matches"]
    )

    # Search with regex
    result = await haops_config_search(ctx, pattern=r"alias:\s+\w+")
    assert result["count"] >= 1


# ── Secrets redaction flow ──


@pytest.mark.asyncio
async def test_secrets_handling(ctx):
    """Verify secrets are redacted by default, visible when opted out."""
    from ha_ops_mcp.tools.config import haops_config_read

    # Default: redacted
    redacted = await haops_config_read(ctx, path="secrets.yaml")
    assert "<REDACTED>" in redacted["content"]
    assert "supersecret123" not in redacted["content"]

    # Opt out
    visible = await haops_config_read(ctx, path="secrets.yaml", redact_secrets=False)
    assert "supersecret123" in visible["content"]


# ── Shell execution flow ──


@pytest.mark.asyncio
async def test_shell_execution_flow(ctx):
    """Preview → confirm → execute a shell command."""
    from ha_ops_mcp.tools.shell import haops_exec_shell

    # 1. Preview
    preview = await haops_exec_shell(ctx, command="echo integration_test")
    assert "token" in preview

    # 2. Execute
    result = await haops_exec_shell(
        ctx,
        command="echo integration_test",
        confirm=True,
        token=preview["token"],
        cwd="/tmp",
    )
    assert result["exit_code"] == 0
    assert "integration_test" in result["stdout"]


# ── System info + logs flow ──


@pytest.mark.asyncio
async def test_system_info_and_logs(ctx):
    """Get system info and filtered logs."""
    from ha_ops_mcp.tools.system import haops_system_info, haops_system_logs

    info = await haops_system_info(ctx)
    assert info["ha_version"] == "2026.4.1"
    assert info["database"]["backend"] == "sqlite"
    assert info["entity_count"] == 3

    # All logs
    logs = await haops_system_logs(ctx)
    assert logs["count"] == 2

    # Filter by level
    errors = await haops_system_logs(ctx, level="error")
    assert errors["count"] == 1
    assert "ERROR" in errors["lines"][0]


# ── Rollback verification ──


@pytest.mark.asyncio
async def test_rollback_state_tracking(ctx):
    """Verify rollback transactions are tracked during mutations."""
    from ha_ops_mcp.tools.config import haops_config_apply, haops_config_patch
    from ha_ops_mcp.utils.diff import unified_diff

    original = (ctx.path_guard.config_root / "configuration.yaml").read_text()
    target = original.replace("name: Test Home", "name: Rollback Test")
    patch = unified_diff(original, target, "configuration.yaml")
    patched = await haops_config_patch(ctx, path="configuration.yaml", patch=patch)
    apply = await haops_config_apply(ctx, token=patched["token"])

    # Transaction should exist
    txn_id = apply["transaction_id"]
    txn = ctx.rollback.get_transaction(txn_id)
    assert txn is not None
    assert txn.committed is True
    assert len(txn.savepoints) == 1
    assert txn.savepoints[0].undo.data["path"].endswith("configuration.yaml")


# ── Path guard integration ──


@pytest.mark.asyncio
async def test_path_guard_blocks_traversal(ctx):
    """Verify path guard blocks traversal through config tools."""
    from ha_ops_mcp.safety.path_guard import PathTraversalError
    from ha_ops_mcp.tools.config import haops_config_read

    with pytest.raises(PathTraversalError):
        await haops_config_read(ctx, path="../../etc/shadow")

    with pytest.raises(PathTraversalError):
        await haops_config_read(ctx, path="/etc/passwd")


# ── Token expiry and reuse ──


@pytest.mark.asyncio
async def test_token_single_use(ctx):
    """Verify tokens can't be reused."""
    from ha_ops_mcp.tools.config import haops_config_apply, haops_config_patch
    from ha_ops_mcp.utils.diff import unified_diff

    original = (ctx.path_guard.config_root / "configuration.yaml").read_text()
    target = original.replace("name: Test Home", "name: Token Test")
    patch = unified_diff(original, target, "configuration.yaml")
    patched = await haops_config_patch(ctx, path="configuration.yaml", patch=patch)
    token = patched["token"]

    # First use succeeds
    result1 = await haops_config_apply(ctx, token=token)
    assert result1["success"] is True

    # Second use fails
    result2 = await haops_config_apply(ctx, token=token)
    assert "error" in result2


# ── Full server startup ──


def test_server_creates_with_all_tools(tmp_path: Path):
    """Verify the server starts and registers all expected tools."""
    from ha_ops_mcp.server import create_server, registry

    config_file = tmp_path / "config.local.yaml"
    config_file.write_text(
        "ha:\n  url: http://test:8123\n  token: test\n"
        f"filesystem:\n  config_root: {tmp_path}\n"
        f"backup:\n  dir: {tmp_path}/backups\n"
        "database:\n  auto_detect: false\n"
    )

    mcp, ctx = create_server(config_file)
    tools = registry.all_tools()

    tool_names = {name for name, _, _ in tools}

    # Spot-check critical tools exist
    assert "haops_db_query" in tool_names
    assert "haops_config_read" in tool_names
    assert "haops_entity_audit" in tool_names
    assert "haops_system_info" in tool_names
    assert "haops_exec_shell" in tool_names
    assert "haops_addon_list" in tool_names
    assert "haops_backup_revert" in tool_names
    assert "haops_dashboard_list" in tool_names
    assert "haops_service_call" in tool_names

    # Total tool count
    assert len(tools) >= 30
