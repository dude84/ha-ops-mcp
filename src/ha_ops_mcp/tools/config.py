"""Configuration tools — haops_config_read/patch/create/apply/validate/search."""

from __future__ import annotations

import io
import re
from typing import TYPE_CHECKING, Any

from ha_ops_mcp.safety.rollback import UndoEntry, UndoType
from ha_ops_mcp.server import registry
from ha_ops_mcp.utils.diff import (
    PatchApplyError,
    apply_patch,
    render_diff,
    unified_diff,
)
from ha_ops_mcp.utils.yaml import make_yaml, write_yaml

if TYPE_CHECKING:
    from ha_ops_mcp.server import HaOpsContext


# When a unified diff exceeds this size in bytes, haops_config_patch also
# persists it to the audit tool-results dir and returns ``diff_file`` so a
# human reviewer can open the full patch in a proper diff viewer instead of
# squinting at escaped JSON in a tool-call log. Chosen at the size where the
# JSON-escaped string becomes meaningfully painful to scan inline.
LARGE_DIFF_BYTES = 30 * 1024

# haops_config_read caps its inline response body at this many bytes so
# large dashboard files / monolithic configuration.yaml setups don't
# silently blow past the MCP result-size cap on the client side. Over the
# cap, the response carries `truncated: True` plus `size_bytes` +
# `cap_bytes` so the caller knows they got a slice and can use `chunk`
# or `haops_exec_shell` to see the rest.
CONFIG_READ_CAP_BYTES = 128 * 1024


def _canonicalise_yaml(text: str) -> str | None:
    """Round-trip YAML through ruamel's dumper to produce a canonical form.

    Used by haops_config_patch to suppress cosmetic churn: HA re-serialises
    automations.yaml / scripts.yaml after each reload with its own line
    wrapping, so an unmodified-on-purpose description shows up as a wrap-
    point delta on the next diff. Canonicalising both sides through the
    same emitter collapses that noise — only genuinely-different YAML
    survives.

    Returns:
        Canonical YAML text, or ``None`` when the input isn't valid YAML
        (caller falls back to the raw text so invalid files still surface
        their real diff).
    """
    yaml = make_yaml()
    try:
        data = yaml.load(text)
    except Exception:
        return None
    buf = io.StringIO()
    try:
        yaml.dump(data, buf)
    except Exception:
        return None
    return buf.getvalue()


def _load_inline_or_file(
    ctx: HaOpsContext,
    *,
    inline: str | None,
    from_file: str | None,
    inline_name: str,
    file_name: str,
) -> str | dict[str, Any]:
    """Resolve a content/patch value from either an inline string or a
    staging file under config_root. Returns the resolved string, or an
    error dict if both/neither were provided or the file can't be read.

    Used by haops_config_patch (patch / patch_from_file) and
    haops_config_create (content / content_from_file) to let callers
    avoid pasting 50+ KB payloads inline. The staging file lives under
    config_root (PathGuard-enforced); callers stage it via
    haops_exec_shell or similar, then reference by path.
    """
    if inline is None and from_file is None:
        return {
            "error": (
                f"Exactly one of {inline_name} or {file_name} is required."
            ),
        }
    if inline is not None and from_file is not None:
        return {
            "error": (
                f"Provide {inline_name} OR {file_name}, not both."
            ),
        }
    if from_file is not None:
        try:
            src = ctx.path_guard.validate(from_file)
        except Exception as e:
            return {"error": f"{file_name} path rejected: {e}"}
        if not src.is_file():
            return {
                "error": (
                    f"{file_name} not found or not a regular file: "
                    f"{from_file}"
                ),
            }
        try:
            return src.read_text()
        except OSError as e:
            return {"error": f"Could not read {file_name}: {e}"}
    # inline-only path
    assert inline is not None
    return inline


def _redact_secrets(content: str) -> str:
    """Redact values in secrets.yaml, keeping keys visible."""
    lines = []
    for line in content.splitlines():
        # Match "key: value" but not comments
        if re.match(r"^\s*[a-zA-Z_][\w]*\s*:", line) and not line.strip().startswith("#"):
            key_part = line.split(":", 1)[0]
            lines.append(f"{key_part}: <REDACTED>")
        else:
            lines.append(line)
    return "\n".join(lines)


