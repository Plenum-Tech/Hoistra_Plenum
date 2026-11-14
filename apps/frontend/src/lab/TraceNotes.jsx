// TraceNotes — notes on a trace's spans (moved here from QuestionPage.jsx when Runs was deleted, 11 Nov 2026). A person
// picks a span, then presses "Add note" under its input or output, selects text in it to quote it, or double-clicks it.
// Notes are free text; each says who wrote it, their Trace Lab role and when; a person's own notes can be edited or
// deleted. A comment on the turn as a whole sits above the trace. Notes keep the step keys Trace Lab has always used
// (logic/traceNotes.js), so the Labels, Notes, Learning and Loop pages read them unchanged. They are step_labels rows,
// read with the trace and saved and removed through labApi as the trace's company.
import React from 'react';
import { labApi } from './labApi.js';
import { Clamp } from './AllLabels.jsx';
import { nextStepFor } from './NextStep.jsx';
import { NOTE_MAX, ROLE_TEXT, errText, isStale, selectedIn } from './logic/labelling.js';
import { SCENARIO } from './logic/steps.js';
import { fmtWhen } from './logic/format.js';
import { keyTitles, nodeKeys, stepOfKey, traceNotesIndex } from './logic/traceNotes.js';
import { AnchorLine, NotePin, quoteAt, relTime } from './TraceTab.jsx';

const ROLES = Object.assign({ consultant: 'Consultant' }, ROLE_TEXT);
const TURN_TITLE = 'Turn as a whole';
const noop = () => {};
const resolved = () => Promise.resolve();

// One note as the page shows it. A note written before 22 Oct may also carry a Right / Wrong tag: it is not shown.
export function noteView(x, me) {
  return {
    id: x.id, lineage: x.lineage_id || x.id, key: x.step_key, block: '', note: x.note || '', quote: x.quote || '',
    who: x.reviewer_email || 'Someone', role: ROLES[x.reviewer_role] || x.reviewer_role || '', at: x.at || '',
    when: fmtWhen(x.at), edited: !!x.edited, mine: !!me && x.reviewer_id === me, expected: x.expected || '',
    onlyIf: x.only_if || '', title: x.step_title || ''
  };
}

// ── Where a note is (14 Nov 2026) ────────────────────────────────────────────────────────────────────────────────────
// Every card names the span its note is on and the side: the span's kind, the spans above it (chat aside), its name and
// "Input" / "Output" ("compliance › analyst › Output"). Notes are numbered in tree order, the same numbers the trace
// view puts in the text, by the block headings and in "Notes on this turn".
const TURN_ANCHOR = Object.freeze({ type: null, path: [], name: TURN_TITLE, side: null, text: TURN_TITLE });
const SIDE_TEXT = { in: 'Input', out: 'Output' };
const atOf = (n) => { const t = Date.parse(n.at || ''); return Number.isNaN(t) ? 0 : t; };

// The anchor of one span's side: { type, path (the spans above it, chat aside), name, side, text }.
export function anchorOfNode(nodes, id, side) {
  const list = Array.isArray(nodes) ? nodes : [];
  const byId = new Map(list.map((n) => [String(n.id), n]));
  const node = byId.get(String(id));
  if (!node) return null;
  const path = [];
  const seen = new Set([String(node.id)]);
  for (let p = byId.get(String(node.parent_id)); p && p.parent_id != null && !seen.has(String(p.id)); p = byId.get(String(p.parent_id))) {
    seen.add(String(p.id));
    path.unshift(String(p.name || ''));
  }
  const name = String(node.name || '');
  const s = SIDE_TEXT[side] ? side : null;
  return { type: node.type || 'span', path, name, side: s, text: path.concat([name]).join(' › ') + (s ? ' › ' + SIDE_TEXT[s] : '') };
}

// The anchor of a note key: its span and side, the turn as a whole, the orchestrator (older notes), or the title it was
// saved with when its span is no longer in the trace.
export function noteAnchor(key, record, nodes, turnId, title) {
  const k = String(key || '');
  if (turnId && k === turnId + SCENARIO) return TURN_ANCHOR;
  const at = k ? stepOfKey(record, nodes, k) : null;
  const a = at ? anchorOfNode(nodes, at.id, at.side) : null;
  if (a) return a;
  if (/:orchestrator$/.test(k)) return { type: 'agent', path: [], name: 'Orchestrator', side: null, text: 'Orchestrator' };
  const name = String(title || '') || 'A span no longer in this trace';
  return { type: 'span', path: [], name, side: null, text: name };
}

