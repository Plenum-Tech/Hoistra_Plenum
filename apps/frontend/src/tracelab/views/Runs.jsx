// Runs — "All live traffic" over the period, then the company's golden runs: start one (the dialog shows its
// estimate first), open one, or tick two and compare them. A run that is open replaces the list, and so does a
// comparison. Picking the live run opens its conversations.
import React from 'react';
import * as act from '../actions.js';
import * as gact from '../goldenActions.js';
import { runsView } from '../logic/views.js';
import { compareView, goldenRunsView, runDetailView } from '../logic/golden.js';
import RunDetail from './RunDetail.jsx';
import RunCompare from './RunCompare.jsx';
import StartDialog from './StartDialog.jsx';

function LiveRuns({ v }) {
  const rows = v.rows.filter((r) => r.live);
  return (
    <section className="tl-card" aria-label="Live traffic">
      {v.loading ? <div className="tl-meta">{'Reading runs…'}</div> : null}
      {v.error ? <div className="tl-bad" role="alert">{"Couldn't read the runs: " + v.error}</div> : null}
      <table className="tl-table">
        <thead><tr>{['Run', 'Period', 'Conversations', 'Answers', 'Models', 'Cost', 'Status'].map((h) => <th key={h} scope="col">{h}</th>)}</tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} className={r.on ? 'tl-row-on' : ''}>
              <td><button type="button" className="tl-link" onClick={() => act.pickRun(r.id)}>{r.name}</button></td>
              <td>{r.period}</td><td>{r.conversations}</td><td>{r.answers}</td><td>{r.models}</td><td>{r.cost}</td><td>{r.status}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function GoldenRuns({ g }) {
  if (g.notice) return <div className="tl-notice" role="status">{g.notice}</div>;
  return (
    <section className="tl-card" aria-label="Golden runs">
      <div className="tl-list-head">
        <h3 className="tl-title">{'Golden runs'}</h3>
        <div className="tl-actions">
          <button type="button" className="tl-btn" disabled={!g.canCompare} onClick={() => gact.compareRuns()}>{'Compare the two ticked'}</button>
          <button type="button" className="tl-btn tl-btn-primary" onClick={() => gact.openStart()}>{'Start a golden run'}</button>
        </div>
      </div>
      {g.pickNote ? <div className="tl-meta">{g.pickNote}</div> : null}
      {g.loading ? <div className="tl-meta">{'Reading golden runs…'}</div> : null}
      {g.error ? <div className="tl-bad" role="alert">{"Couldn't read the golden runs: " + g.error}</div> : null}
      {g.empty ? <div className="tl-meta">{'No golden runs yet.'}</div> : null}
      {g.rows.length ? (
        <table className="tl-table">
          <thead><tr>{['', 'Run', 'Version', 'Purpose', 'Reps', 'Cases', 'Pass rate', 'Cost', 'Status', 'Progress', 'Started', 'By'].map((h, i) => <th key={i} scope="col">{h}</th>)}</tr></thead>
          <tbody>
            {g.rows.map((r) => (
              <tr key={r.id} className={r.open ? 'tl-row-on' : ''}>
                <td><input type="checkbox" checked={r.picked} aria-label={'Compare ' + r.label} onChange={() => gact.togglePick(r.id)} /></td>
                <td><button type="button" className="tl-link" onClick={() => gact.openRun(r.id)}>{r.label}</button></td>
                <td>{r.set}</td><td>{r.purpose}</td><td>{r.k}</td><td>{r.cases}</td>
                <td title={r.passTitle || undefined}>{r.pass}</td><td>{r.cost}</td>
                <td>{r.status}{r.mine && r.running ? ' · this page' : ''}</td><td>{r.progress}</td><td>{r.when || '—'}</td><td>{r.who}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}
    </section>
  );
}

export default function Runs({ s, now }) {
  const g = goldenRunsView(s, now);
  if (runDetailView(s, now)) return <RunDetail s={s} now={now} />;
  if (compareView(s)) return <RunCompare s={s} />;
  return (
    <>
      <LiveRuns v={runsView(s, now)} />
      <GoldenRuns g={g} />
      {g.dialog ? <StartDialog d={g.dialog} /> : null}
    </>
  );
}
