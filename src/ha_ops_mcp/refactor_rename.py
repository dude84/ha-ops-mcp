"""Rewrite entity_id references across YAML config and Lovelace dashboards.

Renaming a registry entry is half a rename. Every dashboard card, automation,
script and template that named the old id keeps naming it, and HA gives no
warning — the card renders "Entity not available" and the automation silently
never fires. This module is the other half.

Two design calls worth knowing about:

**Discovery is textual, not graph-based.** The reference index knows which
files reference an entity, but its accuracy is the accuracy of its extractors
(known card keys, a Jinja regex). A missed extraction would mean a silently
skipped rewrite — the exact failure we are fixing. So we scan the file
*universe* the index defines (its loose-scan walk plus the Lovelace storage
files) and let a boundary-anchored regex decide, which cannot miss an
occurrence it can see. The index is still used, for the opposite purpose:
reporting references that live somewhere we refuse to write.

**YAML is rewritten as text, not re-emitted.** A pure token substitution
leaves every comment, quote style and blank line byte-identical, which no
round-trip through a YAML emitter can promise. The tradeoff is that we cannot
understand structure — acceptable, because an entity_id is a self-delimiting
token.
"""

from __future__ import annotations

import copy
import difflib
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ha_ops_mcp.server import HaOpsContext

logger = logging.getLogger(__name__)


# ── Token rewriting ────────────────────────────────────────────────────

# An entity_id is `domain.object_id`, both `[a-z0-9_]+`. A match must not be
# preceded by an id character or a dot (`.` guards against clipping the tail
# off `states.sensor.foo` or `sensor.foo` inside a longer dotted path), and
# must not be followed by an id character (guards `sensor.foo` matching inside
# `sensor.foo_2`). A trailing dot is allowed — `sensor.foo.attributes` in a
# template is a genuine reference to `sensor.foo`.
_LEAD = r"(?<![A-Za-z0-9_.])"
_TAIL = r"(?![A-Za-z0-9_])"


@dataclass
class Rewriter:
    """Applies one rename mapping to text, counting hits per old id."""

    mapping: dict[str, str]
    _plain: re.Pattern[str] = field(init=False, repr=False)
    _attr: re.Pattern[str] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        # Longest-first so a longer id wins over a shorter prefix of itself;
        # the boundary guards make this belt-and-braces, not load-bearing.
        alt = "|".join(
            re.escape(k) for k in sorted(self.mapping, key=len, reverse=True)
        )
        self._plain = re.compile(rf"{_LEAD}({alt}){_TAIL}")
        # Jinja attribute form — `states.sensor.foo`. The leading dot after
        # `states` is exactly what `_LEAD` refuses, so it needs its own pass.
        self._attr = re.compile(rf"{_LEAD}states\.({alt}){_TAIL}")

    def apply(self, text: str) -> tuple[str, dict[str, int]]:
        """Rewrite every reference in `text`.

        Returns:
            (new_text, {old_id: hit_count}) — only ids that were hit appear.
        """
        counts: dict[str, int] = {}

        def _sub_attr(m: re.Match[str]) -> str:
            old = m.group(1)
            counts[old] = counts.get(old, 0) + 1
            return f"states.{self.mapping[old]}"

        def _sub_plain(m: re.Match[str]) -> str:
            old = m.group(1)
            counts[old] = counts.get(old, 0) + 1
            return self.mapping[old]

        # Attribute form first: it is the only pass that may consume a
        # dot-prefixed occurrence, and `_sub_plain` can never re-match its
        # output (the result is dot-prefixed, which `_LEAD` rejects).
        out = self._attr.sub(_sub_attr, text)
        out = self._plain.sub(_sub_plain, out)
        return out, counts

    def count(self, text: str) -> dict[str, int]:
        """Hit counts without building the rewritten string."""
        return self.apply(text)[1]

    def apply_structure(
        self, obj: Any, path: str = ""
    ) -> tuple[Any, dict[str, int], list[str]]:
        """Rewrite every string in a JSON-ish tree (dashboard config).

        Dict keys are rewritten too — some custom cards key maps by entity_id.

        Returns:
            (new_obj, {old_id: hit_count}, changed_paths)
        """
        counts: dict[str, int] = {}
        changed: list[str] = []

        def _merge(local: dict[str, int], at: str) -> None:
            if not local:
                return
            for k, v in local.items():
                counts[k] = counts.get(k, 0) + v
            changed.append(at)

        def _walk(node: Any, at: str) -> Any:
            if isinstance(node, str):
                new, local = self.apply(node)
                _merge(local, at)
                return new
            if isinstance(node, dict):
                out: dict[Any, Any] = {}
                for k, v in node.items():
                    new_key = k
                    if isinstance(k, str):
                        new_key, local = self.apply(k)
                        _merge(local, f"{at}.{k}" if at else str(k))
                    child_at = f"{at}.{k}" if at else str(k)
                    out[new_key] = _walk(v, child_at)
                return out
            if isinstance(node, list):
                return [
                    _walk(item, f"{at}[{i}]") for i, item in enumerate(node)
                ]
            return node

        return _walk(copy.deepcopy(obj), path), counts, changed


