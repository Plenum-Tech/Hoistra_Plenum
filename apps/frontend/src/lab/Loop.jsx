// Loop — the correction loop (3 Nov 2026): traces -> notes -> failure modes -> SPEC.md -> correction -> regression ->
// rerun. People write the notes and the labels; a coding agent may only PROPOSE a failure mode, a label or an expected
// answer, and each proposal waits here for a person to approve or reject it. The specification in force is the newest
// approved version (download it as SPEC.md); the coding agent commits it and the change made for a failure mode is
// recorded against it. A regression run holds the questions people marked positive for a failure mode, with expected
// answers derived from the specification and the company's records (never from the model's answer); asking it again
// adds the new answer beside the old, and Compare checks both against the expected values.
import React from 'react';
import { labApi } from './labApi.js';
import { errText } from './logic/labelling.js';
import { when } from './LabelsView.jsx';
import { NextStep, nextStepFor } from './NextStep.jsx';

export const LOOP_STEPS = [['traces', 'Traces'], ['notes', 'Notes'], ['taxonomy', 'Failure modes'], ['spec', 'SPEC.md'],
  ['correction', 'Correction'], ['regression', 'Regression'], ['rerun', 'Rerun']];
const SPACES = [['full', 'Full platform'], ['ops', 'Ops Brain'], ['comp', 'Compliance Engine'], ['assets', 'Assets Operating System']];
const CORRECTION_KINDS = [['prompt', 'Prompt'], ['skill', 'Skill'], ['code', 'Code'], ['data', 'Data'], ['spec', 'Specification']];
const KIND_TEXT = { new: 'New failure mode', change: 'Change to a failure mode', retire: 'Retire a failure mode' };
const VERDICT = { fixed: 'Fixed', passing: 'Passing', still_failing: 'Still failing', regressed: 'Regressed',
  no_answer: 'No answer (timed out or failed)', not_rerun: 'Not asked again yet', no_expected: 'No expected values' };
const STATUS = { all_found: 'All expected values found', mismatch: 'An expected value is missing', unclear: 'Unclear', none: 'Nothing to check' };

// How many proposals wait for a person.
export function waitingCount(p) {
  if (!p) return 0;
  return (p.modes || []).length + (p.spec || []).length + (p.expected || []).length + (p.labels || []).length;
}

// One failure mode's place in the loop: each step done or not, and the next one to take.
export function stepChips(mode) {
  const s = (mode && mode.steps) || {};
  const done = { traces: true, notes: !!s.annotated, taxonomy: true, spec: !!s.spec, correction: !!s.correction,
    regression: !!s.regression, rerun: !!s.rerun };
  return LOOP_STEPS.map(([key, label]) => ({ key, label, done: done[key], next: !!mode && mode.next === key }));
}

// Ask a regression run again, one question a request, until the service says there are no more. One a request: an
// answer can take minutes, and Azure's gateway cuts a response idle for about 300 s (6 Nov 2026).
export async function rerunAll(api, code, onProgress) {
  let start = 0;
  let asked = 0;
  let failed = 0;
  let total = null;
  let underTest = [];
  for (let guard = 0; guard < 200; guard += 1) {
    const out = await api.rerun(code, { start, count: 1 });
    total = out && out.total !== undefined ? out.total : total;
    if (out && Array.isArray(out.under_test)) underTest = out.under_test;
    asked += (out && out.asked) || 0;
    failed += ((out && out.results) || []).filter((r) => !r.ok).length;
    if (onProgress) onProgress({ asked, total, failed, underTest });
    if (!out || !out.asked || out.next_start === null || out.next_start === undefined) break;
    start = out.next_start;
  }
  return { asked, total, failed, underTest };
}

function Section({ id, title, hint, children, right }) {
  return (
    <section className="lab-loop-sec" aria-labelledby={id}>
      <div className="lab-qp-sec-head">
        <h2 id={id} className="lab-loop-h">{title}</h2>
        {right || null}
      </div>
      {hint ? <p className="lab-small">{hint}</p> : null}
      {children}
    </section>
  );
}

// Approve or reject one proposal, with an optional note. `blocked` (a reason) keeps Approve off.
function Decide({ busy, blocked, onDecide }) {
  const [note, setNote] = React.useState('');
  return (
    <div className="lab-row lab-loop-decide">
      <input className="lab-input lab-loop-note" type="text" maxLength={2000} value={note} placeholder="Note (optional)"
        aria-label="Note with the decision" onChange={(e) => setNote(e.target.value)} disabled={busy} />
      <button type="button" className="lab-btn lab-btn-sm" disabled={busy || !!blocked} title={blocked || undefined}
        onClick={() => onDecide('approved', note.trim() || null)}>{'Approve'}</button>
      <button type="button" className="lab-btn lab-btn-quiet lab-btn-sm" disabled={busy}
        onClick={() => onDecide('rejected', note.trim() || null)}>{'Reject'}</button>
    </div>
  );
}

export function Derivation({ d }) {
  if (!d) return null;
  if (d.derived) return <span className="lab-chip lab-loop-ok">{'Derived from ' + d.spec_rule}</span>;
  return (
    <div className="lab-loop-warn">
      <strong>{'Not derived from the specification'}</strong>
      {(d.problems || []).length ? <span>{': it ' + d.problems.join('; ') + '.'}</span> : null}
    </div>
  );
}

