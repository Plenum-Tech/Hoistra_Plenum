// ingestionLive — the ingestion validation agent's REAL protocol, against
// svc-deepagents (api/deepAgents.js). The modal's conversation is no longer canned:
//
//   Run       POST /api/workflow/run-stateful-with-files  (message + session_id +
//             building_id + the file) → validation_cases[0] is the case
//   Switch    POST /api/ingestion/cases/{id}/reassign     — the check RE-RUNS there
//   Explain   POST /api/ingestion/cases/{id}/clarify      — LLM assessment, files nothing
//   Decide    POST /api/ingestion/cases/{id}/decide       — the yes/no that binds or rejects
//
// The state machine stays in logic/ingestion.js (same phase names, so the AuditTrail and
// Buildings openers keep working); this module owns the async calls and the shapers that
// map a case onto it. The document is either a real file from the picker (s.ingFile) or a
// sample doc regenerated at run time (sampleDocFile) whose labelled claims — "Property:",
// "Contractor:" — reproduce each ING_DOCS scenario against the live checker.
//
// Endings are real: the decided case's `outcome` (snake_case) is the outcome displayed,
// a locally-shaped audit row is prepended so the trail moves immediately, and auLiveLoad()
// (auditLive.js, called defensively) swaps it for the server's own record.
//
// Errors: a 403 (cannot_ingest / building_not_allocated) is the caller's rights — an agent
// bubble with no retry; any other API failure is a bubble with a Retry; a NETWORK-level
// failure (no status) before a case exists falls back to the canned walkthrough, clearly
// prefixed "(offline demo)". Mid-conversation the case is REAL and held server-side, so a
// network blip there offers a retry rather than fabricating a canned ending.
//
// The shapers are pure named exports; the methods below are mixed into
// HoistraLogic.prototype and `this` is the controller.
import { deepAgentsApi } from '../api/deepAgents.js';
import { AX_BUILDINGS, AX_CHECKS, ING_DOCS } from '../data/hoistra-access.js';
import { checksSummary, OUTCOME_LABEL } from './auditLive.js';

// The pill loop cadence while a request is in flight — same beat the canned run had.
const SPIN_MS = 520;

// Uploaded documents are stored as "<uuid>_<original name>"; the prefix is not for people.
const docLabel = (n) => String(n || "").replace(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}_/i, "");

// The engine's check names → the six UI pills (AX_CHECKS). Anything the engine adds later
// lands on the last pill (graph relationships) rather than being dropped.
export const CHECK_SLOT = {
  building_name: 0, building_code: 0,
  country: 1, region: 1, state: 1, postcode: 1,
  vendor: 2,
  assets: 3,
  certificate_number: 4, contract_ref: 4,
  building_evidence: 5
};

// findings[] → one descriptor per pill: `warn` when a conflicting finding landed on it.
// An `unknown` direction is deliberate and never warns — "no vendors on record to check
// against" is not "the vendor is wrong".
export function checksFromFindings(findings) {
  const slots = AX_CHECKS.map(() => ({ warn: false, seen: false }));
  (Array.isArray(findings) ? findings : []).forEach((f) => {
    if (!f || !f.check) return;
    const i = CHECK_SLOT[f.check] !== undefined ? CHECK_SLOT[f.check] : slots.length - 1;
    slots[i].seen = true;
    if (f.direction === "conflicts") slots[i].warn = true;
  });
  return slots;
}

// A case → the modal's phase machine. verdict matched→"valid", mismatch→"mismatch",
// uncertain→"uncertain"; the agent bubbles come from the case's own words (question or
// message, plus the conflicting findings' lines on a mismatch), and the suggestion from
// the validate response's `suggestion` candidate or the persisted suggested_building_id
// (candidates[] only resolves its name; a bare id's name is resolved by the controller).
export function mapCaseToPhase(c) {
  if (!c) return { phase: "uncertain", bubbles: [], suggestedId: null, suggestedName: "" };
  const phase = c.verdict === "matched" ? "valid" : c.verdict === "mismatch" ? "mismatch" : "uncertain";
  // Only what the engine actually proposed is a suggestion — `suggestion` (the 2.0-margin
  // gate) or the persisted suggested_building_id — never a lower-scoring candidates[]
  // entry it deliberately did not offer. And a reassign leaves the stale
  // suggested_building_id pointing at the building the case now sits on; offering to
  // "switch" there is no switch, so that reads as no suggestion at all.
  let suggestedId = (c.suggestion && c.suggestion.building_id) || c.suggested_building_id || null;
  if (suggestedId && String(suggestedId) === String(c.selected_building_id)) suggestedId = null;
  const cand = (suggestedId && (c.suggestion || (c.candidates || []).find((x) => x && String(x.building_id) === String(suggestedId)))) || null;
  const bubbles = [];
  const q = c.question || c.message || "";
  if (q) bubbles.push(q);
  if (phase === "mismatch") {
    // A finding the question already quotes verbatim is not repeated as a second bubble.
    const lines = (c.findings || []).filter((f) => f && f.direction === "conflicts" && f.message && q.indexOf(f.message) === -1).map((f) => f.message);
    if (lines.length) bubbles.push(lines.join(" "));
  }
  if (!bubbles.length) bubbles.push(
    phase === "valid" ? "Everything lines up — the document validates against the selected building and is clear to ingest."
      : phase === "mismatch" ? "Something this document says contradicts the selected building."
      : "I cannot confidently validate this document against the selected building: nothing contradicts it, but nothing confirms it either. Explicit approval is required before ingestion."
  );
  return { phase, bubbles, suggestedId, suggestedName: (cand && cand.name) || "" };
}

