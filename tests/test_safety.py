"""Tests for safety layer — tokens, path guard, rollback."""

import time
from pathlib import Path

import pytest

from ha_ops_mcp.safety.confirmation import (
    SafetyManager,
    TokenConsumedError,
    TokenNotFoundError,
)
from ha_ops_mcp.safety.path_guard import PathGuard, PathTraversalError
from ha_ops_mcp.safety.rollback import RollbackManager, UndoEntry, UndoType

# ── Confirmation tokens ──

class TestSafetyManager:
    def test_create_and_validate(self):
        sm = SafetyManager()
        token = sm.create_token("test_action", {"key": "value"})
        validated = sm.validate_token(token.id)
        assert validated.action == "test_action"
        assert validated.details == {"key": "value"}

    def test_consume_token(self):
        sm = SafetyManager()
        token = sm.create_token("test", {})
        sm.consume_token(token.id)
        with pytest.raises(TokenConsumedError):
            sm.validate_token(token.id)

    def test_double_consume(self):
        sm = SafetyManager()
        token = sm.create_token("test", {})
        sm.consume_token(token.id)
        with pytest.raises(TokenConsumedError):
            sm.consume_token(token.id)

    def test_unknown_token(self):
        sm = SafetyManager()
        with pytest.raises(TokenNotFoundError):
            sm.validate_token("nonexistent")

    def test_no_expiry(self):
        """v0.20.0: tokens never expire — staleness is caught by each
        tool's own context-match checks, not by a time cap."""
        sm = SafetyManager()
        token = sm.create_token("test", {})
        validated = sm.validate_token(token.id)
        assert validated.action == "test"

    def test_list_tokens_default_only_active(self):
        sm = SafetyManager()
        t1 = sm.create_token("a", {})
        t2 = sm.create_token("b", {})
        sm.consume_token(t1.id)
        tokens = sm.list_tokens()
        assert [t.id for t in tokens] == [t2.id]

    def test_list_tokens_include_consumed(self):
        sm = SafetyManager()
        t1 = sm.create_token("a", {})
        t2 = sm.create_token("b", {})
        sm.consume_token(t1.id)
        tokens = sm.list_tokens(include_consumed=True)
        assert {t.id for t in tokens} == {t1.id, t2.id}

    def test_list_tokens_newest_first(self):
        sm = SafetyManager()
        t1 = sm.create_token("first", {})
        time.sleep(0.005)
        t2 = sm.create_token("second", {})
        tokens = sm.list_tokens()
        assert [t.id for t in tokens] == [t2.id, t1.id]


# ── Path guard ──

class TestPathGuard:
    def test_valid_relative_path(self, tmp_path: Path):
        guard = PathGuard(tmp_path)
        (tmp_path / "configuration.yaml").touch()
        result = guard.validate("configuration.yaml")
        assert result == tmp_path / "configuration.yaml"

    def test_valid_absolute_path(self, tmp_path: Path):
        guard = PathGuard(tmp_path)
        target = tmp_path / "test.yaml"
        target.touch()
        result = guard.validate(str(target))
        assert result == target

    def test_reject_traversal(self, tmp_path: Path):
        guard = PathGuard(tmp_path)
        with pytest.raises(PathTraversalError):
            guard.validate("../../etc/passwd")

    def test_reject_absolute_outside(self, tmp_path: Path):
        guard = PathGuard(tmp_path)
        with pytest.raises(PathTraversalError):
            guard.validate("/etc/passwd")


# ── Rollback manager ──

