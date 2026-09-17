# Frontend Auth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the Hoistra frontend's gate and account menu to the real `/api/auth` service — sign in, create an account and confirm it with a six-digit code, forgotten/reset/changed passwords, sign out of one or every session — with bearer tokens attached to every backend call and refreshed correctly, and the real role deciding who sees Admin view.

**Architecture:** A controller mixin (`logic/auth.js`) mixed into `HoistraLogic` like every other domain, a thin `api/auth.js` with one function per endpoint, and bearer + 401-interceptor plumbing in `api/client.js`. Auth state lives in the one store; `GatePanel.jsx` (the gate's right column) and the account menu read `vals` like everything else. Tests run the real controller in Node against a mocked `fetch`, exactly as `test/buildingsCrud.test.mjs` does.

**Tech Stack:** React 18 (inline styles, no router), Vite 5, `node:test` + `node:assert/strict`, Phosphor icons (`ph ph-*`), plain `fetch`.

**Spec:** `docs/superpowers/specs/2026-09-09-frontend-auth-design.md` — read it first; every table and reason code below comes from it.

## Global Constraints

- **Working directory for every command:** `apps/frontend` (i.e. `/Users/hussain/Desktop/hoist/Hoistra_Plenum/apps/frontend`).
- **Node 24 is required** (the shell default is Node 16 and both Vite and the `test/**` glob fail on it). Start every shell with: `export PATH="$HOME/.nvm/versions/node/v24.18.1/bin:$PATH"` and confirm with `node -v` → `v24.18.1`.
- **Run tests with:** `npm test` (all) or `node --test test/<file>.test.mjs` (one file). Every existing suite must stay green.
- **No git commits, stashes, resets or pushes.** Hussain commits this repo himself. Every task ends with passing tests, not a commit. Leave all changes in the working tree.
- **No request may reach the real backend.** This stack's `localhost:3000` gateway points at the **production** database and `register` / `login` / `refresh` are writes. Tests mock `fetch`; the browser pass in Task 11 proxies to a throwaway mock on `127.0.0.1:3999`. Do not run migrations. Do not create or sign in accounts for real.
- **Existing UI is not changed beyond the edits listed.** `Gate.jsx`'s left marketing column and its animations stay byte-for-byte; the TopBar layout stays; the account menu gains rows through the existing data-driven `acctItems` loop.
- **Server messages are shown verbatim** (`detail.error`). The only substitution is 422 → `Enter a valid email address and password.` (gate) / `Enter your current password and a new one.` (modal). Network failure → `Couldn't reach the sign-in service. Check the backend is running.`
- **Copy style:** typographic quotes and em dashes as the existing copy does; button labels in sentence case ("Create account", "Send reset code").
- **Comments** only where the *why* is non-obvious, one short line; never narrate what the code does.
- Auth endpoint paths are `BASES.opsIntelligence` (`/backend/ops-intelligence`) + `/api/auth/<route>`. In tests the mocked pathname is therefore `/backend/ops-intelligence/api/auth/<route>`.

---

## File map

| File | Responsibility |
|---|---|
| `src/api/client.js` (modify) | Bearer header, `auth:false` opt-out, `ApiError.reason`, readable `ApiError.message`, 401 interceptor (`expired` → refresh → retry once; terminal → `onTerminal`). |
| `src/api/auth.js` (create) | `authApi` — one function per `/api/auth` endpoint. No state. |
| `src/logic/session.js` (modify) | Persist `refreshToken` + validated `account`; `signedIn` restores only with a refresh token; export `SESSION_KEY`. |
| `src/logic/auth.js` (create) | `AUTH_DEFAULTS`, `authMethods` (boot, refresh single-flight, every gate action, sign-out, change-password, countdown, `authVals`). |
| `src/logic/HoistraLogic.js` (modify) | Spread defaults, mix in methods, clamp restored role. |
| `src/logic/core.js` (modify) | `authBoot()` first in mount, `authStop()` in unmount, Escape closes the modal. |
| `src/logic/renderVals.js` (modify) | Old gate/account block → `...this.authVals(s)`; the animated chips no longer fake a sign-in. |
| `src/components/shell/GatePanel.jsx` (create) | The gate's right column: five modes. |
| `src/screens/Gate.jsx` (modify) | Right column renders `<GatePanel vals={vals} />`. |
| `src/components/shell/TopBar.jsx` (modify) | Real initial, name, email. |
| `src/components/shell/PasswordModal.jsx` (create) | Change-password dialog. |
| `src/App.jsx` (modify) | Render the modal. |
| `test/client.test.mjs`, `test/session.test.mjs`, `test/auth.test.mjs` (create) | Node tests. |
| `docs/shell-and-shared-components.md` (modify) | §4.1, §4.2. |

---

### Task 1: `api/client.js` — bearer header, readable errors, 401 interceptor

**Files:**
- Modify: `src/api/client.js` (whole file — shown below)
- Test: `test/client.test.mjs`

**Interfaces:**
- Consumes: nothing new.
- Produces: `configureAuth({ getToken, refresh, onTerminal })`, `TERMINAL_401` (Set), `errorMessage(data, res)`, `ApiError` with `.status .body .reason .message`, `apiFetch(base, path, { …existing, auth?: boolean })`. `refresh()` must resolve to the **new access token string**; `onTerminal(reason, message)` is fire-and-forget.

- [ ] **Step 1: Write the failing test**

Create `test/client.test.mjs`:

```js
// api/client — the bearer header, the readable error message, and the 401 interceptor.
// fetch is mocked per METHOD + path; the auth hooks are plain functions so the test can
// count refreshes and terminal reports without a controller.
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = { location: { origin: 'http://test.local' } };

let calls, handlers;
globalThis.fetch = async (url, opts) => {
  const u = new URL(String(url));
  const method = (opts && opts.method) || 'GET';
  calls.push({ method, path: u.pathname, headers: (opts && opts.headers) || {} });
  const h = handlers[method + ' ' + u.pathname];
  if (!h) throw new TypeError('Failed to fetch: no handler for ' + method + ' ' + u.pathname);
  const [status, body] = typeof h === 'function' ? h(u, opts) : h;
  return { ok: status >= 200 && status < 300, status, statusText: String(status), text: async () => JSON.stringify(body) };
};

const { apiFetch, ApiError, configureAuth, errorMessage, TERMINAL_401 } = await import('../src/api/client.js');

const B = '/backend/ops-intelligence';
const fail = (status, reason, error) => [status, { detail: { ok: false, error, reason } }];

beforeEach(() => {
  calls = []; handlers = {};
  configureAuth({ getToken: () => null, refresh: null, onTerminal: null });
});

test('ApiError carries the reason and shows detail.error, not the JSON of the object', async () => {
  handlers['GET ' + B + '/api/x'] = fail(401, 'invalid_credentials', 'That email address and password do not match an account.');
  await assert.rejects(apiFetch(B, '/api/x', { auth: false }), (e) => {
    assert.ok(e instanceof ApiError);
    assert.equal(e.status, 401);
    assert.equal(e.reason, 'invalid_credentials');
    assert.equal(e.message, 'That email address and password do not match an account.');
    return true;
  });
});

test('a 422 array becomes the joined field messages; a flat string detail is unchanged', () => {
  const res = { status: 422, statusText: 'Unprocessable' };
  assert.equal(errorMessage({ detail: [{ msg: 'value is not a valid email address' }, { msg: 'field required' }] }, res),
    'value is not a valid email address; field required');
  assert.equal(errorMessage({ detail: 'Not found' }, { status: 404, statusText: 'Not Found' }), 'Not found');
  assert.equal(errorMessage(null, { status: 500, statusText: 'Server Error' }), '500 Server Error');
});

test('the bearer header is attached when a token exists, and never with auth:false', async () => {
  configureAuth({ getToken: () => 'acc-1' });
  handlers['GET ' + B + '/api/a'] = [200, { ok: true }];
  handlers['GET ' + B + '/api/auth/config'] = [200, { ok: true }];
  await apiFetch(B, '/api/a');
  await apiFetch(B, '/api/auth/config', { auth: false });
  assert.equal(calls[0].headers.Authorization, 'Bearer acc-1');
  assert.equal(calls[1].headers.Authorization, undefined);
});

test('401 expired → one refresh, the call re-issued once with the new token', async () => {
  let refreshes = 0;
  configureAuth({ getToken: () => 'acc-old', refresh: async () => { refreshes += 1; return 'acc-new'; } });
  handlers['GET ' + B + '/api/a'] = (u, o) => (o.headers.Authorization === 'Bearer acc-new' ? [200, { ok: true, fresh: true }] : fail(401, 'expired', 'Token expired'));
  const r = await apiFetch(B, '/api/a');
  assert.equal(r.fresh, true);
  assert.equal(refreshes, 1);
  assert.equal(calls.filter((c) => c.path === B + '/api/a').length, 2);
});

test('a refresh that fails rethrows the original 401; a still-401 retry is not retried again', async () => {
  configureAuth({ getToken: () => 'acc-old', refresh: async () => { throw new Error('nope'); } });
  handlers['GET ' + B + '/api/a'] = fail(401, 'expired', 'Token expired');
  await assert.rejects(apiFetch(B, '/api/a'), (e) => e.reason === 'expired');
  assert.equal(calls.length, 1, 'no retry when the refresh failed');

  calls = [];
  configureAuth({ getToken: () => 'acc-old', refresh: async () => 'acc-new' });
  await assert.rejects(apiFetch(B, '/api/a'), (e) => e.reason === 'expired');
  assert.equal(calls.length, 2, 'exactly one retry');
});

test('terminal reasons report through onTerminal and are not retried; missing_token is neither', async () => {
  const seen = [];
  let refreshes = 0;
  configureAuth({ getToken: () => 'acc-1', refresh: async () => { refreshes += 1; return 'x'; }, onTerminal: (r, m) => seen.push([r, m]) });
  for (const reason of TERMINAL_401) {
    handlers['GET ' + B + '/api/a'] = fail(401, reason, 'gone: ' + reason);
    await assert.rejects(apiFetch(B, '/api/a'), (e) => e.reason === reason);
  }
  assert.deepEqual(seen.map((x) => x[0]), [...TERMINAL_401]);
  assert.equal(seen[0][1], 'gone: ' + [...TERMINAL_401][0]);
  handlers['GET ' + B + '/api/a'] = fail(401, 'missing_token', 'Send a header.');
  await assert.rejects(apiFetch(B, '/api/a'));
  assert.equal(refreshes, 0);
  assert.equal(seen.length, TERMINAL_401.size, 'missing_token is not terminal');
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `export PATH="$HOME/.nvm/versions/node/v24.18.1/bin:$PATH" && node --test test/client.test.mjs`
Expected: FAIL — `configureAuth` / `errorMessage` / `TERMINAL_401` are not exported (`undefined is not a function`).

- [ ] **Step 3: Rewrite `src/api/client.js`**

Replace the whole file with:

```js
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
  try {
    return await request(base, path, o, useAuth ? hooks.getToken() : null);
  } catch (e) {
    if (!(e instanceof ApiError) || e.status !== 401 || !useAuth || o._retried) throw e;
    if (e.reason === 'expired' && hooks.refresh) {
      let token;
      try { token = await hooks.refresh(); } catch (e2) { throw e; }
      return request(base, path, Object.assign({}, o, { _retried: true }), token);
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
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `node --test test/client.test.mjs`
Expected: 6 passing.

- [ ] **Step 5: Run every suite**

Run: `npm test`
Expected: all green. (Existing callers only ever saw string `detail`s or `data.error`, which are unchanged.)

---

### Task 2: `session.js` — persist the refresh token and the account

**Files:**
- Modify: `src/logic/session.js`
- Test: `test/session.test.mjs`

**Interfaces:**
- Produces: `SESSION_KEY` (`'hoistra.session.v1'`), `loadSession()` now may return `refreshToken` (string) and `account` (`{id, email, full_name, role, status, organization_id, email_verified}`); `saveSession(state)` writes both when `state.signedIn`.

- [ ] **Step 1: Write the failing test**

Create `test/session.test.mjs`:

```js
// session — the reload slice now carries the refresh token and the account. The access
// token never goes to storage, and a slice from the old demo gate (signedIn but no token)
// must land on the sign-in panel, not pretend to be authenticated.
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

const mem = {};
globalThis.window = {
  localStorage: { getItem: (k) => (k in mem ? mem[k] : null), setItem: (k, v) => { mem[k] = String(v); }, removeItem: (k) => { delete mem[k]; } }
};

const { loadSession, saveSession, SESSION_KEY } = await import('../src/logic/session.js');

const ACCOUNT = { id: 'u-1', email: 'sam@example.com', full_name: 'Sam Okafor', organization_id: 'org-1', status: 'active', email_verified: true, role: 'user', role_label: 'Facilities manager', last_login_at: null };

beforeEach(() => { Object.keys(mem).forEach((k) => { delete mem[k]; }); });

test('a legacy slice with signedIn but no refresh token restores nothing', () => {
  mem[SESSION_KEY] = JSON.stringify({ signedIn: true, email: 'a@b.c', role: 'admin', view: 'buildings' });
  assert.deepEqual(loadSession(), {});
});

test('refresh token and account restore; the account is reduced to the fields the shell needs', () => {
  mem[SESSION_KEY] = JSON.stringify({ signedIn: true, refreshToken: 'ref-1', account: ACCOUNT, view: 'home', role: 'user', email: 'sam@example.com' });
  const s = loadSession();
  assert.equal(s.signedIn, true);
  assert.equal(s.refreshToken, 'ref-1');
  assert.deepEqual(s.account, { id: 'u-1', email: 'sam@example.com', full_name: 'Sam Okafor', role: 'user', status: 'active', organization_id: 'org-1', email_verified: true });
  assert.equal(s.view, 'home');
});

test('a malformed account is dropped but the session survives', () => {
  mem[SESSION_KEY] = JSON.stringify({ signedIn: true, refreshToken: 'ref-1', account: { id: 1, email: 'x' }, view: 'home' });
  const s = loadSession();
  assert.equal(s.signedIn, true);
  assert.equal(s.account, undefined);
});

test('saveSession writes the refresh token and account and never the access token', () => {
  saveSession({ signedIn: true, email: 'sam@example.com', role: 'user', navOpen: true, view: 'home', refreshToken: 'ref-9', accessToken: 'acc-9', account: ACCOUNT });
  const d = JSON.parse(mem[SESSION_KEY]);
  assert.equal(d.refreshToken, 'ref-9');
  assert.equal(d.account.email, 'sam@example.com');
  assert.equal(d.account.role, 'user');
  assert.equal('accessToken' in d, false);
  assert.equal(JSON.stringify(d).includes('acc-9'), false);
});

test('signing out removes the key', () => {
  mem[SESSION_KEY] = '{"signedIn":true,"refreshToken":"ref-1"}';
  saveSession({ signedIn: false });
  assert.equal(mem[SESSION_KEY], undefined);
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `node --test test/session.test.mjs`
Expected: FAIL — `SESSION_KEY` undefined; the legacy-slice test returns `{signedIn:true,…}`.

- [ ] **Step 3: Edit `src/logic/session.js`**

Replace the header comment and `KEY` line (lines 1–12):

```js
// session — the slice of state that survives a page reload.
//
// The controller holds everything in memory, so a refresh used to drop you back on the
// sign-in gate having lost the page you were on. This persists just enough to land you
// back where you were: the session credential, who is signed in, the current view, and
// the compliance scope.
//
// The refresh token is stored; the access token is not. The refresh token is what "stay
// signed in" means (14 days, revocable, rotated on every use); the access token lives 30
// minutes and cannot be revoked on its own, so it stays in memory and a reload costs one
// refresh. The account is a summary so the shell can render the avatar and menu before
// that refresh answers; the server's copy replaces it as soon as it does.
//
// Deliberately NOT persisted here: staged files (File objects do not serialise), the live
// register and any in-flight flags (better re-fetched than restored stale), and every
// flow/modal state (a half-open dialog restored from last week is worse than a clean page).
// The transcript itself lives with its session record (logic/sessions.js); only the id of
// the active session is kept here so the record can be found again.
export const SESSION_KEY = 'hoistra.session.v1';
const KEY = SESSION_KEY;
```

After the `isStrArray` line add:

```js
const isStr = (v) => typeof v === 'string';
const ROLES = ['user', 'admin', 'superadmin'];

// The account as the shell needs it. Anything else the server sent (role_label,
// last_login_at) is display-only and comes back fresh with the next refresh.
function cleanAccount(a) {
  if (!a || typeof a !== 'object') return null;
  if (!isStr(a.id) || !isStr(a.email) || !isStr(a.full_name) || !isStr(a.status)) return null;
  if (!isStr(a.role) || ROLES.indexOf(a.role) < 0) return null;
  return {
    id: a.id, email: a.email, full_name: a.full_name, role: a.role, status: a.status,
    organization_id: isStr(a.organization_id) ? a.organization_id : null,
    email_verified: a.email_verified === true
  };
}
```

In `loadSession`, replace the line `if (d.signedIn === true) out.signedIn = true;` with:

```js
  // A session is only a session with the credential that can renew it. A slice from the
  // demo gate — signedIn with no token — restores nothing.
  if (isStr(d.refreshToken) && d.refreshToken) out.refreshToken = d.refreshToken;
  const account = cleanAccount(d.account);
  if (account) out.account = account;
  if (d.signedIn === true && out.refreshToken) out.signedIn = true;
```

In `saveSession`, inside the `JSON.stringify({ … })`, after `role: state.role || 'user',` add:

```js
      refreshToken: state.refreshToken || null,
      account: cleanAccount(state.account),
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `node --test test/session.test.mjs`
Expected: 5 passing.

- [ ] **Step 5: Seed a refresh token in the two legacy suites that persist a sign-in**

Two existing suites write a signed-in slice with no token and then reconstruct the controller expecting it to restore. Under the new rule that slice is the prototype gate's and restores nothing, so they must carry the credential a real session would.

`test/store.test.mjs` line 23 — change:
```js
const fresh = () => { const x = new HoistraLogic(); x.setState({ signedIn: true, view: 'home' }); return x; };
```
to:
```js
// A refresh token rides along: since logic/session.js only restores a session that can renew
// itself, a slice without one lands on the gate instead of the page under test.
const fresh = () => { const x = new HoistraLogic(); x.setState({ signedIn: true, refreshToken: 'ref-test', view: 'home' }); return x; };
```

`test/chat.test.mjs` line 244 — change:
```js
  saveSession({ signedIn: true, email: 'a@b.c', role: 'user', view: 'chat' });
```
to:
```js
  saveSession({ signedIn: true, refreshToken: 'ref-test', email: 'a@b.c', role: 'user', view: 'chat' });
```

- [ ] **Step 6: Run every suite**

Run: `npm test`
Expected: all green — in particular `store.test.mjs` "a reload brings the sessions and the open transcript back", "the space view survives a reload with its key" and "the navigator state survives a reload…", and `chat.test.mjs`'s `loadSession().view === 'chat'` check.

---

### Task 3: `api/auth.js`, `logic/auth.js` core, and the wiring — sign in, sign out, role gating

**Files:**
- Create: `src/api/auth.js`
- Create: `src/logic/auth.js`
- Modify: `src/logic/HoistraLogic.js` (import, `state`, constructor, mixin list)
- Modify: `src/logic/core.js` (`componentDidMount`, Escape handler, `componentWillUnmount`)
- Modify: `src/logic/renderVals.js:223` and `:301-333`
- Test: `test/auth.test.mjs`

**Interfaces:**
- Consumes: Task 1 `configureAuth`, `apiFetch`; Task 2 `loadSession`, `SESSION_KEY`.
- Produces (on the controller): `authBoot() → Promise`, `authStop()`, `authLoadConfig()`, `authRefresh() → Promise<accessToken>`, `authEnter(resp, {keepView})`, `authSignedOut(notice)`, `authSignIn()`, `authSSO()`, `authGo(mode)`, `authSignOut()`, `authArm(seconds)`, `authCooling()`, `authCountdown()`, `authOtp()`, `authVals(s)`; module exports `AUTH_DEFAULTS`, `authMethods`, `canAdmin(account)`, `normaliseCode(s)`, `AUTH_FALLBACK_CONFIG`. `authApi.*` as listed in the code. Later tasks add `authRegister/authVerify/authResend/authAccepted/authCodeFailure` (Task 4), `authForgot/authReset` (Task 5), the storage listener body (Task 6), `pwOpenModal/pwClose/pwSubmit/authSignOutEverywhere` (Task 7) — `authVals` below already references them by name; they are only invoked on click.

- [ ] **Step 1: Write the failing test (the harness plus the sign-in suite)**

Create `test/auth.test.mjs`:

```js
// auth — the real controller in Node, fetch mocked per METHOD + path so nothing reaches a
// host. This stack's backend points at production and every auth route except /config
// writes (accounts, sessions, last_login_at), so these tests must prove the requests and
// the state transitions without ever performing one.
import { test, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';

const mem = {};
let calls, handlers;
globalThis.window = {
  location: { origin: 'http://test.local' }, scrollTo: () => {},
  addEventListener: () => {}, removeEventListener: () => {},
  localStorage: { getItem: (k) => (k in mem ? mem[k] : null), setItem: (k, v) => { mem[k] = String(v); }, removeItem: (k) => { delete mem[k]; } }
};
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };
globalThis.fetch = async (url, opts) => {
  const u = new URL(String(url));
  const method = (opts && opts.method) || 'GET';
  calls.push({ method, path: u.pathname, headers: (opts && opts.headers) || {}, body: opts && opts.body ? JSON.parse(opts.body) : null });
  const h = handlers[method + ' ' + u.pathname];
  if (!h) throw new TypeError('Failed to fetch: no handler for ' + method + ' ' + u.pathname);
  const [status, body] = typeof h === 'function' ? h(u, opts) : h;
  return { ok: status >= 200 && status < 300, status, statusText: String(status), text: async () => JSON.stringify(body) };
};

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
const { SESSION_KEY } = await import('../src/logic/session.js');
const { apiFetch, BASES } = await import('../src/api/client.js');

const A = '/backend/ops-intelligence/api/auth';
const USER = { id: 'u-1', email: 'sam@example.com', full_name: 'Sam Okafor', organization_id: 'org-1', status: 'active', email_verified: true, role: 'user', role_label: 'Facilities manager — the default for a new account', last_login_at: null };
const ADMIN = Object.assign({}, USER, { id: 'u-2', email: 'ada@example.com', full_name: 'Ada Admin', role: 'admin' });
const CONFIG = { ok: true, self_registration: true, password: { min_length: 12 }, otp: { code_length: 6, ttl_minutes: 10, max_attempts: 5, resend_cooldown_seconds: 60, max_per_hour: 5 }, secrets_configured: { jwt: true, otp_pepper: true } };
const tokens = (n) => ({ access_token: 'acc-' + n, refresh_token: 'ref-' + n, token_type: 'Bearer', expires_in: 1800 });
const session = (user, n, message) => [200, Object.assign({ ok: true, user, tokens: tokens(n) }, message ? { message } : {})];
const accepted = (status, message) => [202, { ok: true, status, message, email: 'sam@example.com', otp: CONFIG.otp }];
const fail = (status, reason, error, extra) => [status, { detail: Object.assign({ ok: false, error, reason }, extra || {}) }];
const settle = (ms) => new Promise((r) => setTimeout(r, ms || 10));
const stored = () => JSON.parse(mem[SESSION_KEY] || '{}');
const requests = (path) => calls.filter((c) => c.path === path);

let c;
const fresh = () => { const x = new HoistraLogic(); x.authBoot(); return x; };
const cleanup = (x) => { const k = x || c; k.authStop(); clearTimeout(k._tt); };
beforeEach(() => {
  Object.keys(mem).forEach((k) => { delete mem[k]; });
  calls = []; handlers = { ['GET ' + A + '/config']: [200, CONFIG] };
  c = fresh();
});
afterEach(() => cleanup());

// ── sign in ──────────────────────────────────────────────────────────────────

test('login 200: account, both tokens, signed in on home; only the refresh token is stored', async () => {
  handlers['POST ' + A + '/login'] = session(USER, 1);
  c.setState({ email: ' sam@example.com ', password: 'correct-horse-battery-staple' });
  await c.authSignIn();
  const r = requests(A + '/login')[0];
  assert.deepEqual(r.body, { email: 'sam@example.com', password: 'correct-horse-battery-staple' });
  assert.equal(r.headers.Authorization, undefined, 'login never carries a bearer');
  assert.equal(c.state.signedIn, true);
  assert.equal(c.state.view, 'home');
  assert.equal(c.state.navOpen, true);
  assert.equal(c.state.account.email, 'sam@example.com');
  assert.equal(c.state.accessToken, 'acc-1');
  assert.equal(c.state.refreshToken, 'ref-1');
  assert.equal(c.state.password, '', 'the password leaves memory');
  assert.equal(stored().refreshToken, 'ref-1');
  assert.equal(stored().account.full_name, 'Sam Okafor');
  assert.equal(JSON.stringify(stored()).includes('acc-1'), false, 'the access token is never stored');
});

test('after login every backend call carries the bearer', async () => {
  handlers['POST ' + A + '/login'] = session(USER, 1);
  handlers['GET /backend/udr/api/anything'] = [200, { ok: true }];
  c.setState({ email: 'sam@example.com', password: 'x'.repeat(12) });
  await c.authSignIn();
  await apiFetch(BASES.udr, '/api/anything');
  assert.equal(requests('/backend/udr/api/anything')[0].headers.Authorization, 'Bearer acc-1');
});

test('a user-role account gets User view only: role clamped, no Admin view row', async () => {
  handlers['POST ' + A + '/login'] = session(USER, 1);
  c.setState({ email: 'sam@example.com', password: 'x'.repeat(12), role: 'admin' });
  await c.authSignIn();
  assert.equal(c.state.role, 'user');
  const v = c.renderVals();
  assert.equal(v.canAdmin, false);
  assert.equal(v.acctItems.some((i) => i.label === 'Admin view' || i.label === 'User view'), false);
  assert.ok(v.acctItems.some((i) => i.label === 'Change password'));
  assert.ok(v.acctItems.some((i) => i.label === 'Sign out everywhere'));
  assert.equal(v.acctName, 'Sam Okafor');
  assert.equal(v.acctEmail, 'sam@example.com');
  assert.equal(v.acctInitial, 'S');
});

test('an admin account keeps the Admin view toggle', async () => {
  handlers['POST ' + A + '/login'] = session(ADMIN, 1);
  c.setState({ email: 'ada@example.com', password: 'x'.repeat(12) });
  await c.authSignIn();
  const v = c.renderVals();
  assert.equal(v.canAdmin, true);
  const toggle = v.acctItems.find((i) => i.label === 'Admin view');
  assert.ok(toggle);
  toggle.click();
  assert.equal(c.state.role, 'admin');
  assert.equal(c.state.view, 'buildings');
});

test('login 401 shows the message verbatim and stays gated', async () => {
  handlers['POST ' + A + '/login'] = fail(401, 'invalid_credentials', 'That email address and password do not match an account.');
  c.setState({ email: 'sam@example.com', password: 'wrong-wrong-wrong' });
  await c.authSignIn();
  assert.equal(c.state.signedIn, false);
  assert.equal(c.state.authError, 'That email address and password do not match an account.');
  assert.equal(c.state.authReason, 'invalid_credentials');
  assert.equal(c.state.authBusy, false);
  assert.equal(requests(A + '/refresh').length, 0, 'a failing login never refreshes');
});

test('login 429 locked arms a countdown from retry_after_seconds and offers the reset link', async () => {
  handlers['POST ' + A + '/login'] = fail(429, 'locked', 'Too many failed attempts. Try again in 15 minutes, or reset your password.', { retry_after_seconds: 899 });
  c.setState({ email: 'sam@example.com', password: 'x'.repeat(12) });
  await c.authSignIn();
  assert.equal(c.state.authReason, 'locked');
  assert.ok(c.state.authRetryAt > Date.now() + 890 * 1000);
  const v = c.renderVals();
  assert.equal(v.authLocked, true);
  assert.match(v.authCountdown, /^14:5\d$/);
  v.authGoForgot();
  assert.equal(c.state.authMode, 'forgot');
  assert.equal(c.state.email, 'sam@example.com', 'the email is kept for the reset');
});

test('login 403 email_not_verified goes straight to the code screen without a resend', async () => {
  handlers['POST ' + A + '/login'] = fail(403, 'email_not_verified', 'Confirm your email address first. We have sent a new code to sam@example.com.', { email: 'sam@example.com' });
  c.setState({ email: 'sam@example.com', password: 'x'.repeat(12) });
  await c.authSignIn();
  assert.equal(c.state.authMode, 'verify');
  assert.equal(c.state.authNotice, 'Confirm your email address first. We have sent a new code to sam@example.com.');
  assert.equal(c.state.authError, '');
  assert.equal(requests(A + '/resend-code').length, 0);
  assert.ok(c.state.authRetryAt > Date.now(), 'the resend cooldown is armed — a code is already in the inbox');
});

test('login 422 and a network failure get the gate\'s own one-liners', async () => {
  handlers['POST ' + A + '/login'] = [422, { detail: [{ loc: ['body', 'email'], msg: 'value is not a valid email address', type: 'value_error' }] }];
  c.setState({ email: 'nope', password: 'x' });
  await c.authSignIn();
  assert.equal(c.state.authError, 'Enter a valid email address and password.');
  delete handlers['POST ' + A + '/login'];
  await c.authSignIn();
  assert.equal(c.state.authError, "Couldn't reach the sign-in service. Check the backend is running.");
});

test('the SSO button is inert: a toast, no sign-in', () => {
  c.renderVals().authSSO();
  assert.equal(c.state.signedIn, false);
  assert.equal(c.state.toast, 'Single sign-on is not available yet — sign in with your email and password.');
});

test('the animated page chips no longer fake a sign-in', () => {
  c.renderVals().f4items[0].click();
  assert.equal(c.state.signedIn, false);
  assert.equal(c.state.authMode, 'signin');
  assert.equal(c.state.authNotice, 'Sign in to open NABERS data pack.');
});

test('sign out posts the refresh token and clears everything even when logout fails', async () => {
  handlers['POST ' + A + '/login'] = session(USER, 1);
  c.setState({ email: 'sam@example.com', password: 'x'.repeat(12) });
  await c.authSignIn();
  c.setState({ view: 'buildings', navOpen: true, acctOpen: true });
  // no /logout handler → the mocked fetch rejects, exactly like an unreachable backend
  c.renderVals().signOut();
  await settle();
  assert.equal(requests(A + '/logout')[0].body.refresh_token, 'ref-1');
  assert.equal(c.state.signedIn, false);
  assert.equal(c.state.account, null);
  assert.equal(c.state.accessToken, null);
  assert.equal(c.state.refreshToken, null);
  assert.equal(c.state.view, 'home');
  assert.equal(c.state.acctOpen, false);
  assert.equal(c.state.role, 'user');
  assert.equal(mem[SESSION_KEY], undefined);
});

test('a restored role:admin on a user account is clamped in the constructor', () => {
  cleanup();
  mem[SESSION_KEY] = JSON.stringify({ signedIn: true, refreshToken: 'ref-1', account: USER, role: 'admin', view: 'buildings' });
  c = new HoistraLogic();
  assert.equal(c.state.signedIn, true);
  assert.equal(c.state.role, 'user');
  mem[SESSION_KEY] = JSON.stringify({ signedIn: true, refreshToken: 'ref-1', account: ADMIN, role: 'admin', view: 'buildings' });
  const d = new HoistraLogic();
  assert.equal(d.state.role, 'admin');
});

test('/config is read once at boot and drives the register link and the length hint', async () => {
  await settle();
  assert.equal(requests(A + '/config').length, 1);
  let v = c.renderVals();
  assert.equal(v.authCanRegister, true);
  assert.equal(v.authMinLength, 12);
  assert.equal(v.authCodeLength, 6);
  c.setState({ authConfig: Object.assign({}, CONFIG, { self_registration: false, password: { min_length: 16 } }) });
  v = c.renderVals();
  assert.equal(v.authCanRegister, false);
  assert.equal(v.authMinLength, 16);
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `node --test test/auth.test.mjs`
Expected: FAIL — `x.authBoot is not a function`.

- [ ] **Step 3: Create `src/api/auth.js`**

```js
// api/auth — svc-operations-intelligence's /api/auth: accounts, codes, sessions, passwords.
// Routes: apps/backend/.../svc-operations-intelligence/src/api/routes/auth.py. Mounted behind
// the gateway at /backend/ops-intelligence/. Every failure is {detail: {ok, error, reason}}.
//
// The open endpoints pass `auth: false` — no bearer, and a 401 from /login or /refresh must
// never start a refresh of its own. Only logout-everywhere and password/change need the
// caller's token. GET /me is deliberately not wrapped: this client never stores the access
// token, so a reload exchanges the refresh token instead and reads the user off that reply.
import { BASES, ORG_ID, apiFetch } from './client.js';

const B = BASES.opsIntelligence;
const open = (path, body) => apiFetch(B, path, body === undefined ? { auth: false } : { auth: false, method: 'POST', body });

export const authApi = {
  config: () => open('/api/auth/config'),
  // 202 whether or not the address is already registered — the owner of the mailbox is told.
  register: (f) => open('/api/auth/register', Object.assign(
    { email: f.email, password: f.password, full_name: f.full_name, phone: f.phone || null },
    ORG_ID ? { organization_id: ORG_ID } : {}
  )),
  verifyEmail: (email, code) => open('/api/auth/verify-email', { email, code }),
  resendCode: (email) => open('/api/auth/resend-code', { email }),
  login: (email, password) => open('/api/auth/login', { email, password }),
  // Rotates both tokens; presenting one already exchanged is 401 replayed and revokes all.
  refresh: (refreshToken) => open('/api/auth/refresh', { refresh_token: refreshToken }),
  logout: (refreshToken) => open('/api/auth/logout', { refresh_token: refreshToken }),
  logoutEverywhere: () => apiFetch(B, '/api/auth/logout', { method: 'POST', body: { everywhere: true } }),
  forgot: (email) => open('/api/auth/password/forgot', { email }),
  reset: (email, code, newPassword) => open('/api/auth/password/reset', { email, code, new_password: newPassword }),
  changePassword: (current, next) => apiFetch(B, '/api/auth/password/change', { method: 'POST', body: { current_password: current, new_password: next } })
};
```

- [ ] **Step 4: Create `src/logic/auth.js`**

```js
// auth — accounts and sessions against /api/auth (api/auth.js). Methods are mixed into
// HoistraLogic.prototype; `this` is the controller.
//
// `signedIn` stays the one boolean the shell keys off. `role` stays the view mode
// (user | admin); whether it may ever be admin is decided by the account's real role.
// The access token lives in state only; the refresh token is the persisted credential.
import { authApi } from '../api/auth.js';
import { configureAuth } from '../api/client.js';
import { loadSession, SESSION_KEY } from './session.js';

export const ADMIN_ROLES = new Set(['admin', 'superadmin']);
export const canAdmin = (account) => !!account && ADMIN_ROLES.has(account.role);

// The reference's published defaults, used until GET /config answers (or if it never does).
export const AUTH_FALLBACK_CONFIG = {
  self_registration: true,
  password: { min_length: 12 },
  otp: { code_length: 6, ttl_minutes: 10, max_attempts: 5, resend_cooldown_seconds: 60, max_per_hour: 5 }
};

// "123 456" and "123-456" are the same code.
export const normaliseCode = (s) => String(s || '').replace(/[\s-]/g, '');

// After these the code is dead — the right digits will not work either.
export const DEAD_CODE = new Set(['expired', 'too_many_attempts', 'no_code']);

export const AUTH_DEFAULTS = {
  account: null, accessToken: null, refreshToken: null, authBooting: false,
  authMode: 'signin', authBusy: false, authError: '', authNotice: '', authReason: '',
  authAttemptsLeft: null, authRetryAt: 0, authTick: 0, authConfig: null,
  password: '', fullName: '', phone: '', code: '', newPassword: '',
  pwOpen: false, pwCurrent: '', pwNext: '', pwBusy: false, pwError: ''
};

const NETWORK_MSG = "Couldn't reach the sign-in service. Check the backend is running.";
const SCHEMA_MSG = 'Enter a valid email address and password.';
const SSO_MSG = 'Single sign-on is not available yet — sign in with your email and password.';

// One ApiError → what the gate shows. A 422 is FastAPI's schema array, written for
// developers; the gate says one plain thing instead. Anything without a status is the network.
export function failurePatch(e) {
  if (!e || typeof e.status !== 'number') return { authError: NETWORK_MSG, authReason: '' };
  if (e.status === 422) return { authError: SCHEMA_MSG, authReason: '' };
  return { authError: e.message || String(e.status), authReason: e.reason || '' };
}
const detailOf = (e) => { const d = e && e.body && e.body.detail; return d && typeof d === 'object' && !Array.isArray(d) ? d : {}; };
const retryAfter = (e) => { const n = Number(detailOf(e).retry_after_seconds); return Number.isFinite(n) && n > 0 ? n : 0; };
const attemptsLeft = (e) => { const n = detailOf(e).attempts_remaining; return typeof n === 'number' ? n : null; };
const trimmed = (v) => String(v || '').trim();

export const authMethods = {
  // ── lifecycle ──────────────────────────────────────────────────────────
  // Installs the client hooks, then renews a stored session. The stored account already
  // rendered the shell (loadSession restored signedIn), so the refresh only has to swap in
  // fresh tokens; a 401 here drops to the gate, a network failure leaves things as they are.
  authBoot() {
    configureAuth({
      getToken: () => this.state.accessToken,
      refresh: () => this.authRefresh(),
      onTerminal: (reason, message) => this.authSignedOut(message)
    });
    this._authStorage = (e) => this.authStorageChanged(e);
    window.addEventListener('storage', this._authStorage);
    if (!this.state.refreshToken) {
      this.authLoadConfig();
      return Promise.resolve();
    }
    this.setState({ authBooting: true });
    return this.authRefresh().catch(() => {}).then(() => this.setState({ authBooting: false }));
  },

  authStop() {
    window.removeEventListener('storage', this._authStorage);
    clearInterval(this._authTimer); this._authTimer = null;
  },

  authLoadConfig() {
    if (this._authConfigAsked) return;
    this._authConfigAsked = true;
    authApi.config().then((cfg) => { if (cfg && cfg.ok) this.setState({ authConfig: cfg }); }).catch(() => {});
  },

  // Another tab rotated the refresh token, or signed out. Filled in by Task 6.
  authStorageChanged() {},

  // Single-flight: the 401 interceptor, boot and a second caller all await one promise.
  // The token is re-read from storage because another tab may have rotated it; presenting
  // the stale copy is a replay, and a replay revokes every session on the account.
  authRefresh() {
    if (this._refreshing) return this._refreshing;
    const token = loadSession().refreshToken || this.state.refreshToken;
    if (!token) return Promise.reject(new Error('no refresh token'));
    this._refreshing = authApi.refresh(token)
      .then((resp) => { this.authEnter(resp, { keepView: true }); return resp.tokens.access_token; })
      .catch((e) => {
        if (e && typeof e.status === 'number') this.authSignedOut(e.message || '');
        throw e;
      })
      .finally(() => { this._refreshing = null; });
    return this._refreshing;
  },

  // ── entering and leaving ───────────────────────────────────────────────
  // Apply a SessionResponse. `keepView` is the reload refresh, which must not move the page.
  authEnter(resp, opts) {
    const o = opts || {};
    const admin = canAdmin(resp.user);
    this.setState((p) => ({
      account: resp.user, accessToken: resp.tokens.access_token, refreshToken: resp.tokens.refresh_token,
      signedIn: true,
      role: admin ? p.role : 'user',
      view: o.keepView ? p.view : 'home', navOpen: o.keepView ? p.navOpen : true,
      authMode: 'signin', authBusy: false, authError: '', authReason: '', authAttemptsLeft: null, authRetryAt: 0,
      password: '', code: '', newPassword: '', fullName: '', phone: ''
    }));
    if (resp.message && !o.keepView) this.flash(resp.message);
  },

  // Local sign-out. `notice` is what the gate shows — the server's own line when it ended
  // the session, nothing when the person chose to leave.
  authSignedOut(notice) {
    this.setState({
      account: null, accessToken: null, refreshToken: null, signedIn: false, role: 'user',
      view: 'home', navOpen: false, queueOpen: false, detail: null, acctOpen: false,
      pwOpen: false, pwCurrent: '', pwNext: '', pwBusy: false, pwError: '',
      authMode: 'signin', authBusy: false, authError: '', authReason: '', authAttemptsLeft: null,
      authNotice: notice || '', password: '', code: '', newPassword: ''
    });
    this.authLoadConfig();
  },

  authSignOut() {
    const token = this.state.refreshToken;
    if (token) authApi.logout(token).catch(() => {});
    this.authSignedOut('');
  },

  // ── the gate ───────────────────────────────────────────────────────────
  authGo(mode) {
    this.setState({ authMode: mode, authError: '', authReason: '', authAttemptsLeft: null, code: '' });
  },

  authSSO() { this.flash(SSO_MSG); },

  async authSignIn() {
    const s = this.state;
    if (s.authBusy) return;
    this.setState({ authBusy: true, authError: '', authNotice: '', authReason: '' });
    try {
      this.authEnter(await authApi.login(trimmed(s.email), s.password));
    } catch (e) {
      const patch = Object.assign({ authBusy: false }, failurePatch(e));
      if (e && e.status === 403 && e.reason === 'email_not_verified') {
        // The server has already sent a fresh code: go to the code screen, ask for nothing.
        Object.assign(patch, { authMode: 'verify', authNotice: e.message, authError: '', code: '' });
        this.authArm(this.authOtp().resend_cooldown_seconds);
      } else if (e && e.status === 429) {
        this.authArm(retryAfter(e));
      }
      this.setState(patch);
    }
  },

  // ── cooldowns and lockouts ─────────────────────────────────────────────
  authArm(seconds) {
    const n = Number(seconds) || 0;
    if (n <= 0) return;
    this.setState({ authRetryAt: Date.now() + n * 1000 });
    if (this._authTimer) return;
    this._authTimer = setInterval(() => {
      this.setState((p) => ({ authTick: p.authTick + 1 }));
      if (this.state.authRetryAt <= Date.now()) { clearInterval(this._authTimer); this._authTimer = null; }
    }, 1000);
  },
  authCooling() { return this.state.authRetryAt > Date.now(); },
  authCountdown() {
    const ms = this.state.authRetryAt - Date.now();
    if (ms <= 0) return '';
    const t = Math.ceil(ms / 1000);
    const m = Math.floor(t / 60), sec = t % 60;
    return (m < 10 ? '0' : '') + m + ':' + (sec < 10 ? '0' : '') + sec;
  },
  authOtp() {
    const cfg = this.state.authConfig || AUTH_FALLBACK_CONFIG;
    return Object.assign({}, AUTH_FALLBACK_CONFIG.otp, cfg.otp || {});
  },

  // ── view model ─────────────────────────────────────────────────────────
  authVals(s) {
    const admin = canAdmin(s.account);
    const cfg = s.authConfig || AUTH_FALLBACK_CONFIG;
    const cooling = this.authCooling();
    const primary = { signin: 'authSignIn', register: 'authRegister', verify: 'authVerify', forgot: 'authForgot', reset: 'authReset' }[s.authMode] || 'authSignIn';
    const field = (k) => (e) => this.setState({ [k]: e.target.value });
    const a = s.account || {};
    const name = a.full_name || a.email || '';
    return {
      // the gate
      email: s.email, setEmail: field('email'),
      password: s.password, setPassword: field('password'),
      fullName: s.fullName, setFullName: field('fullName'),
      phone: s.phone, setPhone: field('phone'),
      code: s.code, setCode: field('code'),
      newPassword: s.newPassword, setNewPassword: field('newPassword'),
      authMode: s.authMode, authBusy: s.authBusy, authBooting: s.authBooting,
      authError: s.authError, authNotice: s.authNotice, authReason: s.authReason,
      authAttemptsLeft: s.authAttemptsLeft,
      authCountdown: this.authCountdown(), authCoolingDown: cooling,
      authLocked: s.authMode === 'signin' && s.authReason === 'locked' && cooling,
      authDeadCode: DEAD_CODE.has(s.authReason),
      authCanRegister: cfg.self_registration !== false,
      authMinLength: (cfg.password && cfg.password.min_length) || AUTH_FALLBACK_CONFIG.password.min_length,
      authCodeLength: this.authOtp().code_length,
      authKey: (e) => { if (e.key === 'Enter') this[primary](); },
      authSignIn: () => this.authSignIn(), authSSO: () => this.authSSO(),
      authRegister: () => this.authRegister(), authVerify: () => this.authVerify(), authResend: () => this.authResend(),
      authForgot: () => this.authForgot(), authReset: () => this.authReset(),
      authGoSignin: () => this.authGo('signin'), authGoRegister: () => this.authGo('register'), authGoForgot: () => this.authGo('forgot'),
      signOut: () => this.authSignOut(),

      /* Account menu. The admin view is a mode, not a page: switching into it leaves only
         the admin surfaces in the navigator, so a configuration session cannot be confused
         with reading a report. Only an account whose real role allows it is offered the
         switch; everyone else has User view and nothing to toggle. */
      acctOpen: s.acctOpen,
      toggleAcct: () => this.setState((p) => ({ acctOpen: !p.acctOpen })),
      closeAcct: () => this.setState({ acctOpen: false }),
      acctName: name || 'Account', acctEmail: a.email || '',
      acctInitial: (name || '?').trim().charAt(0).toUpperCase(),
      canAdmin: admin,
      acctRole: s.role === 'admin' ? 'Admin view' : 'User view',
      acctBg: s.role === 'admin' ? 'var(--color-accent)' : 'var(--color-neutral-900)',
      acctFg: s.role === 'admin' ? 'var(--accent-ink)' : 'var(--color-neutral-300)',
      acctEdge: s.role === 'admin' ? 'var(--color-accent)' : 'var(--color-divider)',
      acctItems: [
        { label: 'Pricing', icon: 'ph-tag', click: () => this.setState({ acctOpen: false }, () => this.flash('Pricing and plan usage open in the billing workspace — seats, buildings hoisted and ingest volume.')) },
        { label: 'Support', icon: 'ph-lifebuoy', click: () => this.setState({ acctOpen: false }, () => this.flash('Support: a Hoister is on call for this portfolio. Every request carries the page and the graph state you were on.')) },
        admin ? {
          label: s.role === 'admin' ? 'User view' : 'Admin view',
          icon: s.role === 'admin' ? 'ph-user-focus' : 'ph-shield-star', tick: false,
          click: () => this.setState((p) => ({
            role: p.role === 'admin' ? 'user' : 'admin',
            acctOpen: false,
            view: p.role === 'admin' ? 'home' : 'buildings',
            navOpen: true, detail: null
          }))
        } : null,
        { label: 'Change password', icon: 'ph-key', click: () => this.pwOpenModal() },
        { label: 'Sign out everywhere', icon: 'ph-power', click: () => this.authSignOutEverywhere() }
      ].filter(Boolean).map((i) => ({
        label: i.label, icon: i.icon, click: i.click,
        fg: i.tick ? 'var(--color-accent)' : 'var(--color-text)',
        iconFg: i.tick ? 'var(--color-accent)' : 'var(--color-neutral-500)',
        tickShow: i.tick ? 'block' : 'none'
      })),

      // change-password modal
      pwOpen: s.pwOpen, pwCurrent: s.pwCurrent, pwNext: s.pwNext, pwBusy: s.pwBusy, pwError: s.pwError,
      pwSetCurrent: field('pwCurrent'), pwSetNext: field('pwNext'),
      pwClose: () => this.pwClose(), pwSubmit: () => this.pwSubmit(),
      pwKey: (e) => { if (e.key === 'Enter') this.pwSubmit(); if (e.key === 'Escape') this.pwClose(); }
    };
  }
};
```

- [ ] **Step 5: Wire it into `src/logic/HoistraLogic.js`**

Add the import after line 19 (`import { buildingsCrudMethods } …`):

```js
import { AUTH_DEFAULTS, authMethods, canAdmin } from './auth.js';
```

Change the first line of the `state` class field from:

```js
  state = {
    view: "home", module: null, answerKey: null, askedQuery: "",
```
to:
```js
  state = {
    ...AUTH_DEFAULTS,
    view: "home", module: null, answerKey: null, askedQuery: "",
```

In the constructor, directly after `Object.assign(this.state, loadSession());` add:

```js
    // Admin view is only ever offered to an account whose real role allows it; a stored
    // mode from before the role model, or from another account, is reset.
    if (this.state.role === "admin" && !canAdmin(this.state.account)) this.state.role = "user";
```

In the final `Object.assign(HoistraLogic.prototype, …)` line, insert `authMethods,` immediately before `renderValsMethods`.

- [ ] **Step 6: Wire it into `src/logic/core.js`**

In `componentDidMount()`, make `this.authBoot();` the first statement (before `this._key = …`).

Change the Escape line from:
```js
      if (e.key === "Escape") this.setState({ paletteOpen: false, detail: null, queueOpen: false });
```
to:
```js
      if (e.key === "Escape") this.setState({ paletteOpen: false, detail: null, queueOpen: false, pwOpen: false });
```

In `componentWillUnmount()`, directly after `window.removeEventListener("keydown", this._key);` add `this.authStop();`.

- [ ] **Step 7: Replace the gate and account-menu handlers in `src/logic/renderVals.js`**

Line 223 — change:
```js
      ].map((p) => ({ ...p, click: () => this.setState({ signedIn: true, view: "home" }) })),
```
to:
```js
      ].map((p) => ({ ...p, click: () => this.setState({ authMode: "signin", authNotice: "Sign in to open " + p.name + "." }) })),
```

Lines 301–333 — delete this entire block (from `email: s.email,` through the `})),` that closes `acctItems`):

```js
      email: s.email,
      setEmail: (e) => this.setState({ email: e.target.value }),
      signIn: () => this.setState({ signedIn: true, view: "home", navOpen: true }),
      gateKey: (e) => { if (e.key === "Enter") this.setState({ signedIn: true, view: "home" }); },
      signOut: () => this.setState({ signedIn: false, view: "home", role: "user", acctOpen: false, navOpen: false, queueOpen: false, detail: null }),

      /* Account menu. The admin view is a mode, not a page: switching into it
         leaves only the admin surfaces in the navigator, so a configuration
         session cannot be confused with reading a report. */
      acctOpen: s.acctOpen,
      toggleAcct: () => this.setState((p) => ({ acctOpen: !p.acctOpen })),
      closeAcct: () => this.setState({ acctOpen: false }),
      acctRole: s.role === "admin" ? "Admin view" : "User view",
      acctBg: s.role === "admin" ? "var(--color-accent)" : "var(--color-neutral-900)",
      acctFg: s.role === "admin" ? "var(--accent-ink)" : "var(--color-neutral-300)",
      acctEdge: s.role === "admin" ? "var(--color-accent)" : "var(--color-divider)",
      acctItems: [
        { label: "Pricing", icon: "ph-tag", click: () => this.setState({ acctOpen: false }, () => this.flash("Pricing and plan usage open in the billing workspace — seats, buildings hoisted and ingest volume.")) },
        { label: "Support", icon: "ph-lifebuoy", click: () => this.setState({ acctOpen: false }, () => this.flash("Support: a Hoister is on call for this portfolio. Every request carries the page and the graph state you were on.")) },
        { label: s.role === "admin" ? "User view" : "Admin view",
          icon: s.role === "admin" ? "ph-user-focus" : "ph-shield-star", tick: false,
          click: () => this.setState((p) => ({
            role: p.role === "admin" ? "user" : "admin",
            acctOpen: false,
            view: p.role === "admin" ? "home" : "buildings",
            navOpen: true, detail: null
          })) }
      ].map((a) => ({
        label: a.label, icon: a.icon, click: a.click,
        fg: a.tick ? "var(--color-accent)" : "var(--color-text)",
        iconFg: a.tick ? "var(--color-accent)" : "var(--color-neutral-500)",
        tickShow: a.tick ? "block" : "none"
      })),
