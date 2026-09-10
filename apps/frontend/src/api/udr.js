// api/udr — svc-udr, the Unified Data Repository: schema introspection and read access to
// every table in the plenum_cafm schema. Routes: apps/backend/.../svc-udr/src/api/routes/tables.py,
// mounted behind the gateway at /backend/udr/.
//
// Reads only. The Buildings page draws the Hoist Graph from these — which tables exist, how
// they key into each other, and how many rows hang off a building. `select` is the service's
// SELECT-only endpoint (anything else is rejected server-side); the statements this UI sends
// are built in src/logic/graphLive.js from information_schema and validated identifiers.
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
