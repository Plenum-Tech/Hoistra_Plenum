// ProgressTaxonomy — the failure-mode part of the Progress tab (logic/taxonomy.js, logic/proposals.js): label
// completeness, per-mode counts (first failure beside any instance), the discovery curve of new failure modes with the
// saturation hint, the failure-mode treemap with status pills, reviewers' unfiled notes, the failure modes companies
// proposed (the platform team accepts or rejects each; a company reads its own), the AI suggestion queue and
// (platform team) the AI proposal runs, and the requirement register. In the queue nothing is ticked for the reviewer,
// and "Accept selected" shows the server's audit before anything is approved. Missing data reads "—".
import React from 'react';
import * as a from '../taxonomyActions.js';
import * as sact from '../samplingActions.js';
import { progressView } from '../logic/sampling.js';
import { taxonomyModesVals } from '../logic/taxonomy.js';
import { taxonomyProposalsVals } from '../logic/proposals.js';
import Requirements from './Requirements.jsx';

function Head({ title, right }) {
  return (
    <div className="tl-list-head tl-head-row">
      <div className="tl-kick tl-grow">{title}</div>
      {right}
    </div>
  );
}

function Problem({ text, retry }) {
  if (!text) return null;
  return <div role="alert" className="tl-bad tl-inline">{text}{retry ? <button type="button" className="tl-btn" onClick={retry}>{'Retry'}</button> : null}</div>;
}

function ModeCurve({ curve }) {
  if (curve.empty) return <div className="tl-meta">{'—'}</div>;
  return (
    <svg viewBox={'0 0 ' + curve.width + ' ' + curve.height} role="img" aria-label={curve.caption} className="tl-curve">
      <line x1="28" y1={curve.height - 28} x2={curve.width - 28} y2={curve.height - 28} className="tl-curve-axis" />
      <text x="28" y="18" className="tl-curve-note">{'most in a block: ' + curve.max}</text>
      <path d={curve.path} className="tl-curve-line" />
      {curve.dots.map((dot) => (
        <g key={dot.x}>
          <circle cx={dot.x} cy={dot.y} r="10" className="tl-curve-hit"><title>{dot.label}</title></circle>
          <circle cx={dot.x} cy={dot.y} r="4" className="tl-curve-dot" />
        </g>
      ))}
    </svg>
  );
}

function Treemap({ map }) {
  return (
    <div>
      {map.blocks.length ? (
        <div className="tl-tree" role="list" aria-label="Failure modes by answers marked present" style={{ maxWidth: map.width + 'px', aspectRatio: map.width + ' / ' + map.height }}>
          {map.blocks.map((b) => (
            <button key={b.id} type="button" role="listitem" className={'tl-tree-block tl-tone-' + b.tone} title={b.title} onClick={b.open}
              style={{ left: (100 * b.x / map.width) + '%', top: (100 * b.y / map.height) + '%', width: (100 * b.w / map.width) + '%', height: (100 * b.h / map.height) + '%' }}>
              <span className="tl-tree-name">{b.name}</span>
              <span>{b.count + ' present'}</span>
              <span className={'tl-pill tl-pill-' + b.tone}>{b.statusText}</span>
            </button>
          ))}
        </div>
      ) : null}
      {map.unseen.length ? (
        <div className="tl-pills">
          <span className="tl-meta">{'Not marked present yet:'}</span>
          {map.unseen.map((u) => <button key={u.id} type="button" className={'tl-pill tl-pill-' + u.tone + ' tl-pill-btn'} onClick={u.open}>{u.name + ' · ' + u.statusText}</button>)}
        </div>
      ) : null}
      {map.retired.length ? (
        <div className="tl-pills">
          <span className="tl-meta">{'Retired:'}</span>
          {map.retired.map((r) => <button key={r.id} type="button" className="tl-pill tl-pill-dormant tl-pill-btn" onClick={r.open}>{r.name + ' · ' + r.text}</button>)}
        </div>
      ) : null}
      <div className="tl-meta tl-gap-top">{map.caption}</div>
    </div>
  );
}

