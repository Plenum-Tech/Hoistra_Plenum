// chatCases — answering a held document in the chat that held it.
//
// When the validation gate holds an upload, the reply promises something specific:
//
//     "Reply with your reason and I will put it to the check, or confirm to file it as it
//      is — either way the decision is recorded against your name."
//
// On 21 Sep 2026 that promise was not kept. A WKU contract was held against Bishopsgate
// Tower, the reader typed "test upload, file it anyway", and the orchestrator read it as a
// brand-new question. Its router decided — not unreasonably, given the words "upload" and
// "file" — that this was a data-migration request, handed it to the migration sub-agent,
// and answered "the file cannot be found in the system". The case was never touched.
//
// Nothing was missing from the API. api/deepAgents.js already carries listCases, getCase,
// clarifyCase, reassignCase and decideCase, and the Ingestion page drives all of them. The
// chat printed the question and forgot the case existed.
//
// So the routing is taken away from the model. A session holding an open case sends the
// next message to that case, deterministically — routing is exactly what failed, and the
// promise names one behaviour rather than a judgement.
//
// Three rules:
//
//   A MESSAGE WITH FILES IS NEVER AN ANSWER. Attaching a document is starting an ingest,
//   not explaining the last one. The open case steps aside for it.
//
//   ONE CASE AT A TIME. A second held upload replaces the first and says so. Two open
//   questions and one composer is worse than losing the thread.
//
//   THERE IS ALWAYS A WAY OUT. "Not now" drops the case back to the queue, where the
//   Ingestion page can still decide it. Nobody gets trapped answering.
import { deepAgentsApi } from '../api/deepAgents.js';

const CC_CASE_DEFAULTS = {
  // The held case this session is answering, its question, and the document it is about.
  ccCaseId: null, ccCaseDoc: "", ccCaseQuestion: ""
};

// A verdict that is no longer waiting on anybody. The case closes and the composer goes
// back to the orchestrator.
const SETTLED = new Set(["accepted", "rejected", "bound", "filed", "reassigned", "closed", "resolved"]);

// A reply that answers the question rather than adding to it.
//
// The gate's own wording asks for one: "say yes and I will record the override against your
// name", and before this the chat had no way to send one. clarify() never releases a
// document — validation.py returns requires_confirmation on every reply — so a reader who
// said yes was asked the same question forever. decideCase() is the endpoint that answers
// it; this is what decides which of the two a message is.
//
// Matched on the WHOLE normalised message, never a substring: "why should I file it as it
// is?" contains "file it" and is a question, not consent. Anything not listed here is a
// reason, which is the safe direction — clarify only ever asks again, while decide files a
// document and puts a person's name on it.
const CC_YES = new Set([
  "y", "yes", "yes please", "yep", "yeah", "yup", "ok", "okay", "sure",
  "confirm", "confirmed", "confirm it", "approve", "approved", "proceed",
  "go ahead", "do it", "file it", "file it as it is", "file it as is",
  "file as is", "file it anyway", "yes file it", "correct", "agreed"
]);
const CC_NO = new Set([
  "n", "no", "nope", "no thanks", "cancel", "stop", "reject", "rejected",
  "do not file it", "dont file it", "don't file it", "do not file", "dont file"
]);

// Trailing .!,;: are typing, not meaning. A trailing "?" is kept and disqualifies the
// message outright — somebody asking a question has not consented to anything.
const ccNormalise = (text) =>
  String(text || "").toLowerCase().trim().replace(/[.!,;:]+$/g, "").replace(/\s+/g, " ").trim();

export const ccDecisionIn = (text) => {
  const t = ccNormalise(text);
  if (!t || t.endsWith("?")) return null;
  if (CC_YES.has(t)) return true;
  if (CC_NO.has(t)) return false;
  return null;
};

