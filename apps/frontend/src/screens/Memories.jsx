// Memories — Administration › Chat memory (logic/memoriesPage.js). What the chat has learned
// for this company from earlier conversations, who said it, how often it has been recalled,
// and Forget. Figures are never here: counts, costs and statuses are re-read live every time.
import React from 'react';

const CARD = { borderRadius: "10px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)" };
const KICK = { fontSize: "10.5px", letterSpacing: "0.09em", textTransform: "uppercase", color: "var(--color-neutral-500)" };
const QUIET = { fontSize: "12px", padding: "7px 12px", borderRadius: "7px", border: "1px solid var(--color-divider)", background: "transparent", color: "var(--color-neutral-400)", cursor: "pointer" };
const CELL = { padding: "10px 12px", borderBottom: "1px solid var(--color-divider)", fontSize: "12.5px", verticalAlign: "top" };
const chip = (on) => ({ fontSize: "12px", padding: "6px 12px", borderRadius: "999px", cursor: "pointer", whiteSpace: "nowrap",
  border: "1px solid " + (on ? "var(--color-accent)" : "var(--color-divider)"), background: on ? "var(--color-accent-900)" : "transparent",
  color: on ? "var(--color-accent)" : "var(--color-neutral-400)" });
const KIND_TONE = { fact: ["var(--color-accent-900)", "var(--color-accent)"], correction: ["var(--st-warn-bg)", "var(--st-warn)"], preference: ["var(--color-bg)", "var(--color-neutral-300)"] };

