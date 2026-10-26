// Labels — the Trace Lab's Labels tab (logic/labels.js taxonomyLabelsVals): reviewed answers × failure modes. A solid
// cell is a person's judgment (present, absent, or "via" a merged or split mode); a dashed cell is an AI suggestion
// that counts for nothing until someone accepts it; "? missing" is a judgment nobody has made. One press toggles a
// cell, sending the head it last read as `base`; "⋯" opens the cell's history and its explicit choices. The header
// says what the counts are: shares of the reviewed sample, not rates for all traffic. The cell panel is a dialog
// (Escape closes), portalled to the page body so no ancestor traps it, and drawn in place where there is no body.
import React from 'react';
import { createPortal } from 'react-dom';
import * as a from '../taxonomyActions.js';
import { taxonomyLabelsVals } from '../logic/labels.js';

function Cell({ cell }) {
  return (
    <span className={'tl-cell tl-cell-' + cell.kind + (cell.error ? ' tl-cell-bad' : '')} data-style={cell.style}>
      <button type="button" className="tl-cell-btn" onClick={cell.toggle} disabled={cell.busy} title={cell.title} aria-label={cell.label}>
        {cell.busy ? 'Saving…' : cell.mark}
        {cell.via ? <span className="tl-cell-via">{cell.via}</span> : null}
        {cell.withdrawn ? <span className="tl-cell-via">{'note withdrawn'}</span> : null}
      </button>
      <button type="button" className="tl-cell-more" onClick={cell.more} aria-label={'History and choices — ' + cell.label}>{'⋯'}</button>
    </span>
  );
}

