"""Connection clients for Home Assistant."""

from ha_ops_mcp.connections.database import (
    DatabaseBackend,
    MariaDbBackend,
    PostgresBackend,
    SqliteBackend,
    create_backend,
)
from ha_ops_mcp.connections.rest import RestClient, RestClientError
from ha_ops_mcp.connections.websocket import WebSocketClient, WebSocketError

__all__ = [
    "DatabaseBackend",
    "MariaDbBackend",
    "PostgresBackend",
    "RestClient",
    "RestClientError",
    "SqliteBackend",
    "WebSocketClient",
    "WebSocketError",
    "create_backend",
]
