// RunCompare — two golden runs, the older as the base: whether they are like for like, the pass rates with their
// change (the 95% interval on hover), pass^k, the cost, which cases flipped (in words, not colour alone), and what
// differed between the two runs.
import React from 'react';
import * as act from '../actions.js';
import * as gact from '../goldenActions.js';
import { compareView } from '../logic/golden.js';

export default function RunCompare({ s }) {
  const v = compareView(s);
  if (!v) return null;
  const headId = s.golden.compare && s.golden.compare.head;
  return (
    <section className="tl-card" aria-label="Two runs compared">
      <button type="button" className="tl-link" onClick={gact.closeCompare}>{'‹ All runs'}</button>
      <h3 className="tl-title">{v.base + ' → ' + v.head}</h3>
      {v.loading ? <div className="tl-meta">{'Comparing…'}</div> : null}
      {v.error ? <div className="tl-bad" role="alert">{"Couldn't compare the runs: " + v.error}</div> : null}
      {v.rates ? (
        <>
          {!v.comparable || v.warnings.length ? (
            <div className="tl-notice" role="status">{v.comparable ? 'Read this comparison with care:' : 'These runs are not like for like:'}
              <ul className="tl-issues">{v.warnings.map((w) => <li key={w}>{w}</li>)}</ul>
            </div>
          ) : null}
          <table className="tl-table">
            <thead><tr>{['Measure', v.base, v.head, 'Change'].map((h, i) => <th key={i} scope="col">{h}</th>)}</tr></thead>
            <tbody>
              {v.rates.map((r) => (
                <tr key={r.label}><th scope="row">{r.label}</th><td title={r.baseTitle || undefined}>{r.base}</td><td title={r.headTitle || undefined}>{r.head}</td><td>{r.delta || '—'}</td></tr>
              ))}
            </tbody>
          </table>
          <h4 className="tl-h4">{'Cases that changed'}</h4>
          {v.flips.length ? (
            <ul className="tl-flips">
              {v.flips.map((f) => (
                <li key={f.kind + ':' + f.key} className={f.danger ? 'tl-fail' : ''}>
                  <strong>{f.label}</strong>{' · case ' + f.key + ' · ' + f.question}
                  {f.sessionId ? <button type="button" className="tl-link" onClick={() => act.openGoldenAnswer(headId, f.sessionId, f.turnId)}>{'Open answer'}</button> : null}
                </li>
              ))}
            </ul>
          ) : <div className="tl-meta">{'No case changed between the two runs.'}</div>}
          <div className="tl-meta">{v.stillPassing + ' cases passed in both.'}</div>
          {v.differs.length ? (
            <>
              <h4 className="tl-h4">{'What differed'}</h4>
              <table className="tl-kv"><tbody>{v.differs.map((d) => <tr key={d.label}><th scope="row">{d.label}</th><td>{d.base + ' → ' + d.head}</td></tr>)}</tbody></table>
            </>
          ) : null}
        </>
      ) : null}
    </section>
  );
}
