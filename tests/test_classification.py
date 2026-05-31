"""Tests for op-class/area classification and the activity audit stream."""

from __future__ import annotations

import pytest

from ha_ops_mcp.safety.audit import AuditLog
from ha_ops_mcp.safety.classification import (
    CLASSIFICATION,
    _config_subarea,
    _sql_class,
    classify,
    type_label,
)


class TestClassify:
    def test_read_tool(self):
        assert classify("entity_list", {}) == ("read", "entity")

    def test_mutate_tool(self):
        assert classify("dashboard_apply", {}) == ("mutate", "dashboard")

    def test_destructive_tool(self):
        assert classify("entity_remove", {}) == ("destructive", "entity")

    def test_unknown_defaults_to_mutate_misc(self):
        # Conservative: a tool not yet in the table renders as a mutation,
        # never as a harmless read.
        assert classify("brand_new_tool", {}) == ("mutate", "misc")

    @pytest.mark.parametrize(
        "sql,expected",
        [
            ("SELECT * FROM states", "read"),
            ("  select 1", "read"),
            ("PRAGMA table_info(states)", "read"),
            ("EXPLAIN QUERY PLAN SELECT 1", "read"),
            ("WITH x AS (SELECT 1) SELECT * FROM x", "read"),
            ("INSERT INTO x VALUES (1)", "mutate"),
            ("UPDATE states SET state='x'", "mutate"),
            ("DELETE FROM states WHERE state_id < 5", "destructive"),
            ("DROP TABLE foo", "destructive"),
            ("TRUNCATE states", "destructive"),
            ("-- a comment\nDELETE FROM states", "destructive"),
            ("/* block */ SELECT 1", "read"),
            ("", "destructive"),  # unparseable → conservative
            ("gibberish stmt", "destructive"),
        ],
    )
    def test_db_execute_sql_refinement(self, sql, expected):
        assert classify("db_execute", {"sql": sql}) == (expected, "database")

    @pytest.mark.parametrize(
        "path,area",
        [
            ("/config/automations.yaml", "automation"),
            ("/config/scripts.yaml", "script"),
            ("/config/scenes.yaml", "scene"),
            ("/config/configuration.yaml", "config"),
            ("/config/packages/foo.yaml", "config"),
            ("", "config"),
        ],
    )
    def test_config_subarea(self, path, area):
        assert classify("config_apply", {"path": path}) == ("mutate", area)
        assert _config_subarea(path) == area

    def test_sql_class_direct(self):
        assert _sql_class("select 1") == "read"
        assert _sql_class("") == "destructive"


class TestTypeLabel:
    @pytest.mark.parametrize(
        "tool,details,expected",
        [
            ("service_call", {"domain": "recorder", "service": "purge"}, "service call"),
            ("db_execute", {"sql": "DELETE FROM x"}, "db delete"),
            ("db_execute", {"sql": "SELECT 1"}, "db read"),
            ("db_execute", {"sql": "UPDATE x SET a=1"}, "db write"),
            ("config_create", {}, "new file"),
            ("config_apply", {"old_content": ""}, "new file"),
            ("config_apply", {"old_content": "x"}, "patch"),
            ("entity_remove", {}, "remove"),
            ("entity_list", {}, "list"),
            ("helper_delete", {}, "delete helper"),
            ("brand_new_tool", {}, "tool"),  # fallback = last name segment
        ],
    )
    def test_type_label(self, tool, details, expected):
        assert type_label(tool, details) == expected


