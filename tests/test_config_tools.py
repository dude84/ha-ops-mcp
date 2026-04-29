"""Tests for configuration tools."""

from __future__ import annotations

import pytest

from ha_ops_mcp.tools.config import (
    haops_config_apply,
    haops_config_create,
    haops_config_patch,
    haops_config_read,
)
from ha_ops_mcp.utils.diff import PatchApplyError, apply_patch, unified_diff


@pytest.mark.asyncio
async def test_config_read(ctx):
    result = await haops_config_read(ctx, path="configuration.yaml")
    assert "homeassistant" in result["content"]
    assert result["size_bytes"] > 0


@pytest.mark.asyncio
async def test_config_read_secrets_redacted(ctx):
    result = await haops_config_read(ctx, path="secrets.yaml", redact_secrets=True)
    assert "<REDACTED>" in result["content"]
    assert "supersecret123" not in result["content"]


@pytest.mark.asyncio
async def test_config_read_secrets_unredacted(ctx):
    result = await haops_config_read(ctx, path="secrets.yaml", redact_secrets=False)
    assert "supersecret123" in result["content"]


@pytest.mark.asyncio
async def test_config_read_nonexistent(ctx):
    result = await haops_config_read(ctx, path="nonexistent.yaml")
    assert "error" in result


@pytest.mark.asyncio
async def test_config_read_path_traversal(ctx):
    from ha_ops_mcp.safety.path_guard import PathTraversalError

    with pytest.raises(PathTraversalError):
        await haops_config_read(ctx, path="../../etc/passwd")


@pytest.mark.asyncio
async def test_config_create(ctx):
    content = "light:\n  - platform: template\n    lights: {}\n"
    result = await haops_config_create(
        ctx, path="new_lights.yaml", content=content
    )
    assert "token" in result
    assert "diff" in result
    # All-added diff — every non-header line prefixed with +
    diff_lines = [
        ln for ln in result["diff"].splitlines()
        if ln and not ln.startswith(("+++", "---", "@@"))
    ]
    assert all(ln.startswith("+") for ln in diff_lines)

    apply_result = await haops_config_apply(ctx, token=result["token"])
    assert apply_result["success"] is True
    # No backup_path because there was no prior file to back up
    assert apply_result.get("backup_path") is None

    # ruamel's emitter can re-indent on write, so compare parsed structure
    # rather than byte-identical text.
    from io import StringIO

    from ruamel.yaml import YAML
    yaml = YAML()
    written_path = ctx.path_guard.config_root / "new_lights.yaml"
    assert written_path.is_file()
    assert yaml.load(written_path.read_text()) == yaml.load(StringIO(content))


@pytest.mark.asyncio
async def test_config_create_rejects_existing_file(ctx):
    result = await haops_config_create(
        ctx, path="configuration.yaml", content="key: value\n"
    )
    assert "error" in result
    assert "already exists" in result["error"]


@pytest.mark.asyncio
async def test_config_create_invalid_yaml(ctx):
    result = await haops_config_create(
        ctx, path="broken.yaml", content="key: [unterminated"
    )
    assert "error" in result
    assert "YAML" in result["error"]


@pytest.mark.asyncio
async def test_config_apply(ctx):
    """End-to-end apply via config_patch → config_apply."""
    original = (ctx.path_guard.config_root / "configuration.yaml").read_text()
    target = original.replace("name: Test Home", "name: Applied Name")
    patch = unified_diff(original, target, "configuration.yaml")

    patch_result = await haops_config_patch(
        ctx, path="configuration.yaml", patch=patch
    )
    assert "token" in patch_result

    apply_result = await haops_config_apply(ctx, token=patch_result["token"])
    assert apply_result["success"] is True
    assert apply_result.get("backup_path") is not None
    assert apply_result.get("transaction_id") is not None

    content = (ctx.path_guard.config_root / "configuration.yaml").read_text()
    assert "Applied Name" in content


@pytest.mark.asyncio
async def test_config_apply_invalid_token(ctx):
    result = await haops_config_apply(ctx, token="bogus_token")
    assert "error" in result


# ── apply_patch helper (utils/diff.py) ──


def test_apply_patch_happy_path():
    """A valid unified diff applies and produces the expected result."""
    original = "alpha\nbeta\ngamma\n"
    target = "alpha\nBETA\ngamma\ndelta\n"
    patch = unified_diff(original, target, filename="file.txt")
    assert apply_patch(original, patch) == target