@registry.tool(
    name="haops_config_read",
    description=(
        "Read a configuration file from the Home Assistant config directory. "
        "Path is relative to config root (e.g., 'configuration.yaml', 'automations.yaml', "
        "'esphome/sensor.yaml'). If the file is secrets.yaml, values are redacted by default. "
        "For other YAML files, !secret references are noted but not resolved. "
        "Parameters: path (string, required), redact_secrets (bool, default true), "
        "chunk (list[int, int], optional — [start_byte, end_byte] half-open range), "
        "lines (list[int, int], optional — [start_line, end_line] 1-based, "
        "half-open line range — returns numbered lines so you can build hunk "
        "headers for unified diffs without manual byte counting). "
        "Large files are capped at 128 KB of content by default: if the file "
        "exceeds the cap, the response carries truncated=true, size_bytes, "
        "cap_bytes, and a hint pointing at the `chunk` or `lines` param. "
        "Read-only — does not modify anything."
    ),
    params={
        "path": {"type": "string", "description": "File path relative to config root"},
        "redact_secrets": {
            "type": "boolean", "description": "Redact secret values", "default": True,
        },
        "chunk": {
            "type": "array",
            "description": (
                "Byte range [start, end] to read, half-open. Overrides the "
                "default 128 KB cap — the requested slice is returned verbatim "
                "with a chunk_start / chunk_end echo so the caller can paginate."
            ),
            "items": {"type": "integer"},
        },
        "lines": {
            "type": "array",
            "description": (
                "Line range [start, end] to read, 1-based, half-open. "
                "Returns the selected lines with line numbers. Preferred "
                "over chunk for YAML inspection and patch authoring — no "
                "byte arithmetic needed."
            ),
            "items": {"type": "integer"},
        },
    },
)
async def haops_config_read(
    ctx: HaOpsContext,
    path: str,
    redact_secrets: bool = True,
    chunk: list[int] | None = None,
    lines: list[int] | None = None,
) -> dict[str, Any]:
    resolved = ctx.path_guard.validate(path)

    if not resolved.exists():
        return {"error": f"File not found: {path}"}

    size_bytes = resolved.stat().st_size

    # Chunked read — caller asked for a specific byte range. Trust the
    # range but clamp to the file size to avoid short-read surprises.
    if chunk is not None:
        if (
            not isinstance(chunk, list) or len(chunk) != 2
            or not all(isinstance(x, int) for x in chunk)
        ):
            return {
                "error": (
                    "chunk must be a two-element list of integers "
                    "[start_byte, end_byte]"
                ),
            }
        start, end = chunk
        if start < 0 or end < start:
            return {"error": "chunk range must satisfy 0 <= start <= end"}
        end = min(end, size_bytes)
        with open(resolved, "rb") as f:
            f.seek(start)
            raw = f.read(end - start)
        # Decode as UTF-8; on decode error fall back to replacement so the
        # caller still gets something — the chunk is inherently lossy at
        # byte boundaries for multi-byte sequences.
        content = raw.decode("utf-8", errors="replace")
        if resolved.name == "secrets.yaml" and redact_secrets:
            content = _redact_secrets(content)
        result: dict[str, Any] = {
            "path": str(resolved),
            "content": content,
            "size_bytes": size_bytes,
            "chunk_start": start,
            "chunk_end": end,
        }
        if end < size_bytes:
            result["more"] = True
            result["hint"] = (
                f"More content after byte {end} (size_bytes={size_bytes}). "
                f"Request chunk=[{end}, ...] to continue."
            )
        return result

    # Line-range read — preferred for YAML inspection / patch authoring.
    # Returns content with line numbers so the caller can build hunk
    # headers without manual byte counting.
    if lines is not None:
        if (
            not isinstance(lines, list) or len(lines) != 2
            or not all(isinstance(x, int) for x in lines)
        ):
            return {
                "error": (
                    "lines must be a two-element list of integers "
                    "[start_line, end_line] (1-based, half-open)"
                ),
            }
        start_line, end_line = lines
        if start_line < 1 or end_line < start_line:
            return {
                "error": "lines range must satisfy 1 <= start_line <= end_line"
            }
        all_lines = resolved.read_text().splitlines(keepends=True)
        total_lines = len(all_lines)
        end_line = min(end_line, total_lines + 1)
        selected = all_lines[start_line - 1 : end_line - 1]
        content = "".join(selected)
        if resolved.name == "secrets.yaml" and redact_secrets:
            content = _redact_secrets(content)
        line_result: dict[str, Any] = {
            "path": str(resolved),
            "content": content,
            "size_bytes": size_bytes,
            "total_lines": total_lines,
            "line_start": start_line,
            "line_end": min(end_line, total_lines + 1),
            "lines_returned": len(selected),
        }
        if end_line <= total_lines:
            line_result["more"] = True
            line_result["hint"] = (
                f"More content after line {end_line - 1} "
                f"(total_lines={total_lines}). "
                f"Request lines=[{end_line}, ...] to continue."
            )
        return line_result

    content = resolved.read_text()

    if resolved.name == "secrets.yaml" and redact_secrets:
        content = _redact_secrets(content)

    secret_refs: list[str] = []
    if resolved.suffix in (".yaml", ".yml") and resolved.name != "secrets.yaml":
        secret_refs = re.findall(r"!secret\s+(\S+)", content)

    result = {
        "path": str(resolved),
        "content": content,
        "size_bytes": size_bytes,
    }

    # Default cap — beyond CONFIG_READ_CAP_BYTES the inline content is
    # truncated and the caller gets metadata telling them how to see
    # the rest via chunk.
    if size_bytes > CONFIG_READ_CAP_BYTES:
        result["content"] = content[:CONFIG_READ_CAP_BYTES]
        result["truncated"] = True
        result["cap_bytes"] = CONFIG_READ_CAP_BYTES
        result["hint"] = (
            f"File is {size_bytes:,} bytes; inline content capped at "
            f"{CONFIG_READ_CAP_BYTES:,} bytes. Use "
            f"chunk=[{CONFIG_READ_CAP_BYTES}, {size_bytes}] for the rest, "
            "or haops_exec_shell for a one-shot full read."
        )

    if secret_refs:
        result["secret_references"] = secret_refs

    return result


