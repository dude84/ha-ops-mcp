# OAuth authentication (enabled by default, v0.27.0+)

`auth/provider.py` implements the MCP SDK's `OAuthAuthorizationServerProvider`. The provider is passed to the FastMCP constructor only when both hold:

- `auth.enabled: true` in config, and
- the transport is SSE or streamable-http.

The SDK then mounts the OAuth endpoints and enforces Bearer tokens on all MCP tool calls. OAuth is enabled by default since v0.27.0; set `auth.enabled: false` to disable it.

The issuer URL is auto-derived from HA's `internal_url` via the Supervisor API. Override it with `auth.issuer_url` if auto-detection picks the wrong hostname. HTTP issuer URLs are accepted for local networks: the MCP SDK's HTTPS requirement is patched at startup, with a logged warning.

The provider is a single-user/admin server: it auto-approves all authorization requests, with no consent UI. Client registrations and tokens are persisted to `<data_dir>/oauth.json`. The store is loaded into memory on startup, flushed to disk on every write, and cleaned of expired entries on load.

`haops_auth_status` provides visibility; token values are masked to the first 8 chars. Clearing the OAuth store is an admin action, not an MCP tool, because calling such a tool would terminate the session making the call. It's done with the add-on Configuration's `clear_oauth_on_next_boot` checkbox, added in v0.33.8, which resets itself after firing.