// Everything a coding agent proposed that waits for a person.
export function Waiting({ data, busy, error, onDecide, onProposeFromNotes, run }) {
  const p = data || {};
  const modes = p.modes || [];
  const spec = p.spec || [];
  const expected = p.expected || [];
  const labels = p.labels || [];
  const n = modes.length + spec.length + expected.length + labels.length;
  return (
    <Section id="lab-loop-waiting" title={'Waiting for a decision' + (n ? ' (' + n + ')' : '')}
      hint="A coding agent may propose failure modes, labels and expected answers. Nothing it proposes counts until a person approves it here."
      right={onProposeFromNotes ? (
        <button type="button" className="lab-btn lab-btn-quiet lab-btn-sm" onClick={onProposeFromNotes} disabled={busy || !run}
          title={run ? 'The agent reads the notes on ' + run + ' questions nobody has labelled yet and proposes failure modes and labels. They wait here.'
            : 'Choose a run at the top first: the agent reads only that run’s notes.'}>{'Agent: propose from notes' + (run ? ' on ' + run : '')}</button>
      ) : null}>
      {error ? <div role="alert" className="lab-error">{error}</div> : null}
      {!n ? <p className="lab-small">{'Nothing is waiting.'}</p> : null}
      {modes.length ? (
        <ul className="lab-loop-list" aria-label="Proposed failure modes">
          {modes.map((m) => (
            <li key={m.id} className="lab-loop-item">
              <div><span className="lab-chip">{KIND_TEXT[m.kind] || m.kind}</span> <strong>{m.name || (m.current && m.current.name) || '—'}</strong></div>
              {m.definition ? <p className="lab-small">{m.definition}</p> : null}
              {m.current && m.kind === 'change' ? <p className="lab-small">{'Now: ' + m.current.name + (m.current.definition ? ' · ' + m.current.definition : '')}</p> : null}
              <p className="lab-small">{['Proposed', m.by ? 'by ' + m.by : '', m.at ? when(m.at) : ''].filter(Boolean).join(' · ')}</p>
              {p.can_decide_modes ? (
                <>
                  {m.kind !== 'retire' ? <p className="lab-small">{'Approve: the failure mode is confirmed, its rule is added to SPEC.md (under test) and the labels the agent suggested for it are accepted.'}</p> : null}
                  <Decide busy={busy} onDecide={(d, note) => onDecide('failure_mode', m.id, d, note)} />
                </>
              ) : <p className="lab-small">{'The platform team or the consultant decides failure modes.'}</p>}
            </li>
          ))}
        </ul>
      ) : null}
      {labels.length ? (
        <ul className="lab-loop-list" aria-label="Suggested labels">
          {labels.map((l) => (
            <li key={l.id} className="lab-loop-item">
              <div><span className="lab-chip">{'Suggested label'}</span> <strong>{l.question}</strong> <span className="lab-small">{excerpt(l.question_text, 140)}</span></div>
              <p className="lab-small">{'The agent says this answer shows '}<strong>{l.mode_name}</strong>{'.'}</p>
              <div className="lab-row">
                <button type="button" className="lab-btn lab-btn-sm" disabled={busy} onClick={() => onDecide('label', l.id, 'approved', null)}>{'Accept'}</button>
                <button type="button" className="lab-btn lab-btn-quiet lab-btn-sm" disabled={busy} onClick={() => onDecide('label', l.id, 'rejected', null)}>{'Reject'}</button>
              </div>
            </li>
          ))}
        </ul>
      ) : null}
      {spec.length ? (
        <ul className="lab-loop-list" aria-label="Proposed specification versions">
          {spec.map((v) => (
            <li key={v.id} className="lab-loop-item">
              <div><span className="lab-chip">{'Specification'}</span> <strong>{'Version ' + v.version}</strong>{' · ' + (v.rules || []).length + ' rules'}</div>
              <ol className="lab-loop-rules">
                {(v.rules || []).map((r) => <li key={r.code}><strong>{r.code}</strong>{' ' + (r.title ? r.title + ': ' : '') + r.text}</li>)}
              </ol>
              <p className="lab-small">{['Proposed', v.by ? 'by ' + v.by : '', v.at ? when(v.at) : ''].filter(Boolean).join(' · ')}</p>
              {p.can_decide_modes ? <Decide busy={busy} onDecide={(d, note) => onDecide('spec_version', v.id, d, note)} />
                : <p className="lab-small">{'The platform team or the consultant approves the specification.'}</p>}
            </li>
          ))}
        </ul>
      ) : null}
      {expected.length ? (
        <ul className="lab-loop-list" aria-label="Proposed expected answers">
          {expected.map((e) => {
            const d = e.derivation || {};
            const blocked = d.derived ? null : 'Approve needs a rule of the approved specification and the company records it was read from.';
            return (
              <li key={e.id} className="lab-loop-item">
                <div><span className="lab-chip">{'Expected answer'}</span> <strong>{e.question}</strong> <span className="lab-small">{e.question_text}</span></div>
                <p className="lab-loop-expected">{e.expected}</p>
                <Derivation d={d} />
                <p className="lab-small">{['Proposed', e.by ? 'by ' + e.by : '', e.at ? when(e.at) : ''].filter(Boolean).join(' · ')}</p>
                {p.can_decide_expected ? <Decide busy={busy} blocked={blocked} onDecide={(dd, note) => onDecide('expected_answer', e.id, dd, note)} />
                  : <p className="lab-small">{'A company admin, the platform team or the consultant decides expected answers.'}</p>}
              </li>
            );
          })}
        </ul>
      ) : null}
    </Section>
  );
}

