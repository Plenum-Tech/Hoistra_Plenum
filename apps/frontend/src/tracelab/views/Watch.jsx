// Watch — the Trace Lab's Watch tab (logic/watch.js watchVals): monitoring batches with the corrected failure rate per week
// and a threshold per failure mode, code-check rates, eval cases (regression and capability), release checks and the
// "checked automatically vs by people" trend. The platform team (any superadmin) runs monitoring (Run now, Resume, the review
// batch): viewing a company it is that company's own batch, viewing none it covers every company and the batches of every
// company are listed. A company admin reads the company's own batches, Due marker and charts and owns its eval cases and
// release checks, with nothing to start. A pure view: every number comes from the view model, and a missing one reads "—".
//
// The tab reads when it opens and again for another company (a company switch bumps the store's seq); a company reader's
// cases and release checks are read once the service has said who the person is. If that read fails, the page says so with
// Retry (Refresh reads it again) instead of leaving the company sections on "Reading…".
import React from 'react';
import * as a from '../watchActions.js';
import { watchVals } from '../logic/watch.js';

// The pill of a release decision, by the view model's tone.
const TONE = { ok: 'tl-pill tl-pill-ok', risk: 'tl-pill tl-pill-risk', warn: 'tl-pill tl-pill-warn' };

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

function Section({ title, right, children }) {
  return (
    <section className="tl-card" aria-label={title}>
      <div className="tl-head-row">
        <h3 className="tl-title tl-grow">{title}</h3>
        {right}
      </div>
      {children}
    </section>
  );
}

function PrevalenceChart({ chart }) {
  const g = chart.geometry;
  if (chart.loading) return <p className="tl-meta">{'Reading…'}</p>;
  if (chart.error) return <Problem text={chart.error} />;
  if (g.empty) return <p className="tl-meta">{'No batches yet'}</p>;
  return (
    <svg viewBox={'0 0 ' + g.width + ' ' + g.height} role="img" aria-label={'Corrected failure rate by week for ' + chart.name} className="tl-chart">
      {g.ticks.map((t, i) => (
        <g key={'tick' + i}>
          <line x1={g.padL} x2={g.width - g.padR} y1={t.y} y2={t.y} className="tl-chart-grid" />
          <text x={g.padL - 6} y={t.y + 3} textAnchor="end" className="tl-chart-tick">{t.label}</text>
        </g>
      ))}
      {g.thresholdY !== null ? (
        <g>
          <line x1={g.padL} x2={g.width - g.padR} y1={g.thresholdY} y2={g.thresholdY} className="tl-chart-thr" strokeDasharray="5 4" />
          <text x={g.width - g.padR} y={g.thresholdY - 4} textAnchor="end" className="tl-chart-thr-label">{g.thresholdLabel}</text>
        </g>
      ) : null}
      {g.points.map((p, i) => (
        <g key={'point' + i}>
          <title>{p.title}</title>
          {p.low !== null && p.high !== null ? <line x1={p.x} x2={p.x} y1={p.low} y2={p.high} className="tl-chart-whisker" /> : null}
          {p.raw !== null ? <circle cx={p.x} cy={p.raw} r="3.5" className="tl-chart-raw" /> : null}
          {p.corrected !== null ? <circle cx={p.x} cy={p.corrected} r="4.5" className={p.crossed ? 'tl-chart-dot tl-chart-dot-risk' : 'tl-chart-dot'} /> : null}
          <text x={p.x} y={g.height - 10} textAnchor="middle" className="tl-chart-label">{p.label}</text>
        </g>
      ))}
    </svg>
  );
}

