#!/usr/bin/env bash
# =============================================================================
# dev-deploy.sh — Deploy ha-ops-mcp source to HA as a local addon
#
# Syncs the project source to /addons/ha-ops-mcp/ on the HA host.
# The Supervisor auto-discovers addons in /addons/ and builds the
# Dockerfile on the HA host itself — no local Docker build needed.
#
# Usage:
#   ./scripts/dev-deploy.sh                      # defaults
#   ./scripts/dev-deploy.sh --host 192.168.1.50  # custom host
# =============================================================================

set -euo pipefail

# ── Defaults ──
HA_HOST="${HA_HOST:-homeassistant.local}"
HA_USER="${HA_USER:-root}"
HA_SSH_PORT="${HA_SSH_PORT:-22}"
REMOTE_ADDON_DIR="/addons/ha-ops-mcp"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ADDON_SLUG="local_ha_ops_mcp"
REBUILD=0

# ── Parse args ──
while [[ $# -gt 0 ]]; do
    case "$1" in
        --host)    HA_HOST="$2"; shift 2 ;;
        --user)    HA_USER="$2"; shift 2 ;;
        --port)    HA_SSH_PORT="$2"; shift 2 ;;
        --rebuild) REBUILD=1; shift ;;
        --help)
            echo "Usage: $0 [--host HOST] [--user USER] [--port PORT] [--rebuild]"
            echo ""
            echo "Syncs the ha-ops-mcp source to /addons/ha-ops-mcp/ on the HA host."
            echo "The Supervisor discovers it as a local addon and builds the Docker"
            echo "image on the HA host."
            echo ""
            echo "Defaults:"
            echo "  --host     homeassistant.local  (or HA_HOST env var)"
            echo "  --user     root                 (or HA_USER env var)"
            echo "  --port     22                   (or HA_SSH_PORT env var)"
            echo "  --rebuild  if set, runs 'ha apps rebuild' after sync"
            echo ""
            echo "After deploying:"
            echo "  First time:  Settings > Apps > Install app > Local apps > Install"
            echo "  Updates:     use --rebuild, or manually trigger rebuild in HA"
            exit 0
            ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

SSH_TARGET="${HA_USER}@${HA_HOST}"
SSH_CMD="ssh -p ${HA_SSH_PORT}"

echo "╔══════════════════════════════════════════════════╗"
echo "║  ha-ops-mcp dev deploy                          ║"
echo "╠══════════════════════════════════════════════════╣"
echo "║  Host:   ${SSH_TARGET}:${HA_SSH_PORT}"
echo "║  Target: ${REMOTE_ADDON_DIR}"
echo "╚══════════════════════════════════════════════════╝"
echo ""

# ── Step 0: Refuse to deploy untagged commits ──
# sync-version.sh defaults to the latest git tag. HA Supervisor keys
# rebuilds off config.yaml's `version:` — if the deployed number matches
# what Supervisor already has, the rebuild is silently skipped. Two
# failure modes we need to prevent:
#   1. HEAD has commits ahead of the latest tag → sync-version would
#      downgrade to the old tag, and those new commits never reach HA.
#   2. HEAD is behind the latest tag → rare, but equally wrong.
# Require HEAD to sit exactly on the latest tag's commit. Either tag
# HEAD, or check out the tag you want to deploy.
echo "▶ Checking that HEAD matches the latest tag..."
LATEST_TAG=$(git -C "${REPO_ROOT}" describe --tags --abbrev=0 2>/dev/null || echo "")
if [[ -z "${LATEST_TAG}" ]]; then
    echo "  ✗ No tags in this repository. Create one first: git tag v0.1.0"
    exit 1
fi
LATEST_TAG_SHA=$(git -C "${REPO_ROOT}" rev-list -n1 "${LATEST_TAG}")
HEAD_SHA=$(git -C "${REPO_ROOT}" rev-parse HEAD)
if [[ "${HEAD_SHA}" != "${LATEST_TAG_SHA}" ]]; then
    HEAD_SHORT=$(git -C "${REPO_ROOT}" rev-parse --short HEAD)
    AHEAD=$(git -C "${REPO_ROOT}" rev-list --count "${LATEST_TAG}..HEAD" 2>/dev/null || echo "?")
    BEHIND=$(git -C "${REPO_ROOT}" rev-list --count "HEAD..${LATEST_TAG}" 2>/dev/null || echo "?")
    cat <<EOF

  ✗ HEAD (${HEAD_SHORT}) does not match latest tag ${LATEST_TAG}.
    ${AHEAD} commit(s) ahead, ${BEHIND} commit(s) behind.

    Why this matters: sync-version.sh will set config.yaml's version
    to ${LATEST_TAG}. If your HEAD has code changes ahead of that tag,
    those changes WILL be copied to the host — but HA Supervisor sees
    the same version number it already has and skips the rebuild, so
    your new code never starts.

    Fix:
      • If HEAD has shippable changes:   git tag vX.Y.Z && git push origin vX.Y.Z
      • To deploy an existing release:   git checkout ${LATEST_TAG}

