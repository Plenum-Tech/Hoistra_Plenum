// Integrations — admin integrations
// Ported from the Hoistra prototype template. `vals` is the view model from useHoistra().
import React from 'react';

export default function Integrations({ vals }) {
  return (
      <div style={{ flex: "1", display: "flex", justifyContent: "flex-start", padding: "0 40px 80px" }}>
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
              {"Reports"}
            </span>
            <span style={{ fontSize: "12px", color: "var(--color-neutral-500)" }}>
              {"/"}
            </span>
            <span style={{ fontSize: "12px", color: "var(--color-neutral-500)" }}>
              {"Integrations"}
            </span>
          </div>
          <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", flexWrap: "wrap", gap: "16px 24px", marginTop: "18px" }}>
            <div style={{ minWidth: "0", flex: "1 1 340px" }}>
              <div style={{ fontSize: "10.5px", letterSpacing: "0.13em", textTransform: "uppercase", color: "var(--color-accent)" }}>
                {"Saved report · source connections"}
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap", marginTop: "7px" }}>
                <h2 style={{ fontSize: "28px", margin: "0", lineHeight: "1.15" }}>
                  {"Integrations"}
                </h2>
                <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10px", letterSpacing: "0.09em", textTransform: "uppercase", padding: "3px 8px", borderRadius: "5px", background: "var(--color-accent)", color: "var(--accent-ink)" }}>
                  {"Admin only"}
                </span>
              </div>
              <p style={{ fontSize: "13px", color: "var(--color-neutral-400)", margin: "8px 0 0", maxWidth: "88ch", lineHeight: "1.55" }}>
                {"Every system the portfolio already runs on — finance, ERP, CMMS, CAFM, IWMS, asset and news — landed into the Hoist Graph. A source is only listed with the tables it writes to, so what a connection buys you is visible before it is authorised. New tables mean new capabilities and new reports."}
              </p>
            </div>
            <div style={{ display: "flex", alignItems: "center", flexWrap: "wrap", gap: "10px" }}>
              <div className="btn btn-primary" onClick={vals.intBrowse} style={{ fontSize: "12px", padding: "7px 14px", cursor: "pointer" }}>
                {"Connect a source"}
              </div>
              <div className="hv4" onClick={vals.intSyncAll} style={{ fontSize: "12px", padding: "7px 14px", borderRadius: "8px", border: "1px solid var(--color-divider)", color: "var(--color-neutral-400)", cursor: "pointer", whiteSpace: "nowrap" }}>
                {"Sync all now"}
              </div>
              <div style={{ padding: "8px 13px", borderRadius: "9px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", display: "flex", flexDirection: "column", gap: "2px", whiteSpace: "nowrap" }}>
                <span style={{ fontSize: "9.5px", letterSpacing: "0.11em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                  {"Last sync"}
                </span>
                <span style={{ fontSize: "12.5px", fontVariantNumeric: "tabular-nums" }}>
                  {"02:31 today"}
                </span>
              </div>
            </div>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(158px,1fr))", gap: "11px", marginTop: "22px" }}>
            {(vals.intTiles || []).map((t, $index) => (
              <React.Fragment key={$index}>
                <div className="hv1" onClick={t.click} style={{ padding: "13px 14px 13px 16px", borderRadius: "10px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", position: "relative", overflow: "hidden", minWidth: "0", cursor: "pointer" }}>
                  <div style={{ position: "absolute", left: "0", top: "11px", bottom: "11px", width: "3px", borderRadius: "0 3px 3px 0", background: t.color }}></div>
                  <div style={{ display: "flex", alignItems: "baseline", gap: "6px" }}>
                    <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "23px", lineHeight: "1.1", color: t.color }}>
                      {t.value}
                    </span>
                    <i className="ph ph-arrow-up-right" style={{ fontSize: "10px", color: "var(--color-neutral-500)" }}></i>
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
          <div style={{ display: "flex", gap: "2px", marginTop: "20px", borderBottom: "1px solid var(--color-divider)", overflowX: "auto" }}>
            {(vals.intTabs || []).map((t, $index) => (
              <React.Fragment key={$index}>
                <div onClick={t.pick} style={{ padding: "9px 13px", fontSize: "12.5px", cursor: "pointer", whiteSpace: "nowrap", borderBottom: "2px solid transparent", borderBottomColor: t.edge, color: t.fg }}>
                  <span>
                    {t.label}
                  </span>
                  <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10.5px", color: "var(--color-neutral-500)", marginLeft: "6px" }}>
                    {t.n}
                  </span>
                </div>
              </React.Fragment>
            ))}
          </div>
          {vals.intTabConnected ? (
            <>
              <div style={{ marginTop: "14px", borderRadius: "11px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", overflow: "hidden" }}>
                <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap", padding: "11px 15px", borderBottom: "1px solid var(--color-divider)" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: "7px", padding: "6px 10px", borderRadius: "7px", border: "1px solid var(--color-divider)", background: "var(--color-bg)", minWidth: "220px", flex: "0 1 280px" }}>
                    <i className="ph ph-magnifying-glass" style={{ fontSize: "12px", color: "var(--color-neutral-500)" }}></i>
                    <input className="input" value={vals.intQ} onChange={vals.intSetQ} placeholder="Search connections" style={{ flex: "1", minWidth: "0", border: "0", background: "transparent", color: "var(--color-text)", fontFamily: "var(--font-body)", fontSize: "12px", outline: "none" }} />
                  </div>
                  <div className="hv4" onClick={vals.intExpandAll} style={{ fontSize: "11.5px", padding: "6px 11px", borderRadius: "7px", border: "1px solid var(--color-divider)", color: "var(--color-neutral-400)", cursor: "pointer", whiteSpace: "nowrap" }}>
                    {vals.intExpandLabel}
                  </div>
                  <span style={{ flex: "1", minWidth: "20px" }}></span>
                  <span style={{ fontSize: "11px", color: "var(--color-neutral-500)", fontVariantNumeric: "tabular-nums" }}>
                    {vals.intConnSummary}
                  </span>
                </div>
                <div style={{ overflowX: "auto" }}>
                  <div style={{ minWidth: "940px" }}>
                    <div style={{ display: "grid", gridTemplateColumns: "minmax(220px,1.5fr) 108px 96px 96px minmax(150px,1.1fr) 118px", gap: "12px", padding: "10px 15px", borderBottom: "1px solid var(--color-divider)", fontSize: "10px", letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {"Connection"}
                      </span>
                      <span>
                        {"Status"}
                      </span>
                      <span>
                        {"Last sync"}
                      </span>
                      <span>
                        {"Rows 30d"}
                      </span>
                      <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {"Tables fed"}
                      </span>
                      <span>
                        {"Connected by"}
                      </span>
                    </div>
                    {(vals.intRows || []).map((r, $index) => (
                      <React.Fragment key={$index}>
                        <div style={{ borderBottom: "1px solid var(--color-divider)" }}>
                          <div className="hv2" onClick={r.toggle} style={{ display: "grid", gridTemplateColumns: "minmax(220px,1.5fr) 108px 96px 96px minmax(150px,1.1fr) 118px", gap: "12px", alignItems: "center", padding: "11px 15px", cursor: "pointer", background: r.bg }}>
                            <div style={{ display: "flex", alignItems: "center", gap: "9px", minWidth: "0" }}>
                              <i className={`ph ${r.caret}`} style={{ fontSize: "11px", color: "var(--color-neutral-500)", flexShrink: "0" }}></i>
                              <span style={{ width: "22px", height: "22px", borderRadius: "6px", background: "var(--color-neutral-900)", color: "var(--color-neutral-300)", fontFamily: "ui-monospace,monospace", fontSize: "9px", display: "flex", alignItems: "center", justifyContent: "center", flexShrink: "0" }}>
                                {r.mark}
                              </span>
                              <div style={{ minWidth: "0" }}>
                                <div style={{ fontSize: "12.5px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                  {r.name}
                                </div>
                                <div style={{ fontSize: "10px", color: "var(--color-neutral-500)", marginTop: "1px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                  {r.cat}
                                </div>
                              </div>
                            </div>
                            <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "9.5px", letterSpacing: "0.06em", textTransform: "uppercase", padding: "3px 7px", borderRadius: "5px", background: r.stBg, color: r.stFg, justifySelf: "start", whiteSpace: "nowrap" }}>
                              {r.stLabel}
                            </span>
                            <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "11px", color: r.lastFg, whiteSpace: "nowrap" }}>
                              {r.last}
                            </span>
                            <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "11px", color: r.volFg }}>
                              {r.vol}
                            </span>
                            <span style={{ fontSize: "11px", color: "var(--color-neutral-400)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                              {r.tablesLine}
                            </span>
                            <div style={{ minWidth: "0" }}>
                              <div style={{ fontSize: "11.5px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                {r.by}
                              </div>
                              <div style={{ fontSize: "10px", color: "var(--color-neutral-500)", marginTop: "1px" }}>
                                {r.since}
                              </div>
                            </div>
                          </div>
                          <div style={{ display: r.openShow, flexDirection: "column", gap: "13px", padding: "14px 15px 16px 46px", background: "var(--color-bg)" }}>
                            <div style={{ fontFamily: "ui-monospace,monospace", fontSize: "10.5px", color: "var(--color-neutral-500)" }}>
                              {r.mode}{" · "}{r.auth}
                            </div>
                            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(260px,1fr))", gap: "13px", alignItems: "start" }}>
                              <div style={{ minWidth: "0" }}>
                                <div style={{ fontSize: "10px", letterSpacing: "0.09em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                                  {"Enriches tables already in the graph"}
                                </div>
                                <div style={{ display: "flex", flexWrap: "wrap", gap: "6px", marginTop: "8px" }}>
                                  {(r.enrich || []).map((e, $index) => (
                                    <React.Fragment key={$index}>
                                      <span style={{ display: "flex", alignItems: "baseline", gap: "6px", fontFamily: "ui-monospace,monospace", fontSize: "10.5px", padding: "3px 8px", borderRadius: "5px", background: "var(--color-accent-900)", color: "var(--color-accent)", whiteSpace: "nowrap" }}>
                                        <span>
                                          {e.tbl}
                                        </span>
                                        <span style={{ color: "var(--color-neutral-500)" }}>
                                          {e.n}
                                        </span>
                                      </span>
                                    </React.Fragment>
                                  ))}
                                </div>
                              </div>
                              <div style={{ minWidth: "0" }}>
                                <div style={{ fontSize: "10px", letterSpacing: "0.09em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                                  {"Tables that exist only because of this source"}
                                </div>
                                <div style={{ display: "flex", flexWrap: "wrap", gap: "6px", marginTop: "8px" }}>
                                  {(r.create || []).map((c, $index) => (
                                    <React.Fragment key={$index}>
                                      <span style={{ display: "flex", alignItems: "baseline", gap: "6px", fontFamily: "ui-monospace,monospace", fontSize: "10.5px", padding: "3px 8px", borderRadius: "5px", background: "var(--marker-tint)", border: "1px solid var(--marker)", color: "var(--color-neutral-300)", whiteSpace: "nowrap" }}>
                                        <span>
                                          {c.tbl}
                                        </span>
                                        <span style={{ color: "var(--color-neutral-500)" }}>
                                          {c.n}
                                        </span>
                                      </span>
                                    </React.Fragment>
                                  ))}
                                </div>
                              </div>
                            </div>
                            <div>
                              <div style={{ fontSize: "10px", letterSpacing: "0.09em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                                {"What those tables unlock"}
                              </div>
                              <div style={{ display: "flex", flexDirection: "column", gap: "5px", marginTop: "8px" }}>
                                {(r.unlocks || []).map((u, $index) => (
                                  <React.Fragment key={$index}>
                                    <div className="hv6" onClick={u.click} style={{ display: "flex", alignItems: "flex-start", gap: "8px", fontSize: "12px", color: "var(--color-neutral-300)", cursor: "pointer" }}>
                                      <i className="ph ph-arrow-elbow-down-right" style={{ fontSize: "11px", color: "var(--color-neutral-500)", marginTop: "3px", flexShrink: "0" }}></i>
                                      <span style={{ lineHeight: "1.45" }}>
                                        {u.label}
                                      </span>
                                    </div>
                                  </React.Fragment>
                                ))}
                              </div>
                            </div>
                            <div style={{ fontSize: "11px", color: "var(--color-neutral-500)", lineHeight: "1.55", maxWidth: "96ch" }}>
                              {r.note}
                            </div>
                            <div style={{ display: "flex", flexWrap: "wrap", gap: "7px" }}>
                              {(r.acts || []).map((a, $index) => (
                                <React.Fragment key={$index}>
                                  <div className="hv4" onClick={a.click} style={{ fontSize: "11.5px", padding: "6px 11px", borderRadius: "7px", border: `1px solid ${a.edge}`, color: a.fg, cursor: "pointer", whiteSpace: "nowrap" }}>
                                    {a.label}
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
                <div style={{ padding: "12px 15px", fontSize: "10.5px", color: "var(--color-neutral-500)", lineHeight: "1.5" }}>
                  {"Rows are what landed in the last 30 days, counted per table. A degraded or expired connection keeps serving its last good rows — every page that reads them shows how stale they are rather than hiding the gap."}
                </div>
              </div>
            </>
          ) : null}
          {vals.intTabAvailable ? (
            <>
              <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap", marginTop: "14px" }}>
                <div style={{ display: "flex", alignItems: "center", gap: "7px", padding: "7px 11px", borderRadius: "8px", border: "1px solid var(--color-divider)", background: "var(--color-surface)", minWidth: "220px", flex: "0 1 300px" }}>
                  <i className="ph ph-magnifying-glass" style={{ fontSize: "12px", color: "var(--color-neutral-500)" }}></i>
                  <input className="input" value={vals.intQ} onChange={vals.intSetQ} placeholder="Search for an integration" style={{ flex: "1", minWidth: "0", border: "0", background: "transparent", color: "var(--color-text)", fontFamily: "var(--font-body)", fontSize: "12px", outline: "none" }} />
                </div>
                {(vals.intCatChips || []).map((c, $index) => (
                  <React.Fragment key={$index}>
                    <div onClick={c.pick} style={{ fontSize: "11.5px", padding: "6px 11px", borderRadius: "7px", border: `1px solid ${c.edge}`, background: c.bg, color: c.fg, cursor: "pointer", whiteSpace: "nowrap" }}>
                      {c.label}
                    </div>
                  </React.Fragment>
                ))}
              </div>
              {(vals.intCats || []).map((g, $index) => (
                <React.Fragment key={$index}>
                  <div style={{ marginTop: "22px" }}>
                    <div style={{ display: "flex", alignItems: "baseline", gap: "10px" }}>
                      <i className={`ph ${g.icon}`} style={{ fontSize: "14px", color: "var(--color-accent)" }}></i>
                      <span style={{ fontSize: "13px" }}>
                        {g.label}
                      </span>
                      <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10.5px", color: "var(--color-neutral-500)" }}>
                        {g.n}
                      </span>
                      <span style={{ flex: "1", height: "1px", background: "var(--color-divider)" }}></span>
                    </div>
                    <p style={{ fontSize: "11.5px", color: "var(--color-neutral-500)", margin: "7px 0 0", maxWidth: "96ch", lineHeight: "1.5" }}>
                      {g.blurb}
                    </p>
                    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(320px,1fr))", gap: "10px", marginTop: "11px" }}>
                      {(g.items || []).map((i, $index) => (
                        <React.Fragment key={$index}>
                          <div className="hv1" style={{ display: "grid", gridTemplateColumns: "28px minmax(0,1fr) auto", gap: "11px", alignItems: "start", padding: "12px 13px", borderRadius: "10px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", minWidth: "0" }}>
                            <span style={{ width: "28px", height: "28px", borderRadius: "7px", background: i.markBg, color: i.markFg, fontFamily: "ui-monospace,monospace", fontSize: "10px", display: "flex", alignItems: "center", justifyContent: "center" }}>
                              {i.mark}
                            </span>
                            <div style={{ minWidth: "0" }}>
                              <div style={{ display: "flex", alignItems: "baseline", gap: "7px", flexWrap: "wrap" }}>
                                <span style={{ fontSize: "12.5px" }}>
                                  {i.name}
                                </span>
                                <span style={{ fontSize: "10px", color: "var(--color-neutral-500)" }}>
                                  {i.fam}
                                </span>
                              </div>
                              <div style={{ fontSize: "11px", color: "var(--color-neutral-400)", marginTop: "3px", lineHeight: "1.4" }}>
                                {i.gives}
                              </div>
                              <div style={{ display: "flex", flexWrap: "wrap", gap: "5px", marginTop: "7px" }}>
                                {(i.tables || []).map((t, $index) => (
                                  <React.Fragment key={$index}>
                                    <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "9.5px", padding: "2px 6px", borderRadius: "4px", background: "var(--color-neutral-900)", color: "var(--color-neutral-400)", whiteSpace: "nowrap" }}>
                                      {t.label}
                                    </span>
                                  </React.Fragment>
                                ))}
                              </div>
                            </div>
                            <div className="hv13" onClick={i.click} style={{ fontSize: "11.5px", padding: "6px 11px", borderRadius: "7px", border: `1px solid ${i.btnEdge}`, background: i.btnBg, color: i.btnFg, cursor: i.cursor, whiteSpace: "nowrap", alignSelf: "center" }}>
                              {i.btn}
                            </div>
                          </div>
                        </React.Fragment>
                      ))}
                    </div>
                  </div>
                </React.Fragment>
              ))}
              <div style={{ display: vals.intNoneShow, marginTop: "22px", padding: "26px", borderRadius: "11px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", fontSize: "12.5px", color: "var(--color-neutral-400)", lineHeight: "1.6", maxWidth: "70ch" }}>
                {"Nothing in the catalogue matches that. Anything unlisted still connects through the custom API, an SFTP drop or a read replica — the graph only needs rows it can key and date."}
              </div>
            </>
          ) : null}
          {vals.intTabApi ? (
            <>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(340px,1fr))", gap: "14px", marginTop: "14px", alignItems: "start" }}>
                <div style={{ minWidth: "0", borderRadius: "11px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", overflow: "hidden" }}>
                  <div style={{ padding: "11px 15px", borderBottom: "1px solid var(--color-divider)", fontSize: "12.5px" }}>
                    {"Credentials"}
                  </div>
                  <div style={{ padding: "14px 15px", display: "flex", flexDirection: "column", gap: "13px" }}>
                    <div>
                      <div style={{ fontSize: "10px", letterSpacing: "0.09em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                        {"Base URL"}
                      </div>
                      <div style={{ fontFamily: "ui-monospace,monospace", fontSize: "12px", marginTop: "5px" }}>
                        {"https://api.hoistra.com/v1"}
                      </div>
                    </div>
                    <div>
                      <div style={{ fontSize: "10px", letterSpacing: "0.09em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                        {"Bearer token"}
                      </div>
                      <div style={{ display: "flex", alignItems: "center", gap: "9px", marginTop: "5px" }}>
                        <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "12px", flex: "1", minWidth: "0", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                          {vals.intKey}
                        </span>
                        <span onClick={vals.intToggleKey} style={{ fontSize: "11px", color: "var(--color-accent)", cursor: "pointer", whiteSpace: "nowrap" }}>
                          {vals.intKeyLabel}
                        </span>
                      </div>
                      <div style={{ fontSize: "10.5px", color: "var(--color-neutral-500)", marginTop: "5px", lineHeight: "1.45" }}>
                        {"Rotated every 60 days. Last rotation 08 Aug 2026 by the platform team; the previous token stays valid for 24 hours after a rotation."}
                      </div>
                    </div>
                    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(120px,1fr))", gap: "9px" }}>
                      <div style={{ padding: "9px 11px", borderRadius: "8px", background: "var(--color-bg)" }}>
                        <div style={{ fontFamily: "ui-monospace,monospace", fontSize: "15px" }}>
                          {"10k"}
                        </div>
                        <div style={{ fontSize: "10px", color: "var(--color-neutral-500)", marginTop: "2px" }}>
                          {"rows per minute"}
                        </div>
                      </div>
                      <div style={{ padding: "9px 11px", borderRadius: "8px", background: "var(--color-bg)" }}>
                        <div style={{ fontFamily: "ui-monospace,monospace", fontSize: "15px" }}>
                          {"25 MB"}
                        </div>
                        <div style={{ fontSize: "10px", color: "var(--color-neutral-500)", marginTop: "2px" }}>
                          {"per document push"}
                        </div>
                      </div>
                      <div style={{ padding: "9px 11px", borderRadius: "8px", background: "var(--color-bg)" }}>
                        <div style={{ fontFamily: "ui-monospace,monospace", fontSize: "15px" }}>
                          {"99.9%"}
                        </div>
                        <div style={{ fontSize: "10px", color: "var(--color-neutral-500)", marginTop: "2px" }}>
                          {"ingest availability"}
                        </div>
                      </div>
                    </div>
                    <div style={{ display: "flex", flexWrap: "wrap", gap: "7px" }}>
                      <div className="btn btn-primary" onClick={vals.intRotate} style={{ fontSize: "11.5px", padding: "6px 12px", cursor: "pointer" }}>
                        {"Rotate token"}
                      </div>
                      <div className="hv4" onClick={vals.intDocs} style={{ fontSize: "11.5px", padding: "6px 12px", borderRadius: "7px", border: "1px solid var(--color-divider)", color: "var(--color-neutral-400)", cursor: "pointer" }}>
                        {"Read the ingest guide"}
                      </div>
                      <div className="hv4" onClick={vals.intOnPrem} style={{ fontSize: "11.5px", padding: "6px 12px", borderRadius: "7px", border: "1px solid var(--color-divider)", color: "var(--color-neutral-400)", cursor: "pointer" }}>
                        {"On-prem and VPC options"}
                      </div>
                    </div>
                  </div>
                </div>
                <div style={{ minWidth: "0", borderRadius: "11px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", overflow: "hidden" }}>
                  <div style={{ display: "flex", alignItems: "baseline", gap: "10px", padding: "11px 15px", borderBottom: "1px solid var(--color-divider)" }}>
                    <span style={{ fontSize: "12.5px", flex: "1" }}>
                      {"Endpoints"}
                    </span>
                    <span style={{ fontSize: "10.5px", color: "var(--color-neutral-500)" }}>
                      {"same graph the query interface reads"}
                    </span>
                  </div>
                  {(vals.intEndpoints || []).map((e, $index) => (
                    <React.Fragment key={$index}>
                      <div style={{ display: "grid", gridTemplateColumns: "46px minmax(0,1fr)", gap: "11px", padding: "11px 15px", borderBottom: "1px solid var(--color-divider)", alignItems: "start" }}>
                        <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "9.5px", padding: "3px 0", borderRadius: "5px", textAlign: "center", background: e.mBg, color: e.mFg }}>
                          {e.m}
                        </span>
                        <div style={{ minWidth: "0" }}>
                          <div style={{ fontFamily: "ui-monospace,monospace", fontSize: "11.5px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                            {e.p}
                          </div>
                          <div style={{ fontSize: "11px", color: "var(--color-neutral-500)", marginTop: "3px", lineHeight: "1.45" }}>
                            {e.d}
                          </div>
                        </div>
                      </div>
                    </React.Fragment>
                  ))}
                </div>
                <div style={{ minWidth: "0", gridColumn: "1/-1", borderRadius: "11px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", overflow: "hidden" }}>
                  <div style={{ display: "flex", alignItems: "baseline", gap: "10px", flexWrap: "wrap", padding: "11px 15px", borderBottom: "1px solid var(--color-divider)" }}>
                    <span style={{ fontSize: "12.5px" }}>
                      {"Field mapping — portfolio data lake"}
                    </span>
                    <span style={{ fontSize: "11px", color: "var(--color-neutral-500)", flex: "1", minWidth: "180px" }}>
                      {"a source field is only accepted once it resolves to a table and column, and declares which of them is the natural key"}
                    </span>
                  </div>
                  <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) 96px 24px minmax(0,1.2fr) minmax(0,1fr)", gap: "12px", padding: "10px 15px", borderBottom: "1px solid var(--color-divider)", fontSize: "10px", letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                    <span>
                      {"Source field"}
                    </span>
                    <span>
                      {"Type"}
                    </span>
                    <span></span>
                    <span>
                      {"Graph target"}
                    </span>
                    <span>
                      {"Why it matters"}
                    </span>
                  </div>
                  {(vals.intMapping || []).map((m, $index) => (
                    <React.Fragment key={$index}>
                      <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) 96px 24px minmax(0,1.2fr) minmax(0,1fr)", gap: "12px", padding: "10px 15px", borderBottom: "1px solid var(--color-divider)", alignItems: "center", fontSize: "11.5px" }}>
                        <span style={{ fontFamily: "ui-monospace,monospace", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                          {m.src}
                        </span>
                        <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10.5px", color: "var(--color-neutral-500)" }}>
                          {m.type}
                        </span>
                        <i className="ph ph-arrow-right" style={{ fontSize: "11px", color: "var(--color-neutral-500)" }}></i>
                        <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "11px", color: "var(--color-accent)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                          {m.target}
                        </span>
                        <span style={{ fontSize: "11px", color: "var(--color-neutral-500)" }}>
                          {m.note}
                        </span>
                      </div>
                    </React.Fragment>
                  ))}
                  <div style={{ padding: "12px 15px", fontSize: "10.5px", color: "var(--color-neutral-500)", lineHeight: "1.5" }}>
                    {"This page is a saved report over the connection tables. The same thing can be asked in the query interface — ask which sources feed a table, or what a report would gain from a source that is not connected yet."}
                  </div>
                </div>
              </div>
            </>
          ) : null}
        </div>
      </div>
  );
}
