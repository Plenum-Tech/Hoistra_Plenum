// api — Trace Lab's requests. Its service is mounted behind the gateway at /backend/trace-lab (nginx
// attachment A5; the Vite dev proxy already forwards every /backend path), and every route sits under
// /api/trace-lab there. Every call goes through api/client.js's apiFetch, so the bearer, the refresh-once
// retry and the stale-company check are the app's own. The company is named only for a superadmin's
// "View as": getActingOrg(), re-implemented here as orgParams() because the assistant client's own helper
// is not exported. A caller can never name a company itself.
import { apiFetch, getActingOrg } from '../api/client.js';

export { isStaleScope } from '../api/client.js';

export const BASE = '/backend/trace-lab';
export const PREFIX = '/api/trace-lab';

export function orgParams() {
  const org = getActingOrg();
  return org ? { organization_id: org } : {};
}

function query(params) {
  const q = Object.assign({}, params || {});
  delete q.organization_id;
  return Object.assign(q, orgParams());
}

// `path` is relative to /api/trace-lab, for example tl.get('/conversations', { run: 'live' }).
export const tl = {
  get: (path, params, opts) => apiFetch(BASE, PREFIX + path, Object.assign({ timeoutMs: 20000 }, opts || {}, { query: query(params) })),
  post: (path, body, params, opts) => apiFetch(BASE, PREFIX + path,
    Object.assign({ timeoutMs: 20000 }, opts || {}, { method: 'POST', body: body === undefined ? {} : body, query: query(params) }))
};
