// homeLive — the Home page read from svc-operations-intelligence.
//
// The same treatment the compliance console got (complianceLive.js): the backend reads are
// shaped into exactly what the tiles render, the seed stays as the fallback when nothing
// answers, and every tile says which it is showing. What the Plenum AI shell reads for its
// saved-space panels — the compliance summary, contract parameters, energy meters and
// anomalies, and the unified approvals queue — is what the Home page reads here.
//
//   Hoist Score   ingestion coverage per source, the mean of the sources that answered
//   Hoist Crons   what the engines did, newest first: approvals raised, anomalies detected
//   Hero line     buildings, certificates and countries from the live register
//
// Not sourced yet, and disclosed as such on the page: asset registers (the connector service
// is not part of this stack) and the budget-vs-actual P&L (no ledger in any backend).
//
// shapeLiveHome() is a pure function; the methods below are mixed into HoistraLogic.prototype
// and `this` is the controller.
import { opsApi } from '../api/opsIntelligence.js';
import { complianceApi } from '../api/compliance.js';

const RETRY_MS = 30000;
const RETRY_MAX = 6;
// The engines run on cron cycles; the Plenum panels re-read on the same cadence.
const REFRESH_MS = 15 * 60 * 1000;

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const pad2 = (n) => String(n).padStart(2, "0");
const pctOf = (n, d) => (d > 0 ? Math.round((100 * n) / d) : null);
const num = (v) => (typeof v === "number" && isFinite(v) ? v : null);
const gbp = (v) => "£" + Math.round(v).toLocaleString("en-GB");

// The four sources the Hoist Score reads. Order is the order the bars are drawn in.
const BARS = [
  { key: "contracts", label: "Contracts and framework agreements", short: "Contracts" },
  { key: "assets", label: "Asset registers", short: "Assets" },
  { key: "meters", label: "Meter consent — MPAN / MPRN", short: "Meter consent" },
  { key: "certificates", label: "Certificates and evidence", short: "Certificates" }
];

// At 85 the agents move from supervised to delegated dispatch; below 60 the graph is still
// being read and the score is a progress figure, not an autonomy grade.
export function bandOf(value) {
  if (value === null) return "No source yet";
  if (value >= 85) return "Delegated autonomy";
  if (value >= 60) return "Supervised autonomy";
  return "Ingestion in progress";
}
const barTone = (pct) => (pct === null ? "none" : pct >= 80 ? "ok" : pct >= 60 ? "warn" : "risk");

// Approvals carry the engine that raised them as a feature letter.
const AGENT = { A: "Compliance", B: "Vendor", C: "Energy" };
export function severityTone(sev) {
  const s = String(sev || "");
  if (/critical|lapsed|blocked/i.test(s)) return "risk";
  if (/action|medium|high|overdue/i.test(s)) return "warn";
  return "ok";
}
export function humanise(code) {
  return String(code || "").replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}

// "Today", "Yesterday", else "DD Mon" — relative to `now`, in local time.
export function dayLabel(at, now) {
  const d = new Date(at), n = new Date(now);
  const same = (a, b) => a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
  if (same(d, n)) return "Today";
  const y = new Date(n.getFullYear(), n.getMonth(), n.getDate() - 1);
  if (same(d, y)) return "Yesterday";
  return pad2(d.getDate()) + " " + MONTHS[d.getMonth()];
}
const clock = (at) => { const d = new Date(at); return pad2(d.getHours()) + ":" + pad2(d.getMinutes()); };
export function fmtDateTime(iso) {
  const d = iso ? new Date(iso) : null;
  if (!d || isNaN(d)) return "—";
  return pad2(d.getDate()) + " " + MONTHS[d.getMonth()] + " " + d.getFullYear() + " " + clock(d);
}

// The register's pseudo-buildings — certificates with no building named on them — are not
// buildings anyone hoisted.
const isBuilding = (b) => b && b.name && !/no building on certificate/i.test(b.name);
const COUNTRY_ORDER = ["UK", "US", "SG", "AE"];
// How the hero line names a market — the shell's spelling, not the ISO name.
export const COUNTRY_SHORT = { UK: "UK", US: "US", SG: "Singapore", AE: "UAE" };

