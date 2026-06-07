"""Tests for the UI capture tools (mock the browser; real launch is smoke-tested
in-image via scripts/smoke.sh + the capture CLI)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import ha_ops_mcp.tools.ui_suite as ui
from ha_ops_mcp.config import HaConfig, HaOpsConfig
from ha_ops_mcp.safety.captures import CaptureStore
from ha_ops_mcp.ui.capture import CaptureRequest, hass_tokens


def _ctx(tmp_path, token="LLAT-xyz", url="http://homeassistant:8123"):
    cfg = HaOpsConfig(ha=HaConfig(url=url, token=token))
    audit = SimpleNamespace(tool_results_dir=lambda: tmp_path)
    captures = CaptureStore(tmp_path / "captures")
    return SimpleNamespace(config=cfg, audit=audit, captures=captures)


def test_hass_tokens_shape():
    t = json.loads(hass_tokens("http://ha:8123/", "TOK"))
    assert t["access_token"] == "TOK"
    assert t["hassUrl"] == "http://ha:8123"  # trailing slash stripped
    assert t["token_type"] == "Bearer"
    assert t["expires"] > 0


@pytest.mark.asyncio
async def test_screenshot_browser_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(ui, "browser_available", lambda: False)
    out = await ui.haops_ui_screenshot(_ctx(tmp_path))
    assert out["browser_available"] is False
    assert "Debian addon build" in out["error"]


@pytest.mark.asyncio
async def test_screenshot_requires_token(tmp_path, monkeypatch):
    monkeypatch.setattr(ui, "browser_available", lambda: True)
    out = await ui.haops_ui_screenshot(_ctx(tmp_path, token=""))
    assert "access token" in out["error"].lower()


@pytest.mark.asyncio
async def test_screenshot_success_saves_and_inlines(tmp_path, monkeypatch):
    monkeypatch.setattr(ui, "browser_available", lambda: True)

    async def fake_screenshot(req: CaptureRequest):
        assert req.access_token == "LLAT-xyz"
        assert req.path == "new-dashboard"
        return {
            "url": "http://homeassistant:8123/new-dashboard",
            "png_bytes": b"\x89PNG\r\n\x1a\nFAKE",
            "size_bytes": 12,
            "viewport": {"w": 1280, "h": 2400},
            "full_page": True,
            "nav_ms": 500.0,
            "console_errors": [],
        }

    monkeypatch.setattr(ui, "screenshot", fake_screenshot)
    out = await ui.haops_ui_screenshot(_ctx(tmp_path), path="new-dashboard")

    assert "png_bytes" not in out  # raw bytes never returned inline
    assert out["saved_path"].endswith(".png")
    assert out["capture_id"]
    assert Path(out["saved_path"]).read_bytes().startswith(b"\x89PNG")
    assert out["image_b64"]  # small image inlined


@pytest.mark.asyncio
async def test_screenshot_large_not_inlined(tmp_path, monkeypatch):
    monkeypatch.setattr(ui, "browser_available", lambda: True)
    big = b"\x89PNG" + b"0" * (ui._INLINE_MAX_BYTES + 1)

    async def fake_screenshot(req: CaptureRequest):
        return {
            "url": "u", "png_bytes": big, "size_bytes": len(big),
            "viewport": {"w": 1, "h": 1}, "full_page": True,
            "nav_ms": 1.0, "console_errors": [],
        }

    monkeypatch.setattr(ui, "screenshot", fake_screenshot)
    out = await ui.haops_ui_screenshot(_ctx(tmp_path))
    assert out["image_b64"] is None
    assert "inline_note" in out
    assert out["capture_id"]


@pytest.mark.asyncio
async def test_perf_success_returns_metrics(tmp_path, monkeypatch):
    monkeypatch.setattr(ui, "browser_available", lambda: True)

    async def fake_perf(req: CaptureRequest):
        return {"url": "u", "nav_ms": 800.0, "metrics": {"ha_cards": 42},
                "console_error_count": 0, "console_errors": []}

    monkeypatch.setattr(ui, "perf", fake_perf)
    out = await ui.haops_ui_perf(_ctx(tmp_path), path="lovelace")
    assert out["metrics"]["ha_cards"] == 42
    assert out["nav_ms"] == 800.0


@pytest.mark.asyncio
async def test_perf_capture_error_is_structured(tmp_path, monkeypatch):
    monkeypatch.setattr(ui, "browser_available", lambda: True)

    async def boom(req: CaptureRequest):
        raise RuntimeError("nav timeout")

    monkeypatch.setattr(ui, "perf", boom)
    out = await ui.haops_ui_perf(_ctx(tmp_path))
    assert "nav timeout" in out["error"]
    assert "RuntimeError" in out["error"]


# --- haops_ui_interact ------------------------------------------------------


@pytest.mark.asyncio
async def test_interact_browser_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(ui, "browser_available", lambda: False)
    out = await ui.haops_ui_interact(_ctx(tmp_path))
    assert out["browser_available"] is False
    assert "Debian addon build" in out["error"]


@pytest.mark.asyncio
async def test_interact_requires_token(tmp_path, monkeypatch):
    monkeypatch.setattr(ui, "browser_available", lambda: True)
    out = await ui.haops_ui_interact(_ctx(tmp_path, token=""))
    assert "access token" in out["error"].lower()


@pytest.mark.asyncio
async def test_interact_success_shape(tmp_path, monkeypatch):
    monkeypatch.setattr(ui, "browser_available", lambda: True)

    async def fake_interact(req: CaptureRequest, actions):
        assert req.access_token == "LLAT-xyz"
        assert actions == [{"type": "scroll", "dy": 400}]
        return {
            "url": "http://homeassistant:8123/lovelace",
            "nav_ms": 600.0,
            "actions_run": [{"type": "scroll", "ok": True, "ms": 5.0}],
            "long_tasks": {"count": 2, "total_ms": 120.0, "max_ms": 80.0},
            "console_errors": [],
        }

    monkeypatch.setattr(ui, "interact", fake_interact)
    out = await ui.haops_ui_interact(
        _ctx(tmp_path), actions=[{"type": "scroll", "dy": 400}]
    )
    assert out["long_tasks"]["count"] == 2
    assert out["actions_run"][0]["type"] == "scroll"
    assert out["nav_ms"] == 600.0


@pytest.mark.asyncio
async def test_interact_capture_error_is_structured(tmp_path, monkeypatch):
    monkeypatch.setattr(ui, "browser_available", lambda: True)

    async def boom(req: CaptureRequest, actions):
        raise RuntimeError("nav timeout")

    monkeypatch.setattr(ui, "interact", boom)
    out = await ui.haops_ui_interact(_ctx(tmp_path))
    assert "nav timeout" in out["error"]
    assert "RuntimeError" in out["error"]


# --- haops_ui_trace ---------------------------------------------------------


@pytest.mark.asyncio
async def test_trace_browser_unavailable(tmp_path, monkeypatch):
    monkeypatch.setattr(ui, "browser_available", lambda: False)
    out = await ui.haops_ui_trace(_ctx(tmp_path))
    assert out["browser_available"] is False
    assert "Debian addon build" in out["error"]


@pytest.mark.asyncio
async def test_trace_requires_token(tmp_path, monkeypatch):
    monkeypatch.setattr(ui, "browser_available", lambda: True)
    out = await ui.haops_ui_trace(_ctx(tmp_path, token=""))
    assert "access token" in out["error"].lower()


@pytest.mark.asyncio
async def test_trace_success_shape(tmp_path, monkeypatch):
    monkeypatch.setattr(ui, "browser_available", lambda: True)

    async def fake_trace(req: CaptureRequest, out_path: str):
        assert req.access_token == "LLAT-xyz"
        assert out_path.endswith(".zip")
        # trace fn writes the file in the real impl; the handler reads it back.
        Path(out_path).write_bytes(b"0" * 4096)
        return {
            "url": "http://homeassistant:8123/lovelace",
            "saved_path": out_path,
            "size_bytes": 4096,
            "nav_ms": 700.0,
        }

    monkeypatch.setattr(ui, "trace", fake_trace)
    out = await ui.haops_ui_trace(_ctx(tmp_path))
    assert out["saved_path"].endswith(".zip")
    assert out["capture_id"]
    assert out["size_bytes"] == 4096
    assert out["nav_ms"] == 700.0


@pytest.mark.asyncio
async def test_trace_capture_error_is_structured(tmp_path, monkeypatch):
    monkeypatch.setattr(ui, "browser_available", lambda: True)

    async def boom(req: CaptureRequest, out_path: str):
        raise RuntimeError("trace failed")

    monkeypatch.setattr(ui, "trace", boom)
    out = await ui.haops_ui_trace(_ctx(tmp_path))
    assert "trace failed" in out["error"]
    assert "RuntimeError" in out["error"]


def test_tools_registered():
    import ha_ops_mcp.tools.ui_suite  # noqa: F401
    from ha_ops_mcp.server import registry

    names = {n for n, _, _ in registry.all_tools()}
    assert "haops_ui_screenshot" in names
    assert "haops_ui_perf" in names
    assert "haops_ui_interact" in names
    assert "haops_ui_trace" in names
