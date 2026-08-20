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
    # 8123 is the historical HA Core default and what every existing install
    # still uses. HA 2026.8+ *new* HA OS installs default to port 80, so a
    # fresh instance may need this overridden. The addon does not rely on this
    # default — run.sh probes 8123 then 80 and exports HA_OPS_URL.
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
    host: str = "::"
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
class CaptureConfig:
    # UI capture artifacts (haops_ui_screenshot/trace). Empty dir = derive
    # <backup.dir>/captures in server.py (a mapped /backup volume). Retention
    # mirrors backups: newest max_count kept, older than max_age_days pruned.
    dir: str = ""
    max_count: int = 200
    max_age_days: int = 30


@dataclass
class ShellOutputConfig:
    # Persisted haops_exec_shell output (surfaced inline on the Timeline).
    # Empty dir = derive <backup.dir>/shell_output in server.py. Retention
    # mirrors captures: newest max_count kept, older than max_age_days pruned.
    dir: str = ""
    max_count: int = 500
    max_age_days: int = 30


@dataclass
class AuditConfig:
    # Empty string means "derive from backup.dir" for back-compat with deploys
    # that predate this option. server.py resolves and canonicalises the path.
    dir: str = ""
    # Log read-only tool calls to activity.jsonl so the Timeline is a full
    # activity feed, not just mutations. Rotated at 5 MB. Off = mutations only.
    log_reads: bool = True


@dataclass
class AuthConfig:
    enabled: bool = True
    # "token" (default): static pre-shared Bearer token — the only mode new
    # Claude Code versions can use over plain HTTP on a LAN, since ~v2.1.234
    # (Aug 2026) silently refuses OAuth token requests to non-HTTPS endpoints
    # (localhost exempt; upstream won't-fix, claude-code#3320). Clients send
    # `Authorization: Bearer <token>`, which suppresses OAuth discovery
    # entirely and also lifts the hostname/resource-matching restriction.
    # "oauth": the previous default, now EXPERIMENTAL — works only for clients
    # that reach the server over HTTPS or localhost, or that hold cached
    # tokens from before the client-side TLS guard.
    # "none": no MCP auth (only for strictly trusted networks).
    mode: str = "token"
    # Pre-shared token for mode=token. Empty = auto-generate once and persist
    # to <data_dir>/static_token (printed to the log at generation time).
    static_token: str = ""
    # Empty = derive <backup.dir>/auth in server.py. That lives on a mapped
    # volume (HA /backup) that survives addon uninstall / slug-change, unlike
    # the old /data default which was wiped on uninstall. A legacy
    # /data/oauth.json is migrated into the new home once, on startup.
    data_dir: str = ""
    access_token_ttl: int = 2592000  # 30d; sliding TTL extends on use (provider.py)
    refresh_token_ttl: int = 2592000  # 30 days
    issuer_url: str = ""  # client-facing URL; defaults to http://{host}:{port}


@dataclass
class DockerConfig:
    # Prune dangling images + unused build cache on every addon start.
    # Default ON, but only ever fires when the Docker socket is present
    # (docker_api + Protection mode OFF) — an install that hasn't opted into
    # the socket is untouched. A successful dev-deploy restarts the addon, so
    # each rebuild self-cleans the image it just orphaned. Only DANGLING images
    # and unused build cache are removed — never a tagged image or a volume —
    # so it is safe as a default. Set false to disable.
    # haops_docker_prune runs the same prune on demand regardless of this flag.
    prune_on_start: bool = True


@dataclass
class HaOpsConfig:
    ha: HaConfig = field(default_factory=HaConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    filesystem: FilesystemConfig = field(default_factory=FilesystemConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    backup: BackupConfig = field(default_factory=BackupConfig)
    captures: CaptureConfig = field(default_factory=CaptureConfig)
    shell_output: ShellOutputConfig = field(default_factory=ShellOutputConfig)
    audit: AuditConfig = field(default_factory=AuditConfig)
    auth: AuthConfig = field(default_factory=AuthConfig)
    docker: DockerConfig = field(default_factory=DockerConfig)


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
    "CAPTURES_DIR": ("captures", "dir"),
    "CAPTURES_MAX_COUNT": ("captures", "max_count"),
    "CAPTURES_MAX_AGE_DAYS": ("captures", "max_age_days"),
    "SHELL_OUTPUT_DIR": ("shell_output", "dir"),
    "SHELL_OUTPUT_MAX_COUNT": ("shell_output", "max_count"),
    "SHELL_OUTPUT_MAX_AGE_DAYS": ("shell_output", "max_age_days"),
    "AUDIT_DIR": ("audit", "dir"),
    "AUDIT_LOG_READS": ("audit", "log_reads"),
    "AUTH_ENABLED": ("auth", "enabled"),
    "AUTH_MODE": ("auth", "mode"),
    "AUTH_TOKEN": ("auth", "static_token"),
    "AUTH_DATA_DIR": ("auth", "data_dir"),
    "AUTH_ISSUER_URL": ("auth", "issuer_url"),
    "DOCKER_PRUNE_ON_START": ("docker", "prune_on_start"),
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
        captures=_build_dataclass(CaptureConfig, data.get("captures")),
        shell_output=_build_dataclass(ShellOutputConfig, data.get("shell_output")),
        audit=_build_dataclass(AuditConfig, data.get("audit")),
        auth=_build_dataclass(AuthConfig, data.get("auth")),
        docker=_build_dataclass(DockerConfig, data.get("docker")),
    )