// ── the shaping ─────────────────────────────────────────────────────────
// input.raw       the reads, each null when that request failed:
//                   compliance  GET /api/compliance/saved-space/summary
//                   contracts   GET /api/contract-performance/contracts
//                   meters      GET /api/energy/meters
//                   anomalies   GET /api/energy/anomalies
//                   approvals   GET /api/approvals
// input.register  the compliance console's live register (shapeLiveCompliance), or null
export function shapeLiveHome(input, now) {
  const raw = (input && input.raw) || {};
  const reg = (input && input.register) || null;
  const at = now || new Date();

  // ── Hoist Score: ingestion coverage per source ──
  const bars = BARS.map((b) => ({ key: b.key, label: b.label, short: b.short, pct: null, val: "—", tone: "none", note: "" }));
  const bar = (k) => bars.find((b) => b.key === k);
  const set = (k, pct, note) => { const x = bar(k); x.pct = pct; x.val = pct === null ? "—" : pct + "%"; x.tone = barTone(pct); x.note = note; };

  if (raw.compliance) {
    const b = raw.compliance.building_certificates || {}, v = raw.compliance.vendor_certificates || {};
    const on = (b.total || 0) + (v.total || 0);
    const missing = (b.not_on_record || 0) + (v.not_on_record || 0);
    set("certificates", pctOf(on, on + missing), on + " of " + (on + missing) + " expected certificate types on record");
  } else {
    set("certificates", null, "compliance engine did not answer");
  }

  if (raw.meters) {
    const ms = raw.meters.meters || [];
    const linked = ms.filter((m) => m.active !== false && m.site_id).length;
    set("meters", pctOf(linked, ms.length), ms.length ? linked + " of " + ms.length + " meters linked to a building" : "no meters on record");
  } else {
    set("meters", null, "energy engine did not answer");
  }

  if (raw.contracts) {
    const ps = raw.contracts.parameters || [];
    const vendorsInRegister = reg && reg.vendors ? reg.vendors.length : 0;
    if (vendorsInRegister) {
      const withTerms = ps.reduce((acc, p) => { if (p.vendor_id && acc.indexOf(p.vendor_id) < 0) acc.push(p.vendor_id); return acc; }, []).length;
      set("contracts", pctOf(withTerms, vendorsInRegister), withTerms + " of " + vendorsInRegister + " vendors with contract terms on record");
    } else if (ps.length) {
      const confirmed = ps.filter((p) => String(p.status || "").toLowerCase() === "confirmed").length;
      set("contracts", pctOf(confirmed, ps.length), confirmed + " of " + ps.length + " confirmed contract parameter sets");
    } else {
      set("contracts", null, "no contracts ingested yet");
    }
  } else {
    set("contracts", null, "contract engine did not answer");
  }

  // The asset register lives in the connector service, which this stack does not run yet.
  set("assets", null, "asset registers: the connector service is not connected yet");

  const sourced = bars.filter((b) => b.pct !== null);
  const value = sourced.length ? Math.round(sourced.reduce((a, b) => a + b.pct, 0) / sourced.length) : null;
  const lowest = sourced.slice().sort((a, b) => a.pct - b.pct)[0] || null;
  const unsourced = bars.filter((b) => b.pct === null).map((b) => b.short);
  const score = {
    value: value,
    band: bandOf(value),
    bars: bars,
    sourced: sourced.length,
    total: bars.length,
    gap: lowest ? lowest.short + " lowest at " + lowest.pct + "% — the gap to delegated autonomy" : "No source has answered yet.",
    note: value === null
      ? "No source has answered yet"
      : "Live · " + sourced.length + " of " + bars.length + " sources" + (unsourced.length ? " · " + unsourced.join(", ") + " unsourced" : "")
  };

  // ── Hoist Crons: what the engines did, newest first ──
  const crons = [];
  if (raw.approvals) {
    (raw.approvals.items || []).forEach((it) => {
      const ms = Date.parse(it.created_at);
      if (isNaN(ms)) return;
      const tone = severityTone(it.severity);
      crons.push({
        id: it.id, kind: "approval", at: ms, t: clock(ms), day: dayLabel(ms, at),
        text: it.summary || humanise(it.item_type),
        agent: AGENT[it.source_feature] || "Orchestrator",
        tone: tone,
        action: tone === "ok" ? null : "Review",
        item: it
      });
    });
  }
  if (raw.anomalies) {
    (raw.anomalies.anomalies || []).forEach((a) => {
      const ms = Date.parse(a.detected_at);
      if (isNaN(ms)) return;
      const open = String(a.status || "open").toLowerCase() === "open";
      const pct = num(a.metric_pct), cost = num(a.financial_gbp);
      crons.push({
        id: a.id, kind: "anomaly", at: ms, t: clock(ms), day: dayLabel(ms, at),
        text: humanise(a.anomaly_type) +
          (pct !== null ? " — " + Math.round(pct) + "% above baseline" : "") +
          (cost !== null ? " · " + gbp(cost) + "/yr" : ""),
        agent: "Energy",
        tone: open ? "warn" : "ok",
        action: open ? "Review" : null,
        item: a
      });
    });
  }
  crons.sort((a, b) => b.at - a.at);

  // ── Hero line ──
  const hero = { buildings: null, certificates: null, vendors: null, countries: [] };
  if (reg) {
    hero.buildings = (reg.buildings || []).filter(isBuilding).length;
    hero.certificates = (reg.certs || []).length;
    hero.vendors = (reg.vendors || []).length;
    const seen = {};
    (reg.certs || []).concat(reg.buildings || []).forEach((x) => { if (x.cc) seen[x.cc] = true; });
    hero.countries = Object.keys(seen).sort((a, b) => {
      const ia = COUNTRY_ORDER.indexOf(a), ib = COUNTRY_ORDER.indexOf(b);
      return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib) || a.localeCompare(b);
    });
  }

  const pending = raw.approvals
    ? (typeof raw.approvals.count === "number" ? raw.approvals.count : (raw.approvals.items || []).length)
    : null;

  const live = ["compliance", "contracts", "meters", "anomalies", "approvals"].some((k) => !!raw[k]);
  return { live: live, score: score, crons: crons, hero: hero, pending: pending };
}

