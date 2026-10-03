// maintenanceLive — the whole Maintenance page, read from svc-work-order-management's
// /api/maintenance routes (api/workOrder.js).
//
// This model is the page. There is no seed fallback behind it: a decision, a card, a PPM
// row or a report figure reaches the screen only because one of the reads below returned
// it. When none of them answer the page is empty and says which service did not answer,
// rather than filling itself in from src/data/hoistra-maintenance.js.
//
//   Four cards        GET /api/maintenance/overview                  (each with its sub-counts)
//   Decisions grid    GET /api/maintenance/decisions?group_by=…      (grouping is server-side)
//   Inspection panel  GET /api/maintenance/inspection-intelligence   (corpus + four cards)
//   PPM health        GET /api/maintenance/ppm/contracts             (one row per contract)
//   Last run          GET /api/maintenance/inspection-intelligence/last-read
//   Ask chips         GET /api/maintenance/ask/suggestions?page=…
//
// Scope is the caller's, and it is the server's to decide: none of these reads sends a
// building_id, so each returns every building this account may see — the allocation for a
// user, and nothing for a user allocated to none. That last case is the one the page must
// not render as "no work to do", so the model reports it separately (`unallocated` below).
//
// A superadmin acting as a company sends `organization_id` on every read, the same
// parameter svc-operations-intelligence takes ("Superadmin only: act as this company").
// Before that existed on this service, a superadmin read every company's work orders
// whatever the header said they were viewing, and two companies showed identical figures
// because they were identical. A superadmin who has chosen NO company still reads across
// all of them — that is the one caller meant to — and the page says so rather than letting
// a platform total pass for one company's (`crossCompany` below).
//
// Not sourced — no read returns it — and therefore "—" rather than a number: the per-group
// money total where nothing in the group carries an estimate (the backend sends null, not
// 0, for exactly that reason), and the next-visit date on a contract with none booked.
//
// A card the database cannot answer comes back `answerable: false` with a reason. That is
// not zero: zero reads as "we checked and there are none", which is a different and wrong
// claim, so those cards render as unavailable and say why.
//
// shapeLiveMaintenance() is a pure function; the methods below are mixed into
// HoistraLogic.prototype and `this` is the controller.
import { workOrderApi } from '../api/workOrder.js';
import { isStaleScope } from '../api/client.js';
import { isUnallocated } from './auth.js';

const RETRY_MS = 30000;
const RETRY_MAX = 6;
// The decisions queue moves as people work it; the same cadence the other registers use.
const REFRESH_MS = 15 * 60 * 1000;

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const num = (v) => (typeof v === "number" && isFinite(v) ? v : (typeof v === "string" && v.trim() !== "" && isFinite(Number(v)) ? Number(v) : null));
const lower = (s) => String(s || "").toLowerCase();

// "YYYY-MM-DD" → "DD Mon YYYY", read as a calendar date so a UTC midnight never slips a day.
export function fmtDay(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso || ""));
  return m ? m[3] + " " + MONTHS[Number(m[2]) - 1] + " " + m[1] : null;
}

// Money as the page writes it. `null` in, "—" out: a group whose cost nothing recorded must
// not read as a group that costs nothing.
export function money(v, currency) {
  const n = num(v);
  if (n === null) return "—";
  const sym = currency && currency !== "GBP" ? currency + " " : "£";
  const abs = Math.abs(n);
  if (abs >= 1000) return "~" + sym + (Math.round(n / 100) / 10) + "k";
  return sym + Math.round(n).toLocaleString("en-GB");
}

// The four states, in the order the screen lists them, with the line under each heading.
export const STATE_ORDER = { "Blocked": 0, "To raise": 1, "Deviation": 2, "Awaiting approval": 3 };
export const STATE_TONE = { "Blocked": "risk", "To raise": "warn", "Deviation": "risk", "Awaiting approval": "warn" };
export const STATE_DESC = {
  "Blocked": "cannot proceed until a vendor or certificate is fixed",
  "To raise": "another module says an order should exist; none does",
  "Deviation": "live orders drifting off SLA or certificate",
  "Awaiting approval": "drafted, waiting for you"
};
// The module that raised a decision → its icon in the grid.
export const SOURCE_ICON = {
  Compliance: "ph-shield-check", Vendors: "ph-chart-line-up", Assets: "ph-cube",
  Energy: "ph-lightning", Maintenance: "ph-wrench"
};

