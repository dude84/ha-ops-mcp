# Approved backlog

Non-critical items that have been discussed, scoped, and approved for
implementation — just not scheduled yet. New items land here after they
survive a triage pass; truly speculative ideas stay in `_gaps/`.

When you pick one up: move it into a change plan, implement, then delete
the entry (not strike-through) on the merge commit.

---

## Dedicated `ha-ops-user` service account for addon auth

Instead of the addon authenticating as the owner (LLAT in `ha_token`), use a
dedicated **admin** Home Assistant user `ha-ops-user`. Same visibility (admin
sees all), but:

- **Audit**: every REST/WS action + headless UI session is attributed to
  `ha-ops-user` in the HA logbook/history — "what the MCP did" is separable from
  "what I did". Today (empty token) it's the Supervisor/anon context.
- **Revocation**: disable/revoke the one user → cut all addon access in one
  place, owner account untouched.
- **No owner clutter**: addon tokens live in its own profile.

Setup is one-time manual (create admin user → one LLAT → `ha_token`); the addon
should NOT self-create the user (writing `.storage/auth` + restart = lockout
risk) — unless the User-Account-Management feature below lands first, which can
create it cleanly via the WS admin API.

⚠️ **Profile must mirror the owner.** The headless UI capture renders as
`ha-ops-user`, so its frontend prefs (theme, dark/light/auto, default dashboard,
language, number/date format) must match the owner's — otherwise screenshots
show a *different* UI than what the owner actually sees, defeating the point.
Either copy the owner's `.storage/frontend.user_data.<user_id>` + theme settings
to the service user, or expose a per-call theme/color-scheme override on the UI
tools. Resolve before relying on `ha-ops-user` for visual work.

Scoped/approved 2026-06-06; deferred to test the UI suite with the owner token
first. Related: [[project_ui_suite_program]].

## Native user account management (UAM)

First-class tools to **create / update / delete / disable** HA users, instead of
hand-editing `.storage/auth` via shell (lockout risk + needs a restart).

Use HA's authenticated **admin WebSocket API** — no file edits, no restart:
`config/auth/list|create|update|delete` (update's `is_active` = disable/enable),
`config/auth_provider/homeassistant/create|admin_change_password` for
credentials. Reachable today via `haops_ws_command`; this promotes it to
first-class, audited, two-phase tools:

- `haops_user_list` (read)
- `haops_user_create` (mutate, two-phase) — name, admin, local_only, optional password
- `haops_user_update` (mutate, two-phase) — rename / group / **active** (disable)
- `haops_user_delete` (mutate, two-phase) — back up the auth entry first (irreversible)

Bonus: lets the addon **bootstrap `ha-ops-user`** (create admin + password +
mirror profile) from a one-time owner token, closing the chicken-egg above.
Approved 2026-06-06 (reclassified from `_gaps/`). Related:
[[project_ui_suite_program]].

