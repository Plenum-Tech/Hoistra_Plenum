// Notes — every note written on the questions' steps (22 Oct 2026 feedback: notes are free text, so the Right / Wrong /
// Not sure tiles and the label column are gone). A filter bar (person, role, words, period) that applies as soon as a
// choice is made, and one row per note: when it was annotated, the question (its code, e.g. R3-12, and text) and step,
// the selected text, the note with what should happen and when, who wrote it with their role, "Open" to go to the
// question, and "Delete" on the person's own notes (a note written with other steps can go alone or with its group).
// Company admins, the platform team and the consultant see everyone's notes; everyone else their own. A CSV download
// of the same filter.
import React from 'react';
import { labApi } from './labApi.js';
import { ROLE_CHOICES, ROLE_TEXT, errText, labelView } from './logic/labelling.js';
import './labels.css';

const PAGE = 100;
const EMPTY = { reviewer: '', role: '', q: '', since: '', until: '' };
const ROLES = ROLE_CHOICES.concat([{ key: 'consultant', text: 'Consultant' }, { key: 'platform', text: 'Platform team' }]);
const ROLE = Object.assign({}, ROLE_TEXT, { consultant: 'Consultant' });

// Long text in a cell: three lines, then "Show all" opens the rest in place and "Show less" folds it again.
const LONG = 160;
export function Clamp({ text, children }) {
  const [open, setOpen] = React.useState(false);
  const long = text.length > LONG || text.split('\n').length > 3;
  if (!long) return children;
  return (
    <>
      <div className={open ? 'lab-clamp-open' : 'lab-clamp'}>{children}</div>
      <button type="button" className="lab-link lab-clamp-toggle" aria-expanded={open ? 'true' : 'false'} onClick={() => setOpen(!open)}>
        {open ? 'Show less' : 'Show all (' + text.length.toLocaleString('en-GB') + ' characters)'}
      </button>
    </>
  );
}

// Delete, asked once: a note on one step is deleted or kept; a note written with other steps can go alone or with them.
function DeleteCell({ x, confirming, busy, onAsk, onDelete }) {
  if (!confirming) {
    return <button type="button" className="lab-link lab-danger" onClick={() => onAsk(x.id)} disabled={busy}>{'Delete'}</button>;
  }
  return (
    <span className="lab-confirm" role="group" aria-label="Delete this note?">
      <span className="lab-small">{x.group_id ? 'Delete:' : 'Delete it?'}</span>
      <button type="button" className="lab-link lab-danger" onClick={() => onDelete(x, 'one')} disabled={busy}>{x.group_id ? 'This step' : 'Yes'}</button>
      {x.group_id ? <button type="button" className="lab-link lab-danger" onClick={() => onDelete(x, 'group')} disabled={busy}>{'Whole group'}</button> : null}
      <button type="button" className="lab-link" onClick={() => onAsk(null)} disabled={busy}>{x.group_id ? 'Cancel' : 'No'}</button>
    </span>
  );
}

