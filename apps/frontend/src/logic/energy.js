// energy — energy scope, ratings, buildings list and the investigate conversation.
//
// Database only. Buildings come from buildingsLive.js's bldData(), open anomalies from
// energyLive.js's enAnomalies(), market profiles and ratings from the engines that compute
// them. There is no seed fallback and no bundled portfolio: a page with nothing behind it
// shows nothing and says so. A demo portfolio rendered against an empty database is
// indistinguishable from real data, and was read as exactly that.
//
// What stays hard-coded is the rule book, never the readings: which standard a market is
// held to and what its tariff and data routes are (PACKS/ENC), the attribute rows of the
// market profile (EN_ATTRS), and the detection rules themselves (HOISTRA_EN). None of that
// is ingested; all of it is the reference an ingested reading is judged against.
//
// Methods are mixed into HoistraLogic.prototype; `this` is the controller.
import { PACKS, ENC, EN_ATTRS, ENC_MIXED_AVAIL, ENC_MIXED_HELD, t } from './constants.js';
import { HOISTRA_EN } from '../data/hoistra-energy.js';
import { moneyGBP, IMPLEMENTED_RULE_IDS } from './energyLive.js';

const impactNum = (a) => (typeof a.impactN === "number" ? a.impactN : parseInt(String(a.impact || "0").replace(/[^0-9]/g, ""), 10) || 0);

// Same page size the Buildings page uses. A live portfolio can put 600+ buildings in one
// country group — rendering all of them inline was the actual bug report.
const ENERGY_PAGE_SIZE = 20;

// Pure: slice an already-filtered groups array (each with a full `buildings` list) down to
// one page of ENERGY_PAGE_SIZE buildings total, spanning group boundaries. Each group kept
// on the page carries its ORIGINAL label/std/meta (computed from its full filtered list, not
// the trimmed page slice) — a group's header should still say "603 buildings" even when the
// page only shows 14 of them.
function paginateBuildingGroups(rawGroups, page) {
  const flatTotal = rawGroups.reduce((n, g) => n + g.buildings.length, 0);
  const pageCount = Math.max(1, Math.ceil(flatTotal / ENERGY_PAGE_SIZE));
  const pageN = Math.min(Math.max(0, page || 0), pageCount - 1);
  let skip = pageN * ENERGY_PAGE_SIZE, take = ENERGY_PAGE_SIZE;
  const groups = [];
  rawGroups.forEach((g) => {
    if (take <= 0) return;
    if (skip >= g.buildings.length) { skip -= g.buildings.length; return; }
    const slice = g.buildings.slice(skip, skip + take);
    if (slice.length) groups.push(Object.assign({}, g, { buildings: slice }));
    take -= slice.length;
    skip = 0;
  });
  return { groups: groups, flatTotal: flatTotal, pageCount: pageCount, page: pageN };
}

// How a ratings-position tile earns the word "actual": a certificate or a filing is actual
// the day it is on file; a consumption figure is actual only once its 12-month window is
// complete, projected from 3, and read as insufficient below that. The tile's own `tone`
// (set server-side, including "dormant" for "nothing computed yet") drives the headline
// colour; this only derives the badge/confidence-bar underneath it.
function ratingBadge(r) {
  if (r.basis === "certificate") return { text: "Actual · from certificate", fg: "var(--st-ok)", conf: 100, show: "none" };
  if (r.basis === "filing") return { text: "Actual · from filing", fg: "var(--st-ok)", conf: 100, show: "none" };
  if (r.basis === "scheme") return { text: "No scheme to measure against", fg: "var(--color-neutral-500)", conf: 0, show: "none" };
  const mo = r.months || 0;
  if (mo >= 12) return { text: "Actual · 12 of 12 months", fg: "var(--st-ok)", conf: 100, show: "block" };
  if (mo >= 3) { const c = 45 + (mo - 3) * 5; return { text: "Projected · " + mo + " of 12 months · " + c + "% confidence", fg: "var(--st-warn)", conf: c, show: "block" }; }
  if (mo > 0) return { text: "Insufficient data · " + mo + " of 12 months · shown from 3", fg: "var(--color-neutral-500)", conf: Math.round(mo * 10), show: "block" };
  return { text: "Not computed yet", fg: "var(--color-neutral-500)", conf: 0, show: "none" };
}

