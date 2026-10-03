// maintenance — the Maintenance page's view model: decisions owed, inspection intelligence,
// PPM health, and the inspection-reports panel.
//
// Everything here reads the live model (maintenanceLive.js → mxModel()) and nothing else.
// There is no seed dataset behind this page: a decision, a card, a contract row or a report
// figure is on the screen because a read returned it, and a figure no read carries is a dash.
//
// Mixed into HoistraLogic.prototype, so `this` is the controller.
import { t } from './constants.js';
import { STATE_TONE, STATE_ORDER, STATE_DESC, SOURCE_ICON, fmtDay } from './maintenanceLive.js';
import { fmtTime } from './complianceLive.js';

export const maintenanceMethods = {

  mxVals(s) {
    const isMx = s.view === "module" && s.module === "ops";
    const isInsp = s.view === "insp";
    if (!(isMx || isInsp)) {
      return { isMaint: false, isInsp: false, mxCards: [], mxGroups: [], mxInsights: [], mxPpm: [], inspRows: [] };
    }
    const m = this.mxModel();
    const live = m.live;
    // A figure the read did not carry. The page never prints a zero nobody counted.
    const N = (x) => (x === null || x === undefined ? "—" : String(x));
    // A dash is never green or red — colour states a position, and an unsourced figure has
    // none. Same rule the Vendors page follows.
    const tone = (x, colour) => (x === null || x === undefined ? "var(--color-neutral-400)" : colour);

    // ── the four cards across the top ──
    const mxCards = m.cards.map((c) => ({
      l: c.l,
      v: !c.answerable ? "—" : c.v === null ? "—" : String(c.v) + (c.unit || ""),
      // The sub-line is the backend's own caption; an unanswerable card says why instead.
      s: !c.answerable ? (c.reason || "this database cannot answer that") : (c.s || "no figure on record"),
      color: !c.answerable || c.v === null ? "var(--color-neutral-400)" : t(c.tone).color
    }));

    // ── the decisions grid ──
    // Grouping is the server's: mxSetGroup re-reads with group_by, so `groups` below is what
    // the backend cut and totalled, not a regroup of rows it grouped another way.
    const gk = s.mxGroup || "State";
    const row = (d) => ({
      id: d.id || "not raised",
      idColor: d.id ? "var(--color-text)" : "var(--color-neutral-500)",
      asset: d.asset, b: d.b, vendor: d.vendor, est: d.est,
      estColor: d.estimated === null ? "var(--color-neutral-500)" : "var(--color-text)",
      state: d.state,
      color: t(STATE_TONE[d.state] || "warn").color,
      bg: t(STATE_TONE[d.state] || "warn").bg,
      rail: t(STATE_TONE[d.state] || "warn").color,
      src: d.src, srcIcon: SOURCE_ICON[d.src] || "ph-wrench",
      trigger: d.trigger, detail: d.detail,
      due: d.due || "—",
      statShow: d.statutory ? "inline" : "none",
      statNote: d.statutoryNote,
      // Nothing on this page writes, so a row offers no action that would do nothing. The
      // backend has no decide/approve/reassign route for a decision yet.
      actions: [],
      // DetailDrawer reads icon / module / meta / title / body / chain[].a / chain[].t.
      // Built in any other shape it opens with a broken glyph and a blank chain, so the row
      // looks clickable and tells you nothing.
      open: () => this.setState({ detail: this.mxDecisionDetail(d) })
    });

    const groups = (m.groups || []).map((g, i) => {
      const open = s.mxOpenG ? s.mxOpenG.indexOf(g.name) > -1 : i === 0;
      const gtone = gk === "State" ? (STATE_TONE[g.name] || "ok")
        : (g.blocked || g.deviating) ? "risk" : "warn";
      return {
        name: g.name, n: N(g.n),
        desc: gk === "State"
          ? (STATE_DESC[g.name] || "")
          : N(g.n) + (g.n === 1 ? " decision" : " decisions") + " · " + N(g.blocked) + " blocked · " + N(g.toRaise) + " to raise",
        // "—" when nothing in the group carries an estimate; the backend sends null for that
        // rather than 0, because a group whose cost is unknown is not a group that is free.
        total: g.total,
        totalColor: g.total === "—" ? "var(--color-neutral-500)" : "var(--color-neutral-400)",
        totalNote: g.priced !== null && g.n !== null && g.priced < g.n
          ? g.priced + " of " + g.n + " priced" : "",
        color: t(gtone).color, open: open, caret: open ? "ph-caret-down" : "ph-caret-right",
        toggle: () => this.setState((p) => {
          const cur = p.mxOpenG || [(m.groups[0] || {}).name];
          return { mxOpenG: cur.indexOf(g.name) > -1 ? cur.filter((x) => x !== g.name) : cur.concat([g.name]) };
        }),
        // No bulk action: there is no route that decides a decision, so a button here would
        // be a button that does nothing to the record it names.
        bulkShow: "none", bulk: "",
        items: (g.items || []).map(row)
      };
    });
    // Ungrouped (the group_by read failed but the flat list answered) still renders the rows.
    const flat = m.decisions.map(row);

    const gOpt = (label) => ({
      label: label, on: gk === label,
      edge: gk === label ? "var(--color-accent)" : "var(--color-divider)",
      fg: gk === label ? "var(--color-accent)" : "var(--color-neutral-400)",
      bg: gk === label ? "var(--color-accent-900)" : "transparent",
      pick: () => this.mxSetGroup(label)
    });

    // The filter row: "All", every state, every module. Each carries its count, so the row
    // doubles as the shape of the queue.
    //
    // It used to draw only the states and modules that had rows, on the reasoning that a
    // chip returning nothing is a dead control. What that actually produced was a filter row
    // whose shape moved with the data — eight chips on a populated portfolio, four on one
    // mid-ingest — and the same screen in two environments read as a missing feature rather
    // than an empty category. "Deviation 0" says nothing is deviating, which is a fact worth
    // having; no chip at all says deviation is not a thing on this platform.
    //
    // `available` is exhaustive now (services/maintenance.py), so a state the counts do not
    // mention is empty rather than unknown, and 0 is the honest number for it.
    const cur = s.filter || "All";
    const fOpt = (label, n) => ({
      label: label, n: n === null || n === undefined ? "" : String(n),
      border: cur === label ? "var(--color-accent)" : "var(--color-divider)",
      fg: cur === label ? "var(--color-accent)" : "var(--color-neutral-400)",
      bg: cur === label ? "var(--color-accent-900)" : "transparent",
      click: () => this.mxSetFilter(label)
    });
    const avail = m.available || { state: [], source: [] };
    const mxFilterOpts = [fOpt("All", m.total)]
      .concat((avail.state || []).map((k) => fOpt(k, m.byState[k] || 0)))
      .concat((avail.source || []).map((k) => fOpt(k, m.bySource[k] || 0)));

    // ── inspection intelligence ──
    const insights = m.intelligence.map((c) => ({
      n: c.answerable ? (c.n === null ? "—" : c.n) : "—",
      l: c.l,
      s: c.s || "",
      color: c.answerable && c.n !== null ? t(c.tone).color : "var(--color-neutral-400)",
      // A card the database cannot answer is not asked — the question would return the same
      // "cannot answer", and offering it as a link reads as if there were something behind it.
      askShow: c.answerable ? "pointer" : "default",
      ask: () => (c.answerable ? this.mxInspAsk(c.q) : this.flash(c.l + " — " + (c.s || "this database cannot answer that card."))),
      method: c.method || ""
    }));
    const corpus = m.corpus || null;
    const inspCount = corpus
      ? corpus.reports + (corpus.reports === 1 ? " report · " : " reports · ") + corpus.assets
        + (corpus.assets === 1 ? " asset" : " assets")
        + (corpus.since ? " · since " + (fmtDay(corpus.since) || corpus.since) : "")
      : live ? "No inspection reports on record" : "Not loaded";

    // ── PPM health ──
    const ppm = m.ppm.map((p) => ({
      contract: p.contract, vendor: p.vendor, scope: p.scope,
      done: p.done, pct: p.pct, bar: p.bar, planNote: p.planNote,
      missed: p.missed, late: p.late, reports: p.reports, repPct: p.repPct,
      next: p.next, nextColor: p.nextIsBlocked ? t("risk").color : p.next === "—" ? "var(--color-neutral-500)" : "var(--color-text)",
      deferrals: p.deferrals, note: "", noteShow: p.deferrals ? "block" : "none",
      color: t(p.tone).color, bg: t(p.tone).bg, state: p.state,
      missedColor: p.missed === "—" ? "var(--color-neutral-500)" : p.missedOn ? t("risk").color : "var(--color-neutral-500)",
      lateColor: p.late === "—" ? "var(--color-neutral-500)" : p.lateOn ? t("warn").color : "var(--color-neutral-500)",
      repColor: p.repPct === "—" ? "var(--color-neutral-500)" : p.repLow ? t("warn").color : t("ok").color
    }));
    const ps = m.ppmSummary;
    const mxPpmSummary = ps
      ? N(ps.done) + " of " + N(ps.plan) + " planned visits done" + (ps.yearToDate ? " year to date" : "")
        + " · " + N(ps.missed) + " missed · " + N(ps.reports) + " reports on file · " + N(ps.deferred) + " deferrals"
      : live ? "No PPM visits on record for your buildings" : "Not loaded";

    // ── the Ask bar's answer ──
    const ans = s.mxAnswer || null;
    const chips = (m.chips.length ? m.chips.map((c) => c.question) : []).filter(Boolean);

    return {
      isMaint: isMx, isInsp: isInsp,
      mxGroups: groups.length ? groups : (flat.length ? [{
        name: "All decisions", n: String(flat.length), desc: "", total: "—", totalColor: "var(--color-neutral-500)",
        totalNote: "", color: t("warn").color, open: true, caret: "ph-caret-down",
        toggle: () => {}, bulkShow: "none", bulk: "", items: flat
      }] : []),
      mxGroupOpts: ["State", "Source", "Building", "Vendor"].map(gOpt),
      mxFilterOpts: mxFilterOpts,
      mxCards: mxCards,
      mxDecisions: flat,
      mxDecEmpty: (groups.length || flat.length) ? "none" : "block",
      // Three sentences, never two: nothing loaded, nothing you may see, and nothing owed
      // are different facts and only the last one is good news.
      mxDecEmptyNote: !live
        ? "The maintenance service has not answered, so no decision is shown."
        : m.unallocated
          ? "You are allocated to no buildings, so no decision is in scope for you. An admin allocates buildings on Users & access."
          : "No decision is owed on the buildings you can see — nothing is blocked, awaiting approval, past its due date, or waiting to be raised.",
      mxDecSummary: !live ? "Not loaded"
        : N(m.count) + " of " + N(m.total) + (m.total === 1 ? " decision" : " decisions"),
      mxScope: m.scope || "",
      mxScopeShow: m.scope ? "inline" : "none",

      mxInsights: insights,
      mxInsightNote: m.unanswerable && m.unanswerable.length
        ? m.unanswerable.length + " of the four cards cannot be answered from this database — each says why in place of a number."
        : "",
      mxInsightNoteShow: m.unanswerable && m.unanswerable.length ? "block" : "none",

      mxInspQ: s.inspDraft || "",
      mxInspSet: (e) => this.setState({ inspDraft: e.target.value }),
      mxInspKey: (e) => { if (e.key === "Enter") this.mxInspAsk(s.inspDraft || ""); },
      mxInspRun: () => this.mxInspAsk(s.inspDraft || ""),
      mxInspOpen: () => this.openInsp(""),
      mxInspChips: chips.map((c) => ({ label: c, run: () => this.mxInspAsk(c) })),

      mxPpm: ppm,
      mxPpmEmpty: ppm.length ? "none" : "block",
      mxPpmEmptyNote: !live
        ? "The maintenance service has not answered."
        : m.unallocated
          ? "You are allocated to no buildings, so no contract is in scope for you."
          : "No planned visit is on record for your buildings in this window. Loading the PPM visit history is what fills this table — the endpoint is there.",
      mxPpmSummary: mxPpmSummary,
      mxPpmRule: m.ppmRule || "",

      // ── the inspection-reports page ──
      inspQ: s.mxAsked || "",
      inspTitle: !ans ? "" : ans.understood ? (ans.matched || ans.question || "") : "Not a question these records can answer",
      inspTake: !ans ? "" : (ans.answer || ""),
      inspSource: ans && ans.source ? "Read from " + ans.source.endpoint + " · " + (ans.source.scope || "") : "",
      inspSourceShow: ans && ans.source ? "block" : "none",
      inspRowsA: !ans ? [] : (ans.understood ? (ans.data || []) : (ans.can_answer || [])).map((r) => ({
        t: typeof r === "string" ? r : (r.question || r.headline || r.contract || r.asset_name || JSON.stringify(r))
      })),
      inspAnsEmpty: !ans || (ans.data || ans.can_answer || []).length ? "none" : "block",
      inspBusy: !!s.mxAskBusy,
      inspError: s.mxAskError || "",
      // The reports themselves. A risk level the report carries colours the grade column;
      // an ungraded report shows a dash rather than being ranked as if it scored well.
      inspRows: m.reports.map((r) => ({
        wo: r.wo, asset: r.asset, b: r.b, date: r.date, vendor: r.vendor, type: r.type,
        grade: r.risk || "—",
        gradeColor: !r.risk ? "var(--color-neutral-500)"
          : /high|critical/i.test(r.risk) ? t("risk").color
          : /medium/i.test(r.risk) ? t("warn").color : t("ok").color,
        findings: r.findings,
        rec: r.rec, recState: r.rec === "None" ? "" : r.recOpen ? "open" : "done",
        recColor: r.recOpen ? t("risk").color : "var(--color-neutral-500)",
        warranty: r.warranty || "", warrShow: r.warranty ? "inline" : "none",
        anomShow: "none"
      })),
      inspEmpty: m.reports.length ? "none" : "block",
      inspEmptyNote: !live ? "The maintenance service has not answered."
        : m.unallocated ? "You are allocated to no buildings, so no report is in scope for you."
        : "No inspection report is on record for your buildings. A report reaches a building through the asset it is about, so a report whose asset is not placed is not listed here.",
      inspCount: inspCount,
      inspBack: () => { window.scrollTo(0, 0); this.setState({ view: "module", module: "ops" }); },
      inspScope: corpus ? "inspection reports · " + corpus.reports + " on file" : "inspection reports",

      // ── provenance, said on the page ──
      mxLastRead: m.lastRead && m.lastRead.started_at ? fmtTime(m.lastRead.started_at) : "—",
      mxLastReadNote: m.lastRead && m.lastRead.reports_read !== undefined && m.lastRead.reports_read !== null
        ? m.lastRead.reports_read + " reports read"
        : "no read recorded yet"
    };
  },

  // One decision as the DetailDrawer reads it — shared by the Maintenance grid's rows and
  // the shell's Decision queue, so the same record opens the same drawer from either.
  // DetailDrawer reads icon / module / meta / title / body / chain[].a / chain[].t; built in
  // any other shape it opens with a broken glyph and a blank chain.
  mxDecisionDetail(d) {
    return {
      icon: "ph-wrench", module: "Maintenance",
      title: d.asset, meta: d.b + " · " + d.state, tone: STATE_TONE[d.state] || "warn",
      status: d.state, body: d.detail, refinement: "",
      chain: [{ a: "trigger", t: d.trigger }, { a: "source", t: d.src },
              { a: "building", t: d.b }, { a: "work order", t: d.id || "not raised yet" }],
      fields: [
        { l: "Work order", v: d.id || "not raised yet" },
        { l: "Building", v: d.b }, { l: "Vendor", v: d.vendor },
        { l: "Estimate", v: d.est }, { l: "Due", v: d.due || "—" },
        { l: "Statutory", v: d.statutory ? d.statutoryNote : "not forced by a certificate" }
      ],
      actions: []
    };
  },

  // The inspection ask box, its chips and the four reading cards go to the orchestrator,
  // the way every other page's ask bar does: askScoped → ccAsk, answered in the side dock
  // with the reports still on screen. They used to open the reports page and put the words
  // to POST /api/maintenance/ask — a fixed phrase matcher that answered anything off its
  // list with "not a question these records can answer". chatContext() tells the
  // orchestrator what this page is showing. An empty box opens the reports instead.
  mxInspAsk(question) {
    const q = String(question || "").trim();
    if (!q) return this.openInsp("");
    this.setState({ inspDraft: "" });
    return this.askScoped(q);
  },

  openInsp(q) {
    window.scrollTo(0, 0);
    this.setState({ view: "insp", inspQ: q, inspDraft: q, pq: "", detail: null, queueOpen: false });
    if (q) this.mxAsk(q, "inspection");
  }
};