export const chatCaseMethods = {
  // Called with whatever a turn returned. A held upload opens a case; anything else leaves
  // the session as it was.
  ccCaseFromTurn(r) {
    const cases = r && Array.isArray(r.validation_cases) ? r.validation_cases : [];
    // A check that FAILED also rides back here, as {ok:false} with no id — held, but not a
    // case anybody can answer. Those route through the ordinary error path.
    const held = cases.find((x) => x && x.id && x.ok !== false);
    if (!held) return;
    this.setState({
      ccCaseId: String(held.id),
      ccCaseDoc: String(held.document_name || held.document || ""),
      ccCaseQuestion: String(held.question || "")
    });
  },

  ccCaseClear() { this.setState({ ccCaseId: null, ccCaseDoc: "", ccCaseQuestion: "" }); },

  // Ask the server what is still held. Storage alone cannot answer this: a document held in
  // another browser, or before this code existed, is invisible to it. On 21 Sep five cases
  // were open with nothing in the UI pointing at any of them — listCases was wired in
  // api/deepAgents.js and called from nowhere.
  //
  // The session's OWN case always wins. Swapping the document under a reader who is
  // mid-answer is worse than showing nothing, and only they know which one they meant.
  //
  // Silent on every failure. This runs in the background, and a service that cannot be
  // reached is not something to put in a transcript the reader did not ask for.
  async ccCaseSync() {
    if (this.state.ccCaseId) return;
    try {
      const r = await deepAgentsApi.listCases({ openOnly: true });
      const rows = (r && (r.cases || r.rows || r.items)) || [];
      // `blocking` on the server is "not matched and not may_ingest"; mirror it rather than
      // trusting a verdict string alone, so a settled case is never adopted.
      const held = rows.find((x) => x && x.id && x.verdict !== "matched" && !x.may_ingest);
      if (!held) return;
      // Re-check: an answer may have landed between the request and the reply.
      if (this.state.ccCaseId) return;
      this.setState({
        ccCaseId: String(held.id),
        ccCaseDoc: String(held.document_name || held.document || ""),
        ccCaseQuestion: String(held.question || "")
      });
    } catch (e) {
      // Deliberately nothing.
    }
  },

  // Whether this message should answer the open case rather than go to the orchestrator.
  // Files mean an ingest, always: the reader is starting something, not explaining.
  ccCaseShouldAnswer(hasFiles) { return !!this.state.ccCaseId && !hasFiles; },

  // Put the reader's reason to the check. Returns the bot turn to append.
  async ccCaseAnswer(text) {
    const id = this.state.ccCaseId;
    const doc = this.state.ccCaseDoc;
    // A yes or a no answers the question; anything else adds to it. Deciding is what
    // releases the document, so it is only ever reached by an unambiguous reply.
    const approve = ccDecisionIn(text);
    try {
      const r = approve === null
        ? await deepAgentsApi.clarifyCase(id, text)
        // The reader's own words go in the note: the audit row should say what they typed.
        : await deepAgentsApi.decideCase(id, { approve, note: text });
      if (r && r.ok === false) throw new Error(r.error || "the check could not be reached");
      const verdict = String((r && r.verdict) || "").toLowerCase();
      const question = (r && r.question) || "";
      const settled = SETTLED.has(verdict) || !!(r && r.bound);
      if (settled) this.ccCaseClear();
      const bound = r && r.bound ? r.bound : null;
      const parts = [];
      if (settled) {
        parts.push(docLabel(doc) + " is settled — " + (verdict || "decided") + ".");
        if (bound && bound.error) {
          // The route records the decision before it binds, so this is a filing that can be
          // retried — not a decision that has to be made again.
          parts.push("The decision is recorded, but filing it did not complete: "
            + String(bound.error) + ". It can be filed again from the Ingestion page.");
        } else if (bound) {
          const n = Object.entries(bound).filter(([, v]) => v).map(([k, v]) => v + " " + k);
          if (n.length) parts.push("Filed: " + n.join(", ") + ".");
        }
      } else {
        parts.push("Put to the check.");
        if (question) parts.push(question);
        parts.push("Reply again, or drop the case to go back to asking questions.");
      }
      // Whatever the outcome, the question it now asks is the one the composer answers next.
      if (!settled && question) this.setState({ ccCaseQuestion: question });
      return { role: "bot", text: parts.join(" ") };
    } catch (e) {
      // The case stays open: a reason that did not reach the check is not a reason given,
      // and dropping it here would lose the reader's place as well as their sentence.
      return {
        role: "bot", error: true,
        text: "That did not reach the check: " + ((e && e.message) || e)
          + ". The document is still held — try again, or decide it on the Ingestion page."
      };
    }
  }
};

const docLabel = (name) => {
  const s = String(name || "").trim();
  if (!s) return "The document";
  // Uploads carry a uuid prefix; the reader named the file, not the uuid.
  const m = s.match(/^[0-9a-f-]{8,}_(.+)$/i);
  return m ? m[1] : s;
};

export { CC_CASE_DEFAULTS, docLabel };
