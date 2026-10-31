// Map — the Trace Lab's Map tab (logic/sampling.js): the sampling panel (the batch plan, drawing a batch with a
// preview, the batches drawn, the manifest) above an SVG map of the period's answers projected onto their two main
// axes and grouped. Pick a group to bring it forward; a square marks a reviewed answer and a ring marks one
// already in a review batch. Clicking a point opens the conversation. A group is a prompt to read answers, never
// evidence that they failed. Every control calls samplingActions.js by name; the screen holds no state but the
// reason typed beside a batch's Withdraw.
import React from 'react';
import * as sact from '../samplingActions.js';
import { reviewBatch } from '../annotateActions.js';
import { mapView, samplingOf, samplingView } from '../logic/sampling.js';
import Period from './Period.jsx';

const answers = (n) => n + (n === 1 ? ' answer' : ' answers');

function Plan({ v }) {
  const plan = v.plan;
  return (
    <section className="tl-card" aria-label="Review plan">
      <div className="tl-list-head"><div className="tl-kick">{'Review plan'}</div>{plan ? <span className="tl-meta">{plan.sampled}</span> : null}</div>
      {v.planError ? <div role="alert" className="tl-bad">{'Could not read the plan: ' + v.planError}</div> : null}
      {plan ? (
        <table className="tl-table">
          <thead><tr>{['Step', 'Kind', 'Drawn / target', 'Reviewed', ''].map((h, i) => <th key={i} scope="col">{h}</th>)}</tr></thead>
          <tbody>
            {plan.steps.map((st) => (
              <tr key={st.key}>
                <td>{st.label + (st.next ? ' · next' : '')}</td>
                <td>{st.strategy + (st.fair ? ' · counts toward rates' : ' · for discovery only')}</td>
                <td>{st.selected + ' / ' + st.target}</td>
                <td>{st.reviewed}</td>
                <td>{st.done ? '✓ done' : ''}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : <div className="tl-meta">{'Reading…'}</div>}
      {plan && plan.shortfall ? <div className="tl-meta">{plan.shortfall}</div> : null}
      {plan ? <div className="tl-actions"><button type="button" className="tl-btn" onClick={() => sact.nextBatch()}>{'Next batch: ' + plan.nextLabel}</button></div> : null}
    </section>
  );
}

function Draw({ v }) {
  const f = v.form;
  const set = (patch) => sact.setBatchForm(patch);
  return (
    <section className="tl-card" aria-label="Draw a review batch">
      <div className="tl-kick">{'Draw a review batch'}</div>
      <div className="tl-form">
        <label className="tl-field"><span>{'Step'}</span>
          <select value={f.plan_step} onChange={(e) => set({ plan_step: e.target.value })}>{f.steps.map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}</select>
        </label>
        <label className="tl-field"><span>{'Kind'}</span>
          <select value={f.strategy} onChange={(e) => set({ strategy: e.target.value })}>{f.strategies.map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}</select>
        </label>
        {f.showDimension ? (
          <label className="tl-field"><span>{'Split by'}</span>
            <select value={f.dimension || 'route_target'} onChange={(e) => set({ dimension: e.target.value })}>{f.dimensions.map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}</select>
          </label>
        ) : null}
        <label className="tl-field"><span>{'Answers'}</span><input type="number" min="1" max="200" value={f.k} onChange={(e) => set({ k: e.target.value })} /></label>
        <label className="tl-field"><span>{'Seed'}</span><input type="number" min="0" value={f.seed} onChange={(e) => set({ seed: e.target.value })} /></label>
        <label className="tl-field"><span>{'Reviews per answer'}</span>
          <select value={f.reviews_required} onChange={(e) => set({ reviews_required: Number(e.target.value) })}>{[1, 2, 3].map((n) => <option key={n} value={n}>{n}</option>)}</select>
        </label>
        <label className="tl-check"><input type="checkbox" checked={!!f.include_view_as} onChange={(e) => set({ include_view_as: e.target.checked })} />{'Include our team’s own answers (testing only)'}</label>
      </div>
      <div className="tl-actions">
        <button type="button" className="tl-btn" onClick={() => sact.previewBatch()} disabled={v.busy}>{v.busy ? 'Working…' : 'Preview'}</button>
        <button type="button" className="tl-btn tl-btn-primary" onClick={() => sact.createBatch()} disabled={v.busy}>{'Create batch'}</button>
      </div>
      <div className="tl-meta">{f.fairNote}</div>
      {f.teamNote ? <div role="note" className="tl-bad">{f.teamNote}</div> : null}
      {v.error ? <div role="alert" className="tl-bad">{v.error}</div> : null}
      {v.notice ? <div role="status" className="tl-ok">{v.notice}</div> : null}
      {v.preview ? (
        <div className="tl-preview">
          <div className="tl-meta">{v.preview.empty ? 'No answers are left to draw in this period.'
            : answers(v.preview.picks.length) + ' from a pool of ' + v.preview.poolSize + ' (' + v.preview.excluded + ' already sampled, left out)'
              + (v.preview.truncated ? '; the pool was capped at the newest answers' : '') + '.'}</div>
          <ol className="tl-steps">{v.preview.picks.map((p) => <li key={p.turnId} className="tl-meta">{p.reason}</li>)}</ol>
        </div>
      ) : null}
    </section>
  );
}

function Batches({ v }) {
  const [reason, setReason] = React.useState({});
  return (
    <section className="tl-card" aria-label="Batches drawn">
      <div className="tl-list-head">
        <div className="tl-kick">{'Batches drawn'}</div>
        <button type="button" className="tl-btn" onClick={() => (v.manifest ? sact.closeManifest() : sact.loadManifest())}>{v.manifest ? 'Hide the manifest' : 'Show the manifest'}</button>
      </div>
      {v.batchesError ? <div role="alert" className="tl-bad">{'Could not read the batches: ' + v.batchesError}</div> : null}
      {!v.batches ? <div className="tl-meta">{'Reading…'}</div> : !v.batches.length ? <div className="tl-meta">{'No batches yet.'}</div> : (
        <table className="tl-table">
          <thead><tr>{['Batch', 'Counts', 'Reviewed', 'Seed', 'By', ''].map((h, i) => <th key={i} scope="col">{h}</th>)}</tr></thead>
          <tbody>
            {v.batches.map((b) => (
              <tr key={b.id} className={b.withdrawn ? 'tl-dim' : ''}>
                <td>{b.label + ' · ' + b.strategy + (b.withdrawn ? ' · withdrawn' : '')}</td>
                <td>{b.tag}</td>
                <td>{b.reviewed + ' of ' + b.selected + (b.missing ? ' · ' + b.missing + ' missing' : '')}</td>
                <td>{b.seed}</td>
                <td>{b.who}</td>
                <td>
                  {b.canReview ? <button type="button" className="tl-btn tl-btn-primary" onClick={() => reviewBatch(b.id)}>{'Start reviewing'}</button> : null}
                  {b.canWithdraw ? (
                    <span className="tl-withdraw">
                      <input type="text" aria-label="Why withdraw this batch" placeholder="Reason" value={reason[b.id] || ''}
                        onChange={(e) => setReason(Object.assign({}, reason, { [b.id]: e.target.value }))} />
                      <button type="button" className="tl-btn" onClick={() => sact.withdrawBatch(b.id, reason[b.id])}>{'Withdraw'}</button>
                    </span>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {v.manifestError ? <div role="alert" className="tl-bad">{'Could not read the manifest: ' + v.manifestError}</div> : null}
      {v.manifest ? (
        <div className="tl-manifest">
          <div className="tl-actions"><button type="button" className="tl-btn" onClick={() => sact.downloadManifest()}>{'Download the manifest'}</button></div>
          {v.manifest.map((b) => (
            <details key={b.id}>
              <summary className="tl-meta">{b.label + ' · seed ' + b.seed + ' · ' + b.tag + ' · ' + answers(b.items.length)}</summary>
              <ol className="tl-steps">{b.items.map((i) => <li key={i.turnId} className="tl-meta">{i.reason}</li>)}</ol>
            </details>
          ))}
        </div>
      ) : null}
    </section>
  );
}

function Points({ m }) {
  return (
    <>
      <div className="tl-groups" role="group" aria-label="Groups">
        {m.clusters.map((g) => (
          <button key={g.id} type="button" className={'tl-chip-btn' + (g.on ? ' tl-on' : '')} aria-pressed={g.on ? 'true' : 'false'} onClick={() => sact.focusCluster(g.id)}>{g.label}</button>
        ))}
      </div>
      <svg viewBox={'0 0 ' + m.width + ' ' + m.height} role="img" aria-label="Answers projected onto their two main axes" className="tl-map">
        <rect x="0" y="0" width={m.width} height={m.height} rx="8" className="tl-map-bg" />
        {m.nodes.map((n) => (
          <g key={n.id} className="tl-pt" onClick={() => sact.openMapNode(n)} opacity={n.dim ? 0.25 : 1}>
            <title>{n.title}</title>
            <circle cx={n.x} cy={n.y} r="12" className="tl-pt-hit" />
            {n.sampled ? <circle cx={n.x} cy={n.y} r="8" className="tl-pt-ring" /> : null}
            {n.reviewed
              ? <rect x={n.x - 4} y={n.y - 4} width="8" height="8" className={'tl-pt-sq' + (n.on ? ' tl-pt-on' : '')} />
              : <circle cx={n.x} cy={n.y} r="4.5" className={'tl-pt-dot' + (n.on ? ' tl-pt-on' : '')} />}
          </g>
        ))}
      </svg>
      <div className="tl-meta">{'■ reviewed · ○ not reviewed · ring: already in a review batch. ' + m.explained + (m.truncated ? ' Only the newest 3,000 answers are shown.' : '')}</div>
      <table className="tl-table">
        <thead><tr><th scope="col">{'Group'}</th>{(m.clusters[0] ? m.clusters[0].centroid : []).map((c) => <th key={c.label} scope="col">{c.label + ' (average)'}</th>)}</tr></thead>
        <tbody>
          {m.clusters.map((g) => (
            <tr key={g.id}><td>{g.label}</td>{g.centroid.map((c) => <td key={c.label}>{c.value === null || c.value === undefined ? '—' : c.value}</td>)}</tr>
          ))}
        </tbody>
      </table>
    </>
  );
}

export default function MapTab({ s }) {
  const v = samplingView(s);
  const m = mapView(s);
  const unread = !m.error && !samplingOf(s).map;
  return (
    <div className="tl-stack">
      <Period ranges={v.ranges} />
      <Plan v={v} />
      <Draw v={v} />
      <Batches v={v} />
      <section className="tl-card" aria-label="Map of answers">
        <div className="tl-list-head"><div className="tl-kick">{'Map of answers'}</div><button type="button" className="tl-btn" onClick={() => sact.loadMap()}>{'Refresh'}</button></div>
        <div className="tl-meta">{m.caution}</div>
        {m.error ? <div role="alert" className="tl-bad">{'Could not draw the map: ' + m.error}</div> : null}
        {m.error ? null : m.loading || unread ? <div className="tl-meta">{'Reading…'}</div> : m.tooFew ? <div className="tl-meta">{m.tooFewCopy}</div>
          : m.nodes.length ? <Points m={m} /> : <div className="tl-meta">{'No answers in this period.'}</div>}
      </section>
    </div>
  );
}