```

and put in its place:

```js
      // The gate, the account menu and the change-password modal: logic/auth.js.
      ...this.authVals(s),
```

- [ ] **Step 8: Run the test to verify it passes**

Run: `node --test test/auth.test.mjs`
Expected: 13 passing.

- [ ] **Step 9: Replace the removed `signIn()` call in the legacy navigator test**

`vals.signIn` no longer exists (it faked a sign-in). `test/store.test.mjs` lines 258–261 read:
```js
  const c4 = new HoistraLogic();
  c4.setState({ signedIn: false });
  c4.renderVals().signIn();
  assert.equal(c4.state.navOpen, true);
```
Change them to apply a real session response, which is what opens the navigator now:
```js
  const c4 = new HoistraLogic();
  c4.setState({ signedIn: false, navOpen: false });
  c4.authEnter({
    user: { id: 'u-1', email: 'a@b.c', full_name: 'A B', organization_id: null, status: 'active', email_verified: true, role: 'user' },
    tokens: { access_token: 'acc-test', refresh_token: 'ref-test', token_type: 'Bearer', expires_in: 1800 }
  });
  assert.equal(c4.state.navOpen, true);
```

- [ ] **Step 10: Run every suite**

Run: `npm test`
Expected: all green. The suites that construct the controller may log `Failed to fetch … /api/auth/config` only if they call `authBoot()` — none of the legacy ones do; a *failure* anywhere is not acceptable.

---

### Task 4: Create an account — register, verify, resend

**Files:**
- Modify: `src/logic/auth.js` (add methods to `authMethods`)
- Test: `test/auth.test.mjs` (append)

**Interfaces:**
- Consumes: Task 3 `authEnter`, `authArm`, `authOtp`, `failurePatch`, `normaliseCode`, `DEAD_CODE`, `retryAfter`, `attemptsLeft`, `trimmed`.
- Produces: `authRegister()`, `authVerify()`, `authResend()`, `authAccepted(resp, mode)`, `authCodeFailure(e) → patch`. `authResend` posts `/resend-code` in verify mode and `/password/forgot` in reset mode (Task 5 relies on that).

- [ ] **Step 1: Append the failing tests**

Append to `test/auth.test.mjs`:

```js
// ── create an account ────────────────────────────────────────────────────────

