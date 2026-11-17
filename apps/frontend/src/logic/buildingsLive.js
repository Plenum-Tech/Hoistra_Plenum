// buildingsLive — the Buildings table read from svc-operations-intelligence.
//
// GET /api/energy/buildings returns one row per site with its energy profile, latest EUI,
// the benchmark it reads against and two provenance facts the table must show:
//   under EUI        — how the reading arrives and at what granularity (building-level
//                      metering is amber: attribution to a plant item there is inferred)
//   under Benchmark  — the standard for the country's regulation pack and its legal
//                      standing (enacted · guidance · mandatory submission · none), so a
//                      proposal is never read as a duty.
// The table is DB-only: every row is a plenum_cafm.sites row (keyed on site_id VARCHAR(50))
// read through the endpoint. There is no seed fallback — when the backend is unreachable
// the table is empty and the page says why.
//
// Methods are mixed into HoistraLogic.prototype; `this` is the controller.
import { PACKS, USE_TINT } from './constants.js';
import { energyApi } from '../api/energy.js';
import { normCountry, countryMeta, fmtTime } from './complianceLive.js';
import { isStaleScope } from '../api/client.js';
import { isUnallocated } from './auth.js';

const RETRY_MS = 30000;
// Opening Buildings or Home re-reads a register older than this (bldIfStale).
const BLD_STALE_MS = 60000;
const RETRY_MAX = 6;
export const PAGE_SIZE = 20;

const STANDING_LABEL = {
  enacted: "enacted", guidance: "guidance", mandatory_submission: "submission mandatory", none: "no operational standard"
};
// Tone of the standing line: enacted/mandatory read as a duty (ok), guidance as a warning,
// no standard as dormant. Same palette PACKS uses for the seed.
const STANDING_TONE = { enacted: "ok", mandatory_submission: "ok", guidance: "warn", none: "dormant" };

// How a figure was arrived at, in one word, shown under the number.
//
// A tooltip is not enough for this. Whether an EUI was metered or typed onto a row by hand
// is the difference between a measurement and somebody's estimate, and a table that renders
// both as plain "212 kWh/m²" has already told the reader they are the same thing. You should
// not have to hover to find out which one you are looking at.
const SOURCE_WORD = {
  // area
  spaces_sum: "counted",
  sites: "surveyed",
  buildings_recorded: "surveyed",
  energy_profile: "profile",
  // floors
  floors_table: "counted",
  // eui
  eui_snapshot: "metered",
  sites_recorded: "recorded",
  // benchmark
  tm46: "TM46",
  tm46_combined_by_use: "TM46",
  tm46_by_site_type: "TM46 default",
  portfolio_rolling: "portfolio median"
};

// "recorded" and "surveyed" are somebody's number. They are not wrong, but they are not
// measured either, and the difference is worth a colour.
const SOFT_SOURCES = new Set(["recorded", "surveyed", "profile", "TM46 default"]);

// The same key means different things in different columns. `buildings_recorded` on a floor
// area is a survey somebody carried out; on an EUI it is a number typed onto the row. Calling
// the second one "surveyed" would dress up an estimate as a measurement.
const EUI_SOURCE_WORD = {
  eui_snapshot: "metered",
  buildings_recorded: "recorded",
  sites_recorded: "recorded",
  energy_profile: "profile"
};

const sourceWord = (key) => (key ? SOURCE_WORD[key] || String(key).replace(/_/g, " ") : "");
const euiSourceWord = (key) => (key ? EUI_SOURCE_WORD[key] || sourceWord(key) : "");
const sourceTone = (key) => {
  const w = sourceWord(key);
  return !w ? "var(--color-neutral-500)" : SOFT_SOURCES.has(w) ? "var(--st-warn)" : "var(--color-neutral-500)";
};

// Which table the rows actually came from. The endpoint reports it; before this the
// screen asserted "sites" whichever it was.
const ROOT_TABLE = (state) => ((state.bldMeta || {}).root === "buildings" ? "buildings" : "sites");
const ROOT_NOUN = (state, n) => (ROOT_TABLE(state) === "buildings"
  ? (n === 1 ? "building" : "buildings")
  : (n === 1 ? "site" : "sites"));

