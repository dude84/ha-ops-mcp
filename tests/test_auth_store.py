"""Tests for OAuth persistent store."""

from __future__ import annotations

import time
from pathlib import Path

from ha_ops_mcp.auth.store import OAuthStore


def test_store_creates_empty(tmp_path: Path):
    store = OAuthStore(tmp_path / "oauth.json")
    assert store.clients == {}
    assert store.auth_codes == {}
    assert store.access_tokens == {}
    assert store.refresh_tokens == {}


def test_store_persists_and_reloads(tmp_path: Path):
    path = tmp_path / "oauth.json"
    store = OAuthStore(path)
    store.clients["c1"] = {"client_id": "c1", "client_name": "test"}
    store.access_tokens["t1"] = {
        "token": "t1",
        "client_id": "c1",
        "scopes": [],
        "expires_at": int(time.time()) + 3600,
    }
    store.save()

    # Reload from disk
    store2 = OAuthStore(path)
    assert store2.clients["c1"]["client_name"] == "test"
    assert "t1" in store2.access_tokens


def test_store_cleanup_removes_expired(tmp_path: Path):
    path = tmp_path / "oauth.json"

    # Write data with expired entries
    import json
    past = time.time() - 100
    data = {
        "clients": {"c1": {"client_id": "c1"}},
        "auth_codes": {"old": {"code": "old", "expires_at": past}},
        "access_tokens": {"dead": {"token": "dead", "expires_at": past}},
        "refresh_tokens": {"stale": {"token": "stale", "expires_at": past}},
    }
    path.write_text(json.dumps(data))

    store = OAuthStore(path)
    assert "old" not in store.auth_codes
    assert "dead" not in store.access_tokens
    assert "stale" not in store.refresh_tokens
    # Client survives (no expiry on clients)
    assert "c1" in store.clients


def test_store_atomic_write(tmp_path: Path):
    path = tmp_path / "oauth.json"
    store = OAuthStore(path)
    store.clients["c1"] = {"client_id": "c1"}
    store.save()

    # Verify no .tmp file left behind
    assert not (tmp_path / "oauth.tmp").exists()
    assert path.exists()


def test_store_handles_corrupted_file(tmp_path: Path):
    path = tmp_path / "oauth.json"
    path.write_text("not valid json {{{")

    store = OAuthStore(path)
    assert store.clients == {}
