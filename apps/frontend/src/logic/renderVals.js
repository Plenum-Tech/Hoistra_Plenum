// renderVals — the view model — everything the templates read.
// Methods are mixed into HoistraLogic.prototype; `this` is the controller.
import { USE_TINT, BUILDINGS, GRAPH, GRAPH_EDGES, GB, HUBS, SHARED_N, CHILD_OF_BUILDING, VECTOR_CLASSES, VFILES, UNITS, PER_BUILDING, NUM, SUB_OF, REGIONS, PACKS, ACTION_SPECS, CC, VP, PKG, MK, VENDOR_POOL, CRONS, TONE, t, MODULES } from './constants.js';
import { fmtTime, runwayTicks, overdueBars } from './complianceLive.js';
import { COUNTRY_SHORT, fmtDateTime } from './homeLive.js';
import { domainOf } from './chat.js';
import { CADENCES, DAYS, cadenceLabel, cadenceBadge } from './reports.js';
import { ago, shapeSessionList, sessionIcon } from './sessions.js';
import { filterBuildings, PAGE_SIZE } from './buildingsLive.js';
import { documentUrl } from '../api/docRag.js';

// Stage → icon for the trace rail. The pipeline stages svc-deepagents emits; anything it
// adds later falls back to a generic mark rather than disappearing from the run.
const TRACE_ICON = {
  route: 'ph-signpost', docs: 'ph-file-text', scope: 'ph-crosshair', plan: 'ph-list-checks',
  data: 'ph-database', analyse: 'ph-brain', validate: 'ph-shield-check', review: 'ph-eye',
  revise: 'ph-pencil-simple'
};

// A document's stored name, shortened to the part a person wrote.
//
// The real records carry the filename their ingestion pipeline produced, and each stage
// prepended its own id:
//
//   cf30c299-…-a41_9c519d0f…41c_0927bdcc-…770_79711cc5…069_ten-building-1786715110_FRA_building.pdf
//
// which fills the column and hides the only informative part, at the end. This strips
// leading uuid_ / 32-hex_ segments for display only — plenum_cafm.documents keeps the
// name exactly as ingested, and the row's detail flash still shows it in full. If a name
// is nothing BUT ids, the original is kept rather than rendering an empty cell.
const _FILE_ID_PREFIX =
  /^(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|[0-9a-f]{32})_/i;
export function tidyFileName(name) {
  let out = String(name == null ? "" : name);
  while (_FILE_ID_PREFIX.test(out)) out = out.replace(_FILE_ID_PREFIX, "");
  return out.trim() || String(name == null ? "" : name);
}

// How a document-ish row should be drawn, given what sits behind it.
//
// Two questions, asked in order, because the answers are different findings:
//
//   hasRef    does this row name a document at all? (document_id on the row)
//   hasFile   is there anything to serve for it — a stored file, or extracted text?
//
// A certificate that names no document is a gap in the register — somebody recorded a
// certificate and nothing was ever filed against it. A certificate whose document we do not
// hold is a filing problem: the scan is somewhere else. Both used to render as "no file",
// which told a reader neither, and only the first is a compliance finding.
//
// Both flags are three-valued and the third value matters: null means the branch does not
// track that at all, and drawing it as "no" would be a new untruth in place of the old one.
export function fileAffordance(hasFile, hasRef, labels) {
  const L = labels || {};
  // Dimmed and inert. The row is still a record worth reading — that is what the detail
  // line and the id are for — but there is nothing to open.
  //
  // Mixed rather than picked: the palette's next step down (--color-neutral-700, #CFCCC2)
  // sits at about 1.5:1 on the page ground, which is not a dimmed icon but an invisible
  // one. Half-way to the background reads as disabled and still reads.
  const dim = "color-mix(in srgb, var(--color-neutral-500) 55%, var(--color-bg))";

  if (hasRef === false) {
    return {
      held: false,
      evidenced: false,
      // Not the same icon as "no file", and not the same colour: this one is a finding, so
      // it carries the warning tone the rest of the console uses for a gap rather than the
      // grey it uses for something merely unavailable.
      viewIcon: "ph ph-file-dashed",
      viewColor: "var(--st-warn)",
      viewCursor: "default",
      viewTitle: L.noRefTitle || "No document behind this record — nothing was filed against it",
      dlShow: "none",
      note: L.noRef || " · no document",
    };
  }
  if (hasFile === false) {
    return {
      held: false,
      evidenced: true,
      viewIcon: "ph ph-eye-slash",
      viewColor: dim,
      viewCursor: "default",
      viewTitle: L.noFileTitle || "No file held — this row records that the document exists",
      dlShow: "none",
      note: L.noFile || " · no file",
    };
  }
  return {
    held: hasFile === true,
    evidenced: hasRef !== false,
    viewIcon: "ph ph-eye hv6",
    viewColor: "var(--color-neutral-500)",
    viewCursor: "pointer",
    viewTitle: "View",
    dlShow: "inline-block",
    note: "",
  };
}

