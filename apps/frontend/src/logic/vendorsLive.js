// vendorsLive — the Vendors page read from svc-operations-intelligence.
//
// This model is the whole page. Unlike the compliance console (complianceLive.js), nothing
// sits behind it: there is no seed fallback, so a vendor, a score, a term or a coverage
// figure reaches the screen only because one of the reads below returned it. When none of
// them answer, `empty` below is what renderVals gets — an empty directory and a null against
// every count — and the page says which service did not answer instead of filling itself in.
//
//   Directory + scorecard   GET /api/contract-performance/saved-space/summary  (scorecards, weights,
//                           pending Feature B approvals) — /scorecards is the fallback read
//   Contract terms          GET /api/contract-performance/contracts  (parameter sets with field_sources)
//   Weights                 GET /api/contract-performance/admin/weights
//   Invoice lines held      GET /api/contract-performance/approvals  (invoice_flag items, pending)
//   Coverage                GET /api/compliance/certificates?cert_scope=vendor
//                           GET /api/compliance/coverage/vendors?country_code=…  (block state, gaps)
//                           GET /api/compliance/country-pack?country_code=…      (names for gap codes)
//
// Not sourced — no read endpoint exists — and therefore null here and "—" on the screen:
// the per-work-order breach list behind a score (Evidence tab), the vendor's L1/L2/L3 job
// split, annual spend, contract expiry and page counts, invoice lines that matched (only
// flagged lines reach the approvals queue), open work orders, and when the scorecards were
// last cut (`lastRebuild` reads `created_at` if the list response ever carries it).
//
// Scores are never recomputed here. `overall_score` is the published figure; the rows
// beneath it are the engine's own component points against the weights it snapshotted.
//
// shapeLiveVendors() is a pure function; the methods below are mixed into
// HoistraLogic.prototype and `this` is the controller.
import { opsApi } from '../api/opsIntelligence.js';
import { complianceApi } from '../api/compliance.js';
import { normCountry, countryMeta } from './complianceLive.js';
import { isStaleScope } from '../api/client.js';

const RETRY_MS = 30000;
const RETRY_MAX = 6;
// Scorecards are monthly and the queue moves on cron cycles; the Home page re-reads on the
// same cadence.
const REFRESH_MS = 15 * 60 * 1000;

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const MONTH_IDX = { jan: 0, feb: 1, mar: 2, apr: 3, may: 4, jun: 5, jul: 6, aug: 7, sep: 8, sept: 8, oct: 9, nov: 10, dec: 11 };
const num = (v) => (typeof v === "number" && isFinite(v) ? v : (typeof v === "string" && v.trim() !== "" && isFinite(Number(v)) ? Number(v) : null));
const round1 = (v) => Math.round(v * 10) / 10;
const pad2 = (n) => String(n).padStart(2, "0");

// "YYYY-MM-DD" → "DD Mon YYYY", read as a calendar date so a UTC midnight never slips a day.
export function fmtDay(iso) {
  if (!iso) return "—";
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(iso));
  if (!m) return String(iso);
  return m[3] + " " + MONTHS[Number(m[2]) - 1] + " " + m[1];
}
// "YYYY-MM-01" → "Mon YYYY".
export function monthLabel(iso) {
  const m = /^(\d{4})-(\d{2})/.exec(String(iso || ""));
  return m ? MONTHS[Number(m[2]) - 1] + " " + m[1] : null;
}
// The trend word under each score, from the card's month-on-month delta.
export function trendOf(delta) {
  const d = num(delta);
  if (d === null) return "first month";
  if (d > 1) return "improving";
  if (d < -1) return "declining";
  return "stable";
}
const gbp = (v) => "£" + Math.round(v).toLocaleString("en-GB");
const gbpExact = (v) => "£" + Number(v).toFixed(2);
const rateH = (v) => gbpExact(v) + "/h";
const hours = (h) => (h === 1 ? "1 hour" : h + " hours");
const plural = (n, one, many) => n + " " + (n === 1 ? one : many);
const keysOf = (o) => (o && typeof o === "object" ? Object.keys(o) : []);

// Keys the extractor writes ABOUT a term rather than as one. The Claude path returns
// {note, description} when a contract mentions a subject without committing to anything;
// the heuristic path returns {detected, raw_snippet} for the same situation.
const _COMMENTARY_KEYS = new Set(["note", "notes", "description", "summary", "detected", "raw_snippet", "text", "comment"]);
const substantiveKeys = (o) => keysOf(o).filter((k) => !_COMMENTARY_KEYS.has(String(k).toLowerCase()));

// How a structured term reads on the panel.
//
// Counting every key called WKU's KPI value "2 clauses · document" — and the two keys were
// a description of KPIs being "monitored" and a note reading "No specific KPI targets or
// metrics are stated in the contract." The badge claimed the contract supplied KPI terms
// while the value itself said the opposite. A contract that raises a subject and commits to
// nothing is neither "2 clauses" nor "none stated"; it is its own answer, because the reader
// is about to confirm these values as binding.
const countedTerm = (v, one, many, emptyWord) => {
  const real = substantiveKeys(v).length;
  if (real) return plural(real, one, many);
  return keysOf(v).length ? "mentioned, " + emptyWord : "none stated";
};
const lower = (s) => String(s || "").toLowerCase();
// "PUBLIC_LIABILITY" → "Public Liability": the register sometimes stores the code as the name.
const prettyCode = (code) => String(code || "").split(/[_\s]+/).filter(Boolean)
  .map((w) => (/^[A-Z0-9]{2,4}$/.test(w) && !/^[A-Z][a-z]/.test(w) && w.length <= 4 && /\d/.test(w) ? w : w.charAt(0).toUpperCase() + w.slice(1).toLowerCase())).join(" ");
