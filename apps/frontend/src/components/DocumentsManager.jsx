// DocumentsManager — Manage documents for one building (logic/documentsRegister.js).
// Every document on the building and how it got there; for an admin: its type, personal or
// not, links to other buildings, Index now; and an upload with the type chosen. Identity papers
// are personal: listed for admins only, never indexed for search.
import React from 'react';

const CELL = { padding: "9px 10px", borderBottom: "1px solid var(--color-divider)", fontSize: "12px", verticalAlign: "top" };
const SELECT = { fontSize: "11.5px", padding: "4px 6px", borderRadius: "6px", border: "1px solid var(--color-divider)",
  background: "var(--color-surface)", color: "var(--color-text)", maxWidth: "190px" };
const QUIET = { fontSize: "11.5px", padding: "4px 9px", borderRadius: "6px", border: "1px solid var(--color-divider)",
  background: "transparent", color: "var(--color-neutral-400)", cursor: "pointer", whiteSpace: "nowrap" };
const CHIP = (bg, fg) => ({ fontSize: "10px", padding: "1px 7px", borderRadius: "999px", background: bg, color: fg, whiteSpace: "nowrap" });

export default function DocumentsManager({ vals }) {
  if (!vals.dmShow) return null;
  const types = vals.dmTypes || [];
  const manage = vals.dmCanManage;
  return (
    <div style={{ position: "fixed", inset: "0", background: "rgba(26,26,24,0.38)", zIndex: "60", display: "flex",
      justifyContent: "center", alignItems: "flex-start", paddingTop: "56px", overflowY: "auto" }}>
      <div role="dialog" aria-modal="true" aria-label={"Documents — " + vals.dmBuildingName}
        style={{ width: "1080px", maxWidth: "calc(100% - 32px)", borderRadius: "12px", background: "var(--color-surface)",
          boxShadow: "var(--shadow-md)", padding: "20px 22px", boxSizing: "border-box", marginBottom: "40px" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: "12px" }}>
          <div>
            <div style={{ fontSize: "10.5px", letterSpacing: "0.12em", textTransform: "uppercase", color: "var(--color-accent)" }}>{"Documents"}</div>
            <h3 style={{ margin: "4px 0 0", fontSize: "19px" }}>{vals.dmBuildingName}</h3>
          </div>
          <button type="button" onClick={vals.dmClose} style={QUIET}>{"Close"}</button>
        </div>
        <p style={{ fontSize: "12px", color: "var(--color-neutral-500)", margin: "8px 0 0", lineHeight: "1.5" }}>
          {"Every document on this building — filed here, linked here, or cited by a contract, certificate or job on it. "
            + "Identity papers (visa, Emirates ID, passport, labour card) are personal: admins only, never added to search."}
          {vals.dmHidden ? " " + vals.dmHidden + " personal document(s) are hidden from your role." : ""}
        </p>

        {manage ? (
          <div style={{ display: "flex", flexWrap: "wrap", gap: "8px", alignItems: "center", marginTop: "14px", padding: "10px 12px",
            borderRadius: "9px", background: "var(--color-bg)" }}>
            <span style={{ fontSize: "12px", color: "var(--color-neutral-400)" }}>{"Upload to this building:"}</span>
            <input type="file" aria-label="Document to upload" onChange={vals.dmPickFile} style={{ fontSize: "11.5px", maxWidth: "260px" }} />
            <select aria-label="Document type" value={vals.dmUploadType} onChange={vals.dmSetUploadType} style={SELECT}>
              {types.map((t) => <option key={t.key} value={t.key}>{t.group + " · " + t.label}</option>)}
            </select>
            <button type="button" className="btn btn-primary" onClick={vals.dmUpload} disabled={vals.dmUploading}
              style={{ fontSize: "12px", padding: "6px 13px", cursor: "pointer", opacity: vals.dmUploading ? "0.6" : "1" }}>
              {vals.dmUploading ? "Filing…" : "Upload"}
            </button>
            {vals.dmUploadPersonal ? (
              <span style={{ fontSize: "11px", color: "var(--st-warn)" }}>{"Personal: filed here, not added to search."}</span>
            ) : null}
          </div>
        ) : null}

        {vals.dmMsg ? <div style={{ marginTop: "10px", fontSize: "12px", color: "var(--color-neutral-300)" }}>{vals.dmMsg}</div> : null}
        {vals.dmErr ? <div style={{ marginTop: "10px", fontSize: "12px", color: "var(--st-risk)" }}>{vals.dmErr}</div> : null}
        {vals.dmLoading ? <div style={{ marginTop: "14px", fontSize: "12px", color: "var(--color-neutral-500)" }}>{"Reading the documents…"}</div> : null}

        {!vals.dmLoading && (vals.dmRows || []).length === 0 && !vals.dmErr ? (
          <div style={{ marginTop: "14px", fontSize: "12.5px", color: "var(--color-neutral-500)" }}>{"No documents on this building yet."}</div>
        ) : null}

        {(vals.dmRows || []).length ? (
          <div style={{ marginTop: "12px", overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead>
                <tr style={{ textAlign: "left", fontSize: "10.5px", letterSpacing: "0.08em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                  <th style={CELL}>{"Document"}</th><th style={CELL}>{"Type"}</th><th style={CELL}>{"Search"}</th>
                  <th style={CELL}>{"On this building"}</th>{manage ? <th style={CELL}>{"Link to another building"}</th> : null}
                </tr>
              </thead>
              <tbody>
                {vals.dmRows.map((d) => (
                  <tr key={d.id} style={{ opacity: d.busy ? "0.55" : "1" }}>
                    <td style={{ ...CELL, maxWidth: "300px" }}>
                      <div style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={d.title}>{d.title}</div>
                      <div style={{ display: "flex", gap: "6px", marginTop: "4px", alignItems: "center" }}>
                        {d.personal ? <span style={CHIP("var(--st-warn-bg)", "var(--st-warn)")}>{"personal"}</span> : null}
                        {d.added ? <span style={{ fontSize: "10.5px", color: "var(--color-neutral-500)" }}>{d.added}</span> : null}
                      </div>
                    </td>
                    <td style={CELL}>
                      {manage ? (
                        <div style={{ display: "flex", flexDirection: "column", gap: "5px" }}>
                          <select aria-label={"Type of " + d.title} value={d.type} onChange={d.setType} style={SELECT}>
                            {!d.type ? <option value="">{"Unclassified"}</option> : null}
                            {types.map((t) => <option key={t.key} value={t.key}>{t.label}</option>)}
                          </select>
                          <label style={{ fontSize: "11px", color: "var(--color-neutral-400)", display: "inline-flex", gap: "5px", alignItems: "center", cursor: "pointer" }}>
                            <input type="checkbox" checked={d.personal} onChange={d.togglePersonal} />{"Personal"}
                          </label>
                        </div>
                      ) : d.typeLabel}
                    </td>
                    <td style={CELL}>
                      {d.personal ? <span style={{ fontSize: "11px", color: "var(--color-neutral-500)" }}>{"never indexed"}</span>
                        : d.indexed ? <span style={CHIP("var(--st-ok-bg, transparent)", "var(--st-ok)")}>{"searchable"}</span>
                        : manage ? <button type="button" onClick={d.index} style={QUIET}>{"Index now"}</button>
                        : <span style={{ fontSize: "11px", color: "var(--color-neutral-500)" }}>{"not indexed"}</span>}
                    </td>
                    <td style={CELL}>
                      <div style={{ fontSize: "11.5px", color: "var(--color-neutral-400)" }}>{d.how}</div>
                      {manage && d.linkedHere ? <button type="button" onClick={d.unlink} style={{ ...QUIET, marginTop: "5px" }}>{"Unlink"}</button> : null}
                    </td>
                    {manage ? (
                      <td style={CELL}>
                        <div style={{ display: "flex", gap: "6px", alignItems: "center" }}>
                          <select aria-label={"Link " + d.title + " to"} value={d.linkTo} onChange={d.setLinkTo} style={SELECT}>
                            <option value="">{"Choose a building…"}</option>
                            {(vals.dmOthers || []).map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}
                          </select>
                          <button type="button" onClick={d.link} style={QUIET}>{"Link"}</button>
                        </div>
                      </td>
                    ) : null}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </div>
    </div>
  );
}
