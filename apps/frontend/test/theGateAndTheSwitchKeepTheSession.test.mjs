// Two ways the session was lost without anyone doing anything wrong.
//
// A company switch that landed while /refresh was in flight threw the response away — the
// organisation-epoch check applied to every request, auth endpoints included. That response
// held the only copy of the rotated refresh token, so the next refresh presented the one the
// server had just consumed: a replay, and a replay revokes every session on the account.
//
// And a tab still on the sign-in gate started every live loader at mount. Each one 401'd
// missing_token, asked for a refresh there was no token for, and authRefresh answered by
// signing the tab out: "Your session has ended" to someone who never signed in, with the
// password, code and new password they were typing wiped — again on every 30s retry.
import { test } from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = {
  location: { origin: 'http://test.local', href: 'http://test.local/', search: '' },
  scrollTo: () => {}, addEventListener: () => {}, removeEventListener: () => {},
  localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
};

// fetch that holds each response until the test lets it go, so a switch can land mid-flight.
let pending = [];
globalThis.fetch = () => new Promise((resolve) => {
  pending.push(() => resolve({ ok: true, status: 200, statusText: 'OK', text: async () => '{"ok":true}' }));
});
const releaseAll = () => { const p = pending; pending = []; p.forEach((go) => go()); };

const { apiFetch, setActingOrg, isStaleScope } = await import('../src/api/client.js');
const { authMethods } = await import('../src/logic/auth.js');
const { coreMethods } = await import('../src/logic/core.js');

test('a refresh that comes back after a company switch is still delivered', async () => {
  const inFlight = apiFetch('/backend/ops-intelligence', '/api/auth/refresh', { method: 'POST', body: {}, auth: false });
  setActingOrg('org-other');
  releaseAll();
  assert.deepEqual(await inFlight, { ok: true }, 'the rotated token in this response is the only copy');
  setActingOrg(null);
});

test('a company-scoped read that outlived the switch is still discarded', async () => {
  const inFlight = apiFetch('/backend/ops-intelligence', '/api/buildings');
  setActingOrg('org-other');
  releaseAll();
  await assert.rejects(inFlight, (e) => isStaleScope(e));
  setActingOrg(null);
});

function gateTab() {
  const ctx = {
    state: { signedIn: false, account: null, accessToken: null, refreshToken: null,
             password: 'typed-so-far', code: '1234', newPassword: 'also-typed' },
    signedOutWith: null,
    setState(patch) {
      const next = typeof patch === 'function' ? patch(this.state) : patch;
      this.state = Object.assign({}, this.state, next);
    },
    authSignedOut(message) { this.signedOutWith = message; this.setState({ password: '', code: '', newPassword: '' }); },
  };
  ctx.authRefresh = authMethods.authRefresh.bind(ctx);
  return ctx;
}

test('a refresh asked for on the sign-in gate fails without signing anyone out', async () => {
  const c = gateTab();
  await assert.rejects(c.authRefresh(), (e) => e.status === 401);
  assert.equal(c.signedOutWith, null, 'the gate was told a session it never had had ended');
  assert.equal(c.state.password, 'typed-so-far');
  assert.equal(c.state.code, '1234');
  assert.equal(c.state.newPassword, 'also-typed');
});

function mounted(signedIn) {
  const calls = [];
  const note = (name) => () => { calls.push(name); };
  const ctx = {
    state: { signedIn, view: 'home' },
    setState() {},
    authBoot: note('authBoot'), loadLiveData: note('loadLiveData'),
    usLiveLoad: note('usLiveLoad'), auLiveLoad: note('auLiveLoad'),
    rpLoad: note('rpLoad'), rpLoadPresets: note('rpLoadPresets'),
    rpStop() {},
  };
  // rpStart is the real one, from reports.js's mixin, so its own gate is exercised too.
  return import('../src/logic/reports.js').then(({ reportsMethods }) => {
    ctx.rpStart = reportsMethods.rpStart.bind(ctx);
    coreMethods.componentDidMount.call(ctx);
    clearInterval(ctx._cronTimer); clearInterval(ctx._frameTimer); clearInterval(ctx._rpTimer);
    return calls;
  });
}

test('the sign-in gate starts no live loader at mount', async () => {
  const calls = await mounted(false);
  assert.deepEqual(calls, ['authBoot'], 'every one of these 401s missing_token and retries for three minutes');
});

test('a restored signed-in session still loads everything at mount', async () => {
  const calls = await mounted(true);
  for (const name of ['loadLiveData', 'usLiveLoad', 'auLiveLoad', 'rpLoad', 'rpLoadPresets']) {
    assert.ok(calls.includes(name), name + ' was not started for a signed-in reload');
  }
});