function PlanPanel({ plan }) {
  const d = plan.data;
  return (
    <section className="tl-card" aria-label="Run monitoring now">
      <div className="tl-head-row">
        <h3 className="tl-title tl-grow">{'Run monitoring now'}</h3>
        <button type="button" className="tl-link" onClick={plan.close}>{'Close'}</button>
      </div>
      {plan.scopeText ? <p className="tl-meta">{plan.scopeText}</p> : null}
      {plan.loading && !d ? <p className="tl-meta">{'Reading…'}</p> : null}
      <Problem text={plan.error} retry={plan.refresh} />
      {d ? (
        <div className="tl-fields">
          <p className="tl-text">{d.period + ': ' + d.nWindow + ' chat answers. Every one gets the code checks; ' + d.nRandom + ' are drawn at random for the judges, plus ' + d.nRiskOnly + ' from the risk groups.'}</p>
          <p className="tl-text">{'Judges in use: ' + (d.judges.length ? d.judges.join(', ') : 'none yet') + '. Estimated cost: ' + d.cost + '.'}</p>
          {d.warnings.map((w, i) => <p key={i} className="tl-warn">{w}</p>)}
        </div>
      ) : null}
      <div className="tl-fields-row tl-fields-end tl-gap-top">
        <label className="tl-field">{'Sample rate '}<input className="tl-input tl-narrow" value={plan.rate} placeholder="0.25" onChange={plan.setRate} onBlur={plan.refresh} /></label>
        <label className="tl-field">{'Cost cap ($) '}<input className="tl-input tl-narrow" value={plan.cap} placeholder="5" onChange={plan.setCap} /></label>
        <div className="tl-actions">
          <button type="button" className="tl-btn tl-btn-primary" disabled={plan.starting || !d} onClick={plan.start}>{plan.starting ? 'Starting…' : 'Start'}</button>
        </div>
      </div>
    </section>
  );
}

