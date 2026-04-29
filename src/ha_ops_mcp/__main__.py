"""Entry point for ha-ops-mcp."""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="ha-ops-mcp — HA operations MCP server")
    parser.add_argument("--config", type=Path, default=None, help="Path to config file")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    from ha_ops_mcp.server import create_server

    mcp, ctx = create_server(args.config)

    transport = os.environ.get("HA_OPS_TRANSPORT", ctx.config.server.transport)
    mcp.run(transport=transport)  # type: ignore[arg-type]


if __name__ == "__main__":
    main()
