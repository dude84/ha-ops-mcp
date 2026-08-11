"""Tests for the HA compatibility window (src/ha_ops_mcp/compat.py)."""

from __future__ import annotations

import pytest

from ha_ops_mcp.compat import (
    BUILT_AGAINST_HA,
    MAX_TESTED_HA,
    MIN_SUPPORTED_HA,
    check_ha_version,
    compat_info,
    parse_ha_version,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026.7.4", (2026, 7)),
        ("2026.7", (2026, 7)),
        ("2026.11.0", (2026, 11)),
        ("2026.8.0b3", (2026, 8)),  # beta suffix
        ("  2026.5.1  ", (2026, 5)),  # whitespace
        ("dev", None),
        ("", None),
        ("not.a.version", None),
    ],
)
def test_parse_ha_version(raw: str, expected: tuple[int, int] | None) -> None:
    assert parse_ha_version(raw) == expected


def test_built_against_is_inside_its_own_window() -> None:
    """The declared build target must satisfy the declared window.

    Guards the most likely maintenance slip: bumping BUILT_AGAINST_HA and
    forgetting MAX_TESTED_HA (or vice versa).
    """
    assert check_ha_version(BUILT_AGAINST_HA) is None
    assert parse_ha_version(BUILT_AGAINST_HA) == MAX_TESTED_HA
    assert MIN_SUPPORTED_HA <= MAX_TESTED_HA


def test_in_window_versions_produce_no_warning() -> None:
    assert check_ha_version("2026.6.0") is None
    assert check_ha_version("2026.7.3") is None
    assert check_ha_version("2026.8.1") is None


def test_too_old_warns() -> None:
    warning = check_ha_version("2026.4.1")
    assert warning is not None
    assert "older" in warning
    assert BUILT_AGAINST_HA in warning


def test_too_new_warns_without_claiming_breakage() -> None:
    warning = check_ha_version("2026.9.0")
    assert warning is not None
    assert "newer" in warning
    # A newer HA is untested, NOT known-broken — the message must not scare.
    assert "not a known failure" in warning


def test_unparseable_version_warns_but_does_not_crash() -> None:
    warning = check_ha_version("some-custom-build")
    assert warning is not None
    assert "Could not parse" in warning


def test_missing_version_is_silent() -> None:
    """No version means HA was unreachable — not a compatibility problem."""
    assert check_ha_version(None) is None
    assert check_ha_version("") is None


def test_compat_info_without_live_version() -> None:
    info = compat_info()
    assert info["built_against_ha"] == BUILT_AGAINST_HA
    assert "live_ha" not in info
    assert "in_window" not in info


def test_compat_info_with_in_window_version() -> None:
    info = compat_info("2026.8.1")
    assert info["live_ha"] == "2026.8.1"
    assert info["in_window"] is True
    assert "warning" not in info


def test_compat_info_with_out_of_window_version() -> None:
    info = compat_info("2026.9.1")
    assert info["in_window"] is False
    assert "warning" in info
