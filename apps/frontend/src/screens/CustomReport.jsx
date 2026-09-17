// CustomReport — report cards: a session's question pinned and re-run on a cadence by the
// server (svc-operations-intelligence's /api/reports — see logic/reports.js).
//
// Two modes, one screen: no card picked (vals.reportGridMode) shows every card as a small
// tile — "cards, easy to view" — with a checkbox per tile so several can be selected and
// deleted together; a picked card (vals.reportKey) shows that card's latest refresh full
// size, rendered the way the conversation page renders an answer (structured compliance
// answer or markdown), with when it ran, the tools behind it, and previous refreshes to
// switch to. The state is explicit: pending (never run), running, ready, or failed.
// `vals` is the view model from useHoistra(); the page reads vals.report/report* and
// vals.reportCards/report grid* for the two modes respectively.
import React from 'react';
import ReportCards from '../components/shell/ReportCards.jsx';

const BTN = { fontSize: "12px", padding: "7px 13px", borderRadius: "7px", cursor: "pointer", whiteSpace: "nowrap" };

function ReportsGrid({ vals }) {
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
          <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", gap: "24px", marginTop: "16px", flexWrap: "wrap" }}>
            <div style={{ display: "flex", flexDirection: "column", gap: "7px", minWidth: "0" }}>
              <div style={{ fontSize: "10.5px", letterSpacing: "0.13em", textTransform: "uppercase", color: "var(--color-accent)" }}>{"Custom reports · dynamic"}</div>
              <h1 style={{ fontSize: "31px", margin: "0", lineHeight: "1.1" }}>{"Report cards"}</h1>
              <div style={{ fontSize: "12.5px", color: "var(--color-neutral-400)", maxWidth: "76ch", lineHeight: "1.5" }}>{"Each card is a pinned question, refreshed on its own cadence by the server — no tab needs to stay open. Select the ones you don't want and remove them together."}</div>
            </div>
            {vals.reportAnySelected ? (
              <div style={{ display: "flex", alignItems: "center", gap: "9px", flexShrink: "0" }}>
                <span style={{ fontSize: "11.5px", color: "var(--color-neutral-500)" }}>{vals.reportSelectedCount + " selected"}</span>
                <div onClick={vals.deleteSelectedReports} style={{ ...BTN, border: "1px solid var(--st-risk)", color: vals.deleteSelectedArmed ? "var(--accent-ink)" : "var(--st-risk)", background: vals.deleteSelectedArmed ? "var(--st-risk)" : "transparent" }}>{vals.deleteSelectedLabel}</div>
                <div onClick={vals.clearReportSelection} style={{ ...BTN, border: "1px solid var(--color-divider)", color: "var(--color-neutral-400)" }}>{"Clear"}</div>
              </div>
            ) : !vals.reportGridEmpty ? (
              <div style={{ display: "flex", alignItems: "center", gap: "9px", flexShrink: "0" }}>
                <span style={{ fontSize: "11.5px", color: "var(--color-neutral-500)" }}>{vals.reportAllSelected ? "All selected" : "Select all"}</span>
                <div className="hv4" onClick={vals.toggleSelectAllReports} style={{ width: "17px", height: "17px", borderRadius: "4px", border: `1px solid ${vals.reportAllSelected ? "var(--color-accent)" : "var(--color-divider)"}`, background: vals.reportAllSelected ? "var(--color-accent)" : "transparent", cursor: "pointer" }}></div>
              </div>
            ) : null}
          </div>

          {vals.reportsLoading && vals.reportGridEmpty ? (
            <div style={{ marginTop: "34px", padding: "34px", borderRadius: "12px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", display: "flex", alignItems: "center", gap: "10px", fontSize: "13px", color: "var(--color-neutral-400)" }}>
              <i className="ph ph-circle-notch" style={{ fontSize: "16px", animation: "spin 1s linear infinite" }}></i>
              <span>{"Loading your report cards…"}</span>
            </div>
          ) : vals.reportGridEmpty ? (
            <div style={{ marginTop: "34px", padding: "34px", borderRadius: "12px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", display: "flex", flexDirection: "column", gap: "8px", alignItems: "flex-start" }}>
              <i className="ph ph-sparkle" style={{ fontSize: "21px", color: "var(--color-accent)" }}></i>
              <div style={{ fontSize: "15px" }}>{"No report cards yet"}</div>
              <div style={{ fontSize: "12.5px", color: "var(--color-neutral-400)", maxWidth: "62ch", lineHeight: "1.55" }}>{"Ask something in a session, then use the + next to Reports to pin it and pick a refresh cadence."}</div>
            </div>
          ) : (
            (vals.reportGroups || []).map((g) => (
              <div key={g.id} style={{ marginTop: "26px" }}>
                {/* The report itself. Two levels have always existed server-side — a report
                    holds cards — and this header is where the outer one becomes visible, and
                    deletable. Deleting it takes every card on it, so the armed label says how
                    many before the second click. */}
                <div style={{ display: "flex", alignItems: "center", gap: "10px", paddingBottom: "9px", borderBottom: "1px solid var(--color-divider)" }}>
                  <i className="ph ph-folder-simple" style={{ fontSize: "13px", color: "var(--color-neutral-500)", flexShrink: "0" }}></i>
                  <span style={{ fontSize: "13px", color: "var(--color-text)" }}>{g.name}</span>
                  <span style={{ fontSize: "11px", color: "var(--color-neutral-500)", flex: "1" }}>{g.count}</span>
                  <div className="hv4" onClick={g.remove} title={g.deleteTitle} style={{ fontSize: "11px", padding: "5px 10px", borderRadius: "7px", border: `1px solid ${g.armed ? "var(--st-risk)" : "var(--color-divider)"}`, color: g.armed ? "var(--accent-ink)" : "var(--color-neutral-400)", background: g.armed ? "var(--st-risk)" : "transparent", cursor: "pointer", whiteSpace: "nowrap", flexShrink: "0" }}>
                    {g.deleteLabel}
                  </div>
                </div>
                {g.cardIds.length ? (
                  <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))", gap: "12px", marginTop: "14px" }}>
                    {(vals.reportCards || []).filter((c) => g.cardIds.indexOf(c.id) > -1).map((c) => (
                      <div key={c.id} onClick={c.open} className="hv8" style={{ position: "relative", display: "flex", flexDirection: "column", gap: "8px", padding: "14px 15px", borderRadius: "11px", background: "var(--color-surface)", boxShadow: c.armed ? "0 0 0 1px var(--st-risk)" : c.active ? "0 0 0 1px var(--color-accent)" : "var(--shadow-sm)", cursor: "pointer" }}>
                        <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: "8px" }}>
                          <div style={{ fontSize: "13.5px", fontWeight: "600", lineHeight: "1.3", flex: "1", minWidth: "0" }}>{c.name}</div>
                          <div className="hv4" onClick={c.toggleSelect} title="Select" style={{ width: "16px", height: "16px", borderRadius: "4px", border: `1px solid ${c.selected ? "var(--color-accent)" : "var(--color-divider)"}`, background: c.selected ? "var(--color-accent)" : "transparent", flexShrink: "0", cursor: "pointer" }}></div>
                        </div>
                        <div style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "10.5px" }}>
                          <span style={{ width: "6px", height: "6px", borderRadius: "50%", background: c.statusColor, flexShrink: "0" }}></span>
                          <span style={{ color: "var(--color-neutral-400)" }}>{c.badge}</span>
                        </div>
                        <div style={{ fontSize: "11.5px", color: "var(--color-neutral-500)", lineHeight: "1.4", minHeight: "30px", display: "-webkit-box", WebkitLineClamp: "2", WebkitBoxOrient: "vertical", overflow: "hidden" }}>{c.snippet || c.prompt}</div>
                        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "8px", marginTop: "4px" }}>
                          <span style={{ fontSize: "10px", color: c.armed ? "var(--st-risk)" : "var(--color-neutral-500)", minWidth: "0", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                            {c.armed ? "Click the bin again to delete" : c.lastRun}
                          </span>
                          <div style={{ display: "flex", gap: "8px", flexShrink: "0" }}>
                            <i className="ph ph-arrow-clockwise hv6" onClick={c.runNow} title="Run now" style={{ fontSize: "13px", color: "var(--color-neutral-400)", cursor: "pointer" }}></i>
                            <i className="ph ph-trash hv11" onClick={c.remove} title={c.removeTitle} style={{ fontSize: "13px", color: c.removeColor, cursor: "pointer" }}></i>
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>
                ) : (
                  <div style={{ marginTop: "12px", fontSize: "11.5px", color: "var(--color-neutral-500)" }}>
                    {"No cards on this report — delete it, or pin a question to it with the + next to Reports."}
                  </div>
                )}
              </div>
            ))
          )}
        </div>
      </div>
  );
}

