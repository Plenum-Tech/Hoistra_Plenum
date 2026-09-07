// Compliance — compliance console
// Ported from the Hoistra prototype template. `vals` is the view model from useHoistra().
import React from 'react';

export default function Compliance({ vals }) {
  return (
      <div style={{ flex: "1", display: "flex", justifyContent: "flex-start", padding: "0 56px 80px" }}>
        <div style={{ width: "100%", maxWidth: "1400px", animation: "fadeUp 0.28s ease both" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "12px", padding: "24px 0 0" }}>
            <div className="hv6" onClick={vals.goHome} style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "12px", color: "var(--color-neutral-400)", cursor: "pointer" }}>
              <i className="ph ph-arrow-left" style={{ fontSize: "12px" }}></i>
              <span>
                {"Home"}
              </span>
            </div>
            <span style={{ fontSize: "12px", color: "var(--color-neutral-500)" }}>
              {"/"}
            </span>
            <span style={{ fontSize: "12px", color: "var(--color-neutral-500)" }}>
              {"Compliance"}
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
          <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", flexWrap: "wrap", gap: "16px 24px", marginTop: "18px" }}>
            <div style={{ minWidth: "0", flex: "1 1 300px" }}>
              <div style={{ fontSize: "10.5px", letterSpacing: "0.13em", textTransform: "uppercase", color: "var(--color-accent)" }}>
                {"Compliance certification centre"}
              </div>
              <h2 style={{ fontSize: "28px", margin: "7px 0 0", lineHeight: "1.15" }}>
                {"Compliance"}
              </h2>
              <p style={{ fontSize: "13px", color: "var(--color-neutral-400)", margin: "8px 0 0", maxWidth: "80ch", lineHeight: "1.55" }}>
                {"Every statutory obligation in the building's regulation pack, tracked against the certificate record and the accreditation currency of the vendor who has to do the work. Narrow the scope, then work the register."}
              </p>
            </div>
            <div style={{ display: "flex", alignItems: "center", flexWrap: "wrap", gap: "10px" }}>
              <div title={vals.ccSourceDetail} style={{ display: "inline-flex", alignItems: "center", gap: "7px", padding: "6px 11px", borderRadius: "20px", border: "1px solid var(--color-divider)", fontSize: "11px", color: "var(--color-neutral-400)", whiteSpace: "nowrap" }}>
                <span style={{ width: "7px", height: "7px", borderRadius: "50%", background: vals.ccSourceDot, flexShrink: "0" }}></span>
                <span>{vals.ccSourceLabel}</span>
                <span className="hv11" onClick={vals.ccRetry} style={{ color: "var(--color-accent)", cursor: "pointer", display: vals.ccRetryShow }}>{"Retry"}</span>
              </div>
              <div className="btn btn-primary" onClick={vals.ccScan} style={{ fontSize: "12px", padding: "7px 14px", cursor: "pointer" }}>
                {"Run compliance scan"}
              </div>
              <div style={{ padding: "8px 13px", borderRadius: "9px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", display: "flex", flexDirection: "column", gap: "2px", whiteSpace: "nowrap" }}>
                <span style={{ fontSize: "9.5px", letterSpacing: "0.11em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                  {"Last run"}
                </span>
                <span style={{ fontSize: "12.5px", fontVariantNumeric: "tabular-nums" }}>
                  {vals.ccLastRun}
                </span>
              </div>
            </div>
          </div>
          <div style={{ marginTop: "22px", borderRadius: "11px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", overflow: "hidden" }}>
            <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap", padding: "11px 14px" }}>
              <span style={{ fontSize: "10px", letterSpacing: "0.12em", textTransform: "uppercase", color: "var(--color-neutral-500)", flexShrink: "0" }}>
                {"Scope"}
              </span>
              {(vals.ccChips || []).map((c, $index) => (
                <React.Fragment key={$index}>
                  <span style={{ display: "inline-flex", alignItems: "center", gap: "6px", border: "1px solid var(--color-accent)", background: "var(--color-accent-900)", color: "var(--color-accent)", borderRadius: "20px", padding: "3px 9px 3px 11px", fontSize: "11.5px" }}>
                    <span>
                      {c.label}
                    </span>
                    <i className="ph ph-x hv21" onClick={c.drop} style={{ fontSize: "10px", cursor: "pointer", opacity: "0.6" }}></i>
                  </span>
                </React.Fragment>
              ))}
              <div className="hv4" onClick={vals.ccTogglePanel} style={{ border: "1px dashed var(--color-divider)", color: "var(--color-neutral-400)", borderRadius: "20px", padding: "3px 12px", fontSize: "11.5px", cursor: "pointer" }}>
                {vals.ccPanelLabel}
              </div>
              <div style={{ flex: "1" }}></div>
              <span style={{ fontSize: "11px", color: "var(--color-neutral-500)", fontVariantNumeric: "tabular-nums" }}>
                {vals.ccSummary}
              </span>
              <div className="hv11" onClick={vals.ccClear} style={{ fontSize: "11px", color: "var(--color-neutral-500)", cursor: "pointer" }}>
                {"Clear all"}
              </div>
            </div>
            {vals.ccPanelOpen ? (
              <>
                <div style={{ borderTop: "1px solid var(--color-divider)", background: "var(--color-bg)", display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(220px,1fr))" }}>
                  {(vals.ccCols || []).map((col, $index) => (
                    <React.Fragment key={$index}>
                      <div style={{ padding: "13px 15px", borderRight: "1px solid var(--color-divider)", minWidth: "0" }}>
                        <div style={{ fontSize: "10px", letterSpacing: "0.12em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                          {col.title}
                        </div>
                        <div style={{ fontSize: "11px", color: "var(--color-neutral-500)", margin: "3px 0 8px", lineHeight: "1.4" }}>
                          {col.hint}
                        </div>
                        <div style={{ display: "flex", flexDirection: "column", gap: "3px", maxHeight: "196px", overflowY: "auto" }}>
                          {(col.items || []).map((o, $index) => (
                            <React.Fragment key={$index}>
                              <div className="hv13" onClick={o.pick} style={{ display: "flex", alignItems: "center", gap: "9px", border: `1px solid ${o.border}`, background: o.bg, borderRadius: "7px", padding: "6px 8px", fontSize: "12px", cursor: "pointer", color: o.fg }}>
                                <span style={{ flexShrink: "0", width: "15px", height: "15px", border: `1.5px solid ${o.tickBorder}`, borderRadius: "4px", display: "flex", alignItems: "center", justifyContent: "center", fontSize: "9px", background: o.tickBg, color: o.tickFg }}>
                                  {"✓"}
                                </span>
                                <span style={{ flex: "1", minWidth: "0", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                  {o.label}
                                </span>
                                <span style={{ flexShrink: "0", fontFamily: "ui-monospace,monospace", fontSize: "10px", color: "var(--st-risk)", display: o.badShow }}>
                                  {o.bad}
                                </span>
                                <span style={{ flexShrink: "0", fontFamily: "ui-monospace,monospace", fontSize: "10px", color: "var(--color-neutral-500)" }}>
                                  {o.count}
                                </span>
                              </div>
                            </React.Fragment>
                          ))}
                          <span style={{ fontSize: "11px", color: "var(--color-neutral-500)", display: col.emptyShow }}>
                            {col.empty}
                          </span>
                        </div>
                      </div>
                    </React.Fragment>
                  ))}
                </div>
              </>
            ) : null}
          </div>
          <div style={{ marginTop: "14px", borderRadius: "11px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", overflow: "hidden" }}>
            <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap", padding: "11px 14px", borderBottom: "1px solid var(--color-divider)" }}>
              <span style={{ fontSize: "12.5px" }}>
                {"Needs you"}
              </span>
              <span style={{ fontSize: "11px", color: "var(--color-neutral-500)", flex: "1", minWidth: "180px" }}>
                {"click any card to work it · risk from the building's own record and from the vendors servicing it"}
              </span>
            </div>
            <div style={{ padding: "12px 12px 12px", display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(158px,1fr))", gap: "10px" }}>
              {(vals.ccTiles || []).map((t, $index) => (
                <React.Fragment key={$index}>
                  <div className="hv13" onClick={t.pick} style={{ padding: "12px 13px 12px 15px", borderRadius: "9px", background: t.bg, border: `1px solid ${t.edge}`, cursor: "pointer", position: "relative", overflow: "hidden", minWidth: "0" }}>
                    <div style={{ position: "absolute", left: "0", top: "10px", bottom: "10px", width: "3px", borderRadius: "0 3px 3px 0", background: t.color }}></div>
                    <div style={{ display: "flex", alignItems: "baseline", gap: "6px" }}>
                      <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "23px", lineHeight: "1.1", color: t.color }}>
                        {t.value}
                      </span>
                      <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "12px", color: t.color, display: t.markShow }}>
                        {t.mark}
                      </span>
                    </div>
                    <div style={{ fontSize: "10px", letterSpacing: "0.07em", textTransform: "uppercase", color: "var(--color-neutral-300)", marginTop: "3px" }}>
                      {t.label}
                    </div>
                    <div style={{ fontSize: "10.5px", color: "var(--color-neutral-500)", marginTop: "1px", lineHeight: "1.35" }}>
                      {t.hint}
                    </div>
                  </div>
                </React.Fragment>
              ))}
            </div>
          </div>
          {vals.ccFocused ? (
            <>
              <div style={{ marginTop: "22px" }}>
                <div className="hv6" onClick={vals.closeCCQueue} style={{ display: "inline-flex", alignItems: "center", gap: "7px", fontSize: "12px", color: "var(--color-neutral-400)", cursor: "pointer" }}>
                  <i className="ph ph-arrow-left" style={{ fontSize: "12px" }}></i>
                  <span>
                    {"Back to compliance overview"}
                  </span>
                </div>
                <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", flexWrap: "wrap", gap: "12px 20px", marginTop: "14px" }}>
                  <div style={{ minWidth: "0", flex: "1 1 320px" }}>
                    <div style={{ fontSize: "10px", letterSpacing: "0.12em", textTransform: "uppercase", color: "var(--color-accent)" }}>
                      {"Pending · "}{vals.qCount}
                    </div>
                    <div style={{ fontSize: "22px", marginTop: "6px", lineHeight: "1.2" }}>
                      {vals.qTitle}
                    </div>
                    <div style={{ fontSize: "12.5px", color: "var(--color-neutral-400)", marginTop: "7px", lineHeight: "1.55", maxWidth: "82ch" }}>
                      {vals.qHint}
                    </div>
                  </div>
                  <div style={{ fontSize: "11px", color: "var(--color-neutral-500)", whiteSpace: "nowrap" }}>
                    {vals.ccScopeNote}
                  </div>
                </div>
                {vals.qModeBuildings ? (
                  <>
                    <div style={{ display: "flex", flexDirection: "column", gap: "8px", marginTop: "18px", maxWidth: "1000px" }}>
                      {(vals.qBuildingRows || []).map((b, $index) => (
                        <React.Fragment key={$index}>
                          <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1.4fr) 120px minmax(0,1fr) auto", gap: "14px", alignItems: "center", padding: "13px 16px", borderRadius: "10px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)" }}>
                            <div style={{ minWidth: "0" }}>
                              <div className="hv6" onClick={b.open} style={{ fontSize: "13.5px", cursor: "pointer" }}>
                                {b.name}
                              </div>
                              <div style={{ fontSize: "11px", color: "var(--color-neutral-500)", marginTop: "3px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                {b.meta}
                              </div>
                            </div>
                            <div style={{ display: "flex", flexDirection: "column", gap: "3px" }}>
                              <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: "6px" }}>
                                <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "12.5px", color: b.covColor }}>
                                  {b.cov}
                                </span>
                                <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10px", color: "var(--color-neutral-500)" }}>
                                  {b.frac}
                                </span>
                              </div>
                              <div style={{ height: "5px", borderRadius: "3px", background: "var(--color-neutral-900)", overflow: "hidden" }}>
                                <div style={{ height: "100%", borderRadius: "3px", width: b.cov, background: b.covColor }}></div>
                              </div>
                            </div>
                            <span style={{ fontSize: "11.5px", color: b.noteColor, minWidth: "0", overflow: "hidden", textOverflow: "ellipsis" }}>
                              {b.note}
                            </span>
                            <span className="hv15" onClick={b.act} style={{ fontSize: "11px", padding: "6px 12px", borderRadius: "7px", border: "1px solid var(--color-accent)", color: "var(--color-accent)", cursor: "pointer", whiteSpace: "nowrap", flexShrink: "0" }}>
                              {b.actLabel}
                            </span>
                          </div>
                        </React.Fragment>
                      ))}
                    </div>
                  </>
                ) : null}
                {vals.qModeGaps ? (
                  <>
                    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(300px,1fr))", gap: "7px", marginTop: "18px", maxWidth: "1000px" }}>
                      {(vals.qGapRows || []).map((g, $index) => (
                        <React.Fragment key={$index}>
                          <div style={{ display: "flex", alignItems: "center", gap: "9px", padding: "9px 12px", borderRadius: "8px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)" }}>
                            <span style={{ width: "5px", height: "5px", borderRadius: "50%", background: "var(--st-risk)", flexShrink: "0" }}></span>
                            <div style={{ flex: "1", minWidth: "0" }}>
                              <div style={{ fontSize: "12px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                {g.label}
                              </div>
                              <div style={{ fontSize: "10.5px", color: "var(--color-neutral-500)", marginTop: "1px" }}>
                                {g.building}{" "}
                                <span style={{ color: "var(--st-risk)", display: g.moreShow }}>
                                  {"· "}{g.more}
                                </span>
                              </div>
                            </div>
                            <span onClick={g.act} style={{ fontSize: "10.5px", color: "var(--color-accent)", cursor: "pointer", whiteSpace: "nowrap", flexShrink: "0" }}>
                              {"Upload →"}
                            </span>
                          </div>
                        </React.Fragment>
                      ))}
                    </div>
                  </>
                ) : null}
                {vals.qModeCerts ? (
                  <>
                    <div style={{ display: "flex", flexDirection: "column", gap: "8px", marginTop: "18px", maxWidth: "1000px" }}>
                      {(vals.qItems || []).map((q, $index) => (
                        <React.Fragment key={$index}>
                          <div style={{ borderRadius: "10px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", overflow: "hidden" }}>
                            <div className="hv2" onClick={q.toggle} style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) auto auto", gap: "14px", alignItems: "center", padding: "13px 16px", cursor: "pointer" }}>
                              <div style={{ minWidth: "0" }}>
                                <div style={{ fontSize: "13.5px", lineHeight: "1.35" }}>
                                  {q.nm}
                                </div>
                                <div style={{ fontSize: "11.5px", color: "var(--color-neutral-400)", marginTop: "3px" }}>
                                  {q.holder}{" · "}{q.kind}
                                </div>
                                <div style={{ fontSize: "10.5px", color: "var(--color-neutral-500)", marginTop: "2px" }}>
                                  {q.meta}
                                </div>
                              </div>
                              <div style={{ display: "flex", gap: "6px", alignItems: "center", flexShrink: "0" }}>
                                <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10px", borderRadius: "5px", padding: "2px 7px", background: q.riskBg, color: q.riskFg, whiteSpace: "nowrap" }}>
                                  {q.risk}
                                </span>
                                <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10px", borderRadius: "5px", padding: "2px 7px", background: q.authBg, color: q.authFg, whiteSpace: "nowrap" }}>
                                  {q.auth}
                                </span>
                              </div>
                              <i className={`ph ${q.caret}`} style={{ fontSize: "13px", color: "var(--color-neutral-500)", flexShrink: "0" }}></i>
                            </div>
                            <div style={{ display: q.actShow, gap: "7px", flexWrap: "wrap", padding: "0 16px 14px", alignItems: "center" }}>
                              <span style={{ fontSize: "10px", letterSpacing: "0.11em", textTransform: "uppercase", color: "var(--color-neutral-500)", marginRight: "3px" }}>
                                {"Act on this"}
                              </span>
                              {(q.actions || []).map((a, $index) => (
                                <React.Fragment key={$index}>
                                  <div className="hv4" onClick={a.run} style={{ fontSize: "11.5px", padding: "6px 12px", borderRadius: "7px", border: `1px solid ${a.border}`, background: a.bg, color: a.fg, cursor: "pointer", whiteSpace: "nowrap" }}>
                                    {a.label}
                                  </div>
                                </React.Fragment>
                              ))}
                            </div>
                          </div>
                        </React.Fragment>
                      ))}
                      <div style={{ padding: "22px 2px", fontSize: "12px", color: "var(--color-neutral-500)", display: vals.qEmpty }}>
                        {"Nothing in this bucket for the current scope."}
                      </div>
                    </div>
                  </>
                ) : null}
              </div>
            </>
          ) : null}
          {vals.ccBrowse ? (
            <>
              <div style={{ marginTop: "14px", borderRadius: "11px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", overflow: "hidden" }}>
                <div style={{ display: "flex", alignItems: "baseline", gap: "10px", flexWrap: "wrap", padding: "11px 14px", borderBottom: "1px solid var(--color-divider)" }}>
                  <span style={{ fontSize: "12.5px" }}>
                    {"Expiry runway"}
                  </span>
                  <span style={{ fontSize: "11px", color: "var(--color-neutral-500)" }}>
                    {"every certificate in scope · hover a marker"}
                  </span>
                </div>
                <div style={{ padding: "22px 16px 8px" }}>
                  <div style={{ position: "relative", height: "62px" }}>
                    <div style={{ position: "absolute", left: "0", top: "20px", bottom: "12px", width: "14%", background: "var(--st-risk-bg)", borderRight: "1px dashed var(--st-risk)", borderRadius: "3px 0 0 3px" }}></div>
                    <div style={{ position: "absolute", left: "14%", width: "12%", top: "20px", bottom: "12px", background: "var(--st-warn-bg)", opacity: "0.7" }}></div>
                    <div style={{ position: "absolute", left: "0", right: "0", top: "31px", height: "1px", background: "var(--color-divider)" }}></div>
                    <div style={{ position: "absolute", left: "14%", top: "14px", bottom: "6px", width: "1.5px", background: "var(--color-accent)" }}></div>
                    <span style={{ position: "absolute", left: "14%", top: "0", transform: "translateX(-50%)", fontSize: "9px", letterSpacing: "0.06em", textTransform: "uppercase", color: "var(--color-accent)" }}>
                      {"today"}
                    </span>
                    {(vals.ccPins || []).map((p, $index) => (
                      <React.Fragment key={$index}>
                        <div className="hv22" title={p.tip} onClick={p.click} style={{ position: "absolute", left: p.pos, top: p.top, width: p.size, height: p.size, borderRadius: "50%", border: p.border, background: p.bg, boxShadow: "0 0 0 1.5px var(--color-surface)", transform: "translateX(-50%)", cursor: "pointer" }}></div>
                      </React.Fragment>
                    ))}
                  </div>
                  <div style={{ display: "flex", justifyContent: "space-between", marginTop: "2px" }}>
                    {(vals.ccTicks || []).map((t, $index) => (
                      <React.Fragment key={$index}>
                        <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10px", color: t.color }}>
                          {t.label}
                        </span>
                      </React.Fragment>
                    ))}
                  </div>
                </div>
                <div style={{ display: "flex", gap: "14px", flexWrap: "wrap", padding: "6px 16px 13px" }}>
                  {(vals.ccLegend || []).map((l, $index) => (
                    <React.Fragment key={$index}>
                      <span style={{ display: "inline-flex", alignItems: "center", gap: "5px", fontSize: "11px", color: "var(--color-neutral-500)" }}>
                        <span style={{ width: "9px", height: "9px", borderRadius: "50%", background: l.bg, border: l.border, flexShrink: "0" }}></span>
                        <span>
                          {l.label}
                        </span>
                      </span>
                    </React.Fragment>
                  ))}
                </div>
              </div>
              <div style={{ display: "grid", gridTemplateColumns: vals.ccCols2, gap: "14px", marginTop: "14px", alignItems: "start" }}>
                <div style={{ borderRadius: "11px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", overflow: "hidden", minWidth: "0" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap", padding: "10px 14px", borderBottom: "1px solid var(--color-divider)" }}>
                    <span style={{ fontSize: "12.5px", flex: "1" }}>
                      {vals.ccListTitle}
                    </span>
                    <div style={{ display: "inline-flex", background: "var(--color-bg)", border: "1px solid var(--color-divider)", borderRadius: "8px", padding: "2px" }}>
                      {(vals.ccPivots || []).map((p, $index) => (
                        <React.Fragment key={$index}>
                          <div onClick={p.pick} style={{ borderRadius: "6px", padding: "4px 11px", fontSize: "11.5px", cursor: "pointer", background: p.bg, color: p.fg }}>
                            {p.label}
                          </div>
                        </React.Fragment>
                      ))}
                    </div>
                  </div>
                  {vals.ccIsMatrix ? (
                    <>
                      <div style={{ overflow: "auto", maxHeight: "520px" }}>
                        <div style={{ display: "grid", gridTemplateColumns: vals.ccMxCols, minWidth: "min-content" }}>
                          <div style={{ position: "sticky", left: "0", top: "0", zIndex: "5", background: "var(--color-bg)", padding: "8px 11px", fontSize: "9.5px", letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--color-neutral-500)", borderBottom: "1px solid var(--color-divider)" }}>
                            {"Building"}
                          </div>
                          {(vals.ccMxHead || []).map((h, $index) => (
                            <React.Fragment key={$index}>
                              <div title={h.tip} style={{ position: "sticky", top: "0", zIndex: "4", background: "var(--color-bg)", padding: "8px 4px", textAlign: "center", fontSize: "9px", letterSpacing: "0.04em", color: "var(--color-neutral-400)", borderBottom: "1px solid var(--color-divider)", whiteSpace: "nowrap" }}>
                                {h.label}
                              </div>
                            </React.Fragment>
                          ))}
                          {(vals.ccMxRows || []).map((r, $index) => (
                            <React.Fragment key={$index}>
                              <div onClick={r.click} style={{ position: "sticky", left: "0", zIndex: "3", background: "var(--color-surface)", padding: "7px 11px", borderBottom: "1px solid var(--color-divider)", cursor: "pointer", minWidth: "0" }}>
                                <div style={{ fontSize: "12px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                  {r.name}
                                </div>
                                <div style={{ fontSize: "10.5px", color: "var(--color-neutral-500)" }}>
                                  {r.meta}
                                </div>
                              </div>
                              {(r.cells || []).map((c, $index) => (
                                <React.Fragment key={$index}>
                                  <div style={{ display: "flex", alignItems: "center", justifyContent: "center", borderBottom: "1px solid var(--color-divider)", borderRight: "1px solid var(--color-divider)", minHeight: "38px" }}>
                                    <span title={c.tip} style={{ width: "22px", height: "22px", borderRadius: "6px", display: "flex", alignItems: "center", justifyContent: "center", fontFamily: "ui-monospace,monospace", fontSize: "11px", background: c.bg, color: c.fg, border: c.border }}>
                                      {c.glyph}
                                    </span>
                                  </div>
                                </React.Fragment>
                              ))}
                            </React.Fragment>
                          ))}
                        </div>
                      </div>
                      <div style={{ display: "flex", gap: "14px", flexWrap: "wrap", padding: "11px 14px", borderTop: "1px solid var(--color-divider)" }}>
                        {(vals.ccMxLegend || []).map((l, $index) => (
                          <React.Fragment key={$index}>
                            <span style={{ display: "inline-flex", alignItems: "center", gap: "6px", fontSize: "10.5px", color: "var(--color-neutral-500)" }}>
                              <span style={{ width: "17px", height: "17px", borderRadius: "5px", display: "flex", alignItems: "center", justifyContent: "center", fontFamily: "ui-monospace,monospace", fontSize: "9.5px", background: l.bg, color: l.fg, border: l.border }}>
                                {l.glyph}
                              </span>
                              <span>
                                {l.label}
                              </span>
                            </span>
                          </React.Fragment>
                        ))}
                      </div>
                    </>
                  ) : null}
                  {vals.ccIsList ? (
                    <>
                      <div style={{ maxHeight: "560px", overflowY: "auto" }}>
                        {(vals.ccRows || []).map((r, $index) => (
                          <React.Fragment key={$index}>
                            <div className="hv2" onClick={r.click} style={{ position: "relative", display: "grid", gridTemplateColumns: "minmax(0,1fr) 92px auto", alignItems: "center", gap: "12px", padding: "10px 13px 10px 16px", borderBottom: "1px solid var(--color-divider)", cursor: "pointer", background: r.rowBg }}>
                              <div style={{ position: "absolute", left: "0", top: "0", bottom: "0", width: "3px", background: r.edge }}></div>
                              <div style={{ minWidth: "0" }}>
                                <div style={{ fontSize: "13px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                  {r.name}
                                </div>
                                <div style={{ fontSize: "11px", color: "var(--color-neutral-500)", marginTop: "2px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                  {r.meta}
                                </div>
                                <div style={{ fontSize: "10.5px", color: "var(--st-risk)", marginTop: "2px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", display: r.gapShow }}>
                                  {r.gap}
                                </div>
                              </div>
                              <div style={{ display: "flex", flexDirection: "column", gap: "3px" }}>
                                <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: "6px" }}>
                                  <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "12.5px", color: r.covColor }}>
                                    {r.cov}
                                  </span>
                                  <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10px", color: "var(--color-neutral-500)" }}>
                                    {r.frac}
                                  </span>
                                </div>
                                <div style={{ height: "5px", borderRadius: "3px", background: "var(--color-neutral-900)", overflow: "hidden" }}>
                                  <div style={{ height: "100%", borderRadius: "3px", width: r.cov, background: r.covColor }}></div>
                                </div>
                              </div>
                              <div style={{ display: "flex", gap: "5px", alignItems: "center", flexShrink: "0" }}>
                                {(r.tags || []).map((g, $index) => (
                                  <React.Fragment key={$index}>
                                    <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10px", borderRadius: "5px", padding: "2px 6px", whiteSpace: "nowrap", background: g.bg, color: g.fg }}>
                                      {g.label}
                                    </span>
                                  </React.Fragment>
                                ))}
                              </div>
                            </div>
                          </React.Fragment>
                        ))}
                      </div>
                    </>
                  ) : null}
                </div>
                {vals.ccIsList ? (
                  <>
                    <div style={{ borderRadius: "11px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", overflow: "hidden", minWidth: "0" }}>
                      <div style={{ padding: "13px 15px", borderBottom: "1px solid var(--color-divider)" }}>
                        <div style={{ fontSize: "11px", color: "var(--color-neutral-500)" }}>
                          {vals.ccCrumb}
                        </div>
                        <div style={{ fontSize: "17px", marginTop: "4px", lineHeight: "1.2" }}>
                          {vals.ccFocusName}
                        </div>
                        <div style={{ display: "flex", gap: "18px", flexWrap: "wrap", marginTop: "11px" }}>
                          {(vals.ccFacts || []).map((f, $index) => (
                            <React.Fragment key={$index}>
                              <div>
                                <div style={{ fontSize: "9.5px", letterSpacing: "0.09em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                                  {f.label}
                                </div>
                                <div style={{ fontFamily: "ui-monospace,monospace", fontSize: "14px", marginTop: "1px", color: f.color }}>
                                  {f.value}
                                </div>
                              </div>
                            </React.Fragment>
                          ))}
                        </div>
                      </div>
                      <div style={{ display: "flex", gap: "2px", padding: "0 15px", borderBottom: "1px solid var(--color-divider)", overflowX: "auto" }}>
                        {(vals.ccTabs || []).map((t, $index) => (
                          <React.Fragment key={$index}>
                            <div onClick={t.pick} style={{ padding: "9px 10px", fontSize: "12px", cursor: "pointer", whiteSpace: "nowrap", borderBottom: "2px solid transparent", borderBottomColor: t.edge, color: t.fg }}>
                              <span>
                                {t.label}
                              </span>
                              <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10.5px", opacity: "0.7", marginLeft: "4px" }}>
                                {t.n}
                              </span>
                            </div>
                          </React.Fragment>
                        ))}
                      </div>
                      {vals.ccPaneCerts ? (
                        <>
                          <div style={{ overflowX: "auto" }}>
                            <div style={{ minWidth: "560px" }}>
                              <div style={{ display: "grid", gridTemplateColumns: "1.8fr 0.9fr 0.7fr 0.8fr 0.8fr auto", gap: "10px", padding: "8px 13px", background: "var(--color-bg)", borderBottom: "1px solid var(--color-divider)", fontSize: "9.5px", letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                                <span>
                                  {"Certificate"}
                                </span>
                                <span>
                                  {"Expiry"}
                                </span>
                                <span>
                                  {"Risk"}
                                </span>
                                <span>
                                  {"Authenticity"}
                                </span>
                                <span>
                                  {"Verification"}
                                </span>
                                <span>
                                  {"Action"}
                                </span>
                              </div>
                              {(vals.ccCertRows || []).map((c, $index) => (
                                <React.Fragment key={$index}>
                                  <div style={{ display: "grid", gridTemplateColumns: "1.8fr 0.9fr 0.7fr 0.8fr 0.8fr auto", gap: "10px", padding: "9px 13px", borderBottom: "1px solid var(--color-divider)", fontSize: "12px", alignItems: "center" }}>
                                    <span style={{ minWidth: "0", overflow: "hidden", textOverflow: "ellipsis" }}>
                                      {c.nm}
                                    </span>
                                    <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "11px", whiteSpace: "nowrap" }}>
                                      {c.exp}
                                      <span style={{ display: "block", fontSize: "10px", color: "var(--color-neutral-500)" }}>
                                        {c.rel}
                                      </span>
                                    </span>
                                    <span>
                                      <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10px", borderRadius: "5px", padding: "2px 6px", background: c.riskBg, color: c.riskFg }}>
                                        {c.risk}
                                      </span>
                                    </span>
                                    <span>
                                      <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10px", borderRadius: "5px", padding: "2px 6px", background: c.authBg, color: c.authFg }}>
                                        {c.auth}
                                      </span>
                                    </span>
                                    <span style={{ fontSize: "10.5px", color: "var(--color-neutral-500)", minWidth: "0", overflow: "hidden", textOverflow: "ellipsis" }}>
                                      {c.ver}
                                    </span>
                                    <span className="hv23" onClick={c.act} style={{ fontSize: "10.5px", padding: "4px 10px", borderRadius: "6px", border: `1px solid ${c.actBorder}`, color: c.actFg, cursor: "pointer", whiteSpace: "nowrap" }}>
                                      {c.actLabel}
                                    </span>
                                  </div>
                                </React.Fragment>
                              ))}
                              <div style={{ padding: "14px", fontSize: "11.5px", color: "var(--color-neutral-500)", display: vals.ccCertEmpty }}>
                                {"Nothing on file in this scope."}
                              </div>
                            </div>
                          </div>
                        </>
                      ) : null}
                      {vals.ccPaneServed ? (
                        <>
                          <div style={{ overflowX: "auto" }}>
                            <div style={{ minWidth: "520px" }}>
                              <div style={{ display: "grid", gridTemplateColumns: "1.4fr 1fr 1fr 0.7fr auto", gap: "10px", padding: "8px 13px", background: "var(--color-bg)", borderBottom: "1px solid var(--color-divider)", fontSize: "9.5px", letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                                <span>
                                  {"Building"}
                                </span>
                                <span>
                                  {"Region"}
                                </span>
                                <span>
                                  {"Building coverage"}
                                </span>
                                <span>
                                  {"Certs"}
                                </span>
                                <span></span>
                              </div>
                              {(vals.ccServedRows || []).map((b, $index) => (
                                <React.Fragment key={$index}>
                                  <div style={{ display: "grid", gridTemplateColumns: "1.4fr 1fr 1fr 0.7fr auto", gap: "10px", padding: "9px 13px", borderBottom: "1px solid var(--color-divider)", fontSize: "12px", alignItems: "center" }}>
                                    <span style={{ minWidth: "0", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                      {b.name}
                                    </span>
                                    <span style={{ fontSize: "11px", color: "var(--color-neutral-400)", minWidth: "0", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                      {b.state}
                                    </span>
                                    <div style={{ display: "flex", alignItems: "center", gap: "7px" }}>
                                      <div style={{ flex: "1", height: "4px", borderRadius: "2px", background: "var(--color-neutral-900)", overflow: "hidden" }}>
                                        <div style={{ height: "100%", borderRadius: "2px", width: b.cov, background: b.covColor }}></div>
                                      </div>
                                      <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "11px", color: b.covColor }}>
                                        {b.cov}
                                      </span>
                                    </div>
                                    <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "11px" }}>
                                      {b.certs}
                                    </span>
                                    <span onClick={b.open} style={{ fontSize: "10.5px", color: "var(--color-accent)", cursor: "pointer", whiteSpace: "nowrap" }}>
                                      {"View building →"}
                                    </span>
                                  </div>
                                </React.Fragment>
                              ))}
                              <div style={{ padding: "14px", fontSize: "11.5px", color: "var(--color-neutral-500)", display: vals.ccServedEmpty }}>
                                {"Portfolio-wide accreditation — this vendor is not named on a building certificate, so it can serve any site in its region."}
                              </div>
                            </div>
                          </div>
                        </>
                      ) : null}
                      {vals.ccPaneVendors ? (
                        <>
                          <div style={{ padding: "11px 15px 0", fontSize: "11px", color: "var(--color-neutral-500)", lineHeight: "1.5" }}>
                            {"Coverage is the required accreditation types each vendor holds on file. A blocked vendor cannot be allocated regulated work here, whatever the building's own record says."}
                          </div>
                          <div style={{ overflowX: "auto", marginTop: "8px" }}>
                            <div style={{ minWidth: "560px" }}>
                              <div style={{ display: "grid", gridTemplateColumns: "1.5fr 1fr 0.9fr 1.4fr 0.7fr", gap: "10px", padding: "8px 13px", background: "var(--color-bg)", borderBottom: "1px solid var(--color-divider)", fontSize: "9.5px", letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                                <span>
                                  {"Vendor"}
                                </span>
                                <span>
                                  {"Serves"}
                                </span>
                                <span>
                                  {"Coverage"}
                                </span>
                                <span>
                                  {"Pending types"}
                                </span>
                                <span>
                                  {"Status"}
                                </span>
                              </div>
                              {(vals.ccVendorRows || []).map((v, $index) => (
                                <React.Fragment key={$index}>
                                  <div style={{ display: "grid", gridTemplateColumns: "1.5fr 1fr 0.9fr 1.4fr 0.7fr", gap: "10px", padding: "9px 13px", borderBottom: "1px solid var(--color-divider)", fontSize: "12px", alignItems: "start" }}>
                                    <div style={{ minWidth: "0" }}>
                                      <div style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                        {v.name}
                                      </div>
                                      <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10px", borderRadius: "5px", padding: "1px 6px", background: v.bg, color: v.fg, display: "inline-block", marginTop: "3px" }}>
                                        {v.worst}
                                      </span>
                                    </div>
                                    <span style={{ fontSize: "11px", color: "var(--color-neutral-400)", minWidth: "0" }}>
                                      {v.serves}
                                    </span>
                                    <div style={{ display: "flex", flexDirection: "column", gap: "3px" }}>
                                      <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "11.5px", color: v.covColor }}>
                                        {v.cov}{" "}
                                        <span style={{ color: "var(--color-neutral-500)", fontSize: "10px" }}>
                                          {v.frac}
                                        </span>
                                      </span>
                                      <div style={{ height: "4px", borderRadius: "2px", background: "var(--color-neutral-900)", overflow: "hidden" }}>
                                        <div style={{ height: "100%", borderRadius: "2px", width: v.cov, background: v.covColor }}></div>
                                      </div>
                                    </div>
                                    <div style={{ fontSize: "10.5px", color: "var(--color-neutral-400)", lineHeight: "1.5", minWidth: "0" }}>
                                      {v.gaps}
                                    </div>
                                    <span>
                                      <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10px", borderRadius: "5px", padding: "2px 6px", background: v.blockBg, color: v.blockFg, whiteSpace: "nowrap" }}>
                                        {v.block}
                                      </span>
                                    </span>
                                  </div>
                                </React.Fragment>
                              ))}
                              <div style={{ padding: "14px", fontSize: "11.5px", color: "var(--color-neutral-500)", display: vals.ccVendorEmpty }}>
                                {"No vendor is named on this selection's certificates yet."}
                              </div>
                            </div>
                          </div>
                        </>
                      ) : null}
                      {vals.ccPaneGaps ? (
                        <>
                          <div style={{ padding: "13px 15px" }}>
                            <div style={{ fontSize: "11px", color: "var(--color-neutral-500)", lineHeight: "1.5" }}>
                              {"Required here, nothing filed. Never merged with types the pack does not require — merging inflates every denominator."}
                            </div>
                            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(240px,1fr))", gap: "6px", marginTop: "10px" }}>
                              {(vals.ccGapRows || []).map((g, $index) => (
                                <React.Fragment key={$index}>
                                  <div style={{ display: "flex", alignItems: "center", gap: "8px", padding: "7px 10px", border: "1px solid var(--color-divider)", borderRadius: "7px", fontSize: "11.5px", background: "var(--color-bg)" }}>
                                    <span style={{ width: "5px", height: "5px", borderRadius: "50%", background: "var(--st-risk)", flexShrink: "0" }}></span>
                                    <span style={{ flex: "1", minWidth: "0", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                      {g.label}
                                    </span>
                                    <span onClick={g.act} style={{ fontSize: "10.5px", color: "var(--color-accent)", cursor: "pointer", whiteSpace: "nowrap" }}>
                                      {g.action}
                                    </span>
                                  </div>
                                </React.Fragment>
                              ))}
                            </div>
                          </div>
                        </>
                      ) : null}
                    </div>
                  </>
                ) : null}
              </div>
            </>
          ) : null}
        </div>
      </div>
  );
}
