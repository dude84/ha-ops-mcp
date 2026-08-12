"""Container tools — reach sibling containers via the host Docker socket.

These exist so the add-on can borrow capabilities it doesn't ship: run the
ESPHome compiler that lives in the ESPHome add-on, inspect a container that
exposes no API, read another add-on's logs when Supervisor's own log endpoint
is unhelpful.

All three tools are inert unless the socket is present — see
``connections/docker.py`` for why that needs a manifest capability *and*
Protection mode off. When it's missing they return the remedy instead of a
generic failure, because the fix is a checkbox the user has to find.
"""

from __future__ import annotations

import shlex
from typing import TYPE_CHECKING, Any

from ha_ops_mcp.connections.docker import DockerError, DockerUnavailableError
from ha_ops_mcp.server import registry

if TYPE_CHECKING:
    from ha_ops_mcp.server import HaOpsContext

# Model-response cap. The full output is not persisted anywhere (unlike
# haops_exec_shell, which has a shell-output store), so this is the only copy —
# keep it generous.
_MAX_OUTPUT = 50000


def _unavailable(ctx: HaOpsContext) -> dict[str, Any] | None:
    """Return a structured 'not enabled' response, or None if Docker works."""
    if ctx.docker is not None and ctx.docker.available():
        return None
    reason = (
        ctx.docker.unavailable_reason
        if ctx.docker is not None
        else "Docker client not initialised."
    )
    return {
        "error": reason,
        "docker_available": False,
        "how_to_enable": [
            "Add-on manifest must declare 'docker_api: true' (ha-ops-mcp does, "
            "since v0.57.0).",
            "In Home Assistant: Settings > Add-ons > HA Ops MCP > Info tab, turn "
            "OFF 'Protection mode'.",
            "Restart the add-on.",
        ],
    }


@registry.tool(
    name="haops_container_list",
    description=(
        "READ-ONLY: List Docker containers on the HA host — add-ons, HA Core, "
        "the Supervisor, and anything else running. "
        "Use this to discover a container's name/id before calling "
        "haops_container_exec or haops_container_logs, or to check whether "
        "another add-on is actually running. "
        "Returns id (12-char short form), name, image, state ('running', "
        "'exited', ...) and status text for each container. "
        "Requires the Docker socket: 'docker_api: true' in the manifest AND "
        "Protection mode OFF on the add-on. If it is not enabled the response "
        "explains exactly how to enable it instead of failing opaquely. "
        "Parameters: running_only (bool, default false — set true to hide "
        "stopped containers), name_filter (string, optional substring match on "
        "container name or image)."
    ),
    params={
        "running_only": {
            "type": "boolean",
            "description": "Only list running containers",
            "default": False,
        },
        "name_filter": {
            "type": "string",
            "description": "Substring match on container name or image",
        },
    },
)
async def haops_container_list(
    ctx: HaOpsContext,
    running_only: bool = False,
    name_filter: str | None = None,
) -> dict[str, Any]:
    unavailable = _unavailable(ctx)
    if unavailable:
        return unavailable

    assert ctx.docker is not None
    try:
        containers = await ctx.docker.containers(all_containers=not running_only)
    except DockerUnavailableError as e:
        return {"error": str(e), "docker_available": False}
    except DockerError as e:
        return {"error": str(e)}

    if name_filter:
        needle = name_filter.lower()
        containers = [
            c
            for c in containers
            if needle in (c.get("name") or "").lower()
            or needle in (c.get("image") or "").lower()
        ]

    # Labels are bulky and only occasionally useful; surface just the add-on
    # slug, which is what identifies an HA add-on container.
    for c in containers:
        labels = c.pop("labels", {}) or {}
        slug = labels.get("io.hass.name") or labels.get("io.hass.type")
        if slug:
            c["hass"] = slug

    await ctx.audit.log(
        tool="container_list",
        details={"count": len(containers), "running_only": running_only},
    )

    return {
        "containers": containers,
        "count": len(containers),
        "docker_available": True,
    }


@registry.tool(
    name="haops_container_logs",
    description=(
        "READ-ONLY: Read recent stdout/stderr from another container on the HA "
        "host. "
        "Prefer haops_addon_logs for add-ons — it goes through Supervisor and "
        "needs no Docker access. Use this when the target is not an add-on (HA "
        "Core, Supervisor, a plain container) or when Supervisor's log endpoint "
        "returns nothing useful. "
        "Requires the Docker socket (see haops_container_list). "
        "Parameters: container (string, required — name or id from "
        "haops_container_list), tail (int, default 100, max 5000 lines)."
    ),
    params={
        "container": {
            "type": "string",
            "description": "Container name or id",
        },
        "tail": {
            "type": "integer",
            "description": "Number of trailing log lines (max 5000)",
            "default": 100,
        },
    },
)
async def haops_container_logs(
    ctx: HaOpsContext,
    container: str,
    tail: int = 100,
) -> dict[str, Any]:
    unavailable = _unavailable(ctx)
    if unavailable:
        return unavailable

    assert ctx.docker is not None
    tail = max(1, min(tail, 5000))

    try:
        logs = await ctx.docker.logs(container, tail=tail)
    except DockerUnavailableError as e:
        return {"error": str(e), "docker_available": False}
    except DockerError as e:
        return {"error": str(e), "container": container}

    truncated = len(logs) > _MAX_OUTPUT
    if truncated:
        logs = logs[-_MAX_OUTPUT:]

    await ctx.audit.log(
        tool="container_logs",
        details={"container": container, "tail": tail},
    )

    return {
        "container": container,
        "tail": tail,
        "logs": logs,
        "truncated": truncated,
    }


