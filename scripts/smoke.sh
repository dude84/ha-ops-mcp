#!/usr/bin/env bash
# =============================================================================
# smoke.sh — in-image runtime / contract smoke test for ha-ops-mcp
#
# Asserts the BUILT IMAGE can actually import every dependency, load the
# server + all tool modules, resolve the DB drivers, and (when expected)
# launch the Playwright/Chromium browser stack.
#
# Why this exists: the pytest suite runs against mocks, so it passes
# identically on Alpine or Debian and CANNOT catch base-image-swap breakage
# (native driver load, missing system libs, browser launch). This script runs
# INSIDE the real container and is the gate for the Alpine→Debian migration:
#   - capture GREEN on the current Alpine image first  (SMOKE_EXPECT_BROWSER=0)
#   - after the Debian swap it must stay GREEN          (SMOKE_EXPECT_BROWSER=1)
#
# Usage (inside the addon container, or any built image):
#   PYTHON=/opt/ha-ops-mcp/bin/python ./smoke.sh
#   SMOKE_EXPECT_BROWSER=1 PYTHON=/opt/ha-ops-mcp/bin/python ./smoke.sh
#
# Env:
#   PYTHON                interpreter under test (default: addon venv, else python3)
#   SMOKE_EXPECT_BROWSER  1 = require chromium+playwright; 0 = optional (default)
# =============================================================================
set -uo pipefail

PYTHON="${PYTHON:-/opt/ha-ops-mcp/bin/python}"
command -v "$PYTHON" >/dev/null 2>&1 || PYTHON=python3
EXPECT_BROWSER="${SMOKE_EXPECT_BROWSER:-0}"

fail=0
pass() { echo "  ✓ $1"; }
bad()  { echo "  ✗ $1"; fail=1; }
warn() { echo "  ⚠ $1"; }

echo "▶ ha-ops-mcp smoke — python: ${PYTHON}"
"$PYTHON" --version || { echo "FATAL: no python at ${PYTHON}"; exit 2; }

echo "▶ Core imports (server + every tool module + connections)"
if "$PYTHON" - <<'PY'
import importlib, pkgutil
base = [
    "ha_ops_mcp", "ha_ops_mcp.server", "ha_ops_mcp.config",
    "ha_ops_mcp.connections.rest", "ha_ops_mcp.connections.websocket",
    "ha_ops_mcp.connections.database", "ha_ops_mcp.auth.provider",
]
for m in base:
    importlib.import_module(m)
import ha_ops_mcp.tools as T
for _, name, _ in pkgutil.iter_modules(T.__path__):
    importlib.import_module(f"ha_ops_mcp.tools.{name}")
PY
then pass "core + tools import"; else bad "core/tool import failed"; fi

echo "▶ Dependency drivers"
for mod in sqlalchemy aiosqlite aiomysql asyncpg mcp aiohttp websockets ruamel.yaml deepdiff rapidfuzz jsonpatch; do
    if "$PYTHON" -c "import importlib; importlib.import_module('${mod}')" 2>/dev/null; then
        pass "import ${mod}"
    else
        bad "import ${mod}"
    fi
done

echo "▶ Browser stack (SMOKE_EXPECT_BROWSER=${EXPECT_BROWSER})"
# Playwright manages its own Chromium (not on PATH), so the real contract is
# "can Playwright launch Chromium headless?" — not "is a chromium binary on PATH".
if "$PYTHON" -c "import playwright" 2>/dev/null; then
    pass "playwright python importable"
    if launch_ver="$("$PYTHON" - <<'PY' 2>/dev/null
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    b = p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
    print(b.version)
    b.close()
PY
    )"; then
        pass "chromium launches headless (v${launch_ver})"
    elif [ "${EXPECT_BROWSER}" = "1" ]; then
        bad "chromium launch FAILED (required)"
    else
        warn "chromium launch failed (ok on Alpine baseline)"
    fi
elif [ "${EXPECT_BROWSER}" = "1" ]; then
    bad "playwright MISSING (required)"
else
    warn "playwright absent (expected on Alpine baseline)"
fi

echo ""
if [ "${fail}" = "0" ]; then
    echo "SMOKE: PASS"
    exit 0
else
    echo "SMOKE: FAIL"
    exit 1
fi
