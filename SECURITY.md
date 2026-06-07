# Security Policy

_Last updated: 2026-06-07 (v0.50.0)._

## Reporting a vulnerability

Use **GitHub → Security → [Report a vulnerability](https://github.com/dude84/ha-ops-mcp/security/advisories/new)**
(private advisory). Please don't open a public issue for security reports.
Include the version (`haops_system_info` / addon version), transport
(stdio / SSE / streamable-http), and a reproduction. This is a personal
open-source project — best-effort response, no SLA.

## Supported versions

Fixes land on the **latest released minor** only. Update via the HA Supervisor
(addon → Update) before reporting; older versions are not patched.

| Version | Supported |
|---|---|
| latest minor (0.50.x) | ✅ |
| anything older | ❌ — update first |

## The trust model — read this first

**ha-ops-mcp is a power-user / single-admin tool. It is root-equivalent on your
Home Assistant. Treat it like SSH access to production — because that is what it
is.** It is not a sandbox, not least-privilege, and not multi-tenant.

The addon holds, by design:
- **`haops_exec_shell`** — arbitrary shell in the addon container.
- **`config:rw`** — full read/write of `/config`, including `secrets.yaml`.
- **`backup:rw`, `share:rw`** — the backup + share volumes.
- **`hassio_role: manager`** — add-on management + Core stop/start/restart.
- **`usb` / `uart`** — raw serial/USB devices (e.g. Zigbee coordinator flashing).

Given the above, the **real security boundary is the addon container itself**,
not any in-app permission. The mutation guards below are about *reversibility and
auditability*, not about containing a determined operator (a power user is
expected to be able to bypass them).

## Authentication

- **MCP transport:** OAuth is **enabled by default** (since v0.27.0) on the
  `sse` / `streamable-http` transports — Bearer-token enforced on every tool
  call. Single-admin server: authorization requests are auto-approved (no
  consent UI). Client registrations + tokens persist to
  `<backup_dir>/auth/oauth.json` (a mapped volume that survives addon
  reinstall and is **not** swept into HA snapshots). `stdio` transport is a
  local process and relies on local trust.
- **HA access:** `ha_token` is either a Supervisor token (default, via the
  Supervisor proxy) or a user **long-lived access token**. Supervisor API calls
  always use `SUPERVISOR_TOKEN` regardless. Tokens are never logged;
  `haops_auth_status` masks token values (first 8 chars only).

## UI / headless-browser surface (v0.50.0+)

The Debian image bundles **Playwright + Chromium (headless shell)** for the
`haops_ui_screenshot` / `haops_ui_perf` tools. Notes:
- These tools are **read-only** — they load a dashboard view and capture a
  screenshot / load metrics. They do **not** click or mutate (a future
  `haops_ui_interact` would; not present today).
- The headless browser authenticates to the HA **frontend** by injecting the
  configured user token into `localStorage['hassTokens']`. It therefore acts as
  whatever user that token belongs to (today: the owner). See `docs/BACKLOG.md`
  → dedicated `ha-ops-user` for the attribution trade-offs.
- Chromium runs `--no-sandbox` inside the container (standard for
  headless-in-container; the container is the boundary, per the trust model).

## Mutation safety (reversibility, not a boundary)

- **Two-phase confirmation** on mutating tools: preview returns a diff + token;
  a second call with the token applies.
- **Automatic backups** before filesystem / dashboard / DB writes.
- **In-session rollback** (savepoints) for recoverable operations.
- **Full audit log** — every mutation (and, optionally, reads) is appended to
  `operations.jsonl` under the backup volume.

Confirmation tokens are in-memory, single-use, and **not** auto-invalidated when
the target changes — staleness is each tool's concern (see
`docs/HA_QUIRKS.md` → "Confirmation tokens are NOT auto-invalidated").

## What this project is NOT

- Not a security product or a hardening layer for HA.
- Not safe to expose to untrusted users or the public internet.
- Not a replacement for HA's own auth/permissions.

If you need to limit blast radius, the lever is **don't install it**, or run it
on `stdio` locally — not in-app restrictions.