@registry.tool(
    name="haops_container_exec",
    description=(
        "SUPERUSER TOOL: Run a command inside another container on the HA host. "
        "This is the escalation path for work the add-on's own image cannot do "
        "— e.g. compiling an ESPHome firmware with the ESPHome add-on's "
        "toolchain, or running a CLI that only exists in another container. "
        "Two-phase: call without confirm to preview, then again with "
        "confirm=true and the token to run. "
        "The command runs as the container's own user with that container's "
        "filesystem and network — it is NOT sandboxed, and reaching any "
        "container on the host means this is effectively root on the HA "
        "machine. Prefer haops_exec_shell when the work can be done locally. "
        "On timeout the command is ABANDONED, not killed: the Docker API has no "
        "cancel, so the process keeps running inside the target container. Do "
        "not launch unbounded work. "
        "Requires the Docker socket (see haops_container_list). "
        "Parameters: container (string, required), command (string, required — "
        "a shell string, run via 'sh -c' unless shell=false), "
        "workdir (string, optional), user (string, optional), "
        "shell (bool, default true — false runs the command argv directly with "
        "no shell, so no globbing/pipes), "
        "timeout (int, seconds, default 60, max 600), "
        "confirm (bool, default false), token (string, required if confirm)."
    ),
    params={
        "container": {
            "type": "string",
            "description": "Target container name or id",
        },
        "command": {
            "type": "string",
            "description": "Command to run inside the container",
        },
        "workdir": {
            "type": "string",
            "description": "Working directory inside the container",
        },
        "user": {
            "type": "string",
            "description": "User to run as (default: the container's own user)",
        },
        "shell": {
            "type": "boolean",
            "description": "Run via 'sh -c' (true) or as direct argv (false)",
            "default": True,
        },
        "timeout": {
            "type": "integer",
            "description": "Timeout in seconds (max 600)",
            "default": 60,
        },
        "confirm": {
            "type": "boolean",
            "description": "Execute the command",
            "default": False,
        },
        "token": {
            "type": "string",
            "description": "Confirmation token from the preview step",
        },
    },
)
async def haops_container_exec(
    ctx: HaOpsContext,
    container: str,
    command: str,
    workdir: str | None = None,
    user: str | None = None,
    shell: bool = True,
    timeout: int = 60,
    confirm: bool = False,
    token: str | None = None,
) -> dict[str, Any]:
    unavailable = _unavailable(ctx)
    if unavailable:
        return unavailable

    assert ctx.docker is not None
    timeout = max(1, min(timeout, 600))

    if shell:
        argv = ["sh", "-c", command]
    else:
        try:
            argv = shlex.split(command)
        except ValueError as e:
            return {"error": f"Could not parse command as argv: {e}"}
        if not argv:
            return {"error": "Empty command"}

    if not confirm:
        tk = ctx.safety.create_token(
            action="container_exec",
            details={
                "container": container,
                "command": command,
                "workdir": workdir,
                "user": user,
                "shell": shell,
                "timeout": timeout,
            },
        )
        return {
            "container": container,
            "command": command,
            "argv": argv,
            "workdir": workdir,
            "user": user,
            "timeout": timeout,
            "token": tk.id,
            "message": (
                "Review the command and target container. Call again with "
                "confirm=true and this token to execute."
            ),
        }

    if token is None:
        return {"error": "confirm=true requires a token"}

    try:
        token_data = ctx.safety.validate_token(token)
    except Exception as e:  # noqa: BLE001 — surfaced to the caller as a message
        return {"error": str(e)}

    # Both must match: a token minted for a harmless command in one container
    # must not be replayable against a different container.
    if (
        token_data.details.get("command") != command
        or token_data.details.get("container") != container
    ):
        return {
            "error": (
                "Command or container does not match the token. Re-run the preview."
            )
        }

    try:
        result = await ctx.docker.exec_run(
            container,
            argv,
            workdir=workdir,
            user=user,
            timeout=float(timeout),
        )
    except DockerUnavailableError as e:
        return {"error": str(e), "docker_available": False}
    except DockerError as e:
        return {"error": str(e), "container": container}

    ctx.safety.consume_token(token)

    await ctx.audit.log(
        tool="container_exec",
        details={
            "container": container,
            "command": command,
            "exit_code": result.get("exit_code"),
            "timed_out": result.get("timed_out"),
        },
        token_id=token,
    )

    response: dict[str, Any] = {
        "container": container,
        "command": command,
        "exit_code": result.get("exit_code"),
        "stdout": result.get("stdout", ""),
        "stderr": result.get("stderr", ""),
    }

    if result.get("timed_out"):
        response["error"] = (
            f"Command timed out after {timeout}s and was ABANDONED — it may "
            f"still be running inside {container}."
        )
        response["timed_out"] = True

    for key in ("stdout", "stderr"):
        if len(response[key]) > _MAX_OUTPUT:
            response[key] = response[key][:_MAX_OUTPUT] + "\n... (truncated)"
            response[f"{key}_truncated"] = True

    return response
