// A live question's expected answer, on its turn in the session page (14 Nov 2026, Hussain: "I want the system to add
// it automatically when I ask a question on the live Hoistra platform ... an expected answer in the trace based on the
// spec file"; "only for the live answers, don't change anything in the flow from Codex"). The service drafts it when the
// question is synced (auto_expected); a person accepts it (saved as their own version) or edits it. Codex turns and
// turns with no question show nothing.
//
// Values read from the records (16 Nov 2026, Hussain and Bala): for the questions the answer key knows, every value is a
// button. It opens how the value was found - the query, the values filled into it and the rows it read - with Copy to
// check in the database (psql, read-only), Run again now, and This is right / This is wrong. Nobody edits the database:
// the checks are saved in Trace Lab, as the person's own version of the expected answer.
import React from 'react';
import { labApi } from './labApi.js';
import { errText } from './logic/labelling.js';
import './turnExpected.css';

const evidenceOf = (d) => (((d || {}).expected || {}).evidence) || {};
const isKey = (d) => !!d && d.kind === 'answer_key' && Array.isArray(evidenceOf(d).facts);
export const factText = (f) => (f.label ? f.label + ': ' : '') + String(f.value);

function when(iso) {
  const t = iso ? new Date(iso) : null;
  if (!t || Number.isNaN(t.getTime())) return '';
  return t.toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
}

// What Accept saves: the draft's words as the person's own expected answer, with what it was drawn from.
export function acceptBody(data) {
  const d = data || {};
  if (isKey(d)) return { expected: ((d.expected || {}).expected) || '', evidence: Object.assign({}, evidenceOf(d), { drafted_from: 'answer_key' }) };
  const rule = (d.rules || [])[0];
  const evidence = Object.assign(rule ? { spec_rule: rule } : {}, { drafted_from: 'SPEC.md', spec_version: d.spec_version });
  return { expected: ((d.expected || {}).expected) || '', evidence };
}

// What Save my checks saves: the same answer, each value marked right or wrong (with what it should be and why).
export function checksBody(data, marks) {
  const ev = evidenceOf(data);
  const facts = (ev.facts || []).map((f) => {
    const m = (marks || {})[f.id];
    if (!m || !m.check) return f;
    const out = Object.assign({}, f, { check: m.check });
    if (m.check === 'wrong' && String(m.corrected || '').trim()) out.corrected = String(m.corrected).trim();
    if (m.check === 'wrong' && String(m.note || '').trim()) out.note = String(m.note).trim();
    return out;
  });
  return { expected: ((data || {}).expected || {}).expected || '', evidence: Object.assign({}, ev, { drafted_from: 'answer_key', facts }) };
}

function whereFrom(d) {
  const e = d.expected || {};
  if (isKey(d)) {
    if (d.draft) return 'Read from the records' + (when(evidenceOf(d).read_at) ? ' at ' + when(evidenceOf(d).read_at) : '') + ' · not accepted yet';
    return 'Checked by ' + (e.author_email || 'a person');
  }
  if (d.draft) {
    const rules = (d.rules || []).length ? (d.rules.length === 1 ? 'rule ' : 'rules ') + d.rules.join(', ') : 'no rule cited';   // a draft still says what the answer holds (18 Nov 2026)
    return 'Drafted from SPEC.md' + (d.spec_version ? ' v' + d.spec_version : '') + ' · ' + rules + ' · not accepted yet';
  }
  if (d.from_draft) {
    return ['Accepted from the SPEC.md draft', (d.rules || []).length ? 'rule ' + d.rules.join(', ') : null, e.author_email]
      .filter(Boolean).join(' · ');
  }
  return e.author_email ? 'Written by ' + e.author_email : 'Written by a person';
}

// The columns a person reads first (16 Nov 2026, Hussain): what the record is, whose, when, and its state. Internal
// columns (id, __key, __sort …) are left out; the rest follow.
const FIRST_COLS = ['certificate_number', 'certificate_ref', 'cert_type', 'title', 'name', 'vendor_name', 'building_name',
  'expiry_date', 'next_due_date', 'sla_due_at', 'status', 'priority'];
