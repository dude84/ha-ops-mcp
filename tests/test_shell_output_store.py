"""Tests for the shell-output store."""

from __future__ import annotations

from pathlib import Path

from ha_ops_mcp.safety.shell_output import ShellOutputStore


def test_save_and_read_roundtrip(tmp_path: Path):
    store = ShellOutputStore(tmp_path / "shell_output")
    entry = store.save(
        command="echo hi",
        cwd="/tmp",
        exit_code=0,
        duration_ms=12.3,
        stdout="hi\n",
        stderr="",
    )
    assert entry.exit_code == 0
    assert entry.command == "echo hi"
    assert entry.stdout_bytes == len("hi\n")
    assert entry.truncated is False

    got = store.read_output(entry.id)
    assert got == {"stdout": "hi\n", "stderr": ""}

    fetched = store.get(entry.id)
    assert fetched is not None
    assert fetched.id == entry.id


def test_read_output_unknown_id_returns_none(tmp_path: Path):
    store = ShellOutputStore(tmp_path / "shell_output")
    assert store.read_output("nope") is None
    assert store.get("nope") is None


def test_stream_cap_truncates_and_flags(tmp_path: Path):
    store = ShellOutputStore(tmp_path / "shell_output")
    big = "x" * (ShellOutputStore._STREAM_CAP + 500)
    entry = store.save(
        command="cat big",
        cwd="/tmp",
        exit_code=0,
        duration_ms=1.0,
        stdout=big,
        stderr="",
    )
    assert entry.truncated is True
    out = store.read_output(entry.id)
    assert out is not None
    assert out["stdout"].endswith("\n... (truncated)")
    assert len(out["stdout"]) <= ShellOutputStore._STREAM_CAP + len("\n... (truncated)")


def test_prune_enforces_max_count(tmp_path: Path):
    store = ShellOutputStore(tmp_path / "shell_output", max_count=3, max_age_days=30)
    ids = []
    for i in range(5):
        e = store.save(
            command=f"echo {i}", cwd="/tmp", exit_code=0,
            duration_ms=1.0, stdout=str(i), stderr="",
        )
        ids.append(e.id)
    remaining = {e.id for e in store.list_entries(limit=100)}
    assert len(remaining) == 3
    # Oldest two pruned, their files gone.
    assert store.read_output(ids[0]) is None
    assert store.read_output(ids[4]) is not None