def test_apply_patch_context_mismatch_raises():
    """Stale patch — context doesn't match — raises with specific hunk info."""
    original = "alpha\nbeta\ngamma\n"
    mismatched_original = "alpha\nBETA\ngamma\n"  # what the patch thinks is there
    target = "alpha\nBETA\ngamma\ndelta\n"
    patch = unified_diff(mismatched_original, target, filename="file.txt")
    with pytest.raises(PatchApplyError) as exc:
        apply_patch(original, patch)
    # Error message should mention the mismatch so the caller can regenerate.
    assert "mismatch" in str(exc.value).lower()
    assert "beta" in str(exc.value).lower()  # the actual vs expected line


def test_apply_patch_malformed_body_line_raises():
    """A patch with garbage body prefix is rejected, not silently applied."""
    bad_patch = (
        "--- a/file.txt\n+++ b/file.txt\n"
        "@@ -1,1 +1,1 @@\n"
        "X this line has no valid prefix\n"
    )
    with pytest.raises(PatchApplyError):
        apply_patch("original\n", bad_patch)


def test_apply_patch_empty_patch_raises():
    """Patches must have at least one hunk."""
    with pytest.raises(PatchApplyError):
        apply_patch("content\n", "")


def test_apply_patch_multiple_hunks():
    """Multiple non-overlapping hunks apply in order."""
    original = "\n".join(f"line {i}" for i in range(1, 21)) + "\n"
    target_lines = original.splitlines(keepends=True)
    target_lines[4] = "line 5 MODIFIED\n"
    target_lines[14] = "line 15 MODIFIED\n"
    target = "".join(target_lines)
    patch = unified_diff(original, target, filename="file.txt")
    assert apply_patch(original, patch) == target


def test_apply_patch_tolerates_small_line_drift():
    """Hunk header declares @@ -N but actual anchor is at N+3 — still applies.

    Models the repro from _gaps/session_gaps_2026-04-21.md §1: an LLM
    counting lines against a partial read produces a hunk header that's
    off by 1–3 lines. The context block is still unique, so the patch
    relocates and applies cleanly.
    """
    original = "\n".join(f"line {i}" for i in range(1, 21)) + "\n"
    # Patch targets "line 10" as context, but the header lies: claims line 7.
    drifted_patch = (
        "--- a/file.txt\n+++ b/file.txt\n"
        "@@ -7,3 +7,3 @@\n"
        " line 9\n"
        "-line 10\n"
        "+line 10 MODIFIED\n"
        " line 11\n"
    )
    result = apply_patch(original, drifted_patch)
    assert "line 10 MODIFIED\n" in result
    assert "line 10\n" not in result  # replaced, not duplicated


def test_apply_patch_rejects_ambiguous_anchor():
    """If the hunk's context matches multiple candidates within ±fuzz, fail.

    Duplicate anchors mean we cannot unambiguously relocate — the caller
    must regenerate against the current file state.
    """
    # Two identical "line a / line b / line c" blocks, one at offset 1 and
    # one at offset 7, with the declared start (4) right between them. Both
    # are exactly ±3 lines from the declared start.
    original = (
        "header\n"
        "line a\nline b\nline c\n"
        "middle\nmiddle\nmiddle\n"
        "line a\nline b\nline c\n"
        "tail\n"
    )
    patch = (
        "--- a/file.txt\n+++ b/file.txt\n"
        "@@ -5,3 +5,3 @@\n"
        " line a\n"
        "-line b\n"
        "+line b EDITED\n"
        " line c\n"
    )
    with pytest.raises(PatchApplyError) as exc:
        apply_patch(original, patch)
    assert "ambiguous" in str(exc.value).lower()


def test_apply_patch_strict_mode_disables_fuzz():
    """fuzz=0 reinstates the old strict behaviour — any drift is a hard fail."""
    original = "\n".join(f"line {i}" for i in range(1, 21)) + "\n"
    drifted_patch = (
        "--- a/file.txt\n+++ b/file.txt\n"
        "@@ -7,3 +7,3 @@\n"
        " line 9\n"
        "-line 10\n"
        "+line 10 MODIFIED\n"
        " line 11\n"
    )
    with pytest.raises(PatchApplyError):
        apply_patch(original, drifted_patch, fuzz=0)