const isBareCode = (name, code) => !name || (code && String(name) === String(code)) || /^[A-Z0-9_]+$/.test(String(name));
// Uploaded documents are stored as "<uuid>_<original name>"; the prefix is not for people.
const docName = (n) => String(n || "").replace(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}_/i, "");

// ── the five scorecard components ────────────────────────────────────────
// Key on the card's component_breakdown → weight key in the weights row → the row's copy.
const COMPONENTS = [
  { k: "sla_response", wk: "sla_response_pct", label: "SLA response", def: 25,
    basis: "jobs answered inside their contracted response window", sla: "response" },
  { k: "sla_completion", wk: "sla_completion_pct", label: "SLA completion", def: 25,
    basis: "jobs closed inside their contracted completion window", sla: "completion" },
  { k: "first_fix", wk: "first_fix_pct", label: "First-time fix", def: 20,
    basis: "jobs resolved on the first attendance", requires: "every job fixed first time" },
  { k: "recall", wk: "recall_pct", label: "Recall rate", def: 15,
    basis: "jobs with no return to the same asset inside 30 days", requires: "no recall inside 30 days" },
  { k: "accreditation", wk: "accreditation_pct", label: "Accreditation", def: 15,
    basis: "jobs attended while the vendor's accreditation was current", requires: "current on every job" }
];

// A rate in the currency the CONTRACT states, not the currency the portfolio toggle shows.
// gbp() hardcodes £, and WKU's rates are dollars: rendering $51.40 as £51.40 would be a new
// lie in place of the one the rate card exists to remove. An unstated currency renders the
// number bare rather than guessing a symbol.
const CURRENCY_SYMBOL = { USD: "$", GBP: "£", EUR: "€", AED: "AED ", SGD: "S$", CAD: "C$", AUD: "A$" };
const moneyIn = (cur) => (v) => {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
  const sym = cur ? (CURRENCY_SYMBOL[String(cur).toUpperCase()] || (String(cur).toUpperCase() + " ")) : "";
  return sym + n.toFixed(2);
};

// The card as the panel shows it, or null when the contract prices no trades. An empty
// shell reads as "rates are on file" while holding none, which is the same misdirection a
// £350 default produces.
const rateCardRow = (card) => {
  const lines = (card && Array.isArray(card.lines) ? card.lines : []).filter((l) => l && l.trade);
  if (!lines.length) return null;
  const cur = card.currency || null;
  const m = moneyIn(cur);
  return {
    label: "Labour rates",
    value: plural(lines.length, "trade", "trades")
      + (cur ? " · " + String(cur).toUpperCase() : "")
      + (card.basis ? " / " + card.basis : ""),
    src: "contract",
    srcLabel: "document",
    field: "rate_card_json",
    editable: false,
    raw: null,
    clause: null,
    page: 0,
    currency: cur,
    basis: card.basis || null,
    source: card.source || null,
    note: "Read from " + (card.source || "the signed contract")
      + ". Each trade is priced separately, so there is no single labour rate to check an "
      + "invoice against — the line's own trade decides.",
    lines: lines.map((l) => ({
      trade: String(l.trade),
      straight: m(l.straight),
      overtime: m(l.overtime)
    }))
  };
};

// ── the contract parameter fields, in the order the Terms tab lists them ─
const money = (suffix) => (v) => gbp(v) + suffix;
const TERM_FIELDS = [
  { key: "contract_ref", label: "Contract reference", fmt: (v) => String(v) },
  { key: "sla_response_p1_hours", label: "P1 response", fmt: hours, sla: "response" },
  { key: "sla_response_p2_hours", label: "P2 response", fmt: hours, sla: "response" },
  { key: "sla_response_p3_hours", label: "P3 response", fmt: hours, sla: "response" },
  { key: "sla_response_p4_hours", label: "P4 response", fmt: hours, sla: "response" },
  { key: "sla_completion_p1_hours", label: "P1 completion", fmt: hours, sla: "completion" },
  { key: "sla_completion_p2_hours", label: "P2 completion", fmt: hours, sla: "completion" },
  { key: "sla_completion_p3_hours", label: "P3 completion", fmt: hours, sla: "completion" },
  { key: "sla_completion_p4_hours", label: "P4 completion", fmt: hours, sla: "completion" },
  { key: "labour_day_rate", label: "Labour rate — day", fmt: money(" / day") },
  { key: "labour_hour_rate", label: "Labour rate — hour", fmt: money(" / hr") },
  { key: "overtime_rate", label: "Overtime rate", fmt: money("") },
  { key: "call_out_rate", label: "Call-out rate", fmt: money("") },
  { key: "payment_terms", label: "Payment terms", fmt: (v) => (/^\d+$/.test(String(v).trim()) ? String(v).trim() + " days" : String(v)) },
  { key: "parts_pricing_json", label: "Parts pricing", fmt: (v) => countedTerm(v, "price", "prices", "no prices") },
  { key: "kpi_clauses_json", label: "KPI clauses", fmt: (v) => countedTerm(v, "clause", "clauses", "no targets") },
  { key: "ppm_obligations_json", label: "PPM obligations", fmt: (v) => (substantiveKeys(v).length ? "stated" : (keysOf(v).length ? "mentioned, no detail" : "none stated")) },
  { key: "task_criticality_json", label: "Task criticality", fmt: (v) => countedTerm(v, "level", "levels", "no levels") }
];
const isEmptyValue = (v) => v === null || v === undefined || v === "" || (typeof v === "object" && !keysOf(v).length);

