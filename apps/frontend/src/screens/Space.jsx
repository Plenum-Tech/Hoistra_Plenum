// Space — one space from the navigator: a built-in engine (Compliance, Energy, Vendor
// performance, Vendor operations) with the live figures behind its badge, or a saved space
// from svc-udr. Below the header: an ask bar whose question starts a session filed here, and
// the sessions already filed. `vals` is the view model from useHoistra(); the page reads
// vals.spacePage.
import React from 'react';
import SessionList from '../components/shell/SessionList.jsx';

const BARE = { font: "inherit", background: "transparent", border: "none", padding: "0", margin: "0", cursor: "pointer", color: "inherit" };
const BTN = { fontSize: "12px", padding: "7px 13px", borderRadius: "7px", cursor: "pointer", whiteSpace: "nowrap" };

export default function Space({ vals }) {
  const p = vals.spacePage || { missing: true, kpis: [], groups: [] };
  return (
    <div style={{ flex: "1", display: "flex", justifyContent: "center", padding: "0 32px 80px" }}>
      <div style={{ width: "100%", maxWidth: "960px", animation: "fadeUp 0.28s ease both" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "12px", padding: "24px 0 0" }}>
          <div className="hv6" onClick={vals.goHome} style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "12px", color: "var(--color-neutral-400)", cursor: "pointer" }}>
            <i className="ph ph-arrow-left" style={{ fontSize: "12px" }}></i>
            <span>{"Home"}</span>
          </div>
          <span style={{ fontSize: "12px", color: "var(--color-neutral-600)" }}>{"/"}</span>
          <span className="hv6" onClick={p.back} style={{ fontSize: "12px", color: "var(--color-neutral-500)", cursor: "pointer" }}>{"Spaces"}</span>
          {p.name ? (
            <>
              <span style={{ fontSize: "12px", color: "var(--color-neutral-600)" }}>{"/"}</span>
              <span style={{ fontSize: "12px", color: "var(--color-neutral-500)" }}>{p.name}</span>
            </>
          ) : null}
        </div>

        {p.missing ? (
          <div style={{ marginTop: "24px", padding: "28px", borderRadius: "12px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", fontSize: "13px", color: "var(--color-neutral-400)", lineHeight: "1.55" }}>
            {p.emptyText}
          </div>
        ) : (
          <>
            <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", gap: "24px", marginTop: "18px", flexWrap: "wrap" }}>
              <div style={{ display: "flex", flexDirection: "column", gap: "7px", minWidth: "0" }}>
                <div style={{ fontSize: "10.5px", letterSpacing: "0.13em", textTransform: "uppercase", color: "var(--color-accent)" }}>{p.kicker}</div>
                <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
                  <div style={{ width: "36px", height: "36px", borderRadius: "10px", background: "var(--color-accent-900)", display: "flex", alignItems: "center", justifyContent: "center", flexShrink: "0" }}>
                    <i className={`ph ${p.icon}`} style={{ fontSize: "18px", color: "var(--color-accent)" }}></i>
                  </div>
                  {p.renaming ? (
                    <input className="input" value={p.renameText} onChange={p.setRename} onKeyDown={p.renameKey} autoFocus style={{ fontSize: "24px", padding: "4px 8px", borderRadius: "7px", border: "1px solid var(--color-accent)", background: "var(--color-surface)", color: "var(--color-text)", fontFamily: "var(--font-body)", outline: "none", minWidth: "0" }} />
                  ) : (
                    <h1 style={{ fontSize: "31px", margin: "0", lineHeight: "1.1" }}>{p.name}</h1>
                  )}
                  <span style={{ fontSize: "12px", padding: "4px 10px", borderRadius: "6px", border: `1px solid ${p.badgeColor}`, color: p.badgeColor, whiteSpace: "nowrap" }}>{p.badge}</span>
                </div>
                <div style={{ fontSize: "12px", color: "var(--color-neutral-500)" }}>{p.sourceNote}</div>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: "8px", flexShrink: "0" }}>
                {p.openLabel ? (
                  <div className="btn btn-primary" onClick={p.openPage} style={BTN}>{p.openLabel}</div>
                ) : null}
                {p.isCustom ? (
                  p.renaming ? (
                    <>
                      <div className="btn btn-primary" onClick={p.saveRename} style={BTN}>{p.busy ? "Saving…" : "Save name"}</div>
                      <div onClick={p.cancelRename} style={{ ...BTN, border: "1px solid var(--color-divider)", color: "var(--color-neutral-400)" }}>{"Cancel"}</div>
                    </>
                  ) : (
                    <>
                      <div onClick={p.startRename} style={{ ...BTN, border: "1px solid var(--color-divider)", color: "var(--color-neutral-400)" }}>{"Rename"}</div>
                      <div onClick={p.remove} title="Deletes the space in svc-udr; its sessions stay in the list" style={{ ...BTN, border: "1px solid var(--color-divider)", color: "var(--st-risk)" }}>{p.busy ? "Working…" : "Delete space"}</div>
                    </>
                  )
                ) : null}
              </div>
            </div>

            {p.kpis.length ? (
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(150px, 1fr))", gap: "9px", marginTop: "20px" }}>
                {p.kpis.map((k, i) => (
                  <div key={i} style={{ padding: "13px 15px", borderRadius: "11px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)" }}>
                    <div style={{ fontFamily: "ui-monospace,monospace", fontSize: "22px", lineHeight: "1.1", color: "var(--color-text)" }}>{k.value}</div>
                    <div style={{ fontSize: "10.5px", color: "var(--color-neutral-500)", marginTop: "5px", lineHeight: "1.35" }}>{k.label}</div>
                  </div>
                ))}
              </div>
            ) : null}

            <div style={{ display: "flex", alignItems: "center", gap: "11px", padding: "12px 15px", borderRadius: "11px", background: "var(--color-surface)", boxShadow: "var(--shadow-md)", borderBottom: "2px solid var(--color-accent)", marginTop: "22px" }}>
              <i className="ph ph-sparkle" style={{ fontSize: "16px", color: "var(--color-accent)", flexShrink: "0" }}></i>
              <input className="input" value={p.ask} onChange={p.setAsk} onKeyDown={p.askKey} placeholder={p.askPh} style={{ flex: "1", minWidth: "0", background: "transparent", border: "none", outline: "none", fontFamily: "var(--font-body)", fontSize: "14px", color: "var(--color-text)" }} />
              <button type="button" className="btn btn-primary" onClick={p.askRun} style={{ ...BARE, fontSize: "12px", padding: "7px 15px", whiteSpace: "nowrap", flexShrink: "0" }}>{"Ask"}</button>
            </div>

            <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginTop: "26px" }}>
              <div style={{ fontSize: "15px" }}>{"Sessions in this space"}</div>
              <div className="hv6" onClick={p.back} style={{ fontSize: "11.5px", color: "var(--color-accent)", cursor: "pointer" }}>{"All sessions"}</div>
            </div>
            <SessionList groups={p.groups} empty={p.empty} emptyText={p.emptyText} />
          </>
        )}
      </div>
    </div>
  );
}
