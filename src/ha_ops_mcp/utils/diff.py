"""Diff utilities for config and dashboard comparison."""

from __future__ import annotations

import difflib
import re
from typing import Any

from deepdiff import DeepDiff


class PatchApplyError(Exception):
    """Raised when a unified diff patch cannot be applied cleanly.

    The exception message names the offending hunk and the expected-vs-actual
    line content, so the caller (typically an LLM producing a fresh patch)
    can regenerate against the current file state instead of retrying blind.
    """


def unified_diff(old_text: str, new_text: str, filename: str = "file") -> str:
    """Generate a unified diff between two text strings.

    Returns:
        Unified diff string, or empty string if no changes.
    """
    # Strip a leading slash so a JSON Pointer like "/views/0/cards/2" doesn't
    # produce the cosmetic "a//views/0/cards/2" header. ``apply_patch`` skips
    # the file-header lines, so changing them here doesn't affect parsing.
    label = filename.lstrip("/") or "file"
    old_lines = old_text.splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)
    diff = difflib.unified_diff(
        old_lines, new_lines,
        fromfile=f"a/{label}",
        tofile=f"b/{label}",
    )
    return "".join(diff)


_HUNK_HEADER = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))?"
    r" \+(?P<new_start>\d+)(?:,(?P<new_count>\d+))?"
    r" @@"
)


def apply_patch(original: str, patch: str, fuzz: int = 5) -> str:
    """Apply a unified diff to ``original`` text and return the patched text.

    Context-and-removal lines must match the original exactly, but the hunk
    header's declared start line is allowed to drift by up to ``fuzz`` lines
    in either direction. This mirrors GNU ``patch``'s ``-F`` fuzz factor: a
    hunk authored against a near-current view still applies if the context
    block is uniquely identifiable within ±fuzz of the declared start.

    Ambiguity or absence still fails: if the context block isn't found
    within ±fuzz, or matches multiple positions, ``PatchApplyError`` is
    raised with expected-vs-actual detail so the caller can re-read and
    regenerate.

    Requires a well-formed unified diff where every body line ends with
    ``\\n``. Patches produced by ``git diff`` and our own :func:`unified_diff`
    on content that ends with a trailing newline satisfy this. Patches
    produced by Python's raw ``difflib.unified_diff`` on inputs that lack
    a trailing newline do NOT — difflib concatenates ``-foo`` + ``+foo``
    into a single ambiguous ``-foo+foo`` line. Use ``git diff`` or ensure
    your inputs end with ``\\n`` before diffing.

    Recognises the standard unified diff shape emitted by
    :func:`unified_diff` / ``difflib.unified_diff`` / ``git diff``:

    - Optional ``--- a/path`` / ``+++ b/path`` header (skipped; path is
      established by the caller, not the patch).
    - One or more hunks introduced by ``@@ -<old>[,<n>] +<new>[,<n>] @@``.
    - Body lines beginning with ``' '`` (context), ``'-'`` (remove), or
      ``'+'`` (add). ``'\\\\ No newline at end of file'`` sentinels are
      respected and pass through.

    Args:
        original: Current file content.
        patch: Unified diff to apply.
        fuzz: Maximum line offset to search around each hunk's declared
            start. 0 disables drift tolerance (strict mode). Default 5
            catches the off-by-1-to-3 drift common when an LLM counts
            lines against a partial read, without giving up the
            uniqueness guarantee that makes stale-patch errors diagnostic.

    Returns:
        Patched file content. A newline policy matching ``original`` is
        preserved — we splitlines(keepends=True) so trailing-newline presence
        round-trips naturally.

    Raises:
        PatchApplyError: Malformed patch, context mismatch that can't be
            resolved within ±fuzz, removal that doesn't match, or line
            numbers that go backwards / overlap.
    """
    hunks = _parse_hunks(patch)
    if not hunks:
        raise PatchApplyError(
            "No hunks found in patch. A unified diff must contain at least one "
            "'@@ -x,y +x,y @@' hunk header."
        )

    original_lines = original.splitlines(keepends=True)
    result: list[str] = []
    cursor = 0  # 0-based index into original_lines

    for idx, hunk in enumerate(hunks, start=1):
        # Hunk old_start is 1-based; 0 means "at beginning of file" for pure
        # additions to empty files.
        declared_start = max(hunk.old_start - 1, 0)
        hunk_start = _locate_hunk(
            hunk, original_lines, declared_start, cursor, fuzz, idx
        )

        if hunk_start < cursor:
            raise PatchApplyError(
                f"Hunk #{idx} starts at line {hunk.old_start}, but previous "
                f"hunks already consumed up to line {cursor}. Hunks must be "
                "in ascending, non-overlapping order."
            )

        # Copy unchanged lines before this hunk.
        while cursor < hunk_start:
            if cursor >= len(original_lines):
                raise PatchApplyError(
                    f"Hunk #{idx} claims to start at line {hunk.old_start}, "
                    f"but the file only has {len(original_lines)} lines."
                )
            result.append(original_lines[cursor])
            cursor += 1

        # Apply the hunk body line by line.
        for entry in hunk.entries:
            kind, text = entry
            if kind == " ":  # context — must match
                if cursor >= len(original_lines):
                    raise PatchApplyError(
                        f"Hunk #{idx}: context line past end of file at "
                        f"line {cursor + 1}. Expected: {text!r}."
                    )
                if _line_content(original_lines[cursor]) != _line_content(text):
                    raise PatchApplyError(
                        f"Hunk #{idx} context mismatch at line {cursor + 1}. "
                        f"Expected: {text!r}. Actual: {original_lines[cursor]!r}. "
                        "File has likely changed since the patch was generated — "
                        "re-read and regenerate the diff."
                    )
                result.append(original_lines[cursor])
                cursor += 1
            elif kind == "-":  # removal — must match, do not copy to result
                if cursor >= len(original_lines):
                    raise PatchApplyError(
                        f"Hunk #{idx}: removal past end of file at "
                        f"line {cursor + 1}. Expected to remove: {text!r}."
                    )
                if _line_content(original_lines[cursor]) != _line_content(text):
                    raise PatchApplyError(
                        f"Hunk #{idx} removal mismatch at line {cursor + 1}. "
                        f"Expected to remove: {text!r}. Actual: "
                        f"{original_lines[cursor]!r}."
                    )
                cursor += 1
            elif kind == "+":  # addition — copy to result, do not consume cursor
                result.append(text)
            # Other prefixes (notably '\\ No newline at end of file') are
            # structural sentinels from difflib — we ignore them; the
            # splitlines/keepends round-trip preserves trailing-newline
            # state implicitly.

    # Tail of unchanged lines after the final hunk.
    result.extend(original_lines[cursor:])
    return "".join(result)


