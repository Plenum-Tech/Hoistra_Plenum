// Toast — bottom-centre confirmation
// Ported from the Hoistra prototype template. `vals` is the view model from useHoistra().
import React from 'react';

export default function Toast({ vals }) {
  return (
      <div style={{ position: "fixed", bottom: "24px", left: "50%", transform: "translateX(-50%)", zIndex: "90", display: "flex", alignItems: "center", gap: "11px", padding: "12px 18px", borderRadius: "9px", background: "var(--color-surface)", boxShadow: "var(--shadow-lg)", animation: "fadeUp 0.2s ease both" }}>
        <i className="ph ph-check-circle" style={{ fontSize: "15px", color: "var(--st-ok)" }}></i>
        <span style={{ fontSize: "12.5px" }}>
          {vals.toast}
        </span>
      </div>
  );
}
