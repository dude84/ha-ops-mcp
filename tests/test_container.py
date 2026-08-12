"""Tests for the Docker container tools and the stream demuxer.

The demuxer tests matter most: Docker's frame format is the one piece of this
that we can get subtly wrong in a way that silently corrupts output rather than
raising, and it is pure-function testable with no socket.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from ha_ops_mcp.connections.docker import (
    DockerClient,
    DockerError,
    DockerUnavailableError,
    _demux,
)
from ha_ops_mcp.tools.container import (
    haops_container_exec,
    haops_container_list,
    haops_container_logs,
)


def _frame(stream: int, payload: bytes) -> bytes:
    return bytes([stream, 0, 0, 0]) + len(payload).to_bytes(4, "big") + payload


# ---------------------------------------------------------------- demuxer ----


def test_demux_splits_stdout_and_stderr():
    body = _frame(1, b"out one\n") + _frame(2, b"err one\n") + _frame(1, b"out two\n")
    stdout, stderr = _demux(body)
    assert stdout == "out one\nout two\n"
    assert stderr == "err one\n"


def test_demux_handles_unframed_tty_output():
    """A TTY container sends raw bytes with no 8-byte headers."""
    stdout, stderr = _demux(b"plain text with no frames")
    assert stdout == "plain text with no frames"
    assert stderr == ""


def test_demux_keeps_partial_final_frame():
    """A truncated read must yield what arrived, not raise or drop it."""
    truncated = _frame(1, b"complete\n") + bytes([1, 0, 0, 0]) + (99).to_bytes(
        4, "big"
    ) + b"partial"
    stdout, _ = _demux(truncated)
    assert stdout == "complete\npartial"


def test_demux_empty_body():
    assert _demux(b"") == ("", "")


# ------------------------------------------------------------ availability ----


def test_socket_path_none_when_missing(tmp_path):
    client = DockerClient(socket_path=str(tmp_path / "nope.sock"))
    assert client.socket_path() is None
    assert client.available() is False


def test_socket_path_found_when_present(tmp_path):
    sock = tmp_path / "docker.sock"
    sock.touch()
    client = DockerClient(socket_path=str(sock))
    assert client.socket_path() == str(sock)
    assert client.available() is True


def test_unavailable_reason_names_protection_mode():
    """The remedy is a checkbox — the message must say so, or it's useless."""
    reason = DockerClient(socket_path="/nonexistent").unavailable_reason
    assert "Protection mode" in reason
    assert "docker_api" in reason


@pytest.mark.asyncio
async def test_request_raises_unavailable_without_socket():
    client = DockerClient(socket_path="/nonexistent")
    with pytest.raises(DockerUnavailableError):
        await client.containers()


# ------------------------------------------------------------------ tools ----


class _FakeDocker:
    """Stands in for DockerClient with scripted responses."""

    def __init__(self, *, available: bool = True) -> None:
        self._available = available
        self.exec_calls: list[dict[str, Any]] = []
        self.unavailable_reason = "docker_api + Protection mode OFF required"

    def available(self) -> bool:
        return self._available

    def socket_path(self) -> str | None:
        return "/run/docker.sock" if self._available else None

    async def containers(self, *, all_containers: bool = True):
        return [
            {
                "id": "abc123456789",
                "name": "addon_ha_ops_mcp",
                "names": ["addon_ha_ops_mcp"],
                "image": "ha-ops-mcp",
                "state": "running",
                "status": "Up 2 hours",
                "labels": {"io.hass.name": "HA Ops MCP"},
            },
            {
                "id": "def987654321",
                "name": "addon_esphome",
                "names": ["addon_esphome"],
                "image": "esphome/esphome",
                "state": "exited" if all_containers else "running",
                "status": "Exited (0)",
                "labels": {},
            },
        ]

    async def logs(self, container: str, *, tail: int = 100, timeout: float = 30.0):
        return f"log line for {container} tail={tail}"

    async def exec_run(self, container, cmd, *, workdir=None, user=None, timeout=60.0):
        self.exec_calls.append(
            {"container": container, "cmd": cmd, "workdir": workdir, "user": user}
        )
        return {
            "exit_code": 0,
            "stdout": "hello",
            "stderr": "",
            "timed_out": False,
        }


class _FakeSafety:
    def __init__(self) -> None:
        self.tokens: dict[str, Any] = {}
        self.consumed: list[str] = []
        self._n = 0

    def create_token(self, action: str, details: dict[str, Any]):
        self._n += 1
        tid = f"tok{self._n}"
        tk = SimpleNamespace(id=tid, action=action, details=details)
        self.tokens[tid] = tk
        return tk

    def validate_token(self, token_id: str):
        if token_id not in self.tokens:
            raise ValueError("Invalid or already-used token")
        return self.tokens[token_id]

    def consume_token(self, token_id: str) -> None:
        self.consumed.append(token_id)
        self.tokens.pop(token_id, None)


class _FakeAudit:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    async def log(self, **kwargs: Any) -> None:
        self.entries.append(kwargs)


def _ctx(*, available: bool = True) -> Any:
    return SimpleNamespace(
        docker=_FakeDocker(available=available),
        safety=_FakeSafety(),
        audit=_FakeAudit(),
    )


@pytest.mark.asyncio
async def test_list_returns_containers_and_hass_label():
    ctx = _ctx()
    result = await haops_container_list(ctx)
    assert result["count"] == 2
    assert result["docker_available"] is True
    assert result["containers"][0]["hass"] == "HA Ops MCP"
    # Raw label bag must not leak into the response.
    assert "labels" not in result["containers"][0]


