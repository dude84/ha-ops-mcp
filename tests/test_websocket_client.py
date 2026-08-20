"""Tests for the WebSocket client connection-liveness check and
pending-future cleanup on disconnect."""

from __future__ import annotations

import asyncio

import pytest
from websockets.exceptions import ConnectionClosedError
from websockets.protocol import State

from ha_ops_mcp.connections.websocket import WebSocketClient, WebSocketError


class _FakeConn:
    def __init__(self, state: State) -> None:
        self.state = state


class _ClosingConn:
    """Async-iterates straight into a ConnectionClosed, like a dropped socket."""

    def __aiter__(self) -> _ClosingConn:
        return self

    async def __anext__(self) -> str:
        raise ConnectionClosedError(None, None)


class _CleanCloseConn:
    """Async iterator that ends immediately (server closed with 1000/1001)."""

    def __aiter__(self) -> _CleanCloseConn:
        return self

    async def __anext__(self) -> str:
        raise StopAsyncIteration


def _client() -> WebSocketClient:
    return WebSocketClient("http://homeassistant:8123", "token")


def test_alive_when_open() -> None:
    """An OPEN connection must be reported alive.

    Regression: str(State.OPEN) == '1' on Python 3.11+ (IntEnum.__str__
    change), so the old str(state).endswith('OPEN') check always returned
    False and forced a reconnect on every command.
    """
    client = _client()
    client._conn = _FakeConn(State.OPEN)  # type: ignore[assignment]
    assert client._is_conn_alive() is True


def test_not_alive_when_closed() -> None:
    client = _client()
    client._conn = _FakeConn(State.CLOSED)  # type: ignore[assignment]
    assert client._is_conn_alive() is False


def test_not_alive_when_connecting_or_closing() -> None:
    client = _client()
    client._conn = _FakeConn(State.CONNECTING)  # type: ignore[assignment]
    assert client._is_conn_alive() is False
    client._conn = _FakeConn(State.CLOSING)  # type: ignore[assignment]
    assert client._is_conn_alive() is False


def test_not_alive_when_no_conn() -> None:
    client = _client()
    assert client._is_conn_alive() is False


# ── Pending futures on disconnect ──────────────────────────────────────
#
# Regression: the listener used to exit on ConnectionClosed without failing
# self._pending, so every in-flight send_command stalled for its full
# timeout (30s default) instead of erroring immediately.


@pytest.mark.asyncio
async def test_listener_disconnect_fails_pending_futures() -> None:
    client = _client()
    client._conn = _ClosingConn()  # type: ignore[assignment]
    loop = asyncio.get_running_loop()
    fut1 = loop.create_future()
    fut2 = loop.create_future()
    client._pending[1] = fut1
    client._pending[2] = fut2

    await client._listen()

    assert client._pending == {}
    for fut in (fut1, fut2):
        assert fut.done()
        with pytest.raises(WebSocketError):
            fut.result()


@pytest.mark.asyncio
async def test_listener_clean_close_fails_pending_futures() -> None:
    """A clean server-side close (iterator exhausted) must fail waiters too."""
    client = _client()
    client._conn = _CleanCloseConn()  # type: ignore[assignment]
    fut = asyncio.get_running_loop().create_future()
    client._pending[5] = fut

    await client._listen()

    assert client._pending == {}
    with pytest.raises(WebSocketError):
        fut.result()


@pytest.mark.asyncio
async def test_listener_cancel_fails_pending_futures() -> None:
    """Reconnect cleanup cancels the listener — waiters must not hang."""

    class _BlockingConn:
        def __aiter__(self) -> _BlockingConn:
            return self

        async def __anext__(self) -> str:
            await asyncio.sleep(3600)
            return ""

    client = _client()
    client._conn = _BlockingConn()  # type: ignore[assignment]
    fut = asyncio.get_running_loop().create_future()
    client._pending[9] = fut

    task = asyncio.create_task(client._listen())
    await asyncio.sleep(0)  # let the listener start
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert client._pending == {}
    with pytest.raises(WebSocketError):
        fut.result()


def test_fail_pending_skips_done_futures() -> None:
    """Already-resolved futures must not get set_exception (would raise)."""
    loop = asyncio.new_event_loop()
    try:
        done_fut = loop.create_future()
        done_fut.set_result({"success": True})
        live_fut = loop.create_future()
        client = _client()
        client._pending = {1: done_fut, 2: live_fut}

        client._fail_pending("gone")

        assert client._pending == {}
        assert done_fut.result() == {"success": True}
        with pytest.raises(WebSocketError):
            live_fut.result()
    finally:
        loop.close()