class TestRollbackManager:
    def test_transaction_lifecycle(self):
        rm = RollbackManager()
        txn = rm.begin("test_op")
        assert rm.active is not None
        assert rm.active.id == txn.id

        txn.savepoint("step1", UndoEntry(
            type=UndoType.FILE,
            description="undo step1",
            data={"path": "/config/test.yaml", "content": "old"},
        ))
        txn.savepoint("step2", UndoEntry(
            type=UndoType.FILE,
            description="undo step2",
            data={"path": "/config/test2.yaml", "content": "old2"},
        ))

        assert len(txn.active_savepoints) == 2

        rm.commit(txn.id)
        assert txn.committed is True
        assert rm.active is None

    def test_rollback_single_savepoint(self):
        rm = RollbackManager()
        txn = rm.begin("test")
        undo1 = UndoEntry(type=UndoType.FILE, description="u1", data={"content": "a"})
        undo2 = UndoEntry(type=UndoType.FILE, description="u2", data={"content": "b"})
        sp1 = txn.savepoint("s1", undo1)
        sp2 = txn.savepoint("s2", undo2)

        undo = rm.rollback_savepoint(txn.id, sp2.id)
        assert undo.data["content"] == "b"
        assert len(txn.active_savepoints) == 1
        assert txn.active_savepoints[0].id == sp1.id

    def test_rollback_entire_transaction(self):
        rm = RollbackManager()
        txn = rm.begin("test")
        txn.savepoint("s1", UndoEntry(type=UndoType.FILE, description="u1", data={"c": "1"}))
        txn.savepoint("s2", UndoEntry(type=UndoType.FILE, description="u2", data={"c": "2"}))
        txn.savepoint("s3", UndoEntry(type=UndoType.FILE, description="u3", data={"c": "3"}))

        undos = rm.rollback_transaction(txn.id)
        assert len(undos) == 3
        # Should be in reverse order (newest first)
        assert undos[0].data["c"] == "3"
        assert undos[1].data["c"] == "2"
        assert undos[2].data["c"] == "1"
        assert len(txn.active_savepoints) == 0

    def test_rollback_already_rolled_back(self):
        rm = RollbackManager()
        txn = rm.begin("test")
        sp = txn.savepoint("s1", UndoEntry(type=UndoType.FILE, description="u", data={}))
        rm.rollback_savepoint(txn.id, sp.id)

        with pytest.raises(ValueError):
            rm.rollback_savepoint(txn.id, sp.id)

    def test_discard_transaction(self):
        rm = RollbackManager()
        txn = rm.begin("test")
        rm.discard(txn.id)
        assert rm.active is None
        assert rm.get_transaction(txn.id) is None

    def test_transaction_token_id(self):
        """begin(token_id=...) correlates with authorizing token."""
        rm = RollbackManager()
        txn = rm.begin("op", token_id="tok-abc123")
        assert txn.token_id == "tok-abc123"

    def test_transaction_token_id_defaults_to_none(self):
        rm = RollbackManager()
        txn = rm.begin("op")
        assert txn.token_id is None


# ── AuditLog.read_recent ──


class TestAuditLogReadRecent:
    @pytest.mark.asyncio
    async def test_read_recent_empty(self, tmp_path):
        from ha_ops_mcp.safety.audit import AuditLog
        al = AuditLog(tmp_path / "audit")
        assert al.read_recent() == []

    @pytest.mark.asyncio
    async def test_read_recent_returns_newest_first(self, tmp_path):
        from ha_ops_mcp.safety.audit import AuditLog
        al = AuditLog(tmp_path / "audit")
        await al.log("first", {"x": 1})
        await al.log("second", {"x": 2})
        await al.log("third", {"x": 3})
        entries = al.read_recent()
        assert [e["tool"] for e in entries] == ["third", "second", "first"]

    @pytest.mark.asyncio
    async def test_read_recent_respects_limit(self, tmp_path):
        from ha_ops_mcp.safety.audit import AuditLog
        al = AuditLog(tmp_path / "audit")
        for i in range(10):
            await al.log(f"t{i}", {"i": i})
        entries = al.read_recent(limit=3)
        assert len(entries) == 3
        assert entries[0]["tool"] == "t9"

    @pytest.mark.asyncio
    async def test_read_recent_skips_malformed_lines(self, tmp_path):
        from ha_ops_mcp.safety.audit import AuditLog
        al = AuditLog(tmp_path / "audit")
        await al.log("ok", {})
        # Inject a broken line
        with open(al._log_path, "a") as f:
            f.write("not json at all\n")
        await al.log("also_ok", {})
        entries = al.read_recent()
        assert len(entries) == 2
        assert [e["tool"] for e in entries] == ["also_ok", "ok"]
