// api/energy — svc-operations-intelligence's Feature C: the Buildings table, the Hoist
// Graph's building-graph and cost-driver reads, building create/patch/delete, and the reads
// the Energy module uses (anomalies, meters). Routes:
// apps/backend/.../svc-operations-intelligence/src/api/routes/energy.py, mounted at
// /backend/ops-intelligence/.
import { BASES, currentOrgId, apiFetch } from './client.js';

const B = BASES.opsIntelligence;
const enc = encodeURIComponent;
// currentOrgId() is ORG_ID (the build's default tenant) unless a superadmin is viewing
// as another company, in which case that company's id takes over for every read here —
// including listBuildings(), now that /api/energy/buildings accepts the override too.
const withOrg = (q) => { const o = currentOrgId(); return o ? Object.assign({ organization_id: o }, q || {}) : (q || {}); };

export const energyApi = {
  // Every site with its energy profile, latest EUI, the benchmark it reads against (and
  // where that benchmark came from), and how its reading arrives — the Buildings table and
  // the Energy module's building list both read this one list. DB-only, no seed fallback on
  // the backend; buildingsLive.js / energyLive.js each have their own live-or-seed fallback.
  listBuildings: (query) => apiFetch(B, '/api/energy/buildings', { query: withOrg(Object.assign({ limit: 5000 }, query || {})), timeoutMs: 30000 }),

  // The portfolio Hoist Score: of the buildings the caller may see, how many has each kind
  // of record reached — asset register, certificate, contract, meter, work order — with the
  // buildings still missing each one named. The Home tile's one read (logic/homeLive.js);
  // engines/energy/hoist_score.py says what a bar means.
  hoistScore: () => apiFetch(B, '/api/energy/hoist-score', { query: withOrg(), timeoutMs: 30000 }),

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

  // A read-only preview of the code createBuilding would allocate for this org, country,
  // region and use — GET /buildings/next-code calls the exact same allocator create_building
  // itself does, so this can never drift from what actually gets stored. withOrg() so a
  // superadmin previewing while viewing as another company sees THAT company's code, same
  // as every other read here.
  previewBuildingCode: (query) => apiFetch(B, '/api/energy/buildings/next-code', { query: withOrg(query || {}), timeoutMs: 8000 }),
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

  // Remove a document and everything read out of it — the certificate on Compliance, the
  // contract terms and invoice lines on Vendors, the chunks the assistant answers from.
  // The opposite of deleteBuilding, which detaches its children and keeps them: an extract
  // from a document that should not have been ingested is not a record of anything.
  //
  // Same two-phase shape. confirm:false changes nothing and reports what it would remove,
  // which is what the dialog reads out; confirm:true deletes, and nothing comes back.
  // The original file is kept in blob storage either way.
  deleteDocument: (documentId, opts) => {
    const o = opts || {};
    return apiFetch(B, '/api/energy/documents/' + enc(documentId), {
      method: 'DELETE',
      query: withOrg({ confirm: !!o.confirm, actor: o.actor }),
      timeoutMs: 30000
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
  // Every work order billed against one asset, with billed vs. over-contract amounts.
  // Billed and over-contract are different numbers and are never added together.
  assetWorkHistory: (assetId) =>
    apiFetch(B, '/api/energy/assets/' + enc(assetId) + '/work-history', { timeoutMs: 20000 }),

  // ── Energy module reads ──────────────────────────────────────────────────
  // Open anomalies, newest first. Rows carry raw site_id / asset_id / meter_id (UUIDs), no
  // resolved names — energyLive.js joins site_id to the buildings list for a building name.
  anomalies: (query) =>
    apiFetch(B, '/api/energy/anomalies', { query: withOrg(Object.assign({ status: 'open', limit: 500 }, query || {})) }),
  // Active meters: the route/type/tariff a site's reading is billed and priced at.
  meters: (query) =>
    apiFetch(B, '/api/energy/meters', { query: withOrg(Object.assign({ limit: 500 }, query || {})) }),
  // Sub-meters placed on the floor their section sits on: each meter's kWh over a trailing
  // window, its cost at the contracted rate, its share of the incoming supply, and how much
  // of the main meter the floors account for between them (engines/energy/floor_meters.py).
  metersByFloor: (query) =>
    apiFetch(B, '/api/energy/meters/by-floor', { query: withOrg(Object.assign({ days: 30 }, query || {})), timeoutMs: 30000 }),

  // The ratings-and-duties tiles for one market, assembled server-side from real records —
  // an EPC's energy_rating, a filing row, a chiller reading, months of consumption.
  ratingsPosition: (countryCode, buildingId) =>
    apiFetch(B, '/api/energy/ratings/position', { query: withOrg({ country_code: countryCode, building_id: buildingId }) }),
  // Every ENERGY STAR / LL97 snapshot on record for a building (or every building the
  // caller may see), newest first per scheme.
  ratings: (query) => apiFetch(B, '/api/energy/ratings', { query: withOrg(query || {}) }),
  // The market-profile table: benchmark, data source and commercial terms per market, each
  // cell labelled reference / measured / derived. The reference EUI is each market's own
  // benchmark rule applied to the buildings in scope, not a typed constant.
  marketProfiles: (markets) =>
    apiFetch(B, '/api/energy/market-profiles', { query: withOrg(markets ? { markets: markets.join(',') } : {}), timeoutMs: 20000 }),
  // ── Asset intelligence (docs/api/asset-intelligence-api.md) ──────────────
  // Sub-metered zones with their OWN reference: a server room read against an office
  // benchmark looks like a catastrophe and a car park like a triumph. eui_kwh_per_m2 is
  // null (never 0) when no sub-meter is attached or the area is unknown — `measured: false`
  // means "not metered", which is a different claim from "consumed nothing".
  sections: (buildingId) =>
    apiFetch(B, '/api/energy/sections', { query: withOrg(buildingId ? { building_id: buildingId } : {}), timeoutMs: 20000 }),
  // The headline figure and the assets behind it. assets_not_computable counts assets
  // missing a replacement value, design life or install date; they are excluded, never
  // added as zero, so the page must show that count beside the total.
  assetValueAtRisk: (buildingId) =>
    apiFetch(B, '/api/energy/assets/value-at-risk', { query: withOrg(buildingId ? { building_id: buildingId } : {}), timeoutMs: 20000 }),
  // Findings grouped by building and meter, with a headline that is NOT their sum.
  // Several detectors read one meter over one period and each prices the whole excess it
  // can see — a weekend IS an unoccupied hour, so the weekend rule's kWh are already inside
  // the non-occupancy rule's. Adding them is how a building 3% UNDER its benchmark came to
  // display two gigawatt-hours of waste. `headline` is the largest single finding, `if_added`
  // is what summing would have given, and every rule keeps its own figure and the sentence
  // that defines it.
  anomalyRollup: (query) =>
    apiFetch(B, '/api/energy/anomalies/rollup', {
      query: withOrg(Object.assign({ status: 'open' }, query || {})), timeoutMs: 20000 }),

  // Run the detection rules over every active meter in the company. `historyDays` sweeps
  // that much history instead of only the newest readings: each rule reads a 35-day window,
  // so a year ingested and scanned once reports on its final month alone. Idempotent by
  // window — re-running re-detects the same events and writes nothing.
  //
  // Minutes, not seconds: a year is ~49 windows per meter, and the timeout says so.
  scanAll: (historyDays) =>
    apiFetch(B, '/api/energy/anomalies/scan-all', {
      method: 'POST',
      query: withOrg(historyDays ? { history_days: historyDays } : {}),
      timeoutMs: 300000,
    }),

  // ── Condition engine (docs/api/condition-engine-api.md) ─────────────────
  // Threat / Watch / In control, decided on the server from the section's deviation and the
  // anomalies attributed to the asset, at thresholds held per organisation in
  // asset_condition_rules. Do not recompute this in the page: the thresholds are editable
  // through PUT /api/energy/condition/rules and a browser copy cannot see them, and the
  // section deviation here is the same number the section headers are drawn from.
  //
  // Each row also carries section_id — which AssetResponse does not — so this is the only
  // bulk read that says which section an asset is in.
  conditionAssets: (query) =>
    apiFetch(B, '/api/energy/condition/assets', {
      query: withOrg(Object.assign({ limit: 2000 }, query || {})), timeoutMs: 20000 }),
  // The two steppers at the top of the Assets page, as the ORGANISATION holds them in
  // plenum_cafm.asset_condition_rules — not as this build ships them. `is_default` is true
  // until somebody sets them, and the same object rides along on every conditionAssets()
  // answer under `rules`, so the page normally learns them without a second call.
  conditionRules: () => apiFetch(B, '/api/energy/condition/rules', { query: withOrg() }),
  // Move them. A live write, and the only one this page makes: it changes the rule every
  // future read of /condition/assets and /condition/summary is banded by, for everyone in
  // the company — so the caller re-reads the bands afterwards rather than re-deciding them
  // in the browser. Both thresholds are sent every time; the route takes the pair.
  setConditionRules: (body) =>
    apiFetch(B, '/api/energy/condition/rules', {
      method: 'PUT', query: withOrg(), body: body, timeoutMs: 20000 }),

  // The band counts, the building and section rollups, and the last scan's stamp.
  // summary.section_not_measured counts assets banded on ONE signal because their section
  // has no sub-meter; they are not "checked and clean" and should not read as such.
  conditionSummary: (buildingId) =>
    apiFetch(B, '/api/energy/condition/summary', {
      query: withOrg(buildingId ? { building_id: buildingId } : {}), timeoutMs: 20000 }),

  // One asset: section, vendor, open anomalies, the value-at-risk arithmetic with its
  // `basis` in words, banded readings, and the failure assessment. 404 for an asset outside
  // your buildings — the same answer as one that does not exist.
  assetIntelligence: (assetId) =>
    apiFetch(B, '/api/energy/assets/' + enc(assetId) + '/intelligence', { timeoutMs: 20000 }),

  // kW/RT over a window against a chiller's design figure — the position, breach or not.
  chillerEfficiency: (assetId, windowDays) =>
    apiFetch(B, '/api/energy/chillers/' + enc(assetId) + '/efficiency', { query: { window_days: windowDays || 14 } })
};
