// HoistBuildingCard — the "Hoist a building" / "Edit building" card in the orchestrator dock.
//
// Ported from the prototype's declare flow (reference/Hoistra.dc.html, `fDeclare`) and given
// the real form: every field here is one `logic/buildingsCrud.js` sends. Create runs as three
// steps — the record, the schema it landed in, then documents — and edit is the same card as a
// single step, since a PATCH has nothing to show after itself.
//
// Errors come back from the service keyed by field, so each renders under its own input; the
// one that names no input (or names one this route will not let you touch) is the banner.
import React from 'react';

const LABEL = { fontSize: "9.5px", letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--color-neutral-500)" };
const INPUT = { width: "100%", boxSizing: "border-box", fontSize: "11.5px", padding: "6px 8px", borderRadius: "6px", border: "1px solid var(--color-divider)", background: "var(--color-surface)", color: "var(--color-text)", fontFamily: "var(--font-body)", outline: "none" };
const MONO = { fontFamily: "ui-monospace,monospace" };
const PRIMARY = { flex: "1", textAlign: "center", fontSize: "11.5px", padding: "6px", borderRadius: "7px", background: "var(--color-accent)", color: "var(--accent-ink)", cursor: "pointer" };
const QUIET = { fontSize: "11.5px", padding: "6px 10px", borderRadius: "7px", border: "1px solid var(--color-divider)", color: "var(--color-neutral-500)", cursor: "pointer" };

function Field({ label, hint, hintTone, error, children }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: "3px" }}>
      <span style={LABEL}>{label}</span>
      {children}
      {hint ? <span style={{ fontSize: "10px", lineHeight: "1.4", color: hintTone === "accent" ? "var(--color-accent)" : "var(--color-neutral-500)" }}>{hint}</span> : null}
      {error ? <span style={{ fontSize: "10px", lineHeight: "1.4", color: "var(--st-warn)" }}>{error}</span> : null}
    </div>
  );
}

