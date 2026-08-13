"""haops_entity_rename rewrite_references — moving the refs, not just the id."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ha_ops_mcp.refactor_rename import Rewriter, plan_reference_rewrites
from ha_ops_mcp.storage_registry import reset_write_clock
from ha_ops_mcp.tools.entity import haops_entity_rename


@pytest.fixture(autouse=True)
def _clean_clock():
    reset_write_clock()
    yield
    reset_write_clock()


# ── The token rewriter ────────────────────────────────────────────────


def test_rewrites_a_plain_reference():
    rw = Rewriter({"sensor.old": "sensor.new"})

    out, counts = rw.apply("entity_id: sensor.old\n")

    assert out == "entity_id: sensor.new\n"
    assert counts == {"sensor.old": 1}


def test_does_not_clip_a_longer_id():
    """`sensor.old` must not match inside `sensor.old_2` or `sensor.older`."""
    rw = Rewriter({"sensor.old": "sensor.new"})

    out, counts = rw.apply("a: sensor.old_2\nb: sensor.older\nc: sensor.old\n")

    assert out == "a: sensor.old_2\nb: sensor.older\nc: sensor.new\n"
    assert counts == {"sensor.old": 1}


def test_does_not_match_a_dotted_suffix_of_another_id():
    """`sensor.old` inside `input_number.sensor.old` is not our entity."""
    rw = Rewriter({"sensor.old": "sensor.new"})

    out, _ = rw.apply("weird.sensor.old\n")

    assert out == "weird.sensor.old\n"


def test_rewrites_jinja_call_forms():
    rw = Rewriter({"sensor.old": "sensor.new"})
    text = (
        "{{ states('sensor.old') }} "
        '{{ state_attr("sensor.old", "unit") }} '
        "{{ is_state('sensor.old', 'on') }}"
    )

    out, counts = rw.apply(text)

    assert "sensor.old" not in out
    assert counts == {"sensor.old": 3}


def test_rewrites_the_jinja_attribute_form():
    """`states.sensor.old.state` — the dot-prefixed form needs its own pass."""
    rw = Rewriter({"sensor.old": "sensor.new"})

    out, counts = rw.apply("{{ states.sensor.old.state }}")

    assert out == "{{ states.sensor.new.state }}"
    assert counts == {"sensor.old": 1}


def test_swaps_do_not_chain():
    """a→b and b→c applied together must not turn a into c."""
    rw = Rewriter({"sensor.a": "sensor.b", "sensor.b": "sensor.c"})

    out, counts = rw.apply("x: sensor.a\ny: sensor.b\n")

    assert out == "x: sensor.b\ny: sensor.c\n"
    assert counts == {"sensor.a": 1, "sensor.b": 1}


def test_comments_and_formatting_survive():
    rw = Rewriter({"sensor.old": "sensor.new"})
    text = (
        "# Keep this comment\n"
        "sensors:\n"
        "  - entity_id: sensor.old   # trailing note\n"
        "\n"
        "  - entity_id: 'sensor.old'\n"
    )

    out, counts = rw.apply(text)

    assert out == (
        "# Keep this comment\n"
        "sensors:\n"
        "  - entity_id: sensor.new   # trailing note\n"
        "\n"
        "  - entity_id: 'sensor.new'\n"
    )
    assert counts == {"sensor.old": 2}


def test_structure_rewrite_covers_nested_values_and_keys():
    rw = Rewriter({"sensor.old": "sensor.new"})
    config = {
        "views": [
            {
                "cards": [
                    {"type": "entities", "entities": ["sensor.old"]},
                    {"entity": "sensor.old"},
                    {"styles": {"sensor.old": "red"}},
                    {"type": "markdown", "content": "{{ states('sensor.old') }}"},
                ]
            }
        ]
    }

    new, counts, changed = rw.apply_structure(config)

    assert new["views"][0]["cards"][0]["entities"] == ["sensor.new"]
    assert new["views"][0]["cards"][1]["entity"] == "sensor.new"
    assert new["views"][0]["cards"][2]["styles"] == {"sensor.new": "red"}
    assert "sensor.new" in new["views"][0]["cards"][3]["content"]
    assert counts == {"sensor.old": 4}
    assert len(changed) == 4
    # Original untouched — the plan holds before AND after.
    assert config["views"][0]["cards"][1]["entity"] == "sensor.old"


# ── Planning ──────────────────────────────────────────────────────────


def _seed_registry(config_dir: Path, entities: list[dict]) -> None:
    storage = config_dir / ".storage"
    storage.mkdir(exist_ok=True)
    (storage / "core.entity_registry").write_text(
        json.dumps({"version": 1, "data": {"entities": entities}})
    )


@pytest.mark.asyncio
async def test_plan_finds_yaml_and_dashboard_references(
    ctx, config_dir, dashboard_storage, mock_ws
):
    (config_dir / "automations.yaml").write_text(
        "- alias: Watch\n"
        "  trigger:\n"
        "    - platform: state\n"
        "      entity_id: sensor.temperature\n"
        "  action:\n"
        "    - service: notify.notify\n"
        "      data:\n"
        "        message: \"{{ states('sensor.temperature') }}\"\n"
    )
    lovelace = json.loads((dashboard_storage / "lovelace").read_text())
    lovelace["data"]["config"]["views"][0]["cards"] = [
        {"type": "entities", "entities": ["sensor.temperature"]}
    ]
    (dashboard_storage / "lovelace").write_text(json.dumps(lovelace))
    mock_ws.send_command.return_value = [
        {"url_path": "energy", "title": "Energy"}
    ]

    plan = await plan_reference_rewrites(
        ctx, {"sensor.temperature": "sensor.office_temperature"}
    )

    paths = {f.rel_path for f in plan.files}
    assert "automations.yaml" in paths
    auto = next(f for f in plan.files if f.rel_path == "automations.yaml")
    assert auto.occurrences == {"sensor.temperature": 2}
    assert "sensor.office_temperature" in auto.new_text
    assert "sensor.temperature" not in auto.new_text

    assert [d.url_path for d in plan.dashboards] == ["lovelace"]
    assert plan.dashboards[0].occurrences == {"sensor.temperature": 1}
    assert plan.total_occurrences() >= 3


@pytest.mark.asyncio
async def test_plan_resolves_the_real_dashboard_url_path(
    ctx, config_dir, dashboard_storage, mock_ws
):
    """`.storage/lovelace.new_dashboard` may be url_path `new-dashboard`.

    HA maps `-` to `_` when naming the file, so the filename cannot be
    reversed — saving to the wrong url_path would create a second dashboard.
    """
    (dashboard_storage / "lovelace.new_dashboard").write_text(json.dumps({
        "version": 1,
        "data": {
            "config": {
                "views": [
                    {"cards": [{"entity": "sensor.temperature"}]}
                ]
            }
        },
    }))
    mock_ws.send_command.return_value = [
        {"url_path": "new-dashboard", "title": "New"}
    ]

    plan = await plan_reference_rewrites(
        ctx, {"sensor.temperature": "sensor.office_temperature"}
    )

    assert [d.url_path for d in plan.dashboards] == ["new-dashboard"]


@pytest.mark.asyncio
async def test_unknown_dashboard_file_goes_to_manual_review(
    ctx, config_dir, dashboard_storage, mock_ws
):
    """No live dashboard matches → refuse to guess the url_path."""
    (dashboard_storage / "lovelace.orphan").write_text(json.dumps({
        "version": 1,
        "data": {"config": {"views": [{"cards": [{"entity": "sensor.x"}]}]}},
    }))
    mock_ws.send_command.return_value = []

    plan = await plan_reference_rewrites(ctx, {"sensor.x": "sensor.y"})

    assert not plan.dashboards
    orphan = next(
        m for m in plan.manual_review if m.get("path") == ".storage/lovelace.orphan"
    )
    assert "duplicate dashboard" in orphan["reason"]


@pytest.mark.asyncio
async def test_esphome_and_energy_are_reported_not_rewritten(
    ctx, config_dir, mock_ws
):
    esphome = config_dir / "esphome"
    esphome.mkdir()
    (esphome / "node.yaml").write_text(
        "sensor:\n  - platform: homeassistant\n    entity_id: sensor.temperature\n"
    )
    (config_dir / ".storage" / "energy").write_text(
        json.dumps({"energy_sources": [{"stat_energy_from": "sensor.temperature"}]})
    )
    mock_ws.send_command.return_value = []

    plan = await plan_reference_rewrites(
        ctx, {"sensor.temperature": "sensor.office_temperature"}
    )

    reviewed = {m["path"]: m for m in plan.manual_review if "path" in m}
    assert "esphome/node.yaml" in reviewed
    assert "flash" in reviewed["esphome/node.yaml"]["reason"]
    assert ".storage/energy" in reviewed
    # And neither was queued for rewriting.
    assert all("esphome" not in f.rel_path for f in plan.files)


@pytest.mark.asyncio
async def test_empty_mapping_plans_nothing(ctx):
    plan = await plan_reference_rewrites(ctx, {})

    assert plan.is_empty()
    assert plan.total_occurrences() == 0


# ── The tool, end to end ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_preview_shows_diffs_and_apply_rewrites_everything(
    ctx, config_dir, dashboard_storage, mock_ws
):
    # An id the shared config_dir fixture does not mention, so the occurrence
    # count in this test is exactly what this test wrote.
    _seed_registry(config_dir, [
        {"entity_id": "sensor.probe_temp", "platform": "mqtt"},
    ])
    (config_dir / "automations.yaml").write_text(
        "- alias: Watch\n"
        "  trigger:\n"
        "    - platform: state\n"
        "      entity_id: sensor.probe_temp  # keep me\n"
    )
    lovelace = json.loads((dashboard_storage / "lovelace").read_text())
    lovelace["data"]["config"]["views"][0]["cards"] = [
        {"type": "entities", "entities": ["sensor.probe_temp"]}
    ]
    (dashboard_storage / "lovelace").write_text(json.dumps(lovelace))

    saved: list[dict] = []

    async def _send(command: str, **kwargs):
        if command == "lovelace/dashboards/list":
            return []
        if command == "lovelace/config/save":
            saved.append(kwargs)
            return {}
        return {}

    mock_ws.send_command.side_effect = _send

    preview = await haops_entity_rename(
        ctx,
        renames=[{
            "entity_id": "sensor.probe_temp",
            "new_entity_id": "sensor.office_temperature",
        }],
        rewrite_references=True,
    )

    rewrites = preview["reference_rewrites"]
    assert rewrites["total_occurrences"] == 2
    auto = next(
        f for f in rewrites["files"] if f["path"] == "automations.yaml"
    )
    assert "-      entity_id: sensor.probe_temp" in auto["diff"]
    assert "+      entity_id: sensor.office_temperature" in auto["diff"]
    assert rewrites["dashboards"][0]["url_path"] == "lovelace"
    assert any("reload" in n for n in rewrites["notes"])

    result = await haops_entity_rename(
        ctx, renames=[], confirm=True, token=preview["token"]
    )

    assert result["success"] is True
    written = (config_dir / "automations.yaml").read_text()
    assert "sensor.office_temperature  # keep me" in written
    assert saved[0]["config"]["views"][0]["cards"][0]["entities"] == [
        "sensor.office_temperature"
    ]
    assert result["references_rewritten"]["total_occurrences"] == 2
    assert "haops_system_reload" in result["next_step"]


@pytest.mark.asyncio
async def test_rewrites_are_undone_by_the_rename_transaction(
    ctx, config_dir, mock_ws
):
    _seed_registry(config_dir, [{"entity_id": "sensor.temperature"}])
    original = (
        "- alias: Watch\n"
        "  trigger:\n"
        "    - platform: state\n"
        "      entity_id: sensor.temperature\n"
    )
    (config_dir / "automations.yaml").write_text(original)
    mock_ws.send_command.return_value = []

    preview = await haops_entity_rename(
        ctx,
        renames=[{
            "entity_id": "sensor.temperature",
            "new_entity_id": "sensor.office_temperature",
        }],
        rewrite_references=True,
    )
    applied = await haops_entity_rename(
        ctx, renames=[], confirm=True, token=preview["token"]
    )
    assert "sensor.office_temperature" in (
        config_dir / "automations.yaml"
    ).read_text()

    from ha_ops_mcp.tools.rollback import haops_rollback

    rb_preview = await haops_rollback(
        ctx, transaction_id=applied["transaction_id"]
    )
    rolled = await haops_rollback(
        ctx,
        transaction_id=applied["transaction_id"],
        confirm=True,
        token=rb_preview["token"],
    )

    assert rolled.get("success") is not False
    assert (config_dir / "automations.yaml").read_text() == original


@pytest.mark.asyncio
async def test_apply_refuses_a_file_edited_since_the_preview(
    ctx, config_dir, mock_ws
):
    """Someone else's edit must not be silently clobbered."""
    _seed_registry(config_dir, [{"entity_id": "sensor.temperature"}])
    (config_dir / "automations.yaml").write_text(
        "- entity_id: sensor.temperature\n"
    )
    mock_ws.send_command.return_value = []

    preview = await haops_entity_rename(
        ctx,
        renames=[{
            "entity_id": "sensor.temperature",
            "new_entity_id": "sensor.office_temperature",
        }],
        rewrite_references=True,
    )
    (config_dir / "automations.yaml").write_text(
        "- entity_id: sensor.temperature  # edited in between\n"
    )

    result = await haops_entity_rename(
        ctx, renames=[], confirm=True, token=preview["token"]
    )

    assert result["success"] is False
    err = next(
        e for e in result["references_rewritten"]["errors"]
        if e.get("file") == "automations.yaml"
    )
    assert "changed since the preview" in err["error"]
    # And the in-between edit is intact.
    assert "edited in between" in (config_dir / "automations.yaml").read_text()


