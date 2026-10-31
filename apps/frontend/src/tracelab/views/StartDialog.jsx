// StartDialog — start a golden run: pick the version, how many times each case is asked (k), the purpose and a
// spending cap; the estimate (cost, time, this month's budget) and Trace Lab's own safety check are shown before
// Start is possible. A building selected in the header refuses the run, with how to clear it.
import React from 'react';
import * as gact from '../goldenActions.js';
import FidelityCaveats from './FidelityCaveats.jsx';

export default function StartDialog({ d }) {
  return (
    <div className="tl-side tl-dialog" role="dialog" aria-modal="true" aria-label="Start a golden run"
      onKeyDown={(e) => { if (e.key === 'Escape') gact.closeStart(); }}>
      <button type="button" className="tl-close" aria-label="Close" onClick={gact.closeStart}>{'×'}</button>
      <h3 className="tl-title">{'Start a golden run'}</h3>
      <p className="tl-meta">{'Every case is asked again through the chat, on a copy that cannot change anything, and graded against its expected answer.'}</p>
      <div className="tl-form">
        <label className="tl-field"><span>{'Version'}</span>
          <select value={d.setId} onChange={(e) => gact.setDialog({ setId: e.target.value || null })}>
            {d.sets.length ? d.sets.map((o) => <option key={o.value} value={o.value}>{o.label}</option>) : <option value="">{'The active version'}</option>}
          </select>
        </label>
        <div className="tl-field" role="group" aria-label="Times each case is asked"><span>{'Ask each case'}</span>
          {d.ks.map((k) => (
            <button key={k.k} type="button" className={'tl-chip-btn' + (k.on ? ' tl-on' : '')} aria-pressed={k.on ? 'true' : 'false'}
              onClick={() => gact.setDialog({ k: k.k })}>{k.k === 1 ? 'once' : k.k + ' times'}</button>
          ))}
        </div>
        <div className="tl-field" role="group" aria-label="Purpose"><span>{'Purpose'}</span>
          {d.purposes.map((p) => (
            <button key={p.key} type="button" className={'tl-chip-btn' + (p.on ? ' tl-on' : '')} aria-pressed={p.on ? 'true' : 'false'}
              onClick={() => gact.setDialog({ purpose: p.key })}>{p.label}</button>
          ))}
        </div>
        <label className="tl-field"><span>{'Spending cap ($)'}</span>
          <input type="text" inputMode="decimal" value={d.usdCap} onChange={(e) => gact.setDialog({ usdCap: e.target.value })} />
        </label>
        <label className="tl-field"><span>{'Note'}</span>
          <input type="text" value={d.note} maxLength={500} onChange={(e) => gact.setDialog({ note: e.target.value })} />
        </label>
      </div>
      <div className="tl-report" aria-live="polite">
        {d.estimating ? <div className="tl-meta">{'Estimating…'}</div> : d.lines.map((l) => <div key={l}>{l}</div>)}
      </div>
      {d.problems.length ? (
        <div className="tl-bad" role="alert">{'Trace Lab cannot run safely right now:'}
          <ul className="tl-issues">{d.problems.map((p, i) => <li key={i}>{p}</li>)}</ul>
        </div>
      ) : null}
      {d.building ? <div className="tl-bad" role="alert">{d.building}</div> : null}
      {d.noCases ? <div className="tl-bad" role="alert">{d.noCases}</div> : null}
      {d.error ? (
        <div className="tl-bad" role="alert">{d.error}
          {d.errorProblems.length ? <ul className="tl-issues">{d.errorProblems.map((p, i) => <li key={i}>{p}</li>)}</ul> : null}
        </div>
      ) : null}
      <FidelityCaveats />
      <div className="tl-actions">
        <button type="button" className="tl-btn tl-btn-primary" disabled={!d.canStart} onClick={() => gact.startRun()}>{d.starting ? 'Starting…' : 'Start'}</button>
        <button type="button" className="tl-btn" onClick={gact.closeStart}>{'Cancel'}</button>
      </div>
    </div>
  );
}
