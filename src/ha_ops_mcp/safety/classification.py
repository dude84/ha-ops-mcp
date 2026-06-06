"""Operation classification for the Timeline / audit log.

Single source of truth mapping each tool to an *op-class* (how risky the
operation is) and an *area* (which subsystem it touches). Both the audit
logger (stamping entries at write time) and the UI timeline endpoint
(deriving fields for legacy entries that predate this module) call
``classify()`` so the two never drift.

op_class:
    read        — observes state, changes nothing.
    mutate      — changes state, recoverable / non-destructive.
    destructive — irreversible or data-loss (deletes, purges, prunes).

area: the subsystem the operation touches. See ``_AREAS`` for the set.
"""

from __future__ import annotations

from pathlib import Path

# Tool name (bare, no `haops_` prefix) -> (op_class, area).
# db_execute and the config_* tools are refined by content in classify().
CLASSIFICATION: dict[str, tuple[str, str]] = {
    # --- read-only ---
    "entity_list": ("read", "entity"),
    "entity_state": ("read", "entity"),
    "entity_audit": ("read", "entity"),
    "entity_find": ("read", "entity"),
    "entity_history": ("read", "entity"),
    "config_read": ("read", "config"),
    "config_search": ("read", "config"),
    "config_validate": ("read", "config"),
    "dashboard_list": ("read", "dashboard"),
    "dashboard_get": ("read", "dashboard"),
    "dashboard_diff": ("read", "dashboard"),
    "dashboard_resources": ("read", "dashboard"),
    "dashboard_validate_yaml": ("read", "dashboard"),
    "db_query": ("read", "database"),
    "db_health": ("read", "database"),
    "db_statistics": ("read", "database"),
    "system_info": ("read", "system"),
    "system_logs": ("read", "system"),
    "self_check": ("read", "system"),
    "tools_check": ("read", "system"),
    "auth_status": ("read", "system"),
    "addon_list": ("read", "addon"),
    "addon_info": ("read", "addon"),
    "addon_logs": ("read", "addon"),
    "registry_query": ("read", "registry"),
    "device_info": ("read", "registry"),
    "references": ("read", "references"),
    "refactor_check": ("read", "references"),
    "logbook": ("read", "system"),
    "automation_trace": ("read", "automation"),
    "service_list": ("read", "service"),
    "template_render": ("read", "system"),
    "backup_list": ("read", "backup"),
    "batch_preview": ("read", "config"),
    "helper_list": ("read", "helper"),
    "zigbee_info": ("read", "system"),
    "zigbee_scan": ("read", "system"),
    "monitor_entity": ("read", "entity"),
    "ui_screenshot": ("read", "system"),
    "ui_perf": ("read", "system"),
    # --- mutate ---
    "config_apply": ("mutate", "config"),  # sub-area refined from path
    "config_patch": ("mutate", "config"),
    "config_create": ("mutate", "config"),
    "dashboard_apply": ("mutate", "dashboard"),
    "dashboard_patch": ("mutate", "dashboard"),
    "entity_customize": ("mutate", "entity"),
    "entity_toggle": ("mutate", "entity"),
    "entities_assign_area": ("mutate", "entity"),
    "helper_create": ("mutate", "helper"),
    "helper_update": ("mutate", "helper"),
    "integration_reload": ("mutate", "system"),
    "system_reload": ("mutate", "system"),
    "system_restart": ("mutate", "system"),
    "system_core": ("mutate", "system"),
    "zha_reconfigure_device": ("mutate", "system"),
    "ws_command": ("mutate", "system"),
    "system_backup": ("mutate", "backup"),
    "addon_restart": ("mutate", "addon"),
    "service_call": ("mutate", "service"),
    "scene_activate": ("mutate", "scene"),
    "script_run": ("mutate", "script"),
    "automation_trigger": ("mutate", "automation"),
    "batch_apply": ("mutate", "config"),
    "backup_revert": ("mutate", "backup"),
    "rollback": ("mutate", "config"),
    "exec_shell": ("mutate", "shell"),
    # --- destructive (irreversible / data loss) ---
    "entity_remove": ("destructive", "entity"),
    "helper_delete": ("destructive", "helper"),
    "db_purge": ("destructive", "database"),
    "backup_prune": ("destructive", "backup"),
}

_READ_VERBS = frozenset({"select", "pragma", "explain", "with", "show"})
_MUTATE_VERBS = frozenset({"insert", "update", "replace"})
_DESTRUCTIVE_VERBS = frozenset({"delete", "drop", "truncate", "alter"})


