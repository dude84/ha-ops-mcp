"""Two-phase confirmation tokens for mutating operations.

Tokens bind a preview to an apply: the preview stores the proposed
content/patch in the token, and the apply writes exactly that. Single-use
(consumed on first apply). No expiry — staleness is caught by each tool's
own context-match / structural-validation checks at apply time, so a
time-based cap adds friction without matching value.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any


@dataclass
class ConfirmationToken:
    id: str
    action: str
    details: dict[str, Any]
    created_at: float
    consumed: bool = False


class TokenConsumedError(Exception):
    pass


class TokenNotFoundError(Exception):
    pass


class SafetyManager:
    """Manages confirmation tokens for two-phase mutating operations.

    Tokens are in-memory and single-use. They live until consumed or the
    session ends (addon restart clears the dict).
    """

    def __init__(self) -> None:
        self._tokens: dict[str, ConfirmationToken] = {}

    def create_token(
        self,
        action: str,
        details: dict[str, Any],
    ) -> ConfirmationToken:
        """Create a new confirmation token.

        Args:
            action: The operation name (e.g. "config_apply").
            details: Arbitrary details about the pending operation.

        Returns:
            The created ConfirmationToken.
        """
        token = ConfirmationToken(
            id=uuid.uuid4().hex,
            action=action,
            details=details,
            created_at=time.time(),
        )
        self._tokens[token.id] = token
        return token

    def validate_token(self, token_id: str) -> ConfirmationToken:
        """Validate that a token exists and hasn't been used.

        Raises:
            TokenNotFoundError: Token doesn't exist.
            TokenConsumedError: Token was already used.
        """
        token = self._tokens.get(token_id)
        if token is None:
            raise TokenNotFoundError(f"Token {token_id} not found")
        if token.consumed:
            raise TokenConsumedError(f"Token {token_id} already consumed")
        return token

    def consume_token(self, token_id: str) -> None:
        """Mark a token as consumed after the operation completes."""
        token = self.validate_token(token_id)
        token.consumed = True

    def get_token(self, token_id: str) -> ConfirmationToken:
        """Get a token without validation (for inspection)."""
        token = self._tokens.get(token_id)
        if token is None:
            raise TokenNotFoundError(f"Token {token_id} not found")
        return token

    def list_tokens(
        self, include_consumed: bool = False
    ) -> list[ConfirmationToken]:
        """Enumerate known tokens.

        Returns unconsumed tokens by default. Useful for the Overview
        count and the MCP flow when answering "what's currently staged?".
        """
        results = []
        for token in self._tokens.values():
            if token.consumed and not include_consumed:
                continue
            results.append(token)
        results.sort(key=lambda t: t.created_at, reverse=True)
        return results