const REG_MSG = 'Check sam@example.com for a 6-digit code and enter it to finish setting up your account. The code lasts 10 minutes.';

test('register 202 → the code screen with the server line, cooldown armed, password gone', async () => {
  handlers['POST ' + A + '/register'] = accepted('verification_sent', REG_MSG);
  c.renderVals().authGoRegister();
  c.setState({ email: 'sam@example.com', password: 'correct-horse-battery-staple', fullName: ' Sam Okafor ', phone: '' });
  await c.authRegister();
  assert.deepEqual(requests(A + '/register')[0].body, { email: 'sam@example.com', password: 'correct-horse-battery-staple', full_name: 'Sam Okafor', phone: null });
  assert.equal(c.state.authMode, 'verify');
  assert.equal(c.state.authNotice, REG_MSG);
  assert.equal(c.state.password, '');
  assert.equal(c.state.signedIn, false, '202 issues no tokens');
  assert.ok(c.state.authRetryAt > Date.now() + 55 * 1000);
  assert.equal(c.renderVals().authCoolingDown, true);
});

test('register 400 password shows the rule verbatim and stays on the form', async () => {
  handlers['POST ' + A + '/register'] = fail(400, 'password', 'Use at least 12 characters. Length is what makes a password hard to guess; a memorable phrase beats a short one with symbols in it.');
  c.renderVals().authGoRegister();
  c.setState({ email: 'sam@example.com', password: 'short', fullName: 'Sam' });
  await c.authRegister();
  assert.equal(c.state.authMode, 'register');
  assert.equal(c.state.authReason, 'password');
  assert.match(c.state.authError, /^Use at least 12 characters/);
});

