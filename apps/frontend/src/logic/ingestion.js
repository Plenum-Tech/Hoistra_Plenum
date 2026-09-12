// ingestion — the ingestion validation agent modal. Runs for every ingestion,
// user or admin, any document type. The conversation is REAL now: the document
// (a picked file, or a sample doc regenerated at run time) goes to svc-deepagents
// through ingestionLive.js, the six pills loop until the case answers and resolve
// from its findings, and every ending is a decide call whose recorded outcome
// writes the audit trail. The canned walkthrough below survives only as the
// offline demo (s.ingOffline) when the validation service cannot be reached.
// Methods are mixed into HoistraLogic.prototype; `this` is the controller.
import { AX_BUILDINGS, AX_CHECKS, ING_DOCS } from '../data/hoistra-access.js';
import { canAdmin } from './auth.js';
import { mapCaseToPhase, checksFromFindings } from './ingestionLive.js';

export const ingestionMethods = {
  ingStart() {
    clearInterval(this._ingT);
    const names = this.ingBldList ? this.ingBldList() : AX_BUILDINGS;
    const b = names.indexOf(this.state.ingB) > -1 ? this.state.ingB : (names[0] || "Riverside Court");
    this.setState({
      ingOn: true, ingPhase: "setup", ingB: b, ingDoc: 0, ingChecked: 0, ingSwitched: false, ingReason: "", ingOutcome: null,
      // live-run slices (ingestionLive.js), reset so a reopened modal starts clean
      ingCase: null, ingFile: null, ingB0: "", ingSessionId: "", ingBusy: false,
      ingOffline: false, ingLiveErr: "", ingLiveRetriable: false,
      detail: null, queueOpen: false, paletteOpen: false
    });
  },
  ingClose() { clearInterval(this._ingT); this.setState({ ingOn: false, ingBusy: false }); },

  // ── the canned walkthrough — kept verbatim as the offline demo ──────────
  // ingOfflineRun (ingestionLive.js) sets s.ingOffline before calling ingRun; the live
  // path never comes through here.
  ingRun(patch) {
    clearInterval(this._ingT);
    this.setState({ ingPhase: "run", ingChecked: 0, ...(patch || {}) });
    this._ingT = setInterval(() => {
      this.setState((p) => {
        const n = p.ingChecked + 1;
        if (n >= AX_CHECKS.length) {
          clearInterval(this._ingT);
          const doc = ING_DOCS[p.ingDoc];
          const phase = doc.home === null ? "uncertain" : (p.ingB === doc.home ? "valid" : "mismatch");
          return { ingChecked: n, ingPhase: phase };
        }
        return { ingChecked: n };
      });
    }, 520);
  },

  ingLog(entry) {
    const s = this.state;
    const doc = ING_DOCS[s.ingDoc];
    const row = {
      id: "a" + s.audit.length + "-" + Object.keys(entry).length, when: "Just now", who: s.role === "admin" ? "You (Admin)" : "You (User)", role: s.role === "admin" ? "Admin" : "User",
      building: s.ingSwitched ? (doc.home || s.ingB) : s.ingB, doc: doc.file, checks: "6 of 6 run", ...entry
    };
    this.setState((p) => ({ audit: [row].concat(p.audit) }));
  },
  ingFinish(outcome, entry, msg) {
    this.ingLog(entry);
    this.setState({ ingPhase: "done", ingOutcome: { outcome, msg } });
  },

  ingestionVals(s) {
    // Live is the default; offline is the explicit fallback ingOfflineRun() switched on.
    const offline = !!s.ingOffline;
    const c = !offline && s.ingCase ? s.ingCase : null;
    const m = c ? mapCaseToPhase(c) : null;
    const sugName = m ? (m.suggestedName || (this.ingBldNameOf ? this.ingBldNameOf(m.suggestedId) : "")) : "";
    const doc = ING_DOCS[s.ingDoc];
    const home = doc.home;
    const bName = s.ingB;
    const docLabel = s.ingFile ? s.ingFile.name : doc.file;
    const detail = (d) => d.detail.split("{b}").join(bName).split("{h}").join(home || "");
    const bldNames = this.ingBldList ? this.ingBldList() : AX_BUILDINGS;

    const msgs = [];
    const actions = [];
    let askReason = false;
    if (s.ingPhase !== "setup") msgs.push({ who: "you", text: "Ingest “" + docLabel + "” into " + bName + "." });
    if (offline && s.ingPhase !== "setup") msgs.push({ who: "agent", text: "(offline demo) The validation service is unreachable, so this run is the canned walkthrough — nothing is checked or filed." });
    if (s.ingPhase === "run") msgs.push({ who: "agent", text: "Reading the document and comparing it with " + bName + " and its ontology — vendors, assets, floors, contracts, certificates and UDR relationships already in the graph." });
    if (s.ingPhase === "valid") {
      if (c) {
        m.bubbles.forEach((t) => msgs.push({ who: "agent", text: t }));
        actions.push({ label: "Confirm ingestion", kind: "primary", click: () => this.ingDecideLive && this.ingDecideLive(true) });
      } else {
        msgs.push({ who: "agent", text: (s.ingSwitched ? "Re-validated against " + bName + ". " : "") + "Everything lines up: the vendor is on record for " + bName + ", the country and certificate type match, and the referenced entities exist in this building's graph. This document is valid to ingest." });
        actions.push({ label: "Confirm ingestion", kind: "primary", click: () => this.ingFinish("Accepted", { finalB: bName, outcome: s.ingSwitched ? "Reassigned" : "Accepted", tone: s.ingSwitched ? "accent" : "ok", issue: s.ingSwitched ? "Originally selected building did not match — agent suggested " + bName : "None — clean match against the building ontology", suggested: s.ingSwitched ? bName : "—", explanation: s.ingSwitched ? "Agent suggestion accepted" : "—", assessment: "Validated against the " + bName + " graph", approval: s.ingSwitched ? "Yes — after switch" : "Not required — clean match" }, "Ingested into " + bName + ". The document, the checks and the confirmation are recorded in the audit trail.") });
      }
    }
    if (s.ingPhase === "mismatch") {
      if (c) {
        m.bubbles.forEach((t) => msgs.push({ who: "agent", text: t }));
        if (m.suggestedId && sugName) actions.push({ label: "Switch to " + sugName + " and re-validate", kind: "primary", click: () => this.ingReassignLive && this.ingReassignLive() });
        actions.push({ label: "Keep " + bName, kind: "secondary", click: () => this.setState({ ingPhase: "reason" }) });
      } else {
        msgs.push({ who: "agent", text: "This document appears to relate to " + home + " rather than " + bName + ". " + detail(doc) + ". Would you like to switch the selected building to " + home + "?" });
        actions.push({ label: "Switch to " + home + " and re-validate", kind: "primary", click: () => this.ingRun({ ingB: home, ingSwitched: true }) });
        actions.push({ label: "Keep " + bName, kind: "secondary", click: () => this.setState({ ingPhase: "reason" }) });
      }
    }
    if (s.ingPhase === "reason") {
      if (c) {
        m.bubbles.forEach((t) => msgs.push({ who: "agent", text: t }));
        msgs.push({ who: "you", text: "Keep it on " + bName + "." });
        msgs.push({ who: "agent", text: "Understood — " + bName + " stays selected. What is the reason this document belongs here? I will review the explanation before anything is stored." });
        if (s.ingBusy) {
          msgs.push({ who: "you", text: s.ingReason });
          msgs.push({ who: "agent", text: "Reviewing your explanation against the case…" });
        } else {
          askReason = true;
          actions.push({ label: "Send to agent", kind: "primary", click: () => { if ((this.state.ingReason || "").trim() && this.ingClarifyLive) this.ingClarifyLive(); } });
        }
      } else {
        msgs.push({ who: "agent", text: "This document appears to relate to " + home + " rather than " + bName + ". " + detail(doc) + "." });
        msgs.push({ who: "you", text: "Keep it on " + bName + "." });
        msgs.push({ who: "agent", text: "Understood — " + bName + " stays selected. What is the reason this document belongs here? I will review the explanation before anything is stored." });
        askReason = true;
        actions.push({ label: "Send to agent", kind: "primary", click: () => { if ((this.state.ingReason || "").trim()) this.setState({ ingPhase: "assess" }); } });
      }
    }
    if (s.ingPhase === "assess") {
      if (c) {
        msgs.push({ who: "you", text: c.explanation || s.ingReason });
        const as = c.assessment || {};
        const resolves = as.resolves === true;
        msgs.push({ who: "agent", text: [as.reason, c.message].filter(Boolean).join(" ") || "Assessment complete. Ingest into " + bName + "?" });
        actions.push({ label: resolves ? "Yes — confirm and ingest" : "Yes — override and ingest", kind: resolves ? "primary" : "warn", click: () => this.ingDecideLive && this.ingDecideLive(true) });
        actions.push({ label: "No — do not ingest", kind: "secondary", click: () => this.ingDecideLive && this.ingDecideLive(false) });
      } else {
        msgs.push({ who: "you", text: s.ingReason });
        msgs.push({ who: "agent", text: "Noted. That may account for the vendor, but the asset references still point to " + home + ", so the concern is not resolved. If you proceed, this ingestion is recorded as an override with your explanation attached. Ingest into " + bName + " anyway?" });
        actions.push({ label: "Yes — override and ingest", kind: "warn", click: () => this.ingFinish("Overridden", { finalB: bName, outcome: "Overridden", tone: "warn", issue: detail(doc), suggested: home, explanation: s.ingReason, assessment: "Explanation noted; asset references still conflict — concern not resolved", approval: "Yes — explicit override" }, "Ingested into " + bName + " as an explicit override. Your explanation, the agent's assessment and the approval are on the audit trail.") });
        actions.push({ label: "No — do not ingest", kind: "secondary", click: () => this.ingFinish("Rejected", { finalB: "—", outcome: "Rejected", tone: "risk", issue: detail(doc), suggested: home, explanation: s.ingReason, assessment: "Override declined by uploader", approval: "No" }, "Nothing was ingested. The flagged attempt and your explanation are recorded in the audit trail.") });
      }
    }
    if (s.ingPhase === "uncertain") {
      if (c) {
        m.bubbles.forEach((t) => msgs.push({ who: "agent", text: t }));
        actions.push({ label: "Yes — approve and ingest", kind: "primary", click: () => this.ingDecideLive && this.ingDecideLive(true) });
        actions.push({ label: "No — do not ingest", kind: "secondary", click: () => this.ingDecideLive && this.ingDecideLive(false) });
      } else {
        msgs.push({ who: "agent", text: "I cannot confidently validate this document against " + bName + ": Apex Lifts is an approved vendor here, but the invoice lines reference a lift-car refurbishment with no matching work order or asset history in the graph yet. The match cannot be logically proven, so explicit approval is required before ingestion." });
        actions.push({ label: "Yes — approve and ingest", kind: "primary", click: () => this.ingFinish("Approved on confirmation", { finalB: bName, outcome: "Approved on confirmation", tone: "dormant", issue: "Insufficient reference data — no matching work order or asset history", suggested: "—", explanation: "Uploader confirmed the document belongs to " + bName, assessment: "Cannot be logically proven from the current ontology", approval: "Yes — explicit confirmation" }, "Ingested into " + bName + " after explicit confirmation. As the building's ontology grows, future documents like this validate automatically.") });
        actions.push({ label: "No — do not ingest", kind: "secondary", click: () => this.ingFinish("Rejected", { finalB: "—", outcome: "Rejected", tone: "risk", issue: "Insufficient reference data to validate", suggested: "—", explanation: "Uploader declined to confirm", assessment: "Not ingested", approval: "No" }, "Nothing was ingested. The attempt is recorded in the audit trail.") });
      }
    }
    if (s.ingPhase === "done" && s.ingOutcome) {
      msgs.push({ who: "agent", text: (s.ingOutcome.outcome ? s.ingOutcome.outcome + " — " : "") + s.ingOutcome.msg });
      // The admin VIEW MODE is only for accounts auth.js lets toggle it — the modal runs
      // for every signed-in user and must not escalate a real user account's view.
      actions.push({ label: "View audit trail", kind: "primary", click: () => { clearInterval(this._ingT); window.scrollTo(0, 0); this.setState((p) => ({ ingOn: false, view: "audit", role: canAdmin(p.account) ? "admin" : p.role, navOpen: true, detail: null })); } });
      actions.push({ label: "Close", kind: "secondary", click: () => this.setState({ ingOn: false }) });
    }

    // A decide in flight keeps its phase; the buttons give way to a working line so a
    // double-click cannot double-file.
    if (!offline && s.ingBusy && s.ingPhase !== "run" && s.ingPhase !== "reason" && s.ingPhase !== "done") {
      msgs.push({ who: "agent", text: "Recording the decision…" });
      actions.length = 0;
    }
    // A live failure replaces the actions: a rights problem gets no retry (retrying cannot
    // change the caller's role), everything else gets one.
    if (!offline && s.ingLiveErr) {
      msgs.push({ who: "agent", text: s.ingLiveErr });
      actions.length = 0;
      if (s.ingLiveRetriable) actions.push({ label: "Retry", kind: "primary", click: () => this._ingRetry && this._ingRetry() });
      actions.push({ label: "Close", kind: "secondary", click: () => this.ingClose() });
    }

    // Pills: loop while a request is in flight (ingLiveSpin cycles ingChecked), resolve
    // from the case's findings once it answers — a conflicting finding tints its pill warn.
    const slots = c ? checksFromFindings(c.findings) : null;

    return {
      ingOn: s.ingOn,
      ingStart: () => this.ingStart(),
      ingClose: () => this.ingClose(),
      ingSetup: s.ingPhase === "setup",
      ingBuildings: bldNames.map((b) => ({
        name: b, pick: () => this.setState({ ingB: b }),
        bg: s.ingB === b ? "var(--color-accent-900)" : "var(--color-surface)",
        fg: s.ingB === b ? "var(--color-accent)" : "var(--color-neutral-400)",
        edge: s.ingB === b ? "var(--color-accent)" : "var(--color-divider)"
      })),
      ingDocs: ING_DOCS.map((d, i) => ({
        // Picking a sample doc puts the picked file aside — one document per run.
        file: d.file, kind: d.kind, pick: () => this.setState({ ingDoc: i, ingFile: null }),
        tick: !s.ingFile && s.ingDoc === i ? "ph-radio-button" : "ph-circle",
        fg: !s.ingFile && s.ingDoc === i ? "var(--color-accent)" : "var(--color-neutral-500)",
        bg: !s.ingFile && s.ingDoc === i ? "var(--marker-tint)" : "transparent"
      })),
      // The real file picker row (the modal owns the hidden <input type="file">).
      ingFileName: s.ingFile ? s.ingFile.name : "",
      ingFileLabel: s.ingFile ? s.ingFile.name : "Upload a document…",
      ingFileKind: s.ingFile ? "Your document — validated as uploaded" : "Any file from your machine",
      ingFileTick: s.ingFile ? "ph-radio-button" : "ph-upload-simple",
      ingFileFg: s.ingFile ? "var(--color-accent)" : "var(--color-neutral-500)",
      ingFileBg: s.ingFile ? "var(--marker-tint)" : "transparent",
      ingFilePick: (e) => {
        const f = e && e.target && e.target.files && e.target.files[0];
        if (f) this.setState({ ingFile: f });
        if (e && e.target) e.target.value = "";
      },
      ingGo: () => (this.ingRunLive ? this.ingRunLive() : this.ingRun({ ingSwitched: false })),
      ingChecksOn: s.ingPhase !== "setup",
      ingChecks: AX_CHECKS.map((label, i) => {
        const state = s.ingPhase === "run" ? (i < s.ingChecked ? "done" : i === s.ingChecked ? "busy" : "wait") : "done";
        const warn = state === "done" && s.ingPhase !== "run" && slots && slots[i].warn;
        return { label: label, icon: state === "done" ? "ph-check-circle" : state === "busy" ? "ph-circle-notch" : "ph-circle", fg: warn ? "var(--st-warn)" : state === "done" ? "var(--st-ok)" : state === "busy" ? "var(--color-accent)" : "var(--color-neutral-600)", spin: state === "busy" ? "spin 0.9s linear infinite" : "none" };
      }),
      ingMsgs: msgs.map((mm) => ({
        text: mm.text, agent: mm.who === "agent",
        align: mm.who === "agent" ? "flex-start" : "flex-end",
        bg: mm.who === "agent" ? "var(--color-bg)" : "var(--color-accent-900)",
        icon: mm.who === "agent" ? "flex" : "none"
      })),
      ingAskReason: askReason,
      ingReason: s.ingReason || "", ingSetReason: (e) => this.setState({ ingReason: e.target.value }),
      ingActions: actions.map((a) => ({
        label: a.label, click: a.click,
        bg: a.kind === "primary" ? "var(--color-accent)" : a.kind === "warn" ? "var(--st-warn)" : "transparent",
        fg: a.kind === "secondary" ? "var(--color-neutral-400)" : "var(--accent-ink)",
        edge: a.kind === "secondary" ? "var(--color-divider)" : "transparent"
      })),
      ingScope: "Runs for every ingestion — users and admins alike, any document type, new buildings included."
    };
  }
};
