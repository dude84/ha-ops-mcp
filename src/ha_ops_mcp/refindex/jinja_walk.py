"""Jinja-aware entity reference extraction.

Layer 2 of the refindex builder. Walks Jinja templates embedded in YAML
string values and pulls out entity references so that templated automations
and sensors register their real dependencies.

Supported patterns:
    states('entity_id')
    states("entity_id")
    state_attr('entity_id', 'attribute')
    is_state('entity_id', 'state')
    is_state_attr('entity_id', 'attribute', 'value')
    states.domain.object_id                (attribute access form)
    states.domain.object_id.state          (same, with trailing .state)
    expand('entity_id_or_group')

We use a pragmatic regex extractor rather than a real Jinja parser. The
goal is "catch 95% of real-world refs reliably"; templates using Jinja
variables to compute entity ids (e.g. `states(var)`) can't be resolved
statically regardless — those are a v0.8+ concern.

Extracted refs always use edge_kind="references" — Jinja templates don't
have a trigger/condition/target distinction at the text level.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass

# Well-known HA domains used for the `states.domain.object_id` attribute form.
# We don't need an exhaustive list — only domains that commonly appear in
# templates. Unknown domains fall through and get caught by states('x') form.
_KNOWN_DOMAINS = frozenset({
    "sensor", "binary_sensor", "light", "switch", "fan", "cover", "lock",
    "climate", "media_player", "input_boolean", "input_number", "input_select",
    "input_text", "input_datetime", "input_button", "counter", "timer", "sun",
    "person", "device_tracker", "zone", "weather", "automation", "script",
    "scene", "group", "camera", "vacuum", "alarm_control_panel", "remote",
    "humidifier", "water_heater", "update", "button", "select", "number",
    "text", "date", "time", "datetime", "event", "schedule", "todo",
    "conversation", "calendar", "notify", "image", "valve", "siren",
    "stt", "tts", "wake_word", "lawn_mower",
})


# Function-call forms: states('x'), state_attr('x', 'y'), is_state('x', 'y'), etc.
# Group 1: the entity id.
_FUNC_RE = re.compile(
    r"""
    \b(?:states|state_attr|is_state|is_state_attr|expand|has_value)
    \s*\(\s*['"]
    ([a-z0-9_]+\.[a-z0-9_]+)
    ['"]
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Attribute-access form: states.domain.object_id (optionally followed by .state
# or other attribute accessors). Only matches when domain is in _KNOWN_DOMAINS
# to avoid picking up `states.get(...)` or similar false positives.
_ATTR_RE = re.compile(
    r"""
    \bstates\.
    ([a-z_][a-z0-9_]*)    # domain
    \.
    ([a-z_][a-z0-9_]*)    # object_id
    """,
    re.IGNORECASE | re.VERBOSE,
)


@dataclass(frozen=True)
class JinjaRef:
    """A single entity reference discovered inside a Jinja template string."""

    entity_id: str
    # Byte offset into the input string (for future "jump to line:col" support).
    offset: int


def walk_jinja_for_refs(text: str) -> Iterator[JinjaRef]:
    """Yield entity refs discovered in `text`.

    Deduplication is the caller's concern — we emit each occurrence at its
    offset so callers can report "3 references to sensor.x in this template".
    """
    if not text or "{" not in text:
        # Fast path: no Jinja markers, no refs. This is the common case —
        # most YAML strings aren't templates.
        return

    # Only scan inside {{ ... }} and {% ... %} blocks. This avoids false
    # positives from YAML-level strings like documentation comments that
    # mention `states.sensor.foo` prose-style.
    for block_match in _iter_jinja_blocks(text):
        block_text = block_match.group(0)
        block_offset = block_match.start()

        for m in _FUNC_RE.finditer(block_text):
            yield JinjaRef(entity_id=m.group(1).lower(), offset=block_offset + m.start(1))

        for m in _ATTR_RE.finditer(block_text):
            domain = m.group(1).lower()
            obj = m.group(2).lower()
            if domain not in _KNOWN_DOMAINS:
                continue
            yield JinjaRef(entity_id=f"{domain}.{obj}", offset=block_offset + m.start(1))


_BLOCK_RE = re.compile(r"\{\{.*?\}\}|\{%.*?%\}", re.DOTALL)


def _iter_jinja_blocks(text: str) -> Iterator[re.Match[str]]:
    yield from _BLOCK_RE.finditer(text)
