// SessionList — sessions grouped by day, as the Sessions page and a space page list them.
//
// A row is one conversation with the orchestrator (or one orchestrator task): its first
// question, the engine that answered, the page it was asked from, how many questions it holds,
// and where it is filed. Clicking reopens it; the folder select files it in a saved space; the
// bin deletes it from this browser. `groups` comes from the view model (sessionsPage.groups /
// spacePage.groups); this component holds no state.
import React from 'react';

const BARE = { font: "inherit", background: "transparent", border: "none", padding: "0", margin: "0", cursor: "pointer", color: "inherit" };

export default function SessionList({ groups, empty, emptyText }) {
  if (empty) {
    return (
      <div style={{ marginTop: "18px", padding: "28px", borderRadius: "12px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", fontSize: "13px", lineHeight: "1.55", color: "var(--color-neutral-400)", maxWidth: "62ch" }}>
        {emptyText}
      </div>
    );
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "18px", marginTop: "18px" }}>
      {(groups || []).map((g) => (
        <div key={g.day}>
          <div style={{ fontSize: "10.5px", letterSpacing: "0.11em", textTransform: "uppercase", color: "var(--color-neutral-500)", padding: "0 2px 8px" }}>
            {g.day}
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
            {g.rows.map((r) => (
              <div key={r.id} className="hv1" onClick={r.open} style={{ display: "grid", gridTemplateColumns: "26px minmax(0,1fr) auto", gap: "12px", alignItems: "center", padding: "11px 14px", borderRadius: "10px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", border: r.active ? "1px solid var(--color-accent)" : "1px solid transparent", cursor: "pointer" }}>
                <div style={{ width: "26px", height: "26px", borderRadius: "7px", background: "var(--color-accent-900)", display: "flex", alignItems: "center", justifyContent: "center" }}>
                  <i className={`ph ${r.icon}`} style={{ fontSize: "13px", color: "var(--color-accent)" }}></i>
                </div>
                <div style={{ minWidth: "0" }}>
                  <div style={{ fontSize: "13px", lineHeight: "1.4", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.title}</div>
                  <div style={{ fontSize: "10.5px", color: "var(--color-neutral-500)", marginTop: "3px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {[r.domain, r.page ? "from " + r.page : "", r.turns, r.spaceName ? "filed in " + r.spaceName : ""].filter(Boolean).join(" · ")}
                  </div>
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: "10px", flexShrink: "0" }}>
                  <span style={{ fontSize: "10.5px", color: "var(--color-neutral-500)", whiteSpace: "nowrap" }}>{r.when}</span>
                  {r.canFile ? (
                    <select value={r.fileValue} onChange={r.fileTo} onClick={(e) => e.stopPropagation()} title="File this session in a saved space" style={{ fontFamily: "var(--font-body)", fontSize: "10.5px", padding: "3px 6px", borderRadius: "6px", border: "1px solid var(--color-divider)", background: "var(--color-bg)", color: "var(--color-neutral-400)", maxWidth: "140px" }}>
                      {r.fileOptions.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                    </select>
                  ) : null}
                  <button type="button" className="hv21" onClick={r.remove} title="Delete this session from this browser" style={{ ...BARE, display: "flex", color: "var(--color-neutral-500)", opacity: "0.7" }}>
                    <i className="ph ph-trash" style={{ fontSize: "13px" }}></i>
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