function CellPanel({ p }) {
  const closeRef = React.useRef(p.close);
  closeRef.current = p.close;
  React.useEffect(() => {
    if (typeof document === 'undefined' || !document.addEventListener) return undefined;
    const onKey = (e) => { if (e.key === 'Escape' && closeRef.current) closeRef.current(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, []);
  const node = (
    <div className="tl-side" role="dialog" aria-modal="true" aria-label={'Judgment for ' + p.modeName}>
      <button type="button" className="tl-close" aria-label="Close" onClick={p.close}>{'×'}</button>
      <div className="tl-kick">{'Judgment'}</div>
      <h3 className="tl-title tl-title-tight">{p.modeName}</h3>
      {p.definition ? <div className="tl-meta">{p.definition}</div> : null}
      <div className="tl-panel-q">{p.question}</div>
      <div className={'tl-cell tl-cell-' + p.view.kind}><span className="tl-cell-btn" aria-hidden="true">{p.view.mark}</span></div>
      {p.source ? <div className="tl-meta tl-gap-top">{p.source + (p.decidedBy ? ' · ' + p.decidedBy : '') + (p.decidedAt ? ' · ' + p.decidedAt : '')}</div> : null}
      {p.view.via ? <div className="tl-meta">{'Decided ' + p.view.via + '.'}</div> : null}
      {p.proposal ? (
        <div className="tl-sug-card tl-gap-top">
          <div className="tl-kick tl-kick-focus">{'AI suggestion'}</div>
          <div className="tl-sug-line">{'Suggests ' + p.proposal.suggestion + (p.proposal.evidence ? ': ' + p.proposal.evidence : '')}</div>
          {p.proposal.quote ? <div className="tl-meta">{'“' + p.proposal.quote + '”'}</div> : null}
        </div>
      ) : null}
      <div className="tl-actions">
        <button type="button" className="tl-btn" onClick={p.markPresent} disabled={p.busy}>{p.presentLabel}</button>
        <button type="button" className="tl-btn" onClick={p.markAbsent} disabled={p.busy}>{p.absentLabel}</button>
        {p.clear ? <button type="button" className="tl-btn" onClick={p.clear} disabled={p.busy} aria-label="Clear this judgment">{'× Clear'}</button> : null}
      </div>
      {p.error ? <div role="alert" className="tl-bad">{p.error}</div> : null}
      <div className="tl-kick tl-gap-top">{'History'}</div>
      {p.historyLoading ? <div className="tl-meta">{'Reading…'}</div> : null}
      {p.historyError ? <div role="alert" className="tl-bad">{'Could not read the history: ' + p.historyError}</div> : null}
      {!p.historyLoading && !p.historyError && !p.history.length ? <div className="tl-meta">{'No judgment has been made here.'}</div> : null}
      <ol className="tl-steps">
        {p.history.map((h) => (
          <li key={h.key}>{h.text + (h.current ? ' (now)' : '') + ' · ' + h.source + (h.who ? ' · ' + h.who : '') + (h.when ? ' · ' + h.when : '')}</li>
        ))}
      </ol>
    </div>
  );
  const target = typeof document !== 'undefined' && document && document.body ? document.body : null;
  return target ? createPortal(node, target) : node;
}

export default function Labels({ s }) {
  const vals = taxonomyLabelsVals(s, a);
  const v = vals.tlTaxLabels;
  const ensure = v.ensure;
  // Read when the tab opens, and again for another company (a company switch empties the page and bumps the seq).
  React.useEffect(() => { ensure(); }, [s.seq]);
  return (
    <div className="tl-stack">
      <section className="tl-card" aria-label="Labels">
        <div className="tl-list-head tl-head-row">
          <h3 className="tl-title tl-grow">{v.title}</h3>
          <label className="tl-field"><span>{'Show'}</span>
            <select value={v.filter.batchId} onChange={v.filter.setBatch} aria-label="Which answers">
              {v.filter.batches.map((b) => <option key={b.value || 'all'} value={b.value}>{b.label}</option>)}
            </select>
          </label>
          <button type="button" className={'tl-chip-btn' + (v.filter.onlyMissing ? ' tl-on' : '')} onClick={v.filter.toggleMissing} aria-pressed={v.filter.onlyMissing ? 'true' : 'false'}>{'Only missing'}</button>
          <button type="button" className="tl-btn" onClick={v.reload}>{'Refresh'}</button>
        </div>
        <div className="tl-lead">{v.header}</div>
        {v.hiddenNote ? <div className="tl-meta">{v.hiddenNote}</div> : null}
        <div className="tl-meta tl-legend">{v.legend.map((l) => <span key={l}>{l}</span>)}</div>
        {v.failures.map((f) => (
          <div key={f.key} role="alert" className="tl-bad tl-inline">
            {f.text}
            {f.retry ? <button type="button" className="tl-btn" onClick={f.retry}>{'Retry'}</button> : null}
            {f.conflict ? <button type="button" className="tl-btn" onClick={f.reload}>{'Reload'}</button> : null}
          </div>
        ))}
        {v.error ? <div role="alert" className="tl-bad">{'Could not read the labels: ' + v.error}</div> : null}
        {v.loading ? <div className="tl-meta tl-gap-top">{'Reading…'}</div> : v.empty ? <div className="tl-meta tl-gap-top">{v.emptyText}</div> : (
          <div className="tl-grid-wrap">
            <table className="tl-grid">
              <thead>
                <tr>
                  <th scope="col">{'Answer'}</th>
                  {v.modes.map((m) => (
                    <th key={m.id} scope="col" className="tl-grid-mode">
                      <button type="button" className="tl-link" onClick={m.open} title={m.definition}>{m.name}</button>
                      <div className="tl-meta">{m.statusText + (m.dataFault ? ' · data fault' : '')}</div>
                      <div className="tl-meta" title="Share of the reviewed answers judged for this mode">{m.fraction}</div>
                      <div className="tl-meta" title="From fair (uniform) batches only">{m.fair}</div>
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {v.rows.map((r) => (
                  <tr key={r.turnId}>
                    <th scope="row" className="tl-grid-answer">
                      {r.open ? <button type="button" className="tl-link" onClick={r.open}>{r.question}</button> : <span className="tl-grid-q">{r.question}</span>}
                      <div className="tl-meta">{r.batch + ' · ' + r.verdicts}</div>
                    </th>
                    {r.cells.map((cell) => <td key={cell.modeId}><Cell cell={cell} /></td>)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {v.fractionCopy ? <div className="tl-meta tl-gap-top">{v.fractionCopy}</div> : null}
        {v.pager.text ? (
          <div className="tl-pager">
            <button type="button" className="tl-link" onClick={v.pager.prev} disabled={!v.pager.canPrev}>{'‹ Previous'}</button>
            <span className="tl-meta">{v.pager.text}</span>
            <button type="button" className="tl-link" onClick={v.pager.next} disabled={!v.pager.canNext}>{'Next ›'}</button>
          </div>
        ) : null}
      </section>
      {vals.tlTaxCell ? <CellPanel p={vals.tlTaxCell} /> : null}
    </div>
  );
}
