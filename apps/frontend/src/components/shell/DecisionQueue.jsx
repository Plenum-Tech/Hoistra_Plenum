// DecisionQueue — right drawer
// Ported from the Hoistra prototype template. `vals` is the view model from useHoistra().
//
// 17 Nov 2026 (UI audit): the decisions come first. The schedule (Runs) and channels (Notify on)
// are settings, behind the gear in the header. Before the queue has been read the drawer says so
// (and offers Retry once a read failed) instead of showing sample cards. Every card has Open
// (its page and the email that resolves it) and Snooze 1 day (this browser only; the card leaves
// the count, so the pill can be cleared). Controls are buttons, so the keyboard reaches them.
import React from 'react';

const BARE = { font: "inherit", margin: "0", background: "transparent", border: "0", padding: "0", color: "inherit", cursor: "pointer" };
const LABEL = { fontSize: "10.5px", letterSpacing: "0.1em", textTransform: "uppercase", color: "var(--color-neutral-500)", marginRight: "2px" };
const chip = (o) => ({ ...BARE, fontSize: "11px", padding: "4px 10px", borderRadius: "20px", border: `1px solid ${o.border}`, color: o.fg, background: o.bg });
const FIELD = { width: "100%", boxSizing: "border-box", fontSize: "12px", padding: "6px 8px", borderRadius: "6px", border: "1px solid var(--color-divider)", background: "var(--color-bg)", color: "var(--color-text)", fontFamily: "var(--font-body)" };
const SMALL_BTN = { ...BARE, display: "inline-flex", alignItems: "center", gap: "5px", fontSize: "11.5px", padding: "5px 10px", borderRadius: "7px", border: "1px solid var(--color-divider)", color: "var(--color-neutral-300)" };

// One decision. The whole card opens it; the buttons say so for the keyboard and add Snooze.
function QueueCard({ d }) {
  return (
              <div className="hv1" onClick={d.click} style={{ padding: "14px", borderRadius: "10px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)", cursor: "pointer", opacity: d.snoozed ? "0.6" : "1" }}>
                <div style={{ display: "flex", alignItems: "center", gap: "8px", marginBottom: "7px" }}>
                  <i className={`ph ${d.icon}`} style={{ fontSize: "13px", color: d.color }} aria-hidden="true"></i>
                  <span style={{ fontSize: "10.5px", letterSpacing: "0.09em", textTransform: "uppercase", color: "var(--color-neutral-500)" }}>
                    {d.module}{d.snoozed ? " · snoozed" : ""}
                  </span>
                  <div style={{ flex: "1" }}></div>
                  <span style={{ display: "inline-block", whiteSpace: "nowrap", fontSize: "10.5px", padding: "2px 7px", borderRadius: "5px", color: d.color, background: d.bg }}>
                    {d.money}
                  </span>
                </div>
                <div style={{ fontSize: "13px", lineHeight: "1.4" }}>
                  {d.title}
                </div>
                <div style={{ fontSize: "11px", color: "var(--color-neutral-300)", marginTop: "5px", lineHeight: "1.45" }}>
                  {d.meta}
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: "8px", marginTop: "11px", flexWrap: "wrap" }}>
                  <button type="button" className="hv13" onClick={(e) => { e.stopPropagation(); d.open(); }} style={{ ...SMALL_BTN, color: "var(--color-text)" }}>
                    <i className="ph ph-arrow-square-out" aria-hidden="true"></i>
                    {"Open"}
                  </button>
                  {d.snoozable ? (
                    <button type="button" className="hv13" onClick={(e) => { e.stopPropagation(); if (d.snoozed) d.unsnooze(); else d.snooze(); }}
                      title={d.snoozed ? "Put it back in the count" : "Hidden in this browser until tomorrow; it stays pending for everyone else"} style={SMALL_BTN}>
                      <i className={d.snoozed ? "ph ph-bell" : "ph ph-moon"} aria-hidden="true"></i>
                      {d.snoozed ? "Un-snooze" : "Snooze 1 day"}
                    </button>
                  ) : null}
                </div>
              </div>
  );
}

