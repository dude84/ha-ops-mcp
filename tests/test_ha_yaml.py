"""Tests for the HA-compatible YAML loader."""

from __future__ import annotations

from pathlib import Path

import pytest

from ha_ops_mcp.utils.ha_yaml import HaYamlLoader, merge_packages


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    """Minimal HA config directory."""
    return tmp_path


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


# ── Basic loading ──────────────────────────────────────────────────────


def test_load_plain_yaml(config_dir: Path):
    _write(config_dir / "config.yaml", "name: Home\nvalue: 42\n")
    loader = HaYamlLoader(config_dir)
    result = loader.load(Path("config.yaml"))
    assert result.data == {"name": "Home", "value": 42}
    assert result.source_file == "config.yaml"
    assert result.issues == []


def test_load_missing_file_emits_issue(config_dir: Path):
    loader = HaYamlLoader(config_dir)
    result = loader.load(Path("missing.yaml"))
    assert result.data is None
    assert len(result.issues) == 1
    assert result.issues[0].code == "broken_include"


def test_load_outside_config_root_blocked(config_dir: Path, tmp_path: Path):
    # A file OUTSIDE the config root
    outside = tmp_path.parent / "outside.yaml"
    _write(outside, "x: 1\n")
    loader = HaYamlLoader(config_dir)
    result = loader.load(outside)
    assert result.data is None
    assert any(i.code == "path_traversal" for i in result.issues)


# ── !secret ────────────────────────────────────────────────────────────


def test_secret_resolution(config_dir: Path):
    _write(config_dir / "secrets.yaml", "api_key: supersecret\ndb_url: postgresql://localhost\n")
    _write(config_dir / "config.yaml", "recorder:\n  db_url: !secret db_url\n")
    loader = HaYamlLoader(config_dir)
    result = loader.load(Path("config.yaml"))
    assert result.data == {"recorder": {"db_url": "postgresql://localhost"}}
    assert result.issues == []


def test_missing_secret_emits_issue(config_dir: Path):
    _write(config_dir / "secrets.yaml", "other_key: foo\n")
    _write(config_dir / "config.yaml", "token: !secret api_key\n")
    loader = HaYamlLoader(config_dir)
    result = loader.load(Path("config.yaml"))
    assert result.data == {"token": None}
    assert any(i.code == "missing_secret" and i.target == "api_key" for i in result.issues)


def test_missing_secrets_file_is_tolerated(config_dir: Path):
    # No secrets.yaml at all; reference to one shouldn't crash
    _write(config_dir / "config.yaml", "token: !secret api_key\n")
    loader = HaYamlLoader(config_dir)
    result = loader.load(Path("config.yaml"))
    assert result.data == {"token": None}


# ── !env_var ───────────────────────────────────────────────────────────


def test_env_var_resolution(config_dir: Path, monkeypatch):
    monkeypatch.setenv("MY_VAR", "hello")
    _write(config_dir / "config.yaml", "greeting: !env_var MY_VAR\n")
    loader = HaYamlLoader(config_dir)
    result = loader.load(Path("config.yaml"))
    assert result.data == {"greeting": "hello"}


def test_env_var_with_default(config_dir: Path, monkeypatch):
    monkeypatch.delenv("NOT_SET", raising=False)
    _write(config_dir / "config.yaml", "value: !env_var NOT_SET fallback\n")
    loader = HaYamlLoader(config_dir)
    result = loader.load(Path("config.yaml"))
    assert result.data == {"value": "fallback"}


# ── !include ───────────────────────────────────────────────────────────


def test_include_file(config_dir: Path):
    _write(config_dir / "included.yaml", "foo: 1\nbar: 2\n")
    _write(config_dir / "config.yaml", "child: !include included.yaml\n")
    loader = HaYamlLoader(config_dir)
    result = loader.load(Path("config.yaml"))
    assert result.data == {"child": {"foo": 1, "bar": 2}}
    assert "included.yaml" in result.included_files


def test_include_missing_file_emits_issue(config_dir: Path):
    _write(config_dir / "config.yaml", "child: !include nope.yaml\n")
    loader = HaYamlLoader(config_dir)
    result = loader.load(Path("config.yaml"))
    assert result.data == {"child": None}
    assert any(i.code == "broken_include" for i in result.issues)


