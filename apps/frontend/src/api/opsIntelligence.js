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
import { BASES, ORG_ID, apiFetch } from './client.js';

const B = BASES.opsIntelligence;
const withOrg = (q) => (ORG_ID ? Object.assign({ organization_id: ORG_ID }, q || {}) : (q || {}));

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
  // The scoring weights in force (SLA response / completion / first fix / recall /
  // accreditation percentages, the blocked-score cap, the invoice adversary threshold).
  weights: () =>
    apiFetch(B, '/api/contract-performance/admin/weights', { query: withOrg() }),
  // Feature B's own view of the approvals queue: invoice line flags, contract parameter
  // reviews, overlapping-contract ties. `status` defaults to pending on the server.
  contractApprovals: (query) =>
    apiFetch(B, '/api/contract-performance/approvals', { query: withOrg(query) }),
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