export function columnsOf(rows) {
  const seen = [];
  (rows || []).slice(0, 5).forEach((r) => Object.keys(r || {}).forEach((k) => { if (seen.indexOf(k) < 0) seen.push(k); }));
  const filled = (k) => (rows || []).some((r) => r && r[k] != null && r[k] !== '');
  const keep = seen.filter((k) => k !== 'id' && !/^__/.test(k) && !/_id$/.test(k) && filled(k));
  const first = FIRST_COLS.filter((k) => keep.indexOf(k) >= 0);
  return first.concat(keep.filter((k) => first.indexOf(k) < 0)).slice(0, 8);
}
// Which query a value's "How this value was found" opens. A list answer's count opens the query that read the records
// themselves (the records are what a person checks), not the one that counted them.
export function receiptFor(ev, fact) {
  const e = ev || {};
  if (!fact) return null;
  const items = (e.facts || []).filter((f) => !f.label && f.receipt);
  const rid = typeof fact.value === 'number' && fact.label && items.length ? items[0].receipt : fact.receipt;
  return (e.receipts || []).find((r) => r.id === rid) || (e.receipts || []).find((r) => r.id === fact.receipt) || null;
}
// The values to click: the counts, labelled. A list answer's records are not each a value: its count opens them.
export function valuesOf(ev) {
  const all = (ev || {}).facts || [];
  const named = all.filter((f) => f.label);
  return named.length ? named : all;
}

