"""Tests for haops_addon_update.

The self-update path is what needs covering. Supervisor **forbids** an add-on
from updating itself (HTTP 403 "can't update itself!", verified on Supervisor
2026.07.5), so the contract is: say so honestly, never claim a self-update was
triggered, and point at the HA UI. The old contract — fire a detached child and
report `triggered: true` — was a fiction built on the false premise that
Supervisor would accept and then tear us down; it hid the 403 for three
releases.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from ha_ops_mcp.tools import addon as addon_mod
from ha_ops_mcp.tools.addon import haops_addon_update

SELF_SLUG = "f5a4c56f_ha_ops_mcp"
OTHER_SLUG = "5c53de3b_esphome"


class _FakeSafety:
    def __init__(self) -> None:
        self.tokens: dict[str, Any] = {}
        self.consumed: list[str] = []

    def create_token(self, action: str, details: dict[str, Any]):
        tk = SimpleNamespace(id="tok1", action=action, details=details)
        self.tokens["tok1"] = tk
        return tk

    def validate_token(self, token_id: str):
        if token_id not in self.tokens:
            raise ValueError("Invalid or already-used token")
        return self.tokens[token_id]

    def consume_token(self, token_id: str) -> None:
        self.consumed.append(token_id)
        self.tokens.pop(token_id, None)

    def claim_token(self, token_id: str):
        if token_id not in self.tokens:
            raise ValueError("Invalid or already-used token")
        tk = self.tokens.pop(token_id)
        self.consumed.append(token_id)
        return tk


class _FakeAudit:
    def __init__(self) -> None:
        self.entries: list[dict[str, Any]] = []

    async def log(self, **kwargs: Any) -> None:
        self.entries.append(kwargs)


def _ctx() -> Any:
    return SimpleNamespace(safety=_FakeSafety(), audit=_FakeAudit())


@pytest.fixture(autouse=True)
def _supervisor(monkeypatch):
    """Fake Supervisor: two add-ons, both with an update pending."""
    posts: list[str] = []
    infos = {
        SELF_SLUG: {
            "name": "HA Ops MCP",
            "slug": SELF_SLUG,
            "version": "0.57.0",
            "version_latest": "0.57.1",
            "update_available": True,
            "state": "started",
        },
        OTHER_SLUG: {
            "name": "ESPHome Device Builder",
            "slug": OTHER_SLUG,
            "version": "2026.7.3",
            "version_latest": "2026.7.4",
            "update_available": True,
            "state": "started",
        },
    }

    async def fake_get(ctx, path):
        if path == "/addons/self/info":
            return infos[SELF_SLUG]
        for slug, info in infos.items():
            if path == f"/addons/{slug}/info":
                return info
        return None

    async def fake_post(ctx, path, data=None):
        posts.append(path)
        return {"ok": True}

    fired: list[str] = []

    async def fake_attempt(ctx, slug):
        """Stand in for the real POST — mimics Supervisor's 403 refusal."""
        fired.append(slug)
        return {
            "error": "Supervisor refuses to let an add-on update itself.",
            "http_status": 403,
            "supervisor_message": (
                f'{{"result":"error","message":"App {slug} can\'t update itself!"}}'
            ),
            "how_to_update": "Home Assistant UI: Settings > Add-ons > ... > Update.",
        }

    monkeypatch.setattr(addon_mod, "_supervisor_get", fake_get)
    monkeypatch.setattr(addon_mod, "_supervisor_post", fake_post)
    monkeypatch.setattr(addon_mod, "_attempt_self_update", fake_attempt)
    monkeypatch.setattr(addon_mod, "_self_slug_cache", None)
    monkeypatch.setattr(
        addon_mod, "_self_update_log_path", lambda ctx: Path("/tmp/self_update.log")
    )
    return SimpleNamespace(posts=posts, infos=infos, fired=fired)


@pytest.mark.asyncio
async def test_reloads_store_before_reading_versions(_supervisor):
    """Supervisor caches the store index — a stale index hides new releases."""
    await haops_addon_update(_ctx(), slug=OTHER_SLUG)
    assert "/store/reload" in _supervisor.posts


