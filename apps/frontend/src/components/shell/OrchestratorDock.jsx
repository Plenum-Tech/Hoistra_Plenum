// OrchestratorDock — the agent dock and its flows
// Ported from the Hoistra prototype template. `vals` is the view model from useHoistra().
import React from 'react';

export default function OrchestratorDock({ vals }) {
  return (
      <div style={{ position: "fixed", top: "0", bottom: "0", left: vals.orchLeft, width: vals.orchWidth, boxSizing: "border-box", zIndex: "60", background: "var(--color-surface)", borderRight: "1px solid var(--color-divider)", display: "flex", flexDirection: "column", padding: "22px 28px 24px", overflowY: "auto", transition: "left 0.2s ease" }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
            <span style={{ width: "7px", height: "7px", borderRadius: "50%", background: "var(--color-accent)", display: vals.orchLiveDot }}></span>
            <span style={{ fontSize: "10.5px", letterSpacing: "0.13em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
              {"Ask. Run. Anything."}
            </span>
          </div>
          <i className="ph ph-x hv6" onClick={vals.closeOrch} style={{ fontSize: "15px", color: "var(--color-neutral-500)", cursor: "pointer" }}></i>
        </div>
        <div style={{ fontSize: "15px", lineHeight: "1.35", marginTop: "16px" }}>
          {vals.orchTitle}
        </div>
        <div style={{ fontSize: "11px", color: "var(--color-neutral-500)", marginTop: "4px" }}>
          {vals.orchStatus}
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: "0", marginTop: "18px" }}>
          {(vals.orchSteps || []).map((st, $index) => (
            <React.Fragment key={$index}>
              <div style={{ display: "grid", gridTemplateColumns: "16px 1fr", gap: "10px", padding: "9px 0", borderTop: "1px solid var(--color-divider)" }}>
                <i className={`ph ${st.icon}`} style={{ fontSize: "13px", color: st.dot, marginTop: "2px" }}></i>
                <div style={{ display: "flex", flexDirection: "column", gap: "3px" }}>
                  <span style={{ fontSize: "10.5px", padding: "1px 6px", borderRadius: "4px", background: "var(--color-accent-900)", color: "var(--color-accent-300)", alignSelf: "flex-start" }}>
                    {st.a}
                  </span>
                  <span style={{ fontSize: "11.5px", lineHeight: "1.5", color: st.fg }}>
                    {st.t}
                  </span>
                </div>
              </div>
            </React.Fragment>
          ))}
        </div>
        {vals.fUpdate ? (
          <>
            <div style={{ marginTop: "16px", padding: "12px", borderRadius: "9px", background: "var(--color-bg)", border: "1px solid var(--color-accent)" }}>
              <div style={{ fontSize: "9.5px", letterSpacing: "0.12em", textTransform: "uppercase", color: "var(--color-accent)" }}>
                {"Update the graph"}
              </div>
              {vals.ug.asking ? (
                <>
                  <div style={{ fontSize: "12px", lineHeight: "1.45", marginTop: "8px" }}>
                    {"What do you want to change?"}
                  </div>
                  <div style={{ fontSize: "10.5px", color: "var(--color-neutral-500)", lineHeight: "1.45", marginTop: "4px" }}>
                    {"Remove a record, replace a document with a newer one, or correct a value. Say it plainly."}
                  </div>
                  <textarea className="input" value={vals.ug.text} onChange={vals.ug.setText} rows="3" placeholder="Replace the EICR for AN Other House with the new one" style={{ width: "100%", boxSizing: "border-box", fontSize: "11.5px", lineHeight: "1.5", padding: "7px 8px", borderRadius: "6px", border: "1px solid var(--color-divider)", background: "var(--color-surface)", color: "var(--color-text)", fontFamily: "var(--font-body)", outline: "none", resize: "vertical", marginTop: "9px" }}></textarea>
                  <div style={{ display: "flex", flexDirection: "column", gap: "4px", marginTop: "8px" }}>
                    {(vals.ug.examples || []).map((e, $index) => (
                      <React.Fragment key={$index}>
                        <div className="hv4" onClick={e.pick} style={{ fontSize: "11px", padding: "6px 9px", borderRadius: "6px", border: "1px solid var(--color-divider)", color: "var(--color-neutral-400)", cursor: "pointer", lineHeight: "1.4" }}>
                          {e.label}
                        </div>
                      </React.Fragment>
                    ))}
                  </div>
                  <div style={{ display: "flex", gap: "6px", marginTop: "11px" }}>
                    <div className="hv7" onClick={vals.ug.parse} style={{ flex: "1", textAlign: "center", fontSize: "11.5px", padding: "6px", borderRadius: "7px", background: "var(--color-accent)", color: "var(--accent-ink)", cursor: "pointer" }}>
                      {"Work out what that means"}
                    </div>
                    <div className="hv11" onClick={vals.fCancel} style={{ fontSize: "11.5px", padding: "6px 10px", borderRadius: "7px", border: "1px solid var(--color-divider)", color: "var(--color-neutral-500)", cursor: "pointer" }}>
                      {"Cancel"}
                    </div>
                  </div>
                </>
              ) : null}
              {vals.ug.confirming ? (
                <>
                  <div style={{ fontSize: "11px", color: "var(--color-neutral-500)", lineHeight: "1.45", marginTop: "8px" }}>
                    {"You asked"}
                  </div>
                  <div style={{ fontSize: "11.5px", lineHeight: "1.5", marginTop: "3px", padding: "8px 10px", borderRadius: "7px", background: "var(--color-surface)" }}>
                    {"“"}{vals.ug.text}{"”"}
                  </div>
                  <div style={{ fontSize: "11px", color: "var(--color-neutral-500)", marginTop: "11px" }}>
                    {"Read as"}
                  </div>
                  <div style={{ display: "flex", flexDirection: "column", gap: "5px", marginTop: "6px" }}>
                    {(vals.ug.plan || []).map((p, $index) => (
                      <React.Fragment key={$index}>
                        <div style={{ display: "grid", gridTemplateColumns: "auto minmax(0,1fr)", gap: "8px", alignItems: "baseline", padding: "7px 10px", borderRadius: "7px", background: "var(--color-surface)" }}>
                          <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "9.5px", padding: "2px 6px", borderRadius: "4px", background: p.tagBg, color: p.tagFg, whiteSpace: "nowrap" }}>
                            {p.tag}
                          </span>
                          <div style={{ minWidth: "0" }}>
                            <div style={{ fontSize: "11.5px", lineHeight: "1.4" }}>
                              {p.what}
                            </div>
                            <div style={{ fontFamily: "ui-monospace,monospace", fontSize: "10px", color: "var(--color-neutral-500)", marginTop: "2px" }}>
                              {p.where}
                            </div>
                          </div>
                        </div>
                      </React.Fragment>
                    ))}
                  </div>
                  <div style={{ display: "flex", alignItems: "center", gap: "9px", marginTop: "11px", padding: "9px 10px", borderRadius: "7px", border: "2px solid var(--marker)", background: "var(--marker-tint)" }}>
                    <i className="ph ph-file-text" style={{ fontSize: "15px", color: "var(--color-text)", flexShrink: "0" }}></i>
                    <div style={{ minWidth: "0", flex: "1" }}>
                      <div style={{ fontSize: "11.5px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {vals.ug.docName}
                      </div>
                      <div style={{ fontFamily: "ui-monospace,monospace", fontSize: "10px", color: "var(--color-neutral-300)", marginTop: "2px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {vals.ug.docMeta}
                      </div>
                    </div>
                    <span onClick={vals.ug.viewDoc} style={{ fontSize: "10.5px", color: "var(--color-text)", cursor: "pointer", whiteSpace: "nowrap", textDecoration: "underline" }}>
                      {"View"}
                    </span>
                  </div>
                  <div style={{ fontSize: "10.5px", color: "var(--color-neutral-500)", lineHeight: "1.45", marginTop: "9px" }}>
                    {vals.ug.consequence}
                  </div>
                  <div style={{ display: "flex", gap: "6px", marginTop: "11px" }}>
                    <div className="hv7" onClick={vals.ug.apply} style={{ flex: "1", textAlign: "center", fontSize: "11.5px", padding: "6px", borderRadius: "7px", background: "var(--color-accent)", color: "var(--accent-ink)", cursor: "pointer" }}>
                      {"Apply to the graph"}
                    </div>
                    <div className="hv4" onClick={vals.ug.back} style={{ fontSize: "11.5px", padding: "6px 10px", borderRadius: "7px", border: "1px solid var(--color-divider)", color: "var(--color-neutral-400)", cursor: "pointer" }}>
                      {"Reword"}
                    </div>
                    <div className="hv11" onClick={vals.fCancel} style={{ fontSize: "11.5px", padding: "6px 10px", borderRadius: "7px", border: "1px solid var(--color-divider)", color: "var(--color-neutral-500)", cursor: "pointer" }}>
                      {"Cancel"}
                    </div>
                  </div>
                </>
              ) : null}
            </div>
          </>
        ) : null}
        {vals.fDeclare ? (
          <>
            <div style={{ marginTop: "16px", padding: "12px", borderRadius: "9px", background: "var(--color-bg)", border: "1px solid var(--color-accent)" }}>
              <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: "8px" }}>
                <span style={{ fontSize: "9.5px", letterSpacing: "0.12em", textTransform: "uppercase", color: "var(--color-accent)" }}>
                  {"Hoist a building"}
                </span>
                <span style={{ fontSize: "9.5px", color: "var(--color-neutral-500)", whiteSpace: "nowrap" }}>
                  {vals.dStepLabel}
                </span>
              </div>
              {vals.dStep1 ? (
                <>
                  <div style={{ fontSize: "10.5px", color: "var(--color-neutral-500)", lineHeight: "1.45", marginTop: "7px" }}>
                    {"This record becomes the primary key. Every document ingested afterwards is stamped with it, so nothing sits in the graph unattached to an asset."}
                  </div>
                  <div style={{ display: "flex", flexDirection: "column", gap: "7px", marginTop: "10px" }}>
                    {(vals.dFields || []).map((fd, $index) => (
                      <React.Fragment key={$index}>
                        <div style={{ display: "flex", flexDirection: "column", gap: "3px" }}>
                          <span style={{ fontSize: "9.5px", letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                            {fd.label}
                          </span>
                          <input className="input" value={fd.value} onChange={fd.set} placeholder={fd.ph} style={{ display: fd.isText, width: "100%", boxSizing: "border-box", fontSize: "11.5px", padding: "6px 8px", borderRadius: "6px", border: "1px solid var(--color-divider)", background: "var(--color-surface)", color: "var(--color-text)", fontFamily: "var(--font-body)", outline: "none" }} />
                          <select className="input" value={fd.value} onChange={fd.set} style={{ display: fd.isSelect, width: "100%", boxSizing: "border-box", fontSize: "11.5px", padding: "6px 8px", borderRadius: "6px", border: "1px solid var(--color-divider)", background: "var(--color-surface)", color: "var(--color-text)", fontFamily: "var(--font-body)", outline: "none" }}>
                            {(fd.options || []).map((o, $index) => (
                              <React.Fragment key={$index}>
                                <option value={o}>
                                  {o}
                                </option>
                              </React.Fragment>
                            ))}
                          </select>
                        </div>
                      </React.Fragment>
                    ))}
                    <div style={{ display: vals.dMixShow, flexDirection: "column", gap: "7px", padding: "9px 10px", borderRadius: "7px", background: "var(--color-surface)" }}>
                      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: "8px" }}>
                        <span style={{ fontSize: "9.5px", letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                          {"Floor area by use"}
                        </span>
                        <span style={{ fontSize: "9.5px", fontFamily: "ui-monospace,monospace", color: vals.dMixColor }}>
                          {vals.dMixTotal}
                        </span>
                      </div>
                      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "5px 8px" }}>
                        {(vals.dMixFields || []).map((m, $index) => (
                          <React.Fragment key={$index}>
                            <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) 44px", gap: "5px", alignItems: "center" }}>
                              <span style={{ fontSize: "10.5px", color: "var(--color-neutral-400)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                                {m.label}
                              </span>
                              <input className="input" value={m.value} onChange={m.set} placeholder="0%" style={{ width: "100%", boxSizing: "border-box", fontSize: "11px", padding: "4px 6px", borderRadius: "5px", border: "1px solid var(--color-divider)", background: "var(--color-bg)", color: "var(--color-text)", fontFamily: "ui-monospace,monospace", outline: "none" }} />
                            </div>
                          </React.Fragment>
                        ))}
                      </div>
                    </div>
                  </div>
                  <div style={{ fontSize: "10px", color: "var(--st-warn)", marginTop: "8px", lineHeight: "1.4" }}>
                    {vals.dValidNote}
                  </div>
                  <div style={{ display: "flex", gap: "6px", marginTop: "10px" }}>
                    <div className="hv7" onClick={vals.dNext} style={{ flex: "1", textAlign: "center", fontSize: "11.5px", padding: "6px", borderRadius: "7px", background: "var(--color-accent)", color: "var(--accent-ink)", cursor: "pointer" }}>
                      {"Write the record"}
                    </div>
                    <div className="hv11" onClick={vals.fCancel} style={{ fontSize: "11.5px", padding: "6px 10px", borderRadius: "7px", border: "1px solid var(--color-divider)", color: "var(--color-neutral-500)", cursor: "pointer" }}>
                      {"Cancel"}
                    </div>
                  </div>
                </>
              ) : null}
              {vals.dStep2 ? (
                <>
                  <div style={{ fontSize: "11.5px", lineHeight: "1.5", marginTop: "8px" }}>
                    {vals.dName}{" is keyed as "}
                    <span style={{ fontFamily: "ui-monospace,monospace", color: "var(--color-accent)" }}>
                      {vals.dNewId}
                    </span>
                    {"."}
                  </div>
                  <div style={{ display: "flex", flexDirection: "column", gap: "5px", marginTop: "10px" }}>
                    <div style={{ padding: "8px 10px", borderRadius: "7px", background: "var(--color-surface)", fontSize: "10.5px", lineHeight: "1.5" }}>
                      <span style={{ fontFamily: "ui-monospace,monospace", color: "var(--color-accent-300)" }}>
                        {"buildings"}
                      </span>
                      {" · primary key "}
                      <span style={{ fontFamily: "ui-monospace,monospace" }}>
                        {"building_id"}
                      </span>
                      {" — name, country, region, use, floors, area\n              "}
                    </div>
                    <div style={{ padding: "8px 10px", borderRadius: "7px", background: "var(--color-surface)", fontSize: "10.5px", lineHeight: "1.5" }}>
                      <span style={{ fontFamily: "ui-monospace,monospace", color: "var(--color-accent-300)" }}>
                        {"floors"}
                      </span>
                      {" · primary key "}
                      <span style={{ fontFamily: "ui-monospace,monospace" }}>
                        {"floor_id"}
                      </span>
                      {" — foreign key "}
                      <span style={{ fontFamily: "ui-monospace,monospace" }}>
                        {"building_id"}
                      </span>
                      {", use, area\n              "}
                    </div>
                    <div style={{ padding: "8px 10px", borderRadius: "7px", background: "var(--color-surface)", fontSize: "10.5px", lineHeight: "1.5" }}>
                      <span style={{ fontFamily: "ui-monospace,monospace", color: "var(--color-accent-300)" }}>
                        {"documents"}
                      </span>
                      {" · foreign key "}
                      <span style={{ fontFamily: "ui-monospace,monospace" }}>
                        {"building_id"}
                      </span>
                      {" — every certificate, contract and reading resolves back here\n              "}
                    </div>
                  </div>
                  <div style={{ display: "flex", gap: "6px", marginTop: "11px" }}>
                    <div className="hv7" onClick={vals.dNext} style={{ flex: "1", textAlign: "center", fontSize: "11.5px", padding: "6px", borderRadius: "7px", background: "var(--color-accent)", color: "var(--accent-ink)", cursor: "pointer" }}>
                      {"Next steps"}
                    </div>
                    <div className="hv4" onClick={vals.dBack} style={{ fontSize: "11.5px", padding: "6px 10px", borderRadius: "7px", border: "1px solid var(--color-divider)", color: "var(--color-neutral-400)", cursor: "pointer" }}>
                      {"Back"}
                    </div>
                  </div>
                </>
              ) : null}
              {vals.dStep3 ? (
                <>
                  <div style={{ fontSize: "10.5px", color: "var(--color-neutral-500)", lineHeight: "1.45", marginTop: "8px" }}>
                    {"The record exists but holds no evidence. Ingest now, or come back to it — the building simply carries a 0% Hoist Score until documents arrive."}
                  </div>
                  <div style={{ display: "flex", flexDirection: "column", gap: "6px", marginTop: "11px" }}>
                    <div className="hv12" onClick={vals.dIngestNow} style={{ padding: "10px 11px", borderRadius: "8px", background: "var(--color-accent)", color: "var(--accent-ink)", cursor: "pointer" }}>
                      <div style={{ fontSize: "12px" }}>
                        {"Ingest documents now"}
                      </div>
                      <div style={{ fontSize: "10.5px", opacity: "0.8", marginTop: "2px", lineHeight: "1.4" }}>
                        {"Certificates, contracts, asset registers, meter consent"}
                      </div>
                    </div>
                    <div className="hv13" onClick={vals.dLater} style={{ padding: "10px 11px", borderRadius: "8px", border: "1px solid var(--color-divider)", cursor: "pointer" }}>
                      <div style={{ fontSize: "12px" }}>
                        {"Do it later"}
                      </div>
                      <div style={{ fontSize: "10.5px", color: "var(--color-neutral-500)", marginTop: "2px", lineHeight: "1.4" }}>
                        {"Hoist the record only — live and waiting"}
                      </div>
                    </div>
                  </div>
                </>
              ) : null}
            </div>
          </>
        ) : null}
        {vals.fIngest ? (
          <>
            <div style={{ marginTop: "16px", padding: "12px", borderRadius: "9px", background: "var(--color-bg)", border: "1px solid var(--color-accent)" }}>
              <div style={{ fontSize: "9.5px", letterSpacing: "0.12em", textTransform: "uppercase", color: "var(--color-accent)" }}>
                {"Ingest documents"}
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: "3px", marginTop: "10px" }}>
                <span style={{ fontSize: "9.5px", letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                  {"Which building"}
                </span>
                <select className="input" value={vals.iBuilding} onChange={vals.setIBuilding} style={{ width: "100%", boxSizing: "border-box", fontSize: "11.5px", padding: "6px 8px", borderRadius: "6px", border: "1px solid var(--color-divider)", background: "var(--color-surface)", color: "var(--color-text)", fontFamily: "var(--font-body)", outline: "none" }}>
                  {(vals.iBuildingOpts || []).map((o, $index) => (
                    <React.Fragment key={$index}>
                      <option value={o}>
                        {o}
                      </option>
                    </React.Fragment>
                  ))}
                </select>
              </div>
              <div style={{ fontSize: "10.5px", color: "var(--color-neutral-500)", lineHeight: "1.45", marginTop: "10px" }}>
                {"Everything ingested carries that building's ID as a foreign key."}
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: "4px", marginTop: "8px" }}>
                {(vals.iClasses || []).map((c, $index) => (
                  <React.Fragment key={$index}>
                    <div style={{ display: "flex", alignItems: "center", gap: "7px", padding: "6px 9px", borderRadius: "6px", background: "var(--color-surface)", fontSize: "10.5px" }}>
                      <i className="ph ph-file-arrow-up" style={{ fontSize: "12px", color: "var(--color-accent)", flexShrink: "0" }}></i>
                      <span style={{ minWidth: "0" }}>
                        {c}
                      </span>
                    </div>
                  </React.Fragment>
                ))}
              </div>
              <div style={{ display: "flex", gap: "6px", marginTop: "11px" }}>
                <div className="hv7" onClick={vals.iRun} style={{ flex: "1", textAlign: "center", fontSize: "11.5px", padding: "6px", borderRadius: "7px", background: "var(--color-accent)", color: "var(--accent-ink)", cursor: "pointer" }}>
                  {"Start ingestion"}
                </div>
                <div className="hv11" onClick={vals.fCancel} style={{ fontSize: "11.5px", padding: "6px 10px", borderRadius: "7px", border: "1px solid var(--color-divider)", color: "var(--color-neutral-500)", cursor: "pointer" }}>
                  {"Cancel"}
                </div>
              </div>
            </div>
          </>
        ) : null}
        {vals.fInputs ? (
          <>
            <div style={{ marginTop: "16px", padding: "12px", borderRadius: "9px", background: "var(--color-bg)", border: "1px solid var(--color-accent)" }}>
              <div style={{ fontSize: "9.5px", letterSpacing: "0.12em", textTransform: "uppercase", color: "var(--color-accent)" }}>
                {vals.fiTitle}
              </div>
              <div style={{ fontSize: "10.5px", color: "var(--color-neutral-500)", lineHeight: "1.45", marginTop: "6px" }}>
                {"The graph holds everything except the following. Supply these and the orchestrator drafts the communication."}
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: "7px", marginTop: "10px" }}>
                {(vals.fiFields || []).map((f, $index) => (
                  <React.Fragment key={$index}>
                    <div style={{ display: "flex", flexDirection: "column", gap: "3px" }}>
                      <span style={{ fontSize: "9.5px", letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                        {f.label}
                      </span>
                      <input className="input" type={f.inputType} value={f.value} onChange={f.set} placeholder={f.ph} style={{ display: f.isText, width: "100%", boxSizing: "border-box", fontSize: "11.5px", padding: "6px 8px", borderRadius: "6px", border: "1px solid var(--color-divider)", background: "var(--color-surface)", color: "var(--color-text)", fontFamily: "var(--font-body)", outline: "none" }} />
                      <select className="input" value={f.value} onChange={f.set} style={{ display: f.isSelect, width: "100%", boxSizing: "border-box", fontSize: "11.5px", padding: "6px 8px", borderRadius: "6px", border: "1px solid var(--color-divider)", background: "var(--color-surface)", color: "var(--color-text)", fontFamily: "var(--font-body)", outline: "none" }}>
                        {(f.options || []).map((o, $index) => (
                          <React.Fragment key={$index}>
                            <option value={o}>
                              {o}
                            </option>
                          </React.Fragment>
                        ))}
                      </select>
                    </div>
                  </React.Fragment>
                ))}
              </div>
              <div style={{ display: "flex", gap: "6px", marginTop: "11px" }}>
                <div className="hv7" onClick={vals.fiContinue} style={{ flex: "1", textAlign: "center", fontSize: "11.5px", padding: "6px", borderRadius: "7px", background: "var(--color-accent)", color: "var(--accent-ink)", cursor: "pointer" }}>
                  {"Draft communication"}
                </div>
                <div className="hv11" onClick={vals.fCancel} style={{ fontSize: "11.5px", padding: "6px 10px", borderRadius: "7px", border: "1px solid var(--color-divider)", color: "var(--color-neutral-500)", cursor: "pointer" }}>
                  {"Cancel"}
                </div>
              </div>
            </div>
          </>
        ) : null}
        {vals.fBooking ? (
          <>
            <div style={{ marginTop: "16px", padding: "12px", borderRadius: "9px", background: "var(--color-bg)", border: "1px solid var(--color-accent)" }}>
              <div style={{ fontSize: "9.5px", letterSpacing: "0.12em", textTransform: "uppercase", color: "var(--color-accent)" }}>
                {"Booking request · draft"}
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: "8px", marginTop: "10px" }}>
                <div style={{ display: "flex", flexDirection: "column", gap: "3px" }}>
                  <span style={{ fontSize: "9.5px", letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                    {"Vendor"}
                  </span>
                  <span style={{ fontSize: "12px" }}>
                    {vals.bk.vendor}
                  </span>
                  <span style={{ fontSize: "10px", color: "var(--color-neutral-500)", lineHeight: "1.4" }}>
                    {vals.bk.vendorMeta}
                  </span>
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: "3px" }}>
                  <span style={{ fontSize: "9.5px", letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                    {"Scope"}
                  </span>
                  <span style={{ fontSize: "11.5px", lineHeight: "1.45" }}>
                    {vals.bk.scope}
                  </span>
                </div>
                <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "7px" }}>
                  <div style={{ display: "flex", flexDirection: "column", gap: "3px", minWidth: "0" }}>
                    <span style={{ fontSize: "9.5px", letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                      {"Date"}
                    </span>
                    <input className="input" type="date" value={vals.bk.date} onChange={vals.bk.setDate} disabled={vals.bk.locked} style={{ width: "100%", boxSizing: "border-box", fontSize: "11.5px", padding: "6px 7px", borderRadius: "6px", border: "1px solid var(--color-divider)", background: "var(--color-surface)", color: "var(--color-text)", fontFamily: "var(--font-body)", outline: "none" }} />
                  </div>
                  <div style={{ display: "flex", flexDirection: "column", gap: "3px", minWidth: "0" }}>
                    <span style={{ fontSize: "9.5px", letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                      {"Window · 24h"}
                    </span>
                    <input className="input" value={vals.bk.window} onChange={vals.bk.setWindow} disabled={vals.bk.locked} style={{ width: "100%", boxSizing: "border-box", fontSize: "11.5px", padding: "6px 7px", borderRadius: "6px", border: "1px solid var(--color-divider)", background: "var(--color-surface)", color: "var(--color-text)", fontFamily: "ui-monospace,monospace", outline: "none" }} />
                  </div>
                </div>
                <span style={{ fontSize: "10px", color: "var(--color-neutral-500)", lineHeight: "1.4" }}>
                  {vals.bk.note}
                </span>
              </div>
              <div style={{ display: "flex", gap: "6px", marginTop: "11px" }}>
                <div className="hv7" onClick={vals.bk.approve} style={{ flex: "1", textAlign: "center", fontSize: "11.5px", padding: "6px", borderRadius: "7px", background: "var(--color-accent)", color: "var(--accent-ink)", cursor: "pointer" }}>
                  {"Approve"}
                </div>
                <div className="hv4" onClick={vals.bk.edit} style={{ fontSize: "11.5px", padding: "6px 10px", borderRadius: "7px", border: "1px solid var(--color-divider)", color: "var(--color-neutral-300)", cursor: "pointer" }}>
                  {vals.bk.editLabel}
                </div>
                <div className="hv11" onClick={vals.fCancel} style={{ fontSize: "11.5px", padding: "6px 10px", borderRadius: "7px", border: "1px solid var(--color-divider)", color: "var(--color-neutral-500)", cursor: "pointer" }}>
                  {"Cancel"}
                </div>
              </div>
            </div>
          </>
        ) : null}
        {vals.fPick ? (
          <>
            <div style={{ marginTop: "16px", padding: "12px", borderRadius: "9px", background: "var(--color-bg)", border: "1px solid var(--color-accent)" }}>
              <div style={{ fontSize: "9.5px", letterSpacing: "0.12em", textTransform: "uppercase", color: "var(--color-accent)" }}>
                {"Change contractor"}
              </div>
              <div style={{ fontSize: "10.5px", color: "var(--color-neutral-500)", lineHeight: "1.45", marginTop: "7px" }}>
                {"Accredited for this asset type in the Hoist Graph:"}
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: "5px", marginTop: "9px" }}>
                {(vals.vendorPool || []).map((v, $index) => (
                  <React.Fragment key={$index}>
                    <div className="hv14" onClick={v.pick} style={{ padding: "7px 9px", borderRadius: "7px", border: "1px solid var(--color-divider)", cursor: "pointer", display: "flex", flexDirection: "column", gap: "1px" }}>
                      <span style={{ fontSize: "11.5px" }}>
                        {v.name}
                      </span>
                      <span style={{ fontSize: "10px", color: "var(--color-neutral-500)" }}>
                        {v.spec}{" · "}{v.acc}
                      </span>
                    </div>
                  </React.Fragment>
                ))}
                {vals.poolEmpty ? (
                  <>
                    <div style={{ padding: "9px", borderRadius: "7px", background: "var(--st-warn-bg)", fontSize: "10.5px", color: "var(--st-warn)", lineHeight: "1.45" }}>
                      {"No accredited contractor in the database for this asset type. Enter a new one."}
                    </div>
                  </>
                ) : null}
              </div>
              <div style={{ display: "flex", gap: "6px", marginTop: "11px" }}>
                <div className="hv15" onClick={vals.fNewVendor} style={{ flex: "1", textAlign: "center", fontSize: "11.5px", padding: "6px", borderRadius: "7px", border: "1px solid var(--color-accent)", color: "var(--color-accent)", cursor: "pointer" }}>
                  {"New contractor"}
                </div>
                <div className="hv11" onClick={vals.fCancel} style={{ fontSize: "11.5px", padding: "6px 10px", borderRadius: "7px", border: "1px solid var(--color-divider)", color: "var(--color-neutral-500)", cursor: "pointer" }}>
                  {"Cancel"}
                </div>
              </div>
            </div>
          </>
        ) : null}
        {vals.fNew ? (
          <>
            <div style={{ marginTop: "16px", padding: "12px", borderRadius: "9px", background: "var(--color-bg)", border: "1px solid var(--color-accent)" }}>
              <div style={{ fontSize: "9.5px", letterSpacing: "0.12em", textTransform: "uppercase", color: "var(--color-accent)" }}>
                {"New contractor"}
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: "7px", marginTop: "10px" }}>
                {(vals.nvFields || []).map((f, $index) => (
                  <React.Fragment key={$index}>
                    <div style={{ display: "flex", flexDirection: "column", gap: "3px" }}>
                      <span style={{ fontSize: "9.5px", letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                        {f.label}
                      </span>
                      <input className="input" value={f.value} onChange={f.set} placeholder={f.ph} style={{ width: "100%", boxSizing: "border-box", fontSize: "11.5px", padding: "6px 8px", borderRadius: "6px", border: "1px solid var(--color-divider)", background: "var(--color-surface)", color: "var(--color-text)", fontFamily: "var(--font-body)", outline: "none" }} />
                    </div>
                  </React.Fragment>
                ))}
                <div style={{ display: "flex", flexDirection: "column", gap: "3px" }}>
                  <span style={{ fontSize: "9.5px", letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                    {"Specialisation"}
                  </span>
                  <select className="input" value={vals.nvSpec} onChange={vals.setNvSpec} style={{ width: "100%", boxSizing: "border-box", fontSize: "11.5px", padding: "6px 8px", borderRadius: "6px", border: "1px solid var(--color-divider)", background: "var(--color-surface)", color: "var(--color-text)", fontFamily: "var(--font-body)", outline: "none" }}>
                    {(vals.specOpts || []).map((o, $index) => (
                      <React.Fragment key={$index}>
                        <option value={o}>
                          {o}
                        </option>
                      </React.Fragment>
                    ))}
                  </select>
                </div>
              </div>
              <div style={{ display: "flex", gap: "6px", marginTop: "11px" }}>
                <div className="hv7" onClick={vals.fCreateVendor} style={{ flex: "1", textAlign: "center", fontSize: "11.5px", padding: "6px", borderRadius: "7px", background: "var(--color-accent)", color: "var(--accent-ink)", cursor: "pointer" }}>
                  {"Create vendor"}
                </div>
                <div className="hv11" onClick={vals.fCancel} style={{ fontSize: "11.5px", padding: "6px 10px", borderRadius: "7px", border: "1px solid var(--color-divider)", color: "var(--color-neutral-500)", cursor: "pointer" }}>
                  {"Cancel"}
                </div>
              </div>
            </div>
          </>
        ) : null}
        {vals.fEmail ? (
          <>
            <div style={{ marginTop: "16px", padding: "12px", borderRadius: "9px", background: "var(--color-bg)", border: "1px solid var(--color-accent)" }}>
              <div style={{ fontSize: "9.5px", letterSpacing: "0.12em", textTransform: "uppercase", color: "var(--color-accent)" }}>
                {vals.em.kicker}
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: "7px", marginTop: "10px" }}>
                <div style={{ display: "flex", flexDirection: "column", gap: "3px" }}>
                  <span style={{ fontSize: "9.5px", letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                    {"To"}
                  </span>
                  <input className="input" value={vals.em.to} onChange={vals.em.setTo} placeholder="name@organisation.com" style={{ width: "100%", boxSizing: "border-box", fontSize: "11.5px", padding: "6px 8px", borderRadius: "6px", border: "1px solid var(--color-divider)", background: "var(--color-surface)", color: "var(--color-text)", fontFamily: "var(--font-body)", outline: "none" }} />
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: "3px" }}>
                  <span style={{ fontSize: "9.5px", letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                    {"Subject"}
                  </span>
                  <span style={{ fontSize: "11.5px", lineHeight: "1.4" }}>
                    {vals.em.subject}
                  </span>
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: "3px" }}>
                  <span style={{ fontSize: "9.5px", letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                    {"Draft"}
                  </span>
                  <textarea className="input" value={vals.em.body} onChange={vals.em.setBody} rows="7" style={{ width: "100%", boxSizing: "border-box", fontSize: "11px", lineHeight: "1.5", padding: "7px 8px", borderRadius: "6px", border: "1px solid var(--color-divider)", background: "var(--color-surface)", color: "var(--color-text)", fontFamily: "var(--font-body)", outline: "none", resize: "vertical" }}></textarea>
                </div>
              </div>
              <div style={{ display: "flex", gap: "6px", marginTop: "11px" }}>
                <div className="hv7" onClick={vals.em.send} style={{ flex: "1", textAlign: "center", fontSize: "11.5px", padding: "6px", borderRadius: "7px", background: "var(--color-accent)", color: "var(--accent-ink)", cursor: "pointer" }}>
                  {"Approve & send"}
                </div>
                <div className="hv11" onClick={vals.fCancel} style={{ fontSize: "11.5px", padding: "6px 10px", borderRadius: "7px", border: "1px solid var(--color-divider)", color: "var(--color-neutral-500)", cursor: "pointer" }}>
                  {"Cancel"}
                </div>
              </div>
            </div>
          </>
        ) : null}
        {vals.fInvestigate ? (
          <>
            <div style={{ marginTop: "16px", display: "flex", flexDirection: "column", gap: "10px" }}>
              <div style={{ alignSelf: "flex-end", maxWidth: "92%", padding: "9px 11px", borderRadius: "10px 10px 3px 10px", background: "var(--color-text)", color: "var(--color-bg)", fontSize: "12px", lineHeight: "1.45", textWrap: "pretty" }}>
                {vals.invQuery}
              </div>
              <div style={{ maxWidth: "96%", padding: "10px 11px", borderRadius: "10px 10px 10px 3px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)" }}>
                <div style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "9.5px", letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--color-accent)" }}>
                  <i className="ph ph-cpu" style={{ fontSize: "11px" }}></i>
                  <span>
                    {"Orchestrator"}
                  </span>
                </div>
                <div style={{ fontSize: "12px", lineHeight: "1.45", marginTop: "5px", textWrap: "pretty" }}>
                  {vals.invPlan}
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: "4px", marginTop: "8px" }}>
                  {(vals.invSources || []).map((r, $index) => (
                    <React.Fragment key={$index}>
                      <div style={{ display: "grid", gridTemplateColumns: "13px minmax(0,1fr) auto", gap: "7px", alignItems: "baseline", opacity: r.op }}>
                        <i className={`ph ${r.icon}`} style={{ fontSize: "11px", color: r.fg }}></i>
                        <div style={{ minWidth: "0", fontSize: "10.5px", color: "var(--color-neutral-400)", lineHeight: "1.35" }}>
                          <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10px", color: "var(--color-accent)" }}>
                            {r.tbl}
                          </span>
                          {" · "}{r.what}
                        </div>
                        <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "9.5px", color: r.gap, whiteSpace: "nowrap" }}>
                          {r.n}
                        </span>
                      </div>
                    </React.Fragment>
                  ))}
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: "6px", marginTop: "8px", fontSize: "10px", color: "var(--color-neutral-500)" }}>
                  <span style={{ display: vals.invLive, width: "5px", height: "5px", borderRadius: "50%", background: "var(--color-accent)", animation: "pendingBlink 1.2s ease-in-out infinite", flexShrink: "0" }}></span>
                  <span>
                    {vals.invStatus}
                  </span>
                </div>
              </div>
              {vals.invStage1 ? (
                <>
                  <div style={{ maxWidth: "96%", padding: "10px 11px", borderRadius: "10px 10px 10px 3px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", animation: "fadeUp 0.25s ease both" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "9.5px", letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                      <i className="ph ph-database" style={{ fontSize: "11px" }}></i>
                      <span>
                        {"Worker · evidence"}
                      </span>
                    </div>
                    <div style={{ display: "flex", flexDirection: "column", gap: "6px", marginTop: "7px" }}>
                      {(vals.invFindings || []).map((f, $index) => (
                        <React.Fragment key={$index}>
                          <div style={{ padding: "7px 9px", borderRadius: "7px", background: "var(--color-bg)" }}>
                            <div style={{ fontSize: "11.5px", lineHeight: "1.45", textWrap: "pretty" }}>
                              {f.t}
                            </div>
                            <div style={{ display: "flex", alignItems: "center", gap: "6px", marginTop: "4px", flexWrap: "wrap" }}>
                              <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "9.5px", color: "var(--color-neutral-500)" }}>
                                {f.src}
                              </span>
                              <span style={{ display: f.gapShow, fontFamily: "ui-monospace,monospace", fontSize: "9px", padding: "1px 5px", borderRadius: "4px", background: "var(--st-risk-bg)", color: "var(--st-risk)" }}>
                                {"missing record"}
                              </span>
                              <span style={{ flex: "1" }}></span>
                              <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "9.5px", color: f.confFg }}>
                                {f.conf}
                              </span>
                            </div>
                          </div>
                        </React.Fragment>
                      ))}
                    </div>
                  </div>
                </>
              ) : null}
              {vals.invStage2 ? (
                <>
                  <div style={{ maxWidth: "96%", padding: "10px 11px", borderRadius: "10px 10px 10px 3px", background: "var(--color-accent-900)", animation: "fadeUp 0.25s ease both" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "9.5px", letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--color-accent)" }}>
                      <i className="ph ph-cpu" style={{ fontSize: "11px" }}></i>
                      <span>
                        {"Orchestrator"}
                      </span>
                    </div>
                    <div style={{ fontSize: "12px", lineHeight: "1.45", marginTop: "5px", textWrap: "pretty" }}>
                      {vals.inv.cause}
                    </div>
                    <div style={{ fontFamily: "ui-monospace,monospace", fontSize: "10px", color: "var(--color-neutral-400)", marginTop: "6px" }}>
                      {vals.inv.costLine}
                    </div>
                    <div style={{ display: vals.invEscShow, marginTop: "8px", padding: "7px 9px", borderRadius: "7px", background: "var(--st-warn-bg)", gap: "7px", alignItems: "flex-start" }}>
                      <i className="ph ph-warning" style={{ fontSize: "12px", color: "var(--st-warn)", marginTop: "1px", flexShrink: "0" }}></i>
                      <span style={{ fontSize: "10.5px", lineHeight: "1.45", color: "var(--color-text)" }}>
                        {vals.invEscText}
                      </span>
                    </div>
                  </div>
                </>
              ) : null}
              {vals.invStage3 ? (
                <>
                  <div style={{ maxWidth: "96%", padding: "10px 11px", borderRadius: "10px 10px 10px 3px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", animation: "fadeUp 0.25s ease both" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "9.5px", letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--color-accent)" }}>
                      <i className="ph ph-cpu" style={{ fontSize: "11px" }}></i>
                      <span>
                        {"Orchestrator"}
                      </span>
                    </div>
                    <div style={{ fontSize: "12px", lineHeight: "1.45", marginTop: "5px", textWrap: "pretty" }}>
                      {vals.invAskMsg}
                    </div>
                    <div style={{ display: "flex", flexDirection: "column", gap: "5px", marginTop: "8px" }}>
                      {(vals.invActions || []).map((a, $index) => (
                        <React.Fragment key={$index}>
                          <div className="hv13" onClick={a.click} style={{ display: "grid", gridTemplateColumns: "15px minmax(0,1fr)", gap: "8px", alignItems: "start", padding: "7px 9px", borderRadius: "7px", border: `1px solid ${a.edge}`, background: "var(--color-bg)", cursor: "pointer", opacity: a.op }}>
                            <i className={`ph ${a.icon}`} style={{ fontSize: "13px", color: a.fg, marginTop: "1px" }}></i>
                            <div style={{ minWidth: "0" }}>
                              <div style={{ fontSize: "11.5px", lineHeight: "1.3" }}>
                                {a.l}
                              </div>
                              <div style={{ fontSize: "10px", color: "var(--color-neutral-500)", marginTop: "2px", lineHeight: "1.4" }}>
                                {a.s}
                              </div>
                            </div>
                          </div>
                        </React.Fragment>
                      ))}
                    </div>
                    <div style={{ display: "flex", gap: "6px", marginTop: "9px", flexWrap: "wrap" }}>
                      <div className="hv7" onClick={vals.invApproveAll} style={{ fontSize: "11px", padding: "5px 10px", borderRadius: "7px", background: "var(--color-accent)", color: "var(--accent-ink)", cursor: "pointer" }}>
                        {"Approve all"}
                      </div>
                      <div className="hv16" onClick={vals.fCancel} style={{ fontSize: "11px", padding: "5px 10px", borderRadius: "7px", border: "1px solid var(--color-divider)", color: "var(--color-neutral-500)", cursor: "pointer" }}>
                        {"Not now"}
                      </div>
                    </div>
                  </div>
                </>
              ) : null}
              {(vals.invReplies || []).map((m, $index) => (
                <React.Fragment key={$index}>
                  <div style={{ alignSelf: "flex-end", maxWidth: "92%", padding: "8px 11px", borderRadius: "10px 10px 3px 10px", background: "var(--color-text)", color: "var(--color-bg)", fontSize: "11.5px", lineHeight: "1.4", animation: "fadeUp 0.2s ease both" }}>
                    {m.you}
                  </div>
                  <div style={{ maxWidth: "96%", padding: "8px 11px", borderRadius: "10px 10px 10px 3px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", fontSize: "11.5px", lineHeight: "1.45", animation: "fadeUp 0.25s ease both", textWrap: "pretty" }}>
                    <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "9.5px", color: "var(--color-accent)" }}>
                      {"queued · "}
                    </span>
                    {m.bot}{"\n            "}
                  </div>
                </React.Fragment>
              ))}
            </div>
          </>
        ) : null}
        {vals.fDone ? (
          <>
            <div style={{ marginTop: "16px", padding: "11px 12px", borderRadius: "9px", background: "var(--st-ok-bg)", display: "flex", gap: "9px", alignItems: "flex-start" }}>
              <i className="ph ph-check-circle" style={{ fontSize: "14px", color: "var(--st-ok)", marginTop: "1px" }}></i>
              <span style={{ fontSize: "11px", lineHeight: "1.5", color: "var(--color-text)" }}>
                {vals.fDoneText}
              </span>
            </div>
          </>
        ) : null}
        <div style={{ flex: "1" }}></div>
        <div style={{ display: "flex", gap: "6px", marginTop: "22px" }}>
          <input className="input" value={vals.orchQuery} onChange={vals.setOrchQuery} onKeyDown={vals.orchKey} placeholder="" style={{ flex: "1", minWidth: "0", fontSize: "12px", padding: "8px 10px", borderRadius: "7px", border: "1px solid var(--color-divider)", background: "var(--color-bg)", color: "var(--color-text)", fontFamily: "var(--font-body)", outline: "none" }} />
          <div className="hv7" onClick={vals.orchSubmit} style={{ width: "34px", borderRadius: "7px", background: "var(--color-accent)", color: "var(--accent-ink)", display: "flex", alignItems: "center", justifyContent: "center", cursor: "pointer", flexShrink: "0" }}>
            <i className="ph ph-arrow-right" style={{ fontSize: "13px" }}></i>
          </div>
        </div>
        <div style={{ fontSize: "10px", color: "var(--color-neutral-500)", lineHeight: "1.45", marginTop: "8px" }}>
          {"Every instruction is stored as a session, whichever page it was raised from."}
        </div>
        {vals.orchHasRecent ? (
          <>
            <div style={{ fontSize: "10.5px", letterSpacing: "0.11em", textTransform: "uppercase", color: "var(--color-neutral-500)", marginTop: "20px" }}>
              {"Recent tasks"}
            </div>
            <div style={{ display: "flex", flexDirection: "column", marginTop: "6px" }}>
              {(vals.orchRecent || []).map((r, $index) => (
                <React.Fragment key={$index}>
                  <div style={{ display: "flex", flexDirection: "column", gap: "1px", padding: "7px 0", borderTop: "1px solid var(--color-divider)" }}>
                    <span style={{ fontSize: "11.5px", color: "var(--color-neutral-300)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {r.label}
                    </span>
                    <span style={{ fontSize: "10px", color: "var(--color-neutral-500)" }}>
                      {r.when}
                    </span>
                  </div>
                </React.Fragment>
              ))}
            </div>
          </>
        ) : null}
      </div>
  );
}