// One decision row from the backend → the row the grid renders. Anything the record does not
// carry is a dash, never a stand-in: a decision with no estimate on it is not a free one.
export function shapeDecision(d) {
  const r = d || {};
  const cert = r.statutory_certificate || null;
  return {
    id: r.work_order || null,
    asset: r.asset || "—",
    b: r.building || "—",
    vendor: r.vendor || "—",
    est: money(r.estimated_cost, r.currency),
    estimated: num(r.estimated_cost),
    state: r.state || "—",
    src: r.source || "—",
    trigger: r.trigger || "—",
    detail: r.detail || "—",
    priority: r.priority || null,
    due: fmtDay(r.due),
    assetId: r.asset_id || null,
    vendorId: r.vendor_id || null,
    buildingId: r.building_id || null,
    // Statutory is a property of the decision, not a guess: the backend names the
    // certificate forcing it and how it matched.
    statutory: !!cert,
    statutoryNote: cert
      ? (cert.name || "A certificate") + (cert.expiry_date ? " · " + (fmtDay(cert.expiry_date) || cert.expiry_date) : "")
        + (cert.matched_on ? " · matched on " + String(cert.matched_on).replace(/_/g, " ") : "")
      : ""
  };
}

// The four cards across the top. Each value is the backend's, and `null` stays null — a
// card the read did not carry shows "—" rather than a zero nobody computed.
function overviewCards(overview) {
  const c = (overview && overview.cards) || {};
  const owed = c.decisions_owed || {};
  const stat = c.statutory || {};
  const rec = c.recommendations_unconverted || {};
  const ppm = c.ppm_to_plan || {};
  return [
    { k: "decisions_owed", l: "Decisions owed", v: num(owed.value), unit: "",
      s: owed.caption || null, tone: "risk", answerable: true },
    { k: "statutory", l: "Statutory among them", v: num(stat.value), unit: "",
      s: stat.caption || null, tone: "warn", answerable: true },
    { k: "recommendations_unconverted", l: "Recommendations unconverted", v: num(rec.value), unit: "",
      s: rec.caption || null, tone: "warn",
      // This one alone can be unanswerable — it reads the inspection corpus.
      answerable: rec.answerable !== false, reason: rec.reason || null },
    { k: "ppm_to_plan", l: "PPM to plan", v: num(ppm.value), unit: "%",
      s: ppm.caption || null, tone: num(ppm.missed) > 5 ? "risk" : "warn", answerable: true }
  ];
}

// The inspection-intelligence panel. The backend writes each headline itself, because the
// sentence and the number are the same claim and splitting them invites them to disagree.
function intelligenceCards(ii) {
  const cards = (ii && ii.cards) || {};
  const spec = [
    { k: "unconverted_recommendations", slug: "unconverted-recommendations", tone: "risk",
      l: "recommendations never converted to orders", q: "Which recommendations were never converted to orders?" },
    { k: "corroborated_anomalies", slug: "corroborated-anomalies", tone: "warn",
      l: "open anomalies corroborated by an earlier finding", q: "Which reports confirm the energy anomalies?" },
    { k: "warranted_findings", slug: "warranted-findings", tone: "ok",
      l: "findings on parts still under warranty", q: "What is under warranty?" },
    { k: "poorly_graded", slug: "poorly-graded", tone: "warn",
      l: "assets graded poor (4 of 5) by inspectors", q: "Which assets are in the worst condition?" }
  ];
  return spec.map((x) => {
    const c = cards[x.k] || {};
    const answerable = c.answerable !== false;
    // "6 of 8" — the corroborated card counts against the anomalies open, not on its own.
    const n = x.k === "corroborated_anomalies" && num(c.count) !== null && num(c.open_anomalies) !== null
      ? c.count + " of " + c.open_anomalies
      : (num(c.count) === null ? null : String(c.count));
    return {
      key: x.k, slug: x.slug, tone: x.tone, q: x.q, answerable: answerable,
      n: answerable ? n : null,
      l: x.l,
      // The backend's own headline is the sub-line, minus the count it repeats.
      s: answerable ? subLine(c.headline, x.l) : (c.reason || "This database cannot answer that card."),
      method: c.method || null
    };
  });
}