function BatchPanel({ b }) {
  return (
    <Section title={'Batch ' + (b.period || '')} right={<button type="button" className="tl-link" aria-label="Close batch" onClick={b.close}>{'Close'}</button>}>
      {b.loading ? <p className="tl-meta">{'Reading…'}</p> : null}
      <Problem text={b.error} />
      {b.modes.length ? (
        <div className="tl-rows">
          <table className="tl-table">
            <thead>
              <tr>
                <th scope="col">{'Failure mode'}</th><th scope="col" className="tl-num">{'Corrected rate'}</th><th scope="col" className="tl-num">{'95% interval'}</th>
                <th scope="col" className="tl-num">{'Flagged in the sample'}</th><th scope="col" className="tl-num">{'Answers judged'}</th><th scope="col">{'Threshold'}</th><th scope="col" />
              </tr>
            </thead>
            <tbody>
              {b.modes.map((m, i) => (
                <tr key={i}>
                  <td>
                    {m.name}{m.crossed ? <span className="tl-pill tl-pill-risk tl-gap-left">{'Above threshold'}</span> : null}
                    {m.warning ? <div className="tl-warn">{m.warning}</div> : null}
                    {m.reason ? <div className="tl-meta">{m.reason}</div> : null}
                  </td>
                  <td className="tl-num">{m.corrected}</td><td className="tl-num">{m.interval}</td><td className="tl-num">{m.raw}</td><td className="tl-num">{m.nSample}</td>
                  <td>{m.threshold}</td>
                  <td>
                    {m.reviewIds.length ? <span className="tl-meta">{'Review batch opened (' + m.reviewIds.length + ')'}</span>
                      : m.canReview ? <button type="button" className="tl-btn tl-btn-small" disabled={m.reviewing} onClick={m.review}>{'Open flagged answers as a review batch'}</button> : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {b.checks.length ? (
        <p className="tl-meta">{b.checks.map((x) => x.label + ' ' + x.rate + ' (' + x.flagged + ' of ' + x.decided + ', ' + x.tag + ')').join(' · ')}</p>
      ) : null}
      <h4 className="tl-h4 tl-gap-top">{'Flagged answers'}</h4>
      {b.flagged.length ? (
        <ul className="tl-rows-list">
          {b.flagged.map((f) => (
            <li key={f.turnId + f.mode} className="tl-row-block">
              {f.openAnswer ? <button type="button" className="tl-link" onClick={f.openAnswer}>{f.question}</button> : <span className="tl-text">{f.question}</span>}
              <div className="tl-meta">{[f.mode, f.group].filter(Boolean).join(' · ')}</div>
              {f.critique ? <div className="tl-text">{f.critique}</div> : null}
              {f.hint ? <div className="tl-meta">{f.hint}</div> : null}
            </li>
          ))}
        </ul>
      ) : <p className="tl-meta">{'No flagged answers in this batch.'}</p>}
    </Section>
  );
}

function AutomationBars({ weeks }) {
  return (
    <div className="tl-auto">
      {weeks.map((w) => (
        <div key={w.week} className="tl-auto-row">
          <span className="tl-auto-week">{w.week}</span>
          {w.empty ? <span className="tl-meta">{'No answer was checked this week'}</span> : (
            <div role="img" aria-label={w.week + ': ' + w.autoText + ' checked automatically, ' + w.peopleText + ' by people'} className="tl-auto-bar">
              <div className="tl-auto-seg" style={{ width: w.autoPct + '%' }} />
              <div className="tl-auto-seg tl-auto-seg-people" style={{ width: w.peoplePct + '%' }} />
            </div>
          )}
          <span className="tl-meta">{w.empty ? '—' : w.autoText + ' automatic · ' + w.peopleText + ' people'}</span>
        </div>
      ))}
    </div>
  );
}

function Cases({ v }) {
  return (
    <Section title="Eval cases">
      {!v.tlwKnown ? <p className="tl-meta">{v.tlwIdentityError ? v.tlwIdentityHint : 'Reading…'}</p>
        : !v.tlwCanCases ? <p className="tl-meta">{v.tlwCasesHint}</p>
        : v.tlwCasesLoading ? <p className="tl-meta">{'Reading…'}</p>
        : v.tlwCasesEmpty ? <p className="tl-meta">{'No eval cases yet. A case starts from an answer marked wrong, or from an answer a batch flagged.'}</p>
        : v.tlwCases.length ? (
          <div className="tl-rows">
            <table className="tl-table">
              <thead>
                <tr>
                  <th scope="col">{'Case'}</th><th scope="col">{'Failure mode'}</th><th scope="col">{'Kind'}</th><th scope="col">{'Status'}</th>
                  <th scope="col" className="tl-num">{'pass@1'}</th><th scope="col" className="tl-num">{'pass@3'}</th><th scope="col" className="tl-num">{'pass@5'}</th>
                  <th scope="col" className="tl-num">{'pass^k'}</th><th scope="col" />
                </tr>
              </thead>
              <tbody>
                {v.tlwCases.map((k) => (
                  <tr key={k.id}>
                    <td>{k.key}<div className="tl-meta tl-wrap">{k.question}</div></td>
                    <td>{k.mode}</td><td>{k.kindText}</td><td>{k.statusText}</td>
                    <td className="tl-num">{k.passAt1}</td><td className="tl-num">{k.passAt3}</td><td className="tl-num">{k.passAt5}</td>
                    <td className="tl-num" title={k.passHatTitle || undefined}>{k.passHat}</td>
                    <td className="tl-wrap">
                      {k.canConfirm ? (
                        <div className="tl-fields tl-fields-tight">
                          <textarea className="tl-textarea" aria-label="What a right answer says" placeholder="What a right answer says" value={k.answer} onChange={k.setAnswer} />
                          <select className="tl-input" aria-label="How the answer is checked" value={k.evaluation} onChange={k.setEvaluation}>
                            <option value="objective">{'Against this exact answer'}</option>
                            <option value="judgment">{'Against this as a criterion'}</option>
                          </select>
                          <div className="tl-actions">
                            <button type="button" className="tl-btn tl-btn-primary" disabled={k.busy} onClick={k.confirm}>{'Confirm the failure'}</button>
                          </div>
                        </div>
                      ) : null}
                      <div className="tl-pills">
                        {k.canBaseline ? <button type="button" className="tl-btn tl-btn-primary tl-btn-small" disabled={k.busy} onClick={k.baseline}>{'Baseline (5 runs)'}</button> : null}
                        {k.canRepeat ? (
                          <span className="tl-inline tl-stack-flush">
                            <input className="tl-input tl-narrow" aria-label="Times to repeat" value={k.n} onChange={k.setN} />
                            <button type="button" className="tl-btn tl-btn-small" disabled={k.busy} onClick={k.repeat}>{'Repeat'}</button>
                          </span>
                        ) : null}
                        {k.canRetire ? (
                          <span className="tl-inline tl-stack-flush">
                            <input className="tl-input" aria-label="Why retire it" placeholder="Why retire it" value={k.reason} onChange={k.setReason} />
                            <button type="button" className="tl-btn tl-btn-small" disabled={k.busy} onClick={k.retire}>{'Retire'}</button>
                          </span>
                        ) : null}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      <Problem text={v.tlwCasesError} retry={v.tlwReload} />
    </Section>
  );
}

function Releases({ v }) {
  const rel = v.tlwRelease;
  const right = v.tlwCanCases ? (
    <span className="tl-inline tl-stack-flush">
      <label className="tl-field">{'Runs per case '}<input className="tl-input tl-narrow" value={rel.k} onChange={rel.setK} /></label>
      <label className="tl-check-row"><input type="checkbox" checked={rel.includeCapability} onChange={rel.toggleCapability} />{' Include capability cases'}</label>
      <button type="button" className="tl-btn tl-btn-primary" disabled={rel.starting} onClick={rel.start}>{rel.starting ? 'Starting…' : 'Run a release check'}</button>
    </span>
  ) : null;
  return (
    <Section title="Release checks" right={right}>
      {!v.tlwKnown ? <p className="tl-meta">{v.tlwIdentityError ? v.tlwIdentityHint : 'Reading…'}</p>
        : !v.tlwCanCases ? <p className="tl-meta">{v.tlwCasesHint}</p>
        : rel.loading ? <p className="tl-meta">{'Reading…'}</p>
        : rel.empty ? <p className="tl-meta">{'No release check yet.'}</p>
        : rel.list.length ? (
          <div className="tl-rows">
            <table className="tl-table">
              <thead>
                <tr><th scope="col">{'Run'}</th><th scope="col">{'Decision'}</th><th scope="col">{'Why'}</th><th scope="col" className="tl-num">{'Runs per case'}</th><th scope="col">{'Report'}</th></tr>
              </thead>
              <tbody>
                {rel.list.map((r) => (
                  <tr key={r.id}>
                    <td><button type="button" className="tl-link" onClick={r.openRun}>{r.label}</button><div className="tl-meta">{r.when}</div></td>
                    <td><span className={TONE[r.tone] || 'tl-pill'}>{r.decisionText}</span></td>
                    <td className="tl-wrap">{r.reason}</td>
                    <td className="tl-num">{r.k}</td>
                    <td>
                      <div className="tl-pills">
                        <button type="button" className="tl-btn tl-btn-small" onClick={r.downloadJson}>{'Download JSON'}</button>
                        <button type="button" className="tl-btn tl-btn-small" onClick={r.downloadMd}>{'Download Markdown'}</button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      <Problem text={rel.error} retry={v.tlwReload} />
      {v.tlwCanCases ? <p className="tl-meta">{'Run it on the deployment you are about to ship. Regression cases must pass every run; capability cases never block. Attach the JSON report to the deploy.'}</p> : null}
    </Section>
  );
}

export default function Watch({ s, now, company }) {
  const v = watchVals(s, a, now === undefined ? Date.now() : now, company);
  // Read when the tab opens, and again for another company (a new seq).
  React.useEffect(() => { a.openTab(); }, [s.seq]);
  // A company reader's cases and release checks, once the service has said who the person is.
  React.useEffect(() => { if (v.tlwCompany) a.loadCompany(); }, [v.tlwCompany, s.seq]);
  return (
    <div className="tl-stack">
      <div className="tl-head-row">
        <span className="tl-kick">{v.tlwScope}</span>
        {v.tlwDue ? <span className={'tl-pill ' + (v.tlwDue.isDue ? 'tl-pill-risk' : 'tl-pill-ok')}>{v.tlwDue.text}</span> : null}
        <span className="tl-grow" />
        <button type="button" className="tl-link" onClick={v.tlwReload}>{'Refresh'}</button>
        {v.tlwCanStart ? <button type="button" className="tl-btn tl-btn-primary" onClick={v.tlwRunNow}>{'Run now'}</button> : null}
      </div>
      {v.tlwPlan ? <PlanPanel plan={v.tlwPlan} /> : null}
      <Problem text={v.tlwIdentityError} retry={v.tlwReload} />
      <Problem text={v.tlwError} retry={v.tlwReload} />
      {v.tlwWrites.map((w) => <Problem key={w.key} text={w.error} retry={w.retry} reload={w.reload} />)}

      <Section title="Monitoring batches">
        {v.tlwLoading ? <p className="tl-meta">{'Reading…'}</p>
          : v.tlwBatchesEmpty ? <p className="tl-meta">{v.tlwEmptyText}</p>
          : v.tlwBatches.length ? (
            <div className="tl-rows">
              <table className="tl-table">
                <thead>
                  <tr>
                    <th scope="col">{'Week'}</th><th scope="col">{'Scope'}</th><th scope="col">{'Status'}</th><th scope="col" className="tl-num">{'Answers'}</th>
                    <th scope="col" className="tl-num">{'Sampled'}</th><th scope="col" className="tl-num">{'Cost'}</th><th scope="col" />
                  </tr>
                </thead>
                <tbody>
                  {v.tlwBatches.map((b) => (
                    <tr key={b.id}>
                      <td><button type="button" className="tl-link" aria-current={b.on ? 'true' : undefined} onClick={b.open}>{b.period}</button></td>
                      <td>{b.scope}</td>
                      <td>{b.statusText}{b.crossed ? <span className="tl-pill tl-pill-risk tl-gap-left">{'Above threshold'}</span> : null}</td>
                      <td className="tl-num">{b.nWindow}</td><td className="tl-num">{b.nRandom}</td><td className="tl-num">{b.cost}</td>
                      <td>{b.resume ? <button type="button" className="tl-btn tl-btn-small" disabled={b.resuming} onClick={b.resume}>{'Resume'}</button> : null}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}
      </Section>
      {v.tlwBatch ? <BatchPanel b={v.tlwBatch} /> : null}

      <Section title="Corrected failure rate by week">
        {v.tlwLoading ? <p className="tl-meta">{'Reading…'}</p>
          : v.tlwCharts.length ? v.tlwCharts.map((ch) => (
            <div key={ch.modeId} className="tl-drawer-section">
              <div className="tl-text"><b>{ch.name}</b>{ch.crossed ? <span className="tl-pill tl-pill-risk tl-gap-left">{'Above threshold'}</span> : null}</div>
              <PrevalenceChart chart={ch} />
            </div>
          )) : v.tlwError ? null : <p className="tl-meta">{'No batches yet'}</p>}
        <p className="tl-meta">{'Filled dot: the corrected failure rate, with its 95% interval as the line through it. Hollow dot: the share flagged in the sample. Dashed line: the threshold.'}</p>
      </Section>

      <Section title="Code-check rates">
        <Problem text={v.tlwChecksError} retry={v.tlwReload} />
        {v.tlwChecks.length ? (
          <div className="tl-rows">
            <table className="tl-table">
              <thead>
                <tr>
                  <th scope="col">{'Check'}</th><th scope="col">{'Status'}</th>
                  {(v.tlwChecks[0].weeks || []).map((w) => <th key={w.period} scope="col" className="tl-num">{w.period}</th>)}
                </tr>
              </thead>
              <tbody>
                {v.tlwChecks.map((x) => (
                  <tr key={x.key}>
                    <td>{x.label}</td><td>{x.tag}</td>
                    {x.weeks.map((w) => <td key={w.period} className="tl-num" title={w.n + ' answers'}>{w.rate}</td>)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : v.tlwLoading ? <p className="tl-meta">{'Reading…'}</p> : v.tlwChecksError ? null : <p className="tl-meta">{'No batches yet'}</p>}
      </Section>

      <Cases v={v} />
      <Releases v={v} />

      <Section title="Checked automatically vs by people">
        {v.tlwAuto.loading ? <p className="tl-meta">{'Reading…'}</p>
          : v.tlwAuto.error ? <Problem text={v.tlwAuto.error} retry={v.tlwReload} />
          : v.tlwAuto.empty ? <p className="tl-meta">{'No answer has been checked yet.'}</p>
          : v.tlwAuto.weeks.length ? (
            <div>
              <AutomationBars weeks={v.tlwAuto.weeks} />
              <div className="tl-rows">
                <table className="tl-table">
                  <thead>
                    <tr>
                      <th scope="col">{'Week'}</th><th scope="col" className="tl-num">{'Answers checked'}</th><th scope="col">{'Golden answers right'}</th>
                      <th scope="col">{'Fair-sample share right (sample fraction)'}</th><th scope="col" className="tl-num">{'Failure modes automated'}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {v.tlwAuto.weeks.map((w) => (
                      <tr key={w.week}>
                        <td>{w.week}</td><td className="tl-num">{w.n}</td><td>{w.golden}</td><td>{w.live}</td><td className="tl-num">{w.modesAutomated + ' of ' + w.modesConfirmed}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          ) : null}
        {v.tlwAuto.note ? <p className="tl-meta">{v.tlwAuto.note}</p> : null}
      </Section>
    </div>
  );
}
