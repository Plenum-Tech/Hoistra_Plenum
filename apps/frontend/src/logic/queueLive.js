// queueLive — the Decision queue drawer (the top bar's "N Pending"), shaped from the same
// live reads the pages already make. Nothing here fetches: the unified approvals queue and
// the energy anomalies arrive with the Home reads (homeLive.js), and the maintenance
// decisions with the Maintenance register (maintenanceLive.js) — all loaded once at mount.
//
//   approvals    GET /api/approvals?status=pending      compliance (A) and vendor (B) items
//   anomalies    GET /api/energy/anomalies              open ones only; an acknowledged
//                                                       anomaly is not waiting on anyone
//   decisions    GET /api/maintenance/decisions         blocked / awaiting / to raise
//
// "Ranked by consequence, not by age": risk first, then warn; within a band the priced
// items lead, largest first, and only then does recency order the rest. A £ tag appears
// only where the record carries a figure — an approval with no priced payload shows its
// severity word instead, never an invented amount.
//
// Until any of the three reads answers, the drawer keeps the seed cards it always showed
// (the Home page's pattern), so a cold start reads as loading rather than as empty.
import { humanise, severityTone, fmtDateTime } from './homeLive.js';
import { opsApi } from '../api/opsIntelligence.js';
import { isStaleScope } from '../api/client.js';

const num = (v) => (typeof v === "number" && isFinite(v) ? v : null);
const gbp = (v) => "£" + Math.round(v).toLocaleString("en-GB");
const AGENT = { A: "Compliance", B: "Vendors", C: "Energy" };
const ICON = { Compliance: "ph-shield-check", Vendors: "ph-chart-line-up", Energy: "ph-lightning", Maintenance: "ph-wrench" };
const TONE_RANK = { risk: 0, warn: 1, ok: 2, none: 2 };

// The consequence tag on an approvals card: the priced figure when the payload carries
// one, else the severity said plainly.
function approvalMoney(it) {
  const p = it.payload || {};
  const line = p.line || {};
  const delta = num(line.delta_gbp);
  if (delta !== null) return gbp(Math.abs(delta)) + " in dispute";
  const fin = num(p.financial_gbp);
  if (fin !== null) return gbp(fin) + "/yr";
  const sev = String(it.severity || "").trim();
  return sev ? humanise(sev) + " severity" : "Pending";
}

export function shapeLiveQueue(input, now) {
  const raw = input || {};
  const at = now || new Date();
  const items = [];

  // One card per underlying thing. The energy engine writes each anomaly twice — the
  // anomaly row, and an approvals-queue echo of it (enqueue_approval with
  // related_entity_type "energy_anomaly" and payload.anomaly_id) — so a queue that read
  // both showed the same meter twice with the engine's raw sentence on one of them. When
  // the anomalies read answered, the anomaly row is the record and the echo is dropped;
  // when it did not, the echo is all there is and it stays.
  const anomalyIds = {};
  if (raw.anomalies) (raw.anomalies.anomalies || []).forEach((a) => { if (a.id) anomalyIds[String(a.id)] = true; });

  // ── the unified approvals queue: compliance and vendor items ──
  if (raw.approvals) {
    (raw.approvals.items || []).forEach((it) => {
      const echoOf = String(((it.payload || {}).anomaly_id) || (it.related_entity_type === "energy_anomaly" && it.related_entity_id) || "");
      if (echoOf && anomalyIds[echoOf]) return;
      const module = AGENT[it.source_feature] || "Compliance";
      const ms = Date.parse(it.created_at);
      items.push({
        kind: "approval",
        module: module,
        icon: ICON[module],
        tone: severityTone(it.severity),
        title: it.summary || humanise(it.item_type),
        meta: humanise(it.item_type) + " · raised " + fmtDateTime(it.created_at),
        money: approvalMoney(it),
        moneyValue: num((it.payload || {}).financial_gbp) || Math.abs(num(((it.payload || {}).line || {}).delta_gbp) || 0) || null,
        at: isNaN(ms) ? 0 : ms,
        key: "approval:" + it.id,
        item: it
      });
    });
  }

  // ── open energy anomalies: detected, priced, waiting for a person ──
  if (raw.anomalies) {
    (raw.anomalies.anomalies || []).forEach((a) => {
      if (String(a.status || "open").toLowerCase() !== "open") return;
      const pct = num(a.metric_pct), cost = num(a.financial_gbp);
      const ms = Date.parse(a.detected_at);
      items.push({
        kind: "anomaly",
        module: "Energy",
        icon: ICON.Energy,
        tone: "warn",
        title: humanise(a.anomaly_type) + (a.meter_id ? " — meter " + a.meter_id : ""),
        meta: "Detected " + fmtDateTime(a.detected_at) +
          (pct !== null ? " · " + Math.round(pct) + "% above baseline" : ""),
        money: cost !== null ? gbp(cost) + "/yr" : "Unpriced",
        moneyValue: cost,
        at: isNaN(ms) ? 0 : ms,
        key: "anomaly:" + a.id,
        item: a
      });
    });
  }

  // ── maintenance decisions owed: blocked, awaiting approval, to raise ──
  // Already shaped rows (maintenanceLive.shapeDecision); `d` is kept whole so the click
  // can open the exact detail the Maintenance page's own row opens.
  (raw.decisions || []).forEach((d) => {
    items.push({
      kind: "decision",
      module: "Maintenance",
      icon: ICON.Maintenance,
      tone: d.state === "Blocked" || d.state === "Deviation" ? "risk" : "warn",
      title: (d.id && d.id !== "—" ? d.id + " — " : "") + d.asset,
      meta: d.b + " · " + d.detail,
      // The pill names the estimate when one is priced, else the state — "—" in a pill
      // would read as a figure somebody computed and found empty.
      money: d.estimated !== null ? d.est + " estimate" : d.state + (d.statutory ? " · statutory" : ""),
      moneyValue: d.estimated,
      at: 0,
      key: "decision:" + (d.id || d.asset + "·" + d.b + "·" + d.state),
      d: d
    });
  });

  items.sort((a, b) =>
    (TONE_RANK[a.tone] - TONE_RANK[b.tone])
    || ((b.moneyValue || 0) - (a.moneyValue || 0))
    || (b.at - a.at));

  const live = !!(raw.approvals || raw.anomalies || raw.decisionsAnswered);
  const byModule = {};
  items.forEach((i) => { byModule[i.module] = (byModule[i.module] || 0) + 1; });
  return { live: live, count: live ? items.length : null, items: items, byModule: byModule, at: at };
}

