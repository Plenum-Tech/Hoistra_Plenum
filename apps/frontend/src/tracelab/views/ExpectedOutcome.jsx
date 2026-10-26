// ExpectedOutcome — above a golden answer in Conversations: the case's expected answer and the grader's verdict,
// closed until the reviewer reveals them, so the answer can be judged on its own first. The service records the
// reveal; Hide closes the panel again without a second reveal. Refused steps are listed in a technical panel.
import React from 'react';
import * as act from '../actions.js';
import * as gact from '../goldenActions.js';
import { expectedView } from '../logic/golden.js';
import { isOpen } from '../logic/views.js';
import RawPanel from './RawPanel.jsx';

export default function ExpectedOutcome({ s }) {
  const v = expectedView(s);
  const turnId = v ? v.turnId : null;
  React.useEffect(() => { if (turnId) gact.loadExpected(turnId); }, [turnId]);
  if (!v) return null;
  if (v.unavailable) return <div className="tl-note">{v.unavailable}</div>;
  const where = v.run ? 'Golden run ' + v.run + (v.caseKey ? ' · case ' + v.caseKey : '') + (v.rep ? ' · rep ' + v.rep : '') : '';
  const rawId = 'expected:' + v.turnId + ':refused';
  return (
    <section className="tl-expected" aria-label="Expected outcome">
      <div className="tl-card-head"><span className="tl-role">{'Expected outcome'}</span>{where ? <span className="tl-meta">{where}</span> : null}</div>
      {!v.open ? (
        <>
          <p className="tl-meta">{'Hidden until you reveal it, so you can judge the answer on its own first. Revealing it is recorded.'}</p>
          <button type="button" className="tl-btn" disabled={v.loading || v.revealing} onClick={() => gact.revealExpected(v.turnId)}>
            {v.revealing ? 'Revealing…' : (v.revealed ? 'Show the expected outcome' : 'Reveal the expected outcome')}
          </button>
        </>
      ) : (
        <>
          {v.finalNote ? <div className="tl-note">{v.finalNote}</div> : null}
          <table className="tl-kv">
            <tbody>
              <tr><th scope="row">{'Question'}</th><td>{v.question || '—'}</td></tr>
              {v.followups.length ? <tr><th scope="row">{'Follow-ups'}</th><td>{v.followups.join(' · ')}</td></tr> : null}
              <tr><th scope="row">{'Expected answer'}</th><td>{v.expected}</td></tr>
              {v.values.length ? <tr><th scope="row">{'Expected values'}</th><td>{v.values.join('; ')}</td></tr> : null}
              <tr><th scope="row">{'Evaluation'}</th><td>{v.evaluation}</td></tr>
              <tr><th scope="row">{'Source'}</th><td>{v.source}</td></tr>
              <tr><th scope="row">{'Verified on'}</th><td>{v.verified + (v.stale ? ' (stale)' : '')}</td></tr>
              {v.requirements ? <tr><th scope="row">{'Requirements'}</th><td>{v.requirements}</td></tr> : null}
              <tr><th scope="row">{'Grade'}</th><td>{v.outcome}</td></tr>
            </tbody>
          </table>
          {v.unvalidated ? <div className="tl-note">{v.unvalidated}</div> : null}
          {v.critique ? <div className="tl-text">{v.critique}</div> : null}
          {v.checks.length ? <ul className="tl-issues" aria-label="Expected values found in the answer">{v.checks.map((c, i) => <li key={i}>{c}</li>)}</ul> : null}
          {v.refusedLine ? <div className="tl-bad">{v.refusedLine}</div> : null}
          {v.refused.length ? <RawPanel title="Refused steps" data={v.refused} open={isOpen(s, rawId, false)} onToggle={(o) => act.setOpen(rawId, o)} /> : null}
          <button type="button" className="tl-link" onClick={() => gact.hideExpected(v.turnId)}>{'Hide'}</button>
        </>
      )}
      {v.error ? <div className="tl-bad" role="alert">{"Couldn't read the expected outcome: " + v.error}</div> : null}
    </section>
  );
}
