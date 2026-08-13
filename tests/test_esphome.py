"""ESPHome tools — node inventory, HA mapping, build + fits check."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from ha_ops_mcp.tools.esphome import (
    _fit_verdict,
    _node_configs,
    _parse_memory,
    _parse_node,
    haops_esphome_build,
    haops_esphome_status,
)

NODE_YAML = """\
# NOUS A5T power strip
substitutions:
  device_name: pl-office-powerstrip
  friendly_name: Office Power Strip

esphome:
  name: ${device_name}
  friendly_name: ${friendly_name}

esp8266:
  board: esp8285
  board_build.ldscript: eagle.flash.1m.ld

api:
  encryption:
    key: !secret api_key

ota:
  - platform: esphome
    password: !secret ota_password

switch:
  - platform: gpio
    pin: GPIO14
    name: Socket 1
    on_turn_on:
      - lambda: !lambda |-
          ESP_LOGD("x", "on");
"""

FRAGMENT_YAML = """\
# A package fragment — no esphome: block, so not a compilable node.
sensor:
  - platform: wifi_signal
    name: WiFi
"""


@pytest.fixture
def esphome_dir(config_dir: Path) -> Path:
    d = config_dir / "esphome"
    d.mkdir()
    (d / "pl-office-powerstrip.yaml").write_text(NODE_YAML)
    (d / "common-sensors.yaml").write_text(FRAGMENT_YAML)
    (d / "secrets.yaml").write_text("api_key: abc\nota_password: def\n")
    return d


# ── Node config parsing ───────────────────────────────────────────────


def test_parses_a_node_through_its_substitutions(esphome_dir):
    node = _parse_node(esphome_dir / "pl-office-powerstrip.yaml")

    assert node is not None
    assert node["node"] == "pl-office-powerstrip"
    assert node["friendly_name"] == "Office Power Strip"
    assert node["platform"] == "esp8266"
    assert node["board"] == "esp8285"
    assert node["has_ota"] is True
    assert node["has_api"] is True


def test_esphome_custom_tags_do_not_break_parsing(esphome_dir):
    """`!secret` and `!lambda` must not fail the whole file."""
    node = _parse_node(esphome_dir / "pl-office-powerstrip.yaml")

    assert node is not None
    assert "error" not in node


def test_a_package_fragment_is_not_a_node(esphome_dir):
    assert _parse_node(esphome_dir / "common-sensors.yaml") is None


def test_node_listing_skips_fragments_and_secrets(ctx, esphome_dir):
    nodes = _node_configs(ctx)

    assert [n["node"] for n in nodes] == ["pl-office-powerstrip"]


def test_malformed_yaml_is_reported_not_raised(ctx, esphome_dir):
    (esphome_dir / "broken.yaml").write_text("esphome:\n  name: x\n bad: [indent\n")

    nodes = _node_configs(ctx)

    broken = next((n for n in nodes if n.get("file", "").endswith("broken.yaml")), None)
    assert broken is not None
    assert "unparseable" in broken["error"]


# ── Memory + fit parsing ──────────────────────────────────────────────


def test_parses_platformio_memory_report():
    output = (
        "Compiling .pioenvs/x/src/main.cpp.o\n"
        "RAM:   [====      ]  38.9% (used 31872 bytes from 81920 bytes)\n"
        "Flash: [=====     ]  47.3% (used 483296 bytes from 1022976 bytes)\n"
        "Successfully compiled program.\n"
    )

    mem = _parse_memory(output)

    assert mem["flash"] == {
        "used_bytes": 483296,
        "total_bytes": 1022976,
        "percent": 47.3,
    }
    assert mem["ram"]["used_bytes"] == 31872


def test_fit_verdict_catches_the_a5t_case():
    """483 KB ESPHome image against Tasmota's 372 KB free slot."""
    fit = _fit_verdict(483296, 372 * 1024)

    assert fit is not None
    assert fit["fits"] is False
    assert "DOES NOT FIT" in fit["verdict"]
    assert fit["margin_bytes"] == 380928 - 483296


def test_fit_verdict_reports_headroom_when_it_fits():
    fit = _fit_verdict(300000, 380928)

    assert fit is not None
    assert fit["fits"] is True
    assert "FITS" in fit["verdict"]
    assert fit["margin_bytes"] == 80928


def test_no_verdict_without_a_target():
    assert _fit_verdict(483296, None) is None
    assert _fit_verdict(None, 380928) is None


# ── Status ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_status_without_esphome_dir_says_so(ctx):
    result = await haops_esphome_status(ctx)

    assert result["count"] == 0
    assert "No ESPHome node configs found" in result["note"]


