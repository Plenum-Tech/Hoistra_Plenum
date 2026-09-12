// energy — energy scope, ratings, buildings list and the investigate conversation.
//
// Live-or-seed, same shape as compliance/vendors/buildings: buildings come from
// buildingsLive.js's bldData() (bldIsLive() says which), open anomalies from
// energyLive.js's enAnomalies() once energyLoad() has answered. The seed
// (src/logic/constants.js BUILDINGS, src/data/hoistway-data.js D.anomalies) is the
// fallback while the backend has not answered, or has not answered yet for a given
// building (a live building with no EUI reading shows "—", never a fabricated number).
//
// Methods are mixed into HoistraLogic.prototype; `this` is the controller.
import { BUILDINGS, PACKS, CC_OF, ENC, EN_ATTRS, EN_PROFILE, EN_RATINGS, ENC_MIXED_AVAIL, ENC_MIXED_HELD, t } from './constants.js';
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
     means only what the markets share survives. */
  enScope(s) {
    const all = ["UK", "US", "AE", "SG"];
    const sel = (s.eScope || []).length ? s.eScope : all;
    return { all: all, sel: sel, single: sel.length === 1, isAll: !(s.eScope || []).length };
  },

  enVals(s) {
    const sc = this.enScope(s);
    const isLive = this.bldIsLive();
    // "All countries" means the whole portfolio, not "whichever of these four codes
    // matches" — a live building with no country on record is still in scope until a
    // specific market is picked, at which point it honestly drops out of that market.
    const allBuildings = isLive ? this.bldData() : BUILDINGS;
    const bs = sc.isAll ? allBuildings : allBuildings.filter((b) => sc.sel.indexOf(b.cc) > -1);
    const hasEui = (b) => typeof b.euiN === "number" && typeof b.benchN === "number";
    const m2 = (b) => isLive ? (typeof b.areaM2 === "number" ? b.areaM2 : 0) : parseInt(String(b.area).replace(/[^0-9]/g, ""), 10) * 0.0929;
    const measured = bs.filter(hasEui);
    const area = measured.reduce((q, b) => q + m2(b), 0);
    const eui = area ? measured.reduce((q, b) => q + b.euiN * m2(b), 0) / area : null;
    const bench = area ? measured.reduce((q, b) => q + b.benchN * m2(b), 0) / area : null;
    const excess = measured.reduce((q, b) => q + Math.max(0, b.euiN - b.benchN) * m2(b) * (ENC[b.cc] || ENC.UK).tariff, 0);

    const allAnoms = isLive ? this.enAnomalies() : this.D().anomalies;
    const anomCc = (a) => isLive ? a.cc : (CC_OF[a.building] || "UK");
    const anoms = sc.isAll ? allAnoms : allAnoms.filter((a) => sc.sel.indexOf(anomCc(a)) > -1);
    const anomSum = anoms.reduce((q, a) => q + impactNum(a), 0);
    const cc0 = sc.sel[0];
    const P = PACKS[cc0] || PACKS.UK;
    const E = ENC[cc0] || ENC.UK;
    const money = moneyGBP;
    const delta = bench ? Math.round(((eui - bench) / bench) * 100) : 0;
    // Ratings follow a country picker inside the section; it falls back to the
    // first market in scope whenever the previous pick drops out of scope.
    const rcc = sc.single ? cc0 : (sc.sel.indexOf(s.enRatingCc) > -1 ? s.enRatingCc : cc0);
    const unattributed = isLive ? allBuildings.filter((b) => !b.cc || b.cc === "—").length : 0;

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
          + "Data arrives by " + E.routes.join(" and ") + " — " + E.grain + ". Tariff " + E.tariffLabel + "."
        : sc.sel.length + " markets in scope with " + sc.sel.map((cc) => (ENC[cc] || ENC.UK).routes.length).reduce((a, b) => a + b, 0) + " different data routes and " + sc.sel.length + " different benchmark bases. Only metrics that survive the difference are shown; the rest are named below rather than approximated.")
        + (unattributed ? " " + unattributed + " of " + allBuildings.length + " buildings on record have no country attributed yet, so the per-market figures above undercount the portfolio." : ""),

      enScopeCards: [
        { l: "Buildings in scope", v: String(bs.length), s: sc.single ? PACKS[cc0].flag + " " + PACKS[cc0].name : sc.sel.map((cc) => PACKS[cc].flag).join(" ") + " " + sc.sel.length + " markets", tone: "ok" },
        { l: "EUI, area weighted", v: eui === null ? "—" : Math.round(eui) + "",
          s: eui === null ? (isLive ? "no EUI reading on record for any building in scope yet" : "—")
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
      enMatrixHead: sc.sel.map((cc) => { const n = allBuildings.filter((b) => b.cc === cc).length; return { label: PACKS[cc].flag + " " + PACKS[cc].name, n: n + (n === 1 ? " building" : " buildings") }; }),
      enSideTitle: sc.single ? (cc0 === "AE" ? "EUI vs rolling portfolio benchmark" : "EUI vs " + P.std) : "EUI vs each building's own pack",
      enSideFoot: sc.single
        ? "Bar length is EUI against " + (cc0 === "AE" ? "the 228 kWh/m²/yr rolling portfolio benchmark" : "the " + P.std + " reference") + ". Over-benchmark buildings carry the " + money(excess) + " gap at " + E.tariffLabel + "."
        : "Bar length is EUI against each building's own country pack. The " + money(excess) + " gap is summed at local tariffs and converted to GBP; the bars are not comparable across markets.",
      enAsks: (sc.single ? ({
        UK: ["Which UK buildings sit over their EUI benchmark?", "Which buildings miss EPC B by 2031?", "Rank UK buildings by cost per m²"],
        US: ["Which US buildings sit closest to their LL97 cap?", "What would lift the Energy Star position?", "Is the Green Button feed live on every account?"],
        AE: ["Which chillers drift against cooling degree days?", "What does the DEWA bill show that the BMS cannot see?", "Rank UAE buildings by RT per m²"],
        SG: ["Is the BCA submission consistent with the retailer feed?", "Which building drifted furthest from its own baseline?", "What does the retail contract say about data at renewal?"]
      })[cc0] : ["Which markets drive the excess cost?", "Which buildings are worst against their own pack?", "Where does the data route limit what I can see?"]),
      enMatrixRows: EN_ATTRS.map((a, i) => ({
        section: i === 0 || EN_ATTRS[i - 1][0] !== a[0] ? a[0] : "",
        sectionShow: i === 0 || EN_ATTRS[i - 1][0] !== a[0] ? "flex" : "none",
        label: a[1],
        cells: sc.sel.map((cc) => ({ v: (EN_PROFILE[cc] || {})[a[2]] || "—" }))
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
      return (EN_RATINGS[cc] || []).map((r) => ({
        l: r.l, v: "…", s: "loading from the ratings engine", color: "var(--color-neutral-500)",
        badge: "", badgeFg: "var(--color-neutral-500)", confShow: "none", confPct: "0%", confFg: "var(--color-neutral-500)"
      }));
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

  /* Investigation. A live building or anomaly asks the real orchestrator (askScoped,
     same as Compliance/Vendors/Buildings) and answers in the Energy page's side dock. A
     seed one (Assets, Maintenance, or the Energy page before the backend has answered)
     keeps the scripted four-stage walk below — nothing here reads real data, so it stays
     out of scope for this pass rather than half-converted. */
  investigate(kind, o) {
    if (o && o.live) return this.investigateLive(kind, o);
    const EN = HOISTRA_EN;
    if (!EN) return;
    const fill = (str, m) => String(str).replace(/\{(\w+)\}/g, (_, k) => (m[k] != null ? m[k] : "{" + k + "}"));
    let tpl, m, title, sub;
    if (kind === "anomaly") {
      const b = BUILDINGS.find((x) => x.name === o.building) || {};
      const vendor = CC_OF[o.building] === "AE" ? "Gulf Cooling" : CC_OF[o.building] === "US" ? "Metro Facilities" : CC_OF[o.building] === "SG" ? "Sembawang M&E" : "Apex M&E";
      m = { asset: o.asset, building: o.building, impact: o.impact, vendor: vendor };
      tpl = EN.T[o.type] || EN.T["Baseline drift"];
      title = o.asset + " · " + o.building;
      sub = o.type + " · " + o.impact + " annualised · " + (b.route || "meter feed") + " · " + (b.gran || "");
    } else {
      const b = BUILDINGS.find((x) => x.name === o.name) || {};
      const D = this.D();
      const an = D.anomalies.filter((a) => a.building === o.name && a.status !== "Resolved");
      const anomSum = an.reduce((q, a) => q + parseInt(a.impact.replace(/[^0-9]/g, ""), 10), 0);
      const m2 = parseInt(String(b.area || "0").replace(/[^0-9]/g, ""), 10) * 0.0929;
      const excessN = Math.max(0, (b.euiN || 0) - (b.benchN || 0)) * m2 * ((ENC[b.cc] || ENC.UK).tariff);
      const money = (n) => "£" + (n >= 1000 ? Math.round(n / 1000) + "k" : Math.round(n));
      const over = (b.euiN || 0) > (b.benchN || 0);
      const delta = b.benchN ? Math.round((((b.euiN || 0) - b.benchN) / b.benchN) * 100) : 0;
      m = {
        building: o.name, route: (b.route || "meter feed") + " · " + (b.gran || ""), nAnom: String(an.length),
        eui: (b.euiN || 0) + " kWh/m²/yr", bench: (b.benchN || 0) + " kWh/m²/yr",
        delta: (delta > 0 ? "+" : "") + delta + "%", excess: over ? money(excessN) : "£0",
        anomExcess: money(anomSum), anomShare: excessN > 0 ? Math.min(100, Math.round((anomSum / excessN) * 100)) + "%" : "none of the gap — the building is under reference",
        hours: b.use === "Commercial" ? "06:00–22:00 vs 08:00–18:00 assumed" : b.use === "Hospital" ? "24h vs 24h assumed" : b.use === "Retail" || b.use === "Mixed" ? "07:00–23:00 vs 09:00–21:00 assumed" : "as assumed",
        plant: b.floors > 20 ? "chillers 2009 · AHUs 2009 · L1: 4 assets" : "boilers 2004 · AHUs 2012 · L1: 2 assets",
        rating: b.cc === "UK" ? "EPC D · improvement report lists LED and BMS optimisation" : b.cc === "US" ? "Energy Star 71 · LL84 filed" : b.cc === "SG" ? "BCA return filed · no Green Mark" : "no operational rating scheme",
        hoursFinding: b.use === "Hospital" ? "Operating hours match the pack assumption; the gap is not an hours question." : "The building keeps longer hours than its pack assumes. Part of the gap is a benchmark-fit question, not waste.",
        plantFinding: b.floors > 20 ? "Central plant is 2009 vintage; the two L1 chillers are past mid-life and dominate the load." : "Boilers are 2004 vintage, well past design life; the EPC improvement report already names the measures.",
        cause: over
          ? "Structural in the main — plant age and hours — with " + money(anomSum) + " of anomalies on top. Fixing anomalies narrows the gap; it does not close it."
          : "Under reference. The open anomalies are the only cost on the table; there is no structural gap to fund.",
        capex: b.floors > 20 ? "chiller replacement or sequencing upgrade · payback case from the graph" : "LED and BMS optimisation from the EPC report · payback case from the graph"
      };
      tpl = EN.B;
      title = o.name;
      sub = "EUI vs " + ((PACKS[b.cc] || PACKS.UK).std === "NA" ? "rolling portfolio benchmark" : (PACKS[b.cc] || PACKS.UK).std) + " · " + m.delta + " · " + m.excess + " a year";
    }
    const query = kind === "anomaly"
      ? "Why is " + o.asset + " at " + o.building + " showing a " + o.type.toLowerCase() + " worth " + o.impact + " a year, and what should I do about it?"
      : "Why is " + o.name + " at " + m.eui + " against a reference of " + m.bench + ", and how much of that gap can I act on?";
    const inv = {
      kind: kind, title: title, sub: sub, query: query, replies: [],
      sources: tpl.sources.map((r) => ({ tbl: r[0], what: fill(r[1], m), n: fill(r[2], m) })),
      findings: tpl.findings.map((f) => ({ t: fill(f.t, m), src: f.src, conf: f.conf })),
      cause: fill(tpl.cause, m), costLine: fill(tpl.costLine, m),
      actions: tpl.actions.map((a) => ({ l: fill(a.l, m), s: fill(a.s, m), k: a.k, done: false }))
    };
    // Inconclusive evidence escalates: if the strongest finding is under 85% or a
    // record the contract requires is missing, the FM is asked and an inspection
    // is planned rather than a cause asserted.
    const maxConf = Math.max.apply(null, inv.findings.map((f) => f.conf));
    const hasGap = inv.findings.some((f) => /gap/.test(f.src));
    inv.escalate = maxConf < 85 || hasGap;
    inv.escText = hasGap
      ? "A record the contract requires is missing, so the cause rests on inference. The FM lead is asked to confirm and the missing document is requested before anything is claimed."
      : "No finding clears 85% confidence. Rather than assert a cause, the orchestrator asks the FM lead why and books an inspection to settle it.";
    if (inv.escalate && !inv.actions.some((a) => a.k === "email")) {
      inv.actions.unshift({ l: "Ask the FM lead why", s: "draft email with the evidence attached · reply closes or reopens the case", k: "email", done: false });
    }
    if (inv.escalate && !inv.actions.some((a) => a.k === "wo" || a.k === "inspect")) {
      inv.actions.splice(1, 0, { l: "Plan an inspection", s: "PPM-linked visit · findings written back to the asset record", k: "inspect", done: false });
    }
    const chain = [
      { a: "Orchestrator", t: "Intent: investigate " + title.toLowerCase() + " · why is it where it is, and what can be done" },
      { a: "Planner", t: "Walk " + inv.sources.length + " tables: " + inv.sources.map((r) => r.tbl).join(", ") },
      { a: "Worker", t: "Retrieving rows, weighing each finding by source and confidence, flagging any record that should exist and does not" },
      { a: "Quality", t: inv.escalate ? "Evidence inconclusive or a record missing — escalation armed: ask the FM, plan an inspection" : "Cause supported at ≥85% — actions drafted, none executed until approved" }
    ];
    clearInterval(this._invTick);
    this.orch("Investigate", title, chain);
    this.setState({ inv: inv, invStage: 0, invSrcDone: 0, flow: "investigate", flowDone: "", detail: null });
    // Sources tick in one by one, then each stage lands.
    this._invTick = setInterval(() => {
      this.setState((p) => {
        if (!p.inv) { clearInterval(this._invTick); return {}; }
        if (p.invSrcDone < p.inv.sources.length) return { invSrcDone: p.invSrcDone + 1 };
        const n = p.invStage + 1;
        if (n >= 3) clearInterval(this._invTick);
        return { invStage: Math.min(n, 3) };
      });
    }, 420);
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
    return this.bldIsLive() ? this.enBuildingValsLive(s) : this.enBuildingValsSeed(s);
  },

  enBuildingValsSeed(s) {
    const sc = this.enScope(s);
    const D = this.D();
    const f = s.filter || "All";
    const query = String(s.enBldQuery || "").trim().toLowerCase();
    const money = (n) => "£" + (n >= 1000 ? Math.round(n / 1000) + "k" : Math.round(n));
    const anomHit = (a) => f === "New" ? a.status === "New" : f === "Above £20k" ? parseInt(a.impact.replace(/[^0-9]/g, ""), 10) > 20000 : true;
    const rawGroups = [];
    let total = 0;
    sc.sel.forEach((cc) => {
      const P = PACKS[cc];
      const list = BUILDINGS.filter((B) => B.cc === cc)
        .filter((B) => !query || B.name.toLowerCase().indexOf(query) > -1)
        .map((B) => {
        const b = { name: B.name, eui: B.euiN, bench: B.benchN };
        const m2 = (parseInt(String(B.area || "100,000").replace(/[^0-9]/g, ""), 10)) * 0.0929;
        const excess = Math.max(0, b.eui - b.bench) * m2 * (ENC[cc] || ENC.UK).tariff;
        const all = D.anomalies.filter((a) => a.building === b.name && a.status !== "Resolved");
        const anoms = all.filter(anomHit);
        const anomSum = anoms.reduce((q, a) => q + parseInt(a.impact.replace(/[^0-9]/g, ""), 10), 0);
        return { b: b, B: B, excess: excess, all: all, anoms: anoms, anomSum: anomSum };
      }).filter((x) => {
        if (f === "Over benchmark") return x.b.eui > x.b.bench;
        if (f === "With anomalies") return x.all.length > 0;
        if (f === "New" || f === "Above £20k") return x.anoms.length > 0;
        return true;
      }).sort((p, q) => (q.excess + q.anomSum) - (p.excess + p.anomSum));
      total += BUILDINGS.filter((B) => B.cc === cc).length;
      if (!list.length) return;
      const gExcess = list.reduce((q, x) => q + x.excess, 0);
      const gAnom = list.reduce((q, x) => q + x.anomSum, 0);
      rawGroups.push({
        label: P.flag + " " + P.name,
        std: P.std === "NA" ? "rolling portfolio benchmark" : P.std,
        meta: list.length + (list.length === 1 ? " building" : " buildings") + " · " + money(gExcess) + " above reference · " + money(gAnom) + " in anomalies",
        buildings: list.map((x) => {
          const b = x.b, B = x.B;
          const delta = Math.round(((b.eui - b.bench) / b.bench) * 100);
          const over = b.eui > b.bench;
          const open = s.enOpenB === b.name;
          const tone = b.eui > b.bench + 10 ? "risk" : over ? "warn" : "ok";
          return {
            name: b.name, eui: b.eui + " kWh/m²", bench: "ref " + b.bench,
            delta: (delta > 0 ? "+" : "") + delta + "%", deltaFg: t(tone).color,
            barPct: Math.min(100, Math.round((b.eui / 260) * 100)) + "%", barColor: t(tone).color,
            refPct: Math.min(100, Math.round((b.bench / 260) * 100)) + "%",
            route: (B.route || "meter feed") + " · " + (B.gran || "building-level"),
            granFg: B.gran === "sub-metered" ? "var(--color-neutral-500)" : "var(--st-dormant)",
            excess: over ? money(x.excess) : "at or under reference", excessFg: over ? "var(--color-text)" : "var(--st-ok)",
            anomN: x.all.length ? x.all.length + (x.all.length === 1 ? " anomaly" : " anomalies") + " · " + money(x.all.reduce((q, a) => q + parseInt(a.impact.replace(/[^0-9]/g, ""), 10), 0)) : "no open anomalies",
            anomFg: x.all.length ? "var(--st-warn)" : "var(--color-neutral-500)",
            caret: open ? "ph-caret-down" : "ph-caret-right",
            openShow: open ? "block" : "none",
            bg: open ? "var(--color-neutral-900)" : "var(--color-surface)",
            toggle: () => this.setState((p) => ({ enOpenB: p.enOpenB === b.name ? null : b.name })),
            investigate: (e) => { if (e && e.stopPropagation) e.stopPropagation(); this.investigate("building", b); },
            emptyShow: x.anoms.length ? "none" : "block",
            emptyText: x.all.length ? "No anomalies match the current filter." : "No open anomalies. Any gap above reference here is structural — investigate the building to size the capex case.",
            anomalies: x.anoms.map((a) => ({
              asset: a.asset, type: a.type, impact: a.impact, status: a.status, days: a.days + " days active",
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
    return {
      enGroups: paged.groups,
      enListSummary: (paged.flatTotal ? pageStart + "–" + pageEnd : "0") + " of " + total + " buildings"
        + (f === "All" ? "" : " · filter: " + f) + (query ? " · matching “" + s.enBldQuery + "”" : ""),
      enListEmpty: rawGroups.length ? "none" : "block",
      enBldQuery: s.enBldQuery || "",
      setEnBldQuery: (e) => this.setState({ enBldQuery: e.target.value, enBldPage: 0 }),
      enBldQueryShow: total > ENERGY_PAGE_SIZE ? "flex" : "none",
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

  // Live building rows have no fixed per-country membership to iterate — most buildings
  // in this database carry no country at all — so the buckets are the scope's markets
  // plus "—" (unattributed) rather than assumed to be exactly the four packs.
  enBuildingValsLive(s) {
    const sc = this.enScope(s);
    const f = s.filter || "All";
    const query = String(s.enBldQuery || "").trim().toLowerCase();
    const money = moneyGBP;
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
        const excess = hasEui ? Math.max(0, b.euiN - b.benchN) * m2 * (ENC[cc] || ENC.UK).tariff : 0;
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
            anomN: x.all.length ? x.all.length + (x.all.length === 1 ? " anomaly" : " anomalies") + " · " + money(x.all.reduce((q, a) => q + impactNum(a), 0)) : "no open anomalies",
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
    return {
      enGroups: paged.groups,
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
    // Rule coverage for the scope: how many buildings in scope can arm each rule, from
    // the live Buildings table once it has loaded, the seed otherwise.
    const isLive = this.bldIsLive();
    const allBuildings = isLive ? this.bldData() : BUILDINGS;
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
