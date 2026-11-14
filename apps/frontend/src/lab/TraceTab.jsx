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
const Bubble = () => <Svg size={12}><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" /></Svg>;

const ICONS = { agent: Bot, generation: Sparkles, tool: Wrench, span: ArrowLeftRight };
const TYPE_TEXT = { agent: 'Agent', generation: 'Generation', tool: 'Tool', span: 'Span' };
const VIEWS = [['tree', 'Tree'], ['timeline', 'Timeline'], ['graph', 'Graph']];
const PURGED = "The spans of this trace are no longer kept (they are kept 90 days). The question and answer are below.";

export function TypeIcon({ type }) {
  const t = ICONS[type] ? type : 'span';
  const Icon = ICONS[t];
  return (
    <span className={'lab-tt-icon lab-tt-icon-' + t} title={TYPE_TEXT[t]}>
      <Icon />
    </span>
  );
}

// ── Notes, anchored (14 Nov 2026) ────────────────────────────────────────────────────────────────────────────────────
// A reviewer could not tell which span a note was on or where in its text it applied. Each note now carries a number
// (tree order) and an anchor (the span, the spans above it and the side). A quote is marked where it sits in the span's
// text with that number after it; a note on a whole block is numbered by the block's heading; the same number heads the
// note's card and its row in "Notes on this turn". Hovering any of them lights the others; a number goes to its note.
const SIDES = { in: 'Input', out: 'Output' };
const noop = () => {};
const noteWord = (n) => n + (n === 1 ? ' note' : ' notes');
const squash = (s) => String(s == null ? '' : s).replace(/\s+/g, ' ').trim();
const excerpt = (s, max) => { const t = squash(s); return t.length > max ? t.slice(0, max - 1).trimEnd() + '…' : t; };

// "just now", "5 min ago", "3 h ago", "yesterday", "3 days ago", then the day ("25 Oct", "25 Oct 2025").
export function relTime(at, now) {
  const t = Date.parse(at || '');
  if (Number.isNaN(t)) return '';
  const n = now == null ? Date.now() : now;
  const m = Math.round((n - t) / 60000);
  if ((n - t) < 45000) return 'just now';
  if (m < 60) return Math.max(1, m) + ' min ago';
  const h = Math.round(m / 60);
  if (h < 24) return h + ' h ago';
  const d = new Date(t);
  const today = new Date(n);
  const day = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const days = Math.round((day(today) - day(d)) / 86400000);
  if (days <= 1) return 'yesterday';
  if (days < 7) return days + ' days ago';
  return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: d.getFullYear() === today.getFullYear() ? undefined : 'numeric' });
}

// The text a value shows, as pieces in reading order: { text, gap } (gap: a new block, list item or cell starts, where
// a person's selection reads a line break). Each markdown part learns its piece number (k), so marks land inside it.
function viewOf(v, mode) {
  if (v == null || v === '') return { kind: 'none', pieces: [] };
  const pre = (text) => ({ kind: 'pre', text: String(text == null ? '' : text), pieces: [{ text: String(text == null ? '' : text), gap: true }] });
  if (mode === 'raw') return pre(typeof v === 'string' ? JSON.stringify(v) : JSON.stringify(v, null, 2));
  if (typeof v !== 'string') return pre(JSON.stringify(v, null, 2));
  const blocks = parseMarkdown(v);
  const pieces = [];
  const add = (part, gap) => { part.k = pieces.length; pieces.push({ text: String(part.text || ''), gap }); };
  const inline = (parts) => parts.forEach((p, i) => add(p, i === 0));
  blocks.forEach((b) => {
    if (b.t === 'h' || b.t === 'p') inline(b.inline);
    else if (b.t === 'code') add(b, true);
    else if (b.t === 'ul' || b.t === 'ol') b.items.forEach(inline);
    else { b.head.forEach(inline); b.rows.forEach((r) => r.forEach(inline)); }
  });
  return { kind: 'md', blocks, pieces };
}

