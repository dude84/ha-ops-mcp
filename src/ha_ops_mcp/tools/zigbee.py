"""Zigbee / ZHA tools.

Read-only mesh + coordinator introspection (haops_zigbee_info), plus two
mutating helpers: haops_zha_reconfigure_device and haops_zigbee_scan.

Ground-truth source is ``<config_root>/zigbee.db`` — a zigpy SQLite DB that
is SEPARATE from the HA recorder DB (so haops_db_query does not touch it).
Tables carry a migration-version suffix (``devices_v15`` etc.) that changes
across zigpy releases, so we discover the live suffix at runtime instead of
hard-coding it. ieee->name mapping comes from core.device_registry.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ha_ops_mcp.server import registry
from ha_ops_mcp.tools.device import _get_device_registry

if TYPE_CHECKING:
    from ha_ops_mcp.server import HaOpsContext

logger = logging.getLogger(__name__)

# zigpy neighbor relationship enum (zigpy.zdo.types.Neighbor.RelationShip)
_RELATIONSHIP = {
    0x0: "parent",
    0x1: "child",
    0x2: "sibling",
    0x3: "none",
    0x4: "previous_child",
}


def _zigbee_db_path(ctx: HaOpsContext) -> Path:
    return Path(ctx.config.filesystem.config_root) / "zigbee.db"


def _read_zigbee_db(db_path: str) -> dict[str, Any]:
    """Synchronous SQLite read of the zigpy DB. Runs in a thread.

    Opens read-only (uri mode=ro) so we never risk mutating zigpy's DB.
    Discovers the ``_v<N>`` table suffix from sqlite_master.
    """
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    try:
        tables = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }

        def _suffixed(base: str) -> str | None:
            # exact 'devices', else highest devices_vN
            cands = sorted(
                (t for t in tables if t == base or t.startswith(base + "_v")),
                key=lambda t: int(t.rsplit("_v", 1)[1]) if "_v" in t else -1,
            )
            return cands[-1] if cands else None

        dev_t = _suffixed("devices")
        nbr_t = _suffixed("neighbors")
        bak_t = _suffixed("network_backups")

        devices: list[dict[str, Any]] = []
        if dev_t:
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({dev_t})")}
            for r in conn.execute(f"SELECT * FROM {dev_t}"):
                d = dict(r)
                devices.append({
                    "ieee": d.get("ieee"),
                    "nwk": d.get("nwk"),
                    "last_seen": d.get("last_seen") if "last_seen" in cols else None,
                })

        neighbors: list[dict[str, Any]] = []
        if nbr_t:
            for r in conn.execute(f"SELECT * FROM {nbr_t}"):
                d = dict(r)
                neighbors.append({
                    "device_ieee": d.get("device_ieee"),
                    "ieee": d.get("ieee"),
                    "lqi": d.get("lqi"),
                    "relationship": d.get("relationship"),
                    "depth": d.get("depth"),
                })

        # latest network backup blob (coordinator metadata / firmware)
        backup_meta: dict[str, Any] | None = None
        if bak_t:
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({bak_t})")}
            order = "backup_time" if "backup_time" in cols else "id"
            row = conn.execute(
                f"SELECT * FROM {bak_t} ORDER BY {order} DESC LIMIT 1"
            ).fetchone()
            if row:
                raw = dict(row).get("backup_json")
                if raw:
                    try:
                        blob = json.loads(raw)
                        ni = blob.get("network_info", blob)
                        backup_meta = {
                            "metadata": ni.get("metadata"),
                            "stack_specific": ni.get("stack_specific"),
                            "source": ni.get("source"),
                        }
                    except (json.JSONDecodeError, AttributeError):
                        backup_meta = None

        return {
            "tables": {"devices": dev_t, "neighbors": nbr_t, "backups": bak_t},
            "devices": devices,
            "neighbors": neighbors,
            "backup_meta": backup_meta,
        }
    finally:
        conn.close()


def _zha_ieee_map(reg: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Map lowercased ieee -> {name, device_id} from the device registry.

    ZHA devices carry the ieee in ``connections`` as ("zigbee", ieee) and/or
    ``identifiers`` as ("zha", ieee).

    Note: registry tuples are NOT guaranteed 2-element. HomeKit stores
    3-element identifiers (``["homekit", "<id>", "homekit.bridge"]``), so we
    index defensively rather than unpacking `for k, v in ...` — a strict
    unpack raised "too many values to unpack (expected 2)" and took down
    every caller for the whole registry.
    """
    out: dict[str, dict[str, Any]] = {}
    for dev in reg:
        ieee: str | None = None
        for el in dev.get("connections") or []:
            if len(el) >= 2 and el[0] in ("zigbee", "zha"):
                ieee = el[1]
                break
        if not ieee:
            for el in dev.get("identifiers") or []:
                if len(el) >= 2 and el[0] == "zha":
                    ieee = el[1]
                    break
        if not ieee:
            continue
        out[ieee.lower()] = {
            "name": dev.get("name_by_user") or dev.get("name"),
            "device_id": dev.get("id"),
        }
    return out