export function NotesTable({ rows, myId, confirming, busy, onAsk, onDelete, onOpenQuestion }) {
  return (
    <div className="lab-table-wrap lab-table-full">
      <table className="lab-table">
        <thead><tr>{['Annotated at', 'Question and step', 'Selected text', 'Note', 'Who', ''].map((h, i) => <th key={i} scope="col">{h}</th>)}</tr></thead>
        <tbody>
          {(rows || []).map((x) => {
            const v = labelView(x, myId);
            return (
              <tr key={x.id}>
                <td className="lab-nowrap lab-muted">{v.when || '—'}</td>
                <td className="lab-cell-q">
                  <div className="lab-lv-qline">
                    {x.question_code ? <span className="lab-lv-code">{x.question_code}</span> : null}
                    <span className="lab-cell-main">{x.question || '—'}</span>
                  </div>
                  <div className="lab-small">{x.step_title || x.step_kind || ''}</div>
                  {x.group_id ? <span className="lab-tag lab-tag-group">{'Written with other steps'}</span> : null}
                </td>
                <td className="lab-cell-quote">{x.quote
                  ? <Clamp text={x.quote}><span className="lab-quote-inline">{'“' + x.quote + '”'}</span></Clamp>
                  : <span className="lab-muted">{'Whole step'}</span>}</td>
                <td className="lab-cell-note">
                  {v.note ? <Clamp text={v.note}>{v.note}</Clamp> : (v.expected || v.onlyIf ? null : <span className="lab-muted">{'—'}</span>)}
                  {v.expected ? <div className="lab-small lab-only-if"><strong>{'Should: '}</strong><Clamp text={v.expected}>{v.expected}</Clamp></div> : null}
                  {v.onlyIf ? <div className="lab-small lab-only-if"><strong>{'Only if: '}</strong>{v.onlyIf}</div> : null}
                </td>
                <td className="lab-cell-who">
                  <div className="lab-cell-main">{v.who}</div>
                  <div className="lab-small">{(ROLE[x.reviewer_role] || x.reviewer_role || '') + (v.edited ? ' · edited' : '')}</div>
                </td>
                <td className="lab-nowrap">
                  <div className="lab-cell-actions">
                    {onOpenQuestion && x.question_code ? (
                      <button type="button" className="lab-link" onClick={() => onOpenQuestion(x.question_code, { turn: x.turn_id, step: x.step_key })}
                        aria-label={'Open question ' + x.question_code}>{'Open'}</button>
                    ) : null}
                    {onDelete && myId && x.reviewer_id === myId ? (
                      <DeleteCell x={x} confirming={confirming === x.id} busy={busy} onAsk={onAsk} onDelete={onDelete} />
                    ) : null}
                  </div>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// Nothing to list: either nobody has written a note yet, or the filter finds nothing.
export function NoNotes({ filtered, everyone, onClear }) {
  return (
    <div className="lab-empty lab-empty-big">
      <h2>{filtered ? 'No notes match these filters' : 'No notes yet'}</h2>
      <p className="lab-small">{filtered ? 'Try a wider period, another person or role, or clear the filters.'
        : (everyone ? 'Notes appear here as soon as anyone writes one on a question’s steps.'
          : 'Your notes appear here as soon as you write one on a question’s steps.')}</p>
      {filtered ? <div className="lab-row lab-empty-actions"><button type="button" className="lab-btn lab-btn-quiet lab-btn-sm" onClick={onClear}>{'Clear filters'}</button></div> : null}
    </div>
  );
}

export default function Notes({ me, onOpenQuestion }) {
  const guess = !!(me && (me.manages || me.all_companies));
  const [f, setF] = React.useState(EMPTY);
  const [applied, setApplied] = React.useState(EMPTY);
  const [res, setRes] = React.useState({ rows: [], total: 0, everyone: guess, me: (me && me.user_id) || null, loading: true, error: '', offset: 0 });
  const [csvBusy, setCsvBusy] = React.useState(false);
  const [confirming, setConfirming] = React.useState(null);
  const [deleting, setDeleting] = React.useState(false);
  const ask = React.useRef(0);

  const query = (g, offset) => ({ reviewer: g.reviewer.trim() || undefined, role: g.role || undefined, q: g.q.trim() || undefined,
    since: g.since ? new Date(g.since + 'T00:00:00').toISOString() : undefined,
    until: g.until ? new Date(g.until + 'T23:59:59').toISOString() : undefined, limit: PAGE, offset });

  const load = async (g, offset) => {
    const mine = (ask.current += 1);
    setApplied(g);
    setRes((p) => Object.assign({}, p, { loading: true, error: '' }));
    try {
      const out = await labApi.all(query(g, offset));
      if (mine !== ask.current) return;
      setRes({ rows: (out && out.labels) || [], total: (out && out.total) || 0, everyone: !!(out && out.everyone),
        me: (out && out.me) || (me && me.user_id) || null, loading: false, error: '', offset });
    } catch (e) {
      if (mine === ask.current) setRes((p) => Object.assign({}, p, { loading: false, error: errText(e) }));
    }
  };
  React.useEffect(() => { load(EMPTY, 0); }, []);

  // A choice from a list or a date applies at once; typed words apply on Enter or on leaving the box.
  const pick = (k) => (e) => { const g = Object.assign({}, f, { [k]: e.target.value }); setF(g); load(g, 0); };
  const type = (k) => (e) => setF(Object.assign({}, f, { [k]: e.target.value }));
  const commit = () => { if (f.q !== applied.q || f.reviewer !== applied.reviewer) load(f, 0); };
  const clear = () => { setF(EMPTY); load(EMPTY, 0); };
  const filtered = Object.keys(EMPTY).some((k) => (applied[k] || '').trim());

  // Delete one of my notes, or every note of its group (in the note's own company); then read the same page again.
  const onDelete = async (x, scope) => {
    setDeleting(true);
    try {
      if (scope === 'group' && x.group_id) await labApi.clearGroup(x.turn_id, x.group_id, x.organization_id);
      else await labApi.clearLabel(x.turn_id, x.lineage_id || x.id, x.id, x.organization_id);
      setConfirming(null);
      const offset = res.rows.length === 1 && res.offset > 0 ? Math.max(0, res.offset - PAGE) : res.offset;
      await load(applied, offset);
    } catch (e) {
      setRes((p) => Object.assign({}, p, { error: e && e.status === 409 ? 'This note changed in another window. The list has been read again.' : errText(e) }));
      setConfirming(null);
      load(applied, res.offset);
    } finally {
      setDeleting(false);
    }
  };

  const download = async () => {
    setCsvBusy(true);
    try {
      const out = await labApi.csv(Object.assign(query(applied, 0), { limit: undefined, offset: undefined }));
      if (out && out.blob && typeof URL !== 'undefined' && URL.createObjectURL) {
        const url = URL.createObjectURL(out.blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = out.filename || 'trace-lab-notes.csv';
        document.body.appendChild(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
      }
    } catch (e) {
      setRes((p) => Object.assign({}, p, { error: errText(e) }));
    } finally {
      setCsvBusy(false);
    }
  };

  const last = Math.min(res.offset + PAGE, res.total);
  const wide = !!(me && me.all_companies && !me.organization_id);
  return (
    <div className="lab-page-body lab-page-full">
      <div className="lab-page-head lab-head-row">
        <div>
          <h1>{'Notes'}</h1>
          <p className="lab-small lab-big">{res.everyone ? 'Every note, with who wrote it and when.' + (wide ? ' Across every company.' : '')
            : 'Your notes, with when you wrote them.'}</p>
        </div>
        <button type="button" className="lab-btn lab-btn-quiet lab-btn-sm" onClick={download} disabled={csvBusy || !res.total}>
          {csvBusy ? 'Preparing…' : 'Download CSV'}
        </button>
      </div>
      <form className="lab-filterbar" onSubmit={(e) => { e.preventDefault(); load(f, 0); }}>
        {res.everyone ? (
          <label className="lab-field"><span>{'Person'}</span>
            <input type="search" value={f.reviewer} onChange={type('reviewer')} onBlur={commit} placeholder="Email, then Enter" />
          </label>
        ) : null}
        <label className="lab-field"><span>{'Role'}</span>
          <select value={f.role} onChange={pick('role')}>
            <option value="">{'Any role'}</option>
            {ROLES.map((r) => <option key={r.key} value={r.key}>{r.text}</option>)}
          </select>
        </label>
        <label className="lab-field lab-field-grow"><span>{'Words'}</span>
          <input type="search" value={f.q} onChange={type('q')} onBlur={commit} placeholder="In the question, step, selected text or note, then Enter" />
        </label>
        <label className="lab-field"><span>{'From'}</span><input type="date" value={f.since} onChange={pick('since')} /></label>
        <label className="lab-field"><span>{'To'}</span><input type="date" value={f.until} onChange={pick('until')} /></label>
        <button type="submit" className="lab-visually-hidden" tabIndex={-1}>{'Apply'}</button>
        {filtered ? <button type="button" className="lab-link lab-clear" onClick={clear}>{'Clear filters'}</button> : null}
      </form>
      {res.error ? <div role="alert" className="lab-error">{res.error}</div> : null}
      {res.loading && !res.rows.length ? <p className="lab-small">{'Reading…'}</p> : null}
      {!res.loading && !res.error && !res.rows.length ? <NoNotes filtered={filtered} everyone={res.everyone} onClear={clear} /> : null}
      {res.rows.length ? (
        <>
          <p className="lab-small" aria-live="polite">{res.loading ? 'Reading…' : 'Showing ' + (res.offset + 1) + '–' + last + ' of ' + res.total.toLocaleString('en-GB')}</p>
          <NotesTable rows={res.rows} myId={res.me} confirming={confirming} busy={deleting} onAsk={setConfirming} onDelete={onDelete}
            onOpenQuestion={onOpenQuestion} />
        </>
      ) : null}
      {res.total > PAGE ? (
        <div className="lab-row">
          <button type="button" className="lab-btn lab-btn-quiet lab-btn-sm" disabled={res.offset === 0 || res.loading} onClick={() => load(applied, Math.max(0, res.offset - PAGE))}>{'Previous'}</button>
          <button type="button" className="lab-btn lab-btn-quiet lab-btn-sm" disabled={last >= res.total || res.loading} onClick={() => load(applied, res.offset + PAGE)}>{'Next'}</button>
        </div>
      ) : null}
    </div>
  );
}