// "Vendor invoice — Apex Lifts · INV-30412.pdf" → "Apex Lifts". The vendor is the one
// claim the sample file names in its labelled text AND its file name.
export function vendorOf(fileName) {
  const parts = String(fileName || "").split("—");
  if (parts.length < 2) return "";
  return parts[parts.length - 1].replace(/\.[a-z0-9]+$/i, "").split("·")[0].trim();
}

// The checker reads labelled lines ("Site:", "Property:", "Contractor:") and the file name,
// so each sample doc's text reproduces its ING_DOCS scenario against the live engine:
// the mismatch docs name their true home building, the clean doc (the one whose canned home
// is the default selection) names whatever building is selected NOW — that is why it is
// generated at run time — and the uncertain doc names no property at all.
const CLEAN_HOME = "Riverside Court";
export function sampleDocText(doc, selectedName) {
  const vendor = vendorOf(doc.file);
  const prop = doc.home === null ? null : (doc.home === CLEAN_HOME ? (selectedName || doc.home) : doc.home);
  const lines = [doc.kind + " (sample document)"];
  if (prop) lines.push("Property: " + prop);
  if (vendor) lines.push("Contractor: " + vendor);
  lines.push("Issued: 2026-09-01");
  lines.push(doc.kind === "Vendor invoice"
    ? "Description: quarterly service visit — labour and parts as itemised."
    : "Description: scheduled inspection completed; certificate issued subject to the noted observations.");
  return lines.join("\n");
}

// text/plain and a .txt name, so the labelled claims are actually read rather than fed to
// a PDF parser as bytes that are not a PDF.
export function sampleDocFile(doc, selectedName) {
  const name = String(doc.file || "sample.txt").replace(/\.pdf$/i, ".txt");
  return new File([sampleDocText(doc, selectedName)], name, { type: "text/plain" });
}

// The wire outcome → the seed's tone and approval vocabulary (auditTrail.js renders both).
const OUTCOME_TONE = {
  accepted: "ok", reassigned: "accent", overridden: "warn", rejected: "risk",
  approved_on_confirmation: "dormant"
};
const OUTCOME_APPROVAL = {
  accepted: "Not required — clean match", reassigned: "Yes — after switch",
  overridden: "Yes — explicit override", approved_on_confirmation: "Yes — explicit confirmation",
  rejected: "No"
};

// A DECIDED case → the exact row shape ingLog prepends (AU_SEED's), filled from the real
// case: outcome/tone/approval from the wire outcome, checks from the findings, issue from
// the standing question or the conflicting finding, assessment from the agent's own reason.
// `ctx` carries what only the controller knows — who is signed in and the building names
// the UI keys by: {who, role, building, finalB, suggested, explanation}.
export function auditRowFromCase(c, ctx) {
  const k = ctx || {};
  const o = String((c && c.outcome) || "");
  const conflict = ((c && c.findings) || []).find((f) => f && f.direction === "conflicts");
  return {
    live: true,
    id: "a-" + ((c && c.id) || Math.random().toString(36).slice(2, 8)),
    when: "Just now",
    who: k.who || "—",
    role: k.role || "User",
    building: k.building || "—",
    finalB: o === "rejected" ? "—" : (k.finalB || k.building || "—"),
    doc: docLabel(c && c.document_name) || "—",
    outcome: OUTCOME_LABEL[o] || o || "—",
    tone: OUTCOME_TONE[o] || "dormant",
    checks: checksSummary(c && c.findings),
    issue: (c && c.question) || (conflict && conflict.message) || "None — no warning raised",
    suggested: k.suggested || "—",
    explanation: (c && c.explanation) || k.explanation || "—",
    assessment: (c && c.assessment && c.assessment.reason) || "—",
    approval: OUTCOME_APPROVAL[o] || "—"
  };
}

