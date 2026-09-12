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
  // plenum_cafm.assets — name/code/manufacturer/model/serial/status only; no building,
  // section, install date, criticality or vendor on this table (see assetsLive.js).
  assets: (query) =>
    apiFetch(B, '/api/assets', { query: Object.assign({ limit: 200 }, query || {}) }),
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