export default function Memories({ vals }) {
  return (
    <div style={{ maxWidth: "1180px", margin: "0 auto", padding: "0 24px 60px" }}>
      <div style={{ display: "flex", alignItems: "center", gap: "8px", padding: "24px 0 0", fontSize: "12px", color: "var(--color-neutral-500)" }}>
        <button type="button" onClick={vals.goHome} style={{ ...QUIET, border: "none", padding: "0", display: "flex", alignItems: "center", gap: "6px" }}>
          <i className="ph ph-arrow-left" style={{ fontSize: "12px" }}></i>{"Home"}
        </button>
        <span>{"/"}</span><span>{"Administration"}</span><span>{"/"}</span><span style={{ color: "var(--color-text)" }}>{"Chat memory"}</span>
      </div>

      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-end", gap: "16px", marginTop: "14px", flexWrap: "wrap" }}>
        <div>
          <h2 style={{ margin: "0", fontSize: "24px" }}>{"Chat memory"}</h2>
          <p style={{ margin: "6px 0 0", fontSize: "12.5px", color: "var(--color-neutral-500)", maxWidth: "720px", lineHeight: "1.5" }}>
            {"What the chat has learned from earlier conversations: facts and corrections people told it about this company (shared with every colleague, signed), and your own preferences for how answers read. "
              + "Figures are never remembered — counts, costs and statuses are re-read live on every question."}
          </p>
        </div>
        <button type="button" onClick={vals.mpReload} style={QUIET}>{"Refresh"}</button>
      </div>

      {vals.mpUnavailable ? (
        <div style={{ ...CARD, marginTop: "16px", padding: "14px 16px", fontSize: "12.5px", color: "var(--st-warn)" }}>
          {"The memory store is not available on this deployment right now; the chat is answering without it."}
        </div>
      ) : null}
      {vals.mpError ? <div style={{ ...CARD, marginTop: "16px", padding: "14px 16px", fontSize: "12.5px", color: "var(--st-risk)" }}>{"Could not read the memories: " + vals.mpError}</div> : null}

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(200px,1fr))", gap: "12px", marginTop: "18px" }}>
        {vals.mpTiles.map((t) => (
          <div key={t.label} style={{ ...CARD, padding: "14px 16px" }}>
            <div style={{ fontSize: "26px", fontWeight: "600", color: t.tone, lineHeight: "1" }}>{t.value}</div>
            <div style={{ ...KICK, marginTop: "8px" }}>{t.label}</div>
            <div style={{ fontSize: "11.5px", color: "var(--color-neutral-500)", marginTop: "3px" }}>{t.hint}</div>
          </div>
        ))}
      </div>

      <div style={{ display: "flex", gap: "8px", alignItems: "center", marginTop: "18px", flexWrap: "wrap" }}>
        {vals.mpFilters.map((f) => <button key={f.label} type="button" onClick={f.pick} style={chip(f.on)}>{f.label}</button>)}
        <input aria-label="Search memories" className="input" placeholder="Search text, subject or who said it…" value={vals.mpSearch} onChange={vals.mpSetSearch}
          style={{ marginLeft: "auto", fontSize: "12.5px", padding: "7px 10px", borderRadius: "7px", border: "1px solid var(--color-divider)", background: "var(--color-surface)", color: "var(--color-text)", minWidth: "260px" }} />
      </div>

      <div style={{ ...CARD, marginTop: "12px", overflowX: "auto" }}>
        {vals.mpLoading ? <div style={{ padding: "18px", fontSize: "12.5px", color: "var(--color-neutral-500)" }}>{"Reading what the chat remembers…"}</div>
          : vals.mpEmpty ? (
            <div style={{ padding: "22px 18px", fontSize: "12.5px", color: "var(--color-neutral-500)", lineHeight: "1.6" }}>
              {"Nothing remembered yet. Tell the chat something the records don't hold — \"the plant room at Bishopsgate is on level 3\" — or correct it when it is wrong, and it will appear here. "}
              {"In the chat, \"what do you remember about us?\" lists these and \"forget …\" removes one."}
            </div>
          ) : vals.mpNoMatch ? <div style={{ padding: "18px", fontSize: "12.5px", color: "var(--color-neutral-500)" }}>{"Nothing matches."}</div>
            : (
              <table style={{ width: "100%", borderCollapse: "collapse" }}>
                <thead>
                  <tr style={{ textAlign: "left", ...KICK }}>
                    <th style={CELL}>{"Memory"}</th><th style={CELL}>{"Kind"}</th><th style={CELL}>{"Scope"}</th>
                    <th style={CELL}>{"Said by"}</th><th style={CELL}>{"Recalled"}</th><th style={CELL}></th>
                  </tr>
                </thead>
                <tbody>
                  {vals.mpRows.map((m) => (
                    <tr key={m.id} style={{ opacity: m.busy ? "0.5" : "1" }}>
                      <td style={{ ...CELL, maxWidth: "520px", lineHeight: "1.5" }}>
                        {m.text}
                        {m.subject ? <div style={{ fontSize: "11px", color: "var(--color-neutral-500)", marginTop: "3px" }}>{"About " + m.subject}</div> : null}
                      </td>
                      <td style={CELL}>
                        <span style={{ fontSize: "10.5px", padding: "2px 8px", borderRadius: "999px", background: (KIND_TONE[m.kindKey] || KIND_TONE.preference)[0], color: (KIND_TONE[m.kindKey] || KIND_TONE.preference)[1], whiteSpace: "nowrap" }}>{m.kind}</span>
                      </td>
                      <td style={{ ...CELL, whiteSpace: "nowrap" }}>{m.scope}</td>
                      <td style={{ ...CELL, whiteSpace: "nowrap" }}>
                        <div>{m.who}</div>
                        <div style={{ fontSize: "11px", color: "var(--color-neutral-500)" }}>{m.when}</div>
                      </td>
                      <td style={{ ...CELL, whiteSpace: "nowrap" }}>
                        <div>{m.used + (m.used === 1 ? " time" : " times")}</div>
                        <div style={{ fontSize: "11px", color: "var(--color-neutral-500)" }}>{"last " + m.lastUsed}</div>
                      </td>
                      <td style={{ ...CELL, textAlign: "right", whiteSpace: "nowrap" }}>
                        <button type="button" onClick={m.forget} disabled={m.busy}
                          style={{ ...QUIET, borderColor: m.armed ? "var(--st-risk)" : "var(--color-divider)", color: m.armed ? "var(--st-risk)" : "var(--color-neutral-400)" }}>
                          {m.forgetLabel}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
      </div>
    </div>
  );
}
