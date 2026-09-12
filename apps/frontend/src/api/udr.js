// api/udr — svc-udr, the Unified Data Repository: schema introspection and read access to
// every table in the plenum_cafm schema. Routes: apps/backend/.../svc-udr/src/api/routes/tables.py,
// mounted behind the gateway at /backend/udr/.
//
// Reads only. `select` is the service's SELECT-only endpoint (anything else is rejected
// server-side); today its one caller is energyLive.js's enResolveEquipment, a one-off lookup
// of plenum_cafm.equipment rows by uuid. The Hoist Graph moved off this module — it now reads
// counts from ops-intelligence's /api/energy/graph/* routes (src/api/energy.js, read by
// src/logic/graphLive.js), not from svc-udr. `tables`, `schema` and `records` wrap the service's
// introspection/read endpoints one-to-one but have no caller yet; kept for the next thing that
// needs generic table access rather than a hand-built SELECT.
import { BASES, apiFetch } from './client.js';

const B = BASES.udr;
const enc = encodeURIComponent;

export const udrApi = {
  // Every base table in plenum_cafm with a live row estimate (pg_stat n_live_tup).
  tables: () => apiFetch(B, '/api/tables/'),
  // Columns (name, type, nullable), primary keys and foreign keys of one table.
  schema: (table) => apiFetch(B, '/api/tables/' + enc(table) + '/schema'),
  // A page of rows plus the exact total. The service caps `limit` at MAX_QUERY_ROWS (500).
  records: (table, query) =>
    apiFetch(B, '/api/tables/' + enc(table) + '/records', { query: Object.assign({ limit: 50, offset: 0 }, query || {}), timeoutMs: 60000 }),
  // A single SELECT with :named parameters → { rows, count }.
  select: (sql, params) =>
    apiFetch(B, '/api/tables/query/select', { method: 'POST', body: { sql: sql, params: params || {} }, timeoutMs: 90000 })
};
