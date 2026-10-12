// HoistVendors — vendors named on certificates but not in the vendor master, and the master itself
// (logic/vendorHoist.js). Opened from the Vendors page by an admin in User view.
// Ported from the Hoistra prototype's hoist-vendors-page (hoistra-frontend-changes, 11 Oct 2026),
// set in the Vendors page's own frame so the two read as one place.
import React from 'react';

const LABEL = { fontSize: "11px", letterSpacing: "0.11em", textTransform: "uppercase", color: "var(--color-neutral-500)" };

export default function HoistVendors({ vals }) {
  return (
      <div style={{ flex: "1", display: "flex", justifyContent: "flex-start", padding: "0 40px 80px" }}>
        <div style={{ width: "100%", maxWidth: "1400px", animation: "fadeUp 0.28s ease both" }}>
          <div style={{ display: "flex", alignItems: "center", flexWrap: "wrap", gap: "10px", padding: "24px 0 0", fontSize: "12px", color: "var(--color-neutral-500)" }}>
            <span title="Only an admin can hoist a vendor" style={{ fontSize: "10px", padding: "2px 7px", borderRadius: "5px", background: "var(--color-accent)", color: "var(--accent-ink)" }}>
              {"Admin"}
            </span>
            <button type="button" className="hv6" onClick={vals.vhBack} style={{ font: "inherit", background: "none", border: "none", padding: "0", display: "inline-flex", alignItems: "center", gap: "6px", color: "var(--color-neutral-400)", cursor: "pointer" }}>
              <i className="ph ph-arrow-left" style={{ fontSize: "12px" }}></i>{"Vendors"}
            </button>
            <span style={{ color: "var(--color-neutral-700)" }}>{"/"}</span>
            <span style={{ color: "var(--color-text)" }}>{"Hoist vendors"}</span>
          </div>

          <div style={{ display: "flex", alignItems: "flex-end", gap: "16px 24px", flexWrap: "wrap", marginTop: "16px" }}>
            <div style={{ flex: "1 1 420px", minWidth: "0" }}>
              <h2 style={{ fontSize: "28px", margin: "0", lineHeight: "1.15" }}>
                {"Hoist vendors"}
              </h2>
              <p style={{ fontSize: "13px", color: "var(--color-neutral-400)", margin: "8px 0 0", maxWidth: "84ch", lineHeight: "1.55" }}>
                {"Vendors named on your certificates that are not yet in the vendor master. Hoisting a vendor confirms its record, so it can be allocated work, scored against its contract and checked for compliance. Scope · " + (vals.vhScope || "")}
              </p>
              <p style={{ display: "flex", alignItems: "flex-start", gap: "7px", fontSize: "11.5px", color: "var(--color-neutral-500)", margin: "8px 0 0", maxWidth: "84ch", lineHeight: "1.5" }}>
                <i className="ph ph-info" aria-hidden="true" style={{ fontSize: "13px", color: "var(--color-accent)", marginTop: "1px", flexShrink: "0" }}></i>
                <span>{vals.vhSaveLine}</span>
              </p>
            </div>
            <button type="button" className="btn btn-primary" onClick={vals.vhNewForm} style={{ fontSize: "12px", padding: "8px 14px", cursor: "pointer", whiteSpace: "nowrap", display: "inline-flex", alignItems: "center", gap: "7px", fontFamily: "inherit" }}>
              <i className="ph ph-plus" style={{ fontSize: "12px" }}></i><span>{"Hoist a new vendor"}</span>
            </button>
          </div>

          {/* ── Detected ─────────────────────────────────────────────────────── */}
          <div style={{ display: "flex", alignItems: "baseline", flexWrap: "wrap", gap: "6px 10px", marginTop: "28px" }}>
            <span style={LABEL}>{"Detected vendors · " + vals.vhDetN}</span>
            <span style={{ fontSize: "11px", color: "var(--color-neutral-500)" }}>{"Named on certificates, not in the vendor master"}</span>
            {vals.vhHiddenN ? (
              <button type="button" className="hv6" onClick={vals.vhShowHidden} style={{ marginLeft: "auto", font: "inherit", fontSize: "11px", background: "none", border: "none", padding: "0", color: "var(--color-neutral-400)", cursor: "pointer" }}>
                {"Show hidden · " + vals.vhHiddenN}
              </button>
            ) : null}
          </div>
          {(vals.vhDet || []).length ? (
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(min(100%,340px),1fr))", gap: "11px", marginTop: "12px" }}>
              {vals.vhDet.map((x) => (
                <div key={x.key} style={{ display: "flex", flexDirection: "column", padding: "14px 15px", borderRadius: "10px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", borderLeft: "3px solid var(--marker)", minWidth: "0" }}>
                  <div style={{ display: "flex", alignItems: "baseline", gap: "8px" }}>
                    <span style={{ fontSize: "13.5px", flex: "1", minWidth: "0", overflowWrap: "anywhere" }}>{x.name}</span>
                    <span style={{ fontSize: "10.5px", color: "var(--color-neutral-400)", whiteSpace: "nowrap", fontVariantNumeric: "tabular-nums" }}>{x.count}</span>
                  </div>
                  <div style={{ fontSize: "11px", color: "var(--color-neutral-500)", marginTop: "3px", lineHeight: "1.45" }}>{x.meta}</div>
                  <div style={{ display: "flex", flexDirection: "column", gap: "3px", marginTop: "9px" }}>
                    {x.sources.map((src, i) => (
                      <div key={i} style={{ display: "flex", alignItems: "flex-start", gap: "7px", fontSize: "11.5px", lineHeight: "1.45" }}>
                        <i className="ph ph-file-text" aria-hidden="true" style={{ fontSize: "11px", color: "var(--color-neutral-500)", marginTop: "3px", flexShrink: "0" }}></i>
                        <span style={{ minWidth: "0" }}>{src}</span>
                      </div>
                    ))}
                    <div style={{ display: "flex", alignItems: "flex-start", gap: "7px", fontSize: "11.5px", lineHeight: "1.45", color: "var(--color-neutral-400)" }}>
                      <i className="ph ph-database" aria-hidden="true" style={{ fontSize: "11px", color: "var(--color-neutral-500)", marginTop: "3px", flexShrink: "0" }}></i>
                      <span>{"Not in the vendor master"}</span>
                    </div>
                  </div>
                  <div style={{ display: "flex", gap: "7px", marginTop: "auto", paddingTop: "12px" }}>
                    <button type="button" className="hv7" onClick={x.hoist} style={{ fontFamily: "inherit", fontSize: "11.5px", padding: "6px 12px", borderRadius: "7px", border: "1px solid var(--color-accent)", background: "var(--color-accent)", color: "var(--accent-ink)", cursor: "pointer", whiteSpace: "nowrap" }}>
                      {"Hoist vendor"}
                    </button>
                    <button type="button" className="hv4" onClick={x.dismiss} style={{ fontFamily: "inherit", fontSize: "11.5px", padding: "6px 12px", borderRadius: "7px", border: "1px solid var(--color-divider)", background: "transparent", color: "var(--color-neutral-400)", cursor: "pointer", whiteSpace: "nowrap" }}>
                      {"Not a vendor"}
                    </button>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div style={{ fontSize: "12px", color: "var(--color-neutral-500)", padding: "12px 0" }}>{vals.vhDetNote}</div>
          )}

          {/* ── Hoisted ──────────────────────────────────────────────────────── */}
          <div style={{ display: "flex", alignItems: "baseline", flexWrap: "wrap", gap: "6px 10px", marginTop: "32px" }}>
            <span style={LABEL}>{"Hoisted vendors · " + vals.vhHoistedN}</span>
            {vals.vhPreparedN ? (
              <span style={{ fontSize: "11px", color: "var(--st-warn)" }}>{vals.vhPreparedN + " not saved"}</span>
            ) : null}
          </div>
          {(vals.vhHoisted || []).length ? (
            <div style={{ borderRadius: "10px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", overflow: "hidden", marginTop: "12px" }}>
              {vals.vhHoisted.map((v, i) => (
                <div key={v.key + (v.local ? ":l" : "")} title={v.title || undefined} style={{ display: "flex", alignItems: "center", gap: "8px 16px", flexWrap: "wrap", padding: "10px 14px", borderTop: i ? "1px solid var(--color-divider)" : "none", background: v.prepared ? "var(--marker-tint)" : "transparent" }}>
                  <span style={{ fontSize: "12.5px", flex: "0 1 240px", minWidth: "160px" }}>{v.name}</span>
                  <span style={{ fontSize: "11px", color: "var(--color-neutral-500)", flex: "1 1 220px", minWidth: "0", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{v.trade}</span>
                  <span style={{ fontSize: "10.5px", padding: "2px 8px", borderRadius: "5px", whiteSpace: "nowrap", color: v.fg, background: v.bg }}>{v.status}</span>
                  <span style={{ fontSize: "10.5px", color: v.whenFg, whiteSpace: "nowrap", minWidth: "110px", textAlign: "right" }}>{v.when}</span>
                  {v.prepared ? (
                    <span style={{ display: "inline-flex", alignItems: "center", gap: "10px" }}>
                      <button type="button" className="hv7" onClick={v.canSave ? v.save : undefined} disabled={!v.canSave} title={"Write " + v.name + " to the vendor master"}
                        style={{ fontFamily: "inherit", fontSize: "10.5px", padding: "3px 10px", borderRadius: "6px", border: "1px solid var(--color-accent)", background: "var(--color-accent)", color: "var(--accent-ink)", cursor: v.canSave ? "pointer" : "default", opacity: v.canSave ? "1" : "0.6", whiteSpace: "nowrap" }}>
                        {"Save"}
                      </button>
                      <button type="button" className="hv11" onClick={v.discard} title={"Discard the record for " + v.name + " — nothing was saved"} style={{ fontFamily: "inherit", fontSize: "10.5px", padding: "0", background: "none", border: "none", color: "var(--color-neutral-500)", cursor: "pointer", whiteSpace: "nowrap" }}>
                        {"Discard"}
                      </button>
                    </span>
                  ) : null}
                </div>
              ))}
            </div>
          ) : null}
          {vals.vhListNote ? (
            <div style={{ fontSize: "11.5px", color: "var(--st-warn)", padding: "10px 0 0" }}>{vals.vhListNote}</div>
          ) : null}
          {vals.vhHoistedNote ? (
            <div style={{ fontSize: "12px", color: "var(--color-neutral-500)", padding: "12px 0" }}>{vals.vhHoistedNote}</div>
          ) : null}
          {vals.vhMore ? (
            <button type="button" className="hv4" onClick={vals.vhShowAll} style={{ marginTop: "10px", fontFamily: "inherit", fontSize: "11.5px", padding: "6px 12px", borderRadius: "7px", border: "1px solid var(--color-divider)", background: "transparent", color: "var(--color-neutral-400)", cursor: "pointer" }}>
              {vals.vhMore}
            </button>
          ) : null}
        </div>
      </div>
  );
}