// Where a quote sits in the pieces, whitespace read loosely (a selection reads line breaks and tabs where the page
// draws blocks and cells): { at, ranges: Map(piece -> [start, end)) }, or null when the text no longer holds it.
export function findQuote(pieces, quote) {
  const q = squash(quote);
  if (!q) return null;
  let s = '';
  const at = [];
  let space = true;
  (pieces || []).forEach((p, k) => {
    if (p.gap && !space) { s += ' '; at.push(null); space = true; }
    const text = p.text || '';
    for (let i = 0; i < text.length; i += 1) {
      if (/\s/.test(text[i])) {
        if (!space) { s += ' '; at.push([k, i]); space = true; }
      } else { s += text[i]; at.push([k, i]); space = false; }
    }
  });
  const i = s.indexOf(q);
  if (i < 0) return null;
  const ranges = new Map();
  for (let j = i; j < i + q.length; j += 1) {
    const a = at[j];
    if (!a) continue;
    const r = ranges.get(a[0]);
    if (r) r[1] = a[1] + 1; else ranges.set(a[0], [a[1], a[1] + 1]);
  }
  return { at: i, ranges };
}

// Where a quote sits in a value as the Formatted view draws it (-1 when it is no longer there). Notes on one side are
// numbered in this order, and a card whose quote is gone says so.
export function quoteAt(v, quote) {
  const f = findQuote(viewOf(v, 'formatted').pieces, quote);
  return f ? f.at : -1;
}

// The marks a side's notes put in its text: hits (piece -> [{ s, e, id, n, end }]) and the quoted notes not found.
function placeMarks(pieces, notes) {
  const hits = new Map();
  const missing = [];
  (notes || []).forEach((x) => {
    if (!x.quote) return;
    const f = findQuote(pieces, x.quote);
    if (!f || !f.ranges.size) { missing.push(x); return; }
    const last = Math.max(...f.ranges.keys());
    f.ranges.forEach(([s, e], k) => {
      if (!hits.has(k)) hits.set(k, []);
      hits.get(k).push({ s, e, id: x.id, n: x.n, end: k === last });
    });
  });
  return { hits, missing };
}

const NoteNav = React.createContext(null);

// A note's number. In a value or by a heading it is a button that goes to the note (its digits are drawn by CSS, so a
// selection made across it never copies them); on a card or a row it is plain text.
export function NotePin({ n, id, go, label }) {
  if (go) {
    const name = label || 'Go to note ' + n;
    return <button type="button" className="lab-an-pin" data-note={id} data-n={n} aria-label={name} title={name} onClick={() => go(id)} />;
  }
  return <span className="lab-an-pin" data-note={id}>{n}</span>;
}

function GoPin(props) {
  const nav = React.useContext(NoteNav);
  return <NotePin {...props} go={nav ? nav.go : noop} />;
}

// Quoted text. A click (not the end of a selection) goes to the note; the number after the quote is the keyboard way.
function Mark({ ids, children }) {
  const nav = React.useContext(NoteNav);
  const click = (e) => {
    if (!nav || (e && e.detail > 1)) return;
    const sel = typeof window !== 'undefined' && window.getSelection ? window.getSelection() : null;
    if (sel && !sel.isCollapsed) return;
    nav.go(ids[0]);
  };
  return <mark className={'lab-an-mark' + (ids.length > 1 ? ' lab-an-mark-more' : '')} data-note={ids.join(' ')} onClick={click}>{children}</mark>;
}

// One piece of text with the marks that fall in it, each quote's number after its last part.
function Text({ k, text, hits }) {
  const list = hits ? hits.get(k) : null;
  if (!list || !list.length) return text;
  const cuts = [...new Set([0, text.length].concat(...list.map((h) => [h.s, h.e])))].sort((a, b) => a - b);
  const out = [];
  for (let i = 0; i < cuts.length - 1; i += 1) {
    const a = cuts[i];
    const b = cuts[i + 1];
    const on = list.filter((h) => h.s <= a && h.e >= b).sort((x, y) => x.n - y.n);
    const seg = text.slice(a, b);
    out.push(on.length ? <Mark key={'m' + a} ids={on.map((h) => h.id)}>{seg}</Mark> : <React.Fragment key={'t' + a}>{seg}</React.Fragment>);
    list.filter((h) => h.end && h.e === b).sort((x, y) => x.n - y.n)
      .forEach((h) => out.push(<GoPin key={'p' + h.id} n={h.n} id={h.id} />));
  }
  return out;
}

