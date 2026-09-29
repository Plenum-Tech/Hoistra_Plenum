// VendorAuditTrail — the Audit trail page's "Vendor audit trail" tab (admin). `vals` is the
// view model; logic/vendorAudit.js narrows the rows, and the tiles, chip counts and day
// groups all describe the same filtered set shown beneath them. Laid out like the ingestion
// tab beside it, so the two read as one page.
import React from 'react';

const TILE = { padding: "8px 13px", borderRadius: "9px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", display: "flex", flexDirection: "column", gap: "2px", whiteSpace: "nowrap", minWidth: "84px" };
const ROW_GRID = "62px minmax(150px,0.8fr) minmax(240px,1.7fr) minmax(150px,1fr) 104px 18px";

export default function VendorAuditTrail({ vals }) {
  return (
    <React.Fragment>
      <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", flexWrap: "wrap", gap: "16px 24px", marginTop: "18px" }}>
        <div style={{ minWidth: "0", flex: "1 1 340px" }}>
          <div style={{ fontSize: "10.5px", letterSpacing: "0.13em", textTransform: "uppercase", color: "var(--color-accent)" }}>{"Administration · vendors"}</div>
          <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap", marginTop: "7px" }}>
            <h2 style={{ fontSize: "28px", margin: "0", lineHeight: "1.15" }}>{"Vendor audit trail"}</h2>
            <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10px", letterSpacing: "0.09em", textTransform: "uppercase", padding: "3px 8px", borderRadius: "5px", background: "var(--color-accent)", color: "var(--accent-ink)" }}>{"Admin only"}</span>
          </div>
          <p style={{ fontSize: "13px", color: "var(--color-neutral-400)", margin: "8px 0 0", maxWidth: "88ch", lineHeight: "1.55" }}>
            {"Every time an asset moved to another vendor, and every time compliance blocked or cleared a vendor — who or what made the change, when, and why."}
          </p>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap" }}>
          <span style={{ display: "inline-flex", alignItems: "center", gap: "7px", padding: "3px 10px", borderRadius: "20px", border: "1px solid var(--color-divider)", fontSize: "10.5px", color: "var(--color-neutral-500)" }}>
            <span style={{ width: "6px", height: "6px", borderRadius: "50%", background: vals.vaSourceDot }}></span>
            <span>{vals.vaSourceLabel}</span>
            {vals.vaError ? <span className="hv11" onClick={vals.vaRetry} style={{ color: "var(--color-accent)", cursor: "pointer" }}>{"Retry"}</span> : null}
          </span>
          {[["In view", vals.vaInView], ["Vendors affected", vals.vaVendorsAffected], ["Blocks", vals.vaBlocks]].map((t, $i) => (
            <div key={$i} style={TILE}>
              <span style={{ fontSize: "9.5px", letterSpacing: "0.11em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>{t[0]}</span>
              <span style={{ fontSize: "12.5px", fontVariantNumeric: "tabular-nums" }}>{t[1]}</span>
            </div>
          ))}
        </div>
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap", marginTop: "20px" }}>
        <div style={{ position: "relative", flex: "1 1 320px", minWidth: "220px" }}>
          <i className="ph ph-magnifying-glass" style={{ position: "absolute", left: "11px", top: "50%", transform: "translateY(-50%)", fontSize: "13px", color: "var(--color-neutral-500)" }}></i>
          <input
            value={vals.vaQuery}
            onChange={(e) => vals.vaSetQuery(e.target.value)}
            placeholder="Search vendor, asset or person"
            aria-label="Search the vendor audit trail"
            style={{ width: "100%", boxSizing: "border-box", fontSize: "12px", padding: "8px 12px 8px 31px", borderRadius: "8px", border: "1px solid var(--color-divider)", background: "var(--color-surface)", color: "inherit", outline: "none" }}
          />
        </div>
        <div style={{ display: "flex", gap: "2px", padding: "2px", borderRadius: "8px", background: "var(--color-bg)", border: "1px solid var(--color-divider)" }}>
          {(vals.vaRangePicks || []).map((r, $i) => (
            <div key={$i} role="button" onClick={r.pick} style={{ fontSize: "11.5px", padding: "5px 11px", borderRadius: "6px", cursor: "pointer", whiteSpace: "nowrap", background: r.on ? "var(--color-surface)" : "transparent", color: r.on ? "var(--color-accent)" : "var(--color-neutral-400)", boxShadow: r.on ? "var(--shadow-sm)" : "none" }}>{r.label}</div>
          ))}
        </div>
      </div>

      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "12px", flexWrap: "wrap", marginTop: "14px" }}>
        <div style={{ display: "flex", gap: "7px", flexWrap: "wrap" }}>
          {(vals.vaFilters || []).map((f, $i) => (
            <div key={$i} role="button" onClick={f.pick} style={{ display: "inline-flex", alignItems: "center", gap: "6px", fontSize: "11.5px", padding: "5px 12px", borderRadius: "6px", background: f.bg, color: f.fg, cursor: "pointer", boxShadow: "var(--shadow-sm)" }}>
              <span>{f.label}</span>
              <span style={{ fontVariantNumeric: "tabular-nums", opacity: "0.72" }}>{f.count}</span>
            </div>
          ))}
        </div>
        <span style={{ fontSize: "11px", color: "var(--color-neutral-500)", fontVariantNumeric: "tabular-nums" }}>{vals.vaShownLine}</span>
      </div>

      {vals.vaUnreadableNote ? (
        <div style={{ marginTop: "12px", fontSize: "11.5px", color: "var(--st-warn)" }}>{vals.vaUnreadableNote}</div>
      ) : null}
      {vals.vaError ? (
        <div style={{ marginTop: "16px", padding: "12px 14px", borderRadius: "10px", background: "var(--st-risk-bg)", color: "var(--st-risk)", fontSize: "12.5px", display: "flex", alignItems: "center", justifyContent: "space-between", gap: "12px" }}>
          <span>{vals.vaError}</span>
          <span className="hv11" role="button" onClick={vals.vaRetry} style={{ cursor: "pointer", textDecoration: "underline" }}>{"Try again"}</span>
        </div>
      ) : null}
      {vals.vaLoading ? (
        <div style={{ marginTop: "18px", fontSize: "12.5px", color: "var(--color-neutral-500)" }}>{"Reading the vendor trail…"}</div>
      ) : null}

      {(vals.vaGroups || []).map((g, $g) => (
        <div key={$g} style={{ marginTop: "18px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "9px", padding: "0 2px 8px" }}>
            <i className="ph ph-caret-down" style={{ fontSize: "11px", color: "var(--color-neutral-500)" }}></i>
            <span style={{ fontSize: "12.5px" }}>{g.label}</span>
            <span style={{ fontSize: "11px", color: "var(--color-neutral-500)", fontVariantNumeric: "tabular-nums" }}>{g.count}</span>
          </div>
          <div style={{ borderRadius: "11px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", overflow: "hidden" }}>
            {(g.rows || []).map((r) => (
              <div key={r.id} style={{ borderBottom: "1px solid var(--color-divider)" }}>
                <div className="hv2" role="button" aria-expanded={r.open} onClick={r.toggle} style={{ display: "grid", gridTemplateColumns: ROW_GRID, gap: "12px", alignItems: "center", padding: "11px 16px", cursor: "pointer" }}>
                  <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10.5px", color: "var(--color-neutral-500)", whiteSpace: "nowrap" }}>{r.clock}</span>
                  <div style={{ minWidth: "0" }}>
                    <div style={{ fontSize: "12.5px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.who}</div>
                    <div style={{ fontSize: "10.5px", color: "var(--color-neutral-500)", marginTop: "2px" }}>{r.role}</div>
                  </div>
                  <div style={{ minWidth: "0" }}>
                    <div style={{ fontSize: "12.5px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.vendor}</div>
                    <div style={{ fontSize: "10.5px", color: "var(--color-neutral-500)", marginTop: "2px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.change}</div>
                  </div>
                  <div style={{ fontSize: "11.5px", color: "var(--color-neutral-400)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{r.building}</div>
                  <span style={{ fontSize: "10.5px", padding: "3px 9px", borderRadius: "5px", background: r.bg, color: r.fg, textAlign: "center", whiteSpace: "nowrap" }}>{r.badge}</span>
                  <i className={r.open ? "ph ph-caret-down" : "ph ph-caret-right"} style={{ fontSize: "12px", color: "var(--color-neutral-500)", justifySelf: "end" }}></i>
                </div>
                <div style={{ display: r.panelShow, gridTemplateColumns: "repeat(auto-fit,minmax(240px,1fr))", gap: "12px 24px", padding: "14px 16px 16px 50px", background: "var(--color-bg)", borderTop: "1px solid var(--color-divider)" }}>
                  {(r.fields || []).map((f, $f) => (
                    <div key={$f} style={{ minWidth: "0" }}>
                      <div style={{ fontSize: "9.5px", letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>{f.l}</div>
                      <div style={{ fontSize: "12px", color: "var(--color-neutral-300)", marginTop: "3px", lineHeight: "1.5", overflowWrap: "anywhere" }}>{f.v}</div>
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      ))}

      {vals.vaEmpty ? (
        <div style={{ marginTop: "18px", padding: "34px 16px", borderRadius: "11px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", textAlign: "center", fontSize: "12.5px", color: "var(--color-neutral-500)", lineHeight: "1.6" }}>
          <i className="ph ph-handshake" style={{ display: "block", fontSize: "20px", marginBottom: "8px", color: "var(--color-neutral-500)" }}></i>
          <span style={{ display: "inline-block", maxWidth: "62ch" }}>{vals.vaEmpty}</span>
        </div>
      ) : null}
    </React.Fragment>
  );
}
