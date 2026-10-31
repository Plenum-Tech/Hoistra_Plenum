// Judges — the Trace Lab's Judges tab (logic/judges.js judgesVals): the judges and code checks for everyone who opens
// the tab (dev and held-out rates with their 95% intervals, label counts against the minimum); and, for the platform
// team, the failure modes, a mode's readiness and split, the new-version form, one judge's page (prompt parts with the
// train answers its examples come from, Preview, Run on dev / Run held-out test through the estimate panel, runs,
// disagreement triage, decision, threshold, the iteration log), the failure report, and the one-way freeze dialog. A
// company admin, or a superadmin viewing a company, reads the table only: the service refuses anything more, and
// nothing here asks it to. Every number comes from the view model; a missing one reads "—".
//
// The tab reads when it opens and again for another company (a company switch bumps the store's seq); leaving it
// stops the run watch. The freeze dialog sits on an overlay (Escape closes it unless a freeze is in flight), portalled
// to the page body so no ancestor traps it, and drawn in place where there is no body.
import React from 'react';
import { createPortal } from 'react-dom';
import * as a from '../judgesActions.js';
import { judgesVals } from '../logic/judges.js';

function Problem({ text, retry, reload }) {
  if (!text) return null;
  return (
    <div role="alert" className="tl-bad tl-inline">
      {text}
      {retry ? <button type="button" className="tl-link" onClick={retry}>{'Retry'}</button> : null}
      {reload ? <button type="button" className="tl-link" onClick={reload}>{'Reload'}</button> : null}
    </div>
  );
}

