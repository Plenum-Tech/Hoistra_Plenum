// Gate — sign-in gate
// Ported from the Hoistra prototype template. `vals` is the view model from useHoistra().
import React from 'react';
import GatePanel from '../components/shell/GatePanel.jsx';

export default function Gate({ vals }) {
  return (
      <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) 280px", gap: "0", alignItems: "start" }}>
        <div style={{ padding: "64px 56px 80px", display: "flex", flexDirection: "column" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
            <div style={{ width: "38px", height: "38px", borderRadius: "10px", border: "1.5px solid var(--color-accent)", display: "flex", alignItems: "center", justifyContent: "center" }}>
              <div style={{ width: "13px", height: "13px", borderRadius: "50%", background: "var(--color-accent)" }}></div>
            </div>
            <span style={{ fontSize: "30px", fontWeight: "500", letterSpacing: "-0.015em" }}>
              {"Hoistra"}
            </span>
          </div>
          <div style={{ display: "flex", flexDirection: "column", padding: "64px 0 0" }}>
            <h1 style={{ fontSize: "60px", margin: "0", lineHeight: "0.98", letterSpacing: "-0.035em" }}>
              {"Buildings, Hoisted."}
            </h1>
            <div style={{ fontSize: "17px", letterSpacing: "0.01em", color: "var(--color-accent)", marginTop: "16px" }}>
              {"The Operating brain for the Built Environment."}
            </div>
            <div style={{ marginTop: "34px", maxWidth: "860px", minHeight: "40px", display: "grid", alignItems: "center" }}>
              <div style={{ gridArea: "1/1", display: "flex", alignItems: "center", justifyContent: "flex-start", flexWrap: "wrap", gap: "4px 14px", fontSize: "11.5px", color: "var(--color-neutral-400)", pointerEvents: "none", opacity: vals.f1.o, transform: `translateY(${vals.f1.y})`, transition: "opacity 0.5s ease,transform 0.5s ease" }}>
                <span style={{ display: "flex", alignItems: "baseline", gap: "5px", whiteSpace: "nowrap" }}>
                  <span style={{ fontSize: "18px", color: "var(--color-text)", fontVariantNumeric: "tabular-nums" }}>
                    {"24"}
                  </span>
                  <span>
                    {"buildings hoisted"}
                  </span>
                </span>
                <span style={{ display: "flex", alignItems: "baseline", gap: "5px", whiteSpace: "nowrap" }}>
                  <span style={{ fontSize: "18px", color: "var(--color-text)", fontVariantNumeric: "tabular-nums" }}>
                    {"1.84m"}
                  </span>
                  <span>
                    {"ft²"}
                  </span>
                </span>
                <span style={{ display: "flex", alignItems: "baseline", gap: "5px", whiteSpace: "nowrap" }}>
                  <span style={{ fontSize: "18px", color: "var(--color-text)" }}>
                    {"UK, US"}
                  </span>
                </span>
              </div>
              <div style={{ gridArea: "1/1", display: "flex", alignItems: "center", justifyContent: "flex-start", flexWrap: "wrap", gap: "5px 6px", pointerEvents: "none", opacity: vals.f2.o, transform: `translateY(${vals.f2.y})`, transition: "opacity 0.5s ease,transform 0.5s ease" }}>
                {(vals.f2items || []).map((i, $index) => (
                  <React.Fragment key={$index}>
                    <span style={{ display: "flex", alignItems: "center", gap: "7px", fontSize: "11px", padding: "4px 9px", borderRadius: "6px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", whiteSpace: "nowrap" }}>
                      <span style={{ width: "6px", height: "6px", borderRadius: "50%", background: i.dot, flexShrink: "0" }}></span>
                      <span>
                        {i.label}
                      </span>
                    </span>
                  </React.Fragment>
                ))}
              </div>
              <div style={{ gridArea: "1/1", display: "flex", alignItems: "center", justifyContent: "flex-start", flexWrap: "wrap", gap: "6px 12px", pointerEvents: "none", opacity: vals.f3.o, transform: `translateY(${vals.f3.y})`, transition: "opacity 0.5s ease,transform 0.5s ease" }}>
                <span style={{ fontSize: "11.5px", color: "var(--color-neutral-400)", whiteSpace: "nowrap" }}>
                  {"3 decisions priced"}
                </span>
                <span style={{ display: "flex", alignItems: "center", gap: "7px", fontSize: "12px", padding: "7px 12px", borderRadius: "7px", background: "var(--color-accent)", color: "var(--accent-ink)", whiteSpace: "nowrap" }}>
                  <span>
                    {"Approve £14,200"}
                  </span>
                  <i className="ph ph-arrow-right" style={{ fontSize: "12px" }}></i>
                </span>
              </div>
              <div style={{ gridArea: "1/1", display: "flex", alignItems: "center", justifyContent: "flex-start", flexWrap: "wrap", gap: "6px", pointerEvents: vals.f4.pe, opacity: vals.f4.o, transform: `translateY(${vals.f4.y})`, transition: "opacity 0.5s ease,transform 0.5s ease" }}>
                {(vals.f4items || []).map((p, $index) => (
                  <React.Fragment key={$index}>
                    <div className="hv1" onClick={p.click} title={p.sub} style={{ display: "flex", alignItems: "center", gap: "7px", fontSize: "11.5px", padding: "6px 11px", borderRadius: "7px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", cursor: "pointer", whiteSpace: "nowrap" }}>
                      <i className={`ph ${p.icon}`} style={{ fontSize: "13px", color: "var(--color-accent)" }}></i>
                      <span style={{ color: "var(--color-text)" }}>
                        {p.name}
                      </span>
                      <span style={{ fontSize: "10.5px", color: "var(--color-neutral-500)" }}>
                        {p.sub}
                      </span>
                      <i className="ph ph-arrow-up-right" style={{ fontSize: "11px", color: "var(--color-neutral-500)" }}></i>
                    </div>
                  </React.Fragment>
                ))}
              </div>
            </div>
            <p style={{ fontSize: "21px", lineHeight: "1.4", margin: "48px 0 0", maxWidth: "860px", letterSpacing: "-0.012em", textWrap: "pretty" }}>
              {"Property and asset managers carry the risk but do not hold the data."}
            </p>
            <p style={{ fontSize: "13.5px", lineHeight: "1.6", color: "var(--color-neutral-400)", margin: "10px 0 0", maxWidth: "860px", textWrap: "pretty" }}>
              {"The evidence sits with the FM provider, the finance system, the certificate PDFs and the meter operator — none of which report to them. Hoistra closes that gap and acts on it: predicting failures, chasing compliance, evidencing every service credit the contract owes you and more."}
            </p>
            <div style={{ marginTop: "26px", maxWidth: "860px", padding: "22px 24px 20px", borderRadius: "12px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)" }}>
              <div style={{ fontSize: "10.5px", letterSpacing: "0.13em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                {"Multiple Sources, One System of Record."}
              </div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(110px,1fr))", gap: "8px", marginTop: "14px" }}>
                {(vals.costHeads || []).map((c, $index) => (
                  <React.Fragment key={$index}>
                    <div style={{ display: "flex", flexDirection: "column", gap: "8px", minWidth: "0" }}>
                      <div style={{ flex: "1", padding: "12px 10px 13px", borderRadius: "8px", border: "1px solid var(--color-divider)", display: "flex", flexDirection: "column", gap: "5px", minWidth: "0" }}>
                        <span style={{ fontSize: "13px", color: "var(--color-text)" }}>
                          {c.head}
                        </span>
                        <span style={{ fontSize: "11px", lineHeight: "1.4", color: "var(--color-neutral-500)" }}>
                          {c.body}
                        </span>
                      </div>
                      <div style={{ display: "flex", justifyContent: "center", color: "var(--color-neutral-600)" }}>
                        <i className="ph ph-arrow-down" style={{ fontSize: "12px" }}></i>
                      </div>
                    </div>
                  </React.Fragment>
                ))}
              </div>
              <div style={{ marginTop: "8px", borderRadius: "8px", border: "1px solid var(--color-accent)", background: "var(--color-surface)", color: "var(--color-accent)", padding: "18px 16px 16px" }}>
                <div style={{ textAlign: "center", fontSize: "10px", letterSpacing: "0.14em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                  {"Lands on"}
                </div>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(5,minmax(0,1fr))", gap: "8px", marginTop: "14px" }}>
                  {(vals.noiImpacts || []).map((n, $index) => (
                    <React.Fragment key={$index}>
                      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: "9px", textAlign: "center" }}>
                        <div style={{ width: "40px", height: "40px", borderRadius: "50%", border: "1.5px solid var(--color-accent)", display: "flex", alignItems: "center", justifyContent: "center" }}>
                          <i className={`ph ${n.icon}`} style={{ fontSize: "18px" }}></i>
                        </div>
                        <span style={{ fontSize: "12px", lineHeight: "1.3" }}>
                          {n.label}
                        </span>
                      </div>
                    </React.Fragment>
                  ))}
                </div>
              </div>
            </div>
            <div style={{ fontSize: "10.5px", letterSpacing: "0.14em", textTransform: "uppercase", color: "var(--color-neutral-500)", marginTop: "56px" }}>
              {"What the platform does"}
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(2,1fr)", gap: "13px", marginTop: "16px", maxWidth: "860px" }}>
              {(vals.doesCards || []).map((d, $index) => (
                <React.Fragment key={$index}>
                  <div style={{ padding: "19px", borderRadius: "11px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", display: "flex", flexDirection: "column", gap: "8px" }}>
                    <div style={{ display: "flex", alignItems: "center", gap: "9px" }}>
                      <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10.5px", color: "var(--color-accent)" }}>
                        {d.n}
                      </span>
                      <span style={{ fontSize: "14.5px" }}>
                        {d.name}
                      </span>
                    </div>
                    <div style={{ fontSize: "12.5px", lineHeight: "1.55", color: "var(--color-neutral-300)" }}>
                      {d.body}
                    </div>
                  </div>
                </React.Fragment>
              ))}
            </div>
            <div style={{ marginTop: "34px", maxWidth: "860px", padding: "28px 30px", borderRadius: "12px", background: "var(--color-accent)", color: "var(--accent-ink)" }}>
              <div style={{ fontSize: "10.5px", letterSpacing: "0.14em", textTransform: "uppercase", opacity: "0.62" }}>
                {"The impact"}
              </div>
              <div style={{ fontSize: "27px", lineHeight: "1.24", marginTop: "12px", letterSpacing: "-0.015em", textWrap: "pretty" }}>
                {"A net positive NOI shift on optimised cost, asset value that rises with it, data-backed packs for insurance and asset ratings, compliance and safety demonstrably checked, and an upskilled team."}
              </div>
            </div>
          </div>
          <div style={{ fontSize: "10.5px", letterSpacing: "0.14em", textTransform: "uppercase", color: "var(--color-neutral-500)", marginTop: "56px" }}>
            {"How a building gets hoisted"}
          </div>
          <div style={{ marginTop: "16px", maxWidth: "860px", padding: "26px", borderRadius: "12px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)" }}>
            <div style={{ display: "flex", alignItems: "stretch", gap: "0" }}>
              {(vals.flowSteps || []).map((s, $index) => (
                <React.Fragment key={$index}>
                  <div style={{ flex: "1", display: "flex", alignItems: "stretch", gap: "0" }}>
                    <div style={{ flex: "1", display: "flex", flexDirection: "column", gap: "7px" }}>
                      <div style={{ height: "3px", borderRadius: "2px", background: s.bar }}></div>
                      <div style={{ fontFamily: "ui-monospace,monospace", fontSize: "10px", color: "var(--color-neutral-500)", marginTop: "3px" }}>
                        {s.n}
                      </div>
                      <div style={{ fontSize: "13.5px", color: s.fg }}>
                        {s.name}
                      </div>
                      <div style={{ fontSize: "11.5px", lineHeight: "1.5", color: "var(--color-neutral-400)" }}>
                        {s.body}
                      </div>
                    </div>
                    <div style={{ width: "26px", display: "flex", alignItems: "center", justifyContent: "center", color: "var(--color-neutral-600)", flexShrink: "0" }}>
                      <i className="ph ph-caret-right" style={{ fontSize: "12px", display: s.arrow }}></i>
                    </div>
                  </div>
                </React.Fragment>
              ))}
            </div>
            <div style={{ height: "1px", background: "var(--color-divider)", margin: "22px 0 18px" }}></div>
            <div style={{ display: "grid", gridTemplateColumns: "minmax(150px,0.8fr) 28px minmax(0,1.6fr)", gap: "0", alignItems: "stretch" }}>
              <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
                <span style={{ fontSize: "10.5px", letterSpacing: "0.12em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                  {"Coverage"}
                </span>
                <div style={{ flex: "1", display: "flex", flexDirection: "column", gap: "5px", minHeight: "150px" }}>
                  {(vals.coverageBands || []).map((b, $index) => (
                    <React.Fragment key={$index}>
                      <div style={{ flex: "1", borderRadius: "6px", padding: "9px 11px", display: "flex", flexDirection: "column", justifyContent: "center", gap: "2px", background: b.bg, color: b.fg, border: `1px solid ${b.border}` }}>
                        <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10px", opacity: "0.7" }}>
                          {b.range}
                        </span>
                        <span style={{ fontSize: "12px" }}>
                          {b.label}
                        </span>
                      </div>
                    </React.Fragment>
                  ))}
                </div>
                <span style={{ fontSize: "10.5px", color: "var(--color-neutral-500)", lineHeight: "1.45" }}>
                  {"Hoist Score — how much of the portfolio the graph holds."}
                </span>
              </div>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "center", color: "var(--color-neutral-600)" }}>
                <i className="ph ph-arrow-right" style={{ fontSize: "14px" }}></i>
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
                <span style={{ fontSize: "10.5px", letterSpacing: "0.12em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                  {"Impact"}
                </span>
                <div style={{ flex: "1", display: "flex", flexDirection: "column", justifyContent: "space-between", gap: "7px" }}>
                  {(vals.flowOut || []).map((o, $index) => (
                    <React.Fragment key={$index}>
                      <div style={{ display: "grid", gridTemplateColumns: "11ch 1fr", gap: "12px", alignItems: "center" }}>
                        <span style={{ fontSize: "12.5px" }}>
                          {o.label}
                        </span>
                        <div style={{ height: "8px", borderRadius: "4px", background: "var(--color-neutral-900)", overflow: "hidden" }}>
                          <div style={{ height: "100%", borderRadius: "4px", background: "var(--color-accent)", width: o.pct }}></div>
                        </div>
                      </div>
                    </React.Fragment>
                  ))}
                </div>
                <span style={{ fontSize: "10.5px", color: "var(--color-neutral-500)", lineHeight: "1.45" }}>
                  {"Every point of coverage compounds across all five."}
                </span>
              </div>
            </div>
          </div>
          <div style={{ fontSize: "10.5px", letterSpacing: "0.14em", textTransform: "uppercase", color: "var(--color-neutral-500)", marginTop: "56px" }}>
            {"Regulations we operate in"}
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(4,1fr)", gap: "13px", marginTop: "16px", maxWidth: "860px" }}>
            {(vals.regPacks || []).map((r, $index) => (
              <React.Fragment key={$index}>
                <div style={{ padding: "17px", borderRadius: "11px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", display: "flex", flexDirection: "column", gap: "6px" }}>
                  <div style={{ fontSize: "14.5px" }}>
                    {r.country}
                  </div>
                  <div style={{ fontSize: "11.5px", lineHeight: "1.5", color: "var(--color-neutral-400)" }}>
                    {r.body}
                  </div>
                </div>
              </React.Fragment>
            ))}
          </div>
          <div style={{ fontSize: "10.5px", letterSpacing: "0.14em", textTransform: "uppercase", color: "var(--color-neutral-500)", marginTop: "56px" }}>
            {"Objections, answered"}
          </div>
          <div style={{ marginTop: "16px", maxWidth: "860px", borderRadius: "12px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", overflow: "hidden" }}>
            {(vals.objections || []).map((o, $index) => (
              <React.Fragment key={$index}>
                <div className="hv2" onClick={o.toggle} style={{ padding: "16px 20px", borderBottom: "1px solid var(--color-divider)", cursor: "pointer" }}>
                  <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
                    <span style={{ flex: "1", fontSize: "14px", color: o.fg }}>
                      {o.q}
                    </span>
                    <i className={`ph ${o.icon}`} style={{ fontSize: "14px", color: "var(--color-neutral-500)" }}></i>
                  </div>
                  <div style={{ fontSize: "12.5px", lineHeight: "1.6", color: "var(--color-neutral-300)", maxWidth: "74ch", display: o.show, marginTop: "10px" }}>
                    {o.a}
                  </div>
                </div>
              </React.Fragment>
            ))}
          </div>
        </div>
        <div style={{ position: "sticky", top: "0", height: "100vh", boxSizing: "border-box", background: "var(--color-surface)", padding: "64px 28px", display: "flex", flexDirection: "column", justifyContent: "center", borderLeft: "1px solid var(--color-divider)" }}>
          <GatePanel vals={vals} />
        </div>
      </div>
  );
}