// The headline reads "7 recommendations never converted to orders — 5 on assets now flagged
// by energy". The number and the label are already the card's own two lines, so the sub-line
// is what comes after the dash; a headline with no dash has nothing more to add.
function subLine(headline, label) {
  const h = String(headline || "");
  if (!h) return null;
  const i = h.indexOf(" — ");
  if (i > -1) return h.slice(i + 3);
  // Some headlines put the extra in a trailing clause instead.
  const after = h.toLowerCase().indexOf(lower(label));
  return after > -1 ? (h.slice(after + label.length).replace(/^[\s,·—-]+/, "") || null) : h;
}

// One PPM contract row. The state is the backend's — the rule lives there, with a test that
// reproduces the eight rows on the page exactly — so nothing is re-derived here.
function ppmRow(p) {
  const r = p || {};
  const plan = r.visits_to_plan || {};
  const done = num(plan.done), planned = num(plan.plan);
  const filed = r.reports_to_done || {};
  const pct = num(r.completion_pct);
  const state = r.state || "—";
  const tone = state === "behind plan" ? "risk" : state === "watch" ? "warn" : "ok";
  const repPct = num(filed.done) ? Math.round((num(filed.filed) / num(filed.done)) * 100) : null;
  const notes = [];
  if (num(r.deferred)) notes.push(r.deferred + " deferred");
  if (r.note) notes.push(r.note);
  return {
    contract: r.contract || "—",
    vendor: [r.vendor, r.country_code].filter(Boolean).join(" · ") || "—",
    scope: r.service_scope || "—",
    done: done === null || planned === null ? "—" : done + " / " + planned,
    // The denominator is either what the contract commits to or simply what got booked —
    // different claims, so the row says which rather than letting the reader assume.
    planCommitted: plan.plan_is_committed === true,
    planNote: plan.plan_is_committed === true ? "against the committed plan" : "against visits booked",
    pct: pct === null ? "—" : Math.round(pct) + "%",
    bar: pct === null ? "0%" : Math.max(0, Math.min(100, Math.round(pct))) + "%",
    missed: num(r.missed) === null ? "—" : String(r.missed),
    late: num(r.late) === null ? "—" : String(r.late),
    reports: num(filed.filed) === null || num(filed.done) === null ? "—" : filed.filed + " / " + filed.done,
    repPct: repPct === null ? "—" : repPct + "%",
    next: r.next_due ? (fmtDay(r.next_due) || r.next_due) : (state === "behind plan" && num(r.missed) ? "blocked" : "—"),
    nextIsBlocked: !r.next_due && state === "behind plan" && !!num(r.missed),
    deferrals: notes.join(" · "),
    tone: tone, state: state,
    missedOn: !!num(r.missed), lateOn: !!num(r.late),
    repLow: repPct !== null && repPct < 90,
    buildings: num(r.buildings)
  };
}

