// labAuth — Trace Lab's own sign-in: the Hoistra email and password against the same login service, but a session
// of its own (its storage key, its refresh lock), so signing in or out of the lab never touches Hoistra's session,
// and two lab tabs never present the same refresh token twice (the service treats that as theft).
import { test } from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = globalThis.window || { location: { origin: 'http://test.local', pathname: '/lab/', search: '' } };
const S = await import('../session.js');
const { createLabAuth } = await import('../authFlow.js');

function memStorage(seed) {
  const m = new Map(Object.entries(seed || {}));
  return { getItem: (k) => (m.has(k) ? m.get(k) : null), setItem: (k, v) => m.set(k, String(v)), removeItem: (k) => m.delete(k), _m: m };
}
const err = (status, reason) => Object.assign(new Error(reason), { status, reason });
const USER = { id: 'u1', email: 'a@x.com', role: 'superadmin', organization_id: 'o1', organization_name: 'Your Organisation' };
const TOKENS = (n) => ({ access_token: 'at' + n, refresh_token: 'rt' + n });

function fakeApi(over) {
  const calls = [];
  const api = {
    login: async (email, password) => { calls.push(['login', email, password]); return { tokens: TOKENS(1), user: USER }; },
    verifyEmail: async (email, code) => { calls.push(['verify', email, code]); return { tokens: TOKENS(2), user: USER }; },
    refresh: async (rt) => { calls.push(['refresh', rt]); return { tokens: TOKENS(9), user: USER }; },
    logout: async (rt) => { calls.push(['logout', rt]); return { ok: true }; },
    me: async () => { calls.push(['me']); return { ok: true, user: USER }; }
  };
  return Object.assign(api, over || {}, { calls });
}
const fakeLocks = () => { const names = []; return { names, request: (name, opts, fn) => { names.push(name); return Promise.resolve().then(fn); } }; };

test('the session key and the lock are Trace Lab\'s own, never Hoistra\'s', () => {
  assert.equal(S.KEY, 'tracelab.session.v1');
  assert.equal(S.lockName('u1'), 'tracelab.refresh.u1');
  const st = memStorage({ 'hoistra.session.v1': 'theirs' });
  S.saveSession(st, { refreshToken: 'rt1', user: USER });
  assert.deepEqual(S.loadSession(st), { refreshToken: 'rt1', user: USER });
  S.clearSession(st);
  assert.equal(S.loadSession(st), null);
  assert.equal(st.getItem('hoistra.session.v1'), 'theirs', 'Hoistra\'s session is never touched');
  assert.equal(S.loadSession(memStorage({ 'tracelab.session.v1': '{not json' })), null);
});

test('who is who: the platform team, a company admin, and a member who waits for a Trace Lab role', () => {
  assert.equal(S.access({ role: 'superadmin' }), 'platform');
  assert.equal(S.access({ role: 'admin' }), 'company');
  assert.equal(S.access({ role: 'user' }), 'member');
  assert.equal(S.access(null), 'denied');
});

test('sign in keeps the access token in memory and the refresh token under the lab key', async () => {
  const st = memStorage();
  const api = fakeApi();
  const auth = createLabAuth({ api, storage: st, locks: fakeLocks() });
  assert.deepEqual(await auth.signIn(' a@x.com ', 'pw'), { ok: true });
  assert.deepEqual(api.calls[0], ['login', 'a@x.com', 'pw']);
  assert.equal(auth.token(), 'at1');
  assert.equal(auth.user().email, 'a@x.com');
  assert.equal(S.loadSession(st).refreshToken, 'rt1');
  assert.ok(!st.getItem(S.KEY).includes('at1'), 'the access token is never stored');
});

test('an unverified email asks for the code, and the code signs in', async () => {
  const api = fakeApi({ login: async () => { throw err(403, 'email_not_verified'); } });
  const auth = createLabAuth({ api, storage: memStorage(), locks: fakeLocks() });
  assert.deepEqual(await auth.signIn('a@x.com', 'pw'), { needsCode: true });
  assert.deepEqual(await auth.verify('a@x.com', ' 123 456 '), { ok: true });
  assert.deepEqual(api.calls.at(-1), ['verify', 'a@x.com', '123456']);
  assert.equal(auth.token(), 'at2');
  const bad = createLabAuth({ api: fakeApi({ login: async () => { throw err(401, 'invalid'); } }), storage: memStorage(), locks: fakeLocks() });
  const out = await bad.signIn('a@x.com', 'nope');
  assert.ok(out.error && /email or password/i.test(out.error));
});

test('opening the lab with a stored session refreshes once under the lab lock and keeps the rotated token', async () => {
  const st = memStorage();
  S.saveSession(st, { refreshToken: 'rt1', user: USER });
  const api = fakeApi();
  const locks = fakeLocks();
  const auth = createLabAuth({ api, storage: st, locks });
  assert.equal(await auth.boot(), true);
  assert.deepEqual(locks.names, ['tracelab.refresh.u1']);
  assert.deepEqual(api.calls.filter((c) => c[0] === 'refresh'), [['refresh', 'rt1']]);
  assert.equal(S.loadSession(st).refreshToken, 'rt9');
  assert.equal(auth.token(), 'at9');
});

test('another lab tab rotated the token first: the refresh uses the newest stored token, not a stale one', async () => {
  const st = memStorage();
  S.saveSession(st, { refreshToken: 'rt1', user: USER });
  const api = fakeApi();
  const auth = createLabAuth({ api, storage: st, locks: fakeLocks() });
  await auth.boot();                                    // holds rt9 now
  S.saveSession(st, { refreshToken: 'rt-other-tab', user: USER });
  await auth.refresh();
  assert.deepEqual(api.calls.filter((c) => c[0] === 'refresh').at(-1), ['refresh', 'rt-other-tab']);
});

test('a refresh the service refuses clears the lab session and reports signed out', async () => {
  const st = memStorage({ 'hoistra.session.v1': 'theirs' });
  S.saveSession(st, { refreshToken: 'rt1', user: USER });
  const changes = [];
  const auth = createLabAuth({ api: fakeApi({ refresh: async () => { throw err(401, 'replayed'); } }), storage: st, locks: fakeLocks(),
    onChange: (u) => changes.push(u) });
  assert.equal(await auth.boot(), false);
  assert.equal(S.loadSession(st), null);
  assert.equal(auth.token(), null);
  assert.equal(st.getItem('hoistra.session.v1'), 'theirs');
  assert.equal(changes.at(-1), null);
  assert.equal(await createLabAuth({ api: fakeApi(), storage: memStorage(), locks: fakeLocks() }).boot(), false, 'nothing stored: sign in');
});

test('sign out revokes only the lab\'s refresh token and clears only the lab session', async () => {
  const st = memStorage({ 'hoistra.session.v1': 'theirs' });
  const api = fakeApi();
  const auth = createLabAuth({ api, storage: st, locks: fakeLocks() });
  await auth.signIn('a@x.com', 'pw');
  await auth.signOut();
  assert.deepEqual(api.calls.at(-1), ['logout', 'rt1']);
  assert.equal(S.loadSession(st), null);
  assert.equal(auth.token(), null);
  assert.equal(st.getItem('hoistra.session.v1'), 'theirs');
});
