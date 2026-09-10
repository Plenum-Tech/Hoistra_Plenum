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
