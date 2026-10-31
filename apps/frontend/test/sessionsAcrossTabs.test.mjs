// Sessions across tabs (12 Oct 2026). Every Hoistra tab keeps the session list in the one
// browser store, and each used to write its whole in-memory list over it. A task made in one
// tab (a vendor hoist, a booking) vanished the moment another open tab saved — its list never
// had it. Saving now merges with what is stored, and a deletion leaves a mark so another tab's
// save cannot bring the deleted session back.
import { test } from 'node:test';
import assert from 'node:assert/strict';

const { makeSession, loadSessions, saveSessions, forgetSessionIds, unforgetSessionIds, forgottenSessionIds, SESSIONS_KEY } = await import('../src/logic/sessions.js');

const memStore = () => {
  const m = {};
  return { m, getItem: (k) => (k in m ? m[k] : null), setItem: (k, v) => { m[k] = String(v); }, removeItem: (k) => { delete m[k]; } };
};
const rec = (id, at, extra) => Object.assign(makeSession({ id, title: 'Session ' + id, kind: 'task', task: 'Task ' + id, at, owner: 'ada@example.com' }), extra || {});
const ids = (st) => loadSessions(st).map((r) => r.id).sort();

test('a save keeps the sessions another tab stored', () => {
  const st = memStore();
  saveSessions([rec('a1', 1000)], st);              // tab A
  saveSessions([rec('b1', 2000)], st);              // tab B, which never saw a1
  assert.deepEqual(ids(st), ['a1', 'b1']);
});

test('the newer copy of the same session wins, whichever tab holds it', () => {
  const st = memStore();
  saveSessions([rec('s1', 5000, { title: 'Newer' })], st);
  saveSessions([rec('s1', 1000, { title: 'Older' })], st);
  assert.equal(loadSessions(st)[0].title, 'Newer');
  saveSessions([rec('s1', 9000, { title: 'Newest' })], st);
  assert.equal(loadSessions(st)[0].title, 'Newest');
});

test('a session deleted in one tab is not brought back by another tab still holding it', () => {
  const st = memStore();
  saveSessions([rec('a1', 1000), rec('a2', 1100)], st);
  forgetSessionIds(['a1'], st);                     // tab A deletes a1
  saveSessions([rec('a2', 1100)], st);
  saveSessions([rec('a1', 1000), rec('a2', 1100), rec('b1', 3000)], st);   // tab B saves its stale list
  assert.deepEqual(ids(st), ['a2', 'b1']);
  assert.ok(forgottenSessionIds(st).has('a1'));
});

test('a deletion that failed on the server can be lifted', () => {
  const st = memStore();
  forgetSessionIds(['x1'], st);
  unforgetSessionIds(['x1'], st);
  saveSessions([rec('x1', 1000)], st);
  assert.deepEqual(ids(st), ['x1']);
});

test('a storage that cannot be read still saves what this tab holds', () => {
  const written = {};
  const st = { getItem: () => { throw new Error('blocked'); }, setItem: (k, v) => { written[k] = v; } };
  assert.equal(saveSessions([rec('a1', 1000)], st), true);
  assert.equal(JSON.parse(written[SESSIONS_KEY]).length, 1);
});

// ── the controller ────────────────────────────────────────────────────────────

test('deleting a session leaves the mark in the browser store', async () => {
  const mem = {};
  globalThis.window = {
    location: { origin: 'http://test.local' }, scrollTo: () => {}, addEventListener: () => {}, removeEventListener: () => {},
    localStorage: { getItem: (k) => (k in mem ? mem[k] : null), setItem: (k, v) => { mem[k] = String(v); }, removeItem: (k) => { delete mem[k]; } },
    sessionStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} }
  };
  globalThis.WebSocket = class { constructor() { throw new Error('no sockets'); } };
  globalThis.fetch = async () => { throw new TypeError('no network'); };
  const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
  const c = new HoistraLogic();
  c.setState({ sessions: [rec('t1', 1000), rec('t2', 2000)] });
  c.deleteSessions(['t1']);
  assert.ok(forgottenSessionIds(window.localStorage).has('t1'));
  // Another tab's stale list, saved after the delete, does not resurrect it.
  saveSessions([rec('t1', 1000), rec('t2', 2000)], window.localStorage);
  assert.deepEqual(loadSessions(window.localStorage).map((r) => r.id), ['t2']);
  clearInterval(c._orchTick);
});
