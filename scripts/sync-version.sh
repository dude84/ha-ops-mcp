#!/usr/bin/env bash
# =============================================================================
# sync-version.sh — Sync version across all files from git tag
#
# Reads the latest git tag (vX.Y.Z), strips the 'v' prefix, and updates:
#   - config.yaml          (HA addon version — what Supervisor shows)
#   - pyproject.toml        (Python package version)
#   - src/ha_ops_mcp/__init__.py  (runtime __version__)
#
# Usage:
#   ./scripts/bump-version.sh          # use latest git tag
#   ./scripts/bump-version.sh 0.2.0    # explicit version
# =============================================================================

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# ── Resolve version ──
if [[ $# -ge 1 ]]; then
    VERSION="$1"
else
    TAG=$(git -C "${REPO_ROOT}" describe --tags --abbrev=0 2>/dev/null || echo "")
    if [[ -z "$TAG" ]]; then
        echo "Error: no git tag found and no version argument provided"
        echo "Usage: $0 [VERSION]"
        exit 1
    fi
    # Strip 'v' prefix if present
    VERSION="${TAG#v}"
fi

echo "Bumping version to ${VERSION}"

# ── Update config.yaml (HA addon) ──
sed -i '' "s/^version: .*/version: \"${VERSION}\"/" "${REPO_ROOT}/config.yaml"
echo "  ✓ config.yaml"

# ── Update pyproject.toml ──
sed -i '' "s/^version = .*/version = \"${VERSION}\"/" "${REPO_ROOT}/pyproject.toml"
echo "  ✓ pyproject.toml"

# ── Update __init__.py ──
sed -i '' "s/^__version__ = .*/__version__ = \"${VERSION}\"/" \
    "${REPO_ROOT}/src/ha_ops_mcp/__init__.py"
echo "  ✓ src/ha_ops_mcp/__init__.py"

# ── Verify ──
echo ""
echo "Versions now:"
grep '^version' "${REPO_ROOT}/config.yaml"
grep '^version' "${REPO_ROOT}/pyproject.toml"
grep '^__version__' "${REPO_ROOT}/src/ha_ops_mcp/__init__.py"
