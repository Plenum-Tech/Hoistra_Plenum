// QuestionPage — one question of a run (22 Oct 2026 feedback, items 1–4; laid out like the Cartwheel Trace Lab's trace
// page, in Hoistra's look). Top to bottom: a header line (the question's code, its run, how many recorded answers support
// it, the answer in view, the asker's role, the models and tools that answer used, when); the question with its
// follow-ups; the expected answer (the newest version, its evidence and earlier versions, editable by anyone in the lab);
// the answers that support the question (curators can link another by its turn id); a comment box for the question as a
// whole; then the answer in view as its trace (9 Nov 2026: the Langfuse-style tree, timeline and graph of TraceTab.jsx,
// which replaced the old block view). Notes are written on the trace: pick a step, then press "Add note" under its input
// or output, select text in it to quote it, or double-click it (free text, no Right / Wrong). Each note says who wrote it,
// their Trace Lab role and when; a person's own notes can be edited or deleted. Notes keep Trace Lab's step keys
// (logic/traceNotes.js), so the Labels, Notes, Learning and Loop pages read them as before. Notes are step_labels rows read
// with the question, saved and removed through labApi with the question's company; the trace is read for the answer in view.
import React from 'react';
import { NextStep, nextStepFor } from './NextStep.jsx';
import { labApi } from './labApi.js';
import { Clamp } from './AllLabels.jsx';
import { NOTE_MAX, ROLE_TEXT, errText, isStale, selectedIn } from './logic/labelling.js';
import { SCENARIO, buildSteps } from './logic/steps.js';
import { pretty } from '../tracelab/logic/timeline.js';
import { Derivation } from './Loop.jsx';
import { TraceView } from './TraceTab.jsx';
import { keyTitles, nodeKeys, stepOfKey, traceNotesIndex } from './logic/traceNotes.js';
import './runs.css';

export const EXPECTED_ANSWER_MAX = 8000;
const TEXT_MAX = 1500;
const PRE_MAX = 4000;
const ROLES = Object.assign({ consultant: 'Consultant' }, ROLE_TEXT);
const SOURCES = { excel: 'Excel', manual: 'typed on the page', coding_agent: 'coding agent', sync: 'synced' };
const SCENARIO_TITLE = 'Question as a whole';
const noop = () => {};
const resolved = () => Promise.resolve();
const known = (x) => !!x && x !== 'unavailable' && x !== '—';

// ── small pure helpers, shared with Runs.jsx ──────────────────────────────────────────────────────────────────────────

export const plural = (n, one, many) => (Number(n) || 0) + ' ' + (Number(n) === 1 ? one : many);

// Creating runs, importing and linking answers: company admins, the platform team and the consultant.
export const isCurator = (me) => !!(me && (me.all_companies || me.manages));

function toDate(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

// "22 Oct 2026, 09:14" in the reader's time zone.
export function fmtWhen(iso) {
  const d = toDate(iso);
  return d ? d.toLocaleString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' }) : '';
}

export function fmtTime(iso) {
  const d = toDate(iso);
  return d ? d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' }) : '';
}

// "22 Oct 2026" for a calendar day ("2026-10-22" is that day wherever the reader is, never shifted by the time zone).
export function fmtDay(v) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(v || ''));
  if (m) return new Date(Date.UTC(+m[1], +m[2] - 1, +m[3])).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' });
  const d = toDate(v);
  return d ? d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' }) : '';
}

// "21 Oct 2026, 09:14 → 22 Oct 2026, 17:40", or "22 Oct 2026, 09:14 → 17:40" within one day.
export function fmtRange(a, b) {
  const x = toDate(a);
  const y = toDate(b);
  if (!x && !y) return '';
  if (!x || !y || x.getTime() === y.getTime()) return fmtWhen(x ? a : b);
  return fmtWhen(a) + ' → ' + (x.toDateString() === y.toDateString() ? fmtTime(b) : fmtWhen(b));
}

const uniq = (xs) => xs.map((x) => String(x || '')).filter((x, i, all) => known(x) && all.indexOf(x) === i);

// The models and tools of the answer in view: as the record lists them, else as its steps recorded them.
export function modelsOf(record, steps) {
  const listed = record && Array.isArray(record.models) ? uniq(record.models) : [];
  if (listed.length) return listed;
  const found = [];
  (steps || []).forEach((s) => (s.parts || []).forEach((p) => { if (p.kind === 'model') found.push(p.model); }));
  return uniq(found);
}

export function toolsOf(record, steps) {
  const listed = record && Array.isArray(record.tools) ? uniq(record.tools) : [];
  if (listed.length) return listed;
  const found = [];
  (steps || []).forEach((s) => (s.parts || []).forEach((p) => { if (p.kind === 'tool') found.push((p.raw && p.raw.name) || p.title); }));
  return uniq(found);
}

