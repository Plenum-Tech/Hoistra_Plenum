// Chat — the conversation with the orchestrator, as a page.
//
// Two columns. The conversation is on the left: your questions, and the engine's answers
// under the engine's name. The run trace is on the right: the sequence of thoughts behind
// whichever turn is in view, and while a question is being answered, the live one.
//
// Splitting them is the point. The route used to live folded inside each answer, which meant
// the reasoning was only readable after the answer had already arrived and only by opening a
// panel on top of it. Beside the conversation it is legible while the run happens, it does
// not push the answer down the page, and an older turn's route can be brought back without
// disturbing the transcript.
//
// `vals` is the view model from useHoistra(); the transcript keys are the same ones the
// compliance console's dock reads.
import React, { useRef } from 'react';
import Markdown from '../components/shell/Markdown.jsx';
import ComplianceAnswer from '../components/shell/ComplianceAnswer.jsx';
import RunTrace from '../components/shell/RunTrace.jsx';
import { useFollowBottom } from '../components/shell/useFollowBottom.js';

const KICKER = { fontSize: "10px", letterSpacing: "0.12em", textTransform: "uppercase", color: "var(--color-neutral-500)" };
// Buttons carry the browser's own font and chrome; these strip it back to the surrounding
// type without giving up the focus ring, keyboard activation or the accessible role.
const BARE = { font: "inherit", background: "transparent", border: "none", padding: "0", margin: "0", cursor: "pointer", color: "inherit" };

const DOMAIN_ICON = {
  Compliance: "ph-shield-check", Energy: "ph-lightning", Vendors: "ph-chart-line-up",
  "Work orders": "ph-wrench", Documents: "ph-file-text"
};

