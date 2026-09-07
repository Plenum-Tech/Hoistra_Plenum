// CustomReport — saved custom report
// Ported from the Hoistra prototype template. `vals` is the view model from useHoistra().
import React from 'react';

export default function CustomReport({ vals }) {
  return (
      <div style={{ flex: "1", display: "flex", justifyContent: "center", padding: "0 32px 80px" }}>
        <div style={{ width: "100%", maxWidth: "1180px", animation: "fadeUp 0.28s ease both" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "12px", padding: "24px 0 0" }}>
            <div className="hv6" onClick={vals.goHome} style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "12px", color: "var(--color-neutral-400)", cursor: "pointer" }}>
              <i className="ph ph-arrow-left" style={{ fontSize: "12px" }}></i>
              <span>
                {"Home"}
              </span>
            </div>
            <span style={{ fontSize: "12px", color: "var(--color-neutral-600)" }}>
              {"/"}
            </span>
            <span style={{ fontSize: "12px", color: "var(--color-neutral-500)" }}>
              {"Reports"}
            </span>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: "9px", marginTop: "16px" }}>
            <div style={{ display: "flex", alignItems: "center", gap: "11px", padding: "12px 15px", borderRadius: "11px", background: "var(--color-surface)", boxShadow: "var(--shadow-md)", borderBottom: "2px solid var(--color-accent)" }}>
              <i className="ph ph-sparkle" style={{ fontSize: "16px", color: "var(--color-accent)", flexShrink: "0" }}></i>
              <input className="input" value={vals.pq} onChange={vals.pqSet} onKeyDown={vals.pqKey} placeholder={vals.abPh} style={{ flex: "1", minWidth: "0", background: "transparent", border: "none", outline: "none", fontFamily: "var(--font-body)", fontSize: "14px", color: "var(--color-text)" }} />
              <div className="btn btn-primary" onClick={vals.pqRun} style={{ fontSize: "12px", padding: "7px 15px", cursor: "pointer", whiteSpace: "nowrap", flexShrink: "0" }}>
                {"Ask"}
              </div>
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: "7px", alignItems: "center" }}>
              <span style={{ fontSize: "10.5px", color: "var(--color-neutral-500)" }}>
                {"Ask this page"}
              </span>
              {(vals.abChips || []).map((a, $index) => (
                <React.Fragment key={$index}>
                  <div className="hv4" onClick={a.run} style={{ fontSize: "11.5px", padding: "5px 11px", borderRadius: "7px", border: "1px solid var(--color-divider)", color: "var(--color-neutral-400)", cursor: "pointer" }}>
                    {a.label}
                  </div>
                </React.Fragment>
              ))}
            </div>
          </div>
          <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", gap: "24px", marginTop: "18px" }}>
            <div style={{ display: "flex", flexDirection: "column", gap: "7px" }}>
              <div style={{ fontSize: "10.5px", letterSpacing: "0.13em", textTransform: "uppercase", color: "var(--color-accent)" }}>
                {"Custom report · dynamic"}
              </div>
              <h1 style={{ fontSize: "31px", margin: "0", lineHeight: "1.1" }}>
                {vals.report.title}
              </h1>
              <div style={{ fontSize: "12.5px", color: "var(--color-neutral-400)", maxWidth: "76ch", lineHeight: "1.5" }}>
                {vals.report.kicker}
              </div>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: "9px", flexShrink: "0" }}>
              <span style={{ fontSize: "11.5px", color: "var(--color-neutral-500)" }}>
                {vals.report.lastRun}
              </span>
              <div className="btn btn-primary" onClick={vals.exportReport} style={{ fontSize: "12px", padding: "7px 13px", cursor: "pointer" }}>
                {"Export"}
              </div>
            </div>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "9px", marginTop: "11px", fontSize: "11px", color: "var(--color-neutral-500)" }}>
            <i className="ph ph-clock-counter-clockwise" style={{ fontSize: "12px" }}></i>
            <span>
              {vals.report.meta}
            </span>
          </div>
          {vals.reportPending ? (
            <>
              <div style={{ marginTop: "34px", padding: "34px", borderRadius: "12px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", display: "flex", flexDirection: "column", gap: "11px", alignItems: "flex-start" }}>
                <i className="ph ph-hourglass-medium" style={{ fontSize: "21px", color: "var(--color-accent)" }}></i>
                <div style={{ fontSize: "15px" }}>
                  {"First run scheduled"}
                </div>
                <div style={{ fontSize: "12.5px", color: "var(--color-neutral-400)", maxWidth: "62ch", lineHeight: "1.55" }}>
                  {"The session's query has been pinned. The orchestrator runs it against the current Hoist Graph on the next 30-minute cycle and this page fills with the result. Re-run now to build it immediately."}
                </div>
              </div>
            </>
          ) : null}
          {vals.reportReady ? (
            <>
              <div style={{ fontSize: "13.5px", lineHeight: "1.6", marginTop: "24px", maxWidth: "88ch" }}>
                {vals.report.summary}
              </div>
              <div style={{ marginTop: "18px", borderRadius: "12px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", overflow: "hidden" }}>
                <div style={{ display: "grid", gridTemplateColumns: "1.5fr repeat(4, 0.85fr) 0.8fr", gap: "12px", padding: "11px 18px", borderBottom: "1px solid var(--color-divider)", fontSize: "10.5px", letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                  <span>
                    {"Building"}
                  </span>
                  <span>
                    {"Outlier"}
                  </span>
                  <span>
                    {"Anomaly"}
                  </span>
                  <span>
                    {"Deficit"}
                  </span>
                  <span>
                    {"EUI vs benchmark"}
                  </span>
                  <span>
                    {"At risk"}
                  </span>
                </div>
                {(vals.riskBuildings || []).map((b, $index) => (
                  <React.Fragment key={$index}>
                    <div className="hv2" onClick={b.click} style={{ display: "grid", gridTemplateColumns: "1.5fr repeat(4, 0.85fr) 0.8fr", gap: "12px", padding: "11px 18px", borderBottom: "1px solid var(--color-divider)", fontSize: "12.5px", alignItems: "center", cursor: "pointer" }}>
                      <span>
                        {b.name}
                      </span>
                      <span style={{ fontVariantNumeric: "tabular-nums", color: b.outColor }}>
                        {b.out}
                      </span>
                      <span style={{ fontVariantNumeric: "tabular-nums", color: b.anomColor }}>
                        {b.anom}
                      </span>
                      <span style={{ fontVariantNumeric: "tabular-nums", color: b.defColor }}>
                        {b.def}
                      </span>
                      <span style={{ fontVariantNumeric: "tabular-nums", color: b.euiColor }}>
                        {b.eui}
                      </span>
                      <span style={{ color: b.riskColor }}>
                        {b.risk}
                      </span>
                    </div>
                  </React.Fragment>
                ))}
              </div>
            </>
          ) : null}
        </div>
      </div>
  );
}