// One note as the page shows it. A note written before 22 Oct may also carry a Right / Wrong tag: it is not shown.
export function noteView(x, me) {
  return {
    id: x.id, lineage: x.lineage_id || x.id, key: x.step_key, block: '', note: x.note || '', quote: x.quote || '',
    who: x.reviewer_email || 'Someone', role: ROLES[x.reviewer_role] || x.reviewer_role || '', at: x.at || '',
    when: fmtWhen(x.at), edited: !!x.edited, mine: !!me && x.reviewer_id === me, expected: x.expected || '',
    onlyIf: x.only_if || ''
  };
}

const atOf = (n) => { const t = Date.parse(n.at); return Number.isNaN(t) ? 0 : t; };

function parseEvidence(s) {
  const t = String(s || '').trim();
  if (!t) return null;
  try { return JSON.parse(t); } catch (e) { return { reference: t }; }
}

// The evidence saved with an expected answer (3 Nov 2026): what was typed in Evidence, with the rule of the approved
// specification and the company records it was derived from on top. null when there is nothing.
export function evidenceWith(text, rule, data) {
  const base = parseEvidence(text);
  const out = base && typeof base === 'object' && !Array.isArray(base) ? Object.assign({}, base) : (base ? { reference: base } : {});
  if (String(rule || '').trim()) out.spec_rule = String(rule).trim();
  if (String(data || '').trim()) out.data = String(data).trim();
  return Object.keys(out).length ? out : null;
}

const evOf = (e) => (e && e.evidence && typeof e.evidence === 'object' && !Array.isArray(e.evidence) ? e.evidence : {});
const dataText = (v) => (v === null || v === undefined ? '' : (typeof v === 'string' ? v : pretty(v)));

function expectedLine(e) {
  return [e.source ? 'Source: ' + (SOURCES[e.source] || e.source) : '', e.author_email ? 'by ' + e.author_email : '',
    e.at ? 'at ' + fmtWhen(e.at) : ''].filter(Boolean).join(' · ');
}

function clearPageSelection() {
  try { if (typeof window !== 'undefined' && window.getSelection) window.getSelection().removeAllRanges(); } catch (e) { /* nothing selected */ }
}

// ── pieces of the trace ───────────────────────────────────────────────────────────────────────────────────────────────

function MoreToggle({ all, chars, onToggle }) {
  return (
    <button type="button" className="lab-link lab-more" aria-expanded={all ? 'true' : 'false'} onClick={onToggle}>
      {all ? 'Show less' : 'Show all (' + chars.toLocaleString('en-GB') + ' characters)'}
    </button>
  );
}

function LongPre({ text }) {
  const [all, setAll] = React.useState(false);
  const s = String(text || '');
  const long = s.length > PRE_MAX;
  return (
    <>
      <pre className="lab-pre">{long && !all ? s.slice(0, PRE_MAX) + '…' : s}</pre>
      {long ? <MoreToggle all={all} chars={s.length} onToggle={() => setAll(!all)} /> : null}
    </>
  );
}

function LongText({ text, className }) {
  const [all, setAll] = React.useState(false);
  const s = String(text || '');
  const long = s.length > TEXT_MAX;
  return (
    <>
      <div className={className || 'lab-tb-text'}>{long && !all ? s.slice(0, TEXT_MAX) + '…' : (s || '—')}</div>
      {long ? <MoreToggle all={all} chars={s.length} onToggle={() => setAll(!all)} /> : null}
    </>
  );
}

// ── notes ─────────────────────────────────────────────────────────────────────────────────────────────────────────────

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
  const h = Object.assign({}, DEFAULT_ON, on || {});
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

// ── the parts above the trace ─────────────────────────────────────────────────────────────────────────────────────────

