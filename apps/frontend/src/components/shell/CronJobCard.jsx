// CronJobCard — "schedule a job" in the conversation (logic/crons.js).
//
// A scheduling request in the chat opens this under the sentence that asked for it: the jobs
// the service can run, ticked from what was asked; how often; and Create. The service keeps
// the cadence, so the job runs whether or not anyone has the page open, and each run shows on
// the Hoist Crons panel on Home.
import React from 'react';
import RecipientsField from './RecipientsField.jsx';

const LABEL = { fontSize: "9.5px", letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--color-neutral-500)" };
const INPUT = { boxSizing: "border-box", fontSize: "11.5px", padding: "6px 8px", borderRadius: "6px", border: "1px solid var(--color-divider)", background: "var(--color-surface)", color: "var(--color-text)", fontFamily: "var(--font-body)", outline: "none" };
const PRIMARY = { flex: "1", textAlign: "center", fontSize: "11.5px", padding: "7px", borderRadius: "7px", background: "var(--color-accent)", color: "var(--accent-ink)", cursor: "pointer", border: "none" };
const QUIET = { fontSize: "11.5px", padding: "7px 10px", borderRadius: "7px", border: "1px solid var(--color-divider)", color: "var(--color-neutral-500)", cursor: "pointer", background: "transparent" };
const CHIP = (on) => ({ fontSize: "11px", padding: "5px 10px", borderRadius: "999px", cursor: "pointer", whiteSpace: "nowrap",
  border: "1px solid " + (on ? "var(--color-accent)" : "var(--color-divider)"),
  background: on ? "var(--color-accent-soft, transparent)" : "transparent",
  color: on ? "var(--color-accent)" : "var(--color-neutral-400)" });

