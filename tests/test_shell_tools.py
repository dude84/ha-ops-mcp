"""Tests for shell execution tool."""

from __future__ import annotations

import pytest

from ha_ops_mcp.tools.shell import haops_exec_shell

# ── Shell tool tests ──


@pytest.mark.asyncio
async def test_exec_shell_preview(ctx):
    result = await haops_exec_shell(ctx, command="ls -la /config/")
    assert "token" in result
    assert result["command"] == "ls -la /config/"


@pytest.mark.asyncio
async def test_exec_shell_confirm(ctx):
    preview = await haops_exec_shell(ctx, command="echo hello")
    result = await haops_exec_shell(
        ctx,
        command="echo hello",
        confirm=True,
        token=preview["token"],
        cwd="/tmp",
    )
    assert result["exit_code"] == 0
    assert "hello" in result["stdout"]


@pytest.mark.asyncio
async def test_exec_shell_command_mismatch(ctx):
    preview = await haops_exec_shell(ctx, command="echo hello")
    result = await haops_exec_shell(
        ctx,
        command="echo different",
        confirm=True,
        token=preview["token"],
    )
    assert "error" in result
    assert "does not match" in result["error"]


@pytest.mark.asyncio
async def test_exec_shell_no_token(ctx):
    result = await haops_exec_shell(
        ctx, command="echo test", confirm=True
    )
    assert "error" in result


@pytest.mark.asyncio
async def test_exec_shell_timeout_capped(ctx):
    preview = await haops_exec_shell(
        ctx, command="echo test", timeout=999
    )
    assert preview["timeout"] == 300  # capped at 300


@pytest.mark.asyncio
async def test_exec_shell_persists_output(ctx):
    preview = await haops_exec_shell(ctx, command="echo persistme")
    await haops_exec_shell(
        ctx, command="echo persistme", confirm=True,
        token=preview["token"], cwd="/tmp",
    )
    # The most recent run is retrievable from the store with the output.
    entries = ctx.shell_output.list_entries(limit=5)
    assert entries, "expected a persisted shell run"
    latest = entries[0]
    assert latest.command == "echo persistme"
    out = ctx.shell_output.read_output(latest.id)
    assert out is not None
    assert "persistme" in out["stdout"]


@pytest.mark.asyncio
async def test_exec_shell_audit_carries_output_id(ctx):
    preview = await haops_exec_shell(ctx, command="echo audit")
    await haops_exec_shell(
        ctx, command="echo audit", confirm=True,
        token=preview["token"], cwd="/tmp",
    )
    recent = ctx.audit.read_recent(limit=10)
    shell_rows = [e for e in recent if e.get("tool") == "exec_shell"]
    assert shell_rows, "expected an exec_shell audit row"
    details = shell_rows[0].get("details") or {}
    assert isinstance(details.get("output_id"), str) and details["output_id"]
    # The output_id resolves to a real stored run.
    assert ctx.shell_output.get(details["output_id"]) is not None
