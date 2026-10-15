// VendorDeleteDialog — Delete vendor, opened from the selected vendor's panel on the Vendors page
// (Hussain, 12 Oct 2026). `vals` is logic/vendorDelete.js's vdlVals: the dialog reads what the
// delete would change first, and Delete vendor is armed only once the vendor's name is typed.
//
// Rendered into <body> through a portal: the Vendors page sits inside a fadeUp animation with
// fill-mode "both", and an animated ancestor becomes the containing block for position:fixed —
// the overlay would be pinned to the page box instead of the viewport (CorrectionDrawer.jsx and
// ReportBuilder.jsx do the same). Focus is held inside while it is open, Esc closes it unless the
// delete is on the wire, and focus goes back to the control that opened it.
//
// The status line beside it is always mounted, so the result of a delete — said once the dialog
// has closed — is announced to a screen reader; the toast that says it too is not.
import React, { useEffect, useLayoutEffect, useRef } from 'react';
import { createPortal } from 'react-dom';

const SR_ONLY = { position: 'absolute', width: '1px', height: '1px', padding: '0', margin: '-1px', overflow: 'hidden', clip: 'rect(0,0,0,0)', whiteSpace: 'nowrap', border: '0' };
const FOCUSABLE = 'button:not([disabled]), input:not([disabled]), textarea:not([disabled]), a[href], [tabindex]:not([tabindex="-1"])';
const LABEL = { display: 'block', fontSize: '12px', color: 'var(--color-neutral-400)', marginBottom: '6px', lineHeight: '1.45' };
const FIELD = { width: '100%', boxSizing: 'border-box', fontFamily: 'var(--font-body)', padding: '8px 10px', borderRadius: '8px', background: 'var(--color-bg)', color: 'var(--color-text)' };

// The keys the dialog takes while it is open (12 Oct 2026). Esc closes it unless the delete is on
// the wire — captured, so the page's own Esc (which closes other panels) does not run under it.
// Cmd/Ctrl+K, the command palette, does nothing: the palette would open over the dialog, its Esc
// would close the dialog instead, and Tab would pull focus behind its scrim. Returns what it did.
export function vdlKey(e, h) {
  if (e.key === 'Escape') {
    e.stopPropagation();
    if (!h.busy) h.close();
    return 'escape';
  }
  if ((e.metaKey || e.ctrlKey) && String(e.key || '').toLowerCase() === 'k') {
    e.preventDefault();
    e.stopPropagation();
    return 'swallowed';
  }
  return null;
}

// Where focus goes when the dialog changes under it (12 Oct 2026). Deleting disables every
// control, which would drop focus to the page: the dialog itself takes it first ('box'). Back
// from deleting, focus goes to what needs the admin: the name field when it was cleared (the
// plan moved, or the name was refused), otherwise the error. null: leave it where it is.
export function vdlFocusAfter(prev, next) {
  if (!prev || !next) return null;
  if (!prev.vdlBusy && next.vdlBusy) return 'box';
  if (prev.vdlBusy && !next.vdlBusy) {
    if (next.vdlTypeShow && !next.vdlTyped) return 'confirm';
    return next.vdlError ? 'error' : null;
  }
  return null;
}