@registry.tool(
    name="haops_zigbee_info",
    description=(
        "Read-only ZHA/Zigbee mesh + coordinator introspection. Reads the "
        "zigpy SQLite DB (<config_root>/zigbee.db — SEPARATE from the HA "
        "recorder DB) plus core.device_registry for names. No HA API call, "
        "no mutation. Use to answer: what coordinator firmware/metadata is "
        "running, which devices are stale/offline, per-device LQI + parent + "
        "relationship + last_seen. "
        "CAVEATS: (1) neighbor/LQI data is a periodic snapshot zigpy refreshes "
        "every few hours — a single LQI read is noisy and possibly stale; "
        "use haops_zigbee_scan to force a fresh topology scan, and average a "
        "few reads before judging signal quality. (2) last_seen is zigpy's, "
        "updated on any device traffic. "
        "Parameters: stale_hours (number, default 24 — flag devices not seen "
        "in N+ hours), include_neighbors (bool, default false — include the "
        "raw neighbor table)."
    ),
    params={
        "stale_hours": {
            "type": "number",
            "description": "Flag devices with last_seen older than N hours",
            "default": 24,
        },
        "include_neighbors": {
            "type": "boolean",
            "description": "Include the raw neighbor/LQI table in the output",
            "default": False,
        },
    },
)
async def haops_zigbee_info(
    ctx: HaOpsContext,
    stale_hours: float = 24,
    include_neighbors: bool = False,
) -> dict[str, Any]:
    db_path = _zigbee_db_path(ctx)
    if not db_path.exists():
        return {
            "error": f"No zigbee.db at {db_path}. This instance may not run "
            "ZHA (could be Zigbee2MQTT or no Zigbee at all)."
        }

    try:
        data = await asyncio.to_thread(_read_zigbee_db, str(db_path))
    except sqlite3.Error as e:
        return {"error": f"Failed to read zigbee.db: {e}"}

    try:
        reg = await _get_device_registry(ctx)
    except Exception:  # registry is best-effort enrichment only
        reg = []
    name_map = _zha_ieee_map(reg)

    devices = data["devices"]
    neighbors = data["neighbors"]

    # coordinator = nwk 0x0000 (0)
    coord_ieee: str | None = None
    for d in devices:
        nwk = d.get("nwk")
        if nwk in (0, "0x0000", "0000", "0"):
            coord_ieee = (d.get("ieee") or "").lower()
            break

    # coordinator -> neighbor LQI (inbound link quality at the coordinator)
    coord_lqi: dict[str, int] = {}
    if coord_ieee:
        for n in neighbors:
            if (n.get("device_ieee") or "").lower() == coord_ieee and n.get("ieee"):
                coord_lqi[(n["ieee"]).lower()] = n.get("lqi")

    # relationship/depth as reported by whoever lists this device as a neighbor
    rel_by_ieee: dict[str, dict[str, Any]] = {}
    for n in neighbors:
        nb = (n.get("ieee") or "").lower()
        if nb and nb not in rel_by_ieee:
            rel_by_ieee[nb] = {
                "relationship": _RELATIONSHIP.get(
                    n.get("relationship"), n.get("relationship")
                ),
                "depth": n.get("depth"),
                "reported_by": (n.get("device_ieee") or "").lower(),
            }

    now = time.time()
    out_devices: list[dict[str, Any]] = []
    stale: list[dict[str, Any]] = []
    for d in devices:
        ieee = (d.get("ieee") or "").lower()
        last_seen = d.get("last_seen")
        age_h: float | None = None
        if isinstance(last_seen, (int, float)) and last_seen > 0:
            age_h = round((now - last_seen) / 3600, 2)
        is_coord = ieee == coord_ieee
        rec = {
            "ieee": d.get("ieee"),
            "name": name_map.get(ieee, {}).get("name"),
            "nwk": d.get("nwk"),
            "is_coordinator": is_coord,
            "last_seen_age_hours": age_h,
            "lqi_at_coordinator": coord_lqi.get(ieee),
            "relationship": rel_by_ieee.get(ieee, {}).get("relationship"),
            "depth": rel_by_ieee.get(ieee, {}).get("depth"),
        }
        out_devices.append(rec)
        if not is_coord and age_h is not None and age_h >= stale_hours:
            stale.append({"ieee": d.get("ieee"), "name": rec["name"],
                          "last_seen_age_hours": age_h})

    out_devices.sort(key=lambda r: (not r["is_coordinator"],
                                    r["last_seen_age_hours"] or 0))

    result: dict[str, Any] = {
        "db_path": str(db_path),
        "table_versions": data["tables"],
        "coordinator": {
            "ieee": coord_ieee,
            "name": name_map.get(coord_ieee, {}).get("name") if coord_ieee else None,
            "firmware_metadata": data["backup_meta"],
        },
        "device_count": len(out_devices),
        "stale_count": len(stale),
        "stale_hours_threshold": stale_hours,
        "stale_devices": stale,
        "devices": out_devices,
    }
    if include_neighbors:
        result["neighbors_raw"] = neighbors

    await ctx.audit.log(
        tool="zigbee_info",
        details={"device_count": len(out_devices), "stale_count": len(stale)},
        op_class="read",
    )
    return result


