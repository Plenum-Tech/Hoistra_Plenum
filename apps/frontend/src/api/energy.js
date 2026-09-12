// api/energy — svc-operations-intelligence's Feature C: the Buildings table, the Hoist
// Graph's building-graph and cost-driver reads, building create/patch/delete, and the reads
// the Energy module uses (anomalies, meters). Routes:
// apps/backend/.../svc-operations-intelligence/src/api/routes/energy.py, mounted at
// /backend/ops-intelligence/.
import { BASES, ORG_ID, apiFetch } from './client.js';

const B = BASES.opsIntelligence;
const enc = encodeURIComponent;
const withOrg = (q) => (ORG_ID ? Object.assign({ organization_id: ORG_ID }, q || {}) : (q || {}));

export const energyApi = {
  // Every site with its energy profile, latest EUI, the benchmark it reads against (and
  // where that benchmark came from), and how its reading arrives — the Buildings table and
  // the Energy module's building list both read this one list. DB-only, no seed fallback on
  // the backend; buildingsLive.js / energyLive.js each have their own live-or-seed fallback.
  listBuildings: (query) => apiFetch(B, '/api/energy/buildings', { query: withOrg(Object.assign({ limit: 5000 }, query || {})), timeoutMs: 30000 }),

  // One building by sites.site_id / site_uuid / building_code.
  getBuilding: (siteId) => apiFetch(B, '/api/energy/buildings/' + enc(siteId), { query: withOrg() }),

  tm46: () => apiFetch(B, '/api/energy/tm46'),
  savedSpaceSummary: () => apiFetch(B, '/api/energy/saved-space/summary', { query: withOrg() }),
  // The unified approvals rail — every Phase 2 source unless source_feature narrows it.
  // Lives under /api/approvals rather than /api/energy, but on the same service.
  approvals: (query) => apiFetch(B, '/api/approvals', { query: withOrg(Object.assign({ limit: 50 }, query || {})) }),

  // Hoist-a-building form: create, or patch an existing building (only the sent fields are
  // touched). Both return the row in the same shape listBuildings() does.
  createBuilding: (body) => apiFetch(B, '/api/energy/buildings', { method: 'POST', body: body, timeoutMs: 20000 }),
  patchBuilding: (buildingId, body) =>
    apiFetch(B, '/api/energy/buildings/' + enc(buildingId), { method: 'PATCH', body: body, timeoutMs: 20000 }),
  // opts: { confirm (default false — a dry-run report), detach (default true), actor }.
  // confirm:false reports what would be touched and changes nothing; confirm:true deletes.
  deleteBuilding: (buildingId, opts) => {
    const o = opts || {};
    return apiFetch(B, '/api/energy/buildings/' + enc(buildingId), {
      method: 'DELETE',
      query: withOrg({ confirm: !!o.confirm, detach: o.detach === undefined ? true : !!o.detach, actor: o.actor }),
      timeoutMs: 20000
    });
  },

  // The Hoist Graph: the schema as this database actually holds it (tables/columns/FKs), the
  // per-table row counts and history, and everything hanging off one building.
  graphShape: () => apiFetch(B, '/api/energy/graph/shape', { timeoutMs: 20000 }),
  graphTables: () => apiFetch(B, '/api/energy/graph/tables', { timeoutMs: 20000 }),
  buildingGraph: (buildingId) => apiFetch(B, '/api/energy/buildings/' + enc(buildingId) + '/graph', { timeoutMs: 20000 }),
  // Which plant is driving spend on this building, ranked by billed-vs-contracted gap.
  costDrivers: (buildingId, limit) =>
    apiFetch(B, '/api/energy/buildings/' + enc(buildingId) + '/cost-drivers', { query: { limit: limit || 25 }, timeoutMs: 20000 }),

  // ── Energy module reads ──────────────────────────────────────────────────
  // Open anomalies, newest first. Rows carry raw site_id / asset_id / meter_id (UUIDs), no
  // resolved names — energyLive.js joins site_id to the buildings list for a building name.
  anomalies: (query) =>
    apiFetch(B, '/api/energy/anomalies', { query: withOrg(Object.assign({ status: 'open', limit: 500 }, query || {})) }),
  // Active meters: the route/type/tariff a site's reading is billed and priced at.
  meters: (query) =>
    apiFetch(B, '/api/energy/meters', { query: withOrg(Object.assign({ limit: 500 }, query || {})) }),

  // The ratings-and-duties tiles for one market, assembled server-side from real records —
  // an EPC's energy_rating, a filing row, a chiller reading, months of consumption.
  ratingsPosition: (countryCode, buildingId) =>
    apiFetch(B, '/api/energy/ratings/position', { query: withOrg({ country_code: countryCode, building_id: buildingId }) }),
  // Every ENERGY STAR / LL97 snapshot on record for a building (or every building the
  // caller may see), newest first per scheme.
  ratings: (query) => apiFetch(B, '/api/energy/ratings', { query: withOrg(query || {}) }),
  // kW/RT over a window against a chiller's design figure — the position, breach or not.
  chillerEfficiency: (assetId, windowDays) =>
    apiFetch(B, '/api/energy/chillers/' + enc(assetId) + '/efficiency', { query: { window_days: windowDays || 14 } })
};
