// complianceCell — one cell of the Compliance page's requirement matrix, clicked (Aasim, 8 Oct
// 2026). The cell asks the orchestrator what is behind it — "what is the current problem with my
// asbestos certification?" — and the answer is followed by the next steps as buttons: the renewal
// draft, a visit request to the vendor, the email asking a vendor for its accreditation.
//
// Everything here is read off the register the page already holds. The orchestrator explains; the
// buttons are decided here, so a step is offered whether or not the answer mentions it, and never
// for something with nothing to act on — the rule svc-deepagents compliance_offers.py keeps for
// the same reason. Every button opens a draft; nothing is sent from here. Pure, so it is tested
// on its own (test/complianceCell.test.mjs).
import { letter } from './queueDraft.js';

// A building type names its contractor's trade one way and the vendor accreditations name it
// another; these are the pairs the UK, US and UAE packs disagree on. Compared lower-cased.
const TRADE_ALIAS = {
  "water/legionella": ["water"],
  "water hygiene": ["water"],
  "loler": ["lifts"],
  "f-gas": ["hvac/f-gas"],
  "mechanical": ["hvac"],
  "hvac": ["hvac/f-gas"],
  "hvac/f-gas": ["hvac"]
};
// ISO, insurance and safety-management certificates say nothing about who can carry out a
// building's statutory work, so they never stand in for a contractor.
const NOT_A_TRADE = ["general", "insurance", "hse", "h&s"];

const low = (x) => String(x || "").trim().toLowerCase();
const norm = (x) => String(x || "").toUpperCase().replace(/[^A-Z0-9]/g, "");
const has = (o, k) => Object.prototype.hasOwnProperty.call(o, k);

function tradesFor(trade) {
  const t = low(trade);
  if (!t || NOT_A_TRADE.indexOf(t) > -1) return [];
  return [t].concat(TRADE_ALIAS[t] || []);
}

// The pack's required_contractor_accreditation is a phrase ("UKAS", "BAFE SP203-1", "Gas Safe");
// the vendor type it means is the one whose code or name it is, or starts. A phrase the pack has
// no vendor type for ("BAFE SP205") means nothing can be checked, so nothing is asked for.
function requiredType(requires, packTypes) {
  const r = norm(requires);
  if (!r) return null;
  const codes = Object.keys(packTypes).filter((k) => packTypes[k].scope === "vendor");
  const hit = codes.find((k) => norm(k) === r)
    || codes.find((k) => norm(k).indexOf(r) === 0)
    || codes.find((k) => norm(packTypes[k].name).indexOf(r) === 0);
  return hit ? { code: hit, name: packTypes[hit].name || hit } : null;
}

function accStatus(c) {
  if (c.risk === "Blocked") return "Blocked";
  if (!isFinite(c.days)) return "Current";
  if (c.days < 0) return "Lapsed";
  if (c.days <= 90) return "Expiring";
  return "Current";
}

const ago = (d) => -d + (d === -1 ? " day ago" : " days ago");
function expiryPhrase(days, exp) {
  if (!isFinite(days)) return "no expiry on record";
  if (days < 0) return (exp ? "expired " + exp + ", " : "") + "lapsed " + ago(days);
  return "expires " + (exp ? exp + ", " : "") + "in " + days + (days === 1 ? " day" : " days");
}
function accPhrase(a) {
  if (a.status === "Not on record") return a.name + " not on record — the pack requires it for this work";
  if (a.status === "Lapsed") return a.name + " lapsed " + ago(a.days) + (a.exp !== "—" ? " (" + a.exp + ")" : "");
  if (a.status === "Expiring") return a.name + " expiring in " + a.days + " days (" + a.exp + ")";
  if (a.status === "Blocked") return a.name + " — vendor blocked" + (isFinite(a.days) ? ", " + expiryPhrase(a.days, a.exp) : "");
  return a.name + " current" + (a.exp !== "—" ? " until " + a.exp : "");
}

