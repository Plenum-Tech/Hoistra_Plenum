// EmailComposer — the one email draft panel, in the dock and on the chat page.
//
// 28 Oct 2026. The dock's email card was a stack of <div onClick>s: no control could be reached
// by keyboard, none showed focus, and the labels named no field. It was also drawn ONLY in the
// dock, so "Draft renewal email" pressed on the Hoistra chat page drafted the email (and queued
// it on the Approvals card) and then showed nothing, while a note said "It is open above".
// This is that card as real controls, drawn by both:
//   - the dock (OrchestratorDock.jsx), at the top of its scroll region, as before;
//   - the chat page (screens/Chat.jsx), under the answer whose button opened it.
//
// `em` is renderVals' `em` (the draft and its send), `onCancel` is `fCancel`. The send is
// unchanged: em.send posts the draft and reports what really happened (sent, dry run, handed to
// the mail client, refused). `done`/`doneTone` show that outcome inside the card while the draft
// stays open (a handoff, a refusal): the chat page has no separate result strip beside a draft
// the way the dock does, so the dock passes neither and keeps its own.
//
// Ids carry the variant: the dock can be open over the chat page, and the two must not share ids.
// Button labels are the dock's own words, unchanged. The address lines are text fields with an
// email keyboard, not type="email": that type trims what is typed under React and moves the caret,
// and refuses the "a@x, b@y" lists some drafts carry.
import React from 'react';

export default function EmailComposer({ em, onCancel, variant, done, doneTone }) {
  if (!em) return null;
  const v = variant === 'dock' ? 'dock' : 'page';
  const id = 'emc-' + v;
  const busy = !!em.sendBusy;
  const to = String(em.to || '');
  const sendLabel = busy ? 'Sending…' : em.sample ? 'Sample — not sent' : em.prevIsReminder ? 'Approve & send reminder' : 'Approve & send';
  return (
    <section className={'emc emc-' + v} id={id} aria-labelledby={id + '-title'}>
      <div className="emc-head">
        <i className="ph ph-envelope-simple" aria-hidden="true"></i>
        <h3 className="emc-title" id={id + '-title'}>{em.kicker || 'Email draft'}</h3>
        <span className="emc-hint">{em.sample ? 'Sample — not sent' : 'Nothing leaves Hoistra until you press Approve & send'}</span>
      </div>
      {/* A draft that came with an Approvals card item (a renewal the compliance service queued, a
          Decision-queue card) is sent against that item, so the card records that it went. */}
      {em.queued ? (
        <div className="emc-line">
          <i className="ph ph-tray" aria-hidden="true"></i>
          <span>{'Linked to its item on the Approvals card — sending here records it there.'}</span>
        </div>
      ) : null}
      {/* The service found no vendor contact for this certificate and addressed the renewal to the
          default PM address (to_is_fallback, 28 Oct 2026). It used to arrive looking like the
          vendor's own address, so a renewal could go back to the sender without anyone noticing. */}
      {em.toFallback ? (
        <div className="emc-line is-warn" role="note">
          <i className="ph ph-warning" aria-hidden="true"></i>
          <span>{'No vendor contact was found, so this is addressed to the default PM address. Change To if it should go to the vendor.'}</span>
        </div>
      ) : null}
      {/* Already sent: what went out and when, and the reminder / new-request switch. */}
      {em.prevShow === 'block' ? (
        <div className="emc-prev" role="note">
          <i className="ph ph-clock-counter-clockwise" aria-hidden="true"></i>
          <span className="emc-prev-text">
            {em.prevNote}{' '}
            <span className="emc-prev-sub">{em.prevIsReminder ? 'This draft is a reminder of it.' : 'This draft is a new request.'}</span>
          </span>
          <button type="button" className="emc-link" onClick={em.prevSwitch} disabled={busy}>{em.prevSwitchLabel}</button>
        </div>
      ) : null}
      <div className="emc-fields">
        <label htmlFor={id + '-to'}>{'To'}</label>
        <input id={id + '-to'} type="text" inputMode="email" value={to} onChange={em.setTo} placeholder="name@organisation.com" autoComplete="off" spellCheck={false} />
        {em.canCc ? <label htmlFor={id + '-cc'}>{'Cc'}</label> : null}
        {em.canCc ? <input id={id + '-cc'} type="text" inputMode="email" value={em.cc || ''} onChange={em.setCc} autoComplete="off" spellCheck={false} /> : null}
        <label htmlFor={id + '-subject'}>{'Subject'}</label>
        <input id={id + '-subject'} type="text" value={em.subject || ''} onChange={em.setSubject} autoComplete="off" />
      </div>
      <label className="emc-sr" htmlFor={id + '-body'}>{'Draft'}</label>
      <textarea id={id + '-body'} className="emc-body" value={em.body || ''} onChange={em.setBody} rows={v === 'dock' ? 8 : 11}></textarea>
      {/* Evidence requests carry the generated pack, a file built in the browser: opened, not linked. */}
      {em.packShow === 'flex' ? (
        <div className="emc-attach">
          <button type="button" className="emc-pack" onClick={em.openPack} title="Open the generated evidence pack">
            <i className="ph ph-paperclip" aria-hidden="true"></i>
            <span className="emc-pack-name">{em.packName}</span>
            <span className="emc-pack-size">{em.packSize}</span>
          </button>
        </div>
      ) : null}
      {done ? (
        <div className={'emc-status is-' + (doneTone || 'ok')} role="status">
          <i className={'ph ' + (doneTone === 'risk' ? 'ph-warning-circle' : doneTone === 'warn' ? 'ph-info' : 'ph-check-circle')} aria-hidden="true"></i>
          <span>{done}</span>
        </div>
      ) : null}
      <div className="emc-foot">
        <button type="button" className="emc-btn is-quiet" onClick={onCancel} disabled={busy}>{'Cancel'}</button>
        {/* The send is a real outbound call, so the control says it is working and takes no
            second click while it does: a second click on a slow send is a second email. */}
        <button type="button" className={'emc-btn is-primary' + (em.sample ? ' is-sample' : '')} onClick={em.send} disabled={busy} aria-busy={busy || undefined}>
          <i className={'ph ' + (busy ? 'ph-circle-notch emc-spin' : 'ph-paper-plane-tilt')} aria-hidden="true"></i>
          {sendLabel}
        </button>
      </div>
    </section>
  );
}
