// MarginNotes — the annotation layer on the Conversations timeline (logic/annotate.js).
//
// AnnotatedText renders one annotatable block as the service's own text, pre-wrapped, with yellow highlights;
// selecting inside it opens the note box. MarginNotes is the 240 px column beside the timeline: one note per wrong
// verdict, aligned to its highlight's top, stacked with an 8 px gap, and folded into "+k more notes here" rather
// than piling up. UnplacedNotes lists the notes whose text is not on screen: its quote is gone from the text, or
// the card holding it is closed. Anchors are a step id plus a quote and its occurrence, never a DOM offset.
import React from 'react';
import * as ann from '../annotateActions.js';
import { blockView, marginView, placeMarginNotes } from '../logic/annotate.js';
import { suggestionMarks } from '../logic/suggest.js';
import { taxOf } from '../logic/taxCommon.js';

const TIMELINE = '[data-tl-timeline]';
// Measure before paint in a browser; anywhere else (a server render, a test) there is nothing to measure yet.
const useLayout = typeof window !== 'undefined' && window.document ? React.useLayoutEffect : React.useEffect;

// ── the browser's selection, as offsets inside one block ────────────────────────────────────────────────────────
// Character offset of (node, offset) inside root's text, counting text nodes in document order.
function offsetWithin(root, node, offset) {
  if (!root || !node) return -1;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  let total = 0;
  for (let t = walker.nextNode(); t; t = walker.nextNode()) {
    if (t === node) return total + offset;
    total += t.nodeValue.length;
  }
  return node === root ? total : -1;
}

function blockOf(node) {
  let el = node && node.nodeType === 3 ? node.parentNode : node;
  while (el && !(el.getAttribute && el.getAttribute('data-tl-block'))) el = el.parentNode;
  return el || null;
}

// The selection inside `root` (a block's element) as annotateActions.selectFromBlock takes it: the block key of
// each end and the offsets of each end in this block's text (-1 for an end that is in another block). null when
// nothing is selected.
export function readSelection(root, sel) {
  if (!sel || sel.isCollapsed || !sel.rangeCount) return null;
  const range = sel.getRangeAt(0);
  const sEl = blockOf(range.startContainer);
  const eEl = blockOf(range.endContainer);
  return {
    startKey: sEl ? sEl.getAttribute('data-tl-block') : null, endKey: eEl ? eEl.getAttribute('data-tl-block') : null,
    start: sEl === root ? offsetWithin(root, range.startContainer, range.startOffset) : -1,
    end: eEl === root ? offsetWithin(root, range.endContainer, range.endOffset) : -1
  };
}

// A highlight and its note outline each other while either is hovered.
export function outline(root, ids, on) {
  if (!root) return;
  ids.forEach((id) => root.querySelectorAll('[data-tl-hl~="' + id + '"], [data-tl-note="' + id + '"]').forEach((el) => el.classList.toggle('tl-hover', on)));
}
const timelineOf = (e) => (e.currentTarget && e.currentTarget.closest ? e.currentTarget.closest(TIMELINE) : null);

// The AI suggestions on the open answer as highlight items, found in the block's own text (logic/suggest.js).
function suggestionMarksFor(s) {
  const t = taxOf(s);
  return (turnId, key, text) => (turnId && turnId === t.tlSugTurnId ? suggestionMarks(t.tlSug, key, text) : []);
}

export function AnnotatedText({ s, anchor, className, children }) {
  const b = s ? blockView(s, anchor, suggestionMarksFor(s)) : null;
  if (!b || !b.text) return children || null;
  const turnId = s.turnId;
  const onMouseUp = (e) => {
    const sel = readSelection(e.currentTarget, typeof window !== 'undefined' && window.getSelection ? window.getSelection() : null);
    if (sel) ann.selectFromBlock(turnId, b.block, sel);
  };
  const onKey = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); return ann.saveNote(turnId); }
    if (e.key === 'Escape') { e.preventDefault(); ann.cancelDraft(turnId); }
    return undefined;
  };
  return (
    <div>
      <div className={'tl-block' + (className ? ' ' + className : '')} data-tl-block={b.key} onMouseUp={onMouseUp}>
        {b.segments.map((seg) => seg.ids.length ? (
          <mark key={seg.start} data-tl-hl={seg.ids.join(' ')} className={((seg.ids.some((i) => i.indexOf('sug:') !== 0) ? 'tl-hl' : '') + (seg.ids.some((i) => i.indexOf('sug:') === 0) ? ' tl-hl-sug' : '')
              + (seg.ids.indexOf('draft') >= 0 ? ' tl-hl-draft' : '')).trim()}
            aria-describedby={seg.ids.filter((i) => i !== 'draft').map((i) => 'tl-note-' + i).join(' ') || undefined}
            onMouseEnter={(e) => outline(timelineOf(e), seg.ids, true)} onMouseLeave={(e) => outline(timelineOf(e), seg.ids, false)}>{seg.text}</mark>
        ) : <span key={seg.start}>{seg.text}</span>)}
      </div>
      {b.popover ? (
        <div className="tl-pop" role="dialog" aria-label="Note on the first failure">
          <textarea autoFocus aria-label="What went wrong first?" placeholder="What went wrong first? (Enter to save)" value={b.note}
            onChange={(e) => ann.setNote(turnId, e.target.value)} onKeyDown={onKey} maxLength={2000} className="tl-textarea" />
          <div className="tl-meta">{'First failure only, in your own words. Enter saves, Shift+Enter adds a line, Escape cancels.'}</div>
        </div>
      ) : null}
    </div>
  );
}

