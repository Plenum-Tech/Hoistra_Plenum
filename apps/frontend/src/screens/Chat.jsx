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
import MigrationRun from '../components/shell/MigrationRun.jsx';
import HoistBuildingCard from '../components/shell/HoistBuildingCard.jsx';
import { useFollowBottom } from '../components/shell/useFollowBottom.js';
import { useTraceSpy } from '../components/shell/useTraceSpy.js';

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
  // Which turn's trace the rail shows follows the reader's scroll position, not just
  // whichever question finished most recently — see useTraceSpy for how.
  useTraceSpy(vals.orchTraceFollow, [count, vals.orchBusy]);

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
                  <div data-trace-turn={m.traceShow === "inline-flex" ? m.key : undefined} style={{ display: "grid", gridTemplateColumns: "30px minmax(0,1fr)", gap: "12px", alignItems: "start", animation: "fadeUp 0.25s ease both" }}>
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
                        <div style={{ display: m.migShow, gap: "8px", flexWrap: "wrap", marginTop: "10px" }}>
                          {(m.migIds || []).map((g) => (
                            <button key={g.id} type="button" className="hv13" onClick={g.open} title={g.id} style={{ ...BARE, fontSize: "11.5px", padding: "5px 10px", borderRadius: "7px", border: "1px solid var(--color-accent)", color: "var(--color-accent)", display: "inline-flex", alignItems: "center", gap: "6px" }}>
                              <i className="ph ph-file-arrow-up" style={{ fontSize: "12px" }}></i>
                              {"Open migration " + g.short + " — review its gates"}
                            </button>
                          ))}
                        </div>
                        {/* "migrations" — the runs this company has started, each one a
                            door back into the gates it stopped at. */}
                        <div style={{ display: m.mgListShow, flexDirection: "column", gap: "5px", marginTop: "10px" }}>
                          {/* A list that could not be read says why. Without this a failed
                              read looks exactly like a company with no migrations. */}
                          {vals.mgRecentNote ? (
                            <div style={{ fontSize: "11px", color: "var(--color-neutral-500)" }}>{vals.mgRecentNote}</div>
                          ) : null}
                          {(vals.mgRecent || []).map((r) => (
                            <button key={r.id} type="button" className="hv13" onClick={r.open} title={r.id}
                              style={{ ...BARE, display: "flex", alignItems: "center", gap: "10px", padding: "7px 10px", borderRadius: "8px", border: "1px solid " + (r.active ? "var(--color-accent)" : "var(--color-divider)"), textAlign: "left", width: "100%" }}>
                              <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10.5px", color: "var(--color-neutral-500)" }}>{r.short}</span>
                              <span style={{ fontSize: "11.5px" }}>{r.cmms}</span>
                              <span style={{ flex: "1" }}></span>
                              <span style={{ fontSize: "10.5px", color: r.tone }}>{r.status}</span>
                              <span style={{ fontSize: "10.5px", color: "var(--color-neutral-500)" }}>{r.when}</span>
                            </button>
                          ))}
                        </div>
                        <div style={{ display: m.stoppedShow, fontSize: "10.5px", color: "var(--color-neutral-500)", marginTop: "6px" }}>{"Stopped."}</div>
                        <div style={{ display: m.toolsShow, fontFamily: "ui-monospace,monospace", fontSize: "10px", color: "var(--color-neutral-500)", marginTop: "10px", paddingTop: "8px", borderTop: "1px solid var(--color-divider)" }}>{m.tools}</div>
                      </div>
                    </div>
                  </div>
                )}
              </React.Fragment>
            ))}

            {/* Hoisting a building is an instruction to the platform, so it is carried out
                where the instruction was given rather than on a page of its own. */}
            {vals.bcOpen ? <HoistBuildingCard vals={vals} /> : null}

            {/* And the step that follows it. "Ingest documents now" on step 3 arms this
                flow and closes the hoist card; the dock rendered it and the Orchestrator
                did not, so on this page the button used to leave the reader in front of
                nothing — the card went away and no panel replaced it. */}
            {vals.fIngest ? (
              <div style={{ marginTop: "18px", padding: "14px 16px", borderRadius: "12px", background: "var(--color-surface)", border: "1px solid var(--color-accent)", animation: "fadeUp 0.25s ease both" }}>
                <div style={{ fontSize: "10px", letterSpacing: "0.12em", textTransform: "uppercase", color: "var(--color-accent)" }}>{"Ingest documents"}</div>
                <div style={{ display: "flex", flexDirection: "column", gap: "4px", marginTop: "11px", maxWidth: "460px" }}>
                  <span style={KICKER}>{"Which building"}</span>
                  {/* A real blank choice — without one the browser shows the first building
                      selected the moment the list has options, whether or not one was
                      actually picked. */}
                  <select className="input" value={vals.iBuilding} onChange={vals.setIBuilding} style={{ width: "100%", boxSizing: "border-box", fontSize: "12px", padding: "7px 9px", borderRadius: "7px", border: "1px solid var(--color-divider)", background: "var(--color-bg)", color: "var(--color-text)", fontFamily: "var(--font-body)" }}>
                    <option value="" disabled>{"Choose a building…"}</option>
                    {(vals.iBuildingOpts || []).map((o, i) => <option key={i} value={o}>{o}</option>)}
                  </select>
                </div>
                <div style={{ fontSize: "11px", color: "var(--color-neutral-500)", lineHeight: "1.5", marginTop: "10px", maxWidth: "80ch" }}>
                  {"Everything ingested carries that building's ID as a foreign key. Certificates, contracts, asset registers, meter data or invoices — the orchestrator reads each one and files it where it belongs."}
                </div>
                <label className="hv13" style={{ display: "flex", alignItems: "center", gap: "8px", marginTop: "11px", padding: "9px 11px", borderRadius: "8px", border: "1px dashed var(--color-divider)", cursor: "pointer", maxWidth: "460px" }}>
                  <i className="ph ph-file-arrow-up" style={{ fontSize: "13px", color: "var(--color-accent)", flexShrink: "0" }}></i>
                  <span style={{ fontSize: "11.5px", color: "var(--color-neutral-400)" }}>
                    {vals.orchFileCount
                      ? vals.orchFileCount + (vals.orchFileCount === 1 ? " document attached — add another…" : " documents attached — add another…")
                      : "Attach the documents…"}
                  </span>
                  <input type="file" multiple accept=".pdf,.doc,.docx,.csv,.xls,.xlsx,image/*" onChange={vals.orchPickFiles} style={{ display: "none" }} />
                </label>
                {/* The staged files show once, in the composer's tray below: this panel and
                    the composer share one list. */}
                {vals.iHint ? <div style={{ fontSize: "10.5px", color: "var(--color-neutral-500)", lineHeight: "1.45", marginTop: "9px" }}>{vals.iHint}</div> : null}
                <div style={{ display: "flex", gap: "8px", marginTop: "13px" }}>
                  <button type="button" className="hv7" onClick={vals.iRun} style={{ ...BARE, fontSize: "12px", padding: "8px 16px", borderRadius: "8px", background: "var(--color-accent)", color: "var(--accent-ink)", cursor: vals.iCanRun ? "pointer" : "default", opacity: vals.iCanRun ? "1" : "0.5" }}>
                    {"Start ingestion"}
                  </button>
                  <button type="button" className="hv11" onClick={vals.fCancel} style={{ ...BARE, fontSize: "12px", padding: "8px 12px", borderRadius: "8px", border: "1px solid var(--color-divider)", color: "var(--color-neutral-500)" }}>
                    {"Cancel"}
                  </button>
                </div>
              </div>
            ) : null}

            {/* What a finished flow left behind — the hoist's "keyed as B-42, no documents
                ingested yet" line, and the same for the flows the dock reports this way. */}
            {vals.fDone ? (
              <div style={{ marginTop: "18px", padding: "12px 14px", borderRadius: "10px", background: "var(--st-ok-bg)", display: "flex", gap: "10px", alignItems: "flex-start" }}>
                <i className="ph ph-check-circle" style={{ fontSize: "15px", color: "var(--st-ok)", marginTop: "1px" }}></i>
                <span style={{ fontSize: "12px", lineHeight: "1.55", color: "var(--color-text)" }}>{vals.fDoneText}</span>
              </div>
            ) : null}

            {/* A migration started from this conversation is answered in it — every gate,
                up to and including the write. */}
            {vals.mgHasRun ? <MigrationRun vals={vals} /> : null}

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
            <div style={{ display: vals.orchAttachShow || "none", flexWrap: "wrap", gap: "6px", marginBottom: "8px" }}>
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
            <div style={{ display: vals.orchMigrateShow || "none", alignItems: "center", gap: "9px", flexWrap: "wrap", marginBottom: "9px", padding: "8px 11px", borderRadius: "9px", border: "1px solid var(--color-divider)", background: "var(--color-surface)" }}>
              <i className="ph ph-file-xls" style={{ fontSize: "14px", color: "var(--color-accent)", flexShrink: "0" }}></i>
              <span style={{ flex: "1", minWidth: "0", fontSize: "12px", lineHeight: "1.4" }}>
                {"Spreadsheets go through the migration pipeline. Send with nothing typed and the run opens below at its first gate — every gate is answered here, and nothing reaches plenum_cafm until the last one."}
              </span>
              {/* What the run is labelled with, and what the mapper picks its alias pack
                  from. Free text, as the migration page had it: the service takes any
                  name, and a blank one is sent as Custom. */}
              <label style={{ display: "inline-flex", alignItems: "center", gap: "6px", fontSize: "10.5px", color: "var(--color-neutral-500)", whiteSpace: "nowrap" }}>
                {"Source system"}
                <input className="input" value={vals.mgCmms} onChange={vals.mgSetCmms} placeholder="Custom" aria-label="Source system"
                  style={{ fontSize: "11px", padding: "4px 8px", width: "130px", borderRadius: "6px", border: "1px solid var(--color-divider)", background: "var(--color-bg)", color: "var(--color-text)", fontFamily: "var(--font-body)", outline: "none" }} />
              </label>
              <button type="button" className="hv13" onClick={vals.orchMigrateHere} style={{ ...BARE, fontSize: "11px", padding: "4px 10px", borderRadius: "6px", border: "1px solid var(--color-divider)", cursor: "pointer", whiteSpace: "nowrap" }}>
                {"Migrate it"}
              </button>
            </div>
            <div style={{ display: vals.ccCaseShow || "none", flexDirection: "column", gap: "6px", marginBottom: "9px", padding: "9px 11px", borderRadius: "9px", border: "1px solid var(--color-accent)", background: "var(--color-accent-900)" }}>
              <div style={{ display: "flex", alignItems: "center", gap: "9px", flexWrap: "wrap" }}>
                <i className="ph ph-seal-question" style={{ fontSize: "14px", color: "var(--color-accent)", flexShrink: "0" }}></i>
                <span style={{ flex: "1", minWidth: "0", fontSize: "12px", fontWeight: "500", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {vals.ccCaseLabel}
                </span>
                <button type="button" className="hv13" onClick={vals.ccCaseDrop} title="Leave it held and go back to asking questions" style={{ ...BARE, fontSize: "11px", padding: "4px 10px", borderRadius: "6px", border: "1px solid var(--color-divider)", cursor: "pointer", whiteSpace: "nowrap" }}>
                  {"Not now"}
                </button>
              </div>
              {vals.ccCaseQuestion ? (
                <span style={{ fontSize: "10.5px", color: "var(--color-neutral-500)", lineHeight: "1.45" }}>
                  {vals.ccCaseQuestion}
                </span>
              ) : null}
            </div>
            <div style={{ display: vals.cbShow || "none", flexDirection: "column", gap: "7px", marginBottom: "9px", padding: "9px 11px", borderRadius: "9px", border: "1px solid " + (vals.cbChosen ? "var(--color-divider)" : "var(--st-warn)"), background: vals.cbChosen ? "var(--color-surface)" : "var(--st-warn-bg)" }}>
              <div style={{ display: "flex", alignItems: "center", gap: "9px", flexWrap: "wrap" }}>
                <i className={`ph ${vals.cbChosen ? "ph-buildings" : "ph-warning"}`} style={{ fontSize: "14px", color: vals.cbChosen ? "var(--color-accent)" : "var(--st-warn)", flexShrink: "0" }}></i>
                <span style={{ flex: "1", minWidth: "0", fontSize: "12px", fontWeight: vals.cbChosen ? "500" : "400", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {vals.cbLabel}
                </span>
                <button type="button" className="hv13" onClick={vals.cbOpen} style={{ ...BARE, fontSize: "11px", padding: "4px 10px", borderRadius: "6px", border: "1px solid var(--color-divider)", cursor: "pointer", whiteSpace: "nowrap" }}>
                  {vals.cbChosen ? "Change" : "Choose a building"}
                </button>
                {vals.cbChosen ? (
                  <button type="button" className="hv21" onClick={vals.cbClear} title="File against no building" style={{ ...BARE, display: "flex", opacity: "0.6", cursor: "pointer" }}>
                    <i className="ph ph-x" style={{ fontSize: "11px" }}></i>
                  </button>
                ) : (
                  <button type="button" className="hv13" onClick={vals.cbHoist} style={{ ...BARE, fontSize: "11px", padding: "4px 10px", borderRadius: "6px", border: "1px solid var(--color-divider)", cursor: "pointer", whiteSpace: "nowrap" }}>
                    {"Hoist a new one"}
                  </button>
                )}
              </div>
              <span style={{ fontSize: "10.5px", color: "var(--color-neutral-500)", lineHeight: "1.45" }}>
                {vals.cbWarn}
              </span>
              {vals.cbPickerOpen ? (
                <div style={{ display: "flex", flexDirection: "column", gap: "5px", marginTop: "2px", padding: "8px", borderRadius: "8px", border: "1px solid var(--color-divider)", background: "var(--color-bg)" }}>
                  <input className="input" autoFocus value={vals.cbQuery} onChange={vals.cbSetQuery}
                    onKeyDown={(e) => { if (e.key === "Escape") vals.cbClose(); }}
                    placeholder="Search by name or code" style={{ fontSize: "12px", padding: "6px 9px" }} />
                  <div style={{ display: "flex", flexDirection: "column", maxHeight: "188px", overflowY: "auto" }}>
                    {(vals.cbMatches || []).map((b) => (
                      <React.Fragment key={b.id}>
                        <button type="button" className="hv13" onClick={b.pick} style={{ ...BARE, display: "flex", alignItems: "center", gap: "8px", padding: "6px 8px", borderRadius: "6px", cursor: "pointer", textAlign: "left", background: b.on ? "var(--color-accent-900)" : "transparent" }}>
                          <span style={{ flex: "1", minWidth: "0", fontSize: "12px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{b.name}</span>
                          <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "9.5px", color: "var(--color-neutral-500)", flexShrink: "0" }}>{b.code}</span>
                        </button>
                      </React.Fragment>
                    ))}
                    {vals.cbEmpty ? (
                      <span style={{ padding: "8px", fontSize: "11px", color: "var(--color-neutral-500)" }}>
                        {"No building matches that. Check the code, or hoist a new one."}
                      </span>
                    ) : null}
                  </div>
                </div>
              ) : null}
            </div>
            <div style={{ display: "flex", gap: "8px", alignItems: "stretch" }}>
              {/* No attach for an account whose "Can ingest" is off. The composer itself
                  stays — the conversation is not the thing being withheld. */}
              {vals.canIngest ? (
                <label className="hv13" title="Attach documents or photos — CSV and Excel go to migration, PDF, Word and images are indexed for search" style={{ display: "flex", width: "42px", flexShrink: "0", borderRadius: "10px", border: "1px solid var(--color-divider)", background: "var(--color-surface)", alignItems: "center", justifyContent: "center", cursor: "pointer" }}>
                  <i className="ph ph-paperclip" style={{ fontSize: "15px", color: "var(--color-neutral-400)" }}></i>
                  <input type="file" multiple accept=".pdf,.doc,.docx,.csv,.xls,.xlsx,image/*" onChange={vals.orchPickFiles} style={{ display: "none" }} />
                </label>
              ) : null}
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
