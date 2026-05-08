"""HTTP transport runner with an explicit dual-stack listener.

Replaces ``FastMCP.run(transport=...)`` for the SSE and streamable-http
transports so we control the listening socket. uvicorn 0.46's
``Config.bind_socket`` creates an ``AF_INET6`` socket and calls ``bind()``
without setting ``IPV6_V6ONLY``. The kernel sysctl
``net.ipv6.bindv6only`` is supposed to govern the default, but on the HA
addon container the resulting listener still rejects v4-mapped clients.
HA Supervisor's ingress proxy connects to the addon by IPv4 hostname, so
the panel UI surfaces as 502 Bad Gateway even though MCP clients (which
reach the addon over IPv6 on the hassio bridge) keep working.

Pre-binding the socket here lets us call
``setsockopt(IPV6_V6ONLY, 0)`` *before* ``bind`` and hand the open
listener to uvicorn via ``Server.serve(sockets=[...])``.
"""

from __future__ import annotations

import socket
from typing import TYPE_CHECKING

import uvicorn

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


def _bind_listener(host: str, port: int) -> socket.socket:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    sock = socket.socket(family=family, type=socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if family == socket.AF_INET6:
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
    sock.bind((host, port))
    sock.listen(128)
    return sock


async def serve_http(
    mcp: FastMCP,
    transport: str,
    mount_path: str | None = None,
) -> None:
    if transport == "sse":
        app = mcp.sse_app(mount_path)
    elif transport == "streamable-http":
        app = mcp.streamable_http_app()
    else:
        raise ValueError(f"serve_http does not support transport: {transport}")

    sock = _bind_listener(mcp.settings.host, mcp.settings.port)
    config = uvicorn.Config(
        app,
        host=mcp.settings.host,
        port=mcp.settings.port,
        log_level=mcp.settings.log_level.lower(),
    )
    server = uvicorn.Server(config)
    try:
        await server.serve(sockets=[sock])
    finally:
        sock.close()
