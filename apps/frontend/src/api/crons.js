// api/crons — Hoist crons on svc-operations-intelligence (src/api/routes/crons.py).
//
// A cron is one catalogue job (an energy anomaly scan, a compliance expiry scan, a question
// asked of the orchestrator …) on a cadence the service keeps itself. Everyone in the company
// sees the jobs; only an admin schedules, changes, runs or removes one.
import { BASES, currentOrgId, apiFetch } from './client.js';

const B = BASES.opsIntelligence;
const withOrg = (q) => { const o = currentOrgId(); return o ? Object.assign({ organization_id: o }, q || {}) : (q || {}); };

export const cronsApi = {
  catalogue: () => apiFetch(B, '/api/crons/catalogue'),
  // tz: the reader's zone, for the "runs today" tile.
  list: (tz) => apiFetch(B, '/api/crons', { query: withOrg({ runs: 3, tz: tz || 'UTC' }) }),
  // One job's run history, newest first (30 days are kept).
  runs: (id, limit) => apiFetch(B, '/api/crons/' + encodeURIComponent(id) + '/runs', { query: withOrg({ limit: limit || 100 }) }),
  // Each job day by day in the reader's zone: runs, failures, the last result.
  daily: (days, tz, jobId) => apiFetch(B, '/api/crons/daily', { query: withOrg(Object.assign({ days: days || 14, tz: tz || 'UTC' }, jobId ? { job_id: jobId } : {})) }),
  // Who created, changed, paused, ran or removed a job, and when.
  events: (id) => apiFetch(B, '/api/crons/' + encodeURIComponent(id) + '/events', { query: withOrg({}) }),
  // body: {job_keys: [..], refresh, timezone, prompt?, run_now?, source_session_id?}
  create: (body) => apiFetch(B, '/api/crons', {
    method: 'POST', query: withOrg({}), body: Object.assign({}, body, currentOrgId() ? { organization_id: currentOrgId() } : {}),
    timeoutMs: 30000
  }),
  update: (id, body) => apiFetch(B, '/api/crons/' + encodeURIComponent(id), { method: 'PATCH', query: withOrg({}), body: body || {} }),
  remove: (id) => apiFetch(B, '/api/crons/' + encodeURIComponent(id), { method: 'DELETE', query: withOrg({}) }),
  // Waits for the run: a scan answers in seconds, a question in up to a few minutes.
  run: (id) => apiFetch(B, '/api/crons/' + encodeURIComponent(id) + '/run', { method: 'POST', query: withOrg({}), timeoutMs: 240000 })
};