const titleCase = (s) => String(s || "").replace(/[_-]+/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
const fmtInt = (n) => Math.round(n).toLocaleString("en-GB");

function countryOf(row) {
  const cc = row.country_code ? normCountry(row.country_code) : (row.country ? normCountry(row.country) : "—");
  return cc;
}

// Pure: API row → the seed's building shape (plus live-only provenance fields).
export function shapeLiveBuilding(r, i) {
  const cc = countryOf(r);
  const use = r.use_type ? titleCase(r.use_type) : r.site_type ? titleCase(r.site_type) : (r.building_type ? titleCase(r.building_type) : "—");
  const gran = r.metering_granularity || "none";
  // Floor-area split from sites.use_mix; a single recorded use is a 100% bar.
  const mix = Array.isArray(r.use_mix) && r.use_mix.length
    ? r.use_mix.map((m) => [titleCase(m.use), Math.round(Number(m.pct) || 0)])
    : [[use, 100]];
  return {
    live: true,
    key: r.building_id || r.site_id || r.site_key || r.site_uuid || String(i),
    // Building ID is buildings.building_id when the graph is the root, else sites.site_id.
    id: r.building_id || r.site_id || r.code || r.site_uuid || String(i + 1),
    // The delete and the edit key on the building's own uuid. `id` above falls back to a
    // site_id for older rows, and addressing by that would hit the wrong thing or nothing.
    buildingId: r.building_id || null,
    // The energy and compliance tables key on the site's UUID where the CMMS tables key on
    // site_id; the Hoist Graph counts rows against both.
    uuid: r.site_uuid || null,
    city: r.city || null,
    // Raw values the edit form prefills from. `site_type` is the primary_use enum verbatim;
    // `use_type` above is a resolved TM46-style category ("office"), not the enum member, so
    // editing off it would silently change the building's use on save.
    postcode: r.postcode || null,
    siteId: r.site_id || null,
    siteTypeRaw: r.site_type || null,
    useMixRaw: Array.isArray(r.use_mix) ? r.use_mix : null,
    code: r.code || null,
    name: r.name || "Unnamed site",
    cc: cc,
    state: r.region || r.city || "—",
    use: use,
    floors: typeof r.floors === "number" ? r.floors : null,
    areaM2: typeof r.gfa_sqm === "number" ? r.gfa_sqm : null,
    area: typeof r.gfa_sqm === "number" ? fmtInt(r.gfa_sqm) + " m²" : "—",
    areaSource: r.gfa_source || null,
    euiN: typeof r.eui_kwh_per_m2 === "number" ? r.eui_kwh_per_m2 : null,
    euiPeriod: r.eui_period_end ? (r.eui_period_start || "") + " → " + r.eui_period_end + (r.eui_meter_type ? " · " + r.eui_meter_type : "") : "",
    benchN: typeof r.benchmark_kwh_per_m2 === "number" ? r.benchmark_kwh_per_m2 : null,
    // The rate the benchmark pass priced this building's gap at, from its own meters. Used
    // in place of the market constant so the card and the stored snapshot cost the same kWh
    // the same way; null falls back to the country pack.
    tariffN: typeof r.tariff_gbp_per_kwh === "number" ? r.tariff_gbp_per_kwh : null,
    benchSource: r.benchmark_source || null,
    benchComparables: r.benchmark_comparables || null,
    deviation: typeof r.deviation_pct === "number" ? r.deviation_pct : null,
    std: r.benchmark_standard || (PACKS[cc] ? PACKS[cc].std : "—"),
    stdStanding: r.benchmark_standing || null,
    stdNote: r.benchmark_standing_note || (PACKS[cc] ? PACKS[cc].note : "no regulation pack"),
    stdTone: STANDING_TONE[r.benchmark_standing] || (PACKS[cc] ? PACKS[cc].tone : "dormant"),
    // Hoist Score as recorded on the site; record completeness stays available for the tooltip.
    hoist: typeof r.hoist_score === "number" ? r.hoist_score : null,
    completeness: typeof r.record_completeness_pct === "number" ? r.record_completeness_pct : null,
    missing: r.completeness_missing || [],
    euiSource: r.eui_source || null,
    meteringSource: r.metering_source || null,
    // Where the graph counted each figure, so the cell can say so rather than just show it.
    floorsSource: r.floors_source || null,
    useMixSource: r.use_mix_source || null,
    spaces: typeof r.spaces === "number" ? r.spaces : null,
    counts: r.graph_counts || {},
    buildingsOnSite: typeof r.buildings_on_site === "number" ? r.buildings_on_site : 1,
    // Non-empty means the graph holds only PART of this building — three spaces recorded on
    // a twenty-four storey tower. The most useful prompt there is for deciding what to
    // ingest next, so it is carried to the row rather than left in the payload.
    partial: Array.isArray(r.partial_counts) ? r.partial_counts : [],
    gfaCounted: typeof r.gfa_counted_sqm === "number" ? r.gfa_counted_sqm : null,
    updatedAt: r.updated_at || null,
    mix: mix,
    route: r.metering_route || (gran === "none" ? "No meter on record" : "Meter on record · route not stated"),
    gran: gran === "sub-metered" ? "sub-metered" : gran === "none" ? "none" : "building-level",
    metersActive: r.meters_active || 0,
    metersSimulated: !!r.meters_simulated,
    // Not just WHETHER the feed is simulated but which part of the history is: this
    // building's meters carry real reads to 31 August and a simulated feed after it, and a
    // reader told only "simulated" discounts five real months.
    simulated: r.simulated || { any: !!r.meters_simulated }
  };
}

export function shapeLiveBuildings(payload) {
  const rows = (payload && payload.buildings) || [];
  return rows.map(shapeLiveBuilding).sort((a, b) => a.name.localeCompare(b.name));
}

// ── Search ──────────────────────────────────────────────────────────────────────────────
//
// The search bar reads a query the way a person types it, not as one literal substring:
//   · every word has to match SOME field of the row, in any order ("london commercial")
//   · the words the table shows count, not only the stored codes — the Country column says
//     "United Kingdom" while the row holds "UK", so both (and "GB", "Britain") find it
//   · building IDs and postcodes match with or without their dash or space ("b012", "ec2m4aa")
//   · use types match their everyday synonyms ("offices" → Commercial, "flats" → Residential)
//   · filler words ("buildings in the uae") are optional: they rank a row up if they match
//     and never knock one out if they don't
//   · a typo ("manchster") is forgiven, but ONLY when nothing matches exactly, so a query that
//     does hit a row never also drags in look-alikes ("mill" never brings "Hill House")
// Results are ranked: an exact name or ID first, then word starts, then mid-word hits, with
// the table's own order breaking ties. A blank query returns the rows untouched.

// Everyday names for each regulation-pack country, keyed on normCountry's code.
const COUNTRY_WORDS = {
  UK: "uk gb gbr united kingdom great britain britain british",
  AE: "ae uae are united arab emirates emirates emirati",
  US: "us usa united states america american",
  SG: "sg sgp singapore singaporean"
};
// What people call a building's use when the register spells it differently.
const USE_WORDS = {
  commercial: "office offices business workplace",
  office: "commercial offices business workplace",
  residential: "housing homes home apartments apartment flats flat dwellings",
  retail: "shop shops store stores shopping mall",
  industrial: "warehouse warehouses factory logistics",
  warehouse: "industrial logistics storage",
  mixed: "mixed-use multi-use multiuse",
  education: "school schools university college campus",
  healthcare: "hospital clinic health medical",
  hotel: "hospitality accommodation",
  hospitality: "hotel accommodation",
  leisure: "gym sports",
  public: "civic government council"
};
// Words that carry no meaning about WHICH building — optional, never required.
const FILLER = new Set(["in", "the", "at", "of", "and", "a", "an", "on", "for", "with", "near",
  "from", "to", "building", "buildings", "site", "sites", "located", "show", "me", "all", "find",
  "any", "which", "what", "are", "is", "our", "my", "list", "that", "those", "these"]);

// Units a typed number can carry; the figure fields below spell them.
const UNIT_WORDS = new Set(["floor", "floors", "storey", "storeys", "story", "stories", "m2", "sqm", "kwh", "eui"]);

// Field weights: what the row IS (ID, name) outranks where it is, which outranks how it is
// metered.
const W_ID = 5, W_NAME = 5, W_PLACE = 3, W_COUNTRY = 2, W_USE = 2, W_ALIAS = 1.5, W_OTHER = 1;
// Match strength within one field.
const M_EXACT = 10, M_WORD = 6, M_COMPACT = 5, M_SUB = 3, M_FUZZY = 1;

// NFKD, not NFD: it also folds compatibility forms, so "m²" reads "m2" (17 Nov 2026).
const fold = (v) => String(v == null ? "" : v).normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase().trim();
const compact = (v) => v.replace(/[^a-z0-9]+/g, "");

// One row's searchable text, built once per row object (rows are stable between renders).
const SEARCH_INDEX = new WeakMap();
function searchIndex(b) {
  let idx = SEARCH_INDEX.get(b);
  if (idx) return idx;
  const uses = [b.use].concat((b.mix || []).map((m) => m && m[0])).filter((u) => u && u !== "—");
  const useAlias = uses.map((u) => USE_WORDS[fold(u)] || "").join(" ");
  const raw = [
    [b.id, W_ID], [b.code, W_ID], [b.siteId, W_ID],
    [b.name, W_NAME],
    [b.postcode, W_PLACE], [b.city, W_PLACE], [b.state !== "—" ? b.state : "", W_PLACE],
    [b.cc, W_COUNTRY], [countryMeta(b.cc).name, W_COUNTRY], [COUNTRY_WORDS[b.cc], W_ALIAS],
    [uses.join(" "), W_USE], [useAlias, W_ALIAS],
    [b.std !== "—" ? b.std : "", W_OTHER], [b.route, W_OTHER], [b.gran, W_OTHER],
    // The figures the table shows, as whole numbers a person might type: "22" for a 22-storey
    // building, "49400" for its floor area, "215" for its EUI. Matched whole only (see `num`).
    // Floors match whole only ("2" is never 22 floors); an area or EUI also matches from its
    // first digits, so "49" finds 49,400 m² while it is still being typed.
    [typeof b.floors === "number" ? b.floors + " floors storeys storey story stories" : "", W_PLACE, "whole"],
    [typeof b.areaM2 === "number" ? Math.round(b.areaM2) + " m2 sqm" : "", W_PLACE, "prefix"],
    [typeof b.euiN === "number" ? Math.round(b.euiN) + " kwh eui" : "", W_OTHER, "prefix"]
  ];
  idx = raw.map(([v, w, num]) => {
    const text = fold(v);
    // An opaque key (a UUID) is matched only from its very start: its inner parts ("2271",
    // "4c22") are random, not words anyone types.
    const opaque = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(text);
    return { text, w, num: !!num, numPrefix: num === "prefix", opaque, compact: compact(text), words: text.split(/[^a-z0-9]+/).filter(Boolean) };
  }).filter((f) => f.text);
  SEARCH_INDEX.set(b, idx);
  return idx;
}

// Does `t` start a word inside `text` (at 0, or right after a non-alphanumeric)?
function startsWord(text, t) {
  for (let i = text.indexOf(t); i > -1; i = text.indexOf(t, i + 1)) {
    if (i === 0 || !/[a-z0-9]/.test(text[i - 1])) return true;
  }
  return false;
}

// Optimal-string-alignment distance, giving up once it exceeds `max`.
function editDistance(a, b, max) {
  if (Math.abs(a.length - b.length) > max) return max + 1;
  let prev2 = null, prev = Array.from({ length: b.length + 1 }, (_, j) => j);
  for (let i = 1; i <= a.length; i++) {
    const cur = [i];
    let rowMin = i;
    for (let j = 1; j <= b.length; j++) {
      const cost = a[i - 1] === b[j - 1] ? 0 : 1;
      let d = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost);
      if (prev2 && i > 1 && j > 1 && a[i - 1] === b[j - 2] && a[i - 2] === b[j - 1]) d = Math.min(d, prev2[j - 2] + 1);
      cur.push(d);
      if (d < rowMin) rowMin = d;
    }
    if (rowMin > max) return max + 1;
    prev2 = prev; prev = cur;
  }
  return prev[b.length];
}

