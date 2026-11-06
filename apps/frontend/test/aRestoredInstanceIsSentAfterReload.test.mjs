// A reload keeps the instance on every question (2 Nov 2026). The active space reached the API
// layer only when `instance` changed through setState; a reload restores it in the constructor,
// so after one every question went out with no space, and Ops Brain answered certificate questions.
import { test } from 'node:test';
import assert from 'node:assert/strict';

const store = {};
globalThis.window = {
  location: { origin: 'http://test.local' }, scrollTo: () => {}, addEventListener: () => {}, removeEventListener: () => {},
  localStorage: { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } },
  sessionStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} }
};
globalThis.localStorage = globalThis.window.localStorage;
globalThis.fetch = () => Promise.reject(new TypeError('Failed to fetch'));
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };

store['hoistra.session.v1'] = JSON.stringify({
  signedIn: true, refreshToken: 'r', instance: 'ops', view: 'chat',
  account: { id: 'u1', email: 'pm@x.test', full_name: 'P M', role: 'admin', status: 'active', email_verified: true }
});

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
const { getActiveSpace, setActiveSpace } = await import('../src/api/client.js');

test('the instance a reload restores is the space every question carries', () => {
  setActiveSpace(null);
  const app = new HoistraLogic();
  assert.equal(app.state.instance, 'ops');
  assert.equal(getActiveSpace(), 'ops');
});
