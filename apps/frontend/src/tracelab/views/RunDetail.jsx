// RunDetail — one golden run: what it is, whether this page is keeping it going, its pass rates (with the 95%
// interval on hover), what it cost against its cap, Pause / Resume / Cancel / Grade again, and every case with
// each rep's outcome. "Open answer" shows that rep's conversation on Conversations, where the expected outcome
// waits behind its own reveal.
import React from 'react';
import * as act from '../actions.js';
import * as gact from '../goldenActions.js';
import { runDetailView } from '../logic/golden.js';
import FidelityCaveats from './FidelityCaveats.jsx';

function Actions({ a }) {
  const busy = !!a.busy;
  return (
    <div className="tl-actions">
      {a.canPause ? <button type="button" className="tl-btn" disabled={busy} onClick={() => gact.pauseRun()}>{a.busy === 'pause' ? 'Pausing…' : 'Pause'}</button> : null}
      {a.canResume ? (
        <>
          <label className="tl-field"><span>{'New cap ($)'}</span>
            <input type="text" inputMode="decimal" value={a.resumeCap} placeholder="unchanged" onChange={(e) => gact.setResumeCap(e.target.value)} />
          </label>
          <button type="button" className="tl-btn tl-btn-primary" disabled={busy} onClick={() => gact.resumeRun()}>{a.busy === 'resume' ? 'Resuming…' : 'Resume'}</button>
        </>
      ) : null}
      {a.canCancel ? <button type="button" className="tl-btn tl-btn-danger" disabled={busy} onClick={() => gact.cancelRun()}>{a.busy === 'cancel' ? 'Cancelling…' : 'Cancel the run'}</button> : null}
      {a.canRegrade ? (
        <>
          <button type="button" className="tl-btn" disabled={busy} onClick={() => gact.regradeRun('pending')}>{'Grade what is waiting'}</button>
          <button type="button" className="tl-btn" disabled={busy} onClick={() => gact.regradeRun('all')}>{'Grade every answer again'}</button>
        </>
      ) : null}
    </div>
  );
}

export default function RunDetail({ s, now }) {
  const v = runDetailView(s, now);
  if (!v) return null;
  const back = <button type="button" className="tl-link" onClick={gact.closeRun}>{'‹ All runs'}</button>;
  if (!v.run) {
    return (
      <section className="tl-card" aria-label="Golden run">
        {back}
        {v.loading ? <div className="tl-meta">{'Reading the run…'}</div> : null}
        {v.error ? <div className="tl-bad" role="alert">{"Couldn't read the run: " + v.error}</div> : null}
      </section>
    );
  }
  const r = v.run;
  return (
    <section className="tl-card" aria-label={'Golden run ' + r.label}>
      <div className="tl-list-head">{back}<button type="button" className="tl-link" onClick={() => gact.pollRun()}>{'Refresh'}</button></div>
      <h3 className="tl-title">{r.label}</h3>
      <div className="tl-chips">{[r.status, r.purpose, r.k, r.set].map((c, i) => <span key={i} className="tl-chip">{c}</span>)}</div>
      <div className="tl-meta">{'Started ' + (r.when || '—') + ' by ' + r.who}</div>
      {r.note ? <div className="tl-note">{r.note}</div> : null}
      {r.error ? <div className="tl-bad">{r.error}</div> : null}
      {v.banner ? <div className="tl-notice" role="status">{v.banner}</div> : null}
      <FidelityCaveats />
      {v.loop.stepping && v.loop.note ? <div className="tl-meta" aria-live="polite">{v.loop.note}</div> : null}
      {v.loop.error ? (
        <div className="tl-bad" role="alert">{v.loop.error}{v.loop.hint ? ' ' + v.loop.hint : ''}{v.loop.resumable ? ' Resume carries on.' : ''}</div>
      ) : null}
      <div className="tl-meta">{v.progress}</div>
      <ul className="tl-tiles" aria-label="Results">
        {v.tiles.map((t) => <li key={t.label} className="tl-tile" title={t.title || undefined}><span className="tl-meta">{t.label}</span><strong>{t.value}</strong></li>)}
      </ul>
      {v.outcomes.length ? <div className="tl-meta">{v.outcomes.join(' · ')}</div> : null}
      {v.graderNote ? <div className="tl-note">{v.graderNote}</div> : null}
      <Actions a={v.actions} />
      {v.actions.error ? <div className="tl-bad" role="alert">{v.actions.error}</div> : null}
      {v.params.length ? (
        <details className="tl-panel">
          <summary>{'What this run measured'}</summary>
          <table className="tl-kv"><tbody>{v.params.map((p) => <tr key={p.label}><th scope="row">{p.label}</th><td>{p.value}</td></tr>)}</tbody></table>
        </details>
      ) : null}
      <table className="tl-table tl-cases">
        <thead><tr>{['Case', 'Question', 'Evaluation', 'Result', 'Reps'].map((h) => <th key={h} scope="col">{h}</th>)}</tr></thead>
        <tbody>
          {v.cases.map((c) => (
            <tr key={c.key}>
              <td>{c.key}</td>
              <td className="tl-wrap">{c.question}{c.stale ? <span className="tl-chip">{'Stale'}</span> : null}</td>
              <td>{c.evaluation}</td>
              <td>{c.tally || '—'}</td>
              <td>
                <ul className="tl-reps">
                  {c.reps.map((x) => (
                    <li key={x.rep} className={x.tone ? 'tl-' + x.tone : ''}>
                      <span>{'Rep ' + x.rep + ' · ' + x.outcome + ' · ' + x.cost + ' · ' + x.time + (x.refused ? ' · ' + x.refused : '')}</span>
                      {x.sessionId ? <button type="button" className="tl-link" onClick={() => act.openGoldenAnswer(r.id, x.sessionId, x.turnId)}>{'Open answer'}</button> : null}
                    </li>
                  ))}
                </ul>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {!v.cases.length ? <div className="tl-meta">{'No case has been answered yet.'}</div> : null}
    </section>
  );
}
