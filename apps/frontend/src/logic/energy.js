// energy — energy scope, ratings, buildings list and the investigate conversation.
// Methods are mixed into HoistraLogic.prototype; `this` is the controller.
import { BUILDINGS, PACKS, CC_OF, ENC, EN_ATTRS, EN_PROFILE, EN_RATINGS, ratingState, ENC_MIXED_AVAIL, ENC_MIXED_HELD, t } from './constants.js';
import { HOISTRA_EN } from '../data/hoistra-energy.js';

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
    const bs = BUILDINGS.filter((b) => sc.sel.indexOf(b.cc) > -1);
    const m2 = (b) => parseInt(String(b.area).replace(/[^0-9]/g, ""), 10) * 0.0929;
    const area = bs.reduce((q, b) => q + m2(b), 0);
    const eui = area ? bs.reduce((q, b) => q + b.euiN * m2(b), 0) / area : 0;
    const bench = area ? bs.reduce((q, b) => q + b.benchN * m2(b), 0) / area : 0;
    const excess = bs.reduce((q, b) => q + Math.max(0, b.euiN - b.benchN) * m2(b) * (ENC[b.cc] || ENC.UK).tariff, 0);
    const D = this.D();
    const anoms = D.anomalies.filter((a) => sc.sel.indexOf(CC_OF[a.building] || "UK") > -1);
    const anomSum = anoms.reduce((q, a) => q + parseInt(a.impact.replace(/[^0-9]/g, ""), 10), 0);
    const cc0 = sc.sel[0];
    const P = PACKS[cc0] || PACKS.UK;
    const E = ENC[cc0] || ENC.UK;
    const money = (n) => "£" + (n >= 1000 ? Math.round(n / 1000) + "k" : Math.round(n));
    const delta = bench ? Math.round(((eui - bench) / bench) * 100) : 0;
    // Ratings follow a country picker inside the section; it falls back to the
    // first market in scope whenever the previous pick drops out of scope.
    const rcc = sc.single ? cc0 : (sc.sel.indexOf(s.enRatingCc) > -1 ? s.enRatingCc : cc0);

    return {
      isEnergy: s.view === "module" && s.module === "energy",
      enChips: [{ cc: null, label: "All countries", flag: "" }].concat(sc.all.map((cc) => ({ cc: cc, label: PACKS[cc].name, flag: PACKS[cc].flag }))).map((c) => {
        const on = c.cc ? (s.eScope || []).indexOf(c.cc) > -1 : sc.isAll;
        return {
          label: c.flag ? c.flag + " " + c.label : c.label,
          n: String(c.cc ? BUILDINGS.filter((b) => b.cc === c.cc).length : BUILDINGS.length),
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
      enFidelityNote: sc.single
        ? (cc0 === "AE"
            ? "No operational standard — scored against a rolling portfolio benchmark of 228 kWh/m²/yr. "
            : "Benchmarked against " + P.std + " (" + P.note + "). ")
          + "Data arrives by " + E.routes.join(" and ") + " — " + E.grain + ". Tariff " + E.tariffLabel + "."
        : sc.sel.length + " markets in scope with " + sc.sel.map((cc) => (ENC[cc] || ENC.UK).routes.length).reduce((a, b) => a + b, 0) + " different data routes and " + sc.sel.length + " different benchmark bases. Only metrics that survive the difference are shown; the rest are named below rather than approximated.",

      enScopeCards: [
        { l: "Buildings in scope", v: String(bs.length), s: sc.single ? PACKS[cc0].flag + " " + PACKS[cc0].name : sc.sel.map((cc) => PACKS[cc].flag).join(" ") + " " + sc.sel.length + " markets", tone: "ok" },
        { l: "EUI, area weighted", v: Math.round(eui) + "", s: (delta > 0 ? "+" : "") + delta + "% against " + (sc.single ? (cc0 === "AE" ? "the rolling portfolio benchmark" : P.std) : "each building's own pack"), tone: delta > 8 ? "risk" : delta > 0 ? "warn" : "ok" },
        { l: "Excess cost", v: money(excess), s: sc.single ? "per year at " + E.tariffLabel : "per year, local tariffs converted to GBP", tone: "risk" },
        { l: "Anomalies in scope", v: String(anoms.length), s: money(anomSum) + " annualised impact", tone: anoms.length > 4 ? "warn" : "ok" }
      ].map((c) => ({ l: c.l, v: c.v, s: c.s, color: t(c.tone).color })),

      enAvail: (sc.single ? E.avail : ENC_MIXED_AVAIL).map((x) => ({ label: x })),
      enHeld: (sc.single ? E.held : ENC_MIXED_HELD).map((x) => ({ label: x })),
      enRoutes: sc.sel.map((cc) => ({
        label: PACKS[cc].flag + " " + PACKS[cc].name,
        route: (ENC[cc] || ENC.UK).routes.join(" · "),
        grain: (ENC[cc] || ENC.UK).grain,
        n: BUILDINGS.filter((b) => b.cc === cc).length + " buildings"
      })),
      enHeldTitle: sc.single ? "Limits at this scope" : "Not available across a mixed portfolio",
      enAvailTitle: sc.single ? "Available at this scope" : "Available across every market in scope",

      enMatrixCols: "168px repeat(" + sc.sel.length + ", minmax(200px,1fr))",
      enMatrixHead: sc.sel.map((cc) => { const n = BUILDINGS.filter((b) => b.cc === cc).length; return { label: PACKS[cc].flag + " " + PACKS[cc].name, n: n + (n === 1 ? " building" : " buildings") }; }),
      enSideTitle: sc.single ? (cc0 === "AE" ? "EUI vs rolling portfolio benchmark" : "EUI vs " + P.std) : "EUI vs each building's own pack",
      enSideFoot: sc.single
        ? "Bar length is EUI against " + (cc0 === "AE" ? "the 228 kWh/m²/yr rolling portfolio benchmark" : "the " + P.std + " reference") + ". Over-benchmark buildings carry the " + money(excess) + " gap at " + E.tariffLabel + "."
        : "Bar length is EUI against each building's own country pack. The " + money(excess) + " gap is summed at local tariffs and converted to GBP; the bars are not comparable across markets.",
      enAsks: (sc.single ? ({
        UK: ["Why did Bishopsgate spike on Saturday?", "Which buildings miss EPC B by 2031?", "Rank UK buildings by cost per m²"],
        US: ["Where does Northgate sit against its LL97 cap?", "What lifts the Energy Star score to 75?", "Is Con Edison CMD live on every account?"],
        AE: ["Which chillers drift against cooling degree days?", "What does DEWA bill that the BMS cannot see?", "Rank Marina Heights plant by RTh per m²"],
        SG: ["Is the BCA submission consistent with the retailer feed?", "Why did Raffles Link AHU-2 drift?", "What does the retail contract say about data at renewal?"]
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
      enRatings: (EN_RATINGS[rcc] || []).map((r) => {
        const st = ratingState(r);
        return {
          l: r.l, v: st.ok ? r.v : "—", s: st.ok ? r.s : "not shown until 3 months have landed",
          color: st.ok ? t(r.tone).color : "var(--color-neutral-500)",
          badge: st.badge, badgeFg: st.fg, confShow: st.show, confPct: st.conf + "%", confFg: st.fg
        };
      })
    };
  },


  /* Investigation. Opens a drawer and plays four stages against the graph:
     retrieve → findings → cause → actions. Logged as a session like any task. */
  investigate(kind, o) {
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


  /* Building-centric energy list: one row per building under its market,
     EUI against its own pack first, its anomalies underneath on open. */
  enBuildingVals(s) {
    const sc = this.enScope(s);
    const D = this.D();
    const f = s.filter || "All";
    const money = (n) => "£" + (n >= 1000 ? Math.round(n / 1000) + "k" : Math.round(n));
    const anomHit = (a) => f === "New" ? a.status === "New" : f === "Above £20k" ? parseInt(a.impact.replace(/[^0-9]/g, ""), 10) > 20000 : true;
    const groups = [];
    let shown = 0, total = 0;
    sc.sel.forEach((cc) => {
      const P = PACKS[cc];
      const list = BUILDINGS.filter((B) => B.cc === cc).map((B) => {
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
      shown += list.length;
      const gExcess = list.reduce((q, x) => q + x.excess, 0);
      const gAnom = list.reduce((q, x) => q + x.anomSum, 0);
      groups.push({
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
    return {
      enGroups: groups,
      enListSummary: shown + " of " + total + " buildings" + (f === "All" ? "" : " · filter: " + f),
      enListEmpty: groups.length ? "none" : "block",
      isNotEnergy: !(s.view === "module" && s.module === "energy")
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
    // Rule coverage for the scope: how many buildings in scope can arm each rule.
    const bs = BUILDINGS.filter((b) => b0.sel.indexOf(b.cc) > -1);
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

      enRules: rules.map((r) => {
        const on = bs.filter((b) => (EN.armed(b.gran, b.route).find((x) => x.id === r.id) || {}).on).length;
        return {
          name: r.name, test: r.test, needs: r.needs,
          cls: r.cls === "core" ? "core" : "added",
          clsBg: r.cls === "core" ? "var(--color-accent-900)" : "var(--marker-tint)",
          clsFg: r.cls === "core" ? "var(--color-accent)" : "var(--color-neutral-300)",
          cover: on + " of " + bs.length,
          coverFg: on === bs.length ? "var(--st-ok)" : on === 0 ? "var(--st-risk)" : "var(--st-warn)"
        };
      }),
      enRulesN: String(rules.length),
      enRulesShow: s.enRulesOpen ? "block" : "none",
      enRulesCaret: s.enRulesOpen ? "ph-caret-down" : "ph-caret-right",
      enRulesToggle: () => this.setState((p) => ({ enRulesOpen: !p.enRulesOpen }))
    };
  },

  anomalyDetail(a) {
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
  }
};
