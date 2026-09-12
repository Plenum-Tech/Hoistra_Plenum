// Assets — asset condition from energy: condition rules, buildings → sections → assets, work orders and inspection notes, instrumented (IoT) assets with the failure model and the remediate-or-replace case
// Ported from the Hoistra design reference. `vals` is the view model from useHoistra().
import React from 'react';

export default function Assets({ vals }) {
  return (
    <>
      <div style={{ display: "flex", alignItems: "center", gap: "14px 22px", flexWrap: "wrap", marginTop: "16px", padding: "12px 15px", borderRadius: "10px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)" }}>
        <span style={{ fontSize: "10px", letterSpacing: "0.12em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
          {"Condition rules"}
        </span>
        <div style={{ display: "flex", alignItems: "center", gap: "8px", fontSize: "12px" }}>
          <span style={{ color: "var(--color-neutral-300)" }}>
            {"Section over reference by more than"}
          </span>
          <div style={{ display: "inline-flex", alignItems: "center", border: "1px solid var(--color-divider)", borderRadius: "7px", overflow: "hidden" }}>
            <div className="hv26" onClick={vals.asPctDown} style={{ padding: "3px 8px", cursor: "pointer", color: "var(--color-neutral-400)" }}>
              {"−"}
            </div>
            <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "12px", padding: "3px 6px", minWidth: "40px", textAlign: "center", borderLeft: "1px solid var(--color-divider)", borderRight: "1px solid var(--color-divider)" }}>
              {vals.asPct}
            </span>
            <div className="hv26" onClick={vals.asPctUp} style={{ padding: "3px 8px", cursor: "pointer", color: "var(--color-neutral-400)" }}>
              {"+"}
            </div>
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "8px", fontSize: "12px" }}>
          <span style={{ color: "var(--color-neutral-300)" }}>
            {"Anomaly persistent for"}
          </span>
          <div style={{ display: "inline-flex", alignItems: "center", border: "1px solid var(--color-divider)", borderRadius: "7px", overflow: "hidden" }}>
            <div className="hv26" onClick={vals.asWkDown} style={{ padding: "3px 8px", cursor: "pointer", color: "var(--color-neutral-400)" }}>
              {"−"}
            </div>
            <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "12px", padding: "3px 6px", minWidth: "28px", textAlign: "center", borderLeft: "1px solid var(--color-divider)", borderRight: "1px solid var(--color-divider)" }}>
              {vals.asWeeks}
            </span>
            <div className="hv26" onClick={vals.asWkUp} style={{ padding: "3px 8px", cursor: "pointer", color: "var(--color-neutral-400)" }}>
              {"+"}
            </div>
          </div>
          <span style={{ color: "var(--color-neutral-300)" }}>
            {vals.asWeeksUnit}{" or more"}
          </span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "14px", flexWrap: "wrap", fontSize: "11px", color: "var(--color-neutral-400)", marginLeft: "auto" }}>
          <span style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}>
            <span style={{ width: "8px", height: "8px", borderRadius: "2px", background: "var(--st-risk)" }}></span>
            {"Threat · both"}
          </span>
          <span style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}>
            <span style={{ width: "8px", height: "8px", borderRadius: "2px", background: "var(--st-warn)" }}></span>
            {"Watch · section over, or anomaly persistent"}
          </span>
          <span style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}>
            <span style={{ width: "8px", height: "8px", borderRadius: "2px", background: "var(--st-ok)" }}></span>
            {"In control"}
          </span>
        </div>
      </div>
      <div style={{ marginTop: "32px" }}>
        <div style={{ minWidth: "0" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "9px", marginBottom: "12px", flexWrap: "wrap" }}>
            <div style={{ fontSize: "11px", letterSpacing: "0.11em", textTransform: "uppercase", color: "var(--color-neutral-500)", flex: "1", minWidth: "180px" }}>
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
          <div style={{ fontSize: "11px", color: "var(--color-neutral-500)", marginBottom: "10px" }}>
            {vals.asSummary}
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
            {(vals.asGroups || []).map((g, $index) => (
              <React.Fragment key={$index}>
                <div style={{ borderRadius: "10px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", overflow: "hidden" }}>
                  <div className="hv19" onClick={g.toggle} style={{ display: "flex", alignItems: "center", gap: "14px 24px", flexWrap: "wrap", padding: "13px 16px", borderBottom: "1px solid var(--color-divider)", cursor: "pointer" }}>
                    <div style={{ flex: "1 1 200px", minWidth: "0" }}>
                      <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
                        <i className={`ph ${g.caret}`} style={{ fontSize: "12px", color: "var(--color-neutral-500)" }}></i>
                        <span style={{ fontSize: "14px" }}>
                          {g.name}
                        </span>
                        <span style={{ fontSize: "10.5px", padding: "2px 8px", borderRadius: "5px", whiteSpace: "nowrap", color: g.stColor, background: g.stBg }}>
                          {g.state}
                        </span>
                      </div>
                      <div style={{ fontSize: "11px", color: "var(--color-neutral-500)", marginTop: "4px" }}>
                        {g.std}{" · "}{g.secLine}
                      </div>
                    </div>
                    <div style={{ display: "flex", alignItems: "center", gap: "10px", flex: "1 1 240px", minWidth: "200px" }}>
                      <div style={{ flex: "1", height: "6px", borderRadius: "3px", background: "var(--color-neutral-900)", position: "relative", overflow: "hidden" }}>
                        <div style={{ height: "100%", borderRadius: "3px", width: g.pct, background: g.barColor }}></div>
                        <div style={{ position: "absolute", top: "-2px", bottom: "-2px", left: g.refPct, width: "2px", background: "var(--color-text)" }}></div>
                      </div>
                      <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "11.5px", color: "var(--color-neutral-300)", whiteSpace: "nowrap" }}>
                        {g.eui}{" "}
                        <span style={{ color: g.barColor }}>
                          {g.delta}
                        </span>
                      </span>
                    </div>
                    <div style={{ display: "flex", flexDirection: "column", gap: "3px", alignItems: "flex-end", flex: "0 0 auto", textAlign: "right" }}>
                      <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10.5px", color: "var(--color-neutral-400)", whiteSpace: "nowrap" }}>
                        {g.counts}
                      </span>
                      <span style={{ fontSize: "11.5px", whiteSpace: "nowrap", color: g.actColor }}>
                        {g.actions}
                      </span>
                    </div>
                  </div>
                  {g.open ? (
                    <>
                      {(g.sections || []).map((sec, $index) => (
                        <React.Fragment key={$index}>
                          <div>
                            <div className="hv19" onClick={sec.toggle} style={{ display: "flex", alignItems: "center", gap: "10px 14px", flexWrap: "wrap", padding: "9px 16px 9px 28px", background: "var(--color-bg)", borderBottom: "1px solid var(--color-divider)", cursor: "pointer" }}>
                              <div style={{ flex: "1 1 200px", minWidth: "0", display: "flex", alignItems: "center", gap: "8px" }}>
                                <i className={`ph ${sec.caret}`} style={{ fontSize: "11px", color: "var(--color-neutral-500)" }}></i>
                                <span style={{ fontSize: "12px", minWidth: "0", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                  {sec.name}
                                </span>
                                <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10px", color: "var(--color-neutral-500)", whiteSpace: "nowrap" }}>
                                  {sec.flags}
                                </span>
                              </div>
                              <div style={{ display: "flex", alignItems: "center", gap: "8px", flex: "1 1 200px", minWidth: "160px" }}>
                                <div style={{ flex: "1", height: "5px", borderRadius: "3px", background: "var(--color-neutral-900)", position: "relative", overflow: "hidden" }}>
                                  <div style={{ height: "100%", borderRadius: "3px", width: sec.pct, background: sec.barColor }}></div>
                                  <div style={{ position: "absolute", top: "-2px", bottom: "-2px", left: sec.refPct, width: "2px", background: "var(--color-text)" }}></div>
                                </div>
                                <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "11px", color: "var(--color-neutral-300)", whiteSpace: "nowrap" }}>
                                  {sec.eui}{" "}
                                  <span style={{ color: sec.barColor }}>
                                    {sec.delta}
                                  </span>
                                </span>
                              </div>
                              <span style={{ display: "inline-flex", alignItems: "center", gap: "8px", flexShrink: "0" }}>
                                <span style={{ fontSize: "10.5px", color: "var(--color-neutral-500)", whiteSpace: "nowrap" }}>
                                  {sec.meter}
                                </span>
                                <span style={{ fontSize: "10.5px", padding: "2px 8px", borderRadius: "5px", whiteSpace: "nowrap", color: sec.stColor, background: sec.stBg }}>
                                  {sec.state}
                                </span>
                              </span>
                            </div>
                            {sec.open ? (
                              <>
                                {(sec.rows || []).map((r, $index) => (
                                  <React.Fragment key={$index}>
                                    <div className="hv19" onClick={r.open} style={{ display: "flex", alignItems: "flex-start", gap: "12px", flexWrap: "wrap", padding: "11px 14px 11px 40px", borderBottom: "1px solid var(--color-divider)", borderLeft: `3px solid ${r.rail}`, cursor: "pointer" }}>
                                      <div style={{ flex: "1 1 220px", minWidth: "0" }}>
                                        <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
                                          <span style={{ fontSize: "12.5px" }}>
                                            {r.name}
                                          </span>
                                          <span style={{ fontSize: "10.5px", padding: "2px 8px", borderRadius: "5px", whiteSpace: "nowrap", color: r.color, background: r.bg }}>
                                            {r.cond}
                                          </span>
                                        </div>
                                        <div style={{ fontSize: "11px", color: "var(--color-neutral-500)", marginTop: "3px" }}>
                                          {r.meta}
                                        </div>
                                        <div style={{ fontSize: "11.5px", color: "var(--color-neutral-300)", marginTop: "6px", lineHeight: "1.5", maxWidth: "70ch" }}>
                                          {r.why}
                                        </div>
                                      </div>
                                      <div style={{ flex: "0 1 200px", minWidth: "150px" }}>
                                        <div style={{ fontSize: "10px", letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                                          {"Anomaly"}
                                        </div>
                                        <div style={{ fontSize: "11.5px", marginTop: "3px", color: r.anomColor }}>
                                          {r.anomText}
                                        </div>
                                        <div style={{ fontSize: "10px", letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--color-neutral-500)", marginTop: "8px" }}>
                                          {"Vendor"}
                                        </div>
                                        <div style={{ fontSize: "11.5px", marginTop: "3px" }}>
                                          {r.vendor}
                                        </div>
                                      </div>
                                      <div style={{ flex: "0 1 210px", minWidth: "170px" }}>
                                        <div style={{ fontSize: "10px", letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                                          {"Asset value · 12 months"}
                                        </div>
                                        <div style={{ display: "flex", alignItems: "baseline", gap: "6px", marginTop: "3px", fontFamily: "ui-monospace,monospace", fontSize: "11.5px" }}>
                                          <span>
                                            {r.valNow}
                                          </span>
                                          <span style={{ color: "var(--color-neutral-500)" }}>
                                            {"→"}
                                          </span>
                                          <span>
                                            {r.valAfter}
                                          </span>
                                          <span style={{ color: r.valLossColor }}>
                                            {r.valLoss}
                                          </span>
                                        </div>
                                        <div style={{ fontSize: "10px", color: "var(--color-neutral-500)", marginTop: "3px", lineHeight: "1.45" }}>
                                          {r.valLine}
                                        </div>
                                      </div>
                                      <div style={{ flex: "1 1 100%", display: r.histShow, flexDirection: "column", gap: "5px", marginTop: "2px", paddingTop: "9px", borderTop: "1px dashed var(--color-divider)" }}>
                                        <div style={{ fontSize: "10px", letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                                          {"Work orders and inspection notes"}
                                        </div>
                                        {(r.hist || []).map((h, $index) => (
                                          <React.Fragment key={$index}>
                                            <div style={{ display: "flex", alignItems: "flex-start", gap: "8px", fontSize: "11px", lineHeight: "1.45" }}>
                                              <i className={`ph ${h.icon}`} style={{ fontSize: "12px", color: h.color, flexShrink: "0", marginTop: "2px" }}></i>
                                              <div style={{ minWidth: "0", flex: "1" }}>
                                                <span style={{ fontFamily: "ui-monospace,monospace", color: "var(--color-neutral-400)" }}>
                                                  {h.id}
                                                </span>
                                                <span style={{ color: "var(--color-neutral-500)" }}>
                                                  {" · "}{h.when}
                                                </span>
                                                <div style={{ color: "var(--color-neutral-300)", marginTop: "1px" }}>
                                                  {h.text}
                                                </div>
                                                <div style={{ marginTop: "2px", color: h.flagColor, display: h.flagShow }}>
                                                  {h.flag}
                                                </div>
                                                <span style={{ fontSize: "10.5px", marginTop: "3px", padding: "2px 7px", borderRadius: "5px", background: "var(--marker-tint)", display: h.warrShow }}>
                                                  {h.warranty}
                                                </span>
                                              </div>
                                            </div>
                                          </React.Fragment>
                                        ))}
                                      </div>
                                      <div style={{ display: "flex", flexDirection: "column", gap: "6px", flex: "0 0 auto", alignItems: "stretch" }}>
                                        {r.woPrimary ? (
                                          <>
                                            <div onClick={r.wo} style={{ display: "inline-flex", alignItems: "center", justifyContent: "center", gap: "6px", fontSize: "11px", padding: "6px 11px", borderRadius: "7px", background: "var(--color-accent)", color: "var(--accent-ink)", cursor: "pointer", whiteSpace: "nowrap" }}>
                                              <i className="ph ph-wrench" style={{ fontSize: "11px" }}></i>
                                              <span>
                                                {"Raise work order"}
                                              </span>
                                            </div>
                                            <div className="hv4" onClick={r.inspect} style={{ display: "inline-flex", alignItems: "center", justifyContent: "center", gap: "6px", fontSize: "11px", padding: "6px 11px", borderRadius: "7px", border: "1px solid var(--color-divider)", color: "var(--color-neutral-300)", cursor: "pointer", whiteSpace: "nowrap" }}>
                                              <i className="ph ph-clipboard-text" style={{ fontSize: "11px" }}></i>
                                              <span>
                                                {"Request inspection"}
                                              </span>
                                            </div>
                                          </>
                                        ) : null}
                                        {r.inspectPrimary ? (
                                          <>
                                            <div onClick={r.inspect} style={{ display: "inline-flex", alignItems: "center", justifyContent: "center", gap: "6px", fontSize: "11px", padding: "6px 11px", borderRadius: "7px", background: "var(--color-accent)", color: "var(--accent-ink)", cursor: "pointer", whiteSpace: "nowrap" }}>
                                              <i className="ph ph-clipboard-text" style={{ fontSize: "11px" }}></i>
                                              <span>
                                                {"Request inspection"}
                                              </span>
                                            </div>
                                            <div className="hv4" onClick={r.wo} style={{ display: "inline-flex", alignItems: "center", justifyContent: "center", gap: "6px", fontSize: "11px", padding: "6px 11px", borderRadius: "7px", border: "1px solid var(--color-divider)", color: "var(--color-neutral-300)", cursor: "pointer", whiteSpace: "nowrap" }}>
                                              <i className="ph ph-wrench" style={{ fontSize: "11px" }}></i>
                                              <span>
                                                {"Raise work order"}
                                              </span>
                                            </div>
                                          </>
                                        ) : null}
                                        <div className="hv15" onClick={r.investigate} style={{ display: r.invShow, alignItems: "center", justifyContent: "center", gap: "6px", fontSize: "11px", padding: "6px 11px", borderRadius: "7px", border: "1px solid var(--color-accent)", color: "var(--color-accent)", cursor: "pointer", whiteSpace: "nowrap" }}>
                                          <i className="ph ph-magnifying-glass" style={{ fontSize: "11px" }}></i>
                                          <span>
                                            {"Investigate"}
                                          </span>
                                        </div>
                                      </div>
                                    </div>
                                  </React.Fragment>
                                ))}
                              </>
                            ) : null}
                          </div>
                        </React.Fragment>
                      ))}
                    </>
                  ) : null}
                </div>
              </React.Fragment>
            ))}
          </div>
          <div style={{ padding: "20px 0", fontSize: "12px", color: "var(--color-neutral-500)", display: vals.asEmpty }}>
            {"No assets match this filter."}
          </div>
          <div style={{ fontSize: "11px", color: "var(--color-neutral-600)", marginTop: "12px", lineHeight: "1.55" }}>
            {"Building bar is EUI against its regulation-pack reference; section bars are against each section's own reference (building pack, IT or car-park). The marker is the reference. Sections and buildings are ranked worst first."}
          </div>
        </div>
      </div>
      <div style={{ display: "flex", alignItems: "baseline", gap: "10px", marginTop: "36px", flexWrap: "wrap" }}>
        <span style={{ fontSize: "11px", letterSpacing: "0.11em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
          {"Instrumented assets · live"}
        </span>
        <span style={{ fontSize: "11px", color: "var(--color-neutral-500)" }}>
          {"Two assets with sensors streaming into the graph. Richer data, so the model can say when, not just whether."}
        </span>
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: "14px", marginTop: "12px" }}>
        {(vals.iotCards || []).map((c, $index) => (
          <React.Fragment key={$index}>
            <div style={{ borderRadius: "10px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", overflow: "hidden" }}>
              <div className="hv19" onClick={c.toggle} style={{ display: "flex", alignItems: "center", gap: "12px 24px", flexWrap: "wrap", padding: "13px 16px", cursor: "pointer" }}>
                <div style={{ flex: "1 1 220px", minWidth: "0" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
                    <i className={`ph ${c.caret}`} style={{ fontSize: "12px", color: "var(--color-neutral-500)" }}></i>
                    <span style={{ fontSize: "14px" }}>
                      {c.name}
                    </span>
                    <span style={{ display: "inline-flex", alignItems: "center", gap: "5px", fontSize: "10.5px", color: "var(--st-ok)" }}>
                      <span style={{ width: "6px", height: "6px", borderRadius: "50%", background: "var(--st-ok)", opacity: c.live, transition: "opacity 0.6s" }}></span>
                      {"live · "}{c.last}
                    </span>
                  </div>
                  <div style={{ fontSize: "11px", color: "var(--color-neutral-500)", marginTop: "4px" }}>
                    {c.cls}{" · "}{c.b}{" · "}{c.sec}{" · "}{c.sensors}{" sensors · "}{c.feed}
                  </div>
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: "18px", flexWrap: "wrap" }}>
                  <div style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
                    <span style={{ fontSize: "9.5px", letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                      {"Telemetry"}
                    </span>
                    <span style={{ fontSize: "12px", color: c.outColor }}>
                      {c.outLine}
                    </span>
                  </div>
                  <div style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
                    <span style={{ fontSize: "9.5px", letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                      {"Failure · "}{c.horizon}
                    </span>
                    <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "16px", lineHeight: "1", color: c.pColor }}>
                      {c.pFail}
                    </span>
                  </div>
                  <div style={{ display: "flex", flexDirection: "column", gap: "2px" }}>
                    <span style={{ fontSize: "9.5px", letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                      {"Remaining life"}
                    </span>
                    <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "16px", lineHeight: "1" }}>
                      {c.rul}
                    </span>
                  </div>
                </div>
              </div>
              {c.open ? (
                <>
                  <div style={{ borderTop: "1px solid var(--color-divider)", padding: "14px 16px 16px", display: "grid", gridTemplateColumns: "minmax(0,1.4fr) minmax(280px,1fr)", gap: "22px", alignItems: "start" }}>
                    <div style={{ minWidth: "0" }}>
                      <div style={{ fontSize: "10px", letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                        {"Live readings · last 24 h and now"}
                      </div>
                      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(210px,1fr))", gap: "8px", marginTop: "9px" }}>
                        {(c.readings || []).map((r, $index) => (
                          <React.Fragment key={$index}>
                            <div style={{ padding: "9px 11px", borderRadius: "8px", border: "1px solid var(--color-divider)", background: "var(--color-bg)", minWidth: "0" }}>
                              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "8px" }}>
                                <span style={{ fontSize: "11px", color: "var(--color-neutral-400)", minWidth: "0", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                  {r.k}
                                </span>
                                <span style={{ width: "6px", height: "6px", borderRadius: "50%", background: r.dot, flexShrink: "0" }}></span>
                              </div>
                              <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", gap: "8px", marginTop: "4px" }}>
                                <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "17px", lineHeight: "1", color: r.color, whiteSpace: "nowrap" }}>
                                  {r.v}
                                </span>
                                <svg width="84" height="22" viewBox="0 0 84 22" style={{ flexShrink: "0", overflow: "visible" }}>
                                  <line x1="0" x2="84" y1={r.hiY} y2={r.hiY} stroke="var(--color-neutral-700)" strokeDasharray="2 2" strokeWidth="1" />
                                  <polyline points={r.pts} fill="none" stroke={r.color} strokeWidth="1.5" strokeLinejoin="round" />
                                </svg>
                              </div>
                              <div style={{ fontSize: "10px", color: "var(--color-neutral-500)", marginTop: "4px" }}>
                                {"band "}{r.band}{" · "}{r.state}
                              </div>
                              <div style={{ fontSize: "10.5px", color: "var(--color-neutral-300)", marginTop: "4px", lineHeight: "1.4", display: r.noteShow }}>
                                {r.note}
                              </div>
                            </div>
                          </React.Fragment>
                        ))}
                      </div>
                    </div>
                    <div style={{ display: "flex", flexDirection: "column", gap: "12px", minWidth: "0" }}>
                      <div style={{ padding: "12px 13px", borderRadius: "9px", border: "1px solid var(--color-divider)" }}>
                        <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: "8px" }}>
                          <span style={{ fontSize: "10px", letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                            {"Failure model · "}{c.horizon}
                          </span>
                          <span style={{ fontSize: "10.5px", padding: "2px 8px", borderRadius: "5px", color: c.pColor, background: c.pBg }}>
                            {c.pFail}{" probability"}
                          </span>
                        </div>
                        <div style={{ display: "grid", gridTemplateColumns: "repeat(3,1fr)", gap: "8px", marginTop: "10px" }}>
                          <div>
                            <div style={{ fontSize: "9.5px", color: "var(--color-neutral-500)" }}>
                              {"Accuracy"}
                            </div>
                            <div style={{ fontFamily: "ui-monospace,monospace", fontSize: "14px", marginTop: "2px" }}>
                              {c.acc}
                            </div>
                          </div>
                          <div>
                            <div style={{ fontSize: "9.5px", color: "var(--color-neutral-500)" }}>
                              {"Precision"}
                            </div>
                            <div style={{ fontFamily: "ui-monospace,monospace", fontSize: "14px", marginTop: "2px" }}>
                              {c.prec}
                            </div>
                          </div>
                          <div>
                            <div style={{ fontSize: "9.5px", color: "var(--color-neutral-500)" }}>
                              {"Recall"}
                            </div>
                            <div style={{ fontFamily: "ui-monospace,monospace", fontSize: "14px", marginTop: "2px" }}>
                              {c.rec}
                            </div>
                          </div>
                        </div>
                        <div style={{ fontSize: "10.5px", color: "var(--color-neutral-500)", marginTop: "8px" }}>
                          {"Trained on "}{c.trained}
                        </div>
                        <div style={{ fontSize: "10px", letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--color-neutral-500)", marginTop: "10px" }}>
                          {"Drivers"}
                        </div>
                        <div style={{ display: "flex", flexDirection: "column", gap: "3px", marginTop: "5px" }}>
                          {(c.drivers || []).map((d, $index) => (
                            <React.Fragment key={$index}>
                              <div style={{ display: "flex", alignItems: "flex-start", gap: "7px", fontSize: "11.5px", lineHeight: "1.45" }}>
                                <span style={{ width: "5px", height: "5px", borderRadius: "50%", background: c.pColor, flexShrink: "0", marginTop: "6px" }}></span>
                                <span>
                                  {d.t}
                                </span>
                              </div>
                            </React.Fragment>
                          ))}
                        </div>
                      </div>
                      <div style={{ padding: "12px 13px", borderRadius: "9px", border: "1px solid var(--color-divider)" }}>
                        <div style={{ fontSize: "10px", letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                          {"Life"}
                        </div>
                        <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: "8px", marginTop: "8px" }}>
                          <span style={{ fontSize: "11.5px" }}>
                            {"Design life used"}
                          </span>
                          <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "11.5px", color: c.lifeColor }}>
                            {c.age}{" · "}{c.hours}
                          </span>
                        </div>
                        <div style={{ height: "5px", borderRadius: "3px", background: "var(--color-neutral-900)", overflow: "hidden", marginTop: "6px" }}>
                          <div style={{ height: "100%", width: c.lifePct, background: c.lifeColor }}></div>
                        </div>
                        <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: "8px", marginTop: "10px" }}>
                          <span style={{ fontSize: "11.5px" }}>
                            {"Remaining useful life"}
                          </span>
                          <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "13px" }}>
                            {c.rul}
                          </span>
                        </div>
                        <div style={{ fontSize: "10.5px", color: "var(--color-neutral-500)", marginTop: "2px" }}>
                          {c.rulRange}
                        </div>
                      </div>
                    </div>
                  </div>
                  <div style={{ borderTop: "1px solid var(--color-divider)", padding: "14px 16px 16px" }}>
                    <div style={{ display: "flex", alignItems: "flex-start", gap: "10px", padding: "10px 12px", borderRadius: "8px", background: "var(--marker-tint)" }}>
                      <i className="ph ph-clock" style={{ fontSize: "13px", color: "var(--color-accent)", flexShrink: "0", marginTop: "1px" }}></i>
                      <span style={{ fontSize: "11.5px", lineHeight: "1.5" }}>
                        {c.when}
                      </span>
                      <div className="hv15" onClick={c.investigate} style={{ display: c.invShow, alignItems: "center", gap: "6px", fontSize: "11px", padding: "5px 10px", borderRadius: "7px", border: "1px solid var(--color-accent)", color: "var(--color-accent)", cursor: "pointer", whiteSpace: "nowrap", marginLeft: "auto", flexShrink: "0" }}>
                        <i className="ph ph-magnifying-glass" style={{ fontSize: "11px" }}></i>
                        <span>
                          {"Investigate"}
                        </span>
                      </div>
                    </div>
                    <div style={{ fontSize: "10px", letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--color-neutral-500)", marginTop: "14px" }}>
                      {"Remediate or replace · on asset value"}
                    </div>
                    <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(280px,1fr))", gap: "10px", marginTop: "9px" }}>
                      <div style={{ padding: "12px 13px", borderRadius: "9px", border: `1px solid ${c.remEdge}`, background: "var(--color-bg)" }}>
                        <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: "8px" }}>
                          <span style={{ fontSize: "12.5px" }}>
                            {"Remediate · "}{c.remCost}
                          </span>
                          <span style={{ fontSize: "10px", color: "var(--color-accent)" }}>
                            {c.remTag}
                          </span>
                        </div>
                        <div style={{ fontSize: "11px", color: "var(--color-neutral-400)", marginTop: "5px", lineHeight: "1.45" }}>
                          {c.remWhat}
                        </div>
                        <div style={{ fontSize: "11.5px", marginTop: "7px", lineHeight: "1.45" }}>
                          {c.remGain}
                        </div>
                        <div onClick={c.remediate} style={{ display: "inline-flex", alignItems: "center", gap: "6px", fontSize: "11px", padding: "6px 11px", borderRadius: "7px", marginTop: "10px", background: "var(--color-accent)", color: "var(--accent-ink)", cursor: "pointer", whiteSpace: "nowrap" }}>
                          <i className="ph ph-wrench" style={{ fontSize: "11px" }}></i>
                          <span>
                            {"Schedule remediation"}
                          </span>
                        </div>
                      </div>
                      <div style={{ padding: "12px 13px", borderRadius: "9px", border: `1px solid ${c.repEdge}`, background: "var(--color-bg)" }}>
                        <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: "8px" }}>
                          <span style={{ fontSize: "12.5px" }}>
                            {"Replace · "}{c.replaceCost}
                          </span>
                          <span style={{ fontSize: "10px", color: "var(--color-accent)" }}>
                            {c.repTag}
                          </span>
                        </div>
                        <div style={{ fontSize: "11px", color: "var(--color-neutral-400)", marginTop: "5px", lineHeight: "1.45" }}>
                          {"Book value today "}{c.book}
                        </div>
                        <div style={{ fontSize: "11.5px", marginTop: "7px", lineHeight: "1.45" }}>
                          {c.repGain}
                        </div>
                        <div className="hv4" onClick={c.replace} style={{ display: "inline-flex", alignItems: "center", gap: "6px", fontSize: "11px", padding: "6px 11px", borderRadius: "7px", marginTop: "10px", border: "1px solid var(--color-divider)", color: "var(--color-neutral-300)", cursor: "pointer", whiteSpace: "nowrap" }}>
                          <i className="ph ph-arrows-clockwise" style={{ fontSize: "11px" }}></i>
                          <span>
                            {"Open replacement case"}
                          </span>
                        </div>
                      </div>
                    </div>
                  </div>
                </>
              ) : null}
            </div>
          </React.Fragment>
        ))}
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: "10px", marginTop: "36px", flexWrap: "wrap" }}>
        <span style={{ fontSize: "11px", letterSpacing: "0.11em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
          {"Live asset register"}
        </span>
        <span style={{ fontSize: "11px", color: "var(--color-neutral-500)" }}>
          {vals.asLiveCount}
        </span>
        <span style={{ display: "inline-flex", alignItems: "center", gap: "7px", padding: "3px 10px", borderRadius: "20px", border: "1px solid var(--color-divider)", fontSize: "10.5px", color: "var(--color-neutral-500)", marginLeft: "auto" }}>
          <span style={{ width: "6px", height: "6px", borderRadius: "50%", background: vals.asLiveSourceDot }}></span>
          <span>{vals.asLiveSourceLabel}</span>
          <span className="hv11" onClick={vals.asLiveRetry} style={{ color: "var(--color-accent)", cursor: "pointer", display: vals.asLiveRetryShow }}>{"Retry"}</span>
        </span>
      </div>
      <div style={{ borderRadius: "10px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", overflow: "hidden", marginTop: "10px" }}>
        {(vals.asLiveRows || []).map((r, $index) => (
          <React.Fragment key={$index}>
            <div style={{ display: "flex", alignItems: "center", gap: "10px 18px", flexWrap: "wrap", padding: "10px 16px", borderBottom: "1px solid var(--color-divider)" }}>
              <div style={{ flex: "1 1 200px", minWidth: "0" }}>
                <span style={{ fontSize: "12.5px" }}>{r.name}</span>
                <span style={{ fontSize: "11px", color: "var(--color-neutral-500)", marginLeft: "8px" }}>{r.manufacturer}{" · "}{r.model}{" · "}{r.serial}</span>
              </div>
              <span style={{ fontSize: "10.5px", padding: "2px 8px", borderRadius: "5px", whiteSpace: "nowrap", color: r.status === "active" ? "var(--st-ok)" : "var(--color-neutral-500)", background: "var(--color-bg)" }}>
                {r.status}
              </span>
              <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "11px", color: r.openWorkOrders ? "var(--color-accent)" : "var(--color-neutral-500)", whiteSpace: "nowrap" }}>
                {r.openWorkOrders}{r.openWorkOrders === 1 ? " open work order" : " open work orders"}
              </span>
            </div>
          </React.Fragment>
        ))}
        <div style={{ padding: "16px", fontSize: "12px", color: "var(--color-neutral-500)", display: vals.asLiveEmpty }}>
          {vals.asLiveEmptyText}
        </div>
      </div>
      <div style={{ fontSize: "11px", color: "var(--color-neutral-600)", marginTop: "12px", lineHeight: "1.55" }}>
        {"plenum_cafm.assets, joined to open work orders by asset name. Separate from the condition-scan section above — the two data models are not linked in the database, so building/section, install date, class, vendor and asset value stay from the reference model above until that join exists."}
      </div>
    </>
  );
}