def _hunk_expected_sequence(hunk: _Hunk) -> list[str]:
    """Lines the hunk expects to consume from the original, in order.

    Context (' ') and removed ('-') entries both consume a line from the
    original. Additions ('+') do not — they only appear in the output.
    The returned sequence is what must match contiguously in ``original``
    for the hunk to apply.
    """
    return [text for kind, text in hunk.entries if kind in (" ", "-")]


def _sequence_matches_at(
    expected: list[str], original_lines: list[str], offset: int
) -> bool:
    """Return True if ``original_lines[offset:]`` starts with ``expected``."""
    if offset < 0 or offset + len(expected) > len(original_lines):
        return False
    for i, want in enumerate(expected):
        if _line_content(original_lines[offset + i]) != _line_content(want):
            return False
    return True


def _locate_hunk(
    hunk: _Hunk,
    original_lines: list[str],
    declared_start: int,
    cursor: int,
    fuzz: int,
    idx: int,
) -> int:
    """Resolve the actual start offset for a hunk, allowing ±fuzz drift.

    Tries the declared start first (preserves the fast path for well-formed
    patches). If that doesn't match, searches outward by 1..fuzz in both
    directions, preferring the smallest offset. A position is only
    considered if it doesn't back into already-consumed territory
    (``offset >= cursor``).

    Raises:
        PatchApplyError: No match within ±fuzz, or ambiguous match at the
            same distance on both sides (the canonical "I don't know
            which of two candidates you meant" case).
    """
    expected = _hunk_expected_sequence(hunk)

    # Empty hunks (adds-only against empty files, or a header with no
    # removal/context entries) have nothing to match — trust the declared
    # start and let the caller handle it.
    if not expected:
        return declared_start

    if declared_start >= cursor and _sequence_matches_at(
        expected, original_lines, declared_start
    ):
        return declared_start

    # Search outward. For a given distance d>0 we probe -d then +d, so the
    # first hit wins on ties (mimics GNU patch's preference for earlier).
    # We require the match to be unique at its distance bucket — a hit at
    # both -d and +d with no match at smaller distances is ambiguous.
    for d in range(1, fuzz + 1):
        candidates: list[int] = []
        for offset in (declared_start - d, declared_start + d):
            if offset < cursor:
                continue
            if _sequence_matches_at(expected, original_lines, offset):
                candidates.append(offset)
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            raise PatchApplyError(
                f"Hunk #{idx} is ambiguous: its context block matches at "
                f"both line {candidates[0] + 1} and line {candidates[1] + 1} "
                f"(declared {hunk.old_start}, fuzz ±{fuzz}). "
                "File has changed in a way that creates duplicate anchors — "
                "re-read and regenerate the diff against the current state."
            )

    # Fell through without finding anywhere within ±fuzz. Walk the declared
    # position and report the first line that diverges — that's the most
    # diagnostic signal for the caller, who usually wants to know which
    # specific line moved, not just "no match found".
    mismatch_line = declared_start
    expected_line = expected[0]
    for i, want in enumerate(expected):
        pos = declared_start + i
        if pos >= len(original_lines):
            mismatch_line = pos
            expected_line = want
            break
        if _line_content(original_lines[pos]) != _line_content(want):
            mismatch_line = pos
            expected_line = want
            break
    actual = (
        original_lines[mismatch_line] if mismatch_line < len(original_lines)
        else ""
    )
    raise PatchApplyError(
        f"Hunk #{idx} context mismatch at line {mismatch_line + 1}. "
        f"Expected: {expected_line!r}. Actual: {actual!r}. "
        f"Not found within ±{fuzz} lines either — "
        "file has likely changed since the patch was generated; "
        "re-read and regenerate the diff."
    )


