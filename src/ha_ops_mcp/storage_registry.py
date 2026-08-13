"""Freshness-aware reads of HA's `.storage/core.*` registries.

Why this module exists
----------------------
Every registry read in this server is filesystem-first (see CLAUDE.md,
"Connection stability hierarchy") — the `.storage` JSON is ground truth and
needs no running HA. But it is ground truth *eventually*: HA persists the
registries through a debounced `Store`, so the file lags live state for a
while after any change, and the debounce timer restarts on each further
change — a burst of writes can hold the flush off well past the nominal
delay.

That lag caused a real, reported bug: `haops_registry_query` listed two
devices that had already been removed, and three removal attempts then
failed with `Unknown device`. A read tool that is confidently wrong is worse
than a slow one.

There is no cache of ours to invalidate. What there *is* is a detectable
case: **the file's mtime predates a registry write this process performed**.
That is proof the file has not caught up yet, and it covers exactly the
sequence that bites — mutate, then read back. In that case we go to the
WebSocket (HA's in-memory registry, authoritative) instead of the file.

So every mutating tool that touches a registry calls `mark_registry_write()`
after a successful write, and every read reports where its data came from
plus how old the file was. Callers can then tell fresh from possibly-stale
instead of guessing.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ha_ops_mcp.server import HaOpsContext

logger = logging.getLogger(__name__)


# ── Write clock ────────────────────────────────────────────────────────

# Monotonic-ish wall clock of the last registry write this process made.
# Wall clock (not time.monotonic) on purpose: it is compared against file
# mtimes, which are wall clock too.
_last_write_ts: float = 0.0
_last_write_what: str | None = None


def mark_registry_write(what: str) -> None:
    """Record that this process just wrote to a `.storage/core.*` registry.

    Call this after a *successful* registry mutation (entity rename/remove,
    device removal, area assignment, ...). Subsequent reads use it to detect
    that the on-disk copy has not been flushed yet.

    Args:
        what: Short label for diagnostics, e.g. "entity_rename".
    """
    global _last_write_ts, _last_write_what
    _last_write_ts = time.time()
    _last_write_what = what


def last_registry_write() -> tuple[float, str | None]:
    """Return (timestamp, label) of the last registry write, or (0.0, None)."""
    return _last_write_ts, _last_write_what


def reset_write_clock() -> None:
    """Clear the write clock. Tests only."""
    global _last_write_ts, _last_write_what
    _last_write_ts = 0.0
    _last_write_what = None


# ── Registry specs ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class RegistrySpec:
    """Where a registry lives and how else to reach it."""

    file: str
    data_key: str
    ws_command: str | None


REGISTRY_SPECS: dict[str, RegistrySpec] = {
    "devices": RegistrySpec(
        file=".storage/core.device_registry",
        data_key="devices",
        ws_command="config/device_registry/list",
    ),
    "entities": RegistrySpec(
        file=".storage/core.entity_registry",
        data_key="entities",
        ws_command="config/entity_registry/list",
    ),
    "areas": RegistrySpec(
        file=".storage/core.area_registry",
        data_key="areas",
        ws_command="config/area_registry/list",
    ),
    "floors": RegistrySpec(
        file=".storage/core.floor_registry",
        data_key="floors",
        ws_command="config/floor_registry/list",
    ),
    "config_entries": RegistrySpec(
        file=".storage/core.config_entries",
        data_key="entries",
        # No WS list endpoint that returns the same shape; `config_entries/get`
        # is per-domain and differently shaped, so there is no drop-in fallback.
        ws_command=None,
    ),
}


# ── Read result ────────────────────────────────────────────────────────


@dataclass
class RegistryRead:
    """A registry read plus the provenance a caller needs to trust it."""

    records: list[dict[str, Any]]
    source: str
    """"file" (the .storage JSON) or "websocket" (HA's live in-memory copy)."""
    file_age_seconds: float | None = None
    """Age of the .storage file at read time. None if the file was unreadable."""
    file_predates_our_write: bool = False
    """True when the file's mtime is older than our last registry write —
    i.e. the file is provably behind live state."""
    notes: list[str] = field(default_factory=list)

    def provenance(self) -> dict[str, Any]:
        """The provenance block tools splice into their response."""
        out: dict[str, Any] = {"source": self.source}
        if self.file_age_seconds is not None:
            out["file_age_seconds"] = round(self.file_age_seconds, 1)
        if self.file_predates_our_write:
            out["file_predates_our_write"] = True
        if self.notes:
            out["notes"] = list(self.notes)
        return out


def _file_age(path: Path) -> float | None:
    try:
        return max(0.0, time.time() - path.stat().st_mtime)
    except OSError:
        return None


def _file_is_behind_our_write(path: Path) -> bool:
    """True if the file was last written before our last registry write."""
    if _last_write_ts <= 0.0:
        return False
    try:
        return path.stat().st_mtime < _last_write_ts
    except OSError:
        return False


def _records_from_file(
    path: Path, data_key: str
) -> list[dict[str, Any]] | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    records = data.get("data", {}).get(data_key) if isinstance(data, dict) else None
    if isinstance(records, list):
        return records
    return None


async def _records_from_ws(
    ctx: HaOpsContext, command: str
) -> list[dict[str, Any]] | None:
    from ha_ops_mcp.connections.websocket import WebSocketError

    try:
        result: Any = await ctx.ws.send_command(command)
    except (WebSocketError, OSError) as e:
        logger.debug("Registry WS read %s failed: %s", command, e)
        return None
    if isinstance(result, list):
        return result
    return None


async def load_registry(
    ctx: HaOpsContext,
    name: str,
    *,
    fresh: bool = False,
) -> RegistryRead:
    """Load a registry, filesystem-first, WebSocket when the file can't be trusted.

    The WebSocket is used when either (a) the caller asked for `fresh`, or
    (b) the file provably predates a registry write this process made, or
    (c) the file is missing/corrupt. Otherwise the file wins — it is faster
    and works when HA does not answer.

    Args:
        ctx: Server context.
        name: One of `REGISTRY_SPECS`.
        fresh: Force a live WebSocket read.

    Returns:
        RegistryRead with the records and their provenance.

    Raises:
        KeyError: Unknown registry name.
        RuntimeError: Neither source produced records.
    """
    spec = REGISTRY_SPECS[name]
    path = Path(ctx.config.filesystem.config_root) / spec.file
    age = _file_age(path)
    behind = _file_is_behind_our_write(path)

    prefer_ws = bool(spec.ws_command) and (fresh or behind)

    if prefer_ws and spec.ws_command:
        records = await _records_from_ws(ctx, spec.ws_command)
        if records is not None:
            notes = []
            if behind and not fresh:
                _, what = last_registry_write()
                notes.append(
                    f"Read live over WebSocket: {spec.file} has not been "
                    f"flushed since this session's {what or 'registry write'}, "
                    "so the file would have been stale."
                )
            return RegistryRead(
                records=records,
                source="websocket",
                file_age_seconds=age,
                file_predates_our_write=behind,
                notes=notes,
            )

    from_file = _records_from_file(path, spec.data_key)
    if from_file is not None:
        notes = []
        if behind:
            notes.append(
                f"{spec.file} predates this session's registry write and the "
                "live WebSocket read did not succeed — records may be stale."
            )
        elif prefer_ws:
            notes.append(
                "fresh=true was requested but the live WebSocket read failed; "
                "served the .storage file instead."
            )
        return RegistryRead(
            records=from_file,
            source="file",
            file_age_seconds=age,
            file_predates_our_write=behind,
            notes=notes,
        )

    # File unusable and (if we got here with a ws_command) WS already tried
    # only when prefer_ws was set — try it now for the plain case.
    if spec.ws_command and not prefer_ws:
        records = await _records_from_ws(ctx, spec.ws_command)
        if records is not None:
            return RegistryRead(
                records=records,
                source="websocket",
                file_age_seconds=age,
                notes=[f"{spec.file} unreadable; served the live registry."],
            )

    raise RuntimeError(
        f"Registry '{name}' unavailable — {spec.file} unreadable and "
        + (
            f"the WebSocket fallback ({spec.ws_command}) failed"
            if spec.ws_command
            else "this registry has no WebSocket fallback"
        )
    )
