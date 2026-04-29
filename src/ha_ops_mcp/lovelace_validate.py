"""Lightweight Lovelace YAML validator.

Structural sanity + per-card field checks against bundled schemas. NOT a
full HA-authoritative validator — see docs/lovelace_validation.md for the
explicit scope. Catches the failure modes seen in real paste-back sessions:
YAML syntax, missing card type:, decluttering variables: shape, unterminated
[[[ JS ]]] template blocks.
"""

from __future__ import annotations

import io
from functools import lru_cache
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.error import MarkedYAMLError

SCHEMAS_DIR = Path(__file__).parent / "static" / "lovelace_card_schemas"

_TYPE_VALIDATORS: dict[str, Any] = {
    "string": lambda v: isinstance(v, str),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
    "any": lambda v: True,
}


@lru_cache(maxsize=1)
def _load_card_schemas() -> dict[str, dict[str, Any]]:
    """Load all bundled card schemas keyed by card type."""
    yaml = YAML(typ="safe")
    schemas: dict[str, dict[str, Any]] = {}
    if not SCHEMAS_DIR.is_dir():
        return schemas
    for path in SCHEMAS_DIR.rglob("*.yaml"):
        try:
            with path.open() as f:
                data = yaml.load(f)
            if isinstance(data, dict) and "type" in data:
                schemas[str(data["type"])] = data
        except Exception:
            continue
    return schemas


def _node_line(node: Any) -> int | None:
    """Best-effort 1-indexed source line for a ruamel node."""
    lc = getattr(node, "lc", None)
    if lc is None:
        return None
    line = getattr(lc, "line", None)
    return (line + 1) if isinstance(line, int) else None


def _key_line(parent: Any, key: str) -> int | None:
    """Best-effort 1-indexed source line for a specific key in a mapping."""
    lc = getattr(parent, "lc", None)
    if lc is None:
        return None
    try:
        info = lc.key(key)
    except (KeyError, AttributeError):
        return None
    if isinstance(info, (list, tuple)) and info:
        return int(info[0]) + 1
    return None


def _err(
    errors: list[dict[str, Any]],
    *,
    line: int | None,
    path: str,
    field: str | None,
    reason: str,
    severity: str = "error",
) -> None:
    errors.append({
        "line": line,
        "column": None,
        "path": path,
        "field": field,
        "reason": reason,
        "severity": severity,
    })


def _check_js_templates(text: str, errors: list[dict[str, Any]]) -> None:
    """Count `[[[` vs `]]]` occurrences across the source.

    Mismatched counts mean an unterminated JS template block somewhere — HA's
    editor surfaces this as 'Cannot read properties of undefined (reading
    startsWith)' with no line info. We can't always pinpoint the exact line
    without parsing JS, but we can flag the imbalance and the line of the
    last orphan opener.
    """
    lines = text.splitlines()
    stack: list[int] = []
    closers: list[int] = []
    for idx, line in enumerate(lines, start=1):
        # Count occurrences per line — multiple openers/closers per line are legal
        for _ in range(line.count("[[[")):
            stack.append(idx)
        for _ in range(line.count("]]]")):
            if stack:
                stack.pop()
            else:
                closers.append(idx)
    for line_no in stack:
        _err(
            errors,
            line=line_no,
            path="",
            field=None,
            reason="Unterminated `[[[` JS template block (no matching `]]]`)",
        )
    for line_no in closers:
        _err(
            errors,
            line=line_no,
            path="",
            field=None,
            reason="Stray `]]]` with no matching `[[[`",
        )


def _is_list_of_single_key_maps(value: Any) -> bool:
    if not isinstance(value, list):
        return False
    return all(isinstance(item, dict) and len(item) == 1 for item in value)


