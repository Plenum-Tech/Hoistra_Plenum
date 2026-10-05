// CorrectionDrawer — teach the agent from a span of the run (logic/corrections.js). Opens over
// the trace rail: what ran, the common corrections as choices, a before -> after preview, and
// two ways out — re-answer now (a new traced turn) or save as a teaching the chat recalls.
//
// Rendered through a portal into <body>. Chat.jsx mounts it inside .chat-grid, whose fadeUp
// entrance animation (fill-mode both) leaves a transform in effect, and a transformed ancestor
// becomes the containing block for position:fixed. The drawer then spanned the whole
// conversation instead of the window: its actions sat at the bottom of the chat, below a
// stretch of empty panel. In <body> it is the window's height, so the note and the actions
// stay pinned while only the evidence scrolls.
import React, { useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';

const KICK = { fontSize: "10.5px", letterSpacing: "0.09em", textTransform: "uppercase", color: "var(--color-neutral-500)" };
const MONO = { fontFamily: "ui-monospace,monospace" };
const QUIET = { fontSize: "12px", padding: "7px 12px", borderRadius: "7px", border: "1px solid var(--color-divider)", background: "transparent", color: "var(--color-neutral-400)", cursor: "pointer" };
// The evidence grows with the window instead of stopping at a fixed 220px.
const PRE = { ...MONO, fontSize: "11.5px", lineHeight: "1.5", margin: "6px 0 0", padding: "10px 12px", borderRadius: "8px", background: "var(--color-bg)", color: "var(--color-text)", whiteSpace: "pre-wrap", wordBreak: "break-word", maxHeight: "36vh", overflow: "auto" };
const FIELD = { width: "100%", boxSizing: "border-box", fontSize: "12.5px", padding: "8px 10px", borderRadius: "7px", border: "1px solid var(--color-divider)", background: "var(--color-surface)", color: "var(--color-text)", fontFamily: "inherit" };
// Modes that show what ran. "suggestion" (from under an answer) has nothing to show, so its
// note leads the body rather than sitting pinned beneath an empty panel.
const EVIDENCE = ["query", "tool", "plan", "route", "agent", "model"];
const chip = (on) => ({ fontSize: "11.5px", padding: "4px 10px", borderRadius: "999px", cursor: "pointer", border: "1px solid " + (on ? "var(--st-risk)" : "var(--color-divider)"), background: on ? "var(--st-risk-bg, transparent)" : "transparent", color: on ? "var(--st-risk)" : "var(--color-neutral-400)" });
const SELECT = { fontSize: "12px", padding: "6px 8px", borderRadius: "7px", border: "1px solid var(--color-divider)", background: "var(--color-surface)", color: "var(--color-text)" };

export default function CorrectionDrawer({ vals }) {
  const show = !!vals.crShow;
  const noteRef = useRef(null);
  // vals.crClose is a fresh function every render; read it through a ref so the effect runs on
  // open and close only — re-running it per keystroke would pull focus back to the note.
  const closeRef = useRef(vals.crClose);
  closeRef.current = vals.crClose;
  // Escape closes; the note takes focus on open so the correction can be typed straight away.
  useEffect(() => {
    if (!show) return undefined;
    const t = setTimeout(() => { if (noteRef.current) noteRef.current.focus({ preventScroll: true }); }, 0);
    const onKey = (e) => { if (e.key === "Escape" && closeRef.current) closeRef.current(); };
    document.addEventListener("keydown", onKey);
    return () => { clearTimeout(t); document.removeEventListener("keydown", onKey); };
  }, [show]);
  if (!show || typeof document === "undefined") return null;
  const pv = vals.crPreview;
  const evidence = EVIDENCE.indexOf(vals.crMode) > -1;
  const stage = vals.crMode === "agent" || vals.crMode === "model";

  const note = (
    <div>
      <label htmlFor="cr-note" style={{ ...KICK, display: "block" }}>{vals.crMode === "plan" ? "How the plan should change" : vals.crMode === "route" ? "Or say why" : stage ? "What this agent should do differently" : evidence ? "Or say what was wrong" : "What was wrong"}</label>
      <textarea id="cr-note" ref={noteRef} value={vals.crNote} onChange={vals.crSetNote} rows={evidence ? 2 : 5} placeholder={vals.crMode === "plan" ? "Use get_cost_savings directly instead of the maintenance engine for step 2…" : stage ? "Always include building certificates (DEC, FRA) in lapsed counts, not only vendor accreditations…" : "Cancelled jobs are not raised work; the DEC for Manchester Town Hall is missing…"}
        style={{ ...FIELD, marginTop: "6px", resize: "vertical", minHeight: "52px", maxHeight: "30vh", lineHeight: "1.45" }} />
      <input aria-label="Why" value={vals.crWhy} onChange={vals.crSetWhy} placeholder="Why — kept as the rule (optional)"
        style={{ ...FIELD, marginTop: "6px", fontSize: "12px", padding: "7px 10px" }} />
    </div>
  );

  return createPortal(
    <div role="dialog" aria-modal="true" aria-label={vals.crTitle} style={{ position: "fixed", top: "0", right: "0", bottom: "0", width: "min(600px, 100vw)", zIndex: "70", background: "var(--color-surface)", boxShadow: "var(--shadow-md)", borderLeft: "1px solid var(--color-divider)", display: "flex", flexDirection: "column", animation: "fadeUp 0.2s ease both" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: "12px", padding: "16px 20px 12px", borderBottom: "1px solid var(--color-divider)", flexShrink: "0" }}>
        <div style={{ minWidth: "0" }}>
          <div style={KICK}>{"Teach the agent"}</div>
          <h3 style={{ margin: "4px 0 0", fontSize: "17px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{vals.crTitle}</h3>
          <div style={{ fontSize: "11.5px", color: "var(--color-neutral-500)", marginTop: "3px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{vals.crQuestion}{vals.crBuilding ? " · " + vals.crBuilding : ""}</div>
        </div>
        <button type="button" onClick={vals.crClose} title="Close (Esc)" style={QUIET}>{"Close"}</button>
      </div>

      <div style={{ padding: "14px 20px 18px", overflowY: "auto", overscrollBehavior: "contain", flex: "1 1 auto", minHeight: "0", fontSize: "12.5px" }}>
        {!evidence ? note : null}
        {vals.crMode === "query" ? (
          <>
            <div style={KICK}>{"The query that ran"}</div>
            <pre style={PRE}>{vals.crSql || "—"}</pre>
            {vals.crParams ? <div style={{ ...MONO, fontSize: "11px", color: "var(--color-neutral-500)", marginTop: "4px" }}>{"params " + vals.crParams}</div> : null}
          </>
        ) : vals.crMode === "tool" ? (
          <>
            <div style={KICK}>{"What the tool was asked"}</div>
            <pre style={PRE}>{vals.crToolInput || "—"}</pre>
            <div style={{ ...KICK, marginTop: "14px" }}>{"What it returned"}</div>
            <pre style={PRE}>{vals.crToolOutput || "—"}</pre>
          </>
        ) : vals.crMode === "plan" ? (
          <>
            <div style={KICK}>{"The plan that ran"}</div>
            <pre style={PRE}>{vals.crPlanText || "—"}</pre>
          </>
        ) : vals.crMode === "route" ? (
          <>
            <div style={KICK}>{"The route the orchestrator took"}</div>
            <pre style={PRE}>{vals.crRouteText || "—"}</pre>
          </>
        ) : stage ? (
          <>
            <div style={KICK}>{"What this stage was given"}</div>
            <pre style={PRE}>{vals.crStageInput || "—"}</pre>
            <div style={{ ...KICK, marginTop: "14px" }}>{"What it produced"}</div>
            <pre style={PRE}>{vals.crStageOutput || "—"}</pre>
          </>
        ) : null}

        {(vals.crRoutes || []).length ? (
          <div style={{ marginTop: "14px" }}>
            <div style={KICK}>{vals.crMode === "plan" ? "Where it should have gone instead" : "Which engine should have had it"}</div>
            <div style={{ display: "flex", gap: "6px", flexWrap: "wrap", marginTop: "6px" }}>
              {vals.crRoutes.map((e) => <button key={e.key} type="button" onClick={e.pick} style={{ fontSize: "11.5px", padding: "4px 10px", borderRadius: "999px", cursor: "pointer", border: "1px solid " + (e.on ? "var(--color-accent)" : "var(--color-divider)"), background: e.on ? "var(--color-accent-900)" : "transparent", color: e.on ? "var(--color-accent)" : "var(--color-neutral-400)" }}>{e.label}</button>)}
            </div>
          </div>
        ) : null}

        {vals.crStatuses.length ? (
          <div style={{ marginTop: "14px" }}>
            <div style={KICK}>{"Exclude a status"}</div>
            <div style={{ display: "flex", gap: "6px", flexWrap: "wrap", marginTop: "6px" }}>
              {vals.crStatuses.map((st) => <button key={st.status} type="button" onClick={st.toggle} style={chip(st.on)}>{(st.on ? "× " : "") + st.status}</button>)}
            </div>
          </div>
        ) : null}

        {vals.crMode !== "plan" && vals.crMode !== "route" && !stage ? (
          <div style={{ display: "flex", gap: "8px", marginTop: "14px", flexWrap: "wrap" }}>
            <select aria-label="Period" value={vals.crPeriod} onChange={vals.crSetPeriod} style={SELECT}>{vals.crPeriods.map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}</select>
            <select aria-label="Date field" value={vals.crField} onChange={vals.crSetField} style={SELECT}>{vals.crFields.map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}</select>
          </div>
        ) : null}

        {pv ? (
          <div style={{ marginTop: "14px" }}>
            <div style={KICK}>{"Before → after · " + pv.column}</div>
            <table style={{ width: "100%", borderCollapse: "collapse", marginTop: "6px", ...MONO, fontSize: "11.5px" }}>
              <tbody>
                {pv.lines.map((l) => (
                  <tr key={l.status} style={{ opacity: l.excluded ? "0.55" : "1" }}>
                    <td style={{ padding: "4px 0", borderBottom: "1px solid var(--color-divider)" }}>{l.status}</td>
                    <td style={{ padding: "4px 0", borderBottom: "1px solid var(--color-divider)", textAlign: "right" }}>{l.before}</td>
                    <td style={{ padding: "4px 0 4px 10px", borderBottom: "1px solid var(--color-divider)", textAlign: "right", color: l.excluded ? "var(--st-risk)" : "var(--color-text)" }}>{"→ " + l.after}</td>
                  </tr>
                ))}
                <tr><td style={{ padding: "6px 0", fontWeight: "600" }}>{"Total"}</td><td style={{ padding: "6px 0", textAlign: "right" }}>{pv.before}</td><td style={{ padding: "6px 0 6px 10px", textAlign: "right", color: pv.changed ? "var(--color-accent)" : "var(--color-text)", fontWeight: "600" }}>{"→ " + pv.after}</td></tr>
              </tbody>
            </table>
            <div style={{ fontSize: "11px", color: "var(--color-neutral-500)", marginTop: "4px" }}>{"Computed from the rows this query returned; a period or date-field change is previewed by re-answering."}</div>
          </div>
        ) : null}

        {vals.crText ? (
          <div style={{ marginTop: "14px", padding: "10px 12px", borderRadius: "8px", background: "var(--color-accent-900)", color: "var(--color-accent)", fontSize: "12px", lineHeight: "1.5" }}>
            <span style={{ ...KICK, color: "var(--color-accent)" }}>{"The correction, as it will be sent"}</span><br />{vals.crText}
          </div>
        ) : null}
      </div>

      {/* Pinned: the note (when there is evidence above it) and the three ways out. */}
      <div style={{ flexShrink: "0", borderTop: "1px solid var(--color-divider)", padding: "12px 20px 14px", display: "flex", flexDirection: "column", gap: "10px", background: "var(--color-surface)" }}>
        {evidence ? note : null}
        {vals.crMsg ? <div role="status" style={{ fontSize: "12px", color: vals.crMsg.indexOf("Could not") === 0 || vals.crMsg.indexOf("Choose") === 0 ? "var(--st-risk)" : "var(--st-ok)" }}>{vals.crMsg}</div> : null}
        <div style={{ display: "flex", gap: "8px", alignItems: "center", flexWrap: "wrap" }}>
          <button type="button" className="btn btn-primary" onClick={vals.crRerun} disabled={vals.crBusy} title="The same steps, corrected, and remembered for similar questions" style={{ fontSize: "12px", padding: "7px 14px", cursor: "pointer" }}>{"Re-run the steps"}</button>
          <button type="button" onClick={vals.crReanswer} disabled={vals.crBusy} title="The orchestrator decides again" style={QUIET}>{"Re-answer freely"}</button>
          <button type="button" onClick={vals.crTeach} disabled={vals.crBusy} title="Remembered only, nothing re-runs" style={{ ...QUIET, borderColor: "var(--color-accent)", color: "var(--color-accent)" }}>{vals.crBusy ? "Saving…" : "Save as teaching"}</button>
        </div>
        <div style={{ fontSize: "11px", lineHeight: "1.45", color: "var(--color-neutral-500)" }}>{"Re-run: the same steps, corrected, and remembered for similar questions · Re-answer: the orchestrator decides again · Teaching: remembered only"}</div>
      </div>
    </div>,
    document.body
  );
}
