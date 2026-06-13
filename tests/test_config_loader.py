"""Tests for config loader."""

from pathlib import Path

from ha_ops_mcp.config import load_config


def test_load_default_config_missing_file():
    """Loading with no config file returns defaults."""
    config = load_config(Path("/nonexistent/config.yaml"))
    assert config.ha.url == "http://homeassistant.local:8123"
    assert config.safety.require_confirmation is True
    assert config.safety.max_query_rows == 10000
    # Empty by default → server.py derives <backup.dir>/auth, a survival volume.
    assert config.auth.data_dir == ""


def test_load_config_from_file(tmp_path: Path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "ha:\n  url: http://myha:8123\n  token: abc123\n"
        "safety:\n  max_query_rows: 500\n"
    )
    config = load_config(config_file)
    assert config.ha.url == "http://myha:8123"
    assert config.ha.token == "abc123"
    assert config.safety.max_query_rows == 500


def test_env_var_override(tmp_path: Path, monkeypatch):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("ha:\n  url: http://original:8123\n")

    monkeypatch.setenv("HA_OPS_TOKEN", "env_token_123")
    monkeypatch.setenv("HA_OPS_URL", "http://envhost:8123")

    config = load_config(config_file)
    assert config.ha.token == "env_token_123"
    assert config.ha.url == "http://envhost:8123"


def test_resolve_token_from_file(tmp_path: Path):
    token_file = tmp_path / "token.txt"
    token_file.write_text("file_token_abc\n")

    config_file = tmp_path / "config.yaml"
    config_file.write_text(f"ha:\n  token_file: {token_file}\n")

    config = load_config(config_file)
    assert config.ha.resolve_token() == "file_token_abc"


def test_shell_output_config_defaults():
    from ha_ops_mcp.config import HaOpsConfig

    cfg = HaOpsConfig()
    assert cfg.shell_output.dir == ""
    assert cfg.shell_output.max_count == 500
    assert cfg.shell_output.max_age_days == 30


def test_shell_output_env_override(monkeypatch):
    from ha_ops_mcp.config import load_config

    monkeypatch.setenv("HA_OPS_SHELL_OUTPUT_MAX_COUNT", "12")
    monkeypatch.setenv("HA_OPS_SHELL_OUTPUT_DIR", "/tmp/so")
    cfg = load_config(None)
    assert cfg.shell_output.max_count == 12
    assert cfg.shell_output.dir == "/tmp/so"