// A typed number against a figure field: the whole figure, or (area, EUI) its first digits.
function numMatch(f, tc) {
  const n = f.words[0];
  if (n === tc) return M_EXACT;
  return f.numPrefix && tc.length >= 2 && n.indexOf(tc) === 0 ? M_SUB : 0;
}

// How strongly one query word matches one field (0 = not at all).
function matchField(f, t, tc, fuzzy, unit) {
  const digits = /^\d+$/.test(tc);
  // "22 floors": the number and its unit read as one figure, so it matches only the floor count
  // that is 22 — not an ID starting with 22 on a building that has some other number of floors.
  if (f.opaque) return f.compact === tc ? M_EXACT : tc.length >= 2 && f.compact.indexOf(tc) === 0 ? M_WORD : 0;
  if (unit) return f.num && f.words.some((w) => w.indexOf(unit) === 0) ? numMatch(f, tc) : 0;
  // A figure field: a number matches a whole number on it ("22" is not "220" floors) or, on an
  // area or EUI, its first digits; its unit words ("floors", "m2", "eui") match as words.
  if (f.num) {
    if (digits) return numMatch(f, tc);
    return !/\d/.test(tc) && f.words.some((w) => w.indexOf(tc) === 0) ? M_WORD : 0;
  }
  if (f.text === t || (tc && f.compact === tc && /\d/.test(tc))) return M_EXACT;
  // A bare number matches only at a word start: "301" finds B-301, but "22" never finds a UUID
  // with "22" somewhere in its middle (17 Nov 2026). A whole ID part outranks its start, so a
  // 22-storey building ranks above B-2201.
  if (digits) return f.words.indexOf(tc) > -1 ? M_WORD : startsWord(f.text, t) ? M_SUB : 0;
  if (startsWord(f.text, t)) return M_WORD;
  // IDs and postcodes: compare without dashes and spaces, so "b012" finds "B-012".
  if (tc.length >= 2 && /\d/.test(tc) && f.compact.indexOf(tc) > -1) return M_COMPACT;
  // One or two letters only match at a word start ("uk", "b"), never mid-word noise.
  if (t.length >= 3 && f.text.indexOf(t) > -1) return M_SUB;
  if (fuzzy && tc.length >= 4 && !/\d/.test(tc)) {
    const max = tc.length >= 8 ? 2 : 1;
    for (const w of f.words) {
      if (editDistance(tc, w, max) <= max) return M_FUZZY;
      // Still typing: a misspelt START of a longer word ("bishpsg" → "bishopsgate").
      if (tc.length >= 5 && w.length > tc.length && editDistance(tc, w.slice(0, tc.length), max) <= max) return M_FUZZY;
    }
  }
  return 0;
}