# ── haops_config_patch tool ──


@pytest.mark.asyncio
async def test_config_patch_happy_path(ctx):
    """A valid patch produces a token whose apply step writes the expected file."""
    original = (ctx.path_guard.config_root / "configuration.yaml").read_text()
    target = original.replace("Test Home", "Patched Home")
    patch = unified_diff(original, target, filename="configuration.yaml")

    result = await haops_config_patch(ctx, path="configuration.yaml", patch=patch)
    assert "token" in result
    assert "diff" in result
    assert "diff_rendered" in result
    assert "Patched Home" in result["diff"]

    apply_result = await haops_config_apply(ctx, token=result["token"])
    assert apply_result["success"] is True

    written = (ctx.path_guard.config_root / "configuration.yaml").read_text()
    assert "Patched Home" in written


@pytest.mark.asyncio
async def test_config_patch_diff_is_unified_with_markers(ctx):
    """Config patch's ``diff`` field carries a real unified diff with
    +/- line markers a chat renderer can colourise. ``diff_rendered``
    wraps the same content in a ```diff fence — that's what the
    controller pastes into chat per the REVIEW PROTOCOL."""
    original = (ctx.path_guard.config_root / "configuration.yaml").read_text()
    target = original.replace("Test Home", "Patched Home")
    patch = unified_diff(original, target, filename="configuration.yaml")

    result = await haops_config_patch(
        ctx, path="configuration.yaml", patch=patch
    )
    diff = result["diff"]
    assert "-  name: Test Home" in diff
    assert "+  name: Patched Home" in diff

    rendered = result["diff_rendered"]
    assert rendered.startswith("```diff\n") and rendered.rstrip().endswith("```")
    assert diff in rendered


@pytest.mark.asyncio
async def test_config_patch_rejects_stale_patch(ctx):
    """A patch against stale content returns a clear error with a re-read hint."""
    # Patch generated against a hypothetical state the file is NOT in.
    patch = (
        "--- a/configuration.yaml\n"
        "+++ b/configuration.yaml\n"
        "@@ -1,3 +1,3 @@\n"
        " homeassistant:\n"
        "-  name: Completely Wrong Name\n"
        "+  name: Replacement\n"
        "   unit_system: metric\n"
    )
    result = await haops_config_patch(
        ctx, path="configuration.yaml", patch=patch
    )
    assert "error" in result
    assert "Patch does not apply" in result["error"]
    assert "hint" in result
    assert "re-read" in result["hint"].lower() or "regenerate" in result["hint"].lower()


@pytest.mark.asyncio
async def test_config_patch_rejects_invalid_yaml_result(ctx):
    """A patch that applies but produces invalid YAML is flagged."""
    original = (ctx.path_guard.config_root / "configuration.yaml").read_text()
    # Patch that inserts a syntactically broken YAML line.
    target = original.replace(
        "homeassistant:\n", "homeassistant:\n  {{broken yaml\n"
    )
    patch = unified_diff(original, target, filename="configuration.yaml")

    result = await haops_config_patch(
        ctx, path="configuration.yaml", patch=patch
    )
    assert "error" in result
    assert "invalid YAML" in result["error"]


@pytest.mark.asyncio
async def test_config_patch_auto_apply(ctx):
    """auto_apply=True previews AND applies in one call."""
    original = (ctx.path_guard.config_root / "configuration.yaml").read_text()
    target = original.replace("name: Test Home", "name: Auto Applied")
    patch = unified_diff(original, target, "configuration.yaml")

    result = await haops_config_patch(
        ctx, path="configuration.yaml", patch=patch, auto_apply=True
    )
    assert result["success"] is True
    assert "diff" in result
    assert "diff_rendered" in result
    assert "transaction_id" in result
    assert "Auto Applied" in (
        ctx.path_guard.config_root / "configuration.yaml"
    ).read_text()


@pytest.mark.asyncio
async def test_config_create_auto_apply(ctx):
    """auto_apply=True on config_create previews AND creates in one call."""
    result = await haops_config_create(
        ctx, path="auto_created.yaml", content="key: value\n",
        auto_apply=True,
    )
    assert result["success"] is True
    assert "diff" in result
    assert (ctx.path_guard.config_root / "auto_created.yaml").is_file()