# ── Plan shapes ────────────────────────────────────────────────────────


@dataclass
class FileRewrite:
    """One YAML/text file to rewrite."""

    rel_path: str
    abs_path: str
    occurrences: dict[str, int]
    old_text: str
    new_text: str

    def diff(self, max_lines: int = 60) -> str:
        """Unified diff, truncated for display."""
        lines = list(
            difflib.unified_diff(
                self.old_text.splitlines(keepends=True),
                self.new_text.splitlines(keepends=True),
                fromfile=f"a/{self.rel_path}",
                tofile=f"b/{self.rel_path}",
                n=1,
            )
        )
        if len(lines) > max_lines:
            shown = "".join(lines[:max_lines])
            return (
                f"{shown}... ({len(lines) - max_lines} more diff lines, "
                f"{sum(self.occurrences.values())} occurrences total)\n"
            )
        return "".join(lines)


@dataclass
class DashboardRewrite:
    """One Lovelace dashboard to rewrite."""

    url_path: str
    storage_file: str
    occurrences: dict[str, int]
    old_config: dict[str, Any]
    new_config: dict[str, Any]
    changed_paths: list[str]


@dataclass
class RewritePlan:
    """Everything a rename would change outside the registry."""

    files: list[FileRewrite] = field(default_factory=list)
    dashboards: list[DashboardRewrite] = field(default_factory=list)
    manual_review: list[dict[str, Any]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def total_occurrences(self) -> int:
        per_target: list[dict[str, int]] = [f.occurrences for f in self.files]
        per_target += [d.occurrences for d in self.dashboards]
        return sum(sum(o.values()) for o in per_target)

    def is_empty(self) -> bool:
        return not self.files and not self.dashboards

    def summary(self) -> dict[str, Any]:
        """Preview-shaped summary (diffs included, content omitted)."""
        return {
            "total_occurrences": self.total_occurrences(),
            "files": [
                {
                    "path": f.rel_path,
                    "occurrences": f.occurrences,
                    "diff": f.diff(),
                }
                for f in self.files
            ],
            "dashboards": [
                {
                    "url_path": d.url_path,
                    "occurrences": d.occurrences,
                    "changed_at": d.changed_paths[:25],
                    "changed_count": len(d.changed_paths),
                }
                for d in self.dashboards
            ],
            "manual_review": self.manual_review,
            "notes": self.notes,
        }


# ── Discovery ──────────────────────────────────────────────────────────


# Files under .storage that hold user-chosen entity ids and break on rename,
# but that HA owns at runtime — writing them behind HA's back would be
# overwritten (or worse, ignored until a restart), so we only report them.
_STORAGE_REPORT_ONLY = ("energy",)


async def _dashboard_url_paths(ctx: HaOpsContext) -> dict[str, str]:
    """Map `.storage` filename → real url_path.

    Not derivable from the filename: HA sanitises `-` to `_` when naming the
    file, so `.storage/lovelace.new_dashboard` may be url_path
    `new-dashboard` OR `new_dashboard`, and saving to the wrong one creates a
    second dashboard. The live list is the only authority.
    """
    from ha_ops_mcp.connections.websocket import WebSocketError

    out = {"lovelace": "lovelace"}
    try:
        listed: Any = await ctx.ws.send_command("lovelace/dashboards/list")
    except (WebSocketError, OSError) as e:
        logger.debug("Dashboard list unavailable: %s", e)
        return out
    if not isinstance(listed, list):
        return out
    for dash in listed:
        if not isinstance(dash, dict):
            continue
        url_path = dash.get("url_path")
        if not url_path:
            continue
        out[f"lovelace.{str(url_path).replace('-', '_')}"] = str(url_path)
    return out


def _yaml_universe(ctx: HaOpsContext) -> list[Path]:
    """Every YAML file the reference index considers in scope."""
    from ha_ops_mcp.refindex.builder import (
        _LOOSE_SCAN_SKIP_DIRS,
        _LOOSE_SCAN_SKIP_GLOBS,
        _iter_yaml_files,
        _path_is_excluded,
    )

    root = ctx.path_guard.config_root
    out: list[Path] = []
    for path in _iter_yaml_files(root, _LOOSE_SCAN_SKIP_DIRS):
        try:
            rel = str(path.resolve().relative_to(root))
        except ValueError:
            continue
        if _path_is_excluded(rel, _LOOSE_SCAN_SKIP_DIRS, _LOOSE_SCAN_SKIP_GLOBS):
            continue
        out.append(path)
    return sorted(out)


def _scan_report_only(ctx: HaOpsContext, rw: Rewriter) -> list[dict[str, Any]]:
    """Find occurrences in places we deliberately do not write."""
    root = ctx.path_guard.config_root
    found: list[dict[str, Any]] = []

    # ESPHome node configs: an entity_id here is a `homeassistant:` import on
    # the device, and changing the YAML does nothing until the node is
    # recompiled and flashed. That is a hardware operation, not a text edit.
    esphome_dir = root / "esphome"
    if esphome_dir.is_dir():
        for path in sorted(esphome_dir.rglob("*.yaml")):
            try:
                counts = rw.count(path.read_text())
            except OSError:
                continue
            if counts:
                found.append({
                    "path": str(path.relative_to(root)),
                    "occurrences": counts,
                    "reason": (
                        "ESPHome node config — takes effect only after "
                        "recompile + flash of the device. Edit and flash "
                        "deliberately."
                    ),
                })

    for name in _STORAGE_REPORT_ONLY:
        path = root / ".storage" / name
        try:
            counts = rw.count(path.read_text())
        except OSError:
            continue
        if counts:
            found.append({
                "path": f".storage/{name}",
                "occurrences": counts,
                "reason": (
                    "HA owns this file at runtime — fix it in the UI "
                    "(Settings → Dashboards → Energy) instead."
                ),
            })

    return found


async def _index_refs_outside(
    ctx: HaOpsContext, mapping: dict[str, str], covered: set[str]
) -> list[dict[str, Any]]:
    """Reference-index edges pointing at files we are not rewriting.

    This is the index's job here: not finding what to change (the text scan
    does that) but naming what a text scan is not allowed to touch.
    """
    from ha_ops_mcp.refindex import get_or_build_index, node_id

    try:
        index = await get_or_build_index(ctx)
    except Exception as e:  # index build is best-effort
        logger.debug("Ref index unavailable for rename planning: %s", e)
        return []

    out: list[dict[str, Any]] = []
    for old in mapping:
        for edge in index.incoming(node_id("entity", old)):
            location = edge.location or ""
            file_ref = location.split(":")[0]
            if not file_ref or file_ref in covered:
                continue
            # Registry files describe the entity itself, not a reference to it.
            if file_ref.startswith(".storage/core."):
                continue
            out.append({
                "entity_id": old,
                "location": location,
                "edge_kind": edge.kind,
                "source_node": edge.source,
                "reason": "Outside the rewritable file set — review by hand.",
            })
    return out


async def plan_reference_rewrites(
    ctx: HaOpsContext, mapping: dict[str, str]
) -> RewritePlan:
    """Work out every reference rewrite implied by `mapping` (old id → new id).

    Args:
        ctx: Server context.
        mapping: Old entity_id → new entity_id. Empty mapping → empty plan.

    Returns:
        A RewritePlan holding the full before/after content for each target,
        so the apply step writes exactly what the preview showed.
    """
    plan = RewritePlan()
    if not mapping:
        return plan

    # Entry point of the rename-planning flow: drop any ref index cached by
    # a previous request (ctx is global) so `_index_refs_outside` analyses
    # the config as it stands now, not as it was when some earlier
    # haops_references call built the graph.
    ctx.request_index = None

    rw = Rewriter(mapping)
    root = ctx.path_guard.config_root
    covered: set[str] = set()

    # YAML / text config
    for path in _yaml_universe(ctx):
        try:
            old_text = path.read_text()
        except OSError:
            continue
        new_text, counts = rw.apply(old_text)
        rel = str(path.relative_to(root))
        covered.add(rel)
        if not counts:
            continue
        plan.files.append(
            FileRewrite(
                rel_path=rel,
                abs_path=str(path),
                occurrences=counts,
                old_text=old_text,
                new_text=new_text,
            )
        )

    # Lovelace storage dashboards
    url_paths = await _dashboard_url_paths(ctx)
    storage_dir = root / ".storage"
    if storage_dir.is_dir():
        for path in sorted(storage_dir.iterdir()):
            name = path.name
            if not path.is_file():
                continue
            if name != "lovelace" and not name.startswith("lovelace."):
                continue
            covered.add(f".storage/{name}")
            try:
                data = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            config = (
                data.get("data", {}).get("config")
                if isinstance(data, dict)
                else None
            )
            if not isinstance(config, dict):
                continue
            new_config, counts, changed = rw.apply_structure(config)
            if not counts:
                continue
            url_path = url_paths.get(name)
            if url_path is None:
                plan.manual_review.append({
                    "path": f".storage/{name}",
                    "occurrences": counts,
                    "reason": (
                        "No live dashboard matches this storage file, so its "
                        "url_path is unknown and saving could create a "
                        "duplicate dashboard. Left untouched."
                    ),
                })
                continue
            plan.dashboards.append(
                DashboardRewrite(
                    url_path=url_path,
                    storage_file=f".storage/{name}",
                    occurrences=counts,
                    old_config=config,
                    new_config=new_config,
                    changed_paths=changed,
                )
            )

    plan.manual_review.extend(_scan_report_only(ctx, rw))
    plan.manual_review.extend(await _index_refs_outside(ctx, mapping, covered))

    plan.notes.append(
        "Rewrites cover YAML under the config root and storage-mode Lovelace "
        "dashboards. Not covered: entity ids built at runtime by a template "
        "(e.g. states('sensor.' ~ name)), custom_components source, and "
        "HA-managed .storage files other than dashboards."
    )
    if plan.files:
        plan.notes.append(
            "YAML changes need a reload to take effect — haops_system_reload "
            "with the matching target (automation / script / template)."
        )
    return plan
