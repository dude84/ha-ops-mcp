"""Shared test fixtures for ha-ops-mcp."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from ha_ops_mcp.config import (
    BackupConfig,
    DatabaseConfig,
    FilesystemConfig,
    HaConfig,
    HaOpsConfig,
    SafetyConfig,
)
from ha_ops_mcp.connections.database import SqliteBackend
from ha_ops_mcp.safety.audit import AuditLog
from ha_ops_mcp.safety.backup import BackupManager
from ha_ops_mcp.safety.confirmation import SafetyManager
from ha_ops_mcp.safety.path_guard import PathGuard
from ha_ops_mcp.safety.rollback import RollbackManager
from ha_ops_mcp.server import HaOpsContext

# ── HA database schema (core tables) ──

HA_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS states (
    state_id INTEGER PRIMARY KEY,
    entity_id VARCHAR(255),
    state VARCHAR(255),
    attributes TEXT,
    last_changed_ts REAL,
    last_updated_ts REAL,
    old_state_id INTEGER,
    FOREIGN KEY (old_state_id) REFERENCES states(state_id)
);

CREATE TABLE IF NOT EXISTS events (
    event_id INTEGER PRIMARY KEY,
    event_type VARCHAR(32),
    event_data TEXT,
    time_fired_ts REAL
);

CREATE TABLE IF NOT EXISTS statistics_meta (
    id INTEGER PRIMARY KEY,
    statistic_id VARCHAR(255),
    source VARCHAR(32),
    unit_of_measurement VARCHAR(255),
    has_mean BOOLEAN,
    has_sum BOOLEAN,
    name VARCHAR(255)
);

CREATE TABLE IF NOT EXISTS statistics (
    id INTEGER PRIMARY KEY,
    metadata_id INTEGER,
    start_ts REAL,
    mean REAL,
    min REAL,
    max REAL,
    last_reset_ts REAL,
    state REAL,
    sum REAL,
    FOREIGN KEY (metadata_id) REFERENCES statistics_meta(id)
);

CREATE TABLE IF NOT EXISTS statistics_short_term (
    id INTEGER PRIMARY KEY,
    metadata_id INTEGER,
    start_ts REAL,
    mean REAL,
    min REAL,
    max REAL,
    last_reset_ts REAL,
    state REAL,
    sum REAL,
    FOREIGN KEY (metadata_id) REFERENCES statistics_meta(id)
);

CREATE TABLE IF NOT EXISTS recorder_runs (
    run_id INTEGER PRIMARY KEY,
    start REAL,
    "end" REAL,
    created REAL
);

CREATE TABLE IF NOT EXISTS schema_changes (
    id INTEGER PRIMARY KEY,
    schema_version INTEGER,
    changed REAL
);
"""

HA_SEED_SQL = """
INSERT INTO schema_changes (schema_version, changed) VALUES (43, 1712700000.0);

INSERT INTO states (entity_id, state, attributes, last_changed_ts, last_updated_ts)
VALUES
    ('sensor.temperature', '22.5', '{"unit_of_measurement": "°C"}', 1712700000.0, 1712700000.0),
    ('light.living_room', 'on', '{"brightness": 255}', 1712699000.0, 1712699000.0),
    ('sensor.humidity', '45', '{"unit_of_measurement": "%"}', 1712698000.0, 1712698000.0);

INSERT INTO recorder_runs (start, created) VALUES (1712690000.0, 1712690000.0);
"""


@pytest_asyncio.fixture
async def sqlite_backend(tmp_path: Path):
    """In-memory SQLite backend with HA schema and seed data."""
    db_path = tmp_path / "test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)

    async with engine.begin() as conn:
        for statement in HA_SCHEMA_SQL.split(";"):
            stmt = statement.strip()
            if stmt:
                await conn.execute(text(stmt))
        for statement in HA_SEED_SQL.split(";"):
            stmt = statement.strip()
            if stmt:
                await conn.execute(text(stmt))

    backend = SqliteBackend(engine)
    yield backend
    await engine.dispose()


