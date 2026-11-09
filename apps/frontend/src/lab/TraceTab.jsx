// TraceTab — how the question page shows an answer (9 Nov 2026): the recorded answer as Langfuse shows it. The header
// (time, cost, tokens, session, user, route, models); Tree, Timeline and Graph views of every step; and the selected
// step's Preview (input and output, Formatted or Raw) and Metadata. The service builds the tree with the Langfuse
// export's own rules (svc-trace-lab trace_tree.py), so this view and Langfuse show the same steps. Notes are written here
// too: the page passes `annotate` (see TraceView), and the notes keep Trace Lab's step keys (logic/traceNotes.js).
import React from 'react';
import { axisOf, flowOf, fmtCost, fmtDur, fmtStart, fmtTime, fmtTokens, fmtTotalCost, subtreeCost, timeTicks, timelineBars,
  visibleRows } from './logic/traceTree.js';
import { parseMarkdown } from './logic/miniMarkdown.js';
import './traceTab.css';

// Line icons drawn inline (Trace Lab has no icon library of its own): agent, model call, tool, step, and the
// tree's chevrons and search glass, after Langfuse's.
function Svg({ size, children }) {
  return (
    <svg width={size || 14} height={size || 14} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">{children}</svg>
  );
}
const PATHS = {
  agent: <><path d="M12 8V4H8" /><rect width="16" height="12" x="4" y="8" rx="2" /><path d="M2 14h2M20 14h2M15 13v2M9 13v2" /></>,
  generation: <><path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z" /><path d="M19 15v4M17 17h4" /></>,
  tool: <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" />,
  span: <path d="M8 3 4 7l4 4M4 7h16M16 21l4-4-4-4M20 17H4" />,
};
const Bot = () => <Svg>{PATHS.agent}</Svg>;
const Sparkles = () => <Svg>{PATHS.generation}</Svg>;
const Wrench = () => <Svg>{PATHS.tool}</Svg>;
const ArrowLeftRight = () => <Svg>{PATHS.span}</Svg>;
const Plus = () => <Svg><path d="M12 5v14M5 12h14" /></Svg>;
const Minus = () => <Svg><path d="M5 12h14" /></Svg>;
const Fit = () => <Svg><path d="M8 3H5a2 2 0 0 0-2 2v3M21 8V5a2 2 0 0 0-2-2h-3M3 16v3a2 2 0 0 0 2 2h3M16 21h3a2 2 0 0 0 2-2v-3" /></Svg>;
const ChevronDown = () => <Svg><path d="m6 9 6 6 6-6" /></Svg>;
const ChevronRight = () => <Svg><path d="m9 18 6-6-6-6" /></Svg>;
const Search = () => <Svg><circle cx="11" cy="11" r="7" /><path d="m21 21-4.3-4.3" /></Svg>;

const ICONS = { agent: Bot, generation: Sparkles, tool: Wrench, span: ArrowLeftRight };
const TYPE_TEXT = { agent: 'Agent', generation: 'Model call', tool: 'Tool', span: 'Step' };
const VIEWS = [['tree', 'Tree'], ['timeline', 'Timeline'], ['graph', 'Graph']];
const PURGED = "Hoist Traces no longer keeps this answer's steps (it keeps them 90 days). The question and answer are below.";

function TypeIcon({ type }) {
  const t = ICONS[type] ? type : 'span';
  const Icon = ICONS[t];
  return (
    <span className={'lab-tt-icon lab-tt-icon-' + t} title={TYPE_TEXT[t]}>
      <Icon />
    </span>
  );
}

function Inline({ parts }) {
  return parts.map((p, i) => {
    if (p.t === 'b') return <strong key={i}>{p.text}</strong>;
    if (p.t === 'i') return <em key={i}>{p.text}</em>;
    if (p.t === 'code') return <code key={i}>{p.text}</code>;
    return <React.Fragment key={i}>{p.text}</React.Fragment>;
  });
}

