// Users — Users & access (admin). `vals` is the view model from useHoistra().
import React from 'react';

export default function Users({ vals }) {
  return (
    <div style={{ flex: "1", display: "flex", justifyContent: "flex-start", padding: "0 40px 80px" }}>
      <div style={{ width: "100%", maxWidth: "1400px", animation: "fadeUp 0.28s ease both" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "12px", padding: "24px 0 0" }}>
          <div className="hv6" onClick={vals.goHome} style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "12px", color: "var(--color-neutral-400)", cursor: "pointer" }}>
            <i className="ph ph-arrow-left" style={{ fontSize: "12px" }}></i>
            <span>{"Home"}</span>
          </div>
          <span style={{ fontSize: "12px", color: "var(--color-neutral-500)" }}>{"/"}</span>
          <span style={{ fontSize: "12px", color: "var(--color-neutral-500)" }}>{"Administration"}</span>
          <span style={{ fontSize: "12px", color: "var(--color-neutral-500)" }}>{"/"}</span>
          <span style={{ fontSize: "12px", color: "var(--color-neutral-500)" }}>{"Users & access"}</span>
        </div>
        <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", flexWrap: "wrap", gap: "16px 24px", marginTop: "18px" }}>
          <div style={{ minWidth: "0", flex: "1 1 340px" }}>
            <div style={{ fontSize: "10.5px", letterSpacing: "0.13em", textTransform: "uppercase", color: "var(--color-accent)" }}>{"Administration · access-control"}</div>
            <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap", marginTop: "7px" }}>
              <h2 style={{ fontSize: "28px", margin: "0", lineHeight: "1.15" }}>{"Users & access"}</h2>
              <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10px", letterSpacing: "0.09em", textTransform: "uppercase", padding: "3px 8px", borderRadius: "5px", background: "var(--color-accent)", color: "var(--accent-ink)" }}>{"Admin only"}</span>
            </div>
            <p style={{ fontSize: "13px", color: "var(--color-neutral-400)", margin: "8px 0 0", maxWidth: "88ch", lineHeight: "1.55" }}>
              {"Create users, invite them by email and allocate them to buildings. The building is the access boundary: a user sees and ingests only within the buildings assigned to them. Whether a user can ingest at all is a per-user setting."}
            </p>
          </div>
          <div className="btn btn-primary" onClick={vals.usToggleInvite} style={{ fontSize: "12px", padding: "7px 14px", cursor: "pointer", display: "flex", alignItems: "center", gap: "7px" }}>
            <i className="ph ph-user-plus" style={{ fontSize: "13px" }}></i>
            <span>{"Invite user"}</span>
          </div>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(158px,1fr))", gap: "11px", marginTop: "22px" }}>
          {(vals.usTiles || []).map((t, $index) => (
            <React.Fragment key={$index}>
              <div style={{ padding: "13px 14px 13px 16px", borderRadius: "10px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", position: "relative", overflow: "hidden", minWidth: "0" }}>
                <div style={{ position: "absolute", left: "0", top: "11px", bottom: "11px", width: "3px", borderRadius: "0 3px 3px 0", background: t.color }}></div>
                <div style={{ fontFamily: "ui-monospace,monospace", fontSize: "23px", lineHeight: "1.1", color: t.color }}>{t.value}</div>
                <div style={{ fontSize: "10px", letterSpacing: "0.09em", textTransform: "uppercase", color: "var(--color-neutral-500)", marginTop: "5px" }}>{t.label}</div>
                <div style={{ fontSize: "10.5px", color: "var(--color-neutral-500)", marginTop: "2px" }}>{t.hint}</div>
              </div>
            </React.Fragment>
          ))}
        </div>
        <div style={{ display: "flex", gap: "12px", alignItems: "flex-start", marginTop: "16px", padding: "13px 16px", borderRadius: "10px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)" }}>
          <i className="ph ph-shield-check" style={{ fontSize: "16px", color: "var(--color-accent)", marginTop: "1px" }}></i>
          <div style={{ fontSize: "12px", color: "var(--color-neutral-400)", lineHeight: "1.55" }}>
            <span style={{ color: "var(--color-text)" }}>{"Building-level isolation."}</span>
            {" The logged-in user is resolved, their authorised building IDs are read, and every query on the platform is filtered by them — viewing and ingestion alike. Data from unauthorised buildings is never returned, and new data can only be associated with an authorised building."}
          </div>
        </div>
        {vals.usInviteOpen ? (
          <div style={{ marginTop: "16px", padding: "18px", borderRadius: "11px", background: "var(--color-surface)", boxShadow: "var(--shadow-md)", display: "flex", flexDirection: "column", gap: "13px" }}>
            <div style={{ fontSize: "11px", letterSpacing: "0.11em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>{"Invite a user"}</div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(220px,1fr))", gap: "10px" }}>
              <input className="input" value={vals.usName} onChange={vals.usSetName} placeholder="Full name" style={{ fontSize: "12.5px", padding: "8px 11px", borderRadius: "7px", border: "1px solid var(--color-divider)", background: "var(--color-bg)", color: "var(--color-text)", fontFamily: "var(--font-body)", outline: "none" }} />
              <input className="input" value={vals.usEmail} onChange={vals.usSetEmail} placeholder="work email" style={{ fontSize: "12.5px", padding: "8px 11px", borderRadius: "7px", border: "1px solid var(--color-divider)", background: "var(--color-bg)", color: "var(--color-text)", fontFamily: "var(--font-body)", outline: "none" }} />
            </div>
            <div style={{ fontSize: "10.5px", letterSpacing: "0.09em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>{"Allocate to buildings"}</div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: "7px" }}>
              {(vals.usFormBlds || []).map((b, $index) => (
                <React.Fragment key={$index}>
                  <div onClick={b.pick} style={{ fontSize: "11.5px", padding: "5px 11px", borderRadius: "6px", border: `1px solid ${b.edge}`, background: b.bg, color: b.fg, cursor: "pointer" }}>{b.name}</div>
                </React.Fragment>
              ))}
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
              <div onClick={vals.usToggleIngestForm} style={{ width: "34px", height: "19px", borderRadius: "10px", background: vals.usFormTgBg, position: "relative", cursor: "pointer", flexShrink: "0", transition: "background 0.15s ease" }}>
                <div style={{ position: "absolute", top: "2.5px", left: vals.usFormTgLeft, width: "14px", height: "14px", borderRadius: "50%", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", transition: "left 0.15s ease" }}></div>
              </div>
              <span style={{ fontSize: "12px", color: "var(--color-neutral-300)" }}>{"Can ingest data — "}{vals.usFormTgLabel}</span>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap" }}>
              <div className="btn btn-primary" onClick={vals.usSend} style={{ fontSize: "12px", padding: "8px 15px", cursor: "pointer" }}>{"Send invitation"}</div>
              <div onClick={vals.usToggleInvite} style={{ fontSize: "12px", padding: "8px 13px", borderRadius: "8px", border: "1px solid var(--color-divider)", color: "var(--color-neutral-400)", cursor: "pointer" }}>{"Cancel"}</div>
              <span style={{ fontSize: "11px", color: "var(--color-neutral-500)" }}>{"They receive an email invitation, activate the account, then set or change their password on first sign-in."}</span>
            </div>
          </div>
        ) : null}
        <div style={{ display: "flex", alignItems: "center", marginTop: "18px" }}>
          <span style={{ display: "inline-flex", alignItems: "center", gap: "7px", padding: "3px 10px", borderRadius: "20px", border: "1px solid var(--color-divider)", fontSize: "10.5px", color: "var(--color-neutral-500)", marginLeft: "auto" }}>
            <span style={{ width: "6px", height: "6px", borderRadius: "50%", background: vals.usLiveSourceDot }}></span>
            <span>{vals.usLiveSourceLabel}</span>
            <span className="hv11" onClick={vals.usLiveRetry} style={{ color: "var(--color-accent)", cursor: "pointer", display: vals.usLiveRetryShow }}>{"Retry"}</span>
          </span>
        </div>
        <div style={{ marginTop: "10px", borderRadius: "11px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", overflow: "hidden" }}>
          <div style={{ display: "grid", gridTemplateColumns: "22px minmax(180px,1.3fr) minmax(140px,1fr) 130px minmax(150px,1fr) 92px 28px", gap: "12px", alignItems: "center", padding: "10px 16px", borderBottom: "1px solid var(--color-divider)", fontSize: "10px", letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
            <span></span>
            <span>{"User"}</span>
            <span>{"Buildings"}</span>
            <span>{"Can ingest data"}</span>
            <span>{"Usage"}</span>
            <span>{"Status"}</span>
            <span></span>
          </div>
          {(vals.axUsers || []).map((u, $index) => (
            <React.Fragment key={$index}>
              <div style={{ borderBottom: "1px solid var(--color-divider)" }}>
                <div className="hv2" onClick={u.toggle} style={{ display: "grid", gridTemplateColumns: "22px minmax(180px,1.3fr) minmax(140px,1fr) 130px minmax(150px,1fr) 92px 28px", gap: "12px", alignItems: "center", padding: "11px 16px", cursor: "pointer" }}>
                  <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "11px", color: "var(--color-neutral-500)" }}>{u.arrow}</span>
                  <div style={{ minWidth: "0" }}>
                    <div style={{ fontSize: "13px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{u.name}</div>
                    <div style={{ fontFamily: "ui-monospace,monospace", fontSize: "10.5px", color: "var(--color-neutral-500)", marginTop: "2px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{u.email}{" · "}{u.title}</div>
                  </div>
                  <div style={{ minWidth: "0" }}>
                    <div style={{ fontSize: "12px" }}>{u.nB}</div>
                    <div style={{ fontSize: "10.5px", color: "var(--color-neutral-500)", marginTop: "2px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{u.blds}</div>
                  </div>
                  <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                    <div onClick={u.toggleIngest} title="Can ingest data" style={{ width: "34px", height: "19px", borderRadius: "10px", background: u.tgBg, position: "relative", cursor: "pointer", flexShrink: "0", transition: "background 0.15s ease" }}>
                      <div style={{ position: "absolute", top: "2.5px", left: u.tgLeft, width: "14px", height: "14px", borderRadius: "50%", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", transition: "left 0.15s ease" }}></div>
                    </div>
                    <span style={{ fontSize: "11.5px", color: u.ingFg }}>{u.ingLabel}</span>
                  </div>
                  <div style={{ fontFamily: "ui-monospace,monospace", fontSize: "11px", color: "var(--color-neutral-400)" }}>
                    {u.usage}
                    <div style={{ fontSize: "10px", color: "var(--color-neutral-500)", marginTop: "2px" }}>{"last active "}{u.last}</div>
                  </div>
                  <span onClick={u.stClick} title={u.stTitle} style={{ fontSize: "10.5px", padding: "3px 9px", borderRadius: "5px", background: u.stBg, color: u.stFg, textAlign: "center", whiteSpace: "nowrap", cursor: u.isMe ? "default" : "pointer" }}>{u.status}</span>
                  {u.delShow ? (
                    <button type="button" onClick={u.delClick} title={u.delTitle} style={{ font: "inherit", background: "transparent", border: "none", padding: "2px", margin: "0", cursor: "pointer", color: u.delArmed ? "var(--st-risk)" : "var(--color-neutral-500)", opacity: u.delArmed ? "1" : "0.6", display: "flex", justifySelf: "center" }}>
                      <i className="ph ph-trash" style={{ fontSize: "13px" }}></i>
                    </button>
                  ) : <span></span>}
                </div>
                <div style={{ display: u.panelShow, flexDirection: "column", gap: "9px", padding: "13px 16px 15px 50px", background: "var(--color-bg)", borderTop: "1px solid var(--color-divider)" }}>
                  <div style={{ fontSize: "10.5px", letterSpacing: "0.09em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>{"Building allocation — "}{u.name}{" sees and ingests only here"}</div>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: "7px" }}>
                    {(u.alloc || []).map((b, $index2) => (
                      <React.Fragment key={$index2}>
                        <div onClick={b.pick} style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "11.5px", padding: "5px 11px", borderRadius: "6px", border: `1px solid ${b.edge}`, background: b.bg, color: b.fg, cursor: "pointer" }}>
                          <i className={`ph ${b.tick}`} style={{ fontSize: "12px" }}></i>
                          <span>{b.name}</span>
                        </div>
                      </React.Fragment>
                    ))}
                  </div>
                </div>
              </div>
            </React.Fragment>
          ))}
        </div>
        {vals.drShow ? (
          <div data-panel="data-reset" style={{ marginTop: "28px", padding: "18px 20px 20px", borderRadius: "11px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", borderLeft: "3px solid var(--st-risk)", display: "flex", flexDirection: "column", gap: "14px" }}>
            <div style={{ display: "flex", alignItems: "flex-start", gap: "12px" }}>
              <i className="ph ph-trash" style={{ fontSize: "18px", color: "var(--st-risk)", marginTop: "2px" }}></i>
              <div style={{ minWidth: "0" }}>
                <div style={{ fontSize: "10.5px", letterSpacing: "0.13em", textTransform: "uppercase", color: "var(--st-risk)" }}>{"Administration · data"}</div>
                <h3 style={{ fontSize: "18px", margin: "5px 0 0" }}>{"Reset page data"}</h3>
                <p style={{ fontSize: "12.5px", color: "var(--color-neutral-400)", margin: "6px 0 0", maxWidth: "92ch", lineHeight: "1.55" }}>
                  {"Delete this company's data behind the pages you tick, so the next ingest starts from nothing. Only this company's rows are touched, and it cannot be undone. Tick a page to see exactly what would go."}
                </p>
              </div>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(210px,1fr))", gap: "9px" }}>
              {(vals.drAreas || []).map((ar) => (
                <div key={ar.key} role="checkbox" aria-checked={ar.on} data-area={ar.key} onClick={ar.pick} style={{ display: "flex", gap: "10px", alignItems: "flex-start", padding: "11px 12px", borderRadius: "9px", border: `1px solid ${ar.edge}`, background: ar.bg, color: ar.fg, cursor: "pointer" }}>
                  <i className={`ph ${ar.tick}`} style={{ fontSize: "16px", marginTop: "1px" }}></i>
                  <div style={{ minWidth: "0", flex: "1" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: "7px", fontSize: "13px", color: "var(--color-text)" }}>
                      <i className={`ph ${ar.icon}`} style={{ fontSize: "13px" }}></i>
                      <span>{ar.label}</span>
                      {ar.count ? <span style={{ marginLeft: "auto", fontFamily: "ui-monospace,monospace", fontSize: "11px", color: "var(--st-risk)" }}>{ar.count}</span> : null}
                    </div>
                    <div style={{ fontSize: "11px", color: "var(--color-neutral-500)", marginTop: "3px", lineHeight: "1.4" }}>{ar.what}</div>
                  </div>
                </div>
              ))}
            </div>
            {vals.drDisabled ? (
              <div style={{ fontSize: "12px", color: "var(--color-neutral-400)", padding: "10px 12px", borderRadius: "8px", background: "var(--color-bg)" }}>{vals.drDisabled}</div>
            ) : null}
            {vals.drLoading ? <div style={{ fontSize: "12px", color: "var(--color-neutral-500)" }}>{"Counting what would be deleted…"}</div> : null}
            {vals.drError ? <div data-dr="error" style={{ fontSize: "12px", color: "var(--st-risk)" }}>{vals.drError}</div> : null}
            {vals.drDone ? <div data-dr="done" style={{ fontSize: "12px", color: "var(--st-ok)" }}>{vals.drDone}</div> : null}
            {vals.drHasPlan && !vals.drLoading ? (
              <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
                <div data-dr="summary" style={{ fontSize: "13px", color: "var(--color-text)" }}>
                  {vals.drSummary}
                  <span style={{ color: "var(--color-neutral-500)", fontSize: "11.5px" }}>{"  · "}{vals.drBuildings}</span>
                </div>
                <div style={{ borderRadius: "9px", border: "1px solid var(--color-divider)", overflow: "hidden" }}>
                  {(vals.drAreaRows || []).map((r, i) => (
                    <div key={i} style={{ borderTop: i ? "1px solid var(--color-divider)" : "none" }}>
                      <div onClick={r.toggle} style={{ display: "flex", alignItems: "center", gap: "8px", padding: "9px 12px", cursor: "pointer", fontSize: "12.5px" }}>
                        <i className={`ph ${r.open ? "ph-caret-down" : "ph-caret-right"}`} style={{ fontSize: "11px", color: "var(--color-neutral-500)" }}></i>
                        <span>{r.label}</span>
                        <span style={{ marginLeft: "auto", fontFamily: "ui-monospace,monospace" }}>{r.rows}</span>
                      </div>
                      {r.open ? (
                        <div style={{ padding: "2px 12px 10px 31px", display: "grid", gridTemplateColumns: "1fr auto", gap: "3px 16px", fontSize: "11.5px", color: "var(--color-neutral-400)" }}>
                          {r.tables.map((t) => (
                            <React.Fragment key={t.table}>
                              <span style={{ fontFamily: "ui-monospace,monospace" }}>{t.table}</span>
                              <span style={{ fontFamily: "ui-monospace,monospace", textAlign: "right" }}>{t.rows}</span>
                            </React.Fragment>
                          ))}
                          {!r.tables.length ? <span>{"Nothing to delete."}</span> : null}
                        </div>
                      ) : null}
                    </div>
                  ))}
                </div>
                {(vals.drBlocked || []).length ? (
                  <div data-dr="blocked" style={{ padding: "11px 13px", borderRadius: "9px", background: "var(--st-risk-bg)", display: "flex", flexDirection: "column", gap: "7px" }}>
                    <div style={{ fontSize: "12.5px", color: "var(--st-risk)" }}>{"This reset is blocked: a page you are keeping needs rows it would delete."}</div>
                    {vals.drBlocked.map((b, i) => (
                      <div key={i} style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap", fontSize: "12px", color: "var(--color-text)" }}>
                        <span>{b.what}{" — "}{b.why}</span>
                        {b.add ? (
                          <span className="hv2" data-dr="add-area" onClick={b.add} style={{ fontSize: "11.5px", padding: "3px 9px", borderRadius: "6px", border: "1px solid var(--st-risk)", color: "var(--st-risk)", cursor: "pointer" }}>{"Also clear "}{b.needs}</span>
                        ) : null}
                      </div>
                    ))}
                  </div>
                ) : null}
                {(vals.drLinks || []).length ? (
                  <div style={{ fontSize: "11.5px", color: "var(--color-neutral-400)", lineHeight: "1.6" }}>
                    <div style={{ color: "var(--color-neutral-300)", marginBottom: "3px" }}>{"Kept, but unlinked from what is deleted:"}</div>
                    {vals.drLinks.map((l, i) => (
                      <div key={i}>
                        <span style={{ fontFamily: "ui-monospace,monospace" }}>{l.what}</span>{" · "}{l.rows}{" rows — "}{l.why}
                      </div>
                    ))}
                  </div>
                ) : null}
                <div style={{ fontSize: "11.5px", color: "var(--color-neutral-500)" }}>{vals.drKept}</div>
                <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap" }}>
                  <span style={{ fontSize: "12px", color: "var(--color-neutral-400)" }}>{"Type "}<b style={{ color: "var(--color-text)" }}>{vals.drConfirmName}</b>{" to confirm"}</span>
                  <input className="input" data-dr="confirm" value={vals.drConfirm} onChange={vals.drSetConfirm} placeholder={vals.drConfirmName} style={{ fontSize: "12.5px", padding: "7px 10px", borderRadius: "7px", border: `1px solid ${vals.drNameOk ? "var(--st-risk)" : "var(--color-divider)"}`, background: "var(--color-bg)", color: "var(--color-text)", fontFamily: "var(--font-body)", outline: "none", minWidth: "240px" }} />
                  <button type="button" data-dr="apply" disabled={!vals.drCanApply} onClick={vals.drApply} style={{ font: "inherit", fontSize: "12px", padding: "8px 15px", borderRadius: "8px", border: "none", background: vals.drCanApply ? "var(--st-risk)" : "var(--color-neutral-800)", color: vals.drCanApply ? "#fff" : "var(--color-neutral-500)", cursor: vals.drCanApply ? "pointer" : "not-allowed" }}>{vals.drApplyLabel}</button>
                </div>
              </div>
            ) : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}
