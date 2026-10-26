// Progress — the Trace Lab's Progress tab, open-coding part (logic/sampling.js progressView): four tiles, the batch
// table, the discovery curve, the fair-sample wrong rate, agreement between reviewers, coverage per dimension and
// the saturation line. Missing data reads "—", never zero. The failure-mode charts of later phases go below.
import React from 'react';
import * as sact from '../samplingActions.js';
import { progressView, samplingView } from '../logic/sampling.js';
import Period from './Period.jsx';
import ProgressTaxonomy from './ProgressTaxonomy.jsx';

function Curve({ curve }) {
  if (curve.empty) return <div className="tl-meta">{'No reviews yet.'}</div>;
  return (
    <svg viewBox={'0 0 ' + curve.width + ' ' + curve.height} role="img" aria-label={curve.caption} className="tl-curve">
      <line x1="28" y1={curve.height - 28} x2={curve.width - 28} y2={curve.height - 28} className="tl-curve-axis" />
      <text x="28" y="18" className="tl-curve-note">{'most in a block: ' + curve.max}</text>
      <path d={curve.path} className="tl-curve-line" />
      {curve.dots.map((d) => (
        <g key={d.x}>
          <circle cx={d.x} cy={d.y} r="10" className="tl-curve-hit"><title>{d.label}</title></circle>
          <circle cx={d.x} cy={d.y} r="4" className="tl-curve-dot" />
        </g>
      ))}
    </svg>
  );
}

export default function Progress({ s }) {
  const p = progressView(s);
  const dims = p.coverage.filter((d) => d.rows.length);
  return (
    <div className="tl-stack">
      <div className="tl-list-head">
        <Period ranges={samplingView(s).ranges} />
        <button type="button" className="tl-btn" onClick={() => sact.loadProgress()}>{'Refresh'}</button>
      </div>
      {p.error ? <div role="alert" className="tl-card tl-bad">{'Could not read progress: ' + p.error}</div> : null}
      <div className="tl-bigtiles">
        {p.tiles.map((t) => (
          <div key={t.label} className="tl-bigtile">
            <div className="tl-bigtile-n">{p.loading ? '…' : t.value}</div>
            <div className="tl-kick">{t.label}</div>
          </div>
        ))}
      </div>

      <section className="tl-card" aria-label="Batches">
        <div className="tl-kick">{'Batches'}</div>
        {!p.batches.length ? <div className="tl-meta">{'No open batches.'}</div> : (
          <table className="tl-table">
            <thead><tr>{['Batch', 'Reviewed', 'By you', 'Missing', 'Wrong / right / disputed'].map((h) => <th key={h} scope="col">{h}</th>)}</tr></thead>
            <tbody>
              {p.batches.map((b) => (
                <tr key={b.id}>
                  <td>{b.label + ' · ' + b.tag}</td>
                  <td>{b.reviewed + ' of ' + b.selected}</td>
                  <td>{b.mine}</td>
                  <td>{b.missing}</td>
                  <td>{b.wrong + ' / ' + b.right + ' / ' + b.disputed}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {p.reviewers.length ? <div className="tl-meta">{'Reviewers: ' + p.reviewers.map((r) => r.who + ' (' + r.reviewed + ')').join(', ')}</div> : null}
        {p.missing && p.missing.turns.length ? <div className="tl-meta">{'Still missing a verdict: ' + p.missing.turns.length + ' listed' + (p.missing.more ? ' ' + p.missing.more : '')}</div> : null}
      </section>

      {p.discoveryMoved ? null : <section className="tl-card" aria-label="Discovery">
        <div className="tl-list-head">
          <div className="tl-kick">{'Discovery'}</div>
          <div className="tl-blocks" role="group" aria-label="Reviews per block">
            {p.blocks.map((b) => (
              <button key={b.n} type="button" className={'tl-chip-btn' + (b.on ? ' tl-on' : '')} aria-pressed={b.on ? 'true' : 'false'} onClick={() => sact.setProgressBlock(b.n)}>{b.n + ' per block'}</button>
            ))}
          </div>
        </div>
        <Curve curve={p.curve} />
        <div className="tl-meta">{p.curve.caption}</div>
        <div className="tl-saturation">{p.saturation}</div>
      </section>}

      <div className="tl-pair">
        <section className="tl-card" aria-label="Wrong rate">
          <div className="tl-kick">{'Wrong rate'}</div>
          <div className="tl-big">{p.fair ? p.fair.text : '—'}</div>
          <div className="tl-meta">{p.fair ? p.fair.label + ' · ' + p.fair.detail : 'From fair (uniform) batches only'}</div>
        </section>
        <section className="tl-card" aria-label="Agreement between reviewers">
          <div className="tl-kick">{'Agreement between reviewers'}</div>
          <div className="tl-mid">{p.agreement}</div>
          <div className="tl-meta">{'First, blind verdicts only.'}</div>
        </section>
      </div>

      <section className="tl-card" aria-label="Coverage">
        <div className="tl-kick">{'Coverage'}</div>
        <div className="tl-meta">{'Shares of the reviewed sample, not rates for all answers. A value is thin when it is at least 5% of the answers but under half that share of the reviews.'}</div>
        {!dims.length ? <div className="tl-meta">{'—'}</div> : dims.map((d) => (
          <details key={d.label} className="tl-coverage">
            <summary>{d.label + (d.rows.some((r) => r.thin) ? ' · some values thin' : '')}</summary>
            <table className="tl-table">
              <thead><tr>{['Value', 'Answers', 'Reviewed', 'Share of answers', 'Share of reviews'].map((h) => <th key={h} scope="col">{h}</th>)}</tr></thead>
              <tbody>
                {d.rows.map((r) => (
                  <tr key={r.value}><td>{r.value + (r.thin ? ' · thin' : '')}</td><td>{r.pool}</td><td>{r.reviewed}</td><td>{r.poolShare}</td><td>{r.reviewedShare}</td></tr>
                ))}
              </tbody>
            </table>
          </details>
        ))}
        {p.unreviewedGroups.length ? <div className="tl-meta">{'Map groups with no reviewed answer: ' + p.unreviewedGroups.join(', ')}</div> : null}
      </section>

      <ProgressTaxonomy s={s} />
    </div>
  );
}