// ── the notes column ────────────────────────────────────────────────────────────────────────────────────────────
// Where each note's highlight is on screen, relative to the top of the timeline: {note id -> px}. A note whose
// highlight is not on screen has no entry.
export function measureTops(root, notes) {
  const out = {};
  if (!root) return out;
  const base = root.getBoundingClientRect().top;
  notes.forEach((n) => {
    const el = root.querySelector('[data-tl-hl~="' + n.id + '"]');
    if (el) out[n.id] = Math.round(el.getBoundingClientRect().top - base);
  });
  return out;
}

// The notes that have a highlight, stacked (logic/annotate.js placeMarginNotes), the ids of those that have none,
// and the height the column needs to hold what was placed.
export function layoutNotes(notes, tops, expanded) {
  const measured = notes.filter((n) => tops[n.id] !== undefined);
  const placed = placeMarginNotes(measured.map((n) => ({ id: n.id, top: tops[n.id], height: n.height })), { gap: 8, maxDrift: 240, expanded });
  return {
    placed, missing: notes.filter((n) => tops[n.id] === undefined).map((n) => n.id),
    height: placed.reduce((m, p) => Math.max(m, p.top + p.height), 0)
  };
}

// A note draws itself; an item with `render` (an AI suggestion's card) draws itself at its placed top, given the
// timeline `root` and a way to unfold the notes stacked under it.
export function NoteColumn({ notes, layout, onExpand, root }) {
  const byId = {};
  notes.forEach((n) => { byId[n.id] = n; });
  return (
    <aside aria-label="Notes on this answer" className="tl-margin" style={{ minHeight: layout.height + 'px' }}>
      {layout.placed.map((p) => {
        const n = byId[p.id];
        if (n.render) return n.render(p, root, () => onExpand(p.id));
        return (
          <div key={p.id} id={'tl-note-' + p.id} data-tl-note={p.id} className="tl-mnote" style={{ top: p.top + 'px', minHeight: p.height + 'px' }}
            onMouseEnter={(e) => outline(timelineOf(e), [p.id], true)} onMouseLeave={(e) => outline(timelineOf(e), [p.id], false)}>
            <div className="tl-mnote-text">{'✎ ' + n.note}</div>
            <div className="tl-meta">{'“' + n.clipped + '” — ' + n.who}</div>
            {p.more.length ? (
              <button type="button" className="tl-link tl-more" onClick={() => onExpand(p.id)}>
                {'+' + p.more.length + (p.more.length === 1 ? ' more note here' : ' more notes here')}
              </button>
            ) : null}
          </div>
        );
      })}
    </aside>
  );
}

// `rootRef` is the timeline's container (the one marked data-tl-timeline). `onMissing` is told the ids of the notes
// whose highlight is not on screen (a closed card), so UnplacedNotes can list them. `extra` are further items for the
// column, placed with the notes (the AI suggestions' cards: views/Suggestions.jsx suggestionMargin).
export default function MarginNotes({ s, rootRef, onMissing, extra }) {
  const notes = marginView(s).notes.concat(extra || []);
  const [tops, setTops] = React.useState({});
  const [expanded, setExpanded] = React.useState([]);
  const [, setTick] = React.useState(0);
  const told = React.useRef(null);
  React.useEffect(() => {
    if (typeof window === 'undefined' || !window.addEventListener) return undefined;
    const onResize = () => setTick((t) => t + 1);
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, []);
  useLayout(() => {
    const next = measureTops(rootRef && rootRef.current, notes);
    if (JSON.stringify(next) !== JSON.stringify(tops)) setTops(next);
    const missing = JSON.stringify(notes.filter((n) => next[n.id] === undefined).map((n) => n.id));
    if (onMissing && missing !== told.current) { told.current = missing; onMissing(JSON.parse(missing)); }
  });
  return <NoteColumn notes={notes} layout={layoutNotes(notes, tops, expanded)} onExpand={(id) => setExpanded(expanded.concat([id]))} root={rootRef ? rootRef.current : null} />;
}

export function UnplacedNotes({ s, also }) {
  const m = marginView(s);
  const ids = new Set((also || []).concat(m.unplaced.map((n) => n.id)));
  const list = m.notes.filter((n) => ids.has(n.id));
  if (!list.length) return null;
  return (
    <section aria-label="Notes whose text is not on screen" className="tl-unplaced">
      <div className="tl-verdict-label">{'Notes whose text is not on screen'}</div>
      {list.map((n) => (
        <div key={n.id} className="tl-meta">{'✎ ' + n.note + ' — “' + n.clipped + '”' + (n.step ? ' (step: ' + n.step + ')' : '') + ' — ' + n.who}</div>
      ))}
    </section>
  );
}