@registry.tool(
    name="haops_config_patch",
    description=(
        "Preview (and optionally apply) a configuration change expressed as "
        "a UNIFIED DIFF PATCH. Default: returns a diff + confirmation token; "
        "call haops_config_apply with the token to commit. With "
        "auto_apply=true: previews AND applies atomically in one call — "
        "returns diff + success + transaction_id + backup_path, no second "
        "call needed. "
        "For edits against existing files — use haops_config_create for "
        "new files. The request payload IS the patch, so the tool-call "
        "approval modal shows only the changed lines, not the full file. "
        "Parameters: path (string, required), patch (string — a unified "
        "diff against the CURRENT file content), patch_from_file (string "
        "— path under config root to a staging file containing the patch; "
        "use this to avoid pasting a 50+ KB unified diff inline). Exactly "
        "one of patch or patch_from_file is required. "
        "auto_apply (bool, default false — set true to preview+apply in "
        "one call). "
        "Hunk start lines may drift by up to ±5 lines — the tool relocates "
        "each hunk by its context block, so patches authored against a "
        "slightly-stale view still apply when the anchor is unique. "
        "If the context block isn't found within ±5 or matches multiple "
        "positions, the tool returns a context-mismatch error; re-read "
        "and regenerate the patch. "
        "For YAML files, validates syntax of the patched result. "
        "Response fields: diff (unified diff of old vs. patched content), "
        "diff_rendered (markdown-fenced for chat-side syntax highlighting), "
        "token, message, and diff_file for large diffs. "
        "REVIEW PROTOCOL — TWO non-negotiable parts: "
        "(1) RENDER, ALWAYS (auto_apply=false). After preview returns "
        "you MUST paste `diff_rendered` verbatim (entire markdown "
        "```diff fenced block, not a paraphrase) as your next chat "
        "message. The chat surface colourises +/- lines — this is the "
        "ONLY visual review the human gets, because Claude Code's tool-"
        "result panel only shows escaped JSON. Render every time, even "
        "for one-line edits, even when the user pre-approved. "
        "(2) STOP for approval. After rendering, wait for explicit user "
        "approval before calling haops_config_apply. EXCEPTION applies "
        "ONLY to the stop, NEVER to the render: if the user already "
        "explicitly approved this specific change in the current turn, "
        "you may chain to apply in the same turn — but the render "
        "still happens first. "
        "Example: path='automations.yaml', patch='--- a/automations.yaml\\n"
        "+++ b/automations.yaml\\n@@ -10,3 +10,4 @@\\n id: foo\\n "
        "alias: Foo\\n+ description: new\\n mode: single'"
    ),
    params={
        "path": {"type": "string", "description": "File path relative to config root"},
        "patch": {
            "type": "string",
            "description": "Unified diff against the current file content",
        },
        "patch_from_file": {
            "type": "string",
            "description": (
                "Alternative to patch — path under config root to a "
                "staging file containing the unified diff"
            ),
        },
        "auto_apply": {
            "type": "boolean",
            "description": "Preview AND apply atomically in one call",
            "default": False,
        },
    },
)
async def haops_config_patch(
    ctx: HaOpsContext,
    path: str,
    patch: str | None = None,
    patch_from_file: str | None = None,
    auto_apply: bool = False,
) -> dict[str, Any]:
    resolved = ctx.path_guard.validate(path)

    patch_body = _load_inline_or_file(
        ctx, inline=patch, from_file=patch_from_file,
        inline_name="patch", file_name="patch_from_file",
    )
    if isinstance(patch_body, dict):
        return patch_body  # error dict
    patch = patch_body

    if not resolved.exists():
        return {
            "error": f"File not found: {path}. "
            "haops_config_patch applies a diff against existing content — "
            "use haops_config_create to make a new file."
        }

    old_content = resolved.read_text()

    # Apply the patch. ``apply_patch`` tolerates ±5-line drift in hunk
    # headers (see utils/diff.py docstring) so patches authored against a
    # slightly-stale view still apply when the context block is uniquely
    # identifiable. Genuinely wrong context (not found within ±5, or
    # ambiguous) still raises and is surfaced to the caller with re-read
    # guidance.
    try:
        new_content = apply_patch(old_content, patch)
    except PatchApplyError as e:
        return {
            "error": "Patch does not apply cleanly",
            "details": str(e),
            "hint": (
                "Re-read the file with haops_config_read, regenerate the "
                "unified diff against the current content, and retry."
            ),
        }

    # Tail of the flow: YAML validation, canonicalise-before-diff, token
    # creation, large-diff file escape.
    yaml_error: str | None = None
    is_yaml = resolved.suffix in (".yaml", ".yml")
    if is_yaml:
        from io import StringIO

        from ruamel.yaml import YAML
        yaml = YAML()
        try:
            yaml.load(StringIO(new_content))
        except Exception as e:
            yaml_error = str(e)

    diff_old = old_content
    diff_new = new_content
    if is_yaml and yaml_error is None:
        canon_old = _canonicalise_yaml(old_content)
        canon_new = _canonicalise_yaml(new_content)
        if canon_old is not None and canon_new is not None:
            diff_old = canon_old
            diff_new = canon_new

    diff = unified_diff(diff_old, diff_new, path)

    if yaml_error:
        response: dict[str, Any] = {
            "error": "Patch applied but produced invalid YAML",
            "details": yaml_error,
            "diff": diff,
        }
        if diff:
            response["diff_rendered"] = render_diff(diff)
        return response

    if not diff:
        return {"message": "Patch is a no-op (produced identical content)", "diff": ""}

    if auto_apply:
        # Atomic preview+apply: create token → immediately apply → merge
        # the diff into the apply response. Same code path, same audit
        # shape, same rollback transaction — just no second tool call.
        token = ctx.safety.create_token(
            action="config_apply",
            details={
                "path": str(resolved),
                "new_content": new_content,
                "old_content": old_content,
            },
        )
        apply_result = await haops_config_apply(ctx, token=token.id)
        return {
            "diff": diff,
            "diff_rendered": render_diff(diff),
            **apply_result,
        }

    # Default: preview-only — return diff + token for a separate apply call.
    token = ctx.safety.create_token(
        action="config_apply",
        details={
            "path": str(resolved),
            "new_content": new_content,
            "old_content": old_content,
        },
    )

    response = {
        "diff": diff,
        "diff_rendered": render_diff(diff),
        "token": token.id,
        "message": "Review the diff above. Call haops_config_apply with this token to apply.",
    }

    if len(diff) > LARGE_DIFF_BYTES:
        diff_path = ctx.audit.tool_results_dir() / f"diff-{token.id[:12]}.patch"
        try:
            diff_path.write_text(diff)
            response["diff_file"] = str(diff_path)
            response["message"] = (
                f"Review the diff above (full patch — {len(diff):,} bytes — "
                f"also saved to {diff_path}). "
                "Call haops_config_apply with this token to apply."
            )
        except OSError:
            pass

    return response