//: The drawer's "Show" filter. Fixed order and always all four, zero or not — a chip that
//: vanished when its module had nothing waiting would read as "this module is not wired",
//: when "Vendors 0" is the fact worth seeing.
export const QUEUE_FILTERS = ["All", "Energy", "Vendors", "Compliance", "Maintenance"];

// The items the drawer shows under a filter. Pure.
export function filterQueue(items, filter) {
  if (!filter || filter === "All") return items || [];
  return (items || []).filter((i) => i.module === filter);
}

// When the next scheduled queue refresh fires, in ms from `now`. Pure, so the arithmetic
// is testable without timers. null = no schedule (On demand).
//   freq    On demand | 30 min | Hourly | Nightly 02:00 | Custom
//   custom  { cFreq, cDate, cTime } — read only when freq is Custom
// Daily / Weekly / Monthly are anchored on cDate + cTime and stepped forward to the first
// occurrence after `now`, so re-arming never pushes the run further away.
export function nextQueueDelay(freq, custom, now) {
  const at = now || new Date();
  const MIN = 60000;
  const hm = (v) => {
    const m = /^(\d{1,2}):(\d{2})$/.exec(String(v || "").trim());
    return m && Number(m[1]) < 24 && Number(m[2]) < 60 ? [Number(m[1]), Number(m[2])] : [2, 0];
  };
  const nextDaily = (hhmm) => {
    const [h, mi] = hm(hhmm);
    const next = new Date(at);
    next.setHours(h, mi, 0, 0);
    if (next <= at) next.setDate(next.getDate() + 1);
    return next - at;
  };
  if (freq === "30 min") return 30 * MIN;
  if (freq === "Hourly") return 60 * MIN;
  if (freq === "Nightly 02:00") return nextDaily("02:00");
  if (freq !== "Custom") return null;
  const c = custom || {};
  const fixed = { "Every 15 min": 15 * MIN, "Every 30 min": 30 * MIN, "Hourly": 60 * MIN, "Every 6 hours": 360 * MIN };
  if (fixed[c.cFreq]) return fixed[c.cFreq];
  if (c.cFreq !== "Weekly" && c.cFreq !== "Monthly") return nextDaily(c.cTime);
  const [h, mi] = hm(c.cTime);
  const d = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(c.cDate || ""));
  const next = d ? new Date(Number(d[1]), Number(d[2]) - 1, Number(d[3]), h, mi, 0, 0) : new Date(at);
  if (!d) next.setHours(h, mi, 0, 0);
  const day = next.getDate();
  for (let i = 0; next <= at && i < 1000; i++) {
    if (c.cFreq === "Weekly") next.setDate(next.getDate() + 7);
    else {
      // The anchor's day of month, clamped to shorter months (31 → 30 / 28).
      next.setDate(1);
      next.setMonth(next.getMonth() + 1);
      const last = new Date(next.getFullYear(), next.getMonth() + 1, 0).getDate();
      next.setDate(Math.min(day, last));
    }
  }
  return next - at;
}

