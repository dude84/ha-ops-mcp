"""Entry point for ha-ops-mcp."""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

import anyio


def main() -> None:
    parser = argparse.ArgumentParser(description="ha-ops-mcp — HA operations MCP server")
    parser.add_argument("--config", type=Path, default=None, help="Path to config file")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    try:
        from ha_ops_mcp.server import create_server
    except ImportError as e:
        # The addon image is built on the HA host with no lockfile, so a
        # dependency's new major can land in a rebuild and break the import
        # with no change on our side. A bare traceback here reads like "the
        # addon is broken"; name the actual cause instead. (v0.55.1: mcp 2.0.0
        # removed mcp.server.fastmcp.)
        raise SystemExit(
            f"ha-ops-mcp failed to import a dependency: {e}\n\n"
            "This usually means an incompatible dependency version was pulled "
            "in when the addon image was built. pyproject.toml caps every "
            "dependency below its next major for exactly this reason — if you "
            "installed from source or with --upgrade, reinstall so the "
            "constraints are honoured:\n\n"
            "    pip install --force-reinstall ha-ops-mcp\n\n"
            "If you are on the addon, rebuild it. If this persists, please "
            "report it at https://github.com/dude84/ha-ops-mcp/issues with "
            "the output of `pip list`."
        ) from e

    mcp, ctx = create_server(args.config)

    transport = os.environ.get("HA_OPS_TRANSPORT", ctx.config.server.transport)

    if transport == "sse":
        # Removed in v0.63.0 (MCP's legacy transport; long-lived streams
        # dropped on proxy idle). Fall forward rather than refusing to boot
        # on a config left over from <=0.62.x.
        logging.getLogger(__name__).warning(
            "The 'sse' transport was removed in v0.63.0 — starting on "
            "'streamable-http' instead. Point your MCP client at the /mcp "
            "endpoint and set transport: streamable-http in your config."
        )
        transport = "streamable-http"

    if transport == "stdio":
        mcp.run(transport="stdio")
    else:
        from ha_ops_mcp._runner import serve_http
        anyio.run(lambda: serve_http(mcp, transport, static_token=ctx.static_token))


if __name__ == "__main__":
    main()
