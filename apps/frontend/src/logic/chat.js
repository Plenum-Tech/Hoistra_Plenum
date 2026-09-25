// chat — the full-page conversation with the orchestrator (svc-deepagents).
//
// A question asked from a space's bar, or a conversation reopened from the sessions list,
// opens this page; the answer arrives in the page, the way the Plenum AI shell's Orchestrator
// screen works, not in the side dock (Home and the report pages answer in the dock). The transcript,
// streaming, stop and edit-in-place all live in complianceLive.js (ccChat, ccBusy, ccStream …)
// and are shared with the compliance console's dock — this file only adds what the page
// itself needs: opening it, the connection line, and the domain label on a reply.
//
// Methods are mixed into HoistraLogic.prototype; `this` is the controller.
import { deepAgentsApi } from '../api/deepAgents.js';
import { isTerminal } from './migration.js';

// Which engine answered, read off the tools behind the reply. The orchestrator names its
// tools by domain (compliance_*, *_energy_*, vendor scorecards, work orders, documents),
// so the first family present is the label — compliance first because its accreditation
// tools also mention vendors.
export function domainOf(calls) {
  const names = (calls || []).map((t) => String(t || "").toLowerCase());
  const has = (re) => names.some((n) => re.test(n));
  if (!names.length) return "Orchestrator";
  // The engine that ran, when it said so, beats guessing from tool names. The vendor engine
  // emits `compliance_response` because that is the name the answer renderer matches on — so
  // every vendor answer was being badged Compliance, and the trace beside it said
  // contract_performance. The wrapper is the authority; the ladder below is the fallback.
  const engine = names.find((n) => n.startsWith("phase2_engine:"));
  if (engine) {
    if (engine.includes("contract_performance")) return "Vendors";
    if (engine.includes("energy")) return "Energy";
    if (engine.includes("compliance")) return "Compliance";
  }
  if (has(/compliance|certificate|accreditation|country_pack/)) return "Compliance";
  if (has(/energy|meter|anomal|eui|tm46/)) return "Energy";
  if (has(/contract|scorecard|invoice|vendor/)) return "Vendors";
  if (has(/work_?order|\bwo\b|ppm|technician/)) return "Work orders";
  if (has(/migration|schema|mapping/)) return "Migration";
  if (has(/doc|rag|document|ingest/)) return "Documents";
  return "Orchestrator";
}

// An engine that fails inside the orchestrator can come back as a *successful* turn whose
// answer is a JSON object with one `error` key (the compliance engine does this when its
// model call is refused). That is an error, and is shown as one.
export function errorFromAnswer(answer) {
  let v = answer;
  if (typeof v === "string") {
    const t = v.trim();
    if (!/^\{[\s\S]*\}$/.test(t)) return null;
    try { v = JSON.parse(t); } catch (e) { return null; }
  }
  if (!v || typeof v !== "object" || Array.isArray(v)) return null;
  if (typeof v.error !== "string" || !v.error.trim()) return null;
  // Anything else substantive alongside the error means it is a payload, not a failure.
  const other = Object.keys(v).filter((k) => k !== "error" && k !== "status" && k !== "code" && k !== "detail");
  if (other.length) return null;
  return v.error.trim();
}

export const chatMethods = {
  // The conversation is the page: no dock, no drawers over it.
  openChat() {
    this.setState({ view: "chat", orchOpen: false, queueOpen: false, paletteOpen: false, detail: null });
    if (typeof window !== "undefined" && window.scrollTo) window.scrollTo(0, 0);
    this.chatConnect();
    // A run restored from the session has an id and no document until something reads it,
    // and a run that was being followed has a document that stopped being read the moment
    // the person left: polling is scheduled by the previous poll and only while this is the
    // view. Both are out of date, so both are read again — anything short of a terminal
    // status is by definition not the last word. Testing for a missing document instead is
    // what left the panel reporting "Running · Node 8 of 9" over a run that had finished.
    if (this.state.mgId && !isTerminal(this.state.mgStatus && this.state.mgStatus.status)) {
      this.mgPoll(true);
    }
  },

  // "Connected · N tools" — one read of the tool catalogue, retried on request.
  async chatConnect(force) {
    if (this._chatConnecting) return;
    if (!force && this.state.chatLink === "connected") return;
    this._chatConnecting = true;
    this.setState({ chatLink: "checking" });
    try {
      const tools = await deepAgentsApi.tools();
      this.setState({ chatLink: "connected", chatTools: Array.isArray(tools) ? tools.length : null, chatLinkError: "" });
    } catch (e) {
      this.setState({ chatLink: "unreachable", chatTools: null, chatLinkError: (e && e.message) || String(e) });
    } finally {
      this._chatConnecting = false;
    }
  }
};
