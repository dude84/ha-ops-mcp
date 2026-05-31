// Fake but realistic ha-ops audit data for the UI kit. Mirrors the shapes the
// real /api/ui/timeline, /backups, /self_check, /tools_check endpoints return.
window.HAOPS_DATA = {
  version: '0.34.1',

  timeline: [
    {
      timestamp: '2026-04-21T14:03:07',
      op_class: 'mutate', area: 'automation', tool: 'config_apply', success: true,
      summary: 'Patched automations.yaml — "Morning lights" delay 30 → 45s, enabled flag added',
      diff_present: true, transaction_id: 'tx_8f2a',
      backup_path: '/config/backups/config/automations.yaml.20260421T140306.bak',
      token_id: 'tok_4b1e',
      details_excerpt: { path: '/config/automations.yaml' },
      diff:
`--- a/automations.yaml
+++ b/automations.yaml
@@ -12,6 +12,7 @@
   alias: "Morning lights"
   mode: single
-  delay: 30
+  delay: 45
+  enabled: true
   entity_id: light.kitchen`
    },
    {
      timestamp: '2026-04-21T13:58:44',
      op_class: 'mutate', area: 'dashboard', tool: 'dashboard_patch', success: true,
      summary: 'Moved 4 energy cards from Overview → new Energy view on climate dashboard',
      diff_present: true,
      paired_with: { relation: 'rolled_back_by', index: 2, tool: 'rollback', timestamp: '2026-04-21T13:59:10' },
      details_excerpt: { dashboard_id: 'climate' },
      diff:
`Changed values:
  views[1].title: "Energy"
Added items:
+ views[1].cards[0]: { type: energy-distribution }
+ views[1].cards[1]: { type: energy-date-selection }
Moved:
~ views[0].cards[3] → views[1].cards[2]
~ views[0].cards[4] → views[1].cards[3]`
    },
    {
      timestamp: '2026-04-21T13:59:10',
      op_class: 'mutate', area: 'config', tool: 'rollback', success: true,
      summary: 'Reverted dashboard_patch on climate — restored Overview layout',
      diff_present: false,
      paired_with: { relation: 'reverts', index: 1, tool: 'dashboard_patch', timestamp: '2026-04-21T13:58:44' },
      details_excerpt: { transaction_id: 'tx_7c01', targets: 1 }
    },
    {
      timestamp: '2026-04-21T13:41:22',
      op_class: 'destructive', area: 'database', tool: 'db_purge', success: true,
      summary: 'Purged recorder rows older than 14 days — 412,883 states, 88,210 events removed',
      diff_present: false,
      backup_path: '/config/backups/db/recorder_purge.20260421T134118.sql',
      details_excerpt: { keep_days: 14, states_deleted: 412883, events_deleted: 88210, freed_bytes: 486539264 }
    },
    {
      timestamp: '2026-04-21T13:30:05',
      op_class: 'destructive', area: 'entity', tool: 'entity_remove', success: false,
      summary: 'Remove sensor.old_power_meter — blocked: still referenced by 2 automations',
      diff_present: false,
      error: 'RefactorError: sensor.old_power_meter referenced in automations.yaml (lines 44, 91). Update references first or use force=true.',
      details_excerpt: { entity_id: 'sensor.old_power_meter', references: 2 }
    },
    {
      timestamp: '2026-04-21T13:12:50',
      op_class: 'mutate', area: 'config', tool: 'config_create', success: true,
      summary: 'Created template sensor "daily_energy_cost" in configuration.yaml, validated + reloaded',
      diff_present: true,
      backup_path: '/config/backups/config/configuration.yaml.20260421T131248.bak',
      details_excerpt: { path: '/config/configuration.yaml' },
      diff:
`--- a/configuration.yaml
+++ b/configuration.yaml
@@ -34,3 +34,9 @@
 template:
   - sensor:
+      - name: "Daily energy cost"
+        unique_id: daily_energy_cost
+        unit_of_measurement: "EUR"
+        state: >
+          {{ (states('sensor.energy_today') | float * 0.34) | round(2) }}`
    },
    {
      timestamp: '2026-04-21T12:55:31',
      op_class: 'destructive', area: 'backup', tool: 'backup_prune', success: true,
      summary: 'Retention pass — pruned 9 backups (3 config, 4 dashboard, 2 db), freed 38.2 MB',
      diff_present: false,
      details_excerpt: { pruned_count: 9, bytes_freed: 40056422 }
    },
    // reads — only shown when "Show reads" is on
    {
      timestamp: '2026-04-21T14:02:55', read: true,
      op_class: 'read', area: 'database', tool: 'db_health', success: true,
      summary: 'Recorder DB health — 1.4 GB, 1.2M states, SQLite, no integrity issues',
      diff_present: false,
      details_excerpt: { backend: 'sqlite', size_bytes: 1503238553, integrity: 'ok' }
    },
    {
      timestamp: '2026-04-21T14:01:10', read: true,
      op_class: 'read', area: 'references', tool: 'refactor_check', success: true,
      summary: 'Mapped references for sensor.energy_grid — 6 references across 3 surfaces',
      diff_present: false,
      details_excerpt: { entity_id: 'sensor.energy_grid', references: 6 }
    }
  ],

  backups: {
    backup_dir: '/config/backups',
    summary: {
      total_count: 128, total_bytes: 1503238553,
      per_type: {
        config:    { count: 47, bytes: 2418176,   oldest_ts: '2026-03-22T08:11:00', newest_ts: '2026-04-21T14:03:06' },
        dashboard: { count: 31, bytes: 8912470,   oldest_ts: '2026-03-25T19:40:00', newest_ts: '2026-04-21T13:58:44' },
        entity:    { count: 18, bytes: 462848,    oldest_ts: '2026-03-28T11:02:00', newest_ts: '2026-04-20T22:15:00' },
        db:        { count: 32, bytes: 1491445059,oldest_ts: '2026-03-21T03:00:00', newest_ts: '2026-04-21T13:41:18' }
      }
    },
    retention: { max_age_days: 30, max_per_type: 20 },
    last_prune: { ts: '2026-04-21T12:55:31', pruned_count: 9, bytes_freed: 40056422, type: 'all', clear_all: false }
  },

  selfCheck: {
    ha_ops_version: '0.34.1',
    checks: {
      'config_access': { status: 'ok', config_root: '/config', writable: true },
      'rest_api':      { status: 'ok', ha_version: '2026.4.2', latency_ms: 41 },
      'websocket':     { status: 'ok', connected: true, latency_ms: 28 },
      'database':      { status: 'degraded', backend: 'sqlite', size: '1.4 GB', note: 'approaching recommended purge threshold' },
      'supervisor':    { status: 'ok', token: 'present', addon: 'ha-ops-mcp' }
    }
  },

  toolsCheck: {
    summary: { overall: 'partial' },
    'database': { status: 'pass', tests: {
      'db_query':  { ok: true, rows: 12 },
      'db_health': { ok: true, backend: 'sqlite' }
    }, tools_affected: [] },
    'config': { status: 'pass', tests: {
      'config_read':     { ok: true },
      'config_validate': { ok: true, result: 'valid' }
    }, tools_affected: [] },
    'dashboard': { status: 'partial', tests: {
      'dashboard_list': { ok: true, count: 4 },
      'dashboard_get':  { ok: false, error: 'WS timeout on url_path "energy" (retry succeeded)' }
    }, tools_affected: ['dashboard_get', 'dashboard_diff'] },
    'system': { status: 'pass', tests: {
      'system_info': { ok: true, ha_version: '2026.4.2' },
      'system_logs': { ok: true, lines: 200 }
    }, tools_affected: [] },
    broken_tools: []
  }
};