// ── the shaping ─────────────────────────────────────────────────────────
// input: the reads, each null / missing when that request failed
//   overview      GET /api/maintenance/overview
//   decisions     GET /api/maintenance/decisions?group_by=…
//   intelligence  GET /api/maintenance/inspection-intelligence
//   ppm           GET /api/maintenance/ppm/contracts
//   lastRead      GET /api/maintenance/inspection-intelligence/last-read
//   chips         GET /api/maintenance/ask/suggestions?page=maintenance
// account: the signed-in account, for the scope line only — the boundary itself is the
// server's, applied to the token; this just says out loud which one was applied.
export function shapeLiveMaintenance(input, account, actingName) {
  const raw = input || {};
  const overview = raw.overview || null;
  const decisions = raw.decisions || null;
  const intelligence = raw.intelligence || null;
  const ppm = raw.ppm || null;
  const live = !!(overview || decisions || intelligence || ppm);

  const a = account || null;
  const superadmin = !!a && a.role === "superadmin";
  const admin = !!a && (a.role === "admin" || superadmin);
  const unallocated = isUnallocated(a);
  // A superadmin reads across companies on this service whatever the header says, so the
  // acting company is named as what it is NOT narrowing rather than as the scope.
  // Acting as a company narrows the reads (organization_id on every one), so the only
  // caller still reading across companies is a superadmin who has not chosen one.
  const crossCompany = superadmin && !unallocated && !actingName;
  const scope = !a ? null
    : unallocated ? "You are allocated to no buildings, so there is nothing in scope"
    : crossCompany ? "Every building in every company"
    : actingName ? "Every building in " + actingName
    : admin || a.all_buildings === true ? "Every building in " + (a.organization_name || "your company")
    : "Your buildings only";

  const empty = {
    live: false, admin: admin, superadmin: superadmin, crossCompany: crossCompany,
    actingName: actingName || null, unallocated: unallocated, scope: scope,
    cards: overviewCards(null), decisions: [], groups: null, groupBy: null,
    total: null, byState: {}, bySource: {}, available: { state: [], source: [] },
    intelligence: intelligenceCards(null), corpus: null, unanswerable: [],
    reports: [], reportsOpen: null, byRisk: {},
    ppm: [], ppmSummary: null, lastRead: null, chips: []
  };
  if (!live) return empty;

  const rows = ((decisions && decisions.decisions) || []).map(shapeDecision);
  const groups = decisions && Array.isArray(decisions.groups)
    ? decisions.groups.map((g) => ({
        name: g.key,
        n: num(g.count),
        blocked: num(g.blocked), toRaise: num(g.to_raise),
        deviating: num(g.deviating), awaiting: num(g.awaiting_approval),
        // null, not 0 — see money(). `priced` says how many of the group carry one at all.
        total: money(g.estimated_cost),
        priced: num(g.priced),
        items: (g.decisions || []).map(shapeDecision)
      }))
    : null;

  const ii = intelligence || null;
  const corpus = (ii && ii.corpus) || null;
  const sum = (ppm && ppm.summary) || null;
  const win = (ppm && ppm.window) || null;

  return {
    live: true, admin: admin, superadmin: superadmin, crossCompany: crossCompany,
    actingName: actingName || null, unallocated: unallocated, scope: scope,
    cards: overviewCards(overview),
    decisions: rows,
    groups: groups,
    groupBy: (decisions && decisions.group_by) || null,
    total: decisions ? num(decisions.total) : null,
    count: decisions ? num(decisions.count) : null,
    byState: (decisions && decisions.by_state) || {},
    bySource: (decisions && decisions.by_source) || {},
    available: (decisions && decisions.available) || { state: [], source: [] },
    intelligence: intelligenceCards(ii),
    corpus: corpus,
    // Named so the panel can say which cards it could not answer, rather than showing four
    // and leaving the reader to work out that two of them mean nothing.
    unanswerable: (ii && ii.unanswerable) || [],
    ppm: ((ppm && ppm.contracts) || []).map(ppmRow),
    ppmSummary: sum ? {
      contracts: num(sum.contracts), done: num(sum.done), plan: num(sum.plan),
      missed: num(sum.missed), late: num(sum.late), deferred: num(sum.deferred),
      reports: num(sum.reports), completion: num(sum.completion_pct),
      behind: num(sum.behind_plan), watch: num(sum.watch), toPlan: num(sum.to_plan),
      yearToDate: !!(win && win.year_to_date)
    } : null,
    ppmRule: (ppm && ppm.rule) || null,
    // The reports themselves, behind "Open all reports". A grade the report does not carry
    // is a dash: an ungraded report is not a report graded 1.
    reports: (((raw.inspections && raw.inspections.inspections) || [])).map((r) => ({
      wo: r.work_order_id || r.converted_work_order_id || "—",
      asset: r.asset_name || r.asset_code || "—",
      b: r.building || "—",
      date: fmtDay(r.inspection_date) || "—",
      vendor: r.inspector || "—",
      type: r.finding_type || r.section || "—",
      risk: r.risk_level || null,
      findings: r.observations || r.findings || "—",
      rec: r.recommendation || r.corrective_action || "None",
      recOpen: !!r.recommendation_open,
      warranty: r.warranty || null,
      sourceFile: r.source_file || null
    })),
    reportsOpen: (raw.inspections && num(raw.inspections.recommendations_open)),
    byRisk: (raw.inspections && raw.inspections.by_risk) || {},
    lastRead: (raw.lastRead && raw.lastRead.last_read) || null,
    chips: ((raw.chips && raw.chips.suggestions) || []).map((c) => (typeof c === "string" ? { question: c } : c))
  };
}