// The cell as a subject: the building's certificates of the type, and the vendors behind them.
// null for a cell that is not a question — "Not required", or a column the register cannot name.
//
// The vendors are the one named on the building's certificate of this type, and those serving the
// building (a building certificate names them — the same link as the Vendors page's "Buildings
// served") that hold an accreditation in the type's trade. When no vendor is linked, the vendors
// holding that trade's accreditations anywhere are listed instead, marked unlinked.
export function cellSubject(CC, building, i) {
  const state = (((CC && CC.mx) || {})[building] || [])[i];
  const type = ((CC && CC.mxTypes) || [])[i];
  if (!state || state === "na" || !type) return null;
  const code = (CC.mxCodes || [])[i] || null;
  const packTypes = CC.packTypes || {};
  const pt = (code && packTypes[code]) || {};
  const all = CC.certs || [];
  const certs = code
    ? all.filter((c) => c.kind === "building" && c.holder === building && c.code === code).sort((a, b) => a.days - b.days)
    : [];
  const trades = tradesFor(pt.trade);
  const tradeOf = (c) => low(c.trade || (packTypes[c.code] || {}).trade);
  const vcerts = trades.length ? all.filter((c) => c.kind === "vendor" && c.holder !== "Unlinked vendor" && trades.indexOf(tradeOf(c)) > -1) : [];
  const required = requiredType(pt.requires, packTypes);
  const recOf = (name) => (CC.vendors || []).find((v) => v.name === name) || null;
  const holdsTrade = (name) => vcerts.some((c) => c.holder === name);

  const named = {};
  certs.forEach((c) => { if (c.vendor) named[c.vendor] = named[c.vendor] || c.vendorId || null; });
  let names = Object.keys(named);
  (CC.vendors || []).forEach((v) => {
    if (names.indexOf(v.name) < 0 && (v.serves || []).indexOf(building) > -1 && holdsTrade(v.name)) names.push(v.name);
  });
  let unlinked = false;
  if (!names.length) {
    names = vcerts.map((c) => c.holder).filter((n, j, a) => a.indexOf(n) === j);
    unlinked = names.length > 0;
  }

  const vendors = names.map((name) => {
    const rec = recOf(name);
    const own = vcerts.filter((c) => c.holder === name);
    const accreditations = own.map((c) => ({ id: c.id, code: c.code, name: c.nm, status: accStatus(c), exp: c.exp || "—", days: c.days }));
    // The contractor who did this building's work, without the accreditation the pack requires
    // for it: that is the gap, even though nothing about it has lapsed.
    const holdsRequired = required && all.some((c) => c.kind === "vendor" && c.holder === name && c.code === required.code);
    if (required && has(named, name) && !holdsRequired) {
      accreditations.push({ id: null, code: required.code, name: required.name, status: "Not on record", exp: "—", days: null });
    }
    return {
      name: name,
      id: (rec && rec.id) || named[name] || (own[0] && own[0].vendorId) || null,
      named: has(named, name),
      blocked: !!(rec && rec.block === "Blocked") || accreditations.some((a) => a.status === "Blocked"),
      accreditations: accreditations
    };
  });

  return {
    building: building, code: code, type: type, state: state,
    label: (CC.mxLabel || {})[state] || state,
    trade: pt.trade || null, required: required, certs: certs, vendors: vendors, unlinked: unlinked
  };
}

export function cellQuestion(s) {
  const at = s.type + " at " + s.building;
  if (s.state === "risk") return "Why is the " + at + " lapsed or blocked, and what should I do next?";
  if (s.state === "warn") return "The " + at + " is expiring soon — what is the situation, and what should I do next?";
  if (s.state === "gap") return "Why is there no " + s.type + " on record for " + s.building + ", and what should I do next?";
  return "What is the status of the " + at + ", and who services it?";
}

