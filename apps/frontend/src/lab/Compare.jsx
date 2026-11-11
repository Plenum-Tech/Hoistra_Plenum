// Compare — two run tags side by side (11 Nov 2026, task plan B4.2: "the baseline and post-fix runs can be compared
// side by side").
//
// Each scenario is paired across the two run tags by its id (the latest attempt on each side). Per level the table
// shows the verdict on both sides and what changed: Fixed (fail to pass), Regressed (pass to fail), Same, Changed,
// New (asked only under the newer tag) or Not asked. Above it: how many changed per level, and the median answer
// length on each side, which is how "answers got shorter" is read. A row opens its two sessions next to each other.
import React from 'react';
import { labApi } from './labApi.js';
import { errText } from './logic/labelling.js';
import SessionPage from './SessionPage.jsx';
import { compareView } from './logic/sessions.js';
import './runs.css';
import './sessions.css';

const BASELINE = 'baseline_v0';

// The comparison as a table. `data` is the service's answer; `onOpen(row)` opens a row's sessions.
export function CompareTable({ data, onOpen }) {
  const d = data || {};
  const v = compareView(d);
  return (
    <div className="lab-ses-compare">
      <ul className="lab-ses-lines lab-ses-summary">
        {v.summary.map((line) => <li key={line} className="lab-ses-line">{line}</li>)}
      </ul>
      {v.rows.length ? (
        <div className="lab-table-wrap lab-table-full">
          <table className="lab-table">
            <thead>
              <tr><th scope="col">{'Scenario'}</th><th scope="col">{'Type'}</th>
                <th scope="col">{'Logic'}</th><th scope="col">{'User experience'}</th><th scope="col">{'Domain'}</th>
                <th scope="col">{'Answer length'}</th><th scope="col"><span className="lab-visually-hidden">{'Open'}</span></th></tr>
            </thead>
            <tbody>
              {v.rows.map((r) => (
                <tr key={r.id}>
                  <td><strong>{r.id}</strong></td>
                  <td>{r.type}</td>
                  {r.levels.map((l) => (
                    <td key={l.key}>
                      <div>{l.base + ' → ' + l.head}</div>
                      <span className={'lab-chip' + (l.raw === 'fixed' ? ' lab-chip-ok' : l.raw === 'regressed' ? ' lab-ses-bad' : '')}>{l.change}</span>
                    </td>
                  ))}
                  <td>{(r.words.base == null ? '—' : r.words.base + ' words') + ' → ' + (r.words.head == null ? '—' : r.words.head + ' words')}</td>
                  <td>
                    <button type="button" className="lab-btn lab-btn-quiet lab-btn-sm" onClick={() => onOpen(r)}>
                      {r.baseSession && r.headSession ? 'Open side by side' : 'Open the session'}</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="lab-empty"><p className="lab-small">{'No scenario was asked under both run tags.'}</p></div>
      )}
      <p className="lab-small">{'Comparing ' + (d.base || '—') + ' with ' + (d.head || '—') + '. A scenario is paired by its id; the latest attempt on each side is the one compared.'}</p>
    </div>
  );
}

function TagSelect({ label, value, tags, onChange }) {
  return (
    <label className="lab-field lab-ses-filter"><span>{label}</span>
      <select value={value || ''} onChange={(e) => onChange(e.target.value)}>
        <option value="">{'Choose a run tag'}</option>
        {(tags || []).map((t) => <option key={t.tag} value={t.tag}>{t.tag + ' (' + (Number(t.sessions) || 0) + ')'}</option>)}
      </select>
    </label>
  );
}

export default function Compare({ me, tags }) {
  const list = tags || [];
  const [base, setBase] = React.useState('');
  const [head, setHead] = React.useState('');
  const [data, setData] = React.useState(null);
  const [error, setError] = React.useState('');
  const [busy, setBusy] = React.useState(false);
  const [pair, setPair] = React.useState(null);
  const ask = React.useRef(0);

  // The first time the run tags arrive: the baseline against the newest other tag, when there are two to compare.
  React.useEffect(() => {
    if (base || head || !list.length) return;
    const first = list.find((t) => t.tag === BASELINE) || list[list.length - 1];
    const other = list.find((t) => t.tag !== first.tag);
    setBase(first.tag);
    if (other) setHead(other.tag);
  }, [list]); // eslint-disable-line react-hooks/exhaustive-deps

  React.useEffect(() => {
    if (!base || !head || base === head) { setData(null); return; }
    const mine = (ask.current += 1);
    setBusy(true); setError('');
    labApi.evalCompare(base, head)
      .then((out) => { if (mine === ask.current) { setData(out); setBusy(false); } })
      .catch((e) => { if (mine === ask.current) { setError(errText(e)); setData(null); setBusy(false); } });
  }, [base, head]);

  if (pair) {
    const sides = [[base, pair.baseSession], [head, pair.headSession]].filter(([, id]) => id);
    return (
      <div className="lab-ses-page">
        <div className="lab-row">
          <button type="button" className="lab-btn lab-btn-quiet lab-btn-sm" onClick={() => setPair(null)}>{'← Back to the comparison'}</button>
          <strong>{pair.id}</strong>
        </div>
        <div className={'lab-ses-pair' + (sides.length === 1 ? ' lab-ses-pair-one' : '')}>
          {sides.map(([tag, id]) => (
            <section key={id} className="lab-ses-side" aria-label={'Session under ' + tag}>
              <span className="lab-chip lab-chip-synth">{tag}</span>
              <SessionPage sessionId={id} me={me} />
            </section>
          ))}
        </div>
      </div>
    );
  }
  return (
    <div className="lab-ses-list">
      <p className="lab-small">{'Choose the run before a change and the run after it. Each scenario is compared with itself.'}</p>
      <div className="lab-row lab-ses-filters" role="group" aria-label="Run tags to compare">
        <TagSelect label="Before (base)" value={base} tags={list} onChange={setBase} />
        <TagSelect label="After (head)" value={head} tags={list} onChange={setHead} />
      </div>
      {base && head && base === head ? <div role="alert" className="lab-error">{'Choose two different run tags.'}</div> : null}
      {error ? <div role="alert" className="lab-error">{error}</div> : null}
      {busy ? <p className="lab-small" role="status">{'Comparing…'}</p> : null}
      {!busy && data ? <CompareTable data={data} onOpen={setPair} /> : null}
      {!busy && !data && !error && !(base && head) ? (
        <div className="lab-empty"><p className="lab-small">{list.length < 2
          ? 'Comparing needs two run tags with sessions. There ' + (list.length === 1 ? 'is one so far.' : 'are none yet.')
          : 'Choose two run tags to compare them.'}</p></div>
      ) : null}
    </div>
  );
}