//: What the queue remembers between visits — the schedule and channels are the reader's
//: own settings, like their saved reports, so they live in this browser. Wrapped: private
//: windows and blocked storage must not take the drawer down.
const QUEUE_PREFS_KEY = "hoistra.queue.v1";
export function loadQueuePrefs() {
  try {
    const d = JSON.parse(localStorage.getItem(QUEUE_PREFS_KEY) || "null") || {};
    const out = {};
    if (["On demand", "30 min", "Hourly", "Nightly 02:00", "Custom"].includes(d.freq)) out.freq = d.freq;
    if (typeof d.cFreq === "string") out.cFreq = d.cFreq;
    if (typeof d.cDate === "string") out.cDate = d.cDate;
    if (typeof d.cTime === "string") out.cTime = d.cTime;
    if (Array.isArray(d.channels)) out.channels = d.channels.filter((c) => ["In-platform", "Push"].includes(c));
    if (QUEUE_FILTERS.includes(d.queueFilter)) out.queueFilter = d.queueFilter;
    return out;
  } catch (e) { return {}; }
}
function saveQueuePrefs(s) {
  try {
    localStorage.setItem(QUEUE_PREFS_KEY, JSON.stringify({
      freq: s.freq, cFreq: s.cFreq, cDate: s.cDate, cTime: s.cTime, channels: s.channels,
      queueFilter: s.queueFilter
    }));
  } catch (e) { /* per-viewer convenience only */ }
}

