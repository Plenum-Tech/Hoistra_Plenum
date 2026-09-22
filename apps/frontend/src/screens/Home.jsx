// Home — query-first home
// Ported from the Hoistra prototype template. `vals` is the view model from useHoistra().
import React from 'react';

export default function Home({ vals }) {
  return (
      <div style={{ flex: "1", display: "flex", flexDirection: "column", alignItems: "center", padding: "0 32px 80px" }}>
        <div style={{ width: "100%", maxWidth: "1080px", display: "flex", flexDirection: "column" }}>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(240px,1fr))", gap: "0", padding: "22px 0 0", borderTop: "1px solid var(--color-divider)", borderBottom: "1px solid var(--color-divider)", marginTop: "8px" }}>
            <div style={{ padding: "14px 22px 15px 0", display: "flex", flexDirection: "column", minWidth: "0" }}>
              <span style={{ fontSize: "9.5px", letterSpacing: "0.13em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                {"Hoist Score"}
              </span>
              <div style={{ display: "flex", alignItems: "baseline", gap: "6px", marginTop: "9px" }}>
                <span style={{ fontSize: "22px", lineHeight: "1", fontVariantNumeric: "tabular-nums" }}>
                  {vals.hoistScore.value}
                </span>
                <span style={{ fontSize: "10px", color: "var(--color-neutral-500)" }}>
                  {"/ 100"}
                </span>
                <span style={{ fontSize: "10px", color: "var(--color-accent)", marginLeft: "2px" }}>
                  {vals.hoistScore.band}
                </span>
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: "3px", marginTop: "10px" }}>
                {(vals.hoistBars || []).map((b, $index) => (
                  <React.Fragment key={$index}>
                    <div title={`${b.label} — ${b.val}${b.note ? " · " + b.note : ""}`} style={{ display: "grid", gridTemplateColumns: "1fr 34px 26px", gap: "7px", alignItems: "center" }}>
                      <span style={{ fontSize: "9.5px", color: "var(--color-neutral-400)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {b.short}
                      </span>
                      <div style={{ height: "3px", borderRadius: "2px", background: "var(--color-neutral-900)", overflow: "hidden" }}>
                        <div style={{ height: "100%", borderRadius: "2px", width: b.pct, background: b.color }}></div>
                      </div>
                      <span style={{ fontSize: "9.5px", fontFamily: "ui-monospace,monospace", textAlign: "right", color: b.color }}>
                        {b.val}
                      </span>
                    </div>
                  </React.Fragment>
                ))}
              </div>
              <div title={vals.hoistScore.gap} style={{ fontSize: "9px", color: "var(--color-neutral-500)", marginTop: "9px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {vals.hoistScoreNote}
              </div>
            </div>
            <div style={{ padding: "14px 22px 15px", borderLeft: "1px solid var(--color-divider)", display: "flex", flexDirection: "column", minWidth: "0" }}>
              <span style={{ fontSize: "9.5px", letterSpacing: "0.13em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                {"Portfolio P&L · YTD"}
              </span>
              <div style={{ display: "flex", alignItems: "baseline", gap: "6px", marginTop: "9px" }}>
                <span style={{ fontSize: "22px", lineHeight: "1", fontVariantNumeric: "tabular-nums", color: "var(--st-ok)" }}>
                  {vals.pnlSaved}
                </span>
                <span style={{ fontSize: "10px", color: "var(--color-neutral-500)" }}>
                  {"saved to date"}
                </span>
              </div>
              <div style={{ display: vals.pnlNoteShow, fontSize: "9.5px", lineHeight: "1.45", color: "var(--color-neutral-500)", marginTop: "8px", maxWidth: "30ch" }}>
                {vals.pnlNote}
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: "2px", marginTop: "10px" }}>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 44px 40px", gap: "7px", alignItems: "baseline" }}>
                  <span style={{ fontSize: "9px", letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                    {"Head"}
                  </span>
                  <span style={{ fontSize: "9px", letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--color-neutral-500)", textAlign: "right" }}>
                    {"Budget"}
                  </span>
                  <span style={{ fontSize: "9px", letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--color-neutral-500)", textAlign: "right" }}>
                    {"Actual"}
                  </span>
                </div>
                {(vals.pnlTop || []).map((r, $index) => (
                  <React.Fragment key={$index}>
                    <div style={{ display: "grid", gridTemplateColumns: "1fr 44px 40px", gap: "7px", alignItems: "baseline" }}>
                      <span style={{ fontSize: "9.5px", color: "var(--color-neutral-400)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {r.head}
                      </span>
                      <span style={{ fontSize: "9.5px", fontFamily: "ui-monospace,monospace", textAlign: "right", color: "var(--color-neutral-500)" }}>
                        {r.budget}
                      </span>
                      <span style={{ fontSize: "9.5px", fontFamily: "ui-monospace,monospace", textAlign: "right", color: r.color }}>
                        {r.actual}
                      </span>
                    </div>
                  </React.Fragment>
                ))}
              </div>
              <div style={{ fontSize: "9px", color: "var(--color-neutral-500)", marginTop: "9px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {vals.pnlNote}
              </div>
            </div>
            <div style={{ padding: "14px 0 15px 22px", borderLeft: "1px solid var(--color-divider)", display: "flex", flexDirection: "column", minWidth: "0" }}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "10px" }}>
                <span style={{ fontSize: "9.5px", letterSpacing: "0.13em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                  {"Hoist Crons"}
                </span>
                <span title={vals.cronsTip} style={{ display: "flex", alignItems: "center", gap: "5px", fontSize: "9.5px", color: "var(--color-neutral-500)" }}>
                  <span style={{ width: "5px", height: "5px", borderRadius: "50%", background: vals.cronsDot }}></span>
                  <span>
                    {vals.cronsLabel}
                  </span>
                </span>
              </div>
              <div style={{ marginTop: "9px", height: "104px", overflowY: "auto", display: "flex", flexDirection: "column" }}>
                {(vals.crons || []).map((c, $index) => (
                  <React.Fragment key={$index}>
                    <div style={{ flex: "0 0 26px", height: "26px", minHeight: "26px", boxSizing: "border-box", display: c.isSep, alignItems: "center", gap: "8px" }}>
                      <span style={{ fontSize: "9px", letterSpacing: "0.12em", textTransform: "uppercase", color: "var(--color-neutral-500)", whiteSpace: "nowrap" }}>
                        {c.day}
                      </span>
                      <span style={{ flex: "1", height: "1px", background: "var(--color-divider)" }}></span>
                    </div>
                    <div title={c.tip} style={{ flex: "0 0 26px", height: "26px", minHeight: "26px", boxSizing: "border-box", display: c.isRow, gridTemplateColumns: "5px auto minmax(0,1fr) auto", gap: "7px", alignItems: "center", opacity: c.op, transition: "opacity 0.4s ease" }}>
                      <span style={{ width: "5px", height: "5px", borderRadius: "50%", background: c.dot }}></span>
                      <span style={{ fontSize: "10px", fontFamily: "ui-monospace,monospace", color: "var(--color-neutral-500)", whiteSpace: "nowrap" }}>
                        {c.t}
                      </span>
                      <span style={{ fontSize: "10.5px", color: "var(--color-neutral-400)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {c.text}
                      </span>
                      <div className="hv15" onClick={c.act} style={{ fontSize: "9px", padding: "2px 6px", borderRadius: "5px", border: "1px solid var(--color-accent)", color: "var(--color-accent)", cursor: "pointer", whiteSpace: "nowrap", display: c.actShow }}>
                        {c.action}
                      </div>
                      <span style={{ fontSize: "9px", color: "var(--color-neutral-500)", whiteSpace: "nowrap", display: c.noneShow }}>
                        {"—"}
                      </span>
                    </div>
                  </React.Fragment>
                ))}
              </div>
            </div>
            <div className="hv19" onClick={vals.pvOpen} style={{ padding: "14px 22px 15px", borderLeft: "1px solid var(--color-divider)", display: "flex", flexDirection: "column", minWidth: "0", cursor: "pointer" }}>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "8px" }}>
                <span style={{ fontSize: "9.5px", letterSpacing: "0.13em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                  {"Platform value · 2026"}
                </span>
                <i className="ph ph-arrow-up-right" style={{ fontSize: "11px", color: "var(--color-accent)" }}></i>
              </div>
              <div style={{ display: "flex", alignItems: "baseline", gap: "6px", marginTop: "9px" }}>
                <span style={{ fontSize: "22px", lineHeight: "1", fontVariantNumeric: "tabular-nums", color: "var(--st-ok)" }}>
                  {vals.pvTotal}
                </span>
                <span style={{ fontSize: "10px", color: "var(--color-neutral-500)" }}>
                  {"saved after action"}
                </span>
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: "2px", marginTop: "10px" }}>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 46px 40px", gap: "7px", alignItems: "baseline" }}>
                  <span style={{ fontSize: "9px", letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                    {"Module"}
                  </span>
                  <span style={{ fontSize: "9px", letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--color-neutral-500)", textAlign: "right" }}>
                    {"Detected"}
                  </span>
                  <span style={{ fontSize: "9px", letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--color-neutral-500)", textAlign: "right" }}>
                    {"Saved"}
                  </span>
                </div>
                {(vals.pvRows || []).map((r, $index) => (
                  <React.Fragment key={$index}>
                    <div style={{ display: "grid", gridTemplateColumns: "1fr 46px 40px", gap: "7px", alignItems: "baseline" }}>
                      <span style={{ fontSize: "9.5px", color: "var(--color-neutral-400)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {r.head}
                      </span>
                      <span style={{ fontSize: "9.5px", fontFamily: "ui-monospace,monospace", textAlign: "right", color: "var(--color-neutral-500)" }}>
                        {r.detected}
                      </span>
                      <span style={{ fontSize: "9.5px", fontFamily: "ui-monospace,monospace", textAlign: "right", color: r.color }}>
                        {r.saved}
                      </span>
                    </div>
                  </React.Fragment>
                ))}
              </div>
            </div>
          </div>
          <div style={{ padding: "64px 0 0", display: "flex", flexDirection: "column", gap: "9px", alignItems: "center", textAlign: "center" }}>
            <h1 style={{ fontSize: "37px", margin: "0", lineHeight: "1.1" }}>
              {"Ask. Run. Anything."}
            </h1>
            <div title={vals.heroLive ? "From the live register" : "Seed portfolio — the live register has not answered yet"} style={{ display: "flex", alignItems: "center", justifyContent: "center", flexWrap: "wrap", gap: "6px 10px", fontSize: "15px", letterSpacing: "0.02em", color: "var(--color-neutral-400)", marginTop: "4px" }}>
              {(vals.heroStats || []).map((h, $index) => (
                <React.Fragment key={$index}>
                  {$index > 0 ? (
                    <span style={{ opacity: "0.4" }}>
                      {"·"}
                    </span>
                  ) : null}
                  <span style={{ fontWeight: "400" }}>
                    {h}
                  </span>
                </React.Fragment>
              ))}
            </div>
          </div>
          <div style={{ marginTop: "26px", position: "relative", width: "100%", maxWidth: "760px", alignSelf: "center" }}>
            <div style={{ display: "flex", alignItems: "center", gap: "12px", padding: "15px 18px", borderRadius: "12px", background: "var(--color-surface)", boxShadow: "var(--shadow-md)", borderBottom: "2px solid var(--color-accent)" }}>
              <i className="ph ph-sparkle" style={{ fontSize: "17px", color: "var(--color-accent)" }}></i>
              <input className="input" value={vals.query} onChange={vals.setQuery} onKeyDown={vals.onKey} placeholder="Which buildings put me at risk this month?" style={{ flex: "1", background: "transparent", border: "none", fontSize: "16px", color: "var(--color-text)", padding: "0", outline: "none", fontFamily: "var(--font-body)" }} />
              {/* Hidden outright for an account whose "Can ingest" is off — asking a
                  question is not ingesting, so only this control goes. */}
              {vals.canIngest ? (
                <label className="hv13" title="Ingest a document — attach a contract, certificate or invoice and file it against a building" style={{ display: "flex", alignItems: "center", gap: "6px", padding: "6px 10px", borderRadius: "8px", border: "1px solid var(--color-divider)", cursor: "pointer", flexShrink: "0" }}>
                  <i className="ph ph-paperclip" style={{ fontSize: "15px", color: "var(--color-neutral-400)" }}></i>
                  <span style={{ fontSize: "12px", color: "var(--color-neutral-300)", whiteSpace: "nowrap" }}>{"Ingest"}</span>
                  <input type="file" multiple accept=".pdf,.doc,.docx,.csv,.xls,.xlsx,image/*" onChange={vals.orchPickFiles} style={{ display: "none" }} />
                </label>
              ) : null}
              <div className="btn btn-primary" onClick={vals.runQuery} style={{ fontSize: "12px", padding: "7px 15px", cursor: "pointer", whiteSpace: "nowrap" }}>
                {"Run"}
              </div>
            </div>
            <div style={{ display: vals.orchAttachShow || "none", flexWrap: "wrap", gap: "6px", marginTop: "9px" }}>
              {(vals.orchFiles || []).map((f) => (
                <React.Fragment key={f.key}>
                  <span style={{ display: "inline-flex", alignItems: "center", gap: "6px", padding: "5px 9px", borderRadius: "7px", border: "1px solid var(--color-divider)", background: "var(--color-surface)", fontSize: "11.5px", maxWidth: "260px" }}>
                    <i className={`ph ${f.icon}`} style={{ fontSize: "12px", color: "var(--color-accent)", flexShrink: "0" }}></i>
                    <span style={{ minWidth: "0", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{f.name}</span>
                    <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "9.5px", color: "var(--color-neutral-500)", flexShrink: "0" }}>{f.size}</span>
                    <button type="button" className="hv21" onClick={f.drop} title={"Remove " + f.name} style={{ background: "none", border: "none", padding: "0", display: "flex", opacity: "0.6", cursor: "pointer", flexShrink: "0" }}>
                      <i className="ph ph-x" style={{ fontSize: "10px" }}></i>
                    </button>
                  </span>
                </React.Fragment>
              ))}
            </div>
            <div style={{ display: vals.cbShow || "none", flexDirection: "column", gap: "7px", marginTop: "9px", padding: "9px 11px", borderRadius: "9px", border: "1px solid " + (vals.cbChosen ? "var(--color-divider)" : "var(--st-warn)"), background: vals.cbChosen ? "var(--color-surface)" : "var(--st-warn-bg)", textAlign: "left" }}>
              <div style={{ display: "flex", alignItems: "center", gap: "9px", flexWrap: "wrap" }}>
                <i className={`ph ${vals.cbChosen ? "ph-buildings" : "ph-warning"}`} style={{ fontSize: "14px", color: vals.cbChosen ? "var(--color-accent)" : "var(--st-warn)", flexShrink: "0" }}></i>
                <span style={{ flex: "1", minWidth: "0", fontSize: "12px", fontWeight: vals.cbChosen ? "500" : "400", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {vals.cbLabel}
                </span>
                <button type="button" className="hv13" onClick={vals.cbOpen} style={{ background: "none", fontSize: "11px", padding: "4px 10px", borderRadius: "6px", border: "1px solid var(--color-divider)", cursor: "pointer", whiteSpace: "nowrap", color: "inherit" }}>
                  {vals.cbChosen ? "Change" : "Choose a building"}
                </button>
                {vals.cbChosen ? (
                  <button type="button" className="hv21" onClick={vals.cbClear} title="File against no building" style={{ background: "none", border: "none", padding: "0", display: "flex", opacity: "0.6", cursor: "pointer" }}>
                    <i className="ph ph-x" style={{ fontSize: "11px" }}></i>
                  </button>
                ) : (
                  <button type="button" className="hv13" onClick={vals.cbHoist} style={{ background: "none", fontSize: "11px", padding: "4px 10px", borderRadius: "6px", border: "1px solid var(--color-divider)", cursor: "pointer", whiteSpace: "nowrap", color: "inherit" }}>
                    {"Hoist a new one"}
                  </button>
                )}
              </div>
              <span style={{ fontSize: "10.5px", color: "var(--color-neutral-500)", lineHeight: "1.45" }}>
                {vals.cbWarn}
              </span>
              {vals.cbPickerOpen ? (
                <div style={{ display: "flex", flexDirection: "column", gap: "5px", marginTop: "2px", padding: "8px", borderRadius: "8px", border: "1px solid var(--color-divider)", background: "var(--color-bg)" }}>
                  <input className="input" autoFocus value={vals.cbQuery} onChange={vals.cbSetQuery}
                    onKeyDown={(e) => { if (e.key === "Escape") vals.cbClose(); }}
                    placeholder="Search by name or code" style={{ fontSize: "12px", padding: "6px 9px" }} />
                  <div style={{ display: "flex", flexDirection: "column", maxHeight: "188px", overflowY: "auto" }}>
                    {(vals.cbMatches || []).map((b) => (
                      <React.Fragment key={b.id}>
                        <button type="button" className="hv13" onClick={b.pick} style={{ display: "flex", alignItems: "center", gap: "8px", padding: "6px 8px", borderRadius: "6px", cursor: "pointer", textAlign: "left", border: "none", color: "inherit", background: b.on ? "var(--color-accent-900)" : "transparent" }}>
                          <span style={{ flex: "1", minWidth: "0", fontSize: "12px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{b.name}</span>
                          <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "9.5px", color: "var(--color-neutral-500)", flexShrink: "0" }}>{b.code}</span>
                        </button>
                      </React.Fragment>
                    ))}
                    {vals.cbEmpty ? (
                      <span style={{ padding: "8px", fontSize: "11px", color: "var(--color-neutral-500)" }}>
                        {"No building matches that. Check the code, or hoist a new one."}
                      </span>
                    ) : null}
                  </div>
                </div>
              ) : null}
            </div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: "7px", marginTop: "11px", alignItems: "center", justifyContent: "center" }}>
              <span style={{ fontSize: "10.5px", letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--color-neutral-600)", marginRight: "3px" }}>
                {"Pinned runs"}
              </span>
              {(vals.pinned || []).map((p, $index) => (
                <React.Fragment key={$index}>
                  <div className="hv17" onClick={p.run} style={{ fontSize: "12px", padding: "5px 11px", borderRadius: "20px", border: "1px solid var(--color-divider)", color: "var(--color-neutral-300)", cursor: "pointer" }}>
                    {p.label}
                  </div>
                </React.Fragment>
              ))}
            </div>
          </div>
          <div style={{ display: "none" }}>
            <div>
              <div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
                {(vals.queuePreview || []).map((d, $index) => (
                  <React.Fragment key={$index}>
                    <div className="hv1" onClick={d.click} style={{ display: "flex", gap: "12px", padding: "13px 14px", borderRadius: "10px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", cursor: "pointer", alignItems: "flex-start" }}>
                      <i className={`ph ${d.icon}`} style={{ fontSize: "15px", marginTop: "1px", color: d.color }}></i>
                      <div style={{ flex: "1", minWidth: "0" }}>
                        <div style={{ fontSize: "13px", lineHeight: "1.35" }}>
                          {d.title}
                        </div>
                        <div style={{ fontSize: "11px", color: "var(--color-neutral-500)", marginTop: "3px" }}>
                          {d.meta}
                        </div>
                      </div>
                      <div style={{ fontSize: "11px", padding: "3px 8px", borderRadius: "5px", whiteSpace: "nowrap", color: d.color, background: d.bg }}>
                        {d.money}
                      </div>
                    </div>
                  </React.Fragment>
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>
  );
}
