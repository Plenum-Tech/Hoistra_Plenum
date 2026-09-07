// DetailDrawer — record drawer
// Ported from the Hoistra prototype template. `vals` is the view model from useHoistra().
import React from 'react';

export default function DetailDrawer({ vals }) {
  return (
    <>
      <div onClick={vals.closeDetail} style={{ position: "fixed", inset: "0", background: "var(--scrim)", zIndex: "70" }}></div>
      <div style={{ position: "fixed", top: "0", right: "0", bottom: "0", width: "620px", background: "var(--color-bg)", boxShadow: "var(--shadow-lg)", zIndex: "71", display: "flex", flexDirection: "column", animation: "fadeUp 0.22s ease both" }}>
        <div style={{ display: "flex", alignItems: "flex-start", gap: "12px", padding: "18px 22px", borderBottom: "1px solid var(--color-divider)", flexShrink: "0" }}>
          <i className={`ph ${vals.detail.icon}`} style={{ fontSize: "17px", marginTop: "2px", color: vals.detail.color }}></i>
          <div style={{ flex: "1" }}>
            <div style={{ fontSize: "10.5px", letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
              {vals.detail.module}{" · activity log entry"}
            </div>
            <div style={{ fontSize: "15px", lineHeight: "1.35", marginTop: "5px" }}>
              {vals.detail.title}
            </div>
            <div style={{ fontSize: "11.5px", color: "var(--color-neutral-500)", marginTop: "5px" }}>
              {vals.detail.meta}
            </div>
          </div>
          <i className="ph ph-x" onClick={vals.closeDetail} style={{ fontSize: "16px", color: "var(--color-neutral-500)", cursor: "pointer" }}></i>
        </div>
        <div style={{ flex: "1", overflowY: "auto", padding: "18px 22px 40px" }}>
          <div style={{ fontSize: "11px", letterSpacing: "0.11em", textTransform: "uppercase", color: "var(--color-neutral-500)", marginBottom: "9px" }}>
            {"Activity summary"}
          </div>
          <p style={{ fontSize: "13.5px", lineHeight: "1.6", color: "var(--color-neutral-200)", margin: "0" }}>
            {vals.detail.body}
          </p>
          <div style={{ fontSize: "11px", letterSpacing: "0.11em", textTransform: "uppercase", color: "var(--color-neutral-500)", margin: "26px 0 9px" }}>
            {"Inline actions — editable fields"}
          </div>
          <div style={{ padding: "3px 14px 6px", borderRadius: "10px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)" }}>
            {(vals.detailFields || []).map((f, $index) => (
              <React.Fragment key={$index}>
                <div style={{ display: "flex", alignItems: "center", gap: "12px", padding: "10px 0", borderBottom: "1px solid var(--color-divider)" }}>
                  <span style={{ fontSize: "11.5px", color: "var(--color-neutral-500)", width: "160px", flexShrink: "0" }}>
                    {f.l}
                  </span>
                  <span onClick={f.edit} style={{ fontSize: "12.5px", flex: "1", cursor: f.cursor, color: f.fg, borderBottom: `1px ${f.underline} ${f.fg}`, paddingBottom: "1px" }}>
                    {f.v}
                  </span>
                  <i className={`ph ${f.icon}`} style={{ fontSize: "12px", color: f.fg, opacity: f.iconOp }}></i>
                </div>
              </React.Fragment>
            ))}
            <div style={{ fontSize: "10.5px", color: "var(--color-neutral-600)", padding: "10px 0 4px", lineHeight: "1.5" }}>
              {"Fields in accent are changeable. Changing the contractor re-runs the accreditation check. Every change is logged with timestamp and actor."}
            </div>
          </div>
          <div style={{ fontSize: "11px", letterSpacing: "0.11em", textTransform: "uppercase", color: "var(--color-neutral-500)", margin: "26px 0 9px" }}>
            {"Chain of thought"}
          </div>
          <div style={{ padding: "3px 14px 10px", borderRadius: "10px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)" }}>
            {(vals.detail.chain || []).map((c, $index) => (
              <React.Fragment key={$index}>
                <div style={{ display: "flex", gap: "12px", padding: "12px 0", borderBottom: "1px solid var(--color-divider)" }}>
                  <div style={{ width: "78px", flexShrink: "0", fontSize: "11px", color: "var(--color-accent-300)" }}>
                    {c.a}
                  </div>
                  <div style={{ flex: "1", fontSize: "11.5px", lineHeight: "1.55", color: "var(--color-neutral-400)" }}>
                    {c.t}
                  </div>
                </div>
              </React.Fragment>
            ))}
          </div>
          <div style={{ fontSize: "11px", letterSpacing: "0.11em", textTransform: "uppercase", color: "var(--color-neutral-500)", margin: "26px 0 9px" }}>
            {"Refinement"}
          </div>
          <div className="hv25" onClick={vals.acceptRefinement} style={{ display: "flex", gap: "11px", padding: "13px 14px", borderRadius: "10px", border: "1px solid var(--color-accent-700)", cursor: "pointer" }}>
            <i className="ph ph-lightbulb" style={{ fontSize: "14px", color: "var(--color-accent)", marginTop: "1px" }}></i>
            <div style={{ flex: "1", fontSize: "12.5px", lineHeight: "1.55", color: "var(--color-neutral-200)" }}>
              {vals.detail.refinement}
            </div>
          </div>
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: "9px", padding: "15px 22px", borderTop: "1px solid var(--color-divider)", flexShrink: "0", background: "var(--color-bg)" }}>
          {(vals.detailActions || []).map((a, $index) => (
            <React.Fragment key={$index}>
              <div className={`btn ${a.cls}`} onClick={a.click} style={{ fontSize: "12.5px", padding: "8px 15px", cursor: "pointer" }}>
                {a.label}
              </div>
            </React.Fragment>
          ))}
        </div>
      </div>
    </>
  );
}
