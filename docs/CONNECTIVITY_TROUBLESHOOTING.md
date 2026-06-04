# Connectivity Troubleshooting (Client ↔ ha-ops-mcp)

This is **not** an HA quirk — it's about the client environment (Mac + Claude Code + terminal)
reaching the MCP server. For HA-side behavior see `HA_QUIRKS.md`. For the version baseline to
diff against, see `KNOWN_GOOD_ENV.md`.

---

## #1 — "Failed to connect" but `curl` works → macOS Local Network Privacy

**Symptom:** Claude Code shows ha-ops `✗ Failed to connect`. From the same machine,
`curl http://homeassistant.local:8901/mcp` returns `401` (i.e. server is alive and reachable).

**Cause:** macOS **Local Network Privacy**. macOS attributes a CLI tool's LAN-access permission
to its responsible parent **app bundle** (iTerm2 / Terminal / Ghostty / VS Code), *not* to the
`claude`/bun binary. When the terminal app updates, its code signature changes, macOS treats it
as a new app, and the **Local Network grant is reset**. Every process the terminal spawns
(zsh → claude → bun) then loses LAN socket access.

**Why curl still works:** `/usr/bin/curl` is an Apple-signed system binary, exempt from Local
Network Privacy. So the smoking gun is **curl works, `claude` doesn't**.

**Signature in logs** (`~/Library/Caches/claude-cli-nodejs/<project>/mcp-logs-ha-ops/*.jsonl`):
```
Testing basic HTTP connectivity to http://homeassistant.local:8901/mcp
HTTP Connection failed after 4ms: ... (code: FailedToOpenSocket, errno: none)
```
Failure is **instant** (~4 ms, no network timeout) with `errno: none` — the OS denied it, no
syscall happened. `EHOSTUNREACH` / `ConnectionRefused` with `errno: none` are the same thing.

**3-line confirm** (public ok, LAN blocked, loopback ok = LNP):
```bash
node -e "fetch('https://api.github.com/zen').then(r=>r.text()).then(t=>console.log('public OK',t.slice(0,20)))"
node -e "fetch('http://10.0.0.150:8901/mcp',{method:'POST'}).then(r=>console.log('LAN OK',r.status)).catch(e=>console.log('LAN',e.cause?.code||e.message))"
node -e "fetch('http://127.0.0.1:1/').catch(e=>console.log('loopback',e.cause?.code||e.message))"
```

**Fix:**
1. System Settings → Privacy & Security → **Local Network**.
2. Toggle the terminal app (**iTerm**) OFF→ON. If not listed, trigger a LAN request once so macOS
   registers it, then re-check.
3. **Cmd-Q the terminal and relaunch** — Local Network changes only apply on app restart.
4. `claude mcp list` should flip to `! Needs authentication`. Then `/mcp` → ha-ops → Authenticate.

---

## #2 — `Protected resource ... does not match expected` → don't use an IP in the URL

If you "fix" #1 by swapping the MCP URL to a raw IP (`http://10.0.0.150:8901/mcp`), OAuth then
fails with:
```
SDK auth failed: Protected resource http://homeassistant.local:8901/ does not match expected http://10.0.0.150:8901/mcp (or origin)
```
HA's OAuth protected-resource metadata pins `resource: http://homeassistant.local:8901/`. RFC 8707
requires the client URL to match the advertised resource/origin. **Keep the mDNS hostname** in the
MCP config:
```
claude mcp add --transport http ha-ops http://homeassistant.local:8901/mcp
```

---

## Don't chase these (red herrings)

- **HA core update** — verify with `curl` first; if curl reaches the endpoint, HA is not the cause.
  (During the 2026-06-04 incident HA was on 2026.5.4 and hadn't even taken 2026.6.)
- **Claude Code update** — coincidental; LNP attribution is to the *terminal*, not `claude`.
- **MCP config / OAuth store** — an empty token (`has_access:false`) just means re-auth, not breakage.
- **Bun `.local` mDNS** — once LNP is restored, `homeassistant.local` resolves and connects fine.

---

## Triage order (fastest first)

1. `curl -i -X POST http://homeassistant.local:8901/mcp` — reachable? If `401`, server is fine → it's the client.
2. Read the latest `mcp-logs-ha-ops/*.jsonl` — look for the fast `FailedToOpenSocket`/`errno: none` line.
3. Apply **#1** (Local Network grant + terminal restart).
4. Diff live versions against `KNOWN_GOOD_ENV.md` to spot which component moved.