def _check_card(
    card: dict[str, Any],
    path: str,
    errors: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
    custom_cards: set[str],
) -> None:
    line = _node_line(card)

    card_type = card.get("type")
    if not card_type:
        _err(
            errors,
            line=line,
            path=path,
            field="type",
            reason="Card is missing required `type` field",
        )
        return

    if not isinstance(card_type, str):
        _err(
            errors,
            line=_key_line(card, "type") or line,
            path=path,
            field="type",
            reason=f"`type` must be a string, got {type(card_type).__name__}",
        )
        return

    if card_type.startswith("custom:"):
        custom_cards.add(card_type)

    schemas = _load_card_schemas()
    schema = schemas.get(card_type)
    if schema is None:
        if card_type.startswith("custom:"):
            warnings.append({
                "line": line,
                "path": path,
                "field": "type",
                "reason": (
                    f"Unknown custom card '{card_type}' — structural check "
                    "passed but no field schema available. Verify the card "
                    "module is installed via haops_dashboard_resources."
                ),
                "severity": "warning",
            })
        return

    # Required fields
    for req in schema.get("required", []) or []:
        if req not in card:
            _err(
                errors,
                line=line,
                path=path,
                field=req,
                reason=f"`{card_type}` requires field `{req}`",
            )

    # Field types
    for field, expected in (schema.get("field_types") or {}).items():
        if field not in card:
            continue
        value = card[field]
        if expected == "list_of_single_key_maps":
            if not _is_list_of_single_key_maps(value):
                _err(
                    errors,
                    line=_key_line(card, field) or line,
                    path=f"{path}.{field}",
                    field=field,
                    reason=(
                        f"`{field}` for `{card_type}` must be a "
                        "list of single-key maps (e.g. `- entity: light.x`), "
                        f"got {type(value).__name__}"
                    ),
                )
            continue
        check = _TYPE_VALIDATORS.get(expected)
        if check is not None and not check(value):
            _err(
                errors,
                line=_key_line(card, field) or line,
                path=f"{path}.{field}",
                field=field,
                reason=(
                    f"`{field}` for `{card_type}` must be `{expected}`, "
                    f"got {type(value).__name__}"
                ),
            )

    # Recurse into nested cards (vertical-stack, grid, conditional, etc.)
    nested_cards = card.get("cards")
    if isinstance(nested_cards, list):
        for i, nested in enumerate(nested_cards):
            if isinstance(nested, dict):
                _check_card(
                    nested, f"{path}.cards[{i}]", errors, warnings, custom_cards
                )

    nested_card = card.get("card")
    if isinstance(nested_card, dict):
        _check_card(
            nested_card, f"{path}.card", errors, warnings, custom_cards
        )


def _check_section(
    section: dict[str, Any],
    path: str,
    errors: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
    custom_cards: set[str],
) -> None:
    cards = section.get("cards")
    if cards is None:
        _err(
            errors,
            line=_node_line(section),
            path=path,
            field="cards",
            reason="Section is missing `cards` field",
        )
        return
    if not isinstance(cards, list):
        _err(
            errors,
            line=_key_line(section, "cards"),
            path=f"{path}.cards",
            field="cards",
            reason=f"`cards` must be a list, got {type(cards).__name__}",
        )
        return
    for i, card in enumerate(cards):
        if not isinstance(card, dict):
            _err(
                errors,
                line=_node_line(card),
                path=f"{path}.cards[{i}]",
                field=None,
                reason=f"Card must be a mapping, got {type(card).__name__}",
            )
            continue
        _check_card(
            card, f"{path}.cards[{i}]", errors, warnings, custom_cards
        )


def _check_view(
    view: dict[str, Any],
    path: str,
    errors: list[dict[str, Any]],
    warnings: list[dict[str, Any]],
    custom_cards: set[str],
    counts: dict[str, int],
) -> None:
    sections = view.get("sections")
    cards = view.get("cards")

    if sections is not None:
        if not isinstance(sections, list):
            _err(
                errors,
                line=_key_line(view, "sections"),
                path=f"{path}.sections",
                field="sections",
                reason=f"`sections` must be a list, got {type(sections).__name__}",
            )
        else:
            counts["sections"] += len(sections)
            for i, section in enumerate(sections):
                if isinstance(section, dict):
                    _check_section(
                        section,
                        f"{path}.sections[{i}]",
                        errors,
                        warnings,
                        custom_cards,
                    )
                    inner = section.get("cards")
                    if isinstance(inner, list):
                        counts["cards"] += len(inner)

    if cards is not None:
        if not isinstance(cards, list):
            _err(
                errors,
                line=_key_line(view, "cards"),
                path=f"{path}.cards",
                field="cards",
                reason=f"`cards` must be a list, got {type(cards).__name__}",
            )
        else:
            counts["cards"] += len(cards)
            for i, card in enumerate(cards):
                if isinstance(card, dict):
                    _check_card(
                        card, f"{path}.cards[{i}]", errors, warnings, custom_cards
                    )

    if sections is None and cards is None:
        warnings.append({
            "line": _node_line(view),
            "path": path,
            "field": None,
            "reason": "View has neither `sections` nor `cards` — empty view?",
            "severity": "warning",
        })