// The expected answer, right under the question: the newest version (its id in bold), its evidence behind "Show evidence",
// where it came from, who wrote it and when, earlier versions, and an editor that saves a new version.
export function ExpectedAnswer({ expected, history, onSave, openAll, pending, specRules, canDecide, onDecide }) {
  const e = expected || null;
  const past = Array.isArray(history) ? history : [];
  const [form, setForm] = React.useState(null);
  const [showEvidence, setShowEvidence] = React.useState(!!openAll);
  const [showPast, setShowPast] = React.useState(!!openAll);
  const hasEvidence = !!e && e.evidence !== null && e.evidence !== undefined && e.evidence !== '';
  const rules = Array.isArray(specRules) ? specRules : [];
  const begin = () => {
    const ev = Object.assign({}, evOf(e));
    const rule = ev.spec_rule || '';
    const data = dataText(ev.data);
    delete ev.spec_rule;
    delete ev.data;
    const rest = Object.keys(ev).length ? pretty(ev) : (hasEvidence && typeof e.evidence !== 'object' ? pretty(e.evidence) : '');
    setForm({ text: e ? e.expected || '' : '', id: (e && e.expected_id) || '', rule, data, evidence: rest, saving: false, error: '' });
  };
  const field = (k) => (ev) => { const v = ev.target.value; setForm((c) => (c ? Object.assign({}, c, { [k]: v }) : c)); };
  const save = async () => {
    if (!form || form.saving || !form.text.trim()) return;
    const body = { expected: form.text.trim(), expected_id: form.id.trim() || null, evidence: evidenceWith(form.evidence, form.rule, form.data) };
    setForm(Object.assign({}, form, { saving: true, error: '' }));
    try {
      await (onSave || resolved)(body);
      setForm(null);
    } catch (err) {
      setForm((c) => (c ? Object.assign({}, c, { saving: false, error: errText(err) }) : c));
    }
  };
  const onKey = (ev) => {
    if (ev.key === 'Escape') { ev.preventDefault(); if (form && !form.saving) setForm(null); return; }
    if (ev.key === 'Enter' && (ev.ctrlKey || ev.metaKey)) { ev.preventDefault(); save(); }
  };
  return (
    <section className="lab-qp-expected" aria-labelledby="lab-qp-expected-h">
      <div className="lab-qp-sec-head">
        <h3 id="lab-qp-expected-h" className="lab-qp-h">{'Expected answer'}</h3>
        {!form ? <button type="button" className="lab-link" onClick={begin}>{e ? 'Edit expected answer' : 'Add expected answer'}</button> : null}
      </div>
      {form ? (
        <div className="lab-qp-form" onKeyDown={onKey}>
          <label className="lab-field"><span>{'Expected answer'}</span>
            <textarea className="lab-note lab-qp-expected-input" rows={5} maxLength={EXPECTED_ANSWER_MAX} value={form.text}
              placeholder="What the chat should answer" onChange={field('text')} disabled={form.saving} autoFocus />
          </label>
          <label className="lab-field"><span>{'Expected answer id (optional)'}</span>
            <input className="lab-input" type="text" maxLength={200} value={form.id} placeholder="e.g. EXP-12" onChange={field('id')}
              disabled={form.saving} />
          </label>
          <label className="lab-field"><span>{'Spec rule'}</span>
            <select className="lab-input" value={form.rule} onChange={field('rule')} disabled={form.saving}>
              <option value="">{rules.length ? 'Choose the rule it is derived from' : 'No approved specification yet'}</option>
              {rules.map((r) => <option key={r.code} value={r.code}>{r.code + (r.title ? ' · ' + r.title : '')}</option>)}
            </select>
          </label>
          <label className="lab-field"><span>{'Company records it was read from'}</span>
            <textarea className="lab-note lab-qp-evidence" rows={2} value={form.data} onChange={field('data')} disabled={form.saving}
              placeholder="The query or the records (ids, codes) the expected answer was read from — never the model's answer" />
          </label>
          <label className="lab-field"><span>{'Evidence (optional)'}</span>
            <textarea className="lab-note lab-qp-evidence" rows={3} value={form.evidence} onChange={field('evidence')} disabled={form.saving}
              placeholder="JSON, or plain text kept as a reference" />
          </label>
          {form.error ? <div role="alert" className="lab-error">{form.error}</div> : null}
          <div className="lab-row">
            <button type="button" className="lab-btn lab-btn-sm" onClick={save} disabled={form.saving || !form.text.trim()}>
              {form.saving ? 'Saving…' : 'Save expected answer'}
            </button>
            <button type="button" className="lab-btn lab-btn-quiet lab-btn-sm" onClick={() => setForm(null)} disabled={form.saving}>{'Cancel'}</button>
            <span className="lab-small">{'Saving keeps the earlier version in the history.'}</span>
          </div>
        </div>
      ) : e ? (
        <>
          <div className="lab-qp-expected-body">
            {e.expected_id ? <strong className="lab-qp-eid">{e.expected_id}</strong> : null}
            <LongText text={e.expected} className="lab-qp-expected-text" />
          </div>
          <Derivation d={e.derivation} />
          {hasEvidence ? (
            <div>
              <button type="button" className="lab-link" aria-expanded={showEvidence ? 'true' : 'false'} onClick={() => setShowEvidence(!showEvidence)}>
                {showEvidence ? 'Hide evidence' : 'Show evidence'}
              </button>
              {showEvidence ? <LongPre text={pretty(e.evidence)} /> : null}
            </div>
          ) : null}
          <p className="lab-small">{expectedLine(e)}</p>
        </>
      ) : <p className="lab-small">{'No expected answer yet.'}</p>}
      {pending ? (
        <div className="lab-qp-pending" aria-label="Expected answer proposed by a coding agent">
          <p className="lab-small"><strong>{'Proposed by a coding agent'}</strong>{' · it counts only once a person approves it'}</p>
          <LongText text={pending.expected} className="lab-qp-expected-text" />
          <Derivation d={pending.derivation} />
          <p className="lab-small">{expectedLine(pending)}</p>
          {canDecide && onDecide ? (
            <div className="lab-row">
              <button type="button" className="lab-btn lab-btn-sm" disabled={!(pending.derivation && pending.derivation.derived)}
                title={pending.derivation && pending.derivation.derived ? undefined : 'Approve needs a rule of the approved specification and the company records it was read from.'}
                onClick={() => onDecide(pending.id, 'approved')}>{'Approve'}</button>
              <button type="button" className="lab-btn lab-btn-quiet lab-btn-sm" onClick={() => onDecide(pending.id, 'rejected')}>{'Reject'}</button>
            </div>
          ) : null}
        </div>
      ) : null}
      {past.length ? (
        <div className="lab-qp-past">
          <button type="button" className="lab-link" aria-expanded={showPast ? 'true' : 'false'} onClick={() => setShowPast(!showPast)}>
            {(showPast ? 'Hide ' : '') + plural(past.length, 'earlier version', 'earlier versions')}
          </button>
          {showPast ? (
            <ol className="lab-qp-history">
              {past.map((x, i) => (
                <li key={x.id || i}>
                  {x.expected_id ? <strong className="lab-qp-eid">{x.expected_id}</strong> : null}
                  <span className="lab-qp-history-text">{x.expected}</span>
                  <div className="lab-small">{expectedLine(x)}</div>
                </li>
              ))}
            </ol>
          ) : null}
        </div>
      ) : null}
    </section>
  );
}

