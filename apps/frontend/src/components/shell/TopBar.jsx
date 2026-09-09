// TopBar — logo, currency, Pending pill, tenant, account menu
// Ported from the Hoistra prototype template. `vals` is the view model from useHoistra().
import React from 'react';

export default function TopBar({ vals }) {
  return (
      <div style={{ display: "flex", alignItems: "center", flexWrap: "wrap", gap: "10px 18px", padding: "9px 26px", minHeight: "62px", boxSizing: "border-box", flexShrink: "0", borderBottom: "1px solid var(--color-divider)", position: "sticky", top: "0", background: "var(--color-bg)", zIndex: "40" }}>
        <div className="hv9" onClick={vals.goHome} style={{ display: "flex", alignItems: "center", gap: "11px", cursor: "pointer" }}>
          <div style={{ width: "26px", height: "26px", borderRadius: "7px", border: "1.5px solid var(--color-accent)", display: "flex", alignItems: "center", justifyContent: "center" }}>
            <div style={{ width: "9px", height: "9px", borderRadius: "50%", background: "var(--color-accent)" }}></div>
          </div>
          <span style={{ fontSize: "21px", fontWeight: "500", letterSpacing: "-0.015em" }}>
            {"Hoistra"}
          </span>
        </div>
        <div style={{ flex: "1" }}></div>
        <div style={{ display: "flex", alignItems: "center", gap: "2px", padding: "2px", borderRadius: "7px", border: "1px solid var(--color-divider)" }}>
          {(vals.currencies || []).map((c, $index) => (
            <React.Fragment key={$index}>
              <div onClick={c.pick} title="Reporting currency" style={{ fontSize: "11px", padding: "4px 9px", borderRadius: "5px", cursor: "pointer", color: c.fg, background: c.bg }}>
                {c.label}
              </div>
            </React.Fragment>
          ))}
        </div>
        <div className="hv7" onClick={vals.toggleQueue} style={{ animation: "pendingBlink 2.4s ease-in-out infinite", display: "flex", alignItems: "center", gap: "8px", whiteSpace: "nowrap", flexShrink: "0", fontSize: "13px", padding: "8px 15px", borderRadius: "7px", cursor: "pointer", background: "var(--st-warn)", color: "var(--accent-ink)" }}>
          <i className="ph ph-bell-ringing" style={{ fontSize: "15px" }}></i>
          <span>
            {vals.queueCount}{" Pending"}
          </span>
        </div>
        <div style={{ width: "1px", height: "16px", background: "var(--color-divider)" }}></div>
        <div style={{ display: vals.tenantShow, alignItems: "center", gap: "6px", whiteSpace: "nowrap", flexShrink: "0", fontSize: "11px", color: "var(--color-neutral-500)" }}>
          <i className="ph ph-buildings" style={{ fontSize: "12px" }}></i>
          <span>
            {vals.tenant}
          </span>
        </div>
        <i className="ph ph-cpu hv6" onClick={vals.openOrch} title="Open the orchestrator — ask anything beside this page" style={{ fontSize: "15px", color: "var(--color-neutral-500)", cursor: "pointer" }}></i>
        <div style={{ position: "relative", flexShrink: "0" }}>
          <div className="hv3" onClick={vals.toggleAcct} title="Aasim" style={{ width: "28px", height: "28px", borderRadius: "50%", background: vals.acctBg, color: vals.acctFg, display: "flex", alignItems: "center", justifyContent: "center", fontSize: "12.5px", cursor: "pointer", border: `1px solid ${vals.acctEdge}` }}>
            {"A"}
          </div>
          {vals.acctOpen ? (
            <>
              <div style={{ position: "absolute", top: "36px", right: "0", zIndex: "70", width: "212px", borderRadius: "10px", background: "var(--color-surface)", boxShadow: "var(--shadow-lg)", overflow: "hidden", animation: "fadeUp 0.16s ease both" }}>
                <div style={{ padding: "11px 13px", borderBottom: "1px solid var(--color-divider)" }}>
                  <div style={{ fontSize: "12.5px" }}>
                    {"Aasim"}
                  </div>
                  <div style={{ fontSize: "10.5px", color: "var(--color-neutral-500)", marginTop: "2px" }}>
                    {vals.acctRole}{" · Planum Technologies"}
                  </div>
                </div>
                {(vals.acctItems || []).map((a, $index) => (
                  <React.Fragment key={$index}>
                    <div className="hv2" onClick={a.click} style={{ display: "flex", alignItems: "center", gap: "9px", padding: "9px 13px", fontSize: "12.5px", color: a.fg, cursor: "pointer" }}>
                      <i className={`ph ${a.icon}`} style={{ fontSize: "14px", color: a.iconFg }}></i>
                      <span style={{ flex: "1" }}>
                        {a.label}
                      </span>
                      <i className="ph ph-check" style={{ fontSize: "12px", color: "var(--color-accent)", display: a.tickShow }}></i>
                    </div>
                  </React.Fragment>
                ))}
                <div className="hv10" onClick={vals.signOut} style={{ display: "flex", alignItems: "center", gap: "9px", padding: "9px 13px", fontSize: "12.5px", color: "var(--color-neutral-500)", cursor: "pointer", borderTop: "1px solid var(--color-divider)" }}>
                  <i className="ph ph-sign-out" style={{ fontSize: "14px" }}></i>
                  <span>
                    {"Sign out"}
                  </span>
                </div>
              </div>
            </>
          ) : null}
        </div>
      </div>
  );
}
