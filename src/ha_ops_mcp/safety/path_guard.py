"""Path traversal prevention for config file operations."""

from __future__ import annotations

from pathlib import Path


class PathTraversalError(Exception):
    pass


class PathGuard:
    """Validates that file paths stay within the HA config root."""

    def __init__(self, config_root: Path) -> None:
        self._root = config_root.resolve()

    @property
    def config_root(self) -> Path:
        return self._root

    def validate(self, path: str | Path) -> Path:
        """Resolve a path and assert it's under config_root.

        Args:
            path: Absolute or relative path to validate. Relative paths
                  are resolved against config_root.

        Returns:
            The resolved absolute path.

        Raises:
            PathTraversalError: If the path escapes config_root.
        """
        p = Path(path)
        if not p.is_absolute():
            p = self._root / p

        resolved = p.resolve()

        if not (resolved == self._root or self._is_child(resolved, self._root)):
            raise PathTraversalError(
                f"Path '{path}' resolves to '{resolved}' which is outside "
                f"config root '{self._root}'"
            )

        return resolved

    @staticmethod
    def _is_child(child: Path, parent: Path) -> bool:
        """Check if child is a descendant of parent."""
        try:
            child.relative_to(parent)
            return True
        except ValueError:
            return False