// Markdown drawn as React elements from plain data blocks, never as HTML.
function Markdown({ text }) {
  return parseMarkdown(text).map((b, i) => {
    if (b.t === 'h') return <p key={i} className={'lab-tt-md-h lab-tt-md-h' + b.level}><Inline parts={b.inline} /></p>;
    if (b.t === 'p') return <p key={i}><Inline parts={b.inline} /></p>;
    if (b.t === 'code') return <pre key={i} className="lab-tt-pre">{b.text}</pre>;
    if (b.t === 'ul' || b.t === 'ol') {
      const List = b.t;
      return <List key={i}>{b.items.map((it, j) => <li key={j}><Inline parts={it} /></li>)}</List>;
    }
    return (
      <div key={i} className="lab-tt-tablewrap">
        <table>
          <thead><tr>{b.head.map((c, j) => <th key={j}><Inline parts={c} /></th>)}</tr></thead>
          <tbody>{b.rows.map((r, j) => <tr key={j}>{r.map((c, k) => <td key={k}><Inline parts={c} /></td>)}</tr>)}</tbody>
        </table>
      </div>
    );
  });
}

function Value({ v, mode }) {
  if (v == null || v === '') return <p className="lab-small">{'Nothing recorded.'}</p>;
  if (mode === 'raw') return <pre className="lab-tt-pre">{typeof v === 'string' ? JSON.stringify(v) : JSON.stringify(v, null, 2)}</pre>;
  if (typeof v === 'string') return <div className="lab-tt-md"><Markdown text={v} /></div>;
  return <pre className="lab-tt-pre">{JSON.stringify(v, null, 2)}</pre>;
}

function Switch({ label, items, value, onChange, className }) {
  return (
    <div className={'lab-tt-switch ' + (className || '')} role="tablist" aria-label={label}>
      {items.map(([k, t]) => (
        <button key={k} type="button" role="tab" aria-selected={value === k ? 'true' : 'false'}
          className={value === k ? 'lab-on' : undefined} onClick={() => onChange(k)}>{t}</button>
      ))}
    </div>
  );
}

function TreeView({ nodes, selected, onSelect, count }) {
  const [collapsed, setCollapsed] = React.useState(() => new Set());
  const [query, setQuery] = React.useState('');
  const rows = visibleRows(nodes, collapsed, query);
  const toggle = (id) => setCollapsed((prev) => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });
  return (
    <>
      <label className="lab-tt-search">
        <Search />
        <input type="search" placeholder="Search steps" aria-label="Search steps" value={query}
          onChange={(e) => setQuery(e.target.value)} />
      </label>
      {rows.length ? (
        <ul className="lab-tt-tree" role="tree" aria-label="Steps">
          {rows.map((r) => {
            const n = r.node;
            const id = String(n.id);
            const on = id === selected;
            return (
              <li key={id} role="treeitem" aria-selected={on ? 'true' : 'false'} aria-expanded={r.hasKids ? (r.open ? 'true' : 'false') : undefined}
                className={'lab-tt-row' + (on ? ' lab-on' : '') + (n.level === 'ERROR' ? ' lab-tt-error' : '')}
                style={{ paddingLeft: 6 + r.depth * 18 }}>
                {r.hasKids ? (
                  <button type="button" className="lab-tt-chev" aria-label={(r.open ? 'Collapse ' : 'Expand ') + n.name}
                    onClick={() => toggle(id)}>
                    {r.open ? <ChevronDown /> : <ChevronRight />}
                  </button>
                ) : <span className="lab-tt-chev" aria-hidden="true" />}
                <button type="button" className="lab-tt-pick" onClick={() => onSelect(id)}>
                  <TypeIcon type={n.type} />
                  <span className="lab-tt-label">
                    <span className="lab-tt-name">{n.name}</span>
                    <span className="lab-tt-sub">
                      <span>{fmtTime(n.latency_ms)}</span>
                      {n.type === 'generation' && n.cost_usd != null ? <span>{fmtCost(n.cost_usd)}</span> : null}
                    </span>
                  </span>
                  <NoteCount n={count ? count(id) : 0} />
                </button>
              </li>
            );
          })}
        </ul>
      ) : <p className="lab-small">{'No step matches.'}</p>}
    </>
  );
}

// The timeline, as Langfuse draws it: a ruler, then one bar per step on that time axis, labelled with how long it took
// and what its model calls cost (summed down its steps). Bars too short for a label carry it beside them; hovering a bar
// names the step; Show labels writes the names on the bars; − / + zoom the axis.
const ZOOMS = [1, 1.5, 2, 3, 4, 6, 8];

