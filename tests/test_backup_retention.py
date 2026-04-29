"""BackupManager retention — age cap, per-type ceiling, atomic manifest
rewrite, dry-run neutrality, legacy-dir warning.

Regression: `_gaps/session_gaps_2026-04-16.md` §6 + the backup-lifecycle
backlog item closed by v0.18.0.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from ha_ops_mcp.safety.backup import BackupManager


@pytest.fixture
def fresh_backup_dir(tmp_path: Path) -> Path:
    return tmp_path / "backups"


def _manifest_entries(backup_dir: Path) -> list[dict[str, Any]]:
    manifest = backup_dir / "manifest.jsonl"
    if not manifest.exists():
        return []
    out = []
    for line in manifest.read_text().splitlines():
        if line.strip():
            out.append(json.loads(line))
    return out


async def _seed(mgr: BackupManager, source: Path, n: int) -> None:
    """Seed N config-type backups spread over the last N days."""
    # Use internal helpers so we can control the timestamp precisely.
    for i in range(n):
        source.write_text(f"content {i}\n")
        await mgr.backup_file(source, operation=f"seed_{i}")


@pytest.mark.asyncio
async def test_prune_by_age_drops_old_entries(fresh_backup_dir: Path, tmp_path: Path):
    mgr = BackupManager(fresh_backup_dir, max_age_days=7, max_per_type=1000)
    src = tmp_path / "target.yaml"

    # Seed one fresh + one old entry, forge the old one by editing the manifest.
    await _seed(mgr, src, 1)
    entries = _manifest_entries(fresh_backup_dir)
    assert len(entries) == 1

    # Forge an old entry 30 days ago.
    old_ts = (datetime.now(UTC) - timedelta(days=30)).isoformat()
    old_file = fresh_backup_dir / "config" / "old.bak"
    old_file.write_text("ancient")
    forged = {
        "id": "config_old",
        "timestamp": old_ts,
        "type": "config",
        "source": str(src),
        "backup_path": str(old_file),
        "operation": "seed_old",
        "size_bytes": old_file.stat().st_size,
    }
    with open(fresh_backup_dir / "manifest.jsonl", "a") as f:
        f.write(json.dumps(forged) + "\n")

    # Manual prune (max_age_days=7 applies).
    result = await mgr.prune(dry_run=False)
    assert result["count"] == 1
    assert not old_file.exists()
    assert any(e["id"] == "config_old" for e in result["deleted"])
    # Fresh entry remains.
    remaining = _manifest_entries(fresh_backup_dir)
    assert len(remaining) == 1
    assert remaining[0]["id"] != "config_old"


@pytest.mark.asyncio
async def test_prune_by_type_count_keeps_newest(fresh_backup_dir: Path, tmp_path: Path):
    mgr = BackupManager(fresh_backup_dir, max_age_days=365, max_per_type=3)
    src = tmp_path / "target.yaml"

    await _seed(mgr, src, 5)  # 5 entries, cap at 3 → 2 should be pruned
    # Each write triggers auto-prune, but the 5th write is the final state.
    entries = _manifest_entries(fresh_backup_dir)
    # Write-time prune keeps the per-type count at or below the cap.
    assert len(entries) <= 3


@pytest.mark.asyncio
async def test_prune_dry_run_does_not_touch_disk(
    fresh_backup_dir: Path, tmp_path: Path
):
    mgr = BackupManager(fresh_backup_dir, max_age_days=365, max_per_type=1000)
    src = tmp_path / "target.yaml"
    await _seed(mgr, src, 2)

    # Force a clear_all dry-run; nothing should disappear on disk.
    before = set((fresh_backup_dir / "config").iterdir())
    before_manifest = (fresh_backup_dir / "manifest.jsonl").read_text()

    preview = await mgr.prune(dry_run=True, clear_all=True)
    assert preview["count"] == 2
    assert "would_delete" in preview

    assert set((fresh_backup_dir / "config").iterdir()) == before
    assert (fresh_backup_dir / "manifest.jsonl").read_text() == before_manifest


@pytest.mark.asyncio
async def test_prune_clear_all_respects_type_filter(
    fresh_backup_dir: Path, tmp_path: Path
):
    mgr = BackupManager(fresh_backup_dir, max_age_days=365, max_per_type=1000)
    src = tmp_path / "target.yaml"
    await _seed(mgr, src, 2)  # 2 config backups
    await mgr.backup_dashboard(
        "dash_a", {"title": "A"}, operation="seed_dash"
    )

    result = await mgr.prune(clear_all=True, type_filter="config")
    assert result["count"] == 2
    assert all(e["type"] == "config" for e in result["deleted"])

    # Dashboard backup survived.
    remaining = _manifest_entries(fresh_backup_dir)
    assert len(remaining) == 1
    assert remaining[0]["type"] == "dashboard"


@pytest.mark.asyncio
async def test_prune_older_than_days_override(
    fresh_backup_dir: Path, tmp_path: Path
):
    mgr = BackupManager(fresh_backup_dir, max_age_days=365, max_per_type=1000)
    src = tmp_path / "target.yaml"
    await _seed(mgr, src, 1)

    # Forge an entry 10 days old.
    ten_days_ago = (datetime.now(UTC) - timedelta(days=10)).isoformat()
    old_file = fresh_backup_dir / "config" / "ten_days.bak"
    old_file.write_text("x")
    with open(fresh_backup_dir / "manifest.jsonl", "a") as f:
        f.write(json.dumps({
            "id": "config_10d",
            "timestamp": ten_days_ago,
            "type": "config",
            "source": str(src),
            "backup_path": str(old_file),
            "operation": "forged",
            "size_bytes": 1,
        }) + "\n")

    # Default max_age_days is 365 — normal prune preserves both.
    result = await mgr.prune(dry_run=True)
    assert result["count"] == 0

    # Override to 7 days — 10-day entry is now in scope.
    result = await mgr.prune(dry_run=True, older_than_days=7)
    assert result["count"] == 1
    assert result["would_delete"][0]["id"] == "config_10d"


@pytest.mark.asyncio
async def test_manifest_rewrite_atomic_tmp_rename(
    fresh_backup_dir: Path, tmp_path: Path
):
    """After a prune the manifest has ONLY survivors — no partial writes."""
    mgr = BackupManager(fresh_backup_dir, max_age_days=365, max_per_type=1000)
    src = tmp_path / "target.yaml"
    await _seed(mgr, src, 3)

    await mgr.prune(clear_all=True, type_filter="config")
    manifest = fresh_backup_dir / "manifest.jsonl"
    # Tmp file should NOT linger after the rewrite.
    assert not (fresh_backup_dir / "manifest.jsonl.tmp").exists()
    # Remaining manifest is a valid JSONL containing survivors only.
    assert manifest.exists()
    assert manifest.read_text() == ""  # all entries pruned


@pytest.mark.asyncio
async def test_init_time_prune_runs_once(fresh_backup_dir: Path, tmp_path: Path):
    """Startup prune catches up on accumulated history from before retention
    was enforced (pre-v0.18.0 deploys)."""
    # Seed manifest with a very old entry before BackupManager exists.
    fresh_backup_dir.mkdir(parents=True, exist_ok=True)
    (fresh_backup_dir / "config").mkdir(parents=True, exist_ok=True)
    old_file = fresh_backup_dir / "config" / "very_old.bak"
    old_file.write_text("legacy")
    old_ts = (datetime.now(UTC) - timedelta(days=90)).isoformat()
    with open(fresh_backup_dir / "manifest.jsonl", "w") as f:
        f.write(json.dumps({
            "id": "config_very_old",
            "timestamp": old_ts,
            "type": "config",
            "source": "/config/x.yaml",
            "backup_path": str(old_file),
            "operation": "pre_retention",
            "size_bytes": 6,
        }) + "\n")

    # Instantiating BackupManager with a 30-day max runs the startup prune.
    BackupManager(fresh_backup_dir, max_age_days=30, max_per_type=1000)

    # The very-old entry is gone.
    remaining = _manifest_entries(fresh_backup_dir)
    assert remaining == []
    assert not old_file.exists()


def test_legacy_dir_warning_emitted(
    fresh_backup_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    """§6 — legacy /config/ha-ops-backups with data triggers a WARNING
    when the configured backup_dir is elsewhere.

    We monkeypatch the module-level constant so the test doesn't depend
    on whether the actual /config path exists on the test host.
    """
    import ha_ops_mcp.safety.backup as backup_module

    fake_legacy = tmp_path / "fake_legacy"
    fake_legacy.mkdir()
    (fake_legacy / "config").mkdir()
    (fake_legacy / "manifest.jsonl").write_text(
        json.dumps({"id": "x"}) + "\n"
    )

    monkeypatch.setattr(backup_module, "_LEGACY_BACKUP_DIR", fake_legacy)

    with caplog.at_level(logging.WARNING, logger=backup_module.logger.name):
        BackupManager(fresh_backup_dir)

    assert any(
        "Legacy backup directory detected" in r.message
        for r in caplog.records
    )


def test_legacy_dir_no_warning_when_empty(
    fresh_backup_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
):
    import ha_ops_mcp.safety.backup as backup_module

    fake_legacy = tmp_path / "fake_legacy_empty"
    fake_legacy.mkdir()  # exists but empty — no data to warn about
    monkeypatch.setattr(backup_module, "_LEGACY_BACKUP_DIR", fake_legacy)

    with caplog.at_level(logging.WARNING, logger=backup_module.logger.name):
        BackupManager(fresh_backup_dir)

    assert not any(
        "Legacy backup directory detected" in r.message
        for r in caplog.records
    )