export default function CustomReport({ vals }) {
  if (vals.reportGridMode) return <ReportsGrid vals={vals} />;
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
            <span className="hv6" onClick={vals.openReportsGrid} style={{ fontSize: "12px", color: "var(--color-neutral-500)", cursor: "pointer" }}>{"Reports"}</span>
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
              <div onClick={vals.deleteReport} title={vals.deleteReportTitle} style={{ ...BTN, border: `1px solid ${vals.deleteReportArmed ? "var(--st-risk)" : "var(--color-divider)"}`, background: vals.deleteReportArmed ? "var(--st-risk)" : "transparent", color: vals.deleteReportArmed ? "var(--accent-ink)" : "var(--st-risk)" }}>{vals.deleteReportLabel}</div>
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
                {"The session's question is pinned. The server runs it against the current Hoist Graph at the next due time — no browser tab needs to stay open — and this page fills in with the result the next time it loads. Run now builds it immediately."}
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
              <ReportCards vals={vals} />
              {r.tools ? (
                <div style={{ fontFamily: "ui-monospace,monospace", fontSize: "10px", color: "var(--color-neutral-500)", marginTop: "16px", paddingTop: "10px", borderTop: "1px solid var(--color-divider)" }}>{r.tools}</div>
              ) : null}
            </>
          ) : null}
        </div>
      </div>
  );
}