function TimelineView({ nodes, turn, selected, onSelect, count }) {
  const [z, setZ] = React.useState(0);
  const [labels, setLabels] = React.useState(false);
  const byId = new Map(nodes.map((n) => [String(n.id), n]));
  const costs = subtreeCost(nodes);
  const { ms } = axisOf(nodes, turn);
  return (
    <div className="lab-tt-tl">
      <div className="lab-tt-tlbar" role="toolbar" aria-label="Timeline">
        <button type="button" className="lab-tt-tool" aria-label="Zoom out" disabled={z === 0} onClick={() => setZ(Math.max(0, z - 1))}><Minus /></button>
        <button type="button" className="lab-tt-tool" aria-label="Zoom in" disabled={z === ZOOMS.length - 1}
          onClick={() => setZ(Math.min(ZOOMS.length - 1, z + 1))}><Plus /></button>
        <button type="button" className={'lab-tt-tool lab-tt-tool-text' + (labels ? ' lab-on' : '')} aria-pressed={labels ? 'true' : 'false'}
          onClick={() => setLabels(!labels)}>{'Show labels'}</button>
      </div>
      <div className="lab-tt-tlscroll">
        <div className="lab-tt-tlinner" style={{ width: ZOOMS[z] * 100 + '%' }}>
          <div className="lab-tt-ruler" aria-hidden="true">
            {timeTicks(ms).map((t) => (
              <span key={t.ms} className={'lab-tt-tick' + (t.pct > 86 ? ' lab-tt-tick-end' : '')} style={{ left: t.pct + '%' }}>
                <span>{t.label}</span>
              </span>
            ))}
          </div>
          <ol className="lab-tt-timeline" aria-label="Steps on a time axis">
            {timelineBars(nodes, turn).map((b) => {
              const n = byId.get(b.id);
              const on = b.id === selected;
              const type = ICONS[n.type] ? n.type : 'span';
              const cost = costs.get(b.id) || 0;
              const text = fmtDur(n.latency_ms) + (cost > 0 ? '  ' + fmtCost(cost) : '');
              const inside = b.widthPct >= 18;
              const flip = !inside && b.leftPct + b.widthPct > 72;
              const place = inside ? { left: b.leftPct + '%' } : flip ? { right: (100 - b.leftPct) + '%' } : { left: (b.leftPct + b.widthPct) + '%' };
              return (
                <li key={b.id} className={'lab-tt-trow' + (on ? ' lab-on' : '') + (n.level === 'ERROR' ? ' lab-tt-error' : '')}>
                  <button type="button" className="lab-tt-tbtn" onClick={() => onSelect(b.id)}
                    aria-label={n.name + ', ' + TYPE_TEXT[type] + ', ' + text}>
                    <span className={'lab-tt-bar lab-tt-bar-' + type + (n.level === 'ERROR' ? ' lab-tt-bar-error' : '')}
                      style={{ left: b.leftPct + '%', width: b.widthPct + '%' }} />
                    <span className={'lab-tt-tlabel' + (inside ? ' lab-tt-tlabel-in lab-tt-tlabel-' + type : '') + (flip ? ' lab-tt-tlabel-flip' : '')}
                      style={place}>
                      {labels ? <span className="lab-tt-tname">{n.name}</span> : null}
                      <span className="lab-tt-tnum">{fmtDur(n.latency_ms)}</span>
                      {cost > 0 ? <span className="lab-tt-tnum">{fmtCost(cost)}</span> : null}
                      <NoteCount n={count ? count(b.id) : 0} />
                    </span>
                    <span className="lab-tt-tip" style={{ left: Math.min(b.leftPct, 70) + '%' }} aria-hidden="true">
                      <span className="lab-tt-tip-name"><TypeIcon type={type} />{n.name}<em>{TYPE_TEXT[type]}</em></span>
                      <span className="lab-tt-tip-sub">{text}</span>
                    </span>
                  </button>
                </li>
              );
            })}
          </ol>
        </div>
      </div>
    </div>
  );
}

// The graph, as Langfuse draws it: a start node, then one node per step name in the order the steps ran, top to bottom,
// each arrow to the next. A step that ran more than once says which of its runs is in view, "(k/n)"; clicking it again
// moves to its next run. Zoom with − / + and Fit.
const G = { h: 30, gap: 30, pad: 16, char: 6.6, min: 96, max: 340 };
const GZOOMS = [0.5, 0.75, 1, 1.25, 1.5, 2];