// ── controller methods ──────────────────────────────────────────────────
export const ingestionLiveMethods = {
  // The setup chips: the live admin register (axBldsLive, owned by usersLive — read-only
  // here), else the signed-in account's own allocation, else the seed names.
  ingBldList() {
    const s = this.state;
    if (Array.isArray(s.axBldsLive) && s.axBldsLive.length) return s.axBldsLive.map((b) => b && b.name).filter(Boolean);
    const acc = s.account && s.account.buildings;
    if (Array.isArray(acc) && acc.length) return acc.map((b) => (typeof b === "string" ? b : b && b.name)).filter(Boolean);
    return AX_BUILDINGS;
  },

  // Name → the id the API keys by: axBldsLive, the account's allocation, then the buildings
  // register (bldIdFor), then null — an unresolved name means there is nothing real to
  // validate against, and the run falls back to the offline demo rather than guessing.
  ingBldIdOf(name) {
    const want = String(name || "").trim().toLowerCase();
    const eq = (b) => String((b && b.name) || "").trim().toLowerCase() === want;
    const hit = (this.state.axBldsLive || []).find(eq);
    if (hit && hit.id) return String(hit.id);
    const acc = this.state.account && this.state.account.buildings;
    const ah = (Array.isArray(acc) ? acc : []).find((b) => b && typeof b === "object" && eq(b));
    if (ah && ah.id) return String(ah.id);
    return (typeof this.bldIdFor === "function" && this.bldIdFor(name)) || null;
  },
  ingBldNameOf(id) {
    if (!id) return "";
    const k = String(id);
    const hit = (this.state.axBldsLive || []).find((b) => b && String(b.id) === k);
    if (hit && hit.name) return hit.name;
    const row = (this.state.bldLive || []).find((b) => b && String(b.buildingId || b.building_id) === k);
    return (row && row.name) || "";
  },

  // The six pills loop while the request is in flight — never fake-completing — and
  // resolve when the case answers. Reuses this._ingT, the timer ingClose/ingStart and
  // core.js's unmount already clear.
  ingLiveSpin() {
    clearInterval(this._ingT);
    this.setState({ ingPhase: "run", ingChecked: 0, ingLiveErr: "", ingLiveRetriable: false });
    this._ingT = setInterval(() => {
      this.setState((p) => ({ ingChecked: (p.ingChecked + 1) % AX_CHECKS.length }));
    }, SPIN_MS);
  },

  // A case (validate / reassign shape) → the phase machine. The raw case is kept so the
  // vals can re-derive bubbles, suggestion and pill tints without another state slice.
  ingLiveResolve(c) {
    clearInterval(this._ingT);
    const m = mapCaseToPhase(c);
    this.setState({
      ingCase: c, ingPhase: m.phase, ingChecked: AX_CHECKS.length,
      ingBusy: false, ingLiveErr: "", ingLiveRetriable: false
    });
  },

  // One error router. `retry` is the thunk the Retry action re-fires; a 403 is the
  // caller's rights (no retry can change it), and only a pre-case network failure may
  // fall back to the canned demo — a held case is real and must not get a canned ending.
  ingLiveFail(e, retry) {
    clearInterval(this._ingT);
    if (e && e.cancelled) return this.setState({ ingBusy: false });
    const status = e && typeof e.status === "number" ? e.status : null;
    // deep-agents forwards an ops-intelligence refusal double-nested — its _fail wraps
    // the {ok, error, reason} dict as {ok:false, detail:{…}} — so the human string and
    // the machine token sit one level deeper than ApiError reads, and e.message would be
    // a JSON blob. Unwrap before classifying or displaying anything.
    const nested = e && e.body && e.body.detail && typeof e.body.detail.detail === "object" && e.body.detail.detail !== null ? e.body.detail.detail : null;
    const reason = (e && e.reason) || (nested && nested.reason) || "";
    if (status === 403 && (reason === "cannot_ingest" || reason === "building_not_allocated")) {
      this._ingRetry = null;
      const text = reason === "cannot_ingest"
        ? "Your account does not have the right to ingest documents, so nothing has been filed. An admin can grant “can ingest” on Users & access."
        : "The selected building is outside your allocation, so this document cannot be validated against it. Pick one of your allocated buildings, or ask an admin to extend the allocation.";
      return this.setState({ ingBusy: false, ingChecked: -1, ingLiveErr: text, ingLiveRetriable: false });
    }
    if (status === null && !this.state.ingCase && !(e && e.answered)) {
      // A client-side timeout is not "the service is unreachable": the upload may have
      // fully landed and a REAL case may be held server-side — the canned demo must not
      // claim nothing was checked or filed. Only a connection-level failure falls back.
      if (!(e && /^timed out/.test(String(e.message || "")))) return this.ingOfflineRun();
      this._ingRetry = retry || null;
      return this.setState({
        ingBusy: false, ingChecked: -1,
        ingLiveErr: "The request timed out on this side, but the check may still be running — the document may already be held for a decision. Retry, or look for it in the audit trail shortly.",
        ingLiveRetriable: !!retry
      });
    }
    this._ingRetry = retry || null;
    this.setState({ ingBusy: false, ingChecked: -1, ingLiveErr: (nested && (nested.error || nested.reason)) || (e && e.message) || String(e), ingLiveRetriable: !!retry });
  },

  // The explicit fallback: the canned walkthrough, transcript prefixed "(offline demo)"
  // by ingestionVals. The demo only knows the three sample docs, so a picked real file is
  // set aside rather than pretended about.
  ingOfflineRun() {
    clearInterval(this._ingT);
    this.setState({ ingOffline: true, ingFile: null, ingCase: null, ingBusy: false, ingLiveErr: "", ingLiveRetriable: false });
    this.ingRun({ ingSwitched: false });
  },

  // Run validation: mint a session, resolve the building, send the document. The response's
  // validation_cases[0] is this file's case whatever the verdict — matched files are bound
  // by the upload itself and their case stays open for the explicit confirm.
  async ingRunLive() {
    if (this._ingLiveBusy) return;
    const s = this.state;
    const name = s.ingB;
    const bid = this.ingBldIdOf(name);
    if (!bid) return this.ingOfflineRun();
    const sid = typeof crypto !== "undefined" && crypto.randomUUID
      ? crypto.randomUUID()
      : "ing-" + Date.now().toString(36) + Math.random().toString(36).slice(2, 10);
    const file = s.ingFile || sampleDocFile(ING_DOCS[s.ingDoc], name);
    this._ingLiveBusy = true;
    this.setState({ ingOffline: false, ingCase: null, ingB0: name, ingSessionId: sid, ingBusy: true, ingOutcome: null });
    this.ingLiveSpin();
    try {
      const r = await deepAgentsApi.runStatefulWithFiles("Validate and ingest this document.", sid, null, [file], undefined, bid);
      const c = r && Array.isArray(r.validation_cases) ? r.validation_cases[0] : null;
      // A check that FAILED rides back in validation_cases too, as {ok:false, …} — held,
      // but no id, no verdict, no question. That is not a case the modal can decide (the
      // approve/reject buttons would dead-end on the missing id), so it routes through
      // ingLiveFail's Retry bubble instead of rendering a dead modal. `answered` marks it
      // as a reply the service DID give — the file is registered and may be held, so the
      // offline demo's "nothing is checked or filed" must never play for it.
      if (!c || c.ok === false || !c.id) {
        const err = new Error((c && (c.error || (c.detail && c.detail.error))) || "no validation case in the reply");
        err.answered = true;
        throw err;
      }
      this.ingLiveResolve(c);
    } catch (e) {
      this.ingLiveFail(e, () => this.ingRunLive());
    } finally {
      this._ingLiveBusy = false;
    }
  },

  // "Switch to <X> and re-validate" — the check RE-RUNS against the new building, so the
  // pills animate again and the returned case decides the next phase (a document that does
  // not match there either says so rather than inheriting an approval).
  async ingReassignLive() {
    if (this._ingLiveBusy) return;
    const c = this.state.ingCase;
    const m = mapCaseToPhase(c);
    if (!c || !c.id || !m.suggestedId) return;
    const name = m.suggestedName || this.ingBldNameOf(m.suggestedId) || this.state.ingB;
    this._ingLiveBusy = true;
    this.setState({ ingB: name, ingSwitched: true, ingBusy: true });
    this.ingLiveSpin();
    try {
      const r = await deepAgentsApi.reassignCase(c.id, m.suggestedId);
      this.ingLiveResolve(r);
    } catch (e) {
      this.ingLiveFail(e, () => this.ingReassignLive());
    } finally {
      this._ingLiveBusy = false;
    }
  },

  // "Send to agent" — the uploader's reason goes to the LLM assessment. Nothing is filed
  // by this call; the response carries assessment + the next question, and the phase moves
  // to assess where the confirm button reads as confirmation or override accordingly.
  async ingClarifyLive() {
    if (this._ingLiveBusy) return;
    const s = this.state;
    const text = (s.ingReason || "").trim();
    const c = s.ingCase;
    if (!text || !c || !c.id) return;
    this._ingLiveBusy = true;
    this.setState({ ingBusy: true, ingLiveErr: "", ingLiveRetriable: false });
    try {
      const r = await deepAgentsApi.clarifyCase(c.id, text);
      this.setState({ ingCase: r, ingPhase: "assess", ingBusy: false });
    } catch (e) {
      this.ingLiveFail(e, () => this.ingClarifyLive());
    } finally {
      this._ingLiveBusy = false;
    }
  },

  // The explicit yes or no. On a yes the withheld filing happens (bound rides back); the
  // decided case's outcome — not a local guess — is what the done bubble and the audit row
  // display. A case already closed (a matched file the upload accepted inline) is re-read
  // and finished from its recorded outcome instead of erroring.
  async ingDecideLive(approve) {
    if (this._ingLiveBusy) return;
    const s = this.state;
    const c = s.ingCase;
    if (!c || !c.id) return;
    // decide's note caps at 2000 chars where clarify's explanation allows 4000 — an
    // uncapped pass-through 422s on every retry, sticking the case open from the modal.
    // Nothing is lost: the full explanation is already on the case from clarify.
    const note = (s.ingReason || "").trim().slice(0, 2000);
    this._ingLiveBusy = true;
    this.setState({ ingBusy: true, ingLiveErr: "", ingLiveRetriable: false });
    try {
      const r = await deepAgentsApi.decideCase(c.id, { approve: !!approve, note: note || undefined });
      this.ingLiveFinish(r);
    } catch (e) {
      // "case_closed" arrives three ways on the wire: as a `reason` (none today), as
      // ops-intelligence's own {detail:{ok, error:"case_closed", …}}, or as that same
      // dict forwarded by deep-agents one level deeper ({detail:{ok, detail:{…}}}).
      const d = e && e.body && e.body.detail;
      const closed = (x) => !!x && typeof x === "object" && (x.error === "case_closed" || x.reason === "case_closed");
      if ((e && e.reason === "case_closed") || closed(d) || closed(d && d.detail)) {
        try {
          const full = await deepAgentsApi.getCase(c.id);
          this._ingLiveBusy = false;
          return this.ingLiveFinish(full);
        } catch (e2) { /* fall through to the router with the original error */ }
      }
      this.ingLiveFail(e, () => this.ingDecideLive(approve));
    } finally {
      this._ingLiveBusy = false;
    }
  },

  // Every ending: prepend the locally-shaped row so the trail moves immediately, then
  // refresh the live trail so the server's own record replaces it.
  ingLiveFinish(r) {
    clearInterval(this._ingT);
    const s = this.state;
    const a = s.account || {};
    const m = mapCaseToPhase(r);
    const suggested = m.suggestedName || this.ingBldNameOf(m.suggestedId) || (s.ingSwitched ? s.ingB : "");
    const row = auditRowFromCase(r, {
      who: a.full_name || (s.role === "admin" ? "You (Admin)" : "You (User)"),
      role: a.role === "admin" || a.role === "superadmin" ? "Admin" : "User",
      building: s.ingB0 || s.ingB,
      finalB: s.ingB,
      suggested: suggested || "—",
      explanation: (s.ingReason || "").trim()
    });
    let msg = r.message || (String(r.outcome) === "rejected" ? "Nothing was ingested." : "Ingested into " + s.ingB + ".");
    const bound = r.bound;
    if (bound && bound.error) msg += " The decision is recorded, but the filing itself did not complete and can be retried.";
    else if (bound && typeof bound.documents === "number" && (bound.documents || bound.certificates)) {
      msg += " Bound " + bound.documents + (bound.documents === 1 ? " document" : " documents")
        + (bound.certificates ? " and " + bound.certificates + (bound.certificates === 1 ? " certificate" : " certificates") : "") + ".";
    }
    msg += " The full conversation is recorded in the audit trail.";
    this.setState((p) => ({
      audit: [row].concat(p.audit),
      ingCase: r, ingPhase: "done", ingBusy: false, ingLiveErr: "", ingLiveRetriable: false,
      ingOutcome: { outcome: row.outcome, msg }
    }));
    // Defensive: auditLive.js may not be mixed in everywhere the modal runs (tests).
    if (this.auLiveLoad) this.auLiveLoad();
  }
};
