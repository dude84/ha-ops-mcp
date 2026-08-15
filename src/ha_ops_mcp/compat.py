"""Home Assistant compatibility window.

Single source of truth for which HA Core versions this build is developed,
verified, and supported against. `docs/HA_COMPATIBILITY.md` is the prose
version of this file — keep the two in sync when bumping.

The values here are *claims about verification*, not enforcement. Nothing in
the server refuses to run on an out-of-window HA; we log a warning and get on
with it. HA breaks integrations far more often than it breaks the core API
surface we use (WS commands, REST endpoints, `.storage` layouts, recorder
schema), so a version outside the window is a "look here first when something
is weird" signal, not a failure.
"""

from __future__ import annotations

import re

#: HA Core version this release was verified end-to-end against
#: (`haops_tools_check` all-pass on a live instance).
BUILT_AGAINST_HA = "2026.8.2"

#: Recorder DB schema version seen at BUILT_AGAINST_HA. Bumping HA does not
#: necessarily bump this — schema 53 has held since 2026.5.
BUILT_AGAINST_DB_SCHEMA = 53

#: Oldest HA minor we claim to support. Below this we have not run the tool
#: suite and the `.storage` / WS surface may differ.
MIN_SUPPORTED_HA = (2026, 6)

#: Newest HA minor verified. Above this is untested, not known-broken — HA's
#: monthly cadence means this goes stale within weeks by design.
MAX_TESTED_HA = (2026, 8)

_VERSION_RE = re.compile(r"^(\d{4})\.(\d{1,2})")


def parse_ha_version(version: str) -> tuple[int, int] | None:
    """Parse an HA version string to a (year, month) minor tuple.

    Accepts the full `YYYY.M.patch` form as well as `YYYY.M`, and tolerates
    suffixes like `2026.8.0b3`. Returns None if it doesn't look like an HA
    version at all (custom builds, dev checkouts).

    Args:
        version: HA Core version string, e.g. "2026.7.4".

    Returns:
        (year, month) tuple, or None if unparseable.
    """
    match = _VERSION_RE.match(version.strip())
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def check_ha_version(version: str | None) -> str | None:
    """Compare a live HA version against the supported window.

    Args:
        version: HA Core version as reported by `/api/config` or `.HA_VERSION`.

    Returns:
        A warning message if the version is outside the window, else None.
    """
    if not version:
        return None
    parsed = parse_ha_version(version)
    if parsed is None:
        return (
            f"Could not parse Home Assistant version {version!r}. "
            f"This build is verified against HA {BUILT_AGAINST_HA}."
        )
    if parsed < MIN_SUPPORTED_HA:
        return (
            f"Home Assistant {version} is older than the oldest supported "
            f"release ({MIN_SUPPORTED_HA[0]}.{MIN_SUPPORTED_HA[1]}). "
            f"ha-ops-mcp is verified against HA {BUILT_AGAINST_HA}; older "
            f"instances may differ in .storage layout, WebSocket commands, "
            f"or recorder schema. See docs/HA_COMPATIBILITY.md."
        )
    if parsed > MAX_TESTED_HA:
        return (
            f"Home Assistant {version} is newer than the newest verified "
            f"release ({MAX_TESTED_HA[0]}.{MAX_TESTED_HA[1]}). This is not a "
            f"known failure — HA ships monthly and this build predates it. If "
            f"a tool misbehaves, run haops_tools_check first and check "
            f"docs/HA_COMPATIBILITY.md for the API surface we depend on."
        )
    return None


def compat_info(live_version: str | None = None) -> dict[str, object]:
    """Return the compatibility window as a dict for tool responses.

    Args:
        live_version: The connected instance's HA version, if known.

    Returns:
        Dict with the built-against/supported values plus, when a live
        version was supplied, whether it sits inside the window.
    """
    info: dict[str, object] = {
        "built_against_ha": BUILT_AGAINST_HA,
        "built_against_db_schema": BUILT_AGAINST_DB_SCHEMA,
        "min_supported_ha": f"{MIN_SUPPORTED_HA[0]}.{MIN_SUPPORTED_HA[1]}",
        "max_tested_ha": f"{MAX_TESTED_HA[0]}.{MAX_TESTED_HA[1]}",
    }
    if live_version:
        warning = check_ha_version(live_version)
        info["live_ha"] = live_version
        info["in_window"] = warning is None
        if warning:
            info["warning"] = warning
    return info
