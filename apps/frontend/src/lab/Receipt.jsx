// How one value was found (16 Nov 2026): in plain words, then the rows it read, the query, the values filled in, and what to do
// about it. The session page's values and the query editor's results both open it. Nothing here reads the service.
import React from 'react';

export const factText = (f) => (f.label ? f.label + ': ' : '') + String(f.value);

export function when(iso) {
  const t = iso ? new Date(iso) : null;
  if (!t || Number.isNaN(t.getTime())) return '';
  return t.toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
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
export function Receipt({ fact, receipt, readAt, mark, rerun, rerunBusy, copied, busy, onCopy, onRerun, onMark, onClose, explain: said }) {
  if (!fact || !receipt) return null;
  const counted = typeof fact.value === 'number';
  const listed = counted && receipt.id !== fact.receipt;
  const explain = said ? said : listed ? 'These are the ' + fact.value + ' records the query below found.'
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