async def _resolve_ieee(ctx: HaOpsContext, ident: str) -> str | None:
    """Resolve an entity_id, device_id, or raw ieee to a lowercased ieee."""
    if ":" in ident and ident.count(":") >= 6:
        return ident.lower()  # looks like an ieee already

    reg = await _get_device_registry(ctx)
    name_map = _zha_ieee_map(reg)  # ieee -> {name, device_id}
    # device_id match
    for ieee, meta in name_map.items():
        if meta.get("device_id") == ident:
            return ieee

    # entity_id -> device_id -> ieee
    from ha_ops_mcp.tools.entity import _get_entity_registry
    ents = await _get_entity_registry(ctx)
    ent = next((e for e in ents if e.get("entity_id") == ident), None)
    if ent and ent.get("device_id"):
        for ieee, meta in name_map.items():
            if meta.get("device_id") == ent["device_id"]:
                return ieee
    return None


@registry.tool(
    name="haops_zha_reconfigure_device",
    description=(
        "Trigger a ZHA 'Reconfigure device' (re-interview + re-establish "
        "attribute-report bindings) for a single Zigbee device. Two-phase: "
        "1) call without confirm to preview, 2) confirm=true + token to run. "
        "WHEN TO USE: a ZHA device went silent / stuck after a ZHA reload "
        "(coordinator flash, entity enable/disable) — classic case is the "
        "Aqara FP1 (lumi.motion.ac01) presence sensor dropping its report "
        "bindings until reconfigured. This is the only thing that recovers "
        "such a device; an on-device reset button does not. "
        "Identify the device by ieee (00:15:8d:...), device_id, or any of "
        "its entity_ids. Runs over WebSocket (zha/devices/reconfigure)."
    ),
    params={
        "device": {
            "type": "string",
            "description": "ieee address, device_id, or an entity_id of the device",
        },
        "confirm": {"type": "boolean", "default": False,
                    "description": "Execute the reconfigure"},
        "token": {"type": "string", "description": "Confirmation token from preview"},
    },
)
async def haops_zha_reconfigure_device(
    ctx: HaOpsContext,
    device: str,
    confirm: bool = False,
    token: str | None = None,
) -> dict[str, Any]:
    ieee = await _resolve_ieee(ctx, device)
    if not ieee:
        return {"error": f"Could not resolve '{device}' to a ZHA device ieee. "
                "Pass an ieee, device_id, or a valid entity_id."}

    reg = await _get_device_registry(ctx)
    name = _zha_ieee_map(reg).get(ieee, {}).get("name")

    if not confirm:
        tk = ctx.safety.create_token(
            action="zha_reconfigure_device",
            details={"ieee": ieee, "name": name},
        )
        return {
            "preview": {"ieee": ieee, "name": name},
            "token": tk.id,
            "note": "Reconfigure re-interviews the device (~10-30s) and "
            "re-establishes report bindings. Harmless but the device may be "
            "briefly unavailable.",
            "message": "Call again with confirm=true and this token to reconfigure.",
        }

    if token is None:
        return {"error": "confirm=true requires a token"}
    try:
        token_data = ctx.safety.validate_token(token)
    except Exception as e:
        return {"error": str(e)}
    ieee = token_data.details.get("ieee", ieee)

    from ha_ops_mcp.connections.websocket import WebSocketError
    try:
        await ctx.ws.send_command("zha/devices/reconfigure", ieee=ieee)
    except WebSocketError as e:
        await ctx.audit.log(
            tool="zha_reconfigure_device",
            details={"ieee": ieee}, success=False, error=str(e), token_id=token,
        )
        return {"error": f"Reconfigure failed: {e}"}

    ctx.safety.consume_token(token)
    await ctx.audit.log(
        tool="zha_reconfigure_device",
        details={"ieee": ieee, "name": name}, token_id=token,
    )
    return {
        "success": True,
        "ieee": ieee,
        "name": name,
        "message": "Reconfigure initiated. The device re-interviews over the "
        "next ~10-30s; verify with haops_zigbee_info or its entity state.",
    }