export default function HoistBuildingCard({ vals }) {
  const f = vals.bcForm || {};
  const edit = vals.bcMode === "edit";
  return (
    <div style={{ marginTop: "16px", padding: "12px", borderRadius: "9px", background: "var(--color-bg)", border: "1px solid var(--color-accent)" }}>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: "8px" }}>
        <span style={{ fontSize: "9.5px", letterSpacing: "0.12em", textTransform: "uppercase", color: "var(--color-accent)" }}>{vals.bcTitle}</span>
        <span style={{ fontSize: "9.5px", color: "var(--color-neutral-500)", whiteSpace: "nowrap" }}>{vals.bcStepLabel}</span>
      </div>

      {vals.bcStep1 ? (
        <>
          <div style={{ fontSize: "10.5px", color: "var(--color-neutral-500)", lineHeight: "1.45", marginTop: "7px" }}>{vals.bcIntro}</div>

          {vals.bcTopError ? (
            <div style={{ marginTop: "9px", padding: "7px 9px", borderRadius: "7px", background: "var(--st-warn-bg)", fontSize: "10.5px", color: "var(--st-warn)", lineHeight: "1.45" }}>{vals.bcTopError}</div>
          ) : null}

          <div style={{ display: "flex", flexDirection: "column", gap: "7px", marginTop: "10px" }}>
            <Field label="Building name" error={vals.bcErr("name")}>
              <input className="input" value={f.site_name} onChange={vals.bcSet("site_name")} placeholder="Bishopsgate Tower" style={INPUT} />
            </Field>
            <Field label="Country" hint={vals.bcStandardNote} hintTone="accent" error={vals.bcErr("country_code")}>
              <select className="input" value={f.country_code} onChange={vals.bcSet("country_code")} style={INPUT}>
                {(vals.bcCountries || []).map((c) => (<option key={c.code} value={c.code}>{c.name}</option>))}
              </select>
            </Field>
            <Field label="Region or state" error={vals.bcErr("region")}>
              <input className="input" value={f.state} onChange={vals.bcSet("state")} placeholder="London" style={INPUT} />
            </Field>
            <Field label="Primary use" hint={vals.bcUseNote} error={vals.bcErr("use_type")}>
              <select className="input" value={f.use_type} onChange={vals.bcSet("use_type")} style={INPUT}>
                {(vals.bcUseTypes || []).map((u) => (<option key={u} value={u}>{u}</option>))}
              </select>
            </Field>
            <Field label="Floors" error={vals.bcErr("floors")}>
              <input className="input" value={f.floors} onChange={vals.bcSet("floors")} inputMode="numeric" placeholder="24" style={INPUT} />
            </Field>
            {/* The one field where a wrong unit is accepted, stored, and wrong everywhere
                after. It is labelled m² three times on purpose. */}
            <Field label="Floor area · m²" hint="Square metres, not feet" error={vals.bcErr("gfa_sqm")}>
              <input className="input" value={f.gfa_sqm} onChange={vals.bcSet("gfa_sqm")} inputMode="numeric" placeholder="40000 m²" style={INPUT} />
            </Field>

            {/* Use mix — the running total is shown because "must sum to 100" is a rule you
                can only follow if you can see where you are against it. */}
            <div style={{ display: "flex", flexDirection: "column", gap: "7px", padding: "9px 10px", borderRadius: "7px", background: "var(--color-surface)" }}>
              <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: "8px" }}>
                <span style={LABEL}>{"Use mix"}</span>
                <span style={{ fontSize: "9.5px", color: vals.bcMixTotalColor, fontVariantNumeric: "tabular-nums", ...MONO }}>{vals.bcMixTotalLabel}</span>
              </div>
              {(vals.bcMix || []).map((m) => (
                <div key={m.key} style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) 44px 14px", gap: "5px", alignItems: "center" }}>
                  <input className="input" value={m.use} onChange={m.setUse} placeholder="office" style={{ ...INPUT, fontSize: "11px", padding: "4px 6px", borderRadius: "5px", background: "var(--color-bg)" }} />
                  <input className="input" value={m.pct} onChange={m.setPct} inputMode="numeric" placeholder="100" style={{ ...INPUT, ...MONO, fontSize: "11px", padding: "4px 6px", borderRadius: "5px", background: "var(--color-bg)", fontVariantNumeric: "tabular-nums" }} />
                  <span className="hv11" onClick={m.remove} title="Remove this use" style={{ display: m.removeShow, fontSize: "12px", color: "var(--color-neutral-500)", cursor: "pointer", textAlign: "center" }}>{"×"}</span>
                </div>
              ))}
              <span className="hv11" onClick={vals.bcMixAdd} style={{ fontSize: "10.5px", color: "var(--color-accent)", cursor: "pointer" }}>{"+ Add a use"}</span>
              {vals.bcErr("use_mix") ? <span style={{ fontSize: "10px", lineHeight: "1.4", color: "var(--st-warn)" }}>{vals.bcErr("use_mix")}</span> : null}
            </div>

            <Field label="Metering" hint={vals.bcGranularityNote} error={vals.bcErr("metering_granularity")}>
              <select className="input" value={f.metering_granularity} onChange={vals.bcSet("metering_granularity")} style={INPUT}>
                {(vals.bcGranularities || []).map((g) => (<option key={g.value} value={g.value}>{g.label}</option>))}
              </select>
            </Field>
            <Field label="Building code" error={vals.bcErr("building_code")}>
              <input className="input" value={f.building_code} onChange={vals.bcSet("building_code")} placeholder="allocated as B-NN" style={INPUT} />
            </Field>
            <Field label="Site it belongs to" error={vals.bcErr("site_id")}>
              <input className="input" value={f.site_id} onChange={vals.bcSet("site_id")} placeholder="S-01 — optional, must already exist" style={INPUT} />
            </Field>
          </div>

          {edit ? (
            <div style={{ fontSize: "10px", color: "var(--color-neutral-500)", lineHeight: "1.45", marginTop: "9px" }}>
              {"Only the fields above that you change are sent — PATCH leaves the rest of the record exactly as it was, including anything in raw_metadata."}
            </div>
          ) : null}

          <div style={{ display: "flex", gap: "6px", marginTop: "10px" }}>
            <div className="hv7" onClick={vals.bcSubmit} style={{ ...PRIMARY, cursor: vals.bcSaving ? "default" : "pointer", opacity: vals.bcSaving ? "0.6" : "1" }}>{vals.bcSubmitLabel}</div>
            <div className="hv11" onClick={vals.bcClose} style={QUIET}>{"Cancel"}</div>
          </div>
        </>
      ) : null}

      {vals.bcStep2 ? (
        <>
          <div style={{ fontSize: "11.5px", lineHeight: "1.5", marginTop: "8px" }}>
            {vals.bcResultName}{" is keyed as "}<span style={{ ...MONO, color: "var(--color-accent)" }}>{vals.bcResultCode}</span>{"."}
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: "5px", marginTop: "10px" }}>
            <div style={{ padding: "8px 10px", borderRadius: "7px", background: "var(--color-surface)", fontSize: "10.5px", lineHeight: "1.5" }}>
              <span style={{ ...MONO, color: "var(--color-accent-300)" }}>{"buildings"}</span>{" · primary key "}<span style={MONO}>{"building_id"}</span>{" — name, country, region, use, floors, area"}
            </div>
            <div style={{ padding: "8px 10px", borderRadius: "7px", background: "var(--color-surface)", fontSize: "10.5px", lineHeight: "1.5" }}>
              <span style={{ ...MONO, color: "var(--color-accent-300)" }}>{"floors"}</span>{" · primary key "}<span style={MONO}>{"floor_id"}</span>{" — foreign key "}<span style={MONO}>{"building_id"}</span>{", use, area"}
            </div>
            <div style={{ padding: "8px 10px", borderRadius: "7px", background: "var(--color-surface)", fontSize: "10.5px", lineHeight: "1.5" }}>
              <span style={{ ...MONO, color: "var(--color-accent-300)" }}>{"documents"}</span>{" · foreign key "}<span style={MONO}>{"building_id"}</span>{" — every certificate, contract and reading resolves back here"}
            </div>
          </div>
          {/* Warnings are not failures — "stored as Retail", "no regulation pack for this
              market". They belong after the write, next to what it produced. */}
          {(vals.bcResultWarnings || []).map((w, i) => (
            <div key={i} style={{ fontSize: "10.5px", color: "var(--st-warn)", lineHeight: "1.45", marginTop: "8px" }}>{w}</div>
          ))}
          <div style={{ display: "flex", gap: "6px", marginTop: "11px" }}>
            <div className="hv7" onClick={vals.bcNext} style={PRIMARY}>{"Next steps"}</div>
          </div>
        </>
      ) : null}

      {vals.bcStep3 ? (
        <>
          <div style={{ fontSize: "10.5px", color: "var(--color-neutral-500)", lineHeight: "1.45", marginTop: "8px" }}>
            {"The record exists but holds no evidence. Ingest now, or come back to it — the building simply carries a 0% Hoist Score until documents arrive."}
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: "6px", marginTop: "11px" }}>
            <div className="hv7" onClick={vals.bcIngestNow} style={{ padding: "10px 11px", borderRadius: "8px", background: "var(--color-accent)", color: "var(--accent-ink)", cursor: "pointer" }}>
              <div style={{ fontSize: "12px" }}>{"Ingest documents now"}</div>
              <div style={{ fontSize: "10.5px", opacity: "0.8", marginTop: "2px", lineHeight: "1.4" }}>{"Certificates, contracts, asset registers, meter consent"}</div>
            </div>
            <div className="hv14" onClick={vals.bcLater} style={{ padding: "10px 11px", borderRadius: "8px", border: "1px solid var(--color-divider)", cursor: "pointer" }}>
              <div style={{ fontSize: "12px" }}>{"Do it later"}</div>
              <div style={{ fontSize: "10.5px", color: "var(--color-neutral-500)", marginTop: "2px", lineHeight: "1.4" }}>{"Hoist the record only — live and waiting"}</div>
            </div>
          </div>
        </>
      ) : null}
    </div>
  );
}