// Terms that hold a structure rather than a value. They render as a count ("4 clauses") and
// cannot be edited from a text box without losing what the count was counting.
const STRUCTURED_TERMS = new Set([
  "parts_pricing_json", "kpi_clauses_json", "ppm_obligations_json", "task_criticality_json"
]);

// The register's status vocabulary → the four tags the Coverage tab shows.
function certStatus(raw) {
  const s = lower(raw);
  if (/lapsed|overdue|expired|revoked|suspended/.test(s)) return "Lapsed";
  if (/expir|renewal|due/.test(s)) return "Expiring";
  if (/current|valid|active|compliant|verified/.test(s)) return "Current";
  if (!s) return "Not on record";
  return "Expiring";
}
function certVer(r) {
  const meta = r.raw_metadata || {};
  const v = meta.verification || meta.ccc_verification || {};
  const st = v.status || v.result || meta.verification_status;
  const issuer = r.issuer || r.issuing_body || null;
  if (st) return String(st).replace(/_/g, " ") + (issuer ? " — " + issuer : "");
  return issuer ? "Not checked — " + issuer : "Not checked";
}

// The B approvals queue carries flagged invoice lines. Each becomes an Invoices-tab row.
function invoiceLine(item, now) {
  const p = item.payload || {};
  const line = p.line || {};
  const checks = line.checks || {};
  const ref = String(p.invoice_ref || item.summary || "");
  const short = ref.indexOf("_B3_invoice_") > -1 ? ref.split("_B3_invoice_").pop() : ref;
  // "…_Sept-2023" → "Sep 2023"; otherwise the month the flag was raised.
  // "UKRI-2938_Sept-2023" carries a contract ref before the month, so scan every
  // word-dash-year pair for the first that names a month.
  let period = null;
  const re = /([A-Za-z]{3,4})-(\d{4})/g;
  let pm;
  while ((pm = re.exec(short)) !== null) {
    if (MONTH_IDX[lower(pm[1])] !== undefined) { period = MONTHS[MONTH_IDX[lower(pm[1])]] + " " + pm[2]; break; }
  }
  if (!period) {
    const d = new Date(item.created_at || now);
    period = isNaN(d) ? "—" : MONTHS[d.getMonth()] + " " + d.getFullYear();
  }
  const delta = num(line.delta_gbp);
  const rate = num(checks.labour_rate), contracted = num(checks.contracted_hourly);
  const noWo = checks.wo_exists === false;
  const decision = lower(line.decision);
  const status = decision === "approve" || decision === "approved" ? "Approved"
    : decision === "reject" || decision === "rejected" || decision === "challenge" || decision === "challenged" ? "Credited"
    : /high_value/.test(String(item.item_type || "")) ? "Disputed"
    : "Held";
  return {
    ref: short.replace(/_/g, " "),
    period: period,
    line: (line.wo_code || "unmatched work order") + (line.line_id !== undefined && line.line_id !== null ? " · line " + line.line_id : ""),
    charged: noWo ? (delta !== null ? gbp(delta) : "—") : (rate !== null ? rateH(rate) : (delta !== null ? gbp(delta) : "—")),
    should: noWo ? "no work order" : (contracted !== null ? rateH(contracted) : "—"),
    delta: delta !== null ? (delta >= 0 ? "+" : "−") + gbp(Math.abs(delta)) : "—",
    deltaValue: delta,
    flag: line.discrepancy || item.summary || "",
    status: status,
    invoiceRef: ref,
    vendorId: p.vendor_id || item.related_entity_id || null,
    itemId: item.id
  };
}

