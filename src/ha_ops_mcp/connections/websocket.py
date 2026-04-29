"""WebSocket client for Home Assistant API."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import TYPE_CHECKING, Any

import websockets

if TYPE_CHECKING:
    from websockets.asyncio.client import ClientConnection

logger = logging.getLogger(__name__)


class WebSocketError(Exception):
    """Raised when a WebSocket command fails."""


class WebSocketClient:
    """Async WebSocket client for the Home Assistant WS API.

    Handles authentication and command/response matching by message ID.

    Usage::

        async with WebSocketClient(url, token) as ws:
            result = await ws.send_command("config/check_config")
    """

    def __init__(self, url: str, token: str) -> None:
        ws_url = url.rstrip("/").replace("http://", "ws://").replace("https://", "wss://")
        self._url = f"{ws_url}/api/websocket"
        self._token = token
        self._conn: ClientConnection | None = None
        self._msg_id = 0
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._listener_task: asyncio.Task[None] | None = None

    async def __aenter__(self) -> WebSocketClient:
        self._conn = await websockets.connect(self._url)
        await self._authenticate()
        self._listener_task = asyncio.create_task(self._listen())
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._listener_task:
            self._listener_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._listener_task
        if self._conn:
            await self._conn.close()
            self._conn = None

    async def _authenticate(self) -> None:
        """Complete the HA WebSocket auth handshake."""
        assert self._conn is not None
        # HA sends auth_required first
        try:
            raw = await self._conn.recv()
        except websockets.ConnectionClosed as e:
            raise WebSocketError(
                f"Connection closed before auth_required: {e}"
            ) from e
        msg = json.loads(raw)
        logger.debug("WS received: %s", msg)
        if msg.get("type") != "auth_required":
            raise WebSocketError(f"Expected auth_required, got: {msg.get('type')}")

        auth_msg = {"type": "auth", "access_token": self._token}
        logger.debug("WS sending auth (token len=%d)", len(self._token))
        await self._conn.send(json.dumps(auth_msg))

        try:
            raw = await self._conn.recv()
        except websockets.ConnectionClosed as e:
            raise WebSocketError(
                f"Connection closed after sending auth (token likely rejected): "
                f"close code={e.code}, reason={e.reason!r}"
            ) from e
        msg = json.loads(raw)
        logger.debug("WS received: %s", msg)
        if msg.get("type") == "auth_invalid":
            raise WebSocketError(
                f"Auth rejected: {msg.get('message', 'no details')}"
            )
        if msg.get("type") != "auth_ok":
            raise WebSocketError(f"Unexpected auth response: {msg.get('type')}")

        logger.info("WebSocket authenticated, HA version: %s", msg.get("ha_version"))

    async def _listen(self) -> None:
        """Background listener that routes responses to pending futures."""
        assert self._conn is not None
        try:
            async for raw in self._conn:
                msg = json.loads(raw)
                msg_id = msg.get("id")
                if msg_id is not None and msg_id in self._pending:
                    self._pending[msg_id].set_result(msg)
        except websockets.ConnectionClosed:
            logger.warning("WebSocket connection closed")
        except asyncio.CancelledError:
            return

    def _is_conn_alive(self) -> bool:
        """Check if the WebSocket connection is open and usable."""
        if self._conn is None:
            return False
        # websockets.ClientConnection has a 'state' attribute
        state = getattr(self._conn, "state", None)
        if state is None:
            return False
        # OPEN = 1; anything else is connecting/closing/closed
        return str(state).endswith("OPEN")

    async def _ensure_connected(self) -> None:
        """Reconnect if the connection is dead."""
        if self._is_conn_alive():
            return
        logger.info("WebSocket connection is dead, reconnecting...")
        # Clean up dead state
        if self._listener_task and not self._listener_task.done():
            self._listener_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._listener_task
        if self._conn is not None:
            with contextlib.suppress(Exception):
                await self._conn.close()
        self._conn = None
        self._listener_task = None
        # Reconnect
        self._conn = await websockets.connect(self._url)
        await self._authenticate()
        self._listener_task = asyncio.create_task(self._listen())

    async def send_command(
        self, msg_type: str, timeout: float = 30.0, **kwargs: Any
    ) -> dict[str, Any]:
        """Send a command and wait for the response.

        Args:
            msg_type: The HA WS message type (e.g. "config/check_config").
            timeout: Seconds to wait for a response.
            **kwargs: Additional fields to include in the message.

        Returns:
            The result dict from the response.
        """
        # Ensure we have a live connection — reconnect if needed
        try:
            await self._ensure_connected()
        except WebSocketError:
            raise
        except websockets.ConnectionClosed as e:
            raise WebSocketError(
                f"Connection closed during reconnect: close code={e.code}, "
                f"reason={e.reason!r}"
            ) from e
        except Exception as e:
            raise WebSocketError(f"Failed to reconnect WebSocket: {e}") from e

        assert self._conn is not None

        self._msg_id += 1
        msg_id = self._msg_id
        message = {"id": msg_id, "type": msg_type, **kwargs}

        future: asyncio.Future[dict[str, Any]] = asyncio.get_event_loop().create_future()
        self._pending[msg_id] = future

        try:
            try:
                await self._conn.send(json.dumps(message))
            except websockets.ConnectionClosed as e:
                raise WebSocketError(
                    f"Connection closed while sending {msg_type}: "
                    f"close code={e.code}, reason={e.reason!r}"
                ) from e
            try:
                result = await asyncio.wait_for(future, timeout=timeout)
            except TimeoutError as e:
                raise WebSocketError(
                    f"Timeout waiting for response to {msg_type}"
                ) from e
        finally:
            self._pending.pop(msg_id, None)

        # Special response types that don't use the standard result/success format
        response_type = result.get("type")
        if response_type == "pong":
            return {"pong": True}

        # Standard "result" response format
        if not result.get("success", False):
            error = result.get("error", {})
            raise WebSocketError(
                f"Command {msg_type} failed: {error.get('message', 'unknown')}"
            )

        ret: dict[str, Any] = result.get("result", result)
        return ret
