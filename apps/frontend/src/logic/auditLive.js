// auditLive — the Ingestion audit trail read from svc-operations-intelligence
// (api/admin.js): GET /api/admin/ingestion-audit, admin role required.
//
// Replace-in-place: on success the shaped rows land straight in `audit`, the same store
// the seed (AU_SEED) fills and the sample-ingestion modal (logic/ingestion.js) prepends
// to, so auditVals' filter chips, tones and 8-field expanded record render either without
// knowing which. The seed stays until the read answers; a failure is a small error slice
// (auLiveError) the screen shows next to the Entries tile, never a blank table.
//
// The read is deliberately unfiltered — the endpoint takes exactly ONE ?outcome= token per
// request, and the UI's "Accepted" chip folds approved_on_confirmation in client-side
// (auditTrail.js's prefix match) — so one page of the whole trail is the cheaper fetch.
// The three step outcomes the engine also records (validated, held, clarified) match no
// decision chip and surface under "All" only, by design.
//
// The server keys buildings by id; the UI keys them by name. Names ride on the row itself
// (building_name, reassigned_to_building_name); detail.suggested_building_id is id-only
// and resolves against axBldsLive (usersLive's canonical live buildings list — read-only
// here) with the Buildings table's rows (bldLive) as the second reader.
//
// The ingestion module refreshes the trail after a decision by calling auLiveLoad() again.
//
// shapeLiveAudit() and the formatters are pure; the methods below are mixed into
// HoistraLogic.prototype and `this` is the controller.
import { adminApi } from '../api/admin.js';
import { humanise } from './homeLive.js';

const RETRY_MS = 30000;
const RETRY_MAX = 6;

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const pad2 = (n) => String(n).padStart(2, "0");
// Uploaded documents are stored as "<uuid>_<original name>"; the prefix is not for people.
const docName = (n) => String(n || "").replace(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}_/i, "");

// The seed's timestamp vocabulary: "Just now" inside a minute, "Today 09:41" the same day,
// "Mon 16:20" inside the week, "28 Aug 14:47" beyond it — plus the year once it differs.
export function whenLabel(iso, now) {
  const d = iso ? new Date(iso) : null;
  if (!d || isNaN(d)) return "—";
  const n = now ? new Date(now) : new Date();
  if (Math.abs(n - d) < 60000) return "Just now";
  const clock = pad2(d.getHours()) + ":" + pad2(d.getMinutes());
  const midnight = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate());
  const daysAgo = Math.round((midnight(n) - midnight(d)) / 86400000);
  if (daysAgo === 0) return "Today " + clock;
  if (daysAgo > 0 && daysAgo < 7) return DAYS[d.getDay()] + " " + clock;
  return pad2(d.getDate()) + " " + MONTHS[d.getMonth()] + (d.getFullYear() === n.getFullYear() ? "" : " " + d.getFullYear()) + " " + clock;
}

// "6 of 6 passed" / "6 run · 2 mismatched" / "6 run · 4 inconclusive" from the findings the
// engine recorded (detail.checks on a step row, detail.findings on a decision). An `unknown`
// direction is deliberate — "no vendors on record to check against" is not "the vendor is
// wrong" — so it reads as inconclusive, never as a mismatch.
export function checksSummary(findings) {
  const list = Array.isArray(findings) ? findings : [];
  if (!list.length) return "—";
  const bad = list.filter((f) => f && f.direction === "conflicts").length;
  const grey = list.filter((f) => f && f.direction === "unknown").length;
  if (!bad && !grey) return list.length + " of " + list.length + " passed";
  return list.length + " run" + (bad ? " · " + bad + " mismatched" : "") + (grey ? " · " + grey + " inconclusive" : "");
}

// snake_case on the wire → the display labels the filter chips prefix-match on, and the
// AU_TONE key each renders with. Unknown future tokens humanise rather than throw.
export const OUTCOME_LABEL = {
  accepted: "Accepted", reassigned: "Reassigned", overridden: "Overridden",
  rejected: "Rejected", approved_on_confirmation: "Approved on confirmation",
  validated: "Validated", held: "Held", clarified: "Clarified"
};
const OUTCOME_TONE = {
  accepted: "ok", reassigned: "accent", overridden: "warn", rejected: "risk",
  approved_on_confirmation: "dormant", validated: "ok", held: "warn", clarified: "accent"
};
// The seed's approval vocabulary per decision; a step row carries no decision yet.
const OUTCOME_APPROVAL = {
  accepted: "Not required — clean match", reassigned: "Yes — after switch",
  overridden: "Yes — explicit override", approved_on_confirmation: "Yes — explicit confirmation",
  rejected: "No"
};
// Steps have no final building — nothing has been filed when they are written.
const STEP_OUTCOMES = { validated: true, held: true, clarified: true };

// id → display name across the two live registers that carry one. axBldsLive last, so the
// canonical admin list wins over the Buildings table when both know the id.
export function buildingNames(axBlds, bldRows) {
  const map = {};
  (bldRows || []).forEach((b) => { const id = b && (b.buildingId || b.building_id); if (id && b.name) map[String(id)] = b.name; });
  (axBlds || []).forEach((b) => { if (b && b.id && b.name) map[String(b.id)] = b.name; });
  return map;
}

// The agent's verdict on the uploader's explanation, as one line.
function assessmentText(a) {
  if (!a || typeof a !== "object") return "—";
  if (typeof a.reason === "string" && a.reason) return a.reason;
  if (a.resolves === true) return "Explanation resolves the warning";
  if (a.resolves === false) return "Explanation does not resolve the warning";
  return "—";
}