// The notes index (logic/traceNotes.js) numbered and anchored. In tree order, Input before Output; on one side the notes
// on the whole block first, then quotes in the order they sit in the text, then quotes no longer there; Other notes last.
// Each note gains { n, nodeId, side, anchor, found (its quote is still in the text; null without a quote) }.
// Returns { list (every numbered note), slots, other, scenario (the turn's comments: anchored, not numbered) }.
export function placeNotes(ix, record, nodes, turnId) {
  const list = Array.isArray(nodes) ? nodes : [];
  const index = ix || { slots: {}, other: [], scenario: [] };
  const root = list.find((n) => n && n.parent_id == null) || null;
  const out = { list: [], slots: {}, other: [], scenario: [] };
  let n = 0;
  list.forEach((node) => {
    const id = String(node.id);
    ['in', 'out'].forEach((side) => {
      const items = (index.slots || {})[id + ':' + side] || [];
      if (!items.length) return;
      const value = side === 'in' ? node.input : node.output;
      const anchor = anchorOfNode(list, id, side);
      const ranked = items.map((x) => {
        const at = x.quote ? quoteAt(value, x.quote) : -1;
        return { x, at, group: !x.quote ? 0 : at >= 0 ? 1 : 2 };
      });
      ranked.sort((a, b) => a.group - b.group || (a.group === 1 ? a.at - b.at : 0) || atOf(a.x) - atOf(b.x));
      out.slots[id + ':' + side] = ranked.map(({ x, at }) => {
        n += 1;
        const v = Object.assign({}, x, { n, nodeId: id, side, anchor, found: x.quote ? at >= 0 : null });
        out.list.push(v);
        return v;
      });
    });
  });
  out.other = (index.other || []).slice().sort((a, b) => atOf(a) - atOf(b)).map((x) => {
    n += 1;
    const v = Object.assign({}, x, { n, nodeId: root ? String(root.id) : null, side: null,
      anchor: noteAnchor(x.key, record, list, turnId, x.title), found: null });
    out.list.push(v);
    return v;
  });
  out.scenario = (index.scenario || []).map((x) => Object.assign({}, x, { anchor: TURN_ANCHOR }));
  return out;
}

// "AS" for aasim.shaik@…, "S" for sme@….
export function initialsOf(who) {
  const local = String(who || '').split('@')[0];
  const parts = local.split(/[._\-\s]+/).filter(Boolean);
  return parts.slice(0, 2).map((p) => p[0].toUpperCase()).join('') || '?';
}

function clearPageSelection() {
  try { if (typeof window !== 'undefined' && window.getSelection) window.getSelection().removeAllRanges(); } catch (e) { /* nothing selected */ }
}

export const NOTES_ON = Object.freeze({
  saveTurnComment: resolved, openEditor: noop, blockUp: noop, blockDouble: noop, editorText: noop, editorSave: noop,
  editorCancel: noop, editNote: noop, askDelete: noop, deleteNote: noop
});

// The note editor, in the notes column beside its block: free text, Save / Cancel, Escape cancels, Ctrl/Cmd+Enter saves.
export function NoteEditor({ value, quote, target, anchor, editing, saving, error, onChange, onSave, onCancel }) {
  const ref = React.useRef(null);
  React.useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.focus();
    const n = el.value.length;
    try { el.setSelectionRange(n, n); } catch (e) { /* not a text field */ }
  }, []);
  const text = String(value || '');
  const ready = !!text.trim() && !saving;
  const change = onChange || noop;
  const save = onSave || noop;
  const cancel = onCancel || noop;
  const onKey = (e) => {
    if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); if (!saving) cancel(); return; }
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); if (ready) save(); }
  };
  const label = editing ? 'Edit your note' : 'New note';
  return (
    <div className="lab-ne" onKeyDown={onKey}>
      {anchor ? <div className="lab-ne-head lab-an-head"><span>{label + ' on'}</span><AnchorLine anchor={anchor} /></div>
        : <div className="lab-ne-head">{label + (target ? ' · ' + target : '')}</div>}
      {quote ? <blockquote className="lab-quote lab-quote-small">{quote}</blockquote> : null}
      <textarea ref={ref} className="lab-note lab-ne-text" rows={4} maxLength={NOTE_MAX} value={text} aria-label={label}
        placeholder="What you notice here, in your own words" onChange={(e) => change(e.target.value)} disabled={!!saving} />
      {error ? <div role="alert" className="lab-error">{error}</div> : null}
      <div className="lab-row lab-ne-actions">
        <button type="button" className="lab-btn lab-btn-sm" onClick={() => { if (ready) save(); }} disabled={!ready}>{saving ? 'Saving…' : 'Save'}</button>
        <button type="button" className="lab-btn lab-btn-quiet lab-btn-sm" onClick={cancel} disabled={!!saving}>{'Cancel'}</button>
        <span className="lab-small lab-ne-hint">{'Ctrl/⌘+Enter saves · Esc cancels'}</span>
      </div>
    </div>
  );
}

