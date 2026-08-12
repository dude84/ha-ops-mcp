"""Docker Engine client over the host's unix socket.

Reaches sibling containers (other add-ons, HA core, the Supervisor) so tools
can borrow a toolchain that isn't in our image — the ESPHome compiler being
the motivating case — or inspect another add-on that has no API.

Availability is NOT guaranteed and is not an error condition: the socket
only appears when ``docker_api: true`` is in the add-on manifest AND the user
has switched **Protection mode OFF**. Supervisor silently strips the
capability while protection is on, so a manifest bump alone changes nothing.
Every caller must go through :meth:`available` and surface
:attr:`unavailable_reason` verbatim rather than reporting a generic failure —
the fix is a checkbox in the add-on UI, and the user can only find it if we
say so.

No docker CLI is needed in the image; this drives the HTTP API directly over
the socket via aiohttp's UnixConnector.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)

# Supervisor mounts the socket at /run/docker.sock; /var/run is the classic
# symlinked location. Probe both — which one exists varies by base image.
_SOCKET_PATHS = ("/run/docker.sock", "/var/run/docker.sock")

# Pinned so a future Engine release can't change response shapes under us.
# 1.41 ships in Docker 20.10 (2020), comfortably older than any HA OS host.
_API_VERSION = "v1.41"

# Docker multiplexes exec output into frames: an 8-byte header
# [stream_type, 0, 0, 0, size_be32] followed by ``size`` payload bytes.
_HEADER_LEN = 8
_STREAM_STDOUT = 1
_STREAM_STDERR = 2


class DockerUnavailableError(Exception):
    """Raised when the Docker socket is not reachable.

    Carries the operator-facing remedy, not just the symptom.
    """


class DockerError(Exception):
    """Raised when the Docker Engine API returns an error."""

    def __init__(self, status: int, message: str) -> None:
        self.status = status
        super().__init__(f"Docker API HTTP {status}: {message}")


class DockerClient:
    """Async Docker Engine client speaking HTTP over a unix socket.

    Sessions are created lazily and reused. Nothing is opened at startup —
    an add-on running with protection ON must boot cleanly and simply report
    the capability as absent.
    """

    def __init__(self, socket_path: str | None = None) -> None:
        self._explicit_path = socket_path
        self._session: aiohttp.ClientSession | None = None

    # ---- availability ----

    def socket_path(self) -> str | None:
        """Return the first Docker socket that exists, or None."""
        if self._explicit_path:
            return self._explicit_path if Path(self._explicit_path).exists() else None
        for path in _SOCKET_PATHS:
            if Path(path).exists():
                return path
        return None

    def available(self) -> bool:
        """True when a Docker socket is present in this container."""
        return self.socket_path() is not None

    @property
    def unavailable_reason(self) -> str:
        """Operator-facing explanation + remedy for a missing socket."""
        return (
            "Docker socket not available. This needs BOTH: (1) 'docker_api: true' "
            "in the add-on manifest, and (2) Protection mode switched OFF in the "
            "add-on's Info tab — Supervisor strips docker_api while protection is "
            "on, so the manifest alone is not enough. Restart the add-on after "
            "changing protection."
        )

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        path = self.socket_path()
        if path is None:
            raise DockerUnavailableError(self.unavailable_reason)
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                connector=aiohttp.UnixConnector(path=path),
            )
        return self._session

    # ---- plumbing ----

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        timeout: float = 30.0,
    ) -> Any:
        session = await self._ensure_session()
        # The host part is ignored for unix sockets but aiohttp requires a URL.
        url = f"http://localhost/{_API_VERSION}{path}"
        try:
            async with session.request(
                method,
                url,
                json=json_body,
                params=params,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                body = await resp.read()
                if resp.status >= 400:
                    raise DockerError(resp.status, _error_message(body))
                if not body:
                    return None
                return json.loads(body)
        except aiohttp.ClientError as e:
            raise DockerUnavailableError(
                f"Docker socket present but unusable: {e}. {self.unavailable_reason}"
            ) from e

    # ---- operations ----

    async def containers(self, *, all_containers: bool = True) -> list[dict[str, Any]]:
        """List containers, newest first.

        Names are normalised: Docker returns them with a leading slash and as
        a list, which is noise for every caller.
        """
        raw = await self._request(
            "GET", "/containers/json", params={"all": "1" if all_containers else "0"}
        )
        result: list[dict[str, Any]] = []
        for c in raw or []:
            names = [n.lstrip("/") for n in c.get("Names") or []]
            result.append(
                {
                    "id": (c.get("Id") or "")[:12],
                    "name": names[0] if names else "",
                    "names": names,
                    "image": c.get("Image"),
                    "state": c.get("State"),
                    "status": c.get("Status"),
                    "labels": c.get("Labels") or {},
                }
            )
        return result

    async def logs(
        self, container: str, *, tail: int = 100, timeout: float = 30.0
    ) -> str:
        """Return recent logs for a container as text.

        Log output is frame-multiplexed exactly like exec output when the
        container has no TTY, so it goes through the same demuxer.
        """
        session = await self._ensure_session()
        url = f"http://localhost/{_API_VERSION}/containers/{container}/logs"
        params = {"stdout": "1", "stderr": "1", "tail": str(tail)}
        try:
            async with session.get(
                url, params=params, timeout=aiohttp.ClientTimeout(total=timeout)
            ) as resp:
                body = await resp.read()
                if resp.status >= 400:
                    raise DockerError(resp.status, _error_message(body))
        except aiohttp.ClientError as e:
            raise DockerUnavailableError(
                f"Docker socket present but unusable: {e}"
            ) from e

        stdout, stderr = _demux(body)
        return (stdout + stderr).strip()

    async def exec_run(
        self,
        container: str,
        cmd: list[str],
        *,
        workdir: str | None = None,
        user: str | None = None,
        timeout: float = 60.0,
    ) -> dict[str, Any]:
        """Run a command in a container and return its output and exit code.

        Three Engine calls: create the exec instance, start it (which streams
        the output back), then inspect it for the exit code — the start
        response carries no status.

        On timeout the exec is abandoned rather than killed: the Engine API has
        no cancel endpoint, and the process keeps running inside the target
        container. Callers get ``timed_out: True`` and a null exit code; the
        onus is on them not to launch unbounded work.
        """
        created = await self._request(
            "POST",
            f"/containers/{container}/exec",
            json_body={
                "AttachStdout": True,
                "AttachStderr": True,
                "Tty": False,
                "Cmd": cmd,
                **({"WorkingDir": workdir} if workdir else {}),
                **({"User": user} if user else {}),
            },
            timeout=timeout,
        )
        exec_id = (created or {}).get("Id")
        if not exec_id:
            raise DockerError(500, "Docker did not return an exec id")

        session = await self._ensure_session()
        url = f"http://localhost/{_API_VERSION}/exec/{exec_id}/start"
        timed_out = False
        body = b""
        try:
            async with session.post(
                url,
                json={"Detach": False, "Tty": False},
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                if resp.status >= 400:
                    raise DockerError(resp.status, _error_message(await resp.read()))
                body = await resp.read()
        except TimeoutError:
            timed_out = True
        except aiohttp.ClientError as e:
            raise DockerUnavailableError(
                f"Docker socket present but unusable: {e}"
            ) from e

        stdout, stderr = _demux(body)

        exit_code: int | None = None
        if not timed_out:
            try:
                info = await self._request("GET", f"/exec/{exec_id}/json", timeout=10.0)
                exit_code = (info or {}).get("ExitCode")
            except Exception:  # noqa: BLE001 — output matters more than the code
                logger.debug("exec inspect failed for %s", exec_id, exc_info=True)

        return {
            "exit_code": exit_code,
            "stdout": stdout.rstrip(),
            "stderr": stderr.rstrip(),
            "timed_out": timed_out,
        }


def _error_message(body: bytes) -> str:
    """Pull Docker's ``{"message": ...}`` out of an error body."""
    try:
        parsed = json.loads(body)
        if isinstance(parsed, dict) and "message" in parsed:
            return str(parsed["message"])[:500]
    except (ValueError, TypeError):
        pass
    return body.decode(errors="replace")[:500]