// The reads this page makes, and what each one fills — so a failure names the panel a person
// can see rather than the key it happens to be stored under.
const READS = ['overview', 'decisions', 'intelligence', 'ppm', 'lastRead', 'inspections', 'chips'];
const READ_LABEL = {
  overview: '/maintenance/overview',
  decisions: '/maintenance/decisions',
  intelligence: '/maintenance/inspection-intelligence',
  ppm: '/maintenance/ppm/contracts',
  lastRead: '/maintenance/inspection-intelligence/last-read',
  inspections: '/maintenance/inspections',
  chips: '/maintenance/ask/suggestions'
};

export const maintenanceLiveMethods = {
  // Shaped once per load — renderVals runs on every keystroke.
  mxModel() {
    const raw = this.state.mxRaw;
    const acct = this.state.account;
    // The company the header says is being viewed. In the memo key because switching it must
    // re-shape — even though, on this service, it changes nothing about what came back.
    const acting = this.state.viewOrgId ? (this.state.viewOrgName || null) : null;
    const m = this._mxMemo;
    if (m && m.raw === raw && m.acct === acct && m.acting === acting) return m.model;
    const model = shapeLiveMaintenance(raw, acct, acting);
    this._mxMemo = { raw: raw, acct: acct, acting: acting, model: model };
    return model;
  },
  mxIsLive() { return !!this.state.mxRaw; },

  // Every read is independent: the page goes live when any of them answers, and stays empty
  // — saying so — when none did. The grouping is a server parameter, so changing "Group by"
  // re-reads rather than regrouping a list the server already cut differently.
  async mxLiveLoad(opts) {
    if (this._mxLiveLoading) return;
    this._mxLiveLoading = true;
    clearTimeout(this._mxLiveRetry);
    clearTimeout(this._mxLiveRefresh);
    this.setState({ mxLiveLoading: true });
    const groupBy = lower(this.state.mxGroup || "State");
    // The filter chips are server parameters too: a state or a source, never both, because
    // that is what the route accepts and what the row of chips actually offers.
    const f = this.state.filter && this.state.filter !== 'All' ? this.state.filter : null;
    const dq = { group_by: groupBy };
    if (f && STATE_ORDER[f] !== undefined) dq.state = f;
    else if (f) dq.source = f;
    const reads = {
      overview: () => workOrderApi.maintenanceOverview(),
      // 500 is the route's ceiling. /overview counts the same decisions with an internal
      // limit of 1000, and `total` is len() of a list the limit already truncated, so asking
      // for less than the route allows makes the card and the grid disagree on purpose.
      decisions: () => workOrderApi.maintenanceDecisions(Object.assign({ limit: 500 }, dq)),
      intelligence: () => workOrderApi.inspectionIntelligence(),
      ppm: () => workOrderApi.ppmContracts(),
      lastRead: () => workOrderApi.lastInspectionRead(),
      inspections: () => workOrderApi.maintenanceInspections(),
      chips: () => workOrderApi.maintenanceAskSuggestions('maintenance')
    };
    const keys = Object.keys(reads);
    const settled = await Promise.allSettled(keys.map((k) => reads[k]()));
    const raw = { fetchedAt: new Date().toISOString(), groupBy: groupBy, errors: {} };
    let answered = 0;
    // Set by any read that outlived the company it was issued under (api/client.js).
    let stale = false;
    settled.forEach((r, i) => {
      if (r.status === 'fulfilled') { raw[keys[i]] = r.value; answered += 1; }
      else {
        raw[keys[i]] = null;
        raw.errors[keys[i]] = (r.reason && r.reason.message) || String(r.reason);
        // A 404 here is not "the read failed" — it is "this service does not serve that
        // route", which is a deployment that has not caught up with the code, and it needs
        // a different sentence from a 500.
        raw.status = raw.status || {};
        raw.status[keys[i]] = (r.reason && typeof r.reason.status === 'number') ? r.reason.status : 0;
        if (isStaleScope(r.reason)) stale = true;
      }
    });
    // A company switch mid-load: api/client.js disowned every read that was in flight and
    // loadLiveData() has already started correctly-scoped ones.
    if (stale) { this._mxLiveLoading = false; return; }
    this._mxLiveLoading = false;
    if (!answered) {
      const msg = raw.errors[keys[0]] || 'unreachable';
      this._mxLiveAttempts = (this._mxLiveAttempts || 0) + 1;
      this.setState({ mxLiveLoading: false, mxLiveError: msg });
      if (this._mxLiveAttempts < RETRY_MAX) this._mxLiveRetry = setTimeout(() => this.mxLiveLoad(), RETRY_MS);
      if (opts && opts.announce) this.flash('Maintenance backend unreachable — ' + msg);
      return;
    }
    this._mxLiveAttempts = 0;
    this.setState({ mxRaw: raw, mxLiveLoading: false, mxLiveError: '', mxLiveLoadedAt: raw.fetchedAt });
    if (opts && opts.announce) {
      const m = shapeLiveMaintenance(raw, this.state.account);
      this.flash('Maintenance refreshed — ' + (m.count === null ? 'no decisions read' : m.count + (m.count === 1 ? ' decision' : ' decisions')));
    }
    this._mxLiveRefresh = setTimeout(() => this.mxLiveLoad(), REFRESH_MS);
  },

  mxLiveRetryNow() { this._mxLiveAttempts = 0; return this.mxLiveLoad({ announce: true }); },

  // Where the page's figures came from, said on the page itself — the same pill the
  // compliance console and the Vendors page carry. Nothing here is seed data, so when the
  // service has not answered the page says so rather than filling itself in.
  mxLiveVals(s) {
    const m = this.mxModel();
    const raw = s.mxRaw || null;
    const errs = (raw && raw.errors) || {};
    const status = (raw && raw.status) || {};
    const failed = Object.keys(errs).filter((k) => errs[k]);
    // A 404 means the service does not serve that route at all. When the routes this page
    // reads are missing rather than erroring, the service is running an older build than the
    // code — a deploy that has not caught up, which no amount of retrying will fix. Saying
    // "5 of 7 reads failed" sends someone hunting for a bug in the data instead.
    const missing = failed.filter((k) => status[k] === 404);
    const skew = missing.length > 0 && missing.length === failed.length;
    const total = raw ? Object.keys(errs).length + READS.filter((k) => raw[k]).length : READS.length;
    const label = (k) => (READ_LABEL[k] || k);
    return {
      mxLiveOn: m.live,
      mxLiveSourceLabel: m.live
        ? 'Live · svc-work-order-management'
          + (skew ? ' · ' + missing.length + ' routes not deployed'
            : failed.length ? ' · ' + failed.length + ' of ' + total + ' reads failed' : '')
        : s.mxLiveLoading ? 'Reading svc-work-order-management…'
        : 'No data · maintenance backend unreachable',
      mxLiveSourceDot: m.live ? (failed.length ? 'var(--st-warn)' : 'var(--st-ok)')
        : s.mxLiveLoading ? 'var(--color-neutral-500)' : 'var(--st-warn)',
      mxLiveSourceDetail: s.mxLiveError
        || (failed.length ? failed.map((k) => label(k) + ' — ' + (status[k] ? status[k] + ' ' : '') + errs[k]).join(' · ') : '')
        || (s.mxLiveLoadedAt ? 'Read at ' + s.mxLiveLoadedAt : ''),
      mxLiveRetryShow: !s.mxLiveLoading && (!m.live || !!s.mxLiveError || (failed.length && !skew)) ? 'inline' : 'none',
      mxLiveRetry: () => this.mxLiveRetryNow(),
      // Named on the page, not only in a tooltip: which panels are blank and why.
      // Said as a banner, not a tooltip: a superadmin reading every company's rows under a
      // header naming one company is the kind of thing that has to be on the screen.
      mxCrossShow: m.crossCompany ? 'block' : 'none',
      mxCrossNote: m.crossCompany
        ? 'These are platform totals across every company, not one company\u2019s. '
          + 'You are signed in as a superadmin and have not chosen a company to view as \u2014 '
          + 'pick one from the account menu to narrow every figure on this page to it.'
        : '',
      mxSkewShow: skew ? 'block' : 'none',
      mxSkewNote: skew
        ? 'svc-work-order-management is running a build older than this page: '
          + missing.length + (missing.length === 1 ? ' route it reads is' : ' routes it reads are')
          + ' not deployed (' + missing.map(label).join(', ') + '). '
          + 'The panels they fill are blank until the service is rebuilt from the current source — retrying will not change it.'
        : '',
      // The whole page empty, as opposed to one panel of it.
      mxEmptyShow: m.live ? 'none' : 'block',
      mxBodyShow: m.live ? 'block' : 'none',
      mxEmptyTitle: s.mxLiveLoading ? 'Reading the maintenance records…'
        : skew ? 'Maintenance routes not deployed'
        : 'Maintenance backend unreachable',
      mxEmptyNote: s.mxLiveLoading
        ? 'Nothing is shown until it answers.'
        : skew
        ? 'svc-work-order-management answered, but it does not serve the routes this page reads ('
          + missing.map(label).join(', ') + '). It is running a build older than the current source; rebuilding it is what fills this page.'
        : 'Every figure on this page is read from svc-work-order-management, and it did not answer'
          + (s.mxLiveError ? ': ' + s.mxLiveError : '') + '. Nothing is shown in its place.'
    };
  },

  // "Group by" is a server parameter: the backend cuts the groups and totals them, so
  // changing it re-reads rather than regrouping rows the server grouped differently.
  mxSetGroup(label) {
    if (lower(label) === lower(this.state.mxGroup || 'State')) return;
    this.setState({ mxGroup: label, mxOpenG: null });
    this._mxLiveLoading = false;
    this.mxLiveLoad();
  },

  // So is the filter. Filtering here rather than on the server would narrow the rows the
  // page shows while the counts above them still described the whole queue.
  mxSetFilter(label) {
    if (label === (this.state.filter || 'All')) return;
    this.setState({ filter: label, mxOpenG: null });
    this._mxLiveLoading = false;
    this.mxLiveLoad();
  },

  // The Ask bar on the page and on the inspection panel. The answer is composed from rows a
  // named engine function returned, and carries the endpoint each figure came from.
  async mxAsk(question, page) {
    const q = String(question || '').trim();
    if (!q || this.state.mxAskBusy) return;
    this.setState({ mxAskBusy: true, mxAskError: '', mxAsked: q });
    try {
      const r = await workOrderApi.maintenanceAsk(q, page || 'maintenance');
      this.setState({ mxAnswer: r || null, mxAskBusy: false });
    } catch (e) {
      if (isStaleScope(e)) { this.setState({ mxAskBusy: false }); return; }
      this.setState({ mxAskBusy: false, mxAskError: (e && e.message) || String(e), mxAnswer: null });
    }
  },
  mxClearAnswer() { this.setState({ mxAnswer: null, mxAsked: '', mxAskError: '' }); }
};
