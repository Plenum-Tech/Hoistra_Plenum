// PasswordModal — change the password from inside a session (account menu → Change password).
// On success the API ends every session, this one included, so the controller signs out and
// the gate shows the server's line. `vals` is the view model from useHoistra().
import React from 'react';

const LABEL = { fontSize: "10px", letterSpacing: "0.09em", textTransform: "uppercase", color: "var(--color-neutral-500)" };
const INPUT = { width: "100%", boxSizing: "border-box", marginTop: "6px", fontSize: "12.5px", padding: "8px 10px", borderRadius: "7px", border: "1px solid var(--color-divider)", background: "var(--color-bg)", color: "var(--color-text)", fontFamily: "var(--font-body)", outline: "none" };

export default function PasswordModal({ vals }) {
  return (
    <>
      <div onClick={vals.pwClose} style={{ position: "fixed", inset: "0", background: "var(--scrim)", zIndex: "80" }}></div>
      <div style={{ position: "fixed", top: "50%", left: "50%", transform: "translate(-50%,-50%)", zIndex: "81", width: "min(420px,calc(100vw - 48px))", borderRadius: "12px", background: "var(--color-surface)", boxShadow: "var(--shadow-lg)", overflow: "hidden", animation: "fadeUp 0.2s ease both" }}>
        <div style={{ display: "flex", alignItems: "flex-start", gap: "12px", padding: "15px 17px", borderBottom: "1px solid var(--color-divider)" }}>
          <div style={{ flex: "1", minWidth: "0" }}>
            <div style={{ fontSize: "14px" }}>
              {"Change password"}
            </div>
            <div style={{ fontSize: "11px", color: "var(--color-neutral-500)", marginTop: "3px" }}>
              {"Every session signs out, this one included — you'll sign in again with the new one."}
            </div>
          </div>
          <i className="ph ph-x hv16" onClick={vals.pwClose} style={{ fontSize: "15px", color: "var(--color-neutral-500)", cursor: "pointer" }}></i>
        </div>
        <div style={{ padding: "15px 17px", display: "flex", flexDirection: "column", gap: "12px" }}>
          <div>
            <div style={LABEL}>{"Current password"}</div>
            <input className="input" type="password" autoComplete="current-password" value={vals.pwCurrent} onChange={vals.pwSetCurrent} onKeyDown={vals.pwKey} style={INPUT} />
          </div>
          <div>
            <div style={LABEL}>{"New password"}</div>
            <input className="input" type="password" autoComplete="new-password" value={vals.pwNext} onChange={vals.pwSetNext} onKeyDown={vals.pwKey} style={INPUT} />
            <div style={{ fontSize: "11px", color: "var(--color-neutral-500)", lineHeight: "1.5", marginTop: "6px" }}>
              {"At least " + vals.authMinLength + " characters — a memorable phrase beats a short one with symbols."}
            </div>
          </div>
          {vals.pwError ? <div style={{ fontSize: "12px", lineHeight: "1.5", color: "var(--st-risk)" }}>{vals.pwError}</div> : null}
          <div style={{ display: "flex", alignItems: "center", gap: "9px", justifyContent: "flex-end", marginTop: "2px" }}>
            <div className="hv16" onClick={vals.pwClose} style={{ fontSize: "12px", padding: "7px 13px", borderRadius: "8px", border: "1px solid var(--color-divider)", color: "var(--color-neutral-400)", cursor: "pointer" }}>
              {"Cancel"}
            </div>
            <div className="btn btn-primary" onClick={vals.pwBusy ? undefined : vals.pwSubmit} style={{ fontSize: "12px", padding: "7px 15px", cursor: vals.pwBusy ? "default" : "pointer", opacity: vals.pwBusy ? 0.55 : 1 }}>
              {vals.pwBusy ? "Changing…" : "Change password"}
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
