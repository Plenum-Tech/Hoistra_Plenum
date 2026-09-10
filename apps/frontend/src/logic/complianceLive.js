// complianceLive — the compliance console read from svc-operations-intelligence.
//
// Reads certificates, per-building and per-vendor coverage and the country packs, and
// shapes them exactly like the seed dataset (src/data/hoistra-compliance.js) so
// ccModel() renders either without knowing which it has. The seed stays as the fallback
// when the backend is unreachable, and the page always says which one it is showing —
// nothing is asserted without disclosure.
//
// Methods are mixed into HoistraLogic.prototype; `this` is the controller.
import { HOISTRA_CC } from '../data/hoistra-compliance.js';
import { complianceApi } from '../api/compliance.js';
import { deepAgentsApi, newTurn } from '../api/deepAgents.js';
import { errorFromAnswer } from './chat.js';

// The backend spells the Emirates "UAE"; the shell spells it "AE" (PACKS, CC_OF).
const COUNTRY = {
  UK: { code: "UK", api: "UK", flag: "🇬🇧", name: "United Kingdom" },
  US: { code: "US", api: "US", flag: "🇺🇸", name: "United States" },
  AE: { code: "AE", api: "UAE", flag: "🇦🇪", name: "UAE" },
  SG: { code: "SG", api: "SG", flag: "🇸🇬", name: "Singapore" }
};
export function normCountry(raw) {
  const u = String(raw || "UK").trim().toUpperCase();
  if (u === "UAE" || u === "AE" || u === "ARE") return "AE";
  if (u === "GB" || u === "UK" || u === "GBR") return "UK";
  if (u === "US" || u === "USA") return "US";
  if (u === "SG" || u === "SGP") return "SG";
  return u;
}
export function countryMeta(code) { return COUNTRY[code] || { code: code, api: code, flag: "🏳", name: code }; }

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
export function fmtDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d)) return String(iso);
  return String(d.getDate()).padStart(2, "0") + " " + MONTHS[d.getMonth()] + " " + d.getFullYear();
}
export function fmtTime(iso) {
  const d = iso ? new Date(iso) : null;
  if (!d || isNaN(d)) return "—";
  const today = new Date();
  const hm = String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
  return d.toDateString() === today.toDateString() ? hm + " today" : hm + " · " + fmtDate(d.toISOString());
}
// Tick labels for the expiry runway: today, then +3/+6/+9/+12 months.
export function runwayTicks() {
  const out = [];
  for (let i = 0; i <= 12; i += 3) {
    const d = new Date(); d.setDate(1); d.setMonth(d.getMonth() + i);
    out.push(MONTHS[d.getMonth()] + " " + String(d.getFullYear()).slice(2));
  }
  return out;
}
function daysUntil(iso, stored) {
  if (typeof stored === "number" && isFinite(stored)) return stored;
  if (!iso) return null;
  const d = new Date(iso);
  if (isNaN(d)) return null;
  const today = new Date(); today.setHours(0, 0, 0, 0);
  d.setHours(0, 0, 0, 0);
  return Math.round((d - today) / 86400000);
}

// Expiry runway: 14% of the width is today, the right edge is twelve months out; lapsed
// certificates sit in the 0-14% band. Same geometry as the seed's hand-placed `pos`.
export function runwayPos(days) {
  if (days === null || days === undefined || !isFinite(days)) return 99;
  const p = 14 + days * (85 / 365);
  return Math.max(0.5, Math.min(99, Math.round(p * 10) / 10));
}
// Relative expiry copy. Infinity = no expiry date on the record.
export function relDays(days, long) {
  if (!isFinite(days)) return long ? "no expiry on record" : "no expiry";
  if (days < 0) return Math.abs(days) + (long ? " days ago" : "d ago");
  return "in " + days + (long ? " days" : "d");
}

function authOf(r) {
  const verdict = String(r.forensics_verdict || r.forensics_ccc_verdict || "").toLowerCase();
  const score = r.forensics_risk_score;
  if (r.authenticity_warning || /suspect|forg|fail|reject|high/.test(verdict)) {
    return { auth: "suspect" + (score !== null && score !== undefined ? " · " + Math.round(Number(score)) : ""), authSev: "warn" };
  }
  if (/genuine|pass|verified|clear|low/.test(verdict)) return { auth: "genuine", authSev: "ok" };
  return { auth: "not checked", authSev: "none" };
}
function verOf(r) {
  const meta = r.raw_metadata || {};
  const v = meta.verification || meta.ccc_verification || {};
  const st = v.status || v.result || meta.verification_status;
  if (st) return String(st).replace(/_/g, " ");
  return r.issuing_body || "Not checked";
}
function riskOf(days, blocked) {
  if (blocked) return { risk: "Blocked", sev: "risk" };
  if (!isFinite(days)) return { risk: "No expiry", sev: "none" };
  if (days < 0) return { risk: "Lapsed", sev: "risk" };
  if (days <= 30) return { risk: "<30d", sev: "warn" };
  if (days <= 90) return { risk: "<90d", sev: "warn" };
  return { risk: "OK", sev: "ok" };
}

