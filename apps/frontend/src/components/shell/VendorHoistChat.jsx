// VendorHoistChat — hoisting a vendor as a conversation in the orchestrator dock
// (logic/vendorHoist.js): one question at a time, each answer echoed back, then the record as
// it would be written. Ported from the prototype's hoist-conversation-dock (11 Oct 2026).
import React from 'react';

const BUBBLE = { maxWidth: "96%", padding: "9px 11px", borderRadius: "10px 10px 10px 3px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)" };
const WHO = { display: "flex", alignItems: "center", gap: "6px", fontSize: "9.5px", letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--color-accent)" };
const SMALL_BTN = { fontFamily: "inherit", fontSize: "11px", padding: "5px 10px", borderRadius: "7px", cursor: "pointer", whiteSpace: "nowrap" };

function Who({ step }) {
  return (
    <div style={WHO}>
      <i className="ph ph-cpu" aria-hidden="true" style={{ fontSize: "11px" }}></i>
      <span>{"Orchestrator"}</span>
      {step ? <><span style={{ flex: "1" }}></span><span style={{ color: "var(--color-neutral-500)", letterSpacing: "0.06em", whiteSpace: "nowrap" }}>{step}</span></> : null}
    </div>
  );
}

export default function VendorHoistChat({ vals }) {
  const onKey = (e) => { if (e.key === "Enter") { e.preventDefault(); vals.vhcNext(); } };
  return (
    <div style={{ marginTop: "16px", display: "flex", flexDirection: "column", gap: "10px" }}>
      {(vals.vhcDone || []).map((m, i) => (
        <React.Fragment key={i}>
          <div style={BUBBLE}>
            <Who />
            <div style={{ fontSize: "12px", lineHeight: "1.45", marginTop: "5px", textWrap: "pretty" }}>{m.q}</div>
          </div>
          <div style={{ alignSelf: "flex-end", maxWidth: "90%", padding: "8px 11px", borderRadius: "10px 10px 3px 10px", background: "var(--color-text)", color: "var(--color-bg)", fontSize: "12px", lineHeight: "1.45", textWrap: "pretty", overflowWrap: "anywhere" }}>
            {m.a}
          </div>
          {m.reply ? (
            <div style={{ maxWidth: "96%", padding: "7px 10px", borderRadius: "8px", background: "var(--color-accent-900)", fontSize: "11px", lineHeight: "1.45", color: "var(--color-text)" }}>
              {m.reply}
            </div>
          ) : null}
        </React.Fragment>
      ))}

      {vals.vhcAsking ? (
        <div key={vals.vhcKey} style={Object.assign({}, BUBBLE, { maxWidth: "100%", padding: "10px 11px", border: "1px solid var(--color-accent)", animation: "fadeUp 0.22s ease both" })}>
          <Who step={vals.vhcStep} />
          <div style={{ fontSize: "12px", lineHeight: "1.45", marginTop: "5px", textWrap: "pretty" }}>{vals.vhcQ}</div>
          <div style={{ display: "flex", flexDirection: "column", gap: "7px", marginTop: "9px" }}>
            {(vals.vhcFields || []).map((fd, i) => (
              <label key={fd.key} style={{ display: "flex", flexDirection: "column", gap: "3px" }}>
                <span style={{ display: "flex", alignItems: "baseline", gap: "6px", fontSize: "10px", color: "var(--color-neutral-500)" }}>
                  {fd.label}
                  {fd.tag ? <span style={{ fontSize: "9.5px", padding: "0 5px", borderRadius: "4px", background: "var(--marker-tint)", color: "var(--color-neutral-300)" }}>{fd.tag}</span> : null}
                </span>
                <input className="input" autoFocus={i === 0} value={fd.value} onChange={fd.set} onKeyDown={onKey} placeholder={fd.ph}
                  style={{ fontFamily: "var(--font-body)", fontSize: "12px", padding: "7px 9px", borderRadius: "7px", border: "1px solid var(--color-divider)", background: fd.auto ? "var(--marker-tint)" : "var(--color-bg)", color: "var(--color-text)", outline: "none", minWidth: "0" }} />
              </label>
            ))}
            {vals.vhcFilesShow ? (
              <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
                <label className="hv4" style={{ position: "relative", display: "inline-flex", alignItems: "center", gap: "6px", alignSelf: "flex-start", fontSize: "11.5px", padding: "6px 11px", borderRadius: "7px", border: "1px dashed var(--color-accent)", color: "var(--color-accent)", cursor: "pointer" }}>
                  <i className="ph ph-paperclip" aria-hidden="true" style={{ fontSize: "13px" }}></i>
                  <span>{(vals.vhcFileList || []).length ? "Attach more" : "Attach certificates"}</span>
                  <input type="file" multiple accept=".pdf,.png,.jpg,.jpeg,.heic,.doc,.docx" onChange={vals.vhcAddFiles} style={{ position: "absolute", width: "1px", height: "1px", opacity: "0", overflow: "hidden" }} />
                </label>
                {(vals.vhcFileList || []).map((f, i) => (
                  <div key={f.name + i} style={{ display: "flex", flexDirection: "column", gap: "5px", fontSize: "11px", padding: "6px 8px", borderRadius: "7px", background: "var(--color-bg)", border: "1px solid var(--color-divider)", minWidth: "0" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: "7px", minWidth: "0" }}>
                      <i className="ph ph-file-text" aria-hidden="true" style={{ fontSize: "13px", color: "var(--color-neutral-400)", flexShrink: "0" }}></i>
                      <span style={{ flex: "1", minWidth: "0", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{f.name}</span>
                      <span style={{ color: "var(--color-neutral-500)", whiteSpace: "nowrap" }}>{f.size}</span>
                      <button type="button" className="hv11" onClick={f.remove} aria-label={"Remove " + f.name} style={{ font: "inherit", background: "none", border: "none", padding: "2px", cursor: "pointer", color: "var(--color-neutral-500)", display: "flex" }}>
                        <i className="ph ph-x" style={{ fontSize: "12px" }}></i>
                      </button>
                    </div>
                    <select value={f.target} onChange={f.setTarget} aria-label={"What " + f.name + " is"}
                      style={{ fontFamily: "var(--font-body)", fontSize: "11px", padding: "4px 6px", borderRadius: "6px", border: "1px solid var(--color-divider)", background: "var(--color-surface)", color: "var(--color-text)", minWidth: "0" }}>
                      {(f.targets || []).map((t) => <option key={t.value} value={t.value}>{t.label}</option>)}
                    </select>
                  </div>
                ))}
                <span style={{ fontSize: "10px", color: "var(--color-neutral-500)", lineHeight: "1.45" }}>
                  {"Nothing is sent yet. Say what each file is: the vendor's own accreditation, or a certificate for one of its buildings. After Hoist vendor saves the vendor and assigns its buildings, each file is read, checked and filed here — a building's against that building."}
                </span>
              </div>
            ) : null}
            {vals.vhcChipsShow ? (
              <div style={{ display: "flex", flexDirection: "column", gap: "6px" }}>
                <input className="input" autoFocus value={vals.vhcAreaQ} onChange={vals.vhcAreaSet} placeholder="Search your buildings" aria-label="Search your buildings"
                  style={{ fontFamily: "var(--font-body)", fontSize: "12px", padding: "6px 9px", borderRadius: "7px", border: "1px solid var(--color-divider)", background: "var(--color-bg)", color: "var(--color-text)", outline: "none", minWidth: "0" }} />
                <div style={{ display: "flex", flexWrap: "wrap", gap: "5px" }}>
                  {(vals.vhcChips || []).map((ch) => (
                    <button key={ch.label} type="button" aria-pressed={ch.on} onClick={ch.toggle} className={ch.on ? "" : "hv4"}
                      style={{ fontFamily: "inherit", fontSize: "10.5px", padding: "3px 9px", borderRadius: "12px", cursor: "pointer", border: "1px solid " + (ch.on ? "var(--color-accent)" : "var(--color-divider)"), background: ch.on ? "var(--color-accent)" : "transparent", color: ch.on ? "var(--accent-ink)" : "var(--color-neutral-400)" }}>
                      {ch.label}
                    </button>
                  ))}
                </div>
                {vals.vhcAreaNote || vals.vhcPicked ? (
                  <div style={{ display: "flex", gap: "8px", fontSize: "10.5px", color: "var(--color-neutral-500)" }}>
                    <span style={{ flex: "1" }}>{vals.vhcAreaNote}</span>
                    <span style={{ color: "var(--color-accent)" }}>{vals.vhcPicked}</span>
                  </div>
                ) : null}
              </div>
            ) : null}
          </div>
          {vals.vhcNote ? <div role="alert" style={{ fontSize: "10.5px", color: "var(--st-risk)", marginTop: "6px" }}>{vals.vhcNote}</div> : null}
          <div style={{ display: "flex", gap: "6px", marginTop: "8px", alignItems: "center" }}>
            {vals.vhcBackShow ? (
              <button type="button" className="hv16" onClick={vals.vhcBack} disabled={vals.vhcSaving} style={Object.assign({}, SMALL_BTN, { padding: "5px 9px", border: "none", background: "none", color: "var(--color-neutral-500)" })}>{"Back"}</button>
            ) : null}
            <span style={{ flex: "1" }}></span>
            {vals.vhcSkipShow ? (
              <button type="button" className="hv4" onClick={vals.vhcSkip} style={Object.assign({}, SMALL_BTN, { border: "1px solid var(--color-divider)", background: "transparent", color: "var(--color-neutral-400)" })}>{"Skip"}</button>
            ) : null}
            <button type="button" className="hv7" onClick={vals.vhcNext} style={Object.assign({}, SMALL_BTN, { padding: "5px 12px", border: "1px solid var(--color-accent)", background: "var(--color-accent)", color: "var(--accent-ink)" })}>{"Continue"}</button>
          </div>
        </div>
      ) : null}

      {vals.vhcConfirm ? (
        <div style={Object.assign({}, BUBBLE, { maxWidth: "100%", padding: "10px 11px", border: "1px solid var(--color-accent)", animation: "fadeUp 0.22s ease both" })}>
          <Who step={vals.vhcStep} />
          <div style={{ fontSize: "12px", lineHeight: "1.45", marginTop: "5px" }}>{"Here is " + vals.vhcName + " as it would be written to the vendor master. Hoist it?"}</div>
          <div style={{ display: "flex", flexDirection: "column", gap: "3px", marginTop: "8px" }}>
            {(vals.vhcSummary || []).map((r) => (
              <div key={r.l} style={{ display: "grid", gridTemplateColumns: "104px minmax(0,1fr)", gap: "8px", fontSize: "10.5px", lineHeight: "1.4" }}>
                <span style={{ color: "var(--color-neutral-500)" }}>{r.l}</span>
                <span style={{ color: r.empty ? "var(--color-neutral-500)" : "var(--color-text)", overflowWrap: "anywhere" }}>{r.v}</span>
              </div>
            ))}
          </div>
          <div style={{ display: "flex", alignItems: "flex-start", gap: "6px", marginTop: "9px", padding: "7px 9px", borderRadius: "7px", background: "var(--color-accent-900)", fontSize: "10.5px", lineHeight: "1.45", color: "var(--color-neutral-300)" }}>
            <i className="ph ph-info" aria-hidden="true" style={{ fontSize: "12px", color: "var(--color-accent)", marginTop: "1px", flexShrink: "0" }}></i>
            <span>{vals.vhcSaveNote}</span>
          </div>
          {vals.vhcError ? (
            <div role="alert" style={{ marginTop: "8px", padding: "7px 9px", borderRadius: "7px", background: "var(--st-risk-bg)", color: "var(--st-risk)", fontSize: "10.5px", lineHeight: "1.45" }}>
              {vals.vhcError}
            </div>
          ) : null}
          <div style={{ display: "flex", gap: "6px", marginTop: "10px", alignItems: "center" }}>
            <button type="button" className="hv16" onClick={vals.vhcBack} disabled={vals.vhcSaving} style={Object.assign({}, SMALL_BTN, { padding: "5px 9px", border: "none", background: "none", color: "var(--color-neutral-500)" })}>{"Back"}</button>
            <span style={{ flex: "1" }}></span>
            <button type="button" className="hv7" onClick={vals.vhcSaving ? undefined : vals.vhcFinish} disabled={vals.vhcSaving} aria-busy={vals.vhcSaving}
              style={Object.assign({}, SMALL_BTN, { padding: "5px 12px", border: "1px solid var(--color-accent)", background: "var(--color-accent)", color: "var(--accent-ink)", opacity: vals.vhcSaving ? "0.7" : "1", cursor: vals.vhcSaving ? "default" : "pointer" })}>{vals.vhcFinishLabel}</button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
