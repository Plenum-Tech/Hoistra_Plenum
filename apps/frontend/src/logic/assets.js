// assets — asset condition from energy, condition scan, instrumented (IoT) assets and their failure model.
// Mixed into HoistraLogic.prototype, so `this` is the controller.
import { PACKS, CC_OF, ENC, t } from './constants.js';
import { HOISTRA_AS } from '../data/hoistra-assets.js';
import { HOISTRA_MX } from '../data/hoistra-maintenance.js';

export const assetsMethods = {

  /* Asset condition from energy. Two thresholds the user owns: how far over
     reference a section must be, and how long an anomaly must persist. */
  asVals(s) {
    const AS = HOISTRA_AS;
    const D = this.D();
    const pct = s.asPct, wks = s.asWeeks;
    const money = (n) => "£" + (n >= 1000 ? (n / 1000).toFixed(n >= 10000 ? 0 : 1) + "k" : Math.round(n));
    const pen = (n) => parseInt(String(n).replace(/[^0-9]/g, ""), 10) || 0;
    if (!AS) return { asThreatN: 0, asGroups: [], asCards: [], asBars: [] };
    const secOf = (a) => AS.sections.find((x) => x.b === a.b && x.sec === a.sec) || { eui: 0, ref: 1 };
    const evalA = (a) => {
      const sec = secOf(a);
      const delta = Math.round(((sec.eui - sec.ref) / sec.ref) * 100);
      const over = delta > pct;
      const an = a.anom ? D.anomalies.find((x) => x.id === a.anom) : null;
      const live = an && an.status !== "Resolved" ? an : null;
      const weeks = live ? live.days / 7 : 0;
      const persistent = live && weeks >= wks;
      let cond = "ok", kind = null;
      if (over && live) { cond = "threat"; kind = "threat"; }
      else if (over) { cond = "watch"; kind = "zone"; }
      else if (persistent) { cond = "watch"; kind = "persist"; }
      const why = cond === "ok"
        ? (live ? "Section in control (" + (delta > 0 ? "+" : "") + delta + "%). Anomaly " + weeks.toFixed(1) + " weeks old, under the " + wks + "-week persistence threshold. Watched, not flagged."
          : "Section in control (" + (delta > 0 ? "+" : "") + delta + "%) and no anomaly attributed. Nothing to act on from energy.")
        : AS.recommend[kind].why;
      // Value derived from the energy deviation. Current value is straight-line
      // on the class replacement value; the deviation (anomaly cost over the
      // section's annual spend, or half the section delta where the load is
      // shared) times the class wear coefficient is read as extra life consumed
      // over the next 12 months if nothing is done.
      const cl = AS.classes[a.cls] || { replace: 20000, life: 20, wear: 0.7 };
      const age = 2026 - a.installed;
      const cur = cl.replace * Math.max(0.1, 1 - age / cl.life);
      const tariff = (ENC[CC_OF[a.b] || "UK"] || ENC.UK).tariff;
      const secSpend = sec.eui * (parseInt(String(sec.area || "0").replace(/[^0-9]/g, ""), 10) || 1) * tariff;
      const dev = cond === "ok" ? 0 : live ? Math.min(0.6, pen(live.impact) / Math.max(1, secSpend)) : Math.min(0.5, Math.max(0, delta) / 100) * 0.5;
      const depr = cl.replace / cl.life;
      const loss = Math.min(cur - cl.replace * 0.1, cur * dev * cl.wear);
      const val = { replace: cl.replace, cur: cur, dev: dev, devSrc: live ? "anomaly cost ÷ section spend" : "½ section delta (shared load)", loss: Math.max(0, loss), after: Math.max(cl.replace * 0.1, cur - depr - Math.max(0, loss)), wear: cl.wear, life: cl.life, age: age };
      return { a, sec, delta, over, live, weeks, cond, kind, why, val };
    };
    const all = AS.assets.map(evalA);
    const flagged = all.filter((x) => x.cond !== "ok");
    const threats = all.filter((x) => x.cond === "threat");
    const watches = all.filter((x) => x.cond === "watch");
    const atStake = flagged.reduce((q, x) => q + (x.live ? pen(x.live.impact) : 0), 0);
    const f = s.filter;
    const pctF = /^Above (\d+)%/.exec(f);
    const shown = all.filter((x) => pctF ? x.delta > parseInt(pctF[1], 10) : f === "All" ? true : f === "Threat" ? x.cond === "threat" : f === "Watch" ? x.cond === "watch" : f === "In control" ? x.cond === "ok" : true);
    const rank = { threat: 0, watch: 1, ok: 2 };
    const TONEOF = { threat: "risk", watch: "warn", ok: "ok" };
    const LABEL = { threat: "Threat", watch: "Watch", ok: "In control" };
    const mail = (x, kind) => {
      const a = x.a, an = x.live;
      const isWO = kind === "wo";
      const subject = (isWO ? "Work order request — " : "Inspection request — ") + a.name + " · " + a.b;
      const evidence = [
        "• Section " + a.sec + " at " + x.sec.eui + " kWh/m²/yr against a reference of " + x.sec.ref + " (" + (x.delta > 0 ? "+" : "") + x.delta + "%) · " + x.sec.meter,
        an ? "• " + an.type + " on " + a.name + " · " + an.impact + " annualised · active " + an.days + " days · status " + an.status : "• No anomaly attributed to this asset; it shares the section load",
        "• Asset " + a.id + " · " + a.cls + " · installed " + a.installed + " · last PPM " + a.ppm + (a.l1 ? " · L1 asset" : "")
      ].join("\n");
      const body = "Hello " + a.vendor + " team,\n\n" + (isWO
        ? "Please raise a predictive work order on " + a.name + " at " + a.b + ". The energy engine has both the section and the asset out of pattern, so we are not waiting for a fault report.\n\nWhat the graph shows:\n" + evidence + "\n\nScope: diagnose and rectify the cause of the " + (an ? an.type.toLowerCase() : "excess load") + "; report findings and any parts against the rate schedule in the contract.\n\nPlease confirm attendance date within your P2 window."
        : "Please inspect " + a.name + " at " + a.b + ". The energy engine has flagged this asset for a condition check ahead of any fault.\n\nWhat the graph shows:\n" + evidence + "\n\nScope: condition inspection, controls and schedule check, and a short report with a recommendation. A work order follows if the report supports one.\n\nPlease propose a date within 10 working days.")
        + "\n\nRegards,\nPlanum Technologies · Hoistra";
      return { emKind: kind, emKicker: isWO ? "Work order request · draft" : "Inspection request · draft", emTo: a.email, emSubject: subject, emBody: body, fSubject: a.name + " · " + a.b, fVendor: a.vendor };
    };
    // Work orders and inspection notes on this asset, newest first. An open
    // recommendation on a flagged asset is the inspector having seen it first.
    const MX = HOISTRA_MX;
    const hist = (a) => {
      const wos = D.workorders.filter((w) => w.asset === a.name && w.building === a.b).map((w) => ({ kind: "wo", id: w.id, when: w.type + " · " + w.status, text: w.due, tone: w.tone, flag: "" }));
      const ins = MX ? MX.inspections.filter((x) => x.asset === a.name && x.b === a.b).map((x) => ({ kind: "insp", id: x.wo, when: x.date + " · " + x.vendor + " · grade " + x.cond, text: x.findings.join(" · "), tone: x.cond >= 4 ? "risk" : x.cond === 3 ? "warn" : "ok",
        flag: x.rec !== "None" ? (x.recDone ? "Recommendation done · " + x.rec : "Recommendation open · " + x.rec) : "", flagColor: x.rec !== "None" && !x.recDone ? t("risk").color : "var(--color-neutral-500)",
        warranty: x.warranty || "", warrShow: x.warranty ? "inline-block" : "none" })) : [];
      return ins.concat(wos.filter((w) => !ins.some((i) => i.id === w.id))).slice(0, 4).map((h) => Object.assign({ flagColor: "var(--color-neutral-500)", warranty: "", warrShow: "none" }, h, { icon: h.kind === "insp" ? "ph-clipboard-text" : "ph-wrench", color: t(h.tone).color, flagShow: h.flag ? "block" : "none" }));
    };
    const row = (x) => {
      const a = x.a, tone = t(TONEOF[x.cond]);
      const primary = x.kind ? AS.recommend[x.kind].primary : "inspect";
      return {
        id: a.id, name: a.name, cls: a.cls, vendor: a.vendor, meta: a.cls + " · " + a.id + " · installed " + a.installed + " · last PPM " + a.ppm + (a.l1 ? " · L1" : ""),
        cond: LABEL[x.cond], color: tone.color, bg: tone.bg, rail: x.cond === "ok" ? "var(--color-divider)" : tone.color,
        anomText: x.live ? x.live.type + " · " + x.live.impact + " · " + x.weeks.toFixed(1) + " wks" : "No anomaly attributed",
        anomColor: x.live ? "var(--color-text)" : "var(--color-neutral-500)",
        why: x.why,
        valNow: money(x.val.cur), valAfter: money(x.val.after), valLoss: x.val.loss > 0 ? "−" + money(x.val.loss) : "—",
        valLossColor: x.val.loss > 0 ? t("risk").color : "var(--color-neutral-500)",
        valLine: "of " + money(x.val.replace) + " new · " + x.val.age + " of " + x.val.life + " yrs" + (x.val.dev ? " · deviation " + Math.round(x.val.dev * 100) + "% (" + x.val.devSrc + ") × wear " + x.val.wear : ""),
        hist: hist(a), histShow: hist(a).length ? "flex" : "none",
        invShow: x.live ? "inline-flex" : "none",
        investigate: (e) => { if (e && e.stopPropagation) e.stopPropagation(); this.investigate("anomaly", x.live); },
        woPrimary: primary === "wo", inspectPrimary: primary !== "wo",
        wo: (e) => { if (e && e.stopPropagation) e.stopPropagation(); this.setState({ detail: null }); this.orchWith("Raise work order", a.name + " · " + a.b, "email", mail(x, "wo")); },
        inspect: (e) => { if (e && e.stopPropagation) e.stopPropagation(); this.setState({ detail: null }); this.orchWith("Request inspection", a.name + " · " + a.b, "email", mail(x, "inspect")); },
        open: () => this.setState({ detail: x.live ? this.anomalyDetail(x.live) : { title: a.name, sub: a.b + " · " + a.sec, tone: TONEOF[x.cond], status: LABEL[x.cond], body: x.why, chain: [
          { k: "sections", v: a.sec + " at " + x.sec.eui + " vs " + x.sec.ref + " kWh/m²/yr" },
          { k: "anomalies", v: "none attributed to " + a.id },
          { k: "assets", v: a.cls + " · installed " + a.installed + " · last PPM " + a.ppm }
        ], fields: [], actions: [] } })
      };
    };
    const bNames = []; shown.forEach((x) => { if (bNames.indexOf(x.a.b) < 0) bNames.push(x.a.b); });
    const bRec = (bn) => D.buildings.find((b) => b.name === bn) || { eui: 0, bench: 1 };
    const bDelta = (bn) => { const b = bRec(bn); return Math.round(((b.eui - b.bench) / b.bench) * 100); };
    const groups = bNames.map((bn) => {
      const xs = shown.filter((x) => x.a.b === bn);
      const secs = []; xs.forEach((x) => { if (secs.indexOf(x.a.sec) < 0) secs.push(x.a.sec); });
      const bAll = all.filter((x) => x.a.b === bn);
      const bt = bAll.filter((x) => x.cond === "threat").length, bw = bAll.filter((x) => x.cond === "watch").length;
      const nWO = bAll.filter((x) => x.kind === "threat").length, nIns = bAll.filter((x) => x.kind === "zone" || x.kind === "persist").length;
      const b = bRec(bn), bd = bDelta(bn), bOver = bd > 0;
      const secOverN = AS.sections.filter((x) => x.b === bn && Math.round(((x.eui - x.ref) / x.ref) * 100) > pct).length;
      const ccOf = CC_OF[bn] || "UK";
      const std = (PACKS[ccOf] || PACKS.UK).std;
      const bOpen = s.asOpenB.indexOf(bn) > -1;
      return {
        name: bn, open: bOpen, caret: bOpen ? "ph-caret-down" : "ph-caret-right",
        toggle: () => this.setState((p) => ({ asOpenB: p.asOpenB.indexOf(bn) > -1 ? p.asOpenB.filter((x) => x !== bn) : p.asOpenB.concat([bn]) })),
        eui: b.eui + " vs " + b.bench + " kWh/m²/yr", delta: (bd > 0 ? "+" : "") + bd + "%",
        std: std === "NA" ? "rolling portfolio benchmark" : std,
        pct: Math.min(100, Math.round((b.eui / (b.bench * 1.5)) * 100)) + "%", refPct: Math.round(100 / 1.5) + "%",
        barColor: bd > 10 ? t("risk").color : bOver ? t("warn").color : t("ok").color,
        state: bOver ? "above reference" : "under reference", stColor: bOver ? t(bd > 10 ? "risk" : "warn").color : t("ok").color, stBg: bOver ? t(bd > 10 ? "risk" : "warn").bg : t("ok").bg,
        counts: bt + " threat · " + bw + " watch · " + (bAll.length - bt - bw) + " in control",
        secLine: secOverN + " of " + AS.sections.filter((x) => x.b === bn).length + " sections over reference",
        actions: (nWO || nIns) ? [nWO ? nWO + (nWO === 1 ? " work order" : " work orders") : null, nIns ? nIns + (nIns === 1 ? " inspection" : " inspections") : null].filter(Boolean).join(" · ") + " recommended" : "No action from energy",
        actColor: (nWO || nIns) ? "var(--color-accent)" : "var(--color-neutral-500)",
        sections: secs.map((sn) => {
          const sx = xs.filter((x) => x.a.sec === sn).sort((p, q) => rank[p.cond] - rank[q.cond] || (q.live ? pen(q.live.impact) : 0) - (p.live ? pen(p.live.impact) : 0));
          const sec = sx[0].sec, d = sx[0].delta, over = sx[0].over;
          const st = all.filter((x) => x.a.b === bn && x.a.sec === sn && x.cond === "threat").length, sw = all.filter((x) => x.a.b === bn && x.a.sec === sn && x.cond === "watch").length;
          const sk = bn + "|" + sn, sOpen = s.asOpenS.indexOf(sk) > -1;
          return {
            name: sn, eui: sec.eui + " vs " + sec.ref, delta: (d > 0 ? "+" : "") + d + "%", meter: sec.meter, d: d,
            open: sOpen, caret: sOpen ? "ph-caret-down" : "ph-caret-right",
            toggle: () => this.setState((p) => ({ asOpenS: p.asOpenS.indexOf(sk) > -1 ? p.asOpenS.filter((x) => x !== sk) : p.asOpenS.concat([sk]) })),
            flags: (st || sw) ? [st ? st + " threat" : null, sw ? sw + " watch" : null].filter(Boolean).join(" · ") : sx.length + (sx.length === 1 ? " asset" : " assets"),
            state: over ? "over reference" : "in control", stColor: over ? t("risk").color : t("ok").color, stBg: over ? t("risk").bg : t("ok").bg,
            pct: Math.min(100, Math.round((sec.eui / (sec.ref * 1.5)) * 100)) + "%", refPct: Math.round(100 / 1.5) + "%", barColor: over ? t("risk").color : d > 0 ? t("warn").color : t("ok").color,
            rows: sx.map(row)
          };
        }).sort((p, q) => q.d - p.d)
      };
    }).sort((p, q) => bDelta(q.name) - bDelta(p.name));
    const bump = (k, d, lo, hi) => () => this.setState((p) => ({ [k]: Math.max(lo, Math.min(hi, p[k] + d)) }));
    return {
      asThreatN: threats.length,
      asAll: all,
      asCards: [
        { l: "Threat", v: String(threats.length), s: "section over reference + anomaly on asset", color: t("risk").color },
        { l: "Watch", v: String(watches.length), s: all.filter((x) => x.kind === "zone").length + " shared section · " + all.filter((x) => x.kind === "persist").length + " persistent anomaly", color: t("warn").color },
        { l: "Est. asset value at risk / year", v: money(flagged.reduce((q, x) => q + x.val.loss, 0)), s: money(flagged.reduce((q, x) => q + x.val.cur, 0)) + " → " + money(flagged.reduce((q, x) => q + x.val.after, 0)) + " if not repaired", color: t("risk").color },
        { l: "In control", v: all.length - flagged.length + " of " + all.length, s: all.filter((x) => x.cond === "ok" && x.live).length + " with an anomaly under threshold", color: t("ok").color }
      ],
      asPct: pct + "%", asWeeks: String(wks), asWeeksUnit: wks === 1 ? "week" : "weeks",
      asPctDown: bump("asPct", -5, 5, 40), asPctUp: bump("asPct", 5, 5, 40),
      asWkDown: bump("asWeeks", -1, 1, 12), asWkUp: bump("asWeeks", 1, 1, 12),
      asGroups: groups,
      asEmpty: groups.length ? "none" : "block",
      asSummary: groups.length + (groups.length === 1 ? " building · " : " buildings · ") + shown.length + " of " + all.length + " assets" + (f === "All" ? "" : " · filter: " + f) + " · buildings ranked by EUI against reference"
    };
  },


  /* Instrumented assets: live telemetry, failure model with its own accuracy,
     remaining life, and the remediate-or-replace case on asset value. */
  iotVals(s) {
    const AS = HOISTRA_AS;
    if (!AS || !AS.iot || !(s.view === "module" && s.module === "assets")) return { iotCards: [] };
    const tick = s.iotTick;
    const fmt = (v, u) => (u === "bar" || u === "mm/s" || (u === "°C" && v < 50) ? v.toFixed(1) : Math.round(v)) + (u === "%" ? "%" : " " + u);
    const spark = (hist, lo, hi) => {
      const w = 84, h = 22;
      const min = Math.min.apply(null, hist.concat([lo])), max = Math.max.apply(null, hist.concat([hi]));
      const rng = (max - min) || 1;
      const pts = hist.map((v, i) => (i / (hist.length - 1) * w).toFixed(1) + "," + (h - ((v - min) / rng) * h).toFixed(1)).join(" ");
      const hiY = h - ((hi - min) / rng) * h;
      return { pts: pts, hiY: hiY.toFixed(1), w: w, h: h };
    };
    const cards = AS.iot.map((a) => {
      const open = s.iotOpen === a.id;
      const readings = a.readings.map((r) => {
        const jitter = Math.sin((tick + r.k.length) * 0.9) * (r.hi - r.lo) * 0.004;
        const v = r.v + r.drift * tick + jitter;
        const out = v > r.hi || v < r.lo;
        const near = !out && (v > r.hi - (r.hi - r.lo) * 0.1 || v < r.lo + (r.hi - r.lo) * 0.1);
        const tone = out ? "risk" : near ? "warn" : "ok";
        const sp = spark(r.hist.concat([v]), r.lo, r.hi);
        return {
          k: r.k, v: fmt(v, r.u), band: r.lo + "–" + r.hi + (r.u === "%" ? "%" : " " + r.u),
          color: t(tone).color, dot: t(tone).color, note: r.note, noteShow: r.note ? "block" : "none",
          pts: sp.pts, hiY: sp.hiY, state: out ? "out of band" : near ? "near limit" : "in band"
        };
      });
      const nOut = readings.filter((r) => r.state === "out of band").length;
      const pTone = a.model.pFail >= 50 ? "risk" : a.model.pFail >= 25 ? "warn" : "ok";
      const lifePct = Math.round((a.life.age / a.life.design) * 100);
      const rem = a.recommend === "remediate";
      const mail = (kind) => {
        const isRep = kind === "replace";
        return {
          emKind: isRep ? "wo" : "wo", fSubject: a.name + " · " + a.b, fVendor: a.vendor,
          emKicker: isRep ? "Replacement case · draft" : "Remediation work order · draft", emTo: isRep ? "capex@planumtech.com" : a.email,
          emSubject: (isRep ? "Replacement case — " : "Remediation work order — ") + a.name + " · " + a.b,
          emBody: "Hello,\n\n" + (isRep
            ? "Please open a capex case to replace " + a.name + " at " + a.b + ".\n\nWhy now:\n• Failure model: " + a.model.pFail + "% probability of failure within " + a.model.horizon + " (accuracy " + a.model.accuracy + "% · precision " + a.model.precision + "% · recall " + a.model.recall + "%)\n• Remaining useful life: " + a.life.rulP50 + " months (P50), range " + a.life.rulLo + "–" + a.life.rulHi + "\n• Age " + a.life.age + " of " + a.life.design + " design years · book value " + a.value.book + " · replacement " + a.value.replace + "\n• Gain: " + a.value.replaceGain
            : "Please raise a remediation work order on " + a.name + " at " + a.b + ".\n\nLive telemetry (" + a.sensors + " sensors, " + nOut + " out of band):\n" + readings.filter((r) => r.state !== "in band").map((r) => "• " + r.k + " " + r.v + " (band " + r.band + ")" + (r.note ? " — " + r.note : "")).join("\n") + "\n\nFailure model: " + a.model.pFail + "% within " + a.model.horizon + " · drivers: " + a.model.drivers.join("; ") + "\n\nScope: " + a.value.remediateWhat + ". Estimate " + a.value.remediate + " against the rate schedule.\nExpected effect: " + a.value.remediateGain + ".\n\nPlease confirm attendance within your P2 window.")
            + "\n\nRegards,\nPlanum Technologies · Hoistra"
        };
      };
      return {
        id: a.id, name: a.name, cls: a.cls, b: a.b, sec: a.sec, feed: a.feed, sensors: String(a.sensors), vendor: a.vendor,
        open: open, caret: open ? "ph-caret-down" : "ph-caret-right",
        toggle: () => this.setState((p) => ({ iotOpen: p.iotOpen === a.id ? null : a.id })),
        live: (tick % 2 === 0) ? "1" : "0.35",
        last: (tick % 3) + 1 + " s ago",
        outLine: nOut + " of " + a.readings.length + " readings out of band",
        outColor: nOut ? t("risk").color : t("ok").color,
        readings: readings,
        pFail: a.model.pFail + "%", pColor: t(pTone).color, pBg: t(pTone).bg, horizon: a.model.horizon,
        acc: a.model.accuracy + "%", prec: a.model.precision + "%", rec: a.model.recall + "%", trained: a.model.trained,
        drivers: a.model.drivers.map((d) => ({ t: d })),
        rul: a.life.rulP50 + " " + a.life.unit, rulRange: "P10–P90 · " + a.life.rulLo + "–" + a.life.rulHi + " " + a.life.unit,
        age: a.life.age + " of " + a.life.design + " yrs", lifePct: lifePct + "%", lifeColor: lifePct > 80 ? t("risk").color : lifePct > 60 ? t("warn").color : t("ok").color,
        hours: a.hoursRun.toLocaleString() + " h run",
        book: a.value.book, replaceCost: a.value.replace, remCost: a.value.remediate,
        remWhat: a.value.remediateWhat, remGain: a.value.remediateGain, repGain: a.value.replaceGain, when: a.value.when,
        remEdge: rem ? "var(--color-accent)" : "var(--color-divider)", repEdge: rem ? "var(--color-divider)" : "var(--color-accent)",
        remTag: rem ? "Recommended" : "", repTag: rem ? "" : "Recommended",
        remediate: (e) => { if (e && e.stopPropagation) e.stopPropagation(); this.orchWith("Schedule remediation", a.name + " · " + a.b, "email", mail("remediate")); },
        replace: (e) => { if (e && e.stopPropagation) e.stopPropagation(); this.orchWith("Open replacement case", a.name + " · " + a.b, "email", mail("replace")); },
        invShow: a.id === "AS-1042" ? "inline-flex" : "none",
        investigate: (e) => { if (e && e.stopPropagation) e.stopPropagation(); const an = this.D().anomalies.find((x) => x.id === "a1"); if (an) this.investigate("anomaly", an); }
      };
    });
    return { iotCards: cards };
  },


  /* Condition scan flow in the orchestrator: pick buildings → pick sections or
     all → run (sections tick in) → report. Running re-reads the register and
     stamps last run, so the page's numbers move with it. */
  scanVals(s) {
    const AS = HOISTRA_AS;
    if (!AS || s.flow !== "scan") return { scStage0: false, scStage1: false, scStage2: false, scStage3: false, scBuildings: [], scSections: [], scRunRows: [], scReport: {} };
    const bNames = []; AS.sections.forEach((x) => { if (bNames.indexOf(x.b) < 0) bNames.push(x.b); });
    const selB = s.asScanB, selSec = s.asScanSec, allSec = s.asScanAllSec;
    const secsIn = AS.sections.filter((x) => selB.indexOf(x.b) > -1);
    const secKey = (x) => x.b + "|" + x.sec;
    const chosen = allSec ? secsIn : secsIn.filter((x) => selSec.indexOf(secKey(x)) > -1);
    const stage = s.asScanStage;
    const on = (v) => ({ edge: v ? "var(--color-accent)" : "var(--color-divider)", bg: v ? "var(--color-accent-900)" : "transparent", fg: v ? "var(--color-accent)" : "var(--color-neutral-300)" });
    const rep = s.asScanReport || {};
    return {
      scStage0: stage === 0, scStage1: stage === 1, scStage2: stage === 2, scStage3: stage === 3,
      scBuildings: bNames.map((b) => Object.assign({ name: b, n: AS.sections.filter((x) => x.b === b).length + " sections", on: selB.indexOf(b) > -1,
        pick: () => this.setState((p) => ({ asScanB: p.asScanB.indexOf(b) > -1 ? p.asScanB.filter((x) => x !== b) : p.asScanB.concat([b]) })) }, on(selB.indexOf(b) > -1))),
      scAllB: () => this.setState({ asScanB: bNames.slice() }),
      scBCount: selB.length ? selB.length + " of " + bNames.length + " buildings" : "none selected",
      scBNext: selB.length > 0,
      scToSections: () => { if (selB.length) this.setState({ asScanStage: 1 }); },
      scAllSecOn: on(allSec), scPickSecOn: on(!allSec),
      scAllSec: () => this.setState({ asScanAllSec: true }),
      scPickSec: () => this.setState({ asScanAllSec: false }),
      scSecListShow: allSec ? "none" : "flex",
      scSections: secsIn.map((x) => Object.assign({ key: secKey(x), b: x.b, name: x.sec, meter: x.meter,
        pick: () => this.setState((p) => ({ asScanSec: p.asScanSec.indexOf(secKey(x)) > -1 ? p.asScanSec.filter((k) => k !== secKey(x)) : p.asScanSec.concat([secKey(x)]) })) }, on(selSec.indexOf(secKey(x)) > -1))),
      scSecCount: allSec ? "all " + secsIn.length + " sections" : selSec.length + " of " + secsIn.length + " sections",
      scCanRun: chosen.length > 0,
      scBack: () => this.setState({ asScanStage: 0 }),
      scRun: () => { if (chosen.length) this.runScan(chosen); },
      scRunRows: chosen.map((x, i) => ({ b: x.b, name: x.sec, done: i < s.asScanDone, dot: i < s.asScanDone ? "var(--st-ok)" : "var(--color-neutral-700)", state: i < s.asScanDone ? "read" : i === s.asScanDone ? "reading…" : "" , fg: i < s.asScanDone ? "var(--color-text)" : "var(--color-neutral-500)" })),
      scProgress: Math.round((s.asScanDone / Math.max(1, chosen.length)) * 100) + "%",
      scReport: rep,
      scRepRows: rep.rows || [],
      scRepEmpty: rep.rows && rep.rows.length ? "none" : "block",
      scClose: () => this.setState({ flow: null }),
      scAgain: () => this.setState({ asScanStage: 0, asScanDone: 0, asScanReport: null })
    };
  },

  runScan(chosen) {
    clearInterval(this._scanTick);
    this.setState({ asScanStage: 2, asScanDone: 0 });
    this._scanTick = setInterval(() => {
      const d = this.state.asScanDone + 1;
      if (d < chosen.length) return this.setState({ asScanDone: d });
      clearInterval(this._scanTick);
      // Re-evaluate the register for the chosen sections and stamp the run.
      const v = this.asVals(this.state);
      const keys = chosen.map((x) => x.b + "|" + x.sec);
      const rows = [];
      v.asAll.forEach((x) => { if (keys.indexOf(x.a.b + "|" + x.a.sec) > -1 && x.cond !== "ok") rows.push({ name: x.a.name, b: x.a.b, sec: x.a.sec, cond: x.cond === "threat" ? "Threat" : "Watch", color: t(x.cond === "threat" ? "risk" : "warn").color, bg: t(x.cond === "threat" ? "risk" : "warn").bg, why: x.why.split(". ")[0] + ".", act: x.kind === "threat" ? "Raise work order" : "Request inspection" }); });
      rows.sort((p, q) => (p.cond === "Threat" ? 0 : 1) - (q.cond === "Threat" ? 0 : 1));
      const now = new Date(); const hh = String(now.getHours()).padStart(2, "0") + ":" + String(now.getMinutes()).padStart(2, "0");
      const nT = rows.filter((r) => r.cond === "Threat").length, nW = rows.length - nT;
      const inScope = v.asAll.filter((x) => keys.indexOf(x.a.b + "|" + x.a.sec) > -1).length;
      const bN = []; chosen.forEach((x) => { if (bN.indexOf(x.b) < 0) bN.push(x.b); });
      this.setState({
        asScanStage: 3, asScanDone: chosen.length, asLastRun: hh + " today",
        asScanReport: {
          title: "Condition scan · " + bN.length + (bN.length === 1 ? " building" : " buildings") + " · " + chosen.length + (chosen.length === 1 ? " section" : " sections"),
          when: hh + " today · thresholds " + this.state.asPct + "% over reference · " + this.state.asWeeks + " weeks persistence",
          summary: inScope + " assets read. " + nT + (nT === 1 ? " threat" : " threats") + " and " + nW + (nW === 1 ? " watch" : " watches") + "; " + (inScope - rows.length) + " in control. Sections read against their own reference; anomalies re-attributed from the latest baseline.",
          rows: rows
        }
      });
      this.flash("Condition scan complete · last run " + hh);
    }, 550);
  }
};
