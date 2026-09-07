// api/client — one fetch wrapper for every gateway-mounted backend.
//
// The single-URL model serves the UI at `/` and every service under `/backend/<name>/`
// (see infra/single-url/nginx.conf), so the defaults are same-origin paths and the
// browser never needs CORS. `VITE_*` build args override them for other deployments;
// `npm run dev` proxies `/backend` to the running gateway (vite.config.js).
const env = (typeof import.meta !== 'undefined' && import.meta.env) || {};
const trim = (s) => String(s || '').replace(/\/+$/, '');

export const BASES = {
  opsIntelligence: trim(env.VITE_OPS_INTELLIGENCE_BASE_URL || '/backend/ops-intelligence'),
  workOrder: trim(env.VITE_WO_BASE_URL || '/backend/work-order'),
  connector: trim(env.VITE_CONNECTOR_BASE_URL || '/backend/connector'),
  schemaMapper: trim(env.VITE_SCHEMA_MAPPER_BASE_URL || '/backend/schema-mapper'),
  deepAgents: trim(env.VITE_DEEP_AGENTS_BASE_URL || '/backend/deep-agents')
};

// Optional tenant scope. Empty = the backend's default organisation.
export const ORG_ID = String(env.VITE_ORGANIZATION_ID || '').trim();

export class ApiError extends Error {
  constructor(message, status, body) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.body = body;
  }
}

// apiFetch(base, path, { method, query, body, timeoutMs }) → parsed JSON.
// Throws ApiError on non-2xx (message taken from the FastAPI body when present),
// a plain Error on network failure / timeout. Callers decide how to fall back.
export async function apiFetch(base, path, opts) {
  const o = opts || {};
  const url = new URL(base + path, window.location.origin);
  if (o.query) {
    Object.entries(o.query).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== '') url.searchParams.set(k, String(v));
    });
  }
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), o.timeoutMs || 20000);
  try {
    const res = await fetch(url.toString(), {
      method: o.method || 'GET',
      headers: o.body !== undefined ? { 'Content-Type': 'application/json', Accept: 'application/json' } : { Accept: 'application/json' },
      body: o.body !== undefined ? JSON.stringify(o.body) : undefined,
      signal: ctrl.signal
    });
    const text = await res.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch (e) { data = { raw: text }; }
    if (!res.ok) {
      const msg = (data && (data.error || data.detail || data.message)) || (res.status + ' ' + res.statusText);
      throw new ApiError(typeof msg === 'string' ? msg : JSON.stringify(msg), res.status, data);
    }
    return data;
  } catch (e) {
    if (e && e.name === 'AbortError') throw new Error('timed out after ' + Math.round((o.timeoutMs || 20000) / 1000) + 's');
    throw e;
  } finally {
    clearTimeout(timer);
  }
}
