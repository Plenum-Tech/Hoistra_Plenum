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

const { apiFetch, ApiError, configureAuth, errorMessage, TERMINAL_401, ORG_ID, setActingOrg, getActingOrg, currentOrgId } = await import('../src/api/client.js');

const B = '/backend/ops-intelligence';
const fail = (status, reason, error) => [status, { detail: { ok: false, error, reason } }];

beforeEach(() => {
  calls = []; handlers = {};
  configureAuth({ getToken: () => null, refresh: null, onTerminal: null });
  setActingOrg(null); // module-level, so a leftover override from one test cannot leak into the next
});

// ── the superadmin view-as-company override ─────────────────────────────────
test('currentOrgId() is ORG_ID until a superadmin override is set, then the override wins', () => {
  assert.equal(getActingOrg(), null);
  assert.equal(currentOrgId(), ORG_ID || '');
  setActingOrg('11111111-1111-1111-1111-111111111111');
  assert.equal(getActingOrg(), '11111111-1111-1111-1111-111111111111');
  assert.equal(currentOrgId(), '11111111-1111-1111-1111-111111111111');
  setActingOrg(null);
  assert.equal(getActingOrg(), null);
  assert.equal(currentOrgId(), ORG_ID || '');
});

test('setActingOrg coerces to a string id or null, never leaving a falsy non-null override', () => {
  setActingOrg(123);
  assert.equal(getActingOrg(), '123');
  setActingOrg('');
  assert.equal(getActingOrg(), null);
  setActingOrg(undefined);
  assert.equal(getActingOrg(), null);
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

test('terminal reasons report through onTerminal and are not retried', async () => {
  const seen = [];
  configureAuth({ getToken: () => 'acc-1', refresh: async () => 'x', onTerminal: (r, m) => seen.push([r, m]) });
  for (const reason of TERMINAL_401) {
    handlers['GET ' + B + '/api/a'] = fail(401, reason, 'gone: ' + reason);
    await assert.rejects(apiFetch(B, '/api/a'), (e) => e.reason === reason);
  }
  assert.deepEqual(seen.map((x) => x[0]), [...TERMINAL_401]);
  assert.equal(seen[0][1], 'gone: ' + [...TERMINAL_401][0]);
});

test('missing_token retries once via refresh, same as expired, and is not terminal even when the retry also 401s', async () => {
  // componentDidMount fires every page's load in one breath; on the very first requests of a
  // session the access token is not back from authBoot()'s refresh yet, and those requests
  // go out with no Authorization header at all — a missing_token 401, not an expired one.
  // Treating the two differently would leave every page seed-only on first paint.
  const seen = [];
  let refreshes = 0;
  configureAuth({ getToken: () => null, refresh: async () => { refreshes += 1; return 'acc-new'; }, onTerminal: (r, m) => seen.push([r, m]) });
  handlers['GET ' + B + '/api/a'] = fail(401, 'missing_token', 'Send a header.');
  await assert.rejects(apiFetch(B, '/api/a'), (e) => e.reason === 'missing_token');
  assert.equal(refreshes, 1, 'missing_token retries once via refresh, same as expired');
  assert.equal(calls.length, 2, 'the original attempt plus exactly one retry');
  assert.equal(seen.length, 0, 'missing_token is not terminal, even once the retry also 401s');
});

test('a terminal 401 on the retried request still reports through onTerminal', async () => {
  const seen = [];
  configureAuth({ getToken: () => 'acc-old', refresh: async () => 'acc-new', onTerminal: (r) => seen.push(r) });
  handlers['GET ' + B + '/api/a'] = (u, o) => (o.headers.Authorization === 'Bearer acc-new' ? fail(401, 'session_revoked', 'gone') : fail(401, 'expired', 'Token expired'));
  await assert.rejects(apiFetch(B, '/api/a'), (e) => e.reason === 'session_revoked');
  assert.deepEqual(seen, ['session_revoked']);
  assert.equal(calls.length, 2, 'one try, one retry');
});

// ── svc-work-order-management's error envelope ──────────────────────────────────────────
// That service wraps every HTTPException as {success:false, errors:[{code, message, field}]}
// (app.py's http_exception_handler) — the {detail:{error, reason}} its principal.py builds
// never reaches the wire, so its 401s parsed here with no reason at all. On a reload the
// Assets and Maintenance pages' reads go out before authBoot()'s refresh has a token back;
// every other page's missing_token 401 is refreshed and retried, but these two read
// "Unreachable — 401" until the loader's own 30-second retry — or, as reported on Azure on
// 30 Sep 2026, until a reload happened to win the race.
const WO = '/backend/work-order';
const woFail = (status, code, message) => [status, { success: false, errors: [{ code, message, field: null }] }];

test('ApiError reads the reason and message from the work-order envelope too', async () => {
  handlers['GET ' + WO + '/api/assets'] = woFail(401, 'missing_token', 'Send an Authorization: Bearer <token> header.');
  await assert.rejects(apiFetch(WO, '/api/assets', { auth: false }), (e) => {
    assert.ok(e instanceof ApiError);
    assert.equal(e.status, 401);
    assert.equal(e.reason, 'missing_token');
    assert.equal(e.message, 'Send an Authorization: Bearer <token> header.', 'the envelope message, not "401 401"');
    return true;
  });
});

test('a work-order missing_token 401 on first paint is refreshed and retried once, same as every other service', async () => {
  let refreshes = 0;
  let token = null;
  configureAuth({ getToken: () => token, refresh: async () => { refreshes += 1; token = 'acc-new'; return token; } });
  handlers['GET ' + WO + '/api/assets'] = (u, o) => (o.headers.Authorization === 'Bearer acc-new'
    ? [200, [{ id: 'a1' }]]
    : woFail(401, 'missing_token', 'Send an Authorization: Bearer <token> header.'));
  const r = await apiFetch(WO, '/api/assets');
  assert.deepEqual(r, [{ id: 'a1' }]);
  assert.equal(refreshes, 1);
  assert.equal(calls.filter((c) => c.path === WO + '/api/assets').length, 2, 'the first-paint attempt plus exactly one retry');
});

test('a work-order expired 401 mid-session is refreshed and retried, and a terminal one reports through onTerminal', async () => {
  const seen = [];
  configureAuth({ getToken: () => 'acc-old', refresh: async () => 'acc-new', onTerminal: (r, m) => seen.push([r, m]) });
  handlers['GET ' + WO + '/api/work-orders/'] = (u, o) => (o.headers.Authorization === 'Bearer acc-new' ? [200, []] : woFail(401, 'expired', 'Token expired'));
  assert.deepEqual(await apiFetch(WO, '/api/work-orders/'), []);
  handlers['GET ' + WO + '/api/work-orders/'] = woFail(401, 'session_revoked', 'That session has ended.');
  await assert.rejects(apiFetch(WO, '/api/work-orders/'), (e) => e.reason === 'session_revoked');
  assert.deepEqual(seen, [['session_revoked', 'That session has ended.']]);
});

test("svc-udr's envelope — the same errors[] with no success flag — parses the same, so the navigator's saved spaces are not lost to first paint either", async () => {
  let refreshes = 0;
  let token = null;
  configureAuth({ getToken: () => token, refresh: async () => { refreshes += 1; token = 'acc-new'; return token; } });
  handlers['GET /backend/udr/api/spaces'] = (u, o) => (o.headers.Authorization === 'Bearer acc-new'
    ? [200, { spaces: [] }]
    : [401, { errors: [{ code: 'missing_token', message: 'Send an Authorization: Bearer <token> header.', field: null }] }]);
  assert.deepEqual(await apiFetch('/backend/udr', '/api/spaces'), { spaces: [] });
  assert.equal(refreshes, 1);
});

// ── first paint waits for the token instead of racing it (1 Oct 2026) ─────────────────
// Every page's loads fire in one breath at mount, while authBoot()'s refresh is still in
// the air. They used to go out bare, come back 401 missing_token — 48 of them on one load,
// measured against a stub with the production rules — and each then asked for a SECOND
// refresh once the first had landed: every reload rotated the refresh token twice and sent
// every read twice. A read issued while a refresh is in flight now waits for it.

test('a read issued while the boot refresh is in flight waits for it and goes out once, with the token', async () => {
  let token = null;
  let refreshes = 0;
  let land;
  const inflight = new Promise((r) => { land = () => { token = 'acc-boot'; r('acc-boot'); }; });
  configureAuth({ getToken: () => token, refresh: async () => { refreshes += 1; return inflight; }, pending: () => (token ? null : inflight) });
  handlers['GET ' + B + '/api/approvals'] = (u, opts) => (opts.headers.Authorization === 'Bearer acc-boot' ? [200, { items: [] }] : fail(401, 'missing_token', 'no token'));
  const read = apiFetch(B, '/api/approvals');
  await new Promise((r) => setTimeout(r, 5));
  assert.equal(calls.length, 0, 'nothing is sent before the token it needs exists');
  land();
  assert.deepEqual(await read, { items: [] });
  assert.equal(calls.length, 1, 'one request, not a 401 and a retry');
  assert.equal(calls[0].headers.Authorization, 'Bearer acc-boot');
  assert.equal(refreshes, 0, 'and no second refresh of its own');
});

test('a 401 on a token this tab has already replaced is retried with the newer one, not with another refresh', async () => {
  let token = 'acc-old';
  let refreshes = 0;
  configureAuth({ getToken: () => token, refresh: async () => { refreshes += 1; token = 'acc-other'; return token; } });
  handlers['GET ' + B + '/api/value/summary'] = (u, opts) => {
    if (opts.headers.Authorization === 'Bearer acc-old') { token = 'acc-new'; return fail(401, 'expired', 'expired'); }
    return [200, { ok: true }];
  };
  assert.deepEqual(await apiFetch(B, '/api/value/summary'), { ok: true });
  assert.equal(refreshes, 0, 'a refresh already landed while this read was out');
  assert.equal(calls[1].headers.Authorization, 'Bearer acc-new');
});

test('a boot refresh that fails leaves the read to meet its 401 as before — nothing waits forever', async () => {
  let refreshes = 0;
  const failed = Promise.reject(new Error('gateway down'));
  failed.catch(() => {});
  configureAuth({ getToken: () => null, refresh: async () => { refreshes += 1; throw new Error('gateway down'); }, pending: () => failed });
  handlers['GET ' + B + '/api/approvals'] = fail(401, 'missing_token', 'no token');
  await assert.rejects(apiFetch(B, '/api/approvals'), (e) => e instanceof ApiError && e.status === 401);
  assert.equal(calls.length, 1);
});
