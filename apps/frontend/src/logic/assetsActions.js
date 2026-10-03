// assetsActions — the Assets page's three row actions as data: Raise work order, Request
// inspection, Investigate.
//
// They follow the design (the Hoistra_1 prototype): the dock opens, the four agents run, and a
// draft or an investigation fills it. What differs is where the words come from. The prototype
// typed its findings into a table per anomaly type; here every figure is one a read returned —
// the condition read, the per-asset read, GET /api/energy/assets/{id}/investigate — and what no
// read returned is left out rather than filled. These drafts go to real vendors (the platform
// sends through Microsoft Graph), so a blank beats a guess every time: no address is invented,
// no "null" reaches a vendor, and a cost the engine did not compute is not shown.
//
// Pure. The controller methods that use these live in assetsCondition.js.

const num = (v) => (typeof v === "number" && isFinite(v) ? v : null);
const gbp = (v) => (num(v) === null ? null : "£" + Math.round(v).toLocaleString("en-GB"));
const year = (d) => { const m = /^(\d{4})/.exec(String(d || "")); return m ? m[1] : (d ? String(d) : null); };

/** "AHU-3 · Bishopsgate Tower" — the label a task, a session and a subject line all share. */
export function actionCtx(f) {
  return (f && f.assetName ? f.assetName : "Asset") + " · " + (f && f.building ? f.building : "Unlinked");
}

// What the graph shows, one line per fact a read returned.
function evidenceLines(f) {
  const lines = [];
  const sec = f.section;
  if (sec && sec.name) {
    const eui = num(sec.eui), ref = num(sec.ref), dev = num(sec.deviation);
    const meters = sec.meters ? " · " + sec.meters : "";
    // "Not metered" only where the section has no meter. `measured` is whether an intensity
    // was COMPUTED — a section with meters but no floor area is not measured and is metered.
    // A metered section with no reference is reported as metered; one known only by name (the
    // per-asset read) is just named.
    const metered = num(sec.meterCount) !== null ? sec.meterCount > 0 : null;
    lines.push(eui !== null && ref !== null
      ? "• Section " + sec.name + " at " + eui + " kWh/m²/yr against a reference of " + ref
        + (dev !== null ? " (" + (dev > 0 ? "+" : "") + Math.round(dev) + "%)" : "") + meters
      : eui !== null ? "• Section " + sec.name + " at " + eui + " kWh/m²/yr — no reference on record to compare it with" + meters
      : metered ? "• Section " + sec.name + " — " + (sec.meters || "sub-meters") + " on record, but no intensity could be computed from them"
      : metered === false || sec.measured === false ? "• Section " + sec.name + " — not metered, so its load is not measured"
      : "• Section " + sec.name);
  }
  const an = f.anomaly;
  lines.push(an && an.type
    ? "• " + an.type + " on " + f.assetName
      + (gbp(an.annualCost) ? " · " + gbp(an.annualCost) + " annualised" : "")
      + (num(an.days) !== null ? " · active " + an.days + " day" + (an.days === 1 ? "" : "s") : "")
      + (an.status ? " · status " + an.status : "")
    : "• No anomaly attributed to this asset; it shares the section load");
  lines.push("• Asset " + (f.assetCode || f.assetId || f.assetName)
    + (f.category ? " · " + f.category : "")
    + (year(f.installed) ? " · installed " + year(f.installed) : "")
    + (f.latestNote && f.latestNote.id ? " · latest inspection note " + f.latestNote.id + (f.latestNote.when ? " · " + f.latestNote.when : "") : ""));
  return lines.join("\n");
}

/**
 * The draft the dock opens for Raise work order (`wo`) or Request inspection (`inspect`).
 * `opts.scope` lets an investigation hand over its own scope line.
 */
