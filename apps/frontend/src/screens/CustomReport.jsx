// CustomReport — a saved custom report: a session's question pinned and re-run on a cadence.
//
// The page is the report's latest refresh — the orchestrator's answer to the pinned question,
// rendered the way the conversation page renders it (structured compliance answer or
// markdown) — with when it ran, the tools behind it, and the previous refreshes to switch to.
// The state is explicit: pending (never run), running, ready, or failed. `vals` is the view
// model from useHoistra(); the page reads vals.report and the report* flags.
import React from 'react';
import Markdown from '../components/shell/Markdown.jsx';
import ComplianceAnswer from '../components/shell/ComplianceAnswer.jsx';

const BTN = { fontSize: "12px", padding: "7px 13px", borderRadius: "7px", cursor: "pointer", whiteSpace: "nowrap" };

export default function CustomReport({ vals }) {
  const r = vals.report || {};
  return (
      <div style={{ flex: "1", display: "flex", justifyContent: "center", padding: "0 32px 80px" }}>
        <div style={{ width: "100%", maxWidth: "1180px", animation: "fadeUp 0.28s ease both" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "12px", padding: "24px 0 0" }}>
            <div className="hv6" onClick={vals.goHome} style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "12px", color: "var(--color-neutral-400)", cursor: "pointer" }}>
              <i className="ph ph-arrow-left" style={{ fontSize: "12px" }}></i>
              <span>{"Home"}</span>
            </div>
            <span style={{ fontSize: "12px", color: "var(--color-neutral-600)" }}>{"/"}</span>
            <span style={{ fontSize: "12px", color: "var(--color-neutral-500)" }}>{"Reports"}</span>
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
              <span style={{ fontSize: "10.5px", color: "var(--color-neutral-500)" }}>{"Ask this page"}</span>
              {(vals.abChips || []).map((a, $index) => (
                <React.Fragment key={$index}>
                  <div className="hv4" onClick={a.run} style={{ fontSize: "11.5px", padding: "5px 11px", borderRadius: "7px", border: "1px solid var(--color-divider)", color: "var(--color-neutral-400)", cursor: "pointer" }}>
                    {a.label}
                  </div>
                </React.Fragment>
              ))}
            </div>
          </div>

          <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", gap: "24px", marginTop: "18px", flexWrap: "wrap" }}>
            <div style={{ display: "flex", flexDirection: "column", gap: "7px", minWidth: "0" }}>
              <div style={{ fontSize: "10.5px", letterSpacing: "0.13em", textTransform: "uppercase", color: "var(--color-accent)" }}>{"Custom report · dynamic"}</div>
              <h1 style={{ fontSize: "31px", margin: "0", lineHeight: "1.1" }}>{r.title}</h1>
              <div style={{ fontSize: "12.5px", color: "var(--color-neutral-400)", maxWidth: "76ch", lineHeight: "1.5" }}>{r.kicker}</div>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: "9px", flexShrink: "0" }}>
              <span style={{ fontSize: "11.5px", color: "var(--color-neutral-500)" }}>{r.lastRun}</span>
              <div className="btn btn-primary" onClick={vals.reportRunning ? undefined : vals.runReport} style={{ ...BTN, opacity: vals.reportRunning ? "0.6" : "1", cursor: vals.reportRunning ? "default" : "pointer" }}>
                {vals.reportRunning ? "Refreshing…" : "Run now"}
              </div>
              <div onClick={vals.exportReport} title="Save this refresh as a markdown file" style={{ ...BTN, border: "1px solid var(--color-divider)", color: "var(--color-neutral-400)" }}>{"Export"}</div>
              <div onClick={vals.deleteReport} title="Remove this report from this browser" style={{ ...BTN, border: "1px solid var(--color-divider)", color: "var(--st-risk)" }}>{"Delete"}</div>
            </div>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "9px", marginTop: "11px", fontSize: "11px", color: "var(--color-neutral-500)" }}>
            <i className="ph ph-clock-counter-clockwise" style={{ fontSize: "12px" }}></i>
            <span>{r.meta}</span>
          </div>

          {vals.reportRunning ? (
            <div style={{ marginTop: "24px", padding: "14px 18px", borderRadius: "10px", background: "var(--color-accent-900)", display: "flex", alignItems: "center", gap: "10px", fontSize: "12.5px", color: "var(--color-text)" }}>
              <i className="ph ph-circle-notch" style={{ fontSize: "15px", color: "var(--color-accent)", animation: "spin 1s linear infinite" }}></i>
              <span>{"The orchestrator is re-reading the graph for this question. The page fills in when it answers; the last refresh stays below until then."}</span>
            </div>
          ) : null}

          {vals.reportPending ? (
            <div style={{ marginTop: "34px", padding: "34px", borderRadius: "12px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", display: "flex", flexDirection: "column", gap: "11px", alignItems: "flex-start" }}>
              <i className="ph ph-hourglass-medium" style={{ fontSize: "21px", color: "var(--color-accent)" }}></i>
              <div style={{ fontSize: "15px" }}>{"First refresh scheduled"}</div>
              <div style={{ fontSize: "12.5px", color: "var(--color-neutral-400)", maxWidth: "62ch", lineHeight: "1.55" }}>
                {"The session's question is pinned. The orchestrator runs it against the current Hoist Graph at the next due time while Hoistra is open, and this page fills with the result. Run now builds it immediately."}
              </div>
            </div>
          ) : null}

          {vals.reportHasRuns ? (
            <div style={{ display: "flex", flexWrap: "wrap", gap: "6px", alignItems: "center", marginTop: "22px" }}>
              <span style={{ fontSize: "10.5px", letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--color-neutral-500)", marginRight: "3px" }}>{"Refreshes"}</span>
              {(r.runs || []).map((x, i) => (
                <div key={i} className="hv4" onClick={x.pick} style={{ fontSize: "11.5px", padding: "5px 11px", borderRadius: "7px", border: `1px solid ${x.active ? "var(--color-accent)" : "var(--color-divider)"}`, color: x.active ? "var(--color-accent)" : "var(--color-neutral-400)", background: x.active ? "var(--color-accent-900)" : "transparent", cursor: "pointer" }}>
                  {x.label}
                </div>
              ))}
            </div>
          ) : null}

          {vals.reportFailed ? (
            <div style={{ marginTop: "22px", padding: "18px 20px", borderRadius: "12px", background: "var(--st-risk-bg)", color: "var(--st-risk)", display: "grid", gridTemplateColumns: "16px minmax(0,1fr)", gap: "10px", fontSize: "13px", lineHeight: "1.55" }}>
              <i className="ph ph-warning-circle" style={{ fontSize: "15px", marginTop: "2px" }}></i>
              <div>
                <div>{"This refresh did not complete."}</div>
                <div style={{ marginTop: "4px", fontSize: "12px", opacity: "0.9" }}>{vals.reportFailedText}</div>
                <div style={{ marginTop: "8px", fontSize: "11.5px", color: "var(--color-neutral-400)" }}>{"It stays on its schedule; Run now tries again."}</div>
              </div>
            </div>
          ) : null}

          {vals.reportReady ? (
            <>
              <div style={{ display: "flex", alignItems: "baseline", gap: "10px", marginTop: "22px" }}>
                <span style={{ fontSize: "10px", letterSpacing: "0.12em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>{"Refreshed " + r.runAt}</span>
                {r.runMs ? <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "9.5px", color: "var(--color-neutral-500)" }}>{r.runMs}</span> : null}
              </div>
              <div style={{ marginTop: "8px", padding: "18px 20px", borderRadius: "12px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", fontSize: "13.5px", lineHeight: "1.6", textWrap: "pretty" }}>
                {r.rich
                  ? <ComplianceAnswer rich={r.rich} open={false} onToggle={() => {}} />
                  : <Markdown text={r.answer} />}
                {r.tools ? (
                  <div style={{ fontFamily: "ui-monospace,monospace", fontSize: "10px", color: "var(--color-neutral-500)", marginTop: "14px", paddingTop: "10px", borderTop: "1px solid var(--color-divider)" }}>{r.tools}</div>
                ) : null}
              </div>
            </>
          ) : null}
        </div>
      </div>
  );
}
