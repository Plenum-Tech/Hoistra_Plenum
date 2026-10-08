// Answer — answer view
// Ported from the Hoistra prototype template. `vals` is the view model from useHoistra().
import React from 'react';

export default function Answer({ vals }) {
  return (
      <div style={{ flex: "1", display: "flex", justifyContent: "center", padding: "0 32px 80px" }}>
        <div style={{ width: "100%", maxWidth: "1080px", animation: "fadeUp 0.28s ease both" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "12px", padding: "26px 0 0" }}>
            <div className="hv18" onClick={vals.goHome} style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "12px", color: "var(--color-neutral-400)", cursor: "pointer" }}>
              <i className="ph ph-arrow-left" style={{ fontSize: "12px" }}></i>
              <span>
                {"New question"}
              </span>
            </div>
          </div>
          <div style={{ marginTop: "18px", padding: "14px 17px", borderRadius: "10px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", display: "flex", alignItems: "center", gap: "11px" }}>
            <i className="ph ph-sparkle" style={{ fontSize: "15px", color: "var(--color-accent)" }}></i>
            <span style={{ fontSize: "14px", color: "var(--color-neutral-200)" }}>
              {vals.askedQuery}
            </span>
          </div>
          <div style={{ fontSize: "10.5px", letterSpacing: "0.13em", color: "var(--color-accent-300)", margin: "26px 0 10px", fontFamily: "ui-monospace,monospace" }}>
            {vals.answer.scope}
          </div>
          <h2 style={{ fontSize: "29px", margin: "0", lineHeight: "1.15", maxWidth: "34ch" }}>
            {vals.answer.title}
          </h2>
          <p style={{ fontSize: "15px", lineHeight: "1.65", color: "var(--color-neutral-200)", margin: "16px 0 0", maxWidth: "82ch", textWrap: "pretty" }}>
            {vals.answer.takeaway}
          </p>
          <div style={{ display: "flex", gap: "10px", marginTop: "14px", padding: "11px 14px", borderRadius: "9px", background: "var(--color-neutral-900)", maxWidth: "82ch" }}>
            <i className="ph ph-info" style={{ fontSize: "13px", color: "var(--color-neutral-500)", marginTop: "2px" }}></i>
            <p style={{ fontSize: "12px", lineHeight: "1.55", color: "var(--color-neutral-400)", margin: "0" }}>
              {vals.answer.caveat}
            </p>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(150px,1fr))", gap: "11px", marginTop: "30px" }}>
            {(vals.answerMetrics || []).map((m, $index) => (
              <React.Fragment key={$index}>
                <div style={{ padding: "14px", borderRadius: "10px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)" }}>
                  <div style={{ fontSize: "11px", color: "var(--color-neutral-500)" }}>
                    {m.l}
                  </div>
                  <div style={{ fontSize: "24px", marginTop: "6px", lineHeight: "1", color: m.color }}>
                    {m.v}
                  </div>
                  <div style={{ fontSize: "11px", color: "var(--color-neutral-300)", marginTop: "5px", lineHeight: "1.4" }}>
                    {m.s}
                  </div>
                </div>
              </React.Fragment>
            ))}
          </div>
          <div style={{ display: "grid", gridTemplateColumns: vals.answerCols, gap: "26px", marginTop: "34px", alignItems: "start" }}>
            <div>
              <div style={{ fontSize: "11px", letterSpacing: "0.11em", textTransform: "uppercase", color: "var(--color-neutral-500)", marginBottom: "11px" }}>
                {"The evidence"}
              </div>
              <table className="table" style={{ width: "100%", fontSize: "12.5px" }}>
                <thead>
                  <tr>
                    {(vals.answer.rowHead || []).map((h, $index) => (
                      <th key={$index} style={{ textAlign: "left", fontSize: "10.5px", letterSpacing: "0.07em", textTransform: "uppercase", color: "var(--color-neutral-500)", fontWeight: "400", padding: "8px 10px" }}>
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {(vals.answerRows || []).map((r, $index) => (
                    <tr key={$index} className="hv19" onClick={r.click} style={{ cursor: "pointer" }}>
                      <td style={{ padding: "11px 10px" }}>
                        {r.c0}
                      </td>
                      <td style={{ padding: "11px 10px", color: "var(--color-neutral-300)" }}>
                        {r.c1}
                      </td>
                      <td style={{ padding: "11px 10px", fontFamily: "ui-monospace,monospace", color: r.color }}>
                        {r.c2}
                      </td>
                      <td style={{ padding: "11px 10px", color: "var(--color-neutral-400)" }}>
                        {r.c3}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <div style={{ display: "flex", flexWrap: "wrap", gap: "9px", marginTop: "20px" }}>
                {(vals.answerActions || []).map((a, $index) => (
                  <React.Fragment key={$index}>
                    <div className={`btn ${a.cls}`} onClick={a.click} style={{ fontSize: "12.5px", padding: "8px 15px", cursor: "pointer" }}>
                      {a.label}
                    </div>
                  </React.Fragment>
                ))}
              </div>
            </div>
            <div>
              <div className="hv18" onClick={vals.toggleChain} style={{ display: "flex", alignItems: "center", gap: "8px", fontSize: "11px", letterSpacing: "0.11em", textTransform: "uppercase", color: "var(--color-neutral-500)", marginBottom: "11px", cursor: "pointer" }}>
                <i className={`ph ${vals.chainIcon}`} style={{ fontSize: "12px" }}></i>
                <span>
                  {"Chain of thought"}
                </span>
              </div>
              {vals.chainOpen ? (
                <>
                  <div style={{ display: "flex", flexDirection: "column", gap: "0", padding: "2px 15px 14px", borderRadius: "10px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)" }}>
                    {(vals.answer.chain || []).map((c, $index) => (
                      <React.Fragment key={$index}>
                        <div style={{ display: "flex", gap: "12px", padding: "13px 0", borderBottom: "1px solid var(--color-divider)" }}>
                          <div style={{ width: "74px", flexShrink: "0", fontSize: "11px", color: "var(--color-accent-300)" }}>
                            {c.a}
                          </div>
                          <div style={{ flex: "1", fontSize: "11.5px", lineHeight: "1.55", color: "var(--color-neutral-400)" }}>
                            {c.t}
                          </div>
                        </div>
                      </React.Fragment>
                    ))}
                    <div style={{ fontSize: "10.5px", color: "var(--color-neutral-600)", paddingTop: "11px", lineHeight: "1.5" }}>
                      {"Orchestrator → Planner → Worker → Quality. The Quality agent fires only on actionable output; on read-only answers you are the gate."}
                    </div>
                  </div>
                </>
              ) : null}
              <div style={{ fontSize: "11px", letterSpacing: "0.11em", textTransform: "uppercase", color: "var(--color-neutral-500)", margin: "26px 0 11px" }}>
                {"Refine"}
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                {(vals.refinements || []).map((r, $index) => (
                  <React.Fragment key={$index}>
                    <div className="hv17" onClick={r.run} style={{ fontSize: "12px", padding: "10px 12px", borderRadius: "9px", border: "1px solid var(--color-divider)", color: "var(--color-neutral-300)", cursor: "pointer", lineHeight: "1.45" }}>
                      {r.label}
                    </div>
                  </React.Fragment>
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>
  );
}
