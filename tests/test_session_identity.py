"""Session attribution: audit entries + confirmation tokens carry the MCP
session key (matters once several clients share the static bearer token)."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from ha_ops_mcp.safety.audit import AuditLog
from ha_ops_mcp.safety.confirmation import SafetyManager
from ha_ops_mcp.session import get_current_session, set_current_session


@pytest.fixture(autouse=True)
def _reset_session():
    set_current_session("-")
    yield
    set_current_session("-")


def test_session_var_roundtrip() -> None:
    assert get_current_session() == "-"
    set_current_session("abc123")
    assert get_current_session() == "abc123"
    set_current_session("")
    assert get_current_session() == "-"


@pytest.mark.asyncio
async def test_audit_entries_carry_session(tmp_path: Path) -> None:
    audit = AuditLog(tmp_path)
    set_current_session("clientA:0f0f0f")
    await audit.log(tool="config_apply", details={"path": "x.yaml"})
    line = (tmp_path / "operations.jsonl").read_text().strip().splitlines()[-1]
    assert json.loads(line)["session"] == "clientA:0f0f0f"


def test_token_stamped_with_creating_session() -> None:
    safety = SafetyManager()
    set_current_session("clientA:aaaaaa")
    token = safety.create_token(action="config_apply", details={})
    assert token.session == "clientA:aaaaaa"


def test_cross_session_claim_warns_but_succeeds(
    caplog: pytest.LogCaptureFixture,
) -> None:
    safety = SafetyManager()
    set_current_session("clientA:aaaaaa")
    token = safety.create_token(action="config_apply", details={})
    set_current_session("clientB:bbbbbb")
    with caplog.at_level(logging.WARNING, logger="ha_ops_mcp.safety.confirmation"):
        claimed = safety.claim_token(token.id)
    assert claimed.consumed
    assert any("claimed by session" in r.message for r in caplog.records)


def test_same_session_claim_does_not_warn(
    caplog: pytest.LogCaptureFixture,
) -> None:
    safety = SafetyManager()
    set_current_session("clientA:aaaaaa")
    token = safety.create_token(action="config_apply", details={})
    with caplog.at_level(logging.WARNING, logger="ha_ops_mcp.safety.confirmation"):
        safety.claim_token(token.id)
    assert not any("claimed by session" in r.message for r in caplog.records)
