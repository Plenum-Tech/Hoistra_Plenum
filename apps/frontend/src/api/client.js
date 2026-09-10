// api/client — one fetch wrapper for every gateway-mounted backend.
//
// The single-URL model serves the UI at `/` and every service under `/backend/<name>/`
// (see infra/single-url/nginx.conf), so the defaults are same-origin paths and the
// browser never needs CORS. `VITE_*` build args override them for other deployments;
// `npm run dev` proxies `/backend` to the running gateway (vite.config.js).
//
// Auth. Once the controller has installed its hooks (configureAuth, from logic/auth.js)
// every request carries `Authorization: Bearer <access token>` — on every base, so the day
// a service starts enforcing it nothing here changes. A 401 whose reason is `expired` is
// refreshed once and retried once; the terminal reasons (session revoked, password changed,
// a replayed refresh token …) are reported through onTerminal so the shell can sign out.
// The auth endpoints themselves pass `auth: false`: no header and no interception, so a
// failing /login or /refresh can never trigger a refresh of its own.
const env = (typeof import.meta !== 'undefined' && import.meta.env) || {};
const trim = (s) => String(s || '').replace(/\/+$/, '');

export const BASES = {
  opsIntelligence: trim(env.VITE_OPS_INTELLIGENCE_BASE_URL || '/backend/ops-intelligence'),
  workOrder: trim(env.VITE_WO_BASE_URL || '/backend/work-order'),
  connector: trim(env.VITE_CONNECTOR_BASE_URL || '/backend/connector'),
  schemaMapper: trim(env.VITE_SCHEMA_MAPPER_BASE_URL || '/backend/schema-mapper'),
  deepAgents: trim(env.VITE_DEEP_AGENTS_BASE_URL || '/backend/deep-agents'),
  // svc-udr — schema introspection and read access to every plenum_cafm table.
  udr: trim(env.VITE_UDR_BASE_URL || '/backend/udr'),
  // doc-rag (served by svc-ai-schema-mapper under /doc-rag) — the ingested document index.
  docRag: trim(env.VITE_DOC_RAG_BASE_URL || '/backend/doc-rag')
};

// Optional tenant scope. Empty = the backend's default organisation.
export const ORG_ID = String(env.VITE_ORGANIZATION_ID || '').trim();

let hooks = { getToken: () => null, refresh: null, onTerminal: null };
export function configureAuth(h) { hooks = Object.assign({}, hooks, h || {}); }

// 401 reasons after which the credential is finished. Only `expired` is worth a retry.
export const TERMINAL_401 = new Set(['invalid', 'wrong_type', 'session_revoked', 'password_changed', 'replayed', 'no_account', 'disabled']);

// The message a FastAPI failure body carries. The auth router nests {ok, error, reason}
// under `detail`; a 422 puts an array of field errors there; older routers use a string.
export function errorMessage(data, res) {
  const d = data && data.detail;
  if (d && typeof d === 'object' && !Array.isArray(d)) {
    if (typeof d.error === 'string') return d.error;
    if (typeof d.message === 'string') return d.message;
    return JSON.stringify(d);
  }
  if (Array.isArray(d)) {
    const parts = d.map((x) => (x && typeof x.msg === 'string' ? x.msg : '')).filter(Boolean);
    if (parts.length) return parts.join('; ');
  }
  const msg = data && (data.error || data.detail || data.message);
  if (typeof msg === 'string' && msg) return msg;
  return res.status + ' ' + res.statusText;
}

export class ApiError extends Error {
  constructor(message, status, body) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.body = body;
    const d = body && body.detail;
    this.reason = d && typeof d === 'object' && !Array.isArray(d) && typeof d.reason === 'string' ? d.reason : '';
  }
}

// apiFetch(base, path, { method, query, body, form, timeoutMs, signal, auth }) → parsed JSON.
// Throws ApiError on non-2xx (message taken from the FastAPI body when present),
// a plain Error on network failure / timeout. Callers decide how to fall back.
//
// `form` sends a FormData as multipart — the browser sets the boundary, so no
// Content-Type is provided. `signal` lets a caller cancel in flight (the chat's stop
// button); it is separate from the timeout, and cancelling that way is reported as a
// cancellation rather than a timeout. `auth: false` sends no bearer and skips the 401
// interceptor.
export async function apiFetch(base, path, opts) {
  const o = opts || {};
  const useAuth = o.auth !== false;
  return attempt(base, path, o, useAuth ? hooks.getToken() : null, useAuth);
}

// One attempt plus, at most, one refreshed retry. The retry comes back through here so a
// terminal 401 on it is reported the same way as on the first try.
async function attempt(base, path, o, token, useAuth) {
  try {
    return await request(base, path, o, token);
  } catch (e) {
    if (!(e instanceof ApiError) || e.status !== 401 || !useAuth) throw e;
    if (e.reason === 'expired' && hooks.refresh && !o._retried) {
      let fresh;
      try { fresh = await hooks.refresh(); } catch (e2) { throw e; }
      return attempt(base, path, Object.assign({}, o, { _retried: true }), fresh, useAuth);
    }
    if (TERMINAL_401.has(e.reason) && hooks.onTerminal) hooks.onTerminal(e.reason, e.message);
    throw e;
  }
}

async function request(base, path, o, token) {
  const url = new URL(base + path, window.location.origin);
  if (o.query) {
    Object.entries(o.query).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== '') url.searchParams.set(k, String(v));
    });
  }
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), o.timeoutMs || 20000);
  let cancelled = false;
  const onAbort = () => { cancelled = true; ctrl.abort(); };
  if (o.signal) {
    if (o.signal.aborted) onAbort();
    else o.signal.addEventListener('abort', onAbort);
  }
  try {
    const hasBody = o.body !== undefined;
    const headers = o.form ? { Accept: 'application/json' }
      : hasBody ? { 'Content-Type': 'application/json', Accept: 'application/json' }
      : { Accept: 'application/json' };
    if (token) headers.Authorization = 'Bearer ' + token;
    const res = await fetch(url.toString(), {
      method: o.method || 'GET',
      headers,
      body: o.form ? o.form : (hasBody ? JSON.stringify(o.body) : undefined),
      signal: ctrl.signal
    });
    const text = await res.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch (e) { data = { raw: text }; }
    if (!res.ok) throw new ApiError(errorMessage(data, res), res.status, data);
    return data;
  } catch (e) {
    if (e && e.name === 'AbortError') {
      if (cancelled) {
        const err = new Error('cancelled');
        err.cancelled = true;
        throw err;
      }
      throw new Error('timed out after ' + Math.round((o.timeoutMs || 20000) / 1000) + 's');
    }
    throw e;
  } finally {
    clearTimeout(timer);
    if (o.signal) o.signal.removeEventListener('abort', onAbort);
  }
}
