// HoistGraph — the Compliance Graph report (logic/hoistGraph.js). A permanent report in the
// Compliance Engine instance, in the user view (an admin or a user) and the admin view.
// The prototype's hospital "Building & Hoist Graph" page (hoistra-frontend-changes, 11 Oct 2026,
// 02-compliance-engine/template/hospital-pages.html), card for card, drawn from the live
// compliance register: a building where the prototype had a floor, a regulated area where it had
// a hospital area. No "Hoist a building".
import React from 'react';

const OUTLINE = { fontFamily: "inherit", fontSize: "12px", padding: "7px 13px", borderRadius: "7px", border: "1px solid var(--color-divider)", background: "transparent", color: "var(--color-neutral-300)", cursor: "pointer", whiteSpace: "nowrap" };

// The overview's columns: the building, one per area, then how many areas are out.
const matrixCols = (n) => "minmax(150px,1.6fr) repeat(" + n + ",minmax(58px,1fr)) 78px";

export default function HoistGraph({ vals }) {
  return (
    <div style={{ flex: "1", display: "flex", justifyContent: "center", padding: "0 32px 80px" }}>
      <div style={{ width: "100%", maxWidth: "1180px", animation: "fadeUp 0.28s ease both" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "10px", padding: "24px 0 0", fontSize: "12px", color: "var(--color-neutral-500)", minWidth: "0" }}>
          <i className="ph ph-first-aid-kit" aria-hidden="true" style={{ fontSize: "13px", color: "var(--color-accent)" }}></i>
          <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{vals.hgCrumb}</span>
          <span style={{ color: "var(--color-neutral-700)" }}>{"/"}</span>
          <span style={{ color: "var(--color-text)", whiteSpace: "nowrap" }}>{vals.hgTitle}</span>
        </div>

        <div style={{ display: "flex", alignItems: "flex-end", gap: "16px", flexWrap: "wrap", marginTop: "20px" }}>
          <div style={{ flex: "1 1 420px", minWidth: "0" }}>
            <h2 style={{ fontSize: "24px", margin: "0", lineHeight: "1.15" }}>{vals.hgTitle}</h2>
            <p style={{ fontSize: "13px", color: "var(--color-neutral-400)", margin: "7px 0 0", maxWidth: "80ch", lineHeight: "1.55" }}>{vals.hgBlurb}</p>
          </div>
          <div style={{ display: "flex", gap: "8px", flex: "0 1 auto", minWidth: "0", flexWrap: "wrap" }}>
            {(vals.hgActions || []).map((a) => (
              <button key={a.label} type="button" className="hv4" onClick={a.run} style={OUTLINE}>{a.label}</button>
            ))}
          </div>
        </div>

        <div className="hg-stats" style={{ display: "grid", gridTemplateColumns: "repeat(5,minmax(0,1fr))", gap: "0", marginTop: "20px", borderTop: "1px solid var(--color-divider)", borderBottom: "1px solid var(--color-divider)" }}>
          {(vals.hgStats || []).map((g) => (
            <div key={g.l} style={{ padding: "12px 16px", borderLeft: "1px solid var(--color-divider)" }}>
              <div style={{ fontSize: "9.5px", letterSpacing: "0.12em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>{g.l}</div>
              <div style={{ fontSize: "22px", lineHeight: "1", marginTop: "7px", fontFamily: "ui-monospace,monospace", color: g.v === "—" ? "var(--color-neutral-500)" : "var(--color-text)" }}>{g.v}</div>
            </div>
          ))}
        </div>

        {vals.hgStatus ? (
          <div style={{ display: "flex", alignItems: "center", gap: "12px", flexWrap: "wrap", marginTop: "18px", fontSize: "12.5px", color: "var(--color-neutral-400)" }}>
            <span>{vals.hgStatus}</span>
            {vals.hgRetryShow ? (
              <button type="button" className="hv4" onClick={vals.hgRetry} style={Object.assign({}, OUTLINE, { fontSize: "11.5px", padding: "4px 11px" })}>{"Try again"}</button>
            ) : null}
          </div>
        ) : null}

        {vals.hgNone ? (
          <div style={{ marginTop: "18px", fontSize: "12.5px", color: "var(--color-neutral-400)", lineHeight: "1.55" }}>{vals.hgNone}</div>
        ) : null}

        {/* The whole estate in one table: a row per building, a column per area, each cell the
            certificates held of those required. A row or a cell opens that building below. */}
        {vals.hgMatrix ? (
          <div style={{ marginTop: "20px", borderRadius: "10px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", overflow: "hidden" }}>
            <div style={{ overflowX: "auto" }}>
              <div style={{ minWidth: (228 + vals.hgMatrix.cols.length * 62) + "px" }}>
                <div style={{ display: "grid", gridTemplateColumns: matrixCols(vals.hgMatrix.cols.length), alignItems: "end", borderBottom: "1px solid var(--color-divider)", padding: "9px 0 8px" }}>
                  <span style={{ fontSize: "10px", color: "var(--color-neutral-500)", padding: "0 14px" }}>{"Building"}</span>
                  {vals.hgMatrix.cols.map((col) => (
                    <span key={col} title={col} style={{ fontSize: "10px", lineHeight: "1.25", color: "var(--color-neutral-500)", textAlign: "center", padding: "0 3px", overflowWrap: "anywhere" }}>{col}</span>
                  ))}
                  <span style={{ fontSize: "10px", color: "var(--color-neutral-500)", textAlign: "right", padding: "0 14px" }}>{"Areas out"}</span>
                </div>
                <div style={{ maxHeight: "290px", overflowY: "auto" }}>
                  {vals.hgMatrix.rows.map((r) => (
                    <div key={r.label} className="hv2" role="button" tabIndex={0} aria-pressed={r.on} onClick={r.pick}
                      onKeyDown={(e) => { if (e.target === e.currentTarget && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); r.pick(); } }}
                      style={{ position: "relative", display: "grid", gridTemplateColumns: matrixCols(vals.hgMatrix.cols.length), alignItems: "center", padding: "6px 0", borderBottom: "1px solid var(--color-divider)", cursor: "pointer", background: r.on ? "var(--color-accent-900)" : "transparent" }}>
                      <span aria-hidden="true" style={{ position: "absolute", left: "0", top: "0", bottom: "0", width: "3px", background: r.on ? "var(--color-accent)" : "transparent" }}></span>
                      <span style={{ fontSize: "12px", padding: "0 14px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: r.on ? "var(--color-text)" : "var(--color-neutral-300)" }}>{r.label}</span>
                      {r.cells.map((x, k) => (
                        <span key={k} style={{ display: "flex", justifyContent: "center" }}>
                          {x.pick ? (
                            <button type="button" title={x.title} onClick={(e) => { e.stopPropagation(); x.pick(); }}
                              style={{ fontFamily: "ui-monospace,monospace", fontSize: "10.5px", lineHeight: "1.4", padding: "1px 7px", borderRadius: "5px", border: "none", cursor: "pointer", color: x.color, background: x.bg }}>{x.v}</button>
                          ) : (
                            <span title={x.title} style={{ fontSize: "10.5px", color: x.color }}>{x.v}</span>
                          )}
                        </span>
                      ))}
                      <span style={{ fontSize: "10.5px", color: r.outColor, textAlign: "right", padding: "0 14px", whiteSpace: "nowrap" }}>{r.out}</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        ) : null}

        {/* The building picked above, as the prototype's area cards. */}
        {vals.hgPicked ? (
          <div style={{ marginTop: "18px" }}>
            <div style={{ display: "flex", alignItems: "baseline", gap: "10px", flexWrap: "wrap" }}>
              <span style={{ fontSize: "15px" }}>{vals.hgPicked.label}</span>
              <span style={{ fontSize: "11px", color: "var(--color-neutral-500)" }}>{vals.hgPicked.line}</span>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(min(100%,260px),1fr))", gap: "10px", marginTop: "10px" }}>
              {vals.hgPicked.cards.map((sec) => (
                <div key={sec.name} style={{ padding: "11px 13px", borderRadius: "10px", background: "var(--color-surface)", boxShadow: sec.focus ? "0 0 0 2px var(--color-accent)" : "var(--shadow-sm)", borderLeft: "3px solid " + sec.color, minWidth: "0" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
                    <span style={{ fontSize: "13px", flex: "1", minWidth: "0", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{sec.name}</span>
                    <span style={{ flex: "none", fontSize: "10.5px", padding: "2px 8px", borderRadius: "5px", whiteSpace: "nowrap", color: sec.color, background: sec.bg }}>{sec.label}</span>
                  </div>
                  <div style={{ fontSize: "10.5px", color: "var(--color-neutral-500)", marginTop: "3px" }}>{(sec.use ? sec.use + " · " : "") + sec.sub}</div>
                  {sec.rows.length ? (
                    <div style={{ display: "flex", flexDirection: "column", gap: "4px", marginTop: "8px", paddingTop: "7px", borderTop: "1px dashed var(--color-divider)" }}>
                      {sec.rows.map((r, i) => (
                        <div key={r.t + i} title={r.title} style={{ display: "flex", alignItems: "center", gap: "7px", fontSize: "11px", lineHeight: "1.4", minWidth: "0" }}>
                          <span aria-hidden="true" style={{ width: "6px", height: "6px", borderRadius: "50%", background: r.color, flexShrink: "0" }}></span>
                          <span style={{ flex: "1", minWidth: "0", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.t}</span>
                          <span style={{ color: "var(--color-neutral-500)", whiteSpace: "nowrap", maxWidth: "42%", overflow: "hidden", textOverflow: "ellipsis" }}>{r.v}</span>
                        </div>
                      ))}
                    </div>
                  ) : null}
                </div>
              ))}
            </div>
          </div>
        ) : null}

        {vals.hgOrphans ? (
          <div style={{ fontSize: "11px", color: "var(--color-neutral-500)", marginTop: "14px", lineHeight: "1.55" }}>{vals.hgOrphans}</div>
        ) : null}
        <div style={{ fontSize: "11px", color: "var(--color-neutral-600)", marginTop: "14px", lineHeight: "1.55", maxWidth: "110ch" }}>{vals.hgFoot}</div>
      </div>
    </div>
  );
}
