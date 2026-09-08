// renderVals — the view model — everything the templates read.
// Methods are mixed into HoistraLogic.prototype; `this` is the controller.
import { CADENCES, DAYS, CADENCE_LABEL, CADENCE_BADGE, ASSET_RISK, USE_TINT, BUILDINGS, GRAPH, GRAPH_EDGES, CHILD_OF_BUILDING, UNITS, NUM, SUB_OF, REGIONS, PACKS, ACTION_SPECS, CC, VP, PKG, MK, VENDOR_POOL, CRONS, TONE, t, MODULES } from './constants.js';
import { fmtTime, runwayTicks } from './complianceLive.js';

export const renderValsMethods = {
  renderVals() {
    const D = this.D();
    const s = this.state;
    if (!D) return {};

    const mkActions = (labels, ctx) => labels.map((l, i) => ({
      label: l, cls: i === 0 ? "btn-primary" : "btn-secondary",
      click: () => {
        const low = l.toLowerCase();
        if (low.includes("open compliance")) return this.openModule("compliance");
        if (low.includes("open energy")) return this.openModule("energy");
        if (low.includes("open vendor performance")) return this.openModule("vendors");
        if (low.includes("work the queue")) return this.setState({ queueOpen: true, view: "home" });
        if (low.includes("open vendor record")) return this.openModule("vendors");
        const subject = (detail && detail.title) || (ctx && ctx.title) || this.ctxLabel();
        const vendorName = (detail && detail.fields || []).reduce((a, f) => /vendor|contractor/i.test(f.l) ? f.v : a, "the responsible vendor");
        const spec = ACTION_SPECS.find((sp) => sp.m && sp.m.some((rx) => rx.test(low)));
        if (spec && spec.custom) {
          return this.orchWith(l, subject, spec.custom, { bkLocked: true, fSubject: subject, fVendor: vendorName });
        }
        if (spec) {
          const seed = {};
          (spec.inputs || []).forEach((f) => {
            seed[f.key] = f.seed !== undefined ? f.seed : (f.type === "date" ? "2026-09-16" : (f.type === "select" ? f.options[0] : ""));
          });
          if (spec.noMail) {
            this.orch(l, subject);
            return this.setState({ flow: null, fSubject: subject, fVendor: vendorName, flowDone: spec.done(seed, l, subject) });
          }
          return this.orchWith(l, subject, "inputs", { fSpec: spec.k, fiVals: seed, fLabel: l, fSubject: subject, fVendor: vendorName });
        }
        this.orch(l, subject);
      }
    }));

    const detail = s.detail;
    const modKey = s.module;
    const mod = modKey ? MODULES[modKey] : null;
    const answer = s.answerKey ? D.answers[s.answerKey] : null;
    const rep = s.reports.find((r) => r.key === s.reportKey) || null;
    const vpV = D.vendors.find((x) => x.id === s.vpVendor) || D.vendors[0];
    const vpR = VP.V[vpV.id] || null;
    // The rows ARE the score: sum them, then apply the accreditation cap.
    const vpScore = (id) => {
      const rec = VP.V[id];
      if (!rec) return { raw: 0, score: 0 };
      const sc = VP.scorecard(rec);
      const vend = D.vendors.find((x) => x.id === id) || {};
      return { raw: sc.raw, score: vend.accred === "Lapsed" ? Math.min(60, sc.raw) : sc.raw, rows: sc.rows };
    };
    const SC = vpR ? VP.scorecard(vpR) : { rows: [], raw: 0 };
    const score = vpR ? vpScore(vpV.id).score : 0;
    const repCad = {
      label: (rep && rep.cad) || CADENCES[0].label,
      last: (rep && rep.last) || CADENCES[0].last
    };
    const bkVendor = s.fVendor && s.fVendor !== "the responsible vendor" ? s.fVendor : "Apex Lifts";
    const spec0 = ACTION_SPECS.find((sp) => sp.k === s.fSpec) || null;
    const cc = this.ccModel();

    const vals = {
      tenant: "Planum Technologies",
      scopeLine: D.portfolio.buildings + " buildings · " + D.portfolio.area + " · 4 regulation packs",
      isHome: s.signedIn && s.view === "home", isAnswer: s.signedIn && s.view === "answer", isModule: s.signedIn && s.view === "module",
      queueOpen: s.queueOpen, paletteOpen: s.paletteOpen, detailOpen: !!detail,
      toggleQueue: () => this.setState((p) => ({ queueOpen: !p.queueOpen, acctOpen: false, paletteOpen: false, detail: null })),
      closeQueue: () => this.setState({ queueOpen: false }),
      queueCount: Math.max(D.decisions.length, CRONS.filter((c) => c.action && !s.cronsGone.includes(c.text)).length), query: s.query,
      chainOpen: s.chainOpen, chainIcon: s.chainOpen ? "ph-caret-down" : "ph-caret-right",
      toastOn: !!s.toast, toast: s.toast, askedQuery: s.askedQuery,
      themeIcon: s.dark ? "ph-sun" : "ph-moon",
      themeLabel: s.dark ? "Back to paper" : "Plant room / night shift",
      toggleTheme: () => this.toggleTheme(),
      goHome: () => this.setState({ view: "home", detail: null, queueOpen: false, navOpen: false }),

      signedIn: s.signedIn, gated: !s.signedIn,
      f1: { o: s.frame === 0 ? 1 : 0, y: s.frame === 0 ? "0px" : (s.frame === 1 ? "-10px" : "10px") },
      f2: { o: s.frame === 1 ? 1 : 0, y: s.frame === 1 ? "0px" : (s.frame === 2 ? "-10px" : "10px") },
      f3: { o: s.frame === 2 ? 1 : 0, y: s.frame === 2 ? "0px" : (s.frame === 3 ? "-10px" : "10px") },
      f4: { o: s.frame === 3 ? 1 : 0, y: s.frame === 3 ? "0px" : (s.frame === 0 ? "-10px" : "10px"), pe: s.frame === 3 ? "auto" : "none" },
      f4items: [
        { name: "NABERS data pack", sub: "Bishopsgate Tower", icon: "ph-file-text" },
        { name: "ESOS data pack", sub: "Portfolio · Phase 4", icon: "ph-file-text" },
        { name: "Vendor scorecard", sub: "Meridian Lifts Ltd", icon: "ph-chart-bar" }
      ].map((p) => ({ ...p, click: () => this.setState({ signedIn: true, view: "home" }) })),
      f2items: [
        { label: "3 certificates expired", dot: "var(--st-risk)" },
        { label: "1 asset failure detected", dot: "var(--st-warn)" },
        { label: "Service charge estimated", dot: "var(--st-ok)" }
      ],

      costHeads: [
        { head: "Assets", body: "Maintained to replace, not retain" },
        { head: "Compliance", body: "Hundreds of building and vendor certificates, chased by hand" },
        { head: "Vendors", body: "Performance buried in reports and contracts" },
        { head: "Energy & ESG", body: "Ratings tied to lettability, evidence audited" },
        { head: "Life safety", body: "Named accountability" },
        { head: "Service charge", body: "Tenants audit the recovery" }
      ],

      noiImpacts: [
        { label: "NOI", icon: "ph-currency-circle-dollar" },
        { label: "Ratings", icon: "ph-lightning" },
        { label: "Risk", icon: "ph-scales" },
        { label: "Insurance", icon: "ph-shield-check" },
        { label: "Asset value", icon: "ph-trend-up" }
      ],

      doesCards: [
        { n: "01", name: "One door in", body: "Contracts, framework agreements, purchase orders, invoices, certificates, asset registers and half-hourly meter data are ingested through a single door into an ontology built for property — not a generic data lake with property fields bolted on." },
        { n: "02", name: "Agents on every discipline", body: "Compliance, energy, vendor performance, assets, work orders. Each runs on its own cadence, reads the same graph, and writes its findings with a chain of thought you can open." },
        { n: "03", name: "Priced, not reported", body: "Every deviation and potential deviation carries a financial estimate, and the platform integrates with your finance software so the number sits against the real ledger rather than beside it." },
        { n: "04", name: "You are the authority", body: "The agents take the operating. You take accountability for the building as a whole system — its performance, its risk, its value — deciding where it matters most and running the NOI end to end, rather than clearing a queue of fires." }
      ],

      flowSteps: [
        { n: "01", name: "Hoist Graph", body: "The property knowledge graph. Contracts, asset registers, certificates, invoices and half-hourly meter data enter through one door; a RAG relationships agent links each cell to the clause that governs it.", bar: "var(--color-accent)", fg: "var(--color-text)", arrow: "block" },
        { n: "02", name: "Hoist Agents", body: "Compliance, energy, vendor, asset and work-order agents run against the graph on their own cadence and price what they find.", bar: "var(--color-accent)", fg: "var(--color-text)", arrow: "block" },
        { n: "03", name: "Hoisters", body: "Forward deployed engineers. We hoist buildings; Hoisters do the work — inside your operation, wiring the last feeds, resolving what the agents cannot, signing off the baseline.", bar: "var(--color-accent)", fg: "var(--color-text)", arrow: "block" },
        { n: "04", name: "Hoist Score", body: "How completely the portfolio is held in the graph. Coverage first, autonomy second: the score is what earns the agents more authority.", bar: "var(--st-ok)", fg: "var(--color-text)", arrow: "none" }
      ],
      flowOut: [
        { label: "NOI", pct: "86%" }, { label: "Autonomy", pct: "78%" }, { label: "Energy ratings", pct: "72%" },
        { label: "Safety", pct: "91%" }, { label: "Upskilling", pct: "64%" }
      ],
      coverageBands: [
        { range: "85 – 100", label: "Delegated", bg: "var(--color-accent)", fg: "var(--accent-ink)", border: "var(--color-accent)" },
        { range: "60 – 84", label: "Supervised", bg: "var(--color-accent-900)", fg: "var(--color-text)", border: "var(--color-accent-800)" },
        { range: "0 – 59", label: "Observed", bg: "transparent", fg: "var(--color-neutral-400)", border: "var(--color-divider)" }
      ],

      regPacks: [
        { country: "United Kingdom", body: "LOLER, EICR, Gas Safe CP12, asbestos re-inspection, L8 water. EUI benchmarked against CIBSE TM46." },
        { country: "United States", body: "Energy Star Portfolio Manager and ASHRAE Standard 100, with state and city benchmarking ordinances layered on top." },
        { country: "UAE", body: "No national benchmark, so the pack runs a rolling live comparison against every comparable building in your portfolio." },
        { country: "Singapore", body: "BCA Building Energy Benchmarking Report, with mandatory submission cycles tracked as obligations." }
      ],

      objections: [
        { q: "Is this another FM software?", a: "No. FM software records the work after someone decides to do it. Hoistra decides — it reads the portfolio, finds the exposure, prices it, and brings you the decision. It needs no cooperation from your FM provider, because it ingests the evidence directly rather than asking their system for a report." },
        { q: "Who is this meant for?", a: "The person accountable for NOI on a portfolio they do not operate day to day: asset and property managers, heads of real estate, owner-operators. Not the FM operative on site — Hoistra does not ask them to fill in a form." },
        { q: "Why do I need AI for this?", a: "Because the work is reading. A portfolio generates thousands of documents and millions of meter reads a month, and the exposure is always in the join between them: a lapsed certificate on the asset that is also burning 34% more energy than its peer group. No reporting layer finds that. Agents that read everything, every night, do." },
        { q: "Is my data secure and protected?", a: "Your Hoist Graph is single-tenant and scoped to the portfolios you hold. Meter consent is captured explicitly per MPAN and MPRN. Hoisters see only the buildings they are deployed to, and every read and write is logged with actor and timestamp in the Activity Log." },
        { q: "What happens to my existing systems?", a: "They stay. Hoistra ingests from your CMMS, finance system and document stores rather than replacing them, and writes back to finance. Nothing needs to be migrated before the first answer arrives." },
        { q: "How long before it is useful?", a: "The first Hoist Score lands during onboarding, on whatever has been ingested. Coverage grows from there, and the agents take on more as it does — you are not waiting on a completed data project to get the first decision." },
        { q: "What if the agents get it wrong?", a: "A Quality agent validates anything that carries consequence, and every finding opens to its full chain of thought — which source, which clause, which reading. Nothing dispatches on an agent's authority alone until you raise the autonomy level yourself." },
        { q: "Does this replace my team?", a: "It removes the reading, not the judgement. Teams that run on Hoistra spend their time on decisions and vendor negotiation instead of chasing certificates, which is why upskilling is one of the outcomes we hold ourselves to." }
      ].map((o, i) => {
        const open = s.openObj === i;
        return {
          q: o.q, a: o.a,
          fg: open ? "var(--color-accent)" : "var(--color-text)",
          icon: open ? "ph-minus" : "ph-plus",
          show: open ? "block" : "none",
          toggle: () => this.setState((p) => ({ openObj: p.openObj === i ? -1 : i }))
        };
      }),
      gateBlocks: [
        { name: "Hoist Graph", what: "The property knowledge graph", body: "Contracts, asset registers, certificates, invoices and half-hourly meter data, ingested through one door and linked cell to clause by a RAG relationships agent." },
        { name: "Hoist Score", what: "Ingestion coverage → autonomy", body: "How completely your portfolio is represented in the Hoist Graph. The score is what earns the agents more authority: coverage first, autonomy second." },
        { name: "Hoisters", what: "Forward-deployed engineers", body: "We hoist buildings. Hoisters do the work — they sit inside your operation, wire up the feeds, and hand over a portfolio the agents can already read." }
      ],
      email: s.email,
      setEmail: (e) => this.setState({ email: e.target.value }),
      signIn: () => this.setState({ signedIn: true, view: "home" }),
      gateKey: (e) => { if (e.key === "Enter") this.setState({ signedIn: true, view: "home" }); },
      signOut: () => this.setState({ signedIn: false, view: "home", role: "user", acctOpen: false, navOpen: false, queueOpen: false, detail: null }),

      /* Account menu. The admin view is a mode, not a page: switching into it
         leaves only the admin surfaces in the navigator, so a configuration
         session cannot be confused with reading a report. */
      acctOpen: s.acctOpen,
      toggleAcct: () => this.setState((p) => ({ acctOpen: !p.acctOpen })),
      closeAcct: () => this.setState({ acctOpen: false }),
      acctRole: s.role === "admin" ? "Admin view" : "User view",
      acctBg: s.role === "admin" ? "var(--color-accent)" : "var(--color-neutral-900)",
      acctFg: s.role === "admin" ? "var(--accent-ink)" : "var(--color-neutral-300)",
      acctEdge: s.role === "admin" ? "var(--color-accent)" : "var(--color-divider)",
      acctItems: [
        { label: "Pricing", icon: "ph-tag", click: () => this.setState({ acctOpen: false }, () => this.flash("Pricing and plan usage open in the billing workspace — seats, buildings hoisted and ingest volume.")) },
        { label: "Support", icon: "ph-lifebuoy", click: () => this.setState({ acctOpen: false }, () => this.flash("Support: a Hoister is on call for this portfolio. Every request carries the page and the graph state you were on.")) },
        { label: s.role === "admin" ? "User view" : "Admin view",
          icon: s.role === "admin" ? "ph-user-focus" : "ph-shield-star", tick: false,
          click: () => this.setState((p) => ({
            role: p.role === "admin" ? "user" : "admin",
            acctOpen: false,
            view: p.role === "admin" ? "home" : "buildings",
            navOpen: true, detail: null
          })) }
      ].map((a) => ({
        label: a.label, icon: a.icon, click: a.click,
        fg: a.tick ? "var(--color-accent)" : "var(--color-text)",
        iconFg: a.tick ? "var(--color-accent)" : "var(--color-neutral-500)",
        tickShow: a.tick ? "block" : "none"
      })),

      currencies: ["GBP", "USD", "AED", "SGD"].map((c) => ({
        label: { GBP: "£", USD: "$", AED: "AED", SGD: "S$" }[c],
        fg: c === s.currency ? "var(--accent-ink)" : "var(--color-neutral-500)",
        bg: c === s.currency ? "var(--color-accent)" : "transparent",
        pick: () => this.setState({ currency: c })
      })),

      customOpen: s.freq === "Custom",
      cFreq: s.cFreq, cDate: s.cDate, cTime: s.cTime,
      cFreqOpts: ["Every 15 min", "Every 30 min", "Hourly", "Every 6 hours", "Daily", "Weekly", "Monthly"],
      setCFreq: (e) => this.setState({ cFreq: e.target.value }),
      setCDate: (e) => this.setState({ cDate: e.target.value }),
      setCTime: (e) => this.setState({ cTime: e.target.value }),
      customSummary: s.cFreq + " from " + s.cDate + " at " + s.cTime,
      saveCustom: () => this.orch("Set schedule — " + s.cFreq + " at " + s.cTime, "Activity Log"),

      freqOpts: ["On demand", "30 min", "Hourly", "Nightly 02:00", "Custom"].map((o) => ({
        label: o,
        fg: o === s.freq ? "var(--color-accent)" : "var(--color-neutral-400)",
        border: o === s.freq ? "var(--color-accent)" : "var(--color-divider)",
        bg: o === s.freq ? "var(--color-accent-900)" : "transparent",
        pick: () => this.setState({ freq: o })
      })),
      chanOpts: ["In-platform", "Email", "Push", "SMS"].map((o) => {
        const on = s.channels.includes(o);
        return {
          label: o,
          fg: on ? "var(--color-accent)" : "var(--color-neutral-500)",
          border: on ? "var(--color-accent)" : "var(--color-divider)",
          bg: on ? "var(--color-accent-900)" : "transparent",
          pick: () => this.setState((p) => ({ channels: on ? p.channels.filter((c) => c !== o) : p.channels.concat([o]) }))
        };
      }),

      pnlSaved: "£390k",
      pnlTop: D.pnl.map((r) => ({ head: r.head, budget: r.budget, actual: r.actual, color: TONE[r.tone] ? TONE[r.tone].color : "var(--color-neutral-400)" })),
      cronCount: String(CRONS.filter((c) => c.action && !s.cronsGone.includes(c.text)).length),
      hoistScore: { value: "78", band: "Supervised autonomy", gap: "Meter consent lowest at 71% — the gap to delegated autonomy", note: "Ingestion coverage across the Hoist Graph. At 85 the agents move from supervised to delegated dispatch on L3 assets." },
      hoistBars: [
        { label: "Contracts and framework agreements", short: "Contracts", val: "92%", pct: "92%", color: "var(--st-ok)" },
        { label: "Asset registers", short: "Assets", val: "84%", pct: "84%", color: "var(--st-ok)" },
        { label: "Meter consent — MPAN / MPRN", short: "Meter consent", val: "71%", pct: "71%", color: "var(--st-warn)" },
        { label: "Certificates and evidence", short: "Certificates", val: "65%", pct: "65%", color: "var(--st-warn)" }
      ],

      isBuildings: s.signedIn && s.view === "buildings",
      useLegend: Object.keys(USE_TINT).map((k) => ({ label: k, color: USE_TINT[k].color, hatch: USE_TINT[k].hatch || "none" })),

      gCols: [],

      gNodes: (() => {
        const out = [];
        const CW = 2560;
        const clampL = (l, w) => Math.max(6, Math.min(CW - w - 6, parseFloat(l))) + "px";
        const shaped = (arr) => arr.map((n) => {
          const sq = n.shape === "square";
          return Object.assign({}, n, {
            labelLeft: clampL(n.labelLeft, parseFloat(n.labelW)),
            bx: n.files ? n.cx + n.r * 0.70 - 10 : 0,
            by: n.files ? n.cy - n.r * 0.70 - 10 : 0,
            bOp: n.files ? "1" : "0",
            bLeft: n.files ? (n.cx + n.r * 0.70 - 10) + "px" : "-999px",
            bTop: n.files ? (n.cy - n.r * 0.70 - 10) + "px" : "-999px",
            bText: n.files || "",
            circleR: sq ? 0 : n.r,
            rx: sq ? n.cx - n.r : 0, ry: sq ? n.cy - n.r : 0,
            rw: sq ? n.r * 2 : 0,
            rectOp: sq ? n.op : "0",
            hx: sq ? n.cx - n.r - 5 : 0, hy: sq ? n.cy - n.r - 5 : 0,
            hw: sq ? n.r * 2 + 10 : 0, haloOp: sq && n.halo ? "0.8" : "0"
          });
        });
        // The hand-placed positions, filled with real buildings. `gBuilding` holds a
        // name, so a saved selection can name a building this deployment does not have —
        // the live selection falls back to the first one drawn rather than to nothing.
        const HB = this.glHubs();
        const sel = this.glSelected();
        const selName = (sel && sel.name) || s.gBuilding;
        const selKid = s.gChild;
        const RAD = Math.PI / 180;

        this.glShared().forEach((sh) => {
          const mine = sh.links.indexOf(selName) > -1;
          out.push({
            cx: sh.cx, cy: sh.cy, r: sh.r,
            fill: mine ? "var(--color-accent-900)" : "var(--color-bg)",
            stroke: mine ? "var(--color-accent)" : "var(--color-neutral-700)",
            sw: mine ? "2" : "1.3", dash: "0", op: "1",
            glyph: "", glyphShow: "none", glyphSize: "10px", glyphFill: "transparent",
            glyphLeft: "0px", glyphTop: "0px", glyphW: "0px",
            labelLeft: (sh.cx - sh.r + 6) + "px", labelTop: (sh.cy - 18) + "px", labelW: (sh.r * 2 - 12) + "px",
            align: "center", wrap: "normal", pe: "auto",
            font: "var(--font-body)", fs: "11px",
            fg: mine ? "var(--color-text)" : "var(--color-neutral-300)",
            subFg: "var(--color-neutral-500)",
            label: sh.label, sub: sh.sub + " · " + sh.links.length + " hubs",
            click: () => this.setState({ gTable: sh.id })
          });
        });

        HB.forEach((h) => {
          const b = h.b;
          const on = h.name === selName;
          const r = on ? h.r + 6 : h.r;
          out.push({
            cx: h.cx, cy: h.cy, r: r,
            fill: on ? "var(--color-accent)" : "var(--color-accent-900)",
            stroke: "var(--color-accent)", sw: on ? "2.6" : "1.6", dash: "0", op: "1",
            glyph: "", glyphShow: "none", glyphSize: "11px", glyphFill: "transparent",
            glyphLeft: "0px", glyphTop: "0px", glyphW: "0px",
            labelLeft: (h.cx - r + 10) + "px", labelTop: (h.cy - 24) + "px", labelW: (r * 2 - 20) + "px",
            align: "center", wrap: "normal", pe: "auto",
            font: "var(--font-body)", fs: on ? "13px" : "12px",
            fg: on ? "var(--accent-ink)" : "var(--color-text)",
            subFg: on ? "var(--accent-ink)" : "var(--color-neutral-500)",
            label: h.name,
            // The building's own key and its floor count as recorded, not as computed.
            sub: (b.code || b.id || "") + (typeof b.floors === "number" ? " · " + b.floors + "f" : ""),
            // The badge counted vectorised files bound to this node. Nothing reachable
            // from this panel holds that, so it is absent rather than estimated.
            files: "",
            click: () => this.setState({ gBuilding: h.name, gChild: "asset", gTable: "building" })
          });

          CHILD_OF_BUILDING.forEach((k, i) => {
            const a = h.ang[i] * RAD;
            const cx = h.cx + Math.cos(a) * h.sat, cy = h.cy - Math.sin(a) * h.sat;
            const kOn = on && selKid === k.id;
            const t = GRAPH.find((g) => g.id === k.id);
            const rr = kOn ? 27 : on ? 23 : 18;
            out.push({
              cx: cx, cy: cy, r: rr,
              fill: kOn ? "var(--color-accent)" : "var(--color-bg)",
              stroke: "var(--color-accent)", sw: kOn ? "2.4" : on ? "1.5" : "1.2", dash: "0",
              op: on ? "1" : "0.66",
              glyph: k.glyph, glyphShow: "flex", glyphSize: on ? "10px" : "9px",
              glyphFill: kOn ? "var(--accent-ink)" : "var(--color-accent)",
              glyphLeft: (cx - rr) + "px", glyphTop: (cy - rr) + "px", glyphW: (rr * 2) + "px",
              labelLeft: (cx - 66) + "px", labelTop: (cy + rr + 5) + "px", labelW: "132px", align: "center",
              wrap: "nowrap", pe: on ? "auto" : "none",
              font: "ui-monospace, monospace", fs: "10.5px",
              fg: "var(--color-text)", subFg: "var(--color-neutral-500)",
              label: on ? t.tbl : "",
              // The relation, and beside it what this building actually has on that branch
              // — "?" where nobody counted it, which is not the same as none.
              sub: on && !kOn ? k.rel + " · " + this.glCountText(b, k.id) : "",
              files: "",
              click: () => this.setState({ gBuilding: h.name, gChild: k.id, gTable: k.id })
            });

            if (!kOn) return;
            const subs = SUB_OF[k.id] || [];
            subs.forEach((sb, j) => {
              const spread = (j - (subs.length - 1) / 2) * 48 * RAD;
              const a2 = a + spread;
              const scx = h.cx + Math.cos(a2) * (h.sat + 158), scy = h.cy - Math.sin(a2) * (h.sat + 158);
              const st = GRAPH.find((g) => g.id === sb.id);
              out.push({
                cx: scx, cy: scy, r: 16,
                fill: sb.join ? "var(--color-accent-900)" : "var(--color-bg)",
                stroke: "var(--color-accent)", sw: "1.3", dash: sb.join ? "3 3" : "0", op: "1",
                glyph: sb.glyph, glyphShow: "flex", glyphSize: "9px", glyphFill: "var(--color-accent)",
                glyphLeft: (scx - 16) + "px", glyphTop: (scy - 16) + "px", glyphW: "32px",
                labelLeft: (scx - 72) + "px", labelTop: (scy + 21) + "px", labelW: "144px", align: "center",
                wrap: "nowrap", pe: "auto",
                font: "ui-monospace, monospace", fs: "10.5px", fg: "var(--color-text)",
                subFg: "var(--color-neutral-500)",
                label: st.tbl, sub: sb.on + " · " + this.glCountText(b, sb.id),
                files: "",
                click: () => this.setState({ gTable: sb.id })
              });
            });
          });
        });
        return shaped(out);
      })(),

      gEdges: (() => {
        const out = [];
        const HB = this.glHubs();
        const selNow = this.glSelected();
        const selName = (selNow && selNow.name) || s.gBuilding, selKid = s.gChild;
        const RAD = Math.PI / 180;

        this.glShared().forEach((sh) => {
          sh.links.forEach((nm) => {
            const h = HB.find((x) => x.name === nm);
            if (!h) return;
            const on = nm === selName;
            const dx = sh.cx - h.cx, dy = sh.cy - h.cy, L = Math.sqrt(dx * dx + dy * dy) || 1;
            out.push({
              d: "M" + (h.cx + (dx / L) * (h.r + 6)) + " " + (h.cy + (dy / L) * (h.r + 6)) +
                 " L" + (sh.cx - (dx / L) * sh.r) + " " + (sh.cy - (dy / L) * sh.r),
              stroke: on ? "var(--color-accent)" : "var(--color-neutral-700)",
              w: on ? "2" : "1.3", dash: "3 6", op: on ? "0.9" : "0.5"
            });
          });
        });

        HB.forEach((h) => {
          const on = h.name === selName;
          CHILD_OF_BUILDING.forEach((k, i) => {
            const a = h.ang[i] * RAD;
            const cx = h.cx + Math.cos(a) * h.sat, cy = h.cy - Math.sin(a) * h.sat;
            const kOn = on && selKid === k.id;
            const rr = kOn ? 27 : on ? 23 : 18;
            out.push({
              d: "M" + (h.cx + Math.cos(a) * (on ? h.r + 6 : h.r)) + " " + (h.cy - Math.sin(a) * (on ? h.r + 6 : h.r)) +
                 " L" + (cx - Math.cos(a) * rr) + " " + (cy + Math.sin(a) * rr),
              stroke: "var(--color-accent)", w: kOn ? "2.2" : on ? "1.5" : "1.2", dash: "0", op: on ? "1" : "0.55"
            });
            if (!kOn) return;
            (SUB_OF[k.id] || []).forEach((sb, j) => {
              const subs = SUB_OF[k.id];
              const a2 = a + (j - (subs.length - 1) / 2) * 48 * RAD;
              const scx = h.cx + Math.cos(a2) * (h.sat + 158), scy = h.cy - Math.sin(a2) * (h.sat + 158);
              const dx = scx - cx, dy = scy - cy, L = Math.sqrt(dx * dx + dy * dy) || 1;
              out.push({
                d: "M" + (cx + (dx / L) * rr) + " " + (cy + (dy / L) * rr) +
                   " L" + (scx - (dx / L) * 16) + " " + (scy - (dy / L) * 16),
                stroke: "var(--color-accent)", w: "1.5", dash: sb.join ? "3 4" : "0", op: "0.9"
              });
            });
          });
        });
        return out;
      })(),

      gEdgeLabels: [], /* relationships ride on the nodes and the schema panel */

      // Canonical destination table for whatever is selected, plus the three
      // snapshots behind it. Named by date and build so "which one am I taking"
      // needs no explanation.
      exportT: (() => {
        const n = GRAPH.find((x) => x.id === s.gTable) || GRAPH[4];
        const b = this.glSelected();
        const t = this.glTable(n.id);
        const counted = typeof t.rows === "number";
        const rows = counted ? t.rows : 0;
        const cols = typeof t.columns === "number" ? t.columns : n.cols.length;
        return {
          name: t.table,
          // "?" rather than a number for a table this database does not have. The old panel
          // printed a formula's output here, so a table that did not exist still had rows.
          rows: counted ? NUM(rows) : "?",
          cols: String(cols),
          note: t.why,
          noteShow: t.why ? "block" : "none",
          basis: t.basis,
          basisShow: t.versions.length > 1 ? "block" : "none",
          download: () => counted
            ? this.flash(t.table + ".csv — " + NUM(rows) + " rows counted in plenum_cafm."
                + t.table + ", every column resolved through the graph. Downloading now.")
            : this.flash("Nothing to export: " + t.why),
          downloadHistory: () => this.flash(t.versions.length > 1
            ? "Preparing " + t.table + " at each cutoff as one zip. Each is the set of rows "
              + "whose created_at falls before that time — a reconstruction from the live "
              + "table, not a stored build."
            : "No history for " + t.table + " — " + (t.why || "nothing to reconstruct from.")),
          // Counted at each cutoff, not scaled from a percentage. A table nobody has
          // written to since Friday shows the same figure four times, which is the truth
          // about that table rather than a fixed decline applied to every table alike.
          versions: t.versions.map((v) => ({
            label: v.label, note: v.note, rows: NUM(v.rows),
            delta: v.delta === null || v.delta === undefined ? "—"
              : v.delta === 0 ? "0" : (v.delta > 0 ? "+" : "") + v.delta,
            deltaFg: !v.delta ? "var(--color-neutral-500)"
              : v.delta < 0 ? "var(--color-neutral-400)" : "var(--st-ok)",
            bg: v.current ? "var(--color-accent-900)" : "transparent",
            fg: v.current ? "var(--color-text)" : "var(--color-neutral-300)",
            download: () => this.flash(t.table + " — " + v.label.toLowerCase() + ": "
              + NUM(v.rows) + " rows, " + v.note + ". Downloading as CSV.")
          })),
          versionsShow: t.versions.length ? "block" : "none",
          // A building is named beside the export only when one is selected; the export
          // itself is the whole table, and saying otherwise would misdescribe the file.
          scope: b ? "Whole table · " + b.name + " is the building in focus" : "Whole table"
        };
      })(),

      updateGraph: () => this.runAction("Update the graph", s.gBuilding),
      fUpdate: s.flow === "update",
      ug: (() => {
        const t = s.ugText || "";
        const low = t.toLowerCase();
        const b = s.gBuilding;
        // Read the instruction: which verb, which record, which document.
        const isRemove = /remov|delet|drop|take out|retire/.test(low);
        const isReplace = /replac|update|supersed|newer|new one|re-?issue/.test(low);
        const isCorrect = /correct|fix|wrong|change .*to|amend/.test(low);
        const verb = isRemove ? "REMOVE" : isCorrect ? "CORRECT" : isReplace ? "REPLACE" : "REPLACE";
        const target = /eicr/.test(low) ? "EICR — Landlord Supply"
          : /cp12|gas/.test(low) ? "Gas Safety (CP12)"
          : /loler|lift/.test(low) ? "LOLER Thorough Examination"
          : /asbestos/.test(low) ? "Asbestos Re-inspection"
          : /warrant/.test(low) ? "Warranty certificate"
          : /contract/.test(low) ? "FM framework agreement"
          : /meter|mpan/.test(low) ? "MPAN consent letter"
          : "EICR — Landlord Supply";
        const where = /an other/.test(low) ? "AN Other House" : /town hall/.test(low) ? "Town Hall"
          : /kingsway/.test(low) ? "Kingsway House" : /riverside/.test(low) ? "Riverside Court" : b;
        const plan = [];
        if (verb === "REMOVE") {
          plan.push({ tag: "REMOVE", what: target + " and its extracted rows", where: "documents · certificates — " + where });
          plan.push({ tag: "RECHECK", what: "Compliance position for " + where, where: "the obligation reopens as not on record" });
          plan.push({ tag: "RESCORE", what: "Hoist Score and coverage", where: "certificates coverage falls" });
        } else if (verb === "CORRECT") {
          plan.push({ tag: "CORRECT", what: "The value read from " + target, where: "the cell, not the file — the scan stays as filed" });
          plan.push({ tag: "AUDIT", what: "Original extraction kept alongside the correction", where: "activity log · actor and timestamp" });
          plan.push({ tag: "RESCORE", what: "Anything derived from that value", where: "expiry runway, risk band" });
        } else {
          plan.push({ tag: "REPLACE", what: target + " with the newer issue", where: "documents — " + where });
          plan.push({ tag: "SUPERSEDE", what: "Previous version retained, marked superseded", where: "nothing is destroyed — the old build stays exportable" });
          plan.push({ tag: "REBIND", what: "Re-vectorise and rebind to its column", where: "certificates.cert_type · re-scored" });
          plan.push({ tag: "RESCORE", what: "Expiry, risk band and Hoist Score", where: where + " · recomputed on apply" });
        }
        const TAGC = {
          REMOVE: ["var(--st-risk-bg)", "var(--st-risk)"], REPLACE: ["var(--color-accent-900)", "var(--color-accent)"],
          CORRECT: ["var(--color-accent-900)", "var(--color-accent)"], SUPERSEDE: ["var(--marker-tint)", "var(--color-neutral-300)"],
          REBIND: ["var(--marker-tint)", "var(--color-neutral-300)"], RECHECK: ["var(--color-neutral-900)", "var(--color-neutral-400)"],
          RESCORE: ["var(--color-neutral-900)", "var(--color-neutral-400)"], AUDIT: ["var(--color-neutral-900)", "var(--color-neutral-400)"]
        };
        return {
          asking: !s.ugParsed, confirming: !!s.ugParsed, text: t,
          setText: (e) => this.setState({ ugText: e.target.value }),
          examples: [
            "Replace the EICR for AN Other House with the new one",
            "Remove the duplicate CP12 on Town Hall",
            "The expiry on the asbestos survey is wrong — correct it"
          ].map((x) => ({ label: x, pick: () => this.setState({ ugText: x }) })),
          parse: () => this.setState({ ugParsed: true }),
          back: () => this.setState({ ugParsed: false }),
          plan: plan.map((p) => ({ tag: p.tag, what: p.what, where: p.where,
            tagBg: (TAGC[p.tag] || TAGC.RESCORE)[0], tagFg: (TAGC[p.tag] || TAGC.RESCORE)[1] })),
          docName: target + " — " + where + ".pdf",
          docMeta: verb === "REMOVE" ? "on file since 14 Jul 2026 · 1.2 MB · this is the file that would go"
            : "matched from your last ingestion · 2.4 MB · issued 28 Aug 2026",
          viewDoc: () => this.flash("Opening " + target + " for " + where + " — the file this change acts on, read-only."),
          consequence: verb === "REMOVE"
            ? "Removal is reversible for 30 days and the old build stays exportable. Confirm the file above is the right one."
            : "Nothing is overwritten. The current version becomes the canonical row; the previous one is kept and stays in the snapshot history.",
          apply: () => this.setState({ flow: null, ugParsed: false, ugText: "",
            flowDone: verb === "REMOVE"
              ? target + " removed from " + where + ". The obligation is back on the compliance queue and coverage has been rescored. Reversible for 30 days."
              : verb === "CORRECT"
                ? "Corrected on " + where + ". The original extraction is kept beside it, and everything derived from that value has been recomputed."
                : target + " replaced on " + where + ". Re-vectorised, rebound to its column and rescored — the previous issue is marked superseded, not deleted." })
        };
      })(),

      isVP: s.signedIn && s.view === "vp",
      vpRebuild: () => this.runAction("Rebuild scorecards", "Vendors"),
      vpWeights: () => this.flash("Weights: SLA response 25, SLA completion 25, first-time fix 20, recall rate 15, invoice accuracy 15. Each metric has its own percentage target; shortfall is penalised at three times its relative size. L1 misses weigh 3×, L3 misses 0.5×. A mandatory accreditation lapse puts a ceiling of 60 on the published score."),
      vpTiles: (() => {
        const scored = D.vendors.filter((v) => VP.V[v.id]);
        const avg = Math.round(scored.reduce((q, v) => q + vpScore(v.id).score, 0) / scored.length);
        const blocked = D.vendors.filter((v) => v.accred === "Lapsed").length;
        const held = D.vendors.reduce((q, v) => q + ((VP.V[v.id] || { invoices: [] }).invoices.filter((i) => i.status === "Held" || i.status === "Disputed").length), 0);
        const defaults = D.vendors.reduce((q, v) => q + ((VP.V[v.id] || { contract: { fields: 0, read: 0 } }).contract.fields - (VP.V[v.id] || { contract: { read: 0 } }).contract.read), 0);
        const thinnestPre = null;
        const L1 = D.vendors.reduce((q, v) => q + ((VP.V[v.id] || { breaches: [] }).breaches.filter((b) => b.crit === "L1").length), 0);
        const pending = held + D.vendors.reduce((q, v) => q + ((VP.V[v.id] || { certs: [] }).certs.filter((c) => c.req === "Mandatory" && (c.status === "Lapsed" || c.status === "Not on record")).length), 0);
        const critical = D.vendors.reduce((q, v) => q + ((VP.V[v.id] || { breaches: [] }).breaches.filter((b) => b.crit === "L1" && /blocked|missed/.test(b.actual)).length), 0);
        const worst = scored.slice().sort((a, b) => vpScore(a.id).score - vpScore(b.id).score)[0] || D.vendors[0];
        const thinnest = scored.slice().sort((a, b) => VP.V[a.id].contract.read - VP.V[b.id].contract.read)[0] || D.vendors[0];
        return [
          { value: String(blocked), label: "Vendors blocked", hint: "ceiling of 60 applies", color: "var(--st-risk)", click: () => this.setState({ vpVendor: "v2", vpTab: 3 }) },
          { value: String(pending), label: "Pending tasks", hint: "awaiting your decision", color: "var(--st-warn)", click: () => this.setState({ queueOpen: true }) },
          { value: String(critical), label: "Pending critical", hint: "L1 assets · act first", color: "var(--st-risk)", click: () => this.setState({ vpVendor: "v2", vpTab: 2 }) },
          { value: String(L1), label: "L1 breaches", hint: "weighted 3× · this period", color: "var(--st-risk)", click: () => this.setState({ vpVendor: worst.id, vpTab: 2 }) },
          { value: String(held), label: "Invoice lines held", hint: "fail the rate schedule", color: "var(--st-warn)", click: () => this.setState({ vpVendor: "v1", vpTab: 4 }) },
          { value: String(defaults), label: "Terms on default", hint: "not in any contract", color: "var(--st-warn)", click: () => this.setState({ vpVendor: thinnest.id, vpTab: 1 }) }
        ];
      })(),

      vpStats: (() => {
        const pkgOf = (id) => PKG[id] || "Other";
        const byPkg = {};
        D.vendors.forEach((v) => { byPkg[pkgOf(v.id)] = (byPkg[pkgOf(v.id)] || []).concat([v]); });
        const bar = (n, max) => Math.round((n / Math.max(1, max)) * 100) + "%";
        const pkgMax = Math.max.apply(null, Object.keys(byPkg).map((k) => byPkg[k].length));

        const expiring = D.vendors.filter((v) => {
          const R = VP.V[v.id];
          return R && /2026/.test(R.contract.expires);
        });
        const totalLines = D.vendors.reduce((q, v) => q + ((VP.V[v.id] || { invoices: [] }).invoices.length), 0);
        const heldLines = D.vendors.reduce((q, v) => q + ((VP.V[v.id] || { invoices: [] }).invoices.filter((i) => i.status === "Held" || i.status === "Disputed").length), 0);
        const wo = D.workorders || [];

        const scoredV = D.vendors.filter((v) => VP.V[v.id]);
        const avgS = Math.round(scoredV.reduce((q, v) => q + vpScore(v.id).score, 0) / scoredV.length);
        const band = (lo, hi) => scoredV.filter((v) => { const n = vpScore(v.id).score; return n >= lo && n <= hi; });
        const lowest = scoredV.slice().sort((a, b) => vpScore(a.id).score - vpScore(b.id).score)[0] || D.vendors[0];

        return [
          {
            value: String(avgS), label: "Avg score",
            click: () => this.setState({ vpVendor: lowest.id, vpTab: 0 }),
            rows: [
              { label: "85 and above", n: String(band(85, 100).length), color: "var(--st-ok)", bar: bar(band(85, 100).length, scoredV.length), click: () => this.setState({ vpVendor: (band(85, 100)[0] || D.vendors[0]).id, vpTab: 0 }) },
              { label: "70 to 84", n: String(band(70, 84).length), color: "var(--st-warn)", bar: bar(band(70, 84).length, scoredV.length), click: () => this.setState({ vpVendor: (band(70, 84)[0] || D.vendors[0]).id, vpTab: 0 }) },
              { label: "Below 70", n: String(band(0, 69).length), color: "var(--st-risk)", bar: bar(band(0, 69).length, scoredV.length), click: () => this.setState({ vpVendor: lowest.id, vpTab: 0 }) }
            ]
          },
          {
            value: String(D.vendors.length), label: "Vendors",
            click: () => this.setState({ vpVendor: "v1", vpTab: 0 }),
            rows: [
              { label: "Fully accredited", n: String(D.vendors.filter((v) => v.accred === "Current").length), color: "var(--st-ok)", bar: bar(D.vendors.filter((v) => v.accred === "Current").length, D.vendors.length), click: () => this.setState({ vpVendor: "v3", vpTab: 3 }) },
              { label: "Expiring", n: String(D.vendors.filter((v) => v.accred === "Expiring").length), color: "var(--st-warn)", bar: bar(D.vendors.filter((v) => v.accred === "Expiring").length, D.vendors.length), click: () => this.setState({ vpVendor: "v6", vpTab: 3 }) },
              { label: "Lapsed", n: String(D.vendors.filter((v) => v.accred === "Lapsed").length), color: "var(--st-risk)", bar: bar(D.vendors.filter((v) => v.accred === "Lapsed").length, D.vendors.length), click: () => this.setState({ vpVendor: "v2", vpTab: 3 }) }
            ]
          },
          {
            value: String(Object.keys(byPkg).length), label: "Packages",
            click: () => this.flash("Vendors are grouped by service package. A package with a single vendor is a single point of failure — worth a second accredited contractor before the next renewal."),
            rows: Object.keys(byPkg).map((k) => ({
              label: k, n: String(byPkg[k].length),
              color: byPkg[k].some((v) => v.accred === "Lapsed") ? "var(--st-risk)" : byPkg[k].length === 1 ? "var(--st-warn)" : "var(--st-ok)",
              bar: bar(byPkg[k].length, pkgMax),
              click: () => this.setState({ vpVendor: byPkg[k][0].id, vpTab: 0 })
            }))
          },
          {
            value: String(D.vendors.filter((v) => VP.V[v.id]).length), label: "Contracts",
            click: () => this.setState({ vpVendor: "v1", vpTab: 1 }),
            rows: [
              { label: "Terms read from document", n: String(D.vendors.reduce((q, v) => q + ((VP.V[v.id] || { contract: { read: 0 } }).contract.read), 0)), color: "var(--st-ok)", bar: "72%", click: () => this.setState({ vpVendor: "v3", vpTab: 1 }) },
              { label: "On platform default", n: String(D.vendors.reduce((q, v) => q + ((VP.V[v.id] || { contract: { fields: 0, read: 0 } }).contract.fields - (VP.V[v.id] || { contract: { read: 0 } }).contract.read), 0)), color: "var(--st-warn)", bar: "28%", click: () => this.setState({ vpVendor: thinnest.id, vpTab: 1 }) },
              { label: "Expiring this year", n: String(expiring.length), color: expiring.length ? "var(--st-warn)" : "var(--st-ok)", bar: bar(expiring.length, D.vendors.length), click: () => this.setState({ vpVendor: expiring.length ? expiring[0].id : "v1", vpTab: 1 }) }
            ]
          },
          {
            value: String(totalLines), label: "Commercial orders",
            click: () => this.setState({ vpVendor: "v1", vpTab: 4 }),
            rows: [
              { label: "Approved as charged", n: String(totalLines - heldLines), color: "var(--st-ok)", bar: bar(totalLines - heldLines, totalLines), click: () => this.setState({ vpVendor: "v3", vpTab: 4 }) },
              { label: "Held or disputed", n: String(heldLines), color: "var(--st-risk)", bar: bar(heldLines, totalLines), click: () => this.setState({ vpVendor: "v1", vpTab: 4 }) },
              { label: "Work orders open", n: String(wo.length), color: "var(--color-neutral-400)", bar: bar(wo.length, wo.length || 1), click: () => this.openModule("ops") }
            ]
          }
        ];
      })(),
      vpList: D.vendors.map((v) => {
        const R = VP.V[v.id];
        const req = R ? R.certs.filter((c) => c.req !== "Preferred").length : 0;
        const on = R ? R.certs.filter((c) => c.req !== "Preferred" && (c.status === "Current" || c.status === "Expiring")).length : 0;
        const cov = req ? Math.round((on / req) * 100) : 0;
        const capped = v.accred === "Lapsed";
        const active = s.vpVendor === v.id;
        const sc = vpScore(v.id);
        return {
          name: v.name, meta: v.spend + " annual · " + v.accred.toLowerCase() + " accreditation",
          cap: capped ? "ceiling 60 — mandatory lapse" : "", capShow: capped ? "block" : "none", capFg: "var(--st-risk)",
          score: String(sc.score), trend: v.trend,
          scoreFg: sc.score >= 85 ? "var(--st-ok)" : sc.score >= 70 ? "var(--st-warn)" : "var(--st-risk)",
          cov: cov + "%", covFrac: on + "/" + req,
          covFg: cov >= 90 ? "var(--st-ok)" : cov >= 60 ? "var(--st-warn)" : "var(--st-risk)",
          edge: capped ? "var(--st-risk)" : v.score >= 85 ? "var(--st-ok)" : "var(--st-warn)",
          bg: active ? "var(--color-accent-900)" : "transparent",
          pick: () => this.setState({ vpVendor: v.id, vpTab: 0 })
        };
      }),
      vpTabs: [["Scorecard", 5], ["Contract terms", (vpR ? vpR.terms.length : 0)], ["Evidence", (vpR ? vpR.breaches.length : 0)], ["Coverage", (vpR ? vpR.certs.length : 0)], ["Invoices", (vpR ? vpR.invoices.length : 0)]].map((t, i) => ({
        label: t[0], n: String(t[1]),
        edge: s.vpTab === i ? "var(--color-accent)" : "transparent",
        fg: s.vpTab === i ? "var(--color-accent)" : "var(--color-neutral-500)",
        pick: () => this.setState({ vpTab: i })
      })),
      vpPaneScore: s.vpTab === 0, vpPaneTerms: s.vpTab === 1, vpPaneEvidence: s.vpTab === 2,
      vpPaneCerts: s.vpTab === 3, vpPaneInv: s.vpTab === 4,
      vp: (() => {
        const v = vpV, R = vpR;
        if (!v || !R) return {};
        const capped = v.accred === "Lapsed";
        const req = R.certs.filter((c) => c.req !== "Preferred");
        const onFile = req.filter((c) => c.status === "Current" || c.status === "Expiring");
        const missing = R.certs.filter((c) => c.status === "Not on record" || c.status === "Lapsed");
        const TAGS = {
          Current: ["var(--st-ok-bg)", "var(--st-ok)"], Expiring: ["var(--st-warn-bg)", "var(--st-warn)"],
          Lapsed: ["var(--st-risk-bg)", "var(--st-risk)"], "Not on record": ["var(--color-neutral-900)", "var(--color-neutral-400)"],
          Held: ["var(--st-warn-bg)", "var(--st-warn)"], Disputed: ["var(--st-risk-bg)", "var(--st-risk)"],
          Credited: ["var(--st-ok-bg)", "var(--st-ok)"], Approved: ["var(--color-neutral-900)", "var(--color-neutral-400)"]
        };
        const CRIT = { L1: ["var(--st-risk-bg)", "var(--st-risk)"], L2: ["var(--st-warn-bg)", "var(--st-warn)"], L3: ["var(--color-neutral-900)", "var(--color-neutral-400)"] };
        const credit = R.breaches.reduce((q, b) => q + parseInt(b.cost.replace(/[^0-9]/g, ""), 10), 0);
        const invTotal = R.invoices.filter((i) => i.status === "Held" || i.status === "Disputed")
          .reduce((q, i) => q + parseInt((i.delta || "0").replace(/[^0-9]/g, "") || "0", 10), 0);
        return {
          name: v.name,
          contractLine: R.contract.ref + " · signed " + R.contract.signed + " · expires " + R.contract.expires + " · " + R.contract.read + " of " + R.contract.fields + " terms read from " + R.contract.pages + " pages",
          score: String(score), trend: v.trend,
          scoreFg: score >= 85 ? "var(--st-ok)" : score >= 70 ? "var(--st-warn)" : "var(--st-risk)",
          capShow: capped ? "flex" : "none", capBg: "var(--st-risk-bg)", capFg: "var(--st-risk)",
          capNote: capped
            ? "Score cannot exceed 60 while " + missing.filter((c) => c.req === "Mandatory").map((c) => c.name).join(" and ") + " " + (missing.filter((c) => c.req === "Mandatory").length > 1 ? "are" : "is") + " not current — this vendor cannot hold regulated work whatever the delivery numbers say."
            : "",
          metrics: SC.rows.map((r) => {
            const ratio = r.pts / r.w;
            return {
              label: r.label, max: String(r.w), pts: String(r.pts),
              requires: (r.ceiling ? "no more than " : "at least ") + r.target + "%",
              measured: r.measured + "%",
              sample: r.basis + " · " + R.samples[r.k] + (r.k === "invoice" ? " invoice lines" : " work orders"),
              bar: Math.round(ratio * 100) + "%",
              color: ratio >= 0.95 ? "var(--st-ok)" : ratio >= 0.75 ? "var(--st-warn)" : "var(--st-risk)",
              srcTag: r.fromContract ? "clause " + r.clause + " · p" + r.page
                : r.clause ? "default " + r.target + "% · obligation at " + r.clause
                : "platform default " + r.target + "%",
              srcBg: r.fromContract ? "var(--color-accent-900)" : "var(--marker-tint)",
              srcFg: r.fromContract ? "var(--color-accent)" : "var(--color-neutral-300)"
            };
          }),
          totalPts: String(SC.raw),
          capRowShow: capped ? "grid" : "none",
          finalScore: String(score),
          critSplit: [
            { label: "L1 · critical", n: String(R.crit.L1), note: "misses weigh 3×", color: "var(--st-risk)" },
            { label: "L2 · medium", n: String(R.crit.L2), note: "misses weigh 1×", color: "var(--st-warn)" },
            { label: "L3 · low", n: String(R.crit.L3), note: "misses weigh 0.5×", color: "var(--color-neutral-400)" }
          ],
          critNote: "Criticality is set per asset and approved by a person, not inferred. Unapproved assets default to L2 until someone confirms otherwise, so a mis-set L1 cannot quietly triple a vendor's penalty.",
          sourceNote: R.contract.read + " of " + R.contract.fields + " terms were read from the signed contract — " + Math.round((R.contract.read / R.contract.fields) * 100) + "% source coverage. The rest fell back to platform defaults, which are named below.",
          terms: R.terms.map((t) => ({
            label: t.label, value: t.value,
            valFg: t.src === "contract" ? "var(--color-text)" : "var(--color-neutral-400)",
            src: t.src === "contract" ? t.clause + " · p" + t.page : "default",
            bg: t.src === "contract" ? "var(--color-accent-900)" : "var(--marker-tint)",
            fg: t.src === "contract" ? "var(--color-accent)" : "var(--color-neutral-300)",
            cursor: t.src === "contract" ? "pointer" : "default",
            open: () => t.src === "contract"
              ? this.flash("Opening " + R.contract.ref + " at clause " + t.clause + ", page " + t.page + " — the text this term was read from.")
              : this.flash(t.label + " was not found in " + R.contract.ref + ". The platform default of " + t.value + " applies until the term is agreed in writing.")
          })),
          breaches: R.breaches.map((b) => ({
            wo: b.wo, asset: b.asset, building: b.building, crit: b.crit, metric: b.metric,
            target: b.target, actual: b.actual, mult: b.mult, cost: b.cost,
            critBg: CRIT[b.crit][0], critFg: CRIT[b.crit][1],
            click: () => this.flash(b.wo + " — " + b.asset + " at " + b.building + ". " + b.metric + " target " + b.target + ", actual " + b.actual + ". " + b.crit + " asset, so the miss is weighted " + b.mult + " and carries " + b.cost + " in service credits.")
          })),
          creditTotal: "£" + credit.toLocaleString(),
          claim: () => this.runAction("Claim service credits", v.name),
          evidence: () => this.runAction("Request evidence", v.name),
          covNote: onFile.length + " of " + req.length + " mandatory accreditations are on file" + (missing.length ? ", and " + missing.length + " " + (missing.length > 1 ? "are" : "is") + " lapsed or never supplied. " : ". ") + "Coverage is checked against the issuing register, not the document the vendor sent.",
          certs: R.certs.map((c) => ({
            name: c.name, req: c.req, status: c.status, exp: c.exp, ver: c.ver,
            reqFg: c.req === "Preferred" ? "var(--color-neutral-500)" : "var(--color-accent-300)",
            bg: TAGS[c.status][0], fg: TAGS[c.status][1]
          })),
          chase: () => this.runAction("Request evidence", v.name),
          openCompliance: () => this.setState({ view: "cc", ccPivot: "vendors", ccFocus: { kind: "vendor", name: v.name }, ccTab: 0 }),
          invoices: R.invoices.map((i) => ({
            ref: i.ref, period: i.period, line: i.line, charged: i.charged, should: i.should,
            delta: i.delta, deltaFg: i.delta === "—" ? "var(--color-neutral-500)" : "var(--st-risk)",
            status: i.status, bg: TAGS[i.status][0], fg: TAGS[i.status][1],
            flag: i.flag, flagShow: i.flag ? "block" : "none"
          })),
          invTotal: "£" + invTotal.toLocaleString(),
          challenge: () => this.runAction("Raise credit note", v.name),
          approveInv: () => this.runAction("Approve as charged", v.name)
        };
      })(),

      ...this.intVals(s),
      ...this.enVals(s),
      ...this.invVals(s),
      ...this.enBuildingVals(s),

      /* Query-first: every non-admin report opens with the ask bar above the
         analysis, scoped to the page you are on. Admin pages (Buildings admin,
         Integrations) are configuration surfaces and do not carry it. */
      pq: s.pq || "",
      pqSet: (e) => this.setState({ pq: e.target.value }),
      pqKey: (e) => {
        if (e.key !== "Enter") return;
        const q = (s.pq || "").trim();
        if (!q) return;
        this.setState({ pq: "" });
        this.ask(q);
      },
      pqRun: () => {
        const q = (s.pq || "").trim();
        if (!q) return this.flash("Type a question — it runs against the graph behind this page, not the table on it.");
        this.setState({ pq: "" });
        this.ask(q);
      },
      abPh: (() => {
        if (s.view === "cc") return "Ask anything about compliance — 118 obligations across 24 buildings";
        if (s.view === "vp") return "Ask anything about vendor performance — 6 contracts, August scorecard";
        if (s.view === "report") return "Ask anything about this report, or ask for the next one";
        if (s.view === "buildings") return "Ask anything about your buildings — schema, documents, open risk";
        if (s.view === "module" && mod) return "Ask anything about " + mod.name.toLowerCase() + " across the portfolio";
        return "Ask the portfolio anything";
      })(),
      abChips: (() => {
        const q = s.view === "cc"
          ? ["Which buildings put me at risk this month?", "Which lapses void insurance?", "What needs my approval today?"]
          : s.view === "vp"
          ? ["Rank vendors by first-time fix", "Which vendors are blocked?", "What service credit can I claim?"]
          : s.view === "report"
          ? ["Which buildings drive this result?", "What changed since the last refresh?", "Show me the evidence behind row one"]
          : s.view === "buildings"
          ? ["Which of my buildings has open risk?", "What documents have I ingested?", "Which assets are unaccounted for?"]
          : (mod && mod.asks) || ["Which buildings put me at risk this month?"];
        return q.map((a) => ({ label: a, run: () => this.ask(a) }));
      })(),

      isUser: s.role !== "admin",
      isAdmin: s.role === "admin",
      roleLabel: s.role === "admin" ? "Admin view" : "Your view",
      roleBg: s.role === "admin" ? "var(--color-accent)" : "var(--color-neutral-900)",
      roleFg: s.role === "admin" ? "var(--accent-ink)" : "var(--color-neutral-400)",
      docScope: s.role === "admin" ? "Everything ingested, per building" : "What you ingested, per building",
      docBlurb: s.role === "admin"
        ? "Every document on the graph, per building, read from plenum_cafm.documents — and beside each, the certificates it evidences. A building with nothing filed says so rather than showing an empty list."
        : "The documents on the graph for each building, read from plenum_cafm.documents, and the certificates they evidence. Source files stay with whoever ingested them.",
      docStats: (() => {
        const rows = this.glBuildings();
        const sum = (k) => rows.reduce((q, b) => {
          const n = (b.counts || {})[k];
          return typeof n === "number" ? q + n : q;
        }, 0);
        const docs = sum("documents");
        const certsKnown = this.glBranchCounted("certificates");
        const certs = certsKnown ? sum("certificates") : null;
        const none = rows.filter((b) => ((b.counts || {}).documents || 0) === 0).length;
        return [
          { value: NUM(docs), label: docs === 1 ? "document on the graph" : "documents on the graph", color: "var(--color-text)" },
          { value: certsKnown ? NUM(certs) : "?",
            label: certsKnown ? "certificates bound to them" : "certificates · branch not counted",
            color: certsKnown ? "var(--color-text)" : "var(--color-neutral-500)" },
          // Not a total under management — the buildings with nothing filed, which is the
          // figure that says what to do next. plenum_cafm.documents records no file size,
          // so the "0.6 GB" that sat here was a formula over a seed building.
          { value: String(none), label: none === 1 ? "building with nothing filed" : "buildings with nothing filed",
            color: none ? "var(--st-warn)" : "var(--color-accent)" }
        ];
      })(),
      docBuildings: this.glBuildings().map((b) => {
        const open = s.docOpen === b.name;
        const key = b.buildingId || b.id;
        const c = b.counts || {};
        const nDocs = c.documents, nCerts = c.certificates;
        // The rows themselves come from the graph endpoint, fetched when the building is
        // opened and cached per building — the same call the drawer makes, so opening one
        // in both places costs one request.
        // Deferred, not called here: this runs inside render, and bgLoad sets state on its
        // first call. The cache guard inside bgLoad makes it a one-shot per building.
        if (open) setTimeout(() => this.bgLoad(key), 0);
        const fetched = this.bgRowsFor(key);
        const branch = fetched && fetched.documents;
        const certBranch = fetched && fetched.certificates;
        const state = (this.state.bgTree || {})[key] || {};
        const files = ((branch && branch.rows) || []).map((r) => ({
          file: r.label,
          became: r.detail ? "filed as " + r.detail : "no document type recorded",
          // No size and no ingest timestamp on plenum_cafm.documents; the row's own key is
          // what there is, and it is what identifies the file in the graph.
          meta: "document_id " + String(r.id).slice(0, 8),
          by: "graph",
          view: () => this.flash(r.label + " — plenum_cafm.documents row " + r.id
            + (r.detail ? ", " + r.detail : "") + ", filed against " + b.name + "."),
          download: () => this.flash("plenum_cafm.documents records a blob_url for "
            + r.label + " but no local copy; the file is fetched from storage on request.")
        }));
        return {
          name: b.name, id: b.code || b.id, state: b.state,
          arrow: open ? "▾" : "▸",
          headBg: open ? "var(--color-accent-900)" : "transparent",
          openShow: open ? "flex" : "none",
          // "?" where the rollup did not count this branch, not "0" — the panel used to
          // print "2 structured" for every building in the portfolio, including the ones
          // with nothing filed at all.
          nStruct: typeof nDocs === "number" ? NUM(nDocs) : (this.glBranchCounted("documents") ? "0" : "?"),
          nUnstruct: typeof nCerts === "number" ? NUM(nCerts) : (this.glBranchCounted("certificates") ? "0" : "?"),
          structLabel: nDocs === 1 ? "document" : "documents",
          unstructLabel: nCerts === 1 ? "certificate" : "certificates",
          // plenum_cafm.documents records no file size, so the slot that showed "0.2 GB"
          // for every building shows nothing rather than a figure derived from row counts.
          size: "",
          structured: files,
          // The right-hand column held "unstructured files, vectorised and bound", with a
          // similarity score per row. Those scores were weights in a constant. What the
          // graph does hold on this side is the certificates evidenced by those documents,
          // so that is what sits there — real rows, in the same place.
          unstructured: ((certBranch && certBranch.rows) || []).map((r) => ({
            file: r.label,
            became: r.detail || "no expiry or status recorded",
            sim: "",
            meta: "certificate",
            view: () => this.flash(r.label + " — plenum_cafm.compliance_certificates row "
              + r.id + (r.detail ? ", " + r.detail : "") + ", bound to a document on " + b.name + "."),
            download: () => this.flash("Fetching the certificate scan behind " + r.label + ".")
          })),
          loading: !!state.loading,
          emptyText: state.loading ? "Reading the graph…"
            : state.error ? "Could not read this building's documents: " + state.error
            : branch && !branch.available ? (branch.empty_reason || "Documents cannot be read here.")
            : files.length ? "" : "Nothing filed against " + b.name + " yet.",
          emptyShow: files.length ? "none" : "block",
          // Each column says why it is empty on its own. A heading with nothing under it
          // reads as a screen that has not finished loading rather than as a record.
          certEmpty: !certBranch ? "Reading the graph…"
            : !certBranch.available ? (certBranch.empty_reason || "Certificates cannot be read here.")
            : "No certificate has been filed against a document on this building.",
          certEmptyShow: certBranch && (certBranch.rows || []).length ? "none" : "block",
          toggle: () => {
            const opening = s.docOpen !== b.name;
            this.setState((p) => ({ docOpen: p.docOpen === b.name ? null : b.name }));
            if (opening) this.bgLoad(key);
          },
          downloadAll: () => this.flash(typeof nDocs === "number" && nDocs
            ? "Preparing every document filed against " + b.name + " — " + nDocs
              + " rows in plenum_cafm.documents, each fetched from its blob_url."
            : "Nothing filed against " + b.name + " to download."),
          ingestMore: () => this.runAction("Ingest documents", b.name)
        };
      }),

      // Hierarchy for the selected building only — parent, children, sub-children,
      // each row carrying the key it is identified by.
      hier: (() => {
        const b = this.glSelected();
        // Vector bindings are a real thing the platform does, and nothing this panel can
        // reach reports them: the similarity scores and file counts here were weights in a
        // constant. An empty list renders the row without them rather than with numbers
        // nobody measured.
        const vecs = () => [];
        const parentSel = s.gTable === "building";
        const OK = "var(--color-accent)", INK = "var(--accent-ink)";
        // A branch prints its count, or "?" and the reason nobody has one. The two used to
        // be the same thing, and a building with no meters read identically to a building
        // whose meters were never counted.
        const line = (id, unit) => {
          const c = this.glCount(b, id);
          return c.counted ? NUM(c.n) + " " + unit + (c.surveyed ? " · recorded" : "")
            : "not counted";
        };
        return {
          building: b ? b.name : "No building on the register",
          rowKey: b ? (b.code || b.id) : "—",
          vectors: vecs(),
          empty: b ? "" : "plenum_cafm.buildings has no rows in this deployment.",
          emptyShow: b ? "none" : "block",
          pickParent: () => this.setState({ gTable: "building" }),
          parentBg: parentSel ? OK : "transparent",
          parentFg: parentSel ? INK : "var(--color-accent)",
          parentSub: parentSel ? INK : "var(--color-neutral-500)",
          pkBg: parentSel ? "var(--color-bg)" : OK,
          pkFg: parentSel ? OK : INK,
          children: CHILD_OF_BUILDING.map((k) => {
            const t = GRAPH.find((g) => g.id === k.id);
            const open = s.gChild === k.id;
            const sel = s.gTable === k.id;
            const subs = SUB_OF[k.id] || [];
            return {
              tbl: t.tbl, glyph: k.glyph, rel: k.rel, pk: t.pk, rows: line(k.id, k.unit),
              arrow: open ? "▾" : "▸",
              bg: sel ? OK : "transparent",
              fg: sel ? INK : "var(--color-text)",
              sub: sel ? INK : "var(--color-neutral-500)",
              code: sel ? INK : "var(--color-accent)",
              chev: sel ? INK : "var(--color-neutral-500)",
              pkBg: sel ? "var(--color-bg)" : "var(--color-neutral-900)",
              pkFg: sel ? OK : "var(--color-accent-300)",
              openShow: open ? "flex" : "none", vectors: [],
              toggle: () => this.setState((p) => ({ gChild: p.gChild === k.id && p.gTable === k.id ? null : k.id, gTable: k.id })),
              subs: subs.map((sb) => {
                const st = GRAPH.find((g) => g.id === sb.id);
                const ssel = s.gTable === sb.id;
                return {
                  tbl: st.tbl, glyph: sb.glyph, rel: sb.rel, on: sb.on, pk: st.pk,
                  rows: line(sb.id, UNITS[sb.id]),
                  bg: ssel ? OK : "transparent",
                  fg: ssel ? INK : "var(--color-text)",
                  sub: ssel ? INK : "var(--color-neutral-500)",
                  code: ssel ? INK : "var(--color-accent)",
                  pkBg: ssel ? "var(--color-bg)" : "var(--color-neutral-900)",
                  pkFg: ssel ? OK : "var(--color-accent-300)",
                  vectors: [],
                  pick: () => this.setState({ gTable: sb.id })
                };
              })
            };
          })
        };
      })(),

      gCrumbs: (() => {
        const path = [];
        let cur = GRAPH.find((n) => n.id === s.gTable) || GRAPH[4];
        const up = (id) => { const e = GRAPH_EDGES.find((x) => x[0] === id); return e ? e[1] : null; };
        let guard = 0;
        while (cur && guard++ < 5) { path.unshift(cur); const p = up(cur.id); cur = p ? GRAPH.find((n) => n.id === p) : null; }
        return path.map((n, i) => ({
          label: n.tbl, sep: i < path.length - 1 ? "›" : "",
          color: i === path.length - 1 ? "var(--color-accent)" : "var(--color-neutral-400)",
          click: () => this.setState({ gTable: n.id })
        }));
      })(),
      tbl: (() => {
        const n = GRAPH.find((x) => x.id === s.gTable) || GRAPH[4];
        const kids = GRAPH_EDGES.filter((e) => e[1] === n.id).map((e) => GRAPH.find((x) => x.id === e[0]));
        const pars = GRAPH_EDGES.filter((e) => e[0] === n.id).map((e) => GRAPH.find((x) => x.id === e[1]));
        const live = this.glTable(n.id);
        return {
          name: live.table,
          // Counted in this database. The number compiled in beside each table was written
          // when the diagram was drawn and has never been compared to anything since.
          rows: typeof live.rows === "number" ? NUM(live.rows) : "?",
          rowsNote: live.why,
          rowsNoteShow: live.why ? "block" : "none",
          cols: n.cols.map((c) => ({
            name: c[0], type: c[1], key: c[2],
            keyShow: c[2] ? "inline" : "none",
            keyBg: c[2] === "PK" ? "var(--color-accent)" : "var(--color-neutral-900)",
            keyFg: c[2] === "PK" ? "var(--accent-ink)" : "var(--color-accent-300)",
            fg: c[2] ? "var(--color-text)" : "var(--color-neutral-400)"
          })),
          children: kids.map((k) => ({
            name: k.tbl,
            rows: (() => { const t = this.glTable(k.id); return typeof t.rows === "number" ? NUM(t.rows) : "?"; })(),
            on: "on " + n.pk,
            click: () => this.setState({ gTable: k.id, gChild: CHILD_OF_BUILDING.some((c) => c.id === k.id) ? k.id : s.gChild })
          })),
          leafShow: kids.length ? "none" : "block",
          parentShow: pars.length ? "flex" : "none",
          parents: pars.map((p) => ({ name: p.tbl, click: () => this.setState({ gTable: p.id }) })),
          hopNote: n.hop === 0 ? "This is the key. Everything resolves to it."
            : n.join ? "No building key of its own — it reaches a building only indirectly, through the records it references."
            : n.hop === 1 ? "One hop from a building — a direct foreign key."
            : "Two hops from a building — reached through its parent."
        };
      })(),

      // Live when the register answered; the compiled-in figures only when it did not.
      // Live when the register answered. When it did not, the schema three are what this
      // build was drawn against — but "24 buildings" and "1,204 documents" were typed in,
      // and a figure nobody can source reads as "—" rather than as a number.
      graphStats: this.bldVals().graphStatsLiveShow ? this.bldVals().graphStatsLive : [
        { value: String(GRAPH.length), label: "tables in the diagram", color: "var(--color-neutral-400)" },
        { value: String(GRAPH.reduce((a, g) => a + g.cols.length, 0)), label: "columns in the diagram", color: "var(--color-neutral-400)" },
        { value: String(GRAPH_EDGES.length), label: "relationships drawn", color: "var(--color-neutral-400)" },
        { value: "—", label: "buildings · register unreachable", color: "var(--color-neutral-500)" },
        { value: "—", label: "documents · register unreachable", color: "var(--color-neutral-500)" }
      ],
      graphNote: this.glHubNote(),
      graphLegend: [
        { label: "selected — click any node to expand", fill: "var(--color-accent)", stroke: "0", radius: "50%" },
        { label: "direct child · carries building_id", fill: "var(--color-bg)", stroke: "1.4px solid var(--color-accent)", radius: "50%" },
        { label: "shared by several buildings", fill: "var(--color-bg)", stroke: "1.2px solid var(--color-neutral-700)", radius: "50%" },
        { label: "reaches a building only indirectly", fill: "var(--color-accent-900)", stroke: "1.4px dashed var(--color-accent)", radius: "50%" },
        { label: "counts beside each node are read from the graph; “?” means that branch is not counted", fill: "var(--color-bg)", stroke: "1.2px dashed var(--color-neutral-700)", radius: "3px" }
      ],
      graphCodes: "FL floors · AS assets · DO documents · CO contracts · EQ equipment · ME meters · WO work orders · CE certificates · SP spaces · IN invoices",
      graphHops: [
        { n: "1", node: "Building", rel: ":HAS_FLOOR", gives: "the regulation pack and the benchmark it is held to" },
        { n: "2", node: "Floor", rel: ":HAS_ASSET", gives: "area by use, so consumption can be normalised" },
        { n: "3", node: "Asset", rel: ":METERED_BY", gives: "the plant item and its design load" },
        { n: "4", node: "Meter", rel: ":ASSIGNED_TO", gives: "half-hourly readings, weather-corrected" },
        { n: "5", node: "Work order", rel: ":SERVICES", gives: "what was actually done, and when" },
        { n: "6", node: "Vendor", rel: ":INVOICES", gives: "who attended, their accreditation, what they charged" }
      ].map((h, i) => ({
        n: h.n, node: h.node, rel: h.rel, gives: h.gives,
        dotBg: i === 0 ? "var(--color-accent)" : "var(--color-bg)",
        dotBorder: i === 0 ? "0" : "1.4px solid var(--color-accent)",
        dotFg: i === 0 ? "var(--accent-ink)" : "var(--color-accent)"
      })),
      graphOut: [
        { title: "Assets over design load", value: "23 of 2,847", color: "var(--st-risk)", note: "flagged against their own baseline and their peer group" },
        { title: "Energy attributable", value: "£186k", color: "var(--st-risk)", note: "annualised excess against benchmark, priced at contract tariff" },
        { title: "Paid to maintain them", value: "£412k", color: "var(--color-text)", note: "across 6 vendors — 4 with a PPM visit logged but no reading change" },
        { title: "Recoverable", value: "£38k", color: "var(--st-ok)", note: "service credits where the contract's outcome clause was missed" }
      ],
      // Buildings table — live rows from svc-operations-intelligence, seed as fallback (buildingsLive.js).
      ...this.bldVals(),
      ...this.bcVals(),
      ...this.bgVals(),

      navWidth: s.navOpen ? "248px" : "52px",
      orchWidth: (s.orchOpen && s.flow === "investigate" ? 420 : 280) + "px",
      shellPad: !s.signedIn ? "0px" : ((s.navOpen && s.view !== "home" ? 248 : 52) + (s.orchOpen ? (s.flow === "investigate" ? 420 : 280) : 0)) + "px",
      orchLeft: (s.navOpen && s.view !== "home" ? 248 : 52) + "px",
      contentCols: s.orchOpen ? "minmax(0,1fr)" : "minmax(0,1fr) 320px",
      modLastRun: "02:14 today",
      answerCols: s.orchOpen ? "minmax(0,1fr)" : "minmax(0,1.5fr) minmax(0,1fr)",
      tenantShow: s.orchOpen ? "none" : "flex",

      orchOpen: s.signedIn && s.orchOpen,
      orchTitle: s.orchTask ? s.orchTask.label : "Orchestrator",
      orchSteps: (s.orchTask ? s.orchTask.steps : []).map((st, i) => ({
        a: st.a, t: st.t,
        state: i < s.orchDone ? "done" : (i === s.orchDone ? "live" : "wait"),
        dot: i < s.orchDone ? "var(--st-ok)" : (i === s.orchDone ? "var(--color-accent)" : "var(--color-divider)"),
        fg: i <= s.orchDone ? "var(--color-text)" : "var(--color-neutral-500)",
        icon: i < s.orchDone ? "ph-check" : (i === s.orchDone ? "ph-circle-notch" : "ph-circle")
      })),
      orchFinished: !!s.orchTask && s.orchDone >= s.orchTask.steps.length,
      orchRunning: !!s.orchTask && s.orchDone < s.orchTask.steps.length,
      orchQuery: s.orchQuery,
      setOrchQuery: (e) => this.setState({ orchQuery: e.target.value }),
      orchKey: (e) => { if (e.key === "Enter" && s.orchQuery.trim()) { const q = s.orchQuery.trim(); this.setState({ orchQuery: "" }); this.orch(q, this.ctxLabel()); } },
      orchSubmit: () => { if (s.orchQuery.trim()) { const q = s.orchQuery.trim(); this.setState({ orchQuery: "" }); this.orch(q, this.ctxLabel()); } },
      closeOrch: () => this.closeOrch(),
      openOrch: () => this.setState({ orchOpen: true }),
      fInputs: s.flow === "inputs",
      fiTitle: s.fLabel,
      fiFields: (spec0 && spec0.inputs || []).map((fd) => ({
        label: fd.label, ph: fd.ph || "", value: s.fiVals[fd.key] || "",
        isText: fd.type !== "select" ? "flex" : "none",
        isSelect: fd.type === "select" ? "flex" : "none",
        inputType: fd.type === "date" ? "date" : "text",
        options: fd.options || [],
        set: (e) => { const v = e.target.value; this.setState((p) => ({ fiVals: Object.assign({}, p.fiVals, { [fd.key]: v }) })); }
      })),
      fiContinue: () => {
        if (!spec0) return;
        const m = spec0.mail(s.fiVals, s.fSubject || "the flagged item", s.fVendor || "the responsible vendor");
        this.setState({ flow: "email", emKind: spec0.k, emKicker: m.kicker, emTo: m.to, emSubject: m.subject, emBody: m.body });
      },

      fDeclare: s.flow === "declare",
      dStep1: s.declStep === 0, dStep2: s.declStep === 1, dStep3: s.declStep === 2,
      dStepLabel: "Step " + (s.declStep + 1) + " of 3 · " + ["building record", "schema written", "documents"][s.declStep],
      dValid: !!(s.decl.name && s.decl.state && s.decl.floors),
      dValidNote: s.decl.name && s.decl.state && s.decl.floors ? "" : "Name, state and floors are required — they key the record.",
      dFields: [
        { key: "name", label: "Building name", ph: "Bishopsgate Tower", type: "text" },
        { key: "cc", label: "Country", type: "select", options: ["UK", "US", "AE", "SG"] },
        { key: "state", label: "State or region", type: "select", options: REGIONS[s.decl.cc] || [] },
        { key: "use", label: "Primary use", type: "select", options: ["Commercial", "Retail", "Residential", "Mall", "Hospital", "Hotel", "Mixed"] },
        { key: "floors", label: "Floors", ph: "34", type: "text" },
        { key: "area", label: "Total floor area", ph: "412,000 ft²", type: "text" }
      ].map((fd) => ({
        label: fd.label, ph: fd.ph || "", value: s.decl[fd.key],
        isText: fd.type === "text" ? "block" : "none", isSelect: fd.type === "select" ? "block" : "none",
        options: fd.options || [],
        set: (e) => {
          const v = e.target.value;
          this.setState((p) => {
            const next = Object.assign({}, p.decl, { [fd.key]: v });
            if (fd.key === "cc") next.state = (REGIONS[v] || [""])[0];
            return { decl: next };
          });
        }
      })),
      dMixFields: [["mixC", "Commercial"], ["mixR", "Residential"], ["mixL", "Retail"], ["mixM", "Mall"], ["mixH", "Hospital"], ["mixT", "Hotel"]].map((m) => ({
        label: m[1], value: s.decl[m[0]],
        set: (e) => { const v = e.target.value; this.setState((p) => ({ decl: Object.assign({}, p.decl, { [m[0]]: v }) })); }
      })),
      dMixShow: s.decl.use === "Mixed" ? "flex" : "none",
      dMixTotal: (() => {
        const t = ["mixC", "mixR", "mixL", "mixM", "mixH", "mixT"].reduce((a, k) => a + (parseFloat(s.decl[k]) || 0), 0);
        return t ? t + "% allocated" : "unallocated";
      })(),
      dMixColor: (() => {
        const t = ["mixC", "mixR", "mixL", "mixM", "mixH", "mixT"].reduce((a, k) => a + (parseFloat(s.decl[k]) || 0), 0);
        return t === 100 ? "var(--st-ok)" : t > 100 ? "var(--st-risk)" : "var(--color-neutral-500)";
      })(),
      dNewId: "B-0" + String(BUILDINGS.length + 1).padStart(2, "0"),
      dName: s.decl.name || "the new building",
      dNext: () => this.setState((p) => ({ declStep: p.declStep + 1 })),
      dBack: () => this.setState((p) => ({ declStep: Math.max(0, p.declStep - 1) })),
      dIngestNow: () => this.setState({ flow: "ingest", declFor: s.decl.name || "the new building" }),
      dLater: () => this.setState({ flow: null, flowDone: (s.decl.name || "The building") + " is hoisted and keyed as " + ("B-0" + String(BUILDINGS.length + 1).padStart(2, "0")) + ". No documents ingested — its Hoist Score stays at 0% until they arrive. Run Ingest documents whenever you are ready." }),

      fIngest: s.flow === "ingest",
      iBuilding: s.declFor,
      iBuildingOpts: BUILDINGS.map((b) => b.name).concat(s.decl.name && !BUILDINGS.some((b) => b.name === s.decl.name) ? [s.decl.name] : []),
      setIBuilding: (e) => this.setState({ declFor: e.target.value }),
      iClasses: ["Certificates and statutory evidence", "Contracts and framework agreements", "Asset registers and PPM schedules", "Meter data and consent — MPAN / MPRN", "Invoices and service charge records"],
      iRun: () => this.setState({
        flow: null,
        flowDone: "Ingesting against " + s.declFor + ". Every document is stamped with that building's ID as a foreign key, so a certificate can never be orphaned from the asset it belongs to. Extraction, relationship mapping and Hoist Score recalculation run as one chain — you will be notified when coverage updates."
      }),

      fBooking: s.flow === "booking", fPick: s.flow === "pick", fNew: s.flow === "new",
      fEmail: s.flow === "email", fDone: !!s.flowDone, fDoneText: s.flowDone,
      fCancel: () => this.setState({ flow: null, flowDone: "" }),
      fNewVendor: () => this.setState({ flow: "new" }),

      bk: {
        vendor: bkVendor, vendorMeta: "Accreditation current · LEIA register · 4.2 first-time fix",
        scope: "Statutory inspection and certificate issue — " + (s.fSubject || "flagged obligation"),
        date: s.bkDate, window: s.bkWindow, locked: s.bkLocked ? "true" : "",
        note: s.bkLocked ? "Dates fetched from the vendor's earliest available slot. Edit to override." : "Editing — set the date and window you want, then approve.",
        editLabel: s.bkLocked ? "Edit" : "Lock",
        setDate: (e) => this.setState({ bkDate: e.target.value }),
        setWindow: (e) => this.setState({ bkWindow: e.target.value }),
        edit: () => this.setState((p) => ({ bkLocked: !p.bkLocked })),
        approve: () => this.setState({
          flow: "email", bkLocked: true,
          emKind: "booking", emKicker: "Booking instruction · draft",
          emTo: (bkVendor === "Apex Lifts" ? "ops@apexlifts.co.uk" : "ops@" + bkVendor.toLowerCase().replace(/[^a-z]/g, "") + ".co.uk"),
          emSubject: "Booking instruction — " + (s.fSubject || "statutory inspection") + ", " + s.bkDate,
          emBody: "Dear " + bkVendor + ",\n\nPlease attend to carry out the statutory inspection and certificate issue for " + (s.fSubject || "the obligation named below") + ".\n\nDate: " + s.bkDate + "\nWindow: " + s.bkWindow + "\n\nAccess will be arranged for the window above. Please confirm attendance by return, and upload the satisfactory certificate on completion — it is ingested directly into our compliance record.\n\nThis instruction is issued under the existing framework agreement at contracted rates.\n\nKind regards,\nPlanum Technologies"
        })
      },

      vendorPool: VENDOR_POOL.concat(s.vendors).map((v) => ({
        name: v.name, spec: v.spec, acc: v.acc,
        pick: () => this.setState({
          flow: "email",
          emKind: "booking", emKicker: "Booking instruction · draft",
          emTo: v.email || "ops@" + v.name.toLowerCase().replace(/[^a-z]/g, "") + ".co.uk",
          emSubject: "Booking instruction — " + (s.fSubject || "statutory inspection") + ", " + s.bkDate,
          emBody: "Dear " + v.name + ",\n\nYou have been assigned as the responsible contractor for " + (s.fSubject || "the obligation named below") + ", replacing the previously appointed vendor.\n\nDate: " + s.bkDate + "\nWindow: " + s.bkWindow + "\n\nYour accreditation has been verified as " + v.acc + ". Please confirm attendance by return, and upload the satisfactory certificate on completion — it is ingested directly into our compliance record.\n\nThis instruction is issued under the existing framework agreement at contracted rates.\n\nKind regards,\nPlanum Technologies",
          fVendor: v.name
        })
      })),
      poolEmpty: VENDOR_POOL.concat(s.vendors).length === 0,

      nvFields: [
        { label: "Name", key: "name", ph: "Northgate Lift Services Ltd" },
        { label: "Email", key: "email", ph: "ops@northgate.co.uk" },
        { label: "Vendor ID", key: "id", ph: "V-0142" },
        { label: "Phone", key: "phone", ph: "+44 20 7946 0821" }
      ].map((f) => ({
        label: f.label, ph: f.ph, value: s.nv[f.key],
        set: (e) => { const v = e.target.value; this.setState((p) => ({ nv: Object.assign({}, p.nv, { [f.key]: v }) })); }
      })),
      nvSpec: s.nvSpec,
      setNvSpec: (e) => this.setState({ nvSpec: e.target.value }),
      specOpts: ["Lifts — LOLER", "Gas — Gas Safe", "Electrical — NICEIC", "Water hygiene — L8", "Fire safety", "HVAC and refrigeration", "Building fabric"],
      fCreateVendor: () => {
        const v = { name: s.nv.name || "Unnamed contractor", spec: s.nvSpec, acc: "Pending verification", email: s.nv.email || "ops@contractor.com" };
        this.setState((p) => ({
          vendors: p.vendors.concat([v]), flow: "email",
          nv: { name: "", email: "", id: "", phone: "" },
          emKind: "quote", emKicker: "Quote request · draft", emTo: v.email,
          emSubject: "Request for quotation — " + s.nvSpec.split(" — ")[0].toLowerCase() + " works",
          emBody: "Dear " + v.name + ",\n\nYou have been added to the Planum Technologies contractor register for " + s.nvSpec + ".\n\nWe would like to invite a quotation for statutory inspection and remedial works at the property named in the attached scope. Please confirm your accreditation reference and earliest available attendance date with your price.\n\nQuotations received within five working days will be considered for immediate award.\n\nKind regards,\nPlanum Technologies"
        }));
        this.flash(v.name + " created — accreditation pending verification.");
      },

      em: {
        kicker: s.emKicker, to: s.emTo, subject: s.emSubject, body: s.emBody,
        setTo: (e) => this.setState({ emTo: e.target.value }),
        setBody: (e) => this.setState({ emBody: e.target.value }),
        send: () => this.setState({
          flow: s.emKind === "investigate" && s.inv ? "investigate" : null,
          inv: s.emKind === "investigate" && s.inv ? Object.assign({}, s.inv, { replies: (s.inv.replies || []).concat([{ you: "Send the email", bot: "Sent to " + (s.emTo || "the FM lead") + ". The reply lands on this case; a document attached to it is ingested and bound to the asset record automatically." }]) }) : s.inv,
          flowDone: s.emKind === "investigate" ? "" : spec0 && spec0.done && spec0.k === s.emKind ? spec0.done(s.fiVals, s.fLabel, s.fSubject) : s.emKind === "quote"
            ? "Quote request sent to " + (s.emTo || "the contractor") + ". The reply is watched and a scorecard opens on award."
            : s.emKind === "booking"
              ? "Booking instruction sent to " + (s.emTo || "the vendor") + ". Work order raised for " + s.bkDate + " and the certificate is expected on completion."
              : "Extension request sent to " + (s.emTo || "the authority") + ". The obligation is marked as contested pending their reply."
        })
      },

      orchRecent: s.sessions.filter((q) => q.kind === "task" && q !== s.orchTask).slice(0, 4).map((q) => ({ label: q.label, when: q.when })),
      orchHasRecent: s.sessions.filter((q) => q.kind === "task" && q !== s.orchTask).length > 0,
      orchLiveDot: s.orchTask && s.orchDone < s.orchTask.steps.length ? "block" : "none",
      orchStatus: !s.orchTask ? "Idle — instruct it below, or trigger any action on the page." : (s.orchDone < s.orchTask.steps.length ? "Running · step " + (s.orchDone + 1) + " of " + s.orchTask.steps.length : "Complete · logged to the Activity Log and stored as a session"),
      navOpen: s.navOpen, navClosed: !s.navOpen,
      navOverlay: s.navOpen && s.view === "home",
      toggleNav: () => this.setState((p) => ({ navOpen: !p.navOpen })),
      closeNav: () => this.setState({ navOpen: false }),
      newQuery: () => this.setState({ view: "home", query: "", detail: null, navOpen: false }),
      newSpace: () => this.orch("Create space", "Spaces"),
      addBuilding: () => this.bcOpenForm(),
      allSessions: () => this.flash("Full session history opens in the workspace archive."),

      /* Spaces and sessions in the navigator read the same state the home page
         does — a space is a saved scope, a session is a query or an
         orchestrator task, and both reopen where they came from. */
      navSpaces: [
        { name: "Compliance", n: "3 lapsed", key: "compliance" },
        { name: "Energy", n: "6 anomalies", key: "energy" },
        { name: "Vendor performance", n: "3 below 80", key: "vendors" },
        { name: "Vendor operations", n: "14 to approve", key: "ops" }
      ].map((x) => ({ name: x.name, n: x.n, click: () => this.openModule(x.key) })),

      navSessions: s.sessions.map((q) => ({
        label: q.label,
        when: q.when,
        icon: q.kind === "task" ? "ph-lightning"
          : q.k === "compliance" ? "ph-shield-check"
          : q.k === "energy" ? "ph-lightning"
          : q.k === "vendors" ? "ph-chart-line-up"
          : "ph-magnifying-glass",
        click: () => {
          if (q.kind === "task") return this.setState({ orchOpen: true, navOpen: true });
          if (q.k) return this.openModule(q.k);
          return this.setState({ queueOpen: true });
        }
      })),

      navSections: [
        { label: "Buildings", icon: "ph-buildings", key: "buildings", count: 24 },
        { label: "Buildings", icon: "ph-buildings", key: "buildings_user", count: "" },
        { label: "Compliance", icon: "ph-shield-check", key: "compliance", count: 3 },
        { label: "Vendors", icon: "ph-chart-line-up", key: "vendors", count: 3 },
        { label: "Energy", icon: "ph-lightning", key: "energy", count: 6 },
        { label: "Assets (Pending)", icon: "ph-cube", key: "assets" },
        { label: "Work orders (Pending)", icon: "ph-wrench", key: "ops", count: 14 }
      ].filter((n) => s.role === "admin" ? n.key === "buildings" : n.key !== "buildings").map((n) => {
        const active = (s.view === "module" && s.module === n.key)
          || (s.view === "buildings" && n.key === "buildings" && s.role === "admin")
          || (s.view === "buildings" && n.key === "buildings_user" && s.role !== "admin")
          || (s.view === "cc" && n.key === "compliance")
          || (s.view === "vp" && n.key === "vendors");
        return {
          label: n.label, icon: n.icon, count: n.count || "", show: n.count ? "flex" : "none",
          color: active ? "var(--color-accent)" : "var(--color-neutral-300)",
          chip: active ? "var(--color-accent-900)" : "transparent",
          click: () => {
            if (n.key === "buildings") { window.scrollTo(0, 0); return this.setState({ view: "buildings", role: "admin", navOpen: true, detail: null }); }
            if (n.key === "buildings_user") { window.scrollTo(0, 0); return this.setState({ view: "buildings", role: "user", navOpen: true, detail: null }); }
            if (n.key === "compliance") { window.scrollTo(0, 0); return this.setState({ view: "cc", navOpen: true, detail: null }); }
            if (n.key === "vendors") { window.scrollTo(0, 0); return this.setState({ view: "vp", navOpen: true, detail: null }); }
            if (n.key === "assets") return this.setState({ view: "report", reportKey: "assetrisk", navOpen: true, detail: null });
            return this.openModule(n.key);
          }
        };
      }),

      isReport: s.signedIn && s.view === "report",
      isCC: s.signedIn && s.view === "cc",
      ccScan: () => this.ccRunScan(),
      ccLastRun: s.ccLastScan ? fmtTime(s.ccLastScan) + " · scan" : s.ccLoadedAt ? fmtTime(s.ccLoadedAt) + " · register read" : (s.ccLoading ? "loading…" : "not yet · seed data"),
      ccLive: !!s.ccLive,
      ccSourceLabel: s.ccLive ? "Live · svc-operations-intelligence" + (s.ccError ? " · refresh failed" : "") : s.ccLoading ? "Connecting to svc-operations-intelligence…" : "Seed data · backend unreachable",
      ccSourceDot: s.ccLive ? (s.ccError ? "var(--st-warn)" : "var(--st-ok)") : s.ccLoading ? "var(--color-neutral-500)" : "var(--st-warn)",
      ccSourceDetail: s.ccError || "",
      ccRetryShow: !s.ccLoading && (!s.ccLive || !!s.ccError) ? "inline" : "none",
      ccRetry: () => this.ccRetryNow(),
      ccPanelOpen: s.ccPanel,
      ccPanelLabel: s.ccPanel ? "− Close" : "+ Add scope",
      ccTogglePanel: () => this.setState((p) => ({ ccPanel: !p.ccPanel })),
      ccClear: () => this.setState({ ccCountries: [], ccStates: [], ccBuildings: [], ccTile: null }),
      ccSummary: cc.nCountries + (cc.nCountries === 1 ? " country · " : " countries · ")
        + cc.bs.length + (cc.bs.length === 1 ? " building · " : " buildings · ")
        + cc.vs.length + (cc.vs.length === 1 ? " vendor · " : " vendors · ")
        + cc.certs.length + (cc.certs.length === 1 ? " certificate" : " certificates"),

      nyViews: [["building", "Building view"], ["vendor", "Vendor view"]].map((v) => ({
        label: v[1],
        bg: s.nyView === v[0] ? "var(--color-surface)" : "transparent",
        fg: s.nyView === v[0] ? "var(--color-text)" : "var(--color-neutral-500)",
        pick: () => this.setState({ nyView: v[0] })
      })),
      nyItems: cc.needs,

      ccChips: cc.chips,
      ccCols: cc.cols,
      ccTiles: cc.tiles,
      ccPins: cc.pins,
      ccTicks: [{ label: "lapsed", color: "var(--st-risk)" }].concat(runwayTicks().map((l) => ({ label: l, color: "var(--color-neutral-500)" }))),
      ccLegend: [
        { label: "solid — lapsed or blocked", bg: "var(--st-risk)", border: "0" },
        { label: "ring — expiring within 90 days", bg: "var(--color-surface)", border: "2.5px solid var(--st-warn)" },
        { label: "current", bg: "var(--st-ok)", border: "0" },
        { label: "shaded band = next 90 days", bg: "var(--color-divider)", border: "0" }
      ],

      ccCols2: s.ccPivot === "matrix" ? "minmax(0,1fr)" : "repeat(auto-fit,minmax(360px,1fr))",
      ccIsMatrix: s.ccPivot === "matrix",
      ccIsList: s.ccPivot !== "matrix",
      ccListTitle: s.ccPivot === "buildings" ? "Buildings in scope" : s.ccPivot === "vendors" ? "Vendors in scope" : "Requirement matrix",
      ccPivots: [["buildings", "Buildings"], ["vendors", "Vendors"], ["matrix", "Matrix"]].map((p) => ({
        label: p[1],
        bg: s.ccPivot === p[0] ? "var(--color-surface)" : "transparent",
        fg: s.ccPivot === p[0] ? "var(--color-text)" : "var(--color-neutral-500)",
        pick: () => this.setState({ ccPivot: p[0], ccTab: 0 })
      })),
      ccRows: cc.rows,
      ccMxCols: "minmax(180px,1.4fr) repeat(" + cc.mxTypes.length + ", 62px)",
      ccMxHead: cc.mxShort.map((sh, i) => ({ label: sh, tip: cc.mxTypes[i] })),
      ccMxRows: cc.mxRows,
      ccMxLegend: ["ok", "warn", "risk", "gap", "na"].map((k) => Object.assign({ label: cc.mxLabel[k], glyph: cc.mxGlyph[k] }, MK[k])),

      ccCrumb: cc.crumb,
      ccFocusName: cc.focus ? cc.focus.name : "",
      ccFacts: cc.facts,
      ccTabs: cc.tabs,
      ccPaneCerts: s.ccTab === 0,
      ccPaneVendors: s.ccTab === 1 && s.ccFocus.kind === "building",
      ccPaneServed: s.ccTab === 1 && s.ccFocus.kind === "vendor",
      ccPaneGaps: s.ccTab === 2,
      ccServedRows: cc.servedRows,
      ccServedEmpty: cc.servedRows.length ? "none" : "block",
      ccCertRows: cc.certRows,
      ccCertEmpty: cc.certRows.length ? "none" : "block",
      ccVendorRows: cc.vendorRows,
      ccVendorEmpty: cc.vendorRows.length ? "none" : "block",
      ccGapRows: cc.gapRows,
      ccFocused: cc.queueOpenCC, ccBrowse: !cc.queueOpenCC,
      ccScopeNote: "within " + cc.ccScopeNote,
      qTitle: cc.qTitle, qHint: cc.qHint, qCount: cc.qCount,
      qModeCerts: cc.qModeCerts, qModeBuildings: cc.qModeBuildings, qModeGaps: cc.qModeGaps,
      qBuildingRows: cc.qBuildingRows, qGapRows: cc.qGapRows,
      qItems: cc.qItems, qEmpty: cc.qEmpty, closeCCQueue: cc.closeCCQueue,
      report: {
        title: rep ? rep.name : "",
        kicker: rep ? "Built from the session “" + rep.src + "”." : "",
        summary: "Risky buildings have been evaluated on four parameters: outlier consumption against the peer group, anomaly against the building's own 12-week baseline, deficit against design load, and EUI against its regulation-pack benchmark. Any building failing one or more is listed below; the table re-reads the Hoist Graph on every refresh.",
        lastRun: rep && rep.ready ? "Last refreshed " + repCad.last : "First refresh pending",
        meta: rep && rep.ready
          ? repCad.label + " · re-reads the Hoist Graph on each run"
          : repCad.label + " · first run pending"
      },
      reportPending: !!rep && !rep.ready,
      reportReady: !!rep && !!rep.ready,
      exportReport: () => this.orch("Export report", rep ? rep.name : "Reports"),

      reportMenu: s.reportMenu,
      reportName: s.reportName,
      setReportName: (e) => this.setState({ reportName: e.target.value }),
      toggleReportMenu: () => this.setState((p) => ({ reportMenu: !p.reportMenu, reportName: "" })),
      cancelReport: () => this.setState({ reportMenu: false, reportName: "" }),
      createReport: () => {
        const src = s.sessions.filter((q) => q.kind !== "task")[s.reportSrc].label;
        const name = (s.reportName || "").trim() || "Untitled report";
        const cad = CADENCES[s.reportCad] || CADENCES[1];
        const label = CADENCE_LABEL(s);
        const key = "r" + Date.now();
        this.setState((p) => ({
          reports: p.reports.concat([{ key, name, badge: CADENCE_BADGE(s), src, cad: label, last: cad.last, ready: false }]),
          reportMenu: false, reportName: "", view: "report", reportKey: key, detail: null
        }));
        this.orch("Create report “" + name + "” — " + label.toLowerCase(), "Reports");
      },
      reportCadences: CADENCES.map((c, i) => ({
        label: c.label,
        tick: i === s.reportCad ? "ph-radio-button" : "ph-circle",
        color: i === s.reportCad ? "var(--color-accent)" : "var(--color-neutral-500)",
        chip: i === s.reportCad ? "var(--color-accent-900)" : "transparent",
        pick: () => this.setState({ reportCad: i })
      })),
      reportDaysShow: (CADENCES[s.reportCad] || {}).pickDays ? "flex" : "none",
      reportDays: DAYS.map((d, i) => {
        const on = s.reportDays.indexOf(i) > -1;
        return {
          label: d.charAt(0),
          title: d,
          bg: on ? "var(--color-accent)" : "transparent",
          fg: on ? "var(--accent-ink)" : "var(--color-neutral-400)",
          edge: on ? "var(--color-accent)" : "var(--color-divider)",
          pick: () => this.setState((p) => ({
            reportDays: p.reportDays.indexOf(i) > -1 ? p.reportDays.filter((x) => x !== i) : p.reportDays.concat([i]).sort()
          }))
        };
      }),
      reportTime: s.reportTime,
      setReportTime: (e) => this.setState({ reportTime: e.target.value }),
      reportCadenceNote: "The session's query is pinned and re-run " + CADENCE_LABEL(s).replace(/^Refresh /, "") + ". The report shows its last refresh at the top.",
      reportSources: s.sessions.filter((q) => q.kind !== "task").slice(0, 5).map((q, i) => ({
        label: q.label,
        tick: i === s.reportSrc ? "ph-radio-button" : "ph-circle",
        color: i === s.reportSrc ? "var(--color-accent)" : "var(--color-neutral-500)",
        chip: i === s.reportSrc ? "var(--color-accent-900)" : "transparent",
        pick: () => this.setState({ reportSrc: i })
      })),

      navReports: (s.role === "admin" ? [] : s.reports).map((r) => {
        const active = s.view === "report" && s.reportKey === r.key;
        return {
          name: r.name, badge: r.ready ? "30 min" : "Pending",
          color: active ? "var(--color-accent)" : "var(--color-neutral-300)",
          chip: active ? "var(--color-accent-900)" : "transparent",
          click: () => this.setState({ view: "report", reportKey: r.key, navOpen: true, detail: null })
        };
      }),

      // Aggregated from the session's own asset table, so the report re-reads
      // whenever the UDR changes rather than holding a copy.
      riskBuildings: (() => {
        const by = {};
        ASSET_RISK.forEach((a) => {
          const r = by[a.building] || (by[a.building] = { name: a.building, Outlier: 0, Anomaly: 0, Deficit: 0, risk: 0 });
          r[a.klass] = (r[a.klass] || 0) + 1;
          if (a.risk === "Yes") r.risk += 1;
        });
        const N = (n, c) => ({ v: n ? String(n) : "—", c: n ? c : "var(--color-neutral-600)" });
        return Object.keys(by).map((k) => {
          const r = by[k];
          const b = BUILDINGS.find((x) => x.name === k);
          const o = N(r.Outlier, "var(--st-risk)"), an = N(r.Anomaly, "var(--st-warn)"), d = N(r.Deficit, "var(--st-warn)");
          const over = b && b.euiN > b.benchN;
          return {
            name: r.name,
            out: o.v, outColor: o.c, anom: an.v, anomColor: an.c, def: d.v, defColor: d.c,
            eui: b ? (over ? "+" : "") + Math.round(((b.euiN - b.benchN) / b.benchN) * 100) + "%" : "—",
            euiColor: over ? "var(--st-risk)" : "var(--st-ok)",
            risk: r.risk ? "Yes · " + r.risk : "No",
            riskColor: r.risk ? "var(--st-risk)" : "var(--st-ok)",
            click: () => this.setState({ view: "buildings", role: "admin", gBuilding: r.name, navOpen: true })
          };
        });
      })(),

      queuePreview: D.decisions.slice(0, 3).map((d) => ({
        title: d.title, meta: d.meta, money: d.money, icon: d.icon,
        color: t(d.tone).color, bg: t(d.tone).bg, click: () => this.detailFromDecision(d)
      })),
      queueItems: D.decisions.map((d) => ({
        title: d.title, meta: d.meta, money: d.money, icon: d.icon, module: d.module,
        color: t(d.tone).color, bg: t(d.tone).bg, click: () => this.detailFromDecision(d)
      })),

      crons: (() => {
        const order = ["Today", "Yesterday", "31 Aug"];
        const live = CRONS.filter((c) => !s.cronsGone.includes(c.text));
        const out = [];
        order.forEach((day) => {
          const rows = live.filter((c) => (c.day || "Today") === day).sort((a, b) => b.t.localeCompare(a.t));
          if (!rows.length) return;
          out.push({ sep: true, day });
          rows.forEach((r) => out.push(r));
        });
        return out;
      })().map((c, i) => ({
        isSep: c.sep ? "flex" : "none", isRow: c.sep ? "none" : "grid", day: c.day || "",
        text: c.text || "", agent: c.agent || "", t: c.t || "", dot: c.dot || "transparent", action: c.action || "",
        op: !c.sep && s.cronPulse === i ? "0.5" : "1",
        actShow: c.action ? "block" : "none",
        noneShow: c.sep || c.action ? "none" : "inline",
        tip: c.sep ? c.day : c.text + (c.action ? " — " + c.action : " — no action needed") + " · " + c.agent + " " + c.t,
        act: () => c.sep ? null : (c.action ? this.orch(c.action, c.agent) : this.flash(c.text)),
        dismiss: () => this.setState((p) => ({ cronsGone: p.cronsGone.concat([c.text]) }))
      })),


      spaces: [
        { name: "Compliance", sub: "118 obligations · 4 regulation packs", icon: "ph-shield-check", badge: "3 lapsed", tone: "risk", key: "compliance" },
        { name: "Energy", sub: "24 buildings · half-hourly MPAN", icon: "ph-lightning", badge: "6 anomalies", tone: "warn", key: "energy" },
        { name: "Vendor performance", sub: "6 contracted vendors · Aug scorecard", icon: "ph-chart-line-up", badge: "3 below 80", tone: "warn", key: "vendors" },
        { name: "Vendor operations", sub: "148 live work orders · PPM 92%", icon: "ph-wrench", badge: "14 to approve", tone: "ok", key: "ops" }
      ].map((x) => ({ ...x, color: t(x.tone).color, bg: t(x.tone).bg, click: () => this.openModule(x.key) })),

      pnl: D.pnl.map((r) => ({ ...r, color: t(r.tone).color })),

      answer: answer || { scope: "", title: "", takeaway: "", caveat: "", rowHead: [], chain: [] },
      answerMetrics: answer ? answer.metrics.map((m) => ({ ...m, color: t(m.tone).color })) : [],
      answerRows: answer ? answer.rows.map((r) => ({
        c0: r[0], c1: r[1], c2: r[2], c3: r[3],
        color: r[2].includes("Lapsed") || r[2].includes("Statutory") || r[2].startsWith("£3") ? TONE.risk.color : TONE.warn.color,
        click: () => { const d = D.decisions.find((x) => x.title.includes(r[0]) || x.meta.includes(r[0])); if (d) this.detailFromDecision(d); else this.flash("Opening the record for " + r[0] + "."); }
      })) : [],
      answerActions: answer ? mkActions(answer.actions) : [],
      refinements: answer ? [
        { label: "Narrow this to my three worst buildings", run: () => this.orch("Narrow to three worst buildings", "Query") },
        { label: "Show me what this cost last quarter", run: () => this.orch("Fetch previous quarter", "Query") },
        { label: "Pin this as a weekly run", run: () => this.orch("Pin as weekly run", "Query") }
      ] : [],

      mod: mod || { name: "", head: [] },
      modMetrics: !mod ? [] : modKey === "energy"
        ? this.enVals(s).enScopeCards
        : D.answers[mod.answer].metrics.map((m) => ({ ...m, color: t(m.tone).color })),
      modFilters: mod ? mod.filters.map((f) => ({
        label: f,
        border: f === s.filter ? "var(--color-accent)" : "var(--color-divider)",
        fg: f === s.filter ? "var(--color-accent)" : "var(--color-neutral-400)",
        bg: f === s.filter ? "var(--color-accent-900)" : "transparent",
        click: () => this.setState({ filter: f })
      })) : [],
      modAsks: mod ? mod.asks.map((a) => ({ label: a, run: () => this.ask(a) })) : [],
      modBars: this.bars(modKey).map((b) => Object.assign({ groupShow: "none", rowShow: "flex", invShow: "none", group: "", groupMeta: "", investigate: null }, b)),
      modRows: this.rows(modKey, s.filter).map((r) => Object.assign({ groupShow: "none", rowShow: "table-row", invShow: "none", group: "", groupMeta: "", groupSum: "", investigate: null }, r)),
      modInvHead: modKey === "energy" ? "table-cell" : "none",

      detail: detail ? { ...detail, color: t(detail.tone).color } : { chain: [], color: "var(--color-accent)" },
      detailFields: detail ? (detail.fields || []).map((f) => ({
        l: f.l, v: f.v,
        fg: f.editable ? "var(--color-accent)" : "var(--color-neutral-200)",
        underline: f.editable ? "dashed" : "solid transparent",
        cursor: f.editable ? "pointer" : "default",
        icon: f.editable ? "ph-pencil-simple" : "ph-lock-simple",
        iconOp: f.editable ? "1" : "0.3",
        edit: () => f.editable ? this.orch("Edit " + f.l.toLowerCase(), detail.title) : this.flash(f.l + " is a fixed parameter you cannot change.")
      })) : [],
      detailActions: detail ? mkActions(detail.actions || []) : []
    };

    if (mod) {
      const en = modKey === "energy" ? this.enVals(s) : null;
      if (en) {
        vals.modAsks = en.enAsks.map((a) => ({ label: a, run: () => this.ask(a) }));
        vals.abChips = en.enAsks.map((a) => ({ label: a, run: () => this.ask(a) }));
      }
      vals.mod = {
        ...mod,
        sideTitle: en ? en.enSideTitle : mod.sideTitle,
        sideFoot: en ? en.enSideFoot : mod.sideFoot,
        scan: () => this.orch(mod.scanLabel, mod.name),
        export: () => this.orch(mod.exportLabel, mod.name)
      };
    }
    return this.cvt(vals);
  }
};
