// Vendors — contract performance
// Ported from the Hoistra prototype template. `vals` is the view model from useHoistra().
import React from 'react';

export default function Vendors({ vals }) {
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
              {"Vendors"}
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
            <div style={{ minWidth: "0", flex: "1 1 320px" }}>
              <div style={{ fontSize: "10.5px", letterSpacing: "0.13em", textTransform: "uppercase", color: "var(--color-accent)" }}>
                {"Contract performance"}
              </div>
              <h2 style={{ fontSize: "28px", margin: "7px 0 0", lineHeight: "1.15" }}>
                {"Vendors"}
              </h2>
              <p style={{ fontSize: "13px", color: "var(--color-neutral-400)", margin: "8px 0 0", maxWidth: "84ch", lineHeight: "1.55" }}>
                {"Contract terms, monthly scorecards, compliance coverage and invoice checking for every vendor serving your buildings. No score is asserted — each one decomposes to the clause it was measured against and the work orders behind it."}
              </p>
            </div>
            <div style={{ display: "flex", alignItems: "center", flexWrap: "wrap", gap: "10px" }}>
              <div className="btn btn-primary" onClick={vals.vpRebuild} style={{ fontSize: "12px", padding: "7px 14px", cursor: "pointer" }}>
                {"Rebuild scorecards"}
              </div>
              <div className="hv4" onClick={vals.vpWeights} style={{ fontSize: "12px", padding: "7px 14px", borderRadius: "8px", border: "1px solid var(--color-divider)", color: "var(--color-neutral-400)", cursor: "pointer", whiteSpace: "nowrap" }}>
                {"Scoring weights"}
              </div>
              <div style={{ padding: "8px 13px", borderRadius: "9px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", display: "flex", flexDirection: "column", gap: "2px", whiteSpace: "nowrap" }}>
                <span style={{ fontSize: "9.5px", letterSpacing: "0.11em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                  {"Last rebuild"}
                </span>
                <span style={{ fontSize: "12.5px", fontVariantNumeric: "tabular-nums" }}>
                  {"02:48 today"}
                </span>
              </div>
            </div>
          </div>
          <div style={{ display: "flex", alignItems: "baseline", gap: "10px", marginTop: "22px" }}>
            <span style={{ fontSize: "10px", letterSpacing: "0.12em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
              {"Insights and actions"}
            </span>
            <span style={{ flex: "1", height: "1px", background: "var(--color-divider)" }}></span>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(152px,1fr))", gap: "11px", marginTop: "11px" }}>
            {(vals.vpTiles || []).map((t, $index) => (
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
          <div style={{ display: "flex", alignItems: "baseline", gap: "10px", marginTop: "20px" }}>
            <span style={{ fontSize: "10px", letterSpacing: "0.12em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
              {"Stats"}
            </span>
            <span style={{ flex: "1", height: "1px", background: "var(--color-divider)" }}></span>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(230px,1fr))", gap: "11px", marginTop: "11px" }}>
            {(vals.vpStats || []).map((t, $index) => (
              <React.Fragment key={$index}>
                <div className="hv1" onClick={t.click} style={{ padding: "13px 15px", borderRadius: "10px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", minWidth: "0", cursor: "pointer" }}>
                  <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: "8px" }}>
                    <div style={{ display: "flex", alignItems: "baseline", gap: "7px", minWidth: "0" }}>
                      <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "20px", lineHeight: "1.1" }}>
                        {t.value}
                      </span>
                      <span style={{ fontSize: "10px", letterSpacing: "0.07em", textTransform: "uppercase", color: "var(--color-neutral-300)" }}>
                        {t.label}
                      </span>
                    </div>
                    <i className="ph ph-arrow-up-right" style={{ fontSize: "10px", color: "var(--color-neutral-500)", flexShrink: "0" }}></i>
                  </div>
                  <div style={{ display: "flex", flexDirection: "column", gap: "3px", marginTop: "9px" }}>
                    {(t.rows || []).map((r, $index) => (
                      <React.Fragment key={$index}>
                        <div className="hv20" onClick={r.click} style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) 34px auto", gap: "8px", alignItems: "center", cursor: "pointer" }}>
                          <span style={{ fontSize: "10.5px", color: "var(--color-neutral-400)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                            {r.label}
                          </span>
                          <div style={{ height: "3px", borderRadius: "2px", background: "var(--color-neutral-900)", overflow: "hidden" }}>
                            <div style={{ height: "100%", borderRadius: "2px", width: r.bar, background: r.color }}></div>
                          </div>
                          <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10px", color: r.color, whiteSpace: "nowrap", minWidth: "26px", textAlign: "right" }}>
                            {r.n}
                          </span>
                        </div>
                      </React.Fragment>
                    ))}
                  </div>
                </div>
              </React.Fragment>
            ))}
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(340px,1fr))", gap: "14px", marginTop: "16px", alignItems: "start" }}>
            <div style={{ minWidth: "0", borderRadius: "11px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", overflow: "hidden" }}>
              <div style={{ display: "flex", alignItems: "baseline", gap: "10px", padding: "11px 15px", borderBottom: "1px solid var(--color-divider)" }}>
                <span style={{ fontSize: "12.5px", flex: "1" }}>
                  {"Vendor directory"}
                </span>
                <span style={{ fontSize: "10.5px", color: "var(--color-neutral-500)" }}>
                  {"score · coverage"}
                </span>
              </div>
              {(vals.vpList || []).map((v, $index) => (
                <React.Fragment key={$index}>
                  <div className="hv2" onClick={v.pick} style={{ position: "relative", display: "grid", gridTemplateColumns: "minmax(0,1fr) 52px 68px", gap: "12px", alignItems: "center", padding: "11px 15px 11px 18px", borderBottom: "1px solid var(--color-divider)", cursor: "pointer", background: v.bg }}>
                    <div style={{ position: "absolute", left: "0", top: "0", bottom: "0", width: "3px", background: v.edge }}></div>
                    <div style={{ minWidth: "0" }}>
                      <div style={{ fontSize: "13px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {v.name}
                      </div>
                      <div style={{ fontSize: "10.5px", color: "var(--color-neutral-500)", marginTop: "2px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {v.meta}
                      </div>
                      <div style={{ fontSize: "10px", color: v.capFg, marginTop: "2px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", display: v.capShow }}>
                        {v.cap}
                      </div>
                    </div>
                    <div style={{ textAlign: "right" }}>
                      <div style={{ fontFamily: "ui-monospace,monospace", fontSize: "16px", lineHeight: "1", color: v.scoreFg }}>
                        {v.score}
                      </div>
                      <div style={{ fontSize: "9.5px", color: "var(--color-neutral-500)", marginTop: "2px" }}>
                        {v.trend}
                      </div>
                    </div>
                    <div style={{ display: "flex", flexDirection: "column", gap: "3px" }}>
                      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: "5px" }}>
                        <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10.5px", color: v.covFg }}>
                          {v.cov}
                        </span>
                        <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "9.5px", color: "var(--color-neutral-500)" }}>
                          {v.covFrac}
                        </span>
                      </div>
                      <div style={{ height: "4px", borderRadius: "2px", background: "var(--color-neutral-900)", overflow: "hidden" }}>
                        <div style={{ height: "100%", borderRadius: "2px", width: v.cov, background: v.covFg }}></div>
                      </div>
                    </div>
                  </div>
                </React.Fragment>
              ))}
              <div style={{ padding: "12px 15px", fontSize: "10.5px", color: "var(--color-neutral-500)", lineHeight: "1.5" }}>
                {"Coverage is the accreditation types this vendor holds on file against those its work requires. A mandatory lapse caps the score at 60 whatever the delivery numbers say."}
              </div>
            </div>
            <div style={{ minWidth: "0", display: "flex", flexDirection: "column", gap: "14px" }}>
              <div style={{ borderRadius: "11px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", overflow: "hidden" }}>
                <div style={{ padding: "14px 16px", borderBottom: "1px solid var(--color-divider)" }}>
                  <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: "14px", flexWrap: "wrap" }}>
                    <div style={{ minWidth: "0" }}>
                      <div style={{ fontSize: "17px", lineHeight: "1.2" }}>
                        {vals.vp.name}
                      </div>
                      <div style={{ fontFamily: "ui-monospace,monospace", fontSize: "10.5px", color: "var(--color-neutral-500)", marginTop: "4px" }}>
                        {vals.vp.contractLine}
                      </div>
                    </div>
                    <div style={{ textAlign: "right", flexShrink: "0" }}>
                      <div style={{ fontFamily: "ui-monospace,monospace", fontSize: "26px", lineHeight: "1", color: vals.vp.scoreFg }}>
                        {vals.vp.score}
                      </div>
                      <div style={{ fontSize: "10px", color: "var(--color-neutral-500)", marginTop: "2px" }}>
                        {"of 100 · "}{vals.vp.trend}
                      </div>
                    </div>
                  </div>
                  <div style={{ alignItems: "center", gap: "9px", marginTop: "11px", padding: "9px 11px", borderRadius: "8px", background: vals.vp.capBg, display: vals.vp.capShow }}>
                    <i className="ph ph-warning" style={{ fontSize: "14px", color: vals.vp.capFg, flexShrink: "0" }}></i>
                    <span style={{ fontSize: "11px", lineHeight: "1.45", color: "var(--color-text)" }}>
                      {vals.vp.capNote}
                    </span>
                  </div>
                </div>
                <div style={{ display: "flex", gap: "2px", padding: "0 16px", borderBottom: "1px solid var(--color-divider)", overflowX: "auto" }}>
                  {(vals.vpTabs || []).map((t, $index) => (
                    <React.Fragment key={$index}>
                      <div onClick={t.pick} style={{ padding: "9px 11px", fontSize: "12px", cursor: "pointer", whiteSpace: "nowrap", borderBottom: "2px solid transparent", borderBottomColor: t.edge, color: t.fg }}>
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
                {vals.vpPaneScore ? (
                  <>
                    <div style={{ padding: "11px 16px 0", fontSize: "11px", color: "var(--color-neutral-500)", lineHeight: "1.5" }}>
                      {"Every metric below states the clause it is measured against, what was measured, and over how many jobs. Where the term was not found in the signed contract, the platform default is named as such."}
                    </div>
                    {(vals.vp.metrics || []).map((m, $index) => (
                      <React.Fragment key={$index}>
                        <div style={{ padding: "11px 16px", borderBottom: "1px solid var(--color-divider)" }}>
                          <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) auto", gap: "12px", alignItems: "baseline" }}>
                            <div style={{ minWidth: "0" }}>
                              <span style={{ fontSize: "12.5px" }}>
                                {m.label}
                              </span>
                              <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "9.5px", padding: "1.5px 5px", borderRadius: "4px", marginLeft: "7px", background: m.srcBg, color: m.srcFg, whiteSpace: "nowrap" }}>
                                {m.srcTag}
                              </span>
                            </div>
                            <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10.5px", color: "var(--color-neutral-500)", whiteSpace: "nowrap" }}>
                              {m.pts}{" of "}{m.max}{" pts"}
                            </span>
                          </div>
                          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr 1fr", gap: "10px", marginTop: "7px" }}>
                            <div>
                              <div style={{ fontSize: "9.5px", letterSpacing: "0.09em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                                {"Contract requires"}
                              </div>
                              <div style={{ fontFamily: "ui-monospace,monospace", fontSize: "11.5px", marginTop: "2px" }}>
                                {m.requires}
                              </div>
                            </div>
                            <div>
                              <div style={{ fontSize: "9.5px", letterSpacing: "0.09em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                                {"Measured"}
                              </div>
                              <div style={{ fontFamily: "ui-monospace,monospace", fontSize: "11.5px", marginTop: "2px", color: m.color }}>
                                {m.measured}
                              </div>
                            </div>
                            <div>
                              <div style={{ fontSize: "9.5px", letterSpacing: "0.09em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                                {"Over"}
                              </div>
                              <div style={{ fontFamily: "ui-monospace,monospace", fontSize: "11.5px", marginTop: "2px", color: "var(--color-neutral-400)" }}>
                                {m.sample}
                              </div>
                            </div>
                          </div>
                          <div style={{ height: "5px", borderRadius: "3px", background: "var(--color-neutral-900)", overflow: "hidden", marginTop: "9px" }}>
                            <div style={{ height: "100%", borderRadius: "3px", width: m.bar, background: m.color }}></div>
                          </div>
                        </div>
                      </React.Fragment>
                    ))}
                    <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) auto", gap: "12px", alignItems: "baseline", padding: "11px 16px", borderBottom: "1px solid var(--color-divider)", background: "var(--color-bg)" }}>
                      <span style={{ fontSize: "12.5px" }}>
                        {"Weighted total"}
                      </span>
                      <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "13.5px" }}>
                        {vals.vp.totalPts}{" of 100 pts"}
                      </span>
                    </div>
                    <div style={{ gridTemplateColumns: "minmax(0,1fr) auto", gap: "12px", alignItems: "baseline", padding: "11px 16px", borderBottom: "1px solid var(--color-divider)", background: "var(--color-bg)", display: vals.vp.capRowShow }}>
                      <span style={{ fontSize: "12.5px", color: "var(--st-risk)" }}>
                        {"Accreditation cap applied"}
                      </span>
                      <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "13.5px", color: "var(--st-risk)" }}>
                        {"ceiling 60 pts"}
                      </span>
                    </div>
                    <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) auto", gap: "12px", alignItems: "baseline", padding: "12px 16px", borderBottom: "1px solid var(--color-divider)" }}>
                      <span style={{ fontSize: "12.5px" }}>
                        {"Published score"}
                      </span>
                      <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "16px", color: vals.vp.scoreFg }}>
                        {vals.vp.finalScore}
                      </span>
                    </div>
                    <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: "10px", padding: "12px 16px", borderBottom: "1px solid var(--color-divider)" }}>
                      {(vals.vp.critSplit || []).map((c, $index) => (
                        <React.Fragment key={$index}>
                          <div>
                            <div style={{ fontSize: "9.5px", letterSpacing: "0.09em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                              {c.label}
                            </div>
                            <div style={{ fontFamily: "ui-monospace,monospace", fontSize: "14px", marginTop: "2px", color: c.color }}>
                              {c.n}
                            </div>
                            <div style={{ fontSize: "10px", color: "var(--color-neutral-500)", marginTop: "1px" }}>
                              {c.note}
                            </div>
                          </div>
                        </React.Fragment>
                      ))}
                    </div>
                    <div style={{ padding: "12px 16px", fontSize: "10.5px", color: "var(--color-neutral-500)", lineHeight: "1.5" }}>
                      {vals.vp.critNote}
                    </div>
                  </>
                ) : null}
                {vals.vpPaneTerms ? (
                  <>
                    <div style={{ padding: "11px 16px 0", fontSize: "11px", color: "var(--color-neutral-500)", lineHeight: "1.5" }}>
                      {vals.vp.sourceNote}
                    </div>
                    <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) auto auto", gap: "12px", padding: "9px 16px", background: "var(--color-bg)", borderBottom: "1px solid var(--color-divider)", marginTop: "9px", fontSize: "9.5px", letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                      <span>
                        {"Term"}
                      </span>
                      <span>
                        {"Value"}
                      </span>
                      <span>
                        {"Source"}
                      </span>
                    </div>
                    {(vals.vp.terms || []).map((t, $index) => (
                      <React.Fragment key={$index}>
                        <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) auto auto", gap: "12px", alignItems: "center", padding: "8px 16px", borderBottom: "1px solid var(--color-divider)", fontSize: "12px" }}>
                          <span style={{ minWidth: "0", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                            {t.label}
                          </span>
                          <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "11.5px", whiteSpace: "nowrap", color: t.valFg }}>
                            {t.value}
                          </span>
                          <span onClick={t.open} style={{ fontFamily: "ui-monospace,monospace", fontSize: "9.5px", padding: "2px 6px", borderRadius: "5px", whiteSpace: "nowrap", background: t.bg, color: t.fg, cursor: t.cursor, minWidth: "74px", textAlign: "center" }}>
                            {t.src}
                          </span>
                        </div>
                      </React.Fragment>
                    ))}
                    <div style={{ padding: "12px 16px", fontSize: "10.5px", color: "var(--color-neutral-500)", lineHeight: "1.5" }}>
                      {"A default is not a contract term. Anything marked default was not found in the signed document, so the score is measured against a platform assumption rather than something the vendor agreed to — worth closing before the next renewal."}
                    </div>
                  </>
                ) : null}
                {vals.vpPaneEvidence ? (
                  <>
                    <div style={{ padding: "11px 16px 0", fontSize: "11px", color: "var(--color-neutral-500)", lineHeight: "1.5" }}>
                      {"The jobs behind the numbers. Criticality sets the weight — an L1 miss counts three times an L3 miss, and the service credit is calculated per breach from the clause named on the Terms tab."}
                    </div>
                    <div style={{ overflowX: "auto", marginTop: "9px" }}>
                      <div style={{ minWidth: "600px" }}>
                        <div style={{ display: "grid", gridTemplateColumns: "0.9fr 1.1fr 1.1fr 0.5fr 0.9fr 1.2fr 0.6fr 0.7fr", gap: "10px", padding: "9px 16px", background: "var(--color-bg)", borderBottom: "1px solid var(--color-divider)", fontSize: "9.5px", letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                          <span>
                            {"Work order"}
                          </span>
                          <span>
                            {"Asset"}
                          </span>
                          <span>
                            {"Building"}
                          </span>
                          <span>
                            {"Crit"}
                          </span>
                          <span>
                            {"Metric"}
                          </span>
                          <span>
                            {"Target vs actual"}
                          </span>
                          <span>
                            {"Weight"}
                          </span>
                          <span>
                            {"Credit"}
                          </span>
                        </div>
                        {(vals.vp.breaches || []).map((b, $index) => (
                          <React.Fragment key={$index}>
                            <div className="hv2" onClick={b.click} style={{ display: "grid", gridTemplateColumns: "0.9fr 1.1fr 1.1fr 0.5fr 0.9fr 1.2fr 0.6fr 0.7fr", gap: "10px", alignItems: "center", padding: "9px 16px", borderBottom: "1px solid var(--color-divider)", fontSize: "11.5px", cursor: "pointer" }}>
                              <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "11px", color: "var(--color-accent)" }}>
                                {b.wo}
                              </span>
                              <span style={{ minWidth: "0", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                {b.asset}
                              </span>
                              <span style={{ minWidth: "0", color: "var(--color-neutral-400)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                {b.building}
                              </span>
                              <span>
                                <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "9.5px", padding: "2px 5px", borderRadius: "4px", background: b.critBg, color: b.critFg }}>
                                  {b.crit}
                                </span>
                              </span>
                              <span style={{ color: "var(--color-neutral-400)" }}>
                                {b.metric}
                              </span>
                              <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10.5px", minWidth: "0", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                <span style={{ color: "var(--color-neutral-500)" }}>
                                  {b.target}
                                </span>
                                {" → "}
                                <span style={{ color: "var(--st-risk)" }}>
                                  {b.actual}
                                </span>
                              </span>
                              <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "11px", color: b.critFg }}>
                                {b.mult}
                              </span>
                              <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10.5px", color: "var(--st-ok)" }}>
                                {b.cost}
                              </span>
                            </div>
                          </React.Fragment>
                        ))}
                        <div style={{ display: "grid", gridTemplateColumns: "0.9fr 1.1fr 1.1fr 0.5fr 0.9fr 1.2fr 0.6fr 0.7fr", gap: "10px", padding: "10px 16px", borderBottom: "1px solid var(--color-divider)", fontSize: "11.5px" }}>
                          <span style={{ gridColumn: "1/7", color: "var(--color-neutral-500)" }}>
                            {"Recoverable this period under the service credit clause"}
                          </span>
                          <span style={{ gridColumn: "7/9", fontFamily: "ui-monospace,monospace", fontSize: "12.5px", color: "var(--st-ok)", textAlign: "right" }}>
                            {vals.vp.creditTotal}
                          </span>
                        </div>
                      </div>
                    </div>
                    <div style={{ display: "flex", gap: "9px", flexWrap: "wrap", padding: "12px 16px" }}>
                      <div className="hv15" onClick={vals.vp.claim} style={{ fontSize: "11.5px", padding: "6px 12px", borderRadius: "7px", border: "1px solid var(--color-accent)", color: "var(--color-accent)", cursor: "pointer" }}>
                        {"Claim "}{vals.vp.creditTotal}{" in credits"}
                      </div>
                      <div className="hv4" onClick={vals.vp.evidence} style={{ fontSize: "11.5px", padding: "6px 12px", borderRadius: "7px", border: "1px solid var(--color-divider)", color: "var(--color-neutral-400)", cursor: "pointer" }}>
                        {"Request evidence"}
                      </div>
                    </div>
                  </>
                ) : null}
                {vals.vpPaneCerts ? (
                  <>
                    <div style={{ padding: "11px 16px 0", fontSize: "11px", color: "var(--color-neutral-500)", lineHeight: "1.5" }}>
                      {vals.vp.covNote}
                    </div>
                    <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1.4fr) 0.6fr 0.8fr 0.8fr 1fr", gap: "10px", padding: "9px 16px", background: "var(--color-bg)", borderBottom: "1px solid var(--color-divider)", marginTop: "9px", fontSize: "9.5px", letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                      <span>
                        {"Accreditation"}
                      </span>
                      <span>
                        {"Required"}
                      </span>
                      <span>
                        {"Status"}
                      </span>
                      <span>
                        {"Expiry"}
                      </span>
                      <span>
                        {"Verification"}
                      </span>
                    </div>
                    {(vals.vp.certs || []).map((c, $index) => (
                      <React.Fragment key={$index}>
                        <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1.4fr) 0.6fr 0.8fr 0.8fr 1fr", gap: "10px", alignItems: "center", padding: "9px 16px", borderBottom: "1px solid var(--color-divider)", fontSize: "11.5px" }}>
                          <span style={{ minWidth: "0", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                            {c.name}
                          </span>
                          <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10.5px", color: c.reqFg }}>
                            {c.req}
                          </span>
                          <span>
                            <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "9.5px", padding: "2px 6px", borderRadius: "5px", background: c.bg, color: c.fg, whiteSpace: "nowrap" }}>
                              {c.status}
                            </span>
                          </span>
                          <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10.5px", color: "var(--color-neutral-400)", whiteSpace: "nowrap" }}>
                            {c.exp}
                          </span>
                          <span style={{ fontSize: "10.5px", color: "var(--color-neutral-500)", minWidth: "0", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                            {c.ver}
                          </span>
                        </div>
                      </React.Fragment>
                    ))}
                    <div style={{ display: "flex", gap: "9px", flexWrap: "wrap", padding: "12px 16px" }}>
                      <div className="hv15" onClick={vals.vp.chase} style={{ fontSize: "11.5px", padding: "6px 12px", borderRadius: "7px", border: "1px solid var(--color-accent)", color: "var(--color-accent)", cursor: "pointer" }}>
                        {"Ask for the missing certificates"}
                      </div>
                      <div className="hv4" onClick={vals.vp.openCompliance} style={{ fontSize: "11.5px", padding: "6px 12px", borderRadius: "7px", border: "1px solid var(--color-divider)", color: "var(--color-neutral-400)", cursor: "pointer" }}>
                        {"See this vendor in Compliance"}
                      </div>
                    </div>
                  </>
                ) : null}
                {vals.vpPaneInv ? (
                  <>
                    <div style={{ padding: "11px 16px 0", fontSize: "11px", color: "var(--color-neutral-500)", lineHeight: "1.5" }}>
                      {"Each line is checked against the rate schedule in the contract. A flag names the clause it fails, not just the amount."}
                    </div>
                    {(vals.vp.invoices || []).map((i, $index) => (
                      <React.Fragment key={$index}>
                        <div style={{ padding: "11px 16px", borderBottom: "1px solid var(--color-divider)" }}>
                          <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) auto auto", gap: "12px", alignItems: "baseline" }}>
                            <div style={{ minWidth: "0" }}>
                              <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "11px", color: "var(--color-accent)" }}>
                                {i.ref}
                              </span>
                              <span style={{ fontSize: "11px", color: "var(--color-neutral-500)", marginLeft: "7px" }}>
                                {i.period}
                              </span>
                            </div>
                            <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10.5px", color: i.deltaFg, whiteSpace: "nowrap" }}>
                              {i.delta}
                            </span>
                            <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "9.5px", padding: "2px 6px", borderRadius: "5px", background: i.bg, color: i.fg, whiteSpace: "nowrap" }}>
                              {i.status}
                            </span>
                          </div>
                          <div style={{ fontSize: "12px", marginTop: "5px", lineHeight: "1.4" }}>
                            {i.line}
                          </div>
                          <div style={{ display: "flex", gap: "16px", flexWrap: "wrap", marginTop: "6px" }}>
                            <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10.5px", color: "var(--color-neutral-500)" }}>
                              {"charged "}{i.charged}
                            </span>
                            <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10.5px", color: "var(--color-neutral-500)" }}>
                              {"contract "}{i.should}
                            </span>
                          </div>
                          <div style={{ fontSize: "10.5px", color: "var(--st-warn)", lineHeight: "1.45", marginTop: "5px", display: i.flagShow }}>
                            {i.flag}
                          </div>
                        </div>
                      </React.Fragment>
                    ))}
                    <div style={{ display: "flex", gap: "9px", flexWrap: "wrap", padding: "12px 16px" }}>
                      <div className="hv15" onClick={vals.vp.challenge} style={{ fontSize: "11.5px", padding: "6px 12px", borderRadius: "7px", border: "1px solid var(--color-accent)", color: "var(--color-accent)", cursor: "pointer" }}>
                        {"Raise credit note · "}{vals.vp.invTotal}
                      </div>
                      <div className="hv4" onClick={vals.vp.approveInv} style={{ fontSize: "11.5px", padding: "6px 12px", borderRadius: "7px", border: "1px solid var(--color-divider)", color: "var(--color-neutral-400)", cursor: "pointer" }}>
                        {"Approve as charged"}
                      </div>
                    </div>
                  </>
                ) : null}
              </div>
            </div>
          </div>
        </div>
      </div>
  );
}