export const energyMethods = {

  /* Energy scope. An empty selection means the whole portfolio; one country
     means the analysis can use that market's route and standard; two or more
     means only what the markets share survives.

     The markets are the ones the buildings on record are actually in. This used to be
     the four packs the platform holds, which made every portfolio mixed: two buildings
     both in the United Kingdom reported four markets in scope, and the page then
     withheld the single ranking, the half-hourly pattern analysis and the regulatory
     exposure number on the grounds that they do not survive a mix. There was no mix.
     A pack existing is not a market being in scope.

     Buildings with no country are deliberately not a market of their own — they stay in
     scope under 'all countries' and drop out the moment a specific one is picked, which
     is what the filter below already does. */
  enScope(s) {
    const packs = ["UK", "US", "AE", "SG"];
    const onRecord = {};
    (this.bldData() || []).forEach((b) => {
      if (b && b.cc && packs.indexOf(b.cc) > -1) onRecord[b.cc] = true;
    });
    // Pack order, so the chips do not reshuffle as buildings load. Falling back to every
    // pack only while the register is still empty: mid-load is not a portfolio.
    const found = packs.filter((cc) => onRecord[cc]);
    const all = found.length ? found : packs;
    const sel = (s.eScope || []).length ? s.eScope : all;
    return { all: all, sel: sel, single: sel.length === 1, isAll: !(s.eScope || []).length };
  },

  enVals(s) {
    const sc = this.enScope(s);
    // "All countries" means the whole portfolio, not "whichever of these four codes
    // matches" — a building with no country on record is still in scope until a specific
    // market is picked, at which point it honestly drops out of that market.
    const allBuildings = this.bldData();
    const bs = sc.isAll ? allBuildings : allBuildings.filter((b) => sc.sel.indexOf(b.cc) > -1);
    const hasEui = (b) => typeof b.euiN === "number" && typeof b.benchN === "number";
    const m2 = (b) => (typeof b.areaM2 === "number" ? b.areaM2 : 0);
    // The building's own contracted rate where the API carries one, the market pack only as a
    // fallback. Pricing every building at one constant is what made the card and the stored
    // snapshot disagree about what the same excess kWh costs.
    const rate = (b) => (typeof b.tariffN === "number" && b.tariffN > 0
      ? b.tariffN : (ENC[b.cc] || ENC.UK).tariff);
    const measured = bs.filter(hasEui);
    const area = measured.reduce((q, b) => q + m2(b), 0);
    const eui = area ? measured.reduce((q, b) => q + b.euiN * m2(b), 0) / area : null;
    const bench = area ? measured.reduce((q, b) => q + b.benchN * m2(b), 0) / area : null;
    const excess = measured.reduce((q, b) => q + Math.max(0, b.euiN - b.benchN) * m2(b) * rate(b), 0);

    const allAnoms = this.enAnomalies();
    const anomCc = (a) => a.cc;
    const anoms = sc.isAll ? allAnoms : allAnoms.filter((a) => sc.sel.indexOf(anomCc(a)) > -1);
    const anomSum = anoms.reduce((q, a) => q + impactNum(a), 0);
    const cc0 = sc.sel[0];
    // Say the rate that was actually charged. Where the buildings in scope carry their own
    // contracted rates and those differ, one number would be a fiction, so the range is named.
    const rates = measured.filter((b) => typeof b.tariffN === "number" && b.tariffN > 0).map((b) => b.tariffN);
    const pence = (v) => (v * 100).toFixed(1) + "p";
    const tariffLabel = rates.length === 0
      ? (ENC[cc0] || ENC.UK).tariffLabel
      : (Math.min.apply(null, rates) === Math.max.apply(null, rates)
          ? pence(rates[0]) + "/kWh contracted"
          : pence(Math.min.apply(null, rates)) + "–" + pence(Math.max.apply(null, rates)) + "/kWh contracted")
        + (rates.length < measured.length ? " on " + rates.length + " of " + measured.length + " buildings" : "");
    const P = PACKS[cc0] || PACKS.UK;
    const E = ENC[cc0] || ENC.UK;
    const money = moneyGBP;
    const delta = bench ? Math.round(((eui - bench) / bench) * 100) : 0;
    // Ratings follow a country picker inside the section; it falls back to the
    // first market in scope whenever the previous pick drops out of scope.
    const rcc = sc.single ? cc0 : (sc.sel.indexOf(s.enRatingCc) > -1 ? s.enRatingCc : cc0);
    const unattributed = allBuildings.filter((b) => !b.cc || b.cc === "—").length;

    return {
      isEnergy: s.view === "module" && s.module === "energy",
      enChips: [{ cc: null, label: "All countries", flag: "" }].concat(sc.all.map((cc) => ({ cc: cc, label: PACKS[cc].name, flag: PACKS[cc].flag }))).map((c) => {
        const on = c.cc ? (s.eScope || []).indexOf(c.cc) > -1 : sc.isAll;
        return {
          label: c.flag ? c.flag + " " + c.label : c.label,
          n: String(c.cc ? allBuildings.filter((b) => b.cc === c.cc).length : allBuildings.length),
          edge: on ? "var(--color-accent)" : "var(--color-divider)",
          bg: on ? "var(--color-accent-900)" : "transparent",
          fg: on ? "var(--color-accent)" : "var(--color-neutral-400)",
          pick: () => {
            if (!c.cc) return this.setState({ eScope: [] });
            this.setState((p) => {
              const cur = p.eScope || [];
              const next = cur.indexOf(c.cc) > -1 ? cur.filter((x) => x !== c.cc) : cur.concat([c.cc]);
              return { eScope: next };
            });
          }
        };
      }),

      enFidelity: sc.single ? "Single market · full fidelity" : "Mixed portfolio · normalised metrics only",
      enFidelityFg: sc.single ? "var(--st-ok)" : "var(--st-warn)",
      enFidelityBg: sc.single ? "var(--st-ok-bg)" : "var(--st-warn-bg)",
      enFidelityIcon: sc.single ? "ph-check-circle" : "ph-warning",
      enFidelityNote: (sc.single
        ? (cc0 === "AE"
            ? "No operational standard — scored against a rolling portfolio benchmark of 228 kWh/m²/yr. "
            : "Benchmarked against " + P.std + " (" + P.note + "). ")
          + "Data arrives by " + E.routes.join(" and ") + " — " + E.grain + ". Tariff " + tariffLabel + "."
        : sc.sel.length + " markets in scope with " + sc.sel.map((cc) => (ENC[cc] || ENC.UK).routes.length).reduce((a, b) => a + b, 0) + " different data routes and " + sc.sel.length + " different benchmark bases. Only metrics that survive the difference are shown; the rest are named below rather than approximated.")
        + (unattributed ? " " + unattributed + " of " + allBuildings.length + " buildings on record have no country attributed yet, so the per-market figures above undercount the portfolio." : ""),

      enScopeCards: [
        { l: "Buildings in scope", v: String(bs.length), s: sc.single ? PACKS[cc0].flag + " " + PACKS[cc0].name : sc.sel.map((cc) => PACKS[cc].flag).join(" ") + " " + sc.sel.length + " markets", tone: "ok" },
        { l: "EUI, area weighted", v: eui === null ? "—" : Math.round(eui) + "",
          s: eui === null ? "no EUI reading on record for any building in scope yet"
            : (delta > 0 ? "+" : "") + delta + "% against " + (sc.single ? (cc0 === "AE" ? "the rolling portfolio benchmark" : P.std) : "each building's own pack"),
          tone: eui === null ? "dormant" : delta > 8 ? "risk" : delta > 0 ? "warn" : "ok" },
        { l: "Cost above benchmark / year", v: eui === null ? "—" : money(excess),
          s: eui === null ? "no EUI reading to compare against a benchmark" : "(EUI − reference) × area × tariff",
          tone: eui === null ? "dormant" : "risk" },
        { l: "Anomaly cost / year", v: money(anomSum), s: anoms.length + (anoms.length === 1 ? " anomaly" : " anomalies") + " · deviation from own baseline × tariff", tone: anoms.length > 4 ? "warn" : "ok" }
      ].map((c) => ({ l: c.l, v: c.v, s: c.s, color: c.tone === "dormant" ? "var(--color-neutral-500)" : t(c.tone).color })),

      enAvail: (sc.single ? E.avail : ENC_MIXED_AVAIL).map((x) => ({ label: x })),
      enHeld: (sc.single ? E.held : ENC_MIXED_HELD).map((x) => ({ label: x })),
      enRoutes: sc.sel.map((cc) => ({
        label: PACKS[cc].flag + " " + PACKS[cc].name,
        route: (ENC[cc] || ENC.UK).routes.join(" · "),
        grain: (ENC[cc] || ENC.UK).grain,
        n: allBuildings.filter((b) => b.cc === cc).length + " buildings"
      })),
      enHeldTitle: sc.single ? "Limits at this scope" : "Not available across a mixed portfolio",
      enAvailTitle: sc.single ? "Available at this scope" : "Available across every market in scope",

      enMatrixCols: "168px repeat(" + sc.sel.length + ", minmax(200px,1fr))",
      // EN_ATTRS supplies the ROWS this table has, which is structure and the same for
      // every deployment. Every VALUE comes from the engine or reads "—". A sample value
      // standing in for a measurement is what made an empty database look full.
      enMatrixHead: sc.sel.map((cc) => {
        const live = s.enProfilesLive && s.enProfilesLive.markets && s.enProfilesLive.markets[cc];
        const n = live ? live.buildings : allBuildings.filter((b) => b.cc === cc).length;
        return { label: PACKS[cc].flag + " " + PACKS[cc].name, n: n + (n === 1 ? " building" : " buildings") };
      }),
      enSideTitle: sc.single ? (cc0 === "AE" ? "EUI vs rolling portfolio benchmark" : "EUI vs " + P.std) : "EUI vs each building's own pack",
      enSideFoot: sc.single
        ? "Bar length is EUI against " + (cc0 === "AE" ? "the 228 kWh/m²/yr rolling portfolio benchmark" : "the " + P.std + " reference") + ". Over-benchmark buildings carry the " + money(excess) + " gap at " + tariffLabel + "."
        : "Bar length is EUI against each building's own country pack. The " + money(excess) + " gap is summed at local tariffs and converted to GBP; the bars are not comparable across markets.",
      enAsks: (sc.single ? ({
        UK: ["Which UK buildings sit over their EUI benchmark?", "Which buildings miss EPC B by 2031?", "Rank UK buildings by cost per m²"],
        US: ["Which US buildings sit closest to their LL97 cap?", "What would lift the Energy Star position?", "Is the Green Button feed live on every account?"],
        AE: ["Which chillers drift against cooling degree days?", "What does the DEWA bill show that the BMS cannot see?", "Rank UAE buildings by RT per m²"],
        SG: ["Is the BCA submission consistent with the retailer feed?", "Which building drifted furthest from its own baseline?", "What does the retail contract say about data at renewal?"]
      })[cc0] : ["Which markets drive the excess cost?", "Which buildings are worst against their own pack?", "Where does the data route limit what I can see?"]),
      enMatrixRows: ((s.enProfilesLive && s.enProfilesLive.attributes) || EN_ATTRS).map((a, i, attrs) => ({
        section: i === 0 || attrs[i - 1][0] !== a[0] ? a[0] : "",
        sectionShow: i === 0 || attrs[i - 1][0] !== a[0] ? "flex" : "none",
        label: a[1],
        cells: sc.sel.map((cc) => {
          const live = s.enProfilesLive && s.enProfilesLive.markets && s.enProfilesLive.markets[cc];
          const c = live && live.cells ? live.cells[a[2]] : null;
          if (!c) return { v: "—", basis: "", basisShow: "none", note: "not measured in this deployment yet" };
          // A measured or derived cell earns a small label saying so, with how many
          // buildings or meters stand behind it; a reference one says it is reference.
          const tag = c.basis === "reference" ? "reference"
            : c.basis + (typeof c.n === "number" ? " · " + c.n : "");
          return { v: c.v || "—", basis: tag, basisShow: c.basis ? "inline-block" : "none", note: c.note || "" };
        })
      })),
      enMatrixTitle: sc.single ? "Market profile · " + PACKS[cc0].name : "Market profiles side by side · " + sc.sel.length + " markets",
      enMatrixShow: s.enMatrixOpen ? "block" : "none",
      enMatrixEdge: s.enMatrixOpen ? "1px solid var(--color-divider)" : "0",
      enMatrixCaret: s.enMatrixOpen ? "ph-caret-down" : "ph-caret-right",
      enMatrixCta: s.enMatrixOpen ? "Hide" : "Show benchmark, data source and commercial terms",
      enMatrixToggle: () => this.setState((p) => ({ enMatrixOpen: !p.enMatrixOpen })),
      enMatrixNote: sc.single
        ? "Three things move with the regulation: the benchmark the building is held to, how its data arrives, and what the energy costs and is billed in."
        : "Read across a row to see why the headline numbers above are normalised rather than compared: the standards, units, routes and currencies in scope differ.",

      enRatingSingle: sc.single,
      enRatingMulti: !sc.single,
      enRatingName: PACKS[cc0].flag + " " + PACKS[cc0].name,
      enRatingCc: rcc,
      enRatingOptions: sc.sel.map((cc) => ({ cc: cc, label: PACKS[cc].flag + " " + PACKS[cc].name })),
      enRatingPick: (e) => this.setState({ enRatingCc: e.target.value }),
      enRatingHint: "country-scoped · " + sc.sel.length + " markets in scope",
      enRatings: this.enRatingsFromPosition(rcc)
    };
  },

  // MEES/EPCs (UK), LL97/Energy Star/LL84 (US), BCA/EUI/Green Mark (SG) and the rolling
  // benchmark/chiller position (AE) are assembled server-side now, from real records —
  // GET /api/energy/ratings/position (engines/energy/ratings_position.py), loaded once for
  // every market by enPositionLoad() at mount. Each tile already carries its own tone; this
  // only adds the badge/confidence-bar treatment the card shows underneath (the same
  // actual/projected/insufficient-data language the seed used, now driven by the real
  // `basis` and `months` the engine reports instead of a hand-written constant).
  enRatingsFromPosition(cc) {
    const pos = (this.state.enPosByCc || {})[cc];
    if (!pos || (pos.loading && !((pos.tiles || []).length))) {
      // One honest placeholder. Listing the tiles a constant expects would name positions
      // this deployment may not hold, and they would read as computed once filled in.
      return [{
        l: "Ratings", v: "…", s: "loading from the ratings engine", color: "var(--color-neutral-500)",
        badge: "", badgeFg: "var(--color-neutral-500)", confShow: "none", confPct: "0%", confFg: "var(--color-neutral-500)"
      }];
    }
    if (pos.error && !(pos.tiles || []).length) {
      return [{
        l: "Ratings", v: "—", s: "could not reach the ratings engine — " + pos.error,
        color: "var(--color-neutral-500)", badge: "Not sourced", badgeFg: "var(--color-neutral-500)",
        confShow: "none", confPct: "0%", confFg: "var(--color-neutral-500)"
      }];
    }
    return (pos.tiles || []).map((r) => {
      const badge = ratingBadge(r);
      return {
        l: r.l, v: r.v, s: r.s,
        color: r.tone === "dormant" ? "var(--color-neutral-500)" : t(r.tone).color,
        badge: badge.text, badgeFg: badge.fg, confShow: badge.show, confPct: badge.conf + "%", confFg: badge.fg
      };
    });
  },

  /* Investigation. Every building and anomaly on this page is a database row, so every
     investigation asks the real orchestrator (askScoped, same as Compliance/Vendors/
     Buildings) and answers in the Energy page's side dock. The scripted four-stage walk
     that used to run for seed rows went with the seed rows. */
  investigate(kind, o) {
    return this.investigateLive(kind, o);
  },

  // A live building or anomaly: no scripted stages to play, because there is nothing
  // scripted about it. The question goes to the real orchestrator (deepAgents, through
  // askScoped → ccAsk) and streams into the Energy page's own side dock, exactly the way
  // a question typed into its ask bar already does.
  investigateLive(kind, o) {
    let q;
    if (kind === "anomaly") {
      const where = o.building && o.building !== "Unattributed" ? " at " + o.building : "";
      q = "Why is " + o.asset + where + " showing a " + String(o.type).toLowerCase() +
        (o.impact && o.impact !== "—" ? " worth " + o.impact + " a year" : "") + ", and what should I do about it?";
    } else {
      const hasEui = typeof o.euiN === "number" && typeof o.benchN === "number";
      q = hasEui
        ? "Why is " + o.name + " at " + Math.round(o.euiN) + " kWh/m² against a reference of " + Math.round(o.benchN) + ", and how much of that gap can I act on?"
        : "What do we know about " + o.name + "'s energy performance, and what is missing to assess it properly?";
    }
    this.setState({ flow: null, flowDone: "" });
    this.askScoped(q);
  },


  /* Building-centric energy list: one row per building under its market,
     EUI against its own pack first, its anomalies underneath on open. */
  enBuildingVals(s) {
    return this.enBuildingValsLive(s);
  },

  // Live building rows have no fixed per-country membership to iterate — most buildings
  // in this database carry no country at all — so the buckets are the scope's markets
  // plus "—" (unattributed) rather than assumed to be exactly the four packs.
  enBuildingValsLive(s) {
    const sc = this.enScope(s);
    const f = s.filter || "All";
    const query = String(s.enBldQuery || "").trim().toLowerCase();
    const money = moneyGBP;

    // What a building's findings come to. The engine answers this: findings on one meter are
    // different readings of the same consumption, so the headline is the largest single one
    // and NOT their sum. This line used to add them, which is how a building at or under its
    // reference could show hundreds of thousands of pounds of waste on the same row that said
    // "at or under reference".
    //
    // Without the rollup the old arithmetic is still the only thing available, so it is used
    // and labelled — an overstated figure that says it is overstated can at least be checked.
    const roll = s.enRollup || null;
    const anomLine = (b, x) => {
      if (!x.all.length) return "no open anomalies";
      const n = x.all.length + (x.all.length === 1 ? " anomaly" : " anomalies");
      const r = roll && (roll[String(b.buildingId)] || roll[String(b.uuid)] || roll[String(b.id)]);
      if (!r || !r.headline) {
        return n + " · " + money(x.all.reduce((q, a) => q + impactNum(a), 0)) + " · added, may double count";
      }
      const head = money(r.headline.amount) + " " + r.headline.label.toLowerCase();
      // Only worth saying when adding would actually have given something different.
      const gap = typeof r.if_added === "number" && r.if_added > r.headline.amount
        ? " · " + money(r.if_added) + " if every rule were added, which would count the same energy twice"
        : "";
      return n + " on " + r.meters_affected + (r.meters_affected === 1 ? " meter" : " meters")
        + " · largest " + head + gap;
    };
    const anomHit = (a) => f === "New" ? a.status === "New" : f === "Above £20k" ? impactNum(a) > 20000 : true;
    const allBuildings = this.bldData();
    const allAnoms = this.enAnomalies();
    const total = allBuildings.length;
    const buckets = sc.isAll ? sc.all.concat(["—"]) : sc.sel;
    const rawGroups = [];

    buckets.forEach((cc) => {
      const P = PACKS[cc] || { flag: "—", name: "Unattributed — no country on record", std: "no country on record" };
      const list = allBuildings.filter((b) => (b.cc || "—") === cc)
        .filter((b) => !query || b.name.toLowerCase().indexOf(query) > -1)
        .map((b) => {
        const hasEui = typeof b.euiN === "number" && typeof b.benchN === "number";
        const m2 = typeof b.areaM2 === "number" ? b.areaM2 : 0;
        const excess = hasEui ? Math.max(0, b.euiN - b.benchN) * m2 * (typeof b.tariffN === "number" && b.tariffN > 0 ? b.tariffN : (ENC[cc] || ENC.UK).tariff) : 0;
        const all = allAnoms.filter((a) => a.buildingUuid && a.buildingUuid === b.uuid);
        const anoms = all.filter(anomHit);
        const anomSum = anoms.reduce((q, a) => q + impactNum(a), 0);
        return { b: b, hasEui: hasEui, excess: excess, all: all, anoms: anoms, anomSum: anomSum };
      }).filter((x) => {
        if (f === "Over benchmark") return x.hasEui && x.b.euiN > x.b.benchN;
        if (f === "With anomalies") return x.all.length > 0;
        if (f === "New" || f === "Above £20k") return x.anoms.length > 0;
        return true;
      }).sort((p, q) => (q.excess + q.anomSum) - (p.excess + p.anomSum));
      if (!list.length) return;
      const gExcess = list.reduce((q, x) => q + x.excess, 0);
      const gAnom = list.reduce((q, x) => q + x.anomSum, 0);
      const measured = list.filter((x) => x.hasEui).length;
      rawGroups.push({
        label: P.flag + " " + P.name,
        std: cc === "—" ? "no country on record" : (P.std === "NA" ? "rolling portfolio benchmark" : P.std),
        meta: list.length + (list.length === 1 ? " building" : " buildings") + " · " +
          (measured ? money(gExcess) + " above reference (" + measured + " of " + list.length + " with an EUI reading)" : "no EUI reading on record for any of them yet") +
          " · " + money(gAnom) + " in open anomalies",
        buildings: list.map((x) => {
          const b = x.b, hasEui = x.hasEui;
          const delta = hasEui ? Math.round(((b.euiN - b.benchN) / b.benchN) * 100) : null;
          const over = hasEui && b.euiN > b.benchN;
          const open = s.enOpenB === b.name;
          const tone = !hasEui ? "dormant" : b.euiN > b.benchN + 10 ? "risk" : over ? "warn" : "ok";
          const toneFg = tone === "dormant" ? "var(--color-neutral-500)" : t(tone).color;
          return {
            name: b.name,
            eui: hasEui ? Math.round(b.euiN) + " kWh/m²" : "no reading",
            bench: hasEui ? "ref " + Math.round(b.benchN) : "no benchmark yet",
            delta: delta === null ? "—" : (delta > 0 ? "+" : "") + delta + "%", deltaFg: toneFg,
            barPct: hasEui ? Math.min(100, Math.round((b.euiN / 260) * 100)) + "%" : "0%", barColor: toneFg,
            refPct: hasEui ? Math.min(100, Math.round((b.benchN / 260) * 100)) + "%" : "0%",
            route: (b.route || "meter feed") + " · " + (b.gran || "building-level"),
            granFg: b.gran === "sub-metered" ? "var(--color-neutral-500)" : "var(--st-warn)",
            excess: !hasEui ? "no EUI reading on record" : over ? money(x.excess) : "at or under reference",
            excessFg: !hasEui ? "var(--color-neutral-500)" : over ? "var(--color-text)" : "var(--st-ok)",
            anomN: anomLine(b, x),
            anomFg: x.all.length ? "var(--st-warn)" : "var(--color-neutral-500)",
            caret: open ? "ph-caret-down" : "ph-caret-right",
            openShow: open ? "block" : "none",
            bg: open ? "var(--color-neutral-900)" : "var(--color-surface)",
            toggle: () => this.setState((p) => ({ enOpenB: p.enOpenB === b.name ? null : b.name })),
            investigate: (e) => { if (e && e.stopPropagation) e.stopPropagation(); this.investigate("building", b); },
            emptyShow: x.anoms.length ? "none" : "block",
            emptyText: x.all.length ? "No anomalies match the current filter."
              : (hasEui ? "No open anomalies. Any gap above reference here is structural — investigate the building to size the capex case."
                : "No open anomalies, and no EUI reading on record yet to say whether this building is over reference."),
            anomalies: x.anoms.map((a) => ({
              asset: a.asset, type: a.type, impact: a.impact, status: a.status,
              simulatedShow: a.simulated ? "inline-block" : "none",
              days: a.days == null ? "—" : a.days + " day" + (a.days === 1 ? "" : "s") + " active",
              color: t(a.tone).color, bg: t(a.tone).bg,
              open: () => this.setState({ detail: this.anomalyDetail(a) }),
              investigate: (e) => { if (e && e.stopPropagation) e.stopPropagation(); this.investigate("anomaly", a); }
            }))
          };
        })
      });
    });

    const paged = paginateBuildingGroups(rawGroups, s.enBldPage);
    const pageStart = paged.flatTotal ? paged.page * ENERGY_PAGE_SIZE + 1 : 0;
    const pageEnd = Math.min(paged.flatTotal, (paged.page + 1) * ENERGY_PAGE_SIZE);
    // What of the data in view is simulated, said once above the list rather than left for
    // a reader to infer from a row. A figure computed from an invented reading is invented,
    // and the page is the only place that can say so before somebody acts on it.
    const simBuildings = allBuildings.filter((b) => b && b.simulated && b.simulated.any);
    const simNote = simBuildings.length ? (simBuildings[0].simulated.note || "") : "";
    const simSame = simBuildings.every((b) => (b.simulated.note || "") === simNote);
    return {
      enGroups: paged.groups,
      enSimulatedShow: simBuildings.length ? "flex" : "none",
      enSimulatedText: simBuildings.length
        ? (simBuildings.length === 1
            ? simBuildings[0].name + ": " + simNote
            : simBuildings.length + " buildings carry a simulated feed"
              + (simSame && simNote ? " — " + simNote : ""))
        : "",

      enListSummary: (paged.flatTotal ? pageStart + "–" + pageEnd : "0") + " of " + total + " buildings"
        + (f === "All" ? "" : " · filter: " + f) + (query ? " · matching “" + s.enBldQuery + "”" : ""),
      enListEmpty: rawGroups.length ? "none" : "block",
      // Search — client-side, over the buildings already loaded and in scope.
      enBldQuery: s.enBldQuery || "",
      setEnBldQuery: (e) => this.setState({ enBldQuery: e.target.value, enBldPage: 0 }),
      enBldQueryShow: total > ENERGY_PAGE_SIZE ? "flex" : "none",
      // Pagination — ENERGY_PAGE_SIZE buildings a page, spanning country groups.
      enBldPage: paged.page,
      enBldPageCount: paged.pageCount,
      enBldPagerShow: paged.flatTotal > ENERGY_PAGE_SIZE ? "flex" : "none",
      enBldPagePrevShow: paged.page > 0,
      enBldPageNextShow: paged.page < paged.pageCount - 1,
      enBldPagePrev: () => this.setState((p) => ({ enBldPage: Math.max(0, (p.enBldPage || 0) - 1) })),
      enBldPageNext: () => this.setState((p) => ({ enBldPage: Math.min(paged.pageCount - 1, (p.enBldPage || 0) + 1) })),
      isNotEnergy: !(s.view === "module" && (s.module === "energy" || s.module === "assets" || s.module === "ops"))
    };
  },

  invAct(i) {
    const inv = this.state.inv;
    if (!inv) return;
    const a = inv.actions[i];
    if (a.done) return;
    const REPLY = {
      wo: "Work order drafted against the asset record with the evidence above attached. It waits in your decision queue; on approval it goes to the vendor at the contracted rate.",
      inspect: "Inspection booked into the next PPM window. The engineer's findings write back to the asset record and re-run this investigation automatically.",
      credit: "Service credit logged against the contract clause named in the evidence. The vendor scorecard updates when you approve it in the queue.",
      rule: "Rule armed across every building whose data route supports it. Anything it catches lands as a new anomaly, not a silent adjustment.",
      capex: "Capex case opened with the structural gap, plant age and payback from the graph. It appears under Assets for review.",
      bms: "BMS change drafted as a controlled work order — no setpoint changes without a record of who approved them.",
      watch: "Watch set. If the number is back inside band at the check date the anomaly closes itself; if not, this thread reopens.",
      bench: "Re-benchmark queued with the building's actual operating hours. The gap will be restated as waste versus benchmark fit.",
      audit: "Schedule audit queued: every plant start and stop against the occupancy calendar for the last 8 weeks.",
      anoms: "The open anomalies for this building are listed beneath it in the Energy page."
    };
    const reply = { you: "Approve — " + a.l, bot: REPLY[a.k] || "Queued for your approval in the decision queue." };
    const next = Object.assign({}, inv, {
      actions: inv.actions.map((x, j) => j === i ? Object.assign({}, x, { done: true }) : x),
      replies: (inv.replies || []).concat([reply])
    });
    this.setState({ inv: next });
    if (a.k === "email") {
      const to = /Gulf/.test(inv.sub) ? "fm@gulfcooling.ae" : "fm.lead@ashcombe-estates.com";
      return this.setState({
        flow: "email", emKind: "investigate", emKicker: "Ask the FM lead · draft", emTo: to,
        emSubject: a.l + " — " + inv.title,
        emBody: "Hello,\n\nThe energy engine has flagged " + inv.title + ".\n\nWhat the graph shows:\n" + inv.findings.map((f) => "• " + f.t).join("\n") + "\n\nWorking view: " + inv.cause + "\n\nCould you confirm what happened on site, and send any report or log that should be on file? Your reply is attached to the case and either closes it or reopens it.\n\nKind regards,\nPlanum Technologies"
      });
    }
  },

  invVals(s) {
    const inv = s.inv;
    const EN = HOISTRA_EN;
    const b0 = this.enScope(s);
    const stage = s.invStage || 0;
    const rules = EN ? EN.RULES : [];
    // Rule coverage for the scope: how many buildings in scope can arm each rule, read
    // from the Buildings table.
    const allBuildings = this.bldData();
    const bs = b0.isAll ? allBuildings : allBuildings.filter((b) => b0.sel.indexOf(b.cc) > -1);
    return {
      fInvestigate: s.flow === "investigate" && !!inv,
      inv: inv || { title: "", sub: "", cause: "", costLine: "" },
      invQuery: inv ? inv.query : "",
      invPlan: inv ? "I'll walk " + inv.sources.length + " tables in the Hoist Graph — readings first, then the maintenance record around " + (inv.kind === "anomaly" ? "the asset" : "the building") + ", then the documents that should exist for it." : "",
      invAskMsg: !inv ? "" : inv.escalate
        ? "I can't settle this from the graph alone, so the first two actions ask the people who were on site and put an inspection in the diary. The rest follow if the cause holds. Which should I raise?"
        : "The evidence supports these. Each one goes to your decision queue with the findings attached — approve them one at a time or all at once.",
      invReplies: inv ? (inv.replies || []) : [],
      invEscShow: inv && inv.escalate ? "flex" : "none",
      invEscText: inv ? (inv.escText || "") : "",
      invSources: inv ? inv.sources.map((r, i) => ({
        tbl: r.tbl, what: r.what, n: r.n,
        icon: i < (s.invSrcDone || 0) ? "ph-check-circle" : "ph-circle-dashed",
        fg: i < (s.invSrcDone || 0) ? "var(--st-ok)" : "var(--color-neutral-500)",
        op: i < (s.invSrcDone || 0) ? "1" : "0.55",
        gap: /gap|not found/.test(r.n) ? "var(--st-risk)" : "var(--color-neutral-500)"
      })) : [],
      invStage1: stage >= 1, invStage2: stage >= 2, invStage3: stage >= 3,
      invStatus: !inv ? "" : stage === 0 ? "Retrieving from the Hoist Graph — " + (s.invSrcDone || 0) + " of " + inv.sources.length + " tables" : stage === 1 ? "Weighing the evidence" : stage === 2 ? "Naming the cause" : "Actions ready — nothing has been written yet",
      invLive: inv && stage < 3 ? "block" : "none",
      invFindings: inv ? inv.findings.map((f) => ({
        t: f.t, src: f.src, conf: f.conf + "%",
        confFg: f.conf >= 90 ? "var(--st-ok)" : f.conf >= 80 ? "var(--color-text)" : "var(--st-warn)",
        gapShow: /gap|limit/.test(f.src) ? "inline-flex" : "none"
      })) : [],
      invActions: inv ? inv.actions.map((a, i) => ({
        l: a.l, s: a.s,
        icon: a.done ? "ph-check-circle" : a.k === "wo" ? "ph-wrench" : a.k === "email" ? "ph-envelope-simple" : a.k === "credit" ? "ph-receipt" : a.k === "rule" ? "ph-funnel" : a.k === "capex" ? "ph-coins" : a.k === "bms" ? "ph-sliders" : a.k === "watch" ? "ph-clock-countdown" : "ph-arrow-right",
        fg: a.done ? "var(--st-ok)" : "var(--color-accent)",
        edge: a.done ? "var(--st-ok)" : "var(--color-divider)",
        op: a.done ? "0.7" : "1",
        click: () => this.invAct(i)
      })) : [],
      invApproveAll: () => { if (!inv) return; inv.actions.forEach((a, i) => { if (!a.done && a.k !== "email" && a.k !== "anoms") this.invAct(i); }); },

      // All 13 specified rules now have a detector actually running in
      // engines/energy/anomalies.py / engines/energy/detectors.py (LIVE_ANOMALY_TYPES in
      // energyLive.js maps each anomaly_type the backend emits to the rule id it satisfies).
      // Coverage still reads "not built" for any rule id that catalogue does not name — a
      // rule added to the frontend spec ahead of its backend detector should say so, not
      // show a coverage count that implies it is already watching something.
      enRules: rules.map((r) => {
        const live = IMPLEMENTED_RULE_IDS.has(r.id);
        const on = bs.filter((b) => (EN.armed(b.gran, b.route).find((x) => x.id === r.id) || {}).on).length;
        return {
          name: r.name, test: r.test, needs: r.needs,
          cls: r.cls === "core" ? "core" : "added",
          clsBg: r.cls === "core" ? "var(--color-accent-900)" : "var(--marker-tint)",
          clsFg: r.cls === "core" ? "var(--color-accent)" : "var(--color-neutral-300)",
          cover: live ? on + " of " + bs.length : "not built",
          coverFg: !live ? "var(--color-neutral-500)" : on === bs.length ? "var(--st-ok)" : on === 0 ? "var(--st-risk)" : "var(--st-warn)"
        };
      }),
      enRulesN: String(rules.length),
      enRulesLiveN: String(IMPLEMENTED_RULE_IDS.size),
      enRulesShow: s.enRulesOpen ? "block" : "none",
      enRulesCaret: s.enRulesOpen ? "ph-caret-down" : "ph-caret-right",
      enRulesToggle: () => this.setState((p) => ({ enRulesOpen: !p.enRulesOpen }))
    };
  },

  anomalyDetail(a) {
    if (a.live) return this.anomalyDetailLive(a);
    return {
      module: "Energy", icon: "ph-lightning", tone: a.tone,
      title: a.type + " — " + a.asset + ", " + a.building,
      meta: a.status + " · active " + a.days + " days · annualised impact " + a.impact,
      body: "Detected by the daily 03:00 scan and translated to cost at the current contracted tariff of 28.4p/kWh. " + (a.status === "New" ? "No action taken yet — this is display-only output until you choose to inspect, at which point a work order draft is created." : "Already acknowledged; the engine continues to monitor and will re-alert if the deviation widens."),
      fields: [
        { l: "Building", v: a.building },
        { l: "Asset / circuit", v: a.asset },
        { l: "Anomaly type", v: a.type },
        { l: "Annualised cost", v: a.impact + " at 28.4p/kWh" },
        { l: "Days active", v: a.days + " days" },
        { l: "Status", v: a.status, editable: true },
        { l: "Action", v: "Create inspection work order", editable: true }
      ],
      chain: [
        { a: "Orchestrator", t: "Daily energy scan 03:00 → intent: energy-intelligence-engine" },
        { a: "Planner", t: "Fetch MeterReading → compute EUI → run 13 anomaly rules → translate to £ → check WO threshold" },
        { a: "Worker", t: "Half-hourly readings aggregated. Sub-meter attribution " + (a.asset === "Whole building" ? "unavailable — building-level metering only, cause inferred" : "available at asset level") + "." },
        { a: "Quality", t: "Not fired — display-only. The gate applies only if the anomaly auto-dispatches a work order." }
      ],
      refinement: "Two other buildings show a smaller version of this signature. Run a portfolio-wide audit of the same rule?",
      actions: ["Inspect now", "Monitor", "Override as expected"]
    };
  },

  // Live anomaly: every line below is a real field or a real fact about the code that
  // produced it (engines/energy/anomalies.py, the daily 08:00 energy_anomaly_scan cron in
  // worker.py) — nothing here is invented to fill the shape the seed used.
  anomalyDetailLive(a) {
    return {
      module: "Energy", icon: "ph-lightning", tone: a.tone,
      title: a.type + " — " + a.asset + ", " + a.building,
      meta: a.status + (a.days != null ? " · detected " + a.days + " day" + (a.days === 1 ? "" : "s") + " ago" : "") + " · annualised impact " + a.impact,
      body: "Detected by the daily 08:00 energy-anomaly scan and translated to cost at the meter's contracted tariff. " +
        (a.assetResolved ? "" : a.meterType ? "No specific asset is attributed — the reading comes off " + (/^[aeiou]/i.test(a.meterType) ? "an " : "a ") + a.meterType + " meter with no sub-meter or BMS point resolved behind it. " : "No asset or meter is resolved for this anomaly yet. ") +
        (a.pmAction ? "Recorded action: " + a.pmAction + (a.pmReason ? " — " + a.pmReason : "") + "." : "No action taken yet — this is display-only until you choose to act."),
      fields: [
        { l: "Building", v: a.building },
        { l: "Asset / meter", v: a.asset },
        { l: "Anomaly type", v: a.type },
        { l: "Metric", v: a.metricPct != null ? a.metricPct + "% of baseline" : "—" },
        { l: "Annualised cost", v: a.impact },
        { l: "Excess energy", v: a.excessKwh != null ? Math.round(a.excessKwh).toLocaleString() + " kWh/yr" : "—" },
        { l: "Detected", v: a.detectedAt ? new Date(a.detectedAt).toLocaleString() : "—" },
        { l: "Status", v: a.status }
      ],
      chain: [
        { a: "Scheduler", t: "energy_anomaly_scan · daily 08:00 · scan_all_active_meters()" },
        { a: "Detector", t: (a.ruleId ? "Rule “" + a.ruleId + "” — " : "") + a.type + ", evaluated against the meter's own reading history" },
        { a: "Worker", t: a.assetResolved ? "Resolved to " + a.asset + " via plenum_cafm.equipment" : "No equipment resolved — " + (a.meterType ? "meter-level reading only" : "no meter or asset on record") },
        { a: "Quality", t: "Not fired — display-only. Nothing dispatches automatically from an anomaly." }
      ],
      refinement: "Ask the orchestrator to check every open anomaly of this type across the portfolio.",
      actions: ["Inspect now", "Monitor", "Override as expected"]
    };
  }
};
