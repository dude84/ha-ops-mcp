"""End-to-end FastMCP schema-validation regression net for diff tools.

Each diff-emitting tool gets exercised through ``mcp.call_tool`` against a
real FastMCP instance — the same path the MCP transport takes. Catches
two classes of regression at once:

1. The handler trips FastMCP's auto-derived output-schema validator
   (the v0.27.4/.5 ship-blocker — handlers returning bare dicts with
   ``-> dict[str, Any]`` get wrapped as ``{"result": <dict>}`` and any
   shape mismatch surfaces as ``Field required: result``).
2. The ``diff`` field carries actual unified-diff line markers
   (``+``/``-``), not the legacy ``key: 'old' -> 'new'`` blob — without
   markers no client can syntax-highlight the diff (gap 2026-04-18 §1).
"""

# Note: NO ``from __future__ import annotations`` — FastMCP introspects
# tool function signatures at registration time and needs concrete types.

import pytest
from mcp.types import CallToolResult


def _register_real_tool(mcp, ctx, tool_name: str) -> None:
    """Register a production handler by name on a fresh FastMCP instance."""
    # Force-import the modules whose @registry.tool decorators populate
    # the registry. server.create_server() does this in production; we
    # mirror the imports we need.
    import ha_ops_mcp.tools.config  # noqa: F401
    import ha_ops_mcp.tools.dashboard  # noqa: F401
    from ha_ops_mcp.server import _register_tool, registry

    handler, schema = registry._tools[tool_name]
    _register_tool(mcp, tool_name, schema.description, handler, ctx)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool_name,arg_builder",
    [
        (
            "haops_dashboard_patch",
            lambda: {
                "dashboard_id": "lovelace",
                "patch": [
                    {"op": "replace", "path": "/title", "value": "Patched"}
                ],
            },
        ),
        (
            "haops_dashboard_diff",
            lambda: {
                "dashboard_id": "lovelace",
                "view": 0,
                "new_view": {
                    "title": "Overview Updated",
                    "path": "overview",
                    "cards": [{"type": "markdown", "content": "hi"}],
                },
            },
        ),
        (
            "haops_config_patch",
            lambda: {
                "path": "configuration.yaml",
                # Inline patch — minimal valid unified diff against the
                # configuration.yaml fixture content.
                "patch": (
                    "--- a/configuration.yaml\n"
                    "+++ b/configuration.yaml\n"
                    "@@ -1,3 +1,3 @@\n"
                    " homeassistant:\n"
                    "-  name: Test Home\n"
                    "+  name: Schema Test Home\n"
                    "   unit_system: metric\n"
                ),
            },
        ),
        (
            "haops_config_create",
            lambda: {
                "path": "schema_test_new.yaml",
                "content": "x: 1\n",
            },
        ),
    ],
)
async def test_diff_tools_satisfy_fastmcp_output_schema(
    ctx, dashboard_storage, tool_name: str, arg_builder
) -> None:
    """Each diff-emitting production tool must round-trip through FastMCP's
    auto-derived output-schema validator without raising — and must carry
    a real unified diff (``+``/``-`` line markers) in its ``diff`` field
    so chat-side renderers can colourise it.
    """
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("test")
    _register_real_tool(mcp, ctx, tool_name)

    # FastMCP raises a ToolError if schema validation fails — propagating
    # out of this call is the failure signal we want to catch in CI.
    res = await mcp.call_tool(tool_name, arg_builder())

    # FastMCP returns a (content_seq, structured_dict) tuple when the
    # handler has an output schema. Normalise to the structured dict.
    if isinstance(res, tuple) and len(res) == 2:
        _content, structured = res
    elif isinstance(res, CallToolResult):
        structured = res.structuredContent
    else:
        structured = res
    assert isinstance(structured, dict), (
        f"{tool_name}: expected structured dict in response, got {type(structured).__name__}"
    )

    # FastMCP wraps bare dict returns as {"result": <dict>}.
    assert "result" in structured, (
        f"{tool_name} structuredContent missing the 'result' wrap that "
        "FastMCP's auto-derived schema requires"
    )
    payload = structured["result"]

    # Token surface — every diff tool's preview must produce one.
    assert "token" in payload, (
        f"{tool_name} payload is missing 'token' — caller cannot apply"
    )

    # The whole point of v0.27.6: ``diff`` must contain real unified-diff
    # line markers, not the legacy ``key: 'old' -> 'new'`` blob. Without
    # +/- prefixes no markdown ``diff`` fence colourises and the sidebar's
    # renderDiffHtml has nothing to colour either.
    diff_text = payload.get("diff", "")
    assert isinstance(diff_text, str) and diff_text, (
        f"{tool_name} payload.diff is empty or non-string"
    )
    has_added = any(
        line.startswith("+") and not line.startswith("+++")
        for line in diff_text.splitlines()
    )
    has_removed = any(
        line.startswith("-") and not line.startswith("---")
        for line in diff_text.splitlines()
    )
    assert has_added or has_removed, (
        f"{tool_name} diff has no +/- line markers — got:\n{diff_text[:400]}"
    )

    # diff_rendered must be the markdown-fenced wrapper of diff so a chat
    # renderer can colourise it inline.
    rendered = payload.get("diff_rendered", "")
    assert isinstance(rendered, str) and rendered.startswith("```diff"), (
        f"{tool_name} payload.diff_rendered missing or not a ```diff fence"
    )
