// Requirements — the numbered requirement register that failure modes cite (logic/taxonomy.js tlTaxReqs): each
// requirement's current revision, where it is written down (the quote, and whether its document still holds it), and
// the failure modes that cite it. Adding, revising, withdrawing and loading the starting set are the platform team's.
// Document paths are internal detail, so they sit only inside the collapsed technical panel.
import React from 'react';
import * as a from '../taxonomyActions.js';
import { taxonomyModesVals } from '../logic/taxonomy.js';
import RawPanel from './RawPanel.jsx';

function Form({ f }) {
  return (
    <div role="form" aria-label={f.title} className="tl-subform">
      <div className="tl-verdict-label">{f.title}</div>
      {f.kind === 'create' ? (
        <div className="tl-fields-row">
          <label className="tl-field-row">{'Area'}<select value={f.area} onChange={f.set('area')}>{f.areas.map((x) => <option key={x.value || 'none'} value={x.value}>{x.label}</option>)}</select></label>
          <label className="tl-field-row">{'Who decides it'}<select value={f.level} onChange={f.set('level')}>{f.levels.map((l) => <option key={l.value || 'none'} value={l.value}>{l.label}</option>)}</select></label>
        </div>
      ) : null}
      {f.kind !== 'withdraw' ? (
        <label className="tl-field-row">{'What the chat must do'}<textarea className="tl-textarea" value={f.statement} onChange={f.set('statement')} maxLength={600} /></label>
      ) : <div className="tl-meta">{f.statement}</div>}
      {f.kind !== 'withdraw' ? (
        <div className="tl-fields tl-fields-tight">
          <div className="tl-kick">{'Where it is written down'}</div>
          {f.impls.map((i) => (
            <div key={i.key} className="tl-fields-row tl-fields-end">
              <label className="tl-field-row">{'Document (path in the repository)'}<input value={i.doc} onChange={i.set('doc')} maxLength={300} /></label>
              <label className="tl-field-row">{'Section'}<input value={i.section} onChange={i.set('section')} maxLength={120} /></label>
              <label className="tl-field-row">{'Line'}<input value={i.line} onChange={i.set('line')} inputMode="numeric" /></label>
              <label className="tl-field-row">{'Quote (copied exactly)'}<input value={i.quote} onChange={i.set('quote')} maxLength={600} /></label>
              <button type="button" className="tl-btn" onClick={i.remove}>{'Remove'}</button>
            </div>
          ))}
          {f.addImpl ? <button type="button" className="tl-btn tl-fit" onClick={f.addImpl}>{'Add where it is written'}</button> : null}
        </div>
      ) : null}
      {f.kind !== 'create' ? (
        <label className="tl-field-row">{f.kind === 'withdraw' ? 'Why it is withdrawn' : 'What changed'}<textarea className="tl-textarea" value={f.note} onChange={f.set('note')} maxLength={1000} /></label>
      ) : null}
      {f.error ? <div role="alert" className="tl-bad">{f.error}</div> : null}
      <div className="tl-actions tl-actions-end">
        <button type="button" className="tl-btn" onClick={f.close}>{'Cancel'}</button>
        <button type="button" className="tl-btn tl-btn-primary" onClick={f.save} disabled={f.busy}>{f.busy ? 'Saving…' : f.saveLabel}</button>
      </div>
    </div>
  );
}

export default function Requirements({ s }) {
  const r = taxonomyModesVals(s, a).tlTaxReqs;
  const [raw, setRaw] = React.useState({});
  const ensure = r.ensure;
  React.useEffect(() => { ensure(); }, [s.seq]);
  return (
    <div aria-label="Requirement register">
      <div className="tl-head-row">
        <span className="tl-meta tl-grow">{'Each confirmed failure mode cites at least one active requirement.'}</span>
        {r.seed ? <button type="button" className="tl-btn" onClick={r.seed} disabled={r.seeding}>{r.seeding ? 'Loading…' : 'Load the starting requirements'}</button> : null}
        {r.create ? <button type="button" className="tl-btn" onClick={r.create}>{'New requirement'}</button> : null}
        <button type="button" className="tl-btn" onClick={r.reload}>{'Refresh'}</button>
      </div>
      {r.ownerHint ? <div className="tl-guidance tl-gap-top" role="note">{r.ownerHint}</div> : null}
      {r.notice ? <div role="status" className="tl-ok">{r.notice}</div> : null}
      {r.error ? <div role="alert" className="tl-bad">{'Could not read the requirements: ' + r.error}</div> : null}
      {r.form ? <Form f={r.form} /> : null}
      {r.loading ? <div className="tl-meta">{'Reading…'}</div> : r.empty ? <div className="tl-meta">{r.emptyText}</div> : r.groups.map((g) => (
        <div key={g.area} className="tl-req-group">
          <div className="tl-kick">{g.label}</div>
          {g.rows.map((x) => (
            <div key={x.code} className="tl-req">
              <div className="tl-req-head">
                <strong>{x.code}</strong>{' · ' + x.revision + ' · ' + x.statusText + ' · ' + x.level}
              </div>
              <div className="tl-req-text">{x.statement}</div>
              {x.sources.map((src) => (
                <div key={src.key} className="tl-meta">
                  {(src.section ? src.section + ': ' : '') + '“' + src.quote + '”'}
                  {src.check ? <span className={src.drift ? 'tl-check-missing' : ''}>{' — ' + src.check}</span> : null}
                </div>
              ))}
              <div className="tl-meta">{'Cited by: ' + x.citing}</div>
              {x.earlier.length ? (
                <details className="tl-earlier"><summary className="tl-meta">{'Earlier revisions (' + x.earlier.length + ')'}</summary>
                  {x.earlier.map((e) => <div key={e.key} className="tl-meta">{e.text}</div>)}
                </details>
              ) : null}
              {x.raw.length ? <RawPanel title="Technical detail" data={x.raw} open={!!raw[x.code]} onToggle={(o) => setRaw(Object.assign({}, raw, { [x.code]: o }))} /> : null}
              {x.revise || x.withdraw ? (
                <div className="tl-actions tl-actions-tight">
                  {x.revise ? <button type="button" className="tl-btn" onClick={x.revise}>{'Revise'}</button> : null}
                  {x.withdraw ? <button type="button" className="tl-btn" onClick={x.withdraw}>{'Withdraw'}</button> : null}
                </div>
              ) : null}
            </div>
          ))}
        </div>
      ))}
    </div>
  );
}