@registry.tool(
    name="haops_zigbee_scan",
    description=(
        "Force a fresh ZHA topology/neighbor scan so haops_zigbee_info "
        "returns current LQI/route data instead of zigpy's hours-old "
        "snapshot. Runs over WebSocket (zha/topology/update). "
        "The scan is LONG-RUNNING on the HA side — HA only sends the WS "
        "result once the whole mesh has been walked (often >30s), so this "
        "tool fires the scan and returns without waiting for it to finish "
        "(status='initiated'). Allow ~30-60s (longer on big meshes), then "
        "call haops_zigbee_info to read the refreshed data. "
        "Read-mostly (no device config change) so no confirm step. "
        "No parameters."
    ),
    params={},
)
async def haops_zigbee_scan(ctx: HaOpsContext) -> dict[str, Any]:
    from ha_ops_mcp.connections.websocket import WebSocketError

    # zha/topology/update is a VALID command but its WS result only arrives
    # after the full mesh scan completes (verified live: no reply in 15s, and
    # no error frame either — an unknown command would error immediately).
    # So we fire it with a short timeout and treat the timeout as "started":
    # the scan runs server-side and refreshes zigbee.db regardless. A genuine
    # fast error (unknown_command on a future ZHA) replies quickly with a
    # "Command ... failed" message and is surfaced instead of masked.
    # (Timeout message text is owned by websocket.py send_command.)
    initiated = False
    try:
        await ctx.ws.send_command("zha/topology/update", timeout=8)
    except WebSocketError as e:
        if "Timeout waiting for response" in str(e):
            initiated = True
        else:
            return {"error": f"Topology scan request failed: {e}. The WS "
                    "command type may differ on your HA/ZHA version."}

    await ctx.audit.log(
        tool="zigbee_scan", details={"async": initiated}, op_class="read"
    )
    return {
        "success": True,
        "status": "initiated" if initiated else "completed",
        "message": (
            "Topology scan triggered; it runs asynchronously on HA (the WS "
            "result only returns after the full mesh walk). Allow ~30-60s, "
            "then call haops_zigbee_info for fresh LQI/routes."
            if initiated else
            "Topology scan completed. Call haops_zigbee_info for fresh data."
        ),
    }