test('register 403 registration_closed hides the create-account link from then on', async () => {
  handlers['POST ' + A + '/register'] = fail(403, 'registration_closed', 'Self-registration is off on this deployment. Ask an operator to create your account.');
  c.renderVals().authGoRegister();
  c.setState({ email: 'sam@example.com', password: 'x'.repeat(12), fullName: 'Sam' });
  await c.authRegister();
  assert.equal(c.state.authError, 'Self-registration is off on this deployment. Ask an operator to create your account.');
  assert.equal(c.renderVals().authCanRegister, false);
});

test('verify: a mismatch keeps the typed code and reports attempts left; the right code signs in', async () => {
  c.setState({ authMode: 'verify', email: 'sam@example.com', code: '123 456' });
  handlers['POST ' + A + '/verify-email'] = fail(400, 'mismatch', 'That code is not correct. 4 attempts left.', { attempts_remaining: 4 });
  await c.authVerify();
  assert.equal(requests(A + '/verify-email')[0].body.code, '123456', 'spaces are stripped');
  assert.equal(c.state.code, '123 456', 'kept for a retype');
  assert.equal(c.state.authAttemptsLeft, 4);
  assert.equal(c.state.authError, 'That code is not correct. 4 attempts left.');
  handlers['POST ' + A + '/verify-email'] = session(USER, 3, 'Your email address is confirmed and you are signed in.');
  await c.authVerify();
  assert.equal(c.state.signedIn, true);
  assert.equal(c.state.accessToken, 'acc-3');
  assert.equal(c.state.code, '');
  assert.equal(c.state.toast, 'Your email address is confirmed and you are signed in.');
});

test('verify: a dead code is cleared and resend becomes the emphasised action', async () => {
  c.setState({ authMode: 'verify', email: 'sam@example.com', code: '999999' });
  for (const reason of ['expired', 'too_many_attempts', 'no_code']) {
    handlers['POST ' + A + '/verify-email'] = fail(400, reason, 'dead: ' + reason);
    c.setState({ code: '999999' });
    await c.authVerify();
    assert.equal(c.state.code, '', reason + ' clears the field');
    assert.equal(c.state.authReason, reason);
    assert.equal(c.renderVals().authDeadCode, true);
  }
});

