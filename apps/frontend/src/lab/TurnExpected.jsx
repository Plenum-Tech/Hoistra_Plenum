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
//
// Values read from the records, with their source (19 Nov 2026): every live question's draft holds exact values read by
// each query Hoistra made for it (or Trace Lab's fixed query, or one it proposed). The page shows one block per query -
// where it came from, whose access it was read with, the sentence, a button per value, how the query was read - and a
// value opens its own receipt. See logic/recordsValues.js.
//
// The query editor (19 Nov 2026): "Check and change the query" opens an editor over every part of the records query
// (what is read, its name or code, states, conditions, limits, grouping, linked records), runs the changed query, shows
// what Hoistra's query found beside what the person's finds, and saves the person's version as the expected answer. See
// QueryEditor.jsx and logic/queryForm.js. Only curators and the platform team may run a query (`can_run`).
import React from 'react';
import { labApi } from './labApi.js';
import { errText } from './logic/labelling.js';
import { blocksOf, scopeWords, changedWords, factKey, findFact, receiptOf, explainOf } from './logic/recordsValues.js';
import { usedBody, noQueryWords } from './logic/queryForm.js';
import { Receipt, factText, columnsOf, when } from './Receipt.jsx';
import QueryEditor from './QueryEditor.jsx';
import './turnExpected.css';

export { Receipt, factText, columnsOf };

const evidenceOf = (d) => (((d || {}).expected || {}).evidence) || {};
const isKey = (d) => !!d && d.kind === 'answer_key' && Array.isArray(evidenceOf(d).facts);
const isRecords = (d) => !!d && d.kind === 'records' && Array.isArray(evidenceOf(d).queries);

// What Accept saves: the draft's words as the person's own expected answer, with what it was drawn from.
export function acceptBody(data) {
  const d = data || {};
  if (isRecords(d)) return { expected: ((d.expected || {}).expected) || '', evidence: Object.assign({}, evidenceOf(d), { drafted_from: 'records' }) };
  if (isKey(d)) return { expected: ((d.expected || {}).expected) || '', evidence: Object.assign({}, evidenceOf(d), { drafted_from: 'answer_key' }) };
  const rule = (d.rules || [])[0];
  const evidence = Object.assign(rule ? { spec_rule: rule } : {}, { drafted_from: 'SPEC.md', spec_version: d.spec_version });
  return { expected: ((d.expected || {}).expected) || '', evidence };
}

// What Save my checks saves: the same answer, each value marked right or wrong (with what it should be and why).
function withMark(f, m) {
  if (!m || !m.check) return f;
  const out = Object.assign({}, f, { check: m.check });
  if (m.check === 'wrong' && String(m.corrected || '').trim()) out.corrected = String(m.corrected).trim();
  if (m.check === 'wrong' && String(m.note || '').trim()) out.note = String(m.note).trim();
  return out;
}
export function checksBody(data, marks) {
  const ev = evidenceOf(data);
  const expected = ((data || {}).expected || {}).expected || '';
  if (isRecords(data)) {          // values of different queries share ids: each is marked under its query's number
    const queries = ev.queries.map((q, i) => Object.assign({}, q, { facts: ((q || {}).facts || []).map((f) => withMark(f, (marks || {})[i + ':' + f.id])) }));
    return { expected, evidence: Object.assign({}, ev, { drafted_from: 'records', queries }) };
  }
  const facts = (ev.facts || []).map((f) => withMark(f, (marks || {})[f.id]));
  return { expected, evidence: Object.assign({}, ev, { drafted_from: 'answer_key', facts }) };
}