@pytest.mark.asyncio
async def test_rewrite_is_opt_in(ctx, config_dir, mock_ws):
    """Default behaviour is unchanged — no scan, no rewrite key."""
    _seed_registry(config_dir, [{"entity_id": "sensor.temperature"}])
    (config_dir / "automations.yaml").write_text(
        "- entity_id: sensor.temperature\n"
    )

    preview = await haops_entity_rename(
        ctx,
        renames=[{
            "entity_id": "sensor.temperature",
            "new_entity_id": "sensor.office_temperature",
        }],
    )
    await haops_entity_rename(
        ctx, renames=[], confirm=True, token=preview["token"]
    )

    assert "reference_rewrites" not in preview
    assert (config_dir / "automations.yaml").read_text() == (
        "- entity_id: sensor.temperature\n"
    )


@pytest.mark.asyncio
async def test_name_only_rename_says_there_is_nothing_to_rewrite(
    ctx, config_dir, mock_ws
):
    _seed_registry(config_dir, [{"entity_id": "sensor.temperature"}])

    preview = await haops_entity_rename(
        ctx,
        renames=[{"entity_id": "sensor.temperature", "name": "Office Temp"}],
        rewrite_references=True,
    )

    notes = preview["reference_rewrites"]["notes"]
    assert any("nothing" in n for n in notes)
