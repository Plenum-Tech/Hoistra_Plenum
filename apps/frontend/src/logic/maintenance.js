// maintenance — maintenance decisions, inspection intelligence, PPM health, inspection query page.
// Mixed into HoistraLogic.prototype, so `this` is the controller.
import { t } from './constants.js';
import { HOISTRA_MX } from '../data/hoistra-maintenance.js';
import { STATE_TONE, STATE_ORDER, STATE_DESC } from './maintenanceLive.js';

export const maintenanceMethods = {

  /* Maintenance: decisions owed, inspection intelligence, PPM health. */
  mxVals(s) {
    const MX = HOISTRA_MX;
    const isMx = s.view === "module" && s.module === "ops";
    const isInsp = s.view === "insp";
    if (!MX || !(isMx || isInsp)) return { isMaint: false, isInsp: false, mxCards: [], mxDecisions: [], mxInsights: [], mxPpm: [], inspRows: [] };
    // Live decisions and KPI tiles (svc-work-order-management, via maintenanceLive.js)
    // replace the seed ones once loaded — mxLiveVals shapes them into exactly the row/tile
    // shape below already, so everything here reads `dec`/`decisions`/`mxCards` without
    // knowing which source it came from.
    const live = this.mxLiveVals(s);
    // null only until the real fetch has never once succeeded — once it has, an honestly
    // empty live list (nothing open) is shown as such, not papered over with the seed.
    const decisionsSource = live.mxLiveDecisions !== null ? live.mxLiveDecisions : MX.decisions;
    const ST = Object.assign({ "Blocked": "risk", "To raise": "warn", "Awaiting approval": "warn", "Deviation": "risk" }, STATE_TONE);
    const SRC = { Compliance: "ph-shield-check", Vendors: "ph-chart-line-up", Assets: "ph-cube", Energy: "ph-lightning", "Work order": "ph-wrench" };
    const f = s.filter;
    const dec = decisionsSource.filter((d) => f === "All" ? true : ST[f] ? d.state === f : d.src === f);
    const order = Object.assign({ "Blocked": 0, "To raise": 1, "Deviation": 2, "Awaiting approval": 3 }, STATE_ORDER);
    const decisions = dec.slice().sort((p, q) => order[p.state] - order[q.state]).map((d) => ({
      id: d.id || "not raised", idColor: d.id ? "var(--color-text)" : "var(--color-neutral-500)",
      asset: d.asset, b: d.b, vendor: d.vendor, est: d.est,
      state: d.state, color: t(ST[d.state]).color, bg: t(ST[d.state]).bg, rail: t(ST[d.state]).color,
      src: d.src, srcIcon: SRC[d.src] || "ph-wrench", trigger: d.trigger, detail: d.detail,
      actions: d.actions.map((a, i) => ({ label: a, primary: i === 0,
        bg: i === 0 ? "var(--color-accent)" : "transparent", fg: i === 0 ? "var(--accent-ink)" : "var(--color-neutral-300)", edge: i === 0 ? "var(--color-accent)" : "var(--color-divider)",
        run: (e) => { if (e && e.stopPropagation) e.stopPropagation(); this.runAction(a, d.asset + " · " + d.b, d.vendor); } })),
      open: () => this.setState({ detail: this.woDetail ? (this.D().workorders.find((w) => w.id === d.id) ? this.woDetail(this.D().workorders.find((w) => w.id === d.id)) : { title: d.asset, sub: d.b + " · " + d.state, tone: ST[d.state], status: d.state, body: d.detail, chain: [{ k: "trigger", v: d.trigger }, { k: "source", v: d.src }], fields: [], actions: [] }) : null })
    }));
    const I = MX.inspections;
    const openRec = I.filter((x) => x.rec !== "None" && !x.recDone);
    const warr = I.filter((x) => x.warranty);
    const D = this.D();
    const liveAnoms = D.anomalies.filter((a) => a.status !== "Resolved");
    const corrob = liveAnoms.filter((a) => I.some((x) => x.anom === a.id));
    const grade4 = I.filter((x) => x.cond >= 4).map((x) => x.asset).filter((v, i, arr) => arr.indexOf(v) === i);
    const insights = [
      { n: String(openRec.length), l: "recommendations never converted to orders", s: openRec.filter((x) => x.anom && liveAnoms.some((a) => a.id === x.anom)).length + " on assets now flagged by energy — the inspector saw it first", tone: "risk", q: "Which recommendations were never converted to orders?" },
      { n: corrob.length + " of " + liveAnoms.length, l: "open anomalies corroborated by an earlier finding", s: "same cause named in a report dated before detection", tone: "warn", q: "Which reports confirm the energy anomalies?" },
      { n: String(warr.length), l: "findings on parts still under warranty", s: "£2,100 of invoiced work claimable", tone: "ok", q: "What is under warranty?" },
      { n: String(grade4.length), l: "assets graded poor (4 of 5) by inspectors", s: "all 2004–2009 boilers and chillers — none graded end of life", tone: "warn", q: "Which assets are in the worst condition?" }
    ].map((x) => Object.assign({}, x, { color: t(x.tone).color, ask: () => this.openInsp(x.q) }));
    const ppm = MX.ppm.map((p) => {
      const pct = Math.round((p.done / p.planned) * 100), rep = p.done ? Math.round((p.reports / p.done) * 100) : 0;
      const tone = p.missed >= 3 || p.next === "blocked" ? "risk" : p.missed || p.late || rep < 90 ? "warn" : "ok";
      return {
        contract: p.contract, vendor: p.vendor, scope: p.scope,
        done: p.done + " / " + p.planned, pct: pct + "%", missed: String(p.missed), late: String(p.late), reports: p.reports + " / " + p.done, repPct: rep + "%",
        next: p.next, nextColor: p.next === "blocked" ? t("risk").color : "var(--color-text)",
        deferrals: p.deferrals ? p.deferrals + " deferred" + (p.note ? " · " : "") : "", note: p.note, noteShow: p.note || p.deferrals ? "block" : "none",
        color: t(tone).color, bg: t(tone).bg, state: tone === "risk" ? "behind plan" : tone === "warn" ? "watch" : "to plan",
        missedColor: p.missed ? t("risk").color : "var(--color-neutral-500)", lateColor: p.late ? t("warn").color : "var(--color-neutral-500)", repColor: rep < 90 ? t("warn").color : t("ok").color
      };
    }).sort((p, q) => parseInt(p.pct, 10) - parseInt(q.pct, 10));
    const planned = MX.ppm.reduce((q, p) => q + p.planned, 0), done = MX.ppm.reduce((q, p) => q + p.done, 0), missed = MX.ppm.reduce((q, p) => q + p.missed, 0), reports = MX.ppm.reduce((q, p) => q + p.reports, 0);
    // Inspection query page
    const q = s.inspQ || "";
    const ans = q ? (MX.answers.find((a) => a.m.test(q)) || { title: "No scripted reading for that yet", take: "The reports are indexed; this question would be answered from them. Try one of the suggested questions.", rows: [] }) : MX.defaultAnswer;
    const inspRows = I.slice().sort((p, q2) => q2.cond - p.cond).map((x) => ({
      wo: x.wo, asset: x.asset, b: x.b, date: x.date, vendor: x.vendor, type: x.type,
      grade: String(x.cond), gradeColor: x.cond >= 4 ? t("risk").color : x.cond === 3 ? t("warn").color : t("ok").color,
      findings: x.findings.join(" · "), rec: x.rec, recState: x.rec === "None" ? "" : x.recDone ? "done" : "open", recColor: x.recDone || x.rec === "None" ? "var(--color-neutral-500)" : t("risk").color,
      warranty: x.warranty || "", warrShow: x.warranty ? "inline" : "none",
      anomShow: x.anom && liveAnoms.some((a) => a.id === x.anom) ? "inline" : "none"
    }));
    // Grouping for volume. 40 decisions read as 5 groups with a count, a total
    // and one bulk action where every item in the group wants the same thing.
    const gk = s.mxGroup || "State";
    const keyOf = (d) => gk === "State" ? d.state : gk === "Source" ? d.src : gk === "Building" ? d.b : d.vendor;
    const GDESC = Object.assign({ "Blocked": "cannot proceed until a vendor or certificate is fixed", "To raise": "another module says an order should exist; none does", "Deviation": "live orders drifting off SLA or certificate", "Awaiting approval": "drafted, waiting for you" }, STATE_DESC);
    const gNames = []; decisions.forEach((d) => { const raw = dec.find((x) => (x.id || "not raised") === d.id && x.asset === d.asset); const k = keyOf(raw); if (gNames.indexOf(k) < 0) gNames.push(k); });
    const penny = (v) => parseInt(String(v).replace(/[^0-9]/g, ""), 10) || 0;
    const mxGroups = gNames.map((k, i) => {
      const raws = dec.filter((d) => keyOf(d) === k);
      const items = decisions.filter((d) => raws.some((r) => (r.id || "not raised") === d.id && r.asset === d.asset));
      const firsts = raws.map((r) => r.actions[0]); const same = !!firsts[0] && firsts.every((a) => a === firsts[0]) && raws.length > 1;
      const open = s.mxOpenG ? s.mxOpenG.indexOf(k) > -1 : i === 0;
      const tone = gk === "State" ? (ST[k] || "ok") : raws.some((r) => r.state === "Blocked" || r.state === "Deviation") ? "risk" : "warn";
      return {
        name: k, n: String(items.length), desc: gk === "State" ? (GDESC[k] || "") : items.length + (items.length === 1 ? " decision" : " decisions") + " · " + raws.filter((r) => r.state === "Blocked").length + " blocked · " + raws.filter((r) => r.state === "To raise").length + " to raise",
        total: "~£" + Math.round(raws.reduce((q, r) => q + penny(r.est), 0) / 100) / 10 + "k",
        color: t(tone).color, open: open, caret: open ? "ph-caret-down" : "ph-caret-right",
        toggle: () => this.setState((p) => { const cur = p.mxOpenG || [gNames[0]]; return { mxOpenG: cur.indexOf(k) > -1 ? cur.filter((x) => x !== k) : cur.concat([k]) }; }),
        bulkShow: same ? "inline-flex" : "none", bulk: same ? firsts[0] + " · all " + raws.length : "",
        bulkRun: (e) => { if (e && e.stopPropagation) e.stopPropagation(); this.orch(firsts[0] + " × " + raws.length + " (" + k + ")", raws.map((r) => r.asset).join(", ")); },
        items: items
      };
    });
    const gOpt = (label) => ({ label: label, on: gk === label, edge: gk === label ? "var(--color-accent)" : "var(--color-divider)", fg: gk === label ? "var(--color-accent)" : "var(--color-neutral-400)", bg: gk === label ? "var(--color-accent-900)" : "transparent", pick: () => this.setState({ mxGroup: label, mxOpenG: null }) });
    return {
      isMaint: isMx, isInsp: isInsp,
      mxGroups: mxGroups,
      mxGroupOpts: ["State", "Source", "Building", "Vendor"].map(gOpt),
      mxCards: live.mxLiveTiles ? live.mxLiveTiles.map((x) => ({ l: x.l, v: x.v, s: x.s, color: t(x.tone).color })) : [
        { l: "Decisions owed", v: String(MX.decisions.length), s: MX.decisions.filter((d) => d.state === "Blocked").length + " blocked · " + MX.decisions.filter((d) => d.state === "To raise").length + " to raise · " + MX.decisions.filter((d) => d.state === "Deviation").length + " deviating", color: t("risk").color },
        { l: "Statutory among them", v: String(MX.decisions.filter((d) => d.src === "Compliance").length), s: "certificate lapsed or inside 30 days", color: t("warn").color },
        { l: "Recommendations unconverted", v: String(openRec.length), s: "from " + I.length + " inspection reports since March", color: t("warn").color },
        { l: "PPM to plan", v: Math.round((done / planned) * 100) + "%", s: done + " of " + planned + " visits · " + missed + " missed · " + reports + " reports", color: missed > 5 ? t("risk").color : t("warn").color }
      ],
      mxDecisions: decisions, mxDecEmpty: decisions.length ? "none" : "block",
      mxDecSummary: decisions.length + " of " + MX.decisions.length + " decisions" + (f === "All" ? "" : " · filter: " + f),
      mxInsights: insights,
      mxInspQ: s.inspDraft || "",
      mxInspSet: (e) => this.setState({ inspDraft: e.target.value }),
      mxInspKey: (e) => { if (e.key === "Enter") this.openInsp(s.inspDraft || ""); },
      mxInspRun: () => this.openInsp(s.inspDraft || ""),
      mxInspOpen: () => this.openInsp(""),
      mxInspChips: ["Which recommendations were never converted to orders?", "What is under warranty?", "Which reports confirm the energy anomalies?", "Which assets are in the worst condition?", "Which vendor files the fewest reports?"].map((c) => ({ label: c, run: () => this.openInsp(c) })),
      mxPpm: ppm,
      mxPpmSummary: done + " of " + planned + " planned visits done year to date · " + missed + " missed · " + reports + " reports on file · " + MX.ppm.reduce((q2, p) => q2 + p.deferrals, 0) + " deferrals",
      inspQ: q, inspTitle: ans.title, inspTake: ans.take, inspRowsA: ans.rows.map((r) => ({ t: r })), inspAnsEmpty: ans.rows.length ? "none" : "block",
      inspRows: inspRows, inspCount: I.length + " reports · " + I.map((x) => x.asset).filter((v, i, arr) => arr.indexOf(v) === i).length + " assets · since 10 Mar 2026",
      inspBack: () => { window.scrollTo(0, 0); this.setState({ view: "module", module: "ops" }); },
      inspScope: "inspection reports · " + I.length + " on file"
    };
  },

  openInsp(q) {
    window.scrollTo(0, 0);
    this.setState({ view: "insp", inspQ: q, inspDraft: q, pq: "", detail: null, queueOpen: false });
  }
};
