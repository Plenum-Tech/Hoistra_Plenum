// AllLabels — every label collected, across the full page: count tiles that double as the label filter (All, Right, Wrong,
// Not sure), a filter bar (person, role, words, period) that applies as soon as a choice is made, and one table row per
// label: when, which question and step, the selected text, the label and note (with what should happen and when), who gave it
// with their Trace Lab role, "Open" to jump to that step on Questions, and "Delete" on the person's own labels (a label
// given with other steps can be deleted alone or with its group). Admins see everyone's labels; everyone else sees their
// own. A CSV download of the same filter (up to 500 rows).
import React from 'react';
import { labApi } from './labApi.js';
import { LABEL_CHOICES, ROLE_CHOICES, errText, labelView } from './logic/labelling.js';

const PAGE = 100;
const EMPTY = { reviewer: '', role: '', label: '', q: '', since: '', until: '' };

// The tiles: how many labels the filter finds, split by label. Choosing one filters the table to it.
export function LabelTiles({ total, byLabel, current, onPick }) {
  const tiles = [{ key: '', text: 'All labels', n: total }].concat(LABEL_CHOICES.map((c) => ({ key: c.key, text: c.text, n: (byLabel || {})[c.key] || 0 })));
  return (
    <div className="lab-tiles" role="radiogroup" aria-label="Show labels">
      {tiles.map((t) => (
        <button key={t.key || 'all'} type="button" role="radio" aria-checked={current === t.key ? 'true' : 'false'}
          className={'lab-tile lab-tile-' + (t.key || 'all') + (current === t.key ? ' lab-on' : '')} onClick={() => onPick(t.key)}>
          <span className="lab-tile-n">{t.n.toLocaleString('en-GB')}</span>
          <span className="lab-tile-text">{t.text}</span>
        </button>
      ))}
    </div>
  );
}

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

// Delete, asked once: a label on one step is deleted or kept; a label given with other steps can go alone or with them.
function DeleteCell({ x, confirming, busy, onAsk, onDelete }) {
  if (!confirming) {
    return <button type="button" className="lab-link lab-danger" onClick={() => onAsk(x.id)} disabled={busy}>{'Delete'}</button>;
  }
  return (
    <span className="lab-confirm" role="group" aria-label="Delete this label?">
      <span className="lab-small">{x.group_id ? 'Delete:' : 'Delete it?'}</span>
      <button type="button" className="lab-link lab-danger" onClick={() => onDelete(x, 'one')} disabled={busy}>{x.group_id ? 'This step' : 'Yes'}</button>
      {x.group_id ? <button type="button" className="lab-link lab-danger" onClick={() => onDelete(x, 'group')} disabled={busy}>{'Whole group'}</button> : null}
      <button type="button" className="lab-link" onClick={() => onAsk(null)} disabled={busy}>{x.group_id ? 'Cancel' : 'No'}</button>
    </span>
  );
}