export function mailDraft(kind, f, opts) {
  if (kind === "records") return recordsDraft(f, opts);
  const isWo = kind === "wo";
  const ctx = actionCtx(f);
  const where = f.assetName + (f.building ? " at " + f.building : "");
  const an = f.anomaly && f.anomaly.type ? f.anomaly : null;
  const sectionOver = f.section && num(f.section.deviation) !== null && f.section.deviation > 0;
  const scope = opts && opts.scope ? String(opts.scope).replace(/\.\s*$/, "") + "." : null;
  const intro = isWo
    ? "Please raise a predictive work order on " + where + ". "
      + (an && sectionOver ? "The energy engine has both the section and the asset out of pattern, so we are not waiting for a fault report."
        : an ? "The energy engine has an open anomaly on this asset, so we are not waiting for a fault report."
        : sectionOver ? "The section it serves is over its reference, so we are not waiting for a fault report."
        : "We would like it looked at before a fault is reported.")
    : "Please inspect " + where + ". The energy engine has flagged this asset for a condition check ahead of any fault.";
  const scopeLine = "Scope: " + (scope || (isWo
    ? "diagnose and rectify the cause of the " + (an ? an.type.toLowerCase() : "excess load") + "; report findings and any parts against the rate schedule in the contract."
    : "condition inspection, controls and schedule check, and a short report with a recommendation. A work order follows if the report supports one."));
  const close = isWo ? "Please confirm your attendance date." : "Please propose a date within 10 working days.";
  const body = (f.vendor ? "Hello " + f.vendor + " team," : "Hello,") + "\n\n"
    + intro + "\n\nWhat the graph shows:\n" + evidenceLines(f) + "\n\n" + scopeLine + "\n\n" + close
    + "\n\nRegards,\n" + (f.sender ? f.sender + " · Hoistra" : "Hoistra");
  return {
    emKind: isWo ? "wo" : "inspect",
    emKicker: isWo ? "Work order request · draft" : "Inspection request · draft",
    emTo: f.vendorEmail || "",
    emSubject: (isWo ? "Work order request — " : "Inspection request — ") + ctx,
    emBody: body,
    fSubject: ctx, fVendor: f.vendor || ""
  };
}

/** A request for records the engine found missing — a report, a service or treatment log.
 *  It went out as an inspection request before: "Please inspect … propose a date within 10
 *  working days", and a vendor can reasonably book, and bill, a site visit for what was a
 *  request for documents. */
function recordsDraft(f, opts) {
  const ctx = actionCtx(f);
  const where = f.assetName + (f.building ? " at " + f.building : "");
  const scope = opts && opts.scope ? String(opts.scope).replace(/\.\s*$/, "") + "." : "The records for the last planned visit are not on file.";
  const body = (f.vendor ? "Hello " + f.vendor + " team," : "Hello,") + "\n\n"
    + "We are missing records for " + where + ". " + scope + "\n\n"
    + "Please send the report and the service or treatment log for that visit by reply to this email, attached. "
    + "No site visit is being requested.\n\nRegards,\n" + (f.sender ? f.sender + " · Hoistra" : "Hoistra");
  return {
    emKind: "records", emKicker: "Records request · draft", emTo: f.vendorEmail || "",
    emSubject: "Records request — " + ctx, emBody: body, fSubject: ctx, fVendor: f.vendor || ""
  };
}

/** The four agents for a draft — each line says what this action actually did. */
export function actionSteps(kind, f) {
  const read = [f.section && f.section.name ? "the section against its reference" : null,
    f.anomaly && f.anomaly.type ? "the open anomaly" : null,
    f.latestNote && f.latestNote.id ? "the inspection notes" : null, "the vendor on file"].filter(Boolean);
  return [
    { a: "Orchestrator", t: "Intent: " + (kind === "wo" ? "raise work order" : kind === "records" ? "request missing records" : "request inspection") + " · scope: " + actionCtx(f) },
    { a: "Planner", t: "Read the asset record — " + read.join(", ") },
    // A read that FAILED is not a record that is EMPTY: the reader should retry or look, not
    // file a data fix for a vendor that may well carry an address.
    { a: "Worker", t: f.vendor
        ? "Drafted the request to " + f.vendor + (f.vendorEmail ? " at " + f.vendorEmail
          : f.vendorReadFailed ? " — the vendor record could not be read just now, so the address is not filled; add it below"
          : (f.vendorEmailCandidates || []).length
            ? " — several contacts on record and none marked primary, so the address is left for you: " + f.vendorEmailCandidates.join(", ")
          : " — no address on the vendor record, add one below")
        : f.vendorReadFailed ? "The asset record could not be read just now, so the draft is unaddressed — add the recipient below"
        : "No vendor on the asset record — the draft is unaddressed; add the recipient below" },
    { a: "Quality", t: "Nothing is sent until you approve — the address and the wording are yours to check" }
  ];
}