// The specification: the approved version and its rules, a download, and the editor for the next version.
export function SpecPanel({ spec, draft, canEdit, busy, error, onDraft, onPropose, onCancelDraft, onDownload }) {
  const approved = spec && spec.approved;
  const [rules, setRules] = React.useState(null);
  const [intro, setIntro] = React.useState('');
  React.useEffect(() => {
    setRules(draft ? (draft.rules || []).map((r) => Object.assign({}, r)) : null);
    setIntro((draft && draft.intro) || '');
  }, [draft]);
  const edit = (i, k) => (e) => { const v = e.target.value; setRules((rs) => rs.map((r, j) => (j === i ? Object.assign({}, r, { [k]: v }) : r))); };
  const drop = (i) => setRules((rs) => rs.filter((_, j) => j !== i));
  return (
    <Section id="lab-loop-spec" title="Specification (SPEC.md)"
      hint="One rule per confirmed failure mode. Expected answers are derived from these rules and the company's records."
      right={approved ? <button type="button" className="lab-btn lab-btn-quiet lab-btn-sm" onClick={onDownload} disabled={busy}>{'Download SPEC.md'}</button> : null}>
      {error ? <div role="alert" className="lab-error">{error}</div> : null}
      {approved ? (
        <div className="lab-loop-spec">
          <p className="lab-small">{'Version ' + approved.version + ' in force'
            + (approved.review && approved.review.by ? ' · approved by ' + approved.review.by : '')
            + (approved.review && approved.review.at ? ' · ' + when(approved.review.at) : '')}</p>
          <ol className="lab-loop-rules" aria-label="Rules in force">
            {(approved.rules || []).map((r) => <li key={r.code}><strong>{r.code}</strong>{' ' + (r.title ? r.title + ': ' : '') + r.text}</li>)}
          </ol>
        </div>
      ) : <p className="lab-small">{'No version approved yet.'}</p>}
      {canEdit && !rules ? (
        <div className="lab-row"><button type="button" className="lab-btn lab-btn-sm" onClick={onDraft} disabled={busy}>{'Draft next version'}</button></div>
      ) : null}
      {canEdit && rules ? (
        <div className="lab-qp-form" aria-label="Next version">
          <label className="lab-field"><span>{'Introduction (optional)'}</span>
            <textarea className="lab-note" rows={3} maxLength={20000} value={intro} onChange={(e) => setIntro(e.target.value)} disabled={busy} />
          </label>
          {!rules.length ? <p className="lab-small">{'No rules: confirm a failure mode first.'}</p> : null}
          {rules.map((r, i) => (
            <fieldset key={r.code} className="lab-loop-rule">
              <legend><strong>{r.code}</strong>{r.new ? ' · new' : ''}{r.mode_retired ? ' · its failure mode was retired' : ''}
                {r.positives ? ' · ' + r.positives + ' positive' : ''}</legend>
              <label className="lab-field"><span>{'Title'}</span>
                <input className="lab-input" type="text" maxLength={200} value={r.title || ''} onChange={edit(i, 'title')} disabled={busy} />
              </label>
              <label className="lab-field"><span>{'Rule'}</span>
                <textarea className="lab-note" rows={3} maxLength={4000} value={r.text || ''} onChange={edit(i, 'text')} disabled={busy} />
              </label>
              <button type="button" className="lab-link lab-danger" onClick={() => drop(i)} disabled={busy}>{'Remove rule'}</button>
            </fieldset>
          ))}
          <div className="lab-row">
            <button type="button" className="lab-btn lab-btn-sm" disabled={busy || !rules.length || rules.some((r) => !String(r.text || '').trim())}
              onClick={() => onPropose({ rules: rules.map((r) => ({ code: r.code, mode_lineage_id: r.mode_lineage_id || null, title: r.title || null,
                text: String(r.text || '').trim(), mode_retired: r.mode_retired || undefined })), intro: intro.trim() || null,
              based_on: (draft && draft.based_on) || null })}>{busy ? 'Saving…' : 'Propose version'}</button>
            <button type="button" className="lab-btn lab-btn-quiet lab-btn-sm" onClick={onCancelDraft} disabled={busy}>{'Cancel'}</button>
            <span className="lab-small">{'A proposed version counts once the platform team or the consultant approves it.'}</span>
          </div>
        </div>
      ) : null}
    </Section>
  );
}

function CorrectionForm({ mode, rule, busy, onSave, onCancel }) {
  const [kind, setKind] = React.useState('prompt');
  const [reference, setReference] = React.useState('');
  const [description, setDescription] = React.useState('');
  return (
    <div className="lab-qp-form" aria-label={'Record a correction for ' + mode.name}>
      <label className="lab-field"><span>{'Kind'}</span>
        <select className="lab-input" value={kind} onChange={(e) => setKind(e.target.value)} disabled={busy}>
          {CORRECTION_KINDS.map(([k, t]) => <option key={k} value={k}>{t}</option>)}
        </select>
      </label>
      <label className="lab-field"><span>{'Reference'}</span>
        <input className="lab-input" type="text" maxLength={500} value={reference} placeholder="A commit, pull request or file"
          onChange={(e) => setReference(e.target.value)} disabled={busy} />
      </label>
      <label className="lab-field"><span>{'What changed (optional)'}</span>
        <textarea className="lab-note" rows={2} maxLength={2000} value={description} onChange={(e) => setDescription(e.target.value)} disabled={busy} />
      </label>
      <div className="lab-row">
        <button type="button" className="lab-btn lab-btn-sm" disabled={busy || !reference.trim()}
          onClick={() => onSave({ kind, reference: reference.trim(), description: description.trim() || null, mode: mode.lineage_id,
            spec_rule: rule ? rule.code : null })}>{'Record correction'}</button>
        <button type="button" className="lab-btn lab-btn-quiet lab-btn-sm" onClick={onCancel} disabled={busy}>{'Cancel'}</button>
      </div>
    </div>
  );
}

function RegressionForm({ mode, wide, companies, busy, onMake, onCancel }) {
  const [space, setSpace] = React.useState('full');
  const [company, setCompany] = React.useState((companies && companies[0] && companies[0].id) || '');
  const listId = 'lab-loop-co-' + mode.lineage_id;
  return (
    <div className="lab-qp-form" aria-label={'Make a regression run for ' + mode.name}>
      <label className="lab-field"><span>{'Instance to ask in'}</span>
        <select className="lab-input" value={space} onChange={(e) => setSpace(e.target.value)} disabled={busy}>
          {SPACES.map(([k, t]) => <option key={k} value={k}>{t}</option>)}
        </select>
      </label>
      {wide ? (
        <label className="lab-field"><span>{'Company'}</span>
          <input className="lab-input" type="text" list={listId} value={company} placeholder="The company id" onChange={(e) => setCompany(e.target.value)} disabled={busy} />
          <datalist id={listId}>{(companies || []).map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}</datalist>
        </label>
      ) : null}
      <div className="lab-row">
        <button type="button" className="lab-btn lab-btn-sm" disabled={busy || (wide && !company.trim())}
          onClick={() => onMake({ space, company: wide ? company.trim() : undefined })}>{'Make regression run'}</button>
        <button type="button" className="lab-btn lab-btn-quiet lab-btn-sm" onClick={onCancel} disabled={busy}>{'Cancel'}</button>
      </div>
    </div>
  );
}

// One confirmed failure mode and its place in the loop.
// The expected answer written on the Loop page, as the question page saves it: the rule of the approved specification it is
// derived from, the company records it was read from, and the values an answer must carry (one per line; ! = must not).
export function expectedBody(f, rule) {
  const values = String((f && f.values) || '').split('\n').map((v) => v.trim()).filter(Boolean);
  return { expected: String((f && f.expected) || '').trim(),
    evidence: Object.assign({ spec_rule: rule, data: String((f && f.records) || '').trim() }, values.length ? { values } : {}) };
}