function Unfiled({ u }) {
  return (
    <div>
      {u.ownerHint ? <div className="tl-guidance" role="note">{u.ownerHint}</div> : null}
      {u.notice ? <div role="status" className="tl-ok">{u.notice}{u.undo ? <button type="button" className="tl-btn tl-btn-small tl-gap-left" onClick={u.undo.run}>{'Undo'}</button> : null}</div> : null}
      {u.hiddenNote ? <div className="tl-meta">{u.hiddenNote}</div> : null}
      {u.canCreate || u.canPropose ? (
        <div className="tl-head-row tl-gap-top">
          <span className="tl-meta">{u.chosenCount ? u.chosenCount + ' chosen' : 'Tick notes that the same fix would clear to ' + (u.canPropose ? 'propose one failure mode from them.' : 'start one failure mode from them.')}</span>
          {u.newFromChosen ? <button type="button" className="tl-btn" onClick={u.newFromChosen}>{'New failure mode from these'}</button> : null}
          {u.proposeFromChosen ? <button type="button" className="tl-btn" onClick={u.proposeFromChosen}>{'Propose a failure mode from these'}</button> : null}
          {u.clearChosen ? <button type="button" className="tl-btn" onClick={u.clearChosen}>{'Clear'}</button> : null}
        </div>
      ) : null}
      {u.loading ? <div className="tl-meta">{'Reading…'}</div> : u.empty ? <div className="tl-meta">{u.emptyText}</div> : u.rows.map((n) => (
        <div key={n.id} className="tl-row-block">
          <div className="tl-row-line">
            {n.choose ? <input type="checkbox" aria-label="Choose this note" checked={n.chosen} onChange={n.choose} /> : null}
            <span>{'✎ ' + n.note}</span>
          </div>
          <div className="tl-meta">{(n.quote ? '“' + n.quote + '” · ' : '') + n.step + ' · ' + n.who + (n.when ? ' · ' + n.when : '')}</div>
          <div className="tl-actions tl-actions-tight">
            <button type="button" className="tl-btn" onClick={n.fileUnder}>{'File under…'}</button>
            {n.newMode ? <button type="button" className="tl-btn" onClick={n.newMode}>{'New failure mode'}</button> : null}
            {n.proposeMode ? <button type="button" className="tl-btn" onClick={n.proposeMode}>{'Propose failure mode'}</button> : null}
            {n.notAFailure ? <button type="button" className="tl-btn" onClick={n.notAFailure}>{'Not a failure'}</button> : null}
            {n.noModeFits ? <button type="button" className="tl-btn" onClick={n.noModeFits}>{'No mode fits'}</button> : null}
            {n.open ? <button type="button" className="tl-link" onClick={n.open}>{'Open the answer'}</button> : null}
          </div>
        </div>
      ))}
    </div>
  );
}

