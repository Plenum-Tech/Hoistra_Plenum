// queueDraft — what a Decision-queue card does when it is clicked.
//
// The page the card is about opens, and the dock opens beside it holding the email that
// resolves it, written and ready for Approve & send (Hussain, 7 Oct 2026: "the instinct is to
// solve that immediately", and a drawer that only said where the record lived lost the
// solution). Every card does this — compliance, maintenance, energy, vendors and the sample
// cards — "compliance was only an example".
//
// Where the compliance engine wrote the email to the vendor itself (its vendor email items), that
// draft is the one put up, and its send is recorded on the queue item. Its `to` is not used: the
// engine addresses vendors to their first contact by id, and its building notices to a configured
// PM default (review, 7 Oct 2026). Everything else is written here from the record the card stands
// for, and only from it. Approve & send emails for real, so the To line is only ever read from the
// record of whoever is written to — never a guess. A card that nothing outside Hoistra resolves
// gets an unaddressed internal note: no vendor is looked up, and no engine sentence is mailed out.
//
// Pure. The controller methods that open the page and the dock are in queueLive.js.
import { humanise } from './homeLive.js';
import { fmtDay } from './maintenanceLive.js';
import { vendorKey } from './vendorKey.js';

const num = (v) => (typeof v === "number" && isFinite(v) ? v : null);
const str = (v) => (v === null || v === undefined ? "" : String(v).trim());
// "—" is how the shaped rows say "not on the record"; it is never a name.
const named = (v) => { const s = str(v); return s && s !== "—" ? s : null; };
const sentence = (s) => str(s).replace(/[.\s]+$/, "");
const plural = (n, word) => n + " " + word + (n === 1 ? "" : "s");
const money = (v, currency) => {
  const n = Math.round(Math.abs(v)).toLocaleString("en-GB");
  return currency && currency !== "GBP" ? currency + " " + n : "£" + n;
};
const pence = (v) => "£" + Math.abs(v).toLocaleString("en-GB", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

const PAGE_NAME = { cc: "Compliance", vp: "Vendors", energy: "Energy", assets: "Assets", ops: "Maintenance" };
const CC_CLOSED = { ccQueue: null, ccQueueOpenId: null };
const usable = (d) => (d && typeof d === "object" && str(d.subject) && str(d.body) ? d : null);

const hello = (name) => (name ? "Hello " + name + " team," : "Hello,");
const signoff = (sender) => "Regards,\n" + (sender ? sender + " · Hoistra" : "Hoistra");
export const letter = (name, paragraphs, sender) =>
  [hello(name)].concat(paragraphs.filter(Boolean)).concat([signoff(sender)]).join("\n\n");

/** Where the To line came from, or why there is none — the dock's Worker line says it. */
export function addressLine(found) {
  const f = found || {};
  const who = f.vendor || null;
  if (f.email) return "Addressed to " + f.email + " — " + (who || "the vendor") + "'s contact on record.";
  if ((f.candidates || []).length) {
    return "Several contacts on record for " + (who || "the vendor")
      + " and none marked primary, so the address is left for you: " + f.candidates.join(", ") + ".";
  }
  if (f.noVendor) return "No vendor on the asset record — add the recipient below.";
  if (f.notFound) return (who ? who + " is" : "The vendor is") + " not on a record you can see — add the address below.";
  if (f.failed) return (who ? who + "'s" : "The vendor's") + " record could not be read just now — add the address below.";
  return "No address on " + (who ? who + "'s" : "the vendor's") + " record — add one below.";
}

// ── the compliance register ──────────────────────────────────────────────────────────────────

const certIdOf = (it) => str((it.payload || {}).certificate_id)
  || (it.related_entity_type === "compliance_certificate" ? str(it.related_entity_id) : "");
const vendorIdOf = (it) => (it.related_entity_type === "vendor" ? str(it.related_entity_id) : "")
  || str((it.payload || {}).vendor_id);
const sameName = (a, b) => !!a && !!b && a.toLowerCase() === b.toLowerCase();

// The vendor's certificate a lapse is about: the lapsed one first, else the soonest to expire.
function vendorCert(certs, vendorId, vendorName) {
  const mine = (certs || []).filter((c) => c.kind === "vendor"
    && ((vendorId && str(c.vendorId) === vendorId) || sameName(named(c.holder), vendorName)));
  return mine.slice().sort((a, b) => (a.days - b.days))[0] || null;
}

function expiryPhrase(c) {
  if (!isFinite(c.days) || !named(c.exp)) return "has no expiry date on record";
  if (c.days < 0) return "lapsed on " + c.exp + " (" + plural(-c.days, "day") + " ago)";
  if (c.days === 0) return "expires today, " + c.exp;
  return "expires on " + c.exp + " (in " + plural(c.days, "day") + ")";
}

const certFields = (c) => ["Certificate: " + c.nm, c.number ? "Reference: " + c.number : null,
  "Expiry: " + (named(c.exp) || "not on record")].filter(Boolean).join("\n");

// Where a certificate's card opens. A building's certificate opens the building in Compliance;
// a vendor's accreditation opens that vendor's Accreditations tab on the Vendors page, which is
// where vendors live now (7 Oct 2026). `vendors` is the directory {id: name} the queue reads.
function certWhere(c, vendors) {
  if (!c) return { page: "cc", focus: Object.assign({}, CC_CLOSED), focusName: null };
  if (c.kind === "vendor") {
    const dir = vendors || {};
    const key = vendorKey(c.holder);
    const id = (c.vendorId && dir[c.vendorId] ? c.vendorId : null)
      || Object.keys(dir).find((k) => vendorKey(dir[k]) === key) || null;
    return { page: "vp", focus: id ? { vpVendor: id, vpTab: 5 } : {}, focusName: c.holder };
  }
  return { page: "cc", focus: Object.assign({ ccPivot: "buildings", ccFocus: { kind: "building", name: c.holder }, ccTab: 0 }, CC_CLOSED), focusName: c.holder };
}

// A renewal request to the vendor whose accreditation it is, or an inspection booking to the
// contractor on a building's certificate.
function certDraft(c, sender, woLine) {
  if (c.kind === "vendor") {
    const lapsed = isFinite(c.days) && c.days < 0;
    return {
      emKind: "renewal",
      emKicker: lapsed ? "Accreditation lapsed · draft" : "Accreditation renewal · draft",
      emSubject: (lapsed ? "Accreditation lapsed — " : "Accreditation renewal — ") + c.nm + " — " + c.holder,
      emBody: letter(c.holder, [
        "Our compliance register shows your " + c.nm + " " + expiryPhrase(c) + "."
          + (lapsed ? " Until a current certificate is on record, regulated work of this type cannot be assigned to you." : "")
          + (woLine ? " " + woLine : ""),
        certFields(c),
        "Please reply with the renewed certificate attached as a PDF."
      ], sender),
      vendor: c.holder, vendorId: str(c.vendorId) || null,
      sentLabel: "Renewal request"
    };
  }
  const contractor = named(c.vendor);
  return {
    emKind: "renewal",
    emKicker: "Inspection booking · draft",
    emSubject: "Inspection booking — " + c.nm + " — " + c.holder,
    emBody: letter(contractor, [
      "The " + c.nm + " at " + c.holder + " " + expiryPhrase(c) + ".",
      certFields(c),
      "Please propose an inspection date within the next 10 working days, and send the certificate once it is issued."
    ], sender),
    vendor: contractor, vendorId: str(c.vendorId) || null,
    sentLabel: "Booking request"
  };
}

// The compliance engine's own email to the vendor: this item's when it is a vendor email, else a
// pending vendor email about the same certificate — or, when the item names no certificate, about
// the same vendor. Only vendor emails: the engine's other drafts are notices to the PM.
function engineEmail(approvals, want) {
  const own = want.item;
  if (own && own.item_type === "vendor_email" && usable(own.email_draft)) return { item: own, sibling: false };
  const pool = (approvals || []).filter((x) => x && (!own || x.id !== own.id) && x.source_feature === "A"
    && x.item_type === "vendor_email" && usable(x.email_draft));
  const sib = want.certId ? pool.find((x) => certIdOf(x) === want.certId)
    : want.vendorId ? pool.find((x) => vendorIdOf(x) === want.vendorId) : null;
  return sib ? { item: sib, sibling: true, by: want.certId ? "certificate" : "vendor" } : null;
}

function fromEngine(found) {
  const d = found.item.email_draft;
  return {
    emKind: "renewal",
    emKicker: "Compliance engine · draft",
    emTo: "", emSubject: str(d.subject), emBody: String(d.body),
    emQueueItemId: found.item.id || null,
    emSentLabel: "Renewal request",
    emSentNote: "It is recorded in the sent-email log and on the queue item."
  };
}

const enginePlanner = (found) => (found.sibling
  ? "The compliance engine's email about the same " + found.by + ", from its vendor email item"
  : "The compliance engine wrote this email when it raised the item"
    + (fmtDay(found.item.created_at) ? " (" + fmtDay(found.item.created_at) + ")" : ""))
  + "; the address is read from the vendor's own record, not taken from the engine's draft.";

// ── assembling a plan ────────────────────────────────────────────────────────────────────────

function plan(o) {
  const draft = Object.assign({ emKind: "queue", emKicker: "Draft", emTo: "", emSubject: "", emBody: "",
    emQueueItemId: null, emCertId: null, emSample: false, emSentLabel: "Email",
    emSentNote: "It is recorded in the sent-email log.", fSubject: o.ctx, fVendor: o.vendor || "" }, o.draft);
  const lookup = o.lookup || null;
  const workerLead = PAGE_NAME[o.page] + " opened" + (o.focusName ? " on " + o.focusName : "") + ".";
  const address = lookup
    ? "Looking up " + (lookup.vendor ? lookup.vendor + "'s" : lookup.assetId ? "the asset's vendor and its" : "the vendor's")
      + " contact on record…"
    : o.noAddress || "No address on record — add the recipient below.";
  return {
    page: o.page, focus: o.focus || {},
    task: o.task, ctx: o.ctx,
    draft: draft,
    lookup: lookup,
    workerLead: workerLead,
    steps: [
      { a: "Orchestrator", t: "Intent: " + o.task.toLowerCase() + " · scope: " + o.ctx },
      { a: "Planner", t: o.planner },
      { a: "Worker", t: workerLead + " " + address },
      { a: "Quality", t: "Nothing is sent until you press Approve & send"
        + (draft.emQueueItemId ? "; the send is recorded on this queue item." : ".") }
    ]
  };
}

// The draft fields a plan takes from a certDraft() result.
const draftFields = (d) => ({
  emKind: d.emKind, emKicker: d.emKicker, emSubject: d.emSubject, emBody: d.emBody,
  emSentLabel: d.sentLabel || "Email"
});

const ENGINE_NAME = { A: "compliance engine", B: "vendor engine", C: "energy engine", M: "maintenance watch" };

// A card nothing outside Hoistra resolves — a confirmation, a review, a block lifted, the engines'
// own analysis. The page it is decided on opens, and the dock holds a note for a colleague: no
// vendor is looked up, and the engine's sentence goes to nobody outside.
function internalPlan(it, c, where) {
  const summary = str(it.summary) || humanise(it.item_type);
  const page = PAGE_NAME[where.page];
  return plan({
    page: where.page, focus: where.focus || {}, focusName: where.focusName || null, ctx: summary,
    task: "Internal note",
    planner: "Nothing outside Hoistra resolves this " + humanise(it.item_type).toLowerCase()
      + " — it is decided on the " + page + " page.",
    draft: { emKind: "queue", emKicker: "Internal note · draft", emSubject: summary,
      emBody: letter(null, [sentence(summary) + ".",
        "Raised by the " + (ENGINE_NAME[it.source_feature] || "platform")
          + (fmtDay(it.created_at) ? " on " + fmtDay(it.created_at) : "")
          + ". It needs a decision in Hoistra, on the " + page + " page."], c.sender),
      emSentLabel: "Note", emSentNote: "It is recorded in the sent-email log. Nothing in Hoistra is changed by it." },
    lookup: null,
    noAddress: "Nothing in this item is for a vendor, so the note is unaddressed — send it to whoever should decide it, or decide it on the page."
  });
}

// ── compliance items ─────────────────────────────────────────────────────────────────────────

// The compliance items someone outside Hoistra resolves: a vendor's accreditation (the vendor
// renews it), a building certificate (its contractor inspects), a certificate with defects (its
// contractor puts them right). Everything else is decided in Hoistra.
const VENDOR_RENEWAL = { vendor_email: true, block_ack: true, vendor_risk: true };
const CERT_BOOKING = { alert: true, booking_request: true };

function remedialDraft(cert, p, sender) {
  const to = cert.kind === "vendor" ? cert.holder : named(cert.vendor);
  const defects = str(p.defects_found), actions = str(p.remedial_actions);
  return {
    emKind: "renewal", emKicker: "Remedial works · draft",
    emSubject: "Remedial works — " + cert.nm + " — " + cert.holder,
    emBody: letter(to, [
      "The " + cert.nm + " at " + cert.holder + " came back with defects" + (defects ? ": " + sentence(defects) : "") + "."
        + (actions ? " The remedial action on record is: " + sentence(actions) + "." : ""),
      certFields(cert),
      "Please quote for the remedial works with your earliest date, and send the completion evidence once they are done."
    ], sender),
    vendor: to, vendorId: str(cert.vendorId) || null, sentLabel: "Remedial request"
  };
}

function compliancePlan(it, c) {
  const certs = c.certs || [];
  const type = it.item_type;
  const cid = certIdOf(it), vid = vendorIdOf(it);
  const cert = (cid && certs.find((x) => str(x.id) === cid)) || (vid ? vendorCert(certs, vid, null) : null);
  const where = certWhere(cert, c.vendors);
  const ctx = cert ? cert.nm + " — " + cert.holder : (str(it.summary) || humanise(type));
  const fromCert = (d, task) => plan(Object.assign({}, where, {
    task: task, ctx: ctx, vendor: d.vendor || "",
    planner: "Drafted from the compliance register: " + cert.nm + " — " + cert.holder + ", " + expiryPhrase(cert) + ".",
    draft: draftFields(d),
    lookup: d.vendorId ? { vendorId: d.vendorId, vendor: d.vendor } : null,
    noAddress: d.vendor ? null : "No contractor on the certificate — add the recipient below."
  }));
  if (VENDOR_RENEWAL[type]) {
    const found = engineEmail(c.approvals, { item: it, certId: cid, vendorId: cid ? "" : vid });
    if (found) {
      const evid = vendorIdOf(found.item) || vid;
      const vendor = cert && cert.kind === "vendor" ? cert.holder : null;
      return plan(Object.assign({}, where, { task: "Renewal request", ctx: ctx, vendor: vendor || "",
        planner: enginePlanner(found), draft: fromEngine(found),
        lookup: evid ? { vendorId: evid, vendor: vendor } : null }));
    }
    if (cert && cert.kind === "vendor") return fromCert(certDraft(cert, c.sender), "Renewal request");
    return internalPlan(it, c, where);
  }
  if (CERT_BOOKING[type] && cert) {
    return fromCert(certDraft(cert, c.sender), cert.kind === "vendor" ? "Renewal request" : "Inspection booking");
  }
  if (type === "remedial" && cert) return fromCert(remedialDraft(cert, it.payload || {}, c.sender), "Remedial request");
  return internalPlan(it, c, where);
}

// ── maintenance decisions ────────────────────────────────────────────────────────────────────

// "accreditation: Pennard Fire Services's BAFE SP203-1 has lapsed" / "… Services' NICEIC …" —
// the work order's own words naming whose accreditation stopped it, and which.
const LAPSE = /accreditation:\s*(.+?)(?:['’]s|['’])\s+(.+?)\s+has lapsed/i;

function lapseOf(d, certs) {
  const text = str(d.detail) + " " + str(d.trigger);
  const m = LAPSE.exec(text);
  const said = m ? { vendor: m[1].trim(), accreditation: m[2].trim() } : null;
  // A lapse explains a hold — not an overdue job or one awaiting approval — so the register is
  // only consulted for a Blocked order, or one whose own words name the lapse.
  if (!said && d.state !== "Blocked") return null;
  const vendor = said ? said.vendor : named(d.vendor);
  const sameVendor = !said || sameName(said.vendor, named(d.vendor));
  const vid = sameVendor ? str(d.vendorId) : "";
  // The register's own record of a lapsed accreditation is the stronger evidence; the work
  // order's words stand in only when the register does not hold the certificate.
  const cert = vendorCert(certs, vid, vendor);
  if (cert && isFinite(cert.days) && cert.days < 0) {
    return { vendor: cert.holder, vendorId: str(cert.vendorId) || vid, cert: cert, said: !!said };
  }
  if (said) return { vendor: said.vendor, vendorId: vid, accreditation: said.accreditation, cert: null, said: true };
  return null;
}

const woWhat = (d) => d.asset + (named(d.b) ? " at " + d.b : "");
const woTag = (d) => (d.id ? d.id + " · " : "") + d.asset + (named(d.b) ? ", " + d.b : "");
const woRef = (d) => (d.id ? "Work order " + d.id : "The work order");

function decisionPlan(d, c) {
  const lapse = lapseOf(d, c.certs || []);
  if (lapse) return lapsePlan(d, lapse, c);
  const vendor = named(d.vendor);
  const vid = str(d.vendorId);
  const detail = named(d.detail) ? sentence(d.detail) : "";
  const statutory = d.statutory && d.statutoryNote ? "This is statutory: " + sentence(d.statutoryNote) + "." : null;
  let task, subject, first, ask, label, note;
  if (d.state === "Deviation") {
    task = "Chase overdue work order"; subject = "Overdue — " + woTag(d);
    first = woRef(d) + " for " + woWhat(d) + (d.due ? " was due " + d.due + " and is still open." : " is past its due date and still open.");
    ask = "Please confirm when it will be completed, and send the completion report once it is.";
    label = "Chaser"; note = "It is recorded in the sent-email log. The work order itself is unchanged in Hoistra.";
  } else if (d.state === "Awaiting approval") {
    // The approval is the PM's to give in Hoistra; the email only gets what that decision needs.
    task = "Confirm estimate and date"; subject = "Estimate and date — " + woTag(d);
    first = woRef(d) + " for " + woWhat(d) + " is awaiting our approval"
      + (d.estimated !== null && d.estimated !== undefined ? " at the estimate of " + d.est : "") + ".";
    ask = "Before we approve it, please confirm the estimate still stands and give your earliest attendance date.";
    label = "Estimate check"; note = "It is recorded in the sent-email log. The work order is still awaiting approval in Hoistra.";
  } else if (d.state === "To raise") {
    task = "Request a work order"; subject = "Work order request — " + d.asset + (named(d.b) ? " · " + d.b : "");
    first = "Please raise a work order on " + woWhat(d) + "." + (detail ? " " + detail + "." : "");
    ask = "Please confirm your attendance date.";
    label = "Work order request"; note = "It is recorded in the sent-email log. No work-order record was created in Hoistra.";
  } else {
    task = "Unblock work order"; subject = "On hold — " + woTag(d);
    first = woRef(d) + " for " + woWhat(d) + " is on hold" + (detail ? ": " + detail : "") + ".";
    ask = "Please tell us what is needed for it to go ahead — or that you cannot attend, so it can be reassigned.";
    label = "Hold query"; note = "It is recorded in the sent-email log. The work order itself is unchanged in Hoistra.";
  }
  return plan({
    page: "ops", focus: {}, task: task, ctx: woTag(d), vendor: vendor || "",
    planner: "Drafted from the decision: " + str(d.state) + (d.id ? " · " + d.id : "") + (detail ? " · " + detail : "") + ".",
    draft: { emKind: "queue", emKicker: label + " · draft", emSubject: subject,
      emBody: letter(vendor, [first, statutory, ask], c.sender), emSentLabel: label, emSentNote: note },
    lookup: vid ? { vendorId: vid, vendor: vendor } : null,
    noAddress: vendor ? null : "No vendor on the work order — add the recipient below."
  });
}

function lapsePlan(d, lapse, c) {
  // Causal only when the order's own words say the lapse is what holds it.
  const woLine = d.id
    ? "Work order " + d.id + " (" + d.asset + (named(d.b) ? ", " + d.b : "") + ")"
      + (lapse.said ? " is on hold until it is." : " is currently on hold.")
    : null;
  const ctx = (lapse.cert ? lapse.cert.nm : lapse.accreditation) + " — " + lapse.vendor;
  const focusCert = lapse.cert || vendorCert(c.certs || [], lapse.vendorId, lapse.vendor);
  const base = Object.assign(certWhere(focusCert, c.vendors), { ctx: ctx, vendor: lapse.vendor });
  const lookup = lapse.vendorId ? { vendorId: lapse.vendorId, vendor: lapse.vendor } : null;
  const found = lapse.cert || lapse.vendorId
    ? engineEmail(c.approvals, lapse.cert ? { certId: str(lapse.cert.id) } : { vendorId: lapse.vendorId })
    : null;
  if (found) {
    const evid = lapse.vendorId || vendorIdOf(found.item);
    return plan(Object.assign(base, { task: "Renewal request",
      planner: "The compliance engine's renewal email for " + lapse.vendor + ", from its vendor email item — "
        + (d.id || d.asset) + " is held with this vendor; the address is read from the vendor's own record.",
      draft: fromEngine(found), lookup: evid ? { vendorId: evid, vendor: lapse.vendor } : null }));
  }
  if (lapse.cert) {
    const cd = certDraft(lapse.cert, c.sender, woLine);
    return plan(Object.assign(base, { task: "Renewal request",
      planner: "Drafted from the compliance register: " + lapse.cert.nm + " — " + lapse.vendor + ", "
        + expiryPhrase(lapse.cert) + ". " + (d.id || d.asset) + " is held with this vendor.",
      draft: Object.assign(draftFields(cd), {
        emSentNote: "It is recorded in the sent-email log. The work order is unchanged in Hoistra." }),
      lookup: lookup }));
  }
  return plan(Object.assign(base, { task: "Renewal request",
    planner: "The compliance register holds no certificate for " + lapse.vendor + "; drafted from the work order's own words.",
    draft: { emKind: "renewal", emKicker: "Accreditation lapsed · draft",
      emSubject: "Accreditation lapsed — " + lapse.accreditation + " — " + lapse.vendor,
      emBody: letter(lapse.vendor, [
        woRef(d) + " for " + woWhat(d) + " is on hold: our records show your " + lapse.accreditation + " has lapsed.",
        "Please reply with the renewed certificate attached as a PDF. Until it is on record, work of this type cannot be allocated to you."
      ], c.sender),
      emSentLabel: "Renewal request",
      emSentNote: "It is recorded in the sent-email log. The work order stays on hold until a current certificate is on record." },
    lookup: lookup }));
}

// ── the maintenance watch (source M) ─────────────────────────────────────────────────────────

// The watch names the vendor on the job but carries no vendor id, so no address can be read for
// it: the draft is addressed by name and the To line is left to the reader.
function watchPlan(it, c) {
  const p = it.payload || {};
  const vendor = named(p.vendor);
  const noAddress = vendor ? "The item names " + vendor + " but not its record, so no address can be read — add it below."
    : "No vendor on the item — add the recipient below.";
  if (it.item_type === "wo_sla_breach") {
    const wo = str(p.wo_code), asset = named(p.asset), late = num(p.hours_late);
    const tag = (wo || "work order") + (asset ? " · " + asset : "");
    return plan({
      page: "ops", focus: {}, ctx: tag, task: "Chase overdue work order", vendor: vendor || "",
      planner: "Drafted from the maintenance watch: " + tag + (late !== null ? ", " + late + " hours past its SLA" : "") + ".",
      draft: { emKind: "queue", emKicker: "Chaser · draft", emSubject: "Past SLA — " + tag,
        emBody: letter(vendor, [(wo ? "Work order " + wo : "The work order") + (asset ? " for " + asset : "")
          + (late !== null ? " is " + late + " hours past its SLA." : " is past its SLA."),
          "Please confirm when it will be completed, and send the completion report once it is."], c.sender),
        emSentLabel: "Chaser", emSentNote: "It is recorded in the sent-email log. The work order itself is unchanged in Hoistra." },
      lookup: null, noAddress: noAddress
    });
  }
  if (it.item_type === "ppm_overdue") {
    const sm = str(p.sm_code), asset = named(p.asset_code), due = fmtDay(p.next_due_date);
    const tag = (sm || "planned maintenance") + (asset ? " · " + asset : "");
    return plan({
      page: "ops", focus: {}, ctx: tag, task: "Request a PPM work order", vendor: vendor || "",
      planner: "Drafted from the maintenance watch: " + tag + (due ? ", due " + due : "") + ", no work order open.",
      draft: { emKind: "queue", emKicker: "PPM request · draft", emSubject: "PPM work order request — " + tag,
        emBody: letter(vendor, ["The planned maintenance " + (sm ? sm + " " : "") + (asset ? "on " + asset + " " : "")
          + (due ? "was due " + due : "is overdue") + " and has no work order open.",
          "Please raise it and confirm your attendance date."], c.sender),
        emSentLabel: "PPM request", emSentNote: "It is recorded in the sent-email log. No work-order record was created in Hoistra." },
      lookup: null, noAddress: noAddress
    });
  }
  return internalPlan(it, c, { page: "ops" });
}

// ── energy ───────────────────────────────────────────────────────────────────────────────────

function anomalyPlan(a, c) {
  const type = humanise(a.anomaly_type || "anomaly");
  const subjectOf = named(a.asset_code) || (named(a.meter_ref) ? "meter " + a.meter_ref : "the building supply");
  const where = subjectOf + (named(a.building_name) ? " at " + a.building_name : "");
  const pct = num(a.metric_pct), cost = num(a.financial_gbp), kwh = num(a.annualised_excess_kwh);
  const lead = [pct !== null ? Math.round(pct) + "% above its baseline" : null,
    fmtDay(a.detected_at) ? "since " + fmtDay(a.detected_at) : null].filter(Boolean).join(" ");
  const facts = [lead || null, cost !== null ? money(cost, a.currency) + " a year at the meter's tariff" : null].filter(Boolean);
  const onAsset = !!str(a.asset_id);
  const first = "The energy engine has an open " + type.toLowerCase() + " on " + where
    + (facts.length ? ": " + facts.join(", ") : "") + "."
    + (kwh !== null ? " That is " + Math.round(kwh).toLocaleString("en-GB") + " kWh a year above baseline." : "")
    + (a.simulated ? " The window it was detected in contains simulated readings, so the meter data wants checking too." : "");
  const ask = onAsset
    ? "Please inspect " + subjectOf + " and report what you find, with a recommendation. A work order follows if the report supports one."
    : "Please check what on this supply is running out of pattern and report what you find, with a recommendation.";
  return plan({
    page: "energy", focus: {}, ctx: type + " — " + where,
    task: onAsset ? "Request inspection" : "Ask about an anomaly",
    planner: "Drafted from the anomaly: " + type.toLowerCase() + " on " + where + (lead ? ", " + lead : "") + ".",
    draft: { emKind: "inspect", emKicker: onAsset ? "Inspection request · draft" : "Energy anomaly · draft",
      emSubject: (onAsset ? "Inspection request — " : "Energy anomaly — ") + type + " — " + where,
      emBody: letter(null, [first, ask], c.sender),
      emSentLabel: onAsset ? "Inspection request" : "Anomaly note",
      emSentNote: "It is recorded in the sent-email log. No inspection or work-order record was created in Hoistra." },
    lookup: onAsset ? { assetId: str(a.asset_id) } : null,
    noAddress: "No asset on the anomaly, so no vendor to read — add the recipient below."
  });
}

// ── the vendor engine's items ────────────────────────────────────────────────────────────────

function vendorPlan(it, c) {
  const p = it.payload || {};
  const vid = vendorIdOf(it);
  const vendor = vid ? named((c.vendors || {})[vid]) : null;
  const summary = str(it.summary) || humanise(it.item_type);
  const isInvoice = /^invoice_flag/.test(it.item_type || "");
  const focus = vendor ? { vpVendor: vid, vpTab: isInvoice ? 4 : 0 } : {};
  const lookup = vid ? { vendorId: vid, vendor: vendor } : null;
  if (isInvoice) {
    const line = p.line || {};
    const ref = str(p.invoice_ref);
    const lineId = line.line_id !== undefined && line.line_id !== null ? String(line.line_id) : "";
    const delta = num(line.delta_gbp);
    // Without a reference on the item, the item's own summary names the invoice.
    const invoiceName = ref ? ref + (lineId ? " line " + lineId : "") : summary;
    const first = "We have held " + (lineId ? "line " + lineId + " of " : "a line of ") + (ref ? "invoice " + ref : "the invoice")
      + " for review" + (delta !== null ? ": it differs from what the contract supports by " + pence(delta) : "") + "."
      + (str(line.discrepancy) ? " " + sentence(line.discrepancy) + "." : "");
    return plan({
      page: "vp", focus: focus, focusName: vendor, vendor: vendor || "",
      ctx: invoiceName, task: "Query an invoice",
      planner: "Drafted from the flagged invoice line" + (delta !== null ? " — " + pence(delta) + " against the contract" : "") + ".",
      draft: { emKind: "queue", emKicker: "Invoice query · draft",
        emSubject: "Invoice query — " + invoiceName,
        emBody: letter(vendor, [first, "Please send the evidence behind it — attendance records, approved parts — or a credit note for the difference. The rest of the invoice is not affected."], c.sender),
        emSentLabel: "Invoice query",
        emSentNote: "It is recorded in the sent-email log. The line stays held until it is decided in the queue." },
      lookup: lookup
    });
  }
  if (it.item_type === "fm_report_staleness") {
    const asOf = fmtDay(p.data_as_of) || str(p.data_as_of);
    return plan({
      page: "vp", focus: focus, focusName: vendor, vendor: vendor || "",
      ctx: "FM reports" + (vendor ? " — " + vendor : ""), task: "Ask for FM reports",
      planner: "Drafted from the vendor engine's item: FM reports" + (asOf ? " stop at " + asOf : " are out of date") + ".",
      draft: { emKind: "queue", emKicker: "FM reports · draft", emSubject: "FM reports overdue" + (vendor ? " — " + vendor : ""),
        emBody: letter(vendor, [asOf ? "The FM reports we hold from you stop at " + asOf + "." : "The FM reports we hold from you are out of date.",
          "Please send the FM reports since then, and keep them coming each month."], c.sender),
        emSentLabel: "FM report request" },
      lookup: lookup
    });
  }
  return internalPlan(it, c, { page: "vp", focus: focus, focusName: vendor });
}

// ── the sample cards ─────────────────────────────────────────────────────────────────────────

const SEED = {
  Compliance: { page: "cc", prefix: "Action needed — ", task: "Follow up",
    ask: "Please reply with the current certificate attached as a PDF, or propose a date to inspect." },
  Energy: { page: "energy", prefix: "Inspection request — ", task: "Request inspection",
    ask: "Please inspect and report what you find, with a recommendation." },
  Vendors: { page: "vp", prefix: "Invoice query — ", task: "Query an invoice",
    ask: "Please send the evidence behind the flagged lines, or a credit note for the difference." },
  Assets: { page: "assets", prefix: "Work order request — ", task: "Request a work order",
    ask: "Please raise a work order and confirm your attendance date." },
  Maintenance: { page: "ops", prefix: "Action needed — ", task: "Follow up",
    ask: "Please let us know how you will resolve it." }
};

// A sample card is shown before the live queue answers. Its draft shows the shape of the email;
// it is marked as a sample so Approve & send will not put invented facts in a real inbox.
function seedPlan(card, c) {
  const s = SEED[card.module] || SEED.Maintenance;
  const f = (card.fields || []).find((x) => /^(vendor|proposed contractor|contractor)$/i.test(str(x.l)));
  const vendor = f ? named(String(f.v).split(" · ")[0]) : null;
  const title = str(card.title);
  return plan({
    page: s.page, focus: {}, ctx: title, task: s.task, vendor: vendor || "",
    planner: "Drafted from the sample card shown until the live queue answers.",
    draft: { emKind: "queue", emKicker: "Sample · draft", emSubject: s.prefix + title, emSample: true,
      emBody: letter(vendor, [sentence(title) + ".", s.ask], c.sender), emSentLabel: "Email" },
    lookup: null,
    noAddress: "Sample card — there is no record behind it, so it cannot be sent. The live card opens the real draft."
  });
}

/**
 * The page a Decision-queue card opens and the email the dock holds for it, or null when the
 * card carries nothing to act on (the drawer it always opened takes it).
 *
 *   card  a queue item from shapeLiveQueue (kind approval | anomaly | decision) or a seed card
 *   ctx   { approvals: pending approvals items, certs: the live compliance register's rows,
 *           vendors: { [vendor id]: name } from the Vendors page, sender: company name }
 *
 * Returns { page, focus, task, ctx, draft, lookup, workerLead, steps } — `lookup` names the
 * read that fills the To line ({ vendorId, vendor } or { assetId }), else null.
 */
export function planQueueAction(card, ctx) {
  if (!card || typeof card !== "object") return null;
  const c = ctx || {};
  if (card.kind === "approval") {
    const it = card.item;
    if (!it || typeof it !== "object") return null;
    const sf = it.source_feature || (card.module === "Compliance" ? "A" : card.module === "Maintenance" ? "M" : "");
    if (sf === "A") return compliancePlan(it, c);
    if (sf === "M") return watchPlan(it, c);
    if (sf === "C") return internalPlan(it, c, { page: "energy" });
    return vendorPlan(it, c);
  }
  if (card.kind === "anomaly") return card.item ? anomalyPlan(card.item, c) : null;
  if (card.kind === "decision") return card.d ? decisionPlan(card.d, c) : null;
  if (!card.kind && card.title && card.module) return seedPlan(card, c);
  return null;
}
