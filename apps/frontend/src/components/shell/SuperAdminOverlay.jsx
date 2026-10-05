// SuperAdminOverlay — the Super Admin console, deliberately separate from any
// company's own admin app. `vals` is the view model from useHoistra().
import React from 'react';

export default function SuperAdminOverlay({ vals }) {
  return (
    <div style={{ position: "fixed", inset: "0", zIndex: "96", background: "var(--color-bg)", overflowY: "auto" }}>
      <div style={{ position: "sticky", top: "0", zIndex: "5", display: "flex", alignItems: "center", gap: "12px", padding: "11px 26px", background: "var(--color-surface)", borderBottom: "1px solid var(--color-divider)" }}>
        <i className="ph ph-lock-key" style={{ fontSize: "15px", color: "var(--color-accent)" }}></i>
        <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "12px", color: "var(--color-neutral-300)" }}>{"superadmin.hoistra.com"}</span>
        <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "9.5px", letterSpacing: "0.09em", textTransform: "uppercase", padding: "3px 8px", borderRadius: "5px", background: "var(--color-accent)", color: "var(--accent-ink)" }}>{"Platform operator"}</span>
        <div style={{ flex: "1" }}></div>
        <div className="hv4" onClick={vals.signOut} style={{ display: "flex", alignItems: "center", gap: "7px", fontSize: "12px", padding: "6px 13px", borderRadius: "7px", border: "1px solid var(--color-divider)", color: "var(--color-neutral-400)", cursor: "pointer" }}>
          <i className="ph ph-power" style={{ fontSize: "12px" }}></i>
          <span>{"Sign out"}</span>
        </div>
      </div>
      <div style={{ maxWidth: "1280px", margin: "0 auto", padding: "28px 40px 90px" }}>
        <div style={{ fontSize: "10.5px", letterSpacing: "0.13em", textTransform: "uppercase", color: "var(--color-accent)" }}>{"Super Admin · deliberately light"}</div>
        <h1 style={{ fontSize: "31px", margin: "7px 0 0", lineHeight: "1.1" }}>{"Companies on the platform"}</h1>
        <p style={{ fontSize: "13px", color: "var(--color-neutral-400)", margin: "9px 0 0", maxWidth: "88ch", lineHeight: "1.55" }}>
          {"Create company accounts, invite each company's administrator, and monitor usage and credit consumption. Day-to-day data management stays inside each company's own admin application — this console only onboards and observes."}
        </p>
        {vals.saLiveError ? (
          <div style={{ marginTop: "12px", padding: "8px 12px", borderRadius: "8px", background: "var(--st-warn-bg)", display: "flex", alignItems: "center", gap: "12px", maxWidth: "88ch" }}>
            <span style={{ flex: "1", fontSize: "11.5px", color: "var(--st-warn)", lineHeight: "1.5" }}>{vals.saLiveError}</span>
            <span className="hv4" onClick={vals.saLiveRetry} style={{ fontSize: "11.5px", color: "var(--st-warn)", textDecoration: "underline", cursor: "pointer", whiteSpace: "nowrap" }}>{"Retry now"}</span>
          </div>
        ) : null}
        <div style={{ display: "grid", gridTemplateColumns: "minmax(260px,320px) minmax(0,1fr)", gap: "22px", marginTop: "24px" }}>
          <div style={{ display: "flex", flexDirection: "column", gap: "10px", minWidth: "0" }}>
            <div className="btn btn-primary" onClick={vals.saToggleNew} style={{ fontSize: "12px", padding: "8px 14px", cursor: "pointer", display: "flex", alignItems: "center", justifyContent: "center", gap: "7px" }}>
              <i className="ph ph-plus" style={{ fontSize: "13px" }}></i>
              <span>{"Create company"}</span>
            </div>
            {vals.saNewOpen ? (
              <div style={{ padding: "14px", borderRadius: "10px", background: "var(--color-surface)", boxShadow: "var(--shadow-md)", display: "flex", flexDirection: "column", gap: "10px" }}>
                <input className="input" value={vals.saName} onChange={vals.saSetName} placeholder="Company name" style={{ fontSize: "12.5px", padding: "8px 11px", borderRadius: "7px", border: "1px solid var(--color-divider)", background: "var(--color-bg)", color: "var(--color-text)", fontFamily: "var(--font-body)", outline: "none" }} />
                <div style={{ display: "flex", gap: "5px", flexWrap: "wrap" }}>
                  {(vals.saCcs || []).map((c, $index) => (
                    <React.Fragment key={$index}>
                      <div onClick={c.pick} style={{ fontSize: "11px", padding: "4px 10px", borderRadius: "5px", background: c.bg, color: c.fg, cursor: "pointer" }}>{c.label}</div>
                    </React.Fragment>
                  ))}
                </div>
                <input className="input" value={vals.saEmail} onChange={vals.saSetEmail} placeholder="Administrator email (optional)" style={{ fontSize: "12.5px", padding: "8px 11px", borderRadius: "7px", border: "1px solid var(--color-divider)", background: "var(--color-bg)", color: "var(--color-text)", fontFamily: "var(--font-body)", outline: "none" }} />
                <div className="btn btn-primary" onClick={vals.saCreate} style={{ fontSize: "12px", padding: "7px 13px", cursor: "pointer", textAlign: "center" }}>{"Create and invite"}</div>
              </div>
            ) : null}
            {(vals.saCompanies || []).map((c, $index) => (
              <React.Fragment key={$index}>
                <div className="hv1" onClick={c.pick} style={{ display: "flex", alignItems: "center", gap: "10px", padding: "12px 14px", borderRadius: "10px", background: c.bg, border: `1px solid ${c.edge}`, cursor: "pointer", boxShadow: "var(--shadow-sm)" }}>
                  <span style={{ width: "8px", height: "8px", borderRadius: "50%", background: c.dot, flexShrink: "0" }}></span>
                  <div style={{ flex: "1", minWidth: "0" }}>
                    <div style={{ fontSize: "13px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{c.name}</div>
                    <div style={{ fontSize: "10.5px", color: "var(--color-neutral-500)", marginTop: "2px" }}>{c.cc}{" · "}{c.status}</div>
                  </div>
                  <span title={c.valueTitle} style={{ fontFamily: "ui-monospace,monospace", fontSize: "10.5px", color: "var(--color-neutral-500)", whiteSpace: "nowrap" }}>{c.value}</span>
                </div>
              </React.Fragment>
            ))}
          </div>
          <div style={{ minWidth: "0" }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: "12px" }}>
              <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                <span style={{ width: "9px", height: "9px", borderRadius: "50%", background: vals.saCo.dot }}></span>
                <h2 style={{ fontSize: "22px", margin: "0", lineHeight: "1.15" }}>{vals.saCo.name}</h2>
                <span style={{ fontSize: "11px", color: "var(--color-neutral-500)" }}>{vals.saCo.cc}{" · "}{vals.saCo.status}</span>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                {vals.saCo.viewAsShow ? (
                  <div
                    className="hv25"
                    onClick={vals.saCo.viewAsCurrent ? undefined : vals.saCo.viewAs}
                    title={vals.saCo.viewAsCurrent ? "You are viewing this company's data now" : "See Buildings, Compliance, Vendors, Home, Users & access and Audit trail as this company"}
                    style={{ display: "flex", alignItems: "center", gap: "7px", fontSize: "12px", padding: "7px 14px", borderRadius: "8px", border: "1px solid var(--color-divider)", color: vals.saCo.viewAsCurrent ? "var(--color-neutral-500)" : "var(--color-neutral-300)", cursor: vals.saCo.viewAsCurrent ? "default" : "pointer" }}
                  >
                    <i className="ph ph-eye" style={{ fontSize: "13px" }}></i>
                    <span>{vals.saCo.viewAsCurrent ? "Currently viewing" : "View as this company"}</span>
                  </div>
                ) : null}
                <div className="hv25" onClick={vals.saCo.invite} style={{ display: "flex", alignItems: "center", gap: "7px", fontSize: "12px", padding: "7px 14px", borderRadius: "8px", border: "1px solid var(--color-accent)", color: "var(--color-accent)", cursor: "pointer" }}>
                  <i className="ph ph-paper-plane-tilt" style={{ fontSize: "13px" }}></i>
                  <span>{vals.saCo.inviteLabel}</span>
                </div>
              </div>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(170px,1fr))", gap: "11px", marginTop: "16px" }}>
              {(vals.saTiles || []).map((t, $index) => (
                <React.Fragment key={$index}>
                  <div style={{ padding: "13px 14px 13px 16px", borderRadius: "10px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", position: "relative", overflow: "hidden", minWidth: "0" }}>
                    <div style={{ position: "absolute", left: "0", top: "11px", bottom: "11px", width: "3px", borderRadius: "0 3px 3px 0", background: t.color }}></div>
                    <div style={{ fontFamily: "ui-monospace,monospace", fontSize: "21px", lineHeight: "1.1", color: t.color }}>{t.value}</div>
                    <div style={{ fontSize: "10px", letterSpacing: "0.09em", textTransform: "uppercase", color: "var(--color-neutral-500)", marginTop: "5px" }}>{t.label}</div>
                    <div style={{ fontSize: "10.5px", color: "var(--color-neutral-500)", marginTop: "2px" }}>{t.hint}</div>
                  </div>
                </React.Fragment>
              ))}
            </div>
            <div style={{ marginTop: "18px", padding: "16px 18px", borderRadius: "11px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)" }}>
              <div style={{ fontSize: "10.5px", letterSpacing: "0.11em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>{"Credit consumption across companies — this month"}</div>
              <div style={{ display: "flex", flexDirection: "column", gap: "8px", marginTop: "12px" }}>
                {(vals.saCredits || []).map((c, $index) => (
                  <React.Fragment key={$index}>
                    <div style={{ display: "grid", gridTemplateColumns: "170px minmax(0,1fr) 70px", gap: "12px", alignItems: "center" }}>
                      <span style={{ fontSize: "12px", color: c.fg, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{c.name}</span>
                      <div style={{ height: "7px", borderRadius: "4px", background: "var(--color-bg)", overflow: "hidden" }}>
                        <div style={{ height: "100%", width: c.bar, borderRadius: "4px", background: "var(--color-accent)" }}></div>
                      </div>
                      <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "11px", color: "var(--color-neutral-400)", textAlign: "right" }}>{c.v}</span>
                    </div>
                  </React.Fragment>
                ))}
              </div>
              <div style={{ fontSize: "11px", color: "var(--color-neutral-500)", marginTop: "12px", lineHeight: "1.5" }}>
                {"Billing is usage-led: platform activity, API requests and credits, with a nominal per-seat charge. No user limits are imposed — more users simply consume more."}
              </div>
            </div>
            {/* Platform cost — what each company cost to run against what it was billed (logic/platformCost.js). */}
            <div style={{ marginTop: "18px", padding: "16px 18px", borderRadius: "11px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)" }}>
              <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap" }}>
                <div style={{ flex: "1", fontSize: "10.5px", letterSpacing: "0.11em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>{"Platform cost by company" + (vals.pcMonthLabel ? " — " + vals.pcMonthLabel : "")}</div>
                {(vals.pcMonths || []).map((m, $index) => (
                  <span key={$index} className="hv4" onClick={m.pick} style={{ fontSize: "11px", padding: "4px 10px", borderRadius: "6px", cursor: "pointer", border: "1px solid " + (m.on ? "var(--color-accent)" : "var(--color-divider)"), color: m.on ? "var(--color-accent)" : "var(--color-neutral-400)" }}>{m.label}</span>
                ))}
                <span className="hv4" onClick={vals.pcReload} title="Read again" style={{ fontSize: "12px", color: "var(--color-neutral-500)", cursor: "pointer" }}><i className="ph ph-arrow-clockwise"></i></span>
              </div>
              {vals.pcError ? <div style={{ marginTop: "10px", fontSize: "11.5px", color: "var(--st-warn)" }}>{"Platform cost unavailable — " + vals.pcError}</div> : null}
              {vals.pcLoading ? <div style={{ marginTop: "10px", fontSize: "12px", color: "var(--color-neutral-500)" }}>{"Reading what the platform cost…"}</div> : null}
              {(vals.pcTiles || []).length ? (
                <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(170px,1fr))", gap: "11px", marginTop: "12px" }}>
                  {vals.pcTiles.map((t, $index) => (
                    <div key={$index} style={{ padding: "11px 13px", borderRadius: "9px", background: "var(--color-bg)", borderLeft: "3px solid " + t.color }}>
                      <div style={{ fontFamily: "ui-monospace,monospace", fontSize: "19px", color: t.color }}>{t.value}</div>
                      <div style={{ fontSize: "10px", letterSpacing: "0.09em", textTransform: "uppercase", color: "var(--color-neutral-500)", marginTop: "4px" }}>{t.label}</div>
                      <div style={{ fontSize: "10.5px", color: "var(--color-neutral-500)", marginTop: "2px" }}>{t.hint}</div>
                    </div>
                  ))}
                </div>
              ) : null}
              {(vals.pcRows || []).length ? (
                <div style={{ overflowX: "auto", marginTop: "14px" }}>
                  <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "12px" }}>
                    <thead>
                      <tr style={{ textAlign: "right", fontSize: "10px", letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                        {["Company", "Queries", "Chat model $", "$ / query", "Compliance $ (n)", "Migrations · $", "Infra share", "Total cost", "Billed", "Margin"].map((h, i) => (
                          <th key={i} style={{ padding: "6px 8px", borderBottom: "1px solid var(--color-divider)", textAlign: i === 0 ? "left" : "right", whiteSpace: "nowrap" }}>{h}</th>
                        ))}
                      </tr>
                    </thead>
                    <tbody>
                      {vals.pcRows.map((r, $index) => (
                        <tr key={$index} className="hv1" onClick={r.open} title="Open this company's split" style={{ textAlign: "right", fontFamily: "ui-monospace,monospace", cursor: "pointer", background: r.selected ? "var(--color-accent-900)" : "transparent" }}>
                          <td style={{ padding: "7px 8px", textAlign: "left", fontFamily: "var(--font-body)", borderBottom: "1px solid var(--color-neutral-900)", whiteSpace: "nowrap" }}><i className={"ph " + (r.selected ? "ph-caret-down" : "ph-caret-right")} style={{ fontSize: "10px", marginRight: "6px", color: "var(--color-neutral-500)" }}></i>{r.name}</td>
                          {[r.queries, r.chat, r.perQuery, r.compliance, r.migrations, r.infra, r.cost, r.billed].map((v, i) => (
                            <td key={i} style={{ padding: "7px 8px", borderBottom: "1px solid var(--color-neutral-900)", whiteSpace: "nowrap" }}>{v}</td>
                          ))}
                          <td style={{ padding: "7px 8px", borderBottom: "1px solid var(--color-neutral-900)", color: r.marginColor, whiteSpace: "nowrap" }}>{r.margin}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (!vals.pcLoading && !vals.pcError ? <div style={{ marginTop: "10px", fontSize: "12px", color: "var(--color-neutral-500)" }}>{"No company activity recorded this month."}</div> : null)}
              {vals.pcDetail ? (
                <div style={{ marginTop: "14px", padding: "14px", borderRadius: "10px", border: "1px solid var(--color-divider)", background: "var(--color-bg)" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap" }}>
                    <div style={{ flex: "1", fontSize: "13px" }}>{vals.pcDetail.name}{vals.pcDetail.title ? <span style={{ color: "var(--color-neutral-500)" }}>{" · " + vals.pcDetail.title}</span> : null}</div>
                    <span className="hv4" onClick={vals.pcDetail.close} style={{ fontSize: "11px", color: "var(--color-neutral-500)", cursor: "pointer" }}>{"Close"}</span>
                  </div>
                  <div style={{ display: "flex", gap: "4px", flexWrap: "wrap", marginTop: "10px", borderBottom: "1px solid var(--color-divider)" }}>
                    {(vals.pcDetail.tabs || []).map((t) => (
                      <span key={t.key} className="hv4" onClick={() => vals.pcDetail.pickTab(t.key)} style={{ fontSize: "11.5px", padding: "6px 11px", cursor: "pointer", borderBottom: "2px solid " + (t.on ? "var(--color-accent)" : "transparent"), color: t.on ? "var(--color-text)" : "var(--color-neutral-500)" }}>{t.label}</span>
                    ))}
                  </div>
                  {vals.pcDetail.error ? <div style={{ marginTop: "10px", fontSize: "11.5px", color: "var(--st-warn)" }}>{"Could not read this company — " + vals.pcDetail.error}</div> : null}
                  {vals.pcDetail.loading && !(vals.pcDetail.rows || []).length ? <div style={{ marginTop: "10px", fontSize: "12px", color: "var(--color-neutral-500)" }}>{"Reading the split…"}</div> : null}
                  {(vals.pcDetail.rows || []).length ? (
                    <div style={{ overflowX: "auto", marginTop: "10px" }}>
                      <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "12px" }}>
                        <thead>
                          <tr style={{ fontSize: "10px", letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                            {(vals.pcDetail.columns || []).map((h, i) => <th key={i} style={{ padding: "6px 8px", borderBottom: "1px solid var(--color-divider)", textAlign: i === 0 ? "left" : "right", whiteSpace: "nowrap" }}>{h}</th>)}
                          </tr>
                        </thead>
                        <tbody>
                          {vals.pcDetail.rows.map((row, i) => (
                            <tr key={i}>
                              {row.map((cell, j) => <td key={j} style={{ padding: "6px 8px", borderBottom: "1px solid var(--color-neutral-900)", textAlign: j === 0 ? "left" : "right", fontFamily: j === 0 ? "var(--font-body)" : "ui-monospace,monospace", whiteSpace: j === 0 ? "normal" : "nowrap" }}>{String(cell)}</td>)}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  ) : (!vals.pcDetail.loading && !vals.pcDetail.error ? <div style={{ marginTop: "10px", fontSize: "12px", color: "var(--color-neutral-500)" }}>{"Nothing recorded for this view."}</div> : null)}
                  {vals.pcDetail.note ? <div style={{ marginTop: "10px", fontSize: "11px", color: "var(--color-neutral-500)", lineHeight: "1.5" }}>{vals.pcDetail.note}</div> : null}
                </div>
              ) : null}
              {vals.pcQuiet ? <div style={{ marginTop: "8px", fontSize: "11px", color: "var(--color-neutral-500)" }}>{vals.pcQuiet}</div> : null}
              {(vals.pcModels || []).length ? (
                <div style={{ display: "flex", gap: "8px", flexWrap: "wrap", marginTop: "12px" }}>
                  {vals.pcModels.map((m, $index) => (
                    <span key={$index} style={{ fontSize: "11px", padding: "4px 9px", borderRadius: "6px", background: "var(--color-bg)", color: "var(--color-neutral-400)", fontFamily: "ui-monospace,monospace" }}>{m.model + " · " + m.cost + " · " + m.share + " · " + m.calls + " calls"}</span>
                  ))}
                </div>
              ) : null}
              <div style={{ fontSize: "11px", color: "var(--color-neutral-500)", marginTop: "12px", lineHeight: "1.5" }}>{vals.pcNote}</div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
