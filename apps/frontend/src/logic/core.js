// core — session, orchestrator, queries, drawers, shared table helpers.
// Methods are mixed into HoistraLogic.prototype; `this` is the controller.
import { GB, PACKS, CC_OF, ENC, ACTION_SPECS, TONE, t, MODULES } from './constants.js';
import { HOISTWAY } from '../data/hoistway-data.js';
import { makeSession, newSessionId, trimSessions } from './sessions.js';

export const coreMethods = {
  componentDidMount() {
    this._key = (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        this.setState((s) => ({ paletteOpen: !s.paletteOpen }));
      }
      if (e.key === "Escape") this.setState({ paletteOpen: false, detail: null, queueOpen: false });
    };
    window.addEventListener("keydown", this._key);
    this._cronTimer = setInterval(() => {
      if (this.state.signedIn) this.setState((p) => ({ cronPulse: (p.cronPulse + 1) % 24 }));
    }, 1800);
    this._frameTimer = setInterval(() => {
      if (!this.state.signedIn) this.setState((p) => ({ frame: (p.frame + 1) % 4 }));
    }, 3200);
    // Pull the compliance register, the home tiles and the vendor scorecards from the
    // backend; the seed stays until each answers.
    this.ccLoad();
    this.homeLoad();
    this.vpLoad();
    // The Buildings table; buildingsLive.js loads the per-table graph counts once it answers.
    this.bldLoad();
    // Saved spaces from svc-udr, and the scheduler that refreshes the custom reports.
    this.spLoad();
    this.rpStart();
    // A reload that lands on the conversation page re-checks the orchestrator link.
    if (this.state.view === "chat") this.chatConnect();
  },

  componentWillUnmount() {
    window.removeEventListener("keydown", this._key);
    clearInterval(this._frameTimer); clearInterval(this._cronTimer);
    clearTimeout(this._ccRetry); clearTimeout(this._homeRetry); clearTimeout(this._homeRefresh);
    clearTimeout(this._vpRetry); clearTimeout(this._vpRefresh); clearTimeout(this._bldRetry);
    clearTimeout(this._gphRetry); clearTimeout(this._gphRefresh);
    clearTimeout(this._spRetry); this.rpStop();
  },

  D() { return HOISTWAY; },

  cvt(x) {
    const C = { GBP: { s: "£", r: 1 }, USD: { s: "$", r: 1.27 }, AED: { s: "AED ", r: 4.66 }, SGD: { s: "S$", r: 1.71 } }[this.state.currency];
    if (!C || C.r === 1) return x;
    const one = (str) => str.replace(/£([\d,]+(?:\.\d+)?)(k|m|bn)?/g, (m, num, suf) => {
      let v = parseFloat(num.replace(/,/g, "")) * C.r;
      if (!suf) return C.s + Math.round(v).toLocaleString("en-GB");
      const units = ["k", "m", "bn"];
      let i = units.indexOf(suf);
      while (v >= 1000 && i < units.length - 1) { v /= 1000; i += 1; }
      return C.s + v.toFixed(1) + units[i];
    });
    const walk = (v) => typeof v === "string" ? one(v)
      : typeof v === "function" ? v
      : Array.isArray(v) ? v.map(walk)
      : (v && typeof v === "object" && !v.$$typeof) ? Object.fromEntries(Object.entries(v).map(([k, val]) => [k, walk(val)]))
      : v;
    return walk(x);
  },

  flash(msg) {
    this.setState({ toast: msg });
    clearTimeout(this._tt);
    this._tt = setTimeout(() => this.setState({ toast: "" }), 2600);
  },


  // Any action that makes the orchestrator DO something (not just show data)
  // goes through here: opens the dock, logs the task as a session, plays the chain.
  // `opts.record === false` skips the session record — the chat records its own question
  // as a chat session and only borrows the dock's title.
  orch(task, ctx, chain, opts) {
    const label = ctx ? task + " — " + ctx : task;
    const steps = chain || [
      { a: "Orchestrator", t: "Intent: " + task.toLowerCase() + (ctx ? " · scope: " + ctx : "") },
      { a: "Planner", t: "Resolved the Hoist Graph cells this touches and the agents that own them" },
      { a: "Worker", t: "Executing against the live graph — every write logged with actor and timestamp" },
      { a: "Quality", t: "Validation gate armed: the result is checked before it is written back" }
    ];
    // A task is a session record (logic/sessions.js): `task`/`ctx` keep the raw
    // instruction so Recent tasks can re-run it exactly, and `at` is a real timestamp.
    const entry = makeSession({ id: newSessionId(), title: label, kind: "task", task: task, ctx: ctx || null, steps: steps, page: this.ctxLabel(), at: Date.now() });
    const record = !(opts && opts.record === false);
    clearInterval(this._orchTick);
    this.setState((p) => Object.assign({
      orchOpen: true, orchTask: entry, orchDone: 0,
      paletteOpen: false, queueOpen: false, detail: null
    }, record ? { sessions: trimSessions([entry].concat(p.sessions || [])) } : {}));
    this._orchTick = setInterval(() => {
      this.setState((p) => {
        const n = p.orchDone + 1;
        if (n >= steps.length) clearInterval(this._orchTick);
        return { orchDone: Math.min(n, steps.length) };
      });
    }, 900);
  },

  closeOrch() { clearInterval(this._orchTick); clearInterval(this._invTick); this.setState({ orchOpen: false, flow: null, inv: null }); },

  // Opens the orchestrator AND arms a flow: booking draft, contractor swap, or an email.
  orchWith(task, ctx, flow, patch) {
    this.orch(task, ctx);
    this.setState(Object.assign({ flow: flow, flowDone: "" }, patch || {}));
  },


  // Route any action button — queue drawer, certificate row, detail table —
  // into the same orchestrator flows the decision drawer uses.
  runAction(label, subject, vendorName) {
    const low = label.toLowerCase();
    const spec = ACTION_SPECS.find((sp) => sp.m && sp.m.some((rx) => rx.test(low)));
    const ven = vendorName || "the responsible vendor";
    // Re-running the recorded task reopens the form, not just the trace.
    if (/^hoist building/.test(low)) return this.bcOpenForm();
    if (/^ingest documents/.test(low)) return this.orchWith(label, subject, "ingest", {});
    if (/^update the graph/.test(low)) return this.orchWith(label, subject, "update", { ugText: "", ugParsed: false });
    if (spec && spec.custom) return this.orchWith(label, subject, spec.custom, { bkLocked: true, fSubject: subject, fVendor: ven });
    if (spec) {
      const seed = {};
      (spec.inputs || []).forEach((fd) => {
        seed[fd.key] = fd.seed !== undefined ? fd.seed : (fd.type === "date" ? "2026-09-16" : (fd.type === "select" ? fd.options[0] : ""));
      });
      if (spec.noMail) {
        this.orch(label, subject);
        return this.setState({ flow: null, fSubject: subject, fVendor: ven, flowDone: spec.done(seed, label, subject) });
      }
      return this.orchWith(label, subject, "inputs", { fSpec: spec.k, fiVals: seed, fLabel: label, fSubject: subject, fVendor: ven });
    }
    return this.orch(label, subject);
  },

  ctxLabel() {
    const s = this.state;
    if (s.view === "cc") return "Compliance";
    if (s.view === "vp") return "Vendors";
    if (s.view === "module" && MODULES[s.module]) return MODULES[s.module].name;
    if (s.view === "report") { const r = s.reports.find((x) => x.key === s.reportKey); return r ? r.name : "Reports"; }
    if (s.view === "buildings") return "Buildings";
    if (s.view === "answer") return "Query";
    if (s.view === "chat") return "Orchestrator";
    if (s.view === "sessions") return "Sessions";
    if (s.view === "space") { const sp = this.spaceEntry(s.spaceKey); return sp ? sp.name : "Spaces"; }
    return "Home";
  },

  resolve(q) {
    const a = this.D().answers, low = (q || "").toLowerCase();
    for (const k of Object.keys(a)) if (a[k].match.some((m) => low.includes(m))) return k;
    return "queue";
  },

  ask(q, key) {
    const k = key || this.resolve(q);
    this.setState({ view: "answer", answerKey: k, askedQuery: q, paletteOpen: false, queueOpen: false, detail: null, query: "" });
    window.scrollTo(0, 0);
  },

  openModule(m) {
    this.setState({ view: "module", module: m, filter: "All", paletteOpen: false, queueOpen: false, detail: null, navOpen: true });
    window.scrollTo(0, 0);
  },

  detailFromDecision(d) { this.setState({ detail: d, queueOpen: false }); },

  detailFor(module, obj) {
    const D = this.D();
    const found = D.decisions.find((d) => d.title.includes(obj.key || "@@"));
    if (found) return this.detailFromDecision(found);
    this.setState({ detail: obj, queueOpen: false });
  },

  woDetail(w) {
    return {
      module: "Vendor operations", icon: "ph-wrench", tone: w.tone,
      title: w.id + " — " + w.asset + ", " + w.building,
      meta: w.type + " · " + w.priority + " · " + w.status + " · " + w.vendor,
      body: "Generated by the work-order engine, not raised by the operative. " + w.due + ". Estimate " + w.est + " built from the contracted rates in the Contract entity. " + (w.status === "Held" ? "Allocation is locked because the assigned vendor's accreditation for this asset type is not current." : "The structured inspection form for this asset type has been generated and will be completed by the operative on site."),
      fields: [
        { l: "Work order", v: w.id },
        { l: "Asset", v: w.asset },
        { l: "Trigger type", v: w.type },
        { l: "Priority", v: w.priority + " (asset criticality × severity)", editable: true },
        { l: "Estimated cost", v: w.est },
        { l: "Assigned vendor", v: w.vendor, editable: true },
        { l: "Deadline", v: w.due, editable: true }
      ],
      chain: [
        { a: "Orchestrator", t: "Trigger: " + w.type.toLowerCase() + " → intent: work-order-engine" },
        { a: "Planner", t: "Classify WO type → fetch asset record → priority from criticality × severity → select contractor filtered by accreditation → estimate cost → draft" },
        { a: "Worker", t: "Fields pre-populated from Hoist Graph. Operative availability checked against ResourceSkill. Structured inspection form generated for this AssetType." },
        { a: "Quality", t: w.status === "Held" ? "Fired — contractor accreditation invalid for this AssetType. Dispatch blocked." : "Draft presented to you. You are the quality gate; no auto-dispatch at this cost band." }
      ],
      refinement: "A second asset of the same type shows an early version of this signature. Add it to the same contractor visit?",
      actions: w.status === "Held" ? ["Reassign vendor", "Hold and notify", "Cancel work order"] : ["Approve work order", "Change priority", "Monitor"]
    };
  },

  rows(m, filter) {
    const D = this.D();
    if (m === "compliance") {
      let list = D.certificates;
      if (filter === "Lapsed & overdue") list = list.filter((c) => c.days < 7);
      if (filter === "Inside 30 days") list = list.filter((c) => c.days < 30);
      return list.map((c) => ({
        c0: c.building, c1: c.type, c2: c.asset, c3: c.expiry, c4: c.status, c5: c.vendor,
        color: t(c.tone).color, bg: t(c.tone).bg, click: () => this.setState({ detail: this.certDetail(c) })
      }));
    }
    if (m === "energy") {
      const sel = this.enScope(this.state).sel;
      let list = D.anomalies.filter((a) => sel.indexOf(CC_OF[a.building] || "UK") > -1);
      if (filter === "New") list = list.filter((a) => a.status === "New");
      if (filter === "Above £20k") list = list.filter((a) => parseInt(a.impact.replace(/[£,]/g, ""), 10) > 20000);
      // Segregated by market: a header row per country in scope, with its own
      // subtotal, so a portfolio view never blends a Dubai chiller into a UK total.
      const out = [];
      sel.forEach((cc) => {
        const rows = list.filter((a) => (CC_OF[a.building] || "UK") === cc);
        if (!rows.length) return;
        const sum = rows.reduce((q, a) => q + parseInt(a.impact.replace(/[^0-9]/g, ""), 10), 0);
        out.push({ groupShow: "table-row", rowShow: "none", group: PACKS[cc].flag + " " + PACKS[cc].name, groupMeta: rows.length + (rows.length === 1 ? " anomaly" : " anomalies") + " · " + (ENC[cc] || ENC.UK).grain, groupSum: "£" + Math.round(sum / 1000) + "k annualised", c0: "", c1: "", c2: "", c3: "", c4: "", c5: "", color: "transparent", bg: "transparent", click: null, invShow: "none" });
        rows.forEach((a) => out.push({
          c0: a.building, c1: a.asset, c2: a.type, c3: a.impact, c4: a.status, c5: a.days + " days",
          color: t(a.tone).color, bg: t(a.tone).bg, click: () => this.setState({ detail: this.anomalyDetail(a) }),
          invShow: "table-cell",
          investigate: (e) => { if (e && e.stopPropagation) e.stopPropagation(); this.investigate("anomaly", a); }
        }));
      });
      return out;
    }
    if (m === "vendors") {
      let list = D.vendors;
      if (filter === "Below 80") list = list.filter((v) => v.score < 80);
      if (filter === "Declining") list = list.filter((v) => v.trend === "declining");
      return list.map((v) => ({
        c0: v.name, c1: String(v.score), c2: v.trend, c3: v.spend, c4: v.accred, c5: v.note || "—",
        color: t(v.tone).color, bg: t(v.tone).bg, click: () => this.setState({ detail: this.vendorDetail(v) })
      }));
    }
    if (m === "ops") {
      let list = D.workorders;
      if (filter === "Awaiting approval") list = list.filter((w) => w.status === "Draft");
      if (filter === "Blocked") list = list.filter((w) => w.status === "Held");
      return list.map((w) => ({
        c0: w.id + " · " + w.priority, c1: w.asset, c2: w.building, c3: w.est, c4: w.status, c5: w.due,
        color: t(w.tone).color, bg: t(w.tone).bg, click: () => this.setState({ detail: this.woDetail(w) })
      }));
    }
    return [];
  },

  bars(m) {
    const D = this.D();
    if (m === "compliance") return D.certCategories.map((c) => {
      const pct = Math.round((c.current / c.tracked) * 100);
      return { label: c.name, val: c.current + "/" + c.tracked, pct: pct + "%", color: pct === 100 ? TONE.ok.color : pct >= 92 ? TONE.warn.color : TONE.risk.color };
    });
    if (m === "energy") {
      const sel = this.enScope(this.state).sel;
      const out = [];
      sel.forEach((cc) => {
        const bs = D.buildings.filter((b) => (CC_OF[b.name] || "UK") === cc);
        if (!bs.length) return;
        const P = PACKS[cc];
        out.push({ groupShow: "flex", rowShow: "none", group: P.flag + " " + P.name, groupMeta: (P.std === "NA" ? "rolling portfolio benchmark" : P.std), label: "", val: "", pct: "0%", color: "transparent", invShow: "none" });
        bs.forEach((b) => out.push({
          label: b.name, val: b.eui + " kWh/m²",
          pct: Math.min(100, Math.round((b.eui / 260) * 100)) + "%",
          color: b.eui > b.bench + 10 ? TONE.risk.color : b.eui > b.bench ? TONE.warn.color : TONE.ok.color,
          invShow: "inline-flex",
          investigate: () => this.investigate("building", b)
        }));
      });
      return out;
    }
    if (m === "vendors") return D.vendors.map((v) => ({
      label: v.name, val: v.sla_c + "%", pct: v.sla_c + "%",
      color: v.sla_c >= 95 ? TONE.ok.color : v.sla_c >= 88 ? TONE.warn.color : TONE.risk.color
    }));
    if (m === "ops") return D.buildings.map((b) => ({
      label: b.name, val: b.score + " / 100", pct: b.score + "%", color: t(b.tone).color
    }));
    return [];
  }
};
