"""Safety layer — confirmation, guards, rollback, backup, audit."""

from ha_ops_mcp.safety.audit import AuditLog
from ha_ops_mcp.safety.backup import BackupManager
from ha_ops_mcp.safety.confirmation import (
    SafetyManager,
    TokenConsumedError,
    TokenNotFoundError,
)
from ha_ops_mcp.safety.path_guard import PathGuard, PathTraversalError
from ha_ops_mcp.safety.rollback import RollbackManager, Transaction, UndoEntry, UndoType

__all__ = [
    "AuditLog",
    "BackupManager",
    "PathGuard",
    "PathTraversalError",
    "RollbackManager",
    "SafetyManager",
    "TokenConsumedError",
    "TokenNotFoundError",
    "Transaction",
    "UndoEntry",
    "UndoType",
]
