// CommandPalette — command palette
// Ported from the Hoistra prototype template. `vals` is the view model from useHoistra().
import React from 'react';

export default function CommandPalette({ vals }) {
  return (
      <div onClick={vals.closePalette} style={{ position: "fixed", inset: "0", background: "var(--scrim)", zIndex: "80", display: "flex", justifyContent: "center", paddingTop: "14vh" }}>
        <div onClick={vals.stop} style={{ width: "640px", height: "fit-content", background: "var(--color-surface)", borderRadius: "12px", boxShadow: "var(--shadow-lg)", overflow: "hidden", animation: "fadeUp 0.18s ease both" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "11px", padding: "15px 17px", borderBottom: "1px solid var(--color-divider)" }}>
            <i className="ph ph-sparkle" style={{ fontSize: "16px", color: "var(--color-accent)" }}></i>
            <input value={vals.query} onChange={vals.setQuery} onKeyDown={vals.onKey} placeholder="Ask the portfolio anything…" autoFocus={true} style={{ flex: "1", background: "transparent", border: "none", fontSize: "15px", color: "var(--color-text)", outline: "none", fontFamily: "var(--font-body)" }} />
          </div>
          <div style={{ padding: "9px 9px 11px" }}>
            {(vals.pinned || []).map((p, $index) => (
              <React.Fragment key={$index}>
                <div className="hv19" onClick={p.run} style={{ display: "flex", alignItems: "center", gap: "10px", padding: "10px 11px", borderRadius: "8px", cursor: "pointer", fontSize: "13px", color: "var(--color-neutral-200)" }}>
                  <i className="ph ph-arrow-elbow-down-right" style={{ fontSize: "12px", color: "var(--color-neutral-600)" }}></i>
                  <span style={{ flex: "1" }}>
                    {p.label}
                  </span>
                  <span style={{ fontSize: "10.5px", color: "var(--color-neutral-600)" }}>
                    {p.tag}
                  </span>
                </div>
              </React.Fragment>
            ))}
          </div>
        </div>
      </div>
  );
}