// One GET /api/admin/ingestion-audit entry → exactly the AU_SEED row shape, so auditVals
// renders either without knowing which. `blds` is the id→name map from buildingNames().
export function shapeLiveAudit(entry, blds, now) {
  const e = entry || {};
  const d = e.detail || {};
  const outcome = String(e.outcome || "");
  const names = blds || {};
  const nameOf = (id) => (id ? names[String(id)] || "Building " + String(id).slice(0, 8) : null);
  const building = e.building_name || nameOf(e.building_id) || "—";
  const finalName = e.reassigned_to_building_name || nameOf(e.reassigned_to_building_id) || building;
  const findings = d.checks || d.findings || null;
  const conflict = (findings || []).find((f) => f && f.direction === "conflicts");
  // The backend writes "reassigned" TWICE per switch: once as a STEP when POST /reassign
  // runs (approved_by NULL — nothing decided yet) and once as the decision when the moved
  // case is approved (approved_by set). Only the second is an approval into a building.
  const step = !!STEP_OUTCOMES[outcome] || (outcome === "reassigned" && !e.approved_by);
  return {
    live: true,
    id: String(e.id || ""),
    when: whenLabel(e.occurred_at, now),
    who: e.actor_name || e.actor_email || "—",
    role: e.actor_role === "admin" || e.actor_role === "superadmin" ? "Admin" : "User",
    building: building,
    finalB: (step || outcome === "rejected") ? "—" : finalName,
    doc: docName(e.document_name) || "—",
    outcome: OUTCOME_LABEL[outcome] || humanise(outcome) || "—",
    tone: OUTCOME_TONE[outcome] || "dormant",
    checks: checksSummary(findings),
    issue: e.warning || (conflict && conflict.message) || "None — no warning raised",
    suggested: nameOf(d.suggested_building_id) || "—",
    // The raw id rides along so auLiveReshape can re-resolve the name when a register
    // lands after this row was shaped.
    suggestedId: d.suggested_building_id ? String(d.suggested_building_id) : null,
    explanation: e.explanation || "—",
    assessment: assessmentText(d.assessment),
    approval: step ? "—" : (OUTCOME_APPROVAL[outcome] || "—")
  };
}

// ── controller methods ──────────────────────────────────────────────────
export const auditLiveMethods = {
  async auLiveLoad(opts) {
    // A refresh asked for mid-flight must queue, not drop: a decision's trail refresh can
    // race the mount read, whose response — fetched before the decision — then replaces
    // `audit` wholesale and deletes the freshly prepended row. The queued re-run lands
    // after the decision and restores it from the server's own record.
    if (this._auLiveLoading) { this._auLiveAgain = true; return; }
    this._auLiveLoading = true;
    clearTimeout(this._auLiveRetry);
    this.setState({ auLiveLoading: true });
    try {
      const res = await adminApi.ingestionAudit({ limit: 200 });
      // apiFetch returns null for an empty 200 — that is a malformed answer, not an empty
      // trail (an empty trail is {ok, count: 0, entries: []}), so it must not eat the seed.
      const entries = res && Array.isArray(res.entries) ? res.entries.slice() : null;
      if (!entries) throw new Error("empty response — no entries in the audit read");
      // Newest first is the endpoint's order; sort defensively so it stays true regardless.
      entries.sort((a, b) => String(b.occurred_at || "").localeCompare(String(a.occurred_at || "")));
      const names = buildingNames(this.state.axBldsLive, this.state.bldLive);
      const now = new Date();
      this._auLiveAttempts = 0;
      this.setState({
        audit: entries.map((e) => shapeLiveAudit(e, names, now)),
        auLiveRaw: { count: res.count, by_outcome: res.by_outcome || {} },
        auLiveLoading: false, auLiveError: "", auLiveLoadedAt: now.toISOString()
      });
      if (opts && opts.announce) this.flash("Audit trail refreshed — " + entries.length + " of " + (res.count || entries.length) + " entries");
    } catch (e) {
      const msg = (e && e.message) || String(e);
      this._auLiveAttempts = (this._auLiveAttempts || 0) + 1;
      this.setState({ auLiveLoading: false, auLiveError: msg });
      // A 403 is the caller's role, not the backend's health — a timer will not change it;
      // auLiveRetryNow still works should the account be promoted mid-session.
      if (this._auLiveAttempts < RETRY_MAX && !(e && e.status === 403)) {
        this._auLiveRetry = setTimeout(() => this.auLiveLoad(), RETRY_MS);
      }
      if (opts && opts.announce) this.flash("Audit trail unreachable — " + msg);
    } finally {
      this._auLiveLoading = false;
      if (this._auLiveAgain) { this._auLiveAgain = false; this.auLiveLoad(); }
    }
  },

  auLiveRetryNow() { this._auLiveAttempts = 0; return this.auLiveLoad({ announce: true }); },

  // Suggested-building names resolve against registers (axBldsLive, bldLive) that can
  // land AFTER the audit read — core.js fires the loads in one breath, so an early trail
  // shapes every detail.suggested_building_id as the "Building 1a2b3c4d" placeholder and
  // is never revisited. Re-resolve in place when a register arrives: rows are kept,
  // placeholder names are filled in from the ids the shaper left on the rows.
  auLiveReshape() {
    const names = buildingNames(this.state.axBldsLive, this.state.bldLive);
    this.setState((p) => ({
      audit: p.audit.map((r) => (r && r.live && r.suggestedId && names[r.suggestedId] && names[r.suggestedId] !== r.suggested
        ? { ...r, suggested: names[r.suggestedId] } : r))
    }));
  }
};