@registry.tool(
    name="haops_config_create",
    description=(
        "Create a NEW configuration file that does not yet exist. "
        "Default: returns a unified diff (all lines as additions) + a "
        "confirmation token; call haops_config_apply with the token to "
        "write. With auto_apply=true: previews AND creates atomically "
        "in one call — returns diff + success + transaction_id. "
        "Use this instead of haops_config_patch when the target file "
        "doesn't exist yet — patch mode requires existing content to diff "
        "against. "
        "Parameters: path (string, required — path relative to config root), "
        "content (string — full file content as an inline string), "
        "content_from_file (string — path under config root to a staging "
        "file whose content will be used; lets you avoid pasting a 50+ KB "
        "file inline). Exactly one of content or content_from_file is "
        "required. auto_apply (bool, default false). "
        "Rejects if the target path already exists; use haops_config_patch "
        "to edit existing files. For YAML files, validates syntax before "
        "returning the token. "
        "REVIEW PROTOCOL — TWO non-negotiable parts: "
        "(1) RENDER, ALWAYS (auto_apply=false). After preview returns "
        "you MUST paste `diff_rendered` verbatim (entire markdown "
        "```diff fenced block) as your next chat message. The chat "
        "surface colourises the additions — this is the ONLY visual "
        "review the human gets of the proposed file contents. Render "
        "every time, even for tiny new files, even when pre-approved. "
        "(2) STOP for approval. After rendering, wait for explicit user "
        "approval before calling haops_config_apply. EXCEPTION applies "
        "ONLY to the stop, NEVER to the render: if the user already "
        "explicitly approved this specific file in the current turn, "
        "you may chain to apply — but the render still happens first."
    ),
    params={
        "path": {"type": "string", "description": "Path for the new file, relative to config root"},
        "content": {"type": "string", "description": "Full content of the new file (inline)"},
        "content_from_file": {
            "type": "string",
            "description": (
                "Alternative to content — path under config root to a "
                "staging file whose content will be used verbatim"
            ),
        },
        "auto_apply": {
            "type": "boolean",
            "description": "Preview AND create atomically in one call",
            "default": False,
        },
    },
)
async def haops_config_create(
    ctx: HaOpsContext,
    path: str,
    content: str | None = None,
    content_from_file: str | None = None,
    auto_apply: bool = False,
) -> dict[str, Any]:
    resolved = ctx.path_guard.validate(path)

    content_body = _load_inline_or_file(
        ctx, inline=content, from_file=content_from_file,
        inline_name="content", file_name="content_from_file",
    )
    if isinstance(content_body, dict):
        return content_body  # error dict
    content = content_body

    if resolved.exists():
        return {
            "error": f"File already exists: {path}. "
            "haops_config_create is for new files only — "
            "use haops_config_patch to edit existing files."
        }

    # YAML syntax validation — same shape as config_diff / config_patch so
    # the LLM sees consistent error payloads across the three tools.
    is_yaml = resolved.suffix in (".yaml", ".yml")
    if is_yaml:
        from io import StringIO

        from ruamel.yaml import YAML
        yaml = YAML()
        try:
            yaml.load(StringIO(content))
        except Exception as e:
            return {
                "error": "Invalid YAML syntax",
                "details": str(e),
            }

    # All-added diff: unified_diff against empty old yields one hunk with
    # every line prefixed `+`. Reviewers see the full proposed content
    # exactly the same way they'd see a patch against an existing file.
    diff = unified_diff("", content, path)

    token = ctx.safety.create_token(
        action="config_apply",
        details={
            "path": str(resolved),
            "new_content": content,
            "old_content": "",
        },
    )

    if auto_apply:
        apply_result = await haops_config_apply(ctx, token=token.id)
        return {
            "diff": diff,
            "diff_rendered": render_diff(diff),
            **apply_result,
        }

    return {
        "diff": diff,
        "diff_rendered": render_diff(diff),
        "token": token.id,
        "message": (
            f"Review the proposed new file ({len(content):,} bytes). "
            "Call haops_config_apply with this token to create it."
        ),
    }