/** The question the investigation answers, in the anomaly's own terms. */
export function investigationQuestion(f) {
  const where = f.assetName + (f.building ? " at " + f.building : "");
  const an = f.anomaly && f.anomaly.type ? f.anomaly : null;
  return an
    ? "Why is " + where + " showing a " + an.type.toLowerCase() + (gbp(an.annualCost) ? " worth " + gbp(an.annualCost) + " a year" : "") + ", and what should I do about it?"
    : "Why is " + where + " where it is, and what should I do about it?";
}

/** The four agents for an investigation; the walk and the verdict fill in once they are known. */
export function investigationSteps(f, res) {
  const srcs = res && Array.isArray(res.sources) ? res.sources.map((x) => x.source) : null;
  const c = res && res.conclusion;
  return [
    { a: "Orchestrator", t: "Intent: investigate " + actionCtx(f).toLowerCase() + " · why is it where it is, and what can be done" },
    { a: "Planner", t: srcs ? "Walk " + srcs.length + " sources: " + srcs.join(", ")
        : "Plan the walk: the readings first, then the maintenance record, then the documents that should exist" },
    { a: "Worker", t: "Retrieving rows, weighing each finding by the rule that produced it, flagging any record that should exist and does not" },
    { a: "Quality", t: !c ? "Nothing is written — every action comes back as a proposal for you to approve"
        : c.confirmation_required
          ? "Evidence rests on inference or a missing record — confirmation armed: ask the vendor, plan an inspection"
          : "Cause read from the record — actions drafted, none executed until approved" }
  ];
}

/** "£4,430 to date · £38,400 annualised if left" — only the figures the engine computed. */
export function invCostLine(c) {
  const toDate = gbp(c && c.cost_to_date), annual = gbp(c && c.cost_annualised);
  if (toDate && annual) return toDate + " to date · " + annual + " annualised if left";
  if (annual) return annual + " annualised if left";
  if (toDate) return toDate + " to date";
  return "No cost is attributed to this asset";
}

// The engine's proposals, kept only where this page can carry one out. Each drafts an email to
// the vendor (the only write the reader asked for); a change this platform cannot make — a BMS
// re-sequence, a scheduled re-check — is shown for what it is and not offered as a button.
function invActions(res, f, escalate) {
  const out = (res.actions || []).map((a) => {
    const body = a.body || {};
    const rt = String(body.request_type || "").toLowerCase();
    if (a.id === "raise_work_order" || (a.endpoint && /work-orders/.test(a.endpoint) && rt !== "inspection")) {
      return { k: "wo", l: a.label, s: a.detail || "", scope: body.issue_description || null };
    }
    if (a.id === "request_records") {
      return { k: "records", l: a.label, s: a.detail || "", scope: body.issue_description || null };
    }
    if (rt === "inspection") {
      return { k: "inspect", l: a.label, s: a.detail || "", scope: body.issue_description || null };
    }
    return { k: "note", l: a.label, s: [a.detail, a.note].filter(Boolean).join(" — ") };
  });
  // A records request is the confirmation the caveat asks for ("request the missing
  // document"), so it stands in for the extra inspection as much as an inspection does.
  if (escalate && !out.some((a) => a.k === "inspect" || a.k === "records")) {
    out.push({ k: "inspect", l: "Request an inspection", s: "the cause needs confirming on site · draft to " + (f.vendor || "the vendor"), scope: null });
  }
  return out;
}

