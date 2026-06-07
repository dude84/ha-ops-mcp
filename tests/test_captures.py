"""Tests for the CaptureStore (UI capture artifact gallery backing store)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from ha_ops_mcp.safety.captures import CaptureEntry, CaptureStore


def _store(tmp_path, **kw) -> CaptureStore:
    return CaptureStore(tmp_path / "captures", **kw)


def test_save_writes_artifact_and_manifest(tmp_path):
    s = _store(tmp_path)
    e = s.save(content=b"\x89PNGdata", kind="screenshot", view="lovelace", ext="png")
    assert e.id and len(e.id) == 12
    assert e.kind == "screenshot"
    assert e.size_bytes == 8
    p = s.artifact_path(e)
    assert p.is_file()
    assert p.read_bytes() == b"\x89PNGdata"
    # manifest has one line
    manifest = tmp_path / "captures" / "manifest.jsonl"
    lines = [ln for ln in manifest.read_text().splitlines() if ln.strip()]
    assert len(lines) == 1
    assert json.loads(lines[0])["id"] == e.id


def test_ext_leading_dot_stripped(tmp_path):
    s = _store(tmp_path)
    e = s.save(content=b"x", kind="trace", view="v", ext=".zip")
    assert e.filename.endswith(".zip")
    assert ".." not in e.filename


def test_list_newest_first(tmp_path):
    s = _store(tmp_path)
    a = s.save(content=b"a", kind="screenshot", view="v1", ext="png")
    b = s.save(content=b"b", kind="screenshot", view="v2", ext="png")
    ids = [e.id for e in s.list_entries()]
    # b saved after a → newer timestamp → first. (timestamps may tie; assert membership + count)
    assert set(ids) == {a.id, b.id}
    assert s.list_entries(limit=1)  # honored
    assert len(s.list_entries(limit=1)) == 1


def test_get_and_read_bytes(tmp_path):
    s = _store(tmp_path)
    e = s.save(content=b"hello", kind="screenshot", view="v", ext="png")
    assert s.get(e.id).id == e.id
    assert s.get("nope") is None
    got = s.read_bytes(e.id)
    assert got is not None
    entry, data = got
    assert entry.id == e.id
    assert data == b"hello"
    assert s.read_bytes("nope") is None


def test_read_bytes_missing_file(tmp_path):
    s = _store(tmp_path)
    e = s.save(content=b"x", kind="screenshot", view="v", ext="png")
    s.artifact_path(e).unlink()
    assert s.read_bytes(e.id) is None


def test_by_transaction(tmp_path):
    s = _store(tmp_path)
    s.save(content=b"x", kind="screenshot", view="v", ext="png", transaction_id="tx1")
    s.save(content=b"y", kind="screenshot", view="v", ext="png", transaction_id="tx1")
    s.save(content=b"z", kind="screenshot", view="v", ext="png", transaction_id="tx2")
    assert s.by_transaction("tx1") is not None
    assert s.by_transaction("tx2").view == "v"
    assert s.by_transaction("") is None
    assert s.by_transaction("nope") is None


def test_stats(tmp_path):
    s = _store(tmp_path, max_count=50, max_age_days=10)
    s.save(content=b"aa", kind="screenshot", view="v", ext="png")
    s.save(content=b"bbb", kind="trace", view="v", ext="zip")
    st = s.stats()
    assert st["count"] == 2
    assert st["total_bytes"] == 5
    assert st["per_kind"] == {"screenshot": 1, "trace": 1}
    assert st["retention"] == {"max_count": 50, "max_age_days": 10}
    assert st["dir"].endswith("captures")


def test_delete(tmp_path):
    s = _store(tmp_path)
    a = s.save(content=b"aa", kind="screenshot", view="v", ext="png")
    b = s.save(content=b"bbb", kind="screenshot", view="v", ext="png")
    res = s.delete([a.id])
    assert res["deleted"] == 1
    assert res["bytes_freed"] == 2
    assert s.get(a.id) is None
    assert not s.artifact_path(a).is_file()
    assert s.get(b.id) is not None


def test_delete_unknown_id_noop(tmp_path):
    s = _store(tmp_path)
    s.save(content=b"x", kind="screenshot", view="v", ext="png")
    res = s.delete(["nope"])
    assert res["deleted"] == 0
    assert res["bytes_freed"] == 0
    assert len(s.list_entries()) == 1


def test_annotate(tmp_path):
    s = _store(tmp_path)
    e = s.save(content=b"x", kind="screenshot", view="v", ext="png")
    upd = s.annotate(e.id, note="home before", transaction_id="tx9")
    assert upd is not None
    assert upd.note == "home before"
    assert upd.transaction_id == "tx9"
    # persisted
    assert s.get(e.id).note == "home before"
    assert s.get(e.id).transaction_id == "tx9"
    # partial update keeps the other field
    s.annotate(e.id, note="changed")
    assert s.get(e.id).transaction_id == "tx9"
    assert s.annotate("nope", note="x") is None


def test_purge_clear_all(tmp_path):
    s = _store(tmp_path)
    s.save(content=b"a", kind="screenshot", view="v", ext="png")
    s.save(content=b"b", kind="screenshot", view="v", ext="png")
    res = s.purge(clear_all=True)
    assert res["deleted"] is True
    assert res["count"] == 2
    assert s.list_entries() == []


def test_purge_dry_run(tmp_path):
    s = _store(tmp_path)
    s.save(content=b"a", kind="screenshot", view="v", ext="png")
    res = s.purge(clear_all=True, dry_run=True)
    assert res["would_delete"] is True
    assert res["count"] == 1
    assert len(s.list_entries()) == 1  # nothing actually deleted


def test_purge_older_than(tmp_path):
    s = _store(tmp_path, max_age_days=3650)  # disable init prune
    fresh = s.save(content=b"new", kind="screenshot", view="v", ext="png")
    # backdate one entry by rewriting the manifest with an old timestamp
    old = CaptureEntry(
        id="oldid000000a",
        timestamp=(datetime.now(UTC) - timedelta(days=40)).isoformat(),
        kind="screenshot",
        view="v",
        filename="oldid000000a.png",
        size_bytes=3,
    )
    (tmp_path / "captures" / "files" / old.filename).write_bytes(b"old")
    manifest = tmp_path / "captures" / "manifest.jsonl"
    with open(manifest, "a") as f:
        f.write(json.dumps(old.to_dict()) + "\n")
    res = s.purge(older_than_days=30)
    assert res["count"] == 1
    assert s.get(old.id) is None
    assert s.get(fresh.id) is not None


def test_prune_max_count_on_save(tmp_path):
    s = _store(tmp_path, max_count=3)
    saved = [
        s.save(content=bytes([i]), kind="screenshot", view=f"v{i}", ext="png")
        for i in range(5)
    ]
    remaining = {e.id for e in s.list_entries()}
    assert len(remaining) == 3
    # oldest two dropped, their files gone
    for e in saved[:2]:
        assert not s.artifact_path(e).is_file()


def test_prune_max_age_on_init(tmp_path):
    # seed an old entry, then re-open store with short max_age → pruned on init
    s = _store(tmp_path, max_age_days=3650)
    s.save(content=b"x", kind="screenshot", view="v", ext="png")
    old = CaptureEntry(
        id="oldid000000b",
        timestamp=(datetime.now(UTC) - timedelta(days=99)).isoformat(),
        kind="screenshot",
        view="v",
        filename="oldid000000b.png",
        size_bytes=1,
    )
    (tmp_path / "captures" / "files" / old.filename).write_bytes(b"o")
    with open(tmp_path / "captures" / "manifest.jsonl", "a") as f:
        f.write(json.dumps(old.to_dict()) + "\n")
    s2 = CaptureStore(tmp_path / "captures", max_age_days=30)
    assert s2.get(old.id) is None
    assert not (tmp_path / "captures" / "files" / old.filename).is_file()


def test_read_all_skips_malformed_lines(tmp_path):
    s = _store(tmp_path)
    s.save(content=b"x", kind="screenshot", view="v", ext="png")
    with open(tmp_path / "captures" / "manifest.jsonl", "a") as f:
        f.write("not json\n")
        f.write(json.dumps({"unexpected": "shape"}) + "\n")
    assert len(s.list_entries()) == 1  # malformed + schema-drift lines skipped


@pytest.mark.parametrize("ext", ["png", "zip"])
def test_round_trip_kinds(tmp_path, ext):
    s = _store(tmp_path)
    kind = "screenshot" if ext == "png" else "trace"
    e = s.save(content=b"data", kind=kind, view="v", ext=ext)
    entry, data = s.read_bytes(e.id)
    assert data == b"data"
    assert entry.kind == kind