function ProposedRow({ r }) {
  return (
    <div className="tl-row-block">
      <div className="tl-row-line">
        <strong className="tl-grow">{r.name}</strong>
        <span className={'tl-pill' + (r.status === 'rejected' ? ' tl-pill-risk' : r.status === 'proposed' ? ' tl-pill-focus' : ' tl-pill-ok')}>{r.statusText}</span>
      </div>
      <div className="tl-meta">{(r.who ? 'Proposed by ' + r.who : 'Proposed') + (r.when ? ' · ' + r.when : '')}</div>
      <div>{r.definition}</div>
      {r.boundary ? <div className="tl-meta">{'Boundary: ' + r.boundary}</div> : null}
      <div className="tl-meta">{r.level + ' · ' + r.agent + (r.nearest ? ' · nearest: ' + r.nearest : '')}</div>
      {r.reason ? <div className="tl-guidance" role="note">{'Why it was rejected: ' + r.reason}</div> : null}
      {r.notes.length ? (
        <div>
          {r.notes.map((n) => <div key={n.id} className="tl-meta">{'✎ ' + n.note + (n.quote ? ' — “' + n.quote + '”' : '') + ' · ' + n.step + ' · ' + n.who}</div>)}
        </div>
      ) : null}
      {r.accept || r.reject ? (
        <div className="tl-actions tl-actions-tight">
          {r.accept ? <button type="button" className="tl-btn" onClick={r.accept} disabled={r.busy}>{'Accept'}</button> : null}
          {r.reject ? <button type="button" className="tl-btn" onClick={r.reject} disabled={r.busy}>{'Reject…'}</button> : null}
        </div>
      ) : null}
      {r.error ? <div role="alert" className="tl-bad tl-inline">{r.error}{r.retry ? <button type="button" className="tl-btn" onClick={r.retry}>{'Retry'}</button> : null}</div> : null}
    </div>
  );
}

function ModeProposed({ q }) {
  return (
    <div>
      <div className="tl-meta">{q.intro}</div>
      <Problem text={q.error} retry={q.reload} />
      {q.notice ? <div role="status" className="tl-ok">{q.notice}</div> : null}
      {q.loading ? <div className="tl-meta">{'Reading…'}</div> : q.empty ? <div className="tl-meta">{q.emptyText}</div> : q.rows.map((r) => <ProposedRow key={r.id} r={r} />)}
    </div>
  );
}

function QueueRow({ r }) {
  return (
    <div className="tl-row-block">
      <div className="tl-row-line">
        {r.checkable ? <input type="checkbox" aria-label={'Tick: ' + r.question} checked={r.checked} onChange={r.toggle} disabled={r.locked} /> : null}
        <span className="tl-grow">{r.open ? <button type="button" className="tl-link" onClick={r.open}>{r.question}</button> : r.question}</span>
        {r.modeName ? <span className="tl-pill">{r.modeName}</span> : null}
      </div>
      <div className={r.kind === 'group' ? '' : 'tl-sug-card tl-sug-card-tight'}>
        <div className="tl-sug-line">{r.suggestion}</div>
        {r.evidence ? <div className="tl-meta">{r.evidence}</div> : null}
        {r.quote ? <div className="tl-meta">{'“' + r.quote + '”'}</div> : null}
      </div>
      {r.notes.length ? (
        <div>
          {r.notes.map((n) => <div key={n.id} className="tl-meta">{'✎ ' + n.note + (n.quote ? ' — “' + n.quote + '”' : '') + ' · ' + n.step + ' · ' + n.who}</div>)}
        </div>
      ) : null}
      {r.draft ? (
        <div className="tl-fields-row">
          <label className="tl-field-row">{'Name'}<input value={r.draft.name} onChange={r.draft.set('name')} maxLength={64} /></label>
          <label className="tl-field-row">{'Definition'}<input value={r.draft.definition} onChange={r.draft.set('definition')} maxLength={1000} /></label>
          <label className="tl-field-row">{'Boundary'}<input value={r.draft.boundary} onChange={r.draft.set('boundary')} maxLength={1000} /></label>
          <label className="tl-field-row">{'Who decides it'}<select value={r.draft.level} onChange={r.draft.set('level')}>{[{ value: '', label: 'Choose…' }].concat(r.draft.levels).map((o) => <option key={o.value || 'none'} value={o.value}>{o.label}</option>)}</select></label>
          <label className="tl-field-row">{'Where the failure lives'}<select value={r.draft.agent} onChange={r.draft.set('agent')}>{[{ value: '', label: 'Choose…' }].concat(r.draft.agents).map((o) => <option key={o.value || 'none'} value={o.value}>{o.label}</option>)}</select></label>
        </div>
      ) : null}
      <div className="tl-actions tl-actions-tight">
        {r.accept ? <button type="button" className="tl-btn" onClick={r.accept} disabled={r.busy || r.locked}>{r.acceptLabel}</button> : null}
        {r.other ? <button type="button" className="tl-btn" onClick={r.other} disabled={r.busy || r.locked}>{r.otherLabel}</button> : null}
        {r.otherHint ? <span className="tl-meta">{r.otherHint}</span> : null}
      </div>
      {r.error ? <div role="alert" className="tl-bad">{r.error}</div> : null}
    </div>
  );
}

