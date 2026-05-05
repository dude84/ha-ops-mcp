# Installation

## Option 1: HA Add-on (recommended for HA OS / Supervised)

1. In Home Assistant, go to **Settings > Apps > App Store**
2. Click the three dots menu (top right) > **Repositories**
3. Paste: `https://github.com/dude84/ha-ops-mcp`
4. Click **Add**, then close the dialog
5. Search for **ha-ops-mcp** and click **Install**
6. Go to the add-on's **Configuration** tab and set your options (see below)
7. Click **Start**
8. Check the **Log** tab — you should see `"ha-ops-mcp server created with 64 tools"`

An **HA Ops** sidebar panel appears automatically via ingress.

## Option 2: Dev deploy (SSH to HA)

For development or private repos. Syncs source to `/addons/ha-ops-mcp/` on your HA host.

**Prerequisites:** SSH access to your HA instance (SSH & Web Terminal addon).

```bash
git clone https://github.com/dude84/ha-ops-mcp.git
cd ha-ops-mcp

# Push code to HA
make deploy

# Push code + apply update (picks up version/schema changes)
make update

# Custom host/port:
./scripts/dev-deploy.sh --rebuild --host 192.168.1.50 --port 22
```

First time in HA: **Settings > Apps > App Store** > three dots > **Check for updates**. The addon appears under **Local add-ons**.

**Makefile targets** (`make help` for full list):
- `make deploy` — push code to HA via SCP
- `make update` — push code + store reload + ha apps update (preserves config)
- `make logs` — tail addon logs
- `make check` — ruff + mypy --strict + pytest

**Environment variables:**
- `HA_HOST` — hostname (default: `homeassistant.local`)
- `HA_USER` — SSH user (default: `root`)

## Option 3: Install from source (HA Container, Core, or remote)