// ── controller methods ──────────────────────────────────────────────────
export const homeLiveMethods = {
  // Shaped once per (reads, register) pair — renderVals runs on every keystroke and the
  // approvals feed is hundreds of rows.
  homeModel() {
    const raw = this.state.homeRaw, reg = this.state.ccLive;
    const m = this._homeMemo;
    if (m && m.raw === raw && m.reg === reg) return m.model;
    const model = shapeLiveHome({ raw: raw, register: reg });
    this._homeMemo = { raw: raw, reg: reg, model: model };
    return model;
  },
  homeIsLive() { return !!this.state.homeRaw; },

  // Every read is independent: a tile goes live when its source answers, and the page
  // only falls back to the seed wholesale when nothing answered at all.
  async homeLoad(opts) {
    if (this._homeLoading) return;
    this._homeLoading = true;
    clearTimeout(this._homeRetry);
    clearTimeout(this._homeRefresh);
    this.setState({ homeLoading: true });
    const reads = {
      approvals: () => opsApi.approvals(),
      compliance: () => complianceApi.savedSpaceSummary(),
      contracts: () => opsApi.contracts(),
      meters: () => opsApi.meters(),
      anomalies: () => opsApi.anomalies()
    };
    const keys = Object.keys(reads);
    const settled = await Promise.allSettled(keys.map((k) => reads[k]()));
    const raw = { fetchedAt: new Date().toISOString(), errors: {} };
    let answered = 0;
    settled.forEach((r, i) => {
      if (r.status === "fulfilled") { raw[keys[i]] = r.value; answered += 1; }
      else { raw[keys[i]] = null; raw.errors[keys[i]] = (r.reason && r.reason.message) || String(r.reason); }
    });
    this._homeLoading = false;
    if (!answered) {
      const msg = raw.errors[keys[0]] || "unreachable";
      this._homeAttempts = (this._homeAttempts || 0) + 1;
      this.setState({ homeLoading: false, homeError: msg });
      if (this._homeAttempts < RETRY_MAX) this._homeRetry = setTimeout(() => this.homeLoad(), RETRY_MS);
      if (opts && opts.announce) this.flash("Operations backend unreachable — " + msg);
      return;
    }
    this._homeAttempts = 0;
    this.setState({ homeRaw: raw, homeLoading: false, homeError: "", homeLoadedAt: raw.fetchedAt });
    if (opts && opts.announce) this.flash("Home refreshed — " + answered + " of " + keys.length + " sources answered");
    this._homeRefresh = setTimeout(() => this.homeLoad(), REFRESH_MS);
  },
  homeRetryNow() { this._homeAttempts = 0; return this.homeLoad({ announce: true }); },

  // The record behind a feed row, for the detail drawer. Read-only: deciding an approval
  // or acting on an anomaly is a write, and those stay in the console that owns them.
  cronDetail(c) {
    const it = c.item || {};
    if (c.kind === "anomaly") {
      const pct = num(it.metric_pct), cost = num(it.financial_gbp), kwh = num(it.annualised_excess_kwh);
      return {
        module: "Energy", icon: "ph-lightning", tone: c.tone, title: c.text,
        meta: "Detected " + fmtDateTime(it.detected_at) + " · " + humanise(it.status || "open") + " · svc-operations-intelligence / energy",
        body: "Detected by the energy engine against this meter's own baseline" +
          (kwh !== null ? ", " + Math.round(kwh).toLocaleString("en-GB") + " kWh a year above it" : "") +
          (cost !== null ? ", priced at " + gbp(cost) + " a year at the meter's tariff" : "") +
          ". Acting on it — acknowledge, monitor, mark expected — happens in the Energy space, not here.",
        fields: [
          { l: "Anomaly type", v: humanise(it.anomaly_type) },
          { l: "Deviation", v: pct !== null ? Math.round(pct) + "% above baseline" : "—" },
          { l: "Annualised cost", v: cost !== null ? gbp(cost) + "/yr" : "—" },
          { l: "Annualised excess", v: kwh !== null ? Math.round(kwh).toLocaleString("en-GB") + " kWh" : "—" },
          { l: "Meter", v: it.meter_id || "—" },
          { l: "Building", v: it.site_id || "not linked" },
          { l: "Status", v: humanise(it.status || "open") }
        ],
        chain: [
          { a: "Energy", t: "Half-hourly readings compared with the meter's weekday / weekend baseline" },
          { a: "Energy", t: "Deviation priced at the meter's tariff and annualised" },
          { a: "Quality", t: "Held as open until a person acknowledges it or marks it expected" }
        ],
        refinement: "Open the Energy space to see this meter's readings and the other anomalies on the same building.",
        actions: ["Open energy"]
      };
    }
    const draft = it.email_draft || {};
    const icon = c.agent === "Compliance" ? "ph-shield-check" : c.agent === "Vendor" ? "ph-chart-line-up" : "ph-lightning";
    return {
      module: c.agent, icon: icon, tone: c.tone, title: it.summary || c.text,
      meta: humanise(it.item_type) + " · " + (it.severity || "—") + " · raised " + fmtDateTime(it.created_at),
      body: "Queued for a decision by the " + c.agent.toLowerCase() + " engine. " +
        (draft.to ? "A draft email is attached; nothing has been sent. " : "") +
        "Approving, editing or dismissing it is done where the record lives — this card only shows what was raised.",
      fields: [
        { l: "Source", v: "svc-operations-intelligence · approvals queue" },
        { l: "Type", v: humanise(it.item_type) },
        { l: "Severity", v: it.severity || "—" },
        { l: "Status", v: humanise(it.status || "pending") },
        { l: "Raised", v: fmtDateTime(it.created_at) },
        { l: "Related record", v: it.related_entity_type ? humanise(it.related_entity_type) + " · " + (it.related_entity_id || "") : "—" },
        { l: "Email to", v: draft.to || "—" },
        { l: "Subject", v: draft.subject || "—" }
      ],
      chain: [
        { a: c.agent, t: "Raised by the engine's scan and written to the unified approvals queue" },
        { a: "Quality", t: "Nothing leaves the platform until a person decides it" }
      ],
      refinement: c.agent === "Compliance"
        ? "Open the compliance console to see the certificate behind this and every other item on the same holder."
        : "Open the " + c.agent.toLowerCase() + " space to act on this.",
      actions: c.agent === "Compliance" ? ["Open compliance console"] : c.agent === "Energy" ? ["Open energy"] : ["Open vendor performance"]
    };
  }
};
