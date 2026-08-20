"""The `sse` transport was removed in v0.63.0.

Two behaviours are pinned here: the runner refuses it outright (so no code
path can silently serve the legacy transport), and the entry point falls
forward to streamable-http so an addon/config carrying `transport: sse` from
<=0.62.x still boots instead of crash-looping.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from ha_ops_mcp._runner import serve_http


class _FakeSettings:
    host = "::"
    port = 8901
    log_level = "INFO"


class _FakeMcp:
    """Minimal FastMCP stand-in — records which app factory was used."""

    def __init__(self) -> None:
        self.settings = _FakeSettings()
        self.streamable_called = False

    def streamable_http_app(self) -> Any:
        self.streamable_called = True
        return object()

    def sse_app(self, mount_path: str | None = None) -> Any:  # pragma: no cover
        raise AssertionError("sse_app must never be called after v0.63.0")


@pytest.mark.asyncio
async def test_serve_http_refuses_sse() -> None:
    mcp = _FakeMcp()
    with pytest.raises(ValueError, match="removed in v0.63.0"):
        await serve_http(mcp, "sse")  # type: ignore[arg-type]
    assert not mcp.streamable_called


@pytest.mark.asyncio
async def test_serve_http_rejects_unknown_transport() -> None:
    with pytest.raises(ValueError, match="does not support transport"):
        await serve_http(_FakeMcp(), "carrier-pigeon")  # type: ignore[arg-type]


def test_sse_app_is_never_referenced_in_source() -> None:
    """Guard against a re-introduction: no module may call sse_app()."""
    src = Path(__file__).resolve().parent.parent / "src" / "ha_ops_mcp"
    offenders = [
        p.relative_to(src).as_posix()
        for p in src.rglob("*.py")
        if "sse_app(" in p.read_text(encoding="utf-8")
    ]
    assert offenders == []


def test_addon_no_longer_exposes_a_transport_option() -> None:
    """v0.63.1 dropped the option entirely — one choice is not a choice.

    Default and schema entry must both be gone, and no `sse` may survive
    anywhere in the manifest (ports_description included).
    """
    cfg = (
        Path(__file__).resolve().parent.parent / "config.yaml"
    ).read_text(encoding="utf-8")
    assert "transport:" not in cfg
    assert "sse" not in cfg.lower()


def test_translations_have_no_transport_entry() -> None:
    """The user-visible description told people to keep `sse` — it must go."""
    translations = (
        Path(__file__).resolve().parent.parent / "translations" / "en.yaml"
    ).read_text(encoding="utf-8")
    assert "transport" not in translations
    assert "sse" not in translations.lower()


def test_run_sh_pins_streamable_http() -> None:
    """A stored `transport` value must not stop the addon booting."""
    run_sh = (
        Path(__file__).resolve().parent.parent / "run.sh"
    ).read_text(encoding="utf-8")
    assert 'transport="streamable-http"' in run_sh
    assert 'export HA_OPS_TRANSPORT="streamable-http"' in run_sh
    # No bashio::config read may decide the transport any more — reading a
    # leftover value into `stored_transport` to warn about it is fine.
    assert "\ntransport=$(bashio::config 'transport')" not in run_sh