def test_nested_includes(config_dir: Path):
    _write(config_dir / "a.yaml", "b: !include b.yaml\n")
    _write(config_dir / "b.yaml", "c: !include c.yaml\n")
    _write(config_dir / "c.yaml", "value: 42\n")
    _write(config_dir / "config.yaml", "root: !include a.yaml\n")
    loader = HaYamlLoader(config_dir)
    result = loader.load(Path("config.yaml"))
    assert result.data == {"root": {"b": {"c": {"value": 42}}}}


def test_circular_include_detected(config_dir: Path):
    _write(config_dir / "a.yaml", "b: !include b.yaml\n")
    _write(config_dir / "b.yaml", "a: !include a.yaml\n")
    _write(config_dir / "config.yaml", "x: !include a.yaml\n")
    loader = HaYamlLoader(config_dir)
    result = loader.load(Path("config.yaml"))
    # Should surface a circular_include issue, not stack-overflow
    assert any(i.code == "circular_include" for i in result.issues)


def test_repeated_include_is_not_circular(config_dir: Path):
    """v0.8.10 — a file legitimately !include'd from multiple independent
    parents is NOT a cycle (no ancestor loop). HA supports this; the
    refindex used to false-positive it, producing thousands of bogus
    circular_include issues on ULM-style themed dashboards.
    """
    _write(config_dir / "shared.yaml", "value: 1\n")
    _write(config_dir / "parent1.yaml", "a: !include shared.yaml\n")
    _write(config_dir / "parent2.yaml", "b: !include shared.yaml\n")
    _write(config_dir / "config.yaml",
           "p1: !include parent1.yaml\np2: !include parent2.yaml\n")
    loader = HaYamlLoader(config_dir)
    result = loader.load(Path("config.yaml"))
    circular = [i for i in result.issues if i.code == "circular_include"]
    assert circular == [], f"Unexpected circular_include: {circular}"


def test_each_cycle_reported_once(config_dir: Path):
    """v0.8.10 — a given cycle should emit ONE issue, not one per entry path.
    The previous implementation emitted per-visit so themed dashboards
    with 400 card templates re-entering the same cycle got 400 issues.
    """
    _write(config_dir / "a.yaml", "b: !include b.yaml\n")
    _write(config_dir / "b.yaml", "a: !include a.yaml\n")
    # Multiple entry points into the same cycle
    _write(config_dir / "config.yaml",
           "e1: !include a.yaml\ne2: !include a.yaml\ne3: !include b.yaml\n")
    loader = HaYamlLoader(config_dir)
    result = loader.load(Path("config.yaml"))
    circular = [i for i in result.issues if i.code == "circular_include"]
    # One cycle exists (A ↔ B), so one issue
    assert len(circular) == 1


# ── !include_dir_* ──────────────────────────────────────────────────────


def test_include_dir_list(config_dir: Path):
    _write(config_dir / "parts/one.yaml", "n: 1\n")
    _write(config_dir / "parts/two.yaml", "n: 2\n")
    _write(config_dir / "config.yaml", "items: !include_dir_list parts\n")
    loader = HaYamlLoader(config_dir)
    result = loader.load(Path("config.yaml"))
    # Each file becomes an element in the list
    assert result.data == {"items": [{"n": 1}, {"n": 2}]}


def test_include_dir_merge_list(config_dir: Path):
    """Common automations.yaml: !include automations/ pattern.

    File iteration is alphabetical, so evening.yaml comes before morning.yaml.
    """
    _write(config_dir / "autos/morning.yaml", "- alias: morning\n  trigger: []\n")
    _write(
        config_dir / "autos/evening.yaml",
        "- alias: evening\n  trigger: []\n- alias: night\n  trigger: []\n",
    )
    _write(config_dir / "config.yaml", "automation: !include_dir_merge_list autos\n")
    loader = HaYamlLoader(config_dir)
    result = loader.load(Path("config.yaml"))
    aliases = [a["alias"] for a in result.data["automation"]]
    # evening.yaml (2 entries) before morning.yaml (1 entry) alphabetically
    assert aliases == ["evening", "night", "morning"]


def test_include_dir_named(config_dir: Path):
    _write(config_dir / "scripts/light_on.yaml", "sequence: []\n")
    _write(config_dir / "scripts/light_off.yaml", "sequence: []\n")
    _write(config_dir / "config.yaml", "script: !include_dir_named scripts\n")
    loader = HaYamlLoader(config_dir)
    result = loader.load(Path("config.yaml"))
    assert set(result.data["script"].keys()) == {"light_on", "light_off"}