// Pure: API payloads -> the seed's shape. Exported so it can be tested without a store.
//   certificates: rows from GET /api/compliance/certificates
//   coverage:     { [cc]: { buildings: [...], vendors: [...] } }  (GET /coverage/*)
//   packs:        { [cc]: types[] }                              (GET /country-pack)
export function shapeLiveCompliance(input) {
  const certificates = (input && input.certificates) || [];
  const coverage = (input && input.coverage) || {};
  const packs = (input && input.packs) || {};

  const typeName = {};
  const packByCc = {};
  Object.keys(packs).forEach((cc) => {
    const p = { building: [], vendor: [], byCode: {} };
    (packs[cc] || []).forEach((t) => {
      const code = t.certificate_type_code;
      if (!code) return;
      p.byCode[code] = t;
      if (!typeName[code]) typeName[code] = t.certificate_type_name || code;
      (String(t.certificate_scope || "").toLowerCase() === "vendor" ? p.vendor : p.building).push(code);
    });
    packByCc[cc] = p;
  });
  const nameOf = (code) => typeName[code] || String(code || "").replace(/_/g, " ");

  const rows = certificates.map((r) => {
    const cc = normCountry(r.country_code);
    const kind = String(r.cert_scope || "").toLowerCase() === "vendor" ? "vendor" : "building";
    const d0 = daysUntil(r.expiry_date, r.days_to_expiry);
    const days = d0 === null ? Infinity : d0;
    const vendorBlocked = String(r.vendor_block_state || "").toLowerCase() === "blocked" || String(r.risk_badge || "").toLowerCase() === "blocked";
    const building = r.building_name || r.site_label || r.building_reference || null;
    const vendor = r.vendor_name || null;
    const holder = kind === "vendor" ? (vendor || "Unlinked vendor") : (building || "No building on certificate");
    const a = authOf(r);
    const rk = riskOf(days, kind === "vendor" && vendorBlocked);
    // The real file behind this certificate, if any — the id a download link is built
    // from. Falls back to the first linked document when the certificate's own
    // document_id/graph_document_id were not carried through by the API response.
    const documentId = r.document_id || r.graph_document_id
      || (Array.isArray(r.linked_documents) && r.linked_documents[0]
          && r.linked_documents[0].document_id) || null;
    return {
      id: r.id, code: r.certificate_type_code, number: r.certificate_number || null,
      nm: r.certificate_type_name || nameOf(r.certificate_type_code),
      holder: holder, kind: kind, cc: cc, state: r.state || r.region || null,
      building: building, vendor: vendor, vendorId: r.vendor_id || null,
      exp: fmtDate(r.expiry_date), days: days, risk: rk.risk, sev: rk.sev,
      auth: a.auth, authSev: a.authSev, ver: verOf(r), pos: runwayPos(d0),
      status: r.status || null, trade: r.trade_category || null,
      verificationUrl: r.verification_url || null, draft: !!r.draft,
      // Is there a source document behind this certificate, or only fields someone typed?
      // A certificate with no document cannot be re-read, cannot be scored by forensics and
      // cannot be produced to an insurer — so the register says which it is rather than
      // showing both the same way.
      doc: !!documentId, documentId: documentId
    };
  });

  // Buildings: coverage rows first (they carry required/on-record), then anything a
  // certificate names that coverage did not (a building in a country whose pack is not seeded).
  const bMap = {};
  const ensureB = (name, cc) => {
    if (!bMap[name]) bMap[name] = { name: name, cc: cc, state: null, use: "—", cov: 0, on: 0, req: 0, certs: 0, high: 0, med: 0, cur: 0, blocked: 0, noDoc: 0, gaps: [], gapCodes: [], linked: false, siteId: null, _n: 0, _codes: {} };
    return bMap[name];
  };
  // The country comes from the ROW, not from which country's endpoint returned it. The
  // outer key is only "whose coverage did we ask for"; stamping it onto every building in
  // the reply is what put a Dubai hospital and a New York mall inside the United Kingdom
  // scope chip, and from there into the UK's building count, certificate count and tiles.
  Object.keys(coverage).forEach((cc) => {
    ((coverage[cc] && coverage[cc].buildings) || []).forEach((b) => {
      const x = ensureB(b.site_name || "No building on certificate", normCountry(b.country_code || cc));
      x.cov = Math.round(Number(b.coverage_pct || 0)); x.on = b.on_record || 0; x.req = b.required || 0;
      x.certs = b.certificates_total || 0; x.linked = !!b.linked; x.siteId = b.site_id || b.site_ref || null;
      x.gapCodes = b.gaps || []; x.gaps = (b.gaps || []).map(nameOf);
      x.use = b.linked ? "Site on record" : "Named on certificate only";
      // Where it is, for the scope filter's region tier. The certificate's own state is
      // the fallback below; the site row is the better source because it is filled in for
      // every building rather than only the ones whose document happened to state one.
      if (!x.state) x.state = b.state || b.region || null;
    });
  });
  rows.filter((c) => c.kind === "building").forEach((c) => {
    const x = ensureB(c.holder, c.cc);
    // The building map is keyed by name, but coverage is fetched once per country and a
    // site with no country recorded comes back under more than one — so whichever
    // response landed first used to decide the building's country, at random. The
    // certificates held against the building are the authoritative statement of which
    // country it is in, so they win here.
    if (c.cc) x.cc = c.cc;
    if (!x.state && c.state) x.state = c.state;
    x._n += 1; x._codes[c.code] = true;
    if (!c.doc) x.noDoc += 1;
    if (c.days <= 30) x.high += 1; else if (c.days <= 90) x.med += 1; else x.cur += 1;
  });
  Object.keys(bMap).forEach((name) => {
    const x = bMap[name];
    if (!x.certs) x.certs = x._n;
    if (!x.req) {
      const p = packByCc[x.cc];
      const codes = Object.keys(x._codes);
      x.req = p && p.building.length ? p.building.length : codes.length;
      x.on = p && p.building.length ? codes.filter((k) => p.building.indexOf(k) > -1).length : codes.length;
      x.cov = x.req ? Math.round((100 * x.on) / x.req) : 0;
      x.gapCodes = p ? p.building.filter((k) => !x._codes[k]) : [];
      x.gaps = x.gapCodes.map(nameOf);
    }
    if (!x.state) x.state = countryMeta(x.cc).name + " · region not recorded";
    delete x._n; delete x._codes;
  });

  // Vendors: coverage rows plus every vendor holding an accreditation certificate.
  const vMap = {};
  const ensureV = (name, cc) => {
    if (!vMap[name]) vMap[name] = { name: name, cc: cc, id: null, cov: 0, on: 0, req: 0, block: "Clear", worst: "OK", sev: "ok", serves: [], gaps: [], certs: 0, noDoc: 0, spec: "—", _n: 0, _worst: Infinity };
    return vMap[name];
  };
  Object.keys(coverage).forEach((cc) => {
    ((coverage[cc] && coverage[cc].vendors) || []).forEach((v) => {
      const x = ensureV(v.vendor_name || v.vendor_id, normCountry(v.country_code || cc));
      x.id = v.vendor_id || null; x.cov = Math.round(Number(v.coverage_pct || 0)); x.on = v.on_record || 0; x.req = v.required || 0;
      x.certs = v.certificates_total || 0; x.gaps = (v.gaps || []).map(nameOf);
      if (/blocked/i.test(String(v.block_state || ""))) x.block = "Blocked";
      if (v.blocked_accreditation_type) x.blockedType = nameOf(v.blocked_accreditation_type);
    });
  });
  rows.filter((c) => c.kind === "vendor").forEach((c) => {
    const x = ensureV(c.holder, c.cc);
    // Same fix as buildings: vendor coverage is fetched once per country and a vendor with
    // no country recorded comes back under several, so the first response used to decide
    // the vendor's country at random — which dropped every vendor from scope as soon as a
    // country was picked. Its own accreditations are the authoritative statement.
    if (c.cc) x.cc = c.cc;
    if (!x.id && c.vendorId) x.id = c.vendorId;
    x._n += 1;
    if (!c.doc) x.noDoc += 1;
    if (x.spec === "—" && c.trade) x.spec = c.trade;
    x._worst = Math.min(x._worst, c.days);
    if (c.risk === "Blocked") x.block = "Blocked";
  });
  // A vendor "serves" the buildings whose building certificates name it.
  rows.filter((c) => c.kind === "building" && c.vendor).forEach((c) => {
    const x = vMap[c.vendor];
    if (x && x.serves.indexOf(c.holder) < 0) x.serves.push(c.holder);
  });
  Object.keys(vMap).forEach((name) => {
    const x = vMap[name];
    if (!x.certs) x.certs = x._n;
    const w = x._worst;
    x.worst = x.block === "Blocked" ? "Blocked" : !isFinite(w) ? (x.certs ? "OK" : "None on file") : w < 0 ? "Lapsed" : w <= 30 ? "<30d" : w <= 90 ? "<90d" : "OK";
    x.sev = x.block === "Blocked" || x.worst === "Lapsed" ? "risk" : /</.test(x.worst) ? "warn" : x.certs ? "ok" : "none";
    if (!x.req) x.req = Math.max(x.on, 1);
    if (!x.cov && x.req) x.cov = Math.round((100 * x.on) / x.req);
    delete x._n; delete x._worst;
  });
  const vendors = Object.keys(vMap).map((k) => vMap[k]);
  Object.keys(bMap).forEach((name) => {
    bMap[name].blocked = vendors.filter((v) => v.block === "Blocked" && v.serves.indexOf(name) > -1).length;
  });

  // States (sub-national) with certificate totals, from the buildings' recorded state.
  const stMap = {};
  const stKey = (cc, st) => cc + "|" + st;
  Object.keys(bMap).forEach((name) => {
    const b = bMap[name];
    const k = stKey(b.cc, b.state);
    if (!stMap[k]) stMap[k] = { name: b.state, country: b.cc, certs: 0, lapsed: 0 };
  });
  rows.forEach((c) => {
    const b = c.kind === "building" ? bMap[c.holder] : null;
    if (!b) return;
    const s = stMap[stKey(b.cc, b.state)];
    s.certs += 1; if (c.days < 0) s.lapsed += 1;
  });

  // Region identity must carry its country. Two countries can hold the same region name
  // — the live register has "England" under both UK and US — and keying the scope filter
  // on the bare name made one row's tick select both, so narrowing to UK/England also
  // pulled in the US buildings. `key` is what selection compares on; `label` names the
  // country only when the region name is genuinely ambiguous, so the common case stays clean.
  const states = Object.keys(stMap).map((k) => stMap[k]);
  const stSeen = {};
  states.forEach((st) => { stSeen[st.name] = (stSeen[st.name] || 0) + 1; });
  states.forEach((st) => {
    st.key = stKey(st.country, st.name);
    st.label = stSeen[st.name] > 1 ? st.name + " · " + countryMeta(st.country).name : st.name;
  });

  const ccSet = {};
  rows.forEach((c) => { ccSet[c.cc] = true; });
  Object.keys(coverage).forEach((cc) => { ccSet[cc] = true; });
  const countries = Object.keys(ccSet).map((cc) => {
    const m = countryMeta(cc);
    const cs = rows.filter((c) => c.cc === cc);
    const p = packByCc[cc];
    return {
      code: cc, flag: m.flag, name: m.name,
      pack: p && (p.building.length || p.vendor.length) ? p.building.length + " building · " + p.vendor.length + " vendor types" : "pack not loaded",
      certs: cs.length, lapsed: cs.filter((c) => c.days < 0).length
    };
  }).sort((a, b) => b.certs - a.certs || a.name.localeCompare(b.name));

  // Requirement matrix: the most-held building types first, capped so it stays a summary.
  const held = {};
  rows.filter((c) => c.kind === "building").forEach((c) => { held[c.code] = (held[c.code] || 0) + 1; });
  const typeSet = {};
  Object.keys(packByCc).forEach((cc) => packByCc[cc].building.forEach((code) => { typeSet[code] = true; }));
  Object.keys(held).forEach((code) => { typeSet[code] = true; });
  const mxCodes = Object.keys(typeSet)
    .sort((a, b) => (held[b] || 0) - (held[a] || 0) || nameOf(a).localeCompare(nameOf(b)))
    .slice(0, 18);
  const mx = {};
  Object.keys(bMap).forEach((name) => {
    const b = bMap[name];
    const p = packByCc[b.cc];
    const own = rows.filter((c) => c.kind === "building" && c.holder === name);
    mx[name] = mxCodes.map((code) => {
      const cs = own.filter((c) => c.code === code);
      if (cs.length) {
        const d = Math.min.apply(null, cs.map((c) => c.days));
        return d < 0 ? "risk" : d <= 90 ? "warn" : "ok";
      }
      if (!p || !p.building.length) return "gap";
      return p.building.indexOf(code) > -1 ? "gap" : "na";
    });
  });

  const gapSeen = {};
  const gapTypes = [];
  Object.keys(bMap).forEach((name) => bMap[name].gaps.forEach((g) => { if (!gapSeen[g]) { gapSeen[g] = true; gapTypes.push(g); } }));

  const buildings = Object.keys(bMap).map((k) => bMap[k]).sort((a, b) => b.certs - a.certs || a.name.localeCompare(b.name));
  return {
    live: true,
    countries: countries,
    states: states,
    buildings: buildings,
    vendors: vendors.sort((a, b) => b.certs - a.certs || a.name.localeCompare(b.name)),
    certs: rows,
    mxTypes: mxCodes.map(nameOf),
    mxShort: mxCodes.map((code) => String(code).replace(/_/g, " ").slice(0, 8)),
    mx: mx,
    mxLabel: HOISTRA_CC.mxLabel,
    mxGlyph: HOISTRA_CC.mxGlyph,
    gapTypes: gapTypes
  };
}