def validate_yaml(yaml_str: str, scope: str = "dashboard") -> dict[str, Any]:
    """Validate a Lovelace YAML string.

    Args:
        yaml_str: The YAML to validate.
        scope: One of "dashboard", "view", "section". Determines the expected
            top-level shape.

    Returns:
        ``{ok, errors, warnings, parsed_summary}`` where ``parsed_summary``
        reports view/section/card counts and the list of recognized custom
        card types.
    """
    if scope not in ("dashboard", "view", "section"):
        return {
            "ok": False,
            "errors": [{
                "line": None,
                "path": "",
                "field": "scope",
                "reason": (
                    f"Invalid scope '{scope}' — must be 'dashboard', "
                    "'view', or 'section'"
                ),
                "severity": "error",
            }],
            "warnings": [],
            "parsed_summary": {},
        }

    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    custom_cards: set[str] = set()

    # Cheap early check that operates on the raw text — catches unterminated
    # `[[[` blocks even when YAML parsing succeeds (folded scalars hide them
    # inside the value string).
    _check_js_templates(yaml_str, errors)

    # Parse YAML
    yaml = YAML(typ="rt")
    try:
        data = yaml.load(io.StringIO(yaml_str))
    except MarkedYAMLError as e:
        line = (e.problem_mark.line + 1) if e.problem_mark else None
        col = (e.problem_mark.column + 1) if e.problem_mark else None
        errors.append({
            "line": line,
            "column": col,
            "path": "",
            "field": None,
            "reason": f"YAML parse error: {e.problem}",
            "severity": "error",
        })
        return {
            "ok": False,
            "errors": errors,
            "warnings": warnings,
            "parsed_summary": {},
        }
    except Exception as e:
        errors.append({
            "line": None,
            "column": None,
            "path": "",
            "field": None,
            "reason": f"YAML parse failed: {e}",
            "severity": "error",
        })
        return {
            "ok": False,
            "errors": errors,
            "warnings": warnings,
            "parsed_summary": {},
        }

    if data is None:
        errors.append({
            "line": None,
            "column": None,
            "path": "",
            "field": None,
            "reason": "Empty YAML document",
            "severity": "error",
        })
        return {
            "ok": False,
            "errors": errors,
            "warnings": warnings,
            "parsed_summary": {},
        }

    counts = {"views": 0, "sections": 0, "cards": 0}

    if scope == "dashboard":
        if not isinstance(data, dict):
            _err(
                errors,
                line=1,
                path="",
                field=None,
                reason=(
                    "Dashboard YAML must be a mapping with a top-level "
                    f"`views:` list, got {type(data).__name__}"
                ),
            )
        else:
            views = data.get("views")
            if not isinstance(views, list):
                _err(
                    errors,
                    line=_key_line(data, "views"),
                    path="views",
                    field="views",
                    reason=(
                        "Dashboard must have a `views:` list at the top level. "
                        "If you meant to validate a single view, pass scope='view'."
                    ),
                )
            else:
                counts["views"] = len(views)
                for i, view in enumerate(views):
                    if not isinstance(view, dict):
                        _err(
                            errors,
                            line=_node_line(view),
                            path=f"views[{i}]",
                            field=None,
                            reason="View must be a mapping",
                        )
                        continue
                    _check_view(
                        view,
                        f"views[{i}]",
                        errors,
                        warnings,
                        custom_cards,
                        counts,
                    )

    elif scope == "view":
        if not isinstance(data, dict):
            _err(
                errors,
                line=1,
                path="",
                field=None,
                reason=f"View YAML must be a mapping, got {type(data).__name__}",
            )
        elif "views" in data:
            _err(
                errors,
                line=_key_line(data, "views"),
                path="",
                field="views",
                reason=(
                    "Got a dashboard (top-level `views:` list) but scope='view' "
                    "expects a single view body. Pass scope='dashboard' or "
                    "extract the view first."
                ),
            )
        else:
            counts["views"] = 1
            _check_view(data, "", errors, warnings, custom_cards, counts)

    else:  # section
        if not isinstance(data, dict):
            _err(
                errors,
                line=1,
                path="",
                field=None,
                reason=f"Section YAML must be a mapping, got {type(data).__name__}",
            )
        else:
            counts["sections"] = 1
            _check_section(data, "", errors, warnings, custom_cards)
            inner = data.get("cards")
            if isinstance(inner, list):
                counts["cards"] = len(inner)

    real_errors = [e for e in errors if e.get("severity", "error") == "error"]
    return {
        "ok": not real_errors,
        "errors": real_errors,
        "warnings": warnings,
        "parsed_summary": {
            "scope": scope,
            "views": counts["views"],
            "sections": counts["sections"],
            "cards": counts["cards"],
            "custom_cards": sorted(custom_cards),
        },
    }
