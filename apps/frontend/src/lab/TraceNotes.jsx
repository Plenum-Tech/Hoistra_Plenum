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
import { keyTitles, nodeKeys, traceNotesIndex } from './logic/traceNotes.js';

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
    onlyIf: x.only_if || ''
  };
}

function clearPageSelection() {
  try { if (typeof window !== 'undefined' && window.getSelection) window.getSelection().removeAllRanges(); } catch (e) { /* nothing selected */ }
}

export const NOTES_ON = Object.freeze({
  saveTurnComment: resolved, openEditor: noop, blockUp: noop, blockDouble: noop, editorText: noop, editorSave: noop,
  editorCancel: noop, editNote: noop, askDelete: noop, deleteNote: noop
});

// The note editor, in the notes column beside its block: free text, Save / Cancel, Escape cancels, Ctrl/Cmd+Enter saves.
export function NoteEditor({ value, quote, target, editing, saving, error, onChange, onSave, onCancel }) {
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
      <div className="lab-ne-head">{label + (target ? ' · ' + target : '')}</div>
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

function NoteItem({ n, busy, confirming, on }) {
  return (
    <li className={'lab-nt' + (n.mine ? ' lab-nt-mine' : '')}>
      {n.quote ? <blockquote className="lab-quote lab-quote-small">{n.quote}</blockquote> : null}
      {n.note ? <div className="lab-nt-text"><Clamp text={n.note}>{n.note}</Clamp></div>
        : (n.quote || n.expected ? null : <div className="lab-small">{'No note written.'}</div>)}
      {n.expected ? <div className="lab-small lab-rule"><strong>{'Should: '}</strong>{n.expected}</div> : null}
      {n.onlyIf ? <div className="lab-small lab-rule"><strong>{'Only if: '}</strong>{n.onlyIf}</div> : null}
      <div className="lab-nt-by">{n.mine ? 'You' : n.who}{n.role ? <span className="lab-nt-role">{' · ' + n.role}</span> : null}</div>
      <div className="lab-small">{'Annotated at ' + (n.when || '—') + (n.edited ? ' · edited' : '')}</div>
      {n.mine ? (confirming ? (
        <div className="lab-row lab-nt-actions" role="group" aria-label="Delete this note?">
          <span className="lab-small">{'Delete this note?'}</span>
          <button type="button" className="lab-link lab-danger" onClick={() => on.deleteNote(n)} disabled={busy}>{'Delete'}</button>
          <button type="button" className="lab-link" onClick={() => on.askDelete(null)} disabled={busy}>{'Keep'}</button>
        </div>
      ) : (
        <div className="lab-row lab-nt-actions">
          <button type="button" className="lab-link" onClick={() => on.editNote(n)} disabled={busy}>{'Edit'}</button>
          <button type="button" className="lab-link lab-danger" onClick={() => on.askDelete(n)} disabled={busy}>{'Delete'}</button>
        </div>
      )) : null}
    </li>
  );
}

// Everyone's notes on one place, the reader's own first; the one being edited is its editor.
export function NoteList({ items, editor, busy, confirming, on }) {
  const h = Object.assign({}, NOTES_ON, on || {});
  if (!items || !items.length) return null;
  return (
    <ul className="lab-nts" aria-label="Notes">
      {items.map((n) => (editor && editor.edit && editor.edit.lineage === n.lineage ? (
        <li key={n.id} className="lab-nt lab-nt-editing">
          <NoteEditor value={editor.text} quote={editor.quote} editing saving={editor.saving} error={editor.error}
            onChange={h.editorText} onSave={h.editorSave} onCancel={h.editorCancel} />
        </li>
      ) : <NoteItem key={n.id} n={n} busy={!!busy} confirming={confirming === n.id} on={h} />))}
    </ul>
  );
}

// The notes on one side (Input or Output) of a span: the editor when a note is being written there, everyone's notes,
// an error, and Add note.
function NotesSlot({ noteKey, title, items, editor, busy, error, confirming, on }) {
  const writing = editor && !editor.edit && editor.block === noteKey ? editor : null;
  return (
    <div className="lab-tt-notes" aria-label={'Notes on ' + title}>
      {writing ? (
        <NoteEditor value={writing.text} quote={writing.quote} target={writing.target} saving={writing.saving} error={writing.error}
          onChange={on.editorText} onSave={on.editorSave} onCancel={on.editorCancel} />
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
      const items = notes.slots[String(id) + ':' + side] || [];
      return <NotesSlot noteKey={key} title={side === 'in' ? k.inTitle : k.outTitle} items={items} editor={editor} busy={busy}
        error={errors[key] || ''} confirming={confirmIn(items)} on={on} />;
    },
    other: () => (notes.other.length ? (
      <section className="lab-tt-other" aria-label="Other notes">
        <h3 className="lab-qp-h">{'Other notes'}</h3>
        <p className="lab-small">{'Notes on spans this trace does not show on their own.'}</p>
        <NoteList items={notes.other} editor={editor} busy={busy} confirming={confirmIn(notes.other)} on={on} />
        {errors.other ? <div role="alert" className="lab-error">{errors.other}</div> : null}
      </section>
    ) : null)
  };
  const comment = (
    <TurnComment onSave={on.saveTurnComment}>
      <NoteList items={notes.scenario} editor={editor} busy={busy} confirming={confirmIn(notes.scenario)} on={on} />
      {errors.scenario ? <div role="alert" className="lab-error">{errors.scenario}</div> : null}
    </TurnComment>
  );
  return { annotate, comment, notes, on, editor, busy, errors, confirming };
}