@pytest.fixture
def config_dir(tmp_path: Path) -> Path:
    """Temporary HA config directory with sample files."""
    config = tmp_path / "config"
    config.mkdir()

    # configuration.yaml
    (config / "configuration.yaml").write_text(
        "homeassistant:\n  name: Test Home\n  unit_system: metric\n"
    )

    # secrets.yaml
    (config / "secrets.yaml").write_text(
        "api_password: supersecret123\ndb_url: mysql://user:pass@host/db\n"
    )

    # automations.yaml — includes a real trigger/action graph that exercises
    # every ref-key path the YAML walker handles.
    (config / "automations.yaml").write_text(
        "- id: 'auto_lights'\n"
        "  alias: Morning Lights\n"
        "  trigger:\n"
        "    - platform: state\n"
        "      entity_id: sensor.temperature\n"
        "  condition:\n"
        "    - condition: state\n"
        "      entity_id: light.living_room\n"
        "      state: 'off'\n"
        "  action:\n"
        "    - service: light.turn_on\n"
        "      target:\n"
        "        entity_id: light.living_room\n"
        "        area_id: living_room\n"
        "- alias: No ID Automation\n"
        "  trigger: []\n"
        "  action: []\n"
    )

    # scripts.yaml
    (config / "scripts.yaml").write_text(
        "bedtime:\n"
        "  alias: Bedtime\n"
        "  sequence:\n"
        "    - service: light.turn_off\n"
        "      target:\n"
        "        entity_id: light.living_room\n"
    )

    # scenes.yaml
    (config / "scenes.yaml").write_text(
        "- id: 'movie_time'\n"
        "  name: Movie Time\n"
        "  entities:\n"
        "    light.living_room: 'off'\n"
        "    sensor.temperature:\n"
        "      state: '21'\n"
    )

    # groups.yaml
    (config / "groups.yaml").write_text(
        "downstairs:\n"
        "  name: Downstairs\n"
        "  entities:\n"
        "    - light.living_room\n"
        "    - sensor.temperature\n"
    )

    # customize.yaml
    (config / "customize.yaml").write_text(
        "sensor.temperature:\n"
        "  friendly_name: Kitchen Temperature\n"
    )

    # .storage directory with entity registry
    storage = config / ".storage"
    storage.mkdir()
    (storage / "core.entity_registry").write_text(json.dumps({
        "version": 1,
        "data": {
            "entities": [
                {
                    "entity_id": "sensor.temperature",
                    "name": "Temperature",
                    "original_name": "Temperature",
                    "platform": "mqtt",
                    "area_id": "living_room",
                    "device_id": "dev_001",
                    "disabled_by": None,
                },
                {
                    "entity_id": "light.living_room",
                    "name": "Living Room Light",
                    "original_name": "Light",
                    "platform": "hue",
                    "area_id": "living_room",
                    "device_id": "dev_002",
                    "disabled_by": None,
                },
                {
                    "entity_id": "sensor.orphan",
                    "name": None,
                    "original_name": None,
                    "platform": "mqtt",
                    "area_id": None,
                    "device_id": None,
                    "disabled_by": None,
                },
            ]
        }
    }))

    # Device registry
    (storage / "core.device_registry").write_text(json.dumps({
        "version": 1,
        "data": {
            "devices": [
                {
                    "id": "dev_001",
                    "name": "Living Room Temp",
                    "name_by_user": None,
                    "manufacturer": "Xiaomi",
                    "model": "WSDCGQ11LM",
                    "sw_version": "1.0.1",
                    "hw_version": None,
                    "area_id": "living_room",
                    "disabled_by": None,
                    "identifiers": [["mqtt", "temp_sensor_1"]],
                    "config_entries": ["mqtt_entry_1"],
                },
                {
                    "id": "dev_002",
                    "name": "Philips Hue Bulb",
                    "name_by_user": "Living Room Light",
                    "manufacturer": "Signify",
                    "model": "LCT001",
                    "sw_version": "67.116.3",
                    "hw_version": None,
                    "area_id": "living_room",
                    "disabled_by": None,
                    "identifiers": [["hue", "bulb_001"]],
                    "config_entries": ["hue_entry_1"],
                },
                {
                    "id": "dev_003",
                    "name": "Disabled Device",
                    "manufacturer": "TestCo",
                    "model": "X1",
                    "area_id": None,
                    "disabled_by": "user",
                    "identifiers": [],
                    "config_entries": [],
                },
            ]
        }
    }))

    # Area registry
    (storage / "core.area_registry").write_text(json.dumps({
        "version": 1,
        "data": {
            "areas": [
                {"id": "living_room", "name": "Living Room", "floor_id": "ground"},
                {"id": "kitchen", "name": "Kitchen", "floor_id": "ground"},
            ]
        }
    }))

    # Floor registry
    (storage / "core.floor_registry").write_text(json.dumps({
        "version": 1,
        "data": {
            "floors": [
                {"floor_id": "ground", "name": "Ground Floor", "level": 0},
                {"floor_id": "upstairs", "name": "Upstairs", "level": 1},
            ]
        }
    }))

    # Default Lovelace dashboard (.storage/lovelace)
    (storage / "lovelace").write_text(json.dumps({
        "version": 1,
        "data": {
            "config": {
                "title": "Home",
                "views": [
                    {
                        "title": "Overview",
                        "path": "overview",
                        "cards": [
                            {"type": "entity", "entity": "sensor.temperature"},
                            {
                                "type": "entities",
                                "entities": [
                                    "light.living_room",
                                    {"entity": "sensor.orphan"},
                                ],
                            },
                            {
                                "type": "vertical-stack",
                                "cards": [
                                    {
                                        "type": "custom:mushroom-template-card",
                                        "entity": "sensor.temperature",
                                    }
                                ],
                            },
                        ],
                    }
                ],
            }
        }
    }))

    # Config entries
    (storage / "core.config_entries").write_text(json.dumps({
        "version": 1,
        "data": {
            "entries": [
                {
                    "entry_id": "mqtt_entry_1",
                    "domain": "mqtt",
                    "title": "MQTT",
                    "state": "loaded",
                    "source": "user",
                    "disabled_by": None,
                },
                {
                    "entry_id": "hue_entry_1",
                    "domain": "hue",
                    "title": "Philips Hue Bridge",
                    "state": "loaded",
                    "source": "user",
                    "disabled_by": None,
                },
                {
                    "entry_id": "broken_integration",
                    "domain": "broken",
                    "title": "Broken Integration",
                    "state": "setup_error",
                    "source": "user",
                    "disabled_by": None,
                    "reason": "Could not connect",
                },
            ]
        }
    }))

    return config