// The recorded answers a curator can link, as the picker lists them: the same question asked word for word is marked.
// What the person's browser saw of this question's answers (3 Nov 2026): when the answer finished, how long it took, a
// stop, or the error shown — kept beside the backend's recorded trace.
const BROWSER_KIND = { answer_shown: 'Answer shown', error: 'Error shown', stopped: 'Stopped' };
const secs = (n) => (n === null || n === undefined ? null : (n / 1000).toFixed(1) + ' s');
export function BrowserEvents({ events }) {
  const list = Array.isArray(events) ? events : [];
  if (!list.length) return null;
  return (
    <section className="lab-qp-browser" aria-labelledby="lab-qp-browser-h">
      <h3 id="lab-qp-browser-h" className="lab-qp-h">{'In the browser'}</h3>
      <ul className="lab-qp-browser-list">
        {list.map((x) => (
          <li key={x.id}>
            <strong>{BROWSER_KIND[x.kind] || x.kind}</strong>
            <span className="lab-small">{[x.ms_first_text !== null && x.ms_first_text !== undefined ? 'first text after ' + secs(x.ms_first_text) : '',
              x.ms_total !== null && x.ms_total !== undefined ? 'took ' + secs(x.ms_total) : '', x.view ? 'on ' + x.view : '',
              x.instance ? 'in ' + x.instance : '', x.at ? fmtWhen(x.at) : ''].filter(Boolean).join(' · ')}</span>
            {x.error ? <div className="lab-small lab-qp-browser-error">{x.error}</div> : null}
          </li>
        ))}
      </ul>
    </section>
  );
}

export function AnswerPicks({ picks, busy, onPick }) {
  if (!picks) return <p className="lab-small">{'Reading the recorded answers…'}</p>;
  if (!picks.length) return <p className="lab-small">{'No recorded answers match. Ask the question in the chat first, then search again.'}</p>;
  return (
    <ul className="lab-qp-picks" aria-label="Recorded answers you can link">
      {picks.map((c) => (
        <li key={c.turn_id} className="lab-qp-pick">
          <div className="lab-qp-pick-main">
            <div className="lab-qp-pick-q">
              {c.same ? <span className="lab-chip lab-chip-ok">{'same question'}</span> : null}
              <span>{c.question || '(no question recorded)'}</span>
            </div>
            {c.answer ? <div className="lab-qp-pick-a">{c.answer}</div> : null}
            <div className="lab-small">{[c.email, fmtWhen(c.asked_at), c.ok === false ? 'failed' : null, c.turn_id].filter(Boolean).join(' · ')}</div>
          </div>
          <button type="button" className="lab-btn lab-btn-sm" disabled={!!busy} onClick={() => onPick(c.turn_id)}>
            {busy === c.turn_id ? 'Linking…' : 'Link'}
          </button>
        </li>
      ))}
    </ul>
  );
}

