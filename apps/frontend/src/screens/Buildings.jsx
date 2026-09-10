// Buildings — Hoist Graph, role-driven
// Ported from the Hoistra prototype template. `vals` is the view model from useHoistra().
import React from 'react';

export default function Buildings({ vals }) {
  return (
      <div style={{ flex: "1", display: "flex", justifyContent: "flex-start", padding: "0 32px 80px" }}>
        {/* No max-width: the table below is wide (1620px min) and wants the room a
            collapsed navigator or a closed dock frees up, rather than sitting capped
            with columns truncated either way. */}
        <div style={{ width: "100%", animation: "fadeUp 0.28s ease both" }}>
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
              {"Reports"}
            </span>
          </div>
          {vals.isUser ? (
            <>
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
            </>
          ) : null}
          <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", gap: "24px", marginTop: "18px" }}>
            <div style={{ display: "flex", flexDirection: "column", gap: "7px" }}>
              <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap" }}>
                <h1 style={{ fontSize: "31px", margin: "0", lineHeight: "1.1" }}>
                  {"Buildings"}
                </h1>
                <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10px", letterSpacing: "0.09em", textTransform: "uppercase", padding: "3px 8px", borderRadius: "5px", background: vals.roleBg, color: vals.roleFg, whiteSpace: "nowrap" }}>
                  {vals.roleLabel}
                </span>
              </div>
              <div style={{ fontSize: "13px", color: "var(--color-accent)" }}>
                {vals.bldKicker}
              </div>
              <div title={vals.bldSourceDetail} style={{ display: "inline-flex", alignItems: "center", gap: "7px", padding: "5px 11px", borderRadius: "20px", border: "1px solid var(--color-divider)", fontSize: "11px", color: "var(--color-neutral-400)", whiteSpace: "nowrap", alignSelf: "flex-start" }}>
                <span style={{ width: "7px", height: "7px", borderRadius: "50%", background: vals.bldSourceDot, flexShrink: "0" }}></span>
                <span>{vals.bldSourceLabel}</span>
                <span className="hv11" onClick={vals.bldRetry} style={{ color: "var(--color-accent)", cursor: "pointer", display: vals.bldRetryShow }}>{"Retry"}</span>
              </div>
            </div>
            {vals.bcCanHoist ? (
              <>
                <div className="btn" onClick={vals.ingestDocuments} style={{ fontSize: "12px", padding: "7px 13px", cursor: "pointer", flexShrink: "0" }}>
                  {"Ingest documents"}
                </div>
                <div className="btn btn-primary" onClick={vals.addBuilding} style={{ fontSize: "12px", padding: "7px 13px", cursor: "pointer", flexShrink: "0" }}>
                  {"Hoist a building"}
                </div>
              </>
            ) : null}
          </div>
          <div style={{ display: vals.bldQueryShow, alignItems: "center", gap: "10px", padding: "9px 13px", borderRadius: "10px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", marginTop: "18px", maxWidth: "420px" }}>
            <i className="ph ph-magnifying-glass" style={{ fontSize: "14px", color: "var(--color-neutral-500)", flexShrink: "0" }}></i>
            <input className="input" value={vals.bldQuery} onChange={vals.setBldQuery} placeholder="Search by name, building ID, country or state…" style={{ flex: "1", minWidth: "0", background: "transparent", border: "none", outline: "none", fontFamily: "var(--font-body)", fontSize: "13px", color: "var(--color-text)" }} />
            {vals.bldQuery ? (
              <i className="ph ph-x hv11" onClick={() => vals.setBldQuery({ target: { value: "" } })} style={{ fontSize: "13px", color: "var(--color-neutral-500)", cursor: "pointer", flexShrink: "0" }}></i>
            ) : null}
          </div>
          <div style={{ marginTop: "16px", borderRadius: "12px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", overflowX: "auto", overflowY: "hidden" }}>
            <div style={{ minWidth: "1620px" }}>
              <div style={{ display: "grid", gridTemplateColumns: "76px minmax(148px,1.2fr) 122px minmax(108px,1fr) minmax(148px,1.4fr) 54px 92px minmax(200px,1.2fr) 96px 104px minmax(240px,1.6fr) 72px", gap: "12px", padding: "12px 18px", borderBottom: "1px solid var(--color-divider)", fontSize: "10.5px", letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                <span>
                  {"Building ID"}
                </span>
                <span>
                  {"Name"}
                </span>
                <span>
                  {"Country"}
                </span>
                <span>
                  {"State"}
                </span>
                <span>
                  {"Use · floor area split"}
                </span>
                <span>
                  {"Floors"}
                </span>
                <span>
                  {"Floor area"}
                </span>
                <span>
                  {"EUI"}
                </span>
                <span>
                  {"Benchmark"}
                </span>
                <span>
                  {"EUI vs benchmark"}
                </span>
                <span>
                  {"Benchmark standard"}
                </span>
                <span>
                  {"Hoist Score"}
                </span>
              </div>
              {(vals.buildingRows || []).map((b, $index) => (
                <React.Fragment key={$index}>
                  <div className="hv2" onClick={() => vals.bgToggle(b.id)} title="Open the graph for this building" style={{ display: "grid", gridTemplateColumns: "76px minmax(148px,1.2fr) 122px minmax(108px,1fr) minmax(148px,1.4fr) 54px 92px minmax(200px,1.2fr) 96px 104px minmax(240px,1.6fr) 72px", gap: "12px", padding: "11px 18px", borderBottom: "1px solid var(--color-divider)", fontSize: "12.5px", alignItems: "center", cursor: "pointer" }}>
                    <span title={b.idTip} style={{ fontFamily: "ui-monospace,monospace", color: "var(--color-neutral-400)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {b.idText}
                    </span>
                    <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {b.name}
                    </span>
                    <span style={{ color: "var(--color-neutral-400)", whiteSpace: "nowrap" }}>
                      {b.flag}{" "}{b.country}
                    </span>
                    <span style={{ color: "var(--color-neutral-400)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {b.state}
                    </span>
                    <div style={{ minWidth: "0", display: "flex", flexDirection: "column", gap: "4px" }}>
                      <span style={{ color: "var(--color-neutral-400)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {b.use}
                      </span>
                      <div title={b.mixTip} style={{ display: "flex", gap: "1.5px", height: "5px", borderRadius: "3px", overflow: "hidden" }}>
                        {(b.mix || []).map((m, $index) => (
                          <React.Fragment key={$index}>
                            <div title={m.tip} style={{ width: m.pct, background: m.color, backgroundImage: m.hatch, border: m.border, boxSizing: "border-box" }}></div>
                          </React.Fragment>
                        ))}
                      </div>
                      <span style={{ fontSize: "10.5px", color: "var(--color-neutral-500)", lineHeight: "1.35", textWrap: "pretty" }}>
                        {b.mixText}
                      </span>
                    </div>
                    <span title={b.floorsTip} style={{ fontVariantNumeric: "tabular-nums" }}>
                      {b.floors}
                      <span style={{ display: "block", fontSize: "9.5px", letterSpacing: "0.04em", lineHeight: "1.3", marginTop: "2px", fontVariantNumeric: "normal" }}>
                        <span style={{ color: b.floorsSrcFg }}>{b.floorsSrc}</span>
                      </span>
                    </span>
                    <span title={b.areaTip} style={{ fontVariantNumeric: "tabular-nums", color: "var(--color-neutral-300)" }}>
                      {b.area}
                      <span style={{ display: "block", fontSize: "9.5px", letterSpacing: "0.04em", lineHeight: "1.3", marginTop: "2px", fontVariantNumeric: "normal" }}>
                        <span style={{ color: b.areaSrcFg }}>{b.areaSrc}</span>
                        {" "}
                        <span title={b.partialTip} style={{ display: b.partialShow, padding: "0 4px", borderRadius: "3px", background: "var(--st-warn)", color: "var(--accent-ink,#0d1412)", fontSize: "9px", cursor: "help" }}>
                          {b.partialLabel}
                        </span>
                      </span>
                    </span>
                    <span title={b.routeTip} style={{ minWidth: "0", fontVariantNumeric: "tabular-nums" }}>
                      <span title={b.euiTip} style={{ whiteSpace: "nowrap" }}>
                        {b.eui}
                        <span style={{ display: "inline-block", marginLeft: "6px", fontSize: "9.5px", letterSpacing: "0.04em", fontVariantNumeric: "normal", color: b.euiSrcFg }}>
                          {b.euiSrc}
                        </span>
                      </span>
                      <span style={{ display: "block", fontSize: "10px", color: "var(--color-neutral-500)", lineHeight: "1.3", textWrap: "pretty", fontVariantNumeric: "normal", marginTop: "2px" }}>
                        {b.route}
                      </span>
                      <span style={{ display: "block", fontSize: "10px", color: b.routeGranFg, lineHeight: "1.3", fontVariantNumeric: "normal" }}>
                        {b.routeGran}
                      </span>
                    </span>
                    <span title={b.benchTip} style={{ fontVariantNumeric: "tabular-nums", color: "var(--color-neutral-400)", whiteSpace: "nowrap" }}>
                      {b.bench}
                      <span style={{ display: "block", fontSize: "9.5px", letterSpacing: "0.04em", lineHeight: "1.3", marginTop: "2px", fontVariantNumeric: "normal" }}>
                        <span style={{ color: "var(--color-neutral-500)" }}>{b.benchSrc}</span>
                      </span>
                    </span>
                    <span style={{ fontVariantNumeric: "tabular-nums", color: b.euiColor, whiteSpace: "nowrap" }}>
                      {b.delta}
                      <span style={{ display: "block", fontSize: "10px", color: "var(--color-neutral-500)" }}>
                        {b.deltaWord}
                      </span>
                    </span>
                    <span style={{ color: "var(--color-neutral-400)", minWidth: "0", overflow: "hidden" }}>
                      {b.std}
                      <span style={{ display: "block", fontSize: "10px", color: b.stdFg, lineHeight: "1.3", textWrap: "pretty", marginTop: "2px" }} title={b.stdNote}>
                        {b.stdNote}
                      </span>
                    </span>
                    <span style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "8px" }}>
                      <span title={b.scoreTip} style={{ fontVariantNumeric: "tabular-nums", color: b.scoreColor }}>
                        {b.score}
                      </span>
                      {vals.bcCanHoist && b.buildingId ? (
                        <span className="hv6" title={"Edit " + b.name + " — only the fields you change are sent"} onClick={(e) => { e.stopPropagation(); vals.bcOpenEdit(b.row); }} style={{ fontSize: "11px", color: "var(--color-neutral-500)", cursor: "pointer", flexShrink: "0" }}>
                          <i className="ph ph-pencil-simple"></i>
                        </span>
                      ) : null}
                      {vals.bcCanRemove && b.buildingId ? (
                        <span className="hv11" title={"Remove " + b.name} onClick={(e) => { e.stopPropagation(); vals.bcAskDelete(b); }} style={{ fontSize: "11px", color: "var(--color-neutral-500)", cursor: "pointer", flexShrink: "0" }}>
                          <i className="ph ph-trash"></i>
                        </span>
                      ) : null}
                    </span>
                  </div>
                  {vals.bgIsOpen(b.id) ? (() => {
                    const g = vals.bgFor(b);
                    return (
                      <div style={{ gridColumn: "1 / -1", padding: "18px 22px 22px", background: "var(--color-bg)", borderBottom: "1px solid var(--color-divider)" }}>
                        <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", flexWrap: "wrap", gap: "6px 18px", marginBottom: "14px" }}>
                          <span style={{ fontSize: "13px", fontWeight: "600" }}>
                            {b.name}{" — "}{g.totalText}
                          </span>
                          <span style={{ fontSize: "10.5px", color: g.error ? "var(--st-warn)" : "var(--color-neutral-500)", fontFamily: "ui-monospace,monospace" }}>
                            {g.error ? ("Could not read the graph — " + g.error + ". The counts below are from the table.") : g.scopeNote}
                          </span>
                        </div>

                        {/* the branches, as the graph is written */}
                        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(240px,1fr))", gap: "14px" }}>
                          {(g.branches || []).map((br) => (
                            <div key={br.key} style={{ borderLeft: "2px solid var(--color-divider)", paddingLeft: "13px" }}>
                              <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: "10px" }}>
                                <span style={{ fontSize: "11px", letterSpacing: "0.06em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                                  {br.label}
                                </span>
                                <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "16px", fontVariantNumeric: "tabular-nums", color: br.tone }}>
                                  {br.countText}
                                </span>
                              </div>
                              <div style={{ display: br.noteShow, fontSize: "10.5px", color: "var(--color-neutral-500)", lineHeight: "1.45", marginTop: "3px", textWrap: "pretty" }}>
                                {br.note}
                              </div>
                              {(br.rows || []).map((r) => (
                                <div key={r.id} style={{ display: "flex", justifyContent: "space-between", gap: "10px", fontSize: "11px", marginTop: "3px" }}>
                                  <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: "var(--color-neutral-300)" }}>{r.label}</span>
                                  <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10px", color: "var(--color-neutral-500)", whiteSpace: "nowrap", flexShrink: "0" }}>{r.detail}</span>
                                </div>
                              ))}
                              <div style={{ display: br.moreShow, fontSize: "10px", color: "var(--color-neutral-500)", marginTop: "3px", fontFamily: "ui-monospace,monospace" }}>
                                {br.more}
                              </div>
                              {(br.children || []).map((c) => (
                                <div key={c.key} style={{ marginTop: "9px", paddingLeft: "11px", borderLeft: "1px solid var(--color-divider)" }}>
                                  <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: "10px" }}>
                                    <span style={{ fontSize: "10.5px", color: "var(--color-neutral-500)" }}>
                                      {c.label}
                                    </span>
                                    <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "12.5px", fontVariantNumeric: "tabular-nums", color: c.tone }}>
                                      {c.countText}
                                    </span>
                                  </div>
                                  <div style={{ display: c.noteShow, fontSize: "10px", color: "var(--color-neutral-500)", lineHeight: "1.4", marginTop: "2px", textWrap: "pretty" }}>
                                    {c.note}
                                  </div>
                                  {(c.rows || []).map((r) => (
                                    <div key={r.id} style={{ display: "flex", justifyContent: "space-between", gap: "8px", fontSize: "10.5px", marginTop: "2px" }}>
                                      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: "var(--color-neutral-400)" }}>{r.label}</span>
                                      <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "9.5px", color: "var(--color-neutral-500)", whiteSpace: "nowrap", flexShrink: "0" }}>{r.detail}</span>
                                    </div>
                                  ))}
                                  <div style={{ display: c.moreShow, fontSize: "9.5px", color: "var(--color-neutral-500)", marginTop: "2px", fontFamily: "ui-monospace,monospace" }}>
                                    {c.more}
                                  </div>
                                </div>
                              ))}
                            </div>
                          ))}
                        </div>

                        {/* what the building is costing — ranked on the gap over contract */}
                        <div style={{ display: g.cost.show, marginTop: "18px", borderTop: "1px solid var(--color-divider)", paddingTop: "14px" }}>
                          <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: "10px", flexWrap: "wrap" }}>
                            <span style={{ fontSize: "11px", letterSpacing: "0.06em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                              {"What it is costing"}
                            </span>
                            <span style={{ fontSize: "10.5px", color: "var(--color-neutral-500)" }}>
                              {"Ranked on the gap over contract, not on billed — the biggest spender is usually the biggest asset."}
                            </span>
                          </div>
                          {g.cost.loading ? (
                            <div style={{ fontSize: "11px", color: "var(--color-neutral-500)", marginTop: "8px" }}>{"Reading invoice lines…"}</div>
                          ) : null}
                          {g.cost.error ? (
                            <div style={{ fontSize: "11px", color: "var(--st-warn)", marginTop: "8px", lineHeight: "1.45" }}>{g.cost.error}</div>
                          ) : null}
                          {(g.cost.rows || []).length || g.cost.unattributed ? (
                            <div style={{ marginTop: "8px", borderRadius: "8px", background: "var(--color-surface)", overflow: "hidden" }}>
                              <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1.6fr) 44px 48px 92px 104px 60px", gap: "10px", padding: "7px 12px", borderBottom: "1px solid var(--color-divider)", fontFamily: "ui-monospace,monospace", fontSize: "9.5px", letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                                <span>{"asset"}</span><span style={{ textAlign: "right" }}>{"WOs"}</span><span style={{ textAlign: "right" }}>{"lines"}</span><span style={{ textAlign: "right" }}>{"billed"}</span><span style={{ textAlign: "right" }}>{"over contract"}</span><span style={{ textAlign: "right" }}>{"flagged"}</span>
                              </div>
                              {(g.cost.rows || []).concat(g.cost.unattributed ? [g.cost.unattributed] : []).map((r) => (
                                <div key={r.key} style={{ display: "grid", gridTemplateColumns: "minmax(0,1.6fr) 44px 48px 92px 104px 60px", gap: "10px", padding: "7px 12px", borderBottom: "1px solid var(--color-divider)", fontSize: "11px", fontVariantNumeric: "tabular-nums", alignItems: "baseline" }}>
                                  <span style={{ minWidth: "0", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                    <span style={{ fontFamily: "ui-monospace,monospace", color: r.key === "unattributed" ? "var(--color-neutral-500)" : "var(--color-text)" }}>{r.asset}</span>
                                    {r.name ? <span style={{ color: "var(--color-neutral-500)", marginLeft: "6px", fontSize: "10.5px" }}>{r.name}</span> : null}
                                  </span>
                                  <span style={{ textAlign: "right", color: "var(--color-neutral-400)" }}>{r.wos}</span>
                                  <span style={{ textAlign: "right", color: "var(--color-neutral-400)" }}>{r.lines}</span>
                                  <span style={{ textAlign: "right" }}>{r.billed}</span>
                                  <span style={{ textAlign: "right", color: r.overTone }}>{r.over}</span>
                                  <span style={{ textAlign: "right", color: "var(--color-neutral-400)" }}>{r.flagged}</span>
                                </div>
                              ))}
                              <div style={{ display: g.cost.totalsShow, justifyContent: "space-between", gap: "12px", flexWrap: "wrap", padding: "8px 12px", fontSize: "10.5px", color: "var(--color-neutral-500)", fontVariantNumeric: "tabular-nums" }}>
                                <span>{g.cost.totalWos}{" work orders · "}{g.cost.totalLines}{" invoice lines"}</span>
                                <span>{"billed "}{g.cost.totalBilled}{" · over contract "}<span style={{ color: "var(--st-risk)" }}>{g.cost.totalOver}</span></span>
                              </div>
                            </div>
                          ) : null}
                          <div style={{ display: g.cost.noteShow, fontSize: "10.5px", color: "var(--color-neutral-500)", marginTop: "6px", lineHeight: "1.45", textWrap: "pretty" }}>
                            {g.cost.note}
                          </div>
                        </div>

                        {/* what the counts above explain about the row */}
                        {(g.notes || []).length ? (
                          <div style={{ marginTop: "16px", display: "flex", flexDirection: "column", gap: "6px" }}>
                            {g.notes.map((n, $i) => (
                              <div key={$i} style={{ fontSize: "11.5px", color: n.tone, lineHeight: "1.5", textWrap: "pretty" }}>
                                {n.text}
                              </div>
                            ))}
                          </div>
                        ) : null}
                      </div>
                    );
                  })() : null}
                </React.Fragment>
              ))}
              <div style={{ padding: "22px 16px", fontSize: "12.5px", color: "var(--color-neutral-500)", lineHeight: "1.5", display: vals.bldEmptyShow }}>
                {vals.bldEmptyText}
              </div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: "7px 16px", padding: "11px 18px", borderBottom: "1px solid var(--color-divider)" }}>
                {(vals.useLegend || []).map((u, $index) => (
                  <React.Fragment key={$index}>
                    <span style={{ display: "inline-flex", alignItems: "center", gap: "6px", fontSize: "10.5px", color: "var(--color-neutral-500)" }}>
                      <span style={{ width: "16px", height: "5px", borderRadius: "3px", background: u.color, backgroundImage: u.hatch, flexShrink: "0" }}></span>
                      <span>
                        {u.label}
                      </span>
                    </span>
                  </React.Fragment>
                ))}
              </div>
              <div style={{ display: vals.bldPagerShow, alignItems: "center", justifyContent: "space-between", gap: "12px", padding: "11px 18px", borderBottom: "1px solid var(--color-divider)" }}>
                <span style={{ fontSize: "11px", color: "var(--color-neutral-500)" }}>{vals.bldPageLabel}</span>
                <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                  <div className="hv13" onClick={vals.bldPagePrev} style={{ fontSize: "11.5px", padding: "5px 11px", borderRadius: "7px", border: "1px solid var(--color-divider)", color: vals.bldPagePrevShow ? "var(--color-text)" : "var(--color-neutral-500)", cursor: vals.bldPagePrevShow ? "pointer" : "default", opacity: vals.bldPagePrevShow ? "1" : "0.45" }}>{"Prev"}</div>
                  <span style={{ fontSize: "11px", color: "var(--color-neutral-500)", padding: "0 4px" }}>{"Page " + (vals.bldPage + 1) + " of " + vals.bldPageCount}</span>
                  <div className="hv13" onClick={vals.bldPageNext} style={{ fontSize: "11.5px", padding: "5px 11px", borderRadius: "7px", border: "1px solid var(--color-divider)", color: vals.bldPageNextShow ? "var(--color-text)" : "var(--color-neutral-500)", cursor: vals.bldPageNextShow ? "pointer" : "default", opacity: vals.bldPageNextShow ? "1" : "0.45" }}>{"Next"}</div>
                </div>
              </div>
            </div>
            <div style={{ padding: "13px 18px", fontSize: "10.5px", color: "var(--color-neutral-500)", lineHeight: "1.55" }}>
              {"Use drives the benchmark: each building is scored against the regulation pack for its country — CIBSE TM46, Energy Star Portfolio Manager and ASHRAE 100, the BCA Benchmarking Report, or a rolling live benchmark against comparable buildings in the portfolio. Two provenance lines sit under the numbers. Under EUI: how the reading arrives and at what granularity — a half-hourly data collector under Letter of Authority, a SMETS2 feed through a Smart Energy Code intermediary, a Green Button consent via an aggregator, a contracted retailer feed, or the building's own sub-meters and BMS. Amber marks an inference, never a threshold: building-level metering is amber because attribution to a plant item there is inferred rather than measured, and a figure that was recorded by hand rather than counted or metered carries the same colour. It says how a number was arrived at, not whether it is bad. A word under each figure names its source, and \u201cpartly counted\u201d against a floor area means the graph holds only some of that building \u2014 the surveyed figure is the one shown. Under the benchmark standard: the legal standing of that standard — enacted, guidance, or no operational standard at all — so a proposal is never read as a duty."}
            </div>
          </div>
          <div style={{ marginTop: "34px" }}>
            <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", flexWrap: "wrap", gap: "14px 24px" }}>
              <div style={{ minWidth: "0", flex: "1 1 340px" }}>
                <h2 style={{ fontSize: "23px", margin: "0", lineHeight: "1.15" }}>
                  {"Hoist Graph"}
                </h2>
                <div style={{ fontSize: "13px", color: "var(--color-accent)", marginTop: "5px" }}>
                  {"What a Hoisted Building resolves to"}
                </div>
                <p style={{ fontSize: "12px", color: "var(--color-neutral-400)", margin: "8px 0 0", maxWidth: "76ch", lineHeight: "1.5" }}>
                  {"Every node is a table in plenum_cafm and every figure beside one is counted in it. A building's own key and floor count sit under its name; each satellite carries the relation and how many rows this building has on that branch. A branch nobody counted reads “?” rather than nought — the two are different facts, and only one of them is about the building."}
                </p>
                <div style={{ fontSize: "11.5px", color: "var(--color-neutral-500)", marginTop: "6px" }}>
                  {vals.graphNote}
                </div>
              </div>
              {vals.isAdmin ? (
                <>
                  <div className="btn btn-primary" onClick={vals.updateGraph} style={{ fontSize: "12px", padding: "7px 14px", cursor: "pointer", flexShrink: "0", alignSelf: "flex-start" }}>
                    {"Update the graph"}
                  </div>
                </>
              ) : null}
              <div style={{ display: "flex", gap: "16px", flexWrap: "wrap", flexShrink: "0" }}>
                {(vals.graphStats || []).map((g, $index) => (
                  <React.Fragment key={$index}>
                    <div>
                      <div style={{ fontFamily: "ui-monospace,monospace", fontSize: "19px", lineHeight: "1", color: g.color }}>
                        {g.value}
                      </div>
                      <div style={{ fontSize: "9.5px", letterSpacing: "0.09em", textTransform: "uppercase", color: "var(--color-neutral-500)", marginTop: "3px" }}>
                        {g.label}
                      </div>
                    </div>
                  </React.Fragment>
                ))}
              </div>
            </div>
            <div style={{ marginTop: "16px", borderRadius: "12px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", overflow: "hidden" }}>
              <div style={{ display: "flex", gap: "9px 16px", flexWrap: "wrap", padding: "11px 18px", borderBottom: "1px solid var(--color-divider)" }}>
                <span style={{ flexBasis: "100%", fontFamily: "ui-monospace,monospace", fontSize: "10px", color: "var(--color-neutral-500)", lineHeight: "1.5", order: "2" }}>
                  {vals.graphCodes}
                </span>
                {(vals.graphLegend || []).map((l, $index) => (
                  <React.Fragment key={$index}>
                    <span style={{ display: "inline-flex", alignItems: "center", gap: "7px", fontSize: "10.5px", color: "var(--color-neutral-500)" }}>
                      <span style={{ width: "11px", height: "11px", borderRadius: l.radius, background: l.fill, border: l.stroke, flexShrink: "0" }}></span>
                      <span>
                        {l.label}
                      </span>
                    </span>
                  </React.Fragment>
                ))}
              </div>
              <div style={{ overflow: "auto", maxHeight: "800px", resize: "vertical", borderBottom: "1px solid var(--color-divider)" }}>
                <div style={{ position: "relative", width: "2560px", height: "1180px", flexShrink: "0" }}>
                  <svg viewBox="0 0 2560 1180" style={{ position: "absolute", inset: "0", width: "2560px", height: "1180px" }}>
                    {(vals.gEdges || []).map((e, $index) => (
                      <React.Fragment key={$index}>
                        <path d={e.d} fill="none" stroke={e.stroke} strokeWidth={e.w} strokeDasharray={e.dash} opacity={e.op} />
                      </React.Fragment>
                    ))}
                    {(vals.gNodes || []).map((n, $index) => (
                      <React.Fragment key={$index}>
                        <circle onClick={n.click} cx={n.cx} cy={n.cy} r={n.circleR} fill={n.fill} stroke={n.stroke} strokeWidth={n.sw} strokeDasharray={n.dash} opacity={n.op} style={{ cursor: "pointer" }} />
                        <rect x={n.hx} y={n.hy} width={n.hw} height={n.hw} rx="7" fill="none" stroke="var(--marker)" strokeWidth="1" opacity={n.haloOp} />
                        <rect x={n.bx} y={n.by} width="20" height="20" rx="4" fill="var(--marker)" stroke="var(--color-surface)" strokeWidth="2" opacity={n.bOp} />
                        <rect onClick={n.click} x={n.rx} y={n.ry} width={n.rw} height={n.rw} rx="5" fill={n.fill} stroke={n.stroke} strokeWidth={n.sw} strokeDasharray={n.dash} opacity={n.rectOp} style={{ cursor: "pointer" }} />
                      </React.Fragment>
                    ))}
                  </svg>
                  {(vals.gCols || []).map((c, $index) => (
                    <React.Fragment key={$index}>
                      <div style={{ position: "absolute", left: c.left, top: "14px", width: c.w, textAlign: "center", fontFamily: "ui-monospace,monospace", fontSize: "9.5px", letterSpacing: "0.14em", color: "var(--color-neutral-500)" }}>
                        {c.label}
                      </div>
                    </React.Fragment>
                  ))}
                  {(vals.gEdgeLabels || []).map((e, $index) => (
                    <React.Fragment key={$index}>
                      <div style={{ position: "absolute", left: e.left, top: e.top, width: e.w, textAlign: e.align, fontFamily: "ui-monospace,monospace", fontSize: "9.5px", color: e.fill, pointerEvents: "none" }}>
                        {e.label}
                      </div>
                    </React.Fragment>
                  ))}
                  {(vals.gNodes || []).map((n, $index) => (
                    <React.Fragment key={$index}>
                      <div style={{ position: "absolute", left: n.bLeft, top: n.bTop, width: "20px", height: "20px", display: "flex", alignItems: "center", justifyContent: "center", fontFamily: "ui-monospace,monospace", fontSize: "9px", color: "#1A1A18", pointerEvents: "none" }}>
                        {n.bText}
                      </div>
                      <div onClick={n.click} style={{ position: "absolute", left: n.glyphLeft, top: n.glyphTop, width: n.glyphW, height: n.glyphW, alignItems: "center", justifyContent: "center", fontFamily: "ui-monospace,monospace", fontSize: n.glyphSize, color: n.glyphFill, cursor: "pointer", display: n.glyphShow }}>
                        {n.glyph}
                      </div>
                      <div onClick={n.click} style={{ position: "absolute", left: n.labelLeft, top: n.labelTop, width: n.labelW, textAlign: n.align, cursor: "pointer", opacity: n.op, pointerEvents: n.pe }}>
                        <div style={{ fontFamily: n.font, fontSize: n.fs, color: n.fg, lineHeight: "1.2", whiteSpace: n.wrap, textWrap: "pretty" }}>
                          {n.label}
                        </div>
                        <div style={{ fontFamily: "ui-monospace,monospace", fontSize: "9px", color: n.subFg, lineHeight: "1.3", marginTop: "2px", whiteSpace: n.wrap }}>
                          {n.sub}
                        </div>
                      </div>
                    </React.Fragment>
                  ))}
                </div>
              </div>
              <div style={{ borderTop: "1px solid var(--color-divider)", padding: "14px 18px" }}>
                <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: "12px", flexWrap: "wrap" }}>
                  <div style={{ minWidth: "0" }}>
                    <div style={{ fontSize: "10px", letterSpacing: "0.12em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                      {"Hierarchy · "}{vals.hier.building}
                    </div>
                    <div style={{ fontSize: "11px", color: "var(--color-neutral-500)", lineHeight: "1.45", marginTop: "4px", maxWidth: "70ch" }}>
                      {"Parent, child and sub-child tables for this building alone. Each row names the key it is identified by; children carry their parent's key as a foreign key."}
                    </div>
                  </div>
                  <div style={{ display: "flex", gap: "6px", alignItems: "center", flexShrink: "0" }}>
                    <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10.5px", color: "var(--color-neutral-500)" }}>
                      {"row identity"}
                    </span>
                    <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "11px", padding: "3px 8px", borderRadius: "6px", background: "var(--color-accent)", color: "var(--accent-ink)" }}>
                      {vals.hier.rowKey}
                    </span>
                  </div>
                </div>
                <div style={{ marginTop: "12px", borderRadius: "10px", background: "var(--color-bg)", overflow: "hidden" }}>
                  <div onClick={vals.hier.pickParent} style={{ display: "grid", gridTemplateColumns: "22px minmax(0,1fr) auto auto", gap: "10px", alignItems: "center", padding: "11px 13px", cursor: "pointer", background: vals.hier.parentBg, borderBottom: "1px solid var(--color-divider)" }}>
                    <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "9.5px", color: vals.hier.parentSub }}>
                      {"P"}
                    </span>
                    <div style={{ minWidth: "0" }}>
                      <div style={{ fontFamily: "ui-monospace,monospace", fontSize: "12.5px", color: vals.hier.parentFg }}>
                        {vals.hier.parentTbl}
                      </div>
                      <div style={{ fontSize: "10.5px", color: vals.hier.parentSub, marginTop: "2px" }}>
                        {vals.hier.building}{" · parent table"}
                      </div>
                    </div>
                    <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10px", padding: "2px 7px", borderRadius: "5px", background: vals.hier.pkBg, color: vals.hier.pkFg, whiteSpace: "nowrap" }}>
                      {vals.hier.pk}
                    </span>
                    <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10.5px", color: vals.hier.parentSub, whiteSpace: "nowrap" }}>
                      {vals.hier.rowKey}
                    </span>
                  </div>
                  {(vals.hier.vectors || []).map((v, $index) => (
                    <React.Fragment key={$index}>
                      <div onClick={v.tip} style={{ display: "grid", gridTemplateColumns: "22px minmax(0,1fr) auto auto", gap: "10px", alignItems: "center", padding: "8px 13px 8px 40px", cursor: "pointer", background: "var(--color-surface)", borderBottom: "1px solid var(--color-divider)" }}>
                        <span style={{ width: "13px", height: "13px", border: "2px solid var(--marker)", background: "var(--marker-tint)", borderRadius: "3px", boxShadow: "0 0 0 1.5px var(--color-surface),0 0 0 2.5px var(--marker)" }}></span>
                        <div style={{ minWidth: "0" }}>
                          <div style={{ fontSize: "11.5px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                            {v.label}
                          </div>
                          <div style={{ fontFamily: "ui-monospace,monospace", fontSize: "10px", color: "var(--color-neutral-500)", marginTop: "2px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                            {"~ "}{v.col}{" · similarity "}{v.sim}
                          </div>
                        </div>
                        <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10px", padding: "2px 7px", borderRadius: "5px", background: "var(--marker-tint)", color: "var(--color-neutral-300)", whiteSpace: "nowrap" }}>
                          {"vector"}
                        </span>
                        <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10.5px", color: "var(--color-neutral-500)", whiteSpace: "nowrap" }}>
                          {v.n}{" files"}
                        </span>
                      </div>
                    </React.Fragment>
                  ))}
                  {(vals.hier.children || []).map((c, $index) => (
                    <React.Fragment key={$index}>
                      <div>
                        <div onClick={c.toggle} style={{ display: "grid", gridTemplateColumns: "22px 22px minmax(0,1fr) auto auto", gap: "10px", alignItems: "center", padding: "10px 13px 10px 26px", cursor: "pointer", background: c.bg, borderBottom: "1px solid var(--color-divider)" }}>
                          <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "11px", color: c.chev }}>
                            {c.arrow}
                          </span>
                          <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "9.5px", color: c.code }}>
                            {c.glyph}
                          </span>
                          <div style={{ minWidth: "0" }}>
                            <div style={{ fontFamily: "ui-monospace,monospace", fontSize: "12px", color: c.fg }}>
                              {c.tbl}
                            </div>
                            <div style={{ fontFamily: "ui-monospace,monospace", fontSize: "10px", color: c.sub, marginTop: "2px" }}>
                              {c.rel}{" · "}{c.on}
                            </div>
                          </div>
                          <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10px", padding: "2px 7px", borderRadius: "5px", background: c.pkBg, color: c.pkFg, whiteSpace: "nowrap" }}>
                            {"PK "}{c.pk}
                          </span>
                          <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10.5px", color: c.sub, whiteSpace: "nowrap" }}>
                            {c.rows}
                          </span>
                        </div>
                        <div style={{ display: c.openShow, flexDirection: "column", background: "var(--color-surface)" }}>
                          {(c.vectors || []).map((v, $index) => (
                            <React.Fragment key={$index}>
                              <div onClick={v.tip} style={{ display: "grid", gridTemplateColumns: "22px minmax(0,1fr) auto auto", gap: "10px", alignItems: "center", padding: "8px 13px 8px 70px", cursor: "pointer", background: "var(--color-surface)", borderBottom: "1px solid var(--color-divider)" }}>
                                <span style={{ width: "13px", height: "13px", border: "2px solid var(--marker)", background: "var(--marker-tint)", borderRadius: "3px", boxShadow: "0 0 0 1.5px var(--color-surface),0 0 0 2.5px var(--marker)" }}></span>
                                <div style={{ minWidth: "0" }}>
                                  <div style={{ fontSize: "11.5px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                    {v.label}
                                  </div>
                                  <div style={{ fontFamily: "ui-monospace,monospace", fontSize: "10px", color: "var(--color-neutral-500)", marginTop: "2px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                    {"~ "}{v.col}{" · similarity "}{v.sim}
                                  </div>
                                </div>
                                <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10px", padding: "2px 7px", borderRadius: "5px", background: "var(--marker-tint)", color: "var(--color-neutral-300)", whiteSpace: "nowrap" }}>
                                  {"vector"}
                                </span>
                                <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10.5px", color: "var(--color-neutral-500)", whiteSpace: "nowrap" }}>
                                  {v.n}{" files"}
                                </span>
                              </div>
                            </React.Fragment>
                          ))}
                          {(c.subs || []).map((sb, $index) => (
                            <React.Fragment key={$index}>
                              <div onClick={sb.pick} style={{ display: "grid", gridTemplateColumns: "22px minmax(0,1fr) auto auto", gap: "10px", alignItems: "center", padding: "9px 13px 9px 70px", cursor: "pointer", background: sb.bg, borderBottom: "1px solid var(--color-divider)" }}>
                                <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "9px", color: sb.code }}>
                                  {sb.glyph}
                                </span>
                                <div style={{ minWidth: "0" }}>
                                  <div style={{ fontFamily: "ui-monospace,monospace", fontSize: "11.5px", color: sb.fg }}>
                                    {sb.tbl}
                                  </div>
                                  <div style={{ fontFamily: "ui-monospace,monospace", fontSize: "10px", color: sb.sub, marginTop: "2px" }}>
                                    {sb.rel}{" · "}{sb.on}
                                  </div>
                                </div>
                                <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10px", padding: "2px 7px", borderRadius: "5px", background: sb.pkBg, color: sb.pkFg, whiteSpace: "nowrap" }}>
                                  {"PK "}{sb.pk}
                                </span>
                                <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10.5px", color: sb.sub, whiteSpace: "nowrap" }}>
                                  {sb.rows}
                                </span>
                              </div>
                              {(sb.vectors || []).map((v, $index) => (
                                <React.Fragment key={$index}>
                                  <div onClick={v.tip} style={{ display: "grid", gridTemplateColumns: "22px minmax(0,1fr) auto auto", gap: "10px", alignItems: "center", padding: "7px 13px 7px 100px", cursor: "pointer", background: "var(--color-bg)", borderBottom: "1px solid var(--color-divider)" }}>
                                    <span style={{ width: "12px", height: "12px", border: "2px solid var(--marker)", background: "var(--marker-tint)", borderRadius: "3px", boxShadow: "0 0 0 1.5px var(--color-bg),0 0 0 2.5px var(--marker)" }}></span>
                                    <div style={{ minWidth: "0" }}>
                                      <div style={{ fontSize: "11px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                        {v.label}
                                      </div>
                                      <div style={{ fontFamily: "ui-monospace,monospace", fontSize: "9.5px", color: "var(--color-neutral-500)", marginTop: "2px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                        {"~ "}{v.col}{" · "}{v.sim}
                                      </div>
                                    </div>
                                    <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "9.5px", padding: "1.5px 6px", borderRadius: "5px", background: "var(--marker-tint)", color: "var(--color-neutral-300)", whiteSpace: "nowrap" }}>
                                      {"vector"}
                                    </span>
                                    <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10px", color: "var(--color-neutral-500)", whiteSpace: "nowrap" }}>
                                      {v.n}{" files"}
                                    </span>
                                  </div>
                                </React.Fragment>
                              ))}
                            </React.Fragment>
                          ))}
                        </div>
                      </div>
                    </React.Fragment>
                  ))}
                </div>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(300px,1fr))", gap: "14px", marginTop: "14px", alignItems: "start" }}>
                  <div style={{ minWidth: "0", borderRadius: "10px", background: "var(--color-bg)", overflow: "hidden" }}>
                    <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: "10px", padding: "10px 13px", borderBottom: "1px solid var(--color-divider)" }}>
                      <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "13px", color: "var(--color-accent)" }}>
                        {vals.tbl.name}
                      </span>
                      <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10.5px", color: "var(--color-neutral-500)" }}>
                        {vals.tbl.rows}{" rows"}
                      </span>
                    </div>
                    {(vals.tbl.cols || []).map((c, $index) => (
                      <React.Fragment key={$index}>
                        <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) auto auto", gap: "9px", alignItems: "center", padding: "6px 13px", borderBottom: "1px solid var(--color-divider)" }}>
                          <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "11.5px", color: c.fg, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                            {c.name}
                          </span>
                          <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10px", color: "var(--color-neutral-500)", whiteSpace: "nowrap" }}>
                            {c.type}
                          </span>
                          <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "9px", padding: "1.5px 5px", borderRadius: "4px", whiteSpace: "nowrap", background: c.keyBg, color: c.keyFg, display: c.keyShow }}>
                            {c.key}
                          </span>
                        </div>
                      </React.Fragment>
                    ))}
                  </div>
                  <div style={{ minWidth: "0", display: "flex", flexDirection: "column", gap: "11px" }}>
                    <div>
                      <div style={{ fontSize: "10px", letterSpacing: "0.12em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                        {"Child tables"}
                      </div>
                      <div style={{ fontSize: "10.5px", color: "var(--color-neutral-500)", lineHeight: "1.45", marginTop: "4px" }}>
                        {"Each carries this table's primary key as a foreign key. Portfolio-wide row counts."}
                      </div>
                      <div style={{ display: "flex", flexDirection: "column", gap: "5px", marginTop: "8px" }}>
                        {(vals.tbl.children || []).map((ch, $index) => (
                          <React.Fragment key={$index}>
                            <div className="hv13" onClick={ch.click} style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) auto", gap: "9px", alignItems: "center", padding: "8px 11px", borderRadius: "8px", background: "var(--color-bg)", border: "1px solid var(--color-divider)", cursor: "pointer" }}>
                              <div style={{ minWidth: "0" }}>
                                <div style={{ fontFamily: "ui-monospace,monospace", fontSize: "11.5px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                  {ch.name}
                                </div>
                                <div style={{ fontFamily: "ui-monospace,monospace", fontSize: "9.5px", color: "var(--color-accent-300)", marginTop: "2px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                  {ch.on}
                                </div>
                              </div>
                              <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10px", color: "var(--color-neutral-500)", whiteSpace: "nowrap" }}>
                                {ch.rows}
                              </span>
                            </div>
                          </React.Fragment>
                        ))}
                        <span style={{ fontSize: "11px", color: "var(--color-neutral-500)", display: vals.tbl.leafShow }}>
                          {"Leaf table — nothing hangs off it."}
                        </span>
                      </div>
                    </div>
                    <div style={{ display: vals.tbl.parentShow, flexDirection: "column" }}>
                      <div style={{ fontSize: "10px", letterSpacing: "0.12em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                        {"Resolves upward through"}
                      </div>
                      <div style={{ display: "flex", gap: "6px", flexWrap: "wrap", marginTop: "7px" }}>
                        {(vals.tbl.parents || []).map((p, $index) => (
                          <React.Fragment key={$index}>
                            <span className="hv24" onClick={p.click} style={{ fontFamily: "ui-monospace,monospace", fontSize: "11px", padding: "4px 9px", borderRadius: "6px", background: "var(--color-accent-900)", color: "var(--color-accent)", cursor: "pointer", whiteSpace: "nowrap" }}>
                              {p.name}{" ↑"}
                            </span>
                          </React.Fragment>
                        ))}
                      </div>
                      <div style={{ fontSize: "10.5px", color: "var(--color-neutral-500)", lineHeight: "1.45", marginTop: "7px" }}>
                        {vals.tbl.hopNote}
                      </div>
                    </div>
                  </div>
                </div>
                <div style={{ marginTop: "14px", borderRadius: "10px", background: "var(--color-bg)", overflow: "hidden" }}>
                  <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) auto", gap: "14px", alignItems: "center", padding: "12px 14px", borderBottom: "1px solid var(--color-divider)" }}>
                    <div style={{ minWidth: "0" }}>
                      <div style={{ fontSize: "10px", letterSpacing: "0.12em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                        {"Export canonical table"}
                      </div>
                      <div style={{ fontSize: "12.5px", marginTop: "5px", lineHeight: "1.4" }}>
                        <span style={{ fontFamily: "ui-monospace,monospace", color: "var(--color-accent)" }}>
                          {vals.exportT.name}
                        </span>
                        {" as it stands now — "}{vals.exportT.rows}{" rows, "}{vals.exportT.cols}{" columns, resolved through the graph."}
                      </div>
                    </div>
                    <div className="hv7" onClick={vals.exportT.download} style={{ display: "flex", alignItems: "center", gap: "7px", fontSize: "12px", padding: "8px 14px", borderRadius: "8px", background: "var(--color-accent)", color: "var(--accent-ink)", cursor: "pointer", whiteSpace: "nowrap", flexShrink: "0" }}>
                      <i className="ph ph-download-simple" style={{ fontSize: "13px" }}></i>
                      <span>
                        {"Download CSV"}
                      </span>
                    </div>
                  </div>
                  <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) auto auto auto", gap: "12px", alignItems: "center", padding: "9px 14px", background: "var(--color-surface)", borderBottom: "1px solid var(--color-divider)" }}>
                    <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "9.5px", letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                      {"Snapshot"}
                    </span>
                    <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "9.5px", letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--color-neutral-500)", textAlign: "right" }}>
                      {"Rows"}
                    </span>
                    <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "9.5px", letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--color-neutral-500)", textAlign: "right", minWidth: "56px" }}>
                      {"Change"}
                    </span>
                    <span style={{ width: "16px" }}></span>
                  </div>
                  <div style={{ display: vals.exportT.noteShow, fontSize: "11px", color: "var(--color-neutral-500)", padding: "10px 14px", borderBottom: "1px solid var(--color-divider)" }}>
                    {vals.exportT.note}
                  </div>
                  {(vals.exportT.versions || []).map((v, $index) => (
                    <React.Fragment key={$index}>
                      <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) auto auto auto", gap: "12px", alignItems: "center", padding: "9px 14px", borderBottom: "1px solid var(--color-divider)", background: v.bg }}>
                        <div style={{ minWidth: "0" }}>
                          <div style={{ fontSize: "12px", color: v.fg, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                            {v.label}
                          </div>
                          <div style={{ fontFamily: "ui-monospace,monospace", fontSize: "10px", color: "var(--color-neutral-500)", marginTop: "2px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                            {v.note}
                          </div>
                        </div>
                        <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "11.5px", textAlign: "right", whiteSpace: "nowrap" }}>
                          {v.rows}
                        </span>
                        <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10.5px", textAlign: "right", whiteSpace: "nowrap", color: v.deltaFg, minWidth: "56px" }}>
                          {v.delta}
                        </span>
                        <i className="ph ph-download-simple hv6" onClick={v.download} title="Download this snapshot" style={{ fontSize: "14px", color: "var(--color-neutral-500)", cursor: "pointer" }}></i>
                      </div>
                    </React.Fragment>
                  ))}
                  <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap", padding: "11px 14px" }}>
                    <div className="hv15" onClick={vals.exportT.downloadHistory} style={{ fontSize: "11.5px", padding: "6px 12px", borderRadius: "7px", border: "1px solid var(--color-accent)", color: "var(--color-accent)", cursor: "pointer", whiteSpace: "nowrap" }}>
                      {"Download every cutoff together"}
                    </div>
                    <span style={{ fontSize: "10.5px", color: "var(--color-neutral-500)", lineHeight: "1.45", flex: "1", minWidth: "200px" }}>
                      {vals.exportT.basis}
                    </span>
                  </div>
                </div>
              </div>
              <div style={{ padding: "13px 18px", borderTop: "1px solid var(--color-divider)", fontSize: "10.5px", color: "var(--color-neutral-500)", lineHeight: "1.55" }}>
                {"Dashed nodes hold no building key. A work order reaches one by walking "}
                <span style={{ fontFamily: "ui-monospace,monospace", color: "var(--color-accent-300)" }}>
                  {":ASSIGNED_TO"}
                </span>
                {" to the asset; an invoice reaches one through the work order it bills. That indirection is why an FM system storing either one flat can tell you what was done but not what it cost the building."}
              </div>
            </div>
            {true ? (
              <>
                <div style={{ marginTop: "34px" }}>
                  <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", flexWrap: "wrap", gap: "12px 24px" }}>
                    <div style={{ minWidth: "0", flex: "1 1 340px" }}>
                      <h2 style={{ fontSize: "23px", margin: "0", lineHeight: "1.15" }}>
                        {"Documents"}
                      </h2>
                      <div style={{ fontSize: "13px", color: "var(--color-accent)", marginTop: "5px" }}>
                        {vals.docScope}
                      </div>
                      <p style={{ fontSize: "12px", color: "var(--color-neutral-400)", margin: "8px 0 0", maxWidth: "76ch", lineHeight: "1.5" }}>
                        {vals.docBlurb}
                      </p>
                    </div>
                    <div style={{ display: "flex", gap: "18px", flexWrap: "wrap", flexShrink: "0" }}>
                      {(vals.docStats || []).map((d, $index) => (
                        <React.Fragment key={$index}>
                          <div>
                            <div style={{ fontFamily: "ui-monospace,monospace", fontSize: "19px", lineHeight: "1", color: d.color }}>
                              {d.value}
                            </div>
                            <div style={{ fontSize: "9.5px", letterSpacing: "0.09em", textTransform: "uppercase", color: "var(--color-neutral-500)", marginTop: "3px" }}>
                              {d.label}
                            </div>
                          </div>
                        </React.Fragment>
                      ))}
                    </div>
                  </div>
                  <div style={{ display: vals.docQueryShow, alignItems: "center", gap: "10px", padding: "9px 13px", borderRadius: "10px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", marginTop: "16px", maxWidth: "420px" }}>
                    <i className="ph ph-magnifying-glass" style={{ fontSize: "14px", color: "var(--color-neutral-500)", flexShrink: "0" }}></i>
                    <input className="input" value={vals.docQuery} onChange={vals.setDocQuery} placeholder="Search by name, building ID, country or state…" style={{ flex: "1", minWidth: "0", background: "transparent", border: "none", outline: "none", fontFamily: "var(--font-body)", fontSize: "13px", color: "var(--color-text)" }} />
                    {vals.docQuery ? (
                      <i className="ph ph-x hv11" onClick={() => vals.setDocQuery({ target: { value: "" } })} style={{ fontSize: "13px", color: "var(--color-neutral-500)", cursor: "pointer", flexShrink: "0" }}></i>
                    ) : null}
                  </div>
                  <div style={{ display: vals.docEmptyShow, fontSize: "12.5px", color: "var(--color-neutral-500)", lineHeight: "1.5", padding: "16px 2px" }}>
                    {vals.docEmptyText}
                  </div>
                  <div style={{ display: "flex", flexDirection: "column", gap: "10px", marginTop: "16px" }}>
                    {(vals.docBuildings || []).map((b, $index) => (
                      <React.Fragment key={$index}>
                        <div style={{ borderRadius: "11px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", overflow: "hidden" }}>
                          <div className="hv2" onClick={b.toggle} style={{ display: "grid", gridTemplateColumns: "22px minmax(0,1fr) auto auto auto", gap: "12px", alignItems: "center", padding: "12px 16px", cursor: "pointer", background: b.headBg }}>
                            <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "11px", color: "var(--color-neutral-500)" }}>
                              {b.arrow}
                            </span>
                            <div style={{ minWidth: "0" }}>
                              <div style={{ fontSize: "13.5px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                {b.name}
                              </div>
                              <div style={{ fontFamily: "ui-monospace,monospace", fontSize: "10.5px", color: "var(--color-neutral-500)", marginTop: "2px" }}>
                                {b.id}{" · "}{b.state}
                              </div>
                            </div>
                            <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10.5px", padding: "2px 8px", borderRadius: "5px", background: "var(--color-accent-900)", color: "var(--color-accent)", whiteSpace: "nowrap" }}>
                              {b.nStruct}{" "}{b.structLabel}
                            </span>
                            <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10.5px", padding: "2px 8px", borderRadius: "5px", background: "var(--marker-tint)", color: "var(--color-neutral-300)", whiteSpace: "nowrap" }}>
                              {b.nUnstruct}{" "}{b.unstructLabel}
                            </span>
                            <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10.5px", color: "var(--color-neutral-500)", whiteSpace: "nowrap" }}>
                              {b.size}
                            </span>
                          </div>
                          <div style={{ display: b.openShow, flexDirection: "column", borderTop: "1px solid var(--color-divider)" }}>
                            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(340px,1fr))" }}>
                              <div style={{ minWidth: "0", borderRight: "1px solid var(--color-divider)" }}>
                                <div style={{ display: "flex", alignItems: "center", gap: "8px", padding: "10px 16px", background: "var(--color-bg)", borderBottom: "1px solid var(--color-divider)" }}>
                                  <span style={{ width: "11px", height: "11px", borderRadius: "50%", background: "var(--color-accent)", flexShrink: "0" }}></span>
                                  <span style={{ fontSize: "11px", letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                                    {"Documents — filed against this building"}
                                  </span>
                                </div>
                                {(b.structured || []).map((d, $index) => (
                                  <React.Fragment key={$index}>
                                    <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) auto auto", gap: "10px", alignItems: "center", padding: "9px 16px", borderBottom: "1px solid var(--color-divider)" }}>
                                      <div style={{ minWidth: "0" }}>
                                        <div style={{ fontSize: "12px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                          {d.file}
                                        </div>
                                        <div style={{ fontFamily: "ui-monospace,monospace", fontSize: "10px", color: "var(--color-accent)", marginTop: "2px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                          {"→ "}{d.became}
                                        </div>
                                      </div>
                                      <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10px", color: "var(--color-neutral-500)", whiteSpace: "nowrap" }}>
                                        {d.meta}
                                      </span>
                                      <div style={{ display: "flex", gap: "8px", flexShrink: "0" }}>
                                        <i className={d.viewIcon} onClick={d.view} title={d.viewTitle} style={{ fontSize: "14px", color: d.viewColor, cursor: d.viewCursor }}></i>
                                        <i className="ph ph-download-simple hv6" onClick={d.download} title="Download" style={{ display: d.dlShow, fontSize: "14px", color: "var(--color-neutral-500)", cursor: "pointer" }}></i>
                                      </div>
                                    </div>
                                  </React.Fragment>
                                ))}
                              </div>
                              <div style={{ minWidth: "0" }}>
                                <div style={{ display: "flex", alignItems: "center", gap: "8px", padding: "10px 16px", background: "var(--color-bg)", borderBottom: "1px solid var(--color-divider)" }}>
                                  <span style={{ width: "11px", height: "11px", borderRadius: "3px", background: "var(--marker)", flexShrink: "0" }}></span>
                                  <span style={{ fontSize: "11px", letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                                    {b.certHead}
                                  </span>
                                </div>
                                <div style={{ display: b.certEmptyShow, fontSize: "11.5px", color: "var(--color-neutral-500)", padding: "12px 16px" }}>
                                  {b.certEmpty}
                                </div>
                                {(b.unstructured || []).map((d, $index) => (
                                  <React.Fragment key={$index}>
                                    <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) auto auto", gap: "10px", alignItems: "center", padding: "9px 16px", borderBottom: "1px solid var(--color-divider)" }}>
                                      <div style={{ minWidth: "0" }}>
                                        <div style={{ fontSize: "12px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                          {d.file}
                                        </div>
                                        <div style={{ fontFamily: "ui-monospace,monospace", fontSize: "10px", color: "var(--color-neutral-500)", marginTop: "2px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                          {d.became}
                                        </div>
                                      </div>
                                      <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10px", color: "var(--color-neutral-500)", whiteSpace: "nowrap" }}>
                                        {d.meta}
                                      </span>
                                      <div style={{ display: "flex", gap: "8px", flexShrink: "0" }}>
                                        <i className={d.viewIcon} onClick={d.view} title={d.viewTitle} style={{ fontSize: "14px", color: d.viewColor, cursor: d.viewCursor }}></i>
                                        <i className="ph ph-download-simple hv6" onClick={d.download} title="Download" style={{ display: d.dlShow, fontSize: "14px", color: "var(--color-neutral-500)", cursor: "pointer" }}></i>
                                      </div>
                                    </div>
                                  </React.Fragment>
                                ))}
                              </div>
                            </div>
                            <div style={{ display: b.emptyShow, fontSize: "11.5px", color: "var(--color-neutral-500)", padding: "12px 16px", borderTop: "1px solid var(--color-divider)" }}>
                              {b.emptyText}
                            </div>
                            <div style={{ display: "flex", gap: "9px", flexWrap: "wrap", padding: "12px 16px", background: "var(--color-bg)" }}>
                              <div className="hv15" onClick={b.downloadAll} style={{ fontSize: "11.5px", padding: "6px 12px", borderRadius: "7px", border: "1px solid var(--color-accent)", color: "var(--color-accent)", cursor: "pointer" }}>
                                {"Download all"}
                              </div>
                              <div className="hv4" onClick={b.ingestMore} style={{ fontSize: "11.5px", padding: "6px 12px", borderRadius: "7px", border: "1px solid var(--color-divider)", color: "var(--color-neutral-400)", cursor: "pointer" }}>
                                {"Ingest more"}
                              </div>
                            </div>
                          </div>
                        </div>
                      </React.Fragment>
                    ))}
                  </div>
                  <div style={{ display: vals.docPagerShow, alignItems: "center", justifyContent: "space-between", gap: "12px", marginTop: "14px", padding: "5px 2px" }}>
                    <span style={{ fontSize: "11px", color: "var(--color-neutral-500)" }}>{vals.docPageLabel}</span>
                    <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                      <div className="hv13" onClick={vals.docPagePrev} style={{ fontSize: "11.5px", padding: "5px 11px", borderRadius: "7px", border: "1px solid var(--color-divider)", background: "var(--color-surface)", color: vals.docPagePrevShow ? "var(--color-text)" : "var(--color-neutral-500)", cursor: vals.docPagePrevShow ? "pointer" : "default", opacity: vals.docPagePrevShow ? "1" : "0.45" }}>{"Prev"}</div>
                      <span style={{ fontSize: "11px", color: "var(--color-neutral-500)", padding: "0 4px" }}>{"Page " + (vals.docPage + 1) + " of " + vals.docPageCount}</span>
                      <div className="hv13" onClick={vals.docPageNext} style={{ fontSize: "11.5px", padding: "5px 11px", borderRadius: "7px", border: "1px solid var(--color-divider)", background: "var(--color-surface)", color: vals.docPageNextShow ? "var(--color-text)" : "var(--color-neutral-500)", cursor: vals.docPageNextShow ? "pointer" : "default", opacity: vals.docPageNextShow ? "1" : "0.45" }}>{"Next"}</div>
                    </div>
                  </div>
                </div>
              </>
            ) : null}
          </div>
        </div>

      {/* ── Remove a building ─────────────────────────────────────────────────
          The dialog opens on a DELETE with no confirm, which changes nothing and
          reports what the building holds. That report is the whole dialog: the
          decision is what happens to those records, not "are you sure". */}
      <div style={{ display: vals.bcDelShow, position: "fixed", inset: "0", zIndex: "61", background: "rgba(8,14,13,0.44)", alignItems: "center", justifyContent: "center", padding: "24px" }} onClick={vals.bcDelCancel}>
        <div onClick={(e) => e.stopPropagation()} style={{ width: "100%", maxWidth: "460px", background: "var(--color-surface)", borderRadius: "14px", boxShadow: "var(--shadow-lg,0 24px 60px rgba(0,0,0,.28))", overflow: "hidden" }}>
          <div style={{ padding: "20px 22px 6px" }}>
            <h2 style={{ margin: "0", fontSize: "18px", lineHeight: "1.25" }}>{"Remove " + vals.bcDelName + "?"}</h2>
            <div style={{ fontSize: "11.5px", color: "var(--color-neutral-500)", marginTop: "4px", fontFamily: "ui-monospace,monospace" }}>{vals.bcDelCode}</div>
          </div>
          <div style={{ padding: "10px 22px 18px", fontSize: "13px", lineHeight: "1.55", color: "var(--color-neutral-400)" }}>
            <div style={{ display: vals.bcDelLoading ? "block" : "none" }}>{"Checking what this building holds…"}</div>
            <div style={{ display: vals.bcDelAttachedShow }}>{vals.bcDelAttachedText}</div>
            <div style={{ display: vals.bcDelEmptyShow }}>{"This building holds no records. Nothing else is affected."}</div>
          </div>
          <div style={{ padding: "14px 22px", borderTop: "1px solid var(--color-divider)", display: "flex", justifyContent: "flex-end", gap: "10px" }}>
            <div className="btn" onClick={vals.bcDelCancel} style={{ fontSize: "12.5px", padding: "8px 15px", cursor: "pointer", color: "var(--color-neutral-400)" }}>{"Keep it"}</div>
            <div className="btn" onClick={vals.bcDelConfirm} style={{ fontSize: "12.5px", padding: "8px 16px", cursor: vals.bcDelWorking ? "default" : "pointer", color: "var(--st-warn)", border: "1px solid var(--st-warn)", borderRadius: "8px", opacity: vals.bcDelWorking ? "0.6" : "1" }}>{vals.bcDelBusyLabel}</div>
          </div>
        </div>
      </div>

      </div>
  );
}