// The compliance preflight attaches its structured answer to two tool calls:
//   compliance_response → { narrative, sections, groups, kpis, actions, insights,
//                           certificates, pending, validation, offers }
//   compliance_pipeline → { steps: [{ stage, label, detail, parts?, queries?, issues? }] }
// (See svc-deepagents orchestrator.py — the schema is declared around line 2200.)
//
// Returns null when the preflight did not answer — the generic select_skill → task path
// carries none of this, and the reply then renders as markdown instead.
export function extractComplianceAnswer(toolCalls) {
  const calls = toolCalls || [];
  const find = (name) => {
    const hit = calls.find((t) => t && t.tool === name);
    const out = hit && hit.output;
    // Outputs arrive as objects on this path, but a JSON string is cheap to tolerate.
    if (typeof out === "string") {
      try { return JSON.parse(out); } catch (e) { return null; }
    }
    return out && typeof out === "object" ? out : null;
  };

  const res = find("compliance_response");
  const pipe = find("compliance_pipeline");
  if (!res && !pipe) return null;

  const arr = (v) => (Array.isArray(v) ? v : []);
  return {
    narrative: (res && res.narrative) || "",
    sections: arr(res && res.sections),
    groups: arr(res && res.groups),
    kpis: arr(res && res.kpis),
    actions: arr(res && res.actions),
    insights: arr(res && res.insights),
    certificates: arr(res && res.certificates),
    pending: arr(res && res.pending),
    offers: arr(res && res.offers),
    validation: (res && res.validation) || null,
    steps: arr(pipe && pipe.steps),
    // This build reports no per-step model/ms/usd and no cost block, so nothing about
    // cost is shown rather than guessed. `cost` stays here for a build that does.
    cost: (pipe && pipe.cost) || null
  };
}

// Whether a `rich` payload has any card content of its own — narrative, sections, groups,
// KPIs, actions, insights, certificates, pending items or offers. `compliance_response` does
// not fire on every turn (some route through the pipeline alone, or through a plain skill
// with no structured payload), and when it doesn't, `extractComplianceAnswer` still returns
// a `rich` object because `compliance_pipeline`'s steps are enough to build one — every card
// field on it is simply empty. Callers use this to decide whether to fall back to the plain
// markdown answer instead of rendering an empty-looking card.
export function richHasCards(rich) {
  if (!rich) return false;
  return !!rich.narrative || !!(rich.sections || []).length || !!(rich.groups || []).length ||
    !!(rich.kpis || []).length || !!(rich.actions || []).length || !!(rich.insights || []).length ||
    !!(rich.certificates || []).length || !!(rich.pending || []).length || !!(rich.offers || []).length;
}

// $ formatting for the cost panel: a handful of significant figures, never scientific
// notation. The turn total reads naturally with 3 decimals ("$0.522"); a single role's
// slice is usually under a cent, where 3 fixed decimals would print "$0.045" for a call
// that actually cost $0.0449 — 2 significant figures ("$0.045", "$0.0037") keeps the
// number honest at whatever scale it lands.
function fmtUsdSig(usd, sig) {
  const n = Number(usd) || 0;
  if (n === 0) return "$0";
  const digits = sig || 2;
  const mag = Math.floor(Math.log10(Math.abs(n)));
  const decimals = Math.max(0, digits - 1 - mag);
  return "$" + n.toFixed(Math.min(decimals, 6));
}
const fmtSecs = (ms) => (Math.round(ms) / 1000).toFixed(ms < 10000 ? 1 : 0) + " s";

// Shapes one turn's llm_cost ledger summary (svc-deepagents `compliance_pipeline.output.cost`
// — see llm_cost.py `Ledger.summary()`) into the figures the answer panel's header and its
// "Cost by role" bar read. Every number here is what the orchestrator actually spent on this
// turn's model calls; nothing is estimated or inferred. Returns null when the turn carried no
// ledger at all (the shortcut ran before llm_cost tracked it, or `cost` was never attached).
export function costSummary(cost) {
  if (!cost || typeof cost !== "object") return null;
  const byRole = cost.by_role || {};
  const roleKeys = Object.keys(byRole);
  if (!cost.calls && !roleKeys.length) return null;
  const total = Number(cost.usd) || 0;
  const roles = roleKeys
    .map((role) => ({ role: role, usd: Number(byRole[role]) || 0 }))
    .sort((a, b) => b.usd - a.usd)
    .map((r) => ({
      role: r.role,
      label: r.role.replace(/_/g, " "),
      usd: r.usd,
      usdLabel: fmtUsdSig(r.usd, 2),
      // Share of the priced total, for the segmented bar. A role costing $0 (a pure lookup
      // step with no LLM call recorded under it) still gets a legend row at 0 width.
      pct: total > 0 ? Math.max(0, Math.min(100, (r.usd / total) * 100)) : 0
    }));
  return {
    calls: cost.calls || 0,
    usd: total,
    usdLabel: fmtUsdSig(total, 3),
    usdComplete: cost.usd_complete !== false,
    secsLabel: typeof cost.wall_ms === "number" ? fmtSecs(cost.wall_ms) : null,
    models: (cost.models || []).filter(Boolean),
    inputTokens: cost.input_tokens || 0,
    outputTokens: cost.output_tokens || 0,
    cacheRead: cost.cache_read || 0,
    cacheReadLabel: cost.cache_read ? cost.cache_read.toLocaleString("en-GB") + " tok" : null,
    roles: roles
  };
}

// Shapes one pipeline step's own ledger fields (`ms`, `usd`, `model`, `effort`, `cache_hit` —
// attached server-side to the step that made the call, right after that call returns) into
// the small badge a step row shows beside its label. A step with no `ms` never made a model
// call the ledger saw (a database read, or a call that raised before recording) — it renders
// with no badge, not a "0 s / $0" that would read as a free call that happened.
export function stepCostLabel(step) {
  if (!step || typeof step.ms !== "number") return null;
  const hasUsd = typeof step.usd === "number";
  return {
    secsLabel: fmtSecs(step.ms),
    usdLabel: hasUsd ? fmtUsdSig(step.usd, 2) : null,
    model: step.model || null,
    effort: step.effort || null,
    cacheHit: !!step.cache_hit,
    // One line for a compact render, e.g. "2.7 s · $0.0037 · claude-sonnet-5 · low".
    line: [
      fmtSecs(step.ms),
      hasUsd ? fmtUsdSig(step.usd, 2) : null,
      step.model || null,
      [step.effort, step.cache_hit ? "cache hit" : null].filter(Boolean).join(" · ") || null
    ].filter(Boolean).join(" · ")
  };
}

// Days-overdue bars for the answer panel, split by scope. The analysis names its
// certificates by id, and the live register already knows each one's days-to-expiry, so
// the two are joined on id rather than re-deriving dates. `reason` carries "… , N days"
// as a fallback for a certificate the register does not hold.
export function overdueBars(certificates, register) {
  const byId = {};
  ((register && register.certs) || []).forEach((c) => { if (c.id) byId[c.id] = c; });
  const parse = (reason) => {
    const m = /(\d[\d,]*)\s*days?/i.exec(String(reason || ""));
    return m ? Number(m[1].replace(/,/g, "")) : null;
  };
  const rows = { building: [], vendor: [] };
  (certificates || []).forEach((c) => {
    const live = byId[c.id];
    const days = live && isFinite(live.days) ? Math.max(0, -live.days) : parse(c.reason);
    if (!days) return;
    const scope = String(c.scope || "").toLowerCase() === "vendor" ? "vendor" : "building";
    rows[scope].push({ label: (scope === "vendor" ? "Vendor — " : "Building — ") + (c.company || c.name), days: days, severity: c.severity });
  });
  const top = (list) => list.sort((a, b) => b.days - a.days).slice(0, 8);
  const scale = (list) => {
    const max = list.reduce((m, r) => Math.max(m, r.days), 0) || 1;
    return list.map((r) => Object.assign({}, r, { pct: Math.max(1, Math.round((r.days / max) * 100)) + "%" }));
  };
  return { buildings: scale(top(rows.building)), vendors: scale(top(rows.vendor)) };
}