function LinkAnswer({ onLink, onSearch }) {
  const [f, setF] = React.useState(null);
  const seq = React.useRef(0);
  const search = React.useCallback(async (term) => {
    const mine = ++seq.current;
    setF((c) => (c ? Object.assign({}, c, { picks: null, error: '' }) : c));
    try {
      const out = await (onSearch || (async () => ({ candidates: [] })))(term);
      if (mine === seq.current) setF((c) => (c ? Object.assign({}, c, { picks: (out && out.candidates) || [] }) : c));
    } catch (err) {
      if (mine === seq.current) setF((c) => (c ? Object.assign({}, c, { picks: [], error: errText(err) }) : c));
    }
  }, [onSearch]);
  const open = () => { setF({ term: '', picks: null, busy: '', error: '' }); search(''); };
  if (!f) return <button type="button" className="lab-link" onClick={open}>{'Link an answer'}</button>;
  const pick = async (tid) => {
    if (!tid || f.busy) return;
    setF((c) => Object.assign({}, c, { busy: tid, error: '' }));
    try {
      await (onLink || resolved)(tid);
      setF(null);
    } catch (err) {
      setF((c) => (c ? Object.assign({}, c, { busy: '', error: errText(err) }) : c));
    }
  };
  const submit = (e) => { e.preventDefault(); search(f.term.trim()); };
  return (
    <div className="lab-qp-linkbox" onKeyDown={(e) => { if (e.key === 'Escape' && !f.busy) setF(null); }}>
      <form className="lab-qp-link" onSubmit={submit}>
        <label className="lab-visually-hidden" htmlFor="lab-qp-link-turn">{'Search the recorded answers'}</label>
        <input id="lab-qp-link-turn" className="lab-input" type="search" maxLength={200} value={f.term}
          placeholder="Search words, or paste a turn id" disabled={!!f.busy} autoFocus
          onChange={(e) => { const term = e.target.value; setF((c) => (c ? Object.assign({}, c, { term }) : c)); }} />
        <button type="submit" className="lab-btn lab-btn-quiet lab-btn-sm" disabled={!!f.busy}>{'Search'}</button>
        <button type="button" className="lab-btn lab-btn-quiet lab-btn-sm" onClick={() => setF(null)} disabled={!!f.busy}>{'Cancel'}</button>
      </form>
      {f.error ? <div role="alert" className="lab-error">{f.error}</div> : null}
      <AnswerPicks picks={f.picks} busy={f.busy} onPick={pick} />
    </div>
  );
}

// A comment on the question as a whole (a note on "<turn>:scenario"), with the comments already written under it.
function ScenarioBox({ onSave, children }) {
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
    <section className="lab-qp-scenario" aria-label="Comments on the question as a whole">
      <textarea className="lab-note lab-qp-scenario-input" rows={2} maxLength={NOTE_MAX} value={text} disabled={st.busy}
        aria-label="Comment on the question as a whole" placeholder="Add a comment about this question as a whole…"
        onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); save(); } }} />
      <div className="lab-row">
        <button type="button" className="lab-btn lab-btn-sm" onClick={save} disabled={st.busy || !text.trim()}>{st.busy ? 'Saving…' : 'Save comment'}</button>
      </div>
      {st.error ? <div role="alert" className="lab-error">{st.error}</div> : null}
      {children}
    </section>
  );
}

function NoAnswer({ active, curator }) {
  if (!active) {
    return (
      <div className="lab-empty lab-qp-none">
        <h2>{'No recorded answer is linked to this question yet'}</h2>
        <p className="lab-small">{curator
          ? 'Once the question has been asked in the chat, link its answer above with “Link an answer” and its steps show here.'
          : 'Once the question has been asked in the chat, a company admin or the platform team links its answer and its steps show here.'}</p>
      </div>
    );
  }
  if (active.missing) {
    return (
      <div className="lab-empty lab-qp-none">
        <h2>{'This answer can no longer be read'}</h2>
        <p className="lab-small">{'The recorded answer ' + active.turn_id + ' is not in the company’s records any more. Pick another answer above.'}</p>
      </div>
    );
  }
  return <div className="lab-empty lab-qp-none"><h2>{'Nothing was recorded for this answer'}</h2></div>;
}

const DEFAULT_ON = Object.freeze({
  turn: noop, link: resolved, candidates: async () => ({ candidates: [] }), saveExpected: resolved, decideExpected: resolved, saveScenario: resolved, select: noop, openEditor: noop, blockUp: noop,
  blockDouble: noop, editorText: noop, editorSave: noop, editorCancel: noop, editNote: noop, askDelete: noop, deleteNote: noop
});

// The notes on one side (Input or Output) of a trace step: the editor when a note is being written there, everyone's notes,
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