// Where a note is: the span's kind, the spans above it (chat aside), its name and the side. The full path on hover.
export function AnchorLine({ anchor }) {
  const a = anchor || {};
  return (
    <span className="lab-an-line" title={a.text || undefined}>
      {a.type ? <TypeIcon type={a.type} /> : null}
      {a.path && a.path.length ? <span className="lab-an-path">{a.path.join(' › ') + ' ›'}</span> : null}
      <span className="lab-an-name">{a.name || ''}</span>
      {a.side ? <span className="lab-an-side">{'› ' + SIDES[a.side]}</span> : null}
    </span>
  );
}

// How many notes each span holds, by side, and how many sit on the spans inside it: Map(id -> { total, in, out, other,
// inside }).
export function noteTally(notes, nodes) {
  const out = new Map();
  const get = (id) => {
    if (!out.has(id)) out.set(id, { total: 0, in: 0, out: 0, other: 0, inside: 0 });
    return out.get(id);
  };
  (notes || []).forEach((x) => {
    if (x.nodeId == null) return;
    const t = get(String(x.nodeId));
    t.total += 1;
    t[x.side === 'in' || x.side === 'out' ? x.side : 'other'] += 1;
  });
  const parent = new Map((nodes || []).map((n) => [String(n.id), n.parent_id == null ? null : String(n.parent_id)]));
  [...out].forEach(([id, t]) => {
    if (!t.total) return;
    const seen = new Set([id]);
    for (let p = parent.get(id); p != null && !seen.has(p); p = parent.get(p)) {
      seen.add(p);
      get(p).inside += t.total;
    }
  });
  return out;
}

const tallyText = (t) => [noteWord(t.total)].concat(t.in ? ['Input ' + t.in] : [], t.out ? ['Output ' + t.out] : [],
  t.other ? ['Other notes ' + t.other] : []).join(' · ');

// A span's notes in the tree: a speech bubble and how many (the sides on hover); a collapsed span also says how many
// notes the spans inside it hold.
export function NoteBadges({ tally, closed }) {
  const t = tally || {};
  const own = t.total ? tallyText(t) : '';
  const inside = closed && t.inside ? noteWord(t.inside) + ' on spans inside' : '';
  return (
    <>
      {own ? <span className="lab-tt-ncount" role="img" aria-label={own} title={own}><Bubble />{t.total}</span> : null}
      {inside ? <span className="lab-tt-ncount lab-tt-ncount-in" role="img" aria-label={inside} title={inside}><Bubble />{t.inside}</span> : null}
    </>
  );
}

// "Notes on this turn": every note in tree order, its number, where it is, what it quotes and says, who and when.
// A row selects its span and brings its note into view.
function NotesOverview({ notes, open, onToggle, go }) {
  if (!notes || !notes.length) return null;
  const spans = new Set(notes.map((x) => x.nodeId).filter((x) => x != null)).size;
  return (
    <section className="lab-tt-ov" aria-label="Notes on this turn">
      <div className="lab-tt-ovhead">
        <button type="button" className="lab-tt-ovtoggle" aria-expanded={open ? 'true' : 'false'} onClick={onToggle}>
          {'Notes on this turn'}
        </button>
        <span className="lab-tt-ovcount">{noteWord(notes.length) + ' on ' + spans + (spans === 1 ? ' span' : ' spans')}</span>
      </div>
      {open ? (
        <ol className="lab-tt-ovlist">
          {notes.map((x) => {
            const rel = relTime(x.at);
            return (
              <li key={x.id}>
                <button type="button" className="lab-tt-ovrow" data-note={x.id} onClick={() => go(x.id, 'center')}>
                  <span className="lab-an-pin">{x.n}</span>
                  <span className="lab-tt-ovmain">
                    <AnchorLine anchor={x.anchor} />
                    <span className="lab-tt-ovtext">
                      {x.quote ? (
                        <span className={'lab-tt-ovquote' + (x.found === false ? ' lab-tt-ovquote-stale' : '')}>
                          {'“' + excerpt(x.quote, 90) + '”' + (x.found === false ? ' (quoted text changed)' : '')}
                        </span>
                      ) : null}
                      {x.note ? <span className="lab-tt-ovnote">{excerpt(x.note, 160)}</span> : null}
                    </span>
                  </span>
                  <span className="lab-tt-ovby">
                    <span className="lab-tt-ovwho">{x.mine ? 'You' : x.who}</span>
                    {rel ? <time dateTime={x.at} title={x.when || undefined}>{rel}</time> : null}
                  </span>
                </button>
              </li>
            );
          })}
        </ol>
      ) : null}
    </section>
  );
}

