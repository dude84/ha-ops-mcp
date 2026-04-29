"""Shell execution tool — haops_exec_shell (superuser)."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from ha_ops_mcp.server import registry

if TYPE_CHECKING:
    from ha_ops_mcp.server import HaOpsContext


@registry.tool(
    name="haops_exec_shell",
    description=(
        "SUPERUSER TOOL: Execute a shell command in the server's environment. "
        "If ha-ops-mcp runs as an HA add-on, this executes inside the add-on "
        "container with access to /config/ and the host network. "
        "Two-phase: call without confirm to preview the command. "
        "Call with confirm=true and the token to execute. "
        "Output (stdout + stderr) is captured and returned. "
        "Use cases: check disk space, list files, grep for patterns, inspect "
        "ESPHome configs, view add-on data, run diagnostics. "
        "This tool has NO safety net beyond two-phase confirmation — the "
        "caller is responsible for understanding what the command does. "
        "Parameters: command (string, required), "
        "cwd (string, default '/config/'), "
        "timeout (int, seconds, default 30, max 300), "
        "confirm (bool, default false), "
        "token (string, required if confirm=true)."
    ),
    params={
        "command": {
            "type": "string",
            "description": "Shell command to execute",
        },
        "cwd": {
            "type": "string",
            "description": "Working directory",
            "default": "/config/",
        },
        "timeout": {
            "type": "integer",
            "description": "Timeout in seconds (max 300)",
            "default": 30,
        },
        "confirm": {
            "type": "boolean",
            "description": "Execute the command",
            "default": False,
        },
        "token": {
            "type": "string",
            "description": "Confirmation token from preview step",
        },
    },
)
async def haops_exec_shell(
    ctx: HaOpsContext,
    command: str,
    cwd: str = "/config/",
    timeout: int = 30,
    confirm: bool = False,
    token: str | None = None,
) -> dict[str, Any]:
    timeout = min(timeout, 300)

    if not confirm:
        tk = ctx.safety.create_token(
            action="exec_shell",
            details={"command": command, "cwd": cwd, "timeout": timeout},
        )

        return {
            "command": command,
            "cwd": cwd,
            "timeout": timeout,
            "token": tk.id,
            "message": "Review the command. Call again with "
            "confirm=true and this token to execute.",
        }

    # Phase 2: execute
    if token is None:
        return {"error": "confirm=true requires a token"}

    try:
        token_data = ctx.safety.validate_token(token)
    except Exception as e:
        return {"error": str(e)}

    if token_data.details.get("command") != command:
        return {"error": "Command does not match the token. Re-run the preview."}

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=timeout
        )
    except TimeoutError:
        proc.kill()
        ctx.safety.consume_token(token)
        return {
            "error": f"Command timed out after {timeout}s",
            "command": command,
        }
    except FileNotFoundError:
        return {"error": f"Working directory not found: {cwd}"}
    except Exception as e:
        return {"error": f"Execution failed: {e}"}

    ctx.safety.consume_token(token)

    await ctx.audit.log(
        tool="exec_shell",
        details={
            "command": command,
            "cwd": cwd,
            "exit_code": proc.returncode,
        },
        token_id=token,
    )

    response: dict[str, Any] = {
        "exit_code": proc.returncode,
        "stdout": stdout.decode(errors="replace").rstrip(),
        "stderr": stderr.decode(errors="replace").rstrip(),
    }

    # Truncate very large output
    for key in ("stdout", "stderr"):
        if len(response[key]) > 50000:
            response[key] = response[key][:50000] + "\n... (truncated)"
            response[f"{key}_truncated"] = True

    return response
