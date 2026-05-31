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
    # --- mutate ---
    "config_apply": ("mutate", "config"),  # sub-area refined from path
    "config_patch": ("mutate", "config"),
    "config_create": ("mutate", "config"),
    "dashboard_apply": ("mutate", "dashboard"),
    "dashboard_patch": ("mutate", "dashboard"),
    "entity_customize": ("mutate", "entity"),
    "entity_disable": ("mutate", "entity"),
    "entities_assign_area": ("mutate", "entity"),
    "helper_create": ("mutate", "helper"),
    "helper_update": ("mutate", "helper"),
    "integration_reload": ("mutate", "system"),
    "system_reload": ("mutate", "system"),
    "system_restart": ("mutate", "system"),
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
