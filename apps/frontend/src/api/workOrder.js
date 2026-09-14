// api/workOrder — svc-work-order-management, the CAFM work order / asset / PPM engine.
// Routes: apps/backend/.../svc-work-order-management/src/api/routes/{assets,work_orders,
// ppm_scheduler,dashboard}.py, mounted behind the gateway at /backend/work-order/.
//
// Reads only, for now. assetsLive.js draws the Assets page's live asset register from
// assets()+workOrders(); maintenanceLive.js draws the Maintenance page's live decisions grid
// and KPI tiles from workOrders()+dashboardStats().
import { BASES, apiFetch } from './client.js';

const B = BASES.workOrder;

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
  assets: (query) =>
    apiFetch(B, '/api/assets', { query: Object.assign({ limit: 200 }, query || {}) }),
  // The register behind assets.category_id — {category_id, name, description,
  // parent_category_id, asset_count}. Company-wide: the table carries no building column.
  assetCategories: (query) =>
    apiFetch(B, '/api/asset-categories', { query: Object.assign({ limit: 500 }, query || {}) }),
  locations: (query) =>
    apiFetch(B, '/api/locations', { query: Object.assign({ limit: 200 }, query || {}) }),
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
  ppmDue: () => apiFetch(B, '/api/ppm/due')
};