@pytest.mark.asyncio
async def test_status_reports_builds_unavailable_with_the_remedy(
    ctx, esphome_dir, mock_ws
):
    """No Docker → nodes still listed, artifacts explained rather than missing."""
    mock_ws.send_command.return_value = []

    result = await haops_esphome_status(ctx)

    assert result["count"] == 1
    assert result["builder"]["available"] is False
    assert "Protection mode" in result["builder"]["reason"]
    assert any("Protection mode" in n for n in result["notes"])


@pytest.mark.asyncio
async def test_status_maps_a_node_to_its_ha_device(ctx, config_dir, esphome_dir):
    """Join is by slug — HA title-cases some device names, so case must not matter."""
    storage = config_dir / ".storage"
    storage.mkdir(exist_ok=True)
    (storage / "core.config_entries").write_text(json.dumps({
        "version": 1,
        "data": {"entries": [{
            "entry_id": "entry_esp",
            "domain": "esphome",
            "title": "PL-Office-Powerstrip",
            "state": "loaded",
        }]},
    }))
    (storage / "core.device_registry").write_text(json.dumps({
        "version": 3,
        "data": {"devices": [{
            "id": "dev_strip",
            "name": "Pl-Office-Powerstrip",
            "config_entry_id": "entry_esp",
            "primary_config_entry": "entry_esp",
            "sw_version": "2026.7.4",
        }]},
    }))
    (storage / "core.entity_registry").write_text(json.dumps({
        "version": 1,
        "data": {"entities": [
            {"entity_id": "switch.socket_1", "device_id": "dev_strip"},
            {"entity_id": "switch.socket_2", "device_id": "dev_strip"},
        ]},
    }))
    ctx.rest.get.side_effect = None
    ctx.rest.get.return_value = [
        {"entity_id": "switch.socket_1", "state": "off", "attributes": {}},
        {"entity_id": "switch.socket_2", "state": "unavailable", "attributes": {}},
    ]

    result = await haops_esphome_status(ctx, include_builds=False)

    ha = result["nodes"][0]["ha"]
    assert ha["device_id"] == "dev_strip"
    assert ha["config_entry_id"] == "entry_esp"
    assert ha["entity_count"] == 2
    assert ha["available_entities"] == 1
    assert ha["online"] is True


@pytest.mark.asyncio
async def test_status_flags_a_node_not_adopted_in_ha(ctx, esphome_dir, mock_ws):
    mock_ws.send_command.return_value = []

    result = await haops_esphome_status(ctx, include_builds=False)

    assert result["nodes"][0]["ha"] is None
    assert any("Not adopted in HA" in n for n in result["notes"])


@pytest.mark.asyncio
async def test_status_filters_to_one_node(ctx, esphome_dir, mock_ws):
    mock_ws.send_command.return_value = []

    by_name = await haops_esphome_status(
        ctx, node="pl-office-powerstrip", include_builds=False
    )
    by_file = await haops_esphome_status(
        ctx, node="pl-office-powerstrip.yaml", include_builds=False
    )
    missing = await haops_esphome_status(ctx, node="nope", include_builds=False)

    assert by_name["count"] == 1
    assert by_file["count"] == 1
    assert "error" in missing


# ── Build ─────────────────────────────────────────────────────────────


def _docker(exec_result: dict, containers: list[dict] | None = None) -> AsyncMock:
    docker = AsyncMock()
    docker.available = lambda: True
    docker.socket_path = lambda: "/run/docker.sock"
    docker.containers.return_value = containers if containers is not None else [
        {
            "id": "abc123456789",
            "name": "app_5c53de3b_esphome",
            "image": "ghcr.io/esphome/esphome-hassio:2026.7.4",
            "state": "running",
        }
    ]
    docker.exec_run.side_effect = exec_result  # a callable or list
    return docker


COMPILE_LOG = (
    "INFO Compiling app...\n"
    "RAM:   [====      ]  38.9% (used 31872 bytes from 81920 bytes)\n"
    "Flash: [=====     ]  47.3% (used 483296 bytes from 1022976 bytes)\n"
    "INFO Successfully compiled program.\n"
)

STAT_LINE = (
    "pl-office-powerstrip|.pioenvs/pl-office-powerstrip/firmware.ota.bin|483296:1786000000\n"
)


def _exec_router(compile_exit: int = 0, timed_out: bool = False):
    async def _run(container, cmd, **kwargs):
        script = cmd[-1]
        if "esphome compile" in script:
            return {
                "exit_code": None if timed_out else compile_exit,
                "stdout": COMPILE_LOG,
                "stderr": "",
                "timed_out": timed_out,
            }
        return {"exit_code": 0, "stdout": STAT_LINE, "stderr": ""}

    return _run


