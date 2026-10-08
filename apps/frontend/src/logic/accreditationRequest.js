// accreditationRequest — the email a vendor is sent for one accreditation the register is short
// of: never supplied, lapsed, or expiring. Opened from a row of the Vendors page's Coverage tab
// (compliance.js vpRequestAccreditation) into the dock's email card, where the reader checks it
// and presses Send; nothing goes out from here. Pure, so the wording is tested on its own.
import { letter } from './queueDraft.js';

const named = (x) => (x && x !== "—" ? x : null);

// row: a Coverage row — { name, status: "Not on record" | "Lapsed" | "Expiring" | …, exp }.
export function accreditationRequestDraft(vendor, row, buildings, sender) {
  const type = row.name;
  const exp = named(row.exp);
  const where = (buildings || []).length
    ? " for the work you carry out for us at " + listOf(buildings)
    : " for the work you carry out for us";
  let kicker, subject, ask;
  if (row.status === "Lapsed") {
    kicker = "Accreditation lapsed · draft";
    subject = "Accreditation lapsed — " + type + " — " + vendor;
    ask = "Our compliance register shows your " + type + (exp ? " lapsed on " + exp : " has lapsed") + ". It is a required accreditation" + where + ".";
  } else if (row.status === "Expiring") {
    kicker = "Accreditation renewal · draft";
    subject = "Accreditation renewal — " + type + " — " + vendor;
    ask = "Our compliance register shows your " + type + (exp ? " expires on " + exp : " is due to expire") + ". It is a required accreditation" + where + ".";
  } else {
    kicker = "Accreditation request · draft";
    subject = "Accreditation required — " + type + " — " + vendor;
    ask = "Our compliance register holds no " + type + " for you. It is a required accreditation" + where + ".";
  }
  return {
    emKind: "renewal",
    emKicker: kicker,
    emSubject: subject,
    emBody: letter(vendor, [ask,
      row.status === "Expiring"
        ? "Please send the renewed certificate as a PDF in reply to this email once it is issued."
        : "Please send your current certificate as a PDF in reply to this email. Until it is on record, this requirement stays open on our register."], sender),
    emSentLabel: "Accreditation request",
    emSentNote: "It is recorded in the sent-email log."
  };
}

// "A", "A and B", "A, B and 2 more" — a sentence, not a dump of every building.
function listOf(names) {
  const n = names.filter(Boolean);
  if (n.length <= 2) return n.join(" and ");
  if (n.length === 3) return n[0] + ", " + n[1] + " and " + n[2];
  return n[0] + ", " + n[1] + " and " + (n.length - 2) + " more buildings";
}

// Which Coverage rows can be asked for: anything the vendor owes — never supplied, lapsed or
// running out. A current accreditation has nothing to request.
export function canRequest(row) {
  return !!row && (row.status === "Not on record" || row.status === "Lapsed" || row.status === "Expiring");
}
