// VendorDrawer — the vendor an asset is assigned to, and (for an admin) moving it.
// Same frame as DetailDrawer. `vals.vd` is logic/assetsVendor.js's vendorDrawerModel.
import React from 'react';

const label = { fontSize: "11.5px", color: "var(--color-neutral-500)", width: "170px", flexShrink: "0" };
const value = { fontSize: "12.5px", flex: "1", lineHeight: "1.45", textWrap: "pretty" };
const head = { fontSize: "11px", letterSpacing: "0.11em", textTransform: "uppercase", color: "var(--color-neutral-500)", margin: "22px 0 9px" };

function Row({ l, v, color }) {
  if (!v) return null;
  return (
    <div style={{ display: "flex", alignItems: "flex-start", gap: "12px", padding: "10px 0", borderBottom: "1px solid var(--color-divider)" }}>
      <span style={label}>{l}</span>
      <span style={Object.assign({}, value, color ? { color } : {})}>{v}</span>
    </div>
  );
}

export default function VendorDrawer({ vals }) {
  const d = vals.vd || {};
  return (
    <>
      <div onClick={d.close} style={{ position: "fixed", inset: "0", background: "var(--scrim)", zIndex: "70" }}></div>
      <div role="dialog" aria-label={d.title} style={{ position: "fixed", top: "0", right: "0", bottom: "0", width: "620px", maxWidth: "100vw", background: "var(--color-bg)", boxShadow: "var(--shadow-lg)", zIndex: "71", display: "flex", flexDirection: "column", animation: "fadeUp 0.22s ease both" }}>
        <div style={{ display: "flex", alignItems: "flex-start", gap: "12px", padding: "18px 22px", borderBottom: "1px solid var(--color-divider)", flexShrink: "0" }}>
          <i className="ph ph-handshake" style={{ fontSize: "17px", marginTop: "2px", color: "var(--color-accent)" }}></i>
          <div style={{ flex: "1" }}>
            <div style={{ fontSize: "10.5px", letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
              {"Assets · vendor"}
            </div>
            <div style={{ fontSize: "15px", lineHeight: "1.35", marginTop: "5px" }}>{d.title}</div>
            <div style={{ fontSize: "11.5px", color: "var(--color-neutral-500)", marginTop: "5px" }}>{d.meta}</div>
          </div>
          <i className="ph ph-x" role="button" aria-label="Close" onClick={d.close} style={{ fontSize: "16px", color: "var(--color-neutral-500)", cursor: "pointer" }}></i>
        </div>
        <div style={{ flex: "1", overflowY: "auto", padding: "18px 22px 40px" }}>
          <div style={{ display: d.loadingShow, fontSize: "12.5px", color: "var(--color-neutral-500)" }}>{"Reading the vendor record…"}</div>
          <div style={{ display: d.errorShow, padding: "10px 12px", borderRadius: "8px", background: "var(--st-risk-bg)", color: "var(--st-risk)", fontSize: "12.5px", lineHeight: "1.5" }}>{d.errorText}</div>
          <div style={{ display: d.doneShow, padding: "10px 12px", borderRadius: "8px", background: "var(--st-ok-bg)", color: "var(--st-ok)", fontSize: "12.5px", lineHeight: "1.5", marginTop: "8px" }}>{d.doneText}</div>
          <div style={{ display: d.bodyShow }}>
            <div style={Object.assign({}, head, { marginTop: "4px" })}>{"Assigned vendor"}</div>
            <div style={{ padding: "3px 14px 6px", borderRadius: "10px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)" }}>
              <Row l="Vendor" v={d.vendorName} />
              <Row l="Code · trade · accreditation" v={d.vendorLine} />
              <Row l="Block state" v={d.blockText} color={d.blockColor} />
              <Row l="Contact on record" v={d.contactLine} />
              <Row l="Phone" v={d.phone} />
              <Row l="Latest score" v={d.scoreLine} />
              <Row l="Open work orders here" v={d.openWorkOrders} />
              <Row l="Last change" v={d.lastChange} />
            </div>
            {d.unreadableNote ? (
              <div style={{ fontSize: "11px", color: "var(--st-warn)", marginTop: "8px" }}>{d.unreadableNote}</div>
            ) : null}
            <div style={{ display: d.changeShow }}>
              <div style={head}>{"Change vendor"}</div>
              {d.tradeHint ? (
                <div style={{ fontSize: "11.5px", color: "var(--color-neutral-500)", margin: "-3px 0 9px", lineHeight: "1.5" }}>{d.tradeHint}</div>
              ) : null}
              <div style={{ padding: "12px 14px", borderRadius: "10px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", display: "flex", flexDirection: "column", gap: "10px" }}>
                <select value={d.pick} onChange={d.pickChange} aria-label="Move to vendor"
                  style={{ fontFamily: "var(--font-body)", fontSize: "12.5px", padding: "7px 9px", borderRadius: "7px", border: "1px solid var(--color-divider)", background: "var(--color-bg)", color: "var(--color-text)" }}>
                  <option value="">{d.pickPrompt}</option>
                  {(d.options || []).map((o) => (
                    <option key={o.value} value={o.value} disabled={o.disabled}>{o.label}</option>
                  ))}
                </select>
                <textarea value={d.note} onChange={d.noteChange} rows={2} placeholder="Why — kept in the audit log (optional)"
                  style={{ fontFamily: "var(--font-body)", fontSize: "12.5px", padding: "7px 9px", borderRadius: "7px", border: "1px solid var(--color-divider)", background: "var(--color-bg)", color: "var(--color-text)", resize: "vertical" }} />
                <div className="btn btn-secondary" role="button" onClick={d.ask} style={{ display: d.askShow, alignSelf: "flex-start", fontSize: "12.5px", padding: "7px 14px", cursor: "pointer" }}>
                  {"Change vendor…"}
                </div>
                <div style={{ display: d.confirmShow, padding: "11px 12px", borderRadius: "8px", border: "1px solid var(--color-accent)" }}>
                  <div style={{ fontSize: "12.5px", lineHeight: "1.55" }}>{d.confirmText}</div>
                  <div style={{ display: "flex", gap: "8px", marginTop: "10px" }}>
                    <div className="btn btn-primary" role="button" onClick={d.saving ? undefined : d.confirm} style={{ fontSize: "12.5px", padding: "7px 14px", cursor: d.saving ? "default" : "pointer", opacity: d.saving ? "0.6" : "1" }}>
                      {d.confirmLabel}
                    </div>
                    <div className="btn btn-secondary" role="button" onClick={d.cancel} style={{ fontSize: "12.5px", padding: "7px 14px", cursor: "pointer" }}>
                      {"Cancel"}
                    </div>
                  </div>
                </div>
              </div>
            </div>
            {d.readOnlyNote ? (
              <div style={{ fontSize: "11.5px", color: "var(--color-neutral-500)", marginTop: "14px" }}>{d.readOnlyNote}</div>
            ) : null}
          </div>
        </div>
        <div style={{ display: "flex", gap: "9px", padding: "15px 22px", borderTop: "1px solid var(--color-divider)", flexShrink: "0", background: "var(--color-bg)" }}>
          <div className="btn btn-secondary" role="button" onClick={d.close} style={{ fontSize: "12.5px", padding: "8px 15px", cursor: "pointer" }}>{"Close"}</div>
        </div>
      </div>
    </>
  );
}