@registry.tool(
    name="haops_config_apply",
    description=(
        "Apply a previously previewed config change. Requires a confirmation token from "
        "haops_config_patch or haops_config_create. Creates an in-memory "
        "rollback savepoint by default. "
        "Parameters: token (string, required). "
        "This is a MUTATING operation — it writes to the filesystem."
    ),
    params={
        "token": {
            "type": "string",
            "description": "Confirmation token from haops_config_patch or haops_config_create",
        },
    },
)
async def haops_config_apply(ctx: HaOpsContext, token: str) -> dict[str, Any]:
    from pathlib import Path

    # Validate and consume token
    try:
        token_data = ctx.safety.validate_token(token)
    except Exception as e:
        return {"error": str(e)}

    details = token_data.details
    resolved = Path(details["path"])
    new_content: str = details["new_content"]
    old_content: str = details["old_content"]

    # Start rollback transaction and record savepoint
    txn = ctx.rollback.begin("config_apply")
    txn.savepoint(
        name=f"write:{resolved.name}",
        undo=UndoEntry(
            type=UndoType.FILE,
            description=f"Revert {resolved.name} to previous content",
            data={"path": str(resolved), "content": old_content},
        ),
    )

    # Persistent backup for config files (these are important)
    backup_path: str | None = None
    if ctx.config.safety.backup_on_write and resolved.exists():
        entry = await ctx.backup.backup_file(resolved, operation="config_apply")
        backup_path = entry.backup_path

    # Write the file
    if resolved.suffix in (".yaml", ".yml"):
        from io import StringIO

        from ruamel.yaml import YAML
        yaml = YAML()
        yaml.preserve_quotes = True
        data = yaml.load(StringIO(new_content))
        write_yaml(resolved, data, yaml)
    else:
        resolved.write_text(new_content)

    ctx.safety.consume_token(token)
    ctx.rollback.commit(txn.id)

    # Store old+new content so the Timeline UI can recompute the diff
    # post-hoc. Without this, _recompute_audit_diff (ui/routes.py) has no
    # data to work with and Timeline entries show only "Wrote <path>".
    # transaction_id lets the Timeline Revert button locate the in-session
    # RollbackManager txn and fire haops_rollback against it.
    await ctx.audit.log(
        tool="config_apply",
        details={
            "path": str(resolved),
            "old_content": old_content,
            "new_content": new_content,
            "transaction_id": txn.id,
        },
        backup_path=backup_path,
        token_id=token,
    )

    result: dict[str, Any] = {
        "success": True,
        "path": str(resolved),
        "transaction_id": txn.id,
        "message": f"Config written to {resolved.name}",
    }
    if backup_path:
        result["backup_path"] = backup_path

    return result


