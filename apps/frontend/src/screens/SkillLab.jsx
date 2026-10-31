// SkillLab — Administration › Skill lab (logic/skillLabPage.js). Agent instructions measured on
// this company's own questions, replayed with nothing written: how complete the answers stay when
// the agent manages its own context, what that saves, and rewrites waiting for approval.
import React from 'react';

const CARD = { borderRadius: "10px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)" };
const KICK = { fontSize: "10.5px", letterSpacing: "0.09em", textTransform: "uppercase", color: "var(--color-neutral-500)" };
const QUIET = { fontSize: "12px", padding: "7px 12px", borderRadius: "7px", border: "1px solid var(--color-divider)", background: "transparent", color: "var(--color-neutral-400)", cursor: "pointer" };
const MAIN = { ...QUIET, border: "1px solid var(--color-accent)", color: "var(--color-accent)" };
const CELL = { padding: "8px 10px", borderBottom: "1px solid var(--color-divider)", fontSize: "12px", verticalAlign: "top" };
const PRE = { whiteSpace: "pre-wrap", fontSize: "11.5px", lineHeight: "1.5", margin: "0", padding: "10px", borderRadius: "7px", background: "var(--color-bg)", maxHeight: "320px", overflow: "auto" };

export default function SkillLab({ vals }) {
  return (
    <div style={{ maxWidth: "1180px", margin: "0 auto", padding: "0 24px 60px" }}>
      <div style={{ display: "flex", alignItems: "center", gap: "8px", padding: "24px 0 0", fontSize: "12px", color: "var(--color-neutral-500)" }}>
        <button type="button" onClick={vals.goHome} style={{ ...QUIET, border: "none", padding: "0", display: "flex", alignItems: "center", gap: "6px" }}>
          <i className="ph ph-arrow-left" style={{ fontSize: "12px" }}></i>{"Home"}
        </button>
        <span>{"/"}</span><span>{"Administration"}</span><span>{"/"}</span><span style={{ color: "var(--color-text)" }}>{"Skill lab"}</span>
      </div>

      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-end", gap: "16px", marginTop: "14px", flexWrap: "wrap" }}>
        <div>
          <h2 style={{ margin: "0", fontSize: "24px" }}>{"Skill lab"}</h2>
          <p style={{ margin: "6px 0 0", fontSize: "12.5px", color: "var(--color-neutral-500)", maxWidth: "760px", lineHeight: "1.5" }}>
            {"Questions this company asked, replayed through the real agents with nothing written and nothing sent. "
              + "Measure compares answers where the agent manages its own context with answers that keep every result in view. "
              + "Tune also writes a better version of the context instructions and keeps it only if it wins on questions it was not written from. Nothing changes for the agents until you approve it."}
          </p>
        </div>
        <div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
          <label style={{ fontSize: "12px", color: "var(--color-neutral-500)" }}>{"Questions per split "}
            <input type="number" min="1" max="10" value={vals.slSample} onChange={vals.slSetSample} aria-label="Questions per split"
              style={{ width: "52px", marginLeft: "6px", fontSize: "12px", padding: "6px", borderRadius: "6px", border: "1px solid var(--color-divider)", background: "var(--color-surface)", color: "var(--color-text)" }} />
          </label>
          <button type="button" onClick={vals.slMeasure} disabled={vals.slRunning || !!vals.slStarting} style={MAIN}>{vals.slStarting === 'compare' ? "Starting…" : "Measure"}</button>
          <button type="button" onClick={vals.slTune} disabled={vals.slRunning || !!vals.slStarting} style={MAIN}>{vals.slStarting === 'optimise' ? "Starting…" : "Tune"}</button>
          <button type="button" onClick={vals.slReload} style={QUIET}>{"Refresh"}</button>
        </div>
      </div>

      {vals.slError ? <div style={{ ...CARD, marginTop: "16px", padding: "14px 16px", fontSize: "12.5px", color: "var(--st-risk)" }}>{"Could not read the skill lab: " + vals.slError}</div> : null}
      {vals.slRunning ? <div style={{ ...CARD, marginTop: "16px", padding: "12px 16px", fontSize: "12.5px", color: "var(--st-warn)" }}>{"A run is replaying questions. This page refreshes every 15 seconds until it finishes."}</div> : null}

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(190px,1fr))", gap: "12px", marginTop: "18px" }}>
        {vals.slTiles.map((t) => (
          <div key={t.label} style={{ ...CARD, padding: "14px 16px" }}>
            <div style={{ fontSize: "24px", fontWeight: "600", color: t.tone, lineHeight: "1" }}>{t.value}</div>
            <div style={{ ...KICK, marginTop: "8px" }}>{t.label}</div>
            <div style={{ fontSize: "11.5px", color: "var(--color-neutral-500)", marginTop: "3px" }}>{t.hint}</div>
          </div>
        ))}
      </div>

      {vals.slProposals.map((p) => (
        <div key={p.id} style={{ ...CARD, marginTop: "16px", padding: "14px 16px", borderLeft: "3px solid var(--st-warn)" }}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: "12px", flexWrap: "wrap", alignItems: "center" }}>
            <div>
              <div style={KICK}>{"Rewrite waiting for approval · " + p.doc + " · " + p.when}</div>
              <div style={{ fontSize: "13px", marginTop: "4px" }}>{p.verdict}</div>
              {p.rationale ? <div style={{ fontSize: "12px", color: "var(--color-neutral-500)", marginTop: "2px" }}>{p.rationale}</div> : null}
            </div>
            <div style={{ display: "flex", gap: "8px" }}>
              <button type="button" onClick={p.approve} style={MAIN}>{"Approve"}</button>
              <button type="button" onClick={p.reject} style={QUIET}>{"Reject"}</button>
            </div>
          </div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(320px,1fr))", gap: "10px", marginTop: "10px" }}>
            <div><div style={{ ...KICK, marginBottom: "4px" }}>{"Now"}</div><pre style={PRE}>{p.current}</pre></div>
            <div><div style={{ ...KICK, marginBottom: "4px" }}>{"Proposed"}</div><pre style={PRE}>{p.content}</pre></div>
          </div>
        </div>
      ))}

      {vals.slActive ? (
        <div style={{ ...CARD, marginTop: "16px", padding: "12px 16px", display: "flex", justifyContent: "space-between", alignItems: "center", gap: "12px", fontSize: "12.5px" }}>
          <span>{"The agents read tuned instructions for " + vals.slActive.doc + ", approved " + vals.slActive.when + (vals.slActive.by ? " by " + vals.slActive.by : "") + "."}</span>
          <button type="button" onClick={vals.slActive.revert} style={QUIET}>{"Back to shipped"}</button>
        </div>
      ) : null}

      <div style={{ ...KICK, marginTop: "22px" }}>{"Runs"}</div>
      <div style={{ ...CARD, marginTop: "8px", overflowX: "auto" }}>
        {vals.slLoading ? <div style={{ padding: "18px", fontSize: "12.5px", color: "var(--color-neutral-500)" }}>{"Reading the runs…"}</div>
          : vals.slEmpty ? <div style={{ padding: "18px", fontSize: "12.5px", color: "var(--color-neutral-500)", lineHeight: "1.6" }}>{"No runs yet. Measure replays up to the chosen number of this company's questions twice each - once keeping every result in view, once with the agent managing its own context - and scores how much the second kept."}</div>
            : vals.slRuns.map((r) => (
              <div key={r.id} style={{ borderBottom: "1px solid var(--color-divider)" }}>
                <button type="button" onClick={r.toggle} style={{ display: "flex", gap: "12px", width: "100%", textAlign: "left", padding: "12px 14px", background: "transparent", border: "none", color: "inherit", cursor: "pointer", font: "inherit", alignItems: "baseline" }}>
                  <i className={"ph " + (r.open ? "ph-caret-down" : "ph-caret-right")} style={{ fontSize: "11px", color: "var(--color-neutral-500)" }}></i>
                  <span style={{ fontWeight: "600", fontSize: "12.5px", minWidth: "60px" }}>{r.kind}</span>
                  <span style={{ fontSize: "12.5px", color: r.tone, flex: "1" }}>{r.headline}</span>
                  <span style={{ fontSize: "11.5px", color: "var(--color-neutral-500)", whiteSpace: "nowrap" }}>{r.when + (r.who ? " · " + r.who : "")}</span>
                </button>
                {r.open ? (vals.slRunLoading ? <div style={{ padding: "0 14px 12px", fontSize: "12px", color: "var(--color-neutral-500)" }}>{"Reading the answers…"}</div> : (
                  <table style={{ width: "100%", borderCollapse: "collapse", margin: "0 0 8px" }}>
                    <thead><tr>{["Question", "Split", "Run", "Kept", "Tokens", "Largest prompt", "Cost", "Compactions", "What was lost"].map((h) => <th key={h} style={{ ...CELL, ...KICK, textAlign: "left" }}>{h}</th>)}</tr></thead>
                    <tbody>{vals.slRunRows.map((x) => (
                      <tr key={x.key}>
                        <td style={{ ...CELL, maxWidth: "260px" }}>{x.q}</td><td style={CELL}>{x.split}</td><td style={CELL}>{x.variant}</td>
                        <td style={CELL}>{x.score}</td><td style={CELL}>{x.tokens}</td><td style={CELL}>{x.peak}</td><td style={CELL}>{x.cost}</td>
                        <td style={CELL}>{x.compactions + (x.cut ? " · " + x.cut + " cut" : "")}</td>
                        <td style={{ ...CELL, maxWidth: "320px", color: x.error ? "var(--st-risk)" : "var(--color-neutral-400)" }}>{x.error || x.lost || "—"}</td>
                      </tr>
                    ))}</tbody>
                  </table>
                )) : null}
              </div>
            ))}
      </div>
    </div>
  );
}