/** GET /investigate's answer, shaped for the dock's investigation cards. */
export function shapeInvestigation(res, f) {
  const c = res.conclusion || {};
  const escalate = !!c.confirmation_required;
  return {
    live: true, loading: false, error: "",
    kind: "asset", assetId: f.assetId, title: actionCtx(f),
    sub: f.vendor || (res.asset && res.asset.vendor) || "",
    query: investigationQuestion(f),
    plan: res.plan || "",
    sources: (res.sources || []).map((x) => ({ tbl: x.source, what: x.question || "", n: x.badge || x.status || "", status: x.status || "" })),
    findings: (res.evidence || []).map((e) => ({
      t: e.statement, src: (e.sources || []).join(" · "),
      conf: num(e.confidence) === null ? null : Math.round(e.confidence * 100),
      gap: e.kind === "missing record" || e.kind === "out of band",
      // The chip says which kind of fact it is: a record that is not there, or a reading
      // outside the band set for it. It read "missing record" for both.
      chip: e.kind === "out of band" ? "out of band" : "missing record"
    })),
    cause: c.cause || c.statement || "The sources did not settle a cause.",
    costLine: invCostLine(c),
    escalate: escalate,
    escText: c.caveat || (escalate ? "The sources do not settle the cause. Confirm on site before anything is claimed." : ""),
    actions: invActions(res, f, escalate),
    replies: []
  };
}

/** Why an investigation could not be shown, in the reader's terms. */
export function investigationError(e) {
  if (e && e.status === 404) return "This asset is not in the buildings you can see, so it cannot be investigated from here.";
  return "The investigation did not answer — " + ((e && e.message) || "no response") + ".";
}

// ── The datasets behind the walk, and the orchestrator's summary of them ───────────────

/** What each dataset is called in the dock. */
export const SOURCE_LABEL = { bms_trend: "BMS trend", utility_bill: "Utility bill (metered)", weather: "Weather" };

/** A dataset as the endpoint returned it, without the envelope (ok, written, asset). */
export function datasetOf(res) {
  if (!res || typeof res !== "object") return { status: "unreadable", detail: "the endpoint returned nothing" };
  const out = Object.assign({}, res);
  delete out.ok; delete out.written; delete out.asset;
  return out;
}

/** A dataset read that failed, stated as the same "unreadable" the engine uses. Only the
 *  route's own 404 — which names a reason — means the asset is out of reach; FastAPI's bare
 *  {"detail":"Not Found"} means the deployed backend predates the route, and says nothing
 *  about the data. */
export function datasetFailure(e) {
  const detail = e && e.body && e.body.detail;
  if (e && e.status === 404 && detail && typeof detail === "object") {
    return { status: "not_found", detail: "the asset or building is not in the buildings you can see" };
  }
  if (e && e.status === 404) return { status: "unreadable", detail: "the endpoint is not on the deployed backend (HTTP 404) — it says nothing about the data" };
  return { status: "unreadable", detail: "the endpoint did not answer" + (e && e.status ? " (HTTP " + e.status + ")" : "") };
}

/** The body the summariser is sent: the asset, and the three datasets as fetched. */
export function summaryBody(f, datasets) {
  return {
    asset: { name: f.assetName || null, building: f.building || null, category: f.category || null },
    sources: { bms_trend: datasets.bms_trend, utility_bill: datasets.utility_bill, weather: datasets.weather }
  };
}

/** The dock reply the summary becomes: one line per source, the overall sentence, and — when
 *  the check withheld anything — a note saying so. A failure is said plainly. */
export function summaryReply(res, err) {
  const base = { id: "summary", you: "", tag: "summary · " };
  if (err || !res || res.ok === false) {
    const why = (res && res.reason) || (err && err.message) || "no response";
    return Object.assign(base, { bot: "Summary unavailable — " + why + ". The walk above still stands.", lines: [] });
  }
  const withheld = (res.dropped || []).filter((d) => !d.kept);
  const flagged = (res.dropped || []).filter((d) => d.kept);
  const notes = [];
  if (withheld.length) notes.push(withheld.length + (withheld.length === 1 ? " line" : " lines") + " withheld — it cited a figure not in the data");
  if (flagged.length) notes.push("the overall sentence cites a figure not in the data (" + flagged[0].figures.join(", ") + ")");
  return Object.assign(base, {
    lines: (res.lines || []).map((l) => ({ label: SOURCE_LABEL[l.source] || l.source, text: l.text })),
    bot: res.overall || "",
    note: notes.join("; ")
  });
}