// ── controller methods ──────────────────────────────────────────────────
export const queueLiveMethods = {
  // Shaped once per (homeRaw, mxRaw) pair — renderVals runs on every keystroke and the
  // approvals feed alone can be hundreds of rows.
  queueModel() {
    const hr = this.state.homeRaw, mr = this.state.mxRaw;
    const m = this._queueMemo;
    if (m && m.hr === hr && m.mr === mr) return m.model;
    const mx = this.mxModel();
    const model = shapeLiveQueue({
      approvals: (hr && hr.approvals) || null,
      anomalies: (hr && hr.anomalies) || null,
      decisions: mx.live ? mx.decisions : [],
      decisionsAnswered: mx.live
    });
    this._queueMemo = { hr: hr, mr: mr, model: model };
    return model;
  },

  // Restore the saved schedule and channels, then arm the timer. Called from loadLiveData,
  // so a sign-in re-arms it the same way a reload does; idempotent.
  queueBoot() {
    const saved = loadQueuePrefs();
    if (Object.keys(saved).length) this.setState(saved);
    this.queueSchedule();
  },

  // Arm (or disarm) the refresh timer for the chosen cadence. One timeout, re-armed after
  // each fire, so a change of cadence never leaves a second timer running.
  queueSchedule() {
    clearTimeout(this._qTimer);
    this._qTimer = null;
    if (!this.state.signedIn) return;
    const delay = nextQueueDelay(this.state.freq, this.state, new Date());
    if (delay === null) return;
    // setTimeout overflows past 2^31-1 ms (~24.8 days) and fires at once — a monthly run
    // would then refresh in a tight loop. Anything beyond a day waits a day and re-plans.
    const DAY = 24 * 3600000;
    if (delay > DAY) {
      this._qTimer = setTimeout(() => this.queueSchedule(), DAY);
    } else {
      this._qTimer = setTimeout(async () => {
        await this.queueRefresh();
        this.queueSchedule();
      }, Math.max(delay, 60000));
    }
    // In Node (the test runner) a pending timer holds the process open after the suite
    // finishes; in a browser unref does not exist and the timer behaves as before.
    if (this._qTimer && typeof this._qTimer.unref === "function") this._qTimer.unref();
  },

  // Re-read the queue's sources and say what arrived. The reads are the pages' own
  // (homeLoad, mxLiveLoad — both GETs); nothing is fetched twice on the way in.
  // "New" means not yet reported to this reader — not "absent from the model a moment ago".
  // The Home and Maintenance loaders re-read on their own 15-minute cycle, so by the time a
  // scheduled tick fires the item is often already in the model; diffing against the model
  // would then never find anything and the notice would never be sent.
  queueSeenInit() {
    if (this._qSeen) return;
    const m = this.queueModel();
    if (!m.live) return;
    this._qSeen = {};
    m.items.forEach((i) => { this._qSeen[i.key] = true; });
  },

  // Re-reads only what the drawer draws from: the approvals queue and the open anomalies,
  // patched into homeRaw. Re-running homeLoad here re-ran the compliance summary, the hoist
  // score and the value summary (a dozen aggregates) every tick of every open tab, for data
  // the drawer never shows. Maintenance decisions keep their page's own 15-minute cycle.
  async queueRefresh(opts) {
    this.queueSeenInit();
    const [ap, an] = await Promise.allSettled([opsApi.approvals(), opsApi.anomalies()]);
    // A read that outlived the company it was issued under: the other half may have landed
    // for the old company, and patching it into the new company's (reset) homeRaw would
    // show company A's queue under company B. Drop the whole tick, as homeLoad does.
    if ([ap, an].some((r) => r.status === "rejected" && isStaleScope(r.reason))) return this.queueModel();
    const patch = {};
    if (ap.status === "fulfilled" && ap.value) patch.approvals = ap.value;
    if (an.status === "fulfilled" && an.value) patch.anomalies = an.value;
    if (Object.keys(patch).length) {
      this.setState((p) => ({ homeRaw: Object.assign({}, p.homeRaw || {}, patch) }));
    }
    const model = this.queueModel();
    if (!model.live) return model;
    if (!this._qSeen) { this.queueSeenInit(); return model; }
    const fresh = model.items.filter((i) => !this._qSeen[i.key]);
    fresh.forEach((i) => { this._qSeen[i.key] = true; });
    if (!fresh.length || (opts && opts.silent)) return model;
    const line = fresh.length + (fresh.length === 1 ? " new decision — " : " new decisions — newest: ") + fresh[0].title;
    if (this.state.channels.includes("In-platform")) this.flash("Decision queue: " + line);
    // Browser push — only ever after the person granted it (queueToggleChannel).
    if (this.state.channels.includes("Push")) {
      try {
        if (typeof Notification !== "undefined" && Notification.permission === "granted") {
          new Notification("Hoistra — decision queue", { body: line });
        }
      } catch (e) { /* a blocked notification must not break the refresh */ }
    }
    return model;
  },

  queueSetFilter(f) {
    if (!QUEUE_FILTERS.includes(f)) return;
    this.setState({ queueFilter: f }, () => saveQueuePrefs(this.state));
  },

  queueSetFreq(o) {
    this.setState({ freq: o }, () => { saveQueuePrefs(this.state); this.queueSchedule(); });
  },

  queueSaveCustom() {
    // Saving IS the act: the timer is armed to the stated schedule. This used to hand the
    // words to the chat orchestrator, which scheduled nothing.
    this.setState({ freq: "Custom" }, () => {
      saveQueuePrefs(this.state);
      this.queueSchedule();
      const mins = nextQueueDelay("Custom", this.state, new Date());
      this.flash("Schedule saved — " + this.state.cFreq + " at " + this.state.cTime +
        (mins !== null ? " · next check " + (mins < 3600000 ? "in " + Math.round(mins / 60000) + " min" : "in " + Math.round(mins / 3600000) + "h") : ""));
    });
  },

  // The channels are honest about what exists. In-platform is the toast this app shows;
  // Push is the browser's own notification, granted by the person; Email and SMS have no
  // sending channel wired to this queue on any backend — offering them as selected would
  // promise a message nobody would ever send.
  queueToggleChannel(o) {
    const s = this.state;
    const on = s.channels.includes(o);
    if (on) {
      const next = s.channels.filter((c) => c !== o);
      return this.setState({ channels: next }, () => saveQueuePrefs(this.state));
    }
    if (o === "Email" || o === "SMS") {
      return this.flash("No " + (o === "Email" ? "email" : "SMS") + " notification channel is connected to the decision queue yet — nothing would be sent, so it stays off.");
    }
    if (o === "Push") {
      if (typeof Notification === "undefined") return this.flash("This browser does not support notifications.");
      const grant = (perm) => {
        if (perm !== "granted") return this.flash("Notifications are blocked in the browser, so Push stays off.");
        this.setState((p) => ({ channels: p.channels.concat(["Push"]) }), () => saveQueuePrefs(this.state));
      };
      try {
        if (Notification.permission === "granted") return grant("granted");
        return Notification.requestPermission().then(grant);
      } catch (e) { return this.flash("Notifications are blocked in the browser, so Push stays off."); }
    }
    this.setState((p) => ({ channels: p.channels.concat([o]) }), () => saveQueuePrefs(this.state));
  }
};
