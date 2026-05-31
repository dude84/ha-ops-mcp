// Shared formatters + primitives for the sideload UI kit.
const { useState, useRef } = React;

function fmtTs(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    year: 'numeric', month: 'short', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit'
  });
}

function fmtBytes(n) {
  if (n == null) return '—';
  if (n < 1024) return n + ' B';
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
  if (n < 1024 * 1024 * 1024) return (n / (1024 * 1024)).toFixed(1) + ' MB';
  return (n / (1024 * 1024 * 1024)).toFixed(2) + ' GB';
}

// op-class → short label + badge class (mirrors ui.html opLabel/opBadgeClass)
function opLabel(c) { return c === 'read' ? 'READ' : c === 'destructive' ? 'DELETE' : 'MUTATE'; }
function opClass(c) { return c === 'read' ? 'op-read' : c === 'destructive' ? 'op-destructive' : 'op-mutate'; }

function Badge({ kind, mono, children }) {
  return <span className={'badge ' + (mono ? 'mono ' : '') + kind}>{children}</span>;
}

// Diff renderer — same line-colouring logic as ui.html's renderDiffHtml,
// expressed as React spans.
function DiffView({ text }) {
  const lines = (text || '').split('\n').map((line, i) => {
    let cls = null;
    if (line.startsWith('+++') || line.startsWith('---')) cls = 'd-meta';
    else if (line.startsWith('@@')) cls = 'd-hunk';
    else if (line.startsWith('+')) cls = 'd-add';
    else if (line.startsWith('-')) cls = 'd-rem';
    else {
      const t = line.trimStart();
      if (t.startsWith('+ ')) cls = 'd-add';
      else if (t.startsWith('~ ')) cls = 'd-change';
      else if (t.startsWith('- ') && !t.match(/^- [a-z_]+:/)) cls = 'd-rem';
      else if (line.match(/^[A-Z][a-z].*:$/)) cls = 'd-section';
    }
    // YAML key: value colouring on otherwise-plain lines
    if (!cls) {
      const m = line.match(/^(\s*)([\w._-]+)(:)(\s?)(.*)$/);
      if (m) {
        let val = m[5], valCls = null;
        if (val.match(/^-?\d+(\.\d+)?$/)) valCls = 'd-num';
        else if (val.match(/^(true|false|null|yes|no|on|off)$/i)) valCls = 'd-bool';
        else if (val.match(/^['"]/) || val.match(/^[a-z]+\.[a-z_]+/)) valCls = 'd-str';
        return (
          <div key={i}>
            {m[1]}<span className="d-key">{m[2]}</span><span className="d-meta">{m[3]}</span>{m[4]}
            {valCls ? <span className={valCls}>{val}</span> : val}
          </div>
        );
      }
    }
    return <div key={i} className={cls || undefined}>{line || '\u00a0'}</div>;
  });
  return <pre className="diff">{lines}</pre>;
}

// Area tag — optional icon layer + the canonical ·area· text label.
// Unknown / uncategorized areas fall back to the `misc` icon (mirrors the
// ("mutate", "misc") default in classification.py).
const KNOWN_AREAS = ['config','automation','script','scene','dashboard','entity','registry','database','system','addon','shell','helper','backup','service','references'];
function AreaTag({ area }) {
  const icon = KNOWN_AREAS.includes(area) ? area : 'misc';
  return (
    <span className="tl-area">
      <svg className="area-ic" aria-hidden="true"><use href={'#area-' + icon}></use></svg>
      ·{area}·
    </span>
  );
}

Object.assign(window, { fmtTs, fmtBytes, opLabel, opClass, Badge, DiffView, AreaTag });