@registry.tool(
    name="haops_config_validate",
    description=(
        "Run Home Assistant's config check without making changes. "
        "Calls the homeassistant.check_config service via REST. "
        "Returns valid/invalid with error details. Read-only, no parameters."
    ),
)
async def haops_config_validate(ctx: HaOpsContext) -> dict[str, Any]:
    from ha_ops_mcp.connections.rest import RestClientError

    # POST to the service endpoint with return_response to get the result
    try:
        result = await ctx.rest.post(
            "/api/services/homeassistant/check_config?return_response",
        )
    except RestClientError as e:
        # Fall back to WS if REST doesn't support return_response on this service
        try:
            from ha_ops_mcp.connections.websocket import WebSocketError
            ws_result = await ctx.ws.send_command(
                "call_service",
                domain="homeassistant",
                service="check_config",
                return_response=True,
            )
            if isinstance(ws_result, dict):
                response = ws_result.get("response", {})
                return _format_check_result(response)
        except WebSocketError:
            pass
        return {"error": f"Config check failed: {e}"}

    # REST with return_response returns {"service_response": {...}, "changed_states": [...]}
    if isinstance(result, dict):
        service_response = result.get("service_response", result)
        return _format_check_result(service_response)

    return {"valid": True, "result": str(result)}


def _format_check_result(response: dict[str, Any]) -> dict[str, Any]:
    """Format the check_config service response into valid/invalid output."""
    errors = response.get("errors")
    warnings = response.get("warnings")
    if errors:
        return {"valid": False, "errors": errors, "warnings": warnings}
    result: dict[str, Any] = {"valid": True}
    if warnings:
        result["warnings"] = warnings
    return result