@pytest.mark.asyncio
async def test_config_patch_auto_apply_false_returns_token(ctx):
    """Default auto_apply=False — unchanged behaviour, returns token."""
    original = (ctx.path_guard.config_root / "configuration.yaml").read_text()
    target = original.replace("name: Test Home", "name: Token Path")
    patch = unified_diff(original, target, "configuration.yaml")

    result = await haops_config_patch(
        ctx, path="configuration.yaml", patch=patch, auto_apply=False
    )
    assert "token" in result
    assert "success" not in result


@pytest.mark.asyncio
async def test_config_patch_rejects_missing_file(ctx):
    """Patching a non-existent file errors with a hint to use config_create."""
    patch = (
        "--- a/new.yaml\n+++ b/new.yaml\n@@ -0,0 +1,1 @@\n+hello\n"
    )
    result = await haops_config_patch(
        ctx, path="does_not_exist.yaml", patch=patch
    )
    assert "error" in result
    assert "haops_config_create" in result["error"]


@pytest.mark.asyncio
async def test_config_patch_path_traversal_blocked(ctx):
    """Path guard rejects traversal attempts just like the other config tools."""
    from ha_ops_mcp.safety.path_guard import PathTraversalError
    with pytest.raises(PathTraversalError):
        await haops_config_patch(
            ctx, path="../../etc/passwd", patch="@@ -1,1 +1,1 @@\n-a\n+b\n"
        )


# ── haops_config_read size cap / chunking (v0.19.0) ────────────────────


@pytest.mark.asyncio
async def test_config_read_small_file_no_truncation(ctx):
    """Files under the cap return full content without truncation flag."""
    result = await haops_config_read(ctx, path="configuration.yaml")
    assert "truncated" not in result
    assert result["size_bytes"] > 0


@pytest.mark.asyncio
async def test_config_read_large_file_truncates_with_metadata(ctx):
    """Over-cap files return truncated content + size metadata + hint."""
    import ha_ops_mcp.tools.config as config_module

    big = ctx.path_guard.config_root / "big.yaml"
    # 200 KB > 128 KB cap.
    big.write_text("key: " + ("x" * (200 * 1024)) + "\n")

    result = await haops_config_read(ctx, path="big.yaml")
    assert result["truncated"] is True
    assert result["cap_bytes"] == config_module.CONFIG_READ_CAP_BYTES
    assert result["size_bytes"] > config_module.CONFIG_READ_CAP_BYTES
    assert len(result["content"]) == config_module.CONFIG_READ_CAP_BYTES
    assert "chunk=" in result["hint"]


@pytest.mark.asyncio
async def test_config_read_chunk_reads_range(ctx):
    """Explicit chunk param returns the requested byte range."""
    big = ctx.path_guard.config_root / "chunked.yaml"
    big.write_text("abcdefghijklmnopqrstuvwxyz\n")

    result = await haops_config_read(
        ctx, path="chunked.yaml", chunk=[5, 10]
    )
    assert result["content"] == "fghij"
    assert result["chunk_start"] == 5
    assert result["chunk_end"] == 10
    assert result["size_bytes"] == 27


@pytest.mark.asyncio
async def test_config_read_chunk_more_hint(ctx):
    """Chunk that ends before EOF surfaces a `more` + hint to paginate."""
    big = ctx.path_guard.config_root / "chunked.yaml"
    big.write_text("abcdefghij" * 100)  # 1000 bytes

    result = await haops_config_read(
        ctx, path="chunked.yaml", chunk=[0, 200]
    )
    assert result["more"] is True
    assert "chunk=[200" in result["hint"]


@pytest.mark.asyncio
async def test_config_read_chunk_clamps_to_eof(ctx):
    big = ctx.path_guard.config_root / "short.yaml"
    big.write_text("tiny\n")

    result = await haops_config_read(
        ctx, path="short.yaml", chunk=[0, 99999]
    )
    assert result["content"] == "tiny\n"
    assert result["chunk_end"] == 5
    assert "more" not in result


@pytest.mark.asyncio
async def test_config_read_lines_range(ctx):
    """lines=[start, end] returns 1-based line range with metadata."""
    target = ctx.path_guard.config_root / "multi.yaml"
    target.write_text("line1\nline2\nline3\nline4\nline5\n")

    result = await haops_config_read(ctx, path="multi.yaml", lines=[2, 4])
    assert result["content"] == "line2\nline3\n"
    assert result["line_start"] == 2
    assert result["line_end"] == 4
    assert result["lines_returned"] == 2
    assert result["total_lines"] == 5
    assert result["more"] is True