function GraphView({ nodes, selected, onSelect }) {
  const [z, setZ] = React.useState(2);
  const f = flowOf(nodes);
  if (!f.steps.length) return <p className="lab-small">{'This answer has no recorded steps to draw.'}</p>;
  const items = [{ name: '__start__', start: true, ids: [] }].concat(f.steps);
  const label = (st) => {
    if (st.start || st.ids.length < 2) return '';
    const k = st.ids.indexOf(selected);
    return ' (' + (k >= 0 ? k + 1 : st.ids.length) + '/' + st.ids.length + ')';
  };
  const widthOf = (st) => Math.max(G.min, Math.min(G.max, Math.round((st.name.length + label(st).length) * G.char + (st.start ? 32 : 52))));
  const inner = Math.max(...items.map(widthOf));
  const width = G.pad * 2 + inner;
  const height = G.pad * 2 + items.length * G.h + (items.length - 1) * G.gap;
  const cx = width / 2;
  const y = (i) => G.pad + i * (G.h + G.gap);
  const pick = (st) => {
    const k = st.ids.indexOf(selected);
    onSelect(st.ids[k >= 0 ? (k + 1) % st.ids.length : 0]);
  };
  const scale = GZOOMS[z];
  return (
    <div className="lab-tt-graphwrap">
      <div className="lab-tt-gtools" role="toolbar" aria-label="Graph">
        <button type="button" className="lab-tt-tool" aria-label="Zoom in" disabled={z === GZOOMS.length - 1}
          onClick={() => setZ(Math.min(GZOOMS.length - 1, z + 1))}><Plus /></button>
        <button type="button" className="lab-tt-tool" aria-label="Zoom out" disabled={z === 0} onClick={() => setZ(Math.max(0, z - 1))}><Minus /></button>
        <button type="button" className="lab-tt-tool" aria-label="Fit" onClick={() => setZ(2)}><Fit /></button>
      </div>
      <svg className="lab-tt-graph" width={width * scale} height={height * scale} viewBox={'0 0 ' + width + ' ' + height} role="img"
        aria-label="The steps of this answer in the order they ran">
        <defs>
          <marker id="lab-tt-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto">
            <path d="M0,0 L8,4 L0,8 z" className="lab-tt-arrowhead" />
          </marker>
        </defs>
        {items.slice(1).map((st, i) => (
          <path key={'e' + i} className="lab-tt-edge" markerEnd="url(#lab-tt-arrow)"
            d={'M' + cx + ',' + (y(i) + G.h) + ' L' + cx + ',' + (y(i + 1) - 2)} />
        ))}
        {items.map((st, i) => {
          const w = widthOf(st);
          const x = cx - w / 2;
          if (st.start) {
            return (
              <g key="start" className="lab-tt-gstart" transform={'translate(' + x + ',' + y(i) + ')'}>
                <rect width={w} height={G.h} rx="7" />
                <text x={w / 2} y={G.h / 2 + 4} textAnchor="middle">{'__start__'}</text>
              </g>
            );
          }
          const type = ICONS[st.type] ? st.type : 'span';
          const on = st.ids.indexOf(selected) >= 0;
          const err = st.ids.some((id) => { const n = nodes.find((m) => String(m.id) === id); return n && n.level === 'ERROR'; });
          const name = st.name.length > 44 ? st.name.slice(0, 43) + '…' : st.name;
          return (
            <g key={st.name} className={'lab-tt-gnode lab-tt-gnode-' + type + (on ? ' lab-on' : '') + (err ? ' lab-tt-gnode-error' : '')}
              role="button" tabIndex={0} transform={'translate(' + x + ',' + y(i) + ')'}
              aria-label={TYPE_TEXT[type] + ' ' + st.name + (st.ids.length > 1 ? ', ' + st.ids.length + ' runs' : '')}
              onClick={() => pick(st)} onKeyDown={(ev) => { if (ev.key === 'Enter' || ev.key === ' ') { ev.preventDefault(); pick(st); } }}>
              <title>{st.name}</title>
              <rect width={w} height={G.h} rx="7" />
              <svg x="12" y={G.h / 2 - 6} width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"
                strokeLinecap="round" strokeLinejoin="round" className="lab-tt-gicon" aria-hidden="true">{PATHS[type]}</svg>
              <text x="30" y={G.h / 2 + 4}><tspan>{name}</tspan>{label(st) ? <tspan className="lab-tt-gcount">{label(st)}</tspan> : null}</text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}

function Meta({ node, turn }) {
  const f = node.filled || {};
  const rows = [
    ['Type', node.type], ['Kind', node.kind], ['Agent', node.agent], ['Model', node.model],
    ['Started', fmtStart(node.started_at)], ['Ended', fmtStart(node.ended_at)], ['Time', fmtTime(node.latency_ms)],
    ['Cost', node.type === 'generation' ? fmtCost(node.cost_usd) : null],
    ['Input tokens', node.input_tokens], ['Output tokens', node.output_tokens], ['Cache read tokens', node.cache_read_tokens],
    ['Input from', f.input_from], ['Output from', f.output_from],
    ['Cut', node.truncated ? 'Hoist Traces keeps 16,000 characters of a value' : null],
    ['Step id', node.id], ['Parent step', node.parent_id], ['Sequence', node.seq],
    ['Turn id', turn.turn_id], ['Langfuse trace id', turn.langfuse_trace_id], ['Session', turn.session_id],
  ].filter(([, v]) => v !== null && v !== undefined && v !== '');
  return (
    <dl className="lab-tt-meta">
      {rows.map(([k, v]) => (
        <React.Fragment key={k}><dt>{k}</dt><dd className={/id$|Session/.test(k) ? 'lab-mono' : undefined}>{String(v)}</dd></React.Fragment>
      ))}
    </dl>
  );
}

function Detail({ node, turn, pane, setPane, mode, setMode, annotate }) {
  const f = node.filled || {};
  return (
    <section className="lab-tt-detail" aria-label={'Step ' + node.name}>
      <header className="lab-tt-dhead">
        <TypeIcon type={node.type} />
        <strong className="lab-tt-dname">{node.name}</strong>
      </header>
      <div className="lab-tt-dmeta">
        {node.started_at ? <span className="lab-mono">{fmtStart(node.started_at)}</span> : null}
        {node.latency_ms != null ? <span>{fmtTime(node.latency_ms)}</span> : null}
        {node.type === 'generation' && node.cost_usd != null ? <span>{fmtCost(node.cost_usd)}</span> : null}
        {node.model ? <span className="lab-chip lab-chip-model">{node.model}</span> : null}
        {node.input_tokens != null || node.output_tokens != null
          ? <span>{(node.input_tokens || 0) + ' → ' + (node.output_tokens || 0) + ' tokens'}</span> : null}
      </div>
      {node.error ? <div role="alert" className="lab-error">{node.error}</div> : null}
      <div className="lab-tt-dbar">
        <Switch label="Step detail" items={[['preview', 'Preview'], ['metadata', 'Metadata']]} value={pane} onChange={setPane} />
        {pane === 'preview'
          ? <Switch label="How values show" className="lab-tt-mode" items={[['formatted', 'Formatted'], ['raw', 'Raw']]} value={mode} onChange={setMode} />
          : null}
      </div>
      {pane === 'metadata' ? <Meta node={node} turn={turn} /> : (
        <>
          <Side node={node} side="in" title="Input" mode={mode} annotate={annotate}
            filled={f.input_from ? 'Filled from ' + f.input_from + ' (this step recorded no input of its own).' : ''} />
          <Side node={node} side="out" title="Output" mode={mode} annotate={annotate}
            filled={f.output_from ? 'Filled from ' + f.output_from + '.' : ''} />
          {annotate && node.parent_id == null && annotate.other ? annotate.other() : null}
        </>
      )}
    </section>
  );
}

// One side of a step (Input or Output): its value and, when the page annotates, the notes on that side under it.
// Selecting text in the value or double-clicking it hands the side's note key to the page (today's note gestures).
function Side({ node, side, title, mode, annotate, filled }) {
  const id = String(node.id);
  const key = annotate ? annotate.keyOf(id, side) : null;
  const v = side === 'in' ? node.input : node.output;
  return (
    <details className="lab-tt-io" open>
      <summary>{title}</summary>
      {filled ? <p className="lab-small">{filled}</p> : null}
      {key ? (
        <div className="lab-tt-val" data-note-key={key} title="Select text to note it, or double-click to add a note"
          onMouseUp={(e) => annotate.up(key, e.currentTarget, e)} onDoubleClick={(e) => annotate.double(key, e)}>
          <Value v={v} mode={mode} />
        </div>
      ) : <Value v={v} mode={mode} />}
      {key ? annotate.render(id, side) : null}
    </details>
  );
}

function NoteCount({ n }) {
  if (!n) return null;
  return <span className="lab-tt-ncount" aria-label={n + (n === 1 ? ' note' : ' notes')} title={n + (n === 1 ? ' note' : ' notes')}>{n}</span>;
}

// The trace of one answer. Its state starts from these props so any view can be drawn directly. `annotate` (optional,
// from the question page) adds notes: { keyOf(nodeId, side), count(nodeId), render(nodeId, side), up(key, el, e),
// double(key, e), other() }.
export function TraceView({ tree, view = 'tree', selectedId, pane = 'preview', mode = 'formatted', annotate }) {
  const tr = tree || {};
  const t = tr.turn || {};
  const nodes = Array.isArray(tr.nodes) ? tr.nodes : [];
  const first = nodes.length ? String(nodes[0].id) : null;
  const [v, setV] = React.useState(view);
  const [sel, setSel] = React.useState(selectedId != null ? String(selectedId) : first);
  const [p, setP] = React.useState(pane);
  const [md, setMd] = React.useState(mode);
  const node = nodes.find((n) => String(n.id) === sel) || nodes[0] || null;
  const count = annotate && annotate.count ? (id) => annotate.count(id) : null;
  return (
    <section className="lab-tt" aria-label="Trace">
      <header className="lab-tt-head">
        <code className="lab-tt-tid" title="Hoist Traces turn id">{t.turn_id || '—'}</code>
        <div className="lab-tt-stats">
          {t.latency_ms != null ? <span className="lab-tt-stat">{fmtTime(t.latency_ms)}</span> : null}
          {t.cost_usd != null ? (
            <span className="lab-tt-stat" title={t.cost_complete === false ? 'Some model calls had no price, so this is a floor' : 'Total cost'}>
              {fmtTotalCost(t.cost_usd) + (t.cost_complete === false ? '+' : '')}
            </span>
          ) : null}
          {t.total_tokens ? (
            <span className="lab-tt-stat" title={(t.input_tokens || 0) + ' in · ' + (t.output_tokens || 0) + ' out · ' + (t.cache_read_tokens || 0) + ' cached'}>
              {fmtTokens(t.total_tokens)}
            </span>
          ) : null}
          {t.session_id ? <span className="lab-tt-stat">{'session '}<code className="lab-mono">{t.session_id}</code></span> : null}
          {t.email ? <span className="lab-tt-stat">{'user: ' + t.email}</span> : null}
          {t.route ? <span className="lab-chip">{t.route}</span> : null}
          {(t.models || []).map((m) => <span key={m} className="lab-chip lab-chip-model">{m}</span>)}
        </div>
        {t.error ? <div role="alert" className="lab-error">{t.error}</div> : null}
      </header>
      {tr.purged ? <p className="lab-small lab-tt-purged" role="note">{PURGED}</p> : null}
      <div className="lab-tt-body">
        <div className="lab-tt-left">
          <Switch label="Trace view" className="lab-tt-views" items={VIEWS} value={v} onChange={setV} />
          {v === 'timeline' ? <TimelineView nodes={nodes} turn={t} selected={node && String(node.id)} onSelect={setSel} count={count} />
            : v === 'graph' ? <GraphView nodes={nodes} selected={node && String(node.id)} onSelect={setSel} />
              : <TreeView nodes={nodes} selected={node && String(node.id)} onSelect={setSel} count={count} />}
        </div>
        <div className="lab-tt-right">
          {node ? <Detail node={node} turn={t} pane={p} setPane={setP} mode={md} setMode={setMd} annotate={annotate} />
            : <p className="lab-small">{'This answer has no recorded steps.'}</p>}
        </div>
      </div>
    </section>
  );
}