export default function DecisionQueue({ vals }) {
  return (
    <>
      <div onClick={vals.closeQueue} style={{ position: "fixed", inset: "0", background: "var(--scrim)", zIndex: "60" }}></div>
      <div role="dialog" aria-modal="true" aria-labelledby="dq-title" style={{ position: "fixed", top: "0", right: "0", bottom: "0", width: "min(520px, 100vw)", background: "var(--color-bg)", boxShadow: "var(--shadow-lg)", zIndex: "61", display: "flex", flexDirection: "column", animation: "fadeUp 0.22s ease both" }}>
        <div style={{ display: "flex", alignItems: "center", gap: "12px", padding: "17px 20px", borderBottom: "1px solid var(--color-divider)", flexShrink: "0" }}>
          <i className="ph ph-bell-ringing" style={{ fontSize: "16px", color: "var(--st-warn)" }} aria-hidden="true"></i>
          <div style={{ flex: "1", minWidth: "0" }}>
            <div id="dq-title" style={{ fontSize: "14px" }}>
              {"Decision queue"}
            </div>
            <div style={{ fontSize: "11px", color: "var(--color-neutral-500)", marginTop: "2px" }}>
              {"Urgent first, ranked by consequence · "}{vals.queueHeadLine}
            </div>
          </div>
          <button type="button" className="hv6" onClick={vals.queueSettingsToggle} aria-pressed={!!vals.queueSettingsPressed} aria-controls="dq-settings" title="Refresh schedule and notifications"
            style={{ ...BARE, display: "flex", alignItems: "center", gap: "5px", fontSize: "11px", padding: "4px 8px", borderRadius: "6px", border: "1px solid " + (vals.queueSettingsPressed ? "var(--color-accent)" : "var(--color-divider)"), color: vals.queueSettingsPressed ? "var(--color-accent)" : "var(--color-neutral-500)" }}>
            <i className="ph ph-gear-six" style={{ fontSize: "13px" }} aria-hidden="true"></i>
            {"Settings"}
          </button>
          <button type="button" className="hv6" onClick={vals.closeQueue} aria-label="Close the decision queue" style={{ ...BARE, display: "flex", color: "var(--color-neutral-500)" }}>
            <i className="ph ph-x" style={{ fontSize: "16px" }} aria-hidden="true"></i>
          </button>
        </div>
        <div style={{ padding: "12px 20px", borderBottom: "1px solid var(--color-divider)", flexShrink: "0", display: "flex", flexDirection: "column", gap: "9px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
            <span style={LABEL}>{"Show"}</span>
            {(vals.queueFilterOpts || []).map((o, $index) => (
              <React.Fragment key={$index}>
                <button type="button" onClick={o.pick} aria-pressed={o.border === "var(--color-accent)"} style={{ ...chip(o), display: "flex", alignItems: "center", gap: "5px" }}>
                  {o.label}
                  <span style={{ fontFamily: "ui-monospace,monospace", fontSize: "10px", opacity: "0.75" }}>
                    {o.n}
                  </span>
                </button>
              </React.Fragment>
            ))}
          </div>
          {/* Settings: when the queue re-reads, and how a new item reaches you. */}
          <div id="dq-settings" style={{ display: vals.queueSettingsShow, flexDirection: "column", gap: "9px", paddingTop: "4px" }}>
            <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
              <span style={LABEL}>{"Runs"}</span>
              {(vals.freqOpts || []).map((o, $index) => (
                <React.Fragment key={$index}>
                  <button type="button" onClick={o.pick} aria-pressed={o.border === "var(--color-accent)"} style={chip(o)}>
                    {o.label}
                  </button>
                </React.Fragment>
              ))}
            </div>
            {vals.customOpen ? (
              <div style={{ display: "grid", gridTemplateColumns: "repeat(3,minmax(0,1fr))", gap: "9px", padding: "11px 12px", borderRadius: "9px", background: "var(--color-surface)", boxShadow: "var(--shadow-sm)" }}>
                <label style={{ display: "flex", flexDirection: "column", gap: "5px", minWidth: "0" }}>
                  <span style={{ ...LABEL, fontSize: "9.5px" }}>{"Frequency"}</span>
                  <select className="input" value={vals.cFreq} onChange={vals.setCFreq} style={FIELD}>
                    {(vals.cFreqOpts || []).map((o, $index) => (
                      <React.Fragment key={$index}>
                        <option value={o}>{o}</option>
                      </React.Fragment>
                    ))}
                  </select>
                </label>
                <label style={{ display: "flex", flexDirection: "column", gap: "5px", minWidth: "0" }}>
                  <span style={{ ...LABEL, fontSize: "9.5px" }}>{"Start date"}</span>
                  <input className="input" type="date" value={vals.cDate} onChange={vals.setCDate} style={FIELD} />
                </label>
                <label style={{ display: "flex", flexDirection: "column", gap: "5px", minWidth: "0" }}>
                  <span style={{ ...LABEL, fontSize: "9.5px" }}>{"Time · 24h"}</span>
                  <input className="input" value={vals.cTime} onChange={vals.setCTime} placeholder="02:00" maxLength="5" style={{ ...FIELD, fontFamily: "ui-monospace,monospace" }} />
                </label>
                <div style={{ gridColumn: "1/-1", display: "flex", alignItems: "center", gap: "9px", flexWrap: "wrap" }}>
                  <button type="button" className="hv7" onClick={vals.saveCustom} style={{ ...BARE, fontSize: "11.5px", padding: "5px 12px", borderRadius: "7px", background: "var(--color-accent)", color: "var(--accent-ink)" }}>
                    {"Save schedule"}
                  </button>
                  <span style={{ fontSize: "10.5px", color: "var(--color-neutral-500)" }}>
                    {vals.customSummary}
                  </span>
                </div>
              </div>
            ) : null}
            <div style={{ display: "flex", alignItems: "center", gap: "8px", flexWrap: "wrap" }}>
              <span style={LABEL}>{"Notify on"}</span>
              {(vals.chanOpts || []).map((o, $index) => (
                <React.Fragment key={$index}>
                  <button type="button" onClick={o.pick} aria-pressed={o.border === "var(--color-accent)"} style={chip(o)}>
                    {o.label}
                  </button>
                </React.Fragment>
              ))}
            </div>
          </div>
        </div>
        <div style={{ flex: "1", overflowY: "auto", padding: "14px 20px 40px", display: "flex", flexDirection: "column", gap: "9px" }}>
          {/* Not read yet: reading, or could not be read (with Retry). Never sample cards. */}
          <div role="status" style={{ display: vals.queueStateShow, fontSize: "12px", color: "var(--color-neutral-400)", lineHeight: "1.55", padding: "6px 2px" }}>
            <span>{vals.queueStateText}</span>
            <button type="button" className="hv13" onClick={vals.queueRetry} style={{ ...SMALL_BTN, display: vals.queueRetryShow, marginLeft: "10px", color: "var(--color-text)" }}>
              <i className="ph ph-arrow-clockwise" aria-hidden="true"></i>
              {"Retry"}
            </button>
          </div>
          <div style={{ display: vals.queueEmptyShow, fontSize: "11.5px", color: "var(--color-neutral-400)", lineHeight: "1.55", padding: "6px 2px" }}>
            {vals.queueEmptyNote}
          </div>
          <div style={{ display: vals.queueSnoozedShow, alignItems: "center", gap: "10px", flexWrap: "wrap", fontSize: "11px", color: "var(--color-neutral-500)", padding: "2px 2px 4px" }}>
            <i className="ph ph-moon" aria-hidden="true"></i>
            <span style={{ flex: "1", minWidth: "0" }}>{vals.queueSnoozedNote}</span>
            <button type="button" className="hv13" onClick={vals.queueToggleSnoozed} style={SMALL_BTN}>
              {vals.queueSnoozedToggleLabel}
            </button>
          </div>
          {/* Urgent — lapsed, blocked or critical — counted on the pill; then everything else. */}
          <div style={{ display: vals.queueUrgentShow, ...LABEL, color: "var(--color-text)" }}>{vals.queueUrgentLabel}</div>
          <div style={{ display: vals.queueNoUrgentShow, fontSize: "11.5px", color: "var(--color-neutral-400)", padding: "0 2px 4px" }}>{"Nothing urgent right now."}</div>
          {(vals.queueUrgentItems || []).map((d) => <QueueCard key={d.key} d={d} />)}
          <div style={{ display: vals.queueOtherShow, ...LABEL, marginTop: "12px" }}>{vals.queueOtherLabel}</div>
          {(vals.queueOtherItems || []).map((d) => <QueueCard key={d.key} d={d} />)}
          {/* Snoozed, shown on request: apart from the list above and outside every count. */}
          {(vals.queueSnoozedItems || []).length ? (
            <div style={{ ...LABEL, marginTop: "10px" }}>{"Snoozed · not counted"}</div>
          ) : null}
          {(vals.queueSnoozedItems || []).map((d) => <QueueCard key={d.key} d={d} />)}
        </div>
      </div>
    </>
  );
}