export default function VendorDeleteDialog({ vals }) {
  const show = !!vals.vdlShow;
  const boxRef = useRef(null);
  const cancelRef = useRef(null);
  const confirmRef = useRef(null);
  const errorRef = useRef(null);
  const prevVals = useRef(null);
  // The handlers read the newest vals without re-binding the listener on every render.
  const live = useRef(vals);
  live.current = vals;

  useEffect(() => {
    if (!show || typeof document === 'undefined') return undefined;
    const opener = (live.current.vdlOpener && live.current.vdlOpener()) || document.activeElement;
    // The least destructive control first: nothing on this dialog should be one keypress away
    // from deleting.
    const t = setTimeout(() => { if (cancelRef.current) cancelRef.current.focus({ preventScroll: true }); }, 0);
    const onKey = (e) => {
      if (vdlKey(e, { busy: !!live.current.vdlBusy, close: () => live.current.vdlClose() })) return;
      if (e.key !== 'Tab' || !boxRef.current) return;
      const els = Array.prototype.slice.call(boxRef.current.querySelectorAll(FOCUSABLE));
      if (!els.length) { e.preventDefault(); return; }
      const first = els[0], last = els[els.length - 1];
      const inside = boxRef.current.contains(document.activeElement);
      if (e.shiftKey && (!inside || document.activeElement === first)) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && (!inside || document.activeElement === last)) { e.preventDefault(); first.focus(); }
    };
    document.addEventListener('keydown', onKey, true);
    return () => {
      clearTimeout(t);
      document.removeEventListener('keydown', onKey, true);
      // Back to the control that opened it. After a delete the panel shows the next vendor and
      // its own Delete vendor control; with no vendor left, the directory search takes focus.
      setTimeout(() => {
        const back = opener && opener.isConnected ? opener : document.querySelector('[aria-label="Search the vendor directory"]');
        if (back && typeof back.focus === 'function') back.focus({ preventScroll: true });
      }, 0);
    };
  }, [show]);

  // Focus follows the delete in and out of 'working' (vdlFocusAfter). A layout effect: it runs
  // in the same commit that disables the controls, before paint, so focus never rests on <body>.
  const busyNow = !!vals.vdlBusy;
  useLayoutEffect(() => {
    const prev = prevVals.current;
    prevVals.current = show ? { vdlBusy: busyNow, vdlTyped: vals.vdlTyped, vdlError: vals.vdlError, vdlTypeShow: vals.vdlTypeShow } : null;
    const to = vdlFocusAfter(prev, prevVals.current);
    const el = to === 'box' ? boxRef.current : to === 'confirm' ? confirmRef.current : to === 'error' ? errorRef.current : null;
    if (el && typeof el.focus === 'function') el.focus({ preventScroll: to === 'box' });
  }, [show, busyNow]);

  const status = <div role="status" aria-live="polite" style={SR_ONLY}>{vals.vdlSaid || ''}</div>;
  if (!show || typeof document === 'undefined') return status;

  const busy = !!vals.vdlBusy;
  const can = !!vals.vdlCanDelete;
  return (
    <>
      {status}
      {createPortal(
        <div onClick={busy ? undefined : vals.vdlClose}
          style={{ position: 'fixed', inset: '0', zIndex: '61', background: 'var(--scrim)', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '16px', boxSizing: 'border-box', overflowY: 'auto' }}>
          {/* max-height is .vdl-box's (styles/hover.css): 100dvh, with 100vh where it is not
              understood — on iOS 100vh is the screen without its toolbars, and the top of a long
              plan would sit out of reach. tabIndex -1: it holds focus while the delete runs. */}
          <div ref={boxRef} role="dialog" aria-modal="true" aria-labelledby="vdl-title" aria-describedby="vdl-desc" aria-busy={busy}
            tabIndex={-1} className="vdl-box"
            onClick={(e) => e.stopPropagation()}
            style={{ width: '100%', maxWidth: '520px', outline: 'none', display: 'flex', flexDirection: 'column', background: 'var(--color-surface)', borderRadius: '14px', boxShadow: 'var(--shadow-lg)', overflow: 'hidden' }}>

            <div style={{ padding: '20px 22px 8px', display: 'flex', alignItems: 'flex-start', gap: '12px', flexShrink: '0' }}>
              <span aria-hidden="true" style={{ width: '32px', height: '32px', borderRadius: '9px', background: 'var(--st-risk-bg)', color: 'var(--st-risk)', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', flexShrink: '0' }}>
                <i className="ph ph-trash" style={{ fontSize: '16px' }}></i>
              </span>
              <div style={{ minWidth: '0', flex: '1' }}>
                <h2 id="vdl-title" style={{ margin: '0', fontSize: '18px', lineHeight: '1.3', overflowWrap: 'anywhere', textWrap: 'balance' }}>{vals.vdlTitle}</h2>
                {vals.vdlSub ? (
                  <div style={{ fontFamily: 'ui-monospace,monospace', fontSize: '11.5px', color: 'var(--color-neutral-500)', marginTop: '4px', overflowWrap: 'anywhere' }}>{vals.vdlSub}</div>
                ) : null}
              </div>
            </div>

            <div style={{ padding: '8px 22px 18px', overflowY: 'auto', flex: '1 1 auto', minHeight: '0', fontSize: '13px', lineHeight: '1.55', color: 'var(--color-neutral-400)' }}>
              <div id="vdl-desc">
                {vals.vdlReading ? (
                  <p style={{ margin: '0', display: 'flex', alignItems: 'center', gap: '8px', color: 'var(--color-neutral-500)' }}>
                    <i className="ph ph-circle-notch" aria-hidden="true" style={{ fontSize: '14px', animation: 'spin 1s linear infinite' }}></i>
                    <span>{vals.vdlReading}</span>
                  </p>
                ) : null}
                {vals.vdlMode ? (
                  <p style={{ margin: '0', color: 'var(--color-text)' }}>{vals.vdlMode}</p>
                ) : null}
                {vals.vdlScope ? (
                  <p style={{ margin: '6px 0 0' }}>{vals.vdlScope + ' ' + vals.vdlPermanent}</p>
                ) : null}
              </div>

              {vals.vdlChanged ? (
                <div role="status" style={{ marginTop: '12px', padding: '10px 12px', borderRadius: '8px', background: 'var(--st-warn-bg)', color: 'var(--color-text)', fontSize: '12.5px', display: 'flex', gap: '8px', alignItems: 'flex-start' }}>
                  <i className="ph ph-arrows-clockwise" aria-hidden="true" style={{ fontSize: '14px', color: 'var(--st-warn)', marginTop: '2px', flexShrink: '0' }}></i>
                  <span>{vals.vdlChanged}</span>
                </div>
              ) : null}

              {(vals.vdlGroups || []).map((g) => (
                <section key={g.key} aria-label={g.title} style={{ marginTop: '16px' }}>
                  <div style={{ fontSize: '12.5px', color: 'var(--color-text)' }}>{g.title}</div>
                  <div style={{ fontSize: '11.5px', color: 'var(--color-neutral-500)', marginTop: '1px' }}>{g.note}</div>
                  <ul style={{ listStyle: 'none', margin: '8px 0 0', padding: '0', borderTop: '1px solid var(--color-divider)' }}>
                    {g.rows.map((r) => (
                      <li key={r.key} style={{ padding: '7px 0', borderBottom: '1px solid var(--color-divider)' }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', gap: '12px', fontSize: '12.5px' }}>
                          <span style={{ color: 'var(--color-neutral-300)', minWidth: '0', overflowWrap: 'anywhere' }}>{r.label}</span>
                          <span style={{ fontVariantNumeric: 'tabular-nums', color: 'var(--color-text)', flexShrink: '0' }}>{r.n}</span>
                        </div>
                        {r.items.length ? (
                          <div style={{ fontSize: '11.5px', color: 'var(--color-neutral-500)', marginTop: '3px', overflowWrap: 'anywhere' }}>
                            {r.items.join(', ')}{r.more ? ' ' + r.more : ''}
                          </div>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                </section>
              ))}

              {vals.vdlTypeShow ? (
                <div style={{ marginTop: '18px', display: 'flex', flexDirection: 'column', gap: '14px' }}>
                  <div>
                    <label htmlFor="vdl-reason" style={LABEL}>{'Why it is being deleted (optional)'}</label>
                    <textarea id="vdl-reason" className="vdl-field" rows={2} maxLength={500} value={vals.vdlReasonText} onChange={vals.vdlSetReason} disabled={busy}
                      aria-describedby="vdl-reason-left"
                      style={Object.assign({}, FIELD, { border: '1px solid var(--color-divider)', resize: 'vertical', minHeight: '56px' })} />
                    <div id="vdl-reason-left" style={{ fontSize: '11px', color: 'var(--color-neutral-500)', marginTop: '3px', textAlign: 'right' }}>{vals.vdlReasonLeft}</div>
                  </div>
                  <div>
                    <label htmlFor="vdl-confirm" style={LABEL}>
                      {'Type '}<b style={{ color: 'var(--color-text)', fontWeight: '600', overflowWrap: 'anywhere' }}>{vals.vdlConfirmWith}</b>{' to confirm'}
                    </label>
                    <input id="vdl-confirm" ref={confirmRef} className="vdl-field" value={vals.vdlTyped} onChange={vals.vdlSetTyped} disabled={busy}
                      autoComplete="off" autoCorrect="off" autoCapitalize="off" spellCheck={false}
                      onKeyDown={(e) => { if (e.key === 'Enter' && can) vals.vdlConfirm(); }}
                      style={Object.assign({}, FIELD, { border: '1px solid ' + (vals.vdlNameOk ? 'var(--st-risk)' : 'var(--color-divider)') })} />
                  </div>
                </div>
              ) : null}

              {vals.vdlError ? (
                // The text in the text colour: risk orange on its own tint is under 3:1 in the light
                // theme. The risk colour marks the box, on its left edge (12 Oct 2026).
                <div role="alert" ref={errorRef} tabIndex={-1} style={{ marginTop: '14px', padding: '10px 12px', borderRadius: '8px', borderLeft: '3px solid var(--st-risk)', outline: 'none', background: 'var(--st-risk-bg)', color: 'var(--color-text)', fontSize: '12.5px', lineHeight: '1.5', display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: '12px', flexWrap: 'wrap' }}>
                  <span style={{ minWidth: '0', flex: '1 1 220px' }}>{vals.vdlError}</span>
                  {vals.vdlRetryShow ? (
                    <button type="button" className="hv27" onClick={vals.vdlRetry}
                      style={{ fontFamily: 'inherit', fontSize: '12px', padding: '4px 10px', borderRadius: '7px', border: '1px solid var(--st-risk)', background: 'transparent', color: 'var(--color-text)', cursor: 'pointer', whiteSpace: 'nowrap' }}>
                      {'Try again'}
                    </button>
                  ) : null}
                </div>
              ) : null}
            </div>

            <div style={{ padding: '14px 22px', borderTop: '1px solid var(--color-divider)', display: 'flex', justifyContent: 'flex-end', alignItems: 'center', gap: '10px', flexWrap: 'wrap', flexShrink: '0' }}>
              <button type="button" ref={cancelRef} className="hv4 vdl-btn" onClick={vals.vdlClose} disabled={!vals.vdlCanCancel}
                style={{ fontFamily: 'inherit', fontSize: '12.5px', padding: '8px 15px', borderRadius: '8px', border: '1px solid var(--color-divider)', background: 'transparent', color: 'var(--color-neutral-400)', cursor: vals.vdlCanCancel ? 'pointer' : 'not-allowed', opacity: vals.vdlCanCancel ? '1' : '0.55' }}>
                {vals.vdlCancelLabel}
              </button>
              {vals.vdlDeleteShow ? (
                <button type="button" className="vdl-btn" onClick={can ? vals.vdlConfirm : undefined} disabled={!can} aria-disabled={!can}
                  style={{ display: 'inline-flex', alignItems: 'center', gap: '7px', fontFamily: 'inherit', fontSize: '12.5px', fontWeight: '500', padding: '8px 16px', borderRadius: '8px', border: 'none', background: can || busy ? 'var(--st-risk)' : 'var(--color-neutral-800)', color: can || busy ? 'var(--accent-ink)' : 'var(--color-neutral-500)', cursor: can ? 'pointer' : busy ? 'progress' : 'not-allowed' }}>
                  <i className={busy ? 'ph ph-circle-notch' : 'ph ph-trash'} aria-hidden="true" style={{ fontSize: '14px', animation: busy ? 'spin 1s linear infinite' : 'none' }}></i>
                  <span>{vals.vdlDeleteLabel}</span>
                </button>
              ) : null}
            </div>
          </div>
        </div>,
        document.body
      )}
    </>
  );
}