// One note: where it is (its number, the span and side), what it quotes and says, then who wrote it and when (the exact
// time on hover), with Edit / Delete on the reader's own. A quote no longer in the span's text says so.
function NoteItem({ n, busy, confirming, on }) {
  const changed = !!n.quote && n.found === false;
  const rel = relTime(n.at);
  return (
    <li className={'lab-nt' + (n.mine ? ' lab-nt-mine' : '')} data-note={n.n ? n.id : undefined} data-note-card={n.id} tabIndex={-1}>
      {n.anchor ? (
        <div className="lab-an-head">
          {n.n ? <NotePin n={n.n} id={n.id} /> : null}
          <AnchorLine anchor={n.anchor} />
          {changed ? <span className="lab-an-changed">{'(quoted text changed)'}</span> : null}
        </div>
      ) : null}
      {n.quote ? <blockquote className={'lab-quote lab-quote-small' + (changed ? ' lab-an-stale' : '')}>{n.quote}</blockquote> : null}
      {n.note ? <div className="lab-nt-text"><Clamp text={n.note}>{n.note}</Clamp></div>
        : (n.quote || n.expected ? null : <div className="lab-small">{'No note written.'}</div>)}
      {n.expected ? <div className="lab-small lab-rule"><strong>{'Should: '}</strong>{n.expected}</div> : null}
      {n.onlyIf ? <div className="lab-small lab-rule"><strong>{'Only if: '}</strong>{n.onlyIf}</div> : null}
      <div className="lab-an-foot">
        <span className="lab-an-avatar" aria-hidden="true">{initialsOf(n.who)}</span>
        <span className="lab-an-by">
          <span className="lab-an-who" title={n.who}>{n.mine ? 'You' : n.who}</span>
          {n.role ? <span className="lab-nt-role">{n.role}</span> : null}
          {rel ? <time dateTime={n.at} title={n.when || undefined}>{rel}</time> : null}
          {n.edited ? <span>{'edited'}</span> : null}
        </span>
        {n.mine ? (confirming ? (
          <span className="lab-row lab-nt-actions" role="group" aria-label="Delete this note?">
            <span className="lab-small">{'Delete this note?'}</span>
            <button type="button" className="lab-link lab-danger" onClick={() => on.deleteNote(n)} disabled={busy}>{'Delete'}</button>
            <button type="button" className="lab-link" onClick={() => on.askDelete(null)} disabled={busy}>{'Keep'}</button>
          </span>
        ) : (
          <span className="lab-row lab-nt-actions">
            <button type="button" className="lab-link" onClick={() => on.editNote(n)} disabled={busy}>{'Edit'}</button>
            <button type="button" className="lab-link lab-danger" onClick={() => on.askDelete(n)} disabled={busy}>{'Delete'}</button>
          </span>
        )) : null}
      </div>
    </li>
  );
}

// Everyone's notes on one place, in number order (the order their marks sit in the text); the one being edited is its
// editor.
export function NoteList({ items, editor, busy, confirming, on }) {
  const h = Object.assign({}, NOTES_ON, on || {});
  if (!items || !items.length) return null;
  return (
    <ul className="lab-nts" aria-label="Notes">
      {items.map((n) => (editor && editor.edit && editor.edit.lineage === n.lineage ? (
        <li key={n.id} className="lab-nt lab-nt-editing">
          <NoteEditor value={editor.text} quote={editor.quote} anchor={n.anchor} editing saving={editor.saving} error={editor.error}
            onChange={h.editorText} onSave={h.editorSave} onCancel={h.editorCancel} />
        </li>
      ) : <NoteItem key={n.id} n={n} busy={!!busy} confirming={confirming === n.id} on={h} />))}
    </ul>
  );
}