function Inline({ parts, hits }) {
  return parts.map((p, i) => {
    const t = <Text k={p.k} text={p.text} hits={hits} />;
    if (p.t === 'b') return <strong key={i}>{t}</strong>;
    if (p.t === 'i') return <em key={i}>{t}</em>;
    if (p.t === 'code') return <code key={i}>{t}</code>;
    return <React.Fragment key={i}>{t}</React.Fragment>;
  });
}

// Markdown drawn as React elements from plain data blocks, never as HTML.
function Markdown({ blocks, hits }) {
  return blocks.map((b, i) => {
    if (b.t === 'h') return <p key={i} className={'lab-tt-md-h lab-tt-md-h' + b.level}><Inline parts={b.inline} hits={hits} /></p>;
    if (b.t === 'p') return <p key={i}><Inline parts={b.inline} hits={hits} /></p>;
    if (b.t === 'code') return <pre key={i} className="lab-tt-pre"><Text k={b.k} text={b.text} hits={hits} /></pre>;
    if (b.t === 'ul' || b.t === 'ol') {
      const List = b.t;
      return <List key={i}>{b.items.map((it, j) => <li key={j}><Inline parts={it} hits={hits} /></li>)}</List>;
    }
    return (
      <div key={i} className="lab-tt-tablewrap">
        <table>
          <thead><tr>{b.head.map((c, j) => <th key={j}><Inline parts={c} hits={hits} /></th>)}</tr></thead>
          <tbody>{b.rows.map((r, j) => <tr key={j}>{r.map((c, k) => <td key={k}><Inline parts={c} hits={hits} /></td>)}</tr>)}</tbody>
        </table>
      </div>
    );
  });
}

function Value({ view, hits }) {
  if (view.kind === 'none') return <p className="lab-small">{'Nothing recorded.'}</p>;
  if (view.kind === 'pre') return <pre className="lab-tt-pre"><Text k={0} text={view.text} hits={hits} /></pre>;
  return <div className="lab-tt-md"><Markdown blocks={view.blocks} hits={hits} /></div>;
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

function TreeView({ nodes, selected, onSelect, tallyOf, reveal, jump }) {
  const [collapsed, setCollapsed] = React.useState(() => new Set());
  const [query, setQuery] = React.useState('');
  // Opened on one step (the Notes page links to the step a note is on, 10 Nov 2026): bring that row into view once.
  const list = React.useRef(null);
  React.useEffect(() => {
    const row = reveal && list.current ? list.current.querySelector('.lab-on') : null;
    if (row && row.scrollIntoView) row.scrollIntoView({ block: 'center' });
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  // A note was picked from "Notes on this turn" or a number: open the spans above its span and bring its row into view.
  React.useEffect(() => {
    if (!jump || jump.node == null) return undefined;
    const byId = new Map(nodes.map((n) => [String(n.id), n]));
    const up = [];
    const seen = new Set();
    const at = byId.get(String(jump.node));
    for (let p = at ? byId.get(String(at.parent_id)) : null; p && !seen.has(String(p.id)); p = byId.get(String(p.parent_id))) {
      seen.add(String(p.id));
      up.push(String(p.id));
    }
    setCollapsed((prev) => {
      if (!up.some((id) => prev.has(id))) return prev;
      const next = new Set(prev);
      up.forEach((id) => next.delete(id));
      return next;
    });
    if (typeof requestAnimationFrame !== 'function') return undefined;
    const raf = requestAnimationFrame(() => {
      const row = list.current ? list.current.querySelector('.lab-on') : null;
      if (row && row.scrollIntoView) row.scrollIntoView({ block: 'nearest' });
    });
    return () => cancelAnimationFrame(raf);
  }, [jump]); // eslint-disable-line react-hooks/exhaustive-deps
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
        <input type="search" placeholder="Search spans" aria-label="Search spans" value={query}
          onChange={(e) => setQuery(e.target.value)} />
      </label>
      {rows.length ? (
        <ul className="lab-tt-tree" role="tree" aria-label="Spans" ref={list}>
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
                  {tallyOf ? <NoteBadges tally={tallyOf(id)} closed={r.hasKids && !r.open} /> : null}
                </button>
              </li>
            );
          })}
        </ul>
      ) : <p className="lab-small">{'No span matches.'}</p>}
    </>
  );
}