function scoreRow(b, terms, phrase, fuzzy) {
  const idx = searchIndex(b);
  let total = 0;
  for (const { t, tc, optional, unit } of terms) {
    let best = 0;
    for (const f of idx) {
      const m = matchField(f, t, tc, fuzzy, unit);
      if (m && m * f.w > best) best = m * f.w;
    }
    if (!best && !optional) return 0;
    total += best;
  }
  // The whole query read as one phrase inside the name ("bishopsgate tower") ranks highest.
  if (phrase && fold(b.name).indexOf(phrase) > -1) total += 20;
  return total;
}

// Pure: the search bar's match and ranking (see the block above). A blank query matches
// everything, so the table's own row order is untouched.
export function filterBuildings(rows, query) {
  // "10,000" and "10 000" are one number, as the table prints it, not two (17 Nov 2026). A comma
  // between digits always joins — "49,4" is 49,400 still being typed; a space only before a
  // group of three, so "22 100" typed as two numbers is not forced into one by accident.
  const phrase = fold(query).replace(/(\d),(?=\d)/g, "$1")
    .replace(/(\d)[\u00a0\u202f ](?=\d{3}(?!\d))/g, "$1").replace(/\s+/g, " ");
  const words = phrase.split(/[\s,;/|+]+/).map((t) => t.replace(/^[^a-z0-9]+|[^a-z0-9]+$/g, "")).filter(Boolean);
  if (!words.length) return rows;
  // Filler is optional only alongside a real word; a query of nothing but filler
  // ("tower" is not filler, "building" is) still has to match something.
  const allFiller = words.every((t) => FILLER.has(t));
  const terms = [];
  for (let i = 0; i < words.length; i += 1) {
    const t = words[i];
    // A unit typed straight onto its number ("10000m2") is the same figure as "10000 m2".
    const glued = /^(\d+)([a-z]+2?)$/.exec(t);
    if (glued && UNIT_WORDS.has(glued[2])) {
      terms.push({ t: glued[1], tc: glued[1], optional: false, unit: glued[2] });
      continue;
    }
    // A number followed by its unit is one figure ("22 floors", "49400 m2", "215 kwh").
    if (/^\d+$/.test(t) && UNIT_WORDS.has(words[i + 1])) {
      terms.push({ t, tc: t, optional: false, unit: words[i + 1] });
      i += 1;
      continue;
    }
    terms.push({ t, tc: compact(t), optional: !allFiller && FILLER.has(t) });
  }
  const multi = words.length > 1 ? phrase : "";
  const rank = (fuzzy) => rows
    .map((b, i) => ({ b, i, s: scoreRow(b, terms, multi, fuzzy) }))
    .filter((r) => r.s > 0)
    .sort((x, y) => y.s - x.s || x.i - y.i)
    .map((r) => r.b);
  const exact = rank(false);
  return exact.length ? exact : rank(true);
}