export default function Chat({ vals }) {
  const endRef = useRef(null);
  const count = (vals.orchChat || []).length;
  // Follow the answer as it streams in — the page scrolls, the composer does not. Scrolling
  // up to read an earlier turn pauses the following; the next question resumes it.
  useFollowBottom({ container: null, end: endRef, busy: vals.orchBusy, count: count });

  return (
    <div style={{ flex: "1", display: "flex", justifyContent: "center", padding: "0 32px" }}>
      <div className="chat-grid" style={{ animation: "fadeUp 0.28s ease both" }}>

        {/* ── header, across both columns ────────────────────────────────── */}
        <div className="chat-head">
          <div style={{ display: "flex", alignItems: "center", gap: "12px", padding: "26px 0 0" }}>
            <button type="button" className="hv18" onClick={vals.goHome} style={{ ...BARE, display: "flex", alignItems: "center", gap: "6px", fontSize: "12px", color: "var(--color-neutral-400)" }}>
              <i className="ph ph-arrow-left" style={{ fontSize: "12px" }}></i>
              <span>{"Home"}</span>
            </button>
          </div>
          <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", gap: "16px", flexWrap: "wrap", marginTop: "16px", paddingBottom: "14px", borderBottom: "1px solid var(--color-divider)" }}>
            <div style={{ display: "flex", alignItems: "baseline", gap: "10px", minWidth: "0", flex: "1" }}>
              <span style={{ fontSize: "20px", letterSpacing: "-0.01em", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: "48ch" }} title={vals.chatSessionTitle || ""}>{vals.chatSessionTitle || "Orchestrator"}</span>
              <span style={{ fontSize: "12px", color: "var(--color-neutral-500)", whiteSpace: "nowrap" }}>{vals.chatSessionMeta || "One conversation across the portfolio"}</span>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: "14px" }}>
              {vals.chatCanFile ? (
                <select value={vals.chatFileValue} onChange={vals.chatFileTo} title="File this session in a saved space" style={{ fontFamily: "var(--font-body)", fontSize: "11px", padding: "4px 7px", borderRadius: "6px", border: "1px solid var(--color-divider)", background: "var(--color-surface)", color: "var(--color-neutral-400)", maxWidth: "170px" }}>
                  {(vals.chatFileOptions || []).map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              ) : null}
              <button type="button" title={vals.chatLinkTip} onClick={vals.chatRetry} style={{ ...BARE, display: "flex", alignItems: "center", gap: "6px", fontSize: "11px", color: "var(--color-neutral-500)", whiteSpace: "nowrap" }}>
                <span style={{ width: "6px", height: "6px", borderRadius: "50%", background: vals.chatLinkDot }}></span>
                <span>{vals.chatLinkLabel}</span>
              </button>
              {vals.orchChatHasAny ? (
                <button type="button" className="hv11" onClick={vals.orchChatReset} style={{ ...BARE, fontSize: "11px", color: "var(--color-accent)", whiteSpace: "nowrap" }}>
                  {"New thread"}
                </button>
              ) : null}
            </div>
          </div>
        </div>

        {/* ── left: the conversation ─────────────────────────────────────── */}
        <div className="chat-conv" style={{ display: "flex", flexDirection: "column", minHeight: "calc(100vh - 168px)" }}>
          <div style={{ flex: "1", display: "flex", flexDirection: "column", gap: "16px", padding: "22px 0 18px" }}>
            <div style={{ display: "grid", gridTemplateColumns: "30px minmax(0,1fr)", gap: "12px", alignItems: "start" }}>
              <div style={{ width: "30px", height: "30px", borderRadius: "8px", background: "var(--color-accent-900)", display: "flex", alignItems: "center", justifyContent: "center" }}>
                <i className="ph ph-cpu" style={{ fontSize: "15px", color: "var(--color-accent)" }}></i>
              </div>
              <div style={{ minWidth: "0" }}>
                <div style={KICKER}>{"Orchestrator"}</div>
                <div style={{ fontSize: "13.5px", lineHeight: "1.6", color: "var(--color-neutral-200)", marginTop: "5px", maxWidth: "72ch", textWrap: "pretty" }}>
                  {vals.chatIntro}
                </div>
              </div>
            </div>

            {(vals.orchChat || []).map((m) => (
              <React.Fragment key={m.key}>
                {m.isYou ? (
                  <div style={{ alignSelf: m.editing ? "stretch" : "flex-end", maxWidth: m.editing ? "100%" : "72%", display: "flex", flexDirection: "column", alignItems: m.editing ? "stretch" : "flex-end", gap: "4px" }}>
                    {m.editing ? (
                      <div style={{ border: "1px solid var(--color-accent)", borderRadius: "12px", background: "var(--color-surface)", padding: "12px 14px" }}>
                        <textarea className="input" value={vals.ccEditText} onChange={vals.ccEditSet} onKeyDown={vals.ccEditKey} rows="2" autoFocus style={{ width: "100%", boxSizing: "border-box", fontSize: "13px", lineHeight: "1.5", padding: "0", border: "none", background: "transparent", color: "var(--color-text)", fontFamily: "var(--font-body)", outline: "none", resize: "vertical" }}></textarea>
                        <div style={{ display: "flex", alignItems: "center", gap: "10px", marginTop: "10px" }}>
                          <span style={{ flex: "1", minWidth: "0", fontSize: "10.5px", color: "var(--color-neutral-500)" }}>{"Re-running replaces this question's answer."}</span>
                          <button type="button" className="hv11" onClick={vals.ccEditCancel} style={{ ...BARE, fontSize: "11.5px", color: "var(--color-neutral-500)", whiteSpace: "nowrap" }}>{"Cancel"}</button>
                          <button type="button" className="hv7" onClick={vals.ccEditRun} style={{ ...BARE, fontSize: "11.5px", padding: "6px 12px", borderRadius: "7px", background: "var(--color-text)", color: "var(--color-bg)", whiteSpace: "nowrap" }}>{"Run again"}</button>
                        </div>
                      </div>
                    ) : null}
                    <div style={{ display: m.bubbleShow, padding: "10px 14px", borderRadius: "12px 12px 4px 12px", background: "var(--color-text)", color: "var(--color-bg)", fontSize: "13.5px", lineHeight: "1.5", whiteSpace: "pre-wrap", animation: "fadeUp 0.2s ease both" }}>
                      {m.text}
                      <div style={{ display: m.filesShow, fontFamily: "ui-monospace,monospace", fontSize: "10px", opacity: "0.7", marginTop: "6px" }}>{m.fileNames}</div>
                    </div>
                    <button type="button" className="hv11" onClick={m.edit} title="Edit this question and ask again" style={{ ...BARE, display: m.editShow, alignItems: "center", gap: "4px", fontSize: "10.5px", color: "var(--color-neutral-500)" }}>
                      <i className="ph ph-pencil-simple" style={{ fontSize: "10.5px" }}></i>
                      {"Edit"}
                    </button>
                  </div>
                ) : (
                  <div style={{ display: "grid", gridTemplateColumns: "30px minmax(0,1fr)", gap: "12px", alignItems: "start", animation: "fadeUp 0.25s ease both" }}>
                    <div style={{ width: "30px", height: "30px", borderRadius: "8px", background: m.isNote && m.error ? "var(--st-risk-bg)" : "var(--color-accent-900)", display: "flex", alignItems: "center", justifyContent: "center" }}>
                      <i className={`ph ${m.error ? "ph-warning-circle" : (DOMAIN_ICON[m.domain] || "ph-cpu")}`} style={{ fontSize: "15px", color: m.error ? "var(--st-risk)" : "var(--color-accent)" }}></i>
                    </div>
                    <div style={{ minWidth: "0" }}>
                      <div style={{ display: "flex", alignItems: "baseline", gap: "10px" }}>
                        <span style={KICKER}>{m.domain}</span>
                        {typeof m.ms === "number" ? (
                          <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "9.5px", color: "var(--color-neutral-500)" }}>{(m.ms / 1000).toFixed(m.ms < 10000 ? 1 : 0) + " s"}</span>
                        ) : null}
                        {/* Sends this turn's route to the rail, so an answer read later can
                            still be checked against how it was reached. */}
                        <button type="button" className={m.traceSelected ? "rt-pick is-on" : "rt-pick"} style={{ display: m.traceShow, marginLeft: "auto" }} onClick={m.selectTrace}>
                          <i className="ph ph-path" style={{ fontSize: "10.5px" }}></i>
                          {m.traceSelected ? "In the trace" : "Show the run"}
                        </button>
                      </div>
                      <div style={{ marginTop: "6px", padding: "14px 16px", borderRadius: "4px 12px 12px 12px", background: m.bg, color: m.fg, boxShadow: "var(--shadow-sm)", fontSize: "13px", lineHeight: "1.55", textWrap: "pretty" }}>
                        {m.rich
                          ? <ComplianceAnswer rich={m.rich} ms={m.ms} open={m.stepsOpen} onToggle={m.toggleSteps} fallbackText={m.text} />
                          : m.isNote
                            ? <div style={{ display: "grid", gridTemplateColumns: "14px minmax(0,1fr)", gap: "8px", alignItems: "start" }}>
                                <i className={`ph ${m.error ? "ph-warning-circle" : "ph-check-circle"}`} style={{ fontSize: "13px", color: m.error ? "var(--st-risk)" : "var(--st-ok)", marginTop: "3px" }}></i>
                                <span>{m.text}</span>
                              </div>
                            : <Markdown text={m.text} />}
                        <div style={{ display: m.interruptShow, fontSize: "10.5px", color: "var(--color-accent)", marginTop: "8px" }}>{"Paused for approval before finishing."}</div>
                        <div style={{ display: m.stoppedShow, fontSize: "10.5px", color: "var(--color-neutral-500)", marginTop: "6px" }}>{"Stopped."}</div>
                        <div style={{ display: m.toolsShow, fontFamily: "ui-monospace,monospace", fontSize: "10px", color: "var(--color-neutral-500)", marginTop: "10px", paddingTop: "8px", borderTop: "1px solid var(--color-divider)" }}>{m.tools}</div>
                      </div>
                    </div>
                  </div>
                )}
              </React.Fragment>
            ))}

            {/* The answer being written. Its route is in the rail, so this stays a place
                for the answer's own zones to land as they arrive. */}
            {vals.orchBusy ? (
              <div style={{ display: "grid", gridTemplateColumns: "30px minmax(0,1fr)", gap: "12px", alignItems: "start" }}>
                <div style={{ width: "30px", height: "30px", borderRadius: "8px", background: "var(--color-accent-900)", display: "flex", alignItems: "center", justifyContent: "center" }}>
                  <i className="ph ph-cpu" style={{ fontSize: "15px", color: "var(--color-accent)" }}></i>
                </div>
                <div style={{ minWidth: "0" }}>
                  <div style={KICKER}>{"Answering"}</div>
                  {/* Zones land here one at a time. Until the first arrives there is nothing
                      to show, and a bare label reads as a stall — so say where the run is. */}
                  {vals.orchLiveShow === "none" ? (
                    <div style={{ marginTop: "6px", fontSize: "12.5px", lineHeight: "1.5", color: "var(--color-neutral-500)" }}>
                      {"Building the answer. The run trace shows where it has got to."}
                    </div>
                  ) : (
                    <div style={{ marginTop: "6px", padding: "14px 16px", borderRadius: "4px 12px 12px 12px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", fontSize: "13px" }}>
                      <ComplianceAnswer rich={vals.orchLiveRich} open={false} onToggle={() => {}} />
                    </div>
                  )}
                </div>
              </div>
            ) : null}
            <div ref={endRef}></div>
          </div>

          {/* ── composer ─────────────────────────────────────────────────── */}
          <div style={{ position: "sticky", bottom: "0", background: "var(--color-bg)", padding: "10px 0 18px", borderTop: "1px solid var(--color-divider)" }}>
            <div style={{ display: vals.orchAttachShow, flexWrap: "wrap", gap: "6px", marginBottom: "8px" }}>
              {(vals.orchFiles || []).map((f) => (
                <React.Fragment key={f.key}>
                  <span style={{ display: "inline-flex", alignItems: "center", gap: "6px", padding: "5px 9px", borderRadius: "7px", border: "1px solid var(--color-divider)", background: "var(--color-surface)", fontSize: "11px", maxWidth: "100%" }}>
                    <i className={`ph ${f.icon}`} style={{ fontSize: "12px", color: "var(--color-accent)", flexShrink: "0" }}></i>
                    <span style={{ minWidth: "0", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{f.name}</span>
                    <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "9.5px", color: "var(--color-neutral-500)", flexShrink: "0" }}>{f.size}</span>
                    <button type="button" className="hv21" onClick={f.drop} title={"Remove " + f.name} style={{ ...BARE, display: "flex", opacity: "0.6", flexShrink: "0" }}>
                      <i className="ph ph-x" style={{ fontSize: "10px" }}></i>
                    </button>
                  </span>
                </React.Fragment>
              ))}
            </div>
            <div style={{ display: "flex", gap: "8px", alignItems: "stretch" }}>
              <label className="hv13" title="Attach documents or photos — CSV and Excel go to migration, PDF, Word and images are indexed for search" style={{ display: "flex", width: "42px", flexShrink: "0", borderRadius: "10px", border: "1px solid var(--color-divider)", background: "var(--color-surface)", alignItems: "center", justifyContent: "center", cursor: "pointer" }}>
                <i className="ph ph-paperclip" style={{ fontSize: "15px", color: "var(--color-neutral-400)" }}></i>
                <input type="file" multiple accept=".pdf,.doc,.docx,.csv,.xls,.xlsx,image/*" onChange={vals.orchPickFiles} style={{ display: "none" }} />
              </label>
              <input id="chat-composer" className="input" value={vals.orchQuery} onChange={vals.setOrchQuery} onKeyDown={vals.orchKey} placeholder={vals.orchPlaceholder} aria-label="Message the orchestrator" autoFocus style={{ flex: "1", minWidth: "0", fontSize: "14px", padding: "11px 14px", borderRadius: "10px", border: "1px solid var(--color-divider)", background: "var(--color-surface)", color: "var(--color-text)", fontFamily: "var(--font-body)", outline: "none", boxShadow: "var(--shadow-sm)" }} />
              <button type="button" className="hv7" onClick={vals.orchSendClick} title={vals.orchSendTitle} aria-label={vals.orchSendTitle} style={{ ...BARE, width: "42px", borderRadius: "10px", background: vals.orchSendBg, color: "var(--accent-ink)", display: "flex", alignItems: "center", justifyContent: "center", flexShrink: "0" }}>
                <i className={`ph ${vals.orchSendIcon}`} style={{ fontSize: "15px" }}></i>
              </button>
            </div>
            <div style={{ fontSize: "10.5px", color: "var(--color-neutral-500)", lineHeight: "1.45", marginTop: "8px" }}>
              {"Every question is stored as a session, whichever page it was raised from. Nothing is written back without your decision."}
            </div>
          </div>
        </div>

        {/* ── right: the sequence of thoughts ────────────────────────────── */}
        <RunTrace vals={vals} />
      </div>
    </div>
  );
}
