# Contributing to ha-ops-mcp

## Dev setup

```bash
git clone https://github.com/dude84/ha-ops-mcp.git
cd ha-ops-mcp
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp config.example.local.yaml config.local.yaml
# Edit config.local.yaml with your HA instance details
```

## Quality gates

All three must pass before opening a PR:

```bash
make check          # Runs ruff + mypy --strict + pytest
```

Or individually:

```bash
make lint           # ruff only
make typecheck      # mypy --strict only
make test           # pytest only
```

## Deployment (testing against real HA)

```bash
make deploy         # Push code to HA via SCP
make update         # Push code + store reload + ha apps update (preserves config)
make logs           # Tail addon logs via SSH
```

## Architecture rules

These are non-negotiable — they exist for good reasons.

- **Async everywhere.** All tool handlers, connections, and I/O are async. No sync blocking calls.
- **No ORM.** Database access uses `sqlalchemy.text()` through the `DatabaseBackend` interface. Raw SQL only.
- **Filesystem-first.** When data is available from both filesystem and API (entity registry, dashboard configs), prefer the filesystem. API is a fallback.
- **ruamel.yaml, not PyYAML.** Config edits must preserve comments. PyYAML strips them.
- **Tool descriptions are LLM-facing docs.** Every tool's `description` tells an LLM when to use it, what parameters mean, and what the output looks like. Write them for an AI reader, not a human API doc.
- **Two-phase confirmation is the safety mechanism.** All mutating tools require preview + explicit confirmation. There are no pattern denylists — the caller is responsible for understanding what a command or query does.

## Adding a tool

1. **Pick the right module** in `src/ha_ops_mcp/tools/` — `db.py`, `config.py`, `dashboard.py`, `entity.py`, `system.py`, `service.py`, `backup.py`, `shell.py`, or `addon.py`. Create a new module only if the tool doesn't fit any existing category.

2. **Register via the decorator:**

```python
from ha_ops_mcp.server import registry

@registry.tool(
    name="haops_your_tool",
    description="When to use this, parameters, output format...",
    params={...},
)
async def haops_your_tool(ctx: HaOpsContext, ...) -> dict[str, Any]:
    ...
```

3. **Follow the safety contract** (for mutating tools):
   - Phase 1 (preview): return a diff/summary + confirmation token via `ctx.safety.create_token()`
   - Phase 2 (apply): validate token, create rollback savepoint via `ctx.rollback.begin()` + `txn.savepoint()`, perform the mutation, consume token, commit transaction, log to audit
   - Persistent backup: call `ctx.backup.backup_file()` / `backup_dashboard()` etc. before writing

4. **Add the import** in `src/ha_ops_mcp/server.py` (the tool imports block).

5. **Write tests** — at minimum: preview returns token, blocked input is rejected, confirm executes, invalid token fails.

6. **Update the README** tool table.

## Commit style

Imperative mood, reference the tool group:

```
Add haops_db_optimize tool for post-purge defragmentation
Fix haops_config_apply not preserving YAML anchors
Update entity audit to detect disabled-but-not-unavailable entities
```

Commits should be signed with:
```
Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com>
```
when AI-assisted.

## Versioning

The version is defined by the **git tag** (e.g., `v0.2.0`). Three files must stay in sync:

| File | Field |
|---|---|
| `config.yaml` | `version: "0.2.0"` (HA addon — what Supervisor shows) |
| `pyproject.toml` | `version = "0.2.0"` (Python package) |
| `src/ha_ops_mcp/__init__.py` | `__version__ = "0.2.0"` (runtime) |

**To release a new version:**

```bash
# 1. Tag the release
git tag -a v0.2.0 -m "v0.2.0 — description"
git push origin v0.2.0

# 2. Sync version files from the tag
./scripts/sync-version.sh

# 3. Commit the version bump
git add config.yaml pyproject.toml src/ha_ops_mcp/__init__.py
git commit -m "Bump version to 0.2.0"
git push
```

The `sync-version.sh` script reads the latest git tag, strips the `v` prefix, and updates all three files. It's also called automatically by `dev-deploy.sh` before syncing to HA.

You can also pass an explicit version: `./scripts/sync-version.sh 0.2.0`

## PR checklist

- [ ] All three quality gates pass (`ruff`, `mypy --strict`, `pytest`)
- [ ] New tools have tests
- [ ] Tool descriptions are written for LLM comprehension
- [ ] Mutating tools use two-phase confirmation
- [ ] README tool table updated if tools were added
- [ ] No secrets, credentials, or personal data in the diff
