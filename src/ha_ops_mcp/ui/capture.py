"""Headless-browser capture for Home Assistant dashboards.

Server-side Playwright (Chromium headless shell) — the "eyes" of the UI
perf/UX suite (task 2) and the chart-preview workflow (task 1). This module is
deliberately *pure capture*: it returns raw screenshot bytes and raw metric
dicts. All scoring, thresholds, and pass/fail decisions live in the controller,
not here (see the "brain belongs in the controller" project rule).

Auth: the HA frontend reads its session from `localStorage['hassTokens']`. We
inject a long-lived access token there via an init script *before* navigation,
so a headless context loads already authenticated — no login UI, no consent.
The token must be a real HA user **long-lived access token** (Supervisor tokens
are not accepted by the frontend); it's supplied via config (`ui.access_token`,
falling back to `ha.token`).

Nothing in here imports Playwright at module load — the import is deferred into
the call so the package (and the rest of the server) still imports fine on an
image that doesn't have the browser stack (e.g. the Alpine baseline / unit
tests). Callers should guard with `browser_available()`.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any

# Chromium flags for running headless as root inside a container.
_LAUNCH_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
]

# Collected before any page script runs: long tasks, LCP, cumulative layout
# shift. Buffered observers catch entries that fire during initial load.
_PERF_INIT_SCRIPT = """
(() => {
  window.__haops_perf = { longtasks: [], lcp: 0, cls: 0 };
  try {
    new PerformanceObserver((l) => {
      for (const e of l.getEntries())
        window.__haops_perf.longtasks.push({ start: e.startTime, dur: e.duration });
    }).observe({ type: 'longtask', buffered: true });
  } catch (e) {}
  try {
    new PerformanceObserver((l) => {
      const es = l.getEntries();
      if (es.length) window.__haops_perf.lcp = es[es.length - 1].startTime;
    }).observe({ type: 'largest-contentful-paint', buffered: true });
  } catch (e) {}
  try {
    new PerformanceObserver((l) => {
      for (const e of l.getEntries())
        if (!e.hadRecentInput) window.__haops_perf.cls += e.value;
    }).observe({ type: 'layout-shift', buffered: true });
  } catch (e) {}
})();
"""

_PERF_READ_SCRIPT = """
(() => {
  const nav = performance.getEntriesByType('navigation')[0] || {};
  const paints = {};
  for (const p of performance.getEntriesByType('paint')) paints[p.name] = p.startTime;
  const p = window.__haops_perf || { longtasks: [], lcp: 0, cls: 0 };
  const lt = p.longtasks || [];
  const mem = performance.memory || {};
  // HA renders entirely inside nested shadow DOM, so a flat querySelectorAll
  // counts almost nothing. Walk light + shadow trees to get real totals.
  let domNodes = 0, haCards = 0;
  const walk = (root) => {
    const els = root.querySelectorAll('*');
    domNodes += els.length;
    for (const e of els) {
      const t = e.localName;
      if (t === 'ha-card' || t === 'hui-card') haCards++;
      if (e.shadowRoot) walk(e.shadowRoot);
    }
  };
  try { walk(document); } catch (e) {}
  return {
    nav: {
      dom_content_loaded: nav.domContentLoadedEventEnd || null,
      load_event: nav.loadEventEnd || null,
      dom_interactive: nav.domInteractive || null,
      response_end: nav.responseEnd || null,
      transfer_size: nav.transferSize || null,
    },
    first_contentful_paint: paints['first-contentful-paint'] || null,
    largest_contentful_paint: p.lcp || null,
    cumulative_layout_shift: Number((p.cls || 0).toFixed(4)),
    long_tasks: { count: lt.length, total_ms: Number(lt.reduce((a, t) => a + t.dur, 0).toFixed(1)),
                  max_ms: lt.length ? Number(Math.max(...lt.map((t) => t.dur)).toFixed(1)) : 0 },
    js_heap_mb: mem.usedJSHeapSize ? Number((mem.usedJSHeapSize / 1048576).toFixed(1)) : null,
    dom_nodes: domNodes,
    ha_cards: haCards,
  };
})();
"""


async def _block_sourcemaps(target: Any) -> None:
    """Abort source-map + bundled-source fetches on a context.

    Headless Chromium resolves `//# sourceMappingURL` references that ship in
    some HACS card bundles (e.g. `@webcomponents/scoped-custom-element-registry`
    points at a `.ts` under `/node_modules/`). Those sources aren't published →
    a 404 on every load that pollutes the console-error count. A real browser
    only fetches them with devtools open. Aborting keeps the error count
    meaningful (real errors only) without affecting any served card JS, which
    lives under /frontend_latest/ and /hacsfiles/, never /node_modules/.
    """
    async def _abort(route: Any) -> None:
        await route.abort()

    await target.route("**/node_modules/**", _abort)
    await target.route("**/*.map", _abort)


def browser_available() -> bool:
    """True if the Playwright Chromium stack can be imported + launched here."""
    try:
        import importlib.util

        return importlib.util.find_spec("playwright") is not None
    except Exception:
        return False


def hass_tokens(base_url: str, access_token: str) -> str:
    """Build the `hassTokens` localStorage payload the HA frontend expects."""
    base = base_url.rstrip("/")
    now_ms = int(time.time() * 1000)
    return json.dumps(
        {
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": 1800,
            "hassUrl": base,
            "clientId": base + "/",
            "expires": now_ms + 1800 * 1000,
            "refresh_token": "",
        }
    )


@dataclass
class CaptureRequest:
    base_url: str
    path: str = "lovelace"
    access_token: str = ""
    viewport_width: int = 1280
    viewport_height: int = 800  # 16:10 base; full_page grows to content height
    full_page: bool = True
    device_scale_factor: float = 1.0
    is_mobile: bool = False
    has_touch: bool = False
    theme: str = "dark"  # informational; HA theme follows the user profile
    settle_ms: int = 2500  # wait after load for cards to render/settle
    nav_timeout_ms: int = 30000


# Named device presets for the `device` tool param. "mobile" ≈ iPhone 17 Pro
# (402×874 CSS px, 3× DPR, touch) so the capture matches the HA mobile column
# layout the phone app renders.
_DEVICE_PRESETS: dict[str, dict[str, Any]] = {
    "mobile": {
        "viewport_width": 402,
        "viewport_height": 874,
        "device_scale_factor": 3.0,
        "is_mobile": True,
        "has_touch": True,
    },
}
_DEVICE_ALIASES = {"iphone": "mobile", "phone": "mobile"}


def device_preset(name: str) -> dict[str, Any] | None:
    """Resolve a device name (or alias) to CaptureRequest field overrides."""
    key = name.strip().lower()
    key = _DEVICE_ALIASES.get(key, key)
    return _DEVICE_PRESETS.get(key)


async def _open_page(
    p: Any, req: CaptureRequest
) -> tuple[Any, Any, Any, list[dict[str, str]], float, str]:
    """Launch a context, inject auth + perf observers, navigate, settle."""
    browser = await p.chromium.launch(args=_LAUNCH_ARGS)
    context = await browser.new_context(
        viewport={"width": req.viewport_width, "height": req.viewport_height},
        device_scale_factor=req.device_scale_factor,
        is_mobile=req.is_mobile,
        has_touch=req.has_touch,
        ignore_https_errors=True,
    )
    if req.access_token:
        raw = hass_tokens(req.base_url, req.access_token)
        tokens = raw.replace("\\", "\\\\").replace("'", "\\'")
        await context.add_init_script(
            f"window.localStorage.setItem('hassTokens', '{tokens}');"
        )
    await context.add_init_script(_PERF_INIT_SCRIPT)
    await _block_sourcemaps(context)
    page = await context.new_page()
    console: list[dict[str, str]] = []
    page.on(
        "console",
        lambda m: console.append({"type": m.type, "text": m.text[:500]}),
    )
    url = req.base_url.rstrip("/") + "/" + req.path.lstrip("/")
    t0 = time.monotonic()
    await page.goto(url, wait_until="load", timeout=req.nav_timeout_ms)
    await page.wait_for_timeout(req.settle_ms)
    nav_ms = round((time.monotonic() - t0) * 1000, 1)
    return browser, context, page, console, nav_ms, url


async def screenshot(req: CaptureRequest) -> dict[str, Any]:
    """Capture a PNG of a dashboard view. Returns bytes + metadata."""
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser, context, page, console, nav_ms, url = await _open_page(p, req)
        try:
            png = await page.screenshot(full_page=req.full_page, type="png")
        finally:
            await context.close()
            await browser.close()
    errors = [c for c in console if c["type"] == "error"]
    return {
        "url": url,
        "png_bytes": png,
        "size_bytes": len(png),
        "viewport": {"w": req.viewport_width, "h": req.viewport_height},
        "full_page": req.full_page,
        "nav_ms": nav_ms,
        "console_errors": errors[:20],
    }


async def perf(req: CaptureRequest) -> dict[str, Any]:
    """Capture load-performance metrics for a dashboard view (raw, unscored)."""
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser, context, page, console, nav_ms, url = await _open_page(p, req)
        try:
            metrics = await page.evaluate(_PERF_READ_SCRIPT)
        finally:
            await context.close()
            await browser.close()
    errors = [c for c in console if c["type"] == "error"]
    return {
        "url": url,
        "nav_ms": nav_ms,
        "metrics": metrics,
        "console_error_count": len(errors),
        "console_errors": errors[:20],
    }


# Read just the long-task tally (the _PERF_READ_SCRIPT above does the full
# metric sweep; for interaction we only want the long-task summary so we can
# diff before/after each action without re-walking the whole DOM).
_LONGTASK_READ_SCRIPT = """
(() => {
  const p = window.__haops_perf || { longtasks: [] };
  const lt = p.longtasks || [];
  return {
    count: lt.length,
    total_ms: Number(lt.reduce((a, t) => a + t.dur, 0).toFixed(1)),
    max_ms: lt.length ? Number(Math.max(...lt.map((t) => t.dur)).toFixed(1)) : 0,
  };
})();
"""


def _default_actions() -> list[dict[str, Any]]:
    """A sensible full-page scroll sweep when no actions are supplied."""
    return [{"type": "scroll", "dy": 800} for _ in range(6)]


async def _run_action(page: Any, action: dict[str, Any]) -> dict[str, Any]:
    """Drive one interaction. Records + continues on failure (never raises)."""
    kind = str(action.get("type", "")).lower()
    t0 = time.monotonic()
    result: dict[str, Any] = {"type": kind, "ok": True}
    try:
        if kind == "scroll":
            dy = int(action.get("dy", 800))
            dx = int(action.get("dx", 0))
            await page.mouse.wheel(dx, dy)
        elif kind == "click":
            selector = str(action.get("selector", ""))
            if not selector:
                raise ValueError("click action requires 'selector'")
            result["selector"] = selector
            await page.click(selector, timeout=int(action.get("timeout", 3000)))
        elif kind == "tap":
            x = float(action.get("x", 0))
            y = float(action.get("y", 0))
            result["x"], result["y"] = x, y
            await page.mouse.click(x, y)
        elif kind == "wait":
            await page.wait_for_timeout(int(action.get("ms", 500)))
        else:
            raise ValueError(f"unknown action type {kind!r}")
    except Exception as e:  # noqa: BLE001 — record + continue, don't abort run
        result["ok"] = False
        result["error"] = f"{type(e).__name__}: {e}"[:300]
    settle = int(action.get("settle_ms", 0))
    if settle:
        await page.wait_for_timeout(settle)
    result["ms"] = round((time.monotonic() - t0) * 1000, 1)
    return result


async def interact(
    req: CaptureRequest, actions: list[dict[str, Any]]
) -> dict[str, Any]:
    """Drive a dashboard view through `actions`, capturing long-tasks + console
    errors that occur DURING interaction (raw, unscored)."""
    from playwright.async_api import async_playwright

    seq = actions if actions else _default_actions()
    async with async_playwright() as p:
        browser, context, page, console, nav_ms, url = await _open_page(p, req)
        try:
            # Baseline: long-tasks accrued during load, so we can attribute the
            # delta to the interaction sweep rather than the initial render.
            before = await page.evaluate(_LONGTASK_READ_SCRIPT)
            console_before = len(console)
            runs: list[dict[str, Any]] = []
            for action in seq:
                runs.append(await _run_action(page, action))
            after = await page.evaluate(_LONGTASK_READ_SCRIPT)
        finally:
            await context.close()
            await browser.close()
    long_tasks = {
        "count": after["count"] - before["count"],
        "total_ms": round(after["total_ms"] - before["total_ms"], 1),
        "max_ms": after["max_ms"],
    }
    # Console errors emitted during interaction (after the load baseline).
    errors = [c for c in console[console_before:] if c["type"] == "error"]
    return {
        "url": url,
        "nav_ms": nav_ms,
        "actions_run": runs,
        "long_tasks": long_tasks,
        "console_errors": errors[:20],
    }


async def trace(req: CaptureRequest, out_path: str) -> dict[str, Any]:
    """Record a Playwright trace zip of a dashboard view load to `out_path`."""
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(args=_LAUNCH_ARGS)
        context = await browser.new_context(
            viewport={"width": req.viewport_width, "height": req.viewport_height},
            device_scale_factor=req.device_scale_factor,
            is_mobile=req.is_mobile,
            has_touch=req.has_touch,
            ignore_https_errors=True,
        )
        if req.access_token:
            raw = hass_tokens(req.base_url, req.access_token)
            tokens = raw.replace("\\", "\\\\").replace("'", "\\'")
            await context.add_init_script(
                f"window.localStorage.setItem('hassTokens', '{tokens}');"
            )
        await context.add_init_script(_PERF_INIT_SCRIPT)
        await _block_sourcemaps(context)
        await context.tracing.start(screenshots=True, snapshots=True, sources=True)
        page = await context.new_page()
        url = req.base_url.rstrip("/") + "/" + req.path.lstrip("/")
        t0 = time.monotonic()
        try:
            await page.goto(url, wait_until="load", timeout=req.nav_timeout_ms)
            await page.wait_for_timeout(req.settle_ms)
            nav_ms = round((time.monotonic() - t0) * 1000, 1)
        finally:
            await context.tracing.stop(path=out_path)
            await context.close()
            await browser.close()
    import os

    return {
        "url": url,
        "saved_path": out_path,
        "size_bytes": os.path.getsize(out_path),
        "nav_ms": nav_ms,
    }


# --- CLI for in-image functional testing (no HA required: any URL works) -----
if __name__ == "__main__":  # pragma: no cover
    import asyncio
    import sys

    mode = sys.argv[1] if len(sys.argv) > 1 else "perf"
    url = sys.argv[2] if len(sys.argv) > 2 else "https://example.com"
    out = sys.argv[3] if len(sys.argv) > 3 else "/tmp/capture.png"
    # base_url + path are split only for HA; for an arbitrary URL pass it whole.
    req = CaptureRequest(base_url=url, path="", settle_ms=500, full_page=True)

    async def _main() -> None:
        if mode == "screenshot":
            r = await screenshot(req)
            with open(out, "wb") as f:
                f.write(r["png_bytes"])
            r = {k: v for k, v in r.items() if k != "png_bytes"}
            r["saved"] = out
            print(json.dumps(r, indent=2))
        else:
            print(json.dumps(await perf(req), indent=2))

    asyncio.run(_main())
