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
import os
import tempfile
from typing import TYPE_CHECKING, Any

from mcp.types import ImageContent

from ha_ops_mcp.server import registry
from ha_ops_mcp.ui.capture import (
    CaptureRequest,
    browser_available,
    device_preset,
    interact,
    perf,
    screenshot,
    trace,
)

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


def _apply_device(req: CaptureRequest, device: str) -> dict[str, Any] | None:
    """Apply a named device preset (e.g. 'mobile') to a request in place.

    Returns an error dict for an unknown device name, else None. A preset
    overrides viewport + touch/scale fields, so it takes precedence over the
    default viewport for that call.
    """
    if not device:
        return None
    preset = device_preset(device)
    if preset is None:
        return {"error": f"Unknown device {device!r}. Known: mobile (iphone/phone)."}
    for k, v in preset.items():
        setattr(req, k, v)
    return None


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
        "viewport_width/viewport_height (px; default 1280x800), device (preset "
        "that overrides the viewport — 'mobile' = iPhone-17-Pro-class 402x874 @3x "
        "touch, for the HA mobile column layout; default '' = desktop), settle_ms "
        "(wait after load for cards to render; default 2500), inline (bool, "
        "default false — base64 the image into the response; off by default "
        "because a full-res PNG blows the response token cap), note + "
        "transaction_id (annotate / link the capture), base_url + access_token "
        "(override the HA URL/token; default from config).\n\n"
        "Returns: {url, capture_id, saved_path, size_bytes, viewport, nav_ms, "
        "console_errors}. To SEE the image, call haops_capture_show("
        "capture_id=...) (returns a downscaled native image) or open the "
        "Captures tab — don't set inline=true just to view. Requires the Debian "
        "addon build + an LLAT."
    ),
)
async def haops_ui_screenshot(
    ctx: HaOpsContext,
    path: str = "lovelace",
    full_page: bool = True,
    viewport_width: int = 1280,
    viewport_height: int = 800,
    device: str = "",
    settle_ms: int = 2500,
    base_url: str = "",
    access_token: str = "",
    note: str = "",
    transaction_id: str = "",
    inline: bool = False,
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
    err = _apply_device(req, device)
    if err is not None:
        return err
    try:
        r = await screenshot(req)
    except Exception as e:  # noqa: BLE001 — surface as structured error, never raise
        return {"error": f"screenshot capture failed: {type(e).__name__}: {e}"[:400]}

    png = r.pop("png_bytes")
    entry = ctx.captures.save(
        content=png,
        kind="screenshot",
        view=r.get("url", path),
        ext="png",
        nav_ms=r.get("nav_ms"),
        errors=[str(c.get("text", "")) for c in r.get("console_errors", [])][:20],
        note=note,
        transaction_id=transaction_id,
        viewport=r.get("viewport", {}),
    )
    r["capture_id"] = entry.id
    r["saved_path"] = str(ctx.captures.artifact_path(entry))
    # By default we do NOT inline the base64 — a full-res PNG blows the MCP
    # token cap as a JSON text field. Call haops_capture_show(capture_id) to
    # view it as a (downscaled) native image, or open the Captures tab.
    if inline and len(png) <= _INLINE_MAX_BYTES:
        r["image_b64"] = base64.b64encode(png).decode()
    else:
        r["image_b64"] = None
        r["view_hint"] = (
            f"call haops_capture_show(capture_id='{entry.id}') to view, "
            "or open the Captures tab"
        )
    return r


@registry.tool(
    name="haops_ui_perf",
    description=(
        "Measure load performance of a Home Assistant Lovelace view in headless "
        "Chromium. Use to find slow/heavy dashboard screens (the UI perf / "
        "freeze-hunting suite) and to baseline load cost. READ-ONLY.\n\n"
        "Parameters: path (Lovelace url_path; default 'lovelace'), settle_ms "
        "(wait after load before reading metrics; default 2500), "
        "viewport_width/viewport_height (px; default 1280x800), base_url + "
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
    viewport_height: int = 800,
    device: str = "",
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
    err = _apply_device(req, device)
    if err is not None:
        return err
    try:
        return await perf(req)
    except Exception as e:  # noqa: BLE001
        return {"error": f"perf capture failed: {type(e).__name__}: {e}"[:400]}


@registry.tool(
    name="haops_ui_interact",
    description=(
        "Drive a Home Assistant Lovelace view through a sequence of interactions "
        "in headless Chromium and report the main-thread long-tasks + console "
        "errors that occur DURING the interaction. Use to hunt UI jank/freezes "
        "triggered by user action (scrolling a heavy dashboard, opening a "
        "more-info dialog, switching tabs) rather than just initial load. "
        "READ-ONLY in the ops sense (it only observes the rendered frontend; it "
        "does not mutate HA config/state).\n\n"
        "Parameters: path (Lovelace url_path; default 'lovelace'), actions (list "
        "of action dicts; if empty, defaults to a full-page scroll sweep). "
        "Supported actions: {'type':'scroll','dy':800,'dx':0}, "
        "{'type':'click','selector':'<css>'}, {'type':'tap','x':..,'y':..}, "
        "{'type':'wait','ms':500}. Each action accepts an optional 'settle_ms' to "
        "pause after it. Invalid/missing selectors are recorded and skipped — the "
        "run continues. Other params: settle_ms (wait after load before "
        "interacting; default 2500), viewport_width/viewport_height (px; default "
        "1280x800), base_url + access_token (override; default from config).\n\n"
        "Returns raw, UNSCORED: {url, nav_ms, actions_run:[{type, ok, ms, ...}], "
        "long_tasks:{count,total_ms,max_ms} (delta attributable to the "
        "interaction), console_errors}. Requires the Debian addon build + an LLAT."
    ),
)
async def haops_ui_interact(
    ctx: HaOpsContext,
    path: str = "lovelace",
    actions: list[dict[str, Any]] | None = None,
    settle_ms: int = 2500,
    viewport_width: int = 1280,
    viewport_height: int = 800,
    device: str = "",
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
    err = _apply_device(req, device)
    if err is not None:
        return err
    try:
        return await interact(req, actions or [])
    except Exception as e:  # noqa: BLE001
        return {"error": f"interact capture failed: {type(e).__name__}: {e}"[:400]}


@registry.tool(
    name="haops_ui_trace",
    description=(
        "Record a Playwright trace (zip) of a Home Assistant Lovelace view load "
        "in headless Chromium — screenshots, DOM snapshots, network + console — "
        "and save it to disk for offline inspection in the Playwright trace "
        "viewer (`npx playwright show-trace <file>`). Use for deep diagnosis of a "
        "slow/janky dashboard when the summary numbers from haops_ui_perf aren't "
        "enough. READ-ONLY (loads the page, captures a trace).\n\n"
        "Parameters: path (Lovelace url_path; default 'lovelace'), settle_ms "
        "(wait after load before stopping the trace; default 2500), "
        "viewport_width/viewport_height (px; default 1280x800), base_url + "
        "access_token (override; default from config).\n\n"
        "Returns: {url, saved_path, size_bytes, nav_ms}. The trace zip is written "
        "under the tool-results dir; read/transfer saved_path to open it. "
        "Requires the Debian addon build + an LLAT."
    ),
)
async def haops_ui_trace(
    ctx: HaOpsContext,
    path: str = "lovelace",
    settle_ms: int = 2500,
    viewport_width: int = 1280,
    viewport_height: int = 800,
    device: str = "",
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
    err = _apply_device(req, device)
    if err is not None:
        return err
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".zip")
    os.close(tmp_fd)
    try:
        r = await trace(req, tmp_path)
        with open(tmp_path, "rb") as fh:
            data = fh.read()
    except Exception as e:  # noqa: BLE001
        return {"error": f"trace capture failed: {type(e).__name__}: {e}"[:400]}
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    entry = ctx.captures.save(
        content=data, kind="trace", view=r.get("url", path), ext="zip",
        nav_ms=r.get("nav_ms"),
    )
    return {
        "url": r.get("url"),
        "capture_id": entry.id,
        "saved_path": str(ctx.captures.artifact_path(entry)),
        "size_bytes": entry.size_bytes,
        "nav_ms": r.get("nav_ms"),
    }


def _downscale_jpeg(data: bytes, max_px: int, quality: int = 70) -> bytes:
    """Downscale an image to a JPEG whose long edge is <= max_px (no upscaling).

    JPEG (not PNG) on purpose: a full-page dashboard PNG is multi-hundred-KB,
    and the MCP client echoes the image bytes as text — JPEG keeps the payload
    a fraction of the size while staying perfectly legible for visual review.
    """
    import io

    from PIL import Image

    with Image.open(io.BytesIO(data)) as src:
        im = src.convert("RGB")  # JPEG has no alpha channel
    long_edge = max(im.width, im.height)
    if long_edge > max_px:
        scale = max_px / long_edge
        im = im.resize(
            (max(1, round(im.width * scale)), max(1, round(im.height * scale)))
        )
    out = io.BytesIO()
    im.save(out, format="JPEG", quality=quality, optimize=True)
    return out.getvalue()


@registry.tool(
    name="haops_capture_show",
    description=(
        "View a stored UI capture (screenshot) as an inline image, downscaled to "
        "fit the response budget. This is the read-only way to *see* a capture "
        "produced by haops_ui_screenshot without inlining a full-res base64 (which "
        "overflows the token cap) and without shelling into the host. READ-ONLY.\n\n"
        "Parameters: capture_id (string, required — the id returned by "
        "haops_ui_screenshot, or shown in the Captures tab), max_px (int, default "
        "768 — long-edge cap for the downscaled image; raise for more detail, "
        "lower for a smaller payload).\n\n"
        "Returns a native image content block (JPEG). Errors (as a dict) if the id "
        "is unknown or the capture is a trace zip (not an image) — open a trace "
        "with the Playwright trace viewer instead. Pairs with haops_ui_screenshot "
        "(capture) and the Captures sidebar tab (browse/manage)."
    ),
)
async def haops_capture_show(
    ctx: HaOpsContext,
    capture_id: str,
    max_px: int = 768,
) -> Any:
    got = ctx.captures.read_bytes(capture_id)
    if got is None:
        return {"error": f"Capture {capture_id!r} not found."}
    entry, data = got
    if entry.kind != "screenshot" or not entry.filename.endswith(".png"):
        return {
            "error": (
                f"Capture {capture_id!r} is a {entry.kind} ({entry.filename}), not "
                "a viewable image. Open trace zips in the Playwright trace viewer."
            )
        }
    try:
        small = _downscale_jpeg(data, max(64, max_px))
    except Exception as e:  # noqa: BLE001 — surface as structured error, never raise
        return {"error": f"image decode/resize failed: {type(e).__name__}: {e}"[:300]}
    return ImageContent(
        type="image",
        data=base64.b64encode(small).decode(),
        mimeType="image/jpeg",
    )
