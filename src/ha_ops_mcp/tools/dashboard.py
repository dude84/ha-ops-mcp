"""Dashboard tools — haops_dashboard_list, get, diff, apply."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ha_ops_mcp.connections.websocket import WebSocketError
from ha_ops_mcp.safety.rollback import UndoEntry, UndoType
from ha_ops_mcp.server import registry
from ha_ops_mcp.utils.diff import (
    render_diff,
    yaml_unified_diff,
)

if TYPE_CHECKING:
    from ha_ops_mcp.server import HaOpsContext


async def _check_entity_refs(
    ctx: HaOpsContext, config: dict[str, Any]
) -> list[str]:
    """Walk a dashboard config for entity refs and warn about unresolved ones.

    Returns a list of warning strings (empty if everything resolves).
    Warn-only: templated entities (containing {{ }}), input_* helpers,
    and entities that register dynamically won't resolve at preview time.
    """
    from ha_ops_mcp.refindex.dashboard_walk import walk_dashboard_for_refs

    refs = list(walk_dashboard_for_refs(config))
    if not refs:
        return []

    # Build a set of known entity_ids from the registry. Best-effort:
    # use REST /api/states (includes all current entities) rather than
    # the .storage file (which may lag for dynamically-registered ones).
    known: set[str] = set()
    try:
        states = await ctx.rest.get("/api/states")
        if isinstance(states, list):
            known = {s["entity_id"] for s in states if isinstance(s, dict)}
    except Exception:
        # REST unavailable — skip validation rather than block.
        return []

    warnings: list[str] = []
    seen: set[str] = set()
    for ref in refs:
        eid = ref.ref_id
        if eid in seen:
            continue
        seen.add(eid)
        # Skip template expressions — they're dynamic and can't be
        # resolved at preview time.
        if "{{" in eid or "{%" in eid:
            continue
        if eid not in known:
            warnings.append(
                f"Entity '{eid}' (at {ref.path}) not found in current "
                "states — possible typo. Templated or input_* entities "
                "may not resolve until runtime."
            )

    return warnings


def _render_patch_aware_diff(
    patch: list[dict[str, Any]],
    old_config: dict[str, Any],
    new_config: dict[str, Any],
) -> str:
    """Render a JSON Patch as a human-readable unified diff with per-op anchors.

    For each op, emits a one-line mechanical anchor (op + JSON Pointer +
    view/section title lookup + before/after value kind) followed by a
    real ``difflib.unified_diff`` body of the YAML serialisation at the
    patch path. Concatenated with blank-line separators so a markdown
    ``diff`` fence colourises the ``+``/``-`` line markers and a reviewer
    can scan op-by-op.

    Replaces the prior ``key: 'old' -> 'new'`` blob format that had no
    line markers and was therefore unreadable in any diff-aware renderer
    (gap report 2026-04-18 §1).
    """
    sections: list[str] = []
    for op in patch:
        anchor, diff_text = _describe_patch_op(op, old_config, new_config)
        if diff_text:
            sections.append(f"{anchor}\n\n{diff_text.rstrip()}")
        else:
            sections.append(anchor)
    return "\n\n".join(sections) if sections else ""


def _view_label(config: dict[str, Any], view_idx: int) -> str | None:
    """Return ``"<title>"`` (or path) for a view index, ``None`` if unknown."""
    views = config.get("views") if isinstance(config, dict) else None
    if not isinstance(views, list) or view_idx < 0 or view_idx >= len(views):
        return None
    v = views[view_idx]
    if not isinstance(v, dict):
        return None
    return v.get("title") or v.get("path") or f"view {view_idx}"


def _section_label(view: Any, section_idx: int) -> str | None:
    if not isinstance(view, dict):
        return None
    sections = view.get("sections")
    if not isinstance(sections, list) or section_idx >= len(sections):
        return None
    s = sections[section_idx]
    if not isinstance(s, dict):
        return None
    return s.get("title") or s.get("name") or f"section {section_idx}"


def _path_breadcrumb(config: dict[str, Any], pointer: str) -> str | None:
    """Build a "View › Section" breadcrumb from a JSON Pointer, if possible.

    Mechanical lookup only — the breadcrumb is composed from view/section
    titles already in ``config``. No interpretation. Returns ``None`` if the
    path doesn't reach a known view.
    """
    parts = pointer.strip("/").split("/")
    if len(parts) < 2 or parts[0] != "views":
        return None
    try:
        view_idx = int(parts[1])
    except ValueError:
        return None
    view_lbl = _view_label(config, view_idx)
    if view_lbl is None:
        return None
    crumbs = [view_lbl]
    if len(parts) >= 4 and parts[2] == "sections":
        try:
            section_idx = int(parts[3])
        except ValueError:
            return " › ".join(crumbs)
        views = config.get("views", [])
        view = views[view_idx] if view_idx < len(views) else None
        section_lbl = _section_label(view, section_idx)
        if section_lbl:
            crumbs.append(section_lbl)
    return " › ".join(crumbs)


def _kind_at(value: Any) -> str:
    """Return a short, mechanical kind label for a value at a patch path."""
    if isinstance(value, dict):
        t = value.get("type")
        if isinstance(t, str):
            return t
        return "object"
    if isinstance(value, list):
        return f"list[{len(value)}]"
    if value is None:
        return "null"
    return type(value).__name__


def _describe_patch_op(
    op: dict[str, Any],
    old_config: dict[str, Any],
    new_config: dict[str, Any],
) -> tuple[str, str]:
    """Produce (anchor_line, unified_diff_text) for a single JSON Patch op.

    The anchor is mechanical — composed from op kind, JSON Pointer, view/
    section title lookup, and before/after value kind. No prose. The
    unified diff body is computed from a YAML serialisation of the
    before and after values at the patch path so a diff-aware renderer
    can line-mark it with ``+``/``-``.
    """
    op_type = op.get("op", "")
    path = op.get("path", "")
    crumb = _path_breadcrumb(old_config, path)
    crumb_suffix = f" ({crumb})" if crumb else ""

    if op_type == "replace":
        old_val = _resolve_json_pointer(old_config, path)
        new_val = op.get("value")
        anchor = (
            f"**Replace** `{path}`{crumb_suffix}"
            f" — `{_kind_at(old_val)}` → `{_kind_at(new_val)}`"
        )
        return anchor, yaml_unified_diff(old_val, new_val, label=path or "/")

    if op_type == "add":
        new_val = op.get("value")
        anchor = f"**Add** `{path}`{crumb_suffix} — `{_kind_at(new_val)}`"
        return anchor, yaml_unified_diff(None, new_val, label=path or "/")

    if op_type == "remove":
        old_val = _resolve_json_pointer(old_config, path)
        anchor = f"**Remove** `{path}`{crumb_suffix} — was `{_kind_at(old_val)}`"
        return anchor, yaml_unified_diff(old_val, None, label=path or "/")

    if op_type in ("move", "copy"):
        from_path = op.get("from", "")
        anchor = f"**{op_type.capitalize()}** `{from_path}` → `{path}`{crumb_suffix}"
        return anchor, ""

    if op_type == "test":
        anchor = f"**Test** `{path}` == {op.get('value')!r}"
        return anchor, ""

    return f"**{op_type}** `{path}`", ""


def _compact_value(v: Any) -> str:
    """Render a JSON value for the diff output.

    Dicts and lists are dumped as indented YAML (block style) — HA
    configs are YAML-native so this is reviewable at a glance. Scalars
    stay as inline repr (already readable on one line).
    """
    if v is None:
        return "null"
    if isinstance(v, (dict, list)):
        from io import StringIO

        from ruamel.yaml import YAML
        yaml = YAML()
        yaml.default_flow_style = False
        buf = StringIO()
        yaml.dump(v, buf)
        rendered = buf.getvalue().rstrip()
        # Indent under the op line so it reads as a nested block.
        return "\n    " + rendered.replace("\n", "\n    ")
    if isinstance(v, str):
        return repr(v) if len(v) <= 80 else repr(v[:77] + "...")
    return repr(v)


def _resolve_json_pointer(doc: Any, pointer: str) -> Any:
    """Resolve a JSON Pointer (RFC 6901) against a document. Best-effort."""
    if not pointer or pointer == "/":
        return doc
    parts = pointer.strip("/").split("/")
    current = doc
    for part in parts:
        part = part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return current


async def _get_dashboard_config(
    ctx: HaOpsContext, dashboard_id: str
) -> dict[str, Any] | None:
    """Get dashboard config, preferring filesystem, falling back to WS.

    Args:
        dashboard_id: The url_path of the dashboard. Use "lovelace" for default.
    """
    # Tier 1: filesystem (.storage/lovelace.* files)
    # HA sanitises url_path → storage key by replacing `-` with `_`
    # (e.g. url_path "new-dashboard" → file ".storage/lovelace.new_dashboard").
    storage_dir = Path(ctx.config.filesystem.config_root) / ".storage"
    if dashboard_id == "lovelace":
        storage_file = storage_dir / "lovelace"
    else:
        storage_key = dashboard_id.replace("-", "_")
        storage_file = storage_dir / f"lovelace.{storage_key}"

    try:
        content = storage_file.read_text()
        data = json.loads(content)
        return data.get("data", {}).get("config")  # type: ignore[no-any-return]
    except (FileNotFoundError, KeyError, json.JSONDecodeError):
        pass

    # Tier 2: WebSocket
    try:
        kwargs: dict[str, Any] = {}
        if dashboard_id != "lovelace":
            kwargs["url_path"] = dashboard_id
        return await ctx.ws.send_command("lovelace/config", **kwargs)
    except WebSocketError:
        return None


@registry.tool(
    name="haops_dashboard_list",
    description=(
        "List all Lovelace dashboards (storage-mode and YAML-mode). "
        "Returns url_path, title, mode, and icon for each dashboard. "
        "Read-only, no parameters."
    ),
)
async def haops_dashboard_list(ctx: HaOpsContext) -> dict[str, Any]:
    try:
        dashboards = await ctx.ws.send_command("lovelace/dashboards/list")
    except WebSocketError as e:
        return {"error": f"Could not list dashboards: {e}"}

    # The default dashboard isn't in the list — add it
    result = [
        {
            "url_path": "lovelace",
            "title": "Default",
            "mode": "storage",
            "icon": "mdi:view-dashboard",
            "is_default": True,
        }
    ]

    if isinstance(dashboards, list):
        for d in dashboards:
            result.append({
                "url_path": d.get("url_path", ""),
                "title": d.get("title", ""),
                "mode": d.get("mode", "storage"),
                "icon": d.get("icon", ""),
                "is_default": False,
            })

    return {"dashboards": result, "count": len(result)}


@registry.tool(
    name="haops_dashboard_get",
    description=(
        "Get a Lovelace dashboard's config — full, single view, or a "
        "lightweight summary of views. "
        "Prefers filesystem (.storage/lovelace.*), falls back to WebSocket. "
        "Parameters: "
        "dashboard_id (string, required — the url_path, use 'lovelace' for "
        "the default dashboard); "
        "view (int or string, optional — view index, path, or title); "
        "summary (bool, default false — return lightweight view index only: "
        "[{index, title, path, icon, type, section_count, card_count}]). "
        "Use summary=True first to locate the view you want, then fetch "
        "that specific view by index/path/title. Full config is often large "
        "(60+ KB on real dashboards) so summary+targeted-view is the "
        "recommended browsing pattern. "
        "Returns a dict — that's the source of truth. Reserialising it to "
        "YAML for paste-back into HA's raw editor is fragile (HA's editor "
        "rejects format drift); use haops_dashboard_diff/apply instead. "
        "See docs/HA_QUIRKS.md."
    ),
    params={
        "dashboard_id": {
            "type": "string",
            "description": "Dashboard url_path ('lovelace' for default)",
        },
        "view": {
            "type": ["integer", "string"],
            "description": "View index (int) or path/title (string)",
        },
        "summary": {
            "type": "boolean",
            "description": "Return view index summary (no view bodies)",
            "default": False,
        },
    },
)
async def haops_dashboard_get(
    ctx: HaOpsContext,
    dashboard_id: str,
    view: int | str | None = None,
    summary: bool = False,
) -> dict[str, Any]:
    config = await _get_dashboard_config(ctx, dashboard_id)
    if config is None:
        return {"error": f"Dashboard '{dashboard_id}' not found"}

    views = config.get("views", []) if isinstance(config, dict) else []

    # Summary mode — always cheap
    if summary:
        summaries = []
        for idx, v in enumerate(views):
            if not isinstance(v, dict):
                continue
            # View may have either 'cards' (classic) or 'sections' (dashboard).
            sections = v.get("sections") or []
            cards = v.get("cards") or []
            section_count = len(sections) if isinstance(sections, list) else 0
            card_count = len(cards) if isinstance(cards, list) else 0
            if section_count and not card_count:
                card_count = sum(
                    len(s.get("cards", [])) if isinstance(s, dict) else 0
                    for s in sections
                )
            summaries.append({
                "index": idx,
                "title": v.get("title"),
                "path": v.get("path"),
                "icon": v.get("icon"),
                "type": v.get("type"),
                "section_count": section_count,
                "card_count": card_count,
            })
        return {
            "dashboard_id": dashboard_id,
            "views": summaries,
            "view_count": len(summaries),
        }

    # Resolve view param to an index
    if view is not None:
        found: int | None = None
        # bool is a subclass of int in Python — exclude it explicitly
        # so view=True doesn't get treated as index 1.
        if isinstance(view, int) and not isinstance(view, bool):
            found = view
        else:
            q = str(view).strip()
            # MCP clients sometimes coerce ints to strings — accept "3" as
            # an integer index before falling back to path/title lookup.
            if q.isdigit():
                candidate = int(q)
                if 0 <= candidate < len(views):
                    found = candidate
            if found is None:
                # Try path, then title (case-insensitive)
                for i, v in enumerate(views):
                    if isinstance(v, dict) and v.get("path") == q:
                        found = i
                        break
            if found is None:
                ql = q.lower()
                for i, v in enumerate(views):
                    if isinstance(v, dict) and (v.get("title") or "").lower() == ql:
                        found = i
                        break
            if found is None:
                return {
                    "error": (
                        f"No view with index/path/title '{view}'. "
                        f"Dashboard has {len(views)} views (indices 0-{len(views)-1}). "
                        "Call with summary=True to see available views."
                    ),
                }

        if found < 0 or found >= len(views):
            return {
                "error": f"View index {found} out of range "
                f"(dashboard has {len(views)} views)",
            }
        return {
            "dashboard_id": dashboard_id,
            "view_index": found,
            "view": views[found],
        }

    return {"dashboard_id": dashboard_id, "config": config}


@registry.tool(
    name="haops_dashboard_validate_yaml",
    description=(
        "Pre-paste validator for Lovelace YAML. Catches structural errors and "
        "known-card schema violations BEFORE you paste into HA's raw editor "
        "and get a generic 'Configuration error' / 'Cannot read properties of "
        "undefined' dialog. "
        "Specifically catches: YAML parse errors with line numbers, missing "
        "required `type:` on cards, decluttering-card `variables:` map-vs-list "
        "shape (the most common bug), unterminated `[[[ JS ]]]` template "
        "blocks, and field-type mismatches on known cards. "
        "Validates against ha-ops-mcp's bundled card schemas, NOT HA's full "
        "schema. Unknown custom cards emit a `warning` (not error) suggesting "
        "haops_dashboard_resources to verify the module is installed. A passing "
        "result means 'no known structural problem' — not 'guaranteed to render'. "
        "Parameters: yaml_str (string, required — the YAML to validate), "
        "scope (string, default 'dashboard' — one of 'dashboard' (full config "
        "with top-level `views:` list), 'view' (single view body), 'section' "
        "(single section body)). "
        "Returns: {ok: bool, errors: [{line, column, path, field, reason, "
        "severity}], warnings: [...], parsed_summary: {scope, views, sections, "
        "cards, custom_cards}}. Read-only, no token."
    ),
    params={
        "yaml_str": {
            "type": "string",
            "description": "Lovelace YAML to validate",
        },
        "scope": {
            "type": "string",
            "description": "dashboard (default) | view | section",
            "default": "dashboard",
        },
    },
)
async def haops_dashboard_validate_yaml(
    ctx: HaOpsContext,
    yaml_str: str,
    scope: str = "dashboard",
) -> dict[str, Any]:
    from ha_ops_mcp.lovelace_validate import validate_yaml

    return validate_yaml(yaml_str, scope=scope)


async def _read_dashboard_resources(
    ctx: HaOpsContext, dashboard_id: str
) -> list[dict[str, Any]]:
    """Read the per-dashboard ``resources`` array from a storage dashboard.

    Storage dashboards may carry their own ``config.resources`` list separate
    from the global resources file. Returns [] when missing or unreadable.
    """
    config = await _get_dashboard_config(ctx, dashboard_id)
    if not isinstance(config, dict):
        return []
    raw = config.get("resources")
    if not isinstance(raw, list):
        return []
    return [r for r in raw if isinstance(r, dict)]


@registry.tool(
    name="haops_dashboard_resources",
    description=(
        "List frontend resources registered under Settings → Dashboards → "
        "Resources, plus per-dashboard resource overrides. Resources are "
        "loaded GLOBALLY by HA — once registered they apply to the default "
        "dashboard AND every custom dashboard, so this tool's view is "
        "system-wide, not scoped to a single dashboard. Use BEFORE writing "
        "a config that references `custom:<card-name>` to confirm the "
        "card's module is actually loaded; use AFTER getting a 'Custom "
        "element doesn't exist' / 'Card not found' error to diagnose in "
        "one call instead of asking the user to open the Resources UI. "
        "Pairs with haops_dashboard_validate_yaml — when validation warns "
        "about an unknown custom card, call this to confirm whether it's "
        "installed. "
        "Tier 1: reads .storage/lovelace_resources directly (HA's own "
        "filename retains the legacy 'lovelace' prefix). Tier 2: WS "
        "lovelace/resources fallback (used when Lovelace runs in YAML mode "
        "or the storage file is missing). "
        "Parameters: include_dashboard_usage (bool, default true — also "
        "scan storage dashboards' per-dashboard resources arrays and "
        "report which dashboards reference each global resource). "
        "Returns: {resources: [{url, type, resource_id, scope, "
        "used_by_dashboards}], count, source}. scope is 'global' for "
        "items from the resources registry, 'dashboard' for items only "
        "found in a single dashboard's config.resources."
    ),
    params={
        "include_dashboard_usage": {
            "type": "boolean",
            "description": "Cross-link to dashboards that reference each resource",
            "default": True,
        },
    },
)
async def haops_dashboard_resources(
    ctx: HaOpsContext,
    include_dashboard_usage: bool = True,
) -> dict[str, Any]:
    source = "filesystem"
    items: list[dict[str, Any]] = []

    # Tier 1: filesystem
    storage_path = (
        Path(ctx.config.filesystem.config_root) / ".storage" / "lovelace_resources"
    )
    parsed = False
    try:
        data = json.loads(storage_path.read_text())
        raw_items = data.get("data", {}).get("items", [])
        if isinstance(raw_items, list):
            items = [r for r in raw_items if isinstance(r, dict)]
            parsed = True
    except (FileNotFoundError, KeyError, json.JSONDecodeError):
        pass

    if not parsed:
        # Tier 2: WebSocket fallback (YAML-mode lovelace, or missing storage)
        try:
            result = await ctx.ws.send_command("lovelace/resources")
            if isinstance(result, list):
                items = [r for r in result if isinstance(r, dict)]
                source = "websocket"
            else:
                return {
                    "resources": [],
                    "count": 0,
                    "source": "none",
                    "error": "Lovelace resources unavailable via filesystem or WebSocket",
                }
        except WebSocketError as e:
            return {
                "resources": [],
                "count": 0,
                "source": "none",
                "error": (
                    "Lovelace resources unavailable via filesystem or "
                    f"WebSocket: {e}"
                ),
            }

    # Build the global list (URL is the natural identity key)
    resources: dict[str, dict[str, Any]] = {}
    for item in items:
        url = item.get("url")
        if not url:
            continue
        resources[url] = {
            "url": url,
            "type": item.get("type"),
            "resource_id": item.get("id"),
            "scope": "global",
            "used_by_dashboards": [],
        }

    # Cross-link: scan storage dashboards' per-dashboard resources arrays
    if include_dashboard_usage:
        try:
            dashboards = await ctx.ws.send_command("lovelace/dashboards/list")
        except WebSocketError:
            dashboards = None

        # Always probe the default dashboard ('lovelace'), plus any named
        # storage-mode dashboards from the WS list.
        targets: list[str] = ["lovelace"]
        if isinstance(dashboards, list):
            for d in dashboards:
                if not isinstance(d, dict):
                    continue
                if d.get("mode") and d.get("mode") != "storage":
                    continue
                up = d.get("url_path")
                if up and up not in targets:
                    targets.append(up)

        for url_path in targets:
            per_resources = await _read_dashboard_resources(ctx, url_path)
            for r in per_resources:
                rurl = r.get("url")
                if not rurl:
                    continue
                if rurl not in resources:
                    # Dashboard-only resource (not in the global list)
                    resources[rurl] = {
                        "url": rurl,
                        "type": r.get("type"),
                        "resource_id": None,
                        "scope": "dashboard",
                        "used_by_dashboards": [url_path],
                    }
                else:
                    resources[rurl]["used_by_dashboards"].append(url_path)

    result_list = list(resources.values())
    return {
        "resources": result_list,
        "count": len(result_list),
        "source": source,
    }


@registry.tool(
    name="haops_dashboard_diff",
    description=(
        "Preview dashboard changes before applying. Returns a human-readable "
        "diff and a confirmation token. Does NOT write anything. "
        "Three modes (use ONE): "
        "(a) full config: pass new_config (full dashboard JSON) — rewrites "
        "everything. Use sparingly; large dashboards can be 50+ KB. "
        "(b) single-view replace (RECOMMENDED for targeted edits): pass "
        "view (int index | string path/title) + new_view (the replacement "
        "view JSON). The server composes the full config internally. "
        "(c) view append: pass new_view without view — appends a new view. "
        "Parameters: dashboard_id (string, required), "
        "new_config (dict, optional — full mode), "
        "view (int or string, optional — target view for single-view replace), "
        "new_view (dict, optional — replacement/new view body). "
        "The returned token is used with haops_dashboard_apply. "
        "For surgical edits (changing one card, one field, or a few "
        "properties), prefer haops_dashboard_patch — its JSON Patch input "
        "produces a much smaller approval footprint. Use dashboard_diff "
        "for full view rewrites or appending a new view. "
        "Response fields: diff (real unified diff with +/- line markers), "
        "diff_rendered (same diff wrapped in a markdown ```diff code fence so "
        "chat clients colourise the +/- lines). "
        "REVIEW PROTOCOL — TWO non-negotiable parts: "
        "(1) RENDER, ALWAYS. After this returns you MUST paste the value "
        "of `diff_rendered` verbatim (the entire markdown ```diff fenced "
        "block, not a paraphrase, not a summary, not a description of "
        "what changed) as your next chat message. The chat surface "
        "renders the fence with red/green +/- colourisation — this is "
        "the ONLY visual review the human gets, because Claude Code's "
        "tool-result panel only shows escaped JSON. Render every time, "
        "even for trivial one-line changes, even when the user pre-"
        "approved. The render is the receipt of what is about to land. "
        "(2) STOP for approval. After rendering, wait for explicit user "
        "approval before calling haops_dashboard_apply. EXCEPTION applies "
        "ONLY to the stop, NEVER to the render: if the user has already "
        "explicitly approved this specific change in the current turn "
        "('yes apply it', 'go ahead', or pre-approval in the prompt), "
        "you may chain to apply in the same turn — but the diff render "
        "still happens first."
    ),
    params={
        "dashboard_id": {
            "type": "string",
            "description": "Dashboard url_path",
        },
        "new_config": {
            "type": "object",
            "description": "Full new dashboard config (full mode)",
        },
        "view": {
            "type": ["integer", "string"],
            "description": "Target view (index, path, or title) for view-replace",
        },
        "new_view": {
            "type": "object",
            "description": "New view body (for view-replace or view-append)",
        },
    },
)
async def haops_dashboard_diff(
    ctx: HaOpsContext,
    dashboard_id: str,
    new_config: dict[str, Any] | None = None,
    view: int | str | None = None,
    new_view: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # Validate mode
    modes_used = sum([new_config is not None, new_view is not None])
    if modes_used == 0:
        return {
            "error": "Provide one of: new_config (full) OR new_view "
            "(with optional view for single-view replace)",
        }
    if new_config is not None and new_view is not None:
        return {"error": "Specify either new_config OR new_view, not both"}

    old_config = await _get_dashboard_config(ctx, dashboard_id)
    if old_config is None:
        old_config = {}

    # Compose the target new_config
    if new_config is not None:
        target_config = new_config
    else:
        # View-replace or view-append mode — compose from old_config
        target_config = (
            dict(old_config) if isinstance(old_config, dict) else {}
        )
        views = list(target_config.get("views", []))

        if view is not None:
            # Resolve view to index. Mirrors haops_dashboard_get's resolver:
            # accept native int, digit-string (MCP transports sometimes
            # stringify int params — "5" should be treated as index 5),
            # then fall back to path / title lookup. bool is excluded
            # explicitly so view=True doesn't become index 1.
            idx: int | None = None
            if isinstance(view, int) and not isinstance(view, bool):
                idx = view
            else:
                q = str(view).strip()
                if q.isdigit():
                    candidate = int(q)
                    if 0 <= candidate < len(views):
                        idx = candidate
                if idx is None:
                    for i, v in enumerate(views):
                        if not isinstance(v, dict):
                            continue
                        if (
                            v.get("path") == q
                            or (v.get("title") or "").lower() == q.lower()
                        ):
                            idx = i
                            break
            if idx is None or idx < 0 or idx >= len(views):
                return {
                    "error": f"View '{view}' not found. Use "
                    "haops_dashboard_get(summary=True) to list views.",
                }
            views[idx] = new_view
        else:
            # Append mode
            views.append(new_view)

        target_config["views"] = views

    # Real unified diff of the YAML serialisation of old vs new dashboard
    # config — gives the reviewer line-marked +/- output that markdown-
    # aware clients colourise. Replaces the prior format_json_diff blob,
    # which had no line markers (gap report 2026-04-18 §1).
    diff_text = yaml_unified_diff(old_config, target_config, label=dashboard_id)
    if not diff_text:
        return {"message": "No changes detected", "diff": ""}

    token = ctx.safety.create_token(
        action="dashboard_apply",
        details={
            "dashboard_id": dashboard_id,
            "new_config": target_config,
            "old_config": old_config,
        },
    )

    return {
        "diff": diff_text,
        "diff_rendered": render_diff(diff_text),
        "token": token.id,
        "message": (
            "Review the diff above. "
            "Call haops_dashboard_apply with this token to apply."
        ),
    }


@registry.tool(
    name="haops_dashboard_patch",
    description=(
        "Preview (and optionally apply) a dashboard change expressed as a "
        "JSON PATCH (RFC 6902). Default: returns a human-readable diff + "
        "confirmation token; call haops_dashboard_apply with the token to "
        "commit. With auto_apply=true: previews AND applies atomically in "
        "one call — returns diff + success + transaction_id + backup_path. "
        "Use this instead of haops_dashboard_diff when you want the "
        "tool-call approval modal to show only the targeted edit — the "
        "request payload is a compact JSON Patch array like "
        "[{\"op\": \"replace\", \"path\": \"/views/3/cards/0/entity\", "
        "\"value\": \"light.new\"}], not the entire proposed view or "
        "dashboard config. The `path` field makes the intent explicit "
        "(which card, which field), which is exactly what a human reviewer "
        "needs at approval time. "
        "Supported ops: add, remove, replace, move, copy, test (standard "
        "RFC 6902). Array append via path ending in `/-`. "
        "Parameters: dashboard_id (string, required), patch (list of JSON "
        "Patch op objects, required), auto_apply (bool, default false). "
        "Examples: "
        "[{\"op\": \"replace\", \"path\": \"/title\", \"value\": \"New Title\"}] — "
        "rename dashboard. "
        "[{\"op\": \"add\", \"path\": \"/views/2/cards/-\", \"value\": {\"type\": "
        "\"entities\", \"entities\": [\"sensor.x\"]}}] — append card to view 2. "
        "[{\"op\": \"remove\", \"path\": \"/views/3/cards/5\"}] — remove a card. "
        "For full view rewrites or appending a new view, use "
        "haops_dashboard_diff instead — it accepts complete view JSON. "
        "Response fields match haops_dashboard_diff: diff (per-op anchor + "
        "real unified diff with +/- markers), diff_rendered (same in a "
        "markdown ```diff fence), token, message. "
        "REVIEW PROTOCOL — TWO non-negotiable parts: "
        "(1) RENDER, ALWAYS (auto_apply=false case). After preview "
        "returns you MUST paste `diff_rendered` verbatim (the entire "
        "markdown ```diff fenced block, not a paraphrase) as your next "
        "chat message. The chat surface colourises +/- lines — this is "
        "the ONLY visual review the human gets. Render every time, "
        "even for trivial edits, even when the user pre-approved. "
        "(2) STOP for approval. After rendering, wait for explicit "
        "user approval before calling haops_dashboard_apply. EXCEPTION "
        "applies ONLY to the stop, NEVER to the render: if the user "
        "already explicitly approved this specific change in the "
        "current turn, you may chain to apply in the same turn — but "
        "the render still happens first."
    ),
    params={
        "dashboard_id": {
            "type": "string",
            "description": "Dashboard url_path (e.g. 'lovelace' for the default)",
        },
        "patch": {
            "type": "array",
            "description": (
                "JSON Patch (RFC 6902) array — each item is "
                "{op, path, value?} where op ∈ add|remove|replace|move|copy|test"
            ),
            "items": {"type": "object"},
        },
        "auto_apply": {
            "type": "boolean",
            "description": "Preview AND apply atomically in one call",
            "default": False,
        },
    },
)
async def haops_dashboard_patch(
    ctx: HaOpsContext,
    dashboard_id: str,
    patch: list[dict[str, Any]],
    auto_apply: bool = False,
) -> dict[str, Any]:
    import jsonpatch

    if not isinstance(patch, list) or not patch:
        return {
            "error": (
                "patch must be a non-empty JSON Patch array. Each element "
                "is an operation object like "
                "{\"op\": \"replace\", \"path\": \"/views/0/title\", "
                "\"value\": \"New\"}."
            ),
        }

    old_config = await _get_dashboard_config(ctx, dashboard_id)
    if old_config is None:
        return {
            "error": f"Dashboard not found: {dashboard_id}",
            "hint": (
                "Use haops_dashboard_list to see available dashboards. "
                "For new dashboards, use haops_dashboard_diff with new_config."
            ),
        }

    # Apply the patch. Any JsonPatch / JsonPointer failure lands here with
    # a specific error — do NOT attempt recovery. The common cause is a
    # stale read (dashboard was edited elsewhere between the LLM reading
    # it and sending the patch). Silent fuzzy shifting would corrupt the
    # dashboard in ways that are hard to audit.
    try:
        patch_obj = jsonpatch.JsonPatch(patch)
    except (jsonpatch.InvalidJsonPatch, TypeError) as e:
        return {
            "error": "Invalid JSON Patch",
            "details": str(e),
            "hint": (
                "Each operation must have an 'op' and 'path'. add/replace/test "
                "also require 'value'. move/copy require 'from'. "
                "See RFC 6902."
            ),
        }

    try:
        new_config = patch_obj.apply(old_config)
    except (
        jsonpatch.JsonPatchConflict,
        jsonpatch.JsonPatchTestFailed,
        jsonpatch.JsonPointerException,
    ) as e:
        return {
            "error": "Patch does not apply cleanly",
            "details": str(e),
            "hint": (
                "The dashboard has likely changed since you read it, or the "
                "JSON Pointer path is wrong. Re-read with haops_dashboard_get "
                "and regenerate the patch."
            ),
        }

    if not isinstance(new_config, dict):
        return {
            "error": "Patch produced a non-object result",
            "details": (
                f"Expected a dashboard config object at the root, got "
                f"{type(new_config).__name__}. The patch likely replaced the "
                "root with a primitive value."
            ),
        }

    # Compute the diff. We have two sources of truth here:
    #   1. The JSON Patch ops themselves (what the caller intended).
    #   2. The deepdiff output (positional comparison of before/after).
    # For array-position ops (add/remove on paths ending in a number or
    # /-), deepdiff is misleading — inserting one card shows 20+ "Changed
    # values" because every subsequent card shifts index. Prefer a
    # purpose-built summary from the ops when the patch is all
    # array-position work.
    readable = _render_patch_aware_diff(patch, old_config, new_config)

    if not readable:
        return {
            "message": "Patch is a no-op (produced identical config)",
            "diff": "",
        }

    # Soft entity-existence validation — warn about entity refs in the
    # new config that don't exist in the registry. Catches typos before
    # deploy. Warn only, never block (templated entities, input_* helpers,
    # and entities that register at runtime won't resolve cleanly).
    entity_warnings = await _check_entity_refs(ctx, new_config)

    token = ctx.safety.create_token(
        action="dashboard_apply",
        details={
            "dashboard_id": dashboard_id,
            "new_config": new_config,
            "old_config": old_config,
        },
    )

    if auto_apply:
        apply_result = await haops_dashboard_apply(ctx, token=token.id)
        response: dict[str, Any] = {
            "diff": readable,
            "diff_rendered": render_diff(readable),
            **apply_result,
        }
        if entity_warnings:
            response["entity_warnings"] = entity_warnings
        return response

    response = {
        "diff": readable,
        "diff_rendered": render_diff(readable),
        "token": token.id,
        "message": (
            "Review the diff above. "
            "Call haops_dashboard_apply with this token to apply."
        ),
    }
    if entity_warnings:
        response["entity_warnings"] = entity_warnings
    return response


@registry.tool(
    name="haops_dashboard_apply",
    description=(
        "Apply a previously previewed dashboard change via WebSocket. "
        "Requires a confirmation token from haops_dashboard_diff. "
        "Creates a rollback savepoint and a persistent backup. "
        "Parameters: token (string, required). "
        "This is a MUTATING operation. "
        "Prefer this tool over hand-generating paste-back YAML for HA's raw "
        "config editor — HA's editor silently rejects YAML that drifts from "
        "its own emitter (folded `>-` vs literal `|-`, line-wrap column, "
        "blank-line counts inside templates). See "
        "docs/HA_QUIRKS.md for known gotchas."
    ),
    params={
        "token": {
            "type": "string",
            "description": "Confirmation token from haops_dashboard_diff",
        },
    },
)
async def haops_dashboard_apply(
    ctx: HaOpsContext, token: str
) -> dict[str, Any]:
    try:
        token_data = ctx.safety.validate_token(token)
    except Exception as e:
        return {"error": str(e)}

    details = token_data.details
    dashboard_id: str = details["dashboard_id"]
    new_config: dict[str, Any] = details["new_config"]
    old_config: dict[str, Any] = details["old_config"]

    # Rollback savepoint
    txn = ctx.rollback.begin("dashboard_apply")
    txn.savepoint(
        name=f"dashboard:{dashboard_id}",
        undo=UndoEntry(
            type=UndoType.DASHBOARD,
            description=f"Revert dashboard '{dashboard_id}' to previous config",
            data={
                "dashboard_id": dashboard_id,
                "config": old_config,
            },
        ),
    )

    # Persistent backup
    backup_path: str | None = None
    if ctx.config.safety.backup_on_write and old_config:
        entry = await ctx.backup.backup_dashboard(
            dashboard_id, old_config, operation="dashboard_apply"
        )
        backup_path = entry.backup_path

    # Apply via WebSocket
    try:
        kwargs: dict[str, Any] = {"config": new_config}
        if dashboard_id != "lovelace":
            kwargs["url_path"] = dashboard_id
        await ctx.ws.send_command("lovelace/config/save", **kwargs)
    except WebSocketError as e:
        return {"error": f"Failed to save dashboard: {e}"}

    ctx.safety.consume_token(token)
    ctx.rollback.commit(txn.id)

    # Store old+new config so the Timeline UI can recompute the JSON diff.
    # transaction_id lets the Timeline Revert button locate the in-session
    # RollbackManager txn and fire haops_rollback against it.
    await ctx.audit.log(
        tool="dashboard_apply",
        details={
            "dashboard_id": dashboard_id,
            "old_config": old_config,
            "new_config": new_config,
            "transaction_id": txn.id,
        },
        backup_path=backup_path,
        token_id=token,
    )

    result: dict[str, Any] = {
        "success": True,
        "dashboard_id": dashboard_id,
        "transaction_id": txn.id,
    }
    if backup_path:
        result["backup_path"] = backup_path

    return result
