"""Tests for Jinja-aware entity reference extraction."""

from __future__ import annotations

from ha_ops_mcp.refindex.jinja_walk import walk_jinja_for_refs


def _refs(text: str) -> list[str]:
    return [r.entity_id for r in walk_jinja_for_refs(text)]


def test_empty_string_returns_nothing():
    assert _refs("") == []


def test_plain_string_no_template_markers():
    assert _refs("hello world") == []


def test_states_function_single_quotes():
    assert _refs("{{ states('sensor.temperature') }}") == ["sensor.temperature"]


def test_states_function_double_quotes():
    assert _refs('{{ states("light.living_room") }}') == ["light.living_room"]


def test_state_attr_extracted():
    out = _refs("{{ state_attr('sensor.temp', 'unit_of_measurement') }}")
    assert out == ["sensor.temp"]


def test_is_state_extracted():
    out = _refs("{% if is_state('light.x', 'on') %}yes{% endif %}")
    assert out == ["light.x"]


def test_attribute_access_form():
    out = _refs("{{ states.sensor.temperature.state }}")
    assert out == ["sensor.temperature"]


def test_attribute_access_unknown_domain_ignored():
    """states.get(...) should not match (get isn't a known domain)."""
    out = _refs("{{ states.get('sensor.x') }}")
    # `get` is NOT in _KNOWN_DOMAINS, so the attribute form skips it. The
    # function form (states('x')) would catch `states.get(...)` incorrectly,
    # but that's not what we're testing — `states.get(` isn't `states('`.
    assert out == []


def test_multiple_refs_in_one_template():
    text = """
    {% if is_state('light.a', 'on') and states('sensor.b')|float > 20 %}
      {{ state_attr('climate.c', 'temperature') }}
    {% endif %}
    """
    out = _refs(text)
    assert set(out) == {"light.a", "sensor.b", "climate.c"}


def test_ignores_refs_outside_jinja_blocks():
    """Raw prose mentioning sensor.x shouldn't be extracted — only inside {{ }} / {% %}."""
    text = "This comment mentions states.sensor.fake but not in a template."
    assert _refs(text) == []


def test_expand_function_extracted():
    out = _refs("{{ expand('group.downstairs') | map(attribute='name') | list }}")
    assert out == ["group.downstairs"]


def test_case_insensitive_function_name():
    # Unusual but valid Jinja — function names are case-sensitive in Jinja,
    # but HA lowercases, so we accept mixed-case defensively.
    out = _refs("{{ STATES('sensor.x') }}")
    assert out == ["sensor.x"]


def test_templated_entity_id_not_extracted():
    """We can't statically resolve states(var) — that's expected."""
    out = _refs("{{ states(my_variable) }}")
    assert out == []


def test_lowercases_extracted_ids():
    out = _refs("{{ states('Sensor.Temperature') }}")
    assert out == ["sensor.temperature"]