function JudgesTable({ rows }) {
  return (
    <div className="tl-rows">
      <table className="tl-table">
        <thead>
          <tr>
            <th scope="col">{'Failure mode'}</th><th scope="col">{'Kind'}</th><th scope="col">{'Version'}</th><th scope="col">{'Status'}</th>
            <th scope="col">{'Dev TPR'}</th><th scope="col">{'Dev TNR'}</th><th scope="col">{'Test TPR'}</th><th scope="col">{'Test TNR'}</th>
            <th scope="col">{'Labels'}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id}>
              <td>
                {r.open ? <button type="button" className="tl-link" aria-current={r.on ? 'true' : undefined} onClick={r.open}>{r.mode}</button> : r.mode}
                {r.inUse ? <span className="tl-pill tl-pill-ok tl-gap-left">{'In use'}</span> : null}
              </td>
              <td>{r.kind}</td>
              <td>{r.version}</td>
              <td>{r.status}{r.decision ? <span className="tl-meta">{' · ' + r.decision}</span> : null}</td>
              <td>{r.devTpr}</td>
              <td>{r.devTnr}</td>
              <td>{r.testTpr}</td>
              <td>{r.testTnr}</td>
              <td>{r.labels}{r.badge ? <div className="tl-meta">{r.badge}</div> : r.belowMinimum ? <div className="tl-meta">{'Below the minimum'}</div> : null}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ChecksTable({ checks }) {
  return (
    <div className="tl-rows">
      <table className="tl-table">
        <thead><tr><th scope="col">{'Check'}</th><th scope="col">{'What it catches'}</th><th scope="col">{'Runs on'}</th><th scope="col">{'Version'}</th></tr></thead>
        <tbody>
          {checks.map((x) => (
            <tr key={x.key}><td>{x.label}</td><td className="tl-wrap">{x.description}</td><td>{x.runsOn}</td><td>{x.version}</td></tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Modes({ v }) {
  return (
    <section className="tl-card" aria-label="Failure modes">
      <div className="tl-head-row"><h3 className="tl-title tl-grow">{'Failure modes'}</h3></div>
      {v.tljModesEmpty ? <p className="tl-meta">{'No confirmed failure modes yet. A judge is built for a confirmed failure mode.'}</p> : (
        <ul className="tl-rows-list">
          {v.tljModes.map((m) => (
            <li key={m.id}>
              <button type="button" className={'tl-row' + (m.on ? ' tl-row-on' : '')} aria-current={m.on ? 'true' : undefined} onClick={m.open}>
                <span className="tl-row-q">{m.name}</span><span className="tl-meta">{m.status}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function Readiness({ r }) {
  return (
    <section className="tl-card" aria-label="Failure mode readiness">
      <div className="tl-head-row">
        <h3 className="tl-title tl-grow">{r.name}</h3>
        <button type="button" className="tl-link" onClick={r.reload}>{'Refresh'}</button>
        <button type="button" className="tl-link" aria-label="Close this failure mode" onClick={r.close}>{'Close'}</button>
      </div>
      {r.loading ? <p className="tl-meta">{'Reading…'}</p> : null}
      <Problem text={r.error} retry={r.reload} />
      {r.definition ? <p className="tl-text">{r.definition}</p> : null}
      <p className="tl-text">{r.labels}</p>
      {r.floor ? <p className="tl-meta">{r.floor}</p> : null}
      {r.excluded ? <p className="tl-meta">{r.excluded}</p> : null}
      {r.trainOnly ? <p className="tl-meta">{r.trainOnly}</p> : null}
      <p className="tl-text"><b>{'Next: '}</b>{r.nextStep}</p>
      {r.split ? (
        <p className="tl-text">{r.split.text}{r.split.badge ? <span className="tl-chip tl-gap-left">{r.split.badge}</span> : null}</p>
      ) : <p className="tl-meta">{'No split yet'}</p>}
      {r.canSplit ? (
        <div className="tl-subform">
          {r.splitBelowMin ? (
            <div className="tl-fields">
              <label className="tl-check-row"><input type="checkbox" checked={r.splitOverride} onChange={r.setSplitOverride} />{' Build it below the label minimum'}</label>
              {r.floor ? <p className="tl-meta">{r.floor}</p> : null}
              {r.splitOverride ? (
                <div className="tl-field-row">
                  <label htmlFor="tlj-split-reason">{'Why this split may be built below the minimum'}</label>
                  <textarea id="tlj-split-reason" className="tl-textarea" value={r.splitReason} onChange={r.setSplitReason} />
                </div>
              ) : null}
            </div>
          ) : null}
          <div className="tl-actions">
            <button type="button" className="tl-btn tl-btn-primary" disabled={!r.splitReady} onClick={r.createSplit}>{r.splitBusy ? 'Creating…' : 'Create the split'}</button>
          </div>
          <Problem text={r.splitError} retry={r.splitRetry} />
        </div>
      ) : null}
      {r.canRegister ? (
        <div className="tl-actions">
          <button type="button" className="tl-btn tl-btn-primary" onClick={r.startLlm}>{'Write version ' + r.nextVersion}</button>
          <button type="button" className="tl-btn" onClick={r.startCode}>{'Use a code check as version ' + r.nextVersion}</button>
        </div>
      ) : null}
      {r.warnings.map((w, i) => <p key={i + w} className="tl-warn">{w}</p>)}
      {r.versions.length ? (
        <div>
          <h4 className="tl-h4">{'Versions'}</h4>
          <ul className="tl-rows-list">
            {r.versions.map((x) => (
              <li key={x.id}>
                <button type="button" className={'tl-row' + (x.on ? ' tl-row-on' : '')} aria-current={x.on ? 'true' : undefined} onClick={x.open}>
                  <span className="tl-row-q">{x.label}</span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </section>
  );
}

function DraftForm({ d }) {
  return (
    <section className="tl-card" aria-label="New judge version">
      <div className="tl-head-row"><h3 className="tl-title tl-grow">{d.title}</h3><button type="button" className="tl-link" onClick={d.cancel}>{'Cancel'}</button></div>
      {d.isLlm ? (
        <div className="tl-fields">
          <div className="tl-field-row">
            <label htmlFor="tlj-task">{'Task (optional)'}</label>
            <textarea id="tlj-task" className="tl-textarea" value={d.task} onChange={d.set('task')} />
          </div>
          <div className="tl-field-row">
            <label htmlFor="tlj-fail">{'Fail when'}</label>
            <textarea id="tlj-fail" className="tl-textarea" value={d.failRule} onChange={d.set('fail_rule')} />
          </div>
          <div className="tl-field-row">
            <label htmlFor="tlj-pass">{'Pass when'}</label>
            <textarea id="tlj-pass" className="tl-textarea" value={d.passRule} onChange={d.set('pass_rule')} />
          </div>
          <div className="tl-field-row">
            <label htmlFor="tlj-edge">{'Edge cases (optional)'}</label>
            <textarea id="tlj-edge" className="tl-textarea" value={d.edgeCases} onChange={d.set('edge_cases')} />
          </div>
          <h4 className="tl-h4">{'Examples from the train answers'}</h4>
          {d.splitLoading ? <p className="tl-meta">{'Reading…'}</p> : null}
          <Problem text={d.splitError} />
          {d.trainEmpty ? <p className="tl-meta">{'The split has no train answers.'}</p> : null}
          {d.examples.map((x) => (
            <fieldset key={x.role} className="tl-fieldset tl-fieldset-box tl-fields">
              <legend className="tl-kick">{x.label + ' · verdict ' + x.verdict}</legend>
              <div className="tl-field-row">
                <label htmlFor={'tlj-ex-' + x.role}>{'Train answer'}</label>
                <select id={'tlj-ex-' + x.role} value={x.value} onChange={x.setTurn}>
                  <option value="">{'Choose a train answer…'}</option>
                  {x.options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              </div>
              {x.source ? <pre className="tl-pre">{x.source}</pre> : null}
              <div className="tl-field-row">
                <label htmlFor={'tlj-crit-' + x.role}>{'Critique'}</label>
                <textarea id={'tlj-crit-' + x.role} className="tl-textarea" value={x.critique} onChange={x.setCritique} />
              </div>
            </fieldset>
          ))}
        </div>
      ) : (
        <div className="tl-field-row">
          <label htmlFor="tlj-check">{'Code check'}</label>
          <select id="tlj-check" value={d.checkKey} onChange={d.set('check_key')}>
            <option value="">{'Choose a check…'}</option>
            {d.checks.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </div>
      )}
      <div className="tl-field-row tl-gap-top">
        <label htmlFor="tlj-note">{d.noteNeeded ? 'What changed from the previous version' : 'Note (optional)'}</label>
        <textarea id="tlj-note" className="tl-textarea" value={d.changeNote} onChange={d.set('change_note')} />
      </div>
      <div className="tl-actions">
        <button type="button" className="tl-btn tl-btn-primary" disabled={!d.canSubmit} onClick={d.submit}>{d.busy ? 'Registering…' : 'Register this version'}</button>
      </div>
      <Problem text={d.error} retry={d.retry} />
    </section>
  );
}

function Estimate({ e }) {
  return (
    <div role="region" aria-label={e.title} className="tl-subform">
      <div className="tl-head-row"><b className="tl-grow">{e.title}</b><button type="button" className="tl-link" onClick={e.close}>{'Cancel'}</button></div>
      {e.loading ? <p className="tl-meta">{'Reading…'}</p> : null}
      <Problem text={e.error} />
      {e.text ? <p className="tl-text">{e.text}</p> : null}
      <div className="tl-form tl-form-end">
        <div className="tl-field-row">
          <label htmlFor="tlj-cap">{'Cost cap in dollars (optional)'}</label>
          <input id="tlj-cap" className="tl-fit tl-narrow" inputMode="decimal" value={e.cap} onChange={e.setCap} />
        </div>
        <button type="button" className="tl-btn tl-btn-primary" disabled={!e.canStart} onClick={e.start}>{e.busy ? 'Starting…' : 'Start'}</button>
      </div>
      <Problem text={e.startError} retry={e.retry} />
    </div>
  );
}

function RunView({ r }) {
  return (
    <div className="tl-drawer-section">
      <div className="tl-head-row"><b className="tl-grow">{r.title}</b><span className="tl-chip">{r.status}</span></div>
      {r.loading ? <p className="tl-meta">{'Reading…'}</p> : null}
      <Problem text={r.error} />
      {r.progress ? <p aria-live="polite" className="tl-meta">{r.progress}</p> : null}
      <ul className="tl-tiles">
        {[['TPR', r.tpr], ['TNR', r.tnr], ['Agreement', r.agreement], ['Answers', r.n], ['Cost', r.cost]].map(([label, value]) => (
          <li key={label} className="tl-tile"><span className="tl-meta">{label}</span><strong>{value}</strong></li>
        ))}
      </ul>
      {r.counts ? <p className="tl-meta">{r.counts}</p> : null}
      {r.errors ? <p className="tl-warn">{r.errors}</p> : null}
      {r.truncated ? <p className="tl-meta">{r.truncated}</p> : null}
      {r.finalNote ? <p className="tl-meta">{r.finalNote}</p> : null}
      {r.disagreements.length ? (
        <div>
          <h4 className="tl-h4">{'Disagreements' + (r.untriaged ? ' · ' + r.untriaged + ' to triage' : '')}</h4>
          <ul className="tl-rows-list">
            {r.disagreements.map((d) => (
              <li key={d.turnId} className="tl-row-block">
                <div className="tl-text"><b>{d.kind}</b></div>
                {d.excerpt ? <pre className="tl-pre">{d.excerpt}</pre> : null}
                {d.triageLabel ? <div className="tl-meta">{d.triageLabel + (d.by ? ' · ' + d.by : '') + (d.note ? ' · ' + d.note : '')}</div> : null}
                {d.choices.length ? (
                  <div role="group" aria-label="Triage" className="tl-actions tl-actions-tight">
                    {d.choices.map((ch) => (
                      <button key={ch.key} type="button" className={'tl-btn tl-btn-small' + (ch.on ? ' tl-btn-primary' : '')} aria-pressed={ch.on ? 'true' : 'false'} disabled={d.busy} onClick={ch.pick}>{ch.label}</button>
                    ))}
                    <input aria-label="Triage note" placeholder="Note (optional)" className="tl-input tl-input-wide" value={d.noteDraft} onChange={d.setNote} />
                  </div>
                ) : null}
                <Problem text={d.error} retry={d.retry} reload={d.reload} />
              </li>
            ))}
          </ul>
        </div>
      ) : r.emptyText ? <p className="tl-meta">{r.emptyText}</p> : null}
    </div>
  );
}

function Decision({ d }) {
  return (
    <div className="tl-drawer-section">
      <h4 className="tl-h4">{'Decision after the held-out test'}</h4>
      {d.current ? <p className="tl-text">{'Recorded: ' + d.current + (d.note ? ' — ' + d.note : '')}</p> : <p className="tl-meta">{'No decision recorded yet.'}</p>}
      <div role="radiogroup" aria-label="Decision" className="tl-radios">
        {d.options.map((o) => (
          <label key={o.key} className="tl-check-row"><input type="radio" name="tlj-decision" checked={o.on} onChange={o.pick} />{' ' + o.label}</label>
        ))}
      </div>
      <div className="tl-field-row tl-gap-top">
        <label htmlFor="tlj-decision-note">{'Note (optional)'}</label>
        <input id="tlj-decision-note" value={d.noteDraft} onChange={d.setNote} />
      </div>
      <div className="tl-actions"><button type="button" className="tl-btn tl-btn-primary" disabled={!d.canSave} onClick={d.save}>{d.busy ? 'Saving…' : 'Record decision'}</button></div>
      <Problem text={d.error} reload={d.reload} />
    </div>
  );
}

function Threshold({ t }) {
  return (
    <div className="tl-drawer-section">
      <h4 className="tl-h4">{'Monitoring threshold'}</h4>
      <p className="tl-meta">{'Now ' + t.current + '. Monitoring flags this failure mode when its corrected failure rate goes above the threshold.'}</p>
      <div className="tl-form tl-form-end">
        <div className="tl-field-row">
          <label htmlFor="tlj-threshold">{'New threshold (a share between 0 and 1)'}</label>
          <input id="tlj-threshold" className="tl-fit tl-narrow" inputMode="decimal" value={t.draft} onChange={t.setDraft} />
        </div>
        <button type="button" className="tl-btn tl-btn-primary" disabled={!t.canSave} onClick={t.save}>{t.busy ? 'Saving…' : 'Save threshold'}</button>
      </div>
      {t.invalid ? <p className="tl-warn">{t.invalid}</p> : null}
      <Problem text={t.error} reload={t.reload} />
    </div>
  );
}

function IterationTable({ rows }) {
  return (
    <div className="tl-rows">
      <table className="tl-table">
        <thead>
          <tr>
            <th scope="col">{'Version'}</th><th scope="col">{'Change'}</th><th scope="col">{'Dev runs'}</th><th scope="col">{'Dev TPR'}</th>
            <th scope="col">{'Dev TNR'}</th><th scope="col">{'Disagreements'}</th><th scope="col">{'Label changes'}</th>
            <th scope="col">{'Inputs changed'}</th><th scope="col">{'Frozen'}</th><th scope="col">{'Held-out TPR / TNR'}</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.version}>
              <td>{r.version}</td><td className="tl-wrap">{r.change}</td><td>{r.devRuns}</td><td>{r.devTpr}</td><td>{r.devTnr}</td>
              <td>{r.disagreements}</td><td>{r.flips}</td><td>{r.inputChanged}</td><td>{r.frozen}</td><td>{r.test}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function JudgePage({ j, run }) {
  return (
    <section className="tl-card" aria-label="Judge">
      <div className="tl-head-row">
        <h3 className="tl-title tl-grow">{j.title}</h3>
        <span className="tl-chip">{j.status}</span>
        {j.inUse ? <span className="tl-pill tl-pill-ok">{'In use'}</span> : null}
        <button type="button" className="tl-link" onClick={j.reload}>{'Refresh'}</button>
        <button type="button" className="tl-link" aria-label="Close this judge" onClick={j.close}>{'Close'}</button>
      </div>
      {j.loading ? <p className="tl-meta">{'Reading…'}</p> : null}
      <Problem text={j.error} retry={j.reload} />
      {j.frozenText ? <p className="tl-text">{j.frozenText}</p> : null}
      {j.changeNote ? <p className="tl-meta">{'What changed: ' + j.changeNote}</p> : null}
      {j.warnings.map((w, i) => <p key={i + w} className="tl-warn">{w}</p>)}

      <h4 className="tl-h4">{'Prompt'}</h4>
      {j.check ? <p className="tl-text">{'Code check: ' + j.check.label + ' (' + j.check.version + ')'}</p> : null}
      {j.parts.map((p) => (
        <div key={p.label}><div className="tl-kick">{p.label}</div><p className="tl-text">{p.text}</p></div>
      ))}
      {j.examples.length ? (
        <div>
          <div className="tl-kick tl-gap-top">{'Few-shot examples, from the train answers'}</div>
          {j.examples.map((e) => (
            <div key={e.turnId} className="tl-gap-top">
              <div className="tl-text"><b>{e.role}</b>{' · ' + e.verdict + ' · ' + e.critique}</div>
              <details className="tl-panel"><summary>{'Source answer'}</summary><pre className="tl-pre">{e.source || '—'}</pre></details>
            </div>
          ))}
        </div>
      ) : null}
      {j.promptText ? <details className="tl-panel"><summary>{'Full prompt'}</summary><pre className="tl-pre">{j.promptText}</pre></details> : null}

      {j.preview.length ? (
        <div>
          <h4 className="tl-h4">{'Preview on 5 dev answers'}</h4>
          <ul className="tl-rows-list">
            {j.preview.map((p) => (
              <li key={p.turnId} className="tl-row-block">
                <div className="tl-inline">
                  <span className="tl-text">{p.label}</span>
                  <button type="button" className="tl-btn tl-btn-small" disabled={p.busy} onClick={p.run}>{p.busy ? 'Judging…' : 'Preview'}</button>
                </div>
                {p.result ? <div className="tl-text">{p.result}</div> : null}
                <Problem text={p.error} retry={p.retry} />
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <h4 className="tl-h4">{'Runs'}</h4>
      <div className="tl-actions tl-actions-tight">
        <button type="button" className="tl-btn tl-btn-primary" disabled={!j.dev.enabled} onClick={j.dev.start}>{j.dev.label}</button>
        <button type="button" className="tl-btn" disabled={!j.test.enabled} onClick={j.test.start}>{j.test.label}</button>
        {j.freeze ? <button type="button" className="tl-btn" disabled={!j.freeze.can} onClick={j.freeze.open}>{'Freeze…'}</button> : null}
        {j.revise ? <button type="button" className="tl-link" onClick={j.revise}>{'Write a new version'}</button> : null}
      </div>
      {j.dev.note ? <p className="tl-meta">{j.dev.note}</p> : null}
      {j.test.note ? <p className="tl-meta">{j.test.note}</p> : null}
      {j.freeze && j.freeze.note ? <p className="tl-meta">{j.freeze.note}</p> : null}
      {j.estimate ? <Estimate e={j.estimate} /> : null}
      {j.runs.length ? (
        <ul className="tl-rows-list">
          {j.runs.map((x) => (
            <li key={x.id}>
              <button type="button" className={'tl-row' + (x.on ? ' tl-row-on' : '')} aria-current={x.on ? 'true' : undefined} onClick={x.open}>
                <span className="tl-row-q">{x.label}</span><span className="tl-meta">{x.status}</span>
              </button>
            </li>
          ))}
        </ul>
      ) : <p className="tl-meta">{'No runs yet.'}</p>}
      {run ? <RunView r={run} /> : null}
      {j.decision ? <Decision d={j.decision} /> : null}
      {j.threshold ? <Threshold t={j.threshold} /> : null}

      <h4 className="tl-h4">{'Iteration log'}</h4>
      {j.iterations.length ? <IterationTable rows={j.iterations} /> : <p className="tl-meta">{'No versions yet.'}</p>}
    </section>
  );
}

function Report({ r }) {
  return (
    <section className="tl-card" aria-label="Failure report">
      <div className="tl-head-row">
        <h3 className="tl-title tl-grow">{'Failure report'}</h3>
        <button type="button" className="tl-btn" disabled={r.loading} onClick={r.load}>{r.loaded ? 'Read it again' : 'Read the failure report'}</button>
        <button type="button" className="tl-btn" disabled={r.downloadBusy} onClick={r.download}>{'Download (Markdown)'}</button>
      </div>
      {r.loading ? <p className="tl-meta">{'Reading…'}</p> : null}
      <Problem text={r.error} retry={r.load} />
      <Problem text={r.downloadError} />
      {r.generated ? <p className="tl-meta">{'Generated ' + r.generated + '. Labelled counts are shares of the reviewed sample, not rates for all answers.'}</p> : null}
      {r.empty ? <p className="tl-meta">{'No confirmed failure modes yet.'}</p> : null}
      {r.entries.map((e) => (
        <div key={e.name} className="tl-row-block">
          <div className="tl-text"><b>{e.name}</b></div>
          <div className="tl-meta">{e.definition}</div>
          <div className="tl-text">{e.evaluator}</div>
          <div className="tl-text">{e.labelled}</div>
          <div className="tl-text">{e.prevalence}</div>
          <div className="tl-meta">{'Evaluation case candidates: ' + e.cases}</div>
        </div>
      ))}
    </section>
  );
}

function FreezeDialog({ f }) {
  const ref = React.useRef(f);
  ref.current = f;
  React.useEffect(() => {
    if (typeof document === 'undefined' || !document.addEventListener) return undefined;
    const onKey = (e) => { if (e.key === 'Escape' && !ref.current.busy) ref.current.cancel(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, []);
  const node = (
    <div className="tl-overlay">
      <div className="tl-modal" role="dialog" aria-modal="true" aria-labelledby="tlj-freeze-title" aria-describedby="tlj-freeze-desc">
        <h2 id="tlj-freeze-title" className="tl-title">{'Freeze this judge?'}</h2>
        <p id="tlj-freeze-desc" className="tl-text">{'Freezing is one-way. The prompt, the model and the examples can no longer change, dev runs stop, and the held-out test can run once. To keep revising after this, write a new version.'}</p>
        <div className="tl-field-row">
          <label htmlFor="tlj-freeze-reason">{'Why stop revising?'}</label>
          <textarea id="tlj-freeze-reason" className="tl-textarea" autoFocus aria-describedby="tlj-freeze-hint" value={f.reason} onChange={f.setReason} />
        </div>
        <div id="tlj-freeze-hint" className="tl-meta">{f.hint}</div>
        <Problem text={f.error} reload={f.reload} />
        <div className="tl-actions tl-actions-end">
          <button type="button" className="tl-btn" disabled={f.busy} onClick={f.cancel}>{'Cancel'}</button>
          <button type="button" className={'tl-btn' + (f.canConfirm ? ' tl-btn-primary' : '')} disabled={!f.canConfirm} onClick={f.confirm}>{f.busy ? 'Freezing…' : 'Freeze'}</button>
        </div>
      </div>
    </div>
  );
  const target = typeof document !== 'undefined' && document && document.body ? document.body : null;
  return target ? createPortal(node, target) : node;
}

export default function Judges({ s, now }) {
  const v = judgesVals(s, a, now === undefined ? Date.now() : now);
  const j = v.tljJudge;
  // Read when the tab opens, and again for another company; leaving the tab, or the page, stops the run watch.
  React.useEffect(() => {
    a.openTab();
    return () => { a.stopWatch(); };
  }, [s.seq]);
  // The platform team is known once the failure-mode list has been read: what it had open is read again.
  React.useEffect(() => { if (v.tljOwner) a.reopen(); }, [v.tljOwner, s.seq]);
  return (
    <div className="tl-stack">
      {v.tljReadOnlyNote ? <p role="note" className="tl-meta">{v.tljReadOnlyNote}</p> : null}
      <section className="tl-card" aria-label="Judges">
        <div className="tl-head-row"><h3 className="tl-title tl-grow">{'Judges'}</h3><button type="button" className="tl-link" onClick={v.tljReload}>{'Refresh'}</button></div>
        <Problem text={v.tljError} retry={v.tljReload} />
        {v.tljLoading ? <p className="tl-meta">{'Reading…'}</p>
          : v.tljEmpty ? <p className="tl-meta">{v.tljEmptyText}</p>
          : v.tljRows.length ? <JudgesTable rows={v.tljRows} /> : null}
        <p className="tl-meta">{'TPR: of the answers people passed, the share the judge passed. TNR: of the answers people failed, the share the judge failed. Brackets: the 95% interval.'}</p>
      </section>
      <section className="tl-card" aria-label="Code checks">
        <div className="tl-head-row"><h3 className="tl-title tl-grow">{'Code checks'}</h3></div>
        <Problem text={v.tljChecksError} retry={v.tljReload} />
        {v.tljChecksLoading ? <p className="tl-meta">{'Reading…'}</p>
          : v.tljChecks.length ? <ChecksTable checks={v.tljChecks} />
          : v.tljChecksEmpty ? <p className="tl-meta">{'No code checks.'}</p> : null}
      </section>
      {v.tljOwner ? <Modes v={v} /> : null}
      {v.tljReady ? <Readiness r={v.tljReady} /> : null}
      {v.tljDraft ? <DraftForm d={v.tljDraft} /> : null}
      {j ? <JudgePage j={j} run={v.tljRun} /> : null}
      {v.tljReport ? <Report r={v.tljReport} /> : null}
      {j && j.freezeDialog ? <FreezeDialog f={j.freezeDialog} /> : null}
    </div>
  );
}