def _line_content(line: str) -> str:
    """Strip the trailing newline for comparison purposes.

    Unified diffs can legitimately omit the trailing newline (the
    ``\\ No newline at end of file`` sentinel signals it), and difflib's
    output may or may not include it depending on the source. Comparing
    on content without the trailing \\n avoids false negatives without
    losing information — we only use this for equality checks, not for
    reconstructing output.
    """
    return line[:-1] if line.endswith("\n") else line


def _parse_hunks(patch: str) -> list[_Hunk]:
    """Split a unified diff into structured hunks.

    Skips the optional ``--- a/path`` / ``+++ b/path`` header.
    Lines that aren't part of any hunk (before the first ``@@``) are
    silently discarded. Within a hunk body, each line is classified by
    its first character (space / minus / plus / backslash-sentinel).
    """
    hunks: list[_Hunk] = []
    current: _Hunk | None = None

    for raw_line in patch.splitlines(keepends=True):
        # Strip the keepends for regex matching only — we keep the original
        # line content in the hunk entries so text round-trips cleanly.
        stripped = raw_line.rstrip("\n").rstrip("\r")
        header = _HUNK_HEADER.match(stripped)
        if header:
            current = _Hunk(
                old_start=int(header.group("old_start")),
                old_count=int(header.group("old_count") or 1),
                new_start=int(header.group("new_start")),
                new_count=int(header.group("new_count") or 1),
                entries=[],
            )
            hunks.append(current)
            continue

        if current is None:
            # Pre-hunk preamble (file headers, git header lines). Ignore.
            continue

        if not raw_line:
            continue

        first = raw_line[0]
        if first in (" ", "-", "+"):
            current.entries.append((first, raw_line[1:]))
        elif first == "\\":
            # '\ No newline at end of file' — structural sentinel, skip.
            continue
        else:
            # Unknown prefix. Be strict: this is likely a malformed patch.
            raise PatchApplyError(
                f"Unrecognised line in hunk body: {raw_line!r}. Unified diff "
                "body lines must start with ' ', '-', '+', or '\\\\'."
            )

    return hunks


class _Hunk:
    """Parsed hunk: header range + body entries."""

    __slots__ = ("old_start", "old_count", "new_start", "new_count", "entries")

    def __init__(
        self,
        old_start: int,
        old_count: int,
        new_start: int,
        new_count: int,
        entries: list[tuple[str, str]],
    ) -> None:
        self.old_start = old_start
        self.old_count = old_count
        self.new_start = new_start
        self.new_count = new_count
        self.entries = entries