def test_include_dir_merge_named(config_dir: Path):
    _write(config_dir / "lights/kitchen.yaml", "kitchen_on:\n  sequence: []\n")
    _write(config_dir / "lights/bedroom.yaml", "bedroom_on:\n  sequence: []\n")
    _write(config_dir / "config.yaml", "script: !include_dir_merge_named lights\n")
    loader = HaYamlLoader(config_dir)
    result = loader.load(Path("config.yaml"))
    assert set(result.data["script"].keys()) == {"kitchen_on", "bedroom_on"}


def test_include_dir_nonexistent_emits_issue(config_dir: Path):
    _write(config_dir / "config.yaml", "x: !include_dir_list nope\n")
    loader = HaYamlLoader(config_dir)
    result = loader.load(Path("config.yaml"))
    assert result.data == {"x": []}
    assert any(i.code == "broken_include" for i in result.issues)


# ── Path guard ─────────────────────────────────────────────────────────


def test_path_guard_invoked(config_dir: Path):
    _write(config_dir / "blocked.yaml", "secret: 42\n")
    _write(config_dir / "config.yaml", "x: !include blocked.yaml\n")

    def guard(p: Path) -> Path:
        if "blocked" in p.name:
            raise PermissionError("blocked by test")
        return p

    loader = HaYamlLoader(config_dir, path_guard=guard)
    result = loader.load(Path("config.yaml"))
    assert result.data == {"x": None}
    assert any(i.code == "path_guard_blocked" for i in result.issues)


# ── merge_packages ─────────────────────────────────────────────────────


def test_merge_packages_appends_lists():
    root = {
        "homeassistant": {
            "packages": {
                "lights": {"automation": [{"alias": "pkg_auto"}]},
            },
        },
        "automation": [{"alias": "root_auto"}],
    }
    merged, issues = merge_packages(root, loader=None)  # type: ignore[arg-type]
    aliases = [a["alias"] for a in merged["automation"]]
    assert aliases == ["root_auto", "pkg_auto"]
    assert issues == []


def test_merge_packages_merges_dicts():
    root = {
        "homeassistant": {
            "packages": {
                "sensors": {"script": {"pkg_script": {"sequence": []}}},
            },
        },
        "script": {"root_script": {"sequence": []}},
    }
    merged, issues = merge_packages(root, loader=None)  # type: ignore[arg-type]
    assert set(merged["script"].keys()) == {"root_script", "pkg_script"}


def test_merge_packages_conflicts_keep_root():
    root = {
        "homeassistant": {
            "packages": {
                "bad": {"script": {"shared_key": "from_pkg"}},
            },
        },
        "script": {"shared_key": "from_root"},
    }
    merged, issues = merge_packages(root, loader=None)  # type: ignore[arg-type]
    assert merged["script"]["shared_key"] == "from_root"
    assert any(i.code == "package_key_conflict" for i in issues)


def test_merge_packages_type_conflict_emits_issue():
    root = {
        "homeassistant": {
            "packages": {
                "mismatch": {"automation": {"not": "a list"}},
            },
        },
        "automation": [{"alias": "only_root"}],
    }
    merged, issues = merge_packages(root, loader=None)  # type: ignore[arg-type]
    # Root preserved
    assert merged["automation"] == [{"alias": "only_root"}]
    assert any(i.code == "package_type_conflict" for i in issues)


def test_merge_packages_no_packages_key():
    root = {"automation": [{"alias": "x"}]}
    merged, issues = merge_packages(root, loader=None)  # type: ignore[arg-type]
    assert merged == root
    assert issues == []


# ── Integration: package with !include ─────────────────────────────────


def test_package_with_included_automations(config_dir: Path):
    """!include paths in a package file resolve relative to the package file."""
    _write(
        config_dir / "packages/lights.yaml",
        "automation: !include_dir_merge_list automations_lights\n",
    )
    # Must live under packages/ since the include is relative to lights.yaml
    _write(
        config_dir / "packages/automations_lights/motion.yaml",
        "- alias: motion_light\n  trigger: []\n",
    )
    _write(
        config_dir / "config.yaml",
        "homeassistant:\n  packages: !include_dir_named packages\n"
        "automation:\n  - alias: root_auto\n    trigger: []\n",
    )
    loader = HaYamlLoader(config_dir)
    result = loader.load(Path("config.yaml"))
    merged, _ = merge_packages(result.data, loader)  # type: ignore[arg-type]
    aliases = [a["alias"] for a in merged["automation"]]
    assert "root_auto" in aliases
    assert "motion_light" in aliases