EOF
    exit 1
fi
echo "  ✓ HEAD is at ${LATEST_TAG}"
echo ""

# ── Step 1: Sync version from git tag ──
echo "▶ Syncing version from git tag..."
"${REPO_ROOT}/scripts/sync-version.sh"
echo ""

# ── Step 2: Ensure remote app directory exists ──
echo "▶ Ensuring ${REMOTE_ADDON_DIR} exists on ${HA_HOST}..."
${SSH_CMD} "${SSH_TARGET}" "mkdir -p ${REMOTE_ADDON_DIR}"
echo "  ✓ Directory ready"

# ── Step 3: Clean and sync source files via scp ──
# rsync isn't available on HA OS — use scp instead
echo "▶ Cleaning old files on ${HA_HOST}..."
${SSH_CMD} "${SSH_TARGET}" "rm -rf ${REMOTE_ADDON_DIR}/*"

echo "▶ Copying source to ${HA_HOST}:${REMOTE_ADDON_DIR}..."
SCP_CMD="scp -P ${HA_SSH_PORT}"

# Copy top-level files
${SCP_CMD} \
    "${REPO_ROOT}/config.yaml" \
    "${REPO_ROOT}/Dockerfile" \
    "${REPO_ROOT}/build.yaml" \
    "${REPO_ROOT}/run.sh" \
    "${REPO_ROOT}/pyproject.toml" \
    "${REPO_ROOT}/README.md" \
    "${REPO_ROOT}/CHANGELOG.md" \
    "${REPO_ROOT}/DOCS.md" \
    "${REPO_ROOT}/icon.png" \
    "${REPO_ROOT}/logo.png" \
    "${SSH_TARGET}:${REMOTE_ADDON_DIR}/"

# Copy src/ directory tree
${SSH_CMD} "${SSH_TARGET}" "mkdir -p ${REMOTE_ADDON_DIR}/src"
${SCP_CMD} -r "${REPO_ROOT}/src/ha_ops_mcp" "${SSH_TARGET}:${REMOTE_ADDON_DIR}/src/"

# Copy translations/ (HA Supervisor renders option descriptions from here)
if [[ -d "${REPO_ROOT}/translations" ]]; then
    ${SCP_CMD} -r "${REPO_ROOT}/translations" "${SSH_TARGET}:${REMOTE_ADDON_DIR}/"
fi

echo "  ✓ Source synced"

# ── Step 4: Verify ──
echo "▶ Verifying remote files..."
${SSH_CMD} "${SSH_TARGET}" "ls -la ${REMOTE_ADDON_DIR}/config.yaml ${REMOTE_ADDON_DIR}/Dockerfile ${REMOTE_ADDON_DIR}/src/"
echo "  ✓ Files verified"

# ── Step 5: Rebuild (optional) ──
if [[ "${REBUILD}" == "1" ]]; then
    echo "▶ Rebuilding app on ${HA_HOST}..."
    ${SSH_CMD} "${SSH_TARGET}" "ha apps rebuild ${ADDON_SLUG}"
    echo "  ✓ Rebuild complete (app auto-restarts)"
fi

# ── Read deployed version from config.yaml ──
DEPLOYED_VERSION=$(awk -F'"' '/^version:/{print $2}' "${REPO_ROOT}/config.yaml")

echo ""
echo "══════════════════════════════════════════════════"
echo "  ✓ Deploy complete!  (v${DEPLOYED_VERSION})"
echo ""
if [[ "${REBUILD}" == "1" ]]; then
    echo "  App has been rebuilt and restarted."
    echo "  Check logs: ssh ${SSH_TARGET} 'ha apps logs ${ADDON_SLUG}'"
else
    echo "  Next steps (first time):"
    echo "    1. In HA, go to Settings > Apps > App Store"
    echo "    2. Click the three dots (top right) > Check for updates"
    echo "    3. The app appears under 'Local apps'"
    echo "    4. Click ha-ops-mcp > Install"
    echo ""
    echo "  Next steps (update):"
    echo "    Re-run with --rebuild, or manually rebuild in HA UI"
fi
echo "══════════════════════════════════════════════════"
