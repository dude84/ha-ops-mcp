"""HA-compatible YAML loader.

Home Assistant extends YAML with custom tags — `!include`, `!include_dir_*`,
`!secret`, `!env_var` — that point to other files or values on disk. To walk
HA's real config graph we must resolve these the way HA does.

This module implements a minimal but correct subset that covers the patterns
we see in real HA configs:

- `!secret <key>` — value looked up in `secrets.yaml` (scalar replacement)
- `!env_var <name> [default]` — environment variable
- `!include <path>` — load another YAML file as the value
- `!include_dir_list <dir>` — list of all `*.yaml` file contents in the dir
- `!include_dir_merge_list <dir>` — concatenated list from all `*.yaml` files
- `!include_dir_named <dir>` — dict keyed by filename (without extension)
- `!include_dir_merge_named <dir>` — dict merged across all `*.yaml` files

Design choices:

- Loading produces a `LoadResult` with (1) the resolved data, (2) the list of
  `LoadIssue` records for broken includes / missing secrets — we never crash
  the caller, we degrade. The refindex issue computer converts these to
  user-visible Issues.
- Every resolved path runs through a `path_guard` callable before read, so
  the loader cannot escape the config root.
- Circular `!include` graphs are detected via a visited-set and surfaced as
  an issue, not a stack overflow.
- Source-file provenance: each resolved include returns data plus the file
  it came from, so callers can track where each entry originated for edge
  `location` strings.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

logger = logging.getLogger(__name__)


# Path guard type: accepts a Path, returns the resolved path (or raises if unsafe).
PathGuardFn = Callable[[Path], Path]


@dataclass
class LoadIssue:
    """A non-fatal problem encountered while loading HA YAML."""

    code: str  # "broken_include" | "missing_secrets_file" | "circular_include" | ...
    message: str
    file: str | None = None  # the file that referenced the broken path
    target: str | None = None  # the referenced path or secret name


@dataclass
class LoadResult:
    """Outcome of loading a YAML file (or include tree).

    Attributes:
        data: The fully-resolved data (dict/list/scalar). None if the root
              file itself could not be opened.
        source_file: The root file this load started from (relative to config root).
        issues: Non-fatal problems (broken includes, missing secrets, circular
                references). Empty list if clean.
        included_files: Every file visited during include resolution (relative
                       to config root). Useful for cache keys and debugging.
    """

    data: Any
    source_file: str | None = None
    issues: list[LoadIssue] = field(default_factory=list)
    included_files: list[str] = field(default_factory=list)


class HaYamlLoader:
    """Stateful loader that resolves HA custom tags as it walks YAML.

    One loader instance per top-level load call. Not thread-safe, but that's
    fine — the refindex builder is single-threaded per query.
    """

    def __init__(
        self,
        config_root: Path,
        path_guard: PathGuardFn | None = None,
    ) -> None:
        self._config_root = config_root.resolve()
        self._path_guard = path_guard
        self._yaml = YAML(typ="rt")
        self._yaml.preserve_quotes = True
        # Active include stack — files currently being resolved.
        # A file reappearing in this stack is a real A→B→A cycle, distinct
        # from the benign case of the same file being !include'd from
        # multiple independent parents (which HA fully supports and which
        # we must NOT flag as circular).
        self._active_stack: list[str] = []
        # Cycle signatures we've already reported. A cycle {A,B,C} is
        # signalled once even if the resolver re-enters it via multiple
        # paths — otherwise themed dashboards (ULM card templates) emit
        # thousands of duplicates.
        self._reported_cycles: set[tuple[str, ...]] = set()
        self._issues: list[LoadIssue] = []
        self._included: list[str] = []
        self._secrets: dict[str, Any] | None = None  # lazy-loaded

    # ── Public API ─────────────────────────────────────────────────────

    def load(self, path: Path) -> LoadResult:
        """Load a YAML file, resolving all HA custom tags recursively.

        Args:
            path: File to load. May be absolute or relative to config_root.

        Returns:
            LoadResult with data + issues + file list.
        """
        abs_path = self._resolve(path)
        if abs_path is None:
            return LoadResult(
                data=None,
                source_file=self._rel(path),
                issues=list(self._issues),
                included_files=list(self._included),
            )

        resolved = self._load_and_resolve(abs_path)
        return LoadResult(
            data=resolved,
            source_file=self._rel(abs_path),
            issues=list(self._issues),
            included_files=list(self._included),
        )

    def _load_and_resolve(self, abs_path: Path) -> Any:
        """Load `abs_path` and resolve its tags, with cycle tracking.

        This is the single entry point that manages `_active_stack`. Both
        `load()` and `_resolve_include*` go through here so cycles across
        arbitrary include depths are caught.
        """
        rel = self._rel(abs_path)
        if self._detect_cycle(rel):
            return None
        try:
            raw = self._load_raw(abs_path)
        except OSError as e:
            self._add_issue(
                "broken_include",
                f"Cannot read {rel}: {e}",
                file=None,
                target=rel,
            )
            return None
        self._active_stack.append(rel)
        try:
            return self._resolve_tags(raw, origin=abs_path)
        finally:
            # Defensive pop — match the push even if tag resolution raised.
            if self._active_stack and self._active_stack[-1] == rel:
                self._active_stack.pop()

    # ── Internal helpers ───────────────────────────────────────────────

    def _load_raw(self, abs_path: Path) -> Any:
        """Read and parse a single YAML file — no tag resolution, no cycle
        tracking. Cycle detection is the caller's responsibility via
        `_active_stack`; see `_resolve_include` and `load`.
        """
        rel = self._rel(abs_path)
        if rel not in self._included:
            self._included.append(rel)
        with open(abs_path) as f:
            return self._yaml.load(f)

    def _detect_cycle(self, rel: str) -> bool:
        """Return True if `rel` is already in the active include stack
        (i.e., we'd be including a file into its own ancestor chain).
        Also records the cycle signature so we only report each cycle once.
        """
        if rel not in self._active_stack:
            return False
        # Cycle signature = sorted unique members of the cycle, ensuring we
        # emit one issue per distinct cycle regardless of entry point.
        start = self._active_stack.index(rel)
        members = tuple(sorted(set(self._active_stack[start:] + [rel])))
        if members in self._reported_cycles:
            return True
        self._reported_cycles.add(members)
        cycle_path = " → ".join(self._active_stack[start:] + [rel])
        self._add_issue(
            "circular_include",
            f"Circular !include: {cycle_path}",
            file=rel,
            target=rel,
        )
        return True

    def _resolve_tags(self, node: Any, origin: Path) -> Any:
        """Walk the parsed YAML tree and resolve any HA-specific tags.

        ruamel.yaml exposes custom tags via the `.tag` attribute on scalars
        and container nodes. Unknown tags are passed through unchanged.
        """
        if node is None:
            return None

        # Tagged scalars (!secret, !env_var) are the common case.
        tag = getattr(node, "tag", None)
        if tag is not None:
            tag_value = getattr(tag, "value", None) or str(tag)
            resolved = self._resolve_tag(tag_value, node, origin)
            if resolved is not _PASS_THROUGH:
                return resolved

        # Recurse into containers.
        if isinstance(node, dict):
            return {k: self._resolve_tags(v, origin) for k, v in node.items()}
        if isinstance(node, list):
            return [self._resolve_tags(v, origin) for v in node]
        return node

    def _resolve_tag(self, tag: str, node: Any, origin: Path) -> Any:
        """Resolve a single tagged value. Returns _PASS_THROUGH if not ours."""
        # ruamel.yaml scalar: node itself behaves like a string; coerce for safety.
        arg = str(node) if not isinstance(node, (dict, list)) else ""

        if tag == "!secret":
            return self._resolve_secret(arg.strip(), origin)
        if tag == "!env_var":
            return self._resolve_env_var(arg.strip())
        if tag == "!include":
            return self._resolve_include(arg.strip(), origin)
        if tag == "!include_dir_list":
            return self._resolve_include_dir(arg.strip(), origin, merge_list=False, named=False)
        if tag == "!include_dir_merge_list":
            return self._resolve_include_dir(arg.strip(), origin, merge_list=True, named=False)
        if tag == "!include_dir_named":
            return self._resolve_include_dir(arg.strip(), origin, merge_list=False, named=True)
        if tag == "!include_dir_merge_named":
            return self._resolve_include_dir(arg.strip(), origin, merge_list=True, named=True)
        return _PASS_THROUGH

    def _resolve_secret(self, key: str, origin: Path) -> Any:
        if self._secrets is None:
            # First access — find and load secrets.yaml.
            self._load_secrets(origin)
        if self._secrets is None:
            return None
        if key not in self._secrets:
            self._add_issue(
                "missing_secret",
                f"Secret '{key}' not found in secrets.yaml",
                file=self._rel(origin),
                target=key,
            )
            return None
        return self._secrets[key]

    def _load_secrets(self, origin: Path) -> None:
        """Find and load secrets.yaml (searches up from origin to config_root)."""
        search = origin.parent if origin.is_file() else origin
        while True:
            candidate = search / "secrets.yaml"
            if candidate.is_file():
                try:
                    with open(candidate) as f:
                        loaded = YAML().load(f)
                    self._secrets = dict(loaded) if loaded else {}
                    return
                except OSError as e:
                    self._add_issue(
                        "missing_secrets_file",
                        f"Cannot read {candidate}: {e}",
                        file=self._rel(origin),
                        target="secrets.yaml",
                    )
                    self._secrets = {}
                    return
            if search == self._config_root or search == search.parent:
                # Walked up to config root or filesystem root — give up.
                self._secrets = {}
                return
            search = search.parent

    def _resolve_env_var(self, arg: str) -> Any:
        """`!env_var FOO` or `!env_var FOO default_value`."""
        parts = arg.split(None, 1)
        name = parts[0]
        default = parts[1] if len(parts) > 1 else None
        return os.environ.get(name, default)

    def _resolve_include(self, rel: str, origin: Path) -> Any:
        target = self._resolve_relative(rel, origin)
        if target is None:
            return None
        # Route through _load_and_resolve so the active-stack cycle
        # detector sees this descent.
        return self._load_and_resolve(target)

    def _resolve_include_dir(
        self,
        rel: str,
        origin: Path,
        *,
        merge_list: bool,
        named: bool,
    ) -> Any:
        """Resolve any of the four `!include_dir_*` variants.

        merge_list=False, named=False → list of file contents
        merge_list=True,  named=False → concatenated list (flattens top-level lists)
        merge_list=False, named=True  → dict keyed by filename stem
        merge_list=True,  named=True  → dict merged across files
        """
        target_dir = self._resolve_relative(rel, origin)
        if target_dir is None:
            return [] if not named else {}
        if not target_dir.is_dir():
            self._add_issue(
                "broken_include",
                f"!include_dir_* target is not a directory: {rel}",
                file=self._rel(origin),
                target=rel,
            )
            return [] if not named else {}

        files = sorted(p for p in target_dir.glob("*.yaml") if p.is_file())

        if named:
            result: dict[str, Any] = {}
            for f in files:
                key = f.stem
                loaded = self._load_and_resolve(f)
                if merge_list and isinstance(loaded, dict):
                    result.update(loaded)
                elif not merge_list:
                    result[key] = loaded
                elif loaded is not None:
                    # merge_list with a non-dict, non-None file → issue and skip
                    self._add_issue(
                        "broken_include",
                        f"{f} in !include_dir_merge_named is not a dict",
                        file=self._rel(origin),
                        target=str(f),
                    )
            return result

        # List forms
        result_list: list[Any] = []
        for f in files:
            loaded = self._load_and_resolve(f)
            if merge_list and isinstance(loaded, list):
                result_list.extend(loaded)
            else:
                result_list.append(loaded)
        return result_list

    def _resolve_relative(self, rel: str, origin: Path) -> Path | None:
        """Resolve a path relative to `origin`'s directory. Apply path guard."""
        base = origin.parent if origin.is_file() else origin
        candidate = (base / rel).resolve()
        try:
            candidate.relative_to(self._config_root)
        except ValueError:
            self._add_issue(
                "path_traversal",
                f"Refused to resolve {rel} outside config root",
                file=self._rel(origin),
                target=rel,
            )
            return None
        if self._path_guard is not None:
            try:
                candidate = self._path_guard(candidate)
            except Exception as e:
                self._add_issue(
                    "path_guard_blocked",
                    str(e),
                    file=self._rel(origin),
                    target=rel,
                )
                return None
        return candidate

    def _resolve(self, path: Path) -> Path | None:
        """Resolve top-level load path (may be absolute or relative)."""
        p = path if path.is_absolute() else self._config_root / path
        p = p.resolve()
        try:
            p.relative_to(self._config_root)
        except ValueError:
            self._add_issue(
                "path_traversal",
                f"Refused to open {path} outside config root",
                file=None,
                target=str(path),
            )
            return None
        if not p.exists():
            self._add_issue(
                "broken_include",
                f"File not found: {self._rel(p)}",
                file=None,
                target=str(path),
            )
            return None
        return p

    def _rel(self, p: Path) -> str:
        try:
            return str(p.resolve().relative_to(self._config_root))
        except ValueError:
            return str(p)

    def _add_issue(
        self, code: str, message: str, file: str | None, target: str | None
    ) -> None:
        logger.debug("ha_yaml issue [%s]: %s (file=%s target=%s)", code, message, file, target)
        self._issues.append(
            LoadIssue(code=code, message=message, file=file, target=target)
        )


# Sentinel returned by _resolve_tag when the tag isn't one we handle.
_PASS_THROUGH = object()


def merge_packages(
    root_data: dict[str, Any], loader: HaYamlLoader
) -> tuple[dict[str, Any], list[LoadIssue]]:
    """Merge `homeassistant.packages` into the top-level config view.

    HA's package loader takes every key under `homeassistant.packages.<name>`
    and merges it up into the top-level config. So a package file with
    `automation: [...]` contributes to the top-level `automation:` list.

    Merge semantics mirror HA:
    - Lists: concatenated.
    - Dicts: merged; conflicting keys emit an issue and keep the root value.
    - Scalars in top-level conflict: emit issue, keep root.

    Returns the merged config and any issues.
    """
    issues: list[LoadIssue] = []
    if not isinstance(root_data, dict):
        return root_data, issues

    packages = (
        root_data.get("homeassistant", {}).get("packages")
        if isinstance(root_data.get("homeassistant"), dict)
        else None
    )
    if not packages or not isinstance(packages, dict):
        return root_data, issues

    merged = dict(root_data)

    for pkg_name, pkg_data in packages.items():
        if not isinstance(pkg_data, dict):
            issues.append(
                LoadIssue(
                    code="bad_package",
                    message=f"Package '{pkg_name}' is not a dict",
                    target=pkg_name,
                )
            )
            continue
        for key, value in pkg_data.items():
            if key not in merged:
                merged[key] = value
                continue
            existing = merged[key]
            if isinstance(existing, list) and isinstance(value, list):
                merged[key] = existing + value
            elif isinstance(existing, dict) and isinstance(value, dict):
                conflict_keys = set(existing) & set(value)
                new_dict = dict(existing)
                for vk, vv in value.items():
                    if vk in conflict_keys:
                        issues.append(
                            LoadIssue(
                                code="package_key_conflict",
                                message=(
                                    f"Package '{pkg_name}' key '{key}.{vk}' "
                                    "conflicts with root config; keeping root"
                                ),
                                target=f"{pkg_name}.{key}.{vk}",
                            )
                        )
                    else:
                        new_dict[vk] = vv
                merged[key] = new_dict
            else:
                issues.append(
                    LoadIssue(
                        code="package_type_conflict",
                        message=(
                            f"Package '{pkg_name}' key '{key}' has type "
                            f"{type(value).__name__} but root has {type(existing).__name__}"
                        ),
                        target=f"{pkg_name}.{key}",
                    )
                )

    return merged, issues