// `initial` ({ text, records, values }): an expected answer already saved, opened to edit (10 Nov 2026). Saving adds a new
// version; the newest is the one Compare checks.
export function ExpectedForm({ code, rule, busy, onSave, onCancel, initial }) {
  const [f, setF] = React.useState(() => ({ expected: (initial && initial.text) || '', records: (initial && initial.records) || '',
    values: ((initial && initial.values) || []).join('\n') }));
  const set = (k) => (e) => { const v = e.target.value; setF((p) => Object.assign({}, p, { [k]: v })); };
  const ok = f.expected.trim() && f.records.trim() && rule;
  return (
    <div className="lab-qp-form" aria-label={'Expected answer for ' + code}>
      <p className="lab-small">{'Write it from the company’s records, never from the model’s answer. Derived from rule '}<strong>{rule || '—'}</strong>{'.'}</p>
      <label className="lab-field"><span>{'Expected answer'}</span>
        <textarea className="lab-note" rows={3} maxLength={20000} value={f.expected} onChange={set('expected')} disabled={busy} />
      </label>
      <label className="lab-field"><span>{'Company records it was read from'}</span>
        <textarea className="lab-note" rows={2} maxLength={4000} value={f.records} onChange={set('records')} disabled={busy}
          placeholder="The records or query the expected answer was read from" />
      </label>
      <label className="lab-field"><span>{'Values to check (optional, one per line)'}</span>
        <textarea className="lab-note" rows={3} maxLength={4000} value={f.values} onChange={set('values')} disabled={busy}
          placeholder={'Names and dates the answer must carry, e.g.\nHamworthy Heating\n2026-11-19\nStart a line with ! for something it must not say'} />
      </label>
      <div className="lab-row">
        <button type="button" className="lab-btn lab-btn-sm" disabled={busy || !ok} onClick={() => onSave(code, expectedBody(f, rule))}>{'Save expected answer'}</button>
        <button type="button" className="lab-btn lab-btn-quiet lab-btn-sm" onClick={onCancel} disabled={busy}>{'Cancel'}</button>
      </div>
    </div>
  );
}

// Test the fix (6 Nov 2026): a fresh regression run of the mode's questions (with their expected answers as they are now),
// asked again with the change under test, then compared. One click for what were three.
export async function testFix(api, mode, opts) {
  const o = opts || {};
  const companies = (mode && mode.companies) || [];
  let company;
  if (o.wide) {
    if (companies.length !== 1) {
      throw new Error('Choose the company with Make regression run: this failure mode has questions from '
        + (companies.length ? companies.length + ' companies' : 'no company') + '.');
    }
    company = companies[0];
  }
  const made = await api.makeRegression(mode.lineage_id, Object.assign({ space: 'full' }, company ? { company } : {}));
  const asked = await rerunAll(api, made.run, o.onProgress);
  const compare = await api.compare(made.run);
  return { run: made.run, asked, compare };
}

export function ModeRow({ mode, canTaxonomy, canCurate, wide, companies, busy, rerunning, onCorrection, onRegression, onRerun, onCompare, onDraftFix,
  onAddRule, onTestFix, onSaveExpected, onOpenQuestion, openExpected }) {
  const [form, setForm] = React.useState(null);
  const [expOpen, setExpOpen] = React.useState(null);
  const chips = stepChips(mode);
  const rule = mode.rule || null;
  const questions = mode.questions || [];
  const testing = rerunning && rerunning.mode === mode.lineage_id;
  return (
    <li className="lab-loop-mode" id={'lab-mode-' + mode.lineage_id}>
      <div className="lab-loop-mode-head">
        <strong>{mode.name}</strong>
        {rule ? <span className="lab-chip">{rule.code}</span> : null}
        <span className="lab-small">{(mode.positives || 0) + ' marked positive · ' + (mode.corrections || 0) + (mode.corrections === 1 ? ' correction' : ' corrections')}</span>
      </div>
      {mode.definition ? <p className="lab-small">{mode.definition}</p> : null}
      <ol className="lab-loop-steps" aria-label={'Where ' + mode.name + ' is in the loop'}>
        {chips.map((c) => (
          <li key={c.key} className={'lab-loop-step' + (c.done ? ' lab-loop-done' : '') + (c.next ? ' lab-loop-next' : '')}
            aria-current={c.next ? 'step' : undefined}>{c.label + (c.done ? ' ✓' : '')}</li>
        ))}
      </ol>
      {questions.length ? (
        <ul className="lab-loop-qs" aria-label={'Questions marked positive for ' + mode.name}>
          {questions.map((q) => {
            const open = expOpen === q.code || (!q.has_expected && openExpected === q.code);
            return (
              <li key={q.code}>
                <div className="lab-row">
                  {onOpenQuestion ? <button type="button" className="lab-lv-code" onClick={() => onOpenQuestion(q.code)}>{q.code}</button> : <strong>{q.code}</strong>}
                  <span className="lab-small">{excerpt(q.text, 120)}</span>
                  {q.has_expected ? <><span className="lab-chip lab-loop-ok">{'Expected answer ✓'}</span>
                    {canCurate && onSaveExpected && !open ? (
                      <button type="button" className="lab-link" onClick={() => setExpOpen(q.code)} disabled={busy || !rule}
                        aria-label={'Edit the expected answer for ' + q.code}>{'Edit'}</button>) : null}</>
                    : (canCurate && onSaveExpected && !open ? (
                      <button type="button" className="lab-link" onClick={() => setExpOpen(q.code)} disabled={busy || !rule}
                        title={rule ? undefined : 'Add this failure mode’s rule to SPEC.md first'}>{'Add expected answer'}</button>
                    ) : (!open ? <span className="lab-small">{'No expected answer yet'}</span> : null))}
                </div>
                {open ? <ExpectedForm code={q.code} rule={rule && rule.code} busy={busy} onCancel={() => setExpOpen(null)}
                  initial={q.has_expected ? q.expected : null}
                  onSave={async (c, body) => { if (await onSaveExpected(c, body, mode)) setExpOpen(null); }} /> : null}
              </li>
            );
          })}
        </ul>
      ) : null}
      <div className="lab-row">
        {canTaxonomy && !rule && onAddRule ? (
          <button type="button" className="lab-btn lab-btn-sm" onClick={() => onAddRule(mode)} disabled={busy}
            title="Adds the rule for this failure mode to SPEC.md as a new approved version, under test">{'Add to SPEC.md'}</button>
        ) : null}
        {canCurate && rule && onTestFix ? (
          <button type="button" className="lab-btn lab-btn-sm" onClick={() => onTestFix(mode)} disabled={busy || !!rerunning}
            title="A fresh regression run of these questions, asked again with the change under test, then compared">{'Test the fix'}</button>
        ) : null}
        {testing ? <span className="lab-small" role="status">{rerunning.text}</span> : null}
        {canTaxonomy && !form && onDraftFix ? (
          <button type="button" className="lab-link" onClick={() => onDraftFix(mode)} disabled={busy || !rule}
            title={rule ? 'The agent drafts a change to an instruction document. It waits below for a person.' : 'Add this failure mode to the approved specification first'}>{'Agent: draft fix'}</button>
        ) : null}
        {canTaxonomy && !form ? <button type="button" className="lab-link" onClick={() => setForm('correction')} disabled={busy}>{'Record correction'}</button> : null}
        {canCurate && !form ? (
          <button type="button" className="lab-link" onClick={() => setForm('regression')} disabled={busy || !rule}
            title={rule ? undefined : 'Add this failure mode to the approved specification first'}>{'Make regression run'}</button>
        ) : null}
      </div>
      {form === 'correction' ? <CorrectionForm mode={mode} rule={rule} busy={busy} onCancel={() => setForm(null)}
        onSave={async (b) => { if (await onCorrection(b)) setForm(null); }} /> : null}
      {form === 'regression' ? <RegressionForm mode={mode} wide={wide} companies={companies} busy={busy} onCancel={() => setForm(null)}
        onMake={async (b) => { if (await onRegression(mode, b)) setForm(null); }} /> : null}
      {(mode.regression_runs || []).length ? (
        <ul className="lab-loop-runs" aria-label={'Regression runs of ' + mode.name}>
          {mode.regression_runs.map((g) => (
            <li key={g.run} className="lab-row">
              <strong>{g.run}</strong>
              <span className="lab-small">{[g.spec_rule, (SPACES.find((s) => s[0] === g.space) || [null, g.space])[1], g.at ? when(g.at) : ''].filter(Boolean).join(' · ')}</span>
              {canCurate ? <button type="button" className="lab-link" onClick={() => onRerun(g.run)} disabled={busy || !!rerunning}>{'Ask again'}</button> : null}
              <button type="button" className="lab-link" onClick={() => onCompare(g.run)} disabled={busy}>{'Compare'}</button>
              {rerunning && rerunning.run === g.run ? <span className="lab-small" role="status">{rerunning.text}</span> : null}
            </li>
          ))}
        </ul>
      ) : null}
    </li>
  );
}

