// api/energy — svc-operations-intelligence, Feature C (Energy Intelligence).
// Routes: apps/backend/.../svc-operations-intelligence/src/api/routes/energy.py
import { BASES, ORG_ID, apiFetch } from './client.js';

const B = BASES.opsIntelligence;
const withOrg = (q) => (ORG_ID ? Object.assign({ organization_id: ORG_ID }, q || {}) : (q || {}));

export const energyApi = {
  // The building table: every site with its energy profile, latest EUI, the benchmark it
  // reads against (standard + legal standing + where the number came from), metering
  // route/granularity and a record-completeness figure.
  listBuildings: (query) =>
    apiFetch(B, '/api/energy/buildings', { query: withOrg(Object.assign({ limit: 500 }, query || {})) }),

  // One building by building_id (also matches site_id or building_code).
  getBuilding: (id) =>
    apiFetch(B, '/api/energy/buildings/' + encodeURIComponent(id), { query: withOrg() }),

  // Create one. 201 returns the row in the same shape listBuildings uses, so the table can
  // refresh from the response. 400 carries field-keyed errors; 409 means the code is taken.
  createBuilding: (body) =>
    apiFetch(B, '/api/energy/buildings', {
      method: 'POST', timeoutMs: 20000,
      body: Object.assign({ source: 'hoistra-ui' }, ORG_ID ? { organization_id: ORG_ID } : {}, body || {})
    }),

  // Without confirm this changes nothing and reports what it would touch — that report is
  // what the confirmation dialog shows. Attached records are detached, never deleted.
  deleteBuilding: (id, opts) =>
    apiFetch(B, '/api/energy/buildings/' + encodeURIComponent(id), {
      method: 'DELETE', timeoutMs: 20000,
      query: withOrg({ confirm: !!(opts && opts.confirm), detach: (opts && opts.detach) !== false })
    }),

  tm46: () => apiFetch(B, '/api/energy/tm46'),
  savedSpaceSummary: () => apiFetch(B, '/api/energy/saved-space/summary', { query: withOrg() })
};