@pytest.fixture
def backup_dir(tmp_path: Path) -> Path:
    d = tmp_path / "backups"
    d.mkdir()
    return d


@pytest.fixture
def dashboard_storage(config_dir: Path):
    """Seed ``.storage/lovelace*`` with a small two-dashboard setup.

    Hoisted from ``test_dashboard_tools.py`` so other suites (notably the
    FastMCP schema-round-trip tests) can exercise dashboard tools through
    the filesystem-first read path without re-writing the same fixture.
    """
    import json as _json
    storage = config_dir / ".storage"
    (storage / "lovelace").write_text(_json.dumps({
        "version": 1,
        "data": {
            "config": {
                "title": "Home",
                "views": [
                    {
                        "title": "Overview",
                        "path": "overview",
                        "cards": [{"type": "entities"}],
                    },
                    {"title": "Kitchen", "path": "kitchen", "cards": []},
                ],
            }
        },
    }))
    (storage / "lovelace.energy").write_text(_json.dumps({
        "version": 1,
        "data": {
            "config": {
                "title": "Energy",
                "views": [{"title": "Usage", "cards": []}],
            }
        },
    }))
    return storage


@pytest.fixture
def mock_rest() -> AsyncMock:
    """Mock REST client returning canned HA API responses."""
    rest = AsyncMock()
    rest.get = AsyncMock(side_effect=_mock_rest_get)
    rest.get_text = AsyncMock(return_value=(
        "2026-04-10 ERROR (MainThread) [homeassistant.core] Test error\n"
        "2026-04-10 WARNING (MainThread) [custom_components.hacs] Test warning\n"
    ))
    rest.post = AsyncMock(return_value={})
    rest.post_text = AsyncMock(return_value="2")
    rest.delete = AsyncMock(return_value={})
    return rest