def yaml_unified_diff(
    old: Any,
    new: Any,
    label: str = "value",
) -> str:
    """Unified diff between two values, serialised as YAML.

    Used by dashboard JSON Patch previews where ``old`` and ``new`` are
    arbitrary JSON-shaped values (a card dict, a list of cards, a scalar)
    rather than text. Serialising to block-style YAML first gives the
    diff stable line boundaries that ``difflib.unified_diff`` and any
    diff-aware renderer (fenced ``diff`` markdown, sidebar JS) can both
    line-mark with ``+``/``-``.

    Returns:
        Unified diff text (with ``+``/``-`` line prefixes), or the empty
        string if the YAML serialisations are identical.
    """
    old_text = _yaml_block(old)
    new_text = _yaml_block(new)
    if old_text == new_text:
        return ""
    return unified_diff(old_text, new_text, filename=label)


def _yaml_block(value: Any) -> str:
    """Render a value as block-style YAML with a trailing newline.

    Trailing newline matters: ``unified_diff`` needs both inputs to end
    with ``\\n`` to avoid emitting the ambiguous ``\\ No newline at end
    of file`` sentinel that some renderers display literally.
    """
    if value is None:
        return "null\n"
    if isinstance(value, str):
        return value if value.endswith("\n") else value + "\n"
    from io import StringIO

    from ruamel.yaml import YAML
    yaml = YAML()
    yaml.default_flow_style = False
    buf = StringIO()
    yaml.dump(value, buf)
    text = buf.getvalue()
    return text if text.endswith("\n") else text + "\n"


def render_diff(diff: str, language: str = "diff") -> str:
    """Wrap a diff (or json-diff summary) in a markdown code fence.

    Gives the human reviewer of a two-phase mutation a syntax-highlighted view
    in MCP clients that render markdown, while the raw ``diff`` field stays
    available for programmatic consumers.

    Args:
        diff: The diff or summary text. Must be non-empty — callers should
            skip rendering when there are no changes to show.
        language: Code-fence language hint. ``diff`` works for both unified
            diffs and JSON-diff summaries (the ``+``/``-`` prefixes still
            colourise).

    Returns:
        Markdown fenced code block.
    """
    return f"```{language}\n{diff}\n```"


def json_diff(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    """Compute a structured diff between two dicts using deepdiff.

    Returns:
        A dict representation of the differences.
    """
    dd = DeepDiff(old, new, ignore_order=False)
    return dict(dd) if dd else {}


def _yaml_value(v: Any) -> str:
    """Render a value as indented YAML if it's a dict/list, else repr.

    Mirrors `_compact_value` in `tools/dashboard.py` — same ruamel call,
    same indent convention — but lives here so `format_json_diff` (used
    by the Timeline) gets the same readability as the preview diff.
    """
    if isinstance(v, (dict, list)):
        from io import StringIO

        from ruamel.yaml import YAML
        yaml = YAML()
        yaml.default_flow_style = False
        buf = StringIO()
        yaml.dump(v, buf)
        rendered = buf.getvalue().rstrip()
        return "\n      " + rendered.replace("\n", "\n      ")
    return repr(v)


def format_json_diff(diff: dict[str, Any]) -> str:
    """Format a deepdiff result as a human-readable summary.

    Non-scalar values (dicts, lists) are rendered as indented YAML
    blocks instead of Python repr — dashboard configs are YAML-native
    in HA and the repr is unreadable for anything beyond trivially
    small values. Works retroactively for old audit entries since
    rendering happens on read, not on write.

    Returns:
        Multi-line string describing the changes.
    """
    if not diff:
        return "No changes."

    lines: list[str] = []

    if "values_changed" in diff:
        lines.append("Changed values:")
        for path, change in diff["values_changed"].items():
            old_v = _yaml_value(change["old_value"])
            new_v = _yaml_value(change["new_value"])
            lines.append(f"  {path}: {old_v} -> {new_v}")

    if "dictionary_item_added" in diff:
        lines.append("Added keys:")
        for item in diff["dictionary_item_added"]:
            lines.append(f"  + {item}")

    if "dictionary_item_removed" in diff:
        lines.append("Removed keys:")
        for item in diff["dictionary_item_removed"]:
            lines.append(f"  - {item}")

    if "iterable_item_added" in diff:
        lines.append("Added items:")
        for path, val in diff["iterable_item_added"].items():
            lines.append(f"  + {path}: {_yaml_value(val)}")

    if "iterable_item_removed" in diff:
        lines.append("Removed items:")
        for path, val in diff["iterable_item_removed"].items():
            lines.append(f"  - {path}: {_yaml_value(val)}")

    if "type_changes" in diff:
        lines.append("Type changes:")
        for path, change in diff["type_changes"].items():
            lines.append(
                f"  {path}: {change['old_type'].__name__} -> {change['new_type'].__name__}"
            )

    return "\n".join(lines) if lines else "No changes."