// The page itself, from one labApi.q answer and its trace (9 Nov 2026: the answer is shown only as its trace, and notes
// are written on the trace's steps). `tree` is the /tree response ({turn_id, missing, tree}, or {loading} / {error}).
// `on` holds the handlers (turn, link, saveExpected, saveScenario, select, openEditor, blockUp, blockDouble, editorText,
// editorSave, editorCancel, editNote, askDelete, deleteNote). `editor` is the open note editor ({ block, quote, text, target,
// edit: { lineage, base }, saving, error }). `traceView` seeds the trace's view (selectedId, pane, mode); `openAll` opens the
// expected answer's evidence on first paint (the tests read the content that way).
export function QuestionView(props) {
  const { data, curator, editor, busy, errors, confirming, loading, openAll, tree, traceView } = props;
  const on = React.useMemo(() => Object.assign({}, DEFAULT_ON, props.on || {}), [props.on]);
  const d = data || {};
  const q = d.question || {};
  const run = d.run || {};
  const active = d.active || null;
  const missing = !!(active && active.missing);
  const trace = active && !missing ? active.trace || null : null;
  const turnId = active ? active.turn_id : null;
  const steps = React.useMemo(() => buildSteps(trace), [trace]);
  const traces = Array.isArray(d.traces) ? d.traces : [];
  const models = modelsOf(trace, steps);
  const tools = toolsOf(trace, steps);
  const time = fmtWhen((trace && trace.timestamp) || q.asked_at);
  const errs = errors || {};
  const t = tree || { loading: true };
  const tr = t.tree || null;
  const nodes = React.useMemo(() => (tr && Array.isArray(tr.nodes) ? tr.nodes : []), [tr]);
  const keys = React.useMemo(() => nodeKeys(trace, nodes), [trace, nodes]);
  const notes = React.useMemo(() => traceNotesIndex(d.labels, trace, nodes, d.me, noteView), [d.labels, trace, nodes, d.me]);
  const confirmIn = (list) => (confirming && (list || []).some((n) => n.id === confirming) ? confirming : null);
  const hasAnswer = !!(turnId && !missing);
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
      return <NotesSlot noteKey={key} title={side === 'in' ? k.inTitle : k.outTitle} items={items} editor={editor} busy={!!busy}
        error={errs[key] || ''} confirming={confirmIn(items)} on={on} />;
    },
    other: () => (notes.other.length ? (
      <section className="lab-tt-other" aria-label="Other notes">
        <h3 className="lab-qp-h">{'Other notes'}</h3>
        <p className="lab-small">{'Notes on steps this trace does not show on their own.'}</p>
        <NoteList items={notes.other} editor={editor} busy={busy} confirming={confirmIn(notes.other)} on={on} />
        {errs.other ? <div role="alert" className="lab-error">{errs.other}</div> : null}
      </section>
    ) : null)
  };
  let answer;
  if (!hasAnswer) answer = <NoAnswer active={active} curator={curator} />;
  else if (t.error) answer = <div role="alert" className="lab-error">{'Could not read this answer’s trace: ' + t.error}</div>;
  else if (t.missing) answer = <NoAnswer active={{ turn_id: turnId, missing: true }} curator={curator} />;
  else if (!tr) answer = <p className="lab-small" role="status">{'Reading the trace…'}</p>;
  else {
    const v = traceView || {};
    answer = <TraceView key={turnId} tree={tr} annotate={annotate} selectedId={v.selectedId} view={v.view} pane={v.pane} mode={v.mode} />;
  }
  return (
    <article className="lab-qp" aria-label={'Question ' + (q.code || '')}>
      <header className="lab-qp-head">
        <strong className="lab-qp-code">{q.code || '—'}</strong>
        {run.code ? <span className="lab-chip" title={run.title || undefined}>{run.code}</span> : null}
        <span>{plural(traces.length, 'supporting answer', 'supporting answers')}</span>
        {turnId ? <span>{'active answer: '}<code className="lab-mono">{turnId}</code></span> : null}
        {q.role ? <span className="lab-chip">{q.role}</span> : null}
        {models.map((m) => <span key={'m:' + m} className="lab-chip lab-chip-model" title="Model">{m}</span>)}
        {tools.map((x) => <span key={'t:' + x} className="lab-chip lab-chip-tool" title="Tool">{x}</span>)}
        {time ? <span className="lab-qp-time">{time}</span> : null}
        {loading ? <span className="lab-small" role="status">{'Reading…'}</span> : null}
      </header>

      <section className="lab-qp-card" aria-label="The question">
        <div className="lab-qp-meta">{[q.code, q.role, q.intent].filter(Boolean).join(' · ')}</div>
        <p className="lab-qp-said"><strong>{'Opening: '}</strong>{q.question || '—'}</p>
        {(Array.isArray(q.followups) ? q.followups : []).map((f, i) => (
          <p key={i} className="lab-qp-said"><strong>{'Follow-up ' + (i + 1) + ': '}</strong>{String(f)}</p>
        ))}
      </section>

      <ExpectedAnswer expected={d.expected} history={d.expected_history} onSave={on.saveExpected} openAll={openAll}
        pending={d.expected_pending || null} specRules={d.spec_rules || []} canDecide={!!curator} onDecide={on.decideExpected} />

      <section className="lab-qp-answers" aria-label="Supporting answers">
        <span className="lab-qp-label">{'Supporting answers:'}</span>
        {traces.length ? (
          <div className="lab-turns" role="group" aria-label="Recorded answers to this question">
            {traces.map((x, i) => (
              <button key={x.turn_id} type="button" className={'lab-turn' + (x.turn_id === turnId ? ' lab-on' : '')}
                aria-pressed={x.turn_id === turnId ? 'true' : 'false'} title={[x.turn_id, SOURCES[x.source] || x.source, x.linked_by].filter(Boolean).join(' · ')}
                onClick={() => { if (x.turn_id !== turnId) on.turn(x.turn_id); }}>{'Answer ' + (i + 1) + (i === 0 && traces.length > 1 ? ' (newest)' : '')}</button>
            ))}
          </div>
        ) : <span className="lab-small">{'None yet'}</span>}
        {curator ? <LinkAnswer onLink={on.link} onSearch={on.candidates} /> : null}
      </section>

      <BrowserEvents events={d.browser} />

      {active && active.stored ? (
        <p className="lab-small" role="note">
          {'This answer was asked on ' + (active.stored.origin || 'another machine') + ' and copied into Trace Lab, so the chat here has no record of it: the steps below are its stored trace. '}
          <a href={'?view=sessions&session=' + encodeURIComponent(active.stored.session_id)}>{'Open its session'}</a>
          {' to see the whole conversation' + (active.stored.run_tag ? ' (run tag ' + active.stored.run_tag + ')' : '') + '.'}
        </p>
      ) : null}

      {hasAnswer ? (
        <ScenarioBox onSave={on.saveScenario}>
          <NoteList items={notes.scenario} editor={editor} busy={busy} confirming={confirmIn(notes.scenario)} on={on} />
          {errs.scenario ? <div role="alert" className="lab-error">{errs.scenario}</div> : null}
        </ScenarioBox>
      ) : null}

      {hasAnswer && tr ? (
        <p className="lab-small lab-tt-hint">{'Pick a step, then press Add note under its input or output, select text in it to quote it, or double-click it.'}</p>
      ) : null}
      {answer}
    </article>
  );
}

