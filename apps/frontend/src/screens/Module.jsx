// Module — Energy, Assets, Maintenance
// Ported from the Hoistra prototype template. `vals` is the view model from useHoistra().
import React from 'react';
import Assets from './Assets.jsx';
import Maintenance from './Maintenance.jsx';

export default function Module({ vals }) {
  return (
      <div style={{ flex: "1", display: "flex", justifyContent: "center", padding: "0 32px 80px" }}>
        <div style={{ width: "100%", maxWidth: "1180px", animation: "fadeUp 0.28s ease both" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "12px", padding: "24px 0 0" }}>
            <div className="hv18" onClick={vals.goHome} style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "12px", color: "var(--color-neutral-400)", cursor: "pointer" }}>
              <i className="ph ph-arrow-left" style={{ fontSize: "12px" }}></i>
              <span>
                {"Home"}
              </span>
            </div>
            <span style={{ color: "var(--color-neutral-700)" }}>
              {"/"}
            </span>
            <span style={{ fontSize: "12px", color: "var(--color-neutral-400)" }}>
              {"Saved spaces"}
            </span>
            <span style={{ color: "var(--color-neutral-700)" }}>
              {"/"}
            </span>
            <span style={{ fontSize: "12px" }}>
              {vals.mod.name}
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
          <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", flexWrap: "wrap", gap: "16px 24px", marginTop: "20px" }}>
            <div style={{ minWidth: "0", flex: "1 1 260px" }}>
              <div style={{ fontSize: "10.5px", letterSpacing: "0.13em", textTransform: "uppercase", color: "var(--color-accent-300)" }}>
                {vals.mod.kicker}
              </div>
              <h2 style={{ fontSize: "28px", margin: "7px 0 0", lineHeight: "1.15" }}>
                {vals.mod.name}
              </h2>
              <p style={{ fontSize: "13px", color: "var(--color-neutral-400)", margin: "8px 0 0", maxWidth: "76ch", lineHeight: "1.55" }}>
                {vals.mod.blurb}
              </p>
            </div>
            <div style={{ display: "flex", alignItems: "center", flexWrap: "wrap", gap: "10px" }}>
              <div className="btn btn-primary" onClick={vals.mod.scan} style={{ fontSize: "12px", padding: "7px 14px", cursor: "pointer" }}>
                {vals.mod.scanLabel}
              </div>
              <div style={{ padding: "8px 13px", borderRadius: "9px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", display: "flex", flexDirection: "column", gap: "2px", whiteSpace: "nowrap" }}>
                <span style={{ fontSize: "9.5px", letterSpacing: "0.11em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                  {"Last run"}
                </span>
                <span style={{ fontSize: "12.5px", fontVariantNumeric: "tabular-nums" }}>
                  {vals.modLastRun}
                </span>
              </div>
            </div>
          </div>
          {vals.isEnergy ? (
            <>
              <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap", marginTop: "22px" }}>
                <span style={{ fontSize: "10px", letterSpacing: "0.12em", textTransform: "uppercase", color: "var(--color-neutral-500)", marginRight: "2px" }}>
                  {"Scope"}
                </span>
                {(vals.enChips || []).map((c, $index) => (
                  <React.Fragment key={$index}>
                    <div onClick={c.pick} style={{ display: "flex", alignItems: "baseline", gap: "6px", fontSize: "11.5px", padding: "5px 11px", borderRadius: "7px", border: `1px solid ${c.edge}`, background: c.bg, color: c.fg, cursor: "pointer", whiteSpace: "nowrap" }}>
                      <span>
                        {c.label}
                      </span>
                      <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10px", color: "var(--color-neutral-500)" }}>
                        {c.n}
                      </span>
                    </div>
                  </React.Fragment>
                ))}
              </div>
              <div style={{ display: "flex", alignItems: "flex-start", gap: "10px", marginTop: "12px", padding: "11px 14px", borderRadius: "10px", background: vals.enFidelityBg }}>
                <i className={`ph ${vals.enFidelityIcon}`} style={{ fontSize: "15px", color: vals.enFidelityFg, flexShrink: "0", marginTop: "1px" }}></i>
                <div style={{ minWidth: "0" }}>
                  <div style={{ fontSize: "12.5px", color: vals.enFidelityFg }}>
                    {vals.enFidelity}
                  </div>
                  <div style={{ fontSize: "11.5px", color: "var(--color-text)", marginTop: "4px", lineHeight: "1.5", maxWidth: "96ch" }}>
                    {vals.enFidelityNote}
                  </div>
                </div>
              </div>
            </>
          ) : null}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: "11px", marginTop: "24px" }}>
            {(vals.modMetrics || []).map((m, $index) => (
              <React.Fragment key={$index}>
                <div style={{ padding: "14px", borderRadius: "10px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", position: "relative", overflow: "hidden" }}>
                  <div style={{ position: "absolute", left: "0", top: "0", bottom: "0", width: "2px", background: m.color }}></div>
                  <div style={{ fontSize: "11px", color: "var(--color-neutral-500)" }}>
                    {m.l}
                  </div>
                  <div style={{ fontSize: "24px", marginTop: "6px", lineHeight: "1", color: m.color }}>
                    {m.v}
                  </div>
                  <div style={{ fontSize: "11px", color: "var(--color-neutral-300)", marginTop: "5px", lineHeight: "1.4" }}>
                    {m.s}
                  </div>
                </div>
              </React.Fragment>
            ))}
          </div>
          {vals.isEnergy ? (
            <>
              <div style={{ marginTop: "16px" }}>
                <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap" }}>
                  <span style={{ fontSize: "10px", letterSpacing: "0.12em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                    {"Ratings and duties"}
                  </span>
                  <span style={{ fontSize: "10px", color: "var(--color-neutral-500)" }}>
                    {"·"}
                  </span>
                  {vals.enRatingSingle ? (
                    <>
                      <span style={{ fontSize: "12px" }}>
                        {vals.enRatingName}
                      </span>
                    </>
                  ) : null}
                  {vals.enRatingMulti ? (
                    <>
                      <select value={vals.enRatingCc} onChange={vals.enRatingPick} style={{ fontFamily: "var(--font-body)", fontSize: "12px", padding: "4px 8px", borderRadius: "6px", border: "1px solid var(--color-accent)", background: "var(--color-accent-900)", color: "var(--color-accent)", outline: "none", cursor: "pointer" }}>
                        {(vals.enRatingOptions || []).map((o, $index) => (
                          <React.Fragment key={$index}>
                            <option value={o.cc}>
                              {o.label}
                            </option>
                          </React.Fragment>
                        ))}
                      </select>
                      <span style={{ fontSize: "10.5px", color: "var(--color-neutral-500)" }}>
                        {vals.enRatingHint}
                      </span>
                    </>
                  ) : null}
                  <span style={{ flex: "1", height: "1px", background: "var(--color-divider)", minWidth: "40px" }}></span>
                </div>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(210px,1fr))", gap: "11px", marginTop: "11px" }}>
                  {(vals.enRatings || []).map((r, $index) => (
                    <React.Fragment key={$index}>
                      <div style={{ padding: "14px", borderRadius: "10px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", position: "relative", overflow: "hidden" }}>
                        <div style={{ position: "absolute", left: "0", top: "0", bottom: "0", width: "2px", background: r.color }}></div>
                        <div style={{ fontSize: "11px", color: "var(--color-neutral-500)" }}>
                          {r.l}
                        </div>
                        <div style={{ fontSize: "24px", marginTop: "6px", lineHeight: "1", color: r.color }}>
                          {r.v}
                        </div>
                        <div style={{ fontSize: "11px", color: "var(--color-neutral-300)", marginTop: "5px", lineHeight: "1.4", textWrap: "pretty" }}>
                          {r.s}
                        </div>
                        <div style={{ fontFamily: "ui-monospace,monospace", fontSize: "9.5px", color: r.badgeFg, marginTop: "9px", lineHeight: "1.4" }}>
                          {r.badge}
                        </div>
                        <div style={{ display: r.confShow, height: "3px", borderRadius: "2px", background: "var(--color-neutral-900)", overflow: "hidden", marginTop: "5px" }}>
                          <div style={{ height: "100%", borderRadius: "2px", width: r.confPct, background: r.confFg }}></div>
                        </div>
                      </div>
                    </React.Fragment>
                  ))}
                </div>
              </div>
              <div style={{ display: "flex", alignItems: "baseline", gap: "10px", marginTop: "22px" }}>
                <span style={{ fontSize: "10px", letterSpacing: "0.12em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                  {"Scope of this analysis"}
                </span>
                <span style={{ flex: "1", height: "1px", background: "var(--color-divider)" }}></span>
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(300px,1fr))", gap: "12px", marginTop: "11px", alignItems: "start" }}>
                <div style={{ minWidth: "0", borderRadius: "10px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", overflow: "hidden" }}>
                  <div style={{ padding: "10px 14px", borderBottom: "1px solid var(--color-divider)", fontSize: "12px", color: "var(--st-ok)" }}>
                    {vals.enAvailTitle}
                  </div>
                  <div style={{ padding: "11px 14px", display: "flex", flexDirection: "column", gap: "7px" }}>
                    {(vals.enAvail || []).map((a, $index) => (
                      <React.Fragment key={$index}>
                        <div style={{ display: "flex", alignItems: "flex-start", gap: "8px" }}>
                          <i className="ph ph-check" style={{ fontSize: "11px", color: "var(--st-ok)", marginTop: "3px", flexShrink: "0" }}></i>
                          <span style={{ fontSize: "11.5px", lineHeight: "1.45", color: "var(--color-neutral-300)", textWrap: "pretty" }}>
                            {a.label}
                          </span>
                        </div>
                      </React.Fragment>
                    ))}
                  </div>
                </div>
                <div style={{ minWidth: "0", borderRadius: "10px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", overflow: "hidden" }}>
                  <div style={{ padding: "10px 14px", borderBottom: "1px solid var(--color-divider)", fontSize: "12px", color: "var(--st-warn)" }}>
                    {vals.enHeldTitle}
                  </div>
                  <div style={{ padding: "11px 14px", display: "flex", flexDirection: "column", gap: "7px" }}>
                    {(vals.enHeld || []).map((h, $index) => (
                      <React.Fragment key={$index}>
                        <div style={{ display: "flex", alignItems: "flex-start", gap: "8px" }}>
                          <i className="ph ph-minus" style={{ fontSize: "11px", color: "var(--st-warn)", marginTop: "3px", flexShrink: "0" }}></i>
                          <span style={{ fontSize: "11.5px", lineHeight: "1.45", color: "var(--color-neutral-300)", textWrap: "pretty" }}>
                            {h.label}
                          </span>
                        </div>
                      </React.Fragment>
                    ))}
                  </div>
                </div>
              </div>
              <div style={{ marginTop: "22px", borderRadius: "11px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", overflow: "hidden" }}>
                <div className="hv2" onClick={vals.enMatrixToggle} style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap", padding: "11px 15px", cursor: "pointer", borderBottom: vals.enMatrixEdge }}>
                  <i className={`ph ${vals.enMatrixCaret}`} style={{ fontSize: "12px", color: "var(--color-neutral-500)", flexShrink: "0" }}></i>
                  <span style={{ fontSize: "12.5px" }}>
                    {vals.enMatrixTitle}
                  </span>
                  <span style={{ fontSize: "11px", color: "var(--color-neutral-500)", flex: "1", minWidth: "200px", lineHeight: "1.45" }}>
                    {vals.enMatrixNote}
                  </span>
                  <span style={{ fontSize: "11px", color: "var(--color-accent)", whiteSpace: "nowrap" }}>
                    {vals.enMatrixCta}
                  </span>
                </div>
                <div style={{ overflowX: "auto", display: vals.enMatrixShow }}>
                  <div style={{ display: "grid", gridTemplateColumns: vals.enMatrixCols, gap: "12px", padding: "10px 15px", borderBottom: "1px solid var(--color-divider)", fontSize: "10px", letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                    <span></span>
                    {(vals.enMatrixHead || []).map((h, $index) => (
                      <React.Fragment key={$index}>
                        <span style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
                          <span style={{ color: "var(--color-text)", textTransform: "none", letterSpacing: "0", fontSize: "12px" }}>
                            {h.label}
                          </span>
                          <span style={{ fontFamily: "ui-monospace,monospace", textTransform: "none", letterSpacing: "0" }}>
                            {h.n}
                          </span>
                        </span>
                      </React.Fragment>
                    ))}
                  </div>
                  {(vals.enMatrixRows || []).map((r, $index) => (
                    <React.Fragment key={$index}>
                      <div>
                        <div style={{ display: r.sectionShow, alignItems: "center", gap: "8px", padding: "9px 15px 4px", fontSize: "10px", letterSpacing: "0.11em", textTransform: "uppercase", color: "var(--color-accent)" }}>
                          {r.section}
                        </div>
                        <div style={{ display: "grid", gridTemplateColumns: vals.enMatrixCols, gap: "12px", padding: "7px 15px", borderBottom: "1px solid var(--color-divider)", alignItems: "start" }}>
                          <span style={{ fontSize: "11.5px", color: "var(--color-neutral-400)" }}>
                            {r.label}
                          </span>
                          {(r.cells || []).map((c, $index) => (
                            <React.Fragment key={$index}>
                              <span style={{ fontSize: "11.5px", lineHeight: "1.45", textWrap: "pretty", minWidth: "0" }}>
                                {c.v}
                              </span>
                            </React.Fragment>
                          ))}
                        </div>
                      </div>
                    </React.Fragment>
                  ))}
                </div>
              </div>
            </>
          ) : null}
          {vals.isNotEnergy ? (
            <>
              <div style={{ display: "grid", gridTemplateColumns: vals.contentCols, gap: "26px", marginTop: "32px", alignItems: "start" }}>
                <div>
                  <div style={{ display: "flex", alignItems: "center", gap: "9px", marginBottom: "12px" }}>
                    <div style={{ fontSize: "11px", letterSpacing: "0.11em", textTransform: "uppercase", color: "var(--color-neutral-500)", flex: "1" }}>
                      {vals.mod.tableTitle}
                    </div>
                    {(vals.modFilters || []).map((f, $index) => (
                      <React.Fragment key={$index}>
                        <div onClick={f.click} style={{ fontSize: "11px", padding: "4px 10px", borderRadius: "20px", cursor: "pointer", border: `1px solid ${f.border}`, color: f.fg, background: f.bg }}>
                          {f.label}
                        </div>
                      </React.Fragment>
                    ))}
                  </div>
                  <div style={{ overflowX: "auto" }}>
                    <table className="table" style={{ width: "100%", minWidth: "640px", fontSize: "12.5px" }}>
                      <thead>
                        <tr>
                          {(vals.mod.head || []).map((h, $index) => (
                            <th key={$index} style={{ textAlign: "left", fontSize: "10.5px", letterSpacing: "0.07em", textTransform: "uppercase", color: "var(--color-neutral-500)", fontWeight: "400", padding: "8px 10px" }}>
                              {h}
                            </th>
                          ))}
                          <th style={{ display: vals.modInvHead, padding: "8px 10px" }}></th>
                        </tr>
                      </thead>
                      <tbody>
                        {(vals.modRows || []).map((r, $index) => (
                          <React.Fragment key={$index}>
                            <tr style={{ display: r.groupShow }}>
                              <td colSpan="7" style={{ padding: "12px 10px 6px", borderBottom: "1px solid var(--color-divider)" }}>
                                <div style={{ display: "flex", alignItems: "baseline", gap: "9px" }}>
                                  <span style={{ fontSize: "12px" }}>
                                    {r.group}
                                  </span>
                                  <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10.5px", color: "var(--color-neutral-500)" }}>
                                    {r.groupMeta}
                                  </span>
                                  <span style={{ flex: "1" }}></span>
                                  <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "11px", color: "var(--color-neutral-300)" }}>
                                    {r.groupSum}
                                  </span>
                                </div>
                              </td>
                            </tr>
                            <tr className="hv19" onClick={r.click} style={{ cursor: "pointer", display: r.rowShow }}>
                              <td style={{ padding: "11px 10px" }}>
                                {r.c0}
                              </td>
                              <td style={{ padding: "11px 10px", color: "var(--color-neutral-300)" }}>
                                {r.c1}
                              </td>
                              <td style={{ padding: "11px 10px", color: "var(--color-neutral-400)" }}>
                                {r.c2}
                              </td>
                              <td style={{ padding: "11px 10px", fontFamily: "ui-monospace,monospace", color: "var(--color-neutral-300)" }}>
                                {r.c3}
                              </td>
                              <td style={{ padding: "11px 10px" }}>
                                <span style={{ display: "inline-block", whiteSpace: "nowrap", fontSize: "11px", padding: "3px 8px", borderRadius: "5px", color: r.color, background: r.bg }}>
                                  {r.c4}
                                </span>
                              </td>
                              <td style={{ padding: "11px 10px", color: "var(--color-neutral-500)", fontSize: "11.5px" }}>
                                {r.c5}
                              </td>
                              <td style={{ padding: "11px 10px", display: r.invShow }}>
                                <div className="hv15" onClick={r.investigate} style={{ display: "inline-flex", alignItems: "center", gap: "6px", fontSize: "11px", padding: "5px 10px", borderRadius: "7px", border: "1px solid var(--color-accent)", color: "var(--color-accent)", cursor: "pointer", whiteSpace: "nowrap" }}>
                                  <i className="ph ph-magnifying-glass" style={{ fontSize: "11px" }}></i>
                                  <span>
                                    {"Investigate"}
                                  </span>
                                </div>
                              </td>
                            </tr>
                          </React.Fragment>
                        ))}
                      </tbody>
                    </table>
                  </div>
                  <div style={{ fontSize: "11px", color: "var(--color-neutral-600)", marginTop: "12px" }}>
                    {vals.mod.tableFoot}
                  </div>
                </div>
                <div>
                  <div style={{ fontSize: "11px", letterSpacing: "0.11em", textTransform: "uppercase", color: "var(--color-neutral-500)", marginBottom: "11px" }}>
                    {vals.mod.sideTitle}
                  </div>
                  <div style={{ padding: "15px", borderRadius: "10px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", display: "flex", flexDirection: "column", gap: "11px" }}>
                    {(vals.modBars || []).map((b, $index) => (
                      <React.Fragment key={$index}>
                        <div>
                          <div style={{ display: b.groupShow, alignItems: "baseline", gap: "8px", padding: "6px 0 4px", borderBottom: "1px solid var(--color-divider)", marginBottom: "6px" }}>
                            <span style={{ fontSize: "11.5px" }}>
                              {b.group}
                            </span>
                            <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10px", color: "var(--color-neutral-500)" }}>
                              {b.groupMeta}
                            </span>
                          </div>
                          <div style={{ display: b.rowShow, flexDirection: "column", gap: "5px" }}>
                            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "8px", fontSize: "11.5px" }}>
                              <span style={{ color: "var(--color-neutral-300)", minWidth: "0", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                {b.label}
                              </span>
                              <span style={{ display: "flex", alignItems: "center", gap: "8px", flexShrink: "0" }}>
                                <span style={{ fontFamily: "ui-monospace,monospace", color: b.color }}>
                                  {b.val}
                                </span>
                                <i className="ph ph-magnifying-glass hv15" onClick={b.investigate} title="Investigate" style={{ display: b.invShow, fontSize: "12px", color: "var(--color-accent)", cursor: "pointer", padding: "3px", borderRadius: "5px", border: "1px solid var(--color-accent)" }}></i>
                              </span>
                            </div>
                            <div style={{ height: "4px", borderRadius: "3px", background: "var(--color-neutral-900)", overflow: "hidden" }}>
                              <div style={{ height: "100%", borderRadius: "3px", width: b.pct, background: b.color }}></div>
                            </div>
                          </div>
                        </div>
                      </React.Fragment>
                    ))}
                  </div>
                  <div style={{ fontSize: "11px", color: "var(--color-neutral-600)", marginTop: "11px", lineHeight: "1.55" }}>
                    {vals.mod.sideFoot}
                  </div>
                  <div style={{ fontSize: "11px", letterSpacing: "0.11em", textTransform: "uppercase", color: "var(--color-neutral-500)", margin: "26px 0 11px" }}>
                    {"Ask about this module"}
                  </div>
                  <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                    {(vals.modAsks || []).map((a, $index) => (
                      <React.Fragment key={$index}>
                        <div className="hv17" onClick={a.run} style={{ fontSize: "12px", padding: "10px 12px", borderRadius: "9px", border: "1px solid var(--color-divider)", color: "var(--color-neutral-300)", cursor: "pointer", lineHeight: "1.45" }}>
                          {a.label}
                        </div>
                      </React.Fragment>
                    ))}
                  </div>
                </div>
              </div>
            </>
          ) : null}
          {vals.isAssets ? <Assets vals={vals} /> : null}
          {vals.isMaint ? <Maintenance vals={vals} /> : null}
          {vals.isEnergy ? (
            <>
              <div style={{ display: "flex", alignItems: "center", gap: "9px", marginTop: "32px", flexWrap: "wrap" }}>
                <div style={{ fontSize: "11px", letterSpacing: "0.11em", textTransform: "uppercase", color: "var(--color-neutral-500)", flex: "1", minWidth: "200px" }}>
                  {"Buildings · EUI against their own pack, anomalies beneath"}
                </div>
                {(vals.modFilters || []).map((f, $index) => (
                  <React.Fragment key={$index}>
                    <div onClick={f.click} style={{ fontSize: "11px", padding: "4px 10px", borderRadius: "20px", cursor: "pointer", border: `1px solid ${f.border}`, color: f.fg, background: f.bg }}>
                      {f.label}
                    </div>
                  </React.Fragment>
                ))}
                <span style={{ fontSize: "11px", color: "var(--color-neutral-500)", fontVariantNumeric: "tabular-nums" }}>
                  {vals.enListSummary}
                </span>
              </div>
              <div style={{ display: vals.enBldQueryShow, alignItems: "center", gap: "10px", padding: "9px 13px", borderRadius: "10px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", marginTop: "12px", maxWidth: "420px" }}>
                <i className="ph ph-magnifying-glass" style={{ fontSize: "14px", color: "var(--color-neutral-500)", flexShrink: "0" }}></i>
                <input className="input" value={vals.enBldQuery} onChange={vals.setEnBldQuery} placeholder="Search buildings by name…" style={{ flex: "1", minWidth: "0", background: "transparent", border: "none", outline: "none", fontFamily: "var(--font-body)", fontSize: "13px", color: "var(--color-text)" }} />
                {vals.enBldQuery ? (
                  <i className="ph ph-x hv11" onClick={() => vals.setEnBldQuery({ target: { value: "" } })} style={{ fontSize: "13px", color: "var(--color-neutral-500)", cursor: "pointer", flexShrink: "0" }}></i>
                ) : null}
              </div>
              {(vals.enGroups || []).map((g, $index) => (
                <React.Fragment key={$index}>
                  <div style={{ marginTop: "18px" }}>
                    <div style={{ display: "flex", alignItems: "baseline", gap: "10px", flexWrap: "wrap", paddingBottom: "7px", borderBottom: "1px solid var(--color-divider)" }}>
                      <span style={{ fontSize: "13px", whiteSpace: "nowrap" }}>
                        {g.label}
                      </span>
                      <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10.5px", color: "var(--color-accent)", whiteSpace: "nowrap" }}>
                        {g.std}
                      </span>
                      <span style={{ flex: "1", minWidth: "8px" }}></span>
                      <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10.5px", color: "var(--color-neutral-500)", minWidth: "0", lineHeight: "1.4", textWrap: "pretty" }}>
                        {g.meta}
                      </span>
                    </div>
                    <div style={{ display: "flex", flexDirection: "column", gap: "8px", marginTop: "10px" }}>
                      {(g.buildings || []).map((b, $index) => (
                        <React.Fragment key={$index}>
                          <div style={{ borderRadius: "11px", background: b.bg, boxShadow: "var(--shadow-sm)", overflow: "hidden" }}>
                            <div className="hv2" onClick={b.toggle} style={{ display: "flex", flexDirection: "column", gap: "9px", padding: "12px 14px", cursor: "pointer" }}>
                              <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                                <i className={`ph ${b.caret}`} style={{ fontSize: "11px", color: "var(--color-neutral-500)", flexShrink: "0" }}></i>
                                <div style={{ minWidth: "0", flex: "1" }}>
                                  <div style={{ fontSize: "13px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                    {b.name}
                                  </div>
                                  <div style={{ fontSize: "10px", color: b.granFg, marginTop: "2px", lineHeight: "1.35", textWrap: "pretty" }}>
                                    {b.route}
                                  </div>
                                </div>
                                <div className="hv15" onClick={b.investigate} style={{ display: "inline-flex", alignItems: "center", gap: "6px", fontSize: "11px", padding: "6px 11px", borderRadius: "7px", border: "1px solid var(--color-accent)", color: "var(--color-accent)", cursor: "pointer", whiteSpace: "nowrap", flexShrink: "0" }}>
                                  <i className="ph ph-magnifying-glass" style={{ fontSize: "11px" }}></i>
                                  <span>
                                    {"Investigate building"}
                                  </span>
                                </div>
                              </div>
                              <div style={{ display: "flex", alignItems: "center", gap: "14px", flexWrap: "wrap", paddingLeft: "21px" }}>
                                <div style={{ flex: "1 1 220px", minWidth: "0" }}>
                                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: "8px", fontSize: "11.5px" }}>
                                    <span style={{ fontFamily: "ui-monospace,monospace", color: b.deltaFg, whiteSpace: "nowrap" }}>
                                      {b.eui}{" "}
                                      <span style={{ color: "var(--color-neutral-500)" }}>
                                        {b.bench}
                                      </span>
                                    </span>
                                    <span style={{ fontFamily: "ui-monospace,monospace", color: b.deltaFg }}>
                                      {b.delta}
                                    </span>
                                  </div>
                                  <div style={{ position: "relative", height: "5px", borderRadius: "3px", background: "var(--color-neutral-900)", overflow: "hidden", marginTop: "5px" }}>
                                    <div style={{ height: "100%", borderRadius: "3px", width: b.barPct, background: b.barColor }}></div>
                                    <div style={{ position: "absolute", top: "-1px", bottom: "-1px", left: b.refPct, width: "1.5px", background: "var(--color-text)" }}></div>
                                  </div>
                                </div>
                                <div style={{ flex: "0 1 200px", minWidth: "140px", textAlign: "right" }}>
                                  <div style={{ fontFamily: "ui-monospace,monospace", fontSize: "11.5px", color: b.excessFg, whiteSpace: "nowrap" }}>
                                    {b.excess}
                                  </div>
                                  <div style={{ fontSize: "10.5px", color: b.anomFg, marginTop: "2px", whiteSpace: "nowrap" }}>
                                    {b.anomN}
                                  </div>
                                </div>
                              </div>
                            </div>
                            <div style={{ display: b.openShow, borderTop: "1px solid var(--color-divider)", background: "var(--color-surface)" }}>
                              <div style={{ display: "flex", alignItems: "baseline", gap: "10px", padding: "8px 14px 6px 42px", fontSize: "10px", letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--color-neutral-500)", borderBottom: "1px solid var(--color-divider)" }}>
                                <span>
                                  {"Anomalies beneath this number"}
                                </span>
                              </div>
                              {(b.anomalies || []).map((a, $index) => (
                                <React.Fragment key={$index}>
                                  <div className="hv19" onClick={a.open} style={{ display: "flex", alignItems: "center", gap: "12px", flexWrap: "wrap", padding: "10px 14px 10px 42px", borderBottom: "1px solid var(--color-divider)", fontSize: "12px", cursor: "pointer" }}>
                                    <div style={{ flex: "1 1 200px", minWidth: "0" }}>
                                      <div style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                        {a.asset}
                                      </div>
                                      <div style={{ fontSize: "11px", color: "var(--color-neutral-400)", marginTop: "2px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                        {a.type}
                                      </div>
                                    </div>
                                    <div style={{ flex: "0 0 auto", textAlign: "right" }}>
                                      <div style={{ fontFamily: "ui-monospace,monospace", color: "var(--color-neutral-300)", whiteSpace: "nowrap" }}>
                                        {a.impact}
                                      </div>
                                      <div style={{ color: "var(--color-neutral-500)", fontSize: "10.5px", marginTop: "2px", whiteSpace: "nowrap" }}>
                                        {a.days}
                                      </div>
                                    </div>
                                    <span style={{ display: "inline-block", whiteSpace: "nowrap", fontSize: "11px", padding: "3px 8px", borderRadius: "5px", color: a.color, background: a.bg, flex: "0 0 auto" }}>
                                      {a.status}
                                    </span>
                                    <div className="hv15" onClick={a.investigate} style={{ display: "inline-flex", alignItems: "center", gap: "6px", fontSize: "11px", padding: "5px 10px", borderRadius: "7px", border: "1px solid var(--color-accent)", color: "var(--color-accent)", cursor: "pointer", whiteSpace: "nowrap", flex: "0 0 auto" }}>
                                      <i className="ph ph-magnifying-glass" style={{ fontSize: "11px" }}></i>
                                      <span>
                                        {"Investigate"}
                                      </span>
                                    </div>
                                  </div>
                                </React.Fragment>
                              ))}
                              <div style={{ display: b.emptyShow, padding: "11px 14px 11px 42px", fontSize: "11.5px", color: "var(--color-neutral-500)", lineHeight: "1.5" }}>
                                {b.emptyText}
                              </div>
                            </div>
                          </div>
                        </React.Fragment>
                      ))}
                    </div>
                  </div>
                </React.Fragment>
              ))}
              <div style={{ display: vals.enListEmpty, marginTop: "18px", padding: "22px", borderRadius: "11px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", fontSize: "12.5px", color: "var(--color-neutral-400)", lineHeight: "1.6" }}>
                {"No building in scope matches this filter."}
              </div>
              <div style={{ display: vals.enBldPagerShow, alignItems: "center", justifyContent: "space-between", gap: "12px", marginTop: "14px", padding: "9px 13px", borderRadius: "10px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)" }}>
                <span style={{ fontSize: "11px", color: "var(--color-neutral-500)" }}>{vals.enListSummary}</span>
                <div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
                  <div className="hv13" onClick={vals.enBldPagePrev} style={{ fontSize: "11.5px", padding: "5px 11px", borderRadius: "7px", border: "1px solid var(--color-divider)", color: vals.enBldPagePrevShow ? "var(--color-text)" : "var(--color-neutral-500)", cursor: vals.enBldPagePrevShow ? "pointer" : "default", opacity: vals.enBldPagePrevShow ? "1" : "0.45" }}>{"Prev"}</div>
                  <span style={{ fontSize: "11px", color: "var(--color-neutral-500)", padding: "0 4px" }}>{"Page " + (vals.enBldPage + 1) + " of " + vals.enBldPageCount}</span>
                  <div className="hv13" onClick={vals.enBldPageNext} style={{ fontSize: "11.5px", padding: "5px 11px", borderRadius: "7px", border: "1px solid var(--color-divider)", color: vals.enBldPageNextShow ? "var(--color-text)" : "var(--color-neutral-500)", cursor: vals.enBldPageNextShow ? "pointer" : "default", opacity: vals.enBldPageNextShow ? "1" : "0.45" }}>{"Next"}</div>
                </div>
              </div>
              <div style={{ fontSize: "11px", color: "var(--color-neutral-600)", marginTop: "12px", lineHeight: "1.55" }}>
                {vals.mod.sideFoot}{" The marker on each bar is the building's reference; open a building to see the anomalies beneath its number, and investigate either the building or a single anomaly."}
              </div>
              <div style={{ marginTop: "18px", borderRadius: "10px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", overflow: "hidden" }}>
                <div className="hv2" onClick={vals.enRulesToggle} style={{ display: "flex", alignItems: "center", gap: "10px", padding: "10px 14px", cursor: "pointer" }}>
                  <i className={`ph ${vals.enRulesCaret}`} style={{ fontSize: "12px", color: "var(--color-neutral-500)" }}></i>
                  <span style={{ fontSize: "12.5px" }}>
                    {"Detection rules"}
                  </span>
                  <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10.5px", color: "var(--color-neutral-500)" }}>
                    {vals.enRulesLiveN}{" of "}{vals.enRulesN}{" implemented · coverage shown per building in scope for those"}
                  </span>
                </div>
                <div style={{ display: vals.enRulesShow }}>
                  {(vals.enRules || []).map((r, $index) => (
                    <React.Fragment key={$index}>
                      <div style={{ display: "grid", gridTemplateColumns: "minmax(150px,0.9fr) minmax(0,1.8fr) minmax(120px,0.9fr) 64px", gap: "12px", alignItems: "start", padding: "9px 14px", borderTop: "1px solid var(--color-divider)" }}>
                        <div style={{ minWidth: "0" }}>
                          <div style={{ fontSize: "12px" }}>
                            {r.name}
                          </div>
                          <span style={{ display: "inline-block", marginTop: "4px", fontFamily: "ui-monospace,monospace", fontSize: "9px", letterSpacing: "0.06em", textTransform: "uppercase", padding: "1.5px 6px", borderRadius: "4px", background: r.clsBg, color: r.clsFg }}>
                            {r.cls}
                          </span>
                        </div>
                        <div style={{ fontSize: "11.5px", color: "var(--color-neutral-300)", lineHeight: "1.45", textWrap: "pretty" }}>
                          {r.test}
                        </div>
                        <div style={{ fontSize: "10.5px", color: "var(--color-neutral-500)", lineHeight: "1.4" }}>
                          {"needs "}{r.needs}
                        </div>
                        <div style={{ fontFamily: "ui-monospace,monospace", fontSize: "11px", color: r.coverFg, textAlign: "right" }}>
                          {r.cover}
                        </div>
                      </div>
                    </React.Fragment>
                  ))}
                  <div style={{ padding: "10px 14px", borderTop: "1px solid var(--color-divider)", fontSize: "10.5px", color: "var(--color-neutral-500)", lineHeight: "1.5" }}>
                    {"Coverage is how many buildings in scope have the data route a rule needs. A rule that cannot arm on a building is shown as such rather than silently skipped; the fix is a data route, not a threshold."}
                  </div>
                </div>
              </div>
              <div style={{ fontSize: "11px", letterSpacing: "0.11em", textTransform: "uppercase", color: "var(--color-neutral-500)", margin: "26px 0 11px" }}>
                {"Ask about this module"}
              </div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: "8px" }}>
                {(vals.modAsks || []).map((a, $index) => (
                  <React.Fragment key={$index}>
                    <div className="hv17" onClick={a.run} style={{ fontSize: "12px", padding: "9px 12px", borderRadius: "9px", border: "1px solid var(--color-divider)", color: "var(--color-neutral-300)", cursor: "pointer", lineHeight: "1.45" }}>
                      {a.label}
                    </div>
                  </React.Fragment>
                ))}
              </div>
            </>
          ) : null}
        </div>
      </div>
  );
}