**Prerequisites:**
- Python 3.11+
- Network access to your HA instance (REST API + WebSocket)
- A [long-lived access token](https://developers.home-assistant.io/docs/auth_api/#long-lived-access-token)
- For database tools: direct database access (SQLite file path, or MariaDB/PostgreSQL credentials)

```bash
git clone https://github.com/dude84/ha-ops-mcp.git
cd ha-ops-mcp
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

cp config.example.local.yaml config.local.yaml
# Edit config.local.yaml — set ha.url, ha.token, and optionally database.url

ha-ops-mcp --config config.local.yaml --verbose
```

For development: `pip install -e ".[dev]"`

## Configuration

For most users, the **default configuration works without changes**. The addon auto-uses the Supervisor token and auto-detects the database.

If you need to customise, the addon's **Configuration** tab exposes these options:

- **Token**: leave blank to use the Supervisor token (recommended). Only set a long-lived access token if you need specific permissions.
- **Transport**: `sse` (default) or `streamable-http`.
- **Database URL**: leave blank for auto-detect. Set explicitly for MariaDB or PostgreSQL.
- **Backup directory**: default `/backup/ha-ops-mcp`.
- **Backup retention**: `max_age_days` (default 30), `max_per_type` (default 100).
- **Log level**: `info` or `debug`.

For standalone installs, copy `config.example.local.yaml` to `config.local.yaml`. All values can be overridden with `HA_OPS_*` environment variables.

## OAuth authentication

OAuth is **enabled by default** since v0.27.0 for SSE and streamable-http transports. MCP clients must authenticate before using tools — this prevents unauthenticated access from the LAN.

### Disabling OAuth

If you're on a fully-trusted LAN with no remote access and want to skip auth:

**Addon:** set **auth_enabled** to `false` in the Configuration tab and restart.

**Standalone** (`config.local.yaml`):

```yaml
auth:
  enabled: false
```

Or via environment variable: `HA_OPS_AUTH_ENABLED=false`

### Issuer URL

The issuer URL tells MCP clients where to find the OAuth endpoints. For addon deployments, it is **auto-derived** from HA's internal URL — no configuration needed. If auto-detection picks the wrong hostname, set **auth_issuer_url** in the addon Configuration tab (e.g. `http://homeassistant.local:8901`).

### What it does

When enabled on SSE or streamable-http transports, the MCP SDK automatically:

1. Publishes OAuth metadata at `/.well-known/oauth-authorization-server`
2. Exposes `/authorize`, `/token`, `/register`, `/revoke` endpoints
3. Requires a valid `Authorization: Bearer <token>` on all MCP tool calls
4. Returns `401 Unauthorized` for unauthenticated requests

stdio transport is **never authenticated** regardless of this setting (it's a local process, not a network service).

### How MCP clients connect

OAuth-capable MCP clients (Claude Desktop, etc.) handle the flow automatically:

1. Client discovers the OAuth metadata endpoint
2. Client registers itself via `/register` (dynamic client registration)
3. Client follows the authorization code flow with PKCE
4. Client receives access + refresh tokens
5. All subsequent MCP requests include the Bearer token

Since ha-ops-mcp is a single-user admin tool, authorization requests are **auto-approved** — there is no consent screen. The admin enabled OAuth by configuring the server; that is the consent.

### Token lifetimes

- **Access tokens:** 24 hours (configurable via `auth.access_token_ttl`). Sliding window — TTL is extended on every successful verification, so an actively-used session never expires. Idle sessions still time out on schedule.
- **Refresh tokens:** 30 days (configurable via `auth.refresh_token_ttl`)
- **Authorization codes:** 5 minutes

Client registrations and tokens are persisted to `<data_dir>/oauth.json` and survive addon restarts.

### Claude Code (CLI)

Claude Code uses stdio transport locally — no OAuth needed. For remote connections over SSE with auth enabled, Claude Code supports OAuth natively:

```bash
claude mcp add --transport sse ha-ops http://<your-ha-address>:8901/sse
```

Claude Code will detect the 401, discover the OAuth metadata, and walk you through the auth flow.

## Recommended: client-side review mode for mutations

Every preview tool (`haops_dashboard_diff`, `haops_dashboard_patch`, `haops_config_patch`, `haops_config_create`, `haops_rollback`) now embeds a **REVIEW PROTOCOL** in its description that asks the controller LLM to paste `diff_rendered` to chat and stop for explicit user approval before calling the corresponding `*_apply`. That works in practice today (Claude Opus 4.7 obeys it), but it's *social* — bypass-prone if the LLM drifts.

For mechanical, not-bypassable enforcement, use Claude Code's permission system. Add to `~/.claude/settings.json`:

```json
{
  "permissions": {
    "ask": [
      "mcp__ha-ops__haops_dashboard_apply",
      "mcp__ha-ops__haops_config_apply",
      "mcp__ha-ops__haops_rollback"
    ]
  }
}
```

Every `*_apply` call now triggers Claude Code's native approval modal — outside the LLM's control. The flow becomes: LLM previews → diff renders in chat with `+`/`-` colourisation → LLM calls apply → Claude Code blocks the tool call until you click Approve.

For trivial changes you click once and continue. Claude Code also offers session-level "always allow this tool" if you want to relax mid-session. To revert, remove the entries from `permissions.ask` (or move them to `permissions.allow`).

A future server-side equivalent (`safety.review_mode` config + MCP elicitation) is planned for non-Claude-Code clients — see `docs/BACKLOG.md`.

### When the controller drifts: nudge it

The REVIEW PROTOCOL embedded in every preview tool's description is **advisory** — MCP tool descriptions are instructions the controller LLM reads, not contracts the server can enforce. In practice today (Claude Opus 4.7) the controller obeys, but expect drift across model versions, edge cases, or under heavy autonomy. When the controller paraphrases the diff instead of pasting `diff_rendered`, summarises in prose, or chains preview→apply without showing the diff, **you have to nudge it**. Three patterns, in increasing scope:

1. **Per-message nudge.** When you ask for a mutation, include "show me the formatted diff before applying" in the prompt. Cheapest, most targeted.

2. **Per-session nudge.** Drop this at the start of any session that will touch ha-ops mutations:

   > Whenever a `mcp__ha-ops__*` tool returns a `diff_rendered` field, paste its value verbatim as a fenced markdown ` ```diff ` block in your reply, before calling any `*_apply`. Always — even for trivial one-line changes, even when I've pre-approved. The fenced block is the only way I see the actual diff with red/green colouring.

   Sets the policy for the whole session. Lasts until you compact or restart.

3. **Per-project nudge.** Add the same instruction to the project's `CLAUDE.md` so every Claude Code session in this directory picks it up automatically. Pairs well with `permissions.ask` above — the LLM renders the diff (so you can review), Claude Code's modal blocks the apply (so you can approve mechanically).

**Why this is necessary at all:** Claude Code's tool-result panel (Ctrl+O) renders only the `structuredContent` JSON, with `\n` escaped inside string values — so the diff appears as a one-line wall of escaped JSON, not as a readable patch. Empirically confirmed in `~/_dev/claude-code-diff-render-test` (separate repo). The colourised view exists only when the controller pastes `diff_rendered` into a chat message, where Claude Code's normal markdown renderer applies the syntax highlighting. If the controller doesn't paste, you get no review surface — hence the nudge.

### Post-apply phrasing: name both IDs

Every `haops_*_apply` tool returns two unrelated identifiers that the controller tends to collapse into a single confusing line like *"Applied. Transaction c7b98acc…"*. Without context, the user can't tell what that id is for, or why a different token id appeared at preview time. They're not redundant — they have different lifecycles:

- The **confirmation token** (shown at preview) is a one-shot preview→apply gate. It is consumed on apply and cannot be reused.
- The **transaction id** (returned by apply) is the rollback handle. It stays live so you can pass it to `haops_rollback` later to undo the change.

Drop this into a session (or your project `CLAUDE.md`) so the controller states both every time:

> After any successful `mcp__ha-ops__*_apply` call, report back in this shape: *"Applied. Token `<original_token>` consumed → rollback id `<transaction_id>` (pass it to `haops_rollback` to undo)."* Always include both IDs verbatim, even for trivial changes. They are not redundant — the token was the one-shot preview→apply gate and is now dead; the transaction id is the rollback handle and is still live. Stating both makes the operation traceable and reminds me how to undo it.