function Check({ k }) {
  return (
    <div role="region" aria-label="What the checks found" className="tl-audit">
      <div className="tl-verdict-label">{k.heading}</div>
      {k.refused.map((x) => <div key={'r' + x.rule + x.detail} role="alert" className="tl-bad">{'Rule ' + x.rule + ' — ' + x.title + ': ' + x.detail}</div>)}
      {k.flagged.map((x) => <div key={'f' + x.id} className="tl-warn">{'Left out: ' + x.detail}</div>)}
      {k.warnings.map((x) => <div key={'w' + x} className="tl-meta">{x}</div>)}
      {k.waitText ? <div className="tl-meta">{k.waitText}</div> : null}
      {k.spot.length ? (
        <div className="tl-fields tl-fields-tight">
          <div className="tl-meta">{k.spotText}</div>
          {k.spot.map((sp) => (
            <div key={sp.id} className="tl-spot">
              <div className="tl-sug-line">{sp.question}</div>
              <div className="tl-meta">{sp.modeName + (sp.definition ? ' — ' + sp.definition : '')}</div>
              <div className="tl-actions tl-actions-tight">
                <button type="button" className="tl-btn" onClick={sp.present} disabled={sp.busy}>{'Present'}</button>
                <button type="button" className="tl-btn" onClick={sp.absent} disabled={sp.busy}>{'Absent'}</button>
                {sp.decided ? <span className="tl-meta">{'You said: ' + sp.decided}</span> : null}
              </div>
              {sp.error ? <div role="alert" className="tl-bad">{sp.error}</div> : null}
            </div>
          ))}
        </div>
      ) : null}
      {k.approveError ? <div role="alert" className="tl-bad">{k.approveError}</div> : null}
      <div className="tl-actions tl-actions-tight">
        <button type="button" className={'tl-btn' + (k.canApprove ? ' tl-btn-primary' : '')} onClick={k.approve} disabled={!k.canApprove}>{k.approving ? 'Approving…' : k.approveLabel}</button>
        <button type="button" className="tl-btn" onClick={k.recheck}>{'Check again'}</button>
        <button type="button" className="tl-btn" onClick={k.cancel}>{'Cancel'}</button>
      </div>
    </div>
  );
}