// Pages that answer in the side dock rather than on the chat page. Home is one of them: the
// tiles and the ask bar stay in view and the orchestrator works beside them, as it does on
// the compliance console. The chat page is reached from a space's ask bar and by reopening
// a conversation from the sessions list.
export const DOCK_VIEWS = ["cc", "vp", "buildings"];
// Home is deliberately not a dock view: a question from the hero bar (or "+ New query") is the
// main orchestrator's and answers on the chat page. The dock on Home is only what the top-bar
// icon opens beside the tiles.
const RETRY_MS = 30000;
const RETRY_MAX = 6;

export const complianceLiveMethods = {
  // What ccModel() reads: the live register when loaded, the seed otherwise.
  ccData() { return this.state.ccLive || HOISTRA_CC; },
  ccIsLive() { return !!this.state.ccLive; },

  async ccLoad(opts) {
    if (this._ccLoading) return;
    this._ccLoading = true;
    clearTimeout(this._ccRetry);
    this.setState({ ccLoading: true });
    try {
      const certRes = await complianceApi.listCertificates();
      const certificates = (certRes && certRes.certificates) || [];
      const ccs = [];
      certificates.forEach((r) => { const cc = normCountry(r.country_code); if (ccs.indexOf(cc) < 0) ccs.push(cc); });
      if (!ccs.length) ccs.push("UK");
      const coverage = {}, packs = {};
      await Promise.all(ccs.map(async (cc) => {
        const api = countryMeta(cc).api;
        const [b, v, p] = await Promise.allSettled([
          complianceApi.buildingCoverage(api), complianceApi.vendorCoverage(api), complianceApi.countryPack(api)
        ]);
        coverage[cc] = {
          buildings: b.status === "fulfilled" ? (b.value.buildings || []) : [],
          vendors: v.status === "fulfilled" ? (v.value.vendors || []) : []
        };
        packs[cc] = p.status === "fulfilled" ? (p.value.types || []) : [];
      }));
      const shaped = shapeLiveCompliance({ certificates: certificates, coverage: coverage, packs: packs });
      this._ccAttempts = 0;
      this.setState((s) => {
        // Prune scope selections that do not exist in the live register.
        const cN = shaped.countries.map((c) => c.code);
        const sN = shaped.states.map((x) => x.name);
        const bN = shaped.buildings.map((b) => b.name);
        return {
          ccLive: shaped, ccLoading: false, ccError: "", ccLoadedAt: new Date().toISOString(),
          ccCountries: s.ccCountries.filter((c) => cN.indexOf(c) > -1),
          ccStates: s.ccStates.filter((x) => sN.indexOf(x) > -1),
          ccBuildings: s.ccBuildings.filter((x) => bN.indexOf(x) > -1)
        };
      });
      if (opts && opts.announce) this.flash("Compliance register loaded — " + certificates.length + " certificates");
    } catch (e) {
      const msg = (e && e.message) || String(e);
      this._ccAttempts = (this._ccAttempts || 0) + 1;
      this.setState({ ccLoading: false, ccError: msg });
      if (this._ccAttempts < RETRY_MAX) this._ccRetry = setTimeout(() => this.ccLoad(), RETRY_MS);
      if (opts && opts.announce) this.flash("Compliance backend unreachable — " + msg);
    } finally {
      this._ccLoading = false;
    }
  },

  ccRetryNow() { this._ccAttempts = 0; return this.ccLoad({ announce: true }); },

  // Ask-bar router. The compliance console, the home page and the chat page run their
  // questions through the live orchestrator; every other screen keeps the existing
  // seed-answer behaviour, so nothing else changes shape.
  // Views whose ask bar talks to the live orchestrator. The console, the vendors page and the
  // buildings page keep their side dock (DOCK_VIEWS) so the data stays in view beside the
  // answer; home and the chat page make the conversation the page.
  // A space's ask bar starts a conversation filed in that space, so it is a chat view too.
  chatView() { return ["cc", "home", "chat", "space"].concat(DOCK_VIEWS).indexOf(this.state.view) > -1; },
  // Where the answer lands: the dock on the dock pages (Home included), the chat page
  // everywhere else. A task carried onto a dock page (the Hoist a building form, say) stays
  // in view and the question is answered beside it.
  dockAnswers() { return DOCK_VIEWS.indexOf(this.state.view) > -1; },
  askScoped(q) {
    if (this.chatView()) return this.ccAsk(q);
    return this.ask(q);
  },

  // What the orchestrator is told about the page the question came from. Live figures
  // when the backend answered; an honest note when it did not.
  chatContext() {
    const s = this.state;
    if (s.view === "vp") {
      const vm = this.vpModel();
      const parts = ["The user is on the Hoistra vendors page (contract performance)."];
      if (vm.live) {
        const sel = vm.vendors.find((v) => v.id === s.vpVendor) || vm.vendors[0] || null;
        const tabs = ["Scorecard", "Contract terms", "Evidence", "Coverage", "Invoices"];
        parts.push("It is showing live scorecards from svc-operations-intelligence: " + vm.vendors.length +
          (vm.vendors.length === 1 ? " vendor" : " vendors") + (vm.month ? ", " + vm.month + " scorecard" : "") + " — " +
          vm.vendors.map((v) => v.name + " " + (v.score === null ? "unscored" : v.score + "/100") + " (" + v.accred.toLowerCase() + (v.blocked ? ", blocked" : "") + ")").join("; ") + ".");
        if (sel) parts.push("Selected vendor: " + sel.name + ", tab: " + (tabs[s.vpTab] || tabs[0]) + ".");
        const t = vm.tiles;
        parts.push("Tiles — vendors blocked " + (t.blocked === null ? "—" : t.blocked) + ", pending approvals " + (t.pending === null ? "—" : t.pending) +
          ", invoice lines held " + (t.held === null ? "—" : t.held) + ", contract terms on platform default " + (t.defaults === null ? "—" : t.defaults) + ".");
      } else {
        parts.push("The contract-performance backend has not answered, so it is showing seed data.");
      }
      return parts.join(" ");
    }
    if (s.view === "buildings") {
      const rows = this.bldData();
      const parts = ["The user is on the Hoistra buildings page."];
      if (this.bldIsLive()) parts.push("The building table shows " + rows.length + " plenum_cafm sites live from svc-operations-intelligence (GET /api/energy/buildings).");
      else parts.push("The building table has not loaded from the backend yet.");
      const b = this.glSelected();
      if (b) {
        parts.push("Selected building: " + b.name + " (" + (b.buildingId ? "building_id " + b.buildingId : "sites.site_id " + b.id) + ").");
        const counted = Object.entries(b.counts || {}).map(([k, n]) => n + " " + k);
        if (counted.length) parts.push("On record: " + counted.join(", ") + ".");
      }
      const t = this.glTable(s.gTable);
      if (t) parts.push("Selected table: plenum_cafm." + t.table + (typeof t.rows === "number" ? " (" + t.rows + " rows)" : "") + ".");
      return parts.join(" ");
    }
    if (s.view === "cc") {
      const d = this.ccData();
      const scope = [
        s.ccCountries && s.ccCountries.length ? "countries: " + s.ccCountries.join(", ") : null,
        s.ccBuildings && s.ccBuildings.length ? "buildings: " + s.ccBuildings.join(", ") : null,
        s.ccStates && s.ccStates.length ? "regions: " + s.ccStates.join(", ") : null
      ].filter(Boolean).join(" · ");
      return "The user is on the Hoistra compliance console." +
        (this.ccIsLive()
          ? " It is showing the live register from svc-operations-intelligence: " +
            ((d.certs || []).length) + " certificates, " + ((d.buildings || []).length) + " buildings, " +
            ((d.vendors || []).length) + " vendors."
          : " The backend is unreachable, so it is showing seed data.") +
        (scope ? " Current scope filter — " + scope + "." : " No scope filter is applied.");
    }
    if (s.view === "space") {
      const e = this.spaceEntry(s.spaceKey);
      if (e && e.custom) return "The user is in “" + e.name + "”, a saved space they created in Hoistra to file conversations under. Answer the question on its own terms.";
      if (e) {
        return "The user is in the Hoistra “" + e.name + "” space — questions here are about " + e.page.toLowerCase() + "." +
          (e.live && e.kpis.length ? " Live figures from svc-operations-intelligence: " + e.kpis.map((k) => k.label.toLowerCase() + " " + k.value).join(", ") + "." : " The engine has not answered yet, so no live figures are on the page.");
      }
    }
    const h = this.homeModel();
    const parts = [s.view === "chat" ? "The user is in the Hoistra orchestrator conversation." : "The user is on the Hoistra home page."];
    // A dock carried onto the home page with a task in progress: say so, so "this building"
    // in a question can mean the one being hoisted.
    if (s.view === "home" && s.orchOpen && s.orchTask) {
      parts.push("The orchestrator dock is open on the task “" + s.orchTask.label + "”.");
    }
    if (h.hero.buildings !== null) {
      parts.push("Live register from svc-operations-intelligence: " + h.hero.buildings + " buildings, " +
        h.hero.certificates + " certificates, " + h.hero.vendors + " vendors" +
        (h.hero.countries.length ? " across " + h.hero.countries.join(", ") : "") + ".");
    }
    if (h.pending !== null) parts.push(h.pending + " items are pending in the approvals queue.");
    if (h.score.value !== null) parts.push("Hoist Score " + h.score.value + "/100 — " + h.score.band + "; " + h.score.gap + ".");
    if (!h.live && !this.ccIsLive()) parts.push("The operations backend is unreachable, so the page is showing seed data.");
    return parts.join(" ");
  },

  // "Run compliance scan" — the real A1 scan, then the register is re-read.
  //
  // This is a page action, not a conversation: it does not open the orchestrator dock.
  // Progress and the result are reported where the scan's effect is visible — the status
  // pill, the LAST RUN box and a toast — and every tile, the expiry runway and the pivots
  // are refreshed from the backend when it returns.
  async ccRunScan() {
    if (this.state.ccScanning) return;
    this.setState({ ccScanning: true, ccScanMsg: "" });
    this.flash("Compliance scan running — the register refreshes when it finishes.");
    // The UI's own action goes into the activity log as one turn: input now, output or error
    // when the scan returns, so it sits in the same trail as the server-side stages.
    const t0 = Date.now(); const turn = newTurn();
    deepAgentsApi.logActivity({ turn_id: turn, stage: "action", direction: "input", summary: "Run compliance scan", payload: { action: "scan", request: { scope: "all" } } });
    try {
      const res = await complianceApi.runScan({});
      deepAgentsApi.logActivity({ turn_id: turn, stage: "action", direction: "output", summary: "Compliance scan returned", latency_ms: Date.now() - t0, payload: { action: "scan", response: res } });
      const b = res && res.building_scanned, v = res && res.vendor_scanned;
      const n = (typeof b === "number" || typeof v === "number") ? (b || 0) + (v || 0) : null;
      const alerts = res && res.alerts_created;
      const blocks = res && res.blocks_set;

      // Re-read certificates, coverage and the country packs so the page matches what
      // the scan just wrote.
      await this.ccLoad();

      const msg = "Scan complete" +
        (n !== null ? " — " + n + " certificates evaluated (" + (b || 0) + " building, " + (v || 0) + " vendor)" : "") +
        (typeof alerts === "number" ? ", " + alerts + " alert" + (alerts === 1 ? "" : "s") + " raised" : "") +
        (typeof blocks === "number" && blocks ? ", " + blocks + " vendor block" + (blocks === 1 ? "" : "s") + " set" : "") +
        ". The register has been refreshed.";
      this.setState({ ccScanning: false, ccLastScan: new Date().toISOString(), ccScanMsg: msg });
      this.flash(msg);
    } catch (e) {
      deepAgentsApi.logActivity({ turn_id: turn, stage: "action", direction: "error", summary: "Compliance scan failed", ok: false, error: String((e && e.message) || e), latency_ms: Date.now() - t0, payload: { action: "scan" } });
      const msg = "The scan could not be run: " + ((e && e.message) || e) +
        ". Showing " + (this.ccIsLive() ? "the last loaded register" : "seed data") + ".";
      this.setState({ ccScanning: false, ccScanMsg: msg });
      this.flash(msg);
    }
  },

  // Actions for a certificate row. Live records add the two the backend can do now.
  ccCertActions(c) {
    const base = this.certActions(c);
    if (!c.id) return base;
    return base.concat(["Verify register", "Draft renewal email"]).filter((l, i, a) => a.indexOf(l) === i);
  },

  // Routes a certificate action to the compliance engine when the label has a true
  // endpoint behind it, and to the simulated orchestrator flow when it does not.
  // "Approve booking", "Extend deadline", "Change priority" and "Monitor" are booking /
  // workflow steps with no compliance-engine route, so they keep the existing behaviour
  // rather than being mapped onto an endpoint that means something else.
  ccRunCertAction(label, c, subject, vendorName) {
    const low = label.toLowerCase();
    if (c.id && /^verify register/.test(low)) return this.ccVerify(c, subject);
    if (c.id && /^draft renewal email/.test(low)) return this.ccRenewal(c, subject);
    if (c.code && /^change contractor/.test(low)) return this.ccContractors(c, subject);
    if (c.id && /^escalate/.test(low)) return this.ccEscalate(c, subject);
    if (/^request evidence from vendor/.test(low)) return this.ccVendorEvidence(c, subject);
    // Confirm finalises a draft. On an already-finalised record it would mean nothing,
    // so only a draft takes the live path.
    if (c.id && c.draft && /^acknowledge/.test(low)) return this.ccConfirmDraft(c, subject);
    return this.runAction(label, subject, vendorName);
  },

  async ccVerify(c, subject) {
    this.orch("Verify register", subject, [
      { a: "Orchestrator", t: "Intent: verify certificate against its register · " + c.nm },
      { a: "Planner", t: "Channel from compliance_verification_sources: public API → weekly dump → register bot → Verify-now link" },
      { a: "Worker", t: "POST /api/compliance/certificates/{id}/verify" },
      { a: "Quality", t: "Verdict written to the certificate record; the register re-reads" }
    ]);
    const t0 = Date.now(); const turn = newTurn();
    deepAgentsApi.logActivity({ turn_id: turn, stage: "action", direction: "input", summary: "Verify register — " + c.nm + " — " + c.holder, payload: { action: "verify", certificate_id: c.id, certificate: { type: c.nm, code: c.code, holder: c.holder, number: c.number } } });
    try {
      const r = await complianceApi.verifyCertificate(c.id);
      deepAgentsApi.logActivity({ turn_id: turn, stage: "action", direction: "output", summary: "Verify register returned " + String((r && (r.status || r.verification_status)) || "?"), latency_ms: Date.now() - t0, payload: { action: "verify", certificate_id: c.id, response: r } });
      const st = (r && (r.status || r.verification_status || (r.verification || {}).status)) || (r && r.ok ? "checked" : "unknown");
      const ch = r && (r.channel || (r.verification || {}).channel);
      const url = r && (r.verify_url || r.verification_url || (r.verification || {}).verify_url);
      this.setState({ flow: null, flowDone: "Verification returned “" + String(st).replace(/_/g, " ") + "” for " + c.nm + " — " + c.holder + (ch ? " via " + String(ch).replace(/_/g, " ") : "") + "." + (url ? " Register: " + url : "") });
      this.ccLoad();
    } catch (e) {
      deepAgentsApi.logActivity({ turn_id: turn, stage: "action", direction: "error", summary: "Verify register failed", ok: false, error: String((e && e.message) || e), latency_ms: Date.now() - t0, payload: { action: "verify", certificate_id: c.id } });
      this.setState({ flow: null, flowDone: "Verification failed: " + ((e && e.message) || e) });
    }
  },

  async ccRenewal(c, subject) {
    this.orch("Draft renewal email", subject, [
      { a: "Orchestrator", t: "Intent: renewal email · " + c.nm + " — " + c.holder },
      { a: "Planner", t: "Alert ladder step for " + relDays(c.days, true) + " → responsible party, tone, deadline" },
      { a: "Worker", t: "POST /api/compliance/certificates/renewal-email — drafted, not sent" },
      { a: "Quality", t: "Queued on the approvals card. Nothing leaves the platform until you send it." }
    ]);
    const t0 = Date.now(); const turn = newTurn();
    deepAgentsApi.logActivity({ turn_id: turn, stage: "action", direction: "input", summary: "Draft renewal email — " + c.nm + " — " + c.holder, payload: { action: "renewal_email", certificate_id: c.id, certificate: { type: c.nm, code: c.code, holder: c.holder, days: isFinite(c.days) ? c.days : null } } });
    try {
      const r = await complianceApi.draftRenewalEmail(c.id);
      deepAgentsApi.logActivity({ turn_id: turn, stage: "action", direction: r && r.ok === false ? "error" : "output", summary: (r && r.message) || "Renewal email drafted", ok: !(r && r.ok === false), latency_ms: Date.now() - t0, payload: { action: "renewal_email", certificate_id: c.id, response: r } });
      if (r && r.ok === false) throw new Error(r.error || "not drafted");
      const d = (r && r.email_draft) || {};
      this.setState({ flow: null, flowDone: (r && r.message) || "Renewal email drafted and queued for approval.", emTo: d.to || "", emSubject: d.subject || "", emBody: d.body || "" });
      this.ccLoad();
    } catch (e) {
      deepAgentsApi.logActivity({ turn_id: turn, stage: "action", direction: "error", summary: "Renewal email failed", ok: false, error: String((e && e.message) || e), latency_ms: Date.now() - t0, payload: { action: "renewal_email", certificate_id: c.id } });
      this.setState({ flow: null, flowDone: "Renewal email could not be drafted: " + ((e && e.message) || e) });
    }
  },

  // "Change contractor" — who else actually holds the accreditation this obligation needs.
  async ccContractors(c, subject) {
    this.orch("Change contractor", subject, [
      { a: "Orchestrator", t: "Intent: find an accredited alternative · " + c.nm },
      { a: "Planner", t: "Required accreditation is the certificate type on the record: " + c.code },
      { a: "Worker", t: "POST /api/compliance/contractors/recommend — vendors holding the type, blocked vendors excluded" },
      { a: "Quality", t: "Accreditation currency is read from the register record, not from the vendor's own claim" }
    ]);
    try {
      const r = await complianceApi.recommendContractors(c.code, 5);
      const names = ((r && r.contractors) || []).map((v) => v.vendor_name || v.name).filter(Boolean);
      this.setState({
        flow: null,
        flowDone: names.length
          ? names.length + " accredited alternative" + (names.length === 1 ? "" : "s") + " for " + c.nm + " — " + names.join(", ") + "."
          : "No vendor on record holds " + c.nm + ", so there is no accredited alternative to put forward. Onboard a vendor with this accreditation, or ingest the certificate that proves an existing one holds it."
      });
    } catch (e) {
      this.setState({ flow: null, flowDone: "Contractor recommendation failed: " + ((e && e.message) || e) });
    }
  },

  // "Escalate" — records that the remedial work on this certificate is outstanding.
  // The engine accepts Open | In Progress | Closed; escalating reopens it.
  async ccEscalate(c, subject) {
    this.orch("Escalate", subject, [
      { a: "Orchestrator", t: "Intent: escalate the remedial position · " + c.nm + " — " + c.holder },
      { a: "Planner", t: "Remedial status on the certificate record moves to Open" },
      { a: "Worker", t: "PATCH /api/compliance/certificates/{id}/remedial-status" },
      { a: "Quality", t: "Written to the compliance audit trail with actor and timestamp; the register re-reads" }
    ]);
    try {
      const r = await complianceApi.setRemedialStatus(c.id, "Open");
      if (r && r.ok === false) throw new Error(r.error || "not updated");
      this.setState({ flow: null, flowDone: "Remedial status for " + c.nm + " — " + c.holder + " is now Open. The exposure stays on the register until a satisfactory certificate is ingested." });
      this.ccLoad();
    } catch (e) {
      this.setState({ flow: null, flowDone: "The remedial status could not be updated: " + ((e && e.message) || e) });
    }
  },

  // "Request evidence from vendor" — the engine assembles what is actually on file as the
  // auditable pack. The route answers a PDF, so this hands back the link rather than
  // parsing a body.
  async ccVendorEvidence(c, subject) {
    const scoped = !!c.vendorId;
    this.orch("Request evidence from vendor", subject, [
      { a: "Orchestrator", t: "Intent: evidence pack · " + (scoped ? c.holder : "whole portfolio") },
      { a: "Planner", t: "Certificates, verification verdicts and approvals for the scope, rendered as one bundle" },
      { a: "Worker", t: "GET /api/compliance/evidence-pack" + (scoped ? "?vendor_id=…" : "") + " — application/pdf" },
      { a: "Quality", t: "The pack states what is on file and what is missing; nothing is asserted without a record behind it" }
    ]);
    try {
      const query = scoped ? { vendor_id: c.vendorId } : {};
      // The route answers application/pdf, so the bytes come back as a Blob and the
      // pack is handed to the browser. A URL in a status line is not a deliverable.
      const blob = await complianceApi.evidencePackBlob(query);
      const kb = Math.max(1, Math.round(blob.size / 1024));
      const objectUrl = URL.createObjectURL(blob);
      const name = "compliance-evidence-" +
        (scoped ? String(c.holder || "vendor").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") : "portfolio") +
        "-" + new Date().toISOString().slice(0, 10) + ".pdf";

      // Object URLs pin the blob in memory until revoked. Kept so the pack can be opened
      // from the draft rather than forced on the reader as a download.
      this._ccPack = { url: objectUrl, name: name, kb: kb };
      setTimeout(() => URL.revokeObjectURL(objectUrl), 600000);

      // Draft the request and hand it to the shell's own email panel — To is editable,
      // subject and body are pre-written, and nothing is sent until it is approved.
      // No vendor on the record means there is no named addressee — "Dear the
      // responsible vendor" is worse than a neutral salutation.
      const salutation = scoped ? "Dear " + c.holder + "," : "Dear Sir or Madam,";
      const expiry = c.exp && c.exp !== "—" ? c.exp : "the recorded expiry date";
      const lapsed = isFinite(c.days) && c.days < 0;
      const subject = "Evidence request — " + c.nm + (scoped ? " (" + c.holder + ")" : "");
      const body = [
        salutation,
        "",
        "Our compliance register shows the following certificate is " +
          (lapsed ? "lapsed" : "due for renewal") + " and we need current evidence for it:",
        "",
        "  Certificate:  " + c.nm,
        "  Held against: " + c.holder,
        "  Expiry:       " + expiry + (lapsed ? " (" + Math.abs(c.days) + " days ago)" : ""),
        c.number ? "  Reference:    " + c.number : null,
        "",
        "Please reply with the current certificate as a PDF. Until it is on record this " +
          "obligation stays open on the register and regulated work against it remains blocked.",
        "",
        "A compliance evidence pack covering what we currently hold" +
          (scoped ? " for you" : "") + " is attached for reference (" + kb + " KB).",
        "",
        "Regards,",
        "Compliance — Planum Technologies"
      ].filter((l) => l !== null).join("\n");

      this.setState({
        flow: "email",
        flowDone: "",
        emKind: "evidence",
        emCertId: c.id || null,
        emKicker: "Request evidence from vendor",
        emTo: "",
        emSubject: subject,
        emBody: body
      });
    } catch (e) {
      this.setState({ flow: null, flowDone: "The evidence pack could not be built: " + ((e && e.message) || e) });
    }
  },

  // "Acknowledge" on a draft — PM confirmation finalises the record and links the source
  // document to the vendor / site / asset it resolves to.
  async ccConfirmDraft(c, subject) {
    this.orch("Acknowledge", subject, [
      { a: "Orchestrator", t: "Intent: confirm the draft certificate · " + c.nm },
      { a: "Planner", t: "Clear the draft flag and link the source document to every target that resolves" },
      { a: "Worker", t: "POST /api/compliance/certificates/{id}/confirm" },
      { a: "Quality", t: "Confirmation is a deliberate act by a named PM — it is logged as one" }
    ]);
    try {
      const r = await complianceApi.confirmCertificate(c.id, {});
      if (r && r.ok === false) throw new Error(r.error || "not confirmed");
      const linked = (r && (r.linked || r.link_results)) || null;
      this.setState({
        flow: null,
        flowDone: c.nm + " — " + c.holder + " is confirmed and off the draft list." +
          (linked ? " Linked: " + (Array.isArray(linked) ? linked.join(", ") : Object.keys(linked).join(", ")) + "." : "")
      });
      this.ccLoad();
    } catch (e) {
      this.setState({ flow: null, flowDone: "The certificate could not be confirmed: " + ((e && e.message) || e) });
    }
  },

  // The ask bar on the compliance page, run against svc-deepagents instead of the seed
  // answer set. The orchestrator picks its own sub-agent, so the tool calls it actually
  // made are replayed as the chain — the answer and the route it took are both visible.
  //
  // Scope is passed as context so "which buildings put me at risk" means the scope the
  // page is showing, not the whole graph.
  async ccAsk(q) {
    // One turn at a time on one session: a second question mid-answer would race the first
    // on the server. Stop the running one (or edit it) first.
    if (this.state.ccBusy) return this.flash("Still answering — stop it first, or wait for it to finish.");
    const context = this.chatContext();

    if (this.dockAnswers()) {
      // The console, vendors and buildings pages keep their side dock: the data stays in
      // view beside the answer. First question of a conversation opens the dock and titles
      // the session. Follow-ups append to the same transcript instead of restarting it.
      // A dock that is open on a task with a form in progress keeps that task's title too:
      // the question is asked beside the work, not instead of it.
      //
      // No scripted step chain is passed: the real route is reported per reply by the
      // "how this answer was produced" panel, and a narration of what the code was about
      // to do added nothing beside it.
      if (!this.state.orchOpen || (!(this.state.ccChat || []).length && !this.state.flow)) {
        this.orch(q, this.ctxLabel(), [], { record: false });
      }
      this.sessionEnsure(q);
    } else {
      // A space and the chat page: the conversation IS the page. The question makes (or
      // continues) the session record before the view changes, so the record carries the
      // page it was asked from; then the page opens with no dock, and whatever flow the dock
      // was showing is dropped with it.
      this.sessionEnsure(q);
      this.setState({ flow: null, flowDone: "" });
      this.openChat();
    }

    // Capture the staged attachments before the tray is cleared — the setState below
    // both clears it and records the names on the question bubble.
    const files = (this.state.ccFiles || []).slice();

    // The question lands in the transcript immediately; the reply streams in after. The
    // dock's flow panel (a form mid-entry, a draft) is left as it is — a question is not a
    // cancel; Cancel is.
    this.setState((p) => ({
      ccBusy: true,
      ccFiles: [],
      ccChat: (p.ccChat || []).concat([{ role: "you", text: q, files: files.map((f) => f.name) }])
    }));

    const t0 = Date.now();
    // The rail's clock. Stream events are bursty — without this the elapsed reading would
    // sit still through a long tool call and read as a hung run.
    clearInterval(this._ccTick);
    this.setState({ ccTick: 0, ccTraceIdx: null });
    this._ccTick = setInterval(() => this.setState({ ccTick: Math.round((Date.now() - t0) / 1000) }), 1000);
    // A live turn can be cancelled from the chat's stop button.
    const ctrl = (typeof AbortController !== "undefined") ? new AbortController() : null;
    this._ccAbort = ctrl;
    try {
      // run-stateful, NOT run. The compliance preflight that produces the structured
      // answer (compliance_response + compliance_pipeline) lives in
      // orchestrator._stateful_preflight_shortcut, which only the stateful path calls —
      // the stateless run() has no reference to it and returns prose only.
      // The thread id is the session record's id (sessionEnsure above), so the server keeps
      // its state under the same key this browser lists the conversation by.
      const sid = this.state.sessionId;
      // Files still go by POST — the streaming route takes no upload. Everything else
      // streams, so the pipeline steps and the answer's zones paint as they are produced
      // instead of appearing all at once when the turn ends.
      const r = files.length
        ? await deepAgentsApi.runStatefulWithFiles(q, sid, context, files, ctrl && ctrl.signal)
        : await this.ccStreamTurn(q, context, ctrl);

      const answer = (r && r.answer) || "";
      const calls = (r && r.tool_calls) || [];
      if (r && r.success === false) throw new Error(r.error || "the orchestrator returned no answer");
      // A turn can "succeed" while the engine it routed to failed — the answer is then a
      // bare JSON error. Say so, and say where it came from.
      const engineError = errorFromAnswer(answer);
      if (engineError) {
        const via = calls.map((t) => t.tool).filter(Boolean).filter((t, j, a) => a.indexOf(t) === j);
        throw Object.assign(new Error(engineError + (via.length ? " — returned by " + via.join(", ") : "")), { engine: true });
      }

      this.setState((p) => ({
        ccBusy: false,
        ccChat: (p.ccChat || []).concat([{
          role: "bot",
          text: answer || "The orchestrator returned an empty answer.",
          // Named tools behind this reply, so the route is visible per message.
          calls: calls.map((t) => t.tool).filter(Boolean),
          interrupted: !!(r && r.interrupted),
          // Structured payload when the compliance preflight answered; null otherwise,
          // in which case the reply falls back to rendering the markdown answer.
          rich: extractComplianceAnswer(calls) || (r && r._partial ? {
            narrative: r._partial.zones.narrative || "",
            sections: r._partial.zones.sections || [], groups: r._partial.zones.groups || [],
            kpis: r._partial.zones.kpis || [], actions: r._partial.zones.actions || [],
            insights: r._partial.zones.insights || [], certificates: r._partial.zones.certificates || [],
            pending: r._partial.zones.pending || [], offers: r._partial.zones.offers || [],
            validation: null, steps: r._partial.steps || [], cost: null
          } : null),
          // The run's sequence of thoughts, kept per turn so the trace rail can show an
          // older answer's route. Empty on the POST path, which reports no events.
          trace: (r && r._trace) || [],
          ms: Date.now() - t0
        }])
      }));
    } catch (e) {
      const msg = (e && e.message) || String(e);
      if (e && e.cancelled) {
        this.setState((p) => ({
          ccBusy: false,
          ccChat: (p.ccChat || []).concat([{ role: "bot", stopped: true, text: "Stopped before the orchestrator answered." }])
        }));
        return;
      }
      // A dead upstream reaches here either as a gateway 5xx or as a raw connect error,
      // depending on whether the gateway or the browser gave up first.
      const unreachable = (e && (e.status === 502 || e.status === 503 || e.status === 504)) ||
        /ECONNREFUSED|ENOTFOUND|network|failed to fetch|timed out/i.test(msg);
      this.setState((p) => ({
        ccBusy: false,
        ccChat: (p.ccChat || []).concat([{
          role: "bot",
          error: true,
          text: "Could not answer: " + msg +
            (unreachable
              ? " — svc-deepagents is not reachable at /backend/deep-agents. Start that service and give it an LLM key; the compliance register is unaffected."
              : "")
        }])
      }));
    } finally {
      clearInterval(this._ccTick);
      this._ccTick = null;
      if (this._ccAbort === ctrl) this._ccAbort = null;
    }
  },

  // One streamed turn. Resolves with the same payload shape the POST returns, so the
  // caller does not care which transport was used. Partial state is written into the
  // live placeholder message as events arrive; if the socket cannot open at all, this
  // falls back to the plain POST so a failed upgrade never costs the answer.
  ccStreamTurn(q, context, ctrl) {
    return new Promise((resolve, reject) => {
      const sid = this.state.sessionId;
      let opened = false;
      let done = false;
      const partial = { steps: [], zones: {}, reasoning: "", trace: [] };
      const t0 = Date.now();

      // The trace is the run's sequence of thoughts, kept in arrival order so the rail can
      // replay it. Previously reasoning and tool_started were flattened onto one string and
      // tool_completed / agent_switch were dropped, which left nothing to render but the
      // latest line. Each entry keeps its own clock so a slow stage is visible as a slow one.
      const push = (e) => { partial.trace.push(Object.assign({ at: Date.now() - t0 }, e)); };

      // Everything the stream has produced so far, in the shape ComplianceAnswer reads.
      const paint = () => {
        const z = partial.zones;
        this.setState({
          ccStream: {
            steps: partial.steps.slice(),
            reasoning: partial.reasoning,
            trace: partial.trace.slice(),
            rich: {
              narrative: z.narrative || "",
              sections: z.sections || [], groups: z.groups || [], kpis: z.kpis || [],
              actions: z.actions || [], insights: z.insights || [],
              certificates: z.certificates || [], pending: z.pending || [],
              offers: z.offers || [], validation: z.validation || null,
              steps: partial.steps.slice(), cost: null
            }
          }
        });
      };

      const handle = deepAgentsApi.stream(sid, q, context, {
        onOpen: () => { opened = true; },
        onEvent: (d) => {
          if (d.type === "compliance_step" && d.step) {
            partial.steps.push(d.step);
            push({ kind: "step", step: d.step });
            paint();
            return;
          }
          if (d.type === "compliance_zone" && d.zone) { partial.zones[d.zone] = d.data; paint(); return; }
          if (d.type === "reasoning") {
            partial.reasoning = (d.label ? d.label + " — " : "") + (d.text || "");
            push({ kind: "reason", label: d.label || "Thinking", text: d.text || "", domain: d.domain || "" });
            paint();
            return;
          }
          if (d.type === "tool_started" && d.tool) {
            partial.reasoning = "Running " + d.tool + "…";
            push({ kind: "tool", tool: d.tool, domain: d.domain || "", running: true });
            paint();
            return;
          }
          if (d.type === "tool_completed" && d.tool) {
            // Close the newest open call for this tool rather than appending a second row,
            // so a tool that runs twice still reads as two calls and not four.
            for (let i = partial.trace.length - 1; i >= 0; i -= 1) {
              const e = partial.trace[i];
              if (e.kind === "tool" && e.tool === d.tool && e.running) { e.running = false; e.ranMs = (Date.now() - t0) - e.at; break; }
            }
            paint();
            return;
          }
          if (d.type === "agent_switch" && d.to_domain) {
            push({ kind: "switch", from: d.from_domain || "", to: d.to_domain });
            paint();
            return;
          }
        },
        onDone: (d) => {
          done = true;
          handle.close();
          this.setState({ ccStream: null });
          // Carry the trace out with the answer so the finished turn keeps its own run
          // record — the rail can then replay any past turn, not just the live one.
          resolve(Object.assign({}, d, { _trace: partial.trace.slice() }));
        },
        onError: (e) => {
          if (done) return;
          done = true;
          this.setState({ ccStream: null });
          // Never opened → the upgrade was refused somewhere in the chain. Take the
          // non-streaming path rather than failing the question.
          if (!opened) {
            deepAgentsApi.runStateful(q, sid, context, ctrl && ctrl.signal).then(resolve, reject);
            return;
          }
          reject(e);
        },
        onClose: () => {
          if (done) return;
          done = true;
          this.setState({ ccStream: null });
          if (!opened) {
            deepAgentsApi.runStateful(q, sid, context, ctrl && ctrl.signal).then(resolve, reject);
            return;
          }
          // Closed after opening but before completing — treat what arrived as the answer.
          resolve({ session_id: sid, answer: "", tool_calls: [], success: true, _partial: partial, _trace: partial.trace.slice() });
        }
      });

      // The stop button closes the socket.
      if (ctrl && ctrl.signal) {
        ctrl.signal.addEventListener("abort", () => {
          if (done) return;
          done = true;
          handle.close();
          this.setState({ ccStream: null });
          const err = new Error("cancelled");
          err.cancelled = true;
          reject(err);
        });
      }
    });
  },

  // The action buttons the analysis offers against a certificate. `kind` is the
  // instruction, so the mapping is a lookup rather than matching on the label text.
  //
  // Results land in the transcript as a note. These deliberately do NOT call orch() —
  // that would retitle the dock and replace the conversation with a step chain, the same
  // way the compliance scan used to.
  //
  // NOTE: verify_now and confirm_draft WRITE to the register:
  //   POST /certificates/{id}/verify   records a verification verdict
  //   POST /certificates/{id}/confirm  finalises a draft and links its document
  // renewal_email only drafts — nothing is sent until the email panel is approved.
  async ccRunOffer(offer) {
    if (!offer || !offer.cert_id || !offer.kind) return;
    const key = offer.cert_id + ":" + offer.kind;
    if ((this.state.ccOfferBusy || {})[key]) return;

    const reg = this.ccData();
    const cert = ((reg && reg.certs) || []).find((c) => c.id === offer.cert_id) || null;
    const name = (cert && cert.nm) || offer.cert_name || "this certificate";
    const holder = (cert && cert.holder) || offer.owner || "";
    const who = holder ? name + " — " + holder : name;

    const mark = (on) => this.setState((p) => {
      const b = Object.assign({}, p.ccOfferBusy || {});
      if (on) b[key] = true; else delete b[key];
      return { ccOfferBusy: b };
    });
    const note = (text, isError) => {
      mark(false);
      this.setState((p) => ({ ccChat: (p.ccChat || []).concat([{ role: "bot", note: true, error: !!isError, text: text }]) }));
    };

    mark(true);
    try {
      if (offer.kind === "renewal_email") {
        const r = await complianceApi.draftRenewalEmail(offer.cert_id);
        if (r && r.ok === false) throw new Error(r.error || "not drafted");
        const d = (r && r.email_draft) || {};
        // Hand the real draft to the shell's email panel so the address is editable and
        // nothing leaves the platform until it is approved.
        this.setState({
          flow: "email",
          emKind: "renewal",
          emCertId: offer.cert_id,
          emKicker: "Draft renewal email",
          emTo: d.to || "",
          emSubject: d.subject || ("Renewal required — " + name),
          emBody: d.body || ""
        });
        note("Renewal email drafted for " + who + ". It is open above — check the address, then approve to queue it.");
        return;
      }

      if (offer.kind === "verify_now") {
        const r = await complianceApi.verifyCertificate(offer.cert_id);
        const st = (r && (r.status || r.verification_status || (r.verification || {}).status)) || (r && r.ok ? "checked" : "unknown");
        const ch = r && (r.channel || (r.verification || {}).channel);
        const url = r && (r.verify_url || r.verification_url || (r.verification || {}).verify_url);
        note("Verification for " + who + " returned “" + String(st).replace(/_/g, " ") + "”" +
          (ch ? " via " + String(ch).replace(/_/g, " ") : "") + "." + (url ? " Register: " + url : ""));
        this.ccLoad();
        return;
      }

      if (offer.kind === "confirm_draft") {
        const r = await complianceApi.confirmCertificate(offer.cert_id, {});
        if (r && r.ok === false) throw new Error(r.error || "not confirmed");
        const linked = (r && (r.linked || r.link_results)) || null;
        note(who + " is confirmed and off the draft list." +
          (linked ? " Linked: " + (Array.isArray(linked) ? linked.join(", ") : Object.keys(linked).join(", ")) + "." : ""));
        this.ccLoad();
        return;
      }

      note("No action is wired for “" + (offer.label || offer.kind) + "”.", true);
    } catch (e) {
      note("Could not complete “" + (offer.label || offer.kind) + "” for " + who + ": " + ((e && e.message) || e), true);
    }
  },

  // Inline question editing, in place in the transcript: the bubble becomes a box with
  // the text, a Cancel and a Run again. Re-running drops the old answer.
  ccEditStart(i) {
    const m = (this.state.ccChat || [])[i];
    if (!m || m.role !== "you") return;
    if (this._ccAbort) this._ccAbort.abort();
    this.setState({ ccEditIdx: i, ccEditText: m.text });
  },
  ccEditSet(v) { this.setState({ ccEditText: v }); },
  ccEditCancel() { this.setState({ ccEditIdx: null, ccEditText: "" }); },
  ccEditRun() {
    const i = this.state.ccEditIdx;
    const q = String(this.state.ccEditText || "").trim();
    if (i === null || i === undefined || !q) return;
    this.setState((p) => ({ ccChat: (p.ccChat || []).slice(0, i), ccEditIdx: null, ccEditText: "" }));
    // Let the truncation land before the new turn appends to the transcript.
    setTimeout(() => this.ccAsk(q), 0);
  },

  // Open the chat without asking anything — pressing Ask on an empty bar should get you
  // into the conversation, not a toast telling you off. On the console that is the dock;
  // everywhere else it is the chat page.
  ccOpenChat() {
    if (this.dockAnswers()) this.setState({ orchOpen: true });
    else this.openChat();
    if (typeof document === "undefined") return;
    // Focus whichever composer has mounted.
    setTimeout(() => {
      const el = document.getElementById("chat-composer") || document.getElementById("orch-composer");
      if (el) el.focus();
    }, 80);
  },

  // Composer submit, shared by the send button and the Enter key. While a turn is running
  // the draft stays in the box: the button is Stop, and Enter says so.
  orchSubmitNow() {
    const q = String(this.state.orchQuery || "").trim();
    if (!q) return;
    if (this.state.ccBusy) return this.flash("Still answering — stop it first, or wait for it to finish.");
    this.setState({ orchQuery: "" });
    this.askScoped(q);
  },

  // Stop button — abandons the turn in flight. The orchestrator keeps working server
  // side; this only stops the console waiting for it.
  ccStop() {
    if (this._ccAbort) this._ccAbort.abort();
  },

  // Edit a question already asked: it and everything after it leave the transcript and
  // the text goes back into the composer, so the thread can be taken a different way.
  ccEditQuestion(i) {
    // Editing mid-flight means abandoning the answer being written for the old wording.
    if (this._ccAbort) this._ccAbort.abort();
    this.setState((p) => {
      const chat = (p.ccChat || []);
      const m = chat[i];
      if (!m || m.role !== "you") return {};
      return { ccChat: chat.slice(0, i), orchQuery: m.text, ccFiles: [] };
    });
  },

  // Documents and photos ride along with the next question. CSV/Excel go to the
  // migration flow, PDF/Word/images to doc-rag indexing — the backend routes by type.
  ccAddFiles(fileList) {
    const add = Array.from(fileList || []);
    if (!add.length) return;
    this.setState((p) => ({ ccFiles: (p.ccFiles || []).concat(add).slice(0, 10) }));
  },
  ccDropFile(idx) {
    this.setState((p) => ({ ccFiles: (p.ccFiles || []).filter((f, j) => j !== idx) }));
  },

  // Clears the transcript without closing the dock, and drops the orchestrator session so
  // the next question starts a fresh thread rather than inheriting the old one.
  ccChatReset() {
    if (this._ccAbort) this._ccAbort.abort();
    this.setState({ sessionId: null, ccChat: [], ccBusy: false, ccStream: null, ccTraceIdx: null, ccStepsOpen: {}, flowDone: "" });
  }
};
