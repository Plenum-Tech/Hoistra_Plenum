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

  tm46: () => apiFetch(B, '/api/energy/tm46'),
  savedSpaceSummary: () => apiFetch(B, '/api/energy/saved-space/summary', { query: withOrg() })
};