function Queue({ q }) {
  return (
    <div>
      <div role="tablist" aria-label="Kinds of suggestion" className="tl-blocks">
        {q.kinds.map((k) => (
          <button key={k.key} type="button" role="tab" aria-selected={k.on ? 'true' : 'false'} className={'tl-chip-btn' + (k.on ? ' tl-on' : '')} onClick={k.pick}>{k.label}</button>
        ))}
      </div>
      {q.kind === 'group' ? (
        <div className="tl-fields tl-fields-tight tl-gap-top">
          <div className="tl-guidance">{q.groupingNote}</div>
          {q.fixTest ? <div className="tl-guidance"><strong>{q.fixTest}</strong>{' ' + q.fixTestText}</div> : null}
        </div>
      ) : null}
      {q.bulk ? (
        <div className="tl-head-row tl-gap-top">
          <span className="tl-meta">{q.untickedNote + ' ' + q.selectedCount + ' ticked.'}</span>
          <button type="button" className="tl-btn" onClick={q.selectPage} disabled={q.locked}>{'Select all on this page'}</button>
          <button type="button" className="tl-btn" onClick={q.clearSelection} disabled={q.locked || !q.selectedCount}>{'Clear'}</button>
          <button type="button" className="tl-btn" onClick={q.acceptSelected} disabled={q.locked || !q.selectedCount || q.checking}>{q.checking ? 'Checking…' : 'Accept selected'}</button>
          {q.dismissSelected ? <button type="button" className="tl-btn" onClick={q.dismissSelected} disabled={q.locked || !q.selectedCount || q.checking}>{'Dismiss selected'}</button> : null}
        </div>
      ) : null}
      <Problem text={q.error || q.checkError} />
      {q.result ? <div role="status" className="tl-ok">{q.result.text}<button type="button" className="tl-btn tl-btn-small tl-gap-left" onClick={q.result.dismiss}>{'OK'}</button></div> : null}
      {q.check ? <Check k={q.check} /> : null}
      {q.hiddenNote ? <div className="tl-meta">{q.hiddenNote}</div> : null}
      {q.loading ? <div className="tl-meta">{'Reading…'}</div> : q.empty ? <div className="tl-meta">{q.emptyText}</div> : q.rows.map((r) => <QueueRow key={r.id} r={r} />)}
      {q.pager.text ? (
        <div className="tl-pager">
          <button type="button" className="tl-link" onClick={q.pager.prev} disabled={!q.pager.canPrev}>{'‹ Previous'}</button>
          <span className="tl-meta">{q.pager.text}</span>
          <button type="button" className="tl-link" onClick={q.pager.next} disabled={!q.pager.canNext}>{'Next ›'}</button>
        </div>
      ) : null}
    </div>
  );
}

function Runs({ r }) {
  const f = r.form;
  return (
    <div>
      <Problem text={r.error} retry={r.reload} />
      {r.loading ? <div className="tl-meta">{'Reading…'}</div> : r.empty ? <div className="tl-meta">{r.emptyText}</div> : (
        <table className="tl-table">
          <thead><tr>{['Run', 'Status', 'Done', 'Spent', 'Known answers', ''].map((h, i) => <th key={i} scope="col">{h}</th>)}</tr></thead>
          <tbody>{r.list.map((x) => (
            <tr key={x.id}>
              <td className="tl-wrap">{x.kindText + (x.modeName ? ': ' + x.modeName : '')}<div className="tl-meta">{x.who + (x.when ? ' · ' + x.when : '') + ' · estimated ' + x.estimate}</div>
                {x.error ? <div role="alert" className="tl-bad">{x.error}</div> : null}</td>
              <td>{x.statusText}</td>
              <td>{x.progress}</td>
              <td>{x.spend}</td>
              <td className="tl-wrap">{x.canary}</td>
              <td>
                {x.pause ? <button type="button" className="tl-btn" onClick={x.pause} disabled={x.busy}>{'Pause'}</button> : null}
                {x.resume ? (
                  <span className="tl-withdraw">
                    <input aria-label="New cost cap in dollars (optional)" placeholder="cap $" value={x.resumeCap} onChange={x.setResumeCap} />
                    <button type="button" className="tl-btn" onClick={x.resume} disabled={x.busy}>{'Resume'}</button>
                  </span>
                ) : null}
              </td>
            </tr>))}</tbody>
        </table>
      )}
      <div role="form" aria-label="New AI proposal run" className="tl-form tl-form-end">
        <label className="tl-field-row">{'What to run'}<select value={f.kind} onChange={f.set('kind')}>{f.kinds.map((k) => <option key={k.value} value={k.value}>{k.label}</option>)}</select></label>
        {f.showMode ? <label className="tl-field-row">{'Failure mode'}<select value={f.modeId} onChange={f.set('mode_id')}>{f.modes.map((m) => <option key={m.value || 'none'} value={m.value}>{m.label}</option>)}</select></label> : null}
        {f.showWindow ? <label className="tl-field-row">{'Answers from'}<select value={f.days} onChange={f.set('days')}>{f.dayOptions.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}</select></label> : null}
        {f.showCells ? <label className="tl-field-row">{'At most'}<input value={f.limit} onChange={f.set('limit')} inputMode="numeric" className="tl-narrow" /></label> : null}
        {f.showCells ? <label className="tl-check-row"><input type="checkbox" checked={f.includeCandidates} onChange={f.set('include_candidates')} />{'Include candidate modes'}</label> : null}
        <label className="tl-field-row">{'Cost cap ($)'}<input value={f.cap} onChange={f.set('usd_cap')} inputMode="decimal" className="tl-narrow" /></label>
        <button type="button" className="tl-btn" onClick={r.runEstimate} disabled={r.estimating}>{r.estimating ? 'Estimating…' : 'Estimate'}</button>
        <button type="button" className={'tl-btn' + (r.startProblem ? '' : ' tl-btn-primary')} onClick={r.start} disabled={!!r.startProblem || r.starting}>{r.starting ? 'Starting…' : 'Start'}</button>
      </div>
      {r.estimate ? <div className={'tl-meta tl-gap-top' + (r.estimate.priceKnown ? '' : ' tl-bad')}>{r.estimate.text}</div> : null}
      {r.startProblem && r.estimate ? <div className="tl-meta">{r.startProblem}</div> : null}
      <Problem text={r.estimateError || r.startError} />
    </div>
  );
}

