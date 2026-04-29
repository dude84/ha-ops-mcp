"""YAML utilities using ruamel.yaml for comment-preserving round-trips."""

from __future__ import annotations

import io
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


def write_yaml(path: Path, data: Any, yaml: YAML | None = None) -> None:
    """Write YAML data back to a file, preserving comments if possible.

    Args:
        path: Target file path.
        data: The YAML data (CommentedMap/CommentedSeq from ruamel).
        yaml: The YAML instance from read_yaml(). If None, creates a new one.
    """
    if yaml is None:
        yaml = make_yaml()
    with open(path, "w") as f:
        yaml.dump(data, f)


def yaml_to_string(data: Any, yaml: YAML | None = None) -> str:
    """Serialize YAML data to a string."""
    if yaml is None:
        yaml = make_yaml()
    stream = io.StringIO()
    yaml.dump(data, stream)
    return stream.getvalue()
