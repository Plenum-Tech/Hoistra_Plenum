// api/workOrder — svc-work-order-management, the CAFM work order / asset / PPM engine.
// Routes: apps/backend/.../svc-work-order-management/src/api/routes/{assets,work_orders,
// ppm_scheduler,dashboard}.py, mounted behind the gateway at /backend/work-order/.
//
// Reads only, for now. assetsLive.js draws the Assets page's live asset register from
// assets()+workOrders(); maintenanceLive.js draws the Maintenance page's live decisions grid
// and KPI tiles from workOrders()+dashboardStats().
import { BASES, apiFetch, currentOrgId } from './client.js';

const B = BASES.workOrder;

// The company a superadmin is acting as. Same contract svc-operations-intelligence has
// ("Superadmin only: act as this company"); svc-work-order-management takes it too now, and
// without it a superadmin read every company's rows whatever the header said they were
// viewing — two companies showed identical figures because they were identical.
const withOrg = (q) => { const o = currentOrgId(); return o ? Object.assign({ organization_id: o }, q || {}) : (q || {}); };

export const workOrderApi = {
  // plenum_cafm.assets, portfolio-wide: every asset the caller may see across all their
  // buildings, already narrowed to their allocation. Per asset: name, code, manufacturer,
  // model, serial, status, building_id, category_id AND category_name (resolved server-side
  // by services/asset_catalogue.py, so no second service call to turn a key into a word),
  // location_id, criticality, health_score (float), installation_date.
  // Optional filters: building_id, category_id, criticality, status, q (name/code/serial
  // substring), page. Server caps limit at 200 and returns the pre-paging total in
  // X-Total-Count. Still not on this table: vendor, replacement value, and there is no
  // `section` concept anywhere in the schema (see assetsLive.js).
  //
  // organization_id rides along for the same reason every ops-intelligence read carries it:
  // a superadmin viewing one company must get THAT company here too. Without it this read
  // answered across every company while the buildings register answered for one, and the
  // Assets page had to describe the difference as assets "not in your buildings register" —
  // true, and misleading, because those buildings exist in a company the caller was not
  // looking at. Ignored by the server for anyone who is not a superadmin.
  assets: (query) =>
    apiFetch(B, '/api/assets', { query: withOrg(Object.assign({ limit: 200 }, query || {})) }),
  // The register behind assets.category_id — {category_id, name, description,
  // parent_category_id, asset_count}. Company-wide: the table carries no building column.
  assetCategories: (query) =>
    apiFetch(B, '/api/asset-categories', { query: Object.assign({ limit: 500 }, query || {}) }),
  locations: (query) =>
    apiFetch(B, '/api/locations', { query: withOrg(Object.assign({ limit: 200 }, query || {})) }),
  // plenum_cafm.work_orders, newest first. Note: the response has no estimated_cost field
  // even though the underlying column exists — WorkOrderResponse never exposes it.
  workOrders: (query) =>
    apiFetch(B, '/api/work-orders/', { query: Object.assign({ limit: 100 }, query || {}), timeoutMs: 30000 }),
  activeWorkOrders: () => apiFetch(B, '/api/work-orders/filter/active'),
  pendingApprovalWorkOrders: () => apiFetch(B, '/api/work-orders/filter/pending-approval'),
  workOrderHistory: (workOrderId) => apiFetch(B, '/api/work-orders/' + encodeURIComponent(workOrderId) + '/history'),
  // Aggregate counts: total, by_status, by_priority, by_source, created_today, assets_by_category.
  dashboardStats: () => apiFetch(B, '/api/dashboard/stats'),
  // PPM schedules (plenum_cafm.maintenance_plans) currently due — no vendor/contract/visit
  // history on this table; see MEMORY / the Maintenance page's PPM health note.
  ppmDue: () => apiFetch(B, '/api/ppm/due'),

  // ── Maintenance (/api/maintenance) ──────────────────────────────────────
  // The purpose-built reads behind the Maintenance screen (routes/maintenance.py, docs in
  // docs/api/assets-and-maintenance-api.md and inspection-intelligence-and-ppm-api.md).
  // Every one is building-scoped by the caller's own allocation: no building_id means every
  // building this account may see — the whole company for an admin, the allocation for a
  // user, nothing for a user allocated to nothing. Passing a building_id you are not
  // allocated to is a 403, refused rather than answered empty.

  // The four cards across the top, with the sub-counts printed beneath each.
  maintenanceOverview: (query) => apiFetch(B, '/api/maintenance/overview', { query: withOrg(query) }),
  // The decisions grid. `group_by` (state | source | building | vendor) is done server-side
  // and comes back as `groups`, each with its own counts and total — the page's "Group by"
  // control is that parameter, not a client-side regroup.
  maintenanceDecisions: (query) =>
    apiFetch(B, '/api/maintenance/decisions', { query: withOrg(Object.assign({ limit: 200 }, query || {})), timeoutMs: 30000 }),
  // The inspection-intelligence panel: the corpus header and the four cards. A card this
  // database cannot answer carries `answerable: false` and a reason, never a zero.
  inspectionIntelligence: (query) =>
    apiFetch(B, '/api/maintenance/inspection-intelligence', { query: withOrg(query) }),
  // One card with every row behind it: unconverted-recommendations | corroborated-anomalies
  // | warranted-findings | poorly-graded.
  inspectionIntelligenceCard: (card, query) =>
    apiFetch(B, '/api/maintenance/inspection-intelligence/' + encodeURIComponent(card), { query: withOrg(query) }),
  // When the reports were last re-read. `last_read` is null before the first run — that is
  // "no read has been recorded", not zero.
  lastInspectionRead: () => apiFetch(B, '/api/maintenance/inspection-intelligence/last-read'),
  // PPM health, one row per contract rather than per vendor.
  ppmContracts: (query) =>
    apiFetch(B, '/api/maintenance/ppm/contracts', { query: withOrg(Object.assign({ limit: 200 }, query || {})) }),
  // The individual inspection reports behind the panel.
  maintenanceInspections: (query) =>
    apiFetch(B, '/api/maintenance/inspections', { query: withOrg(Object.assign({ limit: 200 }, query || {})) }),
  // The Ask bar. Answers are composed from rows the engine functions returned — `source`
  // names the endpoint each figure came from — and `understood: false` is a normal 200
  // carrying the list of questions it can answer.
  maintenanceAsk: (question, page, query) =>
    apiFetch(B, '/api/maintenance/ask', { method: 'POST', body: { question: question, page: page || null }, query: withOrg(query), timeoutMs: 30000 }),
  // The chips, served rather than hard-coded: page = maintenance | inspection.
  maintenanceAskSuggestions: (page) =>
    apiFetch(B, '/api/maintenance/ask/suggestions', { query: page ? { page: page } : {} })

  // NOT wired, deliberately: POST /api/maintenance/inspection-intelligence/read. The panel
  // computes live on every read, so the button is not what makes the numbers appear — the
  // POST only INSERTs a row into plenum_cafm.inspection_read_runs to stamp the run. That is
  // a write against the production database, and nothing on this page writes.
};
