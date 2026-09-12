// SuperAdminOverlay — the Super Admin console, deliberately separate from any
// company's own admin app. `vals` is the view model from useHoistra().
import React from 'react';

export default function SuperAdminOverlay({ vals }) {
  return (
    <div style={{ position: "fixed", inset: "0", zIndex: "96", background: "var(--color-bg)", overflowY: "auto" }}>
      <div style={{ position: "sticky", top: "0", zIndex: "5", display: "flex", alignItems: "center", gap: "12px", padding: "11px 26px", background: "var(--color-surface)", borderBottom: "1px solid var(--color-divider)" }}>
        <i className="ph ph-lock-key" style={{ fontSize: "15px", color: "var(--color-accent)" }}></i>
        <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "12px", color: "var(--color-neutral-300)" }}>{"superadmin.hoistra.com"}</span>
        <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "9.5px", letterSpacing: "0.09em", textTransform: "uppercase", padding: "3px 8px", borderRadius: "5px", background: "var(--color-accent)", color: "var(--accent-ink)" }}>{"Platform operator"}</span>
        <div style={{ flex: "1" }}></div>
        <div className="hv4" onClick={vals.saClose} style={{ display: "flex", alignItems: "center", gap: "7px", fontSize: "12px", padding: "6px 13px", borderRadius: "7px", border: "1px solid var(--color-divider)", color: "var(--color-neutral-400)", cursor: "pointer" }}>
          <i className="ph ph-arrow-left" style={{ fontSize: "12px" }}></i>
          <span>{"Back to Hoistra"}</span>
        </div>
      </div>
      <div style={{ maxWidth: "1280px", margin: "0 auto", padding: "28px 40px 90px" }}>
        <div style={{ fontSize: "10.5px", letterSpacing: "0.13em", textTransform: "uppercase", color: "var(--color-accent)" }}>{"Super Admin · deliberately light"}</div>
        <h1 style={{ fontSize: "31px", margin: "7px 0 0", lineHeight: "1.1" }}>{"Companies on the platform"}</h1>
        <p style={{ fontSize: "13px", color: "var(--color-neutral-400)", margin: "9px 0 0", maxWidth: "88ch", lineHeight: "1.55" }}>
          {"Create company accounts, invite each company's administrator, and monitor usage and credit consumption. Day-to-day data management stays inside each company's own admin application — this console only onboards and observes."}
        </p>
        {vals.saLiveError ? (
          <div style={{ marginTop: "12px", padding: "8px 12px", borderRadius: "8px", background: "var(--st-warn-bg)", display: "flex", alignItems: "center", gap: "12px", maxWidth: "88ch" }}>
            <span style={{ flex: "1", fontSize: "11.5px", color: "var(--st-warn)", lineHeight: "1.5" }}>{vals.saLiveError}</span>
            <span className="hv4" onClick={vals.saLiveRetry} style={{ fontSize: "11.5px", color: "var(--st-warn)", textDecoration: "underline", cursor: "pointer", whiteSpace: "nowrap" }}>{"Retry now"}</span>
          </div>
        ) : null}
        <div style={{ display: "grid", gridTemplateColumns: "minmax(260px,320px) minmax(0,1fr)", gap: "22px", marginTop: "24px" }}>
          <div style={{ display: "flex", flexDirection: "column", gap: "10px", minWidth: "0" }}>
            <div className="btn btn-primary" onClick={vals.saToggleNew} style={{ fontSize: "12px", padding: "8px 14px", cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "center", gap: "7px" }}>
              <i className="ph ph-plus" style={{ fontSize: "13px" }}></i>
              <span>{"Create company"}</span>
            </div>
            {vals.saNewOpen ? (
              <div style={{ padding: "14px", borderRadius: "10px", background: "var(--color-surface)", boxShadow: "var(--shadow-md)", display: "flex", flexDirection: "column", gap: "10px" }}>
                <input className="input" value={vals.saName} onChange={vals.saSetName} placeholder="Company name" style={{ fontSize: "12.5px", padding: "8px 11px", borderRadius: "7px", border: "1px solid var(--color-divider)", background: "var(--color-bg)", color: "var(--color-text)", fontFamily: "var(--font-body)", outline: "none" }} />
                <div style={{ display: "flex", gap: "5px", flexWrap: "wrap" }}>
                  {(vals.saCcs || []).map((c, $index) => (
                    <React.Fragment key={$index}>
                      <div onClick={c.pick} style={{ fontSize: "11px", padding: "4px 10px", borderRadius: "5px", background: c.bg, color: c.fg, cursor: "pointer" }}>{c.label}</div>
                    </React.Fragment>
                  ))}
                </div>
                <input className="input" value={vals.saEmail} onChange={vals.saSetEmail} placeholder="Administrator email (optional)" style={{ fontSize: "12.5px", padding: "8px 11px", borderRadius: "7px", border: "1px solid var(--color-divider)", background: "var(--color-bg)", color: "var(--color-text)", fontFamily: "var(--font-body)", outline: "none" }} />
                <div className="btn btn-primary" onClick={vals.saCreate} style={{ fontSize: "12px", padding: "7px 13px", cursor: "pointer", textAlign: "center" }}>{"Create and invite"}</div>
              </div>
            ) : null}
            {(vals.saCompanies || []).map((c, $index) => (
              <React.Fragment key={$index}>
                <div className="hv1" onClick={c.pick} style={{ display: "flex", alignItems: "center", gap: "10px", padding: "12px 14px", borderRadius: "10px", background: c.bg, border: `1px solid ${c.edge}`, cursor: "pointer", boxShadow: "var(--shadow-sm)" }}>
                  <span style={{ width: "8px", height: "8px", borderRadius: "50%", background: c.dot, flexShrink: "0" }}></span>
                  <div style={{ flex: "1", minWidth: "0" }}>
                    <div style={{ fontSize: "13px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{c.name}</div>
                    <div style={{ fontSize: "10.5px", color: "var(--color-neutral-500)", marginTop: "2px" }}>{c.cc}{" · "}{c.status}</div>
                  </div>
                  <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10.5px", color: "var(--color-neutral-500)", whiteSpace: "nowrap" }}>{c.credits}</span>
                </div>
              </React.Fragment>
            ))}
          </div>
          <div style={{ minWidth: "0" }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: "12px" }}>
              <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                <span style={{ width: "9px", height: "9px", borderRadius: "50%", background: vals.saCo.dot }}></span>
                <h2 style={{ fontSize: "22px", margin: "0", lineHeight: "1.15" }}>{vals.saCo.name}</h2>
                <span style={{ fontSize: "11px", color: "var(--color-neutral-500)" }}>{vals.saCo.cc}{" · "}{vals.saCo.status}</span>
              </div>
              <div className="hv25" onClick={vals.saCo.invite} style={{ display: "flex", alignItems: "center", gap: "7px", fontSize: "12px", padding: "7px 14px", borderRadius: "8px", border: "1px solid var(--color-accent)", color: "var(--color-accent)", cursor: "pointer" }}>
                <i className="ph ph-paper-plane-tilt" style={{ fontSize: "13px" }}></i>
                <span>{vals.saCo.inviteLabel}</span>
              </div>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(170px,1fr))", gap: "11px", marginTop: "16px" }}>
              {(vals.saTiles || []).map((t, $index) => (
                <React.Fragment key={$index}>
                  <div style={{ padding: "13px 14px 13px 16px", borderRadius: "10px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", position: "relative", overflow: "hidden", minWidth: "0" }}>
                    <div style={{ position: "absolute", left: "0", top: "11px", bottom: "11px", width: "3px", borderRadius: "0 3px 3px 0", background: t.color }}></div>
                    <div style={{ fontFamily: "ui-monospace,monospace", fontSize: "21px", lineHeight: "1.1", color: t.color }}>{t.value}</div>
                    <div style={{ fontSize: "10px", letterSpacing: "0.09em", textTransform: "uppercase", color: "var(--color-neutral-500)", marginTop: "5px" }}>{t.label}</div>
                    <div style={{ fontSize: "10.5px", color: "var(--color-neutral-500)", marginTop: "2px" }}>{t.hint}</div>
                  </div>
                </React.Fragment>
              ))}
            </div>
            <div style={{ marginTop: "18px", padding: "16px 18px", borderRadius: "11px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)" }}>
              <div style={{ fontSize: "10.5px", letterSpacing: "0.11em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>{"Credit consumption across companies — this month"}</div>
              <div style={{ display: "flex", flexDirection: "column", gap: "8px", marginTop: "12px" }}>
                {(vals.saCredits || []).map((c, $index) => (
                  <React.Fragment key={$index}>
                    <div style={{ display: "grid", gridTemplateColumns: "170px minmax(0,1fr) 70px", gap: "12px", alignItems: "center" }}>
                      <span style={{ fontSize: "12px", color: c.fg, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{c.name}</span>
                      <div style={{ height: "7px", borderRadius: "4px", background: "var(--color-bg)", overflow: "hidden" }}>
                        <div style={{ height: "100%", width: c.bar, borderRadius: "4px", background: "var(--color-accent)" }}></div>
                      </div>
                      <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "11px", color: "var(--color-neutral-400)", textAlign: "right" }}>{c.v}</span>
                    </div>
                  </React.Fragment>
                ))}
              </div>
              <div style={{ fontSize: "11px", color: "var(--color-neutral-500)", marginTop: "12px", lineHeight: "1.5" }}>
                {"Billing is usage-led: platform activity, API requests and credits, with a nominal per-seat charge. No user limits are imposed — more users simply consume more."}
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