@pytest.mark.asyncio
async def test_config_read_lines_clamps_to_eof(ctx):
    target = ctx.path_guard.config_root / "short.yaml"
    target.write_text("a\nb\n")

    result = await haops_config_read(ctx, path="short.yaml", lines=[1, 999])
    assert result["content"] == "a\nb\n"
    assert result["lines_returned"] == 2
    assert "more" not in result


@pytest.mark.asyncio
async def test_config_read_lines_rejects_bad_range(ctx):
    r = await haops_config_read(ctx, path="configuration.yaml", lines=[0, 5])
    assert "error" in r
    r = await haops_config_read(ctx, path="configuration.yaml", lines=[5, 3])
    assert "error" in r


@pytest.mark.asyncio
async def test_config_read_chunk_rejects_bad_shape(ctx):
    r = await haops_config_read(ctx, path="configuration.yaml", chunk=[5])
    assert "error" in r
    r = await haops_config_read(
        ctx, path="configuration.yaml", chunk=[10, 5]
    )
    assert "error" in r


# ── content_from_file / patch_from_file (v0.19.0) ──────────────────────


@pytest.mark.asyncio
async def test_config_create_from_file(ctx):
    """content_from_file reads a staging file under config_root verbatim."""
    staging = ctx.path_guard.config_root / "staging.yaml"
    staging.write_text("light:\n  - platform: template\n    lights: {}\n")

    result = await haops_config_create(
        ctx, path="new_lights.yaml", content_from_file="staging.yaml"
    )
    assert "token" in result
    apply_result = await haops_config_apply(ctx, token=result["token"])
    assert apply_result["success"] is True
    written = ctx.path_guard.config_root / "new_lights.yaml"
    assert written.is_file()


@pytest.mark.asyncio
async def test_config_create_rejects_both_content_and_file(ctx):
    result = await haops_config_create(
        ctx,
        path="x.yaml",
        content="a: 1\n",
        content_from_file="staging.yaml",
    )
    assert "error" in result
    assert "OR content_from_file" in result["error"]


@pytest.mark.asyncio
async def test_config_create_rejects_neither(ctx):
    result = await haops_config_create(ctx, path="x.yaml")
    assert "error" in result
    assert "required" in result["error"]


@pytest.mark.asyncio
async def test_config_create_from_file_missing(ctx):
    result = await haops_config_create(
        ctx, path="x.yaml", content_from_file="no_such_staging.yaml"
    )
    assert "error" in result
    assert "not found" in result["error"]


@pytest.mark.asyncio
async def test_config_create_from_file_rejects_traversal(ctx):
    """PathGuard rejection on the FROM-FILE path — returned as an error
    dict (consistent shape), not a raised exception, so the LLM sees
    the same error payload shape as other validation failures."""
    result = await haops_config_create(
        ctx, path="x.yaml", content_from_file="../../etc/passwd"
    )
    assert "error" in result
    assert "content_from_file path rejected" in result["error"]


@pytest.mark.asyncio
async def test_config_patch_from_file(ctx):
    """patch_from_file reads a staging file containing a unified diff."""
    target = ctx.path_guard.config_root / "configuration.yaml"
    original = target.read_text()
    new_version = original.replace("Test Home", "Patched via File")
    patch_text = unified_diff(original, new_version, "configuration.yaml")

    staging = ctx.path_guard.config_root / "staging.patch"
    staging.write_text(patch_text)

    result = await haops_config_patch(
        ctx, path="configuration.yaml", patch_from_file="staging.patch"
    )
    assert "token" in result
    apply_result = await haops_config_apply(ctx, token=result["token"])
    assert apply_result["success"] is True
    assert "Patched via File" in target.read_text()


@pytest.mark.asyncio
async def test_config_patch_rejects_both_patch_and_file(ctx):
    result = await haops_config_patch(
        ctx,
        path="configuration.yaml",
        patch="@@ -1,1 +1,1 @@\n-a\n+b\n",
        patch_from_file="staging.patch",
    )
    assert "error" in result
    assert "OR patch_from_file" in result["error"]


@pytest.mark.asyncio
async def test_config_patch_rejects_neither(ctx):
    result = await haops_config_patch(ctx, path="configuration.yaml")
    assert "error" in result
    assert "required" in result["error"]
