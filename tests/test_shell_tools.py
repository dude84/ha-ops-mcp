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