class TestRegistryCoverage:
    def test_every_registered_tool_is_classified(self):
        """Catch a newly added tool that forgot a classification entry."""
        # Import all tool modules so they register (mirrors create_server).
        import ha_ops_mcp.tools.addon  # noqa: F401
        import ha_ops_mcp.tools.backup  # noqa: F401
        import ha_ops_mcp.tools.batch  # noqa: F401
        import ha_ops_mcp.tools.config  # noqa: F401
        import ha_ops_mcp.tools.dashboard  # noqa: F401
        import ha_ops_mcp.tools.db  # noqa: F401
        import ha_ops_mcp.tools.debugger  # noqa: F401
        import ha_ops_mcp.tools.entity  # noqa: F401
        import ha_ops_mcp.tools.ergonomics  # noqa: F401
        import ha_ops_mcp.tools.helper  # noqa: F401
        import ha_ops_mcp.tools.refs  # noqa: F401
        import ha_ops_mcp.tools.registry  # noqa: F401
        import ha_ops_mcp.tools.rollback  # noqa: F401
        import ha_ops_mcp.tools.service  # noqa: F401
        import ha_ops_mcp.tools.shell  # noqa: F401
        import ha_ops_mcp.tools.system  # noqa: F401
        import ha_ops_mcp.tools.tools_check  # noqa: F401
        from ha_ops_mcp.server import registry

        # db_execute is intentionally content-refined (not a static row).
        known = set(CLASSIFICATION) | {"db_execute"}
        missing = []
        for name, _handler, _schema in registry.all_tools():
            bare = name[len("haops_"):] if name.startswith("haops_") else name
            if bare not in known:
                missing.append(bare)
        assert not missing, f"Tools missing from CLASSIFICATION: {missing}"


class TestAuditStamping:
    @pytest.mark.asyncio
    async def test_log_stamps_op_class_and_area(self, tmp_path):
        al = AuditLog(tmp_path / "audit")
        await al.log("config_apply", {"path": "/config/automations.yaml"})
        entry = al.read_recent()[0]
        assert entry["op_class"] == "mutate"
        assert entry["area"] == "automation"

    @pytest.mark.asyncio
    async def test_explicit_class_area_win(self, tmp_path):
        al = AuditLog(tmp_path / "audit")
        await al.log("config_apply", {}, op_class="destructive", area="custom")
        entry = al.read_recent()[0]
        assert entry["op_class"] == "destructive"
        assert entry["area"] == "custom"


class TestActivityStream:
    @pytest.mark.asyncio
    async def test_log_activity_separate_from_operations(self, tmp_path):
        al = AuditLog(tmp_path / "audit")
        await al.log("config_apply", {"path": "/config/x.yaml"})  # mutation
        await al.log_activity("entity_list", {"domain": "light"})  # read
        # operations.jsonl holds only the mutation
        ops = al.read_recent()
        assert [e["tool"] for e in ops] == ["config_apply"]
        # merged view holds both, newest-first
        merged = al.read_recent_merged()
        tools = {e["tool"] for e in merged}
        assert tools == {"config_apply", "entity_list"}

    @pytest.mark.asyncio
    async def test_merged_sorted_newest_first(self, tmp_path):
        al = AuditLog(tmp_path / "audit")
        await al.log("config_apply", {"path": "/config/a.yaml"})
        await al.log_activity("entity_list", {})
        await al.log("config_patch", {"path": "/config/b.yaml"})
        merged = al.read_recent_merged()
        ts = [e["timestamp"] for e in merged]
        assert ts == sorted(ts, reverse=True)

    @pytest.mark.asyncio
    async def test_activity_rotation(self, tmp_path):
        al = AuditLog(tmp_path / "audit")
        al._ACTIVITY_MAX_BYTES = 200  # tiny cap to force rotation
        for i in range(50):
            await al.log_activity("entity_list", {"i": i, "pad": "x" * 20})
        assert (al._dir / "activity.1.jsonl").exists()
        # Current file still readable and bounded.
        assert al._activity_path.stat().st_size < al._ACTIVITY_MAX_BYTES * 3

    @pytest.mark.asyncio
    async def test_clear_removes_activity(self, tmp_path):
        al = AuditLog(tmp_path / "audit")
        await al.log("config_apply", {"path": "/config/a.yaml"})
        await al.log_activity("entity_list", {})
        al.clear()
        assert not al._activity_path.exists()
        assert al.read_recent_merged() == []
