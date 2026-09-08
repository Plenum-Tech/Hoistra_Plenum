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

const RETRY_MS = 30000;
const RETRY_MAX = 6;

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
    key: r.building_id || r.site_id || r.site_key || String(i),
    // Building ID is buildings.building_id when the graph is the root, else sites.site_id.
    id: r.building_id || r.site_id || r.code || String(i + 1),
    // The delete keys on the building's own uuid. `id` above falls back to a site_id for
    // older rows, and deleting by that would address the wrong thing or nothing at all.
    buildingId: r.building_id || null,
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
    metersSimulated: !!r.meters_simulated
  };
}

export function shapeLiveBuildings(payload) {
  const rows = (payload && payload.buildings) || [];
  return rows.map(shapeLiveBuilding).sort((a, b) => a.name.localeCompare(b.name));
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
      this.setState({ bldLive: shaped, bldLoading: false, bldError: "", bldLoadedAt: new Date().toISOString(), bldMeta: { sitesRows: res.sites_table_rows, unit: res.benchmark_unit } });
      if (opts && opts.announce) this.flash("Building table loaded — " + shaped.length + " sites");
    } catch (e) {
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
      id: b.id, buildingId: b.buildingId, idTip: b.code && b.code !== b.id ? "sites.site_id " + b.id + " · building_code " + b.code : "sites.site_id",
      name: b.name, use: b.use,
      floors: typeof b.floors === "number" ? String(b.floors) : "—",
      floorsTip: b.floorsSource === "floors_table" ? "Counted from " + b.floors + " rows in plenum_cafm.floors"
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
    return {
      buildingRows: rows.map((b) => this.bldRow(b)),
      bldLive: !!s.bldLive,
      bldCount: rows.length,
      bldSourceLabel: s.bldLive ? "Live · svc-operations-intelligence · " + rows.length + (rows.length === 1 ? " site" : " sites") + (s.bldError ? " · refresh failed" : "")
        : s.bldLoading ? "Connecting to svc-operations-intelligence…" : "Seed data · backend unreachable",
      bldSourceDot: s.bldLive ? (s.bldError ? "var(--st-warn)" : "var(--st-ok)") : s.bldLoading ? "var(--color-neutral-500)" : "var(--st-warn)",
      bldSourceDetail: s.bldError || (s.bldLoadedAt ? "register read " + fmtTime(s.bldLoadedAt) : ""),
      bldRetryShow: !s.bldLoading && (!s.bldLive || !!s.bldError) ? "inline" : "none",
      bldRetry: () => this.bldRetryNow(),
      bldEmptyShow: rows.length ? "none" : "block",
      bldEmptyText: s.bldLoading ? "Reading plenum_cafm.sites…"
        : s.bldError ? "No buildings shown: the backend could not be reached (" + s.bldError + "). This table only ever shows rows from plenum_cafm.sites."
        : s.bldLive ? "plenum_cafm.sites has no rows yet. Hoist a building or insert a site row; nothing is shown that is not in the table."
        : "Waiting for svc-operations-intelligence.",
      bldKicker: "Every row is a plenum_cafm.sites record, keyed on site_id, read against its country's regulation pack"
    };
  }
};
