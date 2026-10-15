// IngestionAgentModal — the ingestion validation agent. Runs for every
// ingestion, users and admins alike, any document type, new buildings
// included. `vals` is the view model from useHoistra(). The document is a
// real upload (the hidden file input below) or one of the sample docs; the
// conversation and the audit records behind it are live (logic/ingestionLive.js).
import React from 'react';

export default function IngestionAgentModal({ vals }) {
  const fileRef = React.useRef(null);
  return (
    <div style={{ position: "fixed", inset: "0", background: "var(--scrim)", zIndex: "95", display: "flex", justifyContent: "center", alignItems: "flex-start", padding: "6vh 20px", overflowY: "auto" }}>
      <div style={{ width: "700px", maxWidth: "100%", background: "var(--color-surface)", borderRadius: "13px", boxShadow: "var(--shadow-lg)", overflow: "hidden", animation: "fadeUp 0.2s ease both" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "11px", padding: "15px 18px", borderBottom: "1px solid var(--color-divider)" }}>
          <i className="ph ph-sparkle" style={{ fontSize: "16px", color: "var(--color-accent)" }}></i>
          <div style={{ flex: "1", minWidth: "0" }}>
            <div style={{ fontSize: "14px" }}>{"Ingestion validation"}</div>
            <div style={{ fontFamily: "ui-monospace,monospace", fontSize: "10px", color: "var(--color-neutral-500)", marginTop: "2px" }}>{vals.ingScope}</div>
          </div>
          <i className="ph ph-x hv6" onClick={vals.ingClose} style={{ fontSize: "15px", color: "var(--color-neutral-500)", cursor: "pointer" }}></i>
        </div>
        {vals.ingSetup ? (
          <div style={{ padding: "17px 18px", display: "flex", flexDirection: "column", gap: "14px" }}>
            <div style={{ fontSize: "10.5px", letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>{"Selected building"}</div>
            <div style={{ display: "flex", flexWrap: "wrap", gap: "7px" }}>
              {(vals.ingBuildings || []).map((b, $index) => (
                <React.Fragment key={$index}>
                  <div onClick={b.pick} style={{ fontSize: "11.5px", padding: "5px 11px", borderRadius: "6px", border: `1px solid ${b.edge}`, background: b.bg, color: b.fg, cursor: "pointer" }}>{b.name}</div>
                </React.Fragment>
              ))}
            </div>
            <div style={{ fontSize: "10.5px", letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--color-neutral-500)", marginTop: "2px" }}>{"Document to ingest"}</div>
            <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
              {(vals.ingDocs || []).map((d, $index) => (
                <React.Fragment key={$index}>
                  <div className="hv19" onClick={d.pick} style={{ display: "flex", alignItems: "center", gap: "10px", padding: "9px 11px", borderRadius: "8px", background: d.bg, cursor: "pointer" }}>
                    <i className={`ph ${d.tick}`} style={{ fontSize: "15px", color: d.fg }}></i>
                    <div style={{ flex: "1", minWidth: "0" }}>
                      <div style={{ fontSize: "12.5px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{d.file}</div>
                      <div style={{ fontSize: "10.5px", color: "var(--color-neutral-500)", marginTop: "2px" }}>{d.kind}</div>
                    </div>
                  </div>
                </React.Fragment>
              ))}
              <input ref={fileRef} type="file" onChange={vals.ingFilePick} style={{ display: "none" }} />
              <div className="hv19" onClick={() => fileRef.current && fileRef.current.click()} style={{ display: "flex", alignItems: "center", gap: "10px", padding: "9px 11px", borderRadius: "8px", background: vals.ingFileBg, border: "1px dashed var(--color-divider)", cursor: "pointer" }}>
                <i className={`ph ${vals.ingFileTick}`} style={{ fontSize: "15px", color: vals.ingFileFg }}></i>
                <div style={{ flex: "1", minWidth: "0" }}>
                  <div style={{ fontSize: "12.5px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{vals.ingFileLabel}</div>
                  <div style={{ fontSize: "10.5px", color: "var(--color-neutral-500)", marginTop: "2px" }}>{vals.ingFileKind}</div>
                </div>
              </div>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: "12px", marginTop: "2px" }}>
              <div className="btn btn-primary" onClick={vals.ingGo} style={{ fontSize: "12px", padding: "8px 16px", cursor: "pointer" }}>{"Run validation"}</div>
              <span style={{ fontSize: "11px", color: "var(--color-neutral-500)" }}>{"Nothing is ingested until validation completes — the agent checks the document against the building and its ontology first."}</span>
            </div>
          </div>
        ) : null}
        {vals.ingChecksOn ? (
          <>
            <div style={{ display: "flex", flexWrap: "wrap", gap: "5px 16px", padding: "12px 18px", borderBottom: "1px solid var(--color-divider)", background: "var(--color-bg)" }}>
              {(vals.ingChecks || []).map((c, $index) => (
                <React.Fragment key={$index}>
                  <div style={{ display: "flex", alignItems: "center", gap: "6px", fontSize: "10.5px", color: "var(--color-neutral-500)" }}>
                    <i className={`ph ${c.icon}`} style={{ fontSize: "12px", color: c.fg, animation: c.spin }}></i>
                    <span>{c.label}</span>
                  </div>
                </React.Fragment>
              ))}
            </div>
            <div style={{ padding: "16px 18px", display: "flex", flexDirection: "column", gap: "10px", maxHeight: "52vh", overflowY: "auto" }}>
              {(vals.ingMsgs || []).map((m, $index) => (
                <React.Fragment key={$index}>
                  <div style={{ display: "flex", gap: "9px", alignSelf: m.align, maxWidth: "86%" }}>
                    <div style={{ display: m.icon, width: "24px", height: "24px", borderRadius: "7px", border: "1px solid var(--color-divider)", alignItems: "center", justifyContent: "center", flexShrink: "0", marginTop: "1px" }}>
                      <i className="ph ph-sparkle" style={{ fontSize: "12px", color: "var(--color-accent)" }}></i>
                    </div>
                    <div style={{ fontSize: "12.5px", lineHeight: "1.55", padding: "9px 13px", borderRadius: "10px", background: m.bg, color: "var(--color-neutral-200)" }}>{m.text}</div>
                  </div>
                </React.Fragment>
              ))}
              {vals.ingAskReason ? (
                <textarea value={vals.ingReason} onChange={vals.ingSetReason} placeholder="Why does this document belong to the selected building?" rows="3" style={{ width: "100%", boxSizing: "border-box", fontSize: "12.5px", padding: "10px 12px", borderRadius: "9px", border: "1px solid var(--color-divider)", background: "var(--color-bg)", color: "var(--color-text)", fontFamily: "var(--font-body)", outline: "none", resize: "vertical" }}></textarea>
              ) : null}
              <div style={{ display: "flex", gap: "8px", flexWrap: "wrap", marginTop: "4px" }}>
                {(vals.ingActions || []).map((a, $index) => (
                  <React.Fragment key={$index}>
                    <div className="hv3" onClick={a.click} style={{ fontSize: "12px", padding: "8px 15px", borderRadius: "8px", background: a.bg, color: a.fg, border: `1px solid ${a.edge}`, cursor: "pointer" }}>{a.label}</div>
                  </React.Fragment>
                ))}
              </div>
            </div>
          </>
        ) : null}
      </div>
    </div>
  );
}