export const renderValsMethods = {
  renderVals() {
    const D = this.D();
    const s = this.state;
    if (!D) return {};
    // The pages that render the live orchestrator transcript: the dock pages (in their dock)
    // and the chat page (as the page). Home is a dock page too, but it has no dock until a
    // question or a carried-over task opens one, so it only counts once that has happened.
    const chatView = ["cc", "chat", "vp", "buildings"].indexOf(s.view) > -1 || (s.view === "home" && !!s.orchOpen);
    // The chat page carries the run in its trace rail, so an answer's own copy of the steps
    // starts collapsed there. The console's dock has no rail and keeps it open.
    const stepsDefault = s.view !== "chat";

    const mkActions = (labels, ctx) => labels.map((l, i) => ({
      label: l, cls: i === 0 ? "btn-primary" : "btn-secondary",
      click: () => {
        const low = l.toLowerCase();
        // The console is the live register; the module is the seed table. Check the
        // console first because its label also contains "open compliance".
        if (low.includes("compliance console")) { window.scrollTo(0, 0); return this.setState({ view: "cc", navOpen: true, detail: null, queueOpen: false }); }
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

    // Dock width. orchBaseW is the designed width for the current flow and acts as the
    // floor; ORCH_MAX_W is the hard ceiling so a drag cannot swallow the page beside it.
    const orchBaseW = s.flow === "investigate" ? 420 : 280;
    const ORCH_MAX_W = 720;
    const orchW = Math.max(orchBaseW, Math.min(ORCH_MAX_W, s.orchW || orchBaseW));

    const detail = s.detail;
    const modKey = s.module;
    const mod = modKey ? MODULES[modKey] : null;
    const answer = s.answerKey ? D.answers[s.answerKey] : null;
    const rep = s.reports.find((r) => r.key === s.reportKey) || null;
    // Vendors: the live model from svc-operations-intelligence once it has loaded, the seed
    // otherwise (vendorsLive.js). Both expose the same shape — a directory, a record per
    // vendor and the scorecard rows behind each published score — so the vp* section renders
    // either without knowing which it holds.
    const vm = this.vpModel();
    const VD = vm.live
      ? {
          vendors: vm.vendors, V: vm.V, pkgOf: vm.pkgOf,
          // The published score is the engine's; the rows are its own components.
          score: (id) => { const R = vm.V[id]; return R ? { raw: R.raw, score: R.score, rows: R.rows } : { raw: 0, score: null, rows: [] }; }
        }
      : {
          vendors: D.vendors, V: VP.V, pkgOf: (id) => PKG[id] || "Other",
          // The rows ARE the score: sum them, then apply the accreditation cap.
          score: (id) => {
            const rec = VP.V[id];
            if (!rec) return { raw: 0, score: 0, rows: [] };
            const sc = VP.scorecard(rec);
            const vend = D.vendors.find((x) => x.id === id) || {};
            return { raw: sc.raw, score: vend.accred === "Lapsed" ? Math.min(60, sc.raw) : sc.raw, rows: sc.rows };
          }
        };
    const firstId = VD.vendors.length ? VD.vendors[0].id : null;
    // Seed click targets name vendors by id; when the live directory holds no such id the
    // computed alternative is used instead.
    const idOr = (id, alt) => (VD.V[id] ? id : (alt !== undefined && alt !== null ? alt : firstId));
    // Live vendors carry the compliance engine's block state; the seed infers it from the
    // accreditation label.
    const capOf = (v) => (v.blocked !== undefined ? !!v.blocked : v.accred === "Lapsed");
    const N = (x) => (x === null || x === undefined ? "—" : String(x));
    const vpV = VD.vendors.find((x) => x.id === s.vpVendor) || VD.vendors[0] || null;
    const vpR = vpV ? (VD.V[vpV.id] || null) : null;
    const vpScore = VD.score;
    const SC = vpR ? vpScore(vpV.id) : { rows: [], raw: 0, score: null };
    const score = vpR ? SC.score : 0;
    // The refresh of the report in view (newest unless an older one was picked).
    const repRun = rep ? ((rep.runs || [])[s.reportRunIdx || 0] || (rep.runs || [])[0] || null) : null;
    // Spaces: the four engines with their live figures plus the saved spaces (spacesLive.js).
    const spm = this.spModel();
    const toneColor = (tone) => (tone && tone !== "none" ? t(tone).color : "var(--color-neutral-500)");
    const iso = (ms) => (typeof ms === "number" ? new Date(ms).toISOString() : null);
    // One session row, as the Sessions page and a space page list them.
    const sessionRow = (r) => ({
      id: r.id, title: r.title, when: r.when, page: r.page, domain: r.domain, icon: r.icon,
      turns: r.turns ? r.turns + (r.turns === 1 ? " question" : " questions") : "",
      spaceName: r.spaceId && spm.byKey[r.spaceId] ? spm.byKey[r.spaceId].name : "",
      active: s.sessionId === r.id,
      open: () => this.openSession(r.id),
      remove: (e) => { if (e && e.stopPropagation) e.stopPropagation(); this.deleteSession(r.id); },
      canFile: r.kind === "chat" && spm.custom.length > 0,
      fileValue: r.spaceId || "",
      fileOptions: [{ value: "", label: "No space" }].concat(spm.custom.map((c) => ({ value: c.id, label: c.name }))),
      fileTo: (e) => { if (e && e.stopPropagation) e.stopPropagation(); this.fileSession(r.id, e.target.value || null); }
    });
    const dayGroups = (groups) => groups.map((g) => ({ day: g.day, rows: g.rows.map(sessionRow) }));
    const bkVendor = s.fVendor && s.fVendor !== "the responsible vendor" ? s.fVendor : "Apex Lifts";
    const spec0 = ACTION_SPECS.find((sp) => sp.k === s.fSpec) || null;
    const cc = this.ccModel();
    // Home tiles from svc-operations-intelligence; hm.live is false until something answers.
    const hm = this.homeModel();
    const hmLive = hm.live;
    const heroLive = hm.hero.buildings !== null;

    // Documents section — search + pagination over the same portfolio, independent of the
    // buildings table's own (docQuery/docPage) so paging one list never moves the other.
    const docAll = this.glBuildings();
    const docFiltered = filterBuildings(docAll, s.docQuery);
    const docPageCount = Math.max(1, Math.ceil(docFiltered.length / PAGE_SIZE));
    const docPage = Math.min(Math.max(0, s.docPage || 0), docPageCount - 1);
    const docPageStart = docFiltered.length ? docPage * PAGE_SIZE + 1 : 0;
    const docPageEnd = Math.min(docFiltered.length, (docPage + 1) * PAGE_SIZE);
    const docPageRows = docFiltered.slice(docPage * PAGE_SIZE, docPage * PAGE_SIZE + PAGE_SIZE);

    const vals = {
      tenant: "Planum Technologies",
      scopeLine: D.portfolio.buildings + " buildings · " + D.portfolio.area + " · 4 regulation packs",
      // The line under "Ask. Run. Anything." — the live register when it has answered, the
      // seed portfolio otherwise. Floor area has no source yet, so the live line counts
      // certificates on record instead of asserting an area it cannot know.
      heroStats: heroLive
        ? [
            hm.hero.buildings + (hm.hero.buildings === 1 ? " building hoisted" : " buildings hoisted"),
            hm.hero.certificates + (hm.hero.certificates === 1 ? " certificate on record" : " certificates on record"),
            hm.hero.countries.length ? hm.hero.countries.map((c) => COUNTRY_SHORT[c] || c).join(", ") : hm.hero.vendors + " vendors"
          ]
        : [D.portfolio.buildings + " buildings hoisted", D.portfolio.area, "UK, US, Singapore, UAE"],
      heroLive: heroLive,
      isHome: s.signedIn && s.view === "home", isAnswer: s.signedIn && s.view === "answer", isModule: s.signedIn && s.view === "module",
      isChat: s.signedIn && s.view === "chat",
      // The chat page's connection line and its empty-state copy.
      chatLinkLabel: s.chatLink === "connected"
        ? "Connected" + (s.chatTools ? " · " + s.chatTools + " tools" : "")
        : s.chatLink === "checking" ? "Connecting…"
        : s.chatLink === "unreachable" ? "Orchestrator unreachable — retry"
        : "",
      chatLinkDot: s.chatLink === "connected" ? "var(--st-ok)" : s.chatLink === "unreachable" ? "var(--st-risk)" : "var(--color-neutral-600)",
      chatLinkTip: s.chatLink === "unreachable" ? (s.chatLinkError || "svc-deepagents did not answer at /backend/deep-agents") : "svc-deepagents · one conversation across every engine",
      chatRetry: () => this.chatConnect(true),
      chatIntro: "Ask about compliance, energy, vendors, work orders or documents. The question goes to the engine that owns the answer, and how the answer was produced is shown with it — step by step.",
      queueOpen: s.queueOpen, paletteOpen: s.paletteOpen, detailOpen: !!detail,
      toggleQueue: () => this.setState((p) => ({ queueOpen: !p.queueOpen, acctOpen: false, paletteOpen: false, detail: null })),
      closeQueue: () => this.setState({ queueOpen: false }),
      queueCount: Math.max(D.decisions.length, CRONS.filter((c) => c.action && !s.cronsGone.includes(c.text)).length), query: s.query,
      chainOpen: s.chainOpen, chainIcon: s.chainOpen ? "ph-caret-down" : "ph-caret-right",
      toastOn: !!s.toast, toast: s.toast, askedQuery: s.askedQuery,
      themeIcon: s.dark ? "ph-sun" : "ph-moon",
      themeLabel: s.dark ? "Back to paper" : "Plant room / night shift",
      toggleTheme: () => this.toggleTheme(),
      // Going Home leaves the navigator as it was, like every other page change.
      goHome: () => this.setState({ view: "home", detail: null, queueOpen: false }),

      // The home ask bar. A question goes to the live orchestrator through the dock chat
      // (askScoped → ccAsk on this view); Run on an empty bar opens the chat instead.
      setQuery: (e) => this.setState({ query: e.target.value }),
      onKey: (e) => {
        if (e.key !== "Enter") return;
        const q = (s.query || "").trim();
        if (!q) return;
        this.setState({ query: "" });
        this.askScoped(q);
      },
      runQuery: () => {
        const q = (s.query || "").trim();
        if (!q) return this.ccOpenChat();
        this.setState({ query: "" });
        this.askScoped(q);
      },
      // Saved reports first — they are the pinned runs proper, re-run on a cadence — then
      // the questions the portfolio is most often asked.
      pinned: s.reports.map((r) => ({
        label: r.name,
        run: () => this.rpOpen(r.key)
      })).concat([
        "Which buildings put me at risk this month?",
        "What needs my approval today?",
        "Which vendors are blocked right now?"
      ].map((a) => ({ label: a, run: () => this.askScoped(a) }))),

      signedIn: s.signedIn, gated: !s.signedIn,
      f1: { o: s.frame === 0 ? 1 : 0, y: s.frame === 0 ? "0px" : (s.frame === 1 ? "-10px" : "10px") },
      f2: { o: s.frame === 1 ? 1 : 0, y: s.frame === 1 ? "0px" : (s.frame === 2 ? "-10px" : "10px") },
      f3: { o: s.frame === 2 ? 1 : 0, y: s.frame === 2 ? "0px" : (s.frame === 3 ? "-10px" : "10px") },
      f4: { o: s.frame === 3 ? 1 : 0, y: s.frame === 3 ? "0px" : (s.frame === 0 ? "-10px" : "10px"), pe: s.frame === 3 ? "auto" : "none" },
      f4items: [
        { name: "NABERS data pack", sub: "Bishopsgate Tower", icon: "ph-file-text" },
        { name: "ESOS data pack", sub: "Portfolio · Phase 4", icon: "ph-file-text" },
        { name: "Vendor scorecard", sub: "Meridian Lifts Ltd", icon: "ph-chart-bar" }
      ].map((p) => ({ ...p, click: () => this.setState({ authMode: "signin", authNotice: "Sign in to open " + p.name + "." }) })),
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
      // The gate, the account menu and the change-password modal: logic/auth.js.
      ...this.authVals(s),

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

      // Portfolio P&L. No backend holds a budget ledger, so this tile is the seed and says so.
      pnlSaved: "£390k",
      pnlTop: D.pnl.map((r) => ({ head: r.head, budget: r.budget, actual: r.actual, color: TONE[r.tone] ? TONE[r.tone].color : "var(--color-neutral-400)" })),
      pnlNote: "Seed figures · no budget ledger is connected yet",
      cronCount: String(CRONS.filter((c) => c.action && !s.cronsGone.includes(c.text)).length),
      // Hoist Score: ingestion coverage per source, live when at least one source answered.
      // A bar with no source draws empty and says why in its tooltip.
      hoistScore: hmLive && hm.score.value !== null
        ? { value: String(hm.score.value), band: hm.score.band, gap: hm.score.gap, note: "Ingestion coverage across the Hoist Graph, read from the operations backend. At 85 the agents move from supervised to delegated dispatch on L3 assets." }
        : { value: "78", band: "Supervised autonomy", gap: "Meter consent lowest at 71% — the gap to delegated autonomy", note: "Ingestion coverage across the Hoist Graph. At 85 the agents move from supervised to delegated dispatch on L3 assets." },
      hoistBars: hmLive && hm.score.value !== null
        ? hm.score.bars.map((b) => ({
            label: b.label, short: b.short, val: b.val, note: b.note,
            pct: (b.pct === null ? 0 : b.pct) + "%",
            color: b.tone === "none" ? "var(--color-neutral-600)" : t(b.tone).color
          }))
        : [
            { label: "Contracts and framework agreements", short: "Contracts", val: "92%", pct: "92%", color: "var(--st-ok)", note: "seed" },
            { label: "Asset registers", short: "Assets", val: "84%", pct: "84%", color: "var(--st-ok)", note: "seed" },
            { label: "Meter consent — MPAN / MPRN", short: "Meter consent", val: "71%", pct: "71%", color: "var(--st-warn)", note: "seed" },
            { label: "Certificates and evidence", short: "Certificates", val: "65%", pct: "65%", color: "var(--st-warn)", note: "seed" }
          ],
      hoistScoreNote: hmLive && hm.score.value !== null
        ? hm.score.note
        : (s.homeLoading ? "Reading the operations backend…" : "Seed figures · " + (s.homeError ? "backend unreachable — " + s.homeError : "the operations backend has not answered yet")),

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
      vpWeights: () => this.flash(vm.live && vm.weightsText
        ? vm.weightsText
        : "Weights: SLA response 25, SLA completion 25, first-time fix 20, recall rate 15, invoice accuracy 15. Each metric has its own percentage target; shortfall is penalised at three times its relative size. L1 misses weigh 3×, L3 misses 0.5×. A mandatory accreditation lapse puts a ceiling of 60 on the published score."),
      vpTiles: (() => {
        const vendors = VD.vendors, V = VD.V;
        const rec = (id) => V[id] || {};
        const scored = vendors.filter((v) => V[v.id]);
        const heldOf = (v) => (rec(v.id).invoices || []).filter((i) => i.status === "Held" || i.status === "Disputed").length;
        const blocked = vendors.filter(capOf).length;
        const held = vm.live ? vm.tiles.held : vendors.reduce((q, v) => q + heldOf(v), 0);
        const defaults = vm.live ? vm.tiles.defaults : vendors.reduce((q, v) => q + ((rec(v.id).contract || { fields: 0, read: 0 }).fields - (rec(v.id).contract || { read: 0 }).read), 0);
        const L1 = vm.live ? vm.tiles.L1 : vendors.reduce((q, v) => q + (rec(v.id).breaches || []).filter((b) => b.crit === "L1").length, 0);
        const pending = vm.live ? vm.tiles.pending
          : held + vendors.reduce((q, v) => q + (rec(v.id).certs || []).filter((c) => c.req === "Mandatory" && (c.status === "Lapsed" || c.status === "Not on record")).length, 0);
        const critical = vm.live ? vm.tiles.critical
          : vendors.reduce((q, v) => q + (rec(v.id).breaches || []).filter((b) => b.crit === "L1" && /blocked|missed/.test(b.actual)).length, 0);
        const worst = scored.slice().sort((a, b) => vpScore(a.id).score - vpScore(b.id).score)[0] || vendors[0] || null;
        const thinnest = scored.slice().sort((a, b) => V[a.id].contract.read - V[b.id].contract.read)[0] || vendors[0] || null;
        const firstBlocked = vendors.find(capOf) || worst;
        const mostHeld = vendors.slice().sort((a, b) => heldOf(b) - heldOf(a))[0] || vendors[0] || null;
        const idOf = (v) => (v ? v.id : firstId);
        return [
          { value: N(blocked), label: "Vendors blocked", hint: "ceiling of 60 applies", color: "var(--st-risk)", click: () => this.setState({ vpVendor: idOr("v2", idOf(firstBlocked)), vpTab: 3 }) },
          { value: N(pending), label: "Pending tasks", hint: "awaiting your decision", color: "var(--st-warn)", click: () => this.setState({ queueOpen: true }) },
          { value: N(critical), label: "Pending critical", hint: "L1 assets · act first", color: "var(--st-risk)", click: () => this.setState({ vpVendor: idOr("v2", idOf(firstBlocked)), vpTab: 2 }) },
          { value: N(L1), label: "L1 breaches", hint: "weighted 3× · this period", color: "var(--st-risk)", click: () => this.setState({ vpVendor: idOf(worst), vpTab: 2 }) },
          { value: N(held), label: "Invoice lines held", hint: "fail the rate schedule", color: "var(--st-warn)", click: () => this.setState({ vpVendor: idOr("v1", idOf(mostHeld)), vpTab: 4 }) },
          { value: N(defaults), label: "Terms on default", hint: "not in any contract", color: "var(--st-warn)", click: () => this.setState({ vpVendor: idOf(thinnest), vpTab: 1 }) }
        ];
      })(),

      vpStats: (() => {
        const vendors = VD.vendors, V = VD.V;
        const rec = (id) => V[id] || {};
        const byPkg = {};
        vendors.forEach((v) => { byPkg[VD.pkgOf(v.id)] = (byPkg[VD.pkgOf(v.id)] || []).concat([v]); });
        const bar = (n, max) => (n === null || n === undefined ? "0%" : Math.round((n / Math.max(1, max)) * 100) + "%");
        const pkgMax = Math.max.apply(null, [1].concat(Object.keys(byPkg).map((k) => byPkg[k].length)));

        const expiringV = vm.live ? [] : vendors.filter((v) => { const R = V[v.id]; return R && /2026/.test(R.contract.expires); });
        const expiring = vm.live ? vm.counts.expiring : expiringV.length;
        const heldLines = vm.live ? vm.counts.heldLines
          : vendors.reduce((q, v) => q + (rec(v.id).invoices || []).filter((i) => i.status === "Held" || i.status === "Disputed").length, 0);
        const totalLines = vm.live ? vm.counts.invoiceLines : vendors.reduce((q, v) => q + (rec(v.id).invoices || []).length, 0);
        const approvedLines = vm.live ? vm.counts.approvedLines : totalLines - heldLines;
        const woOpen = vm.live ? vm.counts.workordersOpen : (D.workorders || []).length;
        const termsRead = vm.live ? vm.counts.termsRead : vendors.reduce((q, v) => q + (rec(v.id).contract || { read: 0 }).read, 0);
        const termsDefault = vm.live ? vm.counts.termsDefault
          : vendors.reduce((q, v) => q + ((rec(v.id).contract || { fields: 0, read: 0 }).fields - (rec(v.id).contract || { read: 0 }).read), 0);
        const contractsN = vm.live ? vm.counts.contracts : vendors.filter((v) => V[v.id]).length;

        const scoredV = vendors.filter((v) => V[v.id] && vpScore(v.id).score !== null);
        const avgS = scoredV.length ? Math.round(scoredV.reduce((q, v) => q + vpScore(v.id).score, 0) / scoredV.length) : null;
        const band = (lo, hi) => scoredV.filter((v) => { const n = vpScore(v.id).score; return n >= lo && n <= hi; });
        const lowest = scoredV.slice().sort((a, b) => vpScore(a.id).score - vpScore(b.id).score)[0] || vendors[0] || null;
        const thinnest = vendors.filter((v) => V[v.id]).slice().sort((a, b) => V[a.id].contract.read - V[b.id].contract.read)[0] || vendors[0] || null;
        const idOf = (v) => (v ? v.id : firstId);
        const byAccred = (a) => vendors.filter((v) => v.accred === a);
        const mostRead = vendors.filter((v) => V[v.id]).slice().sort((a, b) => V[b.id].contract.read - V[a.id].contract.read)[0] || null;
        const heldOf = (v) => (rec(v.id).invoices || []).filter((i) => i.status === "Held" || i.status === "Disputed").length;
        const mostHeld = vendors.slice().sort((a, b) => heldOf(b) - heldOf(a))[0] || null;
        const cleanest = vendors.filter((v) => (rec(v.id).invoices || []).length && !heldOf(v))[0] || mostRead;

        return [
          {
            value: N(avgS), label: "Avg score",
            click: () => this.setState({ vpVendor: idOf(lowest), vpTab: 0 }),
            rows: [
              { label: "85 and above", n: String(band(85, 100).length), color: "var(--st-ok)", bar: bar(band(85, 100).length, scoredV.length), click: () => this.setState({ vpVendor: idOf(band(85, 100)[0] || vendors[0]), vpTab: 0 }) },
              { label: "70 to 84", n: String(band(70, 84).length), color: "var(--st-warn)", bar: bar(band(70, 84).length, scoredV.length), click: () => this.setState({ vpVendor: idOf(band(70, 84)[0] || vendors[0]), vpTab: 0 }) },
              { label: "Below 70", n: String(band(0, 69).length), color: "var(--st-risk)", bar: bar(band(0, 69).length, scoredV.length), click: () => this.setState({ vpVendor: idOf(lowest), vpTab: 0 }) }
            ]
          },
          {
            value: String(vendors.length), label: "Vendors",
            click: () => this.setState({ vpVendor: idOr("v1", firstId), vpTab: 0 }),
            rows: [
              { label: "Fully accredited", n: String(byAccred("Current").length), color: "var(--st-ok)", bar: bar(byAccred("Current").length, vendors.length), click: () => this.setState({ vpVendor: idOr("v3", idOf(byAccred("Current")[0] || vendors[0])), vpTab: 3 }) },
              { label: "Expiring", n: String(byAccred("Expiring").length), color: "var(--st-warn)", bar: bar(byAccred("Expiring").length, vendors.length), click: () => this.setState({ vpVendor: idOr("v6", idOf(byAccred("Expiring")[0] || vendors[0])), vpTab: 3 }) },
              { label: "Lapsed", n: String(byAccred("Lapsed").length), color: "var(--st-risk)", bar: bar(byAccred("Lapsed").length, vendors.length), click: () => this.setState({ vpVendor: idOr("v2", idOf(byAccred("Lapsed")[0] || vendors[0])), vpTab: 3 }) }
            ]
          },
          {
            value: String(Object.keys(byPkg).length), label: "Packages",
            click: () => this.flash("Vendors are grouped by service package. A package with a single vendor is a single point of failure — worth a second accredited contractor before the next renewal."),
            rows: Object.keys(byPkg).map((k) => ({
              label: k, n: String(byPkg[k].length),
              color: byPkg[k].some(capOf) ? "var(--st-risk)" : byPkg[k].length === 1 ? "var(--st-warn)" : "var(--st-ok)",
              bar: bar(byPkg[k].length, pkgMax),
              click: () => this.setState({ vpVendor: byPkg[k][0].id, vpTab: 0 })
            }))
          },
          {
            value: N(contractsN), label: "Contracts",
            click: () => this.setState({ vpVendor: idOr("v1", idOf(mostRead)), vpTab: 1 }),
            rows: [
              { label: "Terms read from document", n: N(termsRead), color: "var(--st-ok)", bar: vm.live ? bar(termsRead, (termsRead || 0) + (termsDefault || 0)) : "72%", click: () => this.setState({ vpVendor: idOr("v3", idOf(mostRead)), vpTab: 1 }) },
              { label: "On platform default", n: N(termsDefault), color: "var(--st-warn)", bar: vm.live ? bar(termsDefault, (termsRead || 0) + (termsDefault || 0)) : "28%", click: () => this.setState({ vpVendor: idOf(thinnest), vpTab: 1 }) },
              { label: "Expiring this year", n: N(expiring), color: expiring ? "var(--st-warn)" : "var(--st-ok)", bar: bar(expiring, vendors.length), click: () => this.setState({ vpVendor: expiringV.length ? expiringV[0].id : idOr("v1", firstId), vpTab: 1 }) }
            ]
          },
          {
            value: N(totalLines), label: "Commercial orders",
            click: () => this.setState({ vpVendor: idOr("v1", idOf(mostHeld)), vpTab: 4 }),
            rows: [
              { label: "Approved as charged", n: N(approvedLines), color: "var(--st-ok)", bar: bar(approvedLines, totalLines || approvedLines), click: () => this.setState({ vpVendor: idOr("v3", idOf(cleanest)), vpTab: 4 }) },
              { label: "Held or disputed", n: N(heldLines), color: "var(--st-risk)", bar: bar(heldLines, totalLines || heldLines), click: () => this.setState({ vpVendor: idOr("v1", idOf(mostHeld)), vpTab: 4 }) },
              { label: "Work orders open", n: N(woOpen), color: "var(--color-neutral-400)", bar: bar(woOpen, woOpen || 1), click: () => this.openModule("ops") }
            ]
          }
        ];
      })(),
      vpList: VD.vendors.map((v) => {
        const R = VD.V[v.id];
        // Live vendors carry the compliance engine's coverage figure; the seed derives it
        // from the certificates on the record.
        const req = v.covReq !== undefined ? v.covReq : (R ? R.certs.filter((c) => c.req !== "Preferred").length : 0);
        const on = v.covOn !== undefined ? v.covOn : (R ? R.certs.filter((c) => c.req !== "Preferred" && (c.status === "Current" || c.status === "Expiring")).length : 0);
        const cov = v.cov !== undefined ? v.cov : (req ? Math.round((on / req) * 100) : 0);
        const capped = capOf(v);
        const active = vpV && vpV.id === v.id;
        const sc = vpScore(v.id);
        const n = sc.score;
        return {
          name: v.name, meta: v.meta || (v.spend + " annual · " + v.accred.toLowerCase() + " accreditation"),
          cap: capped ? "ceiling 60 — mandatory lapse" : "", capShow: capped ? "block" : "none", capFg: "var(--st-risk)",
          score: N(n), trend: v.trend,
          scoreFg: n === null ? "var(--color-neutral-500)" : n >= 85 ? "var(--st-ok)" : n >= 70 ? "var(--st-warn)" : "var(--st-risk)",
          cov: cov + "%", covFrac: on + "/" + req,
          covFg: cov >= 90 ? "var(--st-ok)" : cov >= 60 ? "var(--st-warn)" : "var(--st-risk)",
          edge: capped ? "var(--st-risk)" : (v.score !== null && v.score >= 85) ? "var(--st-ok)" : "var(--st-warn)",
          bg: active ? "var(--color-accent-900)" : "transparent",
          pick: () => this.setState({ vpVendor: v.id, vpTab: 0 })
        };
      }),
      vpTabs: [["Scorecard", SC.rows.length || 5], ["Contract terms", (vpR ? vpR.terms.length : 0)], ["Evidence", (vpR ? vpR.breaches.length : 0)], ["Coverage", (vpR ? vpR.certs.length : 0)], ["Invoices", (vpR ? vpR.invoices.length : 0)]].map((t, i) => ({
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
        const capped = capOf(v);
        // Seed: the cap is a rule applied here. Live: the engine says whether it capped the
        // published score (block_capped); a block raised after the card was cut shows as a
        // note, not as a recomputed number.
        const capApplied = R.capApplied !== undefined ? R.capApplied : capped;
        const req = R.certs.filter((c) => c.req !== "Preferred");
        const onFile = req.filter((c) => c.status === "Current" || c.status === "Expiring");
        const missing = R.certs.filter((c) => c.status === "Not on record" || c.status === "Lapsed");
        const TAGS = {
          Current: ["var(--st-ok-bg)", "var(--st-ok)"], Expiring: ["var(--st-warn-bg)", "var(--st-warn)"],
          Lapsed: ["var(--st-risk-bg)", "var(--st-risk)"], "Not on record": ["var(--color-neutral-900)", "var(--color-neutral-400)"],
          Held: ["var(--st-warn-bg)", "var(--st-warn)"], Disputed: ["var(--st-risk-bg)", "var(--st-risk)"],
          Credited: ["var(--st-ok-bg)", "var(--st-ok)"], Approved: ["var(--color-neutral-900)", "var(--color-neutral-400)"]
        };
        const tag = (k) => TAGS[k] || TAGS["Not on record"];
        const CRIT = { L1: ["var(--st-risk-bg)", "var(--st-risk)"], L2: ["var(--st-warn-bg)", "var(--st-warn)"], L3: ["var(--color-neutral-900)", "var(--color-neutral-400)"] };
        const credit = R.breaches.reduce((q, b) => q + parseInt(b.cost.replace(/[^0-9]/g, ""), 10), 0);
        const invTotal = R.invoices.filter((i) => i.status === "Held" || i.status === "Disputed")
          .reduce((q, i) => q + (typeof i.deltaValue === "number" ? Math.round(Math.abs(i.deltaValue)) : parseInt((i.delta || "0").replace(/[^0-9]/g, "") || "0", 10)), 0);
        const mandatoryMissing = missing.filter((c) => c.req === "Mandatory");
        const capNames = mandatoryMissing.length ? mandatoryMissing.map((c) => c.name).join(" and ")
          : (v.blockedType || "a mandatory accreditation");
        return {
          name: v.name,
          contractLine: R.contract.line || (R.contract.ref + " · signed " + R.contract.signed + " · expires " + R.contract.expires + " · " + R.contract.read + " of " + R.contract.fields + " terms read from " + R.contract.pages + " pages"),
          score: N(score), trend: v.trend,
          scoreFg: score === null ? "var(--color-neutral-500)" : score >= 85 ? "var(--st-ok)" : score >= 70 ? "var(--st-warn)" : "var(--st-risk)",
          capShow: capped ? "flex" : "none", capBg: "var(--st-risk-bg)", capFg: "var(--st-risk)",
          capNote: capped
            ? "Score cannot exceed " + (R.cap || 60) + " while " + capNames + " " + (mandatoryMissing.length > 1 ? "are" : "is") + " not current — this vendor cannot hold regulated work whatever the delivery numbers say."
            : "",
          metrics: SC.rows.map((r) => {
            const ratio = r.w ? r.pts / r.w : 0;
            return {
              label: r.label, max: String(r.w), pts: String(r.pts),
              requires: r.requires || ((r.ceiling ? "no more than " : "at least ") + r.target + "%"),
              measured: r.measured === null || r.measured === undefined ? "—" : r.measured + "%",
              sample: r.sample || (r.basis + " · " + R.samples[r.k] + (r.k === "invoice" ? " invoice lines" : " work orders")),
              bar: Math.round(Math.max(0, Math.min(1, ratio)) * 100) + "%",
              color: ratio >= 0.95 ? "var(--st-ok)" : ratio >= 0.75 ? "var(--st-warn)" : "var(--st-risk)",
              srcTag: r.srcTag || (r.fromContract ? "clause " + r.clause + " · p" + r.page
                : r.clause ? "default " + r.target + "% · obligation at " + r.clause
                : "platform default " + r.target + "%"),
              srcBg: r.fromContract ? "var(--color-accent-900)" : "var(--marker-tint)",
              srcFg: r.fromContract ? "var(--color-accent)" : "var(--color-neutral-300)"
            };
          }),
          totalPts: String(SC.raw),
          capRowShow: capApplied ? "grid" : "none",
          finalScore: N(score),
          critSplit: [
            { label: "L1 · critical", n: R.crit ? String(R.crit.L1) : "—", note: "misses weigh 3×", color: "var(--st-risk)" },
            { label: "L2 · medium", n: R.crit ? String(R.crit.L2) : "—", note: "misses weigh 1×", color: "var(--st-warn)" },
            { label: "L3 · low", n: R.crit ? String(R.crit.L3) : "—", note: "misses weigh 0.5×", color: "var(--color-neutral-400)" }
          ],
          critNote: R.critNote || "Criticality is set per asset and approved by a person, not inferred. Unapproved assets default to L2 until someone confirms otherwise, so a mis-set L1 cannot quietly triple a vendor's penalty.",
          sourceNote: R.sourceNote || (R.contract.read + " of " + R.contract.fields + " terms were read from the signed contract — " + Math.round((R.contract.read / Math.max(1, R.contract.fields)) * 100) + "% source coverage. The rest fell back to platform defaults, which are named below."),
          terms: R.terms.map((t) => ({
            label: t.label, value: t.value,
            valFg: t.src === "contract" ? "var(--color-text)" : "var(--color-neutral-400)",
            src: t.srcLabel || (t.src === "contract" ? t.clause + " · p" + t.page : "default"),
            bg: t.src === "contract" ? "var(--color-accent-900)" : "var(--marker-tint)",
            fg: t.src === "contract" ? "var(--color-accent)" : "var(--color-neutral-300)",
            cursor: t.src === "contract" ? "pointer" : "default",
            open: () => t.note ? this.flash(t.note)
              : t.src === "contract"
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
            bg: tag(c.status)[0], fg: tag(c.status)[1]
          })),
          chase: () => this.runAction("Request evidence", v.name),
          openCompliance: () => this.setState({ view: "cc", ccPivot: "vendors", ccFocus: { kind: "vendor", name: v.name }, ccTab: 0 }),
          invoices: R.invoices.map((i) => ({
            ref: i.ref, period: i.period, line: i.line, charged: i.charged, should: i.should,
            delta: i.delta, deltaFg: i.delta === "—" ? "var(--color-neutral-500)" : "var(--st-risk)",
            status: i.status, bg: tag(i.status)[0], fg: tag(i.status)[1],
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
        this.askScoped(q);
      },
      pqRun: () => {
        const q = (s.pq || "").trim();
        // Empty bar: open the chat and put the caret in it, rather than refusing.
        if (!q) return chatView
          ? this.ccOpenChat()
          : this.flash("Type a question — it runs against the graph behind this page, not the table on it.");
        this.setState({ pq: "" });
        this.askScoped(q);
      },
      abPh: (() => {
        // Counted, not typed. "118 obligations across 24 buildings" sat above a register
        // holding none of either, which is the most misleading place on the screen to put
        // a constant: it is the first line a person reads, and it contradicts the figures
        // directly beneath it.
        if (s.view === "cc") {
          const cc = this.ccData();
          const obligations = (cc.certs || []).length;
          const covered = (cc.buildings || []).length;
          return "Ask anything about compliance — "
            + (obligations || covered
              ? obligations + (obligations === 1 ? " obligation" : " obligations")
                + " across " + covered + (covered === 1 ? " building" : " buildings")
              : "nothing on the register yet");
        }
        if (s.view === "vp") {
          // The live scorecard names its month; until it answers, count what the register
          // holds rather than quote a scorecard that is not there.
          if (vm.live) return "Ask anything about vendor performance — " + vm.vendors.length + (vm.vendors.length === 1 ? " vendor" : " vendors") + (vm.month ? ", " + vm.month + " scorecard" : "");
          const vendors = (this.ccData().vendors || []).length;
          return "Ask anything about vendor performance — "
            + (vendors ? vendors + (vendors === 1 ? " vendor" : " vendors") + " on the register"
              : "no vendors on the register yet");
        }
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
        return q.map((a) => ({ label: a, run: () => this.askScoped(a) }));
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
      // Search + pagination — independent of the buildings table above (docQuery/docPage),
      // over the same portfolio, filtered the same way (name, building ID, country, state).
      docQuery: s.docQuery || "",
      setDocQuery: (e) => this.setState({ docQuery: e.target.value, docPage: 0 }),
      docQueryShow: docAll.length ? "flex" : "none",
      docPage: docPage,
      docPageCount: docPageCount,
      docPageLabel: docFiltered.length ? docPageStart + "–" + docPageEnd + " of " + docFiltered.length + (s.docQuery ? " matching" : "") : "0 of 0",
      docPagerShow: docFiltered.length > PAGE_SIZE ? "flex" : "none",
      docPagePrevShow: docPage > 0,
      docPageNextShow: docPage < docPageCount - 1,
      docPagePrev: () => this.setState((p) => ({ docPage: Math.max(0, (p.docPage || 0) - 1) })),
      docPageNext: () => this.setState((p) => ({ docPage: Math.min(docPageCount - 1, (p.docPage || 0) + 1) })),
      docEmptyShow: docFiltered.length ? "none" : "block",
      docEmptyText: "No buildings match “" + s.docQuery + "”. Try a name, building ID, country or state.",
      docBuildings: docPageRows.map((b) => {
        const open = s.docOpen === b.name;
        const key = b.buildingId || b.id;
        const c = b.counts || {};
        const nDocs = c.documents, nCerts = c.certificates;
        // How many of those documents we hold, and how many of those certificates have
        // nothing filed against them. Both counted by the rollup over the whole building.
        const nHeld = c.documents_held, nBare = c.certificates_unevidenced;
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
        const files = ((branch && branch.rows) || []).map((r) => {
          // A document IS the document — the reference question is answered by its own
          // existence, so only "is there anything to serve" is left to ask.
          const a = fileAffordance(r.has_file, true);
          const open = a.held
            ? () => window.open(documentUrl(r.document_id || r.id), "_blank", "noopener")
            : null;
          return {
            ...a,
            file: tidyFileName(r.label),
            became: r.detail ? "filed as " + r.detail : "no document type recorded",
            // No size and no ingest timestamp on plenum_cafm.documents; the row's own key
            // is what there is, and it is what identifies the file in the graph. The "no
            // file" marker rides here too, so the distinction survives a screenshot rather
            // than living only in a tooltip.
            meta: "document_id " + String(r.id).slice(0, 8) + a.note,
            by: "graph",
            // The flash carries the stored name in full — the trim above is for the row,
            // not a claim about what the file is called.
            view: open || (() => this.flash(r.label + " — plenum_cafm.documents row " + r.id
              + (r.detail ? ", " + r.detail : "") + ", filed against " + b.name
              + ". Nothing is stored for it — no file and no extracted text — so this is a "
              + "record that the document exists, not a copy of it.")),
            // Only ever drawn on a row that has something behind it.
            download: open || (() => {})
          };
        });
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
          // Counted, not sampled: these come from the rollup, so they are right for a
          // building with more rows than the drawer will ever load. Undefined means the
          // rollup did not count them, and the chip then says nothing rather than "0".
          structLabel: (nDocs === 1 ? "document" : "documents")
            + (typeof nHeld === "number" && typeof nDocs === "number" && nDocs
                ? (nHeld ? " · " + nHeld + " held" : " · none held") : ""),
          unstructLabel: (nCerts === 1 ? "certificate" : "certificates")
            + (typeof nBare === "number" && typeof nCerts === "number" && nCerts
                ? (nBare ? " · " + nBare + " unevidenced" : " · all evidenced") : ""),
          // plenum_cafm.documents records no file size, so the slot that showed "0.2 GB"
          // for every building shows nothing rather than a figure derived from row counts.
          size: "",
          structured: files,
          // The right-hand column held "unstructured files, vectorised and bound", with a
          // similarity score per row. Those scores were weights in a constant. What the
          // graph does hold on this side is the certificates evidenced by those documents,
          // so that is what sits there — real rows, in the same place.
          // A certificate holds no file of its own; the document it was read from does, and
          // has_file follows that reference all the way to what the link will actually
          // serve. Three states, because "no document was ever filed against this
          // certificate" and "the document exists and we cannot serve it" are different
          // findings and only the first is a compliance gap.
          unstructured: ((certBranch && certBranch.rows) || []).map((r) => {
            const a = fileAffordance(r.has_file, r.document_id != null, {
              noRef: " · no document",
              noRefTitle: "No document behind this certificate — nothing was ever filed "
                + "against it",
              noFile: " · no scan",
              noFileTitle: "No scan held — the certificate is recorded and nothing can be "
                + "served for the document it came from",
            });
            // Opens the stored original, or its extracted text where no original was kept.
            // Only built where the row says there is something to serve, so the link is
            // never offered on a row that would answer 404.
            const open = a.held
              ? () => window.open(documentUrl(r.document_id), "_blank", "noopener")
              : () => this.flash(r.label + " — plenum_cafm.compliance_certificates row "
                  + r.id + (r.detail ? ", " + r.detail : "")
                  + (a.evidenced
                      ? ", bound to a document on " + b.name + ". Nothing is stored for that "
                        + "document — no file and no extracted text — so there is nothing "
                        + "to open."
                      : ". No document_id on the row: this certificate is recorded on "
                        + b.name + " with nothing filed against it."));
            return {
              ...a,
              file: r.label,
              became: r.detail || "no expiry or status recorded",
              sim: "",
              meta: "certificate" + a.note,
              view: open,
              download: open
            };
          }),
          loading: !!state.loading,
          emptyText: state.loading ? "Reading the graph…"
            : state.error ? "Could not read this building's documents: " + state.error
            : branch && !branch.available ? (branch.empty_reason || "Documents cannot be read here.")
            : files.length ? "" : "Nothing filed against " + b.name + " yet.",
          emptyShow: files.length ? "none" : "block",
          // Each column says why it is empty on its own. A heading with nothing under it
          // reads as a screen that has not finished loading rather than as a record.
          // The column heading claims these certificates are evidenced by the documents
          // beside them. True of the ones that name a document; for the rest it was the
          // panel asserting the very thing they are missing.
          certHead: (() => {
            const rows = (certBranch && certBranch.rows) || [];
            const bare = rows.filter((r) => r.document_id == null).length;
            if (!rows.length) return "Certificates";
            if (!bare) return "Certificates — evidenced by those documents";
            if (bare === rows.length) return "Certificates — none evidenced by a document";
            return "Certificates — " + bare + " of " + rows.length + " with no document";
          })(),
          certEmpty: !certBranch ? "Reading the graph…"
            : !certBranch.available ? (certBranch.empty_reason || "Certificates cannot be read here.")
            : "No certificate has been filed against a document on this building.",
          certEmptyShow: certBranch && (certBranch.rows || []).length ? "none" : "block",
          toggle: () => {
            const opening = s.docOpen !== b.name;
            this.setState((p) => ({ docOpen: p.docOpen === b.name ? null : b.name }));
            if (opening) this.bgLoad(key);
          },
          // Counted over the rows loaded, and only those with a file behind them: a
          // building with eight document rows and no blob_url between them has nothing to
          // prepare, and "preparing eight" was the same untruth the icons were telling.
          downloadAll: () => {
            const held = files.filter((f) => f.held).length;
            return this.flash(
              held
                ? "Preparing " + held + (held === 1 ? " document" : " documents")
                  + " filed against " + b.name + ", each fetched from its blob_url."
                : typeof nDocs === "number" && nDocs
                  ? nDocs + (nDocs === 1 ? " document is" : " documents are") + " recorded "
                    + "against " + b.name + ", none with a file behind it — there is nothing "
                    + "to download."
                  : "Nothing filed against " + b.name + " to download.");
          },
          // Opens the same ingest panel the page header does, with this building already
          // chosen — the card is the answer to the question the panel would otherwise ask.
          // runAction() was routing here with an empty patch, so the building was dropped on
          // the way and the dropdown came up blank.
          ingestMore: () => {
            this.ccChatReset();
            this.orchWith("Ingest documents", b.name, "ingest", { declFor: b.name });
          }
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
      orchWidth: orchW + "px",
      // The navigator behaves the same on every page, Home included: open, it sits beside the
      // content (never over it), and the dock sits beside the navigator.
      shellPad: !s.signedIn ? "0px" : ((s.navOpen ? 248 : 52) + (s.orchOpen ? orchW : 0)) + "px",
      orchLeft: (s.navOpen ? 248 : 52) + "px",

      // Drag the dock's right edge to widen it. Widen only: the floor is the flow's own
      // default (orchBaseW) so a drag can never make the dock narrower than designed, and
      // the ceiling is ORCH_MAX_W so it can never swallow the register beside it.
      orchResizeShow: s.orchOpen ? "block" : "none",
      orchAtMax: orchW >= ORCH_MAX_W,
      // The handle is fixed, not absolute: the dock scrolls its own content, and an
      // absolute handle would scroll away with it.
      orchHandleLeft: ((s.navOpen ? 248 : 52) + orchW - 3) + "px",
      orchHandleTint: orchW > orchBaseW ? "var(--color-accent)" : "transparent",
      orchResizeStart: (e) => {
        if (e.button !== undefined && e.button !== 0) return;
        e.preventDefault();
        e.stopPropagation();
        const startX = e.clientX;
        const startW = orchW;
        const move = (ev) => {
          const next = startW + (ev.clientX - startX);
          this.setState({ orchW: Math.max(orchBaseW, Math.min(ORCH_MAX_W, next)) });
        };
        const up = () => {
          window.removeEventListener("mousemove", move);
          window.removeEventListener("mouseup", up);
          document.body.style.userSelect = "";
          document.body.style.cursor = "";
        };
        window.addEventListener("mousemove", move);
        window.addEventListener("mouseup", up);
        // Without this a drag selects the text it passes over.
        document.body.style.userSelect = "none";
        document.body.style.cursor = "col-resize";
      },
      // Double-click the handle to snap back to the default width.
      orchResizeReset: () => this.setState({ orchW: null }),
      contentCols: s.orchOpen ? "minmax(0,1fr)" : "minmax(0,1fr) 320px",
      modLastRun: "02:14 today",
      answerCols: s.orchOpen ? "minmax(0,1fr)" : "minmax(0,1.5fr) minmax(0,1fr)",
      tenantShow: s.orchOpen ? "none" : "flex",

      orchOpen: s.signedIn && s.orchOpen,
      orchTitle: s.orchTask ? s.orchTask.label : "Orchestrator",
      // The chat reports its own route per reply, so the scripted chain is not shown there.
      orchStepsShow: chatView && ((s.ccChat || []).length > 0 || s.ccBusy) ? "none" : "flex",
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
      // On the compliance console a follow-up is a real question to the orchestrator, so
      // the dock is a conversation. Elsewhere it stays the scripted task replay.
      orchKey: (e) => { if (e.key === "Enter") this.orchSubmitNow(); },
      orchSubmit: () => this.orchSubmitNow(),
      orchPlaceholder: !chatView ? "" : ((s.ccChat || []).length ? "Ask a follow-up…"
        : s.view === "cc" ? "Ask anything about compliance…"
        : s.view === "vp" ? "Ask anything about vendor performance…"
        : s.view === "buildings" ? "Ask anything about your buildings…"
        : s.view === "home" ? "Ask anything about your portfolio…"
        : "Message the orchestrator…"),

      // Chat transcript. The console, vendors and buildings docks fill this; every other flow
      // keeps the single fDone result panel it already had.
      orchChatShow: chatView && ((s.ccChat || []).length > 0 || s.ccBusy),
      orchChat: (s.ccChat || []).map((m, i) => ({
        key: i,
        isYou: m.role === "you",
        isBot: m.role !== "you",
        text: m.text,
        // Which engine answered, read off the tools behind the reply.
        domain: m.error ? "Orchestrator" : (m.rich ? "Compliance" : domainOf(m.calls)),
        // Tool names behind a reply, de-duplicated, so the route is visible per message.
        tools: (m.calls || []).filter((t, j, a) => a.indexOf(t) === j).join(" · "),
        toolsShow: (m.calls || []).length ? "block" : "none",
        isNote: !!m.note,
        bg: m.error ? "var(--st-risk-bg)" : m.note ? "var(--color-bg)" : "var(--color-surface)",
        fg: m.error ? "var(--st-risk)" : "var(--color-text)",
        interruptShow: m.interrupted ? "block" : "none",
        // Structured compliance payload when the preflight answered; the dock renders
        // the markdown answer instead when this is null.
        rich: m.rich ? Object.assign({}, m.rich, {
          // Bars are derived here because the live register (days-to-expiry per
          // certificate) lives on the controller, not in the answer payload.
          overdue: overdueBars(m.rich.certificates, this.ccData()),
          // Each offer becomes a real button. `kind` decides what it runs.
          offers: (m.rich.offers || []).map((o) => {
            const busy = !!(s.ccOfferBusy || {})[o.cert_id + ":" + o.kind];
            return Object.assign({}, o, {
              busy: busy,
              label: busy ? "Working…" : o.label,
              run: busy ? () => {} : () => this.ccRunOffer(o)
            });
          })
        }) : null,
        ms: m.ms,
        // Expanded by default — the route is the point of showing it. Collapses per
        // message once the reader closes it.
        // Always offered on a question, including mid-turn — clicking it stops the run
        // first, because the usual reason to edit is that the answer is going wrong.
        editShow: m.role === "you" && s.ccEditIdx !== i ? "inline-flex" : "none",
        edit: () => this.ccEditStart(i),
        // Editing turns the bubble into a box in place.
        editing: s.ccEditIdx === i,
        bubbleShow: s.ccEditIdx === i ? "none" : "block",
        fileNames: (m.files || []).join(" · "),
        filesShow: (m.files || []).length ? "block" : "none",
        stoppedShow: m.stopped ? "block" : "none",
        // On the chat page the trace rail owns the route, so the in-answer copy of it starts
        // closed — the same steps twice on one screen made the answer harder to read, not
        // better evidenced. The console's dock has no rail, so there it stays open.
        stepsOpen: (s.ccStepsOpen || {})[i] === undefined ? stepsDefault : !!(s.ccStepsOpen || {})[i],
        toggleSteps: () => this.setState((p) => {
          const o = Object.assign({}, p.ccStepsOpen || {});
          const open = o[i] === undefined ? stepsDefault : !!o[i];
          o[i] = !open;
          return { ccStepsOpen: o };
        }),
        // Send this turn's run to the rail. Offered only where there is one to send.
        traceShow: (m.trace || []).length && !s.ccBusy ? "inline-flex" : "none",
        traceSelected: s.ccTraceIdx === i,
        selectTrace: () => this.setState({ ccTraceIdx: i })
      })),
      ccEditText: s.ccEditText,
      ccEditSet: (e) => this.ccEditSet(e.target.value),
      ccEditKey: (e) => {
        if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); this.ccEditRun(); }
        if (e.key === "Escape") this.ccEditCancel();
      },
      ccEditCancel: () => this.ccEditCancel(),
      ccEditRun: () => this.ccEditRun(),

      orchBusy: !!s.ccBusy,
      orchChatHasAny: (s.ccChat || []).length > 0,
      orchChatReset: () => this.ccChatReset(),

      // The turn in progress, painted from the stream as steps and zones arrive.
      orchLiveShow: chatView && s.ccBusy && s.ccStream ? "block" : "none",
      orchLiveRich: (s.ccStream && s.ccStream.rich) || null,
      orchLiveLabel: (s.ccStream && s.ccStream.reasoning) || "Reading the graph…",

      // ── the trace rail ────────────────────────────────────────────────
      // The run's sequence of thoughts, beside the conversation rather than folded inside
      // the answer. While a turn runs this follows the stream; once it lands the rail keeps
      // showing that turn until another is picked, so the route stays readable after the
      // fact. Completed events only — the one still in flight is the rail's live row, which
      // avoids showing "Running list_certificates…" twice.
      ...(() => {
        const chat = s.ccChat || [];
        let lastIdx = -1;
        for (let i = chat.length - 1; i >= 0; i -= 1) {
          if (chat[i].role !== "you" && (chat[i].trace || []).length) { lastIdx = i; break; }
        }
        const picked = (typeof s.ccTraceIdx === "number" && chat[s.ccTraceIdx]) ? s.ccTraceIdx : lastIdx;
        const busy = !!s.ccBusy;
        const src = busy ? ((s.ccStream && s.ccStream.trace) || []) : (picked >= 0 ? (chat[picked].trace || []) : []);
        const secs = (n) => (n / 1000).toFixed(n < 10000 ? 1 : 0) + " s";

        const rows = src
          // A tool still running is the live row, not a completed one.
          .filter((e) => !(busy && e.kind === "tool" && e.running))
          .map((e, i) => {
            if (e.kind === "step") {
              const st = e.step || {};
              const n = (st.queries || []).reduce((a, q) => a + (Number(q.matched_rows) || 0), 0);
              return {
                key: "e" + i, icon: TRACE_ICON[st.stage] || "ph-check-circle", mono: false,
                title: st.label || st.stage || "Step",
                detail: st.detail || "",
                meta: n ? n + (n === 1 ? " row" : " rows") : "",
                parts: (st.parts || []).slice(0, 4).map((p, j) => ({ key: j, text: p.text || p.id || "", scope: p.scope || "" })),
                issues: (st.issues || []).slice(0, 3)
              };
            }
            if (e.kind === "tool") {
              return {
                key: "e" + i, icon: "ph-wrench", mono: true,
                title: e.tool, detail: "",
                meta: typeof e.ranMs === "number" ? secs(e.ranMs) : "",
                parts: [], issues: []
              };
            }
            if (e.kind === "switch") {
              return {
                key: "e" + i, icon: "ph-arrows-left-right", mono: false,
                title: "Handed to " + e.to,
                detail: e.from ? "from " + e.from : "",
                meta: "", parts: [], issues: []
              };
            }
            return {
              key: "e" + i, icon: "ph-lightbulb", mono: false,
              title: e.label || "Thinking", detail: e.text || "",
              meta: "", parts: [], issues: []
            };
          });

        const elapsed = busy
          ? (s.ccTick ? s.ccTick + " s" : "")
          : (picked >= 0 && typeof chat[picked].ms === "number" ? secs(chat[picked].ms) : "");
        // The question this trace belongs to, so a replayed run says which one it was.
        let forQ = "";
        if (!busy && picked > 0 && chat[picked - 1] && chat[picked - 1].role === "you") forQ = chat[picked - 1].text;
        if (busy) { for (let i = chat.length - 1; i >= 0; i -= 1) { if (chat[i].role === "you") { forQ = chat[i].text; break; } } }

        return {
          orchTraceShow: chatView && (busy || rows.length > 0),
          orchTraceRows: rows,
          orchTraceLive: busy,
          orchTraceLiveLabel: (s.ccStream && s.ccStream.reasoning) || "Reading the graph…",
          orchTraceElapsed: elapsed,
          orchTraceTitle: busy ? "Working" : "Run trace",
          orchTraceFor: forQ,
          orchTraceCount: rows.length + (busy ? 1 : 0),
          // Only offered once a past turn is pinned, so it never appears on the live run.
          orchTraceUnpinShow: !busy && typeof s.ccTraceIdx === "number" && s.ccTraceIdx !== lastIdx,
          orchTraceUnpin: () => this.setState({ ccTraceIdx: null }),
          orchTraceStop: () => this.ccStop()
        };
      })(),

      // Stop a turn in flight. Only offered while one is running.
      orchStopShow: chatView && s.ccBusy ? "inline-flex" : "none",
      orchStop: () => this.ccStop(),

      // Documents and photos staged for the next question. The backend routes by type:
      // CSV/Excel to the migration flow, PDF/Word/images to doc-rag indexing.
      orchAttachShow: chatView ? "flex" : "none",
      orchFiles: (s.ccFiles || []).map((f, i) => ({
        key: i,
        name: f.name,
        size: f.size < 1024 ? f.size + " B" : f.size < 1048576 ? Math.round(f.size / 1024) + " KB" : (f.size / 1048576).toFixed(1) + " MB",
        isImage: /^image\//.test(f.type || ""),
        icon: /^image\//.test(f.type || "") ? "ph-image" : /csv|sheet|excel/i.test(f.type || f.name) ? "ph-table" : "ph-file-text",
        drop: () => this.ccDropFile(i)
      })),
      orchFileCount: (s.ccFiles || []).length,
      orchPickFiles: (e) => { this.ccAddFiles(e.target.files); e.target.value = ""; },
      closeOrch: () => this.closeOrch(),
      // The top-bar icon opens the dock beside a page; on the chat page the page is already the
      // orchestrator, so it just puts the caret in the composer.
      openOrch: () => (s.view === "chat" ? this.ccOpenChat() : this.setState({ orchOpen: true })),
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

      // Ingest documents: a real upload against the orchestrator (deepAgentsApi.runStatefulWithFiles
      // via ccAsk/askScoped), not a canned completion message. Files ride the same tray the
      // composer's attach button stages (ccFiles/orchFiles below) — attach from either place
      // and it is there for both, since it is one upload waiting on one send.
      fIngest: s.flow === "ingest",
      iBuilding: s.declFor || "",
      iBuildingOpts: BUILDINGS.map((b) => b.name).concat(this.bldIsLive() ? this.bldData().map((b) => b.name).filter((n) => !BUILDINGS.some((sb) => sb.name === n)) : []),
      setIBuilding: (e) => this.setState({ declFor: e.target.value }),
      iClasses: ["Certificates and statutory evidence", "Contracts and framework agreements", "Asset registers and PPM schedules", "Meter data and consent — MPAN / MPRN", "Invoices and service charge records"],
      iCanRun: (s.ccFiles || []).length > 0 && !!s.declFor,
      iHint: !(s.ccFiles || []).length ? "Attach at least one document first — certificates, contracts, asset registers, meter data or invoices."
        : !s.declFor ? "Choose which building this is for."
        : "",
      iRun: () => {
        if (!(s.ccFiles || []).length) return this.flash("Attach at least one document first — certificates, contracts, asset registers, meter data or invoices.");
        if (!s.declFor) return this.flash("Choose which building this is for.");
        const n = (s.ccFiles || []).length;
        this.setState({ flow: null, flowDone: "" });
        this.askScoped("Ingest " + n + (n === 1 ? " document" : " documents") + " for " + s.declFor + ".");
      },

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
        setSubject: (e) => this.setState({ emSubject: e.target.value }),
        setBody: (e) => this.setState({ emBody: e.target.value }),
        // Evidence requests carry the generated pack. It is a blob built in the browser,
        // so it is offered as an attachment to open rather than a server-side link.
        packShow: s.emKind === "evidence" && this._ccPack ? "flex" : "none",
        packName: (this._ccPack && this._ccPack.name) || "",
        packSize: this._ccPack ? this._ccPack.kb + " KB" : "",
        openPack: () => { if (this._ccPack) window.open(this._ccPack.url, "_blank"); },
        send: () => this.setState((prev) => ({
          // An approved evidence request is remembered against the certificate so its
          // row stops offering the same request again.
          ccRequested: s.emKind === "evidence" && s.emCertId
            ? Object.assign({}, prev.ccRequested || {}, { [s.emCertId]: true })
            : prev.ccRequested,
          flow: s.emKind === "investigate" && s.inv ? "investigate" : null,
          inv: s.emKind === "investigate" && s.inv ? Object.assign({}, s.inv, { replies: (s.inv.replies || []).concat([{ you: "Send the email", bot: "Sent to " + (s.emTo || "the FM lead") + ". The reply lands on this case; a document attached to it is ingested and bound to the asset record automatically." }]) }) : s.inv,
          flowDone: s.emKind === "investigate" ? "" : spec0 && spec0.done && spec0.k === s.emKind ? spec0.done(s.fiVals, s.fLabel, s.fSubject) : s.emKind === "renewal"
            ? "Renewal email queued for " + (s.emTo || "the vendor") + ". It sits on the approvals card until it is sent — nothing has left the platform."
            : s.emKind === "evidence"
            ? "Evidence request queued for " + (s.emTo || "the vendor") + " with the compliance pack attached. Nothing has left the platform — approving it here records the request; sending it needs the mail step wiring on."
            : s.emKind === "quote"
            ? "Quote request sent to " + (s.emTo || "the contractor") + ". The reply is watched and a scorecard opens on award."
            : s.emKind === "booking"
              ? "Booking instruction sent to " + (s.emTo || "the vendor") + ". Work order raised for " + s.bkDate + " and the certificate is expected on completion."
              : "Extension request sent to " + (s.emTo || "the authority") + ". The obligation is marked as contested pending their reply."
        }))
      },

      // Recent tasks. De-duplicated by label (asking the same thing three times used to
      // fill the list with itself), stamped with a real elapsed time, and clickable: a
      // row re-runs the instruction it names. Only the compliance console can re-ask,
      // so elsewhere a row just reopens the dock on that task.
      orchRecent: (() => {
        // Every session is a question worth re-asking, not just the ones this dock
        // raised — filtering on kind === "task" hid all five of the seeded ones, which
        // is why the list read empty. `task` carries the raw instruction when the dock
        // recorded it; a seeded row's label IS the question.
        const seen = {};
        return s.sessions
          .filter((q) => q !== s.orchTask)
          .filter((q) => { const k = q.task || q.label; if (seen[k]) return false; seen[k] = 1; return true; })
          .slice(0, 6)
          .map((q) => {
            const question = q.task || q.label;
            const canAsk = chatView;
            return {
              label: q.label,
              when: ago(q.at),
              hint: canAsk ? "Re-run" : "Open",
              click: () => (canAsk ? this.askScoped(question) : this.ask(question, q.k || null))
            };
          });
      })(),
      orchHasRecent: s.sessions.some((q) => q !== s.orchTask),

      // The composer's button becomes Stop while a turn is running.
      orchSendBusy: !!s.ccBusy && chatView,
      orchSendIcon: (s.ccBusy && chatView) ? "ph-stop-circle" : "ph-arrow-right",
      orchSendBg: (s.ccBusy && chatView) ? "var(--st-risk)" : "var(--color-accent)",
      orchSendTitle: (s.ccBusy && chatView) ? "Stop this answer" : "Send",
      orchSendClick: () => ((s.ccBusy && chatView) ? this.ccStop() : this.orchSubmitNow()),
      orchLiveDot: s.orchTask && s.orchDone < s.orchTask.steps.length ? "block" : "none",
      orchStatus: !s.orchTask ? "Idle — instruct it below, or trigger any action on the page." : (s.orchDone < s.orchTask.steps.length ? "Running · step " + (s.orchDone + 1) + " of " + s.orchTask.steps.length : "Complete · logged to the Activity Log and stored as a session"),
      navOpen: s.navOpen, navClosed: !s.navOpen,
      toggleNav: () => this.setState((p) => ({ navOpen: !p.navOpen })),
      closeNav: () => this.setState({ navOpen: false }),
      newQuery: () => this.newQuery(),
      newSpace: () => this.spNewToggle(),
      // Hoist a building: the dock card (logic/buildingsCrud.js), wired to POST /api/energy/buildings.
      addBuilding: () => this.bcOpenForm(),
      // Ingest documents on its own — for a building that already exists, not only the one
      // just hoisted. A fresh panel, same as opening any other task; nothing preselected.
      ingestDocuments: () => {
        this.ccChatReset();
        this.orchWith('Ingest documents', this.ctxLabel(), 'ingest', { declFor: '' });
      },
      allSessions: () => this.openSessions(null),

      /* Spaces: the four engines with their live figures, then the saved spaces from svc-udr
         (spacesLive.js). Sessions: every conversation and task, newest first, with a real
         elapsed time (sessions.js). Both open where they came from. */
      navSpaces: spm.builtin.map((b) => ({
        name: b.name, n: b.badge, icon: b.icon, tone: toneColor(b.tone), title: b.live ? b.kpis.map((k) => k.label + " " + k.value).join(" · ") : "Waiting for the engine to answer",
        active: s.view === "space" && s.spaceKey === b.key,
        click: () => this.openSpace(b.key)
      })).concat(spm.custom.map((c) => ({
        name: c.name, n: c.sessions ? String(c.sessions) : "", icon: "ph-folder-simple", tone: "var(--color-neutral-500)", title: "Saved space" + (c.sessions ? " · " + c.sessions + (c.sessions === 1 ? " session" : " sessions") : ""),
        active: s.view === "space" && s.spaceKey === c.id,
        click: () => this.openSpace(c.id)
      }))),
      navSpaceNew: !!s.spNew,
      navSpaceName: s.spNewName || "",
      setNavSpaceName: (e) => this.spNewSet(e.target.value),
      navSpaceKey: (e) => { if (e.key === "Enter") this.spCreate(); if (e.key === "Escape") this.spNewCancel(); },
      navSpaceCreate: () => this.spCreate(),
      navSpaceCancel: () => this.spNewCancel(),
      navSpaceBusy: !!s.spBusy,
      navSpaceCanCreate: spm.savedLive && !s.spBusy,
      navSpaceNote: spm.savedLive ? (spm.custom.length ? "" : "No saved spaces yet — add one with +.")
        : s.spLoading ? "Loading saved spaces…"
        : s.spError ? "Saved spaces unavailable — svc-udr did not answer." : "",
      navSpaceNoteTip: s.spError || "",

      navSessions: s.sessions.slice(0, 8).map((q) => ({
        label: q.title || q.label,
        when: ago(q.at),
        icon: sessionIcon(q),
        active: q.kind === "chat" && s.sessionId === q.id && s.view === "chat",
        click: () => this.openSession(q.id)
      })),
      navSessionsEmpty: !s.sessions.length,
      navSessionsMore: s.sessions.length > 8 ? "All sessions · " + s.sessions.length : "All sessions",

      // Section badges are the spaces' live figures (the number off the badge) and the live
      // site count; nothing shows until its source has answered.
      navSections: [
        { label: "Buildings", icon: "ph-buildings", key: "buildings", count: this.bldIsLive() ? this.bldData().length : "" },
        { label: "Buildings", icon: "ph-buildings", key: "buildings_user", count: "" },
        { label: "Compliance", icon: "ph-shield-check", key: "compliance", count: spm.byKey.compliance.count === null ? "" : spm.byKey.compliance.badge.split(" ")[0] },
        { label: "Vendors", icon: "ph-chart-line-up", key: "vendors", count: spm.byKey.vendors.count === null ? "" : spm.byKey.vendors.badge.split(" ")[0] },
        { label: "Energy", icon: "ph-lightning", key: "energy", count: spm.byKey.energy.count === null ? "" : spm.byKey.energy.badge.split(" ")[0] },
        { label: "Assets (Pending)", icon: "ph-cube", key: "assets" },
        { label: "Work orders (Pending)", icon: "ph-wrench", key: "ops", count: spm.byKey.ops.count === null ? "" : spm.byKey.ops.badge.split(" ")[0] }
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
            // Asset registers hang off the buildings in the Hoist Graph; that page is where they are.
            if (n.key === "assets") { window.scrollTo(0, 0); return this.setState({ view: "buildings", role: "user", navOpen: true, detail: null }); }
            return this.openModule(n.key);
          }
        };
      }),

      isReport: s.signedIn && s.view === "report",
      isSessions: s.signedIn && s.view === "sessions",
      isSpace: s.signedIn && s.view === "space",

      // The Sessions page: every conversation and task in this browser, grouped by day,
      // searchable, filterable by space; a row reopens, deletes or files its session.
      sessionsPage: (() => {
        const filter = s.sessionsFilter || null;
        const groups = shapeSessionList(s.sessions, { query: s.sessionsQuery, space: filter });
        const chips = [{ key: null, label: "All" }]
          .concat(spm.builtin.map((b) => ({ key: b.key, label: b.name })))
          .concat(spm.custom.map((c) => ({ key: c.id, label: c.name })));
        return {
          count: s.sessions.length + (s.sessions.length === 1 ? " session" : " sessions") + " · stored in this browser, threads on svc-deepagents",
          query: s.sessionsQuery || "",
          setQuery: (e) => this.setState({ sessionsQuery: e.target.value }),
          chips: chips.map((c) => ({ label: c.label, on: filter === c.key, pick: () => this.setState({ sessionsFilter: c.key }) })),
          groups: dayGroups(groups),
          empty: !groups.length,
          emptyText: s.sessions.length ? "Nothing matches." : "No sessions yet. Ask anything from the home bar — every conversation lands here.",
          newQuery: () => this.newQuery()
        };
      })(),

      // A space page: a built-in engine with its live figures, or a saved space; then the
      // sessions filed there and an ask bar that starts a new one in it.
      spacePage: (() => {
        const e = this.spaceEntry(s.spaceKey);
        if (!e) {
          return {
            missing: true, name: "", kicker: "Space", icon: "ph-folder-simple", kpis: [], groups: [], empty: true,
            emptyText: s.spLoading ? "Loading saved spaces…" : s.spError ? "Saved spaces unavailable — svc-udr did not answer." : "This space is not on record any more.",
            back: () => this.openSessions(null)
          };
        }
        const groups = shapeSessionList(s.sessions, { space: e.custom ? e.id : e.key });
        return {
          missing: false,
          isCustom: !!e.custom,
          kicker: e.custom ? "Space · saved in svc-udr" : "Space · " + e.page,
          name: e.name,
          icon: e.icon,
          badge: e.custom ? (e.sessions ? e.sessions + (e.sessions === 1 ? " session filed" : " sessions filed") : "Nothing filed yet") : e.badge,
          badgeColor: toneColor(e.tone),
          live: !!e.live,
          sourceNote: e.custom
            ? "Created " + (e.createdAt ? fmtDateTime(e.createdAt) : "—") + (e.createdBy ? " by " + e.createdBy : "") + " · plenum_cafm.saved_spaces"
            : (e.live ? "Figures read from svc-operations-intelligence" : "Waiting for the engine to answer"),
          kpis: e.kpis.map((k) => ({ label: k.label, value: String(k.value) })),
          openLabel: e.custom ? "" : "Open " + e.page,
          openPage: () => this.openSpacePage(e.key),
          renaming: !!s.spRenaming,
          renameText: s.spRenameText || "",
          startRename: () => this.setState({ spRenaming: true, spRenameText: e.name }),
          setRename: (ev) => this.setState({ spRenameText: ev.target.value }),
          renameKey: (ev) => { if (ev.key === "Enter") this.spRename(e.id, s.spRenameText); if (ev.key === "Escape") this.setState({ spRenaming: false, spRenameText: "" }); },
          saveRename: () => this.spRename(e.id, s.spRenameText),
          cancelRename: () => this.setState({ spRenaming: false, spRenameText: "" }),
          remove: () => this.spDelete(e.id),
          busy: !!s.spBusy,
          ask: s.spaceAsk || "",
          setAsk: (ev) => this.setState({ spaceAsk: ev.target.value }),
          askKey: (ev) => { if (ev.key === "Enter") this.spaceAskRun(); },
          askRun: () => this.spaceAskRun(),
          askPh: e.custom ? "Ask something to file in " + e.name + "…" : "Ask about " + e.name.toLowerCase() + " — the answer is filed here",
          groups: dayGroups(groups),
          empty: !groups.length,
          emptyText: e.custom ? "Nothing filed here yet. Ask below, or file a session from the Sessions page." : "No conversations with this engine yet. Ask below.",
          back: () => this.openSessions(null)
        };
      })(),

      // The conversation page's header: which session this is and where it is filed.
      chatSessionTitle: (() => { const rec = s.sessions.find((x) => x.id === s.sessionId); return rec ? rec.title : ""; })(),
      chatSessionMeta: (() => {
        const rec = s.sessions.find((x) => x.id === s.sessionId);
        if (!rec) return "";
        const sp = rec.spaceId ? spm.byKey[rec.spaceId] : null;
        return ["Asked from " + rec.page, sp ? "filed in " + sp.name : null, ago(rec.at)].filter(Boolean).join(" · ");
      })(),
      chatCanFile: !!s.sessionId && spm.custom.length > 0,
      chatFileValue: (() => { const rec = s.sessions.find((x) => x.id === s.sessionId); return (rec && rec.spaceId) || ""; })(),
      chatFileOptions: [{ value: "", label: "Not in a space" }].concat(spm.custom.map((c) => ({ value: c.id, label: c.name }))),
      chatFileTo: (e) => { if (s.sessionId) this.fileSession(s.sessionId, e.target.value || null); },
      isCC: s.signedIn && s.view === "cc",
      ccScan: () => this.ccRunScan(),
      ccLastRun: s.ccScanning ? "scanning…" : s.ccLastScan ? fmtTime(s.ccLastScan) + " · scan" : s.ccLoadedAt ? fmtTime(s.ccLoadedAt) + " · register read" : (s.ccLoading ? "loading…" : "not yet · seed data"),
      ccLive: !!s.ccLive,
      ccSourceLabel: s.ccScanning ? "Scanning the register…" : s.ccLive ? "Live · svc-operations-intelligence" + (s.ccError ? " · refresh failed" : "") : s.ccLoading ? "Connecting to svc-operations-intelligence…" : "Seed data · backend unreachable",
      ccSourceDot: s.ccScanning ? "var(--color-accent)" : s.ccLive ? (s.ccError ? "var(--st-warn)" : "var(--st-ok)") : s.ccLoading ? "var(--color-neutral-500)" : "var(--st-warn)",
      ccSourceDetail: s.ccScanMsg || s.ccError || "",
      ccRetryShow: !s.ccLoading && !s.ccScanning && (!s.ccLive || !!s.ccError) ? "inline" : "none",
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
      // The runway is a picture of counts, so it states them: the lapsed zone is labelled
      // with how many sit in it, and each legend key carries the number of dots it
      // explains. Both come from cc.pinCounts, derived from the same rows as the pins.
      ccTicks: [{ label: "lapsed · " + cc.pinCounts.lapsed, color: "var(--st-risk)" }].concat(runwayTicks().map((l) => ({ label: l, color: "var(--color-neutral-500)" }))),
      ccLegend: [
        { label: "solid — lapsed or blocked · " + cc.pinCounts.risk, bg: "var(--st-risk)", border: "0" },
        { label: "ring — expiring within 90 days · " + cc.pinCounts.warn, bg: "var(--color-surface)", border: "2.5px solid var(--st-warn)" },
        { label: "current · " + cc.pinCounts.ok, bg: "var(--st-ok)", border: "0" },
        { label: "shaded band = next 90 days · " + cc.pinCounts.band, bg: "var(--color-divider)", border: "0" },
        { label: cc.pinCounts.total + (cc.pinCounts.total === 1 ? " certificate plotted" : " certificates plotted"), bg: "transparent", border: "1px dashed var(--color-divider)" }
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
      // The report page reads the refresh in view (reports.js). Status is explicit: pending
      // (never run), running, ready, or failed — and a failed refresh keeps the last good one.
      report: (() => {
        const cadL = rep ? cadenceLabel(rep.cad) : "";
        const next = rep && rep.nextRunAt ? fmtDateTime(iso(rep.nextRunAt)) : null;
        return {
          title: rep ? rep.name : "",
          kicker: rep ? "Built from the session “" + rep.prompt + "”" + (rep.page ? ", asked from " + rep.page : "") + "." : "",
          lastRun: !rep ? ""
            : rep.status === "running" ? "Refreshing now…"
            : rep.lastRunAt ? "Last refreshed " + fmtDateTime(iso(rep.lastRunAt))
            : rep.lastTriedAt ? "Last attempt failed " + fmtDateTime(iso(rep.lastTriedAt))
            : "First refresh pending",
          meta: !rep ? "" : cadL + (next && rep.status !== "running" ? " · next " + next : "") + " · re-run while Hoistra is open",
          status: rep ? rep.status : "",
          runAt: repRun ? fmtDateTime(iso(repRun.at)) : "",
          runMs: repRun && typeof repRun.ms === "number" ? Math.round(repRun.ms / 1000) + " s" : "",
          tools: repRun && (repRun.calls || []).length ? repRun.calls.join(" · ") : "",
          answer: repRun && !repRun.error ? (repRun.answer || "") : "",
          rich: repRun && !repRun.error && repRun.rich
            ? Object.assign({}, repRun.rich, { overdue: overdueBars(repRun.rich.certificates || [], this.ccData()), offers: [] })
            : null,
          runs: rep ? (rep.runs || []).map((r, k) => ({
            label: fmtDateTime(iso(r.at)) + (r.error ? " · failed" : ""),
            active: k === (s.reportRunIdx || 0),
            pick: () => this.setState({ reportRunIdx: k })
          })) : []
        };
      })(),
      reportPending: !!rep && !repRun && rep.status !== "running",
      reportRunning: !!rep && rep.status === "running",
      reportReady: !!repRun && !repRun.error,
      reportFailed: !!repRun && !!repRun.error,
      reportFailedText: repRun && repRun.error ? repRun.error : "",
      reportHasRuns: !!rep && (rep.runs || []).length > 1,
      runReport: () => { if (rep) this.rpRun(rep.key); },
      exportReport: () => { if (rep) this.rpExport(rep.key, s.reportRunIdx || 0); },
      deleteReport: () => { if (rep) this.rpDelete(rep.key); },

      reportMenu: s.reportMenu,
      reportName: s.reportName,
      setReportName: (e) => this.setState({ reportName: e.target.value }),
      toggleReportMenu: () => this.setState((p) => ({ reportMenu: !p.reportMenu, reportName: "" })),
      cancelReport: () => this.setState({ reportMenu: false, reportName: "" }),
      createReport: () => this.rpCreate(),
      reportCadences: CADENCES.map((c, k) => ({
        label: c.label,
        tick: k === s.reportCad ? "ph-radio-button" : "ph-circle",
        color: k === s.reportCad ? "var(--color-accent)" : "var(--color-neutral-500)",
        chip: k === s.reportCad ? "var(--color-accent-900)" : "transparent",
        pick: () => this.setState({ reportCad: k })
      })),
      reportDaysShow: (CADENCES[s.reportCad] || {}).pickDays ? "flex" : "none",
      reportDays: DAYS.map((d, k) => {
        const on = s.reportDays.indexOf(k) > -1;
        return {
          label: d.charAt(0),
          title: d,
          bg: on ? "var(--color-accent)" : "transparent",
          fg: on ? "var(--accent-ink)" : "var(--color-neutral-400)",
          edge: on ? "var(--color-accent)" : "var(--color-divider)",
          pick: () => this.setState((p) => ({
            reportDays: p.reportDays.indexOf(k) > -1 ? p.reportDays.filter((x) => x !== k) : p.reportDays.concat([k]).sort()
          }))
        };
      }),
      reportTime: s.reportTime,
      setReportTime: (e) => this.setState({ reportTime: e.target.value }),
      reportCadenceNote: "The session's question is pinned and re-run " + cadenceLabel({ i: s.reportCad, days: s.reportDays, time: s.reportTime }).replace(/^Refresh /, "") + " while Hoistra is open. The first refresh runs as soon as the report is created.",
      // Sources are the chat sessions in this browser — a report is a pinned question.
      reportSources: (() => {
        const chats = s.sessions.filter((q) => q.kind === "chat").slice(0, 5);
        const cur = chats.some((q) => q.id === s.reportSrcId) ? s.reportSrcId : (chats[0] ? chats[0].id : null);
        return chats.map((q) => ({
          label: q.title || q.label,
          tick: q.id === cur ? "ph-radio-button" : "ph-circle",
          color: q.id === cur ? "var(--color-accent)" : "var(--color-neutral-500)",
          chip: q.id === cur ? "var(--color-accent-900)" : "transparent",
          pick: () => this.setState({ reportSrcId: q.id })
        }));
      })(),
      reportSourcesEmpty: !s.sessions.some((q) => q.kind === "chat"),

      navReports: (s.role === "admin" ? [] : s.reports).map((r) => {
        const active = s.view === "report" && s.reportKey === r.key;
        return {
          name: r.name,
          badge: r.status === "running" ? "Running" : r.status === "pending" ? "Pending" : (r.status === "error" && !r.lastRunAt) ? "Failed" : cadenceBadge(r.cad),
          color: active ? "var(--color-accent)" : "var(--color-neutral-300)",
          chip: active ? "var(--color-accent-900)" : "transparent",
          click: () => this.rpOpen(r.key)
        };
      }),

      queuePreview: D.decisions.slice(0, 3).map((d) => ({
        title: d.title, meta: d.meta, money: d.money, icon: d.icon,
        color: t(d.tone).color, bg: t(d.tone).bg, click: () => this.detailFromDecision(d)
      })),
      queueItems: D.decisions.map((d) => ({
        title: d.title, meta: d.meta, money: d.money, icon: d.icon, module: d.module,
        color: t(d.tone).color, bg: t(d.tone).bg, click: () => this.detailFromDecision(d)
      })),

      // Hoist Crons. Live: what the engines raised (approvals queue) and detected (energy
      // anomalies), newest first, grouped by day. Seed until the backend answers, and the
      // header says which it is.
      cronsLive: hmLive && hm.crons.length > 0,
      cronsLabel: hmLive && hm.crons.length > 0 ? "Live" : (s.homeLoading ? "Loading" : "Seed"),
      cronsDot: hmLive && hm.crons.length > 0 ? "var(--st-ok)" : "var(--color-neutral-600)",
      cronsTip: hmLive && hm.crons.length > 0
        ? hm.crons.length + " entries from svc-operations-intelligence" + (s.homeLoadedAt ? " · read " + fmtTime(s.homeLoadedAt) : "")
        : (s.homeError ? "Operations backend unreachable — " + s.homeError : "Seed feed until the operations backend answers"),
      crons: (() => {
        const gone = s.cronsGone || [];
        if (hmLive && hm.crons.length) {
          const out = [];
          let day = null;
          hm.crons.filter((c) => !gone.includes(c.id)).slice(0, 80).forEach((c) => {
            if (c.day !== day) { day = c.day; out.push({ sep: true, day: day }); }
            out.push({ id: c.id, text: c.text, agent: c.agent, t: c.t, dot: t(c.tone).color, action: c.action, live: c });
          });
          return out;
        }
        const order = ["Today", "Yesterday", "31 Aug"];
        const live = CRONS.filter((c) => !gone.includes(c.text));
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
        // A live row opens the record behind it (read-only); a seed row plays its script.
        act: () => c.sep ? null
          : c.live ? this.setState({ detail: this.cronDetail(c.live), queueOpen: false })
          : (c.action ? this.orch(c.action, c.agent) : this.flash(c.text)),
        dismiss: () => this.setState((p) => ({ cronsGone: p.cronsGone.concat([c.id || c.text]) }))
      })),


      // Each card's subtitle is what its engine actually reports (spacesLive.js) — never a
      // constant, so an empty register reads as waiting, not as 118 obligations.
      spaces: spm.builtin.map((b) => ({
        name: b.name, key: b.key, icon: b.icon, badge: b.badge, tone: b.tone,
        sub: b.live ? b.kpis.slice(0, 2).map((k) => k.label.toLowerCase() + " " + k.value).join(" · ") : "waiting for the engine to answer",
        color: toneColor(b.tone), bg: b.tone && b.tone !== "none" ? t(b.tone).bg : "transparent",
        click: () => this.openSpace(b.key)
      })),

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