export default function CronJobCard({ vals }) {
  return (
    <div style={{ marginTop: "16px", padding: "12px", borderRadius: "9px", background: "var(--color-bg)", border: "1px solid var(--color-accent)" }}>
      <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", gap: "8px" }}>
        <span style={{ fontSize: "9.5px", letterSpacing: "0.12em", textTransform: "uppercase", color: "var(--color-accent)" }}>{"Schedule a job"}</span>
        <span style={{ fontSize: "9.5px", color: "var(--color-neutral-500)", whiteSpace: "nowrap" }}>{"Runs on Hoist Crons · " + vals.cronZone}</span>
      </div>

      <div style={{ ...LABEL, marginTop: "11px" }}>{"What to run"}</div>
      <div style={{ display: "flex", flexDirection: "column", gap: "5px", marginTop: "6px" }}>
        {(vals.cronJobOpts || []).map((j) => (
          <label key={j.key} style={{ display: "flex", gap: "8px", alignItems: "flex-start", padding: "6px 8px", borderRadius: "7px", cursor: "pointer",
            border: "1px solid " + (j.on ? "var(--color-accent)" : "var(--color-divider)") }}>
            <input type="checkbox" checked={j.on} onChange={j.toggle} style={{ marginTop: "2px" }} />
            <span style={{ display: "flex", flexDirection: "column", gap: "2px", minWidth: "0" }}>
              <span style={{ fontSize: "11.5px" }}>{j.label}<span style={{ fontSize: "10px", color: "var(--color-neutral-500)", marginLeft: "6px" }}>{j.module}</span>
                {j.wideOnly ? <span style={{ fontSize: "9.5px", color: "var(--st-warn)", marginLeft: "6px" }}>{"company-wide only"}</span> : null}</span>
              {j.desc ? <span style={{ fontSize: "10px", color: "var(--color-neutral-500)", lineHeight: "1.4" }}>{j.desc}</span> : null}
            </span>
          </label>
        ))}
      </div>

      {vals.cronNeedsPrompt ? (
        <div style={{ marginTop: "9px", display: "flex", flexDirection: "column", gap: "4px" }}>
          <span style={LABEL}>{"The question to ask"}</span>
          <textarea className="input" rows={2} value={vals.cronPrompt} onChange={vals.cronSetPrompt}
            placeholder="Which vendor certificates are lapsed or expiring, and what do they affect?" style={{ ...INPUT, width: "100%", resize: "vertical" }} />
          <label style={{ display: "inline-flex", alignItems: "center", gap: "6px", fontSize: "11px", color: "var(--color-neutral-400)", cursor: "pointer", marginTop: "2px" }}>
            <input type="checkbox" checked={vals.cronEmail} onChange={vals.cronToggleEmail} />
            {"Email me each answer — to " + vals.cronEmailTo + ", from admin@hoistra.ai"}
          </label>
        </div>
      ) : null}

      <div style={{ ...LABEL, marginTop: "11px" }}>{"For which building"}</div>
      <select aria-label="Building" className="input" value={vals.cronBuildingId} onChange={vals.cronSetBuilding}
        style={{ ...INPUT, width: "100%", marginTop: "6px" }}>
        {(vals.cronBuildings || []).map((b) => <option key={b.id || "all"} value={b.id}>{b.name}</option>)}
      </select>

      <div style={{ ...LABEL, marginTop: "11px" }}>{"How often"}</div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: "6px", marginTop: "6px" }}>
        {(vals.cronFreqOpts || []).map((x) => (
          <button key={x.key} type="button" onClick={x.pick} style={CHIP(x.on)}>{x.label}</button>
        ))}
      </div>
      {vals.cronShowTime || vals.cronShowDays ? (
        <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: "8px", marginTop: "8px" }}>
          {vals.cronShowDays ? (vals.cronDays || []).map((d) => (
            <button key={d.label} type="button" onClick={d.toggle} style={CHIP(d.on)}>{d.label}</button>
          )) : null}
          {vals.cronShowMonthDay ? (
            <label style={{ display: "inline-flex", alignItems: "center", gap: "6px", fontSize: "11px", color: "var(--color-neutral-400)" }}>
              {"on day"}
              <select aria-label="Day of the month" className="input" value={vals.cronMonthDay} onChange={vals.cronSetMonthDay} style={{ ...INPUT, width: "64px" }}>
                {(vals.cronMonthDays || []).map((d) => <option key={d} value={String(d)}>{String(d)}</option>)}
              </select>
              {"of each month"}
            </label>
          ) : null}
          {vals.cronShowTime ? (
            <label style={{ display: "inline-flex", alignItems: "center", gap: "6px", fontSize: "11px", color: "var(--color-neutral-400)" }}>
              {"at"}
              <input type="time" className="input" value={vals.cronTime} onChange={vals.cronSetTime} style={{ ...INPUT, width: "110px" }} />
            </label>
          ) : null}
        </div>
      ) : null}

      <div style={{ marginTop: "10px" }}><RecipientsField vals={vals} id="cron-card-recipients" /></div>

      <label style={{ display: "inline-flex", alignItems: "center", gap: "6px", fontSize: "11px", color: "var(--color-neutral-400)", marginTop: "10px", cursor: "pointer" }}>
        <input type="checkbox" checked={vals.cronRunNow} onChange={vals.cronToggleRunNow} />
        {"Also run it once now"}
      </label>

      {vals.cronMsg ? (
        <div style={{ marginTop: "9px", padding: "7px 9px", borderRadius: "7px", background: "var(--st-warn-bg)", fontSize: "10.5px", color: "var(--st-warn)", lineHeight: "1.45" }}>{vals.cronMsg}</div>
      ) : null}

      <div style={{ display: "flex", gap: "8px", marginTop: "11px" }}>
        <button type="button" onClick={vals.cronCreate} disabled={vals.cronBusy} style={{ ...PRIMARY, opacity: vals.cronBusy ? "0.6" : "1" }}>{vals.cronCreateLabel}</button>
        <button type="button" onClick={vals.cronClose} style={QUIET}>{"Cancel"}</button>
      </div>
    </div>
  );
}