/** Strip the per-day, per-week and per-month arrays: what a follow-up needs is the totals,
 *  bands, latest readings and out-of-band list. */
function compactDatasets(ds) {
  const out = {};
  Object.keys(ds || {}).forEach((k) => {
    const v = Object.assign({}, ds[k] || {});
    if (Array.isArray(v.types)) v.types = v.types.map((t) => { const x = Object.assign({}, t); delete x.days; return x; });
    if (v.fuels && typeof v.fuels === "object") {
      const f = {};
      Object.keys(v.fuels).forEach((n) => { f[n] = Object.assign({}, v.fuels[n]); delete f[n].weeks; });
      v.fuels = f;
    }
    delete v.series; delete v.months;
    out[k] = v;
  });
  return out;
}

export const CONTEXT_CAP = 6000;

/** Last year's figures removed wherever a dataset says they are not a comparison — the same
 *  rule svc-deepagents' summariser applies. The chat model had no such rule: asked "has
 *  consumption gone up?" it was handed 8,383 kWh of one day against 1.5 GWh of fifty-six. */
function withheld(ds) {
  const out = JSON.parse(JSON.stringify(ds || {}));
  const strip = (b) => {
    if (!b || b.comparable !== false) return;
    delete b.last_year_kwh; delete b.change_pct;
    (b.weeks || []).forEach((w) => { delete w.last_year_kwh; });
    if (b.cost_gbp && typeof b.cost_gbp === "object") delete b.cost_gbp.last_year;
    b.last_year_withheld = "not a comparison — " + (b.last_year_days != null ? b.last_year_days + " of " + b.now_days + " days of last year's window are on record" : "last year is not on record");
  };
  const bill = out.utility_bill;
  if (bill) { strip(bill.total); Object.keys(bill.fuels || {}).forEach((k) => strip(bill.fuels[k])); }
  const wx = out.weather;
  if (wx && wx.total && wx.total.comparable === false) {
    ["last_year_hdd", "last_year_cdd", "hdd_change_pct", "cdd_change_pct"].forEach((k) => { delete wx.total[k]; });
    (wx.months || []).forEach((m) => { delete m.last_year_hdd; delete m.last_year_cdd; });
    wx.total.last_year_withheld = "not a comparison — last year's dates are not all on record";
  }
  return out;
}

/** The block chatContext() adds while an investigation is open, so a follow-up typed in the
 *  dock is answered from the same three datasets. Capped, and it says when it was cut. */
export function investigationContext(inv) {
  if (!inv || !inv.live || !inv.datasets) return "";
  const ds = withheld(inv.datasets);
  let json = JSON.stringify(ds);
  let note = "";
  if (json.length > CONTEXT_CAP) {
    json = JSON.stringify(compactDatasets(ds));
    note = " (per-day and per-week detail removed to fit)";
    if (json.length > CONTEXT_CAP) { json = json.slice(0, CONTEXT_CAP); note = " (cut at " + CONTEXT_CAP.toLocaleString("en-GB") + " characters — say so if an answer needs what was cut)"; }
  }
  return "An investigation of " + (inv.title || "this asset") + " is open in the dock. Its three datasets, as svc-operations-intelligence returned them"
    + " (bms-trend, utility-bill, degree-days)" + note + ": " + json + ". Answer questions about the BMS readings, the metered consumption"
    + " or the weather from these. A dataset with status not_found has nothing on record and one with status unreadable could not be read —"
    + " say so rather than guessing. utility_bill is metered consumption standing in for the bill, not a bill."
    + " Where comparable is false there is no comparison with last year — say so and why, and never describe a rise or fall.";
}