export const buildingsLiveMethods = {
  // DB rows only — never the seed.
  bldData() { return this.state.bldLive || []; },
  bldIsLive() { return !!this.state.bldLive; },

  async bldLoad(opts) {
    if (this._bldLoading) return;
    this._bldLoading = true;
    clearTimeout(this._bldRetry);
    this.setState({ bldLoading: true });
    try {
      const res = await energyApi.listBuildings();
      const shaped = shapeLiveBuildings(res);
      this._bldAttempts = 0;
      this.setState({ bldLive: shaped, bldLoading: false, bldError: "", bldLoadedAt: new Date().toISOString(), bldMeta: { sitesRows: res.sites_table_rows, unit: res.benchmark_unit, root: res.root } });
      if (opts && opts.announce) this.flash("Building table loaded — " + shaped.length + " sites");
      this.bldLoadShape();
      this.glLoadTables();
    } catch (e) {
      // The company changed while this read was in flight: api/client.js disowned the
      // response, and the switch has already started a correctly-scoped read. Reporting
      // it would put a spurious error on a register that is loading perfectly well.
      if (isStaleScope(e)) return;
      const msg = (e && e.message) || String(e);
      this._bldAttempts = (this._bldAttempts || 0) + 1;
      this.setState({ bldLoading: false, bldError: msg });
      if (this._bldAttempts < RETRY_MAX) this._bldRetry = setTimeout(() => this.bldLoad(), RETRY_MS);
      if (opts && opts.announce) this.flash("Buildings backend unreachable — " + msg);
    } finally {
      this._bldLoading = false;
    }
  },

  bldRetryNow() { this._bldAttempts = 0; return this.bldLoad({ announce: true }); },

  // The register is read again when it may have changed (Hussain, 2 Nov 2026: "Buildings is
  // not automatically updated"). It was read at sign-in and after a hoist, an edit or a
  // document delete in this tab - never after a document ingested through the chat, a
  // migration that wrote buildings, or a building hoisted in another tab or by someone else.
  // Now: after a change this tab knows of (bldAfterChange: a chat turn that carried files, a
  // migration that completed), and on opening Buildings or Home with a read older than
  // BLD_STALE_MS (bldIfStale, from HoistraLogic.setState). The per-building rows (bgTree) are
  // dropped with it, so an open building shows its new documents too.
  // Only a register already read: the first read is loadLiveData's.
  bldAfterChange() {
    if (!this.state.signedIn || !this.state.bldLive) return undefined;
    this.setState({ bgTree: {} });
    return this.bldLoad();
  },
  bldIfStale() {
    const at = Date.parse(this.state.bldLoadedAt || '');
    if (isNaN(at) || Date.now() - at < BLD_STALE_MS) return undefined;
    return this.bldAfterChange();
  },

  // The Hoist Graph panel is a picture of the schema. Read it rather than compiling it in:
  // a hardcoded table count goes stale the first time a migration runs, silently, because
  // nothing compares it to anything.
  async bldLoadShape() {
    if (this._shapeLoading) return;
    this._shapeLoading = true;
    try {
      const res = await energyApi.graphShape();
      if (res && res.ok) this.setState({ bldShape: res });
    } catch (e) {
      // A stale panel is better than a broken page; the figures fall back to the row data.
    } finally {
      this._shapeLoading = false;
    }
  },

  // One table row. Handles both the seed record (numbers always present) and a live
  // record (any figure may be missing, and every figure carries its provenance).
  bldRow(b) {
    const pack = PACKS[b.cc] || { flag: countryMeta(b.cc).flag, name: countryMeta(b.cc).name, std: "—", note: "no regulation pack for this market", tone: "dormant" };
    const hasEui = typeof b.euiN === "number";
    const hasBench = typeof b.benchN === "number";
    const over = hasEui && hasBench ? b.euiN > b.benchN : false;
    const deltaN = hasEui && hasBench ? (typeof b.deviation === "number" ? b.deviation : Math.round(((b.euiN - b.benchN) / b.benchN) * 100)) : null;
    const std = b.std || pack.std;
    const stdNote = b.stdNote || pack.note;
    const stdTone = b.stdTone || pack.tone;
    const gran = b.gran || "building-level";
    const hoist = typeof b.hoist === "number" ? b.hoist : null;
    const benchTip = !hasBench ? "No benchmark figure: " + (b.benchSource === null && b.live ? "no reference table for this standard on the platform yet" : "not computed")
      : b.benchSource === "portfolio_rolling" ? "Median EUI of " + b.benchComparables + " comparable buildings in the portfolio (same use)"
      : b.benchSource === "eui_snapshot" ? "Benchmark recorded with the EUI snapshot"
      : b.benchSource === "energy_profile" ? "TM46 figure on the building's energy profile"
      : b.benchSource === "tm46_by_site_type" ? "TM46 category matched from the site's recorded use — a default, not a survey"
      : b.benchSource === "sites_recorded" ? "Recorded on the site row"
      : std;
    return {
      // Carried through so the drawer can read them without a second lookup.
      counts: b.counts, spaces: b.spaces, partial: b.partial,
      buildingsOnSite: b.buildingsOnSite, missing: b.missing, euiN: b.euiN,
      // The human reference is the code; the uuid is the key edit and delete address it by.
      id: b.id, buildingId: b.buildingId, idText: b.code || b.id,
      idTip: b.buildingId ? "Building ID " + b.buildingId + (b.code ? " · code " + b.code : "") + (b.siteId ? " · site " + b.siteId : "")
        : (b.code && b.code !== b.id ? "Site ID " + b.id + " · code " + b.code : "Site ID"),
      row: b,
      name: b.name, use: b.use,
      floors: typeof b.floors === "number" ? String(b.floors) : "—",
      floorsTip: b.floorsSource === "floors_table" ? "Counted from " + b.floors + " floor records"
        : b.floorsSource === "buildings_recorded" ? "Recorded on the building row — no floor rows on record"
        : "No floors on record",
      area: b.area,
      areaTip: b.areaSource === "spaces_sum" ? "Summed from " + (b.spaces || 0) + " spaces"
        : b.areaSource === "sites" || b.areaSource === "buildings_recorded" ? "Recorded on the building row"
        : b.areaSource === "energy_profile" ? "GIA from the building's energy profile"
        : "No floor area on record",
      flag: pack.flag, country: pack.name, state: b.state,
      eui: hasEui ? Math.round(b.euiN) + " kWh/m²" : "—",
      euiTip: hasEui ? (b.euiSource === "sites_recorded" ? "Recorded on the site row (no meter feed yet) — not a computed reading" : b.euiPeriod ? "Annualised from meter readings, " + b.euiPeriod : "annualised EUI") : "No EUI: no meter reading and nothing recorded on the site",
      bench: hasBench ? Math.round(b.benchN) + " kWh/m²" : "—",
      benchTip: benchTip,
      delta: deltaN === null ? "—" : (Math.round(deltaN) > 0 ? "+" : "") + Math.round(deltaN) + "%",
      // Read off the ROUNDED figure so the word never contradicts the number beside it. A
      // building 0.2% over its benchmark displays "0%", and "0% over" reads as a fault where
      // "0% at benchmark" reads as what it is.
      deltaWord: deltaN === null ? (hasEui ? "no benchmark" : "no reading")
        : Math.round(deltaN) > 0 ? "over" : Math.round(deltaN) < 0 ? "under" : "at benchmark",
      std: std, stdNote: stdNote,
      stdFg: stdTone === "ok" ? "var(--st-ok)" : stdTone === "warn" ? "var(--st-warn)" : "var(--st-dormant)",
      route: b.route,
      routeGran: gran === "sub-metered" ? "sub-metered" + (b.metersSimulated ? " · simulated feed" : "") : gran === "none" ? "no meter · nothing inferred" : "building-level · inferred" + (b.metersSimulated ? " · simulated feed" : ""),
      // Amber is reserved for INFERENCE, not for a threshold. Building-level metering is
      // marked because attribution to a plant item there is inferred rather than measured —
      // a statement about how the reading was obtained, not about whether it is bad. A
      // sub-metered building over its benchmark is a problem and reads red; a building-level
      // building at its benchmark is amber, because the number rests on an inference.
      routeGranFg: gran === "sub-metered" ? "var(--color-neutral-500)" : gran === "none" ? "var(--color-neutral-500)" : "var(--st-warn)",
      inferred: gran !== "sub-metered" && gran !== "none",
      routeTip: b.route + " · " + gran + (b.metersActive ? " · " + b.metersActive + " active meter" + (b.metersActive === 1 ? "" : "s") : "") + (b.meteringSource === "sites_recorded" ? " · as recorded on the site" : ""),
      mixText: b.mix.length > 1 ? b.mix.map((m) => m[0] + " " + m[1] + "%").join(" · ") : "single use",
      mixTip: b.mix.map((m) => m[0] + " " + m[1] + "%").join(" · ")
        + (b.useMixSource === "spaces_by_type" ? " · grouped from " + (b.spaces || 0) + " spaces by area" : ""),
      mix: b.mix.map((m) => {
        const t = USE_TINT[m[0]] || { color: "var(--color-neutral-700)" };
        return { pct: m[1] + "%", color: t.color, hatch: t.hatch || "none", border: "0", tip: m[0] + " — " + m[1] + "% of floor area" };
      }),
      // Rendered, not hidden in a tooltip. See SOURCE_WORD.
      floorsSrc: sourceWord(b.floorsSource),
      floorsSrcFg: sourceTone(b.floorsSource),
      areaSrc: sourceWord(b.areaSource),
      areaSrcFg: sourceTone(b.areaSource),
      euiSrc: hasEui ? euiSourceWord(b.euiSource) : "",
      euiSrcFg: SOFT_SOURCES.has(euiSourceWord(b.euiSource)) ? "var(--st-warn)" : "var(--color-neutral-500)",
      benchSrc: hasBench ? sourceWord(b.benchSource) : "",

      // The graph holds only part of this building. The most actionable thing on the row
      // for anyone deciding what to ingest next, so it gets a badge rather than a tooltip.
      partialShow: (b.partial || []).length ? "inline-block" : "none",
      partialLabel: !(b.partial || []).length ? "" : (b.partial.length === 1 ? "part counted" : "partly counted"),
      partialTip: (b.partial || []).join(" · ")
        + " — the graph holds part of this building, so the surveyed figure stands.",

      score: hoist === null ? "—" : String(hoist),
      scoreTip: b.live
        ? "Hoist Score" + (hoist === null ? " not recorded on the site" : "") + (typeof b.completeness === "number" ? " · record completeness " + b.completeness + "%" + (b.missing && b.missing.length ? " — missing: " + b.missing.join(", ") : "") : "")
        : "Hoist Score",
      euiColor: !hasEui ? "var(--color-neutral-500)" : over ? "var(--st-risk)" : "var(--st-ok)",
      scoreColor: hoist === null ? "var(--color-neutral-500)" : hoist >= 80 ? "var(--st-ok)" : hoist >= 65 ? "var(--st-warn)" : "var(--st-risk)",
      click: () => this.flash(b.name + " — " + (typeof b.floors === "number" ? b.floors + " floors, " : "") + b.area + (b.live ? " · " + (b.missing.length ? b.missing.length + " fields missing on the record" : "record complete") : ", floor-level use table keyed on floor ID."))
    };
  },

  bldVals() {
    const s = this.state;
    const rows = this.bldData();
    // Its own key, not bldQuery: that one belongs to the TopBar building picker (auth.js),
    // which clears it whenever the picker opens — sharing it wiped this table's search.
    const query = s.bldSearch || "";
    const filtered = filterBuildings(rows, query);
    const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
    // Clamped rather than reset on every render, so a table that shrinks (a search, or the
    // register itself losing rows on reload) never leaves the page past the end.
    const page = Math.min(Math.max(0, s.bldPage || 0), pageCount - 1);
    const pageStart = filtered.length ? page * PAGE_SIZE + 1 : 0;
    const pageEnd = Math.min(filtered.length, (page + 1) * PAGE_SIZE);
    const pageRows = filtered.slice(page * PAGE_SIZE, page * PAGE_SIZE + PAGE_SIZE);
    return {
      buildingRows: pageRows.map((b) => this.bldRow(b)),
      bldLive: !!s.bldLive,
      bldCount: rows.length,
      // The register is rooted on buildings where the graph has them and falls back to
      // sites where it does not, and the endpoint says which in `root`. Naming the wrong
      // one is not cosmetic: on a fresh database the header read "1 site" while
      // plenum_cafm.sites held nothing, which sends whoever is checking to an empty table.
      bldSourceLabel: s.bldLive ? "Live · " + rows.length + " " + ROOT_NOUN(s, rows.length)
          + (s.bldError ? " · refresh failed" : "")
        : s.bldLoading ? "Connecting…" : "Seed data · backend unreachable",
      bldSourceDot: s.bldLive ? (s.bldError ? "var(--st-warn)" : "var(--st-ok)") : s.bldLoading ? "var(--color-neutral-500)" : "var(--st-warn)",
      bldSourceDetail: s.bldError || (s.bldLoadedAt ? "register read " + fmtTime(s.bldLoadedAt) : ""),
      bldRetryShow: !s.bldLoading && (!s.bldLive || !!s.bldError) ? "inline" : "none",
      bldRetry: () => this.bldRetryNow(),

      // Search — client-side, over the rows already loaded; see filterBuildings.
      bldSearch: query,
      setBldSearch: (e) => this.setState({ bldSearch: e.target.value, bldPage: 0 }),
      bldSearchShow: rows.length ? "flex" : "none",

      // Pagination — 20 rows a page, computed after the search filter.
      bldPage: page,
      bldPageCount: pageCount,
      bldPageLabel: filtered.length ? pageStart + "–" + pageEnd + " of " + filtered.length + (query ? " matching" : "") : "0 of 0",
      bldPagerShow: filtered.length > PAGE_SIZE ? "flex" : "none",
      bldPagePrevShow: page > 0,
      bldPageNextShow: page < pageCount - 1,
      bldPagePrev: () => this.setState((p) => ({ bldPage: Math.max(0, (p.bldPage || 0) - 1) })),
      bldPageNext: () => this.setState((p) => ({ bldPage: Math.min(pageCount - 1, (p.bldPage || 0) + 1) })),

      bldEmptyShow: filtered.length ? "none" : "block",
      bldEmptyText: s.bldLoading ? "Reading the buildings register…"
        : s.bldError ? "No buildings shown: the backend could not be reached (" + s.bldError + "). This table only ever shows rows from the buildings register."
        // An empty allocation and an empty table look identical here, and the fix for each
        // is in a different place — one is an admin granting access, the other is hoisting a
        // building. Saying "the table has no rows yet" to someone who simply cannot see any
        // of them sends them hunting for missing data that is sitting right there.
        : !rows.length && isUnallocated(s.account)
          ? "You are not allocated to any building yet, so this table is empty for your account — the portfolio itself may be full. Ask an admin to allocate you, or to change your role to admin."
        : !rows.length && s.bldLive ? "The buildings register has no rows yet. Hoist a building; nothing is shown that is not in the register."
        : !rows.length ? "Waiting for the register."
        : "No buildings match “" + query + "”. Try a name, building ID, country, city, state, use, floors or postcode.",
      bldKicker: "Every row is a " + ROOT_NOUN(s, 1) + " record, keyed on "
        + (ROOT_TABLE(s) === "buildings" ? "building ID" : "site ID")
        + ", read against its country's regulation pack",

      // Five figures, all read. `buildings` and `bound documents` come from the rows already
      // loaded; the schema three come from the database itself.
      graphStatsLive: (() => {
        const sh = s.bldShape;
        const docs = rows.reduce((t, b) => t + (((b.counts || {}).documents) || 0), 0);
        return [
          { value: sh ? String(sh.tables) : "—", label: "tables", color: "var(--color-text)" },
          { value: sh ? String(sh.columns) : "—", label: "columns", color: "var(--color-text)" },
          { value: sh ? String(sh.relationships) : "—", label: "relationships", color: "var(--color-text)" },
          { value: String(rows.length), label: "buildings", color: "var(--color-accent)" },
          { value: String(docs), label: "bound documents", color: "var(--color-text)" }
        ];
      })(),
      graphStatsLiveShow: !!s.bldLive
    };
  }
};
