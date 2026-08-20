"""ESPHome tools — haops_esphome_status, haops_esphome_build.

`/config/esphome/*.yaml` was previously reachable only through the generic
config tools, which answer "what does this file say" but not the questions that
actually come up: which node maps to which HA device, is it online, what did
the last build produce, and **will the firmware fit the target's free flash**.

That last one has a scar behind it. A NOUS A5T (ESP8285, 1 MB) was flashed from
Tasmota 12.3.1 to a newer Tasmota through the `release/` channel; the image did
fit, barely, and the device was bricked for unrelated reasons — but the near
miss was 364 KB into 372 KB free. The ESPHome replacement firmware for that same
node compiles to ~483 KB, which would *not* have fitted the OTA slot at all. A
tool that reports firmware size against the target's free space turns that from
a post-mortem into a pre-flight check, so `haops_esphome_build` takes
`target_free_bytes` and gives a verdict.

Compiling happens in the **ESPHome add-on's own container** via the Docker
socket, not here: bundling PlatformIO plus the xtensa toolchain to duplicate an
add-on the user already runs would add hundreds of MB to an already-large image.
Borrow the toolchain, don't ship it.

Two facts about the builder that are not guessable, both established by
probing the live add-on (2026-08-13, ESPHome 2026.7.4):

- Build output lands in **two** places depending on when the node was last
  built. Current versions write `<config>/esphome/.esphome/build/<node>/`,
  which is under the config root and so readable *without* Docker. Older
  versions wrote `/data/build/<node>/` — the add-on's private volume, visible
  only through the socket — and that tree survives an upgrade. Both are
  checked, newest mtime wins; reporting a stale artifact after a fresh compile
  would be worse than reporting none.
- The artifact layout differs by framework: Arduino/ESP8266 puts them in
  `.pioenvs/<node>/firmware*.bin`, ESP-IDF/ESP32 in `build/firmware*.bin`.

So only *compiling* actually needs the Docker socket. Sizes and artifact paths
for a recently-built node are plain filesystem reads.
"""

from __future__ import annotations

import io
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ha_ops_mcp.server import registry
from ha_ops_mcp.storage_registry import device_config_entry_ids, load_registry

if TYPE_CHECKING:
    from ha_ops_mcp.server import HaOpsContext

logger = logging.getLogger(__name__)


_NODE_DIR = "esphome"
# Not node configs: secrets, and ESPHome's own package/include fragments are
# still listed (they're valid targets for `esphome compile` only if they define
# an `esphome:` block, which is how we tell them apart).
_SKIP_NAMES = frozenset({"secrets.yaml", "secrets.yml"})

# Platform keys that carry a `board:`. Covers ESP8266/ESP32 plus LibreTiny and
# RP2040 so a BK7231/RTL8710 node isn't reported as "unknown platform".
_PLATFORM_KEYS = ("esp8266", "esp32", "rp2040", "bk72xx", "rtl87xx", "host")

# Where the add-on used to put build output: its own private volume, which is
# invisible to our filesystem and reachable only over Docker. Current versions
# build under <config>/esphome/.esphome/build/ instead, but the old tree
# survives an upgrade, so a node not rebuilt since then only appears here.
_LEGACY_BUILD_DIR = "/data/build"

# Artifact locations, in the order we prefer to report them. `.pioenvs` is the
# Arduino/PlatformIO layout, `build/` the ESP-IDF one.
_ARTIFACT_GLOBS = (
    ".pioenvs/{node}/firmware.ota.bin",
    ".pioenvs/{node}/firmware.factory.bin",
    ".pioenvs/{node}/firmware.bin",
    "build/firmware.ota.bin",
    "build/firmware.factory.bin",
    "build/firmware.bin",
)

# PlatformIO's memory report, e.g.
#   Flash: [=====     ]  47.3% (used 483296 bytes from 1022976 bytes)
_MEM_RE = re.compile(
    r"^(RAM|Flash):\s*\[[=\s]*\]\s*([\d.]+)%\s*"
    r"\(used (\d+) bytes from (\d+) bytes\)",
    re.MULTILINE,
)


# ── Node config parsing ────────────────────────────────────────────────