// The next steps, as option cards the dock draws (logic/choiceCards.js, kind "draft"). The cell's
// own certificate comes first, then each vendor's accreditation — one button per vendor, so with
// two asbestos vendors and one lapsed, the reader asks only the one they still use.
export function cellActions(s) {
  if (!s) return [];
  const out = [];
  const add = (c) => out.push(Object.assign({ id: c.action.draft + ":" + out.length, n: out.length + 1 }, c));
  const lead = s.certs[0] || null;
  // Only a vendor that may do the work is asked to: not blocked, and nothing it needs lapsed or missing.
  const workers = s.vendors.filter((v) => !v.blocked && !v.accreditations.some((a) => a.status === "Lapsed" || a.status === "Not on record"));
  const standing = (v) => s.unlinked
    ? " Not linked to " + s.building + " on record — it holds " + s.trade + " accreditations for other work."
    : v.named ? " Named on this building's certificate." : " Serves " + s.building + ".";

  if ((s.state === "risk" || s.state === "warn") && lead) {
    const status = lead.days < 0 ? "Lapsed" : "Expiring";
    add({
      icon: "ph-envelope-simple", title: "Draft renewal email", cta: "Draft it",
      detail: s.type + (lead.number ? " no. " + lead.number : "") + " — " + expiryPhrase(lead.days, lead.exp)
        + ". The compliance service drafts it; nothing is sent until you approve it.",
      action: { kind: "draft", draft: "renewal", certId: lead.id, certName: s.type, owner: s.building }
    });
    workers.forEach((v) => add({
      icon: "ph-calendar-check", title: "Ask " + v.name + " to book the renewal visit", cta: "Draft the request",
      detail: "An email asking for their earliest date and the new certificate." + standing(v),
      action: { kind: "draft", draft: "visit", vendor: v.name, vendorId: v.id, type: s.type, building: s.building, status: status, exp: lead.exp || "—" }
    }));
  }
  if (s.state === "gap") {
    add({
      icon: "ph-upload-simple", title: "Upload the " + s.type, cta: "Choose the file",
      detail: "If the certificate exists, file it against " + s.building + " — it is read and checked like any other document.",
      action: { kind: "draft", draft: "upload", type: s.type, building: s.building }
    });
    workers.forEach((v) => add({
      icon: "ph-calendar-check", title: "Ask " + v.name + " to carry out the " + s.type, cta: "Draft the request",
      detail: "An email asking for the certificate if it was done, or their earliest date if not." + standing(v),
      action: { kind: "draft", draft: "visit", vendor: v.name, vendorId: v.id, type: s.type, building: s.building, status: "Not on record", exp: "—" }
    }));
  }
  // A vendor not linked to the building is not chased about its own paperwork on this building's account.
  if (!s.unlinked) {
    s.vendors.forEach((v) => v.accreditations.forEach((a) => {
      const status = a.status === "Blocked"
        ? (a.days < 0 ? "Lapsed" : a.days <= 90 ? "Expiring" : null)
        : (a.status === "Current" ? null : a.status);
      if (!status) return;
      add({
        icon: "ph-envelope-simple", title: "Email " + v.name + " for their " + a.name, cta: "Draft the email",
        detail: accPhrase(a) + ". Skip it if " + v.name + " no longer does this work for you.",
        action: { kind: "draft", draft: "accreditation", vendor: v.name, vendorId: v.id, row: { name: a.name, status: status, exp: a.exp || "—" } }
      });
    }));
  }
  return out;
}

// The line above the next-step cards.
export function cellStepsIntro(s) {
  return "Next steps for the " + s.type + " at " + s.building + ". Each opens a draft for you to check — nothing is sent until you send it. Skip any that do not apply.";
}

