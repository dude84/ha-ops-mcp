"""Tests for haops_dashboard_validate_yaml + the underlying validator."""

from __future__ import annotations

from pathlib import Path

import pytest

from ha_ops_mcp.lovelace_validate import _load_card_schemas, validate_yaml
from ha_ops_mcp.tools.dashboard import haops_dashboard_validate_yaml

FIXTURES = Path(__file__).parent / "fixtures" / "lovelace_yaml"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text()


def test_valid_dashboard():
    result = validate_yaml(_read("valid_dashboard.yaml"), scope="dashboard")
    assert result["ok"] is True, result["errors"]
    assert result["errors"] == []
    assert result["parsed_summary"]["views"] == 1
    assert result["parsed_summary"]["cards"] >= 3


def test_valid_view():
    result = validate_yaml(_read("valid_view.yaml"), scope="view")
    assert result["ok"] is True, result["errors"]


def test_valid_section():
    result = validate_yaml(_read("valid_section.yaml"), scope="section")
    assert result["ok"] is True, result["errors"]


def test_invalid_yaml_syntax_reports_line():
    result = validate_yaml(_read("invalid_yaml_syntax.yaml"), scope="dashboard")
    assert result["ok"] is False
    assert any("YAML parse" in e["reason"] for e in result["errors"])
    # ruamel should provide a line number for the parse failure
    assert any(e["line"] is not None for e in result["errors"])


def test_invalid_decluttering_variables_map_not_list():
    """The exact bug from GAP_INTERFACE_UX_ANALYSIS.md."""
    result = validate_yaml(
        _read("invalid_decluttering_variables.yaml"), scope="dashboard"
    )
    assert result["ok"] is False
    msgs = " ".join(e["reason"] for e in result["errors"])
    assert "variables" in msgs
    assert "single-key map" in msgs


def test_valid_decluttering_variables_list_passes():
    result = validate_yaml(
        _read("valid_decluttering_variables.yaml"), scope="dashboard"
    )
    assert result["ok"] is True, result["errors"]


def test_unterminated_js_template_block():
    result = validate_yaml(
        _read("invalid_unterminated_js_template.yaml"), scope="dashboard"
    )
    assert result["ok"] is False
    assert any("`[[[`" in e["reason"] for e in result["errors"])


def test_unknown_custom_card_warns_does_not_error():
    result = validate_yaml(
        _read("valid_unknown_custom_card.yaml"), scope="dashboard"
    )
    assert result["ok"] is True
    assert result["warnings"], "expected at least one warning"
    assert any(
        "Unknown custom card" in w["reason"] for w in result["warnings"]
    )
    assert "custom:totally-made-up-card" in result["parsed_summary"]["custom_cards"]


def test_missing_card_type_errors():
    result = validate_yaml(
        _read("invalid_missing_card_type.yaml"), scope="dashboard"
    )
    assert result["ok"] is False
    assert any(
        "missing required `type`" in e["reason"] for e in result["errors"]
    )


def test_view_scope_rejects_dashboard_input():
    """Passing a dashboard (top-level views:) when scope='view' should error."""
    result = validate_yaml(_read("valid_dashboard.yaml"), scope="view")
    assert result["ok"] is False
    assert any("dashboard" in e["reason"].lower() for e in result["errors"])


def test_dashboard_scope_rejects_view_input():
    """Passing a view (no top-level views:) when scope='dashboard' should error."""
    result = validate_yaml(_read("valid_view.yaml"), scope="dashboard")
    assert result["ok"] is False
    assert any(
        "views" in e["reason"] for e in result["errors"]
    )


def test_invalid_scope_rejected():
    result = validate_yaml("foo: 1", scope="bogus")
    assert result["ok"] is False
    assert any("scope" in e["reason"] for e in result["errors"])


def test_empty_yaml_errors():
    result = validate_yaml("", scope="dashboard")
    assert result["ok"] is False


def test_schema_bundle_loads():
    """All bundled card schemas must parse and have a `type` field."""
    schemas = _load_card_schemas.__wrapped__()  # bypass cache
    assert len(schemas) >= 8
    for card_type, schema in schemas.items():
        assert isinstance(schema, dict)
        assert "type" in schema
        assert schema["type"] == card_type


@pytest.mark.asyncio
async def test_tool_wrapper_returns_validator_output(ctx):
    """Tool wrapper round-trips through validate_yaml."""
    result = await haops_dashboard_validate_yaml(
        ctx, yaml_str=_read("valid_dashboard.yaml"), scope="dashboard"
    )
    assert result["ok"] is True
    assert "parsed_summary" in result


@pytest.mark.asyncio
async def test_tool_wrapper_decluttering_bug(ctx):
    result = await haops_dashboard_validate_yaml(
        ctx,
        yaml_str=_read("invalid_decluttering_variables.yaml"),
        scope="dashboard",
    )
    assert result["ok"] is False
