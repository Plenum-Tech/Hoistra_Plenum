// Crons — Administration › Hoist Crons (logic/cronsPage.js). Every scheduled job, its run
// history and day-by-day status, who did what to it, and New job / Edit schedule. The jobs run
// in the platform on the service's own clock, as the person who scheduled them.
import React from 'react';
import RecipientsField from '../components/shell/RecipientsField.jsx';

const CARD = { borderRadius: "10px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)" };
const KICK = { fontSize: "10.5px", letterSpacing: "0.09em", textTransform: "uppercase", color: "var(--color-neutral-500)" };
const MONO = { fontFamily: "ui-monospace,monospace" };
const QUIET = { fontSize: "12px", padding: "7px 12px", borderRadius: "7px", border: "1px solid var(--color-divider)", background: "transparent", color: "var(--color-neutral-400)", cursor: "pointer" };
const OUTLINE = { ...QUIET, border: "1px solid var(--color-accent)", color: "var(--color-accent)" };
const chip = (on) => ({ fontSize: "12px", padding: "6px 12px", borderRadius: "999px", cursor: "pointer", whiteSpace: "nowrap",
  border: "1px solid " + (on ? "var(--color-accent)" : "var(--color-divider)"), background: on ? "var(--color-accent-900)" : "transparent",
  color: on ? "var(--color-accent)" : "var(--color-neutral-400)" });

function Legend() {
  const item = (tone, text) => (
    <span style={{ display: "flex", alignItems: "center", gap: "5px" }}>
      <span style={{ width: "8px", height: "8px", borderRadius: "2px", background: tone }}></span>{text}
    </span>
  );
  return (
    <div style={{ display: "flex", gap: "14px", flexWrap: "wrap", fontSize: "11.5px", color: "var(--color-neutral-400)", marginTop: "10px" }}>
      {item("var(--st-ok)", "All runs ok")}{item("var(--st-warn)", "Some failed")}{item("var(--st-risk)", "All failed")}{item("var(--color-divider)", "No run")}
      <span>· Hover a day for its runs</span>
    </div>
  );
}

function Crumbs({ vals, job }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: "8px", padding: "24px 0 0", fontSize: "12px", color: "var(--color-neutral-500)" }}>
      <button type="button" onClick={vals.goHome} style={{ ...QUIET, border: "none", padding: "0", display: "flex", alignItems: "center", gap: "6px" }}>
        <i className="ph ph-arrow-left" style={{ fontSize: "12px" }}></i>{"Home"}
      </button>
      <span>{"/"}</span><span>{"Administration"}</span><span>{"/"}</span>
      {job ? (
        <>
          <button type="button" onClick={vals.cpBack} style={{ ...QUIET, border: "none", padding: "0" }}>{"Hoist Crons"}</button>
          <span>{"/"}</span><span style={{ color: "var(--color-text)" }}>{job}</span>
        </>
      ) : <span style={{ color: "var(--color-text)" }}>{"Hoist Crons"}</span>}
    </div>
  );
}