export default function ProgressTaxonomy({ s }) {
  const vals = Object.assign({}, taxonomyModesVals(s, a), taxonomyProposalsVals(s, a));
  const p = vals.tlTaxProgress;
  const [reqsOpen, setReqsOpen] = React.useState(false);
  const ensure = p.ensure;
  React.useEffect(() => { ensure(); }, [s.seq]);
  // The block-size buttons are the Progress tab's own (logic/sampling.js). They sit in the older discovery card while
  // that card is shown, and here once it has stepped aside, so there is one set on the page.
  const pv = progressView(s);
  const blocks = pv.discoveryMoved ? pv.blocks : [];
  const map = vals.tlTaxMap;
  return (
    <div className="tl-stack tl-stack-flush">
      <div className="tl-pair">
        <div className="tl-card">
          <Head title="Label completeness" />
          <div className="tl-big">{p.completeness ? p.completeness.text : '—'}</div>
          <div className="tl-meta">{p.completeness ? p.completeness.missing + ' · ' + p.completeness.detail : 'Shown once a failure mode is confirmed.'}</div>
          {p.completeness && p.completeness.preview.length ? (
            <ul className="tl-steps">{p.completeness.preview.map((x) => <li key={x.key} className="tl-meta">{x.text}</li>)}</ul>
          ) : null}
          {p.completeness && p.completeness.more ? <div className="tl-meta">{p.completeness.more}</div> : null}
          {p.completeness && p.completeness.showMissing ? <button type="button" className="tl-btn tl-gap-top" onClick={p.completeness.showMissing}>{'Show missing judgments'}</button> : null}
        </div>
        <div className="tl-card">
          <Head title="Unfiled notes" />
          <div className="tl-big">{p.unfiled}</div>
          <div className="tl-meta">{'Reviewers’ notes on wrong answers not yet filed under a failure mode or set aside.'}</div>
        </div>
      </div>

      <section className="tl-card" aria-label="Failure modes">
        <Head title="Failure modes" right={<button type="button" className="tl-btn" onClick={map.reload}>{'Refresh failure modes'}</button>} />
        {map.loading ? <div className="tl-meta">{'Reading…'}</div> : map.empty ? <div className="tl-meta">{map.emptyText}</div> : <Treemap map={map} />}
        <Problem text={map.error} retry={map.reload} />
        {p.perMode.length ? (
          <table className="tl-table tl-gap-top">
            <thead><tr>{['Failure mode', 'First failure', 'Any instance', 'Judged', 'Share of judged answers', 'Open AI suggestions'].map((h) => <th key={h} scope="col">{h}</th>)}</tr></thead>
            <tbody>{p.perMode.map((m) => (
              <tr key={m.id}>
                <td><button type="button" className="tl-link" onClick={m.open}>{m.name}</button>{' · ' + m.statusText + (m.dataFault ? ' · data fault' : '')}</td>
                <td>{m.first}</td><td>{m.any}</td><td>{m.decided}</td><td>{m.fraction}</td><td>{m.pending}</td>
              </tr>))}</tbody>
          </table>
        ) : null}
        <div className="tl-meta tl-gap-top">{p.perModeNote}</div>
      </section>

      <section className="tl-card" aria-label="Discovery of failure modes">
        <Head title="Discovery of failure modes" right={blocks.length ? (
          <div className="tl-blocks" role="group" aria-label="Reviews per block">
            {blocks.map((b) => <button key={b.n} type="button" className={'tl-chip-btn' + (b.on ? ' tl-on' : '')} aria-pressed={b.on ? 'true' : 'false'} onClick={() => sact.setProgressBlock(b.n)}>{b.n + ' per block'}</button>)}
          </div>) : null} />
        <ModeCurve curve={p.curve} />
        <div className="tl-meta">{p.curve.caption}</div>
        <div className="tl-saturation">{p.saturation}</div>
        {p.finalUniform ? <div className="tl-saturation">{p.finalUniform}</div> : null}
        {p.byBatch.length ? <ul className="tl-steps">{p.byBatch.map((b) => <li key={b.key} className="tl-meta">{b.text}</li>)}</ul> : null}
        {p.reReview ? (
          <div className="tl-guidance tl-gap-top">
            {p.reReview.text}
            <div className="tl-pills">
              {p.reReview.turns.map((t) => (t.open ? <button key={t.key} type="button" className="tl-link" onClick={t.open}>{t.label}</button> : <span key={t.key} className="tl-meta">{t.label}</span>))}
            </div>
          </div>
        ) : null}
      </section>

      <section className="tl-card" aria-label="Unfiled wrong notes">
        <Head title="Unfiled wrong notes" right={<button type="button" className="tl-btn" onClick={vals.tlTaxUnfiled.reload}>{'Refresh notes'}</button>} />
        <Problem text={vals.tlTaxUnfiled.error} retry={vals.tlTaxUnfiled.reload} />
        <Unfiled u={vals.tlTaxUnfiled} />
      </section>

      {vals.tlTaxModeProposed ? (
        <section className="tl-card" aria-label={vals.tlTaxModeProposed.title}>
          <Head title={vals.tlTaxModeProposed.title} right={<button type="button" className="tl-btn" onClick={vals.tlTaxModeProposed.reload}>{'Refresh proposals'}</button>} />
          <ModeProposed q={vals.tlTaxModeProposed} />
        </section>
      ) : null}

      <section className="tl-card" aria-label="AI suggestions">
        <Head title="AI suggestions" right={<button type="button" className="tl-btn" onClick={vals.tlTaxQueue.reload}>{'Refresh suggestions'}</button>} />
        <Queue q={vals.tlTaxQueue} />
      </section>

      {vals.tlTaxRuns ? (
        <section className="tl-card" aria-label="AI proposal runs">
          <Head title="AI proposal runs" right={<button type="button" className="tl-btn" onClick={vals.tlTaxRuns.reload}>{'Refresh runs'}</button>} />
          <Runs r={vals.tlTaxRuns} />
        </section>
      ) : null}

      <section className="tl-card" aria-label="Requirements">
        <details onToggle={(e) => setReqsOpen(e.currentTarget.open)}>
          <summary className="tl-kick tl-summary">{'Requirements the failure modes cite'}</summary>
          {reqsOpen ? <Requirements s={s} /> : null}
        </details>
      </section>
    </div>
  );
}