def _demux(body: bytes) -> tuple[str, str]:
    """Split Docker's multiplexed stream into (stdout, stderr) text.

    Frames are ``[stream_type, 0, 0, 0, size_be32][payload]``. A container
    started with a TTY sends raw bytes with no framing at all, and a truncated
    final frame is possible when a read is cut short — both are treated as raw
    stdout rather than raising, because partial output is still useful and this
    is a diagnostic path.
    """
    stdout_parts: list[bytes] = []
    stderr_parts: list[bytes] = []
    offset = 0
    length = len(body)

    while offset + _HEADER_LEN <= length:
        stream_type = body[offset]
        if stream_type not in (_STREAM_STDOUT, _STREAM_STDERR):
            # Not framed (TTY mode) — treat the remainder as raw stdout.
            return body.decode(errors="replace"), ""
        size = int.from_bytes(body[offset + 4 : offset + _HEADER_LEN], "big")
        start = offset + _HEADER_LEN
        end = start + size
        if end > length:  # truncated frame — keep what arrived
            end = length
        chunk = body[start:end]
        if stream_type == _STREAM_STDERR:
            stderr_parts.append(chunk)
        else:
            stdout_parts.append(chunk)
        offset = end

    if offset == 0 and body:
        return body.decode(errors="replace"), ""

    return (
        b"".join(stdout_parts).decode(errors="replace"),
        b"".join(stderr_parts).decode(errors="replace"),
    )