// The timeline, as Langfuse draws it: a ruler, then one bar per step on that time axis, labelled with how long it took
// and what its model calls cost (summed down its steps). Bars too short for a label carry it beside them; hovering a bar
// names the step; Show labels writes the names on the bars; − / + zoom the axis.
const ZOOMS = [1, 1.5, 2, 3, 4, 6, 8];

function TimelineView({ nodes, turn, selected, onSelect, tallyOf }) {
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
          <ol className="lab-tt-timeline" aria-label="Spans on a time axis">
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
                      {tallyOf ? <NoteBadges tally={tallyOf(b.id)} closed={false} /> : null}
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
  if (!f.steps.length) return <p className="lab-small">{'This trace has no spans to draw.'}</p>;
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
      <svg className="lab-tt-graph" width={width * scale} height={height * scale} viewBox={'0 0 ' + width + ' ' + height} role="group"
        aria-label="The spans of this trace in the order they ran">
        <defs>
          <marker id="lab-tt-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto">
            <path d="M0,0 L8,4 L0,8 z" className="lab-tt-arrowhead" />
          </marker>
        </defs>
        {items.slice(1).map((st, i) => (
          <path key={'e' + i} className="lab-tt-edge" markerEnd="url(#lab-tt-arrow)" aria-hidden="true"
            d={'M' + cx + ',' + (y(i) + G.h) + ' L' + cx + ',' + (y(i + 1) - 2)} />
        ))}
        {items.map((st, i) => {
          const w = widthOf(st);
          const x = cx - w / 2;
          if (st.start) {
            return (
              <g key="start" className="lab-tt-gstart" aria-hidden="true" transform={'translate(' + x + ',' + y(i) + ')'}>
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
            <g key={st.key} className={'lab-tt-gnode lab-tt-gnode-' + type + (on ? ' lab-on' : '') + (err ? ' lab-tt-gnode-error' : '')}
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
    ['Span id', node.id], ['Parent span', node.parent_id], ['Sequence', node.seq],
    ['Trace id', turn.turn_id], ['Langfuse trace id', turn.langfuse_trace_id], ['Session', turn.session_id],
  ].filter(([, v]) => v !== null && v !== undefined && v !== '');
  return (
    <dl className="lab-tt-meta">
      {rows.map(([k, v]) => (
        <React.Fragment key={k}><dt>{k}</dt><dd className={/id$|Session/.test(k) ? 'lab-mono' : undefined}>{String(v)}</dd></React.Fragment>
      ))}
    </dl>
  );
}

function Detail({ node, turn, pane, setPane, mode, setMode, annotate, notes, shut, setShut }) {
  const f = node.filled || {};
  const id = String(node.id);
  const on = (side) => (notes ? notes.filter((x) => String(x.nodeId) === id && x.side === side) : null);
  const toggle = (side) => () => setShut((s) => Object.assign({}, s, { [side]: !s[side] }));
  return (
    <section className="lab-tt-detail" aria-label={'Span ' + node.name}>
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
        <Switch label="Span detail" items={[['preview', 'Preview'], ['metadata', 'Metadata']]} value={pane} onChange={setPane} />
        {pane === 'preview'
          ? <Switch label="How values show" className="lab-tt-mode" items={[['formatted', 'Formatted'], ['raw', 'Raw']]} value={mode} onChange={setMode} />
          : null}
      </div>
      {pane === 'metadata' ? <Meta node={node} turn={turn} /> : (
        <>
          <Side node={node} side="in" title="Input" mode={mode} annotate={annotate} notes={on('in')} open={!shut.in} onToggle={toggle('in')}
            filled={f.input_from ? 'Filled from ' + f.input_from + ' (this span recorded no input of its own).' : ''} />
          <Side node={node} side="out" title="Output" mode={mode} annotate={annotate} notes={on('out')} open={!shut.out} onToggle={toggle('out')}
            filled={f.output_from ? 'Filled from ' + f.output_from + '.' : ''} />
          {annotate && node.parent_id == null && annotate.other ? annotate.other() : null}
        </>
      )}
    </section>
  );
}

// One side of a step (Input or Output): its heading (with the numbers of notes on the whole side, or whose quote is no
// longer in it), its value with each quote marked and, when the page annotates, the notes on that side under it.
// Selecting text in the value or double-clicking it hands the side's note key to the page (today's note gestures).
function Side({ node, side, title, mode, annotate, filled, notes, open, onToggle }) {
  const id = String(node.id);
  const key = annotate ? annotate.keyOf(id, side) : null;
  const v = side === 'in' ? node.input : node.output;
  const view = viewOf(v, mode);
  const { hits, missing } = placeMarks(view.pieces, notes);
  const pins = (notes || []).filter((x) => !x.quote).concat(missing).sort((a, b) => a.n - b.n);
  return (
    <div className="lab-tt-io" role="group" aria-label={title}>
      <div className="lab-tt-iohead">
        <button type="button" className="lab-tt-iotoggle" aria-expanded={open ? 'true' : 'false'} onClick={onToggle}>{title}</button>
        {pins.map((x) => (
          <GoPin key={x.id} n={x.n} id={x.id}
            label={'Go to note ' + x.n + (x.quote ? ' (its quoted text is no longer here)' : ' (on the whole ' + title + ')')} />
        ))}
      </div>
      {open ? (
        <>
          {filled ? <p className="lab-small">{filled}</p> : null}
          {key ? (
            <div className="lab-tt-val" data-note-key={key} title="Select text to note it, or double-click to add a note"
              onMouseUp={(e) => annotate.up(key, e.currentTarget, e)} onDoubleClick={(e) => annotate.double(key, e)}>
              <Value view={view} hits={hits} />
            </div>
          ) : <Value view={view} hits={hits} />}
          {key ? annotate.render(id, side) : null}
        </>
      ) : null}
    </div>
  );
}

const reducedMotion = () => {
  try { return typeof window !== 'undefined' && !!window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches; } catch (e) { return false; }
};
const holds = (el, attr, id) => (el.getAttribute(attr) || '').split(' ').indexOf(id) >= 0;

// The trace of one answer. Its state starts from these props so any view can be drawn directly. `annotate` (optional,
// from the question page and the session page) adds notes: { keyOf(nodeId, side), count(nodeId), render(nodeId, side),
// up(key, el, e), double(key, e), other(), notes }. `notes` (optional) is every note in tree order, numbered:
// [{ id, n, nodeId, side ('in' | 'out' | null for Other notes), anchor, quote, note, who, mine, at, when }]. With it the
// view marks quotes, numbers notes, tells the sides apart in the tree and lists "Notes on this turn"; without it (the
// question page) the tree shows counts as before.
export function TraceView({ tree, view = 'tree', selectedId, pane = 'preview', mode = 'formatted', annotate }) {
  const tr = tree || {};
  const t = tr.turn || {};
  const nodes = Array.isArray(tr.nodes) ? tr.nodes : [];
  const first = nodes.length ? String(nodes[0].id) : null;
  const [v, setV] = React.useState(view);
  const [sel, setSel] = React.useState(selectedId != null ? String(selectedId) : first);
  const [p, setP] = React.useState(pane);
  const [md, setMd] = React.useState(mode);
  const [shut, setShut] = React.useState({});
  const [ovOpen, setOvOpen] = React.useState(true);
  const [jump, setJump] = React.useState(null);
  const node = nodes.find((n) => String(n.id) === sel) || nodes[0] || null;
  const notes = annotate && Array.isArray(annotate.notes) && annotate.notes.length ? annotate.notes : null;
  const tally = React.useMemo(() => (notes ? noteTally(notes, nodes) : null), [notes, nodes]);
  const counted = annotate && annotate.count ? (id) => ({ total: annotate.count(id) || 0 }) : null;
  const tallyOf = tally ? (id) => tally.get(String(id)) || null : counted;
  // Going to a note: select its span, show its side, then (once drawn) scroll its card into view and light it briefly.
  const root = React.useRef(null);
  const seq = React.useRef(0);
  const goNow = (id, block) => {
    const x = (notes || []).find((n) => n.id === id);
    if (!x) return;
    if (x.nodeId != null) setSel(String(x.nodeId));
    setP('preview');
    if (x.side) setShut((s) => (s[x.side] ? Object.assign({}, s, { [x.side]: false }) : s));
    seq.current += 1;
    setJump({ node: x.nodeId, note: id, block: block || 'nearest', seq: seq.current });
  };
  const goRef = React.useRef(goNow);
  goRef.current = goNow;
  const nav = React.useMemo(() => ({ go: (id, block) => goRef.current(id, block) }), []);
  React.useEffect(() => {
    const el = root.current;
    if (!jump || !el) return undefined;
    const card = Array.from(el.querySelectorAll('[data-note-card]')).find((c) => c.getAttribute('data-note-card') === jump.note);
    if (!card) return undefined;
    if (card.scrollIntoView) card.scrollIntoView({ block: jump.block, behavior: reducedMotion() ? 'auto' : 'smooth' });
    try { card.focus({ preventScroll: true }); } catch (e) { /* not focusable */ }
    card.removeAttribute('data-flash');
    void card.offsetWidth; // restart the highlight when the same note is picked twice
    card.setAttribute('data-flash', '');
    const timer = setTimeout(() => card.removeAttribute('data-flash'), 1600);
    return () => { clearTimeout(timer); card.removeAttribute('data-flash'); };
  }, [jump]);
  // Hovering or focusing a note's mark, number, card or row lights all of them (attributes only: nothing re-draws).
  const hot = React.useRef(null);
  const light = (id) => {
    const el = root.current;
    if (!el || hot.current === id) return;
    el.querySelectorAll('[data-hot]').forEach((x) => x.removeAttribute('data-hot'));
    hot.current = id;
    if (id) el.querySelectorAll('[data-note]').forEach((x) => { if (holds(x, 'data-note', id)) x.setAttribute('data-hot', ''); });
  };
  const noteAt = (target) => {
    const x = target && target.closest ? target.closest('[data-note]') : null;
    return x ? (x.getAttribute('data-note') || '').split(' ')[0] || null : null;
  };
  const hover = notes ? {
    onMouseOver: (e) => light(noteAt(e.target)), onMouseLeave: () => light(null),
    onFocus: (e) => light(noteAt(e.target)), onBlur: () => light(null)
  } : {};
  return (
    <NoteNav.Provider value={notes ? nav : null}>
    <section className="lab-tt" aria-label="Trace" ref={root} {...hover}>
      <header className="lab-tt-head">
        <code className="lab-tt-tid" title="Trace id">{t.turn_id || '—'}</code>
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
          {v === 'timeline' ? <TimelineView nodes={nodes} turn={t} selected={node && String(node.id)} onSelect={setSel} tallyOf={tallyOf} />
            : v === 'graph' ? <GraphView nodes={nodes} selected={node && String(node.id)} onSelect={setSel} />
              : <TreeView nodes={nodes} selected={node && String(node.id)} onSelect={setSel} tallyOf={tallyOf}
                reveal={selectedId != null} jump={jump} />}
        </div>
        <div className="lab-tt-right">
          <NotesOverview notes={notes} open={ovOpen} onToggle={() => setOvOpen(!ovOpen)} go={nav.go} />
          {node ? <Detail node={node} turn={t} pane={p} setPane={setP} mode={md} setMode={setMd} annotate={annotate}
            notes={notes} shut={shut} setShut={setShut} />
            : <p className="lab-small">{'This trace has no spans.'}</p>}
        </div>
      </div>
    </section>
    </NoteNav.Provider>
  );
}