// What the orchestrator is told: the facts behind the cell as the page holds them, and the buttons
// that will sit under its answer, so the answer can point at them instead of inventing steps.
export function cellContext(s, actions) {
  const parts = [
    "The question is about one cell of the requirement matrix: " + s.type + (s.code ? " (" + s.code + ")" : "") + " at " + s.building
      + ". The matrix shows it as “" + s.label + "”."
  ];
  if (!s.code) {
    parts.push("The register behind this cell has not loaded — the page is showing seed data — so its certificates and vendors are not known.");
  } else if (s.certs.length) {
    parts.push("Certificates of this type on record for " + s.building + ": " + s.certs.map((c) =>
      [(c.number || "unnumbered") + " — " + expiryPhrase(c.days, c.exp),
        c.vendor ? "serviced by " + c.vendor : "no vendor named",
        c.doc ? "document on file" : "no document on file",
        "register check: " + ((c.verChip && c.verChip.label) || "not checked")].join(", ")).join("; ") + ".");
  } else {
    parts.push("No certificate of this type is on record for " + s.building + ".");
  }
  if (s.code && s.trade && tradesFor(s.trade).length) {
    parts.push("Contractor trade for this type: " + s.trade + (s.required ? "; the pack requires the contractor to hold " + s.required.name : "") + ".");
    const list = s.vendors.map((v) => v.name + " — " + (v.accreditations.length ? v.accreditations.map(accPhrase).join(", ") : "no " + s.trade + " accreditation on record")
      + (v.blocked ? " (blocked)" : "") + (v.named ? " (named on this building's certificate)" : "")).join("; ");
    if (!s.vendors.length) parts.push("No vendor on record holds a " + s.trade + " accreditation.");
    else if (s.unlinked) parts.push("No vendor is linked to " + s.building + " for this trade (a vendor is linked when a certificate at the building names it). Vendors holding " + s.trade + " accreditations elsewhere: " + list + ".");
    else parts.push("Vendors for this trade at " + s.building + ": " + list + ".");
  } else if (s.code) {
    parts.push("The compliance pack names no contractor trade for this type.");
  }
  parts.push((actions || []).length
    ? "Under your answer the page shows these next steps as buttons: " + actions.map((a) => "“" + a.title + "”").join("; ")
      + ". Each opens a draft for the user to check and send — nothing has been sent. Explain the cause from these facts, naming the certificate or vendor behind it, and say which of these steps fits and why. Do not offer other actions."
    : "No next step is shown under the answer: nothing behind this cell needs chasing, and nothing has been sent. Explain the status from these facts.");
  return parts.join(" ");
}

// The email asking a building's contractor to do the work: renew a lapsed or expiring certificate,
// or carry out (or send) one the register has never held.
export function visitRequestDraft(vendor, row, sender) {
  const exp = row.exp && row.exp !== "—" ? row.exp : null;
  const at = row.type + " at " + row.building;
  const base = { emKind: "renewal", emSentNote: "It is recorded in the sent-email log." };
  if (row.status === "Lapsed" || row.status === "Expiring") {
    const lapsed = row.status === "Lapsed";
    return Object.assign(base, {
      emKicker: "Renewal visit · draft",
      emSubject: "Renewal visit — " + row.type + " — " + row.building,
      emBody: letter(vendor, [
        "Our compliance register shows the " + at + (lapsed ? (exp ? " lapsed on " + exp : " has lapsed") : (exp ? " expires on " + exp : " is due to expire")) + ". It is a required certificate for the building.",
        lapsed
          ? "Please reply with your earliest date to carry out the renewal, and send the new certificate as a PDF in reply to this email once it is issued."
          : "Please book the renewal visit before that date and reply with the date booked. Send the new certificate as a PDF once it is issued."
      ], sender),
      emSentLabel: "Renewal visit request"
    });
  }
  return Object.assign(base, {
    emKicker: "Visit request · draft",
    emSubject: row.type + " required — " + row.building,
    emBody: letter(vendor, [
      "Our compliance register holds no " + row.type + " for " + row.building + ". It is a required certificate for the building.",
      "If you have already carried it out, please send the certificate as a PDF in reply to this email. If not, please reply with your earliest date to carry it out."
    ], sender),
    emSentLabel: "Visit request"
  });
}

// The reply's own buttons that the cell's buttons already cover: the compliance answer offers a
// renewal draft per certificate it cites and an attach per missing type, and two "renewal"
// buttons for one certificate would read as two different things.
export function withoutCellDuplicates(rich, actions, code) {
  if (!rich) return rich;
  const renewing = {};
  let uploading = false;
  (actions || []).forEach((a) => {
    if (a.action.draft === "renewal") renewing[a.action.certId] = true;
    if (a.action.draft === "upload") uploading = true;
  });
  return Object.assign({}, rich, {
    offers: (rich.offers || []).filter((o) =>
      !(o.kind === "renewal_email" && renewing[o.cert_id])
      && !(o.kind === "attach_certificate" && uploading && (!code || o.certificate_type_code === code)))
  });
}
