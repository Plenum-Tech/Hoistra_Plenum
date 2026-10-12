// InstancePicker — "Where do you want to start?", after sign-in (logic/instances.js)
// Ported from the Hoistra prototype's picker (hoistra-frontend-changes, 11 Oct 2026).
// `vals` is the view model from useHoistra().
import React from 'react';

export default function InstancePicker({ vals }) {
  const onKey = (pick) => (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); pick(); } };
  return (
      <div style={{ position: "fixed", inset: "0", zIndex: "400", background: "var(--color-bg)", color: "var(--color-text)", fontFamily: "var(--font-body)", overflowY: "auto" }}>
        <div style={{ maxWidth: "1080px", margin: "0 auto", padding: "40px 32px 72px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "11px", flexWrap: "wrap" }}>
            <div style={{ width: "30px", height: "30px", borderRadius: "8px", border: "1.5px solid var(--color-accent)", display: "flex", alignItems: "center", justifyContent: "center" }}>
              <div style={{ width: "10px", height: "10px", borderRadius: "50%", background: "var(--color-accent)" }}></div>
            </div>
            <span style={{ fontSize: "20px", fontWeight: "500", letterSpacing: "-0.015em" }}>
              {"Hoistra"}
            </span>
            <span style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: "12px", fontSize: "11.5px", color: "var(--color-neutral-500)" }}>
              <span>{vals.pickerSignedIn}</span>
              {vals.pickerBack ? (
                <button type="button" className="hv16" onClick={vals.pickerBack} style={{ font: "inherit", background: "none", border: "none", padding: "0", cursor: "pointer", color: "var(--color-neutral-500)", display: "inline-flex", alignItems: "center", gap: "5px" }}>
                  <i className="ph ph-arrow-left" style={{ fontSize: "12px" }}></i>{"Back to " + vals.instName}
                </button>
              ) : null}
              <button type="button" className="hv16" onClick={vals.pickerSignOut} style={{ font: "inherit", background: "none", border: "none", padding: "0", cursor: "pointer", color: "var(--color-neutral-500)", display: "inline-flex", alignItems: "center", gap: "5px" }}>
                <i className="ph ph-sign-out" style={{ fontSize: "12px" }}></i>{"Sign out"}
              </button>
            </span>
          </div>
          <div style={{ marginTop: "52px" }}>
            <div style={{ fontSize: "10.5px", letterSpacing: "0.13em", textTransform: "uppercase", color: "var(--color-accent-300)" }}>
              {"Choose an instance"}
            </div>
            <h1 style={{ fontSize: "36px", margin: "8px 0 0", lineHeight: "1.1", letterSpacing: "-0.02em" }}>
              {"Where do you want to start?"}
            </h1>
            <p style={{ fontSize: "14px", color: "var(--color-neutral-400)", margin: "10px 0 0", maxWidth: "64ch", lineHeight: "1.55" }}>
              {"Each instance is set up for a different use case: what your building or estate needs, from connected data and daily operations through to your P&L."}
            </p>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(min(100%,440px),1fr))", gap: "14px", marginTop: "30px" }}>
            {(vals.instCards || []).map((c) => (
              <div key={c.key} role="button" tabIndex={0} className="inst-card" onClick={c.pick} onKeyDown={onKey(c.pick)} aria-label={"Enter " + c.name}
                style={{ display: "flex", flexDirection: "column", padding: "20px 22px", borderRadius: "12px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", border: "1px solid " + (c.current ? "var(--color-accent)" : "var(--color-divider)"), cursor: "pointer", minWidth: "0" }}>
                <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
                  <div style={{ width: "38px", height: "38px", borderRadius: "10px", background: "var(--color-accent-900)", display: "flex", alignItems: "center", justifyContent: "center", flexShrink: "0" }}>
                    <i className={`ph ${c.icon}`} style={{ fontSize: "19px", color: "var(--color-accent)" }}></i>
                  </div>
                  <div style={{ minWidth: "0", flex: "1" }}>
                    <div style={{ display: "flex", alignItems: "baseline", gap: "8px" }}>
                      <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "11px", color: "var(--color-neutral-500)" }}>
                        {c.n}
                      </span>
                      <span style={{ fontSize: "17px" }}>
                        {c.name}
                      </span>
                    </div>
                    <div style={{ fontSize: "11.5px", color: "var(--color-neutral-500)", marginTop: "2px" }}>
                      {c.icp}{c.scope ? " · " + c.scope : ""}
                    </div>
                  </div>
                </div>
                <p style={{ fontSize: "12.5px", color: "var(--color-neutral-300)", lineHeight: "1.55", margin: "14px 0 0", textWrap: "pretty" }}>
                  {c.desc}
                </p>
                <div style={{ display: "flex", alignItems: "center", gap: "10px", marginTop: "auto", paddingTop: "16px" }}>
                  <span style={{ fontSize: "10.5px", color: "var(--color-neutral-500)", flex: "1", minWidth: "0" }}>
                    {c.reports}
                  </span>
                  {c.current ? (
                    <span style={{ fontSize: "10.5px", color: "var(--color-neutral-500)", whiteSpace: "nowrap" }}>
                      {"Current"}
                    </span>
                  ) : null}
                  <span style={{ display: "inline-flex", alignItems: "center", gap: "6px", fontSize: "12px", color: "var(--color-accent)", whiteSpace: "nowrap" }}>
                    {"Enter"}<i className="ph ph-arrow-right" style={{ fontSize: "12px" }}></i>
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
  );
}