function Dialog({ vals }) {
  return (
    <div style={{ position: "fixed", inset: "0", background: "rgba(26,26,24,0.38)", zIndex: "60", display: "flex", justifyContent: "center", alignItems: "flex-start", paddingTop: "70px", overflowY: "auto" }}>
      <div role="dialog" aria-modal="true" aria-label={vals.cpModalTitle} style={{ width: "760px", maxWidth: "calc(100% - 32px)", ...CARD, boxShadow: "var(--shadow-md)", padding: "22px 24px", boxSizing: "border-box", marginBottom: "40px" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: "12px" }}>
          <h3 style={{ margin: "0", fontSize: "19px" }}>{vals.cpModalTitle}</h3>
          <span style={{ fontSize: "11.5px", color: "var(--color-neutral-500)" }}>{"Runs as you · " + vals.cronZone}</span>
        </div>

        {vals.cpModalEdit ? null : (
          <>
            <div style={{ ...KICK, marginTop: "16px" }}>{"1 · What to run"}</div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(300px,1fr))", gap: "8px", marginTop: "8px" }}>
              {(vals.cronJobOpts || []).map((j) => (
                <label key={j.key} style={{ display: "flex", gap: "9px", alignItems: "flex-start", padding: "9px 11px", borderRadius: "9px", cursor: "pointer",
                  border: "1px solid " + (j.on ? "var(--color-accent)" : "var(--color-divider)"), background: j.on ? "var(--color-accent-900)" : "transparent" }}>
                  <input type="checkbox" checked={j.on} onChange={j.toggle} style={{ marginTop: "3px" }} />
                  <span style={{ display: "flex", flexDirection: "column", gap: "2px", minWidth: "0" }}>
                    <span style={{ fontSize: "12.5px" }}>{j.label}<span style={{ fontSize: "10.5px", color: "var(--color-neutral-500)", marginLeft: "6px" }}>{j.module}</span></span>
                    {j.desc ? <span style={{ fontSize: "11px", color: "var(--color-neutral-500)", lineHeight: "1.4" }}>{j.desc}</span> : null}
                  </span>
                </label>
              ))}
            </div>
          </>
        )}

        {vals.cronNeedsPrompt ? (
          <div style={{ marginTop: "12px", display: "flex", flexDirection: "column", gap: "6px" }}>
            <label htmlFor="cp-question" style={{ fontSize: "12px", color: "var(--color-neutral-400)" }}>{"The question to ask each time"}</label>
            <textarea id="cp-question" className="input" rows={2} value={vals.cronPrompt} onChange={vals.cronSetPrompt}
              placeholder="Which vendor certificates are lapsed or expiring, and what do they affect?"
              style={{ fontSize: "12.5px", padding: "8px 10px", borderRadius: "7px", border: "1px solid var(--color-divider)", background: "var(--color-surface)", color: "var(--color-text)", fontFamily: "var(--font-body)", resize: "vertical" }} />
            <label style={{ display: "inline-flex", alignItems: "center", gap: "7px", fontSize: "12px", color: "var(--color-neutral-400)", cursor: "pointer" }}>
              <input type="checkbox" checked={vals.cronEmail} onChange={vals.cronToggleEmail} />
              {"Email me each answer — to " + vals.cronEmailTo + ", from admin@hoistra.ai"}
            </label>
          </div>
        ) : null}

        <div style={{ ...KICK, marginTop: "18px" }}>{vals.cpModalEdit ? "When" : "2 · When"}</div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: "6px", marginTop: "8px" }}>
          {(vals.cronFreqOpts || []).map((x) => (
            <button key={x.key} type="button" aria-pressed={x.on} onClick={x.pick} style={chip(x.on)}>{x.label}</button>
          ))}
        </div>
        {vals.cronShowTime || vals.cronShowDays ? (
          <div style={{ display: "flex", flexWrap: "wrap", gap: "6px", alignItems: "center", marginTop: "9px" }}>
            {vals.cronShowDays ? (vals.cronDays || []).map((d) => (
              <button key={d.label} type="button" aria-pressed={d.on} onClick={d.toggle} style={chip(d.on)}>{d.label}</button>
            )) : null}
            {vals.cronShowTime ? (
              <label style={{ display: "inline-flex", alignItems: "center", gap: "6px", fontSize: "12px", color: "var(--color-neutral-400)", marginLeft: "4px" }}>
                {"at"}
                <input type="time" className="input" value={vals.cronTime} onChange={vals.cronSetTime}
                  style={{ fontSize: "12.5px", padding: "6px 8px", borderRadius: "7px", border: "1px solid var(--color-divider)", background: "var(--color-surface)", color: "var(--color-text)" }} />
              </label>
            ) : null}
          </div>
        ) : null}

        <div style={{ ...KICK, marginTop: "18px" }}>{vals.cpModalEdit ? "Who gets the result" : "3 · Who gets the result"}</div>
        <div style={{ marginTop: "8px" }}><RecipientsField vals={vals} id="cp-recipients" /></div>

        <div style={{ marginTop: "14px", padding: "11px 13px", borderRadius: "9px", background: "var(--color-neutral-900)", fontSize: "12px", color: "var(--color-neutral-400)", lineHeight: "1.55" }}>
          <span style={{ color: "var(--color-text)" }}>{"Next runs: "}</span>{vals.cpPreview}
        </div>

        {vals.cronMsg ? (
          <div style={{ marginTop: "10px", padding: "7px 10px", borderRadius: "7px", background: "var(--st-warn-bg)", fontSize: "11.5px", color: "var(--st-warn)" }}>{vals.cronMsg}</div>
        ) : null}

        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "10px", marginTop: "16px", flexWrap: "wrap" }}>
          {vals.cpModalEdit ? <span></span> : (
            <label style={{ display: "inline-flex", alignItems: "center", gap: "7px", fontSize: "12px", color: "var(--color-neutral-400)", cursor: "pointer" }}>
              <input type="checkbox" checked={vals.cronRunNow} onChange={vals.cronToggleRunNow} />{"Also run once now"}
            </label>
          )}
          <div style={{ display: "flex", gap: "8px" }}>
            <button type="button" onClick={vals.cpModalClose} style={QUIET}>{"Cancel"}</button>
            <button type="button" className="btn btn-primary" onClick={vals.cpSave} disabled={vals.cronBusy}
              style={{ fontSize: "12.5px", padding: "8px 15px", cursor: "pointer", opacity: vals.cronBusy ? "0.6" : "1" }}>{vals.cpSaveLabel}</button>
          </div>
        </div>
      </div>
    </div>
  );
}

