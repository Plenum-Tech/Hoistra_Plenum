// ConnectModal — connect-a-source modal (Integrations)
// Ported from the Hoistra prototype template. `vals` is the view model from useHoistra().
import React from 'react';

export default function ConnectModal({ vals }) {
  return (
    <>
      <div onClick={vals.intCancel} style={{ position: "fixed", inset: "0", background: "var(--scrim)", zIndex: "80" }}></div>
      <div style={{ position: "fixed", top: "50%", left: "50%", transform: "translate(-50%,-50%)", zIndex: "81", width: "min(480px,calc(100vw - 48px))", borderRadius: "12px", background: "var(--color-surface)", boxShadow: "var(--shadow-lg)", overflow: "hidden", animation: "fadeUp 0.2s ease both" }}>
        <div style={{ display: "flex", alignItems: "flex-start", gap: "12px", padding: "15px 17px", borderBottom: "1px solid var(--color-divider)" }}>
          <div style={{ flex: "1", minWidth: "0" }}>
            <div style={{ fontSize: "14px" }}>
              {"Connect "}{vals.intModal.name}
            </div>
            <div style={{ fontSize: "11px", color: "var(--color-neutral-500)", marginTop: "3px" }}>
              {vals.intModal.fam}{" · "}{vals.intModal.gives}
            </div>
          </div>
          <i className="ph ph-x hv16" onClick={vals.intCancel} style={{ fontSize: "15px", color: "var(--color-neutral-500)", cursor: "pointer" }}></i>
        </div>
        <div style={{ padding: "15px 17px", display: "flex", flexDirection: "column", gap: "12px" }}>
          <div>
            <div style={{ fontSize: "10px", letterSpacing: "0.09em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
              {"Connection name"}
            </div>
            <input className="input" value={vals.intName} onChange={vals.intSetName} placeholder={vals.intModal.namePh} style={{ width: "100%", boxSizing: "border-box", marginTop: "6px", fontSize: "12.5px", padding: "8px 10px", borderRadius: "7px", border: "1px solid var(--color-divider)", background: "var(--color-bg)", color: "var(--color-text)", fontFamily: "var(--font-body)", outline: "none" }} />
          </div>
          <div>
            <div style={{ fontSize: "10px", letterSpacing: "0.09em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
              {vals.intModal.urlLabel}
            </div>
            <input className="input" value={vals.intUrl} onChange={vals.intSetUrl} placeholder={vals.intModal.urlPh} style={{ width: "100%", boxSizing: "border-box", marginTop: "6px", fontSize: "12.5px", padding: "8px 10px", borderRadius: "7px", border: "1px solid var(--color-divider)", background: "var(--color-bg)", color: "var(--color-text)", fontFamily: "ui-monospace,monospace", outline: "none" }} />
          </div>
          <div>
            <div style={{ fontSize: "10px", letterSpacing: "0.09em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
              {"Tables it will write to"}
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: "6px", marginTop: "7px" }}>
              {(vals.intModalTables || []).map((t, $index) => (
                <React.Fragment key={$index}>
                  <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10.5px", padding: "3px 8px", borderRadius: "5px", background: "var(--color-accent-900)", color: "var(--color-accent)" }}>
                    {t.label}
                  </span>
                </React.Fragment>
              ))}
            </div>
            <div style={{ fontSize: "10.5px", color: "var(--color-neutral-500)", marginTop: "8px", lineHeight: "1.5" }}>
              {"Nothing is written until the field mapping is confirmed. The orchestrator reads the source schema first, proposes the mapping, and holds it in the decision queue for you to approve."}
            </div>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "9px", justifyContent: "flex-end", marginTop: "2px" }}>
            <div className="hv16" onClick={vals.intCancel} style={{ fontSize: "12px", padding: "7px 13px", borderRadius: "8px", border: "1px solid var(--color-divider)", color: "var(--color-neutral-400)", cursor: "pointer" }}>
              {"Cancel"}
            </div>
            <div className="btn btn-primary" onClick={vals.intConfirm} style={{ fontSize: "12px", padding: "7px 15px", cursor: "pointer" }}>
              {"Connect"}
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
