"""UI capture tools — headless screenshots + load-performance metrics.

The "eyes" of the dashboard work: render a Home Assistant Lovelace view in a
server-side headless Chromium (Playwright) and return either a screenshot or
raw load-performance metrics. Powers the chart-preview workflow (task 1) and
the UI perf / freeze-hunting suite (task 2).

Requires the Debian addon build (Playwright + Chromium; the Alpine build has no
browser) and a Home Assistant **long-lived access token** for the frontend
session (Supervisor tokens are not accepted by the frontend). The token is read
from `ha.token` by default, or passed per-call via `access_token`.

These tools are pure capture: they return raw bytes + raw metric dicts and make
no judgement about whether a value is "good". Thresholds / scoring live in the
caller.
"""

from __future__ import annotations

import base64
import time
from typing import TYPE_CHECKING, Any

from ha_ops_mcp.server import registry
from ha_ops_mcp.ui.capture import CaptureRequest, browser_available, perf, screenshot

if TYPE_CHECKING:
    from ha_ops_mcp.server import HaOpsContext

# Inline base64 cap — above this we hand back only the saved path to avoid
# bloating the MCP response.
_INLINE_MAX_BYTES = 2_000_000


def _resolve_target(
    ctx: HaOpsContext, base_url: str, access_token: str
) -> tuple[str, str] | dict[str, Any]:
    """Resolve (base_url, token) or return an error dict."""
    base = base_url or ctx.config.ha.url
    token = access_token or ctx.config.ha.resolve_token()
    if not base:
        return {"error": "No base_url and ha.url is empty."}
    if not token:
        return {
            "error": (
                "No HA access token. UI tools need a long-lived access token "
                "for the frontend session — set `ha.token` (must be an LLAT, "
                "not a Supervisor token) or pass `access_token`."
            )
        }
    return base, token


def _unavailable() -> dict[str, Any]:
    return {
        "error": (
            "Playwright/Chromium not available in this image. The UI tools "
            "require the Debian addon build (the Alpine build ships no "
            "browser). Update/rebuild the addon, then retry."
        ),
        "browser_available": False,
    }


@registry.tool(
    name="haops_ui_screenshot",
    description=(
        "Render a Home Assistant Lovelace dashboard view in headless Chromium "
        "and return a PNG screenshot (base64 + a saved file path) plus capture "
        "metadata. Use to *see* a dashboard view server-side — e.g. before/after "
        "comparisons of chart card changes, or visual checks. READ-ONLY (loads "
        "the page, clicks nothing).\n\n"
        "Parameters: path (Lovelace url_path, e.g. 'lovelace', 'new-dashboard', "
        "or 'dashboard/subview'; default 'lovelace'), full_page (bool, capture "
        "the whole scroll height vs just the viewport; default true), "
        "viewport_width/viewport_height (px; default 1280x2400), settle_ms (wait "
        "after load for cards to render; default 2500), base_url + access_token "
        "(override the HA URL/token; default from config).\n\n"
        "Returns: {url, saved_path, size_bytes, viewport, nav_ms, "
        "console_errors, image_b64}. image_b64 is null for images over ~2MB — "
        "read saved_path instead. Requires the Debian addon build + an LLAT."
    ),
)
async def haops_ui_screenshot(
    ctx: HaOpsContext,
    path: str = "lovelace",
    full_page: bool = True,
    viewport_width: int = 1280,
    viewport_height: int = 2400,
    settle_ms: int = 2500,
    base_url: str = "",
    access_token: str = "",
) -> dict[str, Any]:
    if not browser_available():
        return _unavailable()
    resolved = _resolve_target(ctx, base_url, access_token)
    if isinstance(resolved, dict):
        return resolved
    base, token = resolved
    req = CaptureRequest(
        base_url=base,
        path=path,
        access_token=token,
        full_page=full_page,
        viewport_width=viewport_width,
        viewport_height=viewport_height,
        settle_ms=settle_ms,
    )
    try:
        r = await screenshot(req)
    except Exception as e:  # noqa: BLE001 — surface as structured error, never raise
        return {"error": f"screenshot capture failed: {type(e).__name__}: {e}"[:400]}

    png = r.pop("png_bytes")
    safe = path.strip("/").replace("/", "_") or "root"
    fpath = ctx.audit.tool_results_dir() / f"ui-{safe}-{int(time.time())}.png"
    fpath.write_bytes(png)
    r["saved_path"] = str(fpath)
    if len(png) <= _INLINE_MAX_BYTES:
        r["image_b64"] = base64.b64encode(png).decode()
    else:
        r["image_b64"] = None
        r["note"] = "image >2MB; not inlined — read saved_path"
    return r


@registry.tool(
    name="haops_ui_perf",
    description=(
        "Measure load performance of a Home Assistant Lovelace view in headless "
        "Chromium. Use to find slow/heavy dashboard screens (the UI perf / "
        "freeze-hunting suite) and to baseline load cost. READ-ONLY.\n\n"
        "Parameters: path (Lovelace url_path; default 'lovelace'), settle_ms "
        "(wait after load before reading metrics; default 2500), "
        "viewport_width/viewport_height (px; default 1280x2400), base_url + "
        "access_token (override; default from config).\n\n"
        "Returns raw, UNSCORED metrics: {url, nav_ms, metrics:{nav timing, "
        "first/largest_contentful_paint, cumulative_layout_shift, "
        "long_tasks:{count,total_ms,max_ms}, js_heap_mb, dom_nodes, ha_cards}, "
        "console_error_count, console_errors}. Long tasks + high DOM/card counts "
        "are the usual culprits behind UI jank/freezes — but interpret the "
        "numbers yourself; this tool only reports them. Requires the Debian "
        "addon build + an LLAT."
    ),
)
async def haops_ui_perf(
    ctx: HaOpsContext,
    path: str = "lovelace",
    settle_ms: int = 2500,
    viewport_width: int = 1280,
    viewport_height: int = 2400,
    base_url: str = "",
    access_token: str = "",
) -> dict[str, Any]:
    if not browser_available():
        return _unavailable()
    resolved = _resolve_target(ctx, base_url, access_token)
    if isinstance(resolved, dict):
        return resolved
    base, token = resolved
    req = CaptureRequest(
        base_url=base,
        path=path,
        access_token=token,
        viewport_width=viewport_width,
        viewport_height=viewport_height,
        settle_ms=settle_ms,
    )
    try:
        return await perf(req)
    except Exception as e:  # noqa: BLE001
        return {"error": f"perf capture failed: {type(e).__name__}: {e}"[:400]}