// The notes on one side (Input or Output) of a span: the editor when a note is being written there, everyone's notes,
// an error, and Add note.
function NotesSlot({ noteKey, title, anchor, items, editor, busy, error, confirming, on }) {
  const writing = editor && !editor.edit && editor.block === noteKey ? editor : null;
  return (
    <div className="lab-tt-notes" aria-label={'Notes on ' + title}>
      {writing ? (
        <NoteEditor value={writing.text} quote={writing.quote} target={writing.target} anchor={anchor} saving={writing.saving}
          error={writing.error} onChange={on.editorText} onSave={on.editorSave} onCancel={on.editorCancel} />
      ) : null}
      <NoteList items={items} editor={editor} busy={busy} confirming={confirming} on={on} />
      {error ? <div role="alert" className="lab-error">{error}</div> : null}
      {writing ? null : (
        <button type="button" className="lab-link lab-tt-add" aria-label={'Add note on ' + title}
          onClick={() => on.openEditor(noteKey, '')}>{'Add note'}</button>
      )}
    </div>
  );
}

// A comment on the turn as a whole (a note on "<turn>:scenario"), with the comments already written under it.
export function TurnComment({ onSave, children }) {
  const [text, setText] = React.useState('');
  const [st, setSt] = React.useState({ busy: false, error: '' });
  const save = async () => {
    const t = text.trim();
    if (!t || st.busy) return;
    setSt({ busy: true, error: '' });
    try {
      await (onSave || resolved)(t);
      setText('');
      setSt({ busy: false, error: '' });
    } catch (e) {
      setSt({ busy: false, error: errText(e) });
    }
  };
  return (
    <section className="lab-qp-scenario" aria-label="Comments on this turn as a whole">
      <textarea className="lab-note lab-qp-scenario-input" rows={2} maxLength={NOTE_MAX} value={text} disabled={st.busy}
        aria-label="Comment on this turn as a whole" placeholder="Comment on this turn as a whole…"
        onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); save(); } }} />
      <div className="lab-row">
        <button type="button" className="lab-btn lab-btn-sm" onClick={save} disabled={st.busy || !text.trim()}>{st.busy ? 'Saving…' : 'Save comment'}</button>
      </div>
      {st.error ? <div role="alert" className="lab-error">{st.error}</div> : null}
      {children}
    </section>
  );
}

const STALE_TEXT = 'This note changed in another window. The notes were read again.';