@pytest.mark.asyncio
async def test_other_addon_preview_then_update(_supervisor):
    ctx = _ctx()
    preview = await haops_addon_update(ctx, slug=OTHER_SLUG)
    assert preview["version"] == "2026.7.3"
    assert preview["version_latest"] == "2026.7.4"
    assert preview["self"] is False
    assert f"/addons/{OTHER_SLUG}/update" not in _supervisor.posts

    result = await haops_addon_update(
        ctx, slug=OTHER_SLUG, confirm=True, token=preview["token"]
    )
    assert result["success"] is True
    assert f"/addons/{OTHER_SLUG}/update" in _supervisor.posts
    assert ctx.safety.consumed == ["tok1"]


@pytest.mark.asyncio
async def test_already_latest_is_a_noop(_supervisor):
    _supervisor.infos[OTHER_SLUG]["update_available"] = False
    result = await haops_addon_update(_ctx(), slug=OTHER_SLUG)
    assert result["already_latest"] is True
    assert f"/addons/{OTHER_SLUG}/update" not in _supervisor.posts


@pytest.mark.asyncio
async def test_self_update_refused_without_allow_self(_supervisor):
    """Refusal must name the real cause and the only working route."""
    result = await haops_addon_update(_ctx(), slug=SELF_SLUG)
    assert "error" in result
    assert result["self"] is True
    assert "update itself" in result["error"]
    assert "Settings > Add-ons" in result["how_to_update"]
    # Must not even mint a token for a path that cannot succeed.
    assert "token" not in result
    assert _supervisor.fired == []


@pytest.mark.asyncio
async def test_self_update_never_claims_it_was_triggered(_supervisor):
    """The regression that cost three releases: a fictional success.

    Supervisor answers 403; the tool must surface that, not `triggered: true`.
    """
    ctx = _ctx()
    preview = await haops_addon_update(ctx, slug=SELF_SLUG, allow_self=True)
    result = await haops_addon_update(
        ctx, slug=SELF_SLUG, allow_self=True, confirm=True, token=preview["token"]
    )
    assert "triggered" not in result
    assert result["http_status"] == 403
    assert "update itself" in result["supervisor_message"]
    assert result["outcome_log"].endswith("self_update.log")
    # It did attempt the POST — kept so a future Supervisor that lifts the
    # guard is detected rather than assumed.
    assert _supervisor.fired == [SELF_SLUG]


@pytest.mark.asyncio
async def test_self_update_audits_before_firing(_supervisor):
    """Audit lands before the attempt, in case Supervisor ever does tear us down."""
    ctx = _ctx()
    preview = await haops_addon_update(ctx, slug=SELF_SLUG, allow_self=True)
    await haops_addon_update(
        ctx, slug=SELF_SLUG, allow_self=True, confirm=True, token=preview["token"]
    )
    assert ctx.audit.entries[-1]["tool"] == "addon_update"
    assert ctx.audit.entries[-1]["details"]["self"] is True
    assert ctx.audit.entries[-1]["details"]["version_latest"] == "0.57.1"


@pytest.mark.asyncio
async def test_token_is_bound_to_slug(_supervisor):
    """A token minted for one add-on must not update a different one."""
    ctx = _ctx()
    preview = await haops_addon_update(ctx, slug=OTHER_SLUG)
    result = await haops_addon_update(
        ctx, slug=SELF_SLUG, allow_self=True, confirm=True, token=preview["token"]
    )
    assert "does not match the token" in result["error"]
    assert _supervisor.fired == []


@pytest.mark.asyncio
async def test_confirm_requires_token(_supervisor):
    result = await haops_addon_update(_ctx(), slug=OTHER_SLUG, confirm=True)
    assert result["error"] == "confirm=true requires a token"


@pytest.mark.asyncio
async def test_unknown_addon(_supervisor):
    result = await haops_addon_update(_ctx(), slug="nope")
    assert "not found" in result["error"]


def test_classification():
    from ha_ops_mcp.safety.classification import classify, type_label

    assert classify("addon_update", None) == ("mutate", "addon")
    assert type_label("addon_update", None) == "update addon"
