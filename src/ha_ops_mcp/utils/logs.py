"""Log-fetching helper shared across tools.

``haops_system_logs`` is the user-facing tool for the same data, but several
other tools (notably ``haops_service_call`` on failure) want a quick look at
recent log lines to enrich an error response. Centralising the 3-tier
fallback (filesystem → Supervisor /core/logs → REST /api/error_log) keeps
the behaviour consistent and avoids duplicating the retry logic.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

import aiohttp

from ha_ops_mcp.connections.rest import RestClientError

if TYPE_CHECKING:
    from ha_ops_mcp.server import HaOpsContext


async def fetch_log_text(ctx: HaOpsContext) -> tuple[str, str] | None:
    """Return ``(log_text, source)`` for the current HA log, or None.

    Tries three sources in order — same hierarchy as ``haops_system_logs``:
    1. ``<config_root>/home-assistant.log`` (HA Core when file logging is on)
    2. Supervisor ``/core/logs`` (HA OS, journald-backed)
    3. REST ``/api/error_log`` (standalone installs)

    ``source`` names the tier that succeeded so callers can report accurate
    provenance. Returns ``None`` only if all three fail — callers should
    degrade gracefully rather than error.
    """
    log_file = Path(ctx.config.filesystem.config_root) / "home-assistant.log"
    if log_file.exists():
        try:
            return log_file.read_text(errors="replace"), str(log_file)
        except OSError:
            pass

    try:
        headers = {"Authorization": f"Bearer {ctx.config.ha.resolve_token()}"}
        timeout = aiohttp.ClientTimeout(total=10)
        async with aiohttp.ClientSession() as session, session.get(
            "http://supervisor/core/logs", headers=headers, timeout=timeout
        ) as resp:
            if resp.status == 200:
                return await resp.text(), "supervisor:/core/logs"
    except (aiohttp.ClientError, TimeoutError):
        pass

    try:
        return await ctx.rest.get_text("/api/error_log"), "/api/error_log"
    except RestClientError:
        return None


async def recent_log_matches(
    ctx: HaOpsContext,
    tokens: list[str],
    *,
    tail_lines: int = 200,
    max_matches: int = 10,
) -> list[str]:
    """Scan the tail of the HA log for lines mentioning any ``tokens``.

    Used by error-path enrichment — on a non-2xx service call we want to
    attach the matching stack-trace lines without forcing the caller into a
    second ``haops_system_logs`` round trip.

    Args:
        ctx: Server context.
        tokens: Case-insensitive substrings — a line is included if it
            contains any of them. Typically the service domain, the
            ``domain.service`` tag, and common exception class names.
        tail_lines: Only scan the last N lines. HA logs rotate frequently;
            scanning the entire file adds latency without adding signal.
        max_matches: Cap the returned list — relevant context is usually in
            the top few lines. Trimming keeps error payloads compact.

    Returns:
        Matching log lines (newest last), or an empty list if the log is
        unreachable or has no relevant entries.
    """
    if not tokens:
        return []

    fetched = await fetch_log_text(ctx)
    if not fetched:
        return []
    log_text, _ = fetched

    lines = log_text.splitlines()[-tail_lines:]
    needles = [t.lower() for t in tokens if t]
    if not needles:
        return []

    # Use regex OR for a single pass — marginal over Python iteration but
    # keeps the intent obvious for future readers.
    pattern = re.compile("|".join(re.escape(n) for n in needles), re.IGNORECASE)
    matches = [line for line in lines if pattern.search(line)]
    return matches[-max_matches:]
