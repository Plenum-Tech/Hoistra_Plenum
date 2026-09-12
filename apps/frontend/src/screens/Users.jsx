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
          <div style={{ display: "grid", gridTemplateColumns: "22px minmax(180px,1.3fr) minmax(140px,1fr) 130px minmax(150px,1fr) 92px", gap: "12px", alignItems: "center", padding: "10px 16px", borderBottom: "1px solid var(--color-divider)", fontSize: "10px", letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
            <span></span>
            <span>{"User"}</span>
            <span>{"Buildings"}</span>
            <span>{"Can ingest data"}</span>
            <span>{"Usage"}</span>
            <span>{"Status"}</span>
          </div>
          {(vals.axUsers || []).map((u, $index) => (
            <React.Fragment key={$index}>
              <div style={{ borderBottom: "1px solid var(--color-divider)" }}>
                <div className="hv2" onClick={u.toggle} style={{ display: "grid", gridTemplateColumns: "22px minmax(180px,1.3fr) minmax(140px,1fr) 130px minmax(150px,1fr) 92px", gap: "12px", alignItems: "center", padding: "11px 16px", cursor: "pointer" }}>
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
                  <span style={{ fontSize: "10.5px", padding: "3px 9px", borderRadius: "5px", background: u.stBg, color: u.stFg, textAlign: "center", whiteSpace: "nowrap" }}>{u.status}</span>
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
      </div>
    </div>
  );
}