test('resend: 202 clears the typed code and re-arms the cooldown; 429 shows the wait', async () => {
  c.setState({ authMode: 'verify', email: 'sam@example.com', code: '1234' });
  handlers['POST ' + A + '/resend-code'] = accepted('sent', 'If that address needs a code, one is on its way. It lasts 10 minutes.');
  await c.authResend();
  assert.equal(requests(A + '/resend-code').length, 1);
  assert.equal(c.state.code, '', 'the code in the older email is dead');
  assert.equal(c.state.authNotice, 'If that address needs a code, one is on its way. It lasts 10 minutes.');
  assert.equal(c.renderVals().authCoolingDown, true);
  // Still cooling down: the button is inert, nothing is sent.
  await c.authResend();
  assert.equal(requests(A + '/resend-code').length, 1);
  c.setState({ authRetryAt: 0 });
  handlers['POST ' + A + '/resend-code'] = fail(429, 'cooldown', 'A code was just sent. Ask for another in 59 seconds.', { retry_after_seconds: 59 });
  await c.authResend();
  assert.equal(c.state.authError, 'A code was just sent. Ask for another in 59 seconds.');
  assert.match(c.renderVals().authCountdown, /^00:5\d$/);
});

test('verify 404 no_account returns to sign in', async () => {
  c.setState({ authMode: 'verify', email: 'sam@example.com', code: '123456' });
  handlers['POST ' + A + '/verify-email'] = fail(404, 'no_account', 'That account no longer exists.');
  await c.authVerify();
  assert.equal(c.state.authMode, 'signin');
  assert.equal(c.state.authError, 'That account no longer exists.');
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `node --test test/auth.test.mjs`
Expected: the seven new tests FAIL — `c.authRegister is not a function`.

- [ ] **Step 3: Add the methods to `authMethods` in `src/logic/auth.js`**

Insert after `authSignIn()` (before the `// ── cooldowns and lockouts` comment):

```js
  async authRegister() {
    const s = this.state;
    if (s.authBusy) return;
    this.setState({ authBusy: true, authError: '', authNotice: '', authReason: '' });
    try {
      const resp = await authApi.register({ email: trimmed(s.email), password: s.password, full_name: trimmed(s.fullName), phone: trimmed(s.phone) || null });
      this.authAccepted(resp, 'verify');
    } catch (e) {
      const patch = Object.assign({ authBusy: false }, failurePatch(e));
      if (e && e.reason === 'registration_closed') patch.authConfig = Object.assign({}, s.authConfig || AUTH_FALLBACK_CONFIG, { self_registration: false });
      this.setState(patch);
    }
  },

  // A 202 that sent a code: the code screen, the server's line, and the resend cooldown
  // armed from the limits it published. Any earlier code is dead, so whatever was typed goes.
  // The password is not needed again — verify-email signs the person in — so it leaves memory.
  authAccepted(resp, mode) {
    const otp = Object.assign({}, this.authOtp(), (resp && resp.otp) || {});
    this.setState({ authBusy: false, authMode: mode, authNotice: (resp && resp.message) || '', authError: '', authReason: '', authAttemptsLeft: null, code: '', password: '' });
    this.authArm(otp.resend_cooldown_seconds);
  },

  async authVerify() {
    const s = this.state;
    if (s.authBusy) return;
    this.setState({ authBusy: true, authError: '', authReason: '' });
    try {
      this.authEnter(await authApi.verifyEmail(trimmed(s.email), normaliseCode(s.code)));
    } catch (e) {
      this.setState(Object.assign({ authBusy: false }, this.authCodeFailure(e)));
    }
  },

  // A refused code, for verify-email and password/reset alike. A dead code is cleared so the
  // emphasis moves to Resend; a wrong one is kept for a retype.
  authCodeFailure(e) {
    const patch = failurePatch(e);
    patch.authAttemptsLeft = attemptsLeft(e);
    if (e && DEAD_CODE.has(e.reason)) patch.code = '';
    if (e && e.status === 404) patch.authMode = 'signin';
    return patch;
  },

  // Verify mode asks for another confirmation code; reset mode asks for another reset code.
  // The two purposes are separate on the server and a confirmation code will not reset.
  async authResend() {
    const s = this.state;
    if (s.authBusy || this.authCooling()) return;
    this.setState({ authBusy: true, authError: '', authReason: '' });
    try {
      const email = trimmed(s.email);
      const resp = s.authMode === 'reset' ? await authApi.forgot(email) : await authApi.resendCode(email);
      this.authAccepted(resp, s.authMode);
    } catch (e) {
      if (e && e.status === 429) this.authArm(retryAfter(e));
      this.setState(Object.assign({ authBusy: false }, failurePatch(e)));
    }
  },
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `node --test test/auth.test.mjs`
Expected: 20 passing.

- [ ] **Step 5: Run every suite**

Run: `npm test` → all green.

---

### Task 5: Forgotten password — forgot and reset

**Files:**
- Modify: `src/logic/auth.js`
- Test: `test/auth.test.mjs` (append)

**Interfaces:**
- Consumes: Task 4 `authAccepted`, `authCodeFailure`, `authResend`.
- Produces: `authForgot()`, `authReset()`.

- [ ] **Step 1: Append the failing tests**

```js
// ── forgotten password ───────────────────────────────────────────────────────

const FORGOT_MSG = 'If that address has an account, a reset code is on its way. It is valid for 10 minutes.';

test('forgot 202 → reset screen with the server line; 429 shows the wait', async () => {
  c.renderVals().authGoForgot();
  c.setState({ email: 'sam@example.com' });
  handlers['POST ' + A + '/password/forgot'] = accepted('accepted', FORGOT_MSG);
  await c.authForgot();
  assert.deepEqual(requests(A + '/password/forgot')[0].body, { email: 'sam@example.com' });
  assert.equal(c.state.authMode, 'reset');
  assert.equal(c.state.authNotice, FORGOT_MSG);
  assert.equal(c.renderVals().authCoolingDown, true);
  c.setState({ authMode: 'forgot', authRetryAt: 0 });
  handlers['POST ' + A + '/password/forgot'] = fail(429, 'hourly_cap', 'Too many codes for that address this hour.', { retry_after_seconds: 1800 });
  await c.authForgot();
  assert.equal(c.state.authMode, 'forgot');
  assert.equal(c.state.authError, 'Too many codes for that address this hour.');
  // ceil() of the remaining 1799.9 s reads 30:00 until a whole second has passed.
  assert.match(c.renderVals().authCountdown, /^(30:00|29:5\d)$/);
});

test('reset: a rejected password keeps the code, a wrong code keeps the code minus an attempt, success returns to sign in', async () => {
  c.setState({ authMode: 'reset', email: 'sam@example.com', code: '418902', newPassword: 'correct-horse-battery-staple' });
  handlers['POST ' + A + '/password/reset'] = fail(400, 'password_unchanged', 'That is the password the account already has. If you are resetting it because someone else may know it, choose a different one. Your code is still valid.');
  await c.authReset();
  assert.deepEqual(requests(A + '/password/reset')[0].body, { email: 'sam@example.com', code: '418902', new_password: 'correct-horse-battery-staple' });
  assert.equal(c.state.code, '418902', 'the code survives a rejected password');
  assert.equal(c.state.newPassword, '', 'only the password is retyped');
  assert.equal(c.state.authReason, 'password_unchanged');

  c.setState({ newPassword: 'a-brand-new-long-passphrase' });
  handlers['POST ' + A + '/password/reset'] = fail(400, 'mismatch', 'That code is not correct. 3 attempts left.', { attempts_remaining: 3 });
  await c.authReset();
  assert.equal(c.state.code, '418902');
  assert.equal(c.state.authAttemptsLeft, 3);

  handlers['POST ' + A + '/password/reset'] = [200, { ok: true, message: 'Your password is set. Sign in with it — every other session has been signed out.', sessions_ended: 1 }];
  await c.authReset();
  assert.equal(c.state.authMode, 'signin');
  assert.equal(c.state.authNotice, 'Your password is set. Sign in with it — every other session has been signed out.');
  assert.equal(c.state.email, 'sam@example.com', 'kept for the sign-in');
  assert.equal(c.state.code, '');
  assert.equal(c.state.newPassword, '');
  assert.equal(c.state.signedIn, false, 'a reset deliberately does not sign in');
});

test('reset: a dead code is cleared; resend in reset mode asks /password/forgot', async () => {
  c.setState({ authMode: 'reset', email: 'sam@example.com', code: '000000', newPassword: 'a-brand-new-long-passphrase' });
  handlers['POST ' + A + '/password/reset'] = fail(400, 'expired', 'That code has expired. Ask for a new one.');
  await c.authReset();
  assert.equal(c.state.code, '');
  assert.equal(c.renderVals().authDeadCode, true);
  handlers['POST ' + A + '/password/forgot'] = accepted('accepted', FORGOT_MSG);
  await c.authResend();
  assert.equal(requests(A + '/password/forgot').length, 1);
  assert.equal(requests(A + '/resend-code').length, 0);
  assert.equal(c.state.authMode, 'reset');
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `node --test test/auth.test.mjs` → 3 new FAIL (`c.authForgot is not a function`).

- [ ] **Step 3: Add the methods to `authMethods`**

Insert after `authResend()`:

```js
  async authForgot() {
    const s = this.state;
    if (s.authBusy) return;
    this.setState({ authBusy: true, authError: '', authNotice: '', authReason: '' });
    try {
      this.authAccepted(await authApi.forgot(trimmed(s.email)), 'reset');
    } catch (e) {
      if (e && e.status === 429) this.authArm(retryAfter(e));
      this.setState(Object.assign({ authBusy: false }, failurePatch(e)));
    }
  },

  // The server verifies the code without consuming it and spends it only when the write is
  // certain, so a rejected password leaves the code good: only the password is retyped.
  async authReset() {
    const s = this.state;
    if (s.authBusy) return;
    this.setState({ authBusy: true, authError: '', authReason: '' });
    try {
      const resp = await authApi.reset(trimmed(s.email), normaliseCode(s.code), s.newPassword);
      this.setState({ authBusy: false, authMode: 'signin', authNotice: (resp && resp.message) || '', authError: '', authReason: '', authAttemptsLeft: null, authRetryAt: 0, code: '', newPassword: '', password: '' });
    } catch (e) {
      const patch = this.authCodeFailure(e);
      if (e && (e.reason === 'password' || e.reason === 'password_unchanged')) patch.newPassword = '';
      this.setState(Object.assign({ authBusy: false }, patch));
    }
  },
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `node --test test/auth.test.mjs` → 23 passing.

- [ ] **Step 5: Run every suite**

Run: `npm test` → all green.

---

### Task 6: Reload, refresh single-flight, and the two-tab trap

**Files:**
- Modify: `src/logic/auth.js` (`authStorageChanged` body)
- Test: `test/auth.test.mjs` (append)

**Interfaces:**
- Consumes: Task 1 interceptor, Task 3 `authBoot`/`authRefresh`.
- Produces: `authStorageChanged(e)` — adopts another tab's rotated token or sign-out.

- [ ] **Step 1: Append the failing tests**

```js
// ── reload, refresh, and other tabs ──────────────────────────────────────────

const seedSession = (user, n) => { mem[SESSION_KEY] = JSON.stringify({ signedIn: true, refreshToken: 'ref-' + n, account: user, view: 'buildings', role: 'user', navOpen: true }); };

test('reload: the shell renders on the stored identity, one refresh rotates both tokens and keeps the page', async () => {
  cleanup();
  seedSession(USER, 1);
  handlers['POST ' + A + '/refresh'] = session(Object.assign({}, USER, { full_name: 'Sam O.' }), 2);
  c = new HoistraLogic();
  calls = []; // beforeEach's fresh() already read /config once; measure only the reload
  assert.equal(c.state.signedIn, true, 'optimistic — no gate flash');
  assert.equal(c.state.account.full_name, 'Sam Okafor');
  assert.equal(c.state.accessToken, null);
  await c.authBoot();
  const r = requests(A + '/refresh');
  assert.equal(r.length, 1);
  assert.deepEqual(r[0].body, { refresh_token: 'ref-1' });
  assert.equal(c.state.accessToken, 'acc-2');
  assert.equal(c.state.refreshToken, 'ref-2');
  assert.equal(stored().refreshToken, 'ref-2');
  assert.equal(c.state.account.full_name, 'Sam O.', 'the server copy replaces the summary');
  assert.equal(c.state.view, 'buildings', 'a refresh does not move the page');
  assert.equal(c.state.authBooting, false);
  assert.equal(requests(A + '/config').length, 0, 'config is only read for the gate');
});

test('reload: a replayed refresh token drops to the gate with the server line and clears storage', async () => {
  cleanup();
  seedSession(USER, 1);
  handlers['POST ' + A + '/refresh'] = fail(401, 'replayed', 'That session was already used. Every session has been ended as a precaution — sign in again.');
  c = new HoistraLogic();
  await c.authBoot();
  assert.equal(c.state.signedIn, false);
  assert.equal(c.state.authMode, 'signin');
  assert.equal(c.state.authNotice, 'That session was already used. Every session has been ended as a precaution — sign in again.');
  assert.equal(mem[SESSION_KEY], undefined);
});

test('reload: a network failure keeps the person signed in on the stored account', async () => {
  cleanup();
  seedSession(USER, 1);
  c = new HoistraLogic();
  await c.authBoot();
  assert.equal(c.state.signedIn, true);
  assert.equal(c.state.refreshToken, 'ref-1');
  assert.equal(c.state.account.email, 'sam@example.com');
  assert.equal(c.state.authBooting, false);
});

test('a bearer call that returns 401 expired is refreshed once and retried once', async () => {
  handlers['POST ' + A + '/login'] = session(USER, 1);
  c.setState({ email: 'sam@example.com', password: 'x'.repeat(12) });
  await c.authSignIn();
  handlers['POST ' + A + '/refresh'] = session(USER, 2);
  handlers['GET /backend/ops-intelligence/api/approvals'] = (u, o) => (o.headers.Authorization === 'Bearer acc-2' ? [200, { ok: true, items: [] }] : fail(401, 'expired', 'Token expired'));
  const r = await apiFetch(BASES.opsIntelligence, '/api/approvals');
  assert.equal(r.ok, true);
  assert.equal(requests(A + '/refresh').length, 1);
  assert.equal(requests('/backend/ops-intelligence/api/approvals').length, 2);
  assert.equal(c.state.accessToken, 'acc-2');
  assert.equal(c.state.signedIn, true);
});

test('two calls expiring together share one refresh', async () => {
  handlers['POST ' + A + '/login'] = session(USER, 1);
  c.setState({ email: 'sam@example.com', password: 'x'.repeat(12) });
  await c.authSignIn();
  handlers['POST ' + A + '/refresh'] = session(USER, 2);
  const guard = (u, o) => (o.headers.Authorization === 'Bearer acc-2' ? [200, { ok: true }] : fail(401, 'expired', 'Token expired'));
  handlers['GET /backend/ops-intelligence/api/a'] = guard;
  handlers['GET /backend/ops-intelligence/api/b'] = guard;
  await Promise.all([apiFetch(BASES.opsIntelligence, '/api/a'), apiFetch(BASES.opsIntelligence, '/api/b')]);
  assert.equal(requests(A + '/refresh').length, 1, 'single-flight');
});

test('a terminal 401 on any call signs out with the server line', async () => {
  handlers['POST ' + A + '/login'] = session(USER, 1);
  c.setState({ email: 'sam@example.com', password: 'x'.repeat(12) });
  await c.authSignIn();
  handlers['GET /backend/ops-intelligence/api/a'] = fail(401, 'session_revoked', 'That session has ended. Sign in again.');
  await assert.rejects(apiFetch(BASES.opsIntelligence, '/api/a'));
  assert.equal(c.state.signedIn, false);
  assert.equal(c.state.authNotice, 'That session has ended. Sign in again.');
  assert.equal(mem[SESSION_KEY], undefined);
  assert.equal(requests(A + '/refresh').length, 0);
});

test('a refresh presents the token from storage, not a stale copy in memory', async () => {
  handlers['POST ' + A + '/login'] = session(USER, 1);
  c.setState({ email: 'sam@example.com', password: 'x'.repeat(12) });
  await c.authSignIn();
  // Another tab rotated to ref-7 and wrote it.
  mem[SESSION_KEY] = JSON.stringify(Object.assign(stored(), { refreshToken: 'ref-7' }));
  handlers['POST ' + A + '/refresh'] = session(USER, 8);
  await c.authRefresh();
  assert.equal(requests(A + '/refresh')[0].body.refresh_token, 'ref-7');
  assert.equal(c.state.refreshToken, 'ref-8');
});

test('another tab rotating the token is adopted; another tab signing out signs this one out', async () => {
  handlers['POST ' + A + '/login'] = session(USER, 1);
  c.setState({ email: 'sam@example.com', password: 'x'.repeat(12) });
  await c.authSignIn();
  mem[SESSION_KEY] = JSON.stringify(Object.assign(stored(), { refreshToken: 'ref-5', account: Object.assign({}, USER, { full_name: 'Sam Five' }) }));
  c.authStorageChanged({ key: SESSION_KEY });
  assert.equal(c.state.refreshToken, 'ref-5');
  assert.equal(c.state.account.full_name, 'Sam Five');
  assert.equal(c.state.signedIn, true);
  c.authStorageChanged({ key: 'something.else' });
  assert.equal(c.state.signedIn, true, 'other keys are ignored');
  delete mem[SESSION_KEY];
  c.authStorageChanged({ key: SESSION_KEY });
  assert.equal(c.state.signedIn, false);
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `node --test test/auth.test.mjs`
Expected: the reload tests pass already (Task 3 wrote `authBoot`/`authRefresh`); the last test FAILS because `authStorageChanged` is a no-op.

- [ ] **Step 3: Implement `authStorageChanged` in `src/logic/auth.js`**

Replace the stub:

```js
  // Another tab rotated the refresh token, or signed out. Filled in by Task 6.
  authStorageChanged() {},
```
with:
```js
  // Two tabs share the stored refresh token but each keeps its own access token. When the
  // other tab rotates, this one adopts the new token so its next refresh is not a replay;
  // when the other tab signs out, this one follows. Same-tab writes never fire this event.
  authStorageChanged(e) {
    if (e && e.key && e.key !== SESSION_KEY) return;
    const s = loadSession();
    if (!s.refreshToken) { if (this.state.signedIn) this.authSignedOut(''); return; }
    if (s.refreshToken !== this.state.refreshToken) {
      this.setState({ refreshToken: s.refreshToken, account: s.account || this.state.account });
    }
  },
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `node --test test/auth.test.mjs` → 31 passing.

- [ ] **Step 5: Run every suite**

Run: `npm test` → all green.

---

### Task 7: Change password and sign out everywhere

**Files:**
- Modify: `src/logic/auth.js`
- Test: `test/auth.test.mjs` (append)

**Interfaces:**
- Consumes: Task 3 `authSignedOut`, `flash`; `authApi.changePassword`, `authApi.logoutEverywhere`.
- Produces: `pwOpenModal()`, `pwClose()`, `pwSubmit()`, `authSignOutEverywhere()`.

- [ ] **Step 1: Append the failing tests**

```js
// ── inside a session ─────────────────────────────────────────────────────────

test('change password 200 signs out with the server line — the API ended this session too', async () => {
  handlers['POST ' + A + '/login'] = session(USER, 1);
  c.setState({ email: 'sam@example.com', password: 'x'.repeat(12) });
  await c.authSignIn();
  c.setState({ acctOpen: true });
  c.renderVals().acctItems.find((i) => i.label === 'Change password').click();
  assert.equal(c.state.pwOpen, true);
  assert.equal(c.state.acctOpen, false);
  c.setState({ pwCurrent: 'x'.repeat(12), pwNext: 'yet-another-good-passphrase' });
  handlers['POST ' + A + '/password/change'] = [200, { ok: true, message: 'Your password is changed. Sign in again with the new one.', sessions_ended: 2 }];
  await c.pwSubmit();
  const r = requests(A + '/password/change')[0];
  assert.deepEqual(r.body, { current_password: 'x'.repeat(12), new_password: 'yet-another-good-passphrase' });
  assert.equal(r.headers.Authorization, 'Bearer acc-1');
  assert.equal(c.state.pwOpen, false);
  assert.equal(c.state.signedIn, false);
  assert.equal(c.state.accessToken, null);
  assert.equal(c.state.authNotice, 'Your password is changed. Sign in again with the new one.');
  assert.equal(c.state.pwCurrent, '');
  assert.equal(c.state.pwNext, '');
});

test('change password 401 and 400 stay in the modal with the message', async () => {
  handlers['POST ' + A + '/login'] = session(USER, 1);
  c.setState({ email: 'sam@example.com', password: 'x'.repeat(12) });
  await c.authSignIn();
  c.pwOpenModal();
  c.setState({ pwCurrent: 'nope', pwNext: 'yet-another-good-passphrase' });
  handlers['POST ' + A + '/password/change'] = fail(401, 'invalid_credentials', 'Your current password is not correct.');
  await c.pwSubmit();
  assert.equal(c.state.signedIn, true, 'a wrong current password is not a dead session');
  assert.equal(c.state.pwOpen, true);
  assert.equal(c.state.pwError, 'Your current password is not correct.');
  assert.equal(c.state.pwBusy, false);
  handlers['POST ' + A + '/password/change'] = fail(400, 'password_unchanged', 'That is the password you already have.');
  await c.pwSubmit();
  assert.equal(c.state.pwError, 'That is the password you already have.');
  c.pwClose();
  assert.equal(c.state.pwOpen, false);
  assert.equal(c.state.pwError, '');
});

test('sign out everywhere sends everywhere:true with the bearer, then signs out locally', async () => {
  handlers['POST ' + A + '/login'] = session(USER, 1);
  c.setState({ email: 'sam@example.com', password: 'x'.repeat(12) });
  await c.authSignIn();
  handlers['POST ' + A + '/logout'] = [200, { ok: true, message: 'Signed out.', sessions_ended: 3 }];
  await c.renderVals().acctItems.find((i) => i.label === 'Sign out everywhere').click();
  const r = requests(A + '/logout')[0];
  assert.deepEqual(r.body, { everywhere: true });
  assert.equal(r.headers.Authorization, 'Bearer acc-1');
  assert.equal(c.state.signedIn, false);
  assert.equal(c.state.toast, 'Signed out of 3 sessions.');
  assert.equal(mem[SESSION_KEY], undefined);
});

test('sign out everywhere still signs out locally when the call fails', async () => {
  handlers['POST ' + A + '/login'] = session(USER, 1);
  c.setState({ email: 'sam@example.com', password: 'x'.repeat(12) });
  await c.authSignIn();
  await c.authSignOutEverywhere();
  assert.equal(c.state.signedIn, false);
  assert.equal(c.state.toast, 'Signed out.');
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `node --test test/auth.test.mjs` → 4 new FAIL (`this.pwOpenModal is not a function`).

- [ ] **Step 3: Add the methods to `authMethods`**

Insert after `authSignOut()`:

```js
  async authSignOutEverywhere() {
    let ended = null;
    try {
      const r = await authApi.logoutEverywhere();
      ended = typeof r.sessions_ended === 'number' ? r.sessions_ended : null;
    } catch (e) { /* signing out locally is the point; the server side is best effort */ }
    this.authSignedOut('');
    this.flash(ended === null ? 'Signed out.' : 'Signed out of ' + ended + ' session' + (ended === 1 ? '' : 's') + '.');
  },

  // ── change password ────────────────────────────────────────────────────
  pwOpenModal() { this.setState({ pwOpen: true, acctOpen: false, pwCurrent: '', pwNext: '', pwError: '', pwBusy: false }); },
  pwClose() { this.setState({ pwOpen: false, pwCurrent: '', pwNext: '', pwError: '', pwBusy: false }); },

  async pwSubmit() {
    const s = this.state;
    if (s.pwBusy) return;
    this.setState({ pwBusy: true, pwError: '' });
    try {
      const resp = await authApi.changePassword(s.pwCurrent, s.pwNext);
      // Every session is ended, this one included: the next call would be 401 password_changed.
      this.authSignedOut((resp && resp.message) || 'Your password is changed. Sign in again with the new one.');
    } catch (e) {
      if (!this.state.signedIn) return; // a terminal 401 already signed us out
      const msg = !e || typeof e.status !== 'number' ? NETWORK_MSG
        : e.status === 422 ? 'Enter your current password and a new one.'
        : e.message;
      this.setState({ pwBusy: false, pwError: msg });
    }
  },
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `node --test test/auth.test.mjs` → 35 passing.

- [ ] **Step 5: Run every suite**

Run: `npm test` → all green.

---

### Task 8: `GatePanel.jsx` and the `Gate.jsx` swap

**Files:**
- Create: `src/components/shell/GatePanel.jsx`
- Modify: `src/screens/Gate.jsx:284-311`

**Interfaces:**
- Consumes: every `auth*`, `set*`, `email/password/fullName/phone/code/newPassword` key from `authVals` (Task 3).

- [ ] **Step 1: Create `src/components/shell/GatePanel.jsx`**

```jsx
// GatePanel — the sign-in column of the gate: sign in, create an account, enter a code,
// forgotten and reset password. One panel, five modes, driven by vals.authMode (logic/auth.js).
// Server messages are shown as the server wrote them. `vals` is the view model from useHoistra().
import React from 'react';

const KICKER = { fontSize: "10.5px", letterSpacing: "0.13em", textTransform: "uppercase", color: "var(--color-neutral-500)" };
const H2 = { fontSize: "26px", margin: "12px 0 0", lineHeight: "1.15" };
const BLURB = { fontSize: "12.5px", lineHeight: "1.55", color: "var(--color-neutral-400)", margin: "9px 0 0" };
const INPUT = { width: "100%", boxSizing: "border-box", fontSize: "14px", padding: "11px 13px", borderRadius: "8px", border: "1px solid var(--color-divider)", background: "var(--color-bg)", color: "var(--color-text)", fontFamily: "var(--font-body)", outline: "none" };
const PRIMARY = { textAlign: "center", padding: "11px", borderRadius: "8px", background: "var(--color-text)", color: "var(--color-bg)", fontSize: "14px", cursor: "pointer" };
const SECONDARY = { textAlign: "center", padding: "11px", borderRadius: "8px", border: "1px solid var(--color-divider)", fontSize: "13.5px", color: "var(--color-neutral-300)", cursor: "pointer" };
const DISABLED = { opacity: 0.55, cursor: "default", pointerEvents: "none" };
const LINK = { fontSize: "11.5px", color: "var(--color-neutral-400)", cursor: "pointer" };
const HINT = { fontSize: "11px", color: "var(--color-neutral-500)", lineHeight: "1.5", marginTop: "-3px" };
const NOTE = { fontSize: "12px", lineHeight: "1.5", color: "var(--color-neutral-300)", marginTop: "14px" };
const ERROR = { fontSize: "12px", lineHeight: "1.5", color: "var(--st-risk)", marginTop: "12px" };

const COPY = {
  signin:   { kicker: "Member access", title: "Sign in", blurb: "Your hoisted portfolio is waiting. The reading is done. The decisions are yours." },
  register: { kicker: "New account", title: "Create an account", blurb: "Set a password you can remember. You'll confirm your email with a six-digit code." },
  verify:   { kicker: "Confirm your email", title: "Enter the code", blurb: null },
  forgot:   { kicker: "Password reset", title: "Forgot your password?", blurb: "Enter your email and we'll send a code to reset it." },
  reset:    { kicker: "Password reset", title: "Set a new password", blurb: null }
};

function Field(p) {
  return <input className="input" type={p.type || "text"} value={p.value} onChange={p.onChange} onKeyDown={p.onKey} placeholder={p.placeholder} autoComplete={p.auto} inputMode={p.inputMode} style={INPUT} />;
}

function Button(p) {
  const base = p.secondary ? SECONDARY : PRIMARY;
  return <div className={p.secondary ? "hv4" : "hv3"} onClick={p.disabled ? undefined : p.onClick} style={p.disabled ? { ...base, ...DISABLED } : base}>{p.label}</div>;
}

export default function GatePanel({ vals }) {
  const m = vals.authMode;
  const c = COPY[m] || COPY.signin;
  const busy = vals.authBusy;
  const codeScreen = m === "verify" || m === "reset";
  const blurb = codeScreen ? "Sent to " + (vals.email || "your email") + "." : c.blurb;
  const resendLabel = busy ? "Sending…" : vals.authCoolingDown ? "Resend code · " + vals.authCountdown : "Resend code";
  const minHint = "At least " + vals.authMinLength + " characters — a memorable phrase beats a short one with symbols.";
  const codePh = vals.authCodeLength + "-digit code";
  return (
    <div style={{ width: "100%" }}>
      <div style={KICKER}>{c.kicker}</div>
      <h2 style={H2}>{c.title}</h2>
      <p style={BLURB}>{blurb}</p>
      {vals.authNotice ? <div style={NOTE}>{vals.authNotice}</div> : null}
      <div style={{ display: "flex", flexDirection: "column", gap: "9px", marginTop: vals.authNotice ? "16px" : "26px" }}>
        {m === "signin" ? <>
          <Field value={vals.email} onChange={vals.setEmail} onKey={vals.authKey} placeholder="you@portfolio.com" auto="username" type="email" />
          <Field value={vals.password} onChange={vals.setPassword} onKey={vals.authKey} placeholder="Password" auto="current-password" type="password" />
          <Button label={busy ? "Signing in…" : "Continue"} onClick={vals.authSignIn} disabled={busy} />
          <Button label="Single sign-on" onClick={vals.authSSO} secondary />
        </> : null}
        {m === "register" ? <>
          <Field value={vals.fullName} onChange={vals.setFullName} onKey={vals.authKey} placeholder="Full name" auto="name" />
          <Field value={vals.email} onChange={vals.setEmail} onKey={vals.authKey} placeholder="you@portfolio.com" auto="email" type="email" />
          <Field value={vals.password} onChange={vals.setPassword} onKey={vals.authKey} placeholder="Password" auto="new-password" type="password" />
          <div style={HINT}>{minHint}</div>
          <Field value={vals.phone} onChange={vals.setPhone} onKey={vals.authKey} placeholder="Phone (optional)" auto="tel" type="tel" />
          <Button label={busy ? "Creating…" : "Create account"} onClick={vals.authRegister} disabled={busy} />
        </> : null}
        {m === "verify" ? <>
          <Field value={vals.code} onChange={vals.setCode} onKey={vals.authKey} placeholder={codePh} auto="one-time-code" inputMode="numeric" />
          <Button label={busy ? "Confirming…" : "Confirm"} onClick={vals.authVerify} disabled={busy} secondary={vals.authDeadCode} />
          <Button label={resendLabel} onClick={vals.authResend} disabled={busy || vals.authCoolingDown} secondary={!vals.authDeadCode} />
        </> : null}
        {m === "forgot" ? <>
          <Field value={vals.email} onChange={vals.setEmail} onKey={vals.authKey} placeholder="you@portfolio.com" auto="email" type="email" />
          <Button label={busy ? "Sending…" : "Send reset code"} onClick={vals.authForgot} disabled={busy} />
        </> : null}
        {m === "reset" ? <>
          <Field value={vals.code} onChange={vals.setCode} onKey={vals.authKey} placeholder={codePh} auto="one-time-code" inputMode="numeric" />
          <Field value={vals.newPassword} onChange={vals.setNewPassword} onKey={vals.authKey} placeholder="New password" auto="new-password" type="password" />
          <div style={HINT}>{minHint}</div>
          <Button label={busy ? "Setting…" : "Set password"} onClick={vals.authReset} disabled={busy} secondary={vals.authDeadCode} />
          <Button label={resendLabel} onClick={vals.authResend} disabled={busy || vals.authCoolingDown} secondary={!vals.authDeadCode} />
        </> : null}
      </div>
      {vals.authError ? <div style={ERROR}>{vals.authError}</div> : null}
      {vals.authLocked ? (
        <div style={{ ...NOTE, marginTop: "6px" }}>
          {"Unlocks in " + vals.authCountdown + " · "}
          <span className="hv6" onClick={vals.authGoForgot} style={{ ...LINK, color: "var(--color-text)" }}>{"Reset your password"}</span>
        </div>
      ) : null}
      <div style={{ display: "flex", justifyContent: "space-between", gap: "12px", marginTop: "14px" }}>
        {m === "signin" ? <>
          <span className="hv6" onClick={vals.authGoForgot} style={LINK}>{"Forgot password?"}</span>
          {vals.authCanRegister ? <span className="hv6" onClick={vals.authGoRegister} style={LINK}>{"Create an account"}</span> : null}
        </> : <span className="hv6" onClick={vals.authGoSignin} style={LINK}>{"Back to sign in"}</span>}
      </div>
      <div style={{ fontSize: "11px", color: "var(--color-neutral-500)", lineHeight: "1.55", marginTop: "20px" }}>
        {"No FM cooperation required. Meter consent is captured at onboarding for MPAN and MPRN feeds."}
      </div>
      <a href="Hoistway Customer Journey.dc.html" style={{ fontSize: "11.5px", display: "inline-block", marginTop: "22px" }}>
        {"Read the customer journey →"}
      </a>
    </div>
  );
}
```

- [ ] **Step 2: Swap the panel in `src/screens/Gate.jsx`**

Add the import after `import React from 'react';`:

```js
import GatePanel from '../components/shell/GatePanel.jsx';
```

Replace the inner block of the sticky right column — everything from `<div style={{ width: "100%" }}>` (line 285) through its closing `</div>` (line 310), i.e. the "Member access" kicker, the `Sign in` h2, the paragraph, the email input, the two buttons, the footnote and the customer-journey link — with the single line:

```jsx
          <GatePanel vals={vals} />
```

The sticky column `<div style={{ position: "sticky", top: "0", height: "100vh", … }}>` on line 284 and its closing tag stay. Nothing above line 284 changes.

- [ ] **Step 3: Verify it compiles**

Run: `npm run build`
Expected: `✓ built in …` with no errors. (JSX is not unit-tested in this repo; Task 11 exercises the panel in a browser.)

- [ ] **Step 4: Confirm no reference to the removed handlers remains**

Run: `grep -rn "vals.signIn\b\|vals.gateKey\|gateKey" src`
Expected: no output.

---

### Task 9: TopBar identity, the change-password modal, and `App.jsx`

**Files:**
- Modify: `src/components/shell/TopBar.jsx:41-53`
- Create: `src/components/shell/PasswordModal.jsx`
- Modify: `src/App.jsx`

**Interfaces:**
- Consumes: `acctInitial`, `acctName`, `acctEmail`, `pw*`, `authMinLength` from `authVals`.

- [ ] **Step 1: Edit `src/components/shell/TopBar.jsx`**

Line 41 — change `title="Aasim"` to `title={vals.acctName}`.

Line 42 — change `{"A"}` to `{vals.acctInitial}`.

Lines 48–53 — replace the header block:
```jsx
                  <div style={{ fontSize: "12.5px" }}>
                    {"Aasim"}
                  </div>
                  <div style={{ fontSize: "10.5px", color: "var(--color-neutral-500)", marginTop: "2px" }}>
                    {vals.acctRole}{" · Planum Technologies"}
                  </div>
```
with:
```jsx
                  <div style={{ fontSize: "12.5px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {vals.acctName}
                  </div>
                  <div style={{ fontSize: "10.5px", color: "var(--color-neutral-400)", marginTop: "1px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                    {vals.acctEmail}
                  </div>
                  <div style={{ fontSize: "10.5px", color: "var(--color-neutral-500)", marginTop: "2px" }}>
                    {vals.acctRole}{" · Planum Technologies"}
                  </div>
```

Nothing else in the file changes — the `acctItems` loop and the Sign out row already read `vals`.

- [ ] **Step 2: Create `src/components/shell/PasswordModal.jsx`**

```jsx
// PasswordModal — change the password from inside a session (account menu → Change password).
// On success the API ends every session, this one included, so the controller signs out and
// the gate shows the server's line. `vals` is the view model from useHoistra().
import React from 'react';

const LABEL = { fontSize: "10px", letterSpacing: "0.09em", textTransform: "uppercase", color: "var(--color-neutral-500)" };
const INPUT = { width: "100%", boxSizing: "border-box", marginTop: "6px", fontSize: "12.5px", padding: "8px 10px", borderRadius: "7px", border: "1px solid var(--color-divider)", background: "var(--color-bg)", color: "var(--color-text)", fontFamily: "var(--font-body)", outline: "none" };

export default function PasswordModal({ vals }) {
  return (
    <>
      <div onClick={vals.pwClose} style={{ position: "fixed", inset: "0", background: "var(--scrim)", zIndex: "80" }}></div>
      <div style={{ position: "fixed", top: "50%", left: "50%", transform: "translate(-50%,-50%)", zIndex: "81", width: "min(420px,calc(100vw - 48px))", borderRadius: "12px", background: "var(--color-surface)", boxShadow: "var(--shadow-lg)", overflow: "hidden", animation: "fadeUp 0.2s ease both" }}>
        <div style={{ display: "flex", alignItems: "flex-start", gap: "12px", padding: "15px 17px", borderBottom: "1px solid var(--color-divider)" }}>
          <div style={{ flex: "1", minWidth: "0" }}>
            <div style={{ fontSize: "14px" }}>
              {"Change password"}
            </div>
            <div style={{ fontSize: "11px", color: "var(--color-neutral-500)", marginTop: "3px" }}>
              {"Every session signs out, this one included — you'll sign in again with the new one."}
            </div>
          </div>
          <i className="ph ph-x hv16" onClick={vals.pwClose} style={{ fontSize: "15px", color: "var(--color-neutral-500)", cursor: "pointer" }}></i>
        </div>
        <div style={{ padding: "15px 17px", display: "flex", flexDirection: "column", gap: "12px" }}>
          <div>
            <div style={LABEL}>{"Current password"}</div>
            <input className="input" type="password" autoComplete="current-password" value={vals.pwCurrent} onChange={vals.pwSetCurrent} onKeyDown={vals.pwKey} style={INPUT} />
          </div>
          <div>
            <div style={LABEL}>{"New password"}</div>
            <input className="input" type="password" autoComplete="new-password" value={vals.pwNext} onChange={vals.pwSetNext} onKeyDown={vals.pwKey} style={INPUT} />
            <div style={{ fontSize: "11px", color: "var(--color-neutral-500)", lineHeight: "1.5", marginTop: "6px" }}>
              {"At least " + vals.authMinLength + " characters — a memorable phrase beats a short one with symbols."}
            </div>
          </div>
          {vals.pwError ? <div style={{ fontSize: "12px", lineHeight: "1.5", color: "var(--st-risk)" }}>{vals.pwError}</div> : null}
          <div style={{ display: "flex", alignItems: "center", gap: "9px", justifyContent: "flex-end", marginTop: "2px" }}>
            <div className="hv16" onClick={vals.pwClose} style={{ fontSize: "12px", padding: "7px 13px", borderRadius: "8px", border: "1px solid var(--color-divider)", color: "var(--color-neutral-400)", cursor: "pointer" }}>
              {"Cancel"}
            </div>
            <div className="btn btn-primary" onClick={vals.pwBusy ? undefined : vals.pwSubmit} style={{ fontSize: "12px", padding: "7px 15px", cursor: vals.pwBusy ? "default" : "pointer", opacity: vals.pwBusy ? 0.55 : 1 }}>
              {vals.pwBusy ? "Changing…" : "Change password"}
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
```

- [ ] **Step 3: Render it from `src/App.jsx`**

Add the import after `import ConnectModal from './components/shell/ConnectModal.jsx';`:

```js
import PasswordModal from './components/shell/PasswordModal.jsx';
```

Add, directly after the line `{vals.intModalOn ? <ConnectModal vals={vals} /> : null}`:

```jsx
      {vals.pwOpen ? <PasswordModal vals={vals} /> : null}
```

- [ ] **Step 4: Verify it compiles and the suites are green**

Run: `npm run build && npm test`
Expected: build succeeds; all tests pass.

---

### Task 10: Documentation

**Files:**
- Modify: `docs/shell-and-shared-components.md` §4.1 and §4.2

- [ ] **Step 1: Replace §4.1**

Replace:
```markdown
### 4.1 Sign-in gate
Email field + Continue. Session opens in **user view**.
```
with:
```markdown
### 4.1 Sign-in gate
The right column is one panel with five modes (`GatePanel.jsx`, driven by `logic/auth.js` against svc-operations-intelligence's `/api/auth`). The marketing column beside it is unchanged.

- **Sign in** — email + password, **Continue**. `Single sign-on` is inert (a toast; no SSO endpoint exists yet). Links: *Forgot password?* · *Create an account* (hidden when `GET /api/auth/config` says self-registration is off). A wrong password and an unknown address get the same server line; eight failures lock the account for 15 minutes and the panel counts down beside a *Reset your password* link; a correct password on an unconfirmed address jumps to **Enter the code** (the server has already sent one).
- **Create an account** — full name, email, password (the minimum length comes from `/config`), phone (optional). 202 → **Enter the code**. An address that already has an account gets the same 202 — the mailbox owner is told, the screen is not.
- **Enter the code** — six digits, ten minutes, five guesses. A miss keeps the digits and shows the attempts left; an expired, exhausted or superseded code clears the field and makes **Resend code** the emphasised button. Resend is disabled with a countdown while the 60-second cooldown runs; a resend kills the older code, so the typed digits go. The right code confirms the address **and signs in**.
- **Forgot your password?** — email → 202 (identical whether or not an account exists) → **Set a new password**.
- **Set a new password** — code + new password on one screen (there is no "check the code" step by design). A rejected password keeps the code; only the password is retyped. Success returns to **Sign in** with the server's line — a reset ends every session and deliberately does not sign in.

Tokens: the access token (30 min) lives in memory; the refresh token (14 days, rotated on every use) is the persisted credential (`logic/session.js`). A reload renders the shell on the stored account and refreshes once; every backend call carries `Authorization: Bearer`; a 401 `expired` is refreshed and retried once, any other 401 signs out with the server's line. Session opens in **user view**.
```

- [ ] **Step 2: Replace the account-menu paragraph in §4.2**

Replace:
```markdown
**Account menu (Aasim):** header line states the current mode ("User view · Planum Technologies"). Items: **Pricing**, **Support**, and a mode toggle that reads **Admin view** in user mode and **User view** in admin mode. **Sign out** as a separated last row. The click-away layer sits below the header's stacking context so menu rows stay clickable.
```
with:
```markdown
**Account menu:** the avatar shows the signed-in person's initial; the header shows their name, their email, and the current mode ("User view · Planum Technologies"). Items: **Pricing**, **Support**, the mode toggle (**Admin view** in user mode, **User view** in admin mode — offered only to accounts whose real role is `admin` or `superadmin`; a `user` account has User view and no toggle), **Change password** (a modal: current + new password; success ends every session, this one included, so it returns to the gate with the server's line), **Sign out everywhere** (`POST /api/auth/logout {everywhere:true}`; a toast says how many sessions ended). **Sign out** as a separated last row. The click-away layer sits below the header's stacking context so menu rows stay clickable.
```

- [ ] **Step 3: Check the rendered Markdown reads correctly**

Run: `sed -n '5,14p' docs/shell-and-shared-components.md`
Expected: the two sections above, no stray fences.

---

### Task 11: Full verification — suites, build, and a browser pass against a mock auth server

**Files:**
- Create (scratchpad, not in the repo): `<scratchpad>/mock-auth.mjs`, `<scratchpad>/verify-auth.mjs`

**Constraint restated:** the dev proxy is pointed at the mock on `127.0.0.1:3999`. Nothing in this task may reach `localhost:3000`.

- [ ] **Step 1: Suites and build**

Run: `npm test && npm run build`
Expected: every test file passes (client 6, session 5, auth 35, plus the existing suites); the build succeeds.

- [ ] **Step 2: Write the mock auth server**

Create `/private/tmp/claude-501/-Users-hussain-Desktop-hoist/8d5bac4d-5cc8-41fd-b49f-32324b190afb/scratchpad/mock-auth.mjs`:

```js
// A throwaway /api/auth for the browser pass. In-memory accounts, the fixed code 123456,
// the captured response shapes. Every other /backend/* path is a 404 so the app's pages
// fall back to their seed data. Nothing here touches a database.
import http from 'node:http';

const PORT = 3999, CODE = '123456';
const otp = { code_length: 6, ttl_minutes: 10, max_attempts: 5, resend_cooldown_seconds: 60, max_per_hour: 5 };
const users = new Map();     // email → { id, email, full_name, role, password, verified, attempts, last }
const sessions = new Map();  // refresh token → { email, access }
const spent = new Set();     // refresh tokens already exchanged (a second use is a replay)
let n = 0;
const issue = (email) => { n += 1; const t = { access_token: 'acc-' + n, refresh_token: 'ref-' + n, token_type: 'Bearer', expires_in: 1800 }; sessions.set(t.refresh_token, { email, access: t.access_token }); return t; };
const pub = (u) => ({ id: u.id, email: u.email, full_name: u.full_name, organization_id: 'org-1', status: 'active', email_verified: u.verified, role: u.role, role_label: u.role === 'admin' ? 'Admin — everything inside their own organisation' : 'Facilities manager — the default for a new account', last_login_at: u.last || null });
const send = (res, status, body) => { res.writeHead(status, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(body)); };
const fail = (res, status, reason, error, extra) => send(res, status, { detail: Object.assign({ ok: false, error, reason }, extra || {}) });
const accepted = (res, status, message, email) => send(res, 202, { ok: true, status, message, email, otp });
const weak = (pw) => (String(pw || '').length < 12 ? 'Use at least 12 characters. Length is what makes a password hard to guess; a memorable phrase beats a short one with symbols in it.' : null);
const byBearer = (req) => { const t = (req.headers.authorization || '').replace(/^Bearer\s+/i, ''); for (const s of sessions.values()) if (s.access === t) return users.get(s.email); return null; };

users.set('ada@example.com', { id: 'u-admin', email: 'ada@example.com', full_name: 'Ada Admin', role: 'admin', password: 'correct-horse-battery-staple', verified: true, attempts: 0 });

http.createServer(async (req, res) => {
  let raw = ''; for await (const ch of req) raw += ch;
  const body = raw ? JSON.parse(raw) : {};
  const p = req.url.split('?')[0].replace('/backend/ops-intelligence', '');
  const email = String(body.email || '').trim().toLowerCase();
  const u = users.get(email);
  console.log(req.method, p, email || '');
  if (p === '/api/auth/config') return send(res, 200, { ok: true, self_registration: true, password: { min_length: 12 }, otp, secrets_configured: { jwt: true, otp_pepper: true } });
  if (p === '/api/auth/register') {
    const w = weak(body.password); if (w) return fail(res, 400, 'password', w);
    if (!u) users.set(email, { id: 'u-' + (users.size + 1), email, full_name: body.full_name, role: 'user', password: body.password, verified: false, attempts: 0 });
    return accepted(res, 'verification_sent', 'Check ' + email + ' for a 6-digit code and enter it to finish setting up your account. The code lasts 10 minutes.', email);
  }
  if (p === '/api/auth/verify-email') {
    if (!u) return fail(res, 404, 'no_account', 'That account no longer exists.');
    if (body.code !== CODE) { u.attempts += 1; return fail(res, 400, 'mismatch', 'That code is not correct. ' + (5 - u.attempts) + ' attempts left.', { attempts_remaining: 5 - u.attempts }); }
    u.verified = true; return send(res, 200, { ok: true, user: pub(u), tokens: issue(email), message: 'Your email address is confirmed and you are signed in.' });
  }
  if (p === '/api/auth/resend-code') return accepted(res, 'sent', 'If that address needs a code, one is on its way. It lasts 10 minutes.', email);
  if (p === '/api/auth/login') {
    if (!u || u.password !== body.password) return fail(res, 401, 'invalid_credentials', 'That email address and password do not match an account.');
    if (!u.verified) return fail(res, 403, 'email_not_verified', 'Confirm your email address first. We have sent a new code to ' + email + '.', { email });
    const out = { ok: true, user: pub(u), tokens: issue(email) }; u.last = new Date().toISOString(); return send(res, 200, out);
  }
  if (p === '/api/auth/refresh') {
    const t = body.refresh_token;
    if (spent.has(t)) { sessions.clear(); return fail(res, 401, 'replayed', 'That session was already used. Every session has been ended as a precaution — sign in again.'); }
    const s = sessions.get(t); if (!s) return fail(res, 401, 'invalid', 'That session is not valid. Sign in again.');
    spent.add(t); sessions.delete(t); const uu = users.get(s.email);
    return send(res, 200, { ok: true, user: pub(uu), tokens: issue(s.email) });
  }
  if (p === '/api/auth/logout') {
    let ended = 0;
    if (body.everywhere) { const who = byBearer(req); if (who) for (const [k, s] of sessions) if (s.email === who.email) { sessions.delete(k); ended += 1; } }
    else if (sessions.delete(body.refresh_token)) ended = 1;
    return send(res, 200, { ok: true, message: 'Signed out.', sessions_ended: ended });
  }
  if (p === '/api/auth/password/forgot') return accepted(res, 'accepted', 'If that address has an account, a reset code is on its way. It is valid for 10 minutes.', email);
  if (p === '/api/auth/password/reset') {
    const w = weak(body.new_password); if (w) return fail(res, 400, 'password', w);
    if (!u) return fail(res, 404, 'no_account', 'That account no longer exists.');
    if (body.new_password === u.password) return fail(res, 400, 'password_unchanged', 'That is the password the account already has. If you are resetting it because someone else may know it, choose a different one. Your code is still valid.');
    if (body.code !== CODE) return fail(res, 400, 'mismatch', 'That code is not correct. 4 attempts left.', { attempts_remaining: 4 });
    u.password = body.new_password; u.verified = true; let ended = 0; for (const [k, s] of sessions) if (s.email === email) { sessions.delete(k); ended += 1; }
    return send(res, 200, { ok: true, message: 'Your password is set. Sign in with it — every other session has been signed out.', sessions_ended: ended });
  }
  if (p === '/api/auth/password/change') {
    const who = byBearer(req); if (!who) return fail(res, 401, 'missing_token', 'Send an Authorization: Bearer <token> header.');
    if (who.password !== body.current_password) return fail(res, 401, 'invalid_credentials', 'Your current password is not correct.');
    const w = weak(body.new_password); if (w) return fail(res, 400, 'password', w);
    if (body.new_password === who.password) return fail(res, 400, 'password_unchanged', 'That is the password you already have.');
    who.password = body.new_password; let ended = 0; for (const [k, s] of sessions) if (s.email === who.email) { sessions.delete(k); ended += 1; }
    return send(res, 200, { ok: true, message: 'Your password is changed. Sign in again with the new one.', sessions_ended: ended });
  }
  return send(res, 404, { detail: 'not mocked' });
}).listen(PORT, '127.0.0.1', () => console.log('mock auth on http://127.0.0.1:' + PORT + ' — code is ' + CODE));
```

- [ ] **Step 3: Start the mock and the dev server (background)**

Run (background): `node /private/tmp/claude-501/-Users-hussain-Desktop-hoist/8d5bac4d-5cc8-41fd-b49f-32324b190afb/scratchpad/mock-auth.mjs`
Run (background, from `apps/frontend`): `VITE_DEV_PROXY_TARGET=http://127.0.0.1:3999 PORT=5173 npm run dev -- --host 127.0.0.1 --strictPort`
Then warm it: `curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:5173/` → `200`, and confirm the proxy: `curl -s http://127.0.0.1:5173/backend/ops-intelligence/api/auth/config` → the mock's JSON.

- [ ] **Step 4: Install playwright-core in the scratchpad and write the browser script**

Run: `cd /private/tmp/claude-501/-Users-hussain-Desktop-hoist/8d5bac4d-5cc8-41fd-b49f-32324b190afb/scratchpad && npm i --silent playwright-core`

Create `<scratchpad>/verify-auth.mjs`:

```js
// Drives every gate mode, the account menu, the change-password modal and the role gate
// against the mock on :3999. Logs every non-GET request so the run proves nothing went
// anywhere but the mock, and screenshots each state into the scratchpad.
import { chromium } from 'playwright-core';
const OUT = new URL('.', import.meta.url).pathname;
const b = await chromium.launch({ channel: 'chrome', headless: true });
const page = await b.newPage({ viewport: { width: 1440, height: 1000 } });
const writes = [];
page.on('request', (r) => { if (r.method() !== 'GET') writes.push(r.method() + ' ' + new URL(r.url()).pathname); });
const shot = (name) => page.screenshot({ path: OUT + 'auth-' + name + '.png' });
const text = (t) => page.getByText(t, { exact: true });
const ph = (t) => page.getByPlaceholder(t, { exact: true });

await page.goto('http://127.0.0.1:5173/', { waitUntil: 'domcontentloaded' });
await text('Sign in').waitFor();
await shot('01-signin');

// wrong password → verbatim server line
await ph('you@portfolio.com').fill('nobody@example.com');
await ph('Password').fill('not-the-right-one');
await text('Continue').click();
await text('That email address and password do not match an account.').waitFor();
await shot('02-signin-wrong');

// create an account → code screen → wrong code → right code → signed in
await text('Create an account').click();
await ph('Full name').fill('Sam Okafor');
await ph('you@portfolio.com').fill('sam@example.com');
await ph('Password').fill('correct-horse-battery-staple');
await text('Create account').click();
await text('Enter the code').waitFor();
await shot('03-verify');
await ph('6-digit code').fill('000000');
await text('Confirm').click();
await text('That code is not correct. 4 attempts left.').waitFor();
await shot('04-verify-wrong');
await ph('6-digit code').fill('123 456');
await text('Confirm').click();
await page.getByTitle('Sam Okafor').waitFor();
await shot('05-signed-in-user');

// user role: no Admin view; Change password is there
await page.getByTitle('Sam Okafor').click();
await text('Change password').waitFor();
if (await text('Admin view').count()) throw new Error('user role was offered Admin view');
await shot('06-menu-user');

// change password → back at the gate with the server line
await text('Change password').click();
await page.locator('input[autocomplete="current-password"]').fill('correct-horse-battery-staple');
await page.locator('input[autocomplete="new-password"]').fill('yet-another-good-passphrase');
await page.locator('.btn.btn-primary', { hasText: 'Change password' }).click();
await text('Your password is changed. Sign in again with the new one.').waitFor();
await shot('07-after-change');

// forgot → reset (password_unchanged keeps the code) → success line → sign in with it
await text('Forgot password?').click();
await ph('you@portfolio.com').fill('sam@example.com');
await text('Send reset code').click();
await text('Set a new password').waitFor();
await ph('6-digit code').fill('123456');
await ph('New password').fill('yet-another-good-passphrase');
await text('Set password').click();
await page.getByText('Your code is still valid.', { exact: false }).waitFor();
if ((await ph('6-digit code').inputValue()) !== '123456') throw new Error('a rejected password burned the code');
await shot('08-reset-unchanged');
await ph('New password').fill('a-brand-new-long-passphrase');
await text('Set password').click();
await text('Your password is set. Sign in with it — every other session has been signed out.').waitFor();
await shot('09-reset-done');
await ph('Password').fill('a-brand-new-long-passphrase');
await text('Continue').click();
await page.getByTitle('Sam Okafor').waitFor();

// reload keeps the session (one /refresh); sign out everywhere returns to the gate
await page.reload({ waitUntil: 'domcontentloaded' });
await page.getByTitle('Sam Okafor').waitFor();
await page.getByTitle('Sam Okafor').click();
await text('Sign out everywhere').click();
await text('Sign in').waitFor();
await shot('10-signed-out');

// admin account gets the toggle
await ph('you@portfolio.com').fill('ada@example.com');
await ph('Password').fill('correct-horse-battery-staple');
await text('Continue').click();
await page.getByTitle('Ada Admin').click();
await text('Admin view').waitFor();
await shot('11-menu-admin');

// Every request goes through the dev proxy to the mock on :3999 — that is what keeps the
// production-pointed gateway out of reach. The signed-in pages also POST svc-udr's read-only
// SELECT (/backend/udr/api/tables/query/select); the mock answers 404 and the page falls
// back to seed data. Anything outside /backend/ would be a real surprise.
console.log('non-GET requests:\n  ' + writes.join('\n  '));
const stray = writes.filter((w) => !w.startsWith('POST /backend/'));
if (stray.length) throw new Error('unexpected non-backend writes: ' + stray.join(', '));
const authWrites = writes.filter((w) => w.startsWith('POST /backend/ops-intelligence/api/auth/'));
if (authWrites.filter((w) => w.endsWith('/refresh')).length !== 1) throw new Error('expected exactly one /refresh (the reload): ' + authWrites.join(', '));
await b.close();
console.log('OK — screenshots in ' + OUT);
```

- [ ] **Step 5: Run it and inspect**

Run: `cd <scratchpad> && node verify-auth.mjs`
Expected: `OK — screenshots in …`; the non-GET list contains only `POST /backend/ops-intelligence/api/auth/…` paths; the mock's console shows exactly one `/api/auth/refresh` for the reload. Open `auth-01…11.png` with the Read tool and confirm: the left marketing column is unchanged in every gate shot; the panel copy and buttons match the spec's table; the menu shows name + email; the modal renders; the admin menu has the toggle and the user menu does not.

- [ ] **Step 6: Stop the background processes and report**

Kill the dev server and the mock. Report to Hussain: test counts, build result, the screenshot paths, and the reminder that a real-backend pass needs the two migrations and his explicit go-ahead. Do not commit.

---

## Self-review against the spec

- **Coverage:** backend contract → Tasks 1, 3; token plumbing → 1, 3, 6; state slice → 3; every gate mode and reason mapping → 3, 4, 5, 8; signed-in shell (identity, role-gated toggle, change password, sign out everywhere) → 3, 7, 9; persistence → 2; two-tab handling → 6; edge cases (network on boot, `/config` unreachable, 422, concurrency) → 3, 6; the eleven test groups → 1 (group 11), 2 (group 10), 3 (1, 2, 9), 4 (3), 5 (4), 6 (5, 6), 7 (7, 8); docs → 10; browser verification against a mock only → 11.
- **Names used across tasks:** `authBoot/authStop/authRefresh/authEnter/authSignedOut/authSignIn/authGo/authSSO/authArm/authCooling/authCountdown/authOtp/authVals` (Task 3) ← used in 4–9; `authAccepted/authCodeFailure/authResend` (Task 4) ← used in 5; `pwOpenModal/pwClose/pwSubmit/authSignOutEverywhere` (Task 7) ← referenced by `authVals` in 3 and by `PasswordModal`/`acctItems` in 9; `configureAuth/TERMINAL_401/errorMessage/ApiError.reason` (Task 1) ← used in 3; `SESSION_KEY` (Task 2) ← used in 3, 6, tests. Consistent.
- **No placeholders:** every step carries its code; the only forward references are the Task 3 `authVals` handlers implemented in Tasks 4–7, called out explicitly.
