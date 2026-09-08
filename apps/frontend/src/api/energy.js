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

  // Everything hanging off one building, nested as the graph is. Separate from the table
  // on purpose: forty buildings do not need four hundred child rows to draw.
  buildingGraph: (id) =>
    apiFetch(B, '/api/energy/buildings/' + encodeURIComponent(id) + '/graph'),

  // The graph as the database actually holds it. The Hoist Graph panel is a picture of the
  // schema, and a picture that does not read the schema goes stale the first time a
  // migration runs.
  graphShape: () => apiFetch(B, '/api/energy/graph/shape'),

  // Every graph table with its real row count and the counts behind it. The export panel
  // showed a formula's output and four fixed percentages of it called "builds"; these are
  // counted, and the historical figures are read from created_at rather than a snapshot
  // nobody stores.
  graphTables: () => apiFetch(B, '/api/energy/graph/tables'),

  tm46: () => apiFetch(B, '/api/energy/tm46'),
  savedSpaceSummary: () => apiFetch(B, '/api/energy/saved-space/summary', { query: withOrg() })
};