// ── the shaping ─────────────────────────────────────────────────────────
// input: the reads, each null / missing when that request failed
//   summary       GET /api/contract-performance/saved-space/summary
//   scorecards    GET /api/contract-performance/scorecards           (fallback for summary.scorecards)
//   contracts     GET /api/contract-performance/contracts
//   weights       GET /api/contract-performance/admin/weights
//   approvals     GET /api/contract-performance/approvals            (pending Feature B items)
//   certificates  rows of GET /api/compliance/certificates?cert_scope=vendor
//   coverage      { [cc]: vendors[] }  from GET /api/compliance/coverage/vendors
//   packs         { [cc]: types[] }    from GET /api/compliance/country-pack
export function shapeLiveVendors(input, now) {
  const raw = input || {};
  const at = now || new Date();
  const summary = raw.summary || null;
  const cards = (summary && summary.scorecards) || (raw.scorecards && raw.scorecards.scorecards) || null;
  const params = (raw.contracts && raw.contracts.parameters) || null;
  const weights = (raw.weights && raw.weights.weights) || (summary && summary.weights) || null;
  const approvals = (raw.approvals && raw.approvals.items) || (summary && summary.approvals && !raw.approvals ? null : null);
  const certificates = Array.isArray(raw.certificates) ? raw.certificates : null;
  const coverage = raw.coverage || {};
  const packs = raw.packs || {};

  const live = !!(cards || params || weights || approvals || certificates);
  const empty = {
    live: false, month: null, lastRebuild: null, vendors: [], V: {}, weights: null, weightsText: null,
    tiles: { blocked: null, pending: null, critical: null, L1: null, held: null, defaults: null },
    counts: { contracts: null, termsRead: null, termsDefault: null, expiring: null, invoiceLines: null, heldLines: null, approvedLines: null, workordersOpen: null },
    pkgOf: () => "Unclassified"
  };
  if (!live) return empty;

  // Pack types: names for codes, and which codes are vendor-scope requirements per country.
  const typeName = {};
  const vendorTypes = {};
  Object.keys(packs).forEach((cc) => {
    (packs[cc] || []).forEach((t) => {
      if (!t || !t.certificate_type_code) return;
      if (!typeName[t.certificate_type_code]) typeName[t.certificate_type_code] = t.certificate_type_name || t.certificate_type_code;
      if (lower(t.certificate_scope) === "vendor") vendorTypes[t.certificate_type_code] = true;
    });
  });
  const nameOf = (code) => typeName[code] || prettyCode(code);

  // Coverage rows by vendor id and country (and by name, for certificates that carry no id).
  // Coverage is fetched once per country, and a vendor with no country recorded comes back
  // under every country with that pack's requirements — so a vendor's row is chosen for the
  // country its own accreditations are in, never by whichever response landed last.
  const covById = {}, covByName = {};
  const covCcs = Object.keys(coverage);
  covCcs.forEach((cc) => {
    (coverage[cc] || []).forEach((v) => {
      if (v.vendor_id) (covById[String(v.vendor_id)] = covById[String(v.vendor_id)] || {})[cc] = v;
      if (v.vendor_name) (covByName[lower(v.vendor_name)] = covByName[lower(v.vendor_name)] || {})[cc] = v;
    });
  });
  const covFor = (rows, certs) => {
    if (!rows) return null;
    const tally = {};
    (certs || []).forEach((c) => { const cc = normCountry(c.country_code); if (cc) tally[cc] = (tally[cc] || 0) + 1; });
    const own = Object.keys(tally).sort((a, b) => tally[b] - tally[a] || covCcs.indexOf(a) - covCcs.indexOf(b));
    const cc = own.find((k) => rows[k]) || covCcs.find((k) => rows[k]);
    return cc ? rows[cc] : null;
  };

  // Certificates by vendor id / name.
  const certsById = {}, certsByName = {};
  (certificates || []).forEach((r) => {
    if (r.vendor_id) (certsById[String(r.vendor_id)] = certsById[String(r.vendor_id)] || []).push(r);
    else if (r.vendor_name) (certsByName[lower(r.vendor_name)] = certsByName[lower(r.vendor_name)] || []).push(r);
  });

  // Newest card per vendor. The list is newest-first already, but sort defensively.
  const latest = {};
  const nameById = {};
  (cards || []).forEach((c) => {
    const id = String(c.vendor_id || "");
    if (!id) return;
    if (c.vendor_name && !nameById[id]) nameById[id] = c.vendor_name;
    if (!latest[id] || String(c.score_month) > String(latest[id].score_month)) latest[id] = c;
  });
  // Parameter sets by vendor (newest first in the list, so the first wins) and by id.
  const paramsByVendor = {}, paramsById = {};
  (params || []).forEach((p) => {
    if (p.id) paramsById[String(p.id)] = p;
    const id = String(p.vendor_id || "");
    if (!id) return;
    if (p.vendor_name && !nameById[id]) nameById[id] = p.vendor_name;
    if (!paramsByVendor[id]) paramsByVendor[id] = p;
  });

  const ids = [];
  Object.keys(latest).forEach((id) => { if (ids.indexOf(id) < 0) ids.push(id); });
  Object.keys(paramsByVendor).forEach((id) => { if (ids.indexOf(id) < 0) ids.push(id); });

  const newestMonth = Object.keys(latest).reduce((m, id) => (String(latest[id].score_month) > m ? String(latest[id].score_month) : m), "");
  const month = newestMonth ? monthLabel(newestMonth) : null;
  // When the scorecards were last cut. The row carries created_at but the list response does
  // not return it yet, so this is usually null and the header reads "—". It lights up on its
  // own the day the read includes the column; nothing here invents a time in the meantime.
  const lastRebuild = (cards || []).reduce((t, c) => {
    const v = c && (c.created_at || c.generated_at);
    return v && (!t || String(v) > t) ? String(v) : t;
  }, null);

  const weightOf = (snapshot, wk, def) => {
    const w = num(snapshot && snapshot[wk]);
    if (w !== null) return w;
    const g = num(weights && weights[wk]);
    return g !== null ? g : def;
  };
  const cap = num(weights && weights.blocked_score_cap) !== null ? num(weights.blocked_score_cap) : 60;

  const vendors = [];
  const V = {};
  const pkgById = {};
  let termsRead = 0, termsDefault = 0, contractsN = 0, heldLines = 0;

  // Flagged invoice lines, once, then handed to the vendor they belong to.
  const flagged = (approvals || []).filter((it) => /^invoice_flag/.test(String(it.item_type || ""))).map((it) => invoiceLine(it, at));

  ids.forEach((id) => {
    const card = latest[id] || null;
    const bd = (card && card.component_breakdown) || {};
    const p = paramsByVendor[id] || (bd.contract_parameters_id && paramsById[String(bd.contract_parameters_id)]) || null;
    const anyCov = covById[id] ? covById[id][Object.keys(covById[id])[0]] : null;
    const name = nameById[id] || (anyCov && anyCov.vendor_name) || (p && p.vendor_name) || "Vendor " + id.slice(-4);
    const certs = (certsById[id] || []).concat(certsByName[lower(name)] || []);
    const cov2 = covFor(covById[id], certs) || covFor(covByName[lower(name)], certs) || null;

    // ── accreditation posture ──
    const blockedNow = !!(cov2 && /blocked/i.test(String(cov2.block_state || ""))) ||
      certs.some((c) => /blocked/i.test(String(c.vendor_block_state || "")));
    const statuses = certs.map((c) => certStatus(c.status));
    const accred = blockedNow || statuses.indexOf("Lapsed") > -1 ? "Lapsed"
      : statuses.indexOf("Expiring") > -1 || certs.some((c) => num(c.days_to_expiry) !== null && num(c.days_to_expiry) <= 90) ? "Expiring"
      : certs.length ? "Current" : "Not on record";

    // ── score ──
    const overall = card ? num(card.overall_score) : null;
    const score = overall === null ? null : Math.round(overall);
    const delta = card ? num(card.trend_delta) : null;
    const trend = card ? trendOf(delta) : "not scored";
    const capApplied = !!(card && card.block_capped);
    const woCount = card && Array.isArray(card.wo_score_ids) ? card.wo_score_ids.length : 0;
    const snapshot = bd.weights_snapshot || null;
    const rowsRaw = COMPONENTS.map((c) => {
      const w = weightOf(snapshot, c.wk, c.def);
      const pts = card ? num(bd[c.k]) : null;
      const fromContract = !!(p && c.sla && TERM_FIELDS.some((f) => f.sla === c.sla && lower((p.field_sources || {})[f.key]) === "contract"));
      let requires = c.requires || "";
      if (c.sla) {
        const parts = TERM_FIELDS.filter((f) => f.sla === c.sla).map((f, i) => {
          const h = p ? num(p[f.key]) : null;
          return h === null ? null : "P" + (i + 1) + " " + h + "h";
        }).filter(Boolean);
        requires = parts.length ? parts.join(" · ") : "platform default hours per priority";
      }
      // The badge describes the contract ON SCREEN. When a current parameter row exists, its own
      // field_sources decide the label; the card's recorded `parameter_source` is consulted only
      // when there is no row at all. Production, 17 Sep 2026: Gough and Kelly's SLA rows read
      // "contract-sourced · UKRI-2938" while UKRI-2938 itself said `default` for every SLA
      // field — the label came from a January 2024 card scored against a different, since-
      // deleted contract (ae5ad29f-…). A 2024 provenance stitched onto 2026 default hours is
      // true of a contract that is gone and false of the one the reader is looking at.
      const srcTag = c.sla
        ? (p
          ? (fromContract ? "contract · " : "default · ") + (p.contract_ref || "terms on file")
          : /default/i.test(String(bd.parameter_source || "")) ? "platform default"
          : bd.parameter_source ? "contract-sourced · parameters not on record"
          : "platform default")
        : "platform rule";
      return {
        k: c.k, label: c.label, w: w, basis: c.basis, unit: "%", ceiling: false, target: 100,
        measured: pts === null || !w ? null : Math.round((100 * pts) / w),
        // A component the card did not score is null, not zero: "— of 25 pts" says the
        // engine did not report it, "0 of 25 pts" says the vendor earned nothing.
        pts: pts === null ? null : round1(pts),
        fromContract: fromContract, clause: null, page: 0, citeLabel: c.label,
        requires: requires,
        sample: c.basis + " · " + plural(woCount, "work order", "work orders"),
        srcTag: srcTag
      };
    });
    // No card, no total. The sum counts the components the card did report.
    const rawTotal = card
      ? (num(bd.wo_avg_before_invoice_blend) !== null ? num(bd.wo_avg_before_invoice_blend) : round1(rowsRaw.reduce((q, r) => q + (r.pts || 0), 0)))
      : null;
    const invoiceSignal = card ? num(bd.invoice_match_signal) : null;

    // ── contract terms ──
    const fs = (p && p.field_sources) || {};
    const terms = p ? TERM_FIELDS.filter((f) => f.key in fs || !isEmptyValue(p[f.key])).map((f) => {
      const src = lower(fs[f.key]) === "contract" ? "contract" : "default";
      const v = p[f.key];
      const value = isEmptyValue(v) ? "—" : f.fmt(typeof v === "string" && f.fmt === hours ? Number(v) : v);
      return {
        label: f.label, value: value, src: src, clause: null, page: 0,
        // The column this row would be written back to. The panel knows a row as "P1
        // response"; the PATCH route knows it only as sla_response_p1_hours, and without
        // the name here the editor cannot address the value it is showing.
        field: f.key,
        // Parts pricing, KPI clauses, PPM obligations and the criticality ladder are
        // objects. A single-line box would flatten one into a string and destroy the
        // structure the engine reads, so those rows stay read-only.
        editable: !STRUCTURED_TERMS.has(f.key),
        raw: isEmptyValue(v) ? null : v,
        srcLabel: src === "contract" ? (p.document_name ? "document" : "contract") : "default",
        note: src === "contract"
          ? f.label + " was read from " + (docName(p.document_name) || p.contract_ref || "the signed contract") + " — the extracted value is " + value + "."
          : f.label + " was not found in " + (docName(p.document_name) || p.contract_ref || "the contract document") + ". The platform default of " + value + " applies until the term is agreed in writing."
      };
    }) : [];
    // The rate card sits with the other terms, and where one exists the scalar rate rows
    // stop asserting a platform default beside it. Two answers to "what does labour cost?"
    // is worse than one — and the wrong one looked authoritative, sitting in the same
    // column as twelve figures the contract actually states.
    const _card = rateCardRow(p && p.rate_card);
    if (_card) {
      const at = terms.findIndex((t) => t.field === "labour_day_rate");
      terms.splice(at >= 0 ? at : terms.length, 0, _card);
      terms.forEach((t) => {
        if (t.field === "labour_day_rate" || t.field === "labour_hour_rate") {
          t.value = "priced per trade — see Labour rates";
          t.editable = false;
          t.note = "This contract prices labour per trade, so a single rate here would "
            + "contradict the rate card above. Correct it there, or on the document.";
        }
      });
    }
    const read = terms.filter((t) => t.src === "contract").length;
    const fields = terms.length;
    if (p) { contractsN += 1; termsRead += read; termsDefault += fields - read; }
    const contract = {
      // The parameter set's own id. Everything the panel can DO — confirm the set, patch a
      // term — addresses this, and until it was carried here the panel could only describe
      // the contract, never act on it.
      id: p ? (p.id ? String(p.id) : null) : null,
      ref: p ? (p.contract_ref || null) : null,
      signed: p && p.signed_date ? fmtDay(p.signed_date) : "—",
      expires: null, pages: null, read: read, fields: fields,
      // How many terms are platform defaults rather than anything the document said.
      // Confirming turns every one of them into an agreed value, so the count is what the
      // confirmation step has to be able to state out loud.
      defaults: fields - read,
      doc: p ? (p.document_name ? docName(p.document_name) : null) : null,
      status: p ? (p.status || null) : null,
      // Scoring runs against a confirmed set and refuses a draft; the panel disables its
      // button on this rather than re-confirming something already in force.
      confirmed: !!(p && lower(p.status) === "confirmed"),
      // WHO made these numbers binding, and when. The service resolves the uuid to a name;
      // when it cannot, the name stays empty rather than showing the uuid, which reads as
      // data and answers nothing. The date is worth showing either way.
      confirmedBy: (p && typeof p.confirmed_by_name === "string" && p.confirmed_by_name.trim())
        ? p.confirmed_by_name.trim() : null,
      confirmedOn: p && p.confirmed_at ? fmtDay(p.confirmed_at) : null,
      // A contract that yielded NOTHING is a different event from one with a few gaps, and
      // the table of `default` badges renders both identically. Moreland's ingest read 0 of
      // 16 — correctly, because the document is a property management agreement and not a
      // service contract — and it was confirmed on the strength of a screen that did not say so.
      readNothing: !!(p && fields > 0 && read === 0),
      line: p
        ? (p.contract_ref || "Contract") + " · signed " + (p.signed_date ? fmtDay(p.signed_date) : "date not on record") +
          (p.status ? " · " + p.status : "") + " · " + read + " of " + fields + " terms read" + (p.document_name ? " from " + docName(p.document_name) : " from the contract document")
        : (bd.contract_parameters_id
          ? "Scored against contract parameters " + String(bd.contract_parameters_id).slice(0, 8) + " — the parameter set is not on record any more"
          : "No contract terms on record" + (card ? " — scored against platform defaults" : ""))
    };
    const sourceNote = p ? null
      : "No contract terms are on record for this vendor, so there is nothing to read a term from." +
        (bd.contract_parameters_id
          ? " The scorecard names parameter set " + String(bd.contract_parameters_id).slice(0, 8) + ", which no longer resolves; ingest the contract again to restore the clauses behind the score."
          : " Ingest the signed contract to replace the platform defaults the engine scored against.");

    // ── coverage ──
    const gaps = (cov2 && cov2.gaps) || [];
    const blockedType = cov2 && cov2.blocked_accreditation_type ? String(cov2.blocked_accreditation_type) : null;
    const certRows = certs.map((c) => ({
      name: isBareCode(c.certificate_type_name, c.certificate_type_code) ? nameOf(c.certificate_type_code) : c.certificate_type_name,
      // The pack's vendor-scope types are the mandatory ones; so is whatever the engine blocked on.
      req: vendorTypes[c.certificate_type_code] || gaps.indexOf(c.certificate_type_code) > -1 ||
        (blockedType && (blockedType === c.certificate_type_name || blockedType === c.certificate_type_code)) ? "Mandatory" : "Preferred",
      status: certStatus(c.status),
      exp: c.expiry_date ? fmtDay(c.expiry_date) : "—",
      ver: certVer(c),
      code: c.certificate_type_code
    })).concat(gaps.filter((code) => !certs.some((c) => c.certificate_type_code === code)).map((code) => ({
      name: nameOf(code), req: "Mandatory", status: "Not on record", exp: "—", ver: "Never supplied", code: code
    })));
    certRows.sort((a, b) => {
      const order = { Lapsed: 0, "Not on record": 1, Expiring: 2, Current: 3 };
      return (order[a.status] - order[b.status]) || a.name.localeCompare(b.name);
    });

    // ── invoices ──
    const refs = [p && p.contract_ref].filter(Boolean).map(String);
    const invoices = flagged.filter((l) => (l.vendorId && String(l.vendorId) === id) || (!l.vendorId && refs.some((r) => l.invoiceRef.indexOf(r) > -1)));
    heldLines += invoices.filter((i) => i.status === "Held" || i.status === "Disputed").length;

    // ── package ──
    const trades = {};
    certs.forEach((c) => { if (c.trade_category) trades[c.trade_category] = (trades[c.trade_category] || 0) + 1; });
    const pkg = Object.keys(trades).sort((a, b) => trades[b] - trades[a] || a.localeCompare(b))[0] || "Unclassified";
    pkgById[id] = pkg;

    const tone = score === null ? "none" : score >= 85 ? "ok" : score >= 70 ? "warn" : "risk";
    const samples = {};
    rowsRaw.forEach((r) => { samples[r.k] = woCount; });
    const critNote = "Criticality is set per asset and approved by a person, not inferred. Unapproved assets default to L2 until someone confirms otherwise, so a mis-set L1 cannot quietly triple a vendor's penalty." +
      (card && invoiceSignal !== null && Math.round(rawTotal) !== score
        ? " The published score is 0.85 × this weighted total + 0.15 × the invoice match signal (" + Math.round(invoiceSignal) + "%), which is why it differs from the total above."
        : "") +
      " The work orders behind each component are held by the scoring engine and are not exposed by a read endpoint yet, so the Evidence tab has no rows to show.";

    // The directory's coverage column is the compliance engine's CountryPack figure when it
    // has one for this vendor (types on record over types its trade requires).
    const covPct = cov2 && num(cov2.coverage_pct) !== null ? Math.round(num(cov2.coverage_pct)) : undefined;
    vendors.push({
      id: id, name: name, score: score, trend: trend, delta: delta, spend: null,
      accred: accred, blocked: blockedNow, tone: tone,
      cov: covPct, covOn: cov2 && num(cov2.on_record) !== null ? num(cov2.on_record) : undefined, covReq: cov2 && num(cov2.required) !== null ? num(cov2.required) : undefined,
      note: card ? "Scored on " + plural(woCount, "work order", "work orders") + " in " + (monthLabel(card.score_month) || "the latest month") : "No scorecard yet",
      meta: (p ? (p.contract_ref || "contract on file") : "no contract terms on record") + " · " +
        (accred === "Not on record" ? "no accreditation on record" : accred.toLowerCase() + " accreditation") +
        (card ? " · " + (monthLabel(card.score_month) || "") + " card" : ""),
      month: card ? monthLabel(card.score_month) : null,
      monthIso: card ? card.score_month : null,
      pkg: pkg,
      blockedType: blockedType
    });
    V[id] = {
      // An unscored vendor stays unscored. Zero is a score, and a vendor with a contract
      // but no card has not earned one — it would sit in "below 70" and drag the average
      // down with a number no engine ever published.
      contract: contract, terms: terms, rows: rowsRaw, raw: rawTotal, score: score,
      capApplied: capApplied, cap: cap, samples: samples, crit: null, breaches: [], certs: certRows, invoices: invoices,
      critNote: critNote, sourceNote: sourceNote, woCount: woCount,
      month: card ? monthLabel(card.score_month) : null, ppm: card ? num(card.ppm_compliance_pct) : null,
      invoiceSignal: invoiceSignal, parameterSource: bd.parameter_source || null
    };
  });

  // Newest month first, then the lowest score — the vendor that needs attention leads.
  vendors.sort((a, b) => {
    const ma = a.monthIso || "", mb = b.monthIso || "";
    if (ma !== mb) return mb.localeCompare(ma);
    const sa = a.score === null ? 101 : a.score, sb = b.score === null ? 101 : b.score;
    return sa - sb || a.name.localeCompare(b.name);
  });

  const pendingItems = approvals || null;
  const tiles = {
    blocked: vendors.filter((v) => v.blocked).length,
    pending: summary && typeof summary.pending_approvals === "number" ? summary.pending_approvals
      : (raw.approvals && typeof raw.approvals.count === "number" ? raw.approvals.count : (pendingItems ? pendingItems.length : null)),
    critical: pendingItems ? pendingItems.filter((it) => /high|critical/i.test(String(it.severity || ""))).length : null,
    L1: null,
    held: pendingItems ? flagged.length : null,
    defaults: params ? termsDefault : null
  };
  const counts = {
    contracts: params ? contractsN : null,
    termsRead: params ? termsRead : null,
    termsDefault: params ? termsDefault : null,
    expiring: null,
    invoiceLines: null,
    heldLines: pendingItems ? heldLines : null,
    approvedLines: null,
    workordersOpen: null
  };

  const weightsText = weights
    ? "Weights in force: SLA response " + weightOf(null, "sla_response_pct", 25) + ", SLA completion " + weightOf(null, "sla_completion_pct", 25) +
      ", first-time fix " + weightOf(null, "first_fix_pct", 20) + ", recall rate " + weightOf(null, "recall_pct", 15) + ", accreditation " + weightOf(null, "accreditation_pct", 15) +
      ". L1 misses weigh 3×, L2 1.5×, L3 1×. A blocked vendor's published score is capped at " + cap +
      (num(weights.invoice_flag_adversary_gbp) !== null ? "; invoice lines flagged above " + gbp(weights.invoice_flag_adversary_gbp) + " are adversary-checked" : "") +
      ". Read from svc-operations-intelligence."
    : null;

  return {
    live: true, month: month, lastRebuild: lastRebuild, vendors: vendors, V: V, weights: weights, weightsText: weightsText,
    tiles: tiles, counts: counts, pkgOf: (id) => pkgById[id] || "Unclassified",
    pendingCount: tiles.pending, kpis: (summary && summary.kpis) || null
  };
}

