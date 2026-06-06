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
risk). Scoped/approved 2026-06-06; deferred to test the UI suite with the
owner token first. Related: [[project_ui_suite_program]].