def classify(tool: str, details: dict[str, object] | None) -> tuple[str, str]:
    """Return ``(op_class, area)`` for a tool call, refining by content.

    ``tool`` is the bare audit name (``config_apply``), not the
    ``haops_``-prefixed form. ``details`` is the audit details dict (may be
    None for cheap callers). Unknown tools default to ``("mutate", "misc")``
    — the conservative choice, so a new mutating tool never renders as a
    harmless read before it's added to the table.
    """
    details = details or {}
    op_class, area = CLASSIFICATION.get(tool, ("mutate", "misc"))
    if tool == "db_execute":
        return _sql_class(str(details.get("sql", ""))), "database"
    if tool in ("config_apply", "config_patch", "config_create"):
        return op_class, _config_subarea(str(details.get("path", "")))
    return op_class, area


# Operation-specific type label for the Timeline row — more descriptive than
# the 3-tier op_class (which only drives the risk-dot color). e.g. db_execute
# DELETE reads "db delete", config_create reads "new file", service_call reads
# "service call". Risk stays in op_class; this is the human verb.
_TYPE_LABELS: dict[str, str] = {
    "service_call": "service call", "scene_activate": "activate scene",
    "script_run": "run script", "automation_trigger": "trigger",
    "integration_reload": "reload", "system_reload": "reload",
    "system_restart": "restart", "system_backup": "backup",
    "system_core": "core power", "zha_reconfigure_device": "zha reconfigure",
    "zigbee_scan": "zigbee scan", "ws_command": "ws command",
    "addon_restart": "restart addon",
    "dashboard_apply": "dashboard edit", "dashboard_patch": "dashboard patch",
    "entity_remove": "remove", "entity_toggle": "toggle enable",
    "entity_customize": "customize", "entities_assign_area": "assign area",
    "helper_create": "new helper", "helper_update": "edit helper",
    "helper_delete": "delete helper",
    "backup_revert": "revert", "backup_prune": "prune", "rollback": "rollback",
    "exec_shell": "shell", "batch_apply": "batch", "batch_preview": "preview",
    "db_purge": "db purge", "db_query": "db read", "db_health": "db health",
    "db_statistics": "db stats",
    # reads
    "entity_list": "list", "entity_state": "state", "entity_audit": "audit",
    "entity_find": "find", "entity_history": "history",
    "config_read": "read", "config_search": "search", "config_validate": "validate",
    "dashboard_list": "list", "dashboard_get": "get", "dashboard_diff": "diff",
    "dashboard_resources": "resources", "dashboard_validate_yaml": "validate",
    "system_info": "info", "system_logs": "logs", "self_check": "check",
    "tools_check": "check", "auth_status": "status",
    "addon_list": "list", "addon_info": "info", "addon_logs": "logs",
    "registry_query": "query", "device_info": "info",
    "references": "refs", "refactor_check": "refactor", "logbook": "logbook",
    "automation_trace": "trace", "service_list": "list",
    "template_render": "template", "backup_list": "list", "helper_list": "list",
}


def type_label(tool: str, details: dict[str, object] | None) -> str:
    """Human, operation-specific verb for the Timeline row's type tag.

    Refines db_execute by SQL verb and config writes by create-vs-patch;
    everything else comes from ``_TYPE_LABELS`` with a tool-name fallback.
    """
    details = details or {}
    if tool == "db_execute":
        oc = _sql_class(str(details.get("sql", "")))
        return {"read": "db read", "mutate": "db write", "destructive": "db delete"}[oc]
    if tool in ("config_apply", "config_patch", "config_create"):
        if tool == "config_create" or details.get("old_content") == "":
            return "new file"
        return "patch"
    return _TYPE_LABELS.get(tool, (tool or "op").rsplit("_", 1)[-1])


def _sql_class(sql: str) -> str:
    """Classify a SQL statement by its leading keyword.

    Strips leading line/block comments and whitespace, then matches the
    first word. Unparseable / unknown verbs default to ``destructive`` —
    we'd rather over-warn than render a silent ``DELETE`` as a read.
    """
    s = (sql or "").lstrip()
    # Strip leading `--` line comments and `/* */` block comments.
    while s:
        if s.startswith("--"):
            nl = s.find("\n")
            s = "" if nl == -1 else s[nl + 1:].lstrip()
        elif s.startswith("/*"):
            end = s.find("*/")
            s = "" if end == -1 else s[end + 2:].lstrip()
        else:
            break
    if not s:
        return "destructive"
    verb = s.split(None, 1)[0].lower().strip("(")
    if verb in _READ_VERBS:
        return "read"
    if verb in _MUTATE_VERBS:
        return "mutate"
    if verb in _DESTRUCTIVE_VERBS:
        return "destructive"
    return "destructive"


def _config_subarea(path: str) -> str:
    """Map a config file path to a sub-area from its basename.

    Mirrors the basename handling the diff header already uses in
    ``tools/config.py`` so the timeline area and the diff title agree.
    """
    name = Path(path).name.lower() if path else ""
    if name.startswith("automations") or name.startswith("automation"):
        return "automation"
    if name.startswith("scripts") or name.startswith("script"):
        return "script"
    if name.startswith("scenes") or name.startswith("scene"):
        return "scene"
    return "config"
