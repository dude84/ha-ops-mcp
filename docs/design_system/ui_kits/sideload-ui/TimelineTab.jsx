// Timeline tab — the system's anchor surface.
function TimelineRow({ e, idx, expanded, onToggle, onJump, flash, onRevert, reverted }) {
  return (
    <div className={'card tl-row' + (flash ? ' flash' : '')} id={'tl-row-' + idx}>
      <div className="tl-head" role="button" tabIndex={0} onClick={() => onToggle(idx)}>
        <span className="tl-tri">{expanded ? '▾' : '▸'}</span>
        <div className="tl-body">
          <div className="tl-meta">
            <span className="tl-ts">{fmtTs(e.timestamp)}</span>
            <Badge kind={opClass(e.op_class)} mono>{opLabel(e.op_class)}</Badge>
            <span className="tl-tool">{e.tool}</span>
            {e.area && <AreaTag area={e.area} />}
            <Badge kind={e.success ? 'state-ok' : 'state-fail'}>{e.success ? 'ok' : 'fail'}</Badge>
            {e.paired_with && (
              <button className="rel-chip" onClick={ev => { ev.stopPropagation(); onJump(e.paired_with.index); }}
                title={'Jump to ' + e.paired_with.tool}>
                {e.paired_with.relation === 'rolled_back_by' ? '↺ rolled back' : '↶ reverts apply'}
              </button>
            )}
          </div>
          <div className="tl-summary">{e.summary}</div>
        </div>
      </div>
      {expanded && (
        <div className="tl-detail">
          {e.error && <pre className="diff" style={{ color: 'var(--diff-remove)', marginTop: '12px' }}>{e.error}</pre>}
          {typeof e.diff === 'string' && e.diff && (
            <div>
              <div className="detail-label">Diff</div>
              <DiffView text={e.diff} />
            </div>
          )}
          {e.details_excerpt && Object.keys(e.details_excerpt).length > 0 && (
            <div>
              <div className="detail-label">Details</div>
              <pre className="json">{JSON.stringify(e.details_excerpt, null, 2)}</pre>
            </div>
          )}
          {e.details_excerpt && e.details_excerpt.path && (
            <div className="kv mt3"><span className="k">Target: </span><span className="v">{e.details_excerpt.path}</span></div>
          )}
          {e.backup_path && (
            <div className="kv mb1"><span className="k">Backup: </span><span className="v">{e.backup_path}</span></div>
          )}
          {e.token_id && (
            <div className="kv"><span className="k">Token: </span><span className="v">{e.token_id}</span></div>
          )}
          {e.transaction_id && !reverted && (
            <div className="row-gap2 mt3">
              <button className="btn btn-sm btn-mutate" onClick={() => onRevert(idx)}>Revert</button>
              <span className="revert-note">Undoes this apply via in-session rollback. Falls back to <code>haops_backup_revert</code> after restart.</span>
            </div>
          )}
          {reverted && <div className="banner banner-ok">Reverted 1 target(s).</div>}
        </div>
      )}
    </div>
  );
}

function TimelineTab() {
  const all = window.HAOPS_DATA.timeline;
  const [expanded, setExpanded] = useState(-1);
  const [showReads, setShowReads] = useState(false);
  const [showLegend, setShowLegend] = useState(false);
  const [flashIdx, setFlashIdx] = useState(-1);
  const [reverted, setReverted] = useState({});

  const entries = all.filter(e => showReads || !e.read);

  function jump(i) {
    setExpanded(i); setFlashIdx(i);
    const el = document.getElementById('tl-row-' + i);
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
    setTimeout(() => setFlashIdx(-1), 1500);
  }
  function revert(i) {
    if (window.confirm('Revert this apply?\n\nHA side effects fired during the original apply are NOT rolled back.'))
      setReverted(r => ({ ...r, [i]: true }));
  }

  return (
    <section>
      <div className="row-between mb1">
        <h2 className="h2">Timeline</h2>
        <button className="btn-ghost-danger">Clear audit log</button>
      </div>
      <p className="muted mb3" style={{ maxWidth: '70ch' }}>
        Inspection of recent operations. Click a row to see the diff. Apply rows still in-session
        expose a Revert button that fires <code>haops_rollback</code>; once the transaction is gone
        (addon restart), revert falls back to <code>haops_backup_revert</code>.
      </p>

      <div className="row-between mb4">
        <div className="row-gap2" style={{ fontSize: 'var(--text-xs)' }}>
          <button className={'chip' + (showReads ? ' on' : '')} onClick={() => setShowReads(v => !v)}>
            {showReads ? '✓ Reads shown' : 'Show reads'}
          </button>
          {!showReads && <span className="faint" style={{ fontSize: 'var(--text-xs)' }}>Mutations only</span>}
        </div>
        <button className="btn-link" style={{ color: 'var(--text-muted)' }} onClick={() => setShowLegend(v => !v)}>
          {showLegend ? 'Hide legend' : 'What do the tags mean?'}
        </button>
      </div>

      {showLegend && (
        <div className="legend">
          <span className="legend-item"><Badge kind="op-read" mono>READ</Badge><span className="muted">observes state, changes nothing</span></span>
          <span className="legend-item"><Badge kind="op-mutate" mono>MUTATE</Badge><span className="muted">changes state, recoverable</span></span>
          <span className="legend-item"><Badge kind="op-destructive" mono>DELETE</Badge><span className="muted">irreversible / data loss</span></span>
          <span className="legend-item"><span className="tl-area">·area·</span><span className="muted">subsystem touched</span></span>
        </div>
      )}

      <div className="tl-list">
        {entries.map((e, i) => (
          <TimelineRow key={i} e={e} idx={i}
            expanded={expanded === i}
            onToggle={ix => setExpanded(x => x === ix ? -1 : ix)}
            onJump={jump} flash={flashIdx === i}
            onRevert={revert} reverted={reverted[i]} />
        ))}
      </div>

      <div className="pager">
        <button className="btn btn-sm btn-ghost" disabled>← Newer</button>
        <span className="mono faint" style={{ fontSize: 'var(--text-xs)' }}>Page 1 · auto-refreshing</span>
        <button className="btn btn-sm btn-ghost">Older →</button>
      </div>
    </section>
  );
}
window.TimelineTab = TimelineTab;
