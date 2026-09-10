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

test('a terminal 401 on the retried request still reports through onTerminal', async () => {
  const seen = [];
  configureAuth({ getToken: () => 'acc-old', refresh: async () => 'acc-new', onTerminal: (r) => seen.push(r) });
  handlers['GET ' + B + '/api/a'] = (u, o) => (o.headers.Authorization === 'Bearer acc-new' ? fail(401, 'session_revoked', 'gone') : fail(401, 'expired', 'Token expired'));
  await assert.rejects(apiFetch(B, '/api/a'), (e) => e.reason === 'session_revoked');
  assert.deepEqual(seen, ['session_revoked']);
  assert.equal(calls.length, 2, 'one try, one retry');
});
