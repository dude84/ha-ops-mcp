"""Configuration loader for ha-ops-mcp.

Loads config.yaml with env var overrides (HA_OPS_* prefix), validates
required fields, and returns typed dataclasses.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML


@dataclass
class HaConfig:
    url: str = "http://homeassistant.local:8123"
    ws_url: str = ""  # WebSocket URL override (defaults to url if empty)
    token: str = ""
    token_file: str = ""

    def resolve_token(self) -> str:
        """Return the access token, reading from file if configured."""
        if self.token:
            return self.token
        if self.token_file:
            return Path(self.token_file).read_text().strip()
        return ""


@dataclass
class DatabaseConfig:
    auto_detect: bool = True
    url: str = ""


@dataclass
class FilesystemConfig:
    config_root: str = "/config"


@dataclass
class ServerConfig:
    transport: str = "stdio"
    host: str = "0.0.0.0"
    port: int = 8901


@dataclass
class SafetyConfig:
    require_confirmation: bool = True
    backup_on_write: bool = True
    max_query_rows: int = 10000


@dataclass
class BackupConfig:
    # Default moved off /config/ in v0.18.0 to HA's /backup volume so
    # backups don't live inside the directory they exist to protect.
    # Legacy /config/ha-ops-backups detected at startup with a warning.
    dir: str = "/backup/ha-ops-mcp"
    max_age_days: int = 30
    max_per_type: int = 100


@dataclass
class AuditConfig:
    # Empty string means "derive from backup.dir" for back-compat with deploys
    # that predate this option. server.py resolves and canonicalises the path.
    dir: str = ""


@dataclass
class AuthConfig:
    enabled: bool = True
    data_dir: str = "/data"  # addon persistent storage
    access_token_ttl: int = 3600  # 1 hour
    refresh_token_ttl: int = 2592000  # 30 days
    issuer_url: str = ""  # client-facing URL; defaults to http://{host}:{port}


@dataclass
class HaOpsConfig:
    ha: HaConfig = field(default_factory=HaConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    filesystem: FilesystemConfig = field(default_factory=FilesystemConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    backup: BackupConfig = field(default_factory=BackupConfig)
    audit: AuditConfig = field(default_factory=AuditConfig)
    auth: AuthConfig = field(default_factory=AuthConfig)


# Mapping from env var suffix to (config section, field name)
_ENV_MAP: dict[str, tuple[str, str]] = {
    "URL": ("ha", "url"),
    "WS_URL": ("ha", "ws_url"),
    "TOKEN": ("ha", "token"),
    "TOKEN_FILE": ("ha", "token_file"),
    "DB_URL": ("database", "url"),
    "DB_AUTO_DETECT": ("database", "auto_detect"),
    "CONFIG_ROOT": ("filesystem", "config_root"),
    "TRANSPORT": ("server", "transport"),
    "HOST": ("server", "host"),
    "PORT": ("server", "port"),
    "REQUIRE_CONFIRMATION": ("safety", "require_confirmation"),
    "BACKUP_ON_WRITE": ("safety", "backup_on_write"),
    "MAX_QUERY_ROWS": ("safety", "max_query_rows"),
    "BACKUP_DIR": ("backup", "dir"),
    "BACKUP_MAX_AGE_DAYS": ("backup", "max_age_days"),
    "BACKUP_MAX_PER_TYPE": ("backup", "max_per_type"),
    "AUDIT_DIR": ("audit", "dir"),
    "AUTH_ENABLED": ("auth", "enabled"),
    "AUTH_DATA_DIR": ("auth", "data_dir"),
    "AUTH_ISSUER_URL": ("auth", "issuer_url"),
}

# Env vars that carry list values — comma-separated at the env level.
_LIST_ENV_KEYS: frozenset[str] = frozenset()

_ENV_PREFIX = "HA_OPS_"


def _coerce_value(value: str, target_type: type[Any]) -> Any:
    """Coerce a string env var value to the target field type."""
    if target_type is bool:
        return value.lower() in ("true", "1", "yes")
    if target_type is int:
        return int(value)
    return value


def _apply_env_overrides(data: dict[str, Any]) -> dict[str, Any]:
    """Apply HA_OPS_* environment variable overrides to config data.

    List-valued env vars (see `_LIST_ENV_KEYS`) are comma-separated at the
    shell level: `HA_OPS_FOO="a,b,c"` → `["a", "b", "c"]`. Empty strings
    yield an empty list, not `[""]`.
    """
    for suffix, (section, key) in _ENV_MAP.items():
        env_var = f"{_ENV_PREFIX}{suffix}"
        env_value = os.environ.get(env_var)
        if env_value is None:
            continue
        if section not in data:
            data[section] = {}
        if suffix in _LIST_ENV_KEYS:
            data[section][key] = [
                part.strip() for part in env_value.split(",") if part.strip()
            ]
        else:
            data[section][key] = env_value
    return data


def _build_dataclass(cls: type[Any], data: dict[str, Any] | None) -> Any:
    """Build a dataclass from a dict, ignoring unknown keys."""
    if data is None:
        return cls()
    known_fields = {f.name for f in cls.__dataclass_fields__.values()}
    filtered = {}
    for k, v in data.items():
        if k in known_fields:
            target_type = cls.__dataclass_fields__[k].type
            if isinstance(v, str) and target_type in ("bool", "int"):
                v = _coerce_value(v, eval(target_type))  # noqa: S307
            elif isinstance(v, str):
                real_type = cls.__dataclass_fields__[k].type
                if real_type == "bool":
                    v = _coerce_value(v, bool)
                elif real_type == "int":
                    v = _coerce_value(v, int)
            filtered[k] = v
    return cls(**filtered)


def load_config(config_path: Path | None = None) -> HaOpsConfig:
    """Load configuration from YAML file with env var overrides.

    Args:
        config_path: Path to config.yaml. If None, uses ./config.yaml.

    Returns:
        Validated HaOpsConfig instance.
    """
    data: dict[str, Any] = {}

    if config_path is None:
        config_path = Path("config.local.yaml")

    if config_path.exists():
        yaml = YAML()
        with open(config_path) as f:
            loaded = yaml.load(f)
            if loaded is not None:
                data = dict(loaded)

    data = _apply_env_overrides(data)

    return HaOpsConfig(
        ha=_build_dataclass(HaConfig, data.get("ha")),
        database=_build_dataclass(DatabaseConfig, data.get("database")),
        filesystem=_build_dataclass(FilesystemConfig, data.get("filesystem")),
        server=_build_dataclass(ServerConfig, data.get("server")),
        safety=_build_dataclass(SafetyConfig, data.get("safety")),
        backup=_build_dataclass(BackupConfig, data.get("backup")),
        audit=_build_dataclass(AuditConfig, data.get("audit")),
        auth=_build_dataclass(AuthConfig, data.get("auth")),
    )