function Rows({ rows, count }) {
  const shown = (rows || []).slice(0, 50);
  const cols = columnsOf(shown);
  return (
    <div className="lab-xp-block">
      <span className="lab-xp-h">{'What it found'}</span>
      <p className="lab-small">{count + (count === 1 ? ' row' : ' rows') + (count > shown.length ? ', the first ' + shown.length + ' below' : '')}</p>
      {shown.length ? (
        <div className="lab-xp-scroll">
          <table className="lab-xp-table">
            <thead><tr>{cols.map((c) => <th key={c}>{c}</th>)}</tr></thead>
            <tbody>{shown.map((r, i) => <tr key={i}>{cols.map((c) => <td key={c}>{r[c] == null ? '—' : String(r[c])}</td>)}</tr>)}</tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}

// How one value was found: in plain words, then the query, the values filled in, the rows, and what to do about it.
export function Receipt({ fact, receipt, readAt, mark, rerun, rerunBusy, copied, busy, onCopy, onRerun, onMark, onClose }) {
  if (!fact || !receipt) return null;
  const counted = typeof fact.value === 'number';
  const listed = counted && receipt.id !== fact.receipt;
  const explain = listed ? 'These are the ' + fact.value + ' records the query below found.'
    : counted
      ? (fact.label && /aggregate/i.test(receipt.label || '') ? 'This number is the count the query below gives for ' + fact.label + '.'
        : 'This number is how many records the query below found.')
      : 'This is one of the records the query below found.';
  const params = Object.entries(receipt.params || {});
  const now = rerun ? ((rerun.facts || []).find((x) => x.id === fact.id) || null) : null;
  const m = mark || {};
  return (
    <div className="lab-xp-receipt" role="region" aria-label={'How ' + factText(fact) + ' was found'}>
      <div className="lab-row lab-xp-head">
        <strong>{'How this value was found'}</strong>
        <span className="lab-small">{factText(fact) + (readAt ? ' · read ' + when(readAt) : '')}</span>
        <span className="lab-spacer" />
        <button type="button" className="lab-link" onClick={() => onClose && onClose()}>{'Close'}</button>
      </div>
      <p className="lab-xp-plain">{explain}</p>
      <Rows rows={receipt.rows} count={Number(receipt.row_count) || 0} />
      <details className="lab-xp-block lab-xp-tech">
        <summary>{'Show the query (technical)'}</summary>
        <pre className="lab-xp-sql">{receipt.sql}</pre>
        <p className="lab-small">{'It only reads: it cannot change anything.'}</p>
        {params.length ? (
          <>
            <span className="lab-xp-h">{'Values filled in'}</span>
            <table className="lab-xp-table lab-xp-params"><tbody>
              {params.map(([k, v]) => <tr key={k}><th>{k}</th><td>{Array.isArray(v) ? v.join(', ') : String(v)}</td></tr>)}
            </tbody></table>
          </>
        ) : null}
      </details>
      {now ? (
        <p className="lab-xp-now" role="status">{'Now: ' + String(now.value) + (String(now.value) === String(fact.value)
          ? ' · the same as when the question was asked'
          : ' · was ' + String(fact.value) + ' when the question was asked: the records have changed since')}</p>
      ) : null}
      <div className="lab-row lab-xp-actions">
        {receipt.psql ? <button type="button" className="lab-btn lab-btn-quiet lab-btn-sm" onClick={() => onCopy && onCopy(receipt.psql)}
          title="Paste it into psql or any database tool. It only reads.">{copied ? 'Copied' : 'Copy to check in the database'}</button> : null}
        {onRerun ? <button type="button" className="lab-btn lab-btn-quiet lab-btn-sm" disabled={!!rerunBusy} onClick={() => onRerun()}>{rerunBusy ? 'Reading…' : 'Run again now'}</button> : null}
        <span className="lab-spacer" />
        {onMark ? <><button type="button" className={'lab-btn lab-btn-sm lab-xp-right' + (m.check === 'right' ? ' lab-on' : ' lab-btn-quiet')} disabled={!!busy}
          aria-pressed={m.check === 'right' ? 'true' : 'false'} onClick={() => onMark && onMark({ check: 'right' })}>{'This is right'}</button>
        <button type="button" className={'lab-btn lab-btn-sm lab-xp-wrong' + (m.check === 'wrong' ? ' lab-on' : ' lab-btn-quiet')} disabled={!!busy}
          aria-pressed={m.check === 'wrong' ? 'true' : 'false'} onClick={() => onMark && onMark({ check: 'wrong' })}>{'This is wrong'}</button></> : null}
      </div>
      {m.check === 'wrong' ? (
        <div className="lab-xp-fix">
          <label className="lab-field"><span>{'What should it be?'}</span>
            <input className="lab-input" value={m.corrected || ''} onChange={(ev) => onMark && onMark({ check: 'wrong', corrected: ev.target.value })} /></label>
          <label className="lab-field"><span>{'Why is it wrong?'}</span>
            <textarea className="lab-note" rows={2} value={m.note || ''} onChange={(ev) => onMark && onMark({ check: 'wrong', note: ev.target.value })} /></label>
        </div>
      ) : null}
    </div>
  );
}

function KeyValues({ data, openFact, onOpen, marks }) {
  const ev = evidenceOf(data);
  return (
    <div className="lab-xp-values">
      <p className="lab-small">{'Click a value to see exactly how it was found.'}</p>
      <div className="lab-xp-chips">
        {valuesOf(ev).map((f) => {
          const m = (marks || {})[f.id] || (f.check ? { check: f.check, corrected: f.corrected } : null);
          const tail = m && m.check === 'right' ? ' · Right' : m && m.check === 'wrong' ? ' · Wrong' + (m.corrected ? ', should be ' + m.corrected : '') : '';
          return (
            <button key={f.id} type="button" aria-label={'See how ' + factText(f) + ' was found'} aria-expanded={openFact === f.id ? 'true' : 'false'}
              className={'lab-xp-chip' + (openFact === f.id ? ' lab-on' : '') + (m && m.check ? ' lab-xp-' + m.check : '')}
              onClick={() => onOpen && onOpen(openFact === f.id ? null : f.id)}>
              {f.label ? <span className="lab-xp-k">{f.label}</span> : null}<strong>{String(f.value)}</strong>{tail}
            </button>
          );
        })}
      </div>
    </div>
  );
}

// ── Correcting Hoistra's query (16 Nov 2026, Hussain): the records query the orchestrator made, as plain fields a person
// can change - the states, the building, the dates - read again read-only, and kept as the expected answer if they say so.
const words = (x) => String(x || '').replace(/_/g, ' ');
const ROLLING = /^(next|last)_(\d{1,4})_days$/;
const PRESETS = [['today', 'today'], ['this_week', 'this week'], ['this_month', 'this month'], ['last_month', 'last month'],
  ['next_month', 'next month'], ['this_quarter', 'this quarter'], ['this_year', 'this year']];
const HOW = [['next', 'within the next'], ['last', 'within the last']].concat(PRESETS).concat([['between', 'between'], ['before', 'before'], ['after', 'after']]);

// Plain names (16 Nov 2026, "make sure it is very user friendly").
export function dateName(attr) {
  const a = String(attr || '');
  if (/^sla_due(_at)?$/.test(a)) return 'SLA due date';
  return words(a.replace(/_(at|date|on)$/, '')) + ' date';
}
// What a status means, in plain words. The ontology's description also carries notes for the model ("USE THIS for …"),
// row counts and asides in brackets; none of that helps a person ticking a box.
export function stateHelp(c) {
  let t = String((c || {}).description || '').split(/\bUSE THIS\b/i)[0];
  t = t.replace(/\([^()]*\)/g, ' ').replace(/\([^)]*$/, ' ').replace(/_/g, ' ').replace(/\s+/g, ' ').replace(/[\s.,;:\u2014-]+$/, '').trim();
  const named = t.match(/^[\w\s/,]+?\s+-\s+(.+)$/);
  if (named) t = named[1];
  if (/\s\/\s/.test(t) && !/\s-\s/.test(t)) {
    const parts = t.split(/\s*\/\s*/);
    t = parts.length < 2 ? t : parts.slice(0, -1).join(', ') + ' or ' + parts[parts.length - 1];
  }
  t = t.replace(/\s=\s/g, ' is ');
  if (t.length > 90) t = t.slice(0, 90).replace(/\s+\S*$/, '') + '\u2026';
  return t;
}
export function stateOrder(fields, edit) {
  const on = new Set((edit || {}).states || []);
  const all = (fields || {}).state_choices || [];
  return all.filter((c) => on.has(c.id)).concat(all.filter((c) => !on.has(c.id)));
}
function both(list) {
  const w = list.map(words);
  return w.length < 2 ? (w[0] || '') : w.slice(0, -1).join(', ') + ' and ' + w[w.length - 1];
}
function either(list) {
  const w = list.map(words);
  return w.length < 2 ? (w[0] || '') : w.slice(0, -1).join(', ') + ' or ' + w[w.length - 1];
}
function whenText(d) {
  if (d.how === 'next' || d.how === 'last') return 'within the ' + d.how + ' ' + (parseInt(d.n, 10) || 30) + ' days';
  if (d.how === 'between') return 'between ' + (d.from || '…') + ' and ' + (d.to || '…');
  if (d.how === 'before' || d.how === 'after') return d.how + ' ' + (d.on || '…');
  return 'in ' + words(d.how);
}
// The query as one plain sentence: what, which ones, where, when.
export function describe(fields, edit) {
  const f = fields || {};
  const e = edit || editOf(f);
  const states = allStates(f, e.states) ? [] : (e.states || []);
  const dates = e.dates || [];
  // Most boxes ticked reads shorter as what is left out: "in any state except valid".
  const off = (f.state_choices || []).map((c) => c.id).filter((id) => !states.includes(id));
  const which = !states.length ? ' in any state'
    : states.length > off.length && off.length ? ' in any state except ' + both(off) : ' that are ' + either(states);
  return f.what + which + ', in ' + (e.building || 'every building')
    + (dates.length ? ', where ' + dates.map((d) => 'the ' + dateName(d.attribute) + ' is ' + whenText(d)).join(' and ') : ', with no date limit');
}

function editDate(d) {
  const x = d || {};
  const m = x.op === 'within' ? ROLLING.exec(String(x.value || '')) : null;
  if (m) return { attribute: x.attribute, how: m[1], n: m[2] };
  if (x.op === 'within') return { attribute: x.attribute, how: String(x.value || 'this_month') };
  if (x.op === 'between') return { attribute: x.attribute, how: 'between', from: (x.value || [])[0] || '', to: (x.value || [])[1] || '' };
  if (x.op === 'lt' || x.op === 'lte') return { attribute: x.attribute, how: 'before', on: String(x.value || '') };
  return { attribute: x.attribute, how: 'after', on: String(x.value || '') };
}

// The fields as the form holds them.
// Every state of the concept ticked is the same query as no state filter: "in any state".
function allStates(f, states) {
  const ids = ((f || {}).state_choices || []).map((c) => c.id);
  const on = new Set(states || []);
  return ids.length > 0 && ids.every((id) => on.has(id));
}
function noneTicked(f, states) {
  return ((f || {}).state_choices || []).length > 0 && !(states || []).length;
}
// The form starts as the query is: a query in any state has every box ticked, so unticking one narrows it.
export function editOf(fields) {
  const f = fields || {};
  const states = (f.states || []).length ? f.states.slice() : (f.state_choices || []).map((c) => c.id);
  return { states, building: f.building || '', dates: (f.dates || []).map(editDate) };
}

// The form as the request POST /lab/turns/{id}/expected/query takes.
// The number of the query shown (the first one listed), which the service edits and reads again.
export function planIndex(query) {
  const first = ((query || {}).plans || [])[0];
  return first && typeof first.index === 'number' ? first.index : 0;
}

export function bodyOf(index, edit, fields) {
  const e = edit || {};
  const dates = (e.dates || []).map((d) => {
    if (d.how === 'next' || d.how === 'last') return { attribute: d.attribute, op: 'within', value: d.how + '_' + (parseInt(d.n, 10) || 30) + '_days' };
    if (d.how === 'between') return { attribute: d.attribute, op: 'between', value: [d.from || '', d.to || ''] };
    if (d.how === 'before') return { attribute: d.attribute, op: 'lt', value: d.on || '' };
    if (d.how === 'after') return { attribute: d.attribute, op: 'gt', value: d.on || '' };
    return { attribute: d.attribute, op: 'within', value: d.how };
  });
  return { index: index || 0, states: allStates(fields, e.states) ? [] : (e.states || []).slice(), building: e.building || '', dates };
}

// What Use as the expected answer saves: the corrected values with their receipts and what was changed.
export function correctedBody(result) {
  const r = result || {};
  return { expected: r.sentence || '', evidence: { drafted_from: 'answer_key', source: 'corrected', changes: r.changes || [],
    facts: r.facts || [], receipts: r.receipts || [], read_at: r.read_at || null, plan: r.plan || null, original: r.original || null } };
}

function DateRow({ d, attributes, onChange, onRemove }) {
  const set = (change) => onChange(Object.assign({}, d, change));
  return (
    <div className="lab-row lab-xq-date">
      <select aria-label="Which date" value={d.attribute} onChange={(e) => set({ attribute: e.target.value })}>
        {attributes.map((a) => <option key={a} value={a}>{dateName(a)}</option>)}
      </select>
      <select aria-label="When" value={d.how} onChange={(e) => set({ how: e.target.value })}>
        {HOW.map(([k, t]) => <option key={k} value={k}>{t}</option>)}
      </select>
      {d.how === 'next' || d.how === 'last' ? <><input className="lab-input lab-xq-n" type="number" min="1" max="3660" aria-label="Number of days"
        value={d.n || ''} onChange={(e) => set({ n: e.target.value })} /><span>{'days'}</span></> : null}
      {d.how === 'between' ? <><input className="lab-input" type="date" aria-label="From" value={d.from || ''} onChange={(e) => set({ from: e.target.value })} />
        <span>{'and'}</span><input className="lab-input" type="date" aria-label="To" value={d.to || ''} onChange={(e) => set({ to: e.target.value })} /></> : null}
      {d.how === 'before' || d.how === 'after' ? <input className="lab-input" type="date" aria-label="Date" value={d.on || ''} onChange={(e) => set({ on: e.target.value })} /> : null}
      <button type="button" className="lab-link" onClick={onRemove} aria-label={'Remove the ' + dateName(d.attribute) + ' condition'}>{'Remove'}</button>
    </div>
  );
}

export function QueryFix({ query, edit, onEdit, onRun, busy, error, result, onUse, openFact, onOpenFact, onCopy, copied, onHide }) {
  if (!query) return <p className="lab-small" role="status">{'Reading Hoistra’s query…'}</p>;
  const q = query || {};
  if (!q.ready) return <p className="lab-small">{'Hoistra’s map of the records is not ready yet. Try again in a few minutes.'}</p>;
  const plan = (q.plans || [])[0];
  if (!plan) return <p className="lab-small">{'Hoistra did not read the records with a query for this question, so there is nothing to correct here.'}</p>;
  const f = plan.fields;
  const e = edit || editOf(f);
  const set = (change) => onEdit && onEdit(Object.assign({}, e, change));
  const resultData = result && result.found ? { kind: 'answer_key', expected: { evidence: result } } : null;
  const fact = resultData && openFact ? (result.facts || []).find((x) => x.id === openFact) : null;
  return (
    <div className="lab-xq">
      <div className="lab-row lab-xq-top">
        <ol className="lab-xq-steps">
        <li>{q.source === 'answer_key' ? '1. Check what the question asks for' : '1. Check what Hoistra looked for'}</li><li>{'2. Change what is wrong'}</li><li>{'3. Run it'}</li><li>{'4. Use it'}</li>
        </ol>
        {onHide ? <button type="button" className="lab-link lab-xq-hide" aria-expanded="true" onClick={onHide}>{'Hide'}</button> : null}
      </div>
      {q.source === 'answer_key' ? <p className="lab-small">{'Hoistra answered this with another tool, so here is the same question as a records query.'}</p> : null}
      <p className="lab-xp-plain">{q.source === 'answer_key' ? 'The question asks for ' : 'Hoistra looked for '}<strong>{describe(f, editOf(f))}</strong>{'.'}</p>
      {describe(f, e) !== describe(f, editOf(f)) ? <p className="lab-xp-plain lab-xq-yours">{'Your version: '}<strong>{describe(f, e)}</strong>{'.'}</p> : null}
      <fieldset className="lab-xq-group"><legend>{'Which ones'}</legend>
        <div className="lab-xq-states">
          {stateOrder(f, editOf(f)).map((c) => (
            <label key={c.id} className="lab-xq-state">
              <input type="checkbox" aria-label={words(c.id)} checked={(e.states || []).includes(c.id)}
                onChange={(ev) => set({ states: ev.target.checked ? (e.states || []).concat([c.id]) : (e.states || []).filter((x) => x !== c.id) })} />
              <span><strong>{words(c.id)}</strong>{stateHelp(c) ? <span className="lab-small">{' — ' + stateHelp(c)}</span> : null}</span>
            </label>
          ))}
        </div>
      </fieldset>
      <label className="lab-field lab-xq-building"><span>{'Building'}</span>
        <input className="lab-input" value={e.building || ''} placeholder="every building" list="lab-xq-buildings"
          onChange={(ev) => set({ building: ev.target.value })} /></label>
      <datalist id="lab-xq-buildings">{(q.buildings || []).map((b) => <option key={b} value={b} />)}</datalist>
      {(f.date_attributes || []).length ? (
        <fieldset className="lab-xq-group"><legend>{'Dates'}</legend>
          {(e.dates || []).map((d, i) => (
            <DateRow key={i} d={d} attributes={f.date_attributes} onChange={(nd) => set({ dates: e.dates.map((x, j) => (j === i ? nd : x)) })}
              onRemove={() => set({ dates: e.dates.filter((x, j) => j !== i) })} />
          ))}
          <button type="button" className="lab-link" onClick={() => set({ dates: (e.dates || []).concat([{ attribute: f.date_attributes[0], how: 'next', n: '30' }]) })}>{'Add a date'}</button>
        </fieldset>
      ) : null}
      <div className="lab-row">
        <button type="button" className="lab-btn lab-btn-sm" disabled={!!busy || noneTicked(f, e.states)} onClick={() => onRun && onRun()}>{busy ? 'Reading…' : 'Run with my changes'}</button>
        <button type="button" className="lab-btn lab-btn-quiet lab-btn-sm" disabled={!!busy} onClick={() => onEdit && onEdit(editOf(f))}>{'Start again'}</button>
      </div>
      {noneTicked(f, e.states) ? <p className="lab-small">{'Tick at least one state. All of them ticked means any state.'}</p> : null}
      {error ? <div role="alert" className="lab-error">{error}</div> : null}
      {result ? (
        <div className="lab-xq-result" role="status">
          {result.before && result.before.found ? (
            <div className="lab-xq-before"><span className="lab-xp-h">{q.source === 'answer_key' ? 'The question as asked finds' : 'Hoistra\u2019s query found'}</span><p>{result.before.sentence}</p></div>
          ) : null}
          <span className="lab-xp-h">{'Your version finds'}</span>
          {result.found ? <p className="lab-ses-pre">{result.sentence}</p> : <p>{'The records do not hold ' + (result.unresolved || []).join(', ') + '. Check the spelling of the building.'}</p>}
          {(result.changes || []).length ? <ul className="lab-small lab-xq-changes">{result.changes.map((c) => <li key={c}>{c}</li>)}</ul> : null}
          {resultData ? <KeyValues data={resultData} openFact={openFact} onOpen={onOpenFact} marks={{}} /> : null}
          {fact ? <Receipt fact={fact} receipt={receiptFor(result, fact)} readAt={result.read_at}
            copied={copied} onCopy={onCopy} onClose={() => onOpenFact && onOpenFact(null)} /> : null}
          {result.found ? <div className="lab-row"><button type="button" className="lab-btn lab-btn-sm" disabled={!!busy} onClick={() => onUse && onUse()}>{'Use as the expected answer'}</button></div> : null}
        </div>
      ) : null}
    </div>
  );
}

// The panel, from what GET /lab/turns/{id}/expected returned. Nothing here reads the service.
export function ExpectedView({ data, busy, error, editing, draftText, onAccept, onEdit, onSave, onCancel, onChange,
  openFact, onOpenFact, marks, onMark, rerun, rerunBusy, onRerun, copied, onCopy, onSaveChecks }) {
  const d = data || null;
  if (!d || !d.question || !d.live) return null;
  const e = d.expected;
  const keyed = isKey(d);
  const ev = evidenceOf(d);
  const fact = keyed && openFact ? (ev.facts || []).find((f) => f.id === openFact) : null;
  const receipt = fact ? receiptFor(ev, fact) : null;
  const marked = Object.keys(marks || {}).some((k) => (marks[k] || {}).check);
  return (
    <section className="lab-ses-expected" aria-label="Expected answer">
      <span className="lab-qp-label">{'Expected answer'}</span>
      {editing ? (
        <div className="lab-col">
          <textarea className="lab-note" rows={4} value={draftText || ''} aria-label="Expected answer"
            onChange={(ev2) => onChange && onChange(ev2.target.value)} disabled={!!busy} />
          <div className="lab-row">
            <button type="button" className="lab-btn lab-btn-sm" disabled={!!busy || !String(draftText || '').trim()}
              onClick={() => onSave && onSave()}>{busy ? 'Saving…' : 'Save expected answer'}</button>
            <button type="button" className="lab-btn lab-btn-quiet lab-btn-sm" disabled={!!busy} onClick={() => onCancel && onCancel()}>{'Cancel'}</button>
          </div>
        </div>
      ) : e ? (
        <>
          <p className="lab-ses-pre">{e.expected}</p>
          {keyed ? <KeyValues data={d} openFact={openFact} onOpen={onOpenFact} marks={marks} /> : null}
          {keyed && fact ? (
            <Receipt fact={fact} receipt={receipt} readAt={ev.read_at} mark={(marks || {})[fact.id] || (fact.check ? fact : null)} rerun={rerun}
              rerunBusy={rerunBusy} copied={copied} busy={busy} onCopy={onCopy} onRerun={ev.key ? onRerun : undefined}
              onMark={(change) => onMark && onMark(fact.id, change)} onClose={() => onOpenFact && onOpenFact(null)} />
          ) : null}
          <div className="lab-row">
            <span className="lab-small">{whereFrom(d)}</span>
            {d.draft ? <button type="button" className="lab-btn lab-btn-sm" disabled={!!busy} onClick={() => onAccept && onAccept()}>{busy ? 'Saving…' : 'Accept'}</button> : null}
            {keyed && marked ? <button type="button" className="lab-btn lab-btn-sm" disabled={!!busy} onClick={() => onSaveChecks && onSaveChecks()}>{busy ? 'Saving…' : 'Save my checks'}</button> : null}
            <button type="button" className="lab-btn lab-btn-quiet lab-btn-sm" disabled={!!busy} onClick={() => onEdit && onEdit()}>{'Edit'}</button>
          </div>
        </>
      ) : (
        <div className="lab-row">
          <span className="lab-small">{'No expected answer yet.'}</span>
          <button type="button" className="lab-btn lab-btn-quiet lab-btn-sm" onClick={() => onEdit && onEdit()}>{'Add expected answer'}</button>
        </div>
      )}
      {error ? <div role="alert" className="lab-error">{error}</div> : null}
    </section>
  );
}

// Reads the turn's expected answer; saves Accept, Edit and the checks (PUT /lab/q/{code}/expected); Run again now
// reads the values afresh (POST /lab/turns/{id}/expected/check, nothing stored).
export default function TurnExpected({ turnId }) {
  const [data, setData] = React.useState(null);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState('');
  const [editing, setEditing] = React.useState(false);
  const [text, setText] = React.useState('');
  const [openFact, setOpenFact] = React.useState(null);
  const [marks, setMarks] = React.useState({});
  const [rerun, setRerun] = React.useState(null);
  const [rerunBusy, setRerunBusy] = React.useState(false);
  const [copied, setCopied] = React.useState(false);
  const load = React.useCallback(() => {
    let live = true;
    labApi.turnExpected(turnId).then((out) => { if (live) setData(out || null); }, () => { if (live) setData(null); });
    return () => { live = false; };
  }, [turnId]);
  React.useEffect(() => load(), [load]);
  const save = async (body) => {
    if (!data || !data.question) return;
    setBusy(true); setError('');
    try {
      await labApi.setExpected(data.question, body);
      setEditing(false); setMarks({});
      setData(await labApi.turnExpected(turnId));
      return true;
    } catch (e) {
      setError('Could not save the expected answer: ' + errText(e));
      return false;
    } finally {
      setBusy(false);
    }
  };
  const runAgain = async () => {
    setRerunBusy(true); setError('');
    try { setRerun(await labApi.turnExpectedCheck(turnId)); } catch (e) { setError('Could not read the records again: ' + errText(e)); } finally { setRerunBusy(false); }
  };
  const copy = (textToCopy) => {
    try { navigator.clipboard.writeText(textToCopy).then(() => { setCopied(true); setTimeout(() => setCopied(false), 2000); }); } catch (e) { /* the text is on the page too */ }
  };
  const [fixOpen, setFixOpen] = React.useState(false);
  const [query, setQuery] = React.useState(null);
  const [edit, setEdit] = React.useState(null);
  const [fixed, setFixed] = React.useState(null);
  const [fixBusy, setFixBusy] = React.useState(false);
  const [fixError, setFixError] = React.useState('');
  const [fixFact, setFixFact] = React.useState(null);
  const openFix = async () => {
    setFixOpen(true); setFixError('');
    if (query) return;                          // shown again after Hide: keep the changes and the result
    try {
      const out = await labApi.turnExpectedQuery(turnId);
      setQuery(out); setEdit(out && out.plans && out.plans[0] ? editOf(out.plans[0].fields) : null);
    } catch (e) { setFixError('Could not read Hoistra’s query: ' + errText(e)); }
  };
  const runFix = async () => {
    setFixBusy(true); setFixError(''); setFixFact(null);
    try { setFixed(await labApi.turnExpectedTry(turnId, bodyOf(planIndex(query), edit, query && query.plans && query.plans[0] ? query.plans[0].fields : null))); } catch (e) { setFixError('Could not run it: ' + errText(e)); } finally { setFixBusy(false); }
  };
  if (!data || !data.question || !data.live) return null;
  return (
    <>
    <ExpectedView data={data} busy={busy} error={error} editing={editing} draftText={text} onChange={setText}
      onAccept={() => save(acceptBody(data))}
      onEdit={() => { setText(((data && data.expected) || {}).expected || ''); setEditing(true); setError(''); }}
      onSave={() => save({ expected: text })} onCancel={() => { setEditing(false); setError(''); }}
      openFact={openFact} onOpenFact={(id) => { setOpenFact(id); setCopied(false); }} marks={marks}
      onMark={(id, change) => setMarks((p) => Object.assign({}, p, { [id]: Object.assign({}, p[id] || {}, change) }))}
      rerun={rerun} rerunBusy={rerunBusy} onRerun={runAgain} copied={copied} onCopy={copy}
      onSaveChecks={() => save(checksBody(data, marks))} />
    <div className="lab-xq-wrap">
      {!fixOpen ? <button type="button" className="lab-link" aria-expanded="false" onClick={openFix}>{query ? 'Show the query check' : 'Check Hoistra’s query'}</button> : (
        <QueryFix query={query || (fixError ? { ready: true, plans: [] } : null)} edit={edit} onEdit={setEdit} onRun={runFix} busy={fixBusy || busy} error={fixError}
          result={fixed} onUse={() => save(correctedBody(fixed)).then((ok) => { if (ok) { setFixed(null); setFixOpen(false); } })}
          openFact={fixFact} onOpenFact={setFixFact} copied={copied} onCopy={copy} onHide={() => setFixOpen(false)} />
      )}
    </div>
    </>
  );
}
