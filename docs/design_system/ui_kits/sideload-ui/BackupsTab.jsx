// Backups tab — stat cards, per-type table, last prune, prune/clear actions.
function BackupsTab() {
  const b = window.HAOPS_DATA.backups;
  const [result, setResult] = useState('');
  const [busy, setBusy] = useState('');

  const rows = ['config', 'dashboard', 'entity', 'db'].map(t => {
    const r = b.summary.per_type[t] || {};
    return { type: t, count: r.count || 0, bytes: r.bytes || 0, oldest_ts: r.oldest_ts, newest_ts: r.newest_ts };
  });

  function prune() {
    if (window.confirm('Prune 9 backup(s), freeing ~38.2 MB?')) {
      setBusy('prune');
      setTimeout(() => { setBusy(''); setResult('Removed 9 backup(s), freed 38.2 MB.'); }, 600);
    }
  }
  function clearAll() {
    if (window.confirm('CLEAR ALL 128 backup(s) across every type (1.4 GB)?\n\nThis is irreversible.'))
      setResult('Cancelled — demo kit does not delete.');
  }

  return (
    <section>
      <h2 className="h2 mb1">Backups</h2>
      <p className="muted mb4" style={{ maxWidth: '72ch' }}>
        Summary of persistent backups plus admin-convenience prune actions. Full control
        (revert, targeted deletion) stays in the MCP flow — use <code>haops_backup_revert</code> / <code>haops_backup_prune</code>.
      </p>

      <div className="stack-4">
        <div className="row-gap2">
          <button className="btn btn-primary" onClick={prune} disabled={busy === 'prune'}>
            {busy === 'prune' ? 'Pruning…' : 'Prune now (use retention)'}
          </button>
          <button className="btn btn-danger" onClick={clearAll}>Clear all now</button>
        </div>

        {result && <div className="banner banner-ok">{result}</div>}

        <div className="stat-grid">
          <div className="card stat"><div className="cap">Total backups</div><div className="num">{b.summary.total_count}</div></div>
          <div className="card stat"><div className="cap">Disk usage</div><div className="num">{fmtBytes(b.summary.total_bytes)}</div></div>
          <div className="card stat"><div className="cap">Max age (days)</div><div className="num">{b.retention.max_age_days}</div></div>
          <div className="card stat"><div className="cap">Max per type</div><div className="num">{b.retention.max_per_type}</div></div>
        </div>

        <div className="card" style={{ overflow: 'hidden' }}>
          <div className="tbl-cap">By type</div>
          <table>
            <thead><tr className="tbl-head-row">
              <th>Type</th><th className="ta-right">Count</th><th className="ta-right">Bytes</th>
              <th>Oldest</th><th>Newest</th><th className="ta-right">Actions</th>
            </tr></thead>
            <tbody>
              {rows.map(r => (
                <tr key={r.type}>
                  <td style={{ fontWeight: 'var(--weight-medium)' }}>{r.type}</td>
                  <td className="ta-right">{r.count}</td>
                  <td className="ta-right mono" style={{ fontSize: 'var(--text-xs)' }}>{fmtBytes(r.bytes)}</td>
                  <td className="mono faint" style={{ fontSize: 'var(--text-xs)' }}>{fmtTs(r.oldest_ts)}</td>
                  <td className="mono faint" style={{ fontSize: 'var(--text-xs)' }}>{fmtTs(r.newest_ts)}</td>
                  <td className="ta-right"><button className="btn-link">Clear</button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="card stat">
          <div className="cap mb3">Last prune</div>
          <div className="kv stack-3" style={{ gap: 'var(--space-1)' }}>
            <div><span className="k">When: </span><span className="v">{fmtTs(b.last_prune.ts)}</span></div>
            <div><span className="k">Removed: </span><span>{b.last_prune.pruned_count} backup(s), {fmtBytes(b.last_prune.bytes_freed)} freed</span></div>
            <div><span className="k">Scope: </span><span>{b.last_prune.type}</span></div>
          </div>
        </div>

        <div className="muted" style={{ fontSize: 'var(--text-xs)' }}>
          Backup directory: <span className="mono">{b.backup_dir}</span>
        </div>
      </div>
    </section>
  );
}
window.BackupsTab = BackupsTab;