// Notes on one trace: everything the trace view and the comment box need, from the trace's chat `record` (which gives
// each span its note keys), its `nodes`, the notes already written (`labels`) and the trace's company (`org`).
// `onSaved()` runs after a new note is saved (the page offers the next step). Returns { annotate (for TraceView),
// notes (the index), on (stable handlers), editor, busy, errors, confirming }.
export function useTraceNotes({ turnId, org, record, nodes, me, labels, onSaved }) {
  const [rows, setRows] = React.useState(labels || []);
  const [editor, setEditor] = React.useState(null);
  const [busy, setBusy] = React.useState(false);
  const [errors, setErrors] = React.useState({});
  const [confirming, setConfirming] = React.useState(null);
  React.useEffect(() => {
    setRows(labels || []); setEditor(null); setErrors({}); setConfirming(null);
  }, [turnId, labels]);
  const list = Array.isArray(nodes) ? nodes : [];
  const keys = React.useMemo(() => nodeKeys(record, list), [record, list]);
  const titles = React.useMemo(() => keyTitles(keys), [keys]);
  const notes = React.useMemo(() => traceNotesIndex(rows, record, list, me, noteView), [rows, record, list, me]);
  const placed = React.useMemo(() => placeNotes(notes, record, list, turnId), [notes, record, list, turnId]);
  const targetOf = (key) => (turnId && key === turnId + SCENARIO ? TURN_TITLE : titles[key] || '');
  const reload = async () => {
    try {
      const out = await labApi.turnLabels(turnId, org);
      setRows((out && out.labels) || []);
    } catch (e) { /* the message on screen already says what failed */ }
  };
  const fns = {
    saveTurnComment: async (text) => {
      const out = await labApi.saveLabel(turnId, { step_key: turnId + SCENARIO, note: text, step_title: TURN_TITLE }, org);
      const saved = out && out.label;
      if (saved) setRows((p) => p.concat([saved]));
      if (onSaved) onSaved(nextStepFor('note_saved'));
    },
    openEditor: (key, quote) => {
      setConfirming(null);
      setEditor((cur) => {
        if (cur && !cur.edit && cur.block === key) return quote ? Object.assign({}, cur, { quote }) : cur;
        return { block: key, quote: quote || '', text: '', target: targetOf(key), saving: false, error: '' };
      });
    },
    // Text selected inside one block and released: a note on that block, quoting the text. A double-click's own word
    // selection is not a quote (the double-click opens the editor without one).
    blockUp: (key, el, e) => {
      if (e && e.detail > 1) return;
      if (editor && editor.saving) return;
      const quote = selectedIn(el, typeof window !== 'undefined' && window.getSelection ? window.getSelection() : null);
      if (quote) fns.openEditor(key, quote);
    },
    blockDouble: (key, e) => {
      const t = e && e.target;
      if (t && t.closest && t.closest('button, a, input, textarea, select, summary, label')) return;
      clearPageSelection();
      fns.openEditor(key, '');
    },
    editorText: (text) => setEditor((cur) => (cur ? Object.assign({}, cur, { text }) : cur)),
    editorCancel: () => setEditor(null),
    editorSave: async () => {
      const d = editor;
      if (!d || d.saving) return;
      const text = String(d.text || '').trim();
      if (!text) return;
      setEditor(Object.assign({}, d, { saving: true, error: '' }));
      try {
        const body = d.edit ? { note: text, lineage_id: d.edit.lineage, base: d.edit.base }
          : { step_key: d.block, note: text, step_title: d.target || null, quote: d.quote || null };
        const out = await labApi.saveLabel(turnId, body, org);
        const saved = out && out.label;
        if (saved) setRows((p) => p.filter((x) => (x.lineage_id || x.id) !== saved.lineage_id).concat([saved]));
        setEditor(null);
        clearPageSelection();
        if (!d.edit && onSaved) onSaved(nextStepFor('note_saved'));
      } catch (e) {
        if (isStale(e)) {
          setEditor(null);
          setErrors((p) => Object.assign({}, p, { [d.block]: STALE_TEXT }));
          reload();
          return;
        }
        setEditor((cur) => (cur ? Object.assign({}, cur, { saving: false, error: errText(e) }) : cur));
      }
    },
    editNote: (n) => {
      setConfirming(null);
      setEditor({ block: n.block, edit: { lineage: n.lineage, base: n.id }, quote: n.quote, text: n.note, target: '', saving: false, error: '' });
    },
    askDelete: (n) => setConfirming(n ? n.id : null),
    deleteNote: async (n) => {
      if (!n) return;
      setBusy(true);
      setErrors((p) => Object.assign({}, p, { [n.block]: '' }));
      try {
        await labApi.clearLabel(turnId, n.lineage, n.id, org);
        setRows((p) => p.filter((x) => (x.lineage_id || x.id) !== n.lineage));
        setConfirming(null);
      } catch (e) {
        setErrors((p) => Object.assign({}, p, { [n.block]: isStale(e) ? STALE_TEXT : errText(e) }));
        setConfirming(null);
        if (isStale(e)) reload();
      } finally {
        setBusy(false);
      }
    }
  };
  // Handlers that never change identity (so unchanged blocks are not re-drawn) and always call this render's version.
  const live = React.useRef(fns);
  live.current = fns;
  const on = React.useMemo(() => {
    const out = {};
    Object.keys(NOTES_ON).forEach((k) => { out[k] = (...args) => live.current[k](...args); });
    return out;
  }, []);
  const confirmIn = (items) => (confirming && (items || []).some((n) => n.id === confirming) ? confirming : null);
  const keyOf = (id, side) => { const k = keys.get(String(id)); return k ? (side === 'in' ? k.in : k.out) : null; };
  const annotate = {
    keyOf,
    count: (id) => notes.counts[String(id)] || 0,
    up: on.blockUp,
    double: on.blockDouble,
    render: (id, side) => {
      const key = keyOf(id, side);
      const k = keys.get(String(id)) || {};
      const items = placed.slots[String(id) + ':' + side] || [];
      return <NotesSlot noteKey={key} title={side === 'in' ? k.inTitle : k.outTitle} anchor={anchorOfNode(list, id, side)} items={items}
        editor={editor} busy={busy} error={errors[key] || ''} confirming={confirmIn(items)} on={on} />;
    },
    other: () => (placed.other.length ? (
      <section className="lab-tt-other" aria-label="Other notes">
        <h3 className="lab-qp-h">{'Other notes'}</h3>
        <p className="lab-small">{'Notes on spans this trace does not show on their own.'}</p>
        <NoteList items={placed.other} editor={editor} busy={busy} confirming={confirmIn(placed.other)} on={on} />
        {errors.other ? <div role="alert" className="lab-error">{errors.other}</div> : null}
      </section>
    ) : null),
    notes: placed.list
  };
  const comment = (
    <TurnComment onSave={on.saveTurnComment}>
      <NoteList items={placed.scenario} editor={editor} busy={busy} confirming={confirmIn(placed.scenario)} on={on} />
      {errors.scenario ? <div role="alert" className="lab-error">{errors.scenario}</div> : null}
    </TurnComment>
  );
  return { annotate, comment, notes, placed, on, editor, busy, errors, confirming };
}