@pytest.mark.asyncio
async def test_list_name_filter_matches_image_too():
    ctx = _ctx()
    result = await haops_container_list(ctx, name_filter="esphome")
    assert result["count"] == 1
    assert result["containers"][0]["name"] == "addon_esphome"


@pytest.mark.asyncio
async def test_tools_explain_how_to_enable_when_socket_absent():
    """Every tool must return the remedy, not a bare failure."""
    for call in (
        haops_container_list(_ctx(available=False)),
        haops_container_logs(_ctx(available=False), container="x"),
        haops_container_exec(_ctx(available=False), container="x", command="ls"),
    ):
        result = await call
        assert result["docker_available"] is False
        assert "how_to_enable" in result
        assert any("Protection mode" in s for s in result["how_to_enable"])


@pytest.mark.asyncio
async def test_logs_clamps_tail():
    ctx = _ctx()
    result = await haops_container_logs(ctx, container="addon_esphome", tail=99999)
    assert result["tail"] == 5000


@pytest.mark.asyncio
async def test_exec_preview_does_not_run():
    ctx = _ctx()
    result = await haops_container_exec(
        ctx, container="addon_esphome", command="esphome version"
    )
    assert "token" in result
    assert result["argv"] == ["sh", "-c", "esphome version"]
    assert ctx.docker.exec_calls == []


@pytest.mark.asyncio
async def test_exec_confirm_runs_and_consumes_token():
    ctx = _ctx()
    preview = await haops_container_exec(
        ctx, container="addon_esphome", command="esphome version"
    )
    result = await haops_container_exec(
        ctx,
        container="addon_esphome",
        command="esphome version",
        confirm=True,
        token=preview["token"],
    )
    assert result["exit_code"] == 0
    assert result["stdout"] == "hello"
    assert ctx.docker.exec_calls[0]["container"] == "addon_esphome"
    assert ctx.safety.consumed == [preview["token"]]


@pytest.mark.asyncio
async def test_exec_token_is_not_replayable_against_another_container():
    """A token minted for one container must not run in a different one."""
    ctx = _ctx()
    preview = await haops_container_exec(
        ctx, container="addon_esphome", command="whoami"
    )
    result = await haops_container_exec(
        ctx,
        container="homeassistant",  # different target, same command
        command="whoami",
        confirm=True,
        token=preview["token"],
    )
    assert "error" in result
    assert "does not match the token" in result["error"]
    assert ctx.docker.exec_calls == []


@pytest.mark.asyncio
async def test_exec_rejects_changed_command():
    ctx = _ctx()
    preview = await haops_container_exec(ctx, container="c1", command="ls")
    result = await haops_container_exec(
        ctx, container="c1", command="rm -rf /", confirm=True, token=preview["token"]
    )
    assert "error" in result
    assert ctx.docker.exec_calls == []


@pytest.mark.asyncio
async def test_exec_confirm_requires_token():
    ctx = _ctx()
    result = await haops_container_exec(
        ctx, container="c1", command="ls", confirm=True
    )
    assert result["error"] == "confirm=true requires a token"


@pytest.mark.asyncio
async def test_exec_shell_false_uses_direct_argv():
    ctx = _ctx()
    preview = await haops_container_exec(
        ctx, container="c1", command="esphome compile x.yaml", shell=False
    )
    assert preview["argv"] == ["esphome", "compile", "x.yaml"]
    await haops_container_exec(
        ctx,
        container="c1",
        command="esphome compile x.yaml",
        shell=False,
        confirm=True,
        token=preview["token"],
    )
    assert ctx.docker.exec_calls[0]["cmd"] == ["esphome", "compile", "x.yaml"]


@pytest.mark.asyncio
async def test_exec_timeout_is_reported_as_abandoned():
    """Timeout must not read as 'stopped' — the process is still running."""
    ctx = _ctx()

    async def _timed_out(container, cmd, **kwargs):
        return {"exit_code": None, "stdout": "", "stderr": "", "timed_out": True}

    ctx.docker.exec_run = _timed_out
    preview = await haops_container_exec(ctx, container="c1", command="sleep 999")
    result = await haops_container_exec(
        ctx,
        container="c1",
        command="sleep 999",
        confirm=True,
        token=preview["token"],
    )
    assert result["timed_out"] is True
    assert "ABANDONED" in result["error"]


@pytest.mark.asyncio
async def test_exec_surfaces_docker_errors():
    ctx = _ctx()

    async def _boom(container, cmd, **kwargs):
        raise DockerError(404, "No such container")

    ctx.docker.exec_run = _boom
    preview = await haops_container_exec(ctx, container="ghost", command="ls")
    result = await haops_container_exec(
        ctx, container="ghost", command="ls", confirm=True, token=preview["token"]
    )
    assert "404" in result["error"]


@pytest.mark.asyncio
async def test_exec_audits_the_run():
    ctx = _ctx()
    preview = await haops_container_exec(ctx, container="c1", command="ls")
    await haops_container_exec(
        ctx, container="c1", command="ls", confirm=True, token=preview["token"]
    )
    assert ctx.audit.entries[-1]["tool"] == "container_exec"
    assert ctx.audit.entries[-1]["details"]["container"] == "c1"


def test_classification_covers_container_tools():
    from ha_ops_mcp.safety.classification import classify, type_label

    assert classify("container_list", None) == ("read", "container")
    assert classify("container_logs", None) == ("read", "container")
    assert classify("container_exec", None) == ("mutate", "container")
    assert type_label("container_exec", None) == "container exec"
