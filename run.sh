#!/command/with-contenv bashio
# shellcheck shell=bash
# ==============================================================================
# ha-ops-mcp addon startup script
# ==============================================================================

declare ha_token
declare transport
declare db_url
declare backup_dir
declare backup_max_age_days
declare backup_max_per_type
declare log_level
declare refindex_exclude_dirs
declare refindex_exclude_globs
declare refindex_exclude_dashboards
declare refindex_dynamic_entity_patterns

# Read addon options
ha_token=$(bashio::config 'ha_token')
transport=$(bashio::config 'transport')
db_url=$(bashio::config 'db_url')
backup_dir=$(bashio::config 'backup_dir')
backup_max_age_days=$(bashio::config 'backup_max_age_days')
backup_max_per_type=$(bashio::config 'backup_max_per_type')
log_level=$(bashio::config 'log_level')

# Token and URL routing:
# - No token configured → use SUPERVISOR_TOKEN + Supervisor proxy URLs
# - Custom token (long-lived) → go directly to HA Core (Supervisor proxy
#   would reject a user token since it expects SUPERVISOR_TOKEN)
if bashio::var.is_empty "${ha_token}"; then
    ha_token="${SUPERVISOR_TOKEN}"
    # Both REST and WS go through Supervisor proxy — the Supervisor
    # accepts SUPERVISOR_TOKEN and translates auth for HA Core.
    ha_url="http://supervisor/core"
    ws_url="http://supervisor/core"
    bashio::log.info "Using Supervisor token (auto-provisioned)"
else
    ha_url="http://homeassistant:8123"
    ws_url="http://homeassistant:8123"
    bashio::log.info "Using custom token (long-lived access token)"
fi

# Export as environment variables for ha-ops-mcp config
export HA_OPS_TOKEN="${ha_token}"
export HA_OPS_URL="${ha_url}"
export HA_OPS_WS_URL="${ws_url}"
export HA_OPS_TRANSPORT="${transport}"
export HA_OPS_CONFIG_ROOT="/config"
export HA_OPS_BACKUP_DIR="${backup_dir}"
export HA_OPS_BACKUP_MAX_AGE_DAYS="${backup_max_age_days}"
export HA_OPS_BACKUP_MAX_PER_TYPE="${backup_max_per_type}"

if bashio::var.has_value "${db_url}"; then
    export HA_OPS_DB_URL="${db_url}"
fi

# OAuth auth — enabled by default since v0.27.0
auth_enabled=$(bashio::config 'auth_enabled')
if bashio::var.true "${auth_enabled}"; then
    export HA_OPS_AUTH_ENABLED="true"
    auth_issuer_url=$(bashio::config 'auth_issuer_url')
    if bashio::var.has_value "${auth_issuer_url}"; then
        export HA_OPS_AUTH_ISSUER_URL="${auth_issuer_url}"
    else
        # Auto-derive hostname from HA's internal_url. The OAuth issuer
        # must be reachable from MCP clients, so values like null,
        # "none", "localhost", or loopback addresses are unusable —
        # fall back to the mDNS default in those cases.
        ha_hostname=$(curl -s -H "Authorization: Bearer ${SUPERVISOR_TOKEN}" \
            http://supervisor/core/api/config \
            | python3 -c '
import sys, json
from urllib.parse import urlparse
try:
    url = json.loads(sys.stdin.read()).get("internal_url") or ""
    host = urlparse(url).hostname or ""
    if host.lower() in ("", "none", "localhost", "127.0.0.1", "::1"):
        host = ""
    print(host)
except Exception:
    print("")
' 2>/dev/null)
        if bashio::var.has_value "${ha_hostname}"; then
            export HA_OPS_AUTH_ISSUER_URL="http://${ha_hostname}:8901"
            bashio::log.info "OAuth issuer auto-detected: http://${ha_hostname}:8901"
        else
            export HA_OPS_AUTH_ISSUER_URL="http://homeassistant.local:8901"
            bashio::log.warning "Could not derive OAuth issuer from HA internal_url — defaulting to http://homeassistant.local:8901. Set 'auth_issuer_url' in addon Configuration to override."
        fi
    fi
    mkdir -p /data
fi

# Set log level
case "${log_level}" in
    debug)   verbose_flag="--verbose" ;;
    *)       verbose_flag="" ;;
esac

# Ensure backup directory exists
mkdir -p "${backup_dir}"

bashio::log.info "Starting ha-ops-mcp..."
bashio::log.info "  Transport: ${transport}"
bashio::log.info "  Config root: /config"
bashio::log.info "  Backup dir: ${backup_dir}"
bashio::log.info "  Retention: ${backup_max_age_days} days / ${backup_max_per_type} per type"
bashio::log.info "  DB URL: ${db_url:-auto-detect}"
bashio::log.info "  OAuth: ${auth_enabled:-false}"

# Run the MCP server
exec /opt/ha-ops-mcp/bin/ha-ops-mcp ${verbose_flag}
