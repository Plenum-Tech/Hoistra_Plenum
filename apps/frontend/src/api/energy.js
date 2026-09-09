// api/energy — svc-operations-intelligence, Feature C (Energy Intelligence).
// Routes: apps/backend/.../svc-operations-intelligence/src/api/routes/energy.py
import { BASES, ORG_ID, apiFetch } from './client.js';

const B = BASES.opsIntelligence;
const withOrg = (q) => (ORG_ID ? Object.assign({ organization_id: ORG_ID }, q || {}) : (q || {}));

export const energyApi = {
  // The building table: every site with its energy profile, latest EUI, the benchmark it
  // reads against (standard + legal standing + where the number came from), metering
  // route/granularity and a record-completeness figure.
  // The route accepts up to 5000; the sites table holds more than 500 rows, so ask for all.
  listBuildings: (query) =>
    apiFetch(B, '/api/energy/buildings', { query: withOrg(Object.assign({ limit: 5000 }, query || {})), timeoutMs: 60000 }),

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

  // Edit one. Only the fields in the body are touched — an omitted field is left alone, an
  // empty string clears an optional one. 200 returns { changed, relocated, warnings, building }
  // in the same shape listBuildings uses. 400 field-keyed (including a field the service will
  // not let this route touch, refused BY NAME rather than silently dropped); 409 when the
  // building_code sent is taken by another row, or `expected_updated_at` no longer matches —
  // the row moved since it was read, and the body then also carries `current_updated_at`.
  // Every allowed key is one PatchBuildingRequest declares; there is no organization_id here —
  // sending one would come back as "Not editable here." rather than scoping the request.
  patchBuilding: (id, body) =>
    apiFetch(B, '/api/energy/buildings/' + encodeURIComponent(id), {
      method: 'PATCH', timeoutMs: 20000, body: body || {}
    }),

  // Without confirm this changes nothing and reports what it would touch — that report is
  // what the confirmation dialog shows. Attached records are detached, never deleted.
  deleteBuilding: (id, opts) =>
    apiFetch(B, '/api/energy/buildings/' + encodeURIComponent(id), {
      method: 'DELETE', timeoutMs: 20000,
      query: withOrg({ confirm: !!(opts && opts.confirm), detach: (opts && opts.detach) !== false, actor: 'hoistra-ui' })
    }),

  // Spend on this building ranked by how far over contract each asset has run — not by billed
  // total, which just names the biggest asset. Spend whose work order names no asset comes
  // back separately as `unattributed`: real money, in the building total, but not blamed on
  // any one piece of plant.
  costDrivers: (id, limit) =>
    apiFetch(B, '/api/energy/buildings/' + encodeURIComponent(id) + '/cost-drivers', { query: { limit: limit || 25 } }),

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

  // The unified approvals rail — every Phase 2 source unless source_feature narrows it.
  // Lives under /api/approvals rather than /api/energy, but on the same service.
  approvals: (query) =>
    apiFetch(B, '/api/approvals', { query: withOrg(Object.assign({ limit: 50 }, query || {})) }),

  tm46: () => apiFetch(B, '/api/energy/tm46'),
  savedSpaceSummary: () => apiFetch(B, '/api/energy/saved-space/summary', { query: withOrg() })
};