@pytest.mark.asyncio
async def test_build_compiles_and_reports_size(ctx, esphome_dir, mock_ws):
    ctx.docker = _docker(_exec_router())

    result = await haops_esphome_build(ctx, node="pl-office-powerstrip")

    assert result["success"] is True
    assert result["board"] == "esp8285"
    assert result["memory"]["flash"]["used_bytes"] == 483296
    assert result["artifacts"][0]["size_bytes"] == 483296
    assert "target_free_bytes" in result["hint"]
    assert result["command"] == (
        "esphome compile /config/esphome/pl-office-powerstrip.yaml"
    )


@pytest.mark.asyncio
async def test_build_verdict_would_have_stopped_the_a5t_flash(
    ctx, esphome_dir, mock_ws
):
    ctx.docker = _docker(_exec_router())

    result = await haops_esphome_build(
        ctx, node="pl-office-powerstrip", target_free_bytes=372 * 1024
    )

    assert result["fit"]["fits"] is False
    assert "DOES NOT FIT" in result["fit"]["verdict"]


@pytest.mark.asyncio
async def test_build_reports_a_failed_compile(ctx, esphome_dir, mock_ws):
    ctx.docker = _docker(_exec_router(compile_exit=1))

    result = await haops_esphome_build(ctx, node="pl-office-powerstrip")

    assert result["success"] is False
    assert result["exit_code"] == 1
    assert "Successfully compiled" in result["log_tail"]


@pytest.mark.asyncio
async def test_build_says_a_timeout_was_abandoned_not_killed(
    ctx, esphome_dir, mock_ws
):
    ctx.docker = _docker(_exec_router(timed_out=True))

    result = await haops_esphome_build(ctx, node="pl-office-powerstrip", timeout=60)

    assert result["timed_out"] is True
    assert "ABANDONED, not killed" in result["note"]
    assert "Call again later" in result["note"]


@pytest.mark.asyncio
async def test_build_without_docker_explains_the_gate(ctx, esphome_dir):
    ctx.docker = None

    result = await haops_esphome_build(ctx, node="pl-office-powerstrip")

    assert "Protection mode" in result["error"]


@pytest.mark.asyncio
async def test_build_when_the_builder_is_stopped(ctx, esphome_dir):
    ctx.docker = _docker(
        _exec_router(),
        containers=[{
            "id": "abc",
            "name": "app_5c53de3b_esphome",
            "image": "esphome",
            "state": "exited",
        }],
    )

    result = await haops_esphome_build(ctx, node="pl-office-powerstrip")

    assert "not running" in result["error"]


@pytest.mark.asyncio
async def test_build_rejects_an_unknown_node(ctx, esphome_dir):
    ctx.docker = _docker(_exec_router())

    result = await haops_esphome_build(ctx, node="does-not-exist")

    assert "No ESPHome node config matches" in result["error"]
    assert result["known_nodes"] == ["pl-office-powerstrip"]


# ── Nested substitutions ──────────────────────────────────────────────
#
# Found on the live instance: a node whose substitutions block contains
# `name: "pl-co2-lcd-${room_id}"` while `esphome.name` is `${name}`. A
# single-pass expansion stops at "pl-co2-lcd-${room_id}", which then fails to
# match the HA config entry and reports the node as "not adopted in HA".

NESTED_YAML = """\
substitutions:
  room: "Livingroom"
  room_id: "livingroom"
  name: "pl-co2-lcd-${room_id}"
  friendly_name: "PL CO2 LCD ${room}"

esphome:
  name: ${name}
  friendly_name: ${friendly_name}

esp32:
  board: esp32-c3-devkitm-1
"""


def test_nested_substitutions_resolve_fully(config_dir):
    d = config_dir / "esphome"
    d.mkdir(exist_ok=True)
    path = d / "pl-co2-lcd-livingroom.yaml"
    path.write_text(NESTED_YAML)

    node = _parse_node(path)

    assert node is not None
    assert node["node"] == "pl-co2-lcd-livingroom"
    assert node["friendly_name"] == "PL CO2 LCD Livingroom"


def test_unresolvable_substitution_is_left_visible(config_dir):
    """A ref from a remote package we don't fetch stays as raw text."""
    d = config_dir / "esphome"
    d.mkdir(exist_ok=True)
    path = d / "remote.yaml"
    path.write_text("esphome:\n  name: ${from_remote_package}\n")

    node = _parse_node(path)

    assert node is not None
    assert node["node"] == "${from_remote_package}"


def test_self_referential_substitution_does_not_spin(config_dir):
    d = config_dir / "esphome"
    d.mkdir(exist_ok=True)
    path = d / "loop.yaml"
    path.write_text(
        'substitutions:\n  a: "${b}"\n  b: "${a}"\n\nesphome:\n  name: ${a}\n'
    )

    node = _parse_node(path)

    assert node is not None  # terminated rather than hanging
