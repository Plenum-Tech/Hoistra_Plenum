// AuditTrail — Ingestion audit trail (admin). `vals` is the view model from useHoistra().
import React from 'react';

export default function AuditTrail({ vals }) {
  return (
    <div style={{ flex: "1", display: "flex", justifyContent: "flex-start", padding: "0 40px 80px" }}>
      <div style={{ width: "100%", maxWidth: "1400px", animation: "fadeUp 0.28s ease both" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "12px", padding: "24px 0 0" }}>
          <div className="hv6" onClick={vals.goHome} style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "12px", color: "var(--color-neutral-400)", cursor: "pointer" }}>
            <i className="ph ph-arrow-left" style={{ fontSize: "12px" }}></i>
            <span>{"Home"}</span>
          </div>
          <span style={{ fontSize: "12px", color: "var(--color-neutral-500)" }}>{"/"}</span>
          <span style={{ fontSize: "12px", color: "var(--color-neutral-500)" }}>{"Administration"}</span>
          <span style={{ fontSize: "12px", color: "var(--color-neutral-500)" }}>{"/"}</span>
          <span style={{ fontSize: "12px", color: "var(--color-neutral-500)" }}>{"Ingestion audit trail"}</span>
        </div>
        <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", flexWrap: "wrap", gap: "16px 24px", marginTop: "18px" }}>
          <div style={{ minWidth: "0", flex: "1 1 340px" }}>
            <div style={{ fontSize: "10.5px", letterSpacing: "0.13em", textTransform: "uppercase", color: "var(--color-accent)" }}>{"Administration · ingestion-validation"}</div>
            <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap", marginTop: "7px" }}>
              <h2 style={{ fontSize: "28px", margin: "0", lineHeight: "1.15" }}>{"Ingestion audit trail"}</h2>
              <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10px", letterSpacing: "0.09em", textTransform: "uppercase", padding: "3px 8px", borderRadius: "5px", background: "var(--color-accent)", color: "var(--accent-ink)" }}>{"Admin only"}</span>
            </div>
            <p style={{ fontSize: "13px", color: "var(--color-neutral-400)", margin: "8px 0 0", maxWidth: "88ch", lineHeight: "1.55" }}>
              {"Every flagged ingestion, clarification, override and approval — for users and admins alike. If a document is ever questioned, this record shows who submitted it, what warning was presented, what explanation was given, and who approved the final action."}
            </p>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap" }}>
            <span style={{ display: "inline-flex", alignItems: "center", gap: "7px", padding: "3px 10px", borderRadius: "20px", border: "1px solid var(--color-divider)", fontSize: "10.5px", color: "var(--color-neutral-500)" }}>
              <span style={{ width: "6px", height: "6px", borderRadius: "50%", background: vals.auLiveSourceDot }}></span>
              <span>{vals.auLiveSourceLabel}</span>
              <span className="hv11" onClick={vals.auLiveRetry} style={{ color: "var(--color-accent)", cursor: "pointer", display: vals.auLiveRetryShow }}>{"Retry"}</span>
            </span>
            <div className="btn btn-primary" onClick={vals.ingStart} style={{ fontSize: "12px", padding: "7px 14px", cursor: "pointer", display: "flex", alignItems: "center", gap: "7px" }}>
              <i className="ph ph-play" style={{ fontSize: "12px" }}></i>
              <span>{"Run a sample ingestion"}</span>
            </div>
            <div style={{ padding: "8px 13px", borderRadius: "9px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", display: "flex", flexDirection: "column", gap: "2px", whiteSpace: "nowrap" }}>
              <span style={{ fontSize: "9.5px", letterSpacing: "0.11em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>{"Entries"}</span>
              <span style={{ fontSize: "12.5px", fontVariantNumeric: "tabular-nums" }}>{vals.auCount}</span>
            </div>
          </div>
        </div>
        <div style={{ display: "flex", gap: "7px", flexWrap: "wrap", marginTop: "20px" }}>
          {(vals.auFilters || []).map((f, $index) => (
            <React.Fragment key={$index}>
              <div onClick={f.pick} style={{ fontSize: "11.5px", padding: "5px 12px", borderRadius: "6px", background: f.bg, color: f.fg, cursor: "pointer", boxShadow: "var(--shadow-sm)" }}>{f.label}</div>
            </React.Fragment>
          ))}
        </div>
        <div style={{ marginTop: "14px", borderRadius: "11px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", overflow: "hidden" }}>
          {(vals.auRows || []).map((a, $index) => (
            <React.Fragment key={$index}>
              <div style={{ borderBottom: "1px solid var(--color-divider)" }}>
                <div className="hv2" onClick={a.toggle} style={{ display: "grid", gridTemplateColumns: "22px 110px minmax(150px,0.8fr) minmax(220px,1.4fr) minmax(160px,1fr) 170px", gap: "12px", alignItems: "center", padding: "11px 16px", cursor: "pointer" }}>
                  <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "11px", color: "var(--color-neutral-500)" }}>{a.arrow}</span>
                  <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10.5px", color: "var(--color-neutral-500)", whiteSpace: "nowrap" }}>{a.when}</span>
                  <div style={{ minWidth: "0" }}>
                    <div style={{ fontSize: "12.5px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{a.who}</div>
                    <div style={{ fontSize: "10.5px", color: "var(--color-neutral-500)", marginTop: "2px" }}>{a.role}</div>
                  </div>
                  <div style={{ fontSize: "12px", color: "var(--color-neutral-300)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{a.doc}</div>
                  <div style={{ fontSize: "11.5px", color: "var(--color-neutral-400)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{a.route}</div>
                  <span style={{ fontSize: "10.5px", padding: "3px 9px", borderRadius: "5px", background: a.bg, color: a.fg, textAlign: "center", whiteSpace: "nowrap" }}>{a.outcome}</span>
                </div>
                <div style={{ display: a.panelShow, gridTemplateColumns: "repeat(auto-fit,minmax(260px,1fr))", gap: "12px 24px", padding: "14px 16px 16px 50px", background: "var(--color-bg)", borderTop: "1px solid var(--color-divider)" }}>
                  {(a.fields || []).map((f, $index2) => (
                    <React.Fragment key={$index2}>
                      <div style={{ minWidth: "0" }}>
                        <div style={{ fontSize: "9.5px", letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>{f.l}</div>
                        <div style={{ fontSize: "12px", color: "var(--color-neutral-300)", marginTop: "3px", lineHeight: "1.5" }}>{f.v}</div>
                      </div>
                    </React.Fragment>
                  ))}
                </div>
              </div>
            </React.Fragment>
          ))}
        </div>
      </div>
    </div>
  );
}
