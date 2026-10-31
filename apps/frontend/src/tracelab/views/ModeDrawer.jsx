// ModeDrawer — one failure mode (logic/taxonomy.js tlTaxDrawer): what it means, the contract checklist that gates
// Confirm, the answers where it is present and the close ones where it is not, the notes it came from and its history.
// Changing it is the platform team's (D7); everyone else reads, and a superadmin viewing as a company is told to stop
// viewing as one. A data-level mode says it is routed to the data owner and kept out of prompt fixes and judges. The
// drawer is a dialog (Escape closes), portalled to the page body, and drawn in place where there is no body.
import React from 'react';
import { createPortal } from 'react-dom';
import * as a from '../taxonomyActions.js';
import { taxonomyModesVals } from '../logic/taxonomy.js';

function Section({ title, children }) {
  return (
    <section aria-label={title} className="tl-drawer-section">
      <div className="tl-kick">{title}</div>
      {children}
    </section>
  );
}

function Answers({ items, empty, removeLabel }) {
  if (!items.length) return <div className="tl-meta">{empty}</div>;
  return (
    <div>
      {items.map((x) => (
        <div key={x.turnId} className="tl-row-line">
          {x.open ? <button type="button" className="tl-link" onClick={x.open}>{x.question}</button> : <span>{x.question}</span>}
          <span className={'tl-meta ' + (x.ok ? 'tl-check-ok' : 'tl-check-missing')}>{x.state}</span>
          {x.remove ? <button type="button" className="tl-btn tl-btn-small tl-push" onClick={x.remove}>{removeLabel}</button> : null}
        </div>
      ))}
    </div>
  );
}