function whereFrom(d) {
  const e = d.expected || {};
  if (isRecords(d)) {
    const ev = evidenceOf(d);
    if (d.draft) return 'Read from the records' + (when(ev.read_at) ? ' at ' + when(ev.read_at) : '') + ' · not accepted yet';
    if (blocksOf(ev).some((b) => b.source === 'person')) return ['Changed query used as the expected answer', e.author_email].filter(Boolean).join(' · ');
    if (blocksOf(ev).some((b) => b.facts.some((f) => f.check))) return 'Checked by ' + (e.author_email || 'a person');
    return ['Accepted from the records', e.author_email].filter(Boolean).join(' · ');
  }
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

// One value of a block: a button that opens how it was found, or - with no query behind it - the value and a plain note.
function RecordChip({ block, fact, openKey, onOpen, marks }) {
  const key = factKey(block, fact);
  const m = (marks || {})[key] || (fact.check ? { check: fact.check, corrected: fact.corrected } : null);
  const tail = m && m.check === 'right' ? ' · Right' : m && m.check === 'wrong' ? ' · Wrong' + (m.corrected ? ', should be ' + m.corrected : '') : '';
  const shown = fact.kind === 'missing' ? <strong>{factText(fact)}</strong>
    : <>{fact.label ? <span className="lab-xp-k">{fact.label}</span> : null}<strong>{String(fact.value)}</strong></>;
  if (!receiptOf(block, fact)) {
    return <span className="lab-xp-chip lab-xp-static">{shown}{tail}<span className="lab-small lab-xp-nolink">{'no query to show'}</span></span>;
  }
  return (
    <button type="button" aria-label={'See how ' + factText(fact) + ' was found'} aria-expanded={openKey === key ? 'true' : 'false'}
      className={'lab-xp-chip' + (openKey === key ? ' lab-on' : '') + (m && m.check ? ' lab-xp-' + m.check : '')}
      onClick={() => onOpen && onOpen(openKey === key ? null : key)}>
      {shown}{tail}
    </button>
  );
}

// The values of a records draft: one block per query, then the receipt of the value that is open.
function RecordValues({ blocks, openKey, onOpen, marks, onMark, rerun, rerunBusy, onRerun, copied, busy, onCopy }) {
  const open = findFact(blocks, openKey);
  return (
    <div className="lab-xp-values">
      <p className="lab-small">{'Click a value to see exactly how it was found.'}</p>
      {blocks.map((b) => {
        const here = open && open.block.index === b.index ? open : null;
        return (
          <div key={b.index} className="lab-xp-query">
            <div className="lab-row lab-xp-src">
              {b.sourceWords ? <strong className={'lab-xp-source' + (b.source === 'proposed' ? ' lab-xp-proposed' : '')}>{b.sourceWords}</strong> : null}
              {scopeWords(b) ? <span className="lab-small">{scopeWords(b)}</span> : null}
              {when(b.readAt) ? <span className="lab-small">{'Read ' + when(b.readAt)}</span> : null}
            </div>
            {changedWords(b) ? <p className="lab-small lab-xp-changed" role="status">{changedWords(b)}</p> : null}
            <p className="lab-ses-pre">{b.sentence}</p>
            <div className="lab-xp-chips">
              {b.facts.map((f) => <RecordChip key={f.id} block={b} fact={f} openKey={openKey} onOpen={onOpen} marks={marks} />)}
            </div>
            {here && here.receipt ? (
              <Receipt fact={here.fact} receipt={here.receipt} readAt={b.readAt} explain={explainOf(here.fact)}
                mark={(marks || {})[openKey] || (here.fact.check ? here.fact : null)} rerun={rerun && rerun.index === b.index ? rerun : null}
                rerunBusy={rerunBusy} copied={copied} busy={busy} onCopy={onCopy} onRerun={b.rerunnable && onRerun ? () => onRerun(b.index) : undefined}
                onMark={onMark ? (change) => onMark(openKey, change) : undefined} onClose={() => onOpen && onOpen(null)} />
            ) : null}
            {b.notes.length ? (
              <details className="lab-xp-block lab-xp-tech">
                <summary>{'How the query was read'}</summary>
                <ul className="lab-small lab-xp-notes">{b.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>
              </details>
            ) : null}
          </div>
        );
      })}
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
  const records = isRecords(d);
  const ev = evidenceOf(d);
  const blocks = records ? blocksOf(ev) : [];
  const fact = keyed && openFact ? (ev.facts || []).find((f) => f.id === openFact) : null;
  const receipt = fact ? receiptFor(ev, fact) : null;
  const marked = Object.keys(marks || {}).some((k) => (marks[k] || {}).check);
  // A draft from SPEC.md holds no values when no query could be made for the question; the page says why (Ruling 18).
  const noQuery = d.draft && !records && !keyed && typeof ev.no_query === 'string' && ev.no_query ? ev.no_query : '';
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
          {records && blocks.length && blocks.map((b) => b.sentence).join(' ') === e.expected ? null : <p className="lab-ses-pre">{e.expected}</p>}
          {records && blocks.length ? (
            <RecordValues blocks={blocks} openKey={openFact} onOpen={onOpenFact} marks={marks} onMark={onMark} rerun={rerun} rerunBusy={rerunBusy}
              onRerun={onRerun} copied={copied} busy={busy} onCopy={onCopy} />
          ) : null}
          {keyed ? <KeyValues data={d} openFact={openFact} onOpen={onOpenFact} marks={marks} /> : null}
          {keyed && fact ? (
            <Receipt fact={fact} receipt={receipt} readAt={ev.read_at} mark={(marks || {})[fact.id] || (fact.check ? fact : null)} rerun={rerun}
              rerunBusy={rerunBusy} copied={copied} busy={busy} onCopy={onCopy} onRerun={ev.key ? onRerun : undefined}
              onMark={(change) => onMark && onMark(fact.id, change)} onClose={() => onOpenFact && onOpenFact(null)} />
          ) : null}
          <div className="lab-row">
            <span className="lab-small">{whereFrom(d)}</span>
            {d.draft ? <button type="button" className="lab-btn lab-btn-sm" disabled={!!busy} onClick={() => onAccept && onAccept()}>{busy ? 'Saving…' : 'Accept'}</button> : null}
            {(keyed || records) && marked ? <button type="button" className="lab-btn lab-btn-sm" disabled={!!busy} onClick={() => onSaveChecks && onSaveChecks()}>{busy ? 'Saving…' : 'Save my checks'}</button> : null}
            <button type="button" className="lab-btn lab-btn-quiet lab-btn-sm" disabled={!!busy} onClick={() => onEdit && onEdit()}>{'Edit'}</button>
          </div>
          {noQuery ? <p className="lab-small lab-xe-noquery">{noQueryWords(noQuery)}</p> : null}
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
  const runAgain = async (index) => {   // a records draft reads one of its queries again (by number); the answer key reads its one
    setRerunBusy(true); setError('');
    try {
      const out = await labApi.turnExpectedCheck(turnId, index);
      setRerun(typeof index === 'number' ? Object.assign({}, out, { index }) : out);
    } catch (e) { setError('Could not read the records again: ' + errText(e)); } finally { setRerunBusy(false); }
  };
  const copy = (textToCopy) => {
    try { navigator.clipboard.writeText(textToCopy).then(() => { setCopied(true); setTimeout(() => setCopied(false), 2000); }); } catch (e) { /* the text is on the page too */ }
  };
  const [fixOpen, setFixOpen] = React.useState(false);
  const [query, setQuery] = React.useState(null);
  const [fixError, setFixError] = React.useState('');
  const openFix = async () => {
    setFixOpen(true); setFixError('');
    if (query) return;                          // shown again after Hide: keep the changes and the results
    try { setQuery(await labApi.turnExpectedQuery(turnId)); } catch (e) { setFixError('Could not read the query: ' + errText(e)); }
  };
  // The editor runs and shows its own errors (the words of a query the records engine refuses go beside the fields).
  const runFix = (body) => labApi.turnExpectedTry(turnId, body);
  const useFix = async ({ index, plan, result }) => {
    const ok = await save(usedBody({ evidence: evidenceOf(data), queries: (query && query.queries) || [], index, plan, result }));
    if (ok) { setFixOpen(false); setQuery(null); }   // read again next time: the query shown is now the person's
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
      {!fixOpen ? <button type="button" className="lab-link" aria-expanded="false" onClick={openFix}>{'Check and change the query'}</button> : null}
      {fixOpen || query ? (   // folded away, not thrown away: the changes and the results are still there when it is opened again
        <div hidden={!fixOpen}>
          <QueryEditor query={query} error={fixError} onRun={runFix} onUse={useFix} saving={busy} copied={copied} onCopy={copy} onHide={() => setFixOpen(false)} />
        </div>
      ) : null}
    </div>
    </>
  );
}