// The page with its data: one request for the question (its expected answer, answers, the active answer's steps and
// everyone's notes), then notes saved and removed one at a time. `onChanged(what)` tells the run's list to read its counts
// again ('expected', 'note' or 'link'); `onLinked(turnId)` follows a newly linked answer.
// `focus` ({ turn, step }, from the Notes page) opens the question on the answer and step a note was written on.
export default function QuestionPage({ code, me, onChanged, onLinked, onOpenLoop, focus }) {
  // After a note is saved: a pop-up says the next step is the agent's proposal on the Loop page (6 Nov 2026).
  const [next, setNext] = React.useState(null);
  const [st, setSt] = React.useState({ data: null, loading: true, error: '' });
  const [rows, setRows] = React.useState([]);
  const [selected, setSelected] = React.useState(null);
  const [editor, setEditor] = React.useState(null);
  const [busy, setBusy] = React.useState(false);
  const [errors, setErrors] = React.useState({});
  const [confirming, setConfirming] = React.useState(null);
  const ask = React.useRef(0);
  // Read once: the page opens on the note's answer and step, and after that the reviewer moves freely.
  const firstTurn = React.useRef((focus && focus.turn) || null);
  const focusStep = React.useRef((focus && focus.step) || null);

  const load = React.useCallback(async (turn) => {
    const mine = (ask.current += 1);
    setSt((p) => ({ data: p.data, loading: true, error: '' }));
    try {
      const out = await labApi.q(code, turn || undefined);
      if (mine !== ask.current) return;
      setSt({ data: out || null, loading: false, error: '' });
      setRows((out && out.labels) || []);
    } catch (e) {
      if (mine === ask.current) setSt((p) => ({ data: p.data, loading: false, error: errText(e) }));
    }
  }, [code]);

  React.useEffect(() => {
    setSt({ data: null, loading: !!code, error: '' });
    setRows([]); setEditor(null); setSelected(null); setErrors({}); setConfirming(null);
    if (code) load(firstTurn.current);
  }, [load]);

  const data = st.data;
  const active = data && data.active && !data.active.missing ? data.active : null;
  const turnId = active ? active.turn_id : null;
  const org = active ? active.organization_id || undefined : undefined;
  const meId = (data && data.me) || (me && me.user_id) || null;
  // The answer's trace (9 Nov 2026), read for the answer in view: the page shows the answer only as its trace.
  const [tree, setTree] = React.useState(null);
  const treeAsk = React.useRef(0);
  React.useEffect(() => {
    if (!code || !turnId) { setTree(null); return; }
    const mine = (treeAsk.current += 1);
    setTree({ loading: true });
    labApi.qTree(code, turnId)
      .then((out) => { if (mine === treeAsk.current) setTree(out || { missing: true }); })
      .catch((e) => { if (mine === treeAsk.current) setTree({ error: errText(e) }); });
  }, [code, turnId]);
  const titles = React.useMemo(() => keyTitles(nodeKeys(active && active.trace, tree && tree.tree ? tree.tree.nodes : [])),
    [active, tree]);
  const viewData = React.useMemo(() => (data ? Object.assign({}, data, { labels: rows }) : null), [data, rows]);
  const focusAt = React.useMemo(() => (focusStep.current && tree && tree.tree
    ? stepOfKey(active && active.trace, tree.tree.nodes, focusStep.current) : null), [active, tree]);

  const changed = (what) => { if (onChanged) onChanged(what); };
  const targetOf = (key) => {
    if (turnId && key === turnId + SCENARIO) return SCENARIO_TITLE;
    return titles[key] || '';
  };
  const reloadNotes = async () => {
    if (!turnId) return;
    try {
      const out = await labApi.turnLabels(turnId, org);
      setRows((out && out.labels) || []);
    } catch (e) { /* the message on screen already says what failed */ }
  };
  const staleText = 'This note changed in another window. The notes were read again.';

  const fns = {
    turn: (tid) => { setEditor(null); setSelected(null); setErrors({}); setConfirming(null); load(tid); },
    candidates: (term) => labApi.answerCandidates(code, term),
    link: async (tid) => {
      const out = await labApi.linkTrace(code, tid);
      setSt({ data: out || null, loading: false, error: '' });
      setRows((out && out.labels) || []);
      setEditor(null); setSelected(null); setErrors({}); setConfirming(null);
      if (onLinked) onLinked(tid);
      changed('link');
    },
    saveExpected: async (body) => {
      await labApi.setExpected(code, body);
      await load(turnId);
      changed('expected');
    },
    decideExpected: async (id, decision) => {
      try {
        await labApi.decideProposal('expected_answer', id, { decision });
        await load(turnId);
        changed('expected');
      } catch (e) {
        setSt((p) => Object.assign({}, p, { error: 'Could not record the decision: ' + errText(e) }));
      }
    },
    saveScenario: async (text) => {
      if (!turnId) return;
      const out = await labApi.saveLabel(turnId, { step_key: turnId + SCENARIO, note: text, step_title: SCENARIO_TITLE }, org);
      const saved = out && out.label;
      if (saved) setRows((p) => p.concat([saved]));
      changed('note');
      setNext(nextStepFor('note_saved'));
    },
    select: (key) => setSelected(key),
    openEditor: (key, quote) => {
      if (!turnId) return;
      setSelected(key);
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
      if (!d || d.saving || !turnId) return;
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
        changed('note');
        if (!d.edit) setNext(nextStepFor('note_saved'));
      } catch (e) {
        if (isStale(e)) {
          setEditor(null);
          setErrors((p) => Object.assign({}, p, { [d.block]: staleText }));
          reloadNotes();
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
      if (!turnId || !n) return;
      setBusy(true);
      setErrors((p) => Object.assign({}, p, { [n.block]: '' }));
      try {
        await labApi.clearLabel(turnId, n.lineage, n.id, org);
        setRows((p) => p.filter((x) => (x.lineage_id || x.id) !== n.lineage));
        setConfirming(null);
        changed('note');
      } catch (e) {
        setErrors((p) => Object.assign({}, p, { [n.block]: isStale(e) ? staleText : errText(e) }));
        setConfirming(null);
        if (isStale(e)) reloadNotes();
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
    Object.keys(DEFAULT_ON).forEach((k) => { out[k] = (...args) => live.current[k](...args); });
    return out;
  }, []);

  if (!data) {
    return (
      <div className="lab-qp">
        {st.loading ? <p className="lab-small">{'Reading the question…'}</p> : null}
        {st.error ? <div role="alert" className="lab-error">{'Could not open ' + (code || 'this question') + ': ' + st.error}</div> : null}
      </div>
    );
  }
  return (
    <>
      {st.error ? <div role="alert" className="lab-error">{st.error}</div> : null}
      <QuestionView data={viewData} tree={tree} curator={isCurator(me)} editor={editor}
        busy={busy} errors={errors} confirming={confirming} loading={st.loading} on={on}
        traceView={focusAt ? { selectedId: focusAt.id } : undefined} />
      <NextStep step={onOpenLoop ? next : null} onClose={() => setNext(null)}
        onAct={(a) => { if (a === 'open_loop' && onOpenLoop) onOpenLoop(); }} />
    </>
  );
}
