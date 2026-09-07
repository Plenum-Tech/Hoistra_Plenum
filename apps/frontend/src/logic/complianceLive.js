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
    return {
      id: r.id, code: r.certificate_type_code, number: r.certificate_number || null,
      nm: r.certificate_type_name || nameOf(r.certificate_type_code),
      holder: holder, kind: kind, cc: cc, state: r.state || r.region || null,
      building: building, vendor: vendor, vendorId: r.vendor_id || null,
      exp: fmtDate(r.expiry_date), days: days, risk: rk.risk, sev: rk.sev,
      auth: a.auth, authSev: a.authSev, ver: verOf(r), pos: runwayPos(d0),
      status: r.status || null, trade: r.trade_category || null,
      verificationUrl: r.verification_url || null, draft: !!r.draft
    };
  });

  // Buildings: coverage rows first (they carry required/on-record), then anything a
  // certificate names that coverage did not (a building in a country whose pack is not seeded).
  const bMap = {};
  const ensureB = (name, cc) => {
    if (!bMap[name]) bMap[name] = { name: name, cc: cc, state: null, use: "—", cov: 0, on: 0, req: 0, certs: 0, high: 0, med: 0, cur: 0, blocked: 0, gaps: [], gapCodes: [], linked: false, siteId: null, _n: 0, _codes: {} };
    return bMap[name];
  };
  Object.keys(coverage).forEach((cc) => {
    ((coverage[cc] && coverage[cc].buildings) || []).forEach((b) => {
      const x = ensureB(b.site_name || "No building on certificate", cc);
      x.cov = Math.round(Number(b.coverage_pct || 0)); x.on = b.on_record || 0; x.req = b.required || 0;
      x.certs = b.certificates_total || 0; x.linked = !!b.linked; x.siteId = b.site_id || b.site_ref || null;
      x.gapCodes = b.gaps || []; x.gaps = (b.gaps || []).map(nameOf);
      x.use = b.linked ? "Site on record" : "Named on certificate only";
    });
  });
  rows.filter((c) => c.kind === "building").forEach((c) => {
    const x = ensureB(c.holder, c.cc);
    if (!x.state && c.state) x.state = c.state;
    x._n += 1; x._codes[c.code] = true;
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
    if (!vMap[name]) vMap[name] = { name: name, cc: cc, id: null, cov: 0, on: 0, req: 0, block: "Clear", worst: "OK", sev: "ok", serves: [], gaps: [], certs: 0, spec: "—", _n: 0, _worst: Infinity };
    return vMap[name];
  };
  Object.keys(coverage).forEach((cc) => {
    ((coverage[cc] && coverage[cc].vendors) || []).forEach((v) => {
      const x = ensureV(v.vendor_name || v.vendor_id, cc);
      x.id = v.vendor_id || null; x.cov = Math.round(Number(v.coverage_pct || 0)); x.on = v.on_record || 0; x.req = v.required || 0;
      x.certs = v.certificates_total || 0; x.gaps = (v.gaps || []).map(nameOf);
      if (/blocked/i.test(String(v.block_state || ""))) x.block = "Blocked";
      if (v.blocked_accreditation_type) x.blockedType = nameOf(v.blocked_accreditation_type);
    });
  });
  rows.filter((c) => c.kind === "vendor").forEach((c) => {
    const x = ensureV(c.holder, c.cc);
    if (!x.id && c.vendorId) x.id = c.vendorId;
    x._n += 1;
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
    states: Object.keys(stMap).map((k) => stMap[k]),
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

  // "Run compliance scan" - the real A1 scan, then the register is re-read.
  async ccRunScan() {
    this.orch("Run compliance scan", "Compliance", [
      { a: "Orchestrator", t: "Intent: compliance scan · scope: whole register" },
      { a: "Planner", t: "POST /api/compliance/scan — Building (A2) and Vendor (A3) workers in parallel, adversary check after" },
      { a: "Worker", t: "svc-operations-intelligence recomputes status, alert ladder and vendor block state for every certificate" },
      { a: "Quality", t: "The register above is re-read when the scan returns" }
    ]);
    const t0 = Date.now(); const turn = newTurn();
    deepAgentsApi.logActivity({ turn_id: turn, stage: "action", direction: "input", summary: "Run compliance scan", payload: { action: "scan", request: { scope: "all" } } });
    try {
      const res = await complianceApi.runScan({});
      deepAgentsApi.logActivity({ turn_id: turn, stage: "action", direction: "output", summary: "Compliance scan returned", latency_ms: Date.now() - t0, payload: { action: "scan", response: res } });
      await this.ccLoad();
      const b = res && res.building_scanned, v = res && res.vendor_scanned;
      const n = (typeof b === "number" || typeof v === "number") ? (b || 0) + (v || 0) : null;
      const alerts = res && res.alerts_created;
      const blocks = res && res.blocks_set;
      this.setState({
        flow: null, ccLastScan: new Date().toISOString(),
        flowDone: "Scan complete" + (n !== null ? " — " + n + " certificates evaluated (" + (b || 0) + " building, " + (v || 0) + " vendor)" : "") +
          (typeof alerts === "number" ? ", " + alerts + " alerts raised" : "") +
          (typeof blocks === "number" && blocks ? ", " + blocks + " vendor blocks set" : "") + ". The register has been re-read."
      });
    } catch (e) {
      deepAgentsApi.logActivity({ turn_id: turn, stage: "action", direction: "error", summary: "Compliance scan failed", ok: false, error: String((e && e.message) || e), latency_ms: Date.now() - t0, payload: { action: "scan" } });
      this.setState({ flow: null, flowDone: "The scan could not be run: " + ((e && e.message) || e) + ". Showing " + (this.ccIsLive() ? "the last loaded register" : "seed data") + "." });
    }
  },

  // Actions for a certificate row. Live records add the two the backend can do now.
  ccCertActions(c) {
    const base = this.certActions(c);
    if (!c.id) return base;
    return base.concat(["Verify register", "Draft renewal email"]).filter((l, i, a) => a.indexOf(l) === i);
  },

  ccRunCertAction(label, c, subject, vendorName) {
    const low = label.toLowerCase();
    if (c.id && /^verify register/.test(low)) return this.ccVerify(c, subject);
    if (c.id && /^draft renewal email/.test(low)) return this.ccRenewal(c, subject);
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
  }
};