def _mock_rest_get(path: str) -> Any:
    if path == "/api/config":
        return {
            "version": "2026.4.1",
            "location_name": "Test Home",
            "unit_system": {"length": "km"},
            "time_zone": "Europe/Warsaw",
        }
    if path == "/api/states":
        return [
            {
                "entity_id": "sensor.temperature",
                "state": "22.5",
                "attributes": {"friendly_name": "Temperature", "unit_of_measurement": "°C"},
                "last_changed": "2026-04-10T12:00:00+00:00",
                "last_updated": "2026-04-10T12:00:00+00:00",
            },
            {
                "entity_id": "light.living_room",
                "state": "on",
                "attributes": {"friendly_name": "Living Room Light", "brightness": 255},
                "last_changed": "2026-04-10T11:00:00+00:00",
                "last_updated": "2026-04-10T11:00:00+00:00",
            },
            {
                "entity_id": "sensor.orphan",
                "state": "unavailable",
                "attributes": {},
                "last_changed": "2026-01-01T00:00:00+00:00",
                "last_updated": "2026-01-01T00:00:00+00:00",
            },
        ]
    if path.startswith("/api/states/"):
        eid = path[len("/api/states/"):]
        # Return the same mock data if the id matches one we know
        states = _mock_rest_get("/api/states")
        if isinstance(states, list):
            for s in states:
                if s["entity_id"] == eid:
                    return s
        # Unknown → raise 404 (simulating real HA)
        from ha_ops_mcp.connections.rest import RestClientError
        raise RestClientError(404, f"Entity {eid} not found")
    if path == "/api/config/entity_registry":
        return [
            {
                "entity_id": "sensor.temperature", "platform": "mqtt",
                "area_id": "living_room", "device_id": "dev_001",
                "disabled_by": None, "name": "Temperature",
                "original_name": "Temperature",
            },
            {
                "entity_id": "light.living_room", "platform": "hue",
                "area_id": "living_room", "device_id": "dev_002",
                "disabled_by": None, "name": "Living Room Light",
                "original_name": "Light",
            },
            {
                "entity_id": "sensor.orphan", "platform": "mqtt",
                "area_id": None, "device_id": None,
                "disabled_by": None, "name": None,
                "original_name": None,
            },
        ]
    return {}


@pytest.fixture
def mock_ws() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def ctx(
    config_dir: Path,
    backup_dir: Path,
    mock_rest: AsyncMock,
    mock_ws: AsyncMock,
    sqlite_backend: SqliteBackend,
) -> HaOpsContext:
    """Full HaOpsContext with mock connections and real safety layer."""
    config = HaOpsConfig(
        ha=HaConfig(url="http://test:8123", token="test_token"),
        database=DatabaseConfig(url="sqlite:///test.db"),
        filesystem=FilesystemConfig(config_root=str(config_dir)),
        safety=SafetyConfig(),
        backup=BackupConfig(dir=str(backup_dir)),
    )

    return HaOpsContext(
        config=config,
        rest=mock_rest,
        ws=mock_ws,
        db=sqlite_backend,
        safety=SafetyManager(),
        rollback=RollbackManager(),
        backup=BackupManager(backup_dir),
        audit=AuditLog(backup_dir / "audit"),
        path_guard=PathGuard(config_dir),
    )