const excerpt = (s, n) => { const t = String(s || '').trim(); return t.length > n ? t.slice(0, n) + '…' : t; };

const byLine = (verb, who, at) => [verb, who ? 'by ' + who : '', at ? when(at) : ''].filter(Boolean).join(' · ');

function ChangeHead({ c }) {
  return (
    <div className="lab-loop-mode-head">
      <code className="lab-loop-path">{c.path}</code>
      {c.spec_rule ? <span className="lab-chip">{c.spec_rule}</span> : null}
      {c.spec_version ? <span className="lab-chip">{'SPEC.md version ' + c.spec_version}</span> : null}
      {c.mode_name ? <span className="lab-small">{c.mode_name}</span> : null}
      {(c.companions || []).length ? (
        <span className="lab-small">{'with '}{(c.companions || []).map((x) => <code key={x.id} className="lab-loop-path">{x.path}</code>)}
          {' (the reviewer\u2019s clause, decided together)'}</span>
      ) : null}
    </div>
  );
}

// The instruction documents the agent changed (Bala, 5 Nov 2026): the SKILL.md files, their reference documents and
// SPEC.md live in one place and every agent reads them at run time, so a change needs no rebuild or redeploy. A change
// waits for a person; approved, it is under test and only the reruns here read it; made live, every agent reads it;
// the live documents go into the repo with the next commit, once testing is complete. `changes` is null for anyone
// who cannot change the instructions, and the panel is not shown.
export function ChangesPanel({ changes, busy, error, onApprove, onReject, onLive, onExport }) {
  if (!changes) return null;
  const proposed = changes.proposed || [];
  const testing = changes.testing || [];
  const live = changes.live || [];
  return (
    <Section id="lab-loop-changes" title={'Instruction changes' + (proposed.length ? ' (' + proposed.length + ' waiting)' : '')}
      hint="The agents read these documents from one place every time they answer, so a change needs no rebuild or redeploy. Approved text is under test: only the reruns in this lab read it. Make it live once Compare shows it fixed. The repo gets the files at the next commit."
      right={live.length ? <button type="button" className="lab-btn lab-btn-quiet lab-btn-sm" onClick={onExport} disabled={busy}>{'Download for commit (.zip)'}</button> : null}>
      {error ? <div role="alert" className="lab-error">{error}</div> : null}
      {!proposed.length && !testing.length && !live.length ? <p className="lab-small">{'No instruction changes yet.'}</p> : null}
      {proposed.length ? (
        <>
          <h3 className="lab-loop-sub">{'Waiting for a decision'}</h3>
          <ul className="lab-loop-list" aria-label="Instruction changes waiting for a decision">
            {proposed.map((c) => (
              <li key={c.id} className="lab-loop-item">
                <ChangeHead c={c} />
                {c.section ? <pre className="lab-loop-section">{c.section}</pre> : null}
                {c.why ? <p className="lab-small">{'Why: ' + c.why}</p> : null}
                {(c.questions || []).length ? <p className="lab-small">{'From ' + c.questions.join(', ')}</p> : null}
                <p className="lab-small">{['Drafted by the agent', c.by ? 'for ' + c.by : '', c.at ? when(c.at) : ''].filter(Boolean).join(' · ')}</p>
                <div className="lab-row">
                  <button type="button" className="lab-btn lab-btn-sm" disabled={busy} onClick={() => onApprove(c.id)}>{'Approve for testing'}</button>
                  <button type="button" className="lab-btn lab-btn-quiet lab-btn-sm" disabled={busy} onClick={() => onReject(c.id)}>{'Reject'}</button>
                </div>
              </li>
            ))}
          </ul>
        </>
      ) : null}
      {testing.length ? (
        <>
          <h3 className="lab-loop-sub">{'Under test'}</h3>
          <ul className="lab-loop-list" aria-label="Instruction changes under test">
            {testing.map((c) => (
              <li key={c.id} className="lab-loop-item">
                <ChangeHead c={c} />
                <p className="lab-small">{byLine('Approved', c.decided_by, c.decided_at) + ' · only the reruns in this lab read it'}</p>
                <div className="lab-row">
                  <button type="button" className="lab-btn lab-btn-sm" disabled={busy} onClick={() => onLive(c.id)}>{'Make live'}</button>
                  <button type="button" className="lab-btn lab-btn-quiet lab-btn-sm" disabled={busy} onClick={() => onReject(c.id)}>{'Reject'}</button>
                </div>
              </li>
            ))}
          </ul>
        </>
      ) : null}
      {live.length ? (
        <>
          <h3 className="lab-loop-sub">{'Live'}</h3>
          <ul className="lab-loop-list" aria-label="Live instruction changes">
            {live.map((c) => (
              <li key={c.id} className="lab-loop-item">
                <ChangeHead c={c} />
                <p className="lab-small">{byLine('Made live', c.decided_by, c.decided_at) + ' · every agent reads it'}</p>
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </Section>
  );
}

// A regression run compared: each question's expected answer, the answer people marked, the newest answer, and whether
// each carries the expected values.
// Where each missing value was lost in the rerun (10 Nov 2026), and what the next step is: a value the data never had
// needs a code change; anything else is drafted again, and the next draft reads this test.
const LOST = {
  not_in_data: 'not in the data it read: the lookup asked for the wrong thing, or no tool returns it',
  not_stated: 'a figure worked out from the data, not stated',
  dropped_by_writer: 'in the data, left out by the answer',
  removed_in_review: 'written, then removed by the review',
  lost_at_the_end: 'written, then lost before the final answer',
  said_anyway: 'still said'
};

function LostList({ lost }) {
  const gapsOnly = lost.every((x) => x.stage === 'not_in_data');
  return (
    <div className="lab-small lab-loop-lost">
      <div>{'Where it was lost:'}</div>
      <ul>{lost.map((x, i) => <li key={i}><code>{x.raw}</code>{' \u2014 ' + (LOST[x.stage] || x.stage)}</li>)}</ul>
      <div>{gapsOnly ? 'Press Agent: draft fix: it fixes the lookup first. If a fix was already tried, the tool needs a code change.'
        : 'Press Agent: draft fix again: the next draft reads this test.'}</div>
    </div>
  );
}

export function CompareView({ data, onClose, onOpenQuestion }) {
  if (!data) return null;
  const sm = data.summary || {};
  return (
    <Section id="lab-loop-compare" title={'Compare ' + data.run + (data.spec_rule ? ' · ' + data.spec_rule : '')}
      right={onClose ? <button type="button" className="lab-link" onClick={onClose}>{'Close'}</button> : null}>
      <div className="lab-tiles" role="list" aria-label="Summary">
        {['fixed', 'passing', 'still_failing', 'regressed', 'no_answer', 'not_rerun', 'no_expected'].map((k) => (
          <div key={k} role="listitem" className={'lab-tile lab-loop-tile-' + k}>
            <span className="lab-tile-n">{sm[k] || 0}</span><span className="lab-tile-text">{VERDICT[k]}</span>
          </div>
        ))}
      </div>
      <div className="lab-lv-scroll" role="region" aria-label="Questions compared" tabIndex={0}>
        <table className="lab-loop-table">
          <thead><tr><th scope="col">{'Question'}</th><th scope="col">{'Expected'}</th><th scope="col">{'Before'}</th><th scope="col">{'After'}</th><th scope="col">{'Verdict'}</th><th scope="col">{'People'}</th></tr></thead>
          <tbody>
            {(data.questions || []).map((q) => (
              <tr key={q.question}>
                <th scope="row">
                  {onOpenQuestion ? <button type="button" className="lab-lv-code" onClick={() => onOpenQuestion(q.question)}>{q.question}</button> : q.question}
                  <div className="lab-small">{excerpt(q.text, 160)}</div>
                  {q.from ? <div className="lab-small">{'From ' + q.from}</div> : null}
                </th>
                <td>{q.expected ? excerpt(q.expected, 240) : <span className="lab-small">{'No expected answer'}</span>}
                  {q.expected && !q.derived ? <div className="lab-loop-warn">{'Not derived from the specification'}</div> : null}</td>
                <td>{q.before ? <><div>{excerpt(q.before.answer, 240)}</div>{q.before.check ? <div className="lab-small">{STATUS[q.before.check.status] || q.before.check.status}</div> : null}</> : '—'}</td>
                <td>{q.after ? <><div>{excerpt(q.after.answer, 240)}</div>{q.after.check ? <div className="lab-small">{STATUS[q.after.check.status] || q.after.check.status}</div> : null}</> : '—'}</td>
                <td><span className={'lab-chip lab-loop-v-' + q.verdict}>{VERDICT[q.verdict] || q.verdict}</span>
                  {(q.lost || []).length ? <LostList lost={q.lost} /> : null}</td>
                <td className="lab-small">{((q.people && q.people.positive) || 0) + ' positive · ' + ((q.people && q.people.negative) || 0) + ' negative'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Section>
  );
}

// Companies a regression run can be made for, from the runs in view (the platform team and the consultant see them all).
export function companiesOf(runs) {
  const seen = {};
  (runs || []).forEach((r) => {
    if (!r || !r.organization_id || seen[r.organization_id]) return;
    const who = r.owner_email && r.owner_email.indexOf('@') > 0 ? r.owner_email.split('@')[1] : r.title;
    seen[r.organization_id] = { id: r.organization_id, label: who || r.organization_id };
  });
  return Object.keys(seen).map((k) => seen[k]);
}

// The page itself, from what the service returned. Loop (below) reads the service and hands the actions back.
export function LoopView({ me, run, loop, proposals, spec, draft, compare, changes, notice, busy, errors, rerunning, companies, on, next, openExpected }) {
  const wide = !!(me && me.all_companies);
  const curate = !!(me && (me.all_companies || me.manages));
  const errs = errors || {};
  const modes = (loop && loop.modes) || [];
  return (
    <div className="lab-page-body lab-page-full lab-cloop">
      <div className="lab-page-head">
        <h1>{'Correction loop'}</h1>
        <ol className="lab-loop-steps lab-loop-flow" aria-label="The loop">
          {LOOP_STEPS.map(([k, t]) => <li key={k} className="lab-loop-step">{t}</li>)}
        </ol>
        <p className="lab-small lab-big">{'People write the notes and labels. A coding agent only proposes. Expected answers come from the approved specification and the company’s records, never from the model’s answer.'}</p>
      </div>
      {errs.load ? <div role="alert" className="lab-error">{errs.load}</div> : null}
      {errs.agent ? <div role="alert" className="lab-error">{errs.agent}</div> : null}
      {notice ? <p className="lab-small lab-loop-notice" role="status">{notice}</p> : null}
      <Waiting data={proposals} busy={busy} error={errs.decide} onDecide={on.decide}
        onProposeFromNotes={wide && on.proposeFromNotes ? on.proposeFromNotes : null} run={run} />
      <SpecPanel spec={spec} draft={draft} canEdit={wide} busy={busy} error={errs.spec} onDraft={on.draft} onPropose={on.propose}
        onCancelDraft={on.cancelDraft} onDownload={on.download} />
      <Section id="lab-loop-modes" title="Failure modes in the loop"
        hint="Each confirmed failure mode: the questions people marked positive, its rule, the corrections made for it, and its regression runs.">
        {errs.modes ? <div role="alert" className="lab-error">{errs.modes}</div> : null}
        {!modes.length ? <p className="lab-small">{'No confirmed failure modes yet. Add one in Labels, or approve one a coding agent proposed.'}</p> : (
          <ul className="lab-loop-list">
            {modes.map((m) => <ModeRow key={m.lineage_id} mode={m} canTaxonomy={wide} canCurate={curate} wide={wide} companies={companies}
              busy={busy} rerunning={rerunning} onCorrection={on.correction} onRegression={on.regression} onRerun={on.rerun} onCompare={on.compare}
              onDraftFix={changes ? on.draftFix : null} onAddRule={on.addRule} onTestFix={on.testFix} onSaveExpected={on.saveExpected}
              onOpenQuestion={on.openQuestion} openExpected={openExpected} />)}
          </ul>
        )}
      </Section>
      <ChangesPanel changes={changes} busy={busy} error={errs.changes} onApprove={on.approveChange} onReject={on.rejectChange}
        onLive={on.liveChange} onExport={on.exportChanges} />
      {compare ? <CompareView data={compare} onClose={on.closeCompare} onOpenQuestion={on.openQuestion} /> : null}
      <NextStep step={next} busy={busy} onAct={on.nextAct} onClose={on.nextClose} />
    </div>
  );
}

function save(blob, name) {
  try {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (e) { /* the browser refused the download */ }
}

export default function Loop({ me, runs, run, onOpenQuestion }) {
  const [loop, setLoop] = React.useState(null);
  const [proposals, setProposals] = React.useState(null);
  const [spec, setSpec] = React.useState(null);
  const [draft, setDraft] = React.useState(null);
  const [compare, setCompare] = React.useState(null);
  const [busy, setBusy] = React.useState(false);
  const [errors, setErrors] = React.useState({});
  const [rerunning, setRerunning] = React.useState(null);
  const [changes, setChanges] = React.useState(null);
  const [notice, setNotice] = React.useState('');
  // The "Next step" pop-up, the failure mode the steps are about, and the question whose expected answer form is open.
  const [next, setNext] = React.useState(null);
  const [focus, setFocus] = React.useState(null);
  const [openExpected, setOpenExpected] = React.useState(null);
  const latest = React.useRef({ loop: null, changes: null });
  latest.current = { loop, changes };
  const show = (event, data) => setNext(nextStepFor(event, data));
  const modeOf = (lin) => (((latest.current.loop || {}).modes) || []).find((x) => x.lineage_id === lin) || null;
  const scrollTo = (id) => { try { const el = document.getElementById(id); if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' }); } catch (e) { /* no page */ } };
  const err = (k, v) => setErrors((p) => Object.assign({}, p, { [k]: v }));
  const wide = !!(me && me.all_companies);

  const load = React.useCallback(async () => {
    const [l, p, s, c] = await Promise.allSettled([labApi.loop(), labApi.proposals(), labApi.spec(),
      wide ? labApi.changes() : Promise.resolve(null)]);
    if (l.status === 'fulfilled') setLoop(l.value); else err('modes', errText(l.reason));
    if (p.status === 'fulfilled') setProposals(p.value); else err('decide', errText(p.reason));
    if (s.status === 'fulfilled') setSpec(s.value); else err('spec', errText(s.reason));
    // Only the platform team and the consultant change the instructions; for anyone else the panel is not shown.
    if (c.status === 'fulfilled') setChanges(c.value);
    else if (c.reason && c.reason.status === 403) setChanges(null);
    else { setChanges({ proposed: [], testing: [], live: [] }); err('changes', errText(c.reason)); }
  }, [wide]);
  React.useEffect(() => { load(); }, [load]);

  const act = async (key, fn) => {
    setBusy(true);
    err(key, '');
    try {
      await fn();
      await load();
      return true;
    } catch (e) {
      err(key, errText(e));
      return false;
    } finally {
      setBusy(false);
    }
  };
  const on = {
    decide: (kind, id, decision, note) => act('decide', async () => {
      const out = await labApi.decideProposal(kind, id, { decision, note });
      if (out && out.published === 'testing') setNotice('SPEC.md version approved: it is under test. Only the reruns in this lab read it until a person makes it live.');
      else if (out && out.published === false) setNotice('SPEC.md version approved, but it could not be put under test: ' + (out.publish_error || 'the instruction store did not answer') + '.');
      if (decision !== 'approved' || !out) return;
      if (kind === 'failure_mode' && out.mode) { setFocus(out.mode); show('mode_approved', out); }
      else if (kind === 'label') { setFocus(out.mode); show('label_accepted', { hasRule: !!(modeOf(out.mode) || {}).rule }); }
    }),
    addRule: (mode) => act('modes', async () => {
      const out = await labApi.addRule(mode.lineage_id);
      setFocus(mode.lineage_id);
      show('rule_added', out);
    }),
    saveExpected: (code, body, mode) => act('modes', async () => {
      await labApi.setExpected(code, body);
      setOpenExpected(null);
      setFocus(mode.lineage_id);
      show('expected_saved');
    }),
    testFix: async (mode) => {
      err('modes', '');
      setFocus(mode.lineage_id);
      setRerunning({ mode: mode.lineage_id, text: 'Making the regression run…' });
      try {
        const out = await testFix(labApi, mode, { wide, onProgress: (p) => setRerunning({ mode: mode.lineage_id,
          text: 'Asked ' + p.asked + (p.total !== null ? ' of ' + p.total : '') + (p.failed ? ' · ' + p.failed + ' failed' : '') }) });
        setRerunning(null);
        setCompare(out.compare);
        await load();
        show('tested', (out.compare && out.compare.summary) || {});
      } catch (e) {
        setRerunning(null);
        err('modes', 'Could not test the fix: ' + errText(e));
      }
    },
    nextAct: (a) => {
      const m = modeOf(focus) || (((latest.current.loop || {}).modes) || [])[0] || null;
      if (a === 'show_waiting') scrollTo('lab-loop-waiting');
      else if (a === 'show_changes') scrollTo('lab-loop-changes');
      else if (a === 'show_compare') scrollTo('lab-loop-compare');
      else if (a === 'export') on.exportChanges();
      else if (!m) scrollTo('lab-loop-modes');
      else if (a === 'expected') {
        const q = (m.questions || []).find((x) => !x.has_expected);
        setOpenExpected(q ? q.code : null);
        scrollTo('lab-mode-' + m.lineage_id);
      } else if (a === 'add_rule') on.addRule(m);
      else if (a === 'draft_fix') on.draftFix(m);
      else if (a === 'test_fix') on.testFix(m);
    },
    nextClose: () => setNext(null),
    proposeFromNotes: () => act('agent', async () => {
      const out = await labApi.proposeFromNotes(run);
      show('proposed', out);
      const n = ((out && out.proposed_modes) || []).length;
      const read = (out && out.notes_read) || 0;
      setNotice(!read ? (out && out.message) || ('No new notes on unlabelled questions of ' + run + '.')
        : 'The agent read ' + read + (read === 1 ? ' note' : ' notes') + ' on ' + (((out && out.questions) || []).join(', ') || run)
          + ' and proposed ' + n + (n === 1 ? ' new failure mode' : ' new failure modes') + ' and ' + ((out && out.labels) || 0)
          + ' labels. They wait for a decision below.');
    }),
    draftFix: (mode) => act('agent', async () => {
      setNotice('The agent is drafting a change for ' + mode.name + '…');
      let out;
      try {
        out = await labApi.draftFix(mode.lineage_id);
      } catch (e) {
        setNotice('');
        throw e;
      }
      setNotice('The agent drafted a change' + (out && out.path ? ' to ' + out.path : '') + ' for ' + mode.name + '. It waits under Instruction changes.');
      setFocus(mode.lineage_id);
      show('fix_drafted', out);
    }),
    approveChange: (id) => act('changes', async () => {
      const out = await labApi.approveChange(id);
      setNotice((out && out.path ? out.path + ' is' : 'The change is') + ' under test. Test the fix next.');
      const c = ((latest.current.changes || {}).proposed || []).find((x) => x.id === id);
      if (c && c.mode) setFocus(c.mode);
      show('change_approved');
    }),
    rejectChange: (id) => act('changes', () => labApi.rejectChange(id)),
    liveChange: (id) => act('changes', async () => {
      await labApi.liveChange(id);
      setNotice('The change is live: every agent reads it from its next answer. Download the live files for the commit.');
      show('made_live');
    }),
    exportChanges: () => act('changes', async () => {
      const out = await labApi.exportChanges();
      if (out && out.blob) save(out.blob, 'hoistra-instructions-for-commit.zip');
    }),
    draft: () => act('spec', async () => setDraft(await labApi.specDraft())),
    cancelDraft: () => setDraft(null),
    propose: (body) => act('spec', async () => { await labApi.proposeSpec(body); setDraft(null); }),
    download: () => act('spec', async () => {
      const out = await labApi.specFile();
      if (out && out.blob) save(out.blob, 'SPEC.md');
    }),
    correction: (body) => act('modes', () => labApi.addCorrection(body)),
    regression: (mode, body) => act('modes', () => labApi.makeRegression(mode.lineage_id, body)),
    rerun: async (code) => {
      setRerunning({ run: code, text: 'Asking…' });
      err('modes', '');
      try {
        const out = await rerunAll(labApi, code, (p) => setRerunning({ run: code,
          text: 'Asked ' + p.asked + (p.total !== null ? ' of ' + p.total : '') + (p.failed ? ' · ' + p.failed + ' failed' : '') }));
        setRerunning({ run: code, text: 'Asked ' + out.asked + ' again' + (out.failed ? ' · ' + out.failed + ' failed' : '')
          + (out.underTest.length ? ' · read under test: ' + out.underTest.join(', ') : '') });
        setCompare(await labApi.compare(code));
      } catch (e) {
        err('modes', 'Could not ask ' + code + ' again: ' + errText(e));
        setRerunning(null);
      }
    },
    compare: (code) => act('modes', async () => setCompare(await labApi.compare(code))),
    closeCompare: () => setCompare(null),
    openQuestion: onOpenQuestion
  };
  return (
    <LoopView me={me} run={run} loop={loop} proposals={proposals} spec={spec} draft={draft} compare={compare} changes={changes} notice={notice}
      busy={busy} errors={errors} next={next} openExpected={openExpected}
      rerunning={rerunning} companies={companiesOf(runs)} on={on} />
  );
}
