// DecisionQueue — right drawer
// Ported from the Hoistra prototype template. `vals` is the view model from useHoistra().
import React from 'react';

export default function DecisionQueue({ vals }) {
  return (
    <>
      <div onClick={vals.closeQueue} style={{ position: "fixed", inset: "0", background: "var(--scrim)", zIndex: "60" }}></div>
      <div style={{ position: "fixed", top: "0", right: "0", bottom: "0", width: "520px", background: "var(--color-bg)", boxShadow: "var(--shadow-lg)", zIndex: "61", display: "flex", flexDirection: "column", animation: "fadeUp 0.22s ease both" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "12px", padding: "17px 20px", borderBottom: "1px solid var(--color-divider)", flexShrink: "0" }}>
          <i className="ph ph-bell-ringing" style={{ fontSize: "16px", color: "var(--st-warn)" }}></i>
          <div style={{ flex: "1" }}>
            <div style={{ fontSize: "14px" }}>
              {"Decision queue"}
            </div>
            <div style={{ fontSize: "11px", color: "var(--color-neutral-500)", marginTop: "2px" }}>
              {"Ranked by consequence, not by age"}
            </div>
          </div>
          <i className="ph ph-x" onClick={vals.closeQueue} style={{ fontSize: "16px", color: "var(--color-neutral-500)", cursor: "pointer" }}></i>
        </div>
        <div style={{ padding: "12px 20px", borderBottom: "1px solid var(--color-divider)", flexShrink: "0", display: "flex", flexDirection: "column", gap: "9px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
            <span style={{ fontSize: "10.5px", letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--color-neutral-500)", marginRight: "2px" }}>
              {"Runs"}
            </span>
            {(vals.freqOpts || []).map((o, $index) => (
              <React.Fragment key={$index}>
                <div onClick={o.pick} style={{ fontSize: "11px", padding: "4px 10px", borderRadius: "20px", cursor: "pointer", border: `1px solid ${o.border}`, color: o.fg, background: o.bg }}>
                  {o.label}
                </div>
              </React.Fragment>
            ))}
          </div>
          {vals.customOpen ? (
            <>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(3,minmax(0,1fr))", gap: "9px", padding: "11px 12px", borderRadius: "9px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)" }}>
                <div style={{ display: "flex", flexDirection: "column", gap: "5px", minWidth: "0" }}>
                  <span style={{ fontSize: "9.5px", letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                    {"Frequency"}
                  </span>
                  <select className="input" value={vals.cFreq} onChange={vals.setCFreq} style={{ width: "100%", boxSizing: "border-box", fontSize: "12px", padding: "6px 8px", borderRadius: "6px", border: "1px solid var(--color-divider)", background: "var(--color-bg)", color: "var(--color-text)", fontFamily: "var(--font-body)", outline: "none" }}>
                    {(vals.cFreqOpts || []).map((o, $index) => (
                      <React.Fragment key={$index}>
                        <option value={o}>
                          {o}
                        </option>
                      </React.Fragment>
                    ))}
                  </select>
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: "5px", minWidth: "0" }}>
                  <span style={{ fontSize: "9.5px", letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                    {"Start date"}
                  </span>
                  <input className="input" type="date" value={vals.cDate} onChange={vals.setCDate} style={{ width: "100%", boxSizing: "border-box", fontSize: "12px", padding: "6px 8px", borderRadius: "6px", border: "1px solid var(--color-divider)", background: "var(--color-bg)", color: "var(--color-text)", fontFamily: "var(--font-body)", outline: "none" }} />
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: "5px", minWidth: "0" }}>
                  <span style={{ fontSize: "9.5px", letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                    {"Time · 24h"}
                  </span>
                  <input className="input" value={vals.cTime} onChange={vals.setCTime} placeholder="02:00" maxLength="5" style={{ width: "100%", boxSizing: "border-box", fontSize: "12px", padding: "6px 8px", borderRadius: "6px", border: "1px solid var(--color-divider)", background: "var(--color-bg)", color: "var(--color-text)", fontFamily: "ui-monospace,monospace", outline: "none" }} />
                </div>
                <div style={{ gridColumn: "1/-1", display: "flex", alignItems: "center", gap: "9px", flexWrap: "wrap" }}>
                  <div className="hv7" onClick={vals.saveCustom} style={{ fontSize: "11.5px", padding: "5px 12px", borderRadius: "7px", background: "var(--color-accent)", color: "var(--accent-ink)", cursor: "pointer" }}>
                    {"Save schedule"}
                  </div>
                  <span style={{ fontSize: "10.5px", color: "var(--color-neutral-500)" }}>
                    {vals.customSummary}
                  </span>
                </div>
              </div>
            </>
          ) : null}
          <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
            <span style={{ fontSize: "10.5px", letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--color-neutral-500)", marginRight: "2px" }}>
              {"Notify on"}
            </span>
            {(vals.chanOpts || []).map((o, $index) => (
              <React.Fragment key={$index}>
                <div onClick={o.pick} style={{ fontSize: "11px", padding: "4px 10px", borderRadius: "20px", cursor: "pointer", border: `1px solid ${o.border}`, color: o.fg, background: o.bg }}>
                  {o.label}
                </div>
              </React.Fragment>
            ))}
          </div>
        </div>
        <div style={{ flex: "1", overflowY: "auto", padding: "14px 20px 40px", display: "flex", flexDirection: "column", gap: "9px" }}>
          {(vals.queueItems || []).map((d, $index) => (
            <React.Fragment key={$index}>
              <div className="hv1" onClick={d.click} style={{ padding: "14px", borderRadius: "10px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", cursor: "pointer" }}>
                <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "7px" }}>
                  <i className={`ph ${d.icon}`} style={{ fontSize: "13px", color: d.color }}></i>
                  <span style={{ fontSize: "10.5px", letterSpacing: "0.09em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                    {d.module}
                  </span>
                  <div style={{ flex: "1" }}></div>
                  <span style={{ display: "inline-block", whiteSpace: "nowrap", fontSize: "10.5px", padding: "2px 7px", borderRadius: "5px", color: d.color, background: d.bg }}>
                    {d.money}
                  </span>
                </div>
                <div style={{ fontSize: "13px", lineHeight: "1.4" }}>
                  {d.title}
                </div>
                <div style={{ fontSize: "11px", color: "var(--color-neutral-300)", marginTop: "5px", lineHeight: "1.45" }}>
                  {d.meta}
                </div>
              </div>
            </React.Fragment>
          ))}
        </div>
      </div>
    </>
  );
}