export default function ModeDrawer({ s }) {
  const vals = taxonomyModesVals(s, a);
  const d = vals.tlTaxDrawer;
  const closeRef = React.useRef(d ? d.close : null);
  closeRef.current = d ? d.close : null;
  // A dialog opened from the drawer sits on top of it: Escape closes that one first (ModeDialogs), not both at once.
  const coveredRef = React.useRef(false);
  coveredRef.current = !!vals.tlTaxDialog;
  const open = !!d;
  React.useEffect(() => {
    if (!open || typeof document === 'undefined' || !document.addEventListener) return undefined;
    const onKey = (e) => { if (e.key === 'Escape' && closeRef.current && !coveredRef.current) closeRef.current(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open]);
  if (!d) return null;
  const m = d.mode;
  const x = d.actions;
  const node = (
    <div className="tl-side" role="dialog" aria-modal="true" aria-label={m ? 'Failure mode ' + m.name : 'Failure mode'}>
      <button type="button" className="tl-close" aria-label="Close" onClick={d.close}>{'×'}</button>
      <div className="tl-kick">{'Failure mode'}</div>
      {d.loading ? <div className="tl-meta">{'Reading…'}</div> : null}
      {d.error ? <div role="alert" className="tl-bad tl-inline">{d.error}{d.retry ? <button type="button" className="tl-btn" onClick={d.retry}>{'Retry'}</button> : null}</div> : null}
      {d.notice ? <div role="status" className="tl-ok">{d.notice}</div> : null}
      {m ? (
        <>
          <h3 className="tl-title tl-title-tight tl-wrap-any">
            {m.name}{' '}<span className={'tl-pill tl-pill-' + m.tone}>{m.statusText}</span>
          </h3>
          {m.dataFault ? <div className="tl-guidance" role="note">{m.dataFault}</div> : null}
          <table className="tl-kv tl-kv-plain"><tbody>
            <tr><th scope="row">{'Present when'}</th><td>{m.definition}</td></tr>
            <tr><th scope="row">{'Boundary'}</th><td>{m.boundary}</td></tr>
            <tr><th scope="row">{'Nearest mode'}</th><td>{m.nearest}</td></tr>
            <tr><th scope="row">{'Who decides'}</th><td>{m.level}</td></tr>
            <tr><th scope="row">{'Where it lives'}</th><td>{m.agent}</td></tr>
            <tr><th scope="row">{'Checked by'}</th><td>{m.evaluator}</td></tr>
            <tr><th scope="row">{'Requirements'}</th><td>{m.requirements}</td></tr>
          </tbody></table>
          {m.flags.map((f) => <div key={f} role="note" className="tl-warn">{f}</div>)}
          {m.counts ? <div className="tl-meta">{m.counts}</div> : null}
          {d.ownerHint ? <div className="tl-guidance tl-gap-top" role="note">{d.ownerHint}</div> : null}
          {x ? (
            <div className="tl-actions">
              {x.edit ? <button type="button" className="tl-btn" onClick={x.edit} disabled={d.busy}>{'Edit'}</button> : null}
              {x.rename ? <button type="button" className="tl-btn" onClick={x.rename} disabled={d.busy}>{'Rename'}</button> : null}
              {x.merge ? <button type="button" className="tl-btn" onClick={x.merge} disabled={d.busy}>{'Merge'}</button> : null}
              {x.split ? <button type="button" className="tl-btn" onClick={x.split} disabled={d.busy}>{'Split'}</button> : null}
              {x.toCandidate ? <button type="button" className="tl-btn" onClick={x.toCandidate} disabled={d.busy}>{'Back to candidate'}</button> : null}
              {x.freeze ? <button type="button" className="tl-btn" onClick={x.freeze} disabled={d.busy}>{'Freeze'}</button> : null}
              {x.retire ? <button type="button" className="tl-btn" onClick={x.retire} disabled={d.busy}>{'Retire'}</button> : null}
              {x.restore ? <button type="button" className="tl-btn" onClick={x.restore} disabled={d.busy}>{'Restore'}</button> : null}
            </div>
          ) : null}
          {d.contract ? (
            <Section title="Ready to confirm?">
              <ul className="tl-checklist">
                {d.contract.items.map((it) => (
                  <li key={it.key} className={it.ok ? 'tl-check-ok' : 'tl-check-missing'}>{(it.ok ? '✓ ' : '✗ ') + it.text}</li>
                ))}
              </ul>
              {x && x.confirm ? (
                <div className="tl-gap-top">
                  <button type="button" className={'tl-btn' + (x.confirm.enabled ? ' tl-btn-primary' : '')} disabled={!x.confirm.enabled || d.busy} onClick={x.confirm.run}>{'Confirm'}</button>
                  {!x.confirm.enabled ? <div className="tl-meta tl-gap-top">{'Missing: ' + d.contract.missingTexts.join('; ') + '.'}</div> : null}
                </div>
              ) : null}
            </Section>
          ) : null}
          <Section title="Answers where it is present">
            <Answers items={d.positives} empty="None listed yet." removeLabel="Remove" />
          </Section>
          <Section title="Close answers where it is absent">
            <Answers items={d.closeNegatives} empty="None listed yet." removeLabel="Remove" />
            {d.candidates.length ? (
              <div className="tl-gap-top">
                <div className="tl-meta">{'Judged absent one at a time, not listed yet:'}</div>
                {d.candidates.map((cn) => (
                  <div key={cn.turnId} className="tl-row-line">
                    {cn.open ? <button type="button" className="tl-link" onClick={cn.open}>{cn.question}</button> : <span>{cn.question}</span>}
                    {cn.add ? <button type="button" className="tl-btn tl-btn-small tl-push" onClick={cn.add}>{'List it'}</button> : null}
                  </div>
                ))}
              </div>
            ) : null}
          </Section>
          <Section title="Notes it came from or filed under it">
            {!d.notes.length ? <div className="tl-meta">{'No notes you can see.'}</div> : d.notes.map((n) => (
              <div key={n.id} className="tl-row-block">
                <div>{'✎ ' + n.note}</div>
                <div className="tl-meta">{(n.quote ? '“' + n.quote + '” · ' : '') + n.step + ' · ' + n.who + (n.when ? ' · ' + n.when : '')}</div>
                <div className="tl-actions tl-actions-tight">
                  {n.open ? <button type="button" className="tl-link" onClick={n.open}>{'Open the answer'}</button> : null}
                  {n.usePositive ? <button type="button" className="tl-btn tl-btn-small" onClick={n.usePositive}>{'List as present'}</button> : null}
                </div>
              </div>
            ))}
          </Section>
          <Section title="History">
            {!d.events.length ? <div className="tl-meta">{'—'}</div> : (
              <ol className="tl-steps">
                {d.events.map((e) => <li key={e.key}>{e.text + (e.who ? ' · ' + e.who : '') + (e.when ? ' · ' + e.when : '')}</li>)}
              </ol>
            )}
          </Section>
        </>
      ) : null}
    </div>
  );
  const target = typeof document !== 'undefined' && document && document.body ? document.body : null;
  return target ? createPortal(node, target) : node;
}
