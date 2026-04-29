"""REST client for Home Assistant API."""

from __future__ import annotations

import logging
from typing import Any

import aiohttp

logger = logging.getLogger(__name__)


class RestClientError(Exception):
    """Raised when a REST API call fails."""

    def __init__(self, status: int, message: str) -> None:
        self.status = status
        super().__init__(f"HTTP {status}: {message}")


class RestClient:
    """Async HTTP client for the Home Assistant REST API.

    The startup lifespan enters this client via ``async with`` to open the
    initial aiohttp session. If that session dies mid-flight (long idle,
    network wobble, or an errant ``__aexit__`` on the singleton) every
    request method re-opens lazily via :meth:`_ensure_session` instead of
    raising "RestClient not initialized" and holding the whole MCP server
    hostage until someone restarts the addon.

    Usage::

        async with RestClient(base_url, token) as client:
            states = await client.get("/api/states")
    """

    def __init__(self, base_url: str, token: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._session: aiohttp.ClientSession | None = None

    async def __aenter__(self) -> RestClient:
        await self._ensure_session()
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._session:
            await self._session.close()
            self._session = None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """Return a live aiohttp session, creating one if missing or closed.

        Ordinarily the startup lifespan entered via ``__aenter__`` will have
        created the session. We still check every call because aiohttp
        sessions can close themselves on unrecoverable errors and we'd
        rather transparently reopen than force an addon restart.
        """
        if self._session is None or self._session.closed:
            if self._session is not None and self._session.closed:
                logger.info("RestClient session was closed — reopening lazily")
            self._session = aiohttp.ClientSession(
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Content-Type": "application/json",
                },
                timeout=aiohttp.ClientTimeout(total=30),
            )
        return self._session

    def _url(self, path: str) -> str:
        """Build full URL from base + path."""
        return f"{self._base_url}{path}"

    async def get(self, path: str) -> Any:
        """GET request, returns parsed JSON."""
        session = await self._ensure_session()
        async with session.get(self._url(path)) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RestClientError(resp.status, text)
            return await resp.json()

    async def get_text(self, path: str) -> str:
        """GET request, returns raw text."""
        session = await self._ensure_session()
        async with session.get(self._url(path)) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RestClientError(resp.status, text)
            return await resp.text()

    async def post(self, path: str, data: dict[str, Any] | None = None) -> Any:
        """POST request with JSON body, returns parsed JSON."""
        session = await self._ensure_session()
        async with session.post(self._url(path), json=data or {}) as resp:
            if resp.status not in (200, 201):
                text = await resp.text()
                raise RestClientError(resp.status, text)
            return await resp.json()

    async def post_text(self, path: str, data: dict[str, Any] | None = None) -> str:
        """POST request with JSON body, returns raw text response.

        Use for endpoints that return text/plain (notably /api/template,
        which renders Jinja and returns the result as a plain string).
        """
        session = await self._ensure_session()
        async with session.post(self._url(path), json=data or {}) as resp:
            if resp.status not in (200, 201):
                text = await resp.text()
                raise RestClientError(resp.status, text)
            return await resp.text()

    async def delete(self, path: str) -> Any:
        """DELETE request, returns parsed JSON."""
        session = await self._ensure_session()
        async with session.delete(self._url(path)) as resp:
            if resp.status != 200:
                text = await resp.text()
                raise RestClientError(resp.status, text)
            return await resp.json()
