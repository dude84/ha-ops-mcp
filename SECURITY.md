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
- **`docker_api`** — declared, but **inert unless you turn Protection mode off**.
  See below.

Given the above, the **real security boundary is the addon container itself**,
not any in-app permission.

### Docker socket access (v0.57.0+) — opt-in, and a genuine step up

The manifest declares `docker_api: true`, which powers `haops_container_list`,
`haops_container_logs` and `haops_container_exec`. These let the addon borrow
capabilities its own image lacks — the motivating case being compiling an
ESPHome firmware with the ESPHome addon's toolchain instead of shipping a second
copy of it.

**The declaration alone grants nothing.** Supervisor strips `docker_api` while
Protection mode is ON, and Protection mode is ON by default. Every installation
must deliberately switch it off (Settings → Add-ons → HA Ops MCP → Info) and
restart the addon. That checkbox *is* the opt-in gate; if you never touch it,
the three container tools return an explanation of how to enable them and do
nothing else.

**Understand what you are granting.** The Docker socket reaches *every*
container on the host, so this widens the blast radius from "all of Home
Assistant" to "all containers on the machine" — Supervisor and HA Core included.
Since the addon is already root-equivalent on HA (`config:rw` + `exec_shell`),
this is a smaller step than it first looks, but it is a real one and worth a
deliberate decision rather than being switched on by habit.

`haops_container_exec` is two-phase confirmed like `haops_exec_shell`, and its
token is bound to **both** the command and the target container, so a token
minted for a harmless command in one container cannot be replayed against
another. Every exec is written to the audit log with its container, command and
exit code. As with all mutation guards here: that is reversibility and
auditability, not containment.

One sharp edge worth knowing: the Docker Engine API has no "cancel exec". If an
exec times out, ha-ops-mcp **abandons** it — the process keeps running inside
the target container. The response says so explicitly. Don't launch unbounded
work through it. The mutation guards below are about *reversibility and
auditability*, not about containing a determined operator (a power user is
expected to be able to bypass them).

## Authentication

- **MCP transport:** a **static pre-shared Bearer token** is enforced by
  default (since v0.62.0, `auth_mode: token`) on the `sse` / `streamable-http`
  transports — checked constant-time on every request except the
  HA-ingress-authenticated sidebar paths (`/ui`, `/api/ui/*`). The token is
  set in the addon Configuration (`auth_token`, masked field) or
  auto-generated and persisted to `<backup_dir>/auth/static_token` (0600,
  printed once to the addon log at generation). Threat model: equivalent to
  OAuth's Bearer tokens on the same plain-HTTP LAN transport — both travel
  in cleartext on the wire; neither protects against an attacker who can
  sniff the LAN segment. Use a TLS reverse proxy if that is in your threat
  model.
- **OAuth (experimental since v0.62.0, default v0.27.0–v0.61.x):**
  `auth_mode: oauth` — Bearer-token enforced on every tool call, single-admin
  server, authorization requests auto-approved (no consent UI). Client
  registrations + tokens persist to `<backup_dir>/auth/oauth.json` (a mapped
  volume that survives addon reinstall and is **not** swept into HA
  snapshots). Demoted from default because Claude Code ~v2.1.234 (Aug 2026)
  silently refuses fresh OAuth flows to non-HTTPS endpoints
  (claude-code#3320, closed not-planned) — viable only behind TLS or from
  localhost. `stdio` transport is a local process and relies on local trust.
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