def _tolerant_yaml_load(text: str) -> Any:
    """Parse ESPHome YAML, tolerating its custom tags.

    ESPHome configs are full of tags a plain safe-loader rejects — `!secret`,
    `!lambda`, `!include`, `!extend`, `!remove`. We only want the top-level
    scalars (name, board, platform), so every unknown tag resolves to None
    rather than failing the whole file.
    """
    from ruamel.yaml import YAML
    from ruamel.yaml.constructor import SafeConstructor

    class _Tolerant(SafeConstructor):
        pass

    def _ignore(loader: Any, tag_suffix: Any, node: Any) -> None:
        return None

    _Tolerant.add_multi_constructor("!", _ignore)
    _Tolerant.add_multi_constructor("tag:yaml.org,2002:python/", _ignore)

    yaml = YAML(typ="safe")
    yaml.Constructor = _Tolerant
    return yaml.load(io.StringIO(text))


_SUB_RE = re.compile(r"\$\{([a-zA-Z0-9_]+)\}|\$([a-zA-Z0-9_]+)")

# Substitutions nest — a real config has `name: "pl-co2-lcd-${room_id}"` in the
# substitutions block and `esphome.name: ${name}`, so one pass yields
# "pl-co2-lcd-${room_id}" and the node is misidentified. Iterate to a fixed
# point instead, capped so a self-referential substitution can't spin.
_SUB_MAX_PASSES = 8


def _expand_substitutions(value: str, subs: dict[str, Any]) -> str:
    """Resolve `${var}` / `$var` against a substitutions block, recursively.

    Unresolvable references are left as-is (they may come from a remote
    package we don't fetch), which keeps the raw text visible instead of
    silently emptying the field.
    """

    def _one(m: re.Match[str]) -> str:
        key = m.group(1) or m.group(2)
        replacement = subs.get(key)
        return str(replacement) if replacement is not None else m.group(0)

    out = value
    for _ in range(_SUB_MAX_PASSES):
        expanded = _SUB_RE.sub(_one, out)
        if expanded == out:
            break
        out = expanded
    return out


def _has_block(data: dict[str, Any], key: str) -> bool | None:
    """True/False for a top-level block, or None when it may be in a package."""
    if key in data:
        return True
    return None if "packages" in data else False


def _parse_node(path: Path) -> dict[str, Any] | None:
    """Read one node config. Returns None if it isn't an ESPHome node."""
    try:
        text = path.read_text()
    except OSError as e:
        return {"file": path.name, "error": f"unreadable: {e}"}

    try:
        data = _tolerant_yaml_load(text)
    except Exception as e:  # malformed YAML is a finding, not a crash
        return {"file": path.name, "error": f"unparseable: {str(e)[:200]}"}

    if not isinstance(data, dict) or "esphome" not in data:
        # A package/include fragment, not a compilable node.
        return None

    subs = data.get("substitutions") or {}
    if not isinstance(subs, dict):
        subs = {}
    esphome_block = data.get("esphome") or {}
    if not isinstance(esphome_block, dict):
        esphome_block = {}

    raw_name = esphome_block.get("name")
    name = (
        _expand_substitutions(str(raw_name), subs)
        if isinstance(raw_name, str)
        else None
    )
    # ESPHome falls back to the filename when `name:` is templated away.
    node = name or path.stem

    platform = None
    board = None
    for key in _PLATFORM_KEYS:
        block = data.get(key)
        if isinstance(block, dict):
            platform = key
            raw_board = block.get("board")
            if isinstance(raw_board, str):
                board = _expand_substitutions(raw_board, subs)
            break

    return {
        "node": node,
        "file": f"{_NODE_DIR}/{path.name}",
        "friendly_name": (
            _expand_substitutions(str(esphome_block["friendly_name"]), subs)
            if isinstance(esphome_block.get("friendly_name"), str)
            else subs.get("friendly_name")
        ),
        "platform": platform,
        "board": board,
        # A node that pulls remote packages defines `api:` / `ota:` inside the
        # package, which we do not fetch — so absence here proves nothing and
        # `false` would be a lie. Report null instead.
        "has_ota": _has_block(data, "ota"),
        "has_api": _has_block(data, "api"),
        "uses_packages": "packages" in data,
    }


