"""Persistent JSON store for OAuth state (clients, tokens, codes)."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_EMPTY: dict[str, dict[str, Any]] = {
    "clients": {},
    "auth_codes": {},
    "access_tokens": {},
    "refresh_tokens": {},
}


class OAuthStore:
    """JSON-file persistence for OAuth state.

    Loaded into memory on init, flushed to disk on every write.
    Expired tokens/codes are cleaned on load.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._data = self._load()
        self._cleanup()

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self._path.exists():
            return {k: dict(v) for k, v in _EMPTY.items()}
        try:
            raw: dict[str, dict[str, Any]] = json.loads(self._path.read_text())
            # Ensure all expected keys exist
            for key in _EMPTY:
                raw.setdefault(key, {})
            return raw
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("OAuth store corrupted, starting fresh: %s", e)
            return {k: dict(v) for k, v in _EMPTY.items()}

    def save(self) -> None:
        """Atomic write: write to .tmp, then rename."""
        tmp = self._path.with_suffix(".tmp")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(self._data, indent=2, default=str))
        tmp.rename(self._path)

    def _cleanup(self) -> None:
        """Remove expired auth codes and access tokens on load."""
        now = time.time()
        changed = False
        for code, data in list(self._data["auth_codes"].items()):
            if data.get("expires_at", 0) < now:
                del self._data["auth_codes"][code]
                changed = True
        for token, data in list(self._data["access_tokens"].items()):
            exp = data.get("expires_at")
            if exp is not None and exp < now:
                del self._data["access_tokens"][token]
                changed = True
        for token, data in list(self._data["refresh_tokens"].items()):
            exp = data.get("expires_at")
            if exp is not None and exp < now:
                del self._data["refresh_tokens"][token]
                changed = True
        if changed:
            self.save()

    @property
    def clients(self) -> dict[str, Any]:
        return self._data["clients"]

    @property
    def auth_codes(self) -> dict[str, Any]:
        return self._data["auth_codes"]

    @property
    def access_tokens(self) -> dict[str, Any]:
        return self._data["access_tokens"]

    @property
    def refresh_tokens(self) -> dict[str, Any]:
        return self._data["refresh_tokens"]
