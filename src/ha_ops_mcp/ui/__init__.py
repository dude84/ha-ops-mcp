"""Sidebar UI — read-only HTTP surface mounted on the FastMCP server.

Exposes `/ui` (the single-file SPA) and `/api/ui/*` (JSON endpoints) behind
HA's Ingress. The UI is a window into the reference index + safety state;
it never performs mutations — that stays in the MCP flow.
"""