export function LabelsTable({ rows, onOpen, me, confirming, busy, onAsk, onDelete }) {
  return (
    <div className="lab-table-wrap lab-table-full">
      <table className="lab-table">
        <thead><tr>{['When', 'Question and step', 'Selected text', 'Label', 'Note', 'Who', ''].map((h, i) => <th key={i} scope="col">{h}</th>)}</tr></thead>
        <tbody>
          {rows.map((x) => {
            const v = labelView(x);
            return (
              <tr key={x.id}>
                <td className="lab-nowrap lab-muted">{v.when}</td>
                <td className="lab-cell-q">
                  <div className="lab-cell-main">{x.question || '—'}</div>
                  <div className="lab-small">{x.step_title || x.step_kind}</div>
                  {x.group_id ? <span className="lab-tag lab-tag-group">{'Labelled with other steps'}</span> : null}
                </td>
                <td className="lab-cell-quote">{x.quote
                  ? <Clamp text={x.quote}><span className="lab-quote-inline">{'“' + x.quote + '”'}</span></Clamp>
                  : <span className="lab-muted">{'Whole step'}</span>}</td>
                <td><span className={'lab-tag lab-tag-' + x.label}>{v.labelText}</span></td>
                <td className="lab-cell-note">
                  {v.note ? <Clamp text={v.note}>{v.note}</Clamp> : (v.expected || v.onlyIf ? null : <span className="lab-muted">{'—'}</span>)}
                  {v.expected ? <div className="lab-small lab-only-if"><strong>{'Should: '}</strong><Clamp text={v.expected}>{v.expected}</Clamp></div> : null}
                  {v.onlyIf ? <div className="lab-small lab-only-if"><strong>{'Only if: '}</strong>{v.onlyIf}</div> : null}
                </td>
                <td className="lab-cell-who">
                  <div className="lab-cell-main">{v.who}</div>
                  <div className="lab-small">{v.role + (v.edited ? ' · edited' : '')}</div>
                </td>
                <td className="lab-nowrap">
                  <div className="lab-cell-actions">
                    {onOpen && x.conversation_key ? <button type="button" className="lab-link" onClick={() => onOpen(x)}
                      aria-label={'Open the step for ' + (x.question || 'this label')}>{'Open'}</button> : null}
                    {onDelete && me && x.reviewer_id === me ? <DeleteCell x={x} confirming={confirming === x.id} busy={busy} onAsk={onAsk}
                      onDelete={onDelete} /> : null}
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

// Nothing to list: either nobody has labelled anything yet, or the filter finds nothing.
export function NoLabels({ filtered, everyone, onClear, onQuestions }) {
  return (
    <div className="lab-empty lab-empty-big">
      <h2>{filtered ? 'No labels match these filters' : 'No labels yet'}</h2>
      <p className="lab-small">{filtered ? 'Try a wider period, another person or role, or clear the filters.'
        : (everyone ? 'Labels appear here as soon as anyone in this company labels a step on Questions.'
          : 'Your labels appear here as soon as you label a step on Questions.')}</p>
      <div className="lab-row lab-empty-actions">
        {filtered ? <button type="button" className="lab-btn lab-btn-quiet lab-btn-sm" onClick={onClear}>{'Clear filters'}</button> : null}
        {onQuestions ? <button type="button" className="lab-btn lab-btn-sm" onClick={onQuestions}>{'Go to Questions'}</button> : null}
      </div>
    </div>
  );
}

export default function AllLabels({ onOpen, onQuestions }) {
  const [f, setF] = React.useState(EMPTY);
  const [applied, setApplied] = React.useState(EMPTY);
  const [res, setRes] = React.useState({ rows: [], total: 0, all: 0, byLabel: {}, everyone: false, loading: true, error: '', offset: 0 });
  const [csvBusy, setCsvBusy] = React.useState(false);
  const [confirming, setConfirming] = React.useState(null);
  const [deleting, setDeleting] = React.useState(false);
  const ask = React.useRef(0);

  const query = (g, offset) => ({ reviewer: g.reviewer.trim() || undefined, role: g.role || undefined, label: g.label || undefined,
    q: g.q.trim() || undefined, since: g.since ? new Date(g.since + 'T00:00:00').toISOString() : undefined,
    until: g.until ? new Date(g.until + 'T23:59:59').toISOString() : undefined, limit: PAGE, offset });

  // The tiles count every label the other filters find, so with a label chosen a second, one-row read brings the split.
  const load = async (g, offset) => {
    const mine = (ask.current += 1);
    setApplied(g);
    setRes((p) => Object.assign({}, p, { loading: true, error: '' }));
    try {
      const [out, split] = await Promise.all([labApi.all(query(g, offset)),
        g.label ? labApi.all(Object.assign(query(g, 0), { label: undefined, limit: 1 })) : Promise.resolve(null)]);
      if (mine !== ask.current) return;
      const base = split || out;
      setRes({ rows: (out && out.labels) || [], total: (out && out.total) || 0, all: (base && base.total) || 0,
        byLabel: (base && base.by_label) || {}, everyone: !!(out && out.everyone), me: (out && out.me) || null, loading: false,
        error: '', offset });
    } catch (e) {
      if (mine === ask.current) setRes((p) => Object.assign({}, p, { loading: false, error: errText(e) }));
    }
  };
  React.useEffect(() => { load(EMPTY, 0); }, []);

  // A choice from a list or a date applies at once; typed words apply on Enter or on leaving the box.
  const pick = (k) => (e) => { const g = Object.assign({}, f, { [k]: e.target.value }); setF(g); load(g, 0); };
  const type = (k) => (e) => setF(Object.assign({}, f, { [k]: e.target.value }));
  const commit = () => { if (f.q !== applied.q || f.reviewer !== applied.reviewer) load(f, 0); };
  const setLabel = (label) => { const g = Object.assign({}, f, { label }); setF(g); load(g, 0); };
  const clear = () => { setF(EMPTY); load(EMPTY, 0); };
  const filtered = Object.keys(EMPTY).some((k) => (applied[k] || '').trim());

  // Delete one of my labels, or every label of its group; then read the same page again (the counts change too).
  const onDelete = async (x, scope) => {
    setDeleting(true);
    try {
      if (scope === 'group' && x.group_id) await labApi.clearGroup(x.turn_id, x.group_id);
      else await labApi.clearLabel(x.turn_id, x.lineage_id || x.id, x.id);
      setConfirming(null);
      const offset = res.rows.length === 1 && res.offset > 0 ? Math.max(0, res.offset - PAGE) : res.offset;
      await load(applied, offset);
    } catch (e) {
      setRes((p) => Object.assign({}, p, { error: e && e.status === 409 ? 'This label changed in another window. The list has been read again.' : errText(e) }));
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
        a.download = out.filename || 'trace-lab-step-labels.csv';
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
  return (
    <div className="lab-page-body lab-page-full">
      <div className="lab-page-head lab-head-row">
        <div>
          <h1>{'All labels'}</h1>
          <p className="lab-small lab-big">{res.everyone ? 'Every label given in this company, with who gave it.' : 'The labels you have given.'}</p>
        </div>
        <button type="button" className="lab-btn lab-btn-quiet lab-btn-sm" onClick={download} disabled={csvBusy || !res.total}>
          {csvBusy ? 'Preparing…' : 'Download CSV'}
        </button>
      </div>
      <LabelTiles total={res.all} byLabel={res.byLabel} current={applied.label} onPick={setLabel} />
      <form className="lab-filterbar" onSubmit={(e) => { e.preventDefault(); load(f, 0); }}>
        {res.everyone ? (
          <label className="lab-field"><span>{'Person'}</span>
            <input type="search" value={f.reviewer} onChange={type('reviewer')} onBlur={commit} placeholder="Email, then Enter" />
          </label>
        ) : null}
        <label className="lab-field"><span>{'Role'}</span>
          <select value={f.role} onChange={pick('role')}>
            <option value="">{'Any role'}</option>
            {ROLE_CHOICES.map((r) => <option key={r.key} value={r.key}>{r.text}</option>)}
            <option value="platform">{'Platform team'}</option>
          </select>
        </label>
        <label className="lab-field lab-field-grow"><span>{'Words'}</span>
          <input type="search" value={f.q} onChange={type('q')} onBlur={commit} placeholder="In the question, step, selected text, note or what should happen, then Enter" />
        </label>
        <label className="lab-field"><span>{'From'}</span><input type="date" value={f.since} onChange={pick('since')} /></label>
        <label className="lab-field"><span>{'To'}</span><input type="date" value={f.until} onChange={pick('until')} /></label>
        <button type="submit" className="lab-visually-hidden" tabIndex={-1}>{'Apply'}</button>
        {filtered ? <button type="button" className="lab-link lab-clear" onClick={clear}>{'Clear filters'}</button> : null}
      </form>
      {res.error ? <div role="alert" className="lab-error">{res.error}</div> : null}
      {res.loading && !res.rows.length ? <p className="lab-small">{'Reading…'}</p> : null}
      {!res.loading && !res.error && !res.rows.length
        ? <NoLabels filtered={filtered} everyone={res.everyone} onClear={clear} onQuestions={onQuestions} /> : null}
      {res.rows.length ? (
        <>
          <p className="lab-small" aria-live="polite">{res.loading ? 'Reading…' : 'Showing ' + (res.offset + 1) + '–' + last + ' of ' + res.total.toLocaleString('en-GB')}</p>
          <LabelsTable rows={res.rows} onOpen={onOpen} me={res.me} confirming={confirming} busy={deleting} onAsk={setConfirming}
            onDelete={onDelete} />
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
