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

// The company a superadmin has chosen to view as, overriding ORG_ID for the rest of the
// tab's session — null means no override (the caller's own company). This only chooses
// what the client SENDS; every route that reads it enforces server-side
// (access.organization_for) that only a superadmin gets the company they asked for, so
// setting this from a non-superadmin session would just get every request 403'd, not
// grant access. Cleared back to null on sign-out.
let actingOrgId = null;
// Bumped every time the acting company changes. A request is stamped with the value in
// force when it was ISSUED; if that has moved by the time it comes back, the response
// describes the company the caller was looking at before the switch, and handing it to
// the register that asked would repaint the previous company's rows under the new
// company's name. That is exactly how the Buildings table came to show one company's 619
// buildings while the header said another's.
let orgEpoch = 0;
export function setActingOrg(id) { actingOrgId = id ? String(id) : null; orgEpoch += 1; }
// True for the error thrown when a response outlived the company it was issued under.
// Loaders check it to stay quiet: the switch has already started a correctly-scoped read,
// so there is nothing to report and nothing to retry.
export function isStaleScope(e) { return !!(e && e.staleScope); }
export function getActingOrg() { return actingOrgId; }
export function currentOrgId() { return actingOrgId || ORG_ID || ''; }

let hooks = { getToken: () => null, refresh: null, onTerminal: null };
export function configureAuth(h) { hooks = Object.assign({}, hooks, h || {}); }

// The access token itself, for the one caller that cannot go through apiFetch: a browser
// WebSocket constructor takes a URL and a list of subprotocols and nothing else, so the
// streaming route reads the token out of the handshake instead of a header. Everything that
// can send a header should — apiFetch attaches it, refreshes it and retries, and none of
// that is available here.
export function accessToken() { return hooks.getToken(); }

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
  const issuedUnder = orgEpoch;
  const out = await attempt(base, path, o, useAuth ? hooks.getToken() : null, useAuth);
  // Checked AFTER the await, on the way back — the company can change while this is in
  // flight, and this is the one place every register's reads pass through.
  if (issuedUnder !== orgEpoch) {
    const stale = new Error('the company changed while this request was in flight');
    stale.staleScope = true;
    throw stale;
  }
  return out;
}

// One attempt plus, at most, one refreshed retry. The retry comes back through here so a
// terminal 401 on it is reported the same way as on the first try.
//
// `missing_token` retries the same way `expired` does: componentDidMount fires every page's
// load in one breath, and the access token — never persisted, only rebuilt by authBoot()'s
// refresh — is not back from that refresh yet on the very first requests of a session. That
// used to mean every page loaded seed-only on first paint, live backend or not; a request
// that gets no token together is exactly a request that should ask for one and go again.
const RETRY_ONCE_401 = new Set(['expired', 'missing_token']);
async function attempt(base, path, o, token, useAuth) {
  try {
    return await request(base, path, o, token);
  } catch (e) {
    if (!(e instanceof ApiError) || e.status !== 401 || !useAuth) throw e;
    if (RETRY_ONCE_401.has(e.reason) && hooks.refresh && !o._retried) {
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
