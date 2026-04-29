"""Ephemeral transaction/savepoint rollback system.

Provides per-step undo within operations and full-operation rollback.
Undo entries are in-memory for the MCP session duration — no disk backup
for small mutations. Persistent backups are handled separately by BackupManager
for heavy/destructive operations only.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class UndoType(Enum):
    FILE = "file"
    DASHBOARD = "dashboard"
    ENTITY = "entity"
    DB_ROWS = "db_rows"
    SERVICE_CALL = "service_call"


@dataclass
class UndoEntry:
    """The minimum state needed to reverse a single step."""

    type: UndoType
    description: str
    data: dict[str, Any]
    """Type-specific undo data. Examples:
    - FILE: {"path": "/config/configuration.yaml", "content": "old content..."}
    - DASHBOARD: {"dashboard_id": "overview", "config": {...old config...}}
    - ENTITY: {"entity_id": "sensor.x", "state": {...old registry entry...}}
    - DB_ROWS: {"table": "states", "sql": "INSERT INTO ...", "params": [...]}
    - SERVICE_CALL: {"domain": "light", "service": "turn_off", "data": {...}}
    """


@dataclass
class Savepoint:
    """A named checkpoint within a transaction."""

    id: str
    name: str
    undo: UndoEntry
    created_at: float
    rolled_back: bool = False


@dataclass
class Transaction:
    """A group of savepoints that can be rolled back individually or together."""

    id: str
    operation: str
    savepoints: list[Savepoint] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    committed: bool = False
    token_id: str | None = None  # optional: the ConfirmationToken that authorized this

    def savepoint(self, name: str, undo: UndoEntry) -> Savepoint:
        """Record a savepoint with its undo entry.

        Args:
            name: Human-readable name for this step (e.g. "write_config").
            undo: The UndoEntry that can reverse this step.

        Returns:
            The created Savepoint.
        """
        sp = Savepoint(
            id=uuid.uuid4().hex[:12],
            name=name,
            undo=undo,
            created_at=time.time(),
        )
        self.savepoints.append(sp)
        logger.debug("Savepoint '%s' recorded in transaction '%s'", name, self.operation)
        return sp

    def get_undo_stack(self, since: str | None = None) -> list[Savepoint]:
        """Get savepoints to undo, in reverse order.

        Args:
            since: If provided, only return savepoints from this name onward.
                   If None, returns all non-rolled-back savepoints.

        Returns:
            Savepoints in reverse chronological order (newest first).
        """
        active = [sp for sp in self.savepoints if not sp.rolled_back]
        if since:
            idx = next(
                (i for i, sp in enumerate(active) if sp.name == since or sp.id == since),
                None,
            )
            if idx is not None:
                active = active[idx:]
        return list(reversed(active))

    @property
    def active_savepoints(self) -> list[Savepoint]:
        return [sp for sp in self.savepoints if not sp.rolled_back]


class RollbackManager:
    """Manages ephemeral transactions with savepoints for the MCP session.

    Each operation (tool call, recipe step) creates a transaction. Within that
    transaction, individual steps create savepoints. Rollback can target a single
    savepoint or the entire transaction.

    Undo entries stay in memory — the actual rollback execution is delegated to
    the caller, since it depends on the undo type (write a file, call an API, etc.).
    """

    def __init__(self) -> None:
        self._transactions: dict[str, Transaction] = {}
        self._active_transaction: Transaction | None = None

    def begin(
        self, operation: str, token_id: str | None = None
    ) -> Transaction:
        """Start a new transaction for an operation.

        Args:
            operation: Name of the operation (e.g. "config_apply", "recipe:ghost_cleanup").
            token_id: Optional ConfirmationToken id that authorized this transaction.
                      Enables correlation between pending tokens and applied transactions.

        Returns:
            The new Transaction.
        """
        txn = Transaction(
            id=uuid.uuid4().hex[:16],
            operation=operation,
            token_id=token_id,
        )
        self._transactions[txn.id] = txn
        self._active_transaction = txn
        logger.debug("Transaction '%s' started for operation '%s'", txn.id, operation)
        return txn

    def commit(self, txn_id: str) -> None:
        """Mark a transaction as committed (successful).

        Committed transactions keep their savepoints for potential rollback,
        but signal that the operation completed normally.
        """
        txn = self._transactions.get(txn_id)
        if txn:
            txn.committed = True
            if self._active_transaction and self._active_transaction.id == txn_id:
                self._active_transaction = None
            logger.debug("Transaction '%s' committed", txn_id)

    def get_transaction(self, txn_id: str) -> Transaction | None:
        """Get a transaction by ID."""
        return self._transactions.get(txn_id)

    @property
    def active(self) -> Transaction | None:
        """The currently active (uncommitted) transaction, if any."""
        return self._active_transaction

    def rollback_savepoint(self, txn_id: str, savepoint_id: str) -> UndoEntry:
        """Mark a single savepoint as rolled back and return its undo entry.

        The caller is responsible for actually executing the undo.

        Returns:
            The UndoEntry to execute.

        Raises:
            KeyError: If transaction or savepoint not found.
            ValueError: If savepoint was already rolled back.
        """
        txn = self._transactions.get(txn_id)
        if not txn:
            raise KeyError(f"Transaction {txn_id} not found")

        sp = next((s for s in txn.savepoints if s.id == savepoint_id), None)
        if not sp:
            raise KeyError(f"Savepoint {savepoint_id} not found in transaction {txn_id}")
        if sp.rolled_back:
            raise ValueError(f"Savepoint {savepoint_id} already rolled back")

        sp.rolled_back = True
        logger.debug("Savepoint '%s' rolled back in transaction '%s'", sp.name, txn.operation)
        return sp.undo

    def rollback_transaction(self, txn_id: str) -> list[UndoEntry]:
        """Roll back all active savepoints in a transaction (newest first).

        Returns:
            List of UndoEntry objects to execute, in reverse order.

        Raises:
            KeyError: If transaction not found.
        """
        txn = self._transactions.get(txn_id)
        if not txn:
            raise KeyError(f"Transaction {txn_id} not found")

        undos: list[UndoEntry] = []
        for sp in reversed(txn.savepoints):
            if not sp.rolled_back:
                sp.rolled_back = True
                undos.append(sp.undo)

        if self._active_transaction and self._active_transaction.id == txn_id:
            self._active_transaction = None

        logger.debug(
            "Transaction '%s' fully rolled back (%d savepoints)", txn.operation, len(undos)
        )
        return undos

    def list_transactions(self, include_committed: bool = False) -> list[Transaction]:
        """List transactions, optionally including committed ones."""
        txns = list(self._transactions.values())
        if not include_committed:
            txns = [t for t in txns if not t.committed]
        return sorted(txns, key=lambda t: t.created_at, reverse=True)

    def discard(self, txn_id: str) -> None:
        """Remove a transaction entirely (e.g. after successful rollback)."""
        self._transactions.pop(txn_id, None)
        if self._active_transaction and self._active_transaction.id == txn_id:
            self._active_transaction = None
