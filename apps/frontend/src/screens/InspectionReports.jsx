// InspectionReports — inspection-report query page (Maintenance → Ask the inspection reports)
// Ported from the Hoistra design reference. `vals` is the view model from useHoistra().
import React from 'react';

export default function InspectionReports({ vals }) {
  return (
      <div style={{ flex: "1", display: "flex", justifyContent: "center", padding: "0 32px 80px" }}>
        <div style={{ width: "100%", maxWidth: "1180px", animation: "fadeUp 0.28s ease both" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "12px", padding: "24px 0 0" }}>
            <div className="hv18" onClick={vals.inspBack} style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "12px", color: "var(--color-neutral-400)", cursor: "pointer" }}>
              <i className="ph ph-arrow-left" style={{ fontSize: "12px" }}></i>
              <span>
                {"Maintenance"}
              </span>
            </div>
            <span style={{ color: "var(--color-neutral-700)" }}>
              {"/"}
            </span>
            <span style={{ fontSize: "12px" }}>
              {"Inspection reports"}
            </span>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: "9px", marginTop: "16px" }}>
            <div style={{ display: "flex", alignItems: "center", gap: "11px", padding: "12px 15px", borderRadius: "11px", background: "var(--color-surface)", boxShadow: "var(--shadow-md)", borderBottom: "2px solid var(--color-accent)" }}>
              <i className="ph ph-file-magnifying-glass" style={{ fontSize: "16px", color: "var(--color-accent)", flexShrink: "0" }}></i>
              <input className="input" value={vals.mxInspQ} onChange={vals.mxInspSet} onKeyDown={vals.mxInspKey} placeholder="Ask across every inspection report…" style={{ flex: "1", minWidth: "0", background: "transparent", border: "none", outline: "none", fontFamily: "var(--font-body)", fontSize: "14px", color: "var(--color-text)" }} />
              <div className="btn btn-primary" onClick={vals.mxInspRun} style={{ fontSize: "12px", padding: "7px 15px", cursor: "pointer", whiteSpace: "nowrap", flexShrink: "0" }}>
                {"Ask"}
              </div>
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: "7px", alignItems: "center" }}>
              <span style={{ fontSize: "10.5px", color: "var(--color-neutral-500)" }}>
                {"Scope · "}{vals.inspScope}
              </span>
              {(vals.mxInspChips || []).map((c, $index) => (
                <React.Fragment key={$index}>
                  <div className="hv4" onClick={c.run} style={{ fontSize: "11.5px", padding: "5px 11px", borderRadius: "7px", border: "1px solid var(--color-divider)", color: "var(--color-neutral-400)", cursor: "pointer" }}>
                    {c.label}
                  </div>
                </React.Fragment>
              ))}
            </div>
          </div>
          <div style={{ marginTop: "24px", padding: "18px 20px", borderRadius: "11px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)" }}>
            <div style={{ fontSize: "10.5px", letterSpacing: "0.13em", textTransform: "uppercase", color: "var(--color-accent-300)" }}>
              {"Reading · "}{vals.inspCount}
            </div>
            <h2 style={{ fontSize: "22px", margin: "7px 0 0", lineHeight: "1.2" }}>
              {vals.inspTitle}
            </h2>
            <p style={{ fontSize: "13px", color: "var(--color-neutral-300)", margin: "9px 0 0", maxWidth: "80ch", lineHeight: "1.55" }}>
              {vals.inspTake}
            </p>
            <div style={{ display: "flex", flexDirection: "column", gap: "6px", marginTop: "14px" }}>
              {(vals.inspRowsA || []).map((r, $index) => (
                <React.Fragment key={$index}>
                  <div style={{ display: "flex", alignItems: "flex-start", gap: "9px", fontSize: "12px", lineHeight: "1.5", padding: "8px 10px", borderRadius: "7px", background: "var(--color-bg)" }}>
                    <span style={{ width: "5px", height: "5px", borderRadius: "50%", background: "var(--color-accent)", flexShrink: "0", marginTop: "7px" }}></span>
                    <span>
                      {r.t}
                    </span>
                  </div>
                </React.Fragment>
              ))}
            </div>
          </div>
          <div style={{ fontSize: "11px", letterSpacing: "0.11em", textTransform: "uppercase", color: "var(--color-neutral-500)", margin: "30px 0 12px" }}>
            {"Every report · worst condition first"}
          </div>
          <div style={{ borderRadius: "10px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", overflow: "hidden" }}>
            {(vals.inspRows || []).map((x, $index) => (
              <React.Fragment key={$index}>
                <div style={{ display: "flex", alignItems: "flex-start", gap: "12px 18px", flexWrap: "wrap", padding: "11px 14px", borderBottom: "1px solid var(--color-divider)" }}>
                  <div style={{ flex: "0 0 auto", display: "flex", flexDirection: "column", alignItems: "center", gap: "2px", width: "44px" }}>
                    <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "18px", lineHeight: "1", color: x.gradeColor }}>
                      {x.grade}
                    </span>
                    <span style={{ fontSize: "9px", letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                      {"grade"}
                    </span>
                  </div>
                  <div style={{ flex: "1 1 300px", minWidth: "0" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
                      <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "11px", color: "var(--color-neutral-400)" }}>
                        {x.wo}
                      </span>
                      <span style={{ fontSize: "12.5px" }}>
                        {x.asset}
                      </span>
                      <span style={{ fontSize: "11px", color: "var(--color-neutral-500)" }}>
                        {x.b}{" · "}{x.date}{" · "}{x.vendor}{" · "}{x.type}
                      </span>
                      <span style={{ fontSize: "10px", padding: "2px 7px", borderRadius: "5px", background: "var(--st-warn-bg)", color: "var(--st-warn)", display: x.anomShow }}>
                        {"corroborates a live anomaly"}
                      </span>
                    </div>
                    <div style={{ fontSize: "11.5px", color: "var(--color-neutral-300)", marginTop: "5px", lineHeight: "1.5" }}>
                      {x.findings}
                    </div>
                    <div style={{ fontSize: "11.5px", marginTop: "5px", lineHeight: "1.5" }}>
                      <span style={{ color: "var(--color-neutral-500)" }}>
                        {"Recommendation · "}
                      </span>
                      {x.rec}{" "}
                      <span style={{ fontSize: "10.5px", color: x.recColor }}>
                        {x.recState}
                      </span>
                    </div>
                    <div style={{ fontSize: "11px", marginTop: "4px", padding: "3px 8px", borderRadius: "5px", background: "var(--marker-tint)", display: x.warrShow }}>
                      {x.warranty}
                    </div>
                  </div>
                </div>
              </React.Fragment>
            ))}
          </div>
        </div>
      </div>
  );
}
