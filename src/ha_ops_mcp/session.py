"""Per-request MCP session identity.

With a shared static Bearer token (default since v0.62.0) several MCP clients
routinely connect concurrently, and "who did this" stops being answerable from
transport auth alone. The tool dispatch wrapper stamps a short session key
into a ContextVar before invoking the handler; audit entries and confirmation
tokens read it from here so every mutation is attributable to the client
connection that made it — with zero signature changes at call sites.

The key is ephemeral (derived from the live server-session object, plus the
client-declared id when present): it distinguishes concurrent clients and
links a preview to its confirm, but is not stable across reconnects.
"""

from __future__ import annotations

from contextvars import ContextVar

UNKNOWN_SESSION = "-"

_current_session: ContextVar[str] = ContextVar(
    "haops_current_session", default=UNKNOWN_SESSION
)


def set_current_session(key: str) -> None:
    """Record the session key for the current tool-call task context."""
    _current_session.set(key or UNKNOWN_SESSION)


def get_current_session() -> str:
    """Session key for the current task context ("-" when unknown)."""
    return _current_session.get()
