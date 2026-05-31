// Health tab — self_check (config + connectivity) + tools_check (per-group).
function statusBadge(status) {
  const kind = status === 'ok' || status === 'pass' ? 'state-ok'
    : status === 'fail' ? 'state-fail'
    : status === 'degraded' || status === 'partial' ? 'op-mutate' : 'op-read';
  return <Badge kind={kind}>{status}</Badge>;
}

function HealthTab() {
  const sc = window.HAOPS_DATA.selfCheck;
  const tc = window.HAOPS_DATA.toolsCheck;
  const groups = Object.keys(tc).filter(k => k !== 'summary' && k !== 'broken_tools');

  return (
    <section className="section">
      <div>
        <h2 className="h2 mb4">Health</h2>
        <h3 className="h3 mb3">Self check (config + connectivity)</h3>
        <div className="card check-card">
          {Object.entries(sc.checks).map(([name, check]) => (
            <div className="check-entry" key={name}>
              <div className="check-head">
                <span className="check-name">{name}</span>
                {statusBadge(check.status)}
              </div>
              <div className="check-fields">
                {Object.entries(check).filter(([k]) => k !== 'status').map(([k, v]) => (
                  <div key={k}><span className="fk">{k}:</span><span className="fv">{String(v)}</span></div>
                ))}
              </div>
            </div>
          ))}
        </div>
      </div>

      <div>
        <h3 className="h3 mb3">Tools check (per-group capabilities)</h3>
        <div className="card check-card">
          <div className="muted mb3" style={{ fontSize: 'var(--text-xs)', textTransform: 'uppercase', letterSpacing: 'var(--tracking-label)' }}>
            Overall: {tc.summary.overall}
          </div>
          <div className="stack-3">
            {groups.map(name => {
              const g = tc[name];
              return (
                <div className="group" key={name}>
                  <div className="check-head"><span className="check-name">{name}</span>{statusBadge(g.status)}</div>
                  {Object.entries(g.tests || {}).map(([tname, test]) => (
                    <div className="test-row" key={tname}>
                      <Badge kind={test.ok ? 'state-ok' : 'state-fail'}>{test.ok ? 'ok' : 'fail'}</Badge>
                      <div style={{ flex: 1 }}>
                        <span className="mono">{tname}</span>
                        {test.error
                          ? <span className="mono" style={{ color: 'var(--diff-remove)', marginLeft: 8 }}>{test.error}</span>
                          : <span className="muted" style={{ marginLeft: 8, fontSize: 'var(--text-xs)' }}>
                              {Object.entries(test).filter(([k]) => k !== 'ok' && k !== 'error').map(([k, v]) => k + '=' + v).join(' · ')}
                            </span>}
                      </div>
                    </div>
                  ))}
                  {g.tools_affected && g.tools_affected.length > 0 && g.status !== 'pass' && (
                    <div className="mono faint mt3" style={{ fontSize: 'var(--text-xs)' }}>
                      Affects: {g.tools_affected.join(', ')}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </section>
  );
}
window.HealthTab = HealthTab;