def _node_configs(ctx: HaOpsContext) -> list[dict[str, Any]]:
    """Every compilable node config under `<config>/esphome/`."""
    node_dir = Path(ctx.config.filesystem.config_root) / _NODE_DIR
    if not node_dir.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for path in sorted(node_dir.glob("*.y*ml")):
        if path.name in _SKIP_NAMES:
            continue
        parsed = _parse_node(path)
        if parsed is not None:
            out.append(parsed)
    return out


# ── HA mapping ─────────────────────────────────────────────────────────


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")


async def _ha_mapping(
    ctx: HaOpsContext, nodes: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """Map node name → its HA config entry, device and entities.

    ESPHome node devices carry no `identifiers` (only a MAC in `connections`),
    so the join is by name: the config entry title is the node name at adoption
    time, and the device name follows it — but case drifts (`pl-ir-blaster-down`
    vs `Pl-Ir-Blaster-Down`), hence the slug comparison.
    """
    by_slug: dict[str, dict[str, Any]] = {}
    try:
        entries = (await load_registry(ctx, "config_entries")).records
        devices = (await load_registry(ctx, "devices")).records
        entities = (await load_registry(ctx, "entities")).records
    except RuntimeError as e:
        logger.debug("ESPHome mapping unavailable: %s", e)
        return by_slug

    # A config entry's runtime `state` (loaded / setup_retry / ...) exists only
    # in HA's memory — the .storage copy has no such field — so reading it from
    # the file would report null for every node. Ask HA when it will answer.
    live_states: dict[str, Any] = {}
    try:
        from ha_ops_mcp.tools.device import _config_entries_by_id

        live_states = {
            entry_id: entry.get("state")
            for entry_id, entry in (await _config_entries_by_id(ctx)).items()
        }
    except Exception as e:
        logger.debug("Live config entry states unavailable: %s", e)

    esphome_entries = {
        e.get("entry_id"): e
        for e in entries
        if e.get("domain") == "esphome" and e.get("entry_id")
    }
    entry_by_slug = {
        _slug(str(e.get("title") or "")): e for e in esphome_entries.values()
    }

    devices_by_entry: dict[str, dict[str, Any]] = {}
    for dev in devices:
        for entry_id in device_config_entry_ids(dev):
            if entry_id in esphome_entries:
                devices_by_entry[entry_id] = dev

    from ha_ops_mcp.tools.entity import _get_states

    states = await _get_states(ctx)

    for entry_node in nodes:
        slug = _slug(str(entry_node["node"]))
        entry = entry_by_slug.get(slug)
        if entry is None:
            continue
        entry_id = str(entry.get("entry_id"))
        node_dev = devices_by_entry.get(entry_id)
        dev_id = node_dev.get("id") if node_dev else None
        node_entities = [
            e.get("entity_id")
            for e in entities
            if dev_id and e.get("device_id") == dev_id and e.get("entity_id")
        ]
        live = [
            eid
            for eid in node_entities
            if states.get(str(eid), {}).get("state")
            not in (None, "unavailable", "unknown")
        ]
        by_slug[slug] = {
            "config_entry_id": entry_id,
            "entry_state": live_states.get(entry_id) or entry.get("state"),
            "device_id": dev_id,
            "device_name": (
                (node_dev.get("name_by_user") or node_dev.get("name"))
                if node_dev
                else None
            ),
            "sw_version": node_dev.get("sw_version") if node_dev else None,
            "entity_count": len(node_entities),
            # "Online" for an ESPHome node means its API connection is up, which
            # shows as its entities having real states rather than unavailable.
            "online": bool(live),
            "available_entities": len(live),
        }
    return by_slug


# ── Builder container ──────────────────────────────────────────────────


async def _find_builder(ctx: HaOpsContext) -> tuple[str | None, str | None]:
    """Locate the running ESPHome add-on container.

    Returns:
        (container_name, error_message) — exactly one is non-None.
    """
    if ctx.docker is None or not ctx.docker.available():
        reason = (
            ctx.docker.unavailable_reason
            if ctx.docker is not None
            else "Docker client not initialised"
        )
        return None, (
            f"Docker socket unavailable ({reason}). Build/artifact data comes "
            "from the ESPHome add-on's own container, so this needs "
            "'docker_api: true' (declared since v0.57.0) AND Protection mode "
            "OFF on this add-on, then an add-on restart."
        )

    try:
        containers = await ctx.docker.containers(all_containers=True)
    except Exception as e:
        return None, f"Could not list containers: {str(e)[:200]}"

    candidates = [
        c
        for c in containers
        if "esphome" in (c.get("name") or "").lower()
        or "esphome" in (c.get("image") or "").lower()
    ]
    running = [c for c in candidates if c.get("state") == "running"]
    if not candidates:
        return None, (
            "No ESPHome container found on this host. The ESPHome add-on "
            "provides the toolchain; install it, or compile in the ESPHome UI."
        )
    if not running:
        names = ", ".join(str(c.get("name")) for c in candidates)
        return None, (
            f"ESPHome container is not running ({names}). Start the ESPHome "
            "add-on first."
        )
    return str(running[0]["name"]), None


def _artifacts_filesystem(
    ctx: HaOpsContext, nodes: list[str]
) -> dict[str, list[dict[str, Any]]]:
    """Stat build artifacts visible on OUR filesystem, no Docker involved.

    Current ESPHome versions build into `<config>/esphome/.esphome/build/<node>/`,
    which is under the config root and therefore readable directly. That makes
    firmware sizes answerable with no Protection-mode opt-in at all — only
    *compiling* needs the socket.
    """
    root = Path(ctx.config.filesystem.config_root) / _NODE_DIR / ".esphome" / "build"
    out: dict[str, list[dict[str, Any]]] = {}
    if not root.is_dir():
        return out
    for node in nodes:
        for pattern in _ARTIFACT_GLOBS:
            path = root / node / pattern.format(node=node)
            try:
                stat = path.stat()
            except OSError:
                continue
            out.setdefault(node, []).append({
                "artifact": path.name,
                "path": str(path),
                "size_bytes": stat.st_size,
                "mtime_epoch": int(stat.st_mtime),
                "location": "config",
            })
    return out


async def _artifacts_container(
    ctx: HaOpsContext, container: str, nodes: list[str]
) -> dict[str, list[dict[str, Any]]]:
    """Stat build artifacts inside the builder's private `/data/build`.

    Older ESPHome add-on versions built here, and the directory survives an
    upgrade, so a node whose last build predates the change is only visible
    this way. One `sh -c` covers every node — each exec is three Engine
    round-trips and this is a read for a status report.
    """
    if not nodes:
        return {}

    lines: list[str] = []
    for node in nodes:
        for pattern in _ARTIFACT_GLOBS:
            rel = pattern.format(node=node)
            lines.append(
                f'p="{_LEGACY_BUILD_DIR}/{node}/{rel}"; '
                f'[ -f "$p" ] && printf "%s|%s|%s\\n" '
                f'"{node}" "{rel}" "$(stat -c %s:%Y "$p")"'
            )
    script = "; ".join(lines) + "; true"

    try:
        result = await ctx.docker.exec_run(  # type: ignore[union-attr]
            container, ["sh", "-c", script], timeout=60.0
        )
    except Exception as e:
        logger.debug("Artifact stat failed: %s", e)
        return {}

    out: dict[str, list[dict[str, Any]]] = {}
    for line in (result.get("stdout") or "").splitlines():
        parts = line.strip().split("|")
        if len(parts) != 3 or ":" not in parts[2]:
            continue
        node, rel, stat = parts
        size_str, mtime_str = stat.split(":", 1)
        try:
            size = int(size_str)
            mtime = int(mtime_str)
        except ValueError:
            continue
        out.setdefault(node, []).append({
            "artifact": rel.rsplit("/", 1)[-1],
            "path": f"{_LEGACY_BUILD_DIR}/{node}/{rel}",
            "size_bytes": size,
            "mtime_epoch": mtime,
            "location": "builder_data",
        })
    return out


async def _artifacts(
    ctx: HaOpsContext, container: str | None, nodes: list[str]
) -> dict[str, list[dict[str, Any]]]:
    """Build artifacts per node, newest wins across both build locations.

    Both directories can hold a copy of the same artifact — the add-on moved
    its build path and left the old tree behind. Reporting the stale one after
    a fresh compile is worse than reporting nothing, so entries are keyed by
    artifact name and the newest mtime wins.
    """
    merged: dict[str, dict[str, dict[str, Any]]] = {}

    for source in (
        _artifacts_filesystem(ctx, nodes),
        await _artifacts_container(ctx, container, nodes) if container else {},
    ):
        for node, found in source.items():
            slot = merged.setdefault(node, {})
            for item in found:
                existing = slot.get(item["artifact"])
                if existing is None or item["mtime_epoch"] > existing["mtime_epoch"]:
                    slot[item["artifact"]] = item

    return {
        node: sorted(
            items.values(),
            key=lambda a: (-a["mtime_epoch"], str(a["artifact"])),
        )
        for node, items in merged.items()
    }


# ── haops_esphome_status ───────────────────────────────────────────────


@registry.tool(
    name="haops_esphome_status",
    description=(
        "Inventory of ESPHome nodes: which config yaml maps to which HA "
        "device, whether the node is online, and what its last build produced. "
        "READ-ONLY. Answers the questions the generic config tools can't: "
        "'which yaml is this device', 'is this node online', 'how big is its "
        "firmware'. "
        "Parameters: node (string, optional — restrict to one node name or "
        "yaml filename), include_builds (bool, default true — stat build "
        "artifacts, needs the Docker socket). "
        "Everything here is a filesystem + registry read and needs NO special "
        "access: node configs from <config>/esphome/*.yaml, and artifacts from "
        "<config>/esphome/.esphome/build/ where current ESPHome versions build. "
        "The Docker socket adds only the legacy /data/build tree inside the "
        "add-on's private volume (where older versions built, and where a node "
        "not rebuilt since still lives); when both hold the same artifact the "
        "newest wins. Compiling — haops_esphome_build — is the part that does "
        "need the socket. "
        "Returns: {nodes: [{node, file, platform, board, ha: {device_id, "
        "online, entity_count, ...}, builds: [{artifact, size_bytes, ...}]}], "
        "count, builder, notes}."
    ),
    params={
        "node": {
            "type": "string",
            "description": "Restrict to one node (name or yaml filename)",
        },
        "include_builds": {
            "type": "boolean",
            "description": "Stat build artifacts (requires the Docker socket)",
            "default": True,
        },
    },
)
async def haops_esphome_status(
    ctx: HaOpsContext,
    node: str | None = None,
    include_builds: bool = True,
) -> dict[str, Any]:
    nodes = _node_configs(ctx)
    if not nodes:
        return {
            "nodes": [],
            "count": 0,
            "note": (
                f"No ESPHome node configs found under "
                f"{ctx.config.filesystem.config_root}/{_NODE_DIR}/. A node "
                "config is a yaml with an `esphome:` block."
            ),
        }

    if node:
        wanted = _slug(node.removesuffix(".yaml").removesuffix(".yml"))
        nodes = [
            n
            for n in nodes
            if _slug(str(n.get("node") or "")) == wanted
            or _slug(Path(str(n.get("file"))).stem) == wanted
        ]
        if not nodes:
            return {"error": f"No ESPHome node matches '{node}'"}

    mapping = await _ha_mapping(ctx, nodes)

    builds: dict[str, list[dict[str, Any]]] = {}
    # Not "unavailable" — unchecked. Claiming the builder is missing when the
    # caller asked us not to look is the same class of lie as reporting a field
    # we never read.
    builder: dict[str, Any] = {"checked": False}
    if include_builds:
        # Artifacts do NOT require Docker: current ESPHome versions build under
        # <config>/esphome/.esphome/build/. The socket only adds the legacy
        # /data/build tree, and only compiling truly needs it — so a missing
        # socket downgrades coverage rather than removing the feature.
        container, err = await _find_builder(ctx)
        builder = (
            {"available": True, "container": container}
            if container
            else {
                "available": False,
                "reason": err,
                "impact": (
                    "Compiling (haops_esphome_build) is unavailable. Artifacts "
                    "built by current ESPHome versions are still reported from "
                    f"the config dir; only the legacy {_LEGACY_BUILD_DIR} tree "
                    "is out of reach."
                ),
            }
        )
        builds = await _artifacts(
            ctx, container, [str(n["node"]) for n in nodes if n.get("node")]
        )

    for entry in nodes:
        entry["ha"] = mapping.get(_slug(str(entry.get("node") or "")))
        if include_builds:
            entry["builds"] = builds.get(str(entry.get("node")), [])

    unmapped = [n["node"] for n in nodes if not n.get("ha")]
    notes: list[str] = []
    if unmapped:
        notes.append(
            f"Not adopted in HA (no esphome config entry): {', '.join(map(str, unmapped))}"
        )
    if include_builds and not builder.get("available"):  # noqa: SIM102
        notes.append(f"{builder.get('impact')} {builder.get('reason')}")

    return {
        "nodes": nodes,
        "count": len(nodes),
        "builder": builder,
        "notes": notes,
    }


# ── haops_esphome_build ────────────────────────────────────────────────


def _parse_memory(output: str) -> dict[str, Any]:
    """Pull PlatformIO's RAM/Flash usage out of build output."""
    out: dict[str, Any] = {}
    for kind, pct, used, total in _MEM_RE.findall(output):
        out[kind.lower()] = {
            "used_bytes": int(used),
            "total_bytes": int(total),
            "percent": float(pct),
        }
    return out


def _fit_verdict(
    ota_size: int | None, target_free_bytes: int | None
) -> dict[str, Any] | None:
    """Compare the OTA image against the target's free space."""
    if ota_size is None or target_free_bytes is None:
        return None
    margin = target_free_bytes - ota_size
    return {
        "firmware_bytes": ota_size,
        "target_free_bytes": target_free_bytes,
        "margin_bytes": margin,
        "fits": margin >= 0,
        "verdict": (
            f"FITS with {margin:,} bytes to spare "
            f"({margin / target_free_bytes * 100:.1f}% headroom)"
            if margin >= 0
            else f"DOES NOT FIT — {abs(margin):,} bytes too large"
        ),
        "note": (
            "Compared against the free space you supplied. For a first flash "
            "off another firmware, that is the OTA slot the current firmware "
            "reports (e.g. Tasmota's Information page 'Free Program Space'), "
            "not the chip's total flash."
        ),
    }


@registry.tool(
    name="haops_esphome_build",
    description=(
        "Compile an ESPHome node and report the firmware size — including "
        "whether it fits the target's free flash. Runs `esphome compile` inside "
        "the ESPHome add-on's own container over the Docker socket, borrowing "
        "its PlatformIO/xtensa toolchain rather than shipping a second copy. "
        "USE FOR: checking a config actually builds; getting the firmware size "
        "BEFORE flashing a flash-tight device (1 MB ESP8285 etc.); confirming "
        "what the last edit did to the image. "
        "Parameters: node (string, required — node name or yaml filename), "
        "target_free_bytes (int, optional — the target's free program space; "
        "when given, the response includes a fits/doesn't-fit verdict with the "
        "margin), timeout (int seconds, default 110). "
        "PASS target_free_bytes for a flash-tight device: a NOUS A5T (1 MB) "
        "reported 372 KB free under Tasmota while its ESPHome image is ~483 KB "
        "— that is a doesn't-fit that only shows up as a failed flash otherwise. "
        "SLOW: a cold build takes minutes, which is longer than most MCP "
        "clients wait (commonly ~120s). The default timeout sits UNDER that on "
        "purpose, so you get a real response — 'still compiling' — instead of a "
        "dead call. On timeout the compile is ABANDONED, not killed: it keeps "
        "running in the builder, so call again and it reports the finished "
        "artifact. Raise timeout only if your client's limit is higher. "
        "Does not touch any device: it writes only to the builder's build "
        "cache. Flashing is a separate, deliberate act (ESPHome UI, or OTA). "
        "Requires the Docker socket ('docker_api: true' + Protection mode off). "
        "Returns: {node, success, artifacts, memory: {flash, ram}, fit, "
        "log_tail}."
    ),
    params={
        "node": {
            "type": "string",
            "description": "Node name or yaml filename to compile",
        },
        "target_free_bytes": {
            "type": "integer",
            "description": (
                "Target's free program space, for a fits/doesn't-fit verdict"
            ),
        },
        "timeout": {
            "type": "integer",
            "description": (
                "Seconds to wait (default 110 — under the usual MCP client "
                "limit, so a long build returns 'still compiling' rather than "
                "killing the call)"
            ),
            "default": 110,
        },
    },
)
async def haops_esphome_build(
    ctx: HaOpsContext,
    node: str,
    target_free_bytes: int | None = None,
    timeout: int = 110,
) -> dict[str, Any]:
    configs = _node_configs(ctx)
    wanted = _slug(node.removesuffix(".yaml").removesuffix(".yml"))
    match = next(
        (
            c
            for c in configs
            if _slug(str(c.get("node") or "")) == wanted
            or _slug(Path(str(c.get("file"))).stem) == wanted
        ),
        None,
    )
    if match is None:
        return {
            "error": f"No ESPHome node config matches '{node}'",
            "known_nodes": [c.get("node") for c in configs],
        }

    container, err = await _find_builder(ctx)
    if container is None:
        return {"error": err}

    node_name = str(match["node"])
    yaml_name = Path(str(match["file"])).name
    # The builder sees HA's config dir as /config, so the node yaml is at the
    # same relative path it has for us.
    command = f"esphome compile /config/{_NODE_DIR}/{yaml_name}"

    # Two concurrent compiles in the same build dir corrupt each other's
    # artifacts, so refuse immediately instead of queueing — a queued build
    # would silently double the caller's wait and likely time out anyway.
    # NOTE: lock.locked() → acquire is a benign race — a competing call
    # could slip in between the check and the acquire, in which case we
    # queue behind exactly one build instead of refusing. That still never
    # runs two compiles at once, which is the property that matters.
    # NOTE: this guards concurrent MCP calls only. A compile ABANDONED by
    # the timeout path below keeps running inside the builder after the
    # lock is released — that pre-existing race is documented in the tool
    # description ("call again and it reports the finished artifact").
    build_lock = ctx.mutation_lock(f"esphome:{node_name}")
    if build_lock.locked():
        return {
            "error": (
                f"Build already in progress for node '{node_name}' — "
                "not starting a second compile in the same build dir "
                "(it would corrupt the artifacts). Wait for the running "
                "build and call again."
            ),
            "node": node_name,
        }

    async with build_lock:
        try:
            result = await ctx.docker.exec_run(  # type: ignore[union-attr]
                container, ["sh", "-c", command], timeout=float(timeout)
            )
        except Exception as e:
            return {"error": f"Compile failed to start: {str(e)[:300]}"}

        output = (result.get("stdout") or "") + (result.get("stderr") or "")
        timed_out = bool(result.get("timed_out"))
        exit_code = result.get("exit_code")

        artifacts = (await _artifacts(ctx, container, [node_name])).get(
            node_name, []
        )
    ota = next(
        (a for a in artifacts if a["artifact"] == "firmware.ota.bin"),
        next((a for a in artifacts if a["artifact"] == "firmware.bin"), None),
    )

    response: dict[str, Any] = {
        "node": node_name,
        "yaml": match["file"],
        "platform": match.get("platform"),
        "board": match.get("board"),
        "builder": container,
        "command": command,
        "success": exit_code == 0,
        "exit_code": exit_code,
        "artifacts": artifacts,
        "memory": _parse_memory(output),
        # The tail is where both the error and the size report live.
        "log_tail": output[-4000:],
    }

    fit = _fit_verdict(ota["size_bytes"] if ota else None, target_free_bytes)
    if fit:
        response["fit"] = fit
    elif target_free_bytes is not None:
        response["fit"] = {
            "error": "No OTA artifact found to measure — see log_tail."
        }

    if timed_out:
        response["timed_out"] = True
        response["note"] = (
            f"Compile exceeded {timeout}s and was ABANDONED, not killed — it "
            "is still running inside the builder. Call again in a minute or "
            "two; the artifact list will show the finished firmware. Any "
            "artifacts listed above are from the PREVIOUS build."
        )
    if ota and not target_free_bytes:
        response["hint"] = (
            f"{ota['artifact']} is {ota['size_bytes']:,} bytes. Pass "
            "target_free_bytes to have this checked against the device's free "
            "program space before you flash."
        )
    return response
