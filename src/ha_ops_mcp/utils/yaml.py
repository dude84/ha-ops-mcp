"""YAML utilities using ruamel.yaml for comment-preserving round-trips."""

from __future__ import annotations

import contextlib
import io
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML


def make_yaml() -> YAML:
    """Create a ruamel.yaml instance configured for round-trip."""
    yaml = YAML()
    yaml.preserve_quotes = True
    return yaml


def read_yaml(path: Path) -> tuple[Any, YAML]:
    """Read a YAML file preserving comments and formatting.

    Returns:
        Tuple of (parsed data, YAML instance for later write-back).
    """
    yaml = make_yaml()
    with open(path) as f:
        data = yaml.load(f)
    return data, yaml


def atomic_write_text(path: Path, content: str) -> None:
    """Write text to ``path`` atomically (tmp file + os.replace).

    HA (and its file-watching integrations) can read a config file at any
    moment — an in-place truncate-and-write briefly exposes a half-written
    file. Writing to a temp file in the same directory and renaming over
    the target makes the swap atomic on POSIX. Preserves the original
    file's permission bits when the file already exists.

    Args:
        path: Target file path.
        content: Full file content to write.
    """
    mode: int | None = None
    with contextlib.suppress(OSError):
        mode = stat.S_IMODE(path.stat().st_mode)

    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
        if mode is not None:
            os.chmod(tmp_name, mode)
        os.replace(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


def write_yaml(path: Path, data: Any, yaml: YAML | None = None) -> None:
    """Write YAML data back to a file, preserving comments if possible.

    The write is atomic: serialized to a string first, then swapped into
    place via :func:`atomic_write_text` so readers never see a partial file.

    Args:
        path: Target file path.
        data: The YAML data (CommentedMap/CommentedSeq from ruamel).
        yaml: The YAML instance from read_yaml(). If None, creates a new one.
    """
    if yaml is None:
        yaml = make_yaml()
    atomic_write_text(path, yaml_to_string(data, yaml))


def yaml_to_string(data: Any, yaml: YAML | None = None) -> str:
    """Serialize YAML data to a string."""
    if yaml is None:
        yaml = make_yaml()
    stream = io.StringIO()
    yaml.dump(data, stream)
    return stream.getvalue()
