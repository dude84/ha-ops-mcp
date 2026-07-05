"""Tests for the WebSocket client connection-liveness check."""

from __future__ import annotations

from websockets.protocol import State

from ha_ops_mcp.connections.websocket import WebSocketClient


class _FakeConn:
    def __init__(self, state: State) -> None:
        self.state = state


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
