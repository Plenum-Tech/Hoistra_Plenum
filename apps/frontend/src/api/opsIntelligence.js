// api/opsIntelligence — svc-operations-intelligence, the routers api/compliance.js does not cover:
//   /api/approvals             the unified approvals queue (Features A, B and C write into it)
//   /api/contract-performance  Feature B — contract terms, scorecards, weights, invoice flags
//   /api/energy                Feature C — meters, anomalies, EUI reports
// Routes: apps/backend/.../svc-operations-intelligence/src/api/routes/{approvals,contract_performance,energy}.py
// Mounted behind the gateway at /backend/ops-intelligence/.
//
// Reads only. The Home page composes these into its tiles (src/logic/homeLive.js) and the
// Vendors page into its scorecards (src/logic/vendorsLive.js), the way the Plenum AI shell's
// saved-space panels read the same summaries. Nothing here holds state or touches the
// store; the transport is api/client.js.
import { BASES, currentOrgId, apiFetch } from './client.js';

const B = BASES.opsIntelligence;
// Writes wait longer than reads: confirming and patching both commit and audit in one call.
const T_WRITE = 30000;
// currentOrgId() is ORG_ID (the build's default tenant) unless a superadmin is viewing
// as another company, in which case that company's id takes over for every read here.
const withOrg = (q) => { const o = currentOrgId(); return o ? Object.assign({ organization_id: o }, q || {}) : (q || {}); };

export const opsApi = {
  // ── Approvals ───────────────────────────────────────────────────────────
  // Everything the engines have queued for a human. `status` defaults to pending on the
  // server too; `source_feature` (A|B|C) narrows to one engine. Server caps limit at 500.
  approvals: (query) =>
    apiFetch(B, '/api/approvals', { query: withOrg(Object.assign({ status: 'pending', limit: 500 }, query || {})) }),

  // ── Contract performance ────────────────────────────────────────────────
  // Contract parameter sets read out of contract documents: SLA hours, rates, KPI clauses.
  // `status` filters draft | confirmed.
  contracts: (query) =>
    apiFetch(B, '/api/contract-performance/contracts', { query: withOrg(Object.assign({ limit: 500 }, query || {})) }),
  // Monthly vendor scorecards, newest first.
  scorecards: (query) =>
    apiFetch(B, '/api/contract-performance/scorecards', { query: withOrg(query) }),
  // The Vendors saved-space summary: the newest 200 scorecards, the weights, the pending
  // Feature B approvals and unapproved asset criticalities, one read.
  contractSummary: () =>
    apiFetch(B, '/api/contract-performance/saved-space/summary', { query: withOrg() }),
  // One contract parameter set by id — scorecards name the set they were scored against.
  contract: (parametersId) =>
    apiFetch(B, '/api/contract-performance/contracts/' + encodeURIComponent(parametersId)),

  // ── The two writes on this router ──────────────────────────────────────
  // A draft parameter set is what an ingest produces; scoring refuses to run against one.
  // Confirming is the act that turns extracted numbers into agreed ones, so it is deliberate
  // and it is audited — `confirmed_by` names the person when the session has a uuid for them.
  //
  // Correct before you confirm, not after: a confirmed set is already being scored against.
  // `updates` carries ONLY the fields that changed, because the route writes what it is given
  // and a whole-row body would silently restate every value the reader never looked at.
  confirmContract: (parametersId, body) =>
    apiFetch(B, '/api/contract-performance/contracts/' + encodeURIComponent(parametersId) + '/confirm', {
      method: 'POST', body: body || {}, timeoutMs: T_WRITE
    }),
  updateContract: (parametersId, updates, actor) =>
    apiFetch(B, '/api/contract-performance/contracts/' + encodeURIComponent(parametersId), {
      method: 'PATCH', body: { updates: updates, actor: actor || null }, timeoutMs: T_WRITE
    }),
  // Cut the scorecards from the work orders already migrated into plenum_cafm. This is what
  // the Vendors page's "Rebuild scorecards" button does — it used to hand the WORDS to the
  // chat orchestrator through runAction(), which matched no action spec, so the button asked
  // a question and nothing was ever scored. vendor_wo_scores and vendor_monthly_scorecards
  // both sat empty while the jobs to score were in the table.
  //
  // all_buckets scores every vendor x month present, not just last month, so a portfolio
  // migrated in one go is scored in one go. A vendor with no CONFIRMED contract comes back
  // ok:false and is skipped — scoring against platform defaults would hold a vendor to terms
  // they never agreed to.
  rebuildScorecards: (body) =>
    apiFetch(B, '/api/contract-performance/score/from-udr', {
      method: 'POST',
      body: Object.assign({ all_buckets: true, generate_scorecard: true, limit: 500 }, withOrg(body || {})),
      timeoutMs: T_WRITE
    }),
  // The scoring weights in force (SLA response / completion / first fix / recall /
  // accreditation percentages, the blocked-score cap, the invoice adversary threshold).
  weights: () =>
    apiFetch(B, '/api/contract-performance/admin/weights', { query: withOrg() }),
  // Feature B's own view of the approvals queue: invoice line flags, contract parameter
  // reviews, overlapping-contract ties. `status` defaults to pending on the server.
  contractApprovals: (query) =>
    apiFetch(B, '/api/contract-performance/approvals', { query: withOrg(query) }),
  // FR-039: a work order whose re-ingested values disagree with the stored row is flagged
  // and excluded from scoring, and the flag is sticky — the scoring fetch filters on
  // `conflict_flag IS NOT TRUE`. The resolver has existed on the server since the flag did;
  // nothing here ever called it, so the only way to release a work order was a hand-written
  // UPDATE. `accept` is 'stored' or 'incoming': the two records genuinely differ and someone
  // has to say which is true, because scoring measures money against those numbers.
  resolveWoConflict: (woCode, accept, note) =>
    apiFetch(B, '/api/contract-performance/work-orders/' + encodeURIComponent(woCode) + '/resolve-conflict', {
      method: 'POST',
      body: { accept: accept, note: note || null },
      query: withOrg(),
      timeoutMs: T_WRITE
    }),
  // Asset criticality register (L1 / L2 / L3), approved or proposed.
  assetCriticalities: (query) =>
    apiFetch(B, '/api/contract-performance/asset-criticality', { query: withOrg(Object.assign({ limit: 500 }, query || {})) }),
  // FR-028 cost and labour variance for a vendor (or the portfolio), with the work orders behind it.
  insights: (query) =>
    apiFetch(B, '/api/contract-performance/insights', { query: withOrg(query) }),

  // ── Energy ──────────────────────────────────────────────────────────────
  // Meters on record with their MPAN / MPRN and the site they are linked to.
  meters: (query) =>
    apiFetch(B, '/api/energy/meters', { query: withOrg(Object.assign({ limit: 500 }, query || {})) }),
  // Detected anomalies. `status` defaults to open on the server.
  anomalies: (query) =>
    apiFetch(B, '/api/energy/anomalies', { query: withOrg(Object.assign({ status: 'open', limit: 100 }, query || {})) }),
  // The Energy saved-space summary: KPIs, monthly reports with their EUI trend, open anomalies.
  energySummary: () =>
    apiFetch(B, '/api/energy/saved-space/summary', { query: withOrg() })
};
