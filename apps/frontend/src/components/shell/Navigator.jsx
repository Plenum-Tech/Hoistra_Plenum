// Navigator — rail / panel, role-scoped reports, spaces, sessions, new report
// Ported from the Hoistra prototype template. `vals` is the view model from useHoistra().
import React from 'react';

export default function Navigator({ vals }) {
  return (
      <div style={{ position: "fixed", left: "0", top: "0", bottom: "0", width: vals.navWidth, zIndex: "50", background: "var(--color-surface)", borderRight: "1px solid var(--color-divider)", display: "flex", flexDirection: "column", overflow: "hidden", transition: "width 0.2s ease" }}>
        {vals.navClosed ? (
          <>
            <div style={{ flex: "1", display: "flex", flexDirection: "column", alignItems: "center", gap: "3px", padding: "14px 0 16px", width: "52px" }}>
              <div className="hv5" onClick={vals.toggleNav} title="Open navigator" style={{ width: "34px", height: "34px", borderRadius: "8px", display: "flex", alignItems: "center", justifyContent: "center", cursor: "pointer", color: "var(--color-neutral-400)", marginBottom: "12px" }}>
                <i className="ph ph-sidebar-simple" style={{ fontSize: "18px" }}></i>
              </div>
              {(vals.navSections || []).map((n, $index) => (
                <React.Fragment key={$index}>
                  <div className="hv2" onClick={n.click} title={n.label} style={{ position: "relative", width: "34px", height: "34px", borderRadius: "8px", display: "flex", alignItems: "center", justifyContent: "center", cursor: "pointer", color: n.color, background: n.chip }}>
                    <i className={`ph ${n.icon}`} style={{ fontSize: "17px" }}></i>
                    <span style={{ position: "absolute", top: "0px", right: "-1px", minWidth: "14px", height: "14px", padding: "0 3px", borderRadius: "7px", background: "var(--color-accent)", color: "var(--accent-ink)", fontSize: "9px", lineHeight: "1", display: n.show, alignItems: "center", justifyContent: "center" }}>
                      {n.count}
                    </span>
                  </div>
                </React.Fragment>
              ))}
              <div style={{ flex: "1" }}></div>
              <div className="hv5" onClick={vals.toggleNav} title="Sessions and spaces" style={{ width: "34px", height: "34px", borderRadius: "8px", display: "flex", alignItems: "center", justifyContent: "center", cursor: "pointer", color: "var(--color-neutral-500)" }}>
                <i className="ph ph-clock-counter-clockwise" style={{ fontSize: "17px" }}></i>
              </div>
            </div>
          </>
        ) : null}
        {vals.navOpen ? (
          <>
            <div style={{ flex: "1", width: "248px", display: "flex", flexDirection: "column", overflowY: "auto", paddingBottom: "18px" }}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "16px 14px 12px" }}>
                <span style={{ fontSize: "10.5px", letterSpacing: "0.13em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                  {"Navigator"}
                </span>
                <i className="ph ph-sidebar-simple hv6" onClick={vals.toggleNav} title="Collapse" style={{ fontSize: "16px", color: "var(--color-neutral-500)", cursor: "pointer" }}></i>
              </div>
              <div className="hv7" onClick={vals.newQuery} style={{ margin: "0 12px 16px", padding: "9px 13px", borderRadius: "8px", background: "var(--color-accent)", color: "var(--accent-ink)", fontSize: "13px", display: "flex", alignItems: "center", gap: "8px", cursor: "pointer" }}>
                <i className="ph ph-plus" style={{ fontSize: "14px" }}></i>
                <span>
                  {"New query"}
                </span>
              </div>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "0 14px 7px" }}>
                <span style={{ fontSize: "10.5px", letterSpacing: "0.11em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                  {"Reports"}
                </span>
                <i className="ph ph-plus hv6" onClick={vals.toggleReportMenu} title="New report" style={{ fontSize: "12px", color: "var(--color-neutral-500)", cursor: "pointer" }}></i>
              </div>
              {vals.reportMenu ? (
                <>
                  <div style={{ margin: "0 10px 12px", padding: "12px", borderRadius: "9px", background: "var(--color-neutral-900)", display: "flex", flexDirection: "column", gap: "8px" }}>
                    <div style={{ fontSize: "10px", letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                      {"Build from a session"}
                    </div>
                    {vals.reportSourcesEmpty ? (
                      <div style={{ fontSize: "11px", lineHeight: "1.45", color: "var(--color-neutral-500)", padding: "2px 6px" }}>
                        {"Ask something first — a report is built from a session's question and re-runs it."}
                      </div>
                    ) : null}
                    {(vals.reportSources || []).map((r, $index) => (
                      <React.Fragment key={$index}>
                        <div className="hv8" onClick={r.pick} style={{ display: "flex", gap: "7px", alignItems: "flex-start", padding: "5px 6px", borderRadius: "6px", cursor: "pointer", background: r.chip }}>
                          <i className={`ph ${r.tick}`} style={{ fontSize: "13px", color: r.color, marginTop: "1px", flexShrink: "0" }}></i>
                          <span style={{ fontSize: "11.5px", lineHeight: "1.35", color: "var(--color-neutral-300)" }}>
                            {r.label}
                          </span>
                        </div>
                      </React.Fragment>
                    ))}
                    <div style={{ fontSize: "10px", letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--color-neutral-500)", marginTop: "3px" }}>
                      {"Refresh"}
                    </div>
                    <div style={{ display: "flex", flexDirection: "column", gap: "3px", maxHeight: "150px", overflowY: "auto" }}>
                      {(vals.reportCadences || []).map((c, $index) => (
                        <React.Fragment key={$index}>
                          <div className="hv8" onClick={c.pick} style={{ display: "flex", gap: "7px", alignItems: "center", padding: "5px 6px", borderRadius: "6px", cursor: "pointer", background: c.chip }}>
                            <i className={`ph ${c.tick}`} style={{ fontSize: "13px", color: c.color, flexShrink: "0" }}></i>
                            <span style={{ fontSize: "11.5px", lineHeight: "1.35", color: "var(--color-neutral-300)" }}>
                              {c.label}
                            </span>
                          </div>
                        </React.Fragment>
                      ))}
                    </div>
                    <div style={{ display: vals.reportDaysShow, flexDirection: "column", gap: "6px", padding: "8px", borderRadius: "7px", background: "var(--color-surface)" }}>
                      <div style={{ display: "flex", gap: "3px" }}>
                        {(vals.reportDays || []).map((d, $index) => (
                          <React.Fragment key={$index}>
                            <div onClick={d.pick} title={d.title} style={{ flex: "1", textAlign: "center", fontFamily: "ui-monospace,monospace", fontSize: "10.5px", padding: "5px 0", borderRadius: "5px", border: `1px solid ${d.edge}`, background: d.bg, color: d.fg, cursor: "pointer" }}>
                              {d.label}
                            </div>
                          </React.Fragment>
                        ))}
                      </div>
                      <div style={{ display: "flex", alignItems: "center", gap: "7px" }}>
                        <span style={{ fontSize: "10.5px", color: "var(--color-neutral-500)", whiteSpace: "nowrap" }}>
                          {"at"}
                        </span>
                        <input className="input" value={vals.reportTime} onChange={vals.setReportTime} placeholder="14:00" maxLength="5" style={{ flex: "1", minWidth: "0", boxSizing: "border-box", fontSize: "11.5px", padding: "5px 8px", borderRadius: "5px", border: "1px solid var(--color-divider)", background: "var(--color-bg)", color: "var(--color-text)", fontFamily: "ui-monospace,monospace", outline: "none" }} />
                        <span style={{ fontSize: "10px", color: "var(--color-neutral-500)", whiteSpace: "nowrap" }}>
                          {"24h"}
                        </span>
                      </div>
                    </div>
                    <input className="input" value={vals.reportName} onChange={vals.setReportName} placeholder="Report name" style={{ width: "100%", boxSizing: "border-box", fontSize: "12px", padding: "7px 9px", borderRadius: "6px", border: "1px solid var(--color-divider)", background: "var(--color-surface)", color: "var(--color-text)", fontFamily: "var(--font-body)", outline: "none", marginTop: "3px" }} />
                    <div style={{ display: "flex", gap: "7px" }}>
                      <div className="hv7" onClick={vals.createReport} style={{ flex: "1", textAlign: "center", padding: "7px", borderRadius: "7px", background: "var(--color-accent)", color: "var(--accent-ink)", fontSize: "12px", cursor: "pointer" }}>
                        {"Create report"}
                      </div>
                      <div onClick={vals.cancelReport} style={{ padding: "7px 11px", borderRadius: "7px", border: "1px solid var(--color-divider)", fontSize: "12px", color: "var(--color-neutral-400)", cursor: "pointer" }}>
                        {"Cancel"}
                      </div>
                    </div>
                    <div style={{ fontSize: "10px", color: "var(--color-neutral-500)", lineHeight: "1.45" }}>
                      {vals.reportCadenceNote}
                    </div>
                  </div>
                </>
              ) : null}
              {(vals.navSections || []).map((n, $index) => (
                <React.Fragment key={$index}>
                  <div className="hv2" onClick={n.click} style={{ display: "flex", alignItems: "center", gap: "10px", margin: "0 8px", padding: "7px 10px", borderRadius: "7px", cursor: "pointer", fontSize: "13px", color: n.color, background: n.chip }}>
                    <i className={`ph ${n.icon}`} style={{ fontSize: "15px" }}></i>
                    <span style={{ flex: "1" }}>
                      {n.label}
                    </span>
                    <span style={{ fontSize: "11px", color: "var(--color-accent)" }}>
                      {n.count}
                    </span>
                  </div>
                </React.Fragment>
              ))}
              {(vals.navReports || []).map((r, $index) => (
                <React.Fragment key={$index}>
                  <div className="hv2" onClick={r.click} style={{ display: "flex", alignItems: "center", gap: "10px", margin: "0 8px", padding: "7px 10px", borderRadius: "7px", cursor: "pointer", fontSize: "13px", color: r.color, background: r.chip }}>
                    <span title="Custom report" style={{ width: "15px", height: "15px", borderRadius: "4px", border: "1px solid currentColor", fontSize: "9.5px", lineHeight: "1", display: "flex", alignItems: "center", justifyContent: "center", flexShrink: "0" }}>
                      {"C"}
                    </span>
                    <span style={{ flex: "1" }}>
                      {r.name}
                    </span>
                    <span style={{ fontSize: "10px", color: "var(--color-neutral-500)" }}>
                      {r.badge}
                    </span>
                  </div>
                </React.Fragment>
              ))}
              {(vals.navAdmin || []).map((a, $index) => (
                <React.Fragment key={$index}>
                  <div className="hv2" onClick={a.click} style={{ display: "flex", alignItems: "center", gap: "10px", margin: "0 8px", padding: "7px 10px", borderRadius: "7px", cursor: "pointer", fontSize: "13px", color: a.color, background: a.chip }}>
                    <i className={`ph ${a.icon}`} style={{ fontSize: "15px" }}></i>
                    <span style={{ flex: "1" }}>
                      {a.label}
                    </span>
                    <span style={{ fontSize: "10px", color: "var(--color-neutral-500)" }}>
                      {a.badge}
                    </span>
                  </div>
                </React.Fragment>
              ))}
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "20px 14px 7px" }}>
                <span style={{ fontSize: "10.5px", letterSpacing: "0.11em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                  {"Spaces"}
                </span>
                <i className="ph ph-plus hv6" onClick={vals.newSpace} title="New space" style={{ fontSize: "12px", color: "var(--color-neutral-500)", cursor: "pointer" }}></i>
              </div>
              {vals.navSpaceNew ? (
                <div style={{ display: "flex", alignItems: "center", gap: "6px", margin: "0 10px 8px" }}>
                  <input className="input" value={vals.navSpaceName} onChange={vals.setNavSpaceName} onKeyDown={vals.navSpaceKey} placeholder="Space name" autoFocus maxLength="120" disabled={!vals.navSpaceCanCreate} style={{ flex: "1", minWidth: "0", boxSizing: "border-box", fontSize: "12px", padding: "6px 9px", borderRadius: "6px", border: "1px solid var(--color-accent)", background: "var(--color-surface)", color: "var(--color-text)", fontFamily: "var(--font-body)", outline: "none" }} />
                  <div className="hv7" onClick={vals.navSpaceCanCreate ? vals.navSpaceCreate : undefined} title="Saves to svc-udr (plenum_cafm.saved_spaces)" style={{ padding: "6px 9px", borderRadius: "6px", background: "var(--color-accent)", color: "var(--accent-ink)", fontSize: "11.5px", cursor: vals.navSpaceCanCreate ? "pointer" : "default", opacity: vals.navSpaceCanCreate ? "1" : "0.5" }}>
                    {vals.navSpaceBusy ? "…" : "Save"}
                  </div>
                  <i className="ph ph-x hv6" onClick={vals.navSpaceCancel} title="Cancel" style={{ fontSize: "12px", color: "var(--color-neutral-500)", cursor: "pointer" }}></i>
                </div>
              ) : null}
              {(vals.navSpaces || []).map((sp, $index) => (
                <React.Fragment key={$index}>
                  <div className="hv2" onClick={sp.click} title={sp.title} style={{ display: "flex", alignItems: "center", gap: "10px", margin: "0 8px", padding: "6px 10px", borderRadius: "7px", cursor: "pointer", fontSize: "12.5px", color: sp.active ? "var(--color-accent)" : "var(--color-neutral-300)", background: sp.active ? "var(--color-accent-900)" : "transparent" }}>
                    <i className={`ph ${sp.icon}`} style={{ fontSize: "14px", color: sp.active ? "var(--color-accent)" : "var(--color-neutral-500)" }}></i>
                    <span style={{ flex: "1", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {sp.name}
                    </span>
                    <span style={{ fontSize: "11px", color: sp.tone, whiteSpace: "nowrap" }}>
                      {sp.n}
                    </span>
                  </div>
                </React.Fragment>
              ))}
              {vals.navSpaceNote ? (
                <div title={vals.navSpaceNoteTip} style={{ padding: "2px 18px 4px", fontSize: "10.5px", lineHeight: "1.4", color: "var(--color-neutral-500)" }}>
                  {vals.navSpaceNote}
                </div>
              ) : null}
              <div style={{ fontSize: "10.5px", letterSpacing: "0.11em", textTransform: "uppercase", color: "var(--color-neutral-500)", padding: "20px 14px 7px" }}>
                {"Sessions"}
              </div>
              {vals.navSessionsEmpty ? (
                <div style={{ padding: "2px 18px 4px", fontSize: "10.5px", lineHeight: "1.4", color: "var(--color-neutral-500)" }}>
                  {"No sessions yet — every question you ask lands here."}
                </div>
              ) : null}
              {(vals.navSessions || []).map((q, $index) => (
                <React.Fragment key={$index}>
                  <div className="hv2" onClick={q.click} title={q.label} style={{ display: "grid", gridTemplateColumns: "14px 1fr", gap: "8px", alignItems: "start", margin: "0 8px", padding: "6px 10px", borderRadius: "7px", cursor: "pointer", background: q.active ? "var(--color-accent-900)" : "transparent" }}>
                    <i className={`ph ${q.icon}`} style={{ fontSize: "12px", color: q.active ? "var(--color-accent)" : "var(--color-neutral-500)", marginTop: "3px" }}></i>
                    <div style={{ display: "flex", flexDirection: "column", gap: "1px", minWidth: "0" }}>
                      <span style={{ fontSize: "12.5px", color: q.active ? "var(--color-accent)" : "var(--color-neutral-300)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {q.label}
                      </span>
                      <span style={{ fontSize: "10.5px", color: "var(--color-neutral-500)" }}>
                        {q.when}
                      </span>
                    </div>
                  </div>
                </React.Fragment>
              ))}
              <div onClick={vals.allSessions} style={{ margin: "8px 8px 0", padding: "6px 10px", fontSize: "11.5px", color: "var(--color-accent)", cursor: "pointer" }}>
                {vals.navSessionsMore}
              </div>
            </div>
          </>
        ) : null}
      </div>
  );
}