// ── controller methods ──────────────────────────────────────────────────
export const vendorsLiveMethods = {
  // Shaped once per load — renderVals runs on every keystroke.
  vpModel() {
    const raw = this.state.vpRaw;
    const m = this._vpMemo;
    if (m && m.raw === raw) return m.model;
    const model = shapeLiveVendors(raw);
    this._vpMemo = { raw: raw, model: model };
    return model;
  },
  vpIsLive() { return !!this.state.vpRaw; },

  // Every read is independent: the page goes live when any contract-performance read
  // answers, and stays empty — saying so — when none of them did.
  async vpLoad(opts) {
    if (this._vpLoading) return;
    this._vpLoading = true;
    clearTimeout(this._vpRetry);
    clearTimeout(this._vpRefresh);
    this.setState({ vpLoading: true });
    const reads = {
      summary: () => opsApi.contractSummary(),
      contracts: () => opsApi.contracts(),
      weights: () => opsApi.weights(),
      approvals: () => opsApi.contractApprovals(),
      certificates: () => complianceApi.listCertificates({ cert_scope: "vendor" })
    };
    const keys = Object.keys(reads);
    const settled = await Promise.allSettled(keys.map((k) => reads[k]()));
    const raw = { fetchedAt: new Date().toISOString(), errors: {} };
    let answered = 0;
    // Set by any read that outlived the company it was issued under (api/client.js). Carried
    // as a flag rather than sniffed out of the message later, so the check survives a reword.
    let stale = false;
    settled.forEach((r, i) => {
      if (r.status === "fulfilled") { raw[keys[i]] = r.value; answered += 1; }
      else {
        raw[keys[i]] = null; raw.errors[keys[i]] = (r.reason && r.reason.message) || String(r.reason);
        if (isStaleScope(r.reason)) stale = true;
      }
    });
    // The summary is the scorecards read; only when it failed is the list worth a second call.
    if (!raw.summary) {
      try { raw.scorecards = await opsApi.scorecards({ limit: 200 }); answered += 1; }
      catch (e) { raw.scorecards = null; raw.errors.scorecards = (e && e.message) || String(e); if (isStaleScope(e)) stale = true; }
    }
    const certRows = (raw.certificates && raw.certificates.certificates) || null;
    raw.certificates = certRows;
    // Coverage and pack names per country the vendor certificates are in (UK when none say).
    const ccs = [];
    (certRows || []).forEach((r) => { const cc = normCountry(r.country_code); if (ccs.indexOf(cc) < 0) ccs.push(cc); });
    if (!ccs.length) ccs.push("UK");
    raw.coverage = {}; raw.packs = {};
    await Promise.all(ccs.map(async (cc) => {
      const api = countryMeta(cc).api;
      const [v, p] = await Promise.allSettled([complianceApi.vendorCoverage(api), complianceApi.countryPack(api)]);
      // "fulfilled" only means the fetch resolved — apiFetch returns null for an empty 200,
      // and `null.vendors` would throw here rather than fall back to the empty list below.
      raw.coverage[cc] = v.status === "fulfilled" ? ((v.value && v.value.vendors) || []) : [];
      raw.packs[cc] = p.status === "fulfilled" ? ((p.value && p.value.types) || []) : [];
      if (v.status === "rejected") {
        raw.errors["coverage:" + cc] = (v.reason && v.reason.message) || String(v.reason);
        if (isStaleScope(v.reason)) stale = true;
      }
    }));
    // A company switch mid-load: api/client.js disowned every read that was in flight and
    // loadLiveData() has already started correctly-scoped ones. Reporting these would put a
    // spurious error on the new company's register and retry against the old one.
    if (stale) return;
    this._vpLoading = false;
    if (!answered) {
      const msg = raw.errors[keys[0]] || "unreachable";
      this._vpAttempts = (this._vpAttempts || 0) + 1;
      this.setState({ vpLoading: false, vpError: msg });
      if (this._vpAttempts < RETRY_MAX) this._vpRetry = setTimeout(() => this.vpLoad(), RETRY_MS);
      if (opts && opts.announce) this.flash("Contract performance backend unreachable — " + msg);
      return;
    }
    this._vpAttempts = 0;
    this.setState({ vpRaw: raw, vpLoading: false, vpError: "", vpLoadedAt: raw.fetchedAt });
    if (opts && opts.announce) {
      const m = shapeLiveVendors(raw);
      this.flash("Vendors refreshed — " + m.vendors.length + " vendors" + (m.month ? ", " + m.month + " scorecard" : ""));
    }
    this._vpRefresh = setTimeout(() => this.vpLoad(), REFRESH_MS);
  },
  vpRetryNow() { this._vpAttempts = 0; return this.vpLoad({ announce: true }); }
};
