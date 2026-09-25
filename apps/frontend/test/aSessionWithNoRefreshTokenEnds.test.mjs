// A signed-in shell with no refresh token is a session that cannot be repaired.
//
// authRefresh() rejected with a plain Error when there was no token to refresh with. A
// plain Error carries no `status`, and the catch beneath it signs out only on a 401 — so
// nothing signed out. The shell stayed "signed in" holding no usable credential: every
// panel rendered "Unreachable — 401 Unauthorized", every read went on retrying on its own
// timer, and no sign-in gate was ever offered. 117 requests in ten minutes against a
// session that had been dead for five hours, with no way out but clearing site data.
//
// The 401 branch already reached the right conclusion for a credential that is finished.
// This one is just as finished — there is nothing to refresh with — so it ends the same way.
import { test } from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = {
  location: { origin: 'http://test.local', href: 'http://test.local/', search: '' },
  scrollTo: () => {}, addEventListener: () => {}, removeEventListener: () => {},
  localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
};
globalThis.fetch = () => Promise.reject(new TypeError('no network in this test'));

const { authMethods } = await import('../src/logic/auth.js');

function harness(state) {
  const ctx = {
    state: Object.assign({ signedIn: true, account: { id: 'u1' }, accessToken: 'stale',
                           refreshToken: null }, state || {}),
    signedOutWith: null,
    setState(patch) {
      const next = typeof patch === 'function' ? patch(this.state) : patch;
      this.state = Object.assign({}, this.state, next);
    },
    authSignedOut(message) { this.signedOutWith = message; this.setState({ signedIn: false }); },
  };
  ctx.authRefresh = authMethods.authRefresh.bind(ctx);
  return ctx;
}

test('it signs out rather than leaving a shell that cannot recover', async () => {
  const c = harness();
  await assert.rejects(c.authRefresh());
  assert.equal(c.state.signedIn, false, 'the shell stayed signed in with no credential');
  assert.match(c.signedOutWith, /Sign in again/);
});

test('the rejection looks like a 401, so the interceptor stops instead of retrying', async () => {
  // apiFetch refreshes once on `expired`/`missing_token` and gives up on a terminal reason.
  // A plain Error is neither, so the caller learned nothing and the read simply failed.
  const c = harness();
  await assert.rejects(c.authRefresh(), (e) => {
    assert.equal(e.status, 401, 'no status meant the catch below never signed out');
    assert.equal(e.reason, 'invalid', 'a terminal reason ends the retry');
    return true;
  });
});

test('a session that still has a refresh token is left alone', async () => {
  // The fix must not reach a recoverable session: with a token present this goes to the
  // network, and a failure there is the network's problem, not the credential's.
  const c = harness({ refreshToken: 'rt-still-good' });
  await assert.rejects(c.authRefresh());           // fetch rejects in this harness
  assert.equal(c.state.signedIn, true, 'a network failure must not end the session');
  assert.equal(c.signedOutWith, null);
});
