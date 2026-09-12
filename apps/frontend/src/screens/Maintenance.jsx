// Maintenance — maintenance: grouped decisions with bulk actions, inspection intelligence panel, PPM health
// Ported from the Hoistra design reference. `vals` is the view model from useHoistra().
import React from 'react';

export default function Maintenance({ vals }) {
  return (
    <>
      <div style={{ marginTop: "32px" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "9px", marginBottom: "12px", flexWrap: "wrap" }}>
          <div style={{ fontSize: "11px", letterSpacing: "0.11em", textTransform: "uppercase", color: "var(--color-neutral-500)", flex: "1", minWidth: "180px" }}>
            {"Decisions · blocked, to raise, deviating"}
          </div>
          {(vals.modFilters || []).map((f, $index) => (
            <React.Fragment key={$index}>
              <div onClick={f.click} style={{ fontSize: "11px", padding: "4px 10px", borderRadius: "20px", cursor: "pointer", border: `1px solid ${f.border}`, color: f.fg, background: f.bg }}>
                {f.label}
              </div>
            </React.Fragment>
          ))}
        </div>
        <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: "10px" }}>
          <span style={{ display: "inline-flex", alignItems: "center", gap: "7px", padding: "3px 10px", borderRadius: "20px", border: "1px solid var(--color-divider)", fontSize: "10.5px", color: "var(--color-neutral-500)" }}>
            <span style={{ width: "6px", height: "6px", borderRadius: "50%", background: vals.mxLiveSourceDot }}></span>
            <span>{vals.mxLiveSourceLabel}</span>
            <span className="hv11" onClick={vals.mxLiveRetry} style={{ color: "var(--color-accent)", cursor: "pointer", display: vals.mxLiveRetryShow }}>{"Retry"}</span>
          </span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap", marginBottom: "12px" }}>
          <span style={{ fontSize: "11px", color: "var(--color-neutral-500)", flex: "1", minWidth: "160px" }}>
            {vals.mxDecSummary}
          </span>
          <span style={{ fontSize: "10.5px", color: "var(--color-neutral-500)" }}>
            {"Group by"}
          </span>
          <div style={{ display: "inline-flex", gap: "4px" }}>
            {(vals.mxGroupOpts || []).map((g, $index) => (
              <React.Fragment key={$index}>
                <div onClick={g.pick} style={{ fontSize: "11px", padding: "4px 10px", borderRadius: "7px", cursor: "pointer", border: `1px solid ${g.edge}`, color: g.fg, background: g.bg }}>
                  {g.label}
                </div>
              </React.Fragment>
            ))}
          </div>
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
          {(vals.mxGroups || []).map((grp, $index) => (
            <React.Fragment key={$index}>
              <div style={{ borderRadius: "10px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", overflow: "hidden" }}>
                <div className="hv19" onClick={grp.toggle} style={{ display: "flex", alignItems: "center", gap: "10px 18px", flexWrap: "wrap", padding: "11px 14px", cursor: "pointer", borderLeft: `3px solid ${grp.color}` }}>
                  <i className={`ph ${grp.caret}`} style={{ fontSize: "12px", color: "var(--color-neutral-500)" }}></i>
                  <span style={{ fontSize: "13px" }}>
                    {grp.name}
                  </span>
                  <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "11px", padding: "1px 7px", borderRadius: "5px", background: "var(--color-neutral-900)", color: grp.color }}>
                    {grp.n}
                  </span>
                  <span style={{ fontSize: "11px", color: "var(--color-neutral-500)", flex: "1 1 200px", minWidth: "0" }}>
                    {grp.desc}
                  </span>
                  <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "11px", color: "var(--color-neutral-400)", whiteSpace: "nowrap" }}>
                    {grp.total}
                  </span>
                  <div className="hv15" onClick={grp.bulkRun} style={{ display: grp.bulkShow, alignItems: "center", gap: "6px", fontSize: "11px", padding: "5px 10px", borderRadius: "7px", border: "1px solid var(--color-accent)", color: "var(--color-accent)", cursor: "pointer", whiteSpace: "nowrap" }}>
                    <i className="ph ph-stack" style={{ fontSize: "11px" }}></i>
                    <span>
                      {grp.bulk}
                    </span>
                  </div>
                </div>
                {grp.open ? (
                  <>
                    {(grp.items || []).map((d, $index) => (
                      <React.Fragment key={$index}>
                        <div className="hv19" onClick={d.open} style={{ display: "flex", alignItems: "flex-start", gap: "12px 18px", flexWrap: "wrap", padding: "12px 14px 12px 24px", borderTop: "1px solid var(--color-divider)", borderLeft: `3px solid ${d.rail}`, cursor: "pointer" }}>
                          <div style={{ flex: "1 1 260px", minWidth: "0" }}>
                            <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
                              <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "11px", color: d.idColor }}>
                                {d.id}
                              </span>
                              <span style={{ fontSize: "12.5px" }}>
                                {d.asset}
                              </span>
                              <span style={{ fontSize: "11px", color: "var(--color-neutral-500)" }}>
                                {d.b}
                              </span>
                              <span style={{ fontSize: "10.5px", padding: "2px 8px", borderRadius: "5px", whiteSpace: "nowrap", color: d.color, background: d.bg }}>
                                {d.state}
                              </span>
                            </div>
                            <div style={{ display: "flex", alignItems: "center", gap: "6px", marginTop: "5px", fontSize: "11.5px" }}>
                              <i className={`ph ${d.srcIcon}`} style={{ fontSize: "12px", color: "var(--color-neutral-500)" }}></i>
                              <span style={{ color: "var(--color-neutral-500)" }}>
                                {d.src}
                              </span>
                              <span style={{ color: "var(--color-neutral-700)" }}>
                                {"·"}
                              </span>
                              <span style={{ minWidth: "0" }}>
                                {d.trigger}
                              </span>
                            </div>
                            <div style={{ fontSize: "11.5px", color: "var(--color-neutral-300)", marginTop: "6px", lineHeight: "1.5", maxWidth: "78ch" }}>
                              {d.detail}
                            </div>
                          </div>
                          <div style={{ flex: "0 1 150px", minWidth: "130px" }}>
                            <div style={{ fontSize: "10px", letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                              {"Vendor"}
                            </div>
                            <div style={{ fontSize: "11.5px", marginTop: "3px" }}>
                              {d.vendor}
                            </div>
                            <div style={{ fontSize: "10px", letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--color-neutral-500)", marginTop: "8px" }}>
                              {"Estimate"}
                            </div>
                            <div style={{ fontFamily: "ui-monospace,monospace", fontSize: "11.5px", marginTop: "3px" }}>
                              {d.est}
                            </div>
                          </div>
                          <div style={{ display: "flex", flexDirection: "column", gap: "6px", flex: "0 0 auto" }}>
                            {(d.actions || []).map((a, $index) => (
                              <React.Fragment key={$index}>
                                <div className="hv4" onClick={a.run} style={{ display: "inline-flex", alignItems: "center", justifyContent: "center", fontSize: "11px", padding: "6px 11px", borderRadius: "7px", border: `1px solid ${a.edge}`, background: a.bg, color: a.fg, cursor: "pointer", whiteSpace: "nowrap" }}>
                                  {a.label}
                                </div>
                              </React.Fragment>
                            ))}
                          </div>
                        </div>
                      </React.Fragment>
                    ))}
                  </>
                ) : null}
              </div>
            </React.Fragment>
          ))}
        </div>
        <div style={{ padding: "16px", fontSize: "12px", color: "var(--color-neutral-500)", display: vals.mxDecEmpty }}>
          {"No decisions match this filter."}
        </div>
      </div>
      <div style={{ marginTop: "36px", borderRadius: "12px", background: "var(--color-surface)", boxShadow: "var(--shadow-md)", borderTop: "3px solid var(--color-accent)", overflow: "hidden" }}>
        <div style={{ display: "flex", alignItems: "flex-start", gap: "14px 24px", flexWrap: "wrap", padding: "18px 20px 0" }}>
          <div style={{ width: "40px", height: "40px", borderRadius: "10px", background: "var(--color-accent-900)", display: "flex", alignItems: "center", justifyContent: "center", flexShrink: "0" }}>
            <i className="ph ph-file-magnifying-glass" style={{ fontSize: "20px", color: "var(--color-accent)" }}></i>
          </div>
          <div style={{ flex: "1 1 300px", minWidth: "0" }}>
            <div style={{ fontSize: "10.5px", letterSpacing: "0.13em", textTransform: "uppercase", color: "var(--color-accent-300)" }}>
              {"Inspection intelligence"}
            </div>
            <h3 style={{ fontSize: "19px", margin: "5px 0 0", lineHeight: "1.2" }}>
              {"Ask the inspection reports"}
            </h3>
            <p style={{ fontSize: "12.5px", color: "var(--color-neutral-400)", margin: "6px 0 0", maxWidth: "72ch", lineHeight: "1.55" }}>
              {vals.inspCount}{". Every report attached to a completed order, read together with warranty documents, so a question is answered across all of them rather than one file at a time."}
            </p>
          </div>
          <div className="hv4" onClick={vals.mxInspOpen} style={{ display: "inline-flex", alignItems: "center", gap: "7px", fontSize: "12px", padding: "8px 14px", borderRadius: "8px", border: "1px solid var(--color-divider)", color: "var(--color-neutral-300)", cursor: "pointer", whiteSpace: "nowrap", flexShrink: "0", alignSelf: "flex-start" }}>
            <span>
              {"Open all reports"}
            </span>
            <i className="ph ph-arrow-right" style={{ fontSize: "12px" }}></i>
          </div>
        </div>
        <div style={{ padding: "16px 20px 0" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "11px", padding: "12px 15px", borderRadius: "11px", background: "var(--color-bg)", border: "1px solid var(--color-divider)" }}>
            <i className="ph ph-sparkle" style={{ fontSize: "16px", color: "var(--color-accent)", flexShrink: "0" }}></i>
            <input className="input" value={vals.mxInspQ} onChange={vals.mxInspSet} onKeyDown={vals.mxInspKey} placeholder="e.g. Which recommendations were never turned into work orders?" style={{ flex: "1", minWidth: "0", background: "transparent", border: "none", outline: "none", fontFamily: "var(--font-body)", fontSize: "14px", color: "var(--color-text)" }} />
            <div className="btn btn-primary" onClick={vals.mxInspRun} style={{ fontSize: "12px", padding: "7px 15px", cursor: "pointer", whiteSpace: "nowrap", flexShrink: "0" }}>
              {"Ask"}
            </div>
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: "7px", marginTop: "9px", alignItems: "center" }}>
            <span style={{ fontSize: "10.5px", color: "var(--color-neutral-500)" }}>
              {"Try"}
            </span>
            {(vals.mxInspChips || []).map((c, $index) => (
              <React.Fragment key={$index}>
                <div className="hv4" onClick={c.run} style={{ fontSize: "11.5px", padding: "5px 11px", borderRadius: "7px", border: "1px solid var(--color-divider)", color: "var(--color-neutral-400)", cursor: "pointer" }}>
                  {c.label}
                </div>
              </React.Fragment>
            ))}
          </div>
        </div>
        <div style={{ padding: "16px 20px 18px" }}>
          <div style={{ fontSize: "10px", letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
            {"What the reports say together · click to open the reading"}
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(220px,1fr))", gap: "10px", marginTop: "9px" }}>
            {(vals.mxInsights || []).map((i, $index) => (
              <React.Fragment key={$index}>
                <div className="hv14" onClick={i.ask} style={{ padding: "13px 14px", borderRadius: "9px", background: "var(--color-bg)", border: "1px solid var(--color-divider)", position: "relative", overflow: "hidden", cursor: "pointer" }}>
                  <div style={{ position: "absolute", left: "0", top: "0", bottom: "0", width: "2px", background: i.color }}></div>
                  <div style={{ fontSize: "24px", lineHeight: "1", color: i.color }}>
                    {i.n}
                  </div>
                  <div style={{ fontSize: "12px", marginTop: "7px", lineHeight: "1.4" }}>
                    {i.l}
                  </div>
                  <div style={{ fontSize: "11px", color: "var(--color-neutral-400)", marginTop: "5px", lineHeight: "1.4" }}>
                    {i.s}
                  </div>
                </div>
              </React.Fragment>
            ))}
          </div>
        </div>
      </div>
      <div style={{ display: "flex", alignItems: "baseline", gap: "10px", marginTop: "36px", flexWrap: "wrap" }}>
        <span style={{ fontSize: "11px", letterSpacing: "0.11em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
          {"PPM health · year to date"}
        </span>
        <span style={{ fontSize: "11px", color: "var(--color-neutral-500)" }}>
          {vals.mxPpmSummary}
        </span>
      </div>
      <div style={{ borderRadius: "10px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", overflowX: "auto", marginTop: "12px" }}>
        <div style={{ minWidth: "860px" }}>
          <div style={{ display: "grid", gridTemplateColumns: "minmax(200px,1.6fr) minmax(160px,1.2fr) 90px 70px 70px 100px 110px", gap: "12px", padding: "8px 14px", fontSize: "10px", letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--color-neutral-500)", borderBottom: "1px solid var(--color-divider)" }}>
            <span>
              {"Contract"}
            </span>
            <span>
              {"Visits to plan"}
            </span>
            <span>
              {"Missed"}
            </span>
            <span>
              {"Late"}
            </span>
            <span>
              {"Reports"}
            </span>
            <span>
              {"Next"}
            </span>
            <span>
              {"State"}
            </span>
          </div>
          {(vals.mxPpm || []).map((p, $index) => (
            <React.Fragment key={$index}>
              <div style={{ borderBottom: "1px solid var(--color-divider)" }}>
                <div style={{ display: "grid", gridTemplateColumns: "minmax(200px,1.6fr) minmax(160px,1.2fr) 90px 70px 70px 100px 110px", gap: "12px", alignItems: "center", padding: "10px 14px", fontSize: "12px" }}>
                  <div style={{ minWidth: "0" }}>
                    <div style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {p.contract}
                    </div>
                    <div style={{ fontSize: "10.5px", color: "var(--color-neutral-500)", marginTop: "2px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {p.vendor}{" · "}{p.scope}
                    </div>
                  </div>
                  <div style={{ display: "flex", alignItems: "center", gap: "8px", minWidth: "0" }}>
                    <div style={{ flex: "1", height: "5px", borderRadius: "3px", background: "var(--color-neutral-900)", overflow: "hidden" }}>
                      <div style={{ height: "100%", width: p.pct, background: p.color }}></div>
                    </div>
                    <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "11px", whiteSpace: "nowrap" }}>
                      {p.done}
                    </span>
                  </div>
                  <span style={{ fontFamily: "ui-monospace,monospace", color: p.missedColor }}>
                    {p.missed}
                  </span>
                  <span style={{ fontFamily: "ui-monospace,monospace", color: p.lateColor }}>
                    {p.late}
                  </span>
                  <span style={{ fontFamily: "ui-monospace,monospace", color: p.repColor }}>
                    {p.reports}
                  </span>
                  <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "11px", color: p.nextColor, whiteSpace: "nowrap" }}>
                    {p.next}
                  </span>
                  <span>
                    <span style={{ fontSize: "10.5px", padding: "2px 8px", borderRadius: "5px", whiteSpace: "nowrap", color: p.color, background: p.bg }}>
                      {p.state}
                    </span>
                  </span>
                </div>
                <div style={{ padding: "0 14px 10px", fontSize: "11px", color: "var(--color-neutral-400)", lineHeight: "1.45", display: p.noteShow }}>
                  {p.deferrals}{p.note}
                </div>
              </div>
            </React.Fragment>
          ))}
        </div>
      </div>
      <div style={{ fontSize: "11px", color: "var(--color-neutral-600)", marginTop: "12px", lineHeight: "1.55" }}>
        {"Visits from the contract PPM schedule; reports are inspection reports ingested against a completed visit. A visit without a report counts as done but unverified."}
      </div>
    </>
  );
}