@registry.tool(
    name="haops_config_search",
    description=(
        "Search across config files and (optionally) HA's .storage registries. "
        "Default: recursive scan of **/*.yaml and **/*.yml under config root "
        "(covers scripts/, packages/, esphome/, automations/, dashboards/, etc.), "
        "excluding hidden directories (.storage, .cloud, .git, ...). "
        "Set include_registries=true to also scan .storage/core.* JSON files "
        "(device_registry, entity_registry, area_registry, config_entries) — "
        "the ground truth for HA's registries. "
        "Parameters: pattern (string, required — regex or plain text, "
        "case-insensitive), paths (list of glob patterns — overrides defaults), "
        "include_registries (bool, default false), "
        "max_results (int, default 100). "
        "Returns matches with file path, line number, and content."
    ),
    params={
        "pattern": {
            "type": "string",
            "description": "Regex or string to search for",
        },
        "paths": {
            "type": "array",
            "description": "Glob patterns to search (overrides defaults)",
        },
        "include_registries": {
            "type": "boolean",
            "description": "Also scan .storage/core.* JSON registries",
            "default": False,
        },
        "max_results": {
            "type": "integer",
            "description": "Max matches to return",
            "default": 100,
        },
    },
)
async def haops_config_search(
    ctx: HaOpsContext,
    pattern: str,
    paths: list[str] | None = None,
    include_registries: bool = False,
    max_results: int = 100,
) -> dict[str, Any]:
    import re
    from pathlib import Path

    if paths is None:
        # Recursive scan — covers scripts/, packages/, esphome/, automations/,
        # dashboards/, and any other nested YAML. Hidden dirs (.storage, .cloud,
        # .git) are skipped below unless include_registries is set.
        paths = ["**/*.yaml", "**/*.yml"]
        if include_registries:
            paths = paths + [".storage/core.*"]

    # If caller explicitly included .storage in paths, allow it even with
    # include_registries=False
    allow_dotfiles = include_registries or any(
        ".storage" in p or p.startswith(".") for p in paths
    )

    try:
        compiled = re.compile(pattern, re.I)
    except re.error as e:
        return {"error": f"Invalid regex pattern: {e}"}

    config_root = Path(ctx.config.filesystem.config_root)
    matches: list[dict[str, Any]] = []

    for glob_pattern in paths:
        for filepath in sorted(config_root.glob(glob_pattern)):
            if not filepath.is_file():
                continue
            rel = filepath.relative_to(config_root)
            parts = rel.parts
            # Skip hidden/dot dirs unless registries were requested
            if not allow_dotfiles and any(p.startswith(".") for p in parts):
                continue

            try:
                ctx.path_guard.validate(filepath)
            except Exception:
                continue

            try:
                lines = filepath.read_text().splitlines()
            except Exception:
                continue

            for line_num, line in enumerate(lines, 1):
                if compiled.search(line):
                    matches.append({
                        "file": str(rel),
                        "line": line_num,
                        "content": line.rstrip(),
                    })
                    if len(matches) >= max_results:
                        return {
                            "matches": matches,
                            "count": len(matches),
                            "truncated": True,
                        }

    return {"matches": matches, "count": len(matches)}
