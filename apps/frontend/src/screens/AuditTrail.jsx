// AuditTrail — Ingestion audit trail (admin). `vals` is the view model from useHoistra().
// The tiles, chip counts and day groups all describe the SAME filtered set the rows below
// them show; auditTrail.js is where that narrowing happens.
import React from 'react';

export default function AuditTrail({ vals }) {
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
          <span style={{ fontSize: "12px", color: "var(--color-neutral-500)" }}>{"Ingestion audit trail"}</span>
        </div>

        <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", flexWrap: "wrap", gap: "16px 24px", marginTop: "18px" }}>
          <div style={{ minWidth: "0", flex: "1 1 340px" }}>
            <div style={{ fontSize: "10.5px", letterSpacing: "0.13em", textTransform: "uppercase", color: "var(--color-accent)" }}>{"Administration · ingestion-validation"}</div>
            <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap", marginTop: "7px" }}>
              <h2 style={{ fontSize: "28px", margin: "0", lineHeight: "1.15" }}>{"Ingestion audit trail"}</h2>
              <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10px", letterSpacing: "0.09em", textTransform: "uppercase", padding: "3px 8px", borderRadius: "5px", background: "var(--color-accent)", color: "var(--accent-ink)" }}>{"Admin only"}</span>
            </div>
            <p style={{ fontSize: "13px", color: "var(--color-neutral-400)", margin: "8px 0 0", maxWidth: "88ch", lineHeight: "1.55" }}>
              {"Every flagged ingestion, clarification, override and approval — for users and admins alike. If a document is ever questioned, this record shows who submitted it, what warning was presented, what explanation was given, and who approved the final action."}
            </p>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap" }}>
            <span style={{ display: "inline-flex", alignItems: "center", gap: "7px", padding: "3px 10px", borderRadius: "20px", border: "1px solid var(--color-divider)", fontSize: "10.5px", color: "var(--color-neutral-500)" }}>
              <span style={{ width: "6px", height: "6px", borderRadius: "50%", background: vals.auLiveSourceDot }}></span>
              <span>{vals.auLiveSourceLabel}</span>
              <span className="hv11" onClick={vals.auLiveRetry} style={{ color: "var(--color-accent)", cursor: "pointer", display: vals.auLiveRetryShow }}>{"Retry"}</span>
            </span>
            {vals.canIngest ? (
              <div className="btn btn-primary" onClick={vals.ingStart} style={{ fontSize: "12px", padding: "7px 14px", cursor: "pointer", display: "flex", alignItems: "center", gap: "7px" }}>
                <i className="ph ph-play" style={{ fontSize: "12px" }}></i>
                <span>{"Run a sample ingestion"}</span>
              </div>
            ) : null}
            {[["In view", vals.auInView], ["Clean rate", vals.auCleanRate], ["Needs review", vals.auNeedsReview]].map((t, $i) => (
              <div key={$i} style={{ padding: "8px 13px", borderRadius: "9px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", display: "flex", flexDirection: "column", gap: "2px", whiteSpace: "nowrap", minWidth: "78px" }}>
                <span style={{ fontSize: "9.5px", letterSpacing: "0.11em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>{t[0]}</span>
                <span style={{ fontSize: "12.5px", fontVariantNumeric: "tabular-nums" }}>{t[1]}</span>
              </div>
            ))}
          </div>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap", marginTop: "20px" }}>
          <div style={{ position: "relative", flex: "1 1 320px", minWidth: "220px" }}>
            <i className="ph ph-magnifying-glass" style={{ position: "absolute", left: "11px", top: "50%", transform: "translateY(-50%)", fontSize: "13px", color: "var(--color-neutral-500)" }}></i>
            <input
              value={vals.auQuery}
              onChange={(e) => vals.auSetQuery(e.target.value)}
              placeholder="Search person, document or building"
              style={{ width: "100%", boxSizing: "border-box", fontSize: "12px", padding: "8px 12px 8px 31px", borderRadius: "8px", border: "1px solid var(--color-divider)", background: "var(--color-surface)", color: "inherit", outline: "none" }}
            />
          </div>
          <div style={{ position: "relative" }}>
            <div onClick={vals.auBldToggle} title={vals.auBuildingLabel} style={{ display: "inline-flex", alignItems: "center", gap: "7px", fontSize: "12px", padding: "8px 11px", borderRadius: "8px", border: "1px solid var(--color-divider)", background: "var(--color-surface)", cursor: "pointer", whiteSpace: "nowrap", maxWidth: "230px" }}>
              <i className="ph ph-buildings" style={{ fontSize: "13px", color: "var(--color-neutral-500)", flexShrink: "0" }}></i>
              <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{vals.auBuildingLabel}</span>
              {vals.auBuildingChip
                ? <i className="ph ph-x-circle hv11" onClick={(e) => { e.stopPropagation(); vals.auBuildingChip.clear(); }} style={{ fontSize: "13px", color: "var(--color-neutral-500)", cursor: "pointer", flexShrink: "0" }}></i>
                : <i className="ph ph-caret-down" style={{ fontSize: "11px", color: "var(--color-neutral-500)", flexShrink: "0" }}></i>}
            </div>
            {vals.auBldOpen ? (
              <React.Fragment>
                <div onClick={vals.auBldClose} style={{ position: "fixed", inset: "0", zIndex: "39" }}></div>
                <div style={{ position: "absolute", top: "calc(100% + 6px)", left: "0", zIndex: "40", width: "290px", borderRadius: "10px", background: "var(--color-surface)", border: "1px solid var(--color-divider)", boxShadow: "var(--shadow-lg, 0 10px 30px rgba(0,0,0,0.18))", overflow: "hidden" }}>
                  <div style={{ position: "relative", padding: "9px 10px", borderBottom: "1px solid var(--color-divider)" }}>
                    <i className="ph ph-magnifying-glass" style={{ position: "absolute", left: "19px", top: "50%", transform: "translateY(-50%)", fontSize: "12px", color: "var(--color-neutral-500)" }}></i>
                    <input
                      autoFocus
                      value={vals.auBldQuery}
                      onChange={(e) => vals.auSetBldQuery(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Escape") vals.auBldClose();
                        // Type three letters, press return: the name at the top is the one
                        // being reached for, and reaching for the mouse to confirm it is a
                        // step the keyboard already earned.
                        if (e.key === "Enter" && (vals.auBuildings || [])[0]) vals.auBuildings[0].pick();
                      }}
                      placeholder="Type a building or code…"
                      style={{ width: "100%", boxSizing: "border-box", fontSize: "12px", padding: "6px 8px 6px 22px", borderRadius: "6px", border: "1px solid var(--color-divider)", background: "var(--color-bg)", color: "inherit", outline: "none" }}
                    />
                  </div>
                  <div style={{ maxHeight: "268px", overflowY: "auto" }}>
                    <div className="hv2" onClick={vals.auBuildingAll.pick} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "10px", padding: "8px 12px", cursor: "pointer", borderBottom: "1px solid var(--color-divider)", background: vals.auBuildingAll.on ? "var(--color-bg)" : "transparent" }}>
                      <span style={{ fontSize: "12.5px", color: vals.auBuildingAll.on ? "var(--color-accent)" : "inherit" }}>{vals.auBuildingAll.label}</span>
                      {vals.auBuildingAll.on ? <i className="ph ph-check" style={{ fontSize: "12px", color: "var(--color-accent)" }}></i> : null}
                    </div>
                    {(vals.auBuildings || []).map((b, $i) => (
                      <div key={$i} className="hv2" onClick={b.pick} title={b.label} style={{ display: "grid", gridTemplateColumns: "1fr auto auto", gap: "10px", alignItems: "center", padding: "8px 12px", cursor: "pointer", background: b.on ? "var(--color-bg)" : "transparent" }}>
                        <span style={{ fontSize: "12.5px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: b.on ? "var(--color-accent)" : "inherit" }}>{b.label}</span>
                        <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10px", letterSpacing: "0.04em", color: "var(--color-neutral-500)" }}>{b.hint}</span>
                        <span style={{ fontSize: "11px", color: "var(--color-neutral-500)", width: "12px", textAlign: "right" }}>
                          {b.on ? <i className="ph ph-check" style={{ fontSize: "12px", color: "var(--color-accent)" }}></i> : ($i === 0 && vals.auBldQuery ? "\u21b5" : "")}
                        </span>
                      </div>
                    ))}
                    {!(vals.auBuildings || []).length ? (
                      <div style={{ padding: "16px 12px", fontSize: "12px", color: "var(--color-neutral-500)", textAlign: "center" }}>{"No building by that name or code."}</div>
                    ) : null}
                  </div>
                  <div style={{ padding: "7px 12px", borderTop: "1px solid var(--color-divider)", fontSize: "10.5px", color: "var(--color-neutral-500)", fontVariantNumeric: "tabular-nums" }}>{vals.auBuildingsLine}</div>
                </div>
              </React.Fragment>
            ) : null}
          </div>
          <div style={{ position: "relative" }}>
            <div onClick={vals.auPeopleToggle} style={{ display: "inline-flex", alignItems: "center", gap: "7px", fontSize: "12px", padding: "8px 11px", borderRadius: "8px", border: "1px solid var(--color-divider)", background: "var(--color-surface)", cursor: "pointer", whiteSpace: "nowrap" }}>
              <i className="ph ph-user" style={{ fontSize: "13px", color: "var(--color-neutral-500)" }}></i>
              <span>{vals.auPersonLabel}</span>
              {vals.auPersonChip
                ? <i className="ph ph-x-circle hv11" onClick={(e) => { e.stopPropagation(); vals.auPersonChip.clear(); }} style={{ fontSize: "13px", color: "var(--color-neutral-500)", cursor: "pointer" }}></i>
                : <i className="ph ph-caret-down" style={{ fontSize: "11px", color: "var(--color-neutral-500)" }}></i>}
            </div>
            {vals.auPeopleOpen ? (
              <div style={{ position: "absolute", top: "calc(100% + 6px)", left: "0", zIndex: "40", width: "268px", borderRadius: "10px", background: "var(--color-surface)", border: "1px solid var(--color-divider)", boxShadow: "var(--shadow-lg, 0 10px 30px rgba(0,0,0,0.18))", overflow: "hidden" }}>
                <div style={{ position: "relative", padding: "9px 10px", borderBottom: "1px solid var(--color-divider)" }}>
                  <i className="ph ph-magnifying-glass" style={{ position: "absolute", left: "19px", top: "50%", transform: "translateY(-50%)", fontSize: "12px", color: "var(--color-neutral-500)" }}></i>
                  <input
                    autoFocus
                    value={vals.auPeopleQuery}
                    onChange={(e) => vals.auSetPeopleQuery(e.target.value)}
                    placeholder="Type a name…"
                    style={{ width: "100%", boxSizing: "border-box", fontSize: "12px", padding: "6px 8px 6px 22px", borderRadius: "6px", border: "1px solid var(--color-divider)", background: "var(--color-bg)", color: "inherit", outline: "none" }}
                  />
                </div>
                <div style={{ display: "flex", gap: "4px", padding: "8px 10px", borderBottom: "1px solid var(--color-divider)" }}>
                  {(vals.auPeopleTabs || []).map((t, $i) => (
                    <div key={$i} onClick={t.pick} style={{ fontSize: "11.5px", padding: "4px 9px", borderRadius: "6px", cursor: "pointer", whiteSpace: "nowrap", color: t.on ? "var(--color-accent)" : "var(--color-neutral-400)", background: t.on ? "var(--color-bg)" : "transparent" }}>{t.label}</div>
                  ))}
                </div>
                <div style={{ maxHeight: "232px", overflowY: "auto" }}>
                  {(vals.auPeople || []).map((p, $i) => (
                    <div key={$i} className="hv2" onClick={p.pick} style={{ display: "grid", gridTemplateColumns: "1fr auto auto", gap: "10px", alignItems: "center", padding: "8px 12px", cursor: "pointer", background: p.on ? "var(--color-bg)" : "transparent" }}>
                      <span style={{ fontSize: "12.5px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{p.name}</span>
                      <span style={{ fontSize: "9.5px", letterSpacing: "0.09em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>{p.role}</span>
                      <span style={{ fontSize: "11px", color: "var(--color-neutral-500)", fontVariantNumeric: "tabular-nums" }}>{p.count}</span>
                    </div>
                  ))}
                  {!(vals.auPeople || []).length ? (
                    <div style={{ padding: "16px 12px", fontSize: "12px", color: "var(--color-neutral-500)", textAlign: "center" }}>{"No one by that name in this range."}</div>
                  ) : null}
                </div>
              </div>
            ) : null}
          </div>
          <div style={{ display: "flex", gap: "2px", padding: "2px", borderRadius: "8px", background: "var(--color-bg)", border: "1px solid var(--color-divider)" }}>
            {(vals.auRangePicks || []).map((r, $i) => (
              <div key={$i} onClick={r.pick} style={{ fontSize: "11.5px", padding: "5px 11px", borderRadius: "6px", cursor: "pointer", whiteSpace: "nowrap", background: r.on ? "var(--color-surface)" : "transparent", color: r.on ? "var(--color-accent)" : "var(--color-neutral-400)", boxShadow: r.on ? "var(--shadow-sm)" : "none" }}>{r.label}</div>
            ))}
          </div>
        </div>

        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "12px", flexWrap: "wrap", marginTop: "14px" }}>
          <div style={{ display: "flex", gap: "7px", flexWrap: "wrap" }}>
            {(vals.auFilters || []).map((f, $index) => (
              <div key={$index} onClick={f.pick} style={{ display: "inline-flex", alignItems: "center", gap: "6px", fontSize: "11.5px", padding: "5px 12px", borderRadius: "6px", background: f.bg, color: f.fg, cursor: "pointer", boxShadow: "var(--shadow-sm)" }}>
                <span>{f.label}</span>
                <span style={{ fontVariantNumeric: "tabular-nums", opacity: "0.72" }}>{f.count}</span>
              </div>
            ))}
          </div>
          <span style={{ fontSize: "11px", color: "var(--color-neutral-500)", fontVariantNumeric: "tabular-nums" }}>{vals.auShownLine}</span>
        </div>

        {(vals.auGroups || []).map((g, $g) => (
          <div key={$g} style={{ marginTop: "18px" }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "12px", flexWrap: "wrap", padding: "0 2px 8px" }}>
              <div style={{ display: "flex", alignItems: "center", gap: "9px" }}>
                <i className="ph ph-caret-down" style={{ fontSize: "11px", color: "var(--color-neutral-500)" }}></i>
                <span style={{ fontSize: "12.5px" }}>{g.label}</span>
                <span style={{ fontSize: "11px", color: "var(--color-neutral-500)", fontVariantNumeric: "tabular-nums" }}>{g.count}</span>
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: "12px", flexWrap: "wrap" }}>
                {(g.legend || []).map((l, $l) => (
                  <span key={$l} style={{ display: "inline-flex", alignItems: "center", gap: "5px", fontSize: "10.5px", color: "var(--color-neutral-500)" }}>
                    <span style={{ width: "5px", height: "5px", borderRadius: "50%", background: l.dot }}></span>
                    <span>{l.label}</span>
                  </span>
                ))}
              </div>
            </div>
            <div style={{ borderRadius: "11px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", overflow: "hidden" }}>
              {(g.rows || []).map((a, $index) => (
                <div key={$index} style={{ borderBottom: "1px solid var(--color-divider)" }}>
                  <div className="hv2" onClick={a.toggle} style={{ display: "grid", gridTemplateColumns: "62px minmax(140px,0.8fr) minmax(220px,1.6fr) minmax(150px,1fr) 118px 18px", gap: "12px", alignItems: "center", padding: "11px 16px", cursor: "pointer" }}>
                    <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10.5px", color: "var(--color-neutral-500)", whiteSpace: "nowrap" }}>{a.clock}</span>
                    <div style={{ minWidth: "0" }}>
                      <div className="hv11" onClick={a.pickPerson} style={{ fontSize: "12.5px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", cursor: "pointer" }}>{a.who}</div>
                      <div style={{ fontSize: "10.5px", color: "var(--color-neutral-500)", marginTop: "2px" }}>{a.role}</div>
                    </div>
                    <div style={{ fontSize: "12px", color: "var(--color-neutral-300)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{a.doc}</div>
                    <div style={{ fontSize: "11.5px", color: "var(--color-neutral-400)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{a.route}</div>
                    <span style={{ fontSize: "10.5px", padding: "3px 9px", borderRadius: "5px", background: a.bg, color: a.fg, textAlign: "center", whiteSpace: "nowrap" }}>{a.outcome}</span>
                    <i className={a.open ? "ph ph-caret-down" : "ph ph-caret-right"} style={{ fontSize: "12px", color: "var(--color-neutral-500)", justifySelf: "end" }}></i>
                  </div>
                  <div style={{ display: a.panelShow, gridTemplateColumns: "repeat(auto-fit,minmax(260px,1fr))", gap: "12px 24px", padding: "14px 16px 16px 50px", background: "var(--color-bg)", borderTop: "1px solid var(--color-divider)" }}>
                    {(a.fields || []).map((f, $index2) => (
                      <div key={$index2} style={{ minWidth: "0" }}>
                        <div style={{ fontSize: "9.5px", letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>{f.l}</div>
                        <div style={{ fontSize: "12px", color: "var(--color-neutral-300)", marginTop: "3px", lineHeight: "1.5" }}>{f.v}</div>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          </div>
        ))}

        {!(vals.auGroups || []).length ? (
          <div style={{ marginTop: "18px", padding: "34px 16px", borderRadius: "11px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", textAlign: "center", fontSize: "12.5px", color: "var(--color-neutral-500)" }}>
            {"Nothing in this range. Widen the dates, or clear a filter."}
          </div>
        ) : null}
      </div>
    </div>
  );
}