function JobDetail({ vals }) {
  const d = vals.cpDetail;
  return (
    <>
      <Crumbs vals={vals} job={d.name} />
      <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", flexWrap: "wrap", gap: "16px 24px", marginTop: "18px" }}>
        <div style={{ minWidth: "0", flex: "1 1 360px" }}>
          <div style={{ fontSize: "10.5px", letterSpacing: "0.13em", textTransform: "uppercase", color: "var(--color-accent)" }}>{d.module + " · " + (d.question ? "scheduled question" : "engine job")}</div>
          <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap", marginTop: "7px" }}>
            <h2 style={{ fontSize: "26px", margin: "0", lineHeight: "1.15" }}>{d.name}</h2>
            <span style={{ ...MONO, fontSize: "10px", letterSpacing: "0.09em", textTransform: "uppercase", padding: "3px 8px", borderRadius: "5px", border: "1px solid " + d.statusTone, color: d.statusTone }}>{d.status}</span>
          </div>
          {d.question ? (
            <p style={{ fontSize: "13px", color: "var(--color-neutral-400)", margin: "8px 0 0", maxWidth: "80ch", lineHeight: "1.55" }}>
              {"Asks: “" + d.question + "”"}
            </p>
          ) : null}
          <p style={{ fontSize: "12.5px", color: "var(--color-neutral-400)", margin: "6px 0 0", maxWidth: "80ch", lineHeight: "1.55" }}>
            <i className="ph ph-envelope-simple" style={{ marginRight: "6px" }}></i>
            {d.mailTo ? "Each result is emailed to " + d.mailTo + ", from admin@hoistra.ai." : "Not emailed — add recipients with Edit schedule."}
          </p>
        </div>
        {d.manage ? (
          <div style={{ display: "flex", gap: "8px", flexWrap: "wrap" }}>
            <button type="button" className="btn btn-primary" onClick={d.run} style={{ fontSize: "12px", padding: "7px 14px", cursor: "pointer" }}>{d.running ? "Running…" : "Run now"}</button>
            <button type="button" onClick={d.pause} style={QUIET}>{d.pauseLabel}</button>
            <button type="button" onClick={d.edit} style={QUIET}>{"Edit schedule"}</button>
            <button type="button" onClick={d.remove} style={{ ...QUIET, color: "var(--st-risk)" }}>{d.removeLabel}</button>
          </div>
        ) : null}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(200px,1fr))", gap: "11px", marginTop: "22px" }}>
        {[["When", d.cadence, d.zone], ["Next run", d.next, ""], ["Runs as", d.runsAs, "Created by " + d.createdBy], ["Last 30 days", d.okLine, d.avg]].map(([k, v, h], i) => (
          <div key={k} style={{ padding: "13px 15px", ...CARD }}>
            <div style={KICK}>{k}</div>
            <div style={{ fontSize: "14px", marginTop: "6px", color: i === 3 ? d.okTone : "var(--color-text)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={v}>{v}</div>
            {h ? <div style={{ fontSize: "11px", color: "var(--color-neutral-500)", marginTop: "2px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={h}>{h}</div> : null}
          </div>
        ))}
      </div>

      {d.error ? <div style={{ marginTop: "14px", fontSize: "12px", color: "var(--st-risk)" }}>{"Could not read this job: " + d.error}</div> : null}

      <h3 style={{ fontSize: "15px", margin: "26px 0 10px" }}>{"Status of each day"}</h3>
      <div style={{ padding: "14px 16px", ...CARD }}>
        {d.loading ? <div style={{ fontSize: "12px", color: "var(--color-neutral-500)" }}>{"Reading…"}</div> : (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(30, minmax(0, 1fr))", gap: "4px" }}>
            {d.days.map((x, i) => (
              <div key={i} title={x.title} style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: "4px" }}>
                <span style={{ width: "100%", height: "24px", borderRadius: "4px", background: x.tone }}></span>
                <span style={{ fontSize: "9.5px", color: "var(--color-neutral-500)" }}>{x.label}</span>
              </div>
            ))}
          </div>
        )}
        <Legend />
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1.6fr) minmax(0,1fr)", gap: "18px", marginTop: "24px", alignItems: "start" }}>
        <div>
          <h3 style={{ fontSize: "15px", margin: "0 0 10px" }}>{"Run history"}</h3>
          <div style={{ ...CARD, overflow: "hidden" }}>
            <div style={{ display: "grid", gridTemplateColumns: "1.3fr 1.7fr 0.6fr 2.6fr", gap: "10px", padding: "10px 15px", ...KICK, borderBottom: "1px solid var(--color-divider)" }}>
              <span>{"Finished"}</span><span>{"Asked by"}</span><span>{"Took"}</span><span>{"Result"}</span>
            </div>
            {d.runs.length ? d.runs.map((r, i) => (
              <div key={i} style={{ padding: "10px 15px", borderBottom: "1px solid var(--color-neutral-900)" }}>
                <div style={{ display: "grid", gridTemplateColumns: "1.3fr 1.7fr 0.6fr 2.6fr", gap: "10px", fontSize: "12px" }}>
                  <span style={{ ...MONO, fontSize: "11.5px" }}>{r.at}</span>
                  <span style={{ color: "var(--color-neutral-400)", overflow: "hidden", textOverflow: "ellipsis" }}>{r.by}</span>
                  <span style={{ ...MONO, fontSize: "11.5px", color: "var(--color-neutral-400)" }}>{r.took}</span>
                  <span style={{ color: r.tone }}>{r.result}</span>
                </div>
                {r.answer ? <div style={{ fontSize: "11.5px", color: "var(--color-neutral-400)", marginTop: "6px", whiteSpace: "pre-line", lineHeight: "1.5" }}>{r.answer}</div> : null}
              </div>
            )) : <div style={{ padding: "14px 15px", fontSize: "12px", color: "var(--color-neutral-500)" }}>{d.loading ? "Reading…" : "No runs yet."}</div>}
          </div>
        </div>
        <div>
          <h3 style={{ fontSize: "15px", margin: "0 0 10px" }}>{"Who did what"}</h3>
          <div style={{ ...CARD, padding: "4px 15px" }}>
            {d.events.length ? d.events.map((e, i) => (
              <div key={i} style={{ display: "flex", gap: "11px", padding: "10px 0", borderBottom: "1px solid var(--color-neutral-900)" }}>
                <span style={{ width: "8px", height: "8px", borderRadius: "50%", background: e.tone, marginTop: "6px", flexShrink: "0" }}></span>
                <div style={{ minWidth: "0" }}>
                  <div style={{ fontSize: "12.5px" }}><span style={{ fontWeight: "500" }}>{e.action}</span>{" · " + e.who}</div>
                  <div style={{ fontSize: "11px", color: "var(--color-neutral-500)", marginTop: "2px" }}>{e.at + (e.detail ? " · " + e.detail : "")}</div>
                </div>
              </div>
            )) : <div style={{ padding: "10px 0", fontSize: "12px", color: "var(--color-neutral-500)" }}>{d.loading ? "Reading…" : "Nothing recorded yet."}</div>}
          </div>
          <p style={{ fontSize: "11px", color: "var(--color-neutral-500)", lineHeight: "1.5", margin: "9px 2px 0" }}>{"Every create, change, pause, resume, Run now and removal is kept, with the person's account and email."}</p>
        </div>
      </div>
    </>
  );
}

function JobList({ vals }) {
  const cols = "2fr 1.1fr 0.9fr 1.9fr 1fr 1.3fr 2.1fr";
  return (
    <>
      <Crumbs vals={vals} />
      <div style={{ display: "flex", alignItems: "flex-end", justifyContent: "space-between", flexWrap: "wrap", gap: "16px 24px", marginTop: "18px" }}>
        <div style={{ minWidth: "0", flex: "1 1 340px" }}>
          <div style={{ fontSize: "10.5px", letterSpacing: "0.13em", textTransform: "uppercase", color: "var(--color-accent)" }}>{"Administration · scheduling"}</div>
          <div style={{ display: "flex", alignItems: "center", gap: "10px", flexWrap: "wrap", marginTop: "7px" }}>
            <h2 style={{ fontSize: "28px", margin: "0", lineHeight: "1.15" }}>{"Hoist Crons"}</h2>
            <span style={{ ...MONO, fontSize: "10px", letterSpacing: "0.09em", textTransform: "uppercase", padding: "3px 8px", borderRadius: "5px", background: "var(--st-ok-bg)", color: "var(--st-ok)" }}>{"Clock running · every 30 s"}</span>
          </div>
          <p style={{ fontSize: "13px", color: "var(--color-neutral-400)", margin: "8px 0 0", maxWidth: "88ch", lineHeight: "1.55" }}>
            {"Engine jobs and scheduled questions that run on their own, in the platform, whether or not anyone has a page open. Each one runs as the person who scheduled it, so it reads and writes only what they may. Every run is kept for 30 days."}
          </p>
        </div>
        {vals.cpManage ? (
          <button type="button" className="btn btn-primary" onClick={vals.cpNew} style={{ fontSize: "12px", padding: "7px 14px", cursor: "pointer", display: "flex", alignItems: "center", gap: "7px" }}>
            <i className="ph ph-plus" style={{ fontSize: "13px" }}></i><span>{"New job"}</span>
          </button>
        ) : null}
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(170px,1fr))", gap: "11px", marginTop: "22px" }}>
        {(vals.cpTiles || []).map((t) => (
          <div key={t.label} style={{ padding: "13px 15px", ...CARD }}>
            <div style={{ ...MONO, fontSize: "22px", lineHeight: "1.1", color: t.tone }}>{t.value}</div>
            <div style={{ ...KICK, marginTop: "5px" }}>{t.label}</div>
            <div style={{ fontSize: "11px", color: "var(--color-neutral-500)", marginTop: "2px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={t.hint}>{t.hint}</div>
          </div>
        ))}
      </div>

      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "12px", marginTop: "24px", flexWrap: "wrap" }}>
        <div role="group" aria-label="Filter jobs" style={{ display: "flex", gap: "6px", flexWrap: "wrap" }}>
          {(vals.cpFilters || []).map((f) => (
            <button key={f.label} type="button" aria-pressed={f.on} onClick={f.pick} style={chip(f.on)}>{f.label}</button>
          ))}
        </div>
        <input type="search" className="input" aria-label="Search jobs" value={vals.cpSearch} onChange={vals.cpSetSearch} placeholder="Search job or person"
          style={{ fontSize: "12.5px", padding: "7px 10px", borderRadius: "7px", border: "1px solid var(--color-divider)", width: "230px", background: "var(--color-surface)", color: "var(--color-text)" }} />
      </div>

      <div style={{ marginTop: "12px", ...CARD, overflow: "hidden" }}>
        <div style={{ display: "grid", gridTemplateColumns: cols, gap: "12px", padding: "10px 16px", ...KICK, borderBottom: "1px solid var(--color-divider)" }}>
          <span>{"Job"}</span><span>{"When"}</span><span>{"Next run"}</span><span>{"Last run"}</span><span>{"Last 14 days"}</span><span>{"Scheduled by"}</span><span style={{ textAlign: "right" }}>{"Actions"}</span>
        </div>
        {vals.cpLoading ? <div style={{ padding: "16px", fontSize: "12px", color: "var(--color-neutral-500)" }}>{"Reading the jobs…"}</div> : null}
        {vals.cpError ? <div style={{ padding: "16px", fontSize: "12px", color: "var(--st-risk)" }}>{"The jobs could not be read: " + vals.cpError}</div> : null}
        {vals.cpEmpty && !vals.cpLoading && !vals.cpError ? (
          <div style={{ padding: "18px 16px", fontSize: "12.5px", color: "var(--color-neutral-400)", lineHeight: "1.55" }}>
            {"No jobs scheduled yet. Use New job, or ask in the chat — “Run an energy anomaly scan every hour”."}
          </div>
        ) : null}
        {vals.cpNoMatch ? <div style={{ padding: "16px", fontSize: "12px", color: "var(--color-neutral-500)" }}>{"No job matches this filter."}</div> : null}
        {(vals.cpRows || []).map((j) => (
          <div key={j.id} style={{ display: "grid", gridTemplateColumns: cols, gap: "12px", padding: "12px 16px", alignItems: "center", borderBottom: "1px solid var(--color-neutral-900)", fontSize: "12.5px" }}>
            <div style={{ minWidth: "0" }}>
              <button type="button" onClick={j.open} title="View this job's runs, status and history"
                style={{ border: "none", background: "transparent", padding: "0", cursor: "pointer", color: "var(--color-accent)", fontSize: "13px", textAlign: "left", textDecoration: "underline", textUnderlineOffset: "3px" }}>
                {j.name}<i className="ph ph-arrow-right" style={{ fontSize: "11px", marginLeft: "5px" }}></i>
              </button>
              <div style={{ fontSize: "11px", color: "var(--color-neutral-500)", marginTop: "2px" }}>{j.module + " · "}<span style={{ color: j.statusTone }}>{j.status}</span></div>
            </div>
            <span style={{ color: "var(--color-neutral-400)" }}>{j.cadence}</span>
            <span style={{ ...MONO, fontSize: "11.5px" }}>{j.next}</span>
            <span style={{ fontSize: "11.5px", color: j.lastTone, lineHeight: "1.4" }}>{j.last}</span>
            <span style={{ display: "flex", gap: "2px" }} aria-label={"Last 14 days for " + j.name}>
              {j.days.map((d) => (<span key={d.date} title={d.title} style={{ width: "6px", height: "14px", borderRadius: "2px", background: d.tone }}></span>))}
            </span>
            <span style={{ fontSize: "11.5px", color: "var(--color-neutral-400)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={j.by}>{j.by}</span>
            <span style={{ display: "flex", gap: "6px", justifyContent: "flex-end", flexWrap: "wrap" }}>
              <button type="button" onClick={j.open} style={QUIET}><i className="ph ph-eye" style={{ marginRight: "5px" }}></i>{"View"}</button>
              {j.manage ? (
                <>
                  <button type="button" onClick={j.edit} style={QUIET}><i className="ph ph-pencil-simple" style={{ marginRight: "5px" }}></i>{"Edit"}</button>
                  <button type="button" onClick={j.run} style={OUTLINE}>{j.running ? "Running…" : "Run now"}</button>
                  <button type="button" onClick={j.pause} style={QUIET}>{j.pauseLabel}</button>
                </>
              ) : null}
            </span>
          </div>
        ))}
      </div>
      <Legend />

      <h3 style={{ fontSize: "15px", margin: "28px 0 10px" }}>{"Recent runs"}</h3>
      <div style={{ ...CARD, overflow: "hidden" }}>
        <div style={{ display: "grid", gridTemplateColumns: "1.1fr 2fr 2fr 0.7fr 3fr", gap: "12px", padding: "10px 16px", ...KICK, borderBottom: "1px solid var(--color-divider)" }}>
          <span>{"Finished"}</span><span>{"Job"}</span><span>{"Asked by"}</span><span>{"Took"}</span><span>{"Result"}</span>
        </div>
        {(vals.cpRecent || []).length ? vals.cpRecent.map((r, i) => (
          <div key={i} style={{ display: "grid", gridTemplateColumns: "1.1fr 2fr 2fr 0.7fr 3fr", gap: "12px", padding: "10px 16px", fontSize: "12px", borderBottom: "1px solid var(--color-neutral-900)", alignItems: "center" }}>
            <span style={{ ...MONO, fontSize: "11.5px" }}>{r.at}</span>
            <button type="button" onClick={r.open} title="View this job" style={{ border: "none", background: "transparent", padding: "0", cursor: "pointer", color: "var(--color-accent)", fontSize: "12px", textAlign: "left", textDecoration: "underline", textUnderlineOffset: "3px" }}>{r.job}</button>
            <span style={{ color: "var(--color-neutral-400)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={r.by}>{r.by}</span>
            <span style={{ ...MONO, fontSize: "11.5px", color: "var(--color-neutral-400)" }}>{r.took}</span>
            <span style={{ color: r.tone }}>{r.result}</span>
          </div>
        )) : <div style={{ padding: "14px 16px", fontSize: "12px", color: "var(--color-neutral-500)" }}>{"No runs yet."}</div>}
      </div>
    </>
  );
}

export default function Crons({ vals }) {
  return (
    <div style={{ flex: "1", display: "flex", justifyContent: "flex-start", padding: "0 40px 80px" }}>
      <div style={{ width: "100%", maxWidth: "1400px", animation: "fadeUp 0.28s ease both" }}>
        {vals.cpDetail ? <JobDetail vals={vals} /> : <JobList vals={vals} />}
      </div>
      {vals.cpModal ? <Dialog vals={vals} /> : null}
    </div>
  );
}
