// The whole session history (Hussain, 7 Oct 2026): the server's threads are read page by page
// until they run out — not just the newest 60 — and any session, task or conversation, can be
// filed in any space, one at a time or many at once.
import { test, afterEach } from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = { location: { origin: 'http://test.local' }, scrollTo: () => {}, addEventListener: () => {}, removeEventListener: () => {} };
globalThis.fetch = () => Promise.reject(new TypeError('Failed to fetch'));
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
const { deepAgentsApi } = await import('../src/api/deepAgents.js');
const { makeSession } = await import('../src/logic/sessions.js');

const real = { threads: deepAgentsApi.threads };
let c = null;
afterEach(() => {
  deepAgentsApi.threads = real.threads;
  if (c) { clearInterval(c._orchTick); clearTimeout(c._tt); clearTimeout(c._homeRetry); clearTimeout(c._ccRetry); clearTimeout(c._vpRetry); clearTimeout(c._qTimer); clearTimeout(c._homeRefresh); }
  c = null;
});
const OWNER = 'pm@northbridge.co.uk';
const boot = (sessions) => {
  c = new HoistraLogic({});
  c.setState({ signedIn: true, account: { email: OWNER }, sessions: sessions || [] });
  return c;
};
// n threads, newest first, one minute apart, from `start` minutes ago.
const page = (n, start) => Array.from({ length: n }, (_, i) => ({
  id: 'th-' + (start + i), title: 'Question ' + (start + i), turn_count: 2,
  created_at: new Date(Date.UTC(2026, 9, 7, 12, 0) - (start + i) * 60000).toISOString(),
  last_message_at: new Date(Date.UTC(2026, 9, 7, 12, 0) - (start + i) * 60000).toISOString()
}));

test('every page of the server\'s threads is read, each from where the last ended', async () => {
  const asked = [];
  deepAgentsApi.threads = async (q, before) => { asked.push(before || null); return { ok: true, threads: before ? page(50, 200) : page(200, 0) }; };
  boot();
  await c.sessionsSyncFromServer();
  assert.equal(asked.length, 2);
  assert.equal(asked[0], null);
  assert.equal(asked[1], page(200, 0)[199].last_message_at, 'the second page starts after the last row of the first');
  assert.equal(c.state.sessions.length, 250, 'all 250 conversations listed, not the newest 60');
  assert.equal(c.state.sessionsServerCount, 250);
});

test('a server that ignores the cursor is read once, not forever', async () => {
  let calls = 0;
  deepAgentsApi.threads = async () => { calls += 1; return { ok: true, threads: page(200, 0) }; };
  boot();
  await c.sessionsSyncFromServer();
  assert.equal(calls, 2, 'the repeat page brings nothing new, so it stops');
  assert.equal(c.state.sessions.length, 200);
  // ...and does not say it read everything: an older server stops at 200 (8 Oct 2026 review).
  assert.equal(c.state.sessionsServerComplete, false);
});

test('threads sharing the boundary time are not skipped (the cursor is inclusive)', async () => {
  // The last two rows of page 1 and the first of page 2 share one last_message_at: the server
  // returns the boundary rows again, the client keeps one copy and reads on.
  const p1 = page(200, 0);
  const t = p1[199].last_message_at;
  p1[198].last_message_at = t;
  const p2 = [p1[198], p1[199], Object.assign(page(1, 500)[0], { last_message_at: t })].concat(page(10, 600));
  deepAgentsApi.threads = async (q, before) => ({ ok: true, threads: before ? p2 : p1 });
  boot();
  await c.sessionsSyncFromServer();
  assert.equal(c.state.sessions.length, 211, 'th-500 shares the boundary time and is still listed');
  assert.equal(c.state.sessionsServerComplete, true);
});

test('opening the Sessions page reads the history again', async () => {
  let calls = 0;
  deepAgentsApi.threads = async () => { calls += 1; return { ok: true, threads: [] }; };
  boot();
  c.openSessions(null);
  await new Promise((r) => setTimeout(r, 10));
  assert.equal(calls, 1);
});

test('tasks and conversations can be filed — one, or many at once — and unfiled', () => {
  const task = makeSession({ id: 't1', title: 'Upload Asbestos Register', page: 'Compliance', kind: 'task', owner: OWNER, at: 3 });
  const chat = makeSession({ id: 'c1', title: 'What needs my approval today?', page: 'Home', owner: OWNER, at: 2 });
  const other = makeSession({ id: 'c2', title: 'Boilers', page: 'Home', owner: OWNER, at: 1 });
  boot([task, chat, other]);
  const v = c.renderVals().sessionsPage;
  assert.ok(v.spaces.some((t) => t.key === 'compliance'), 'the built-in spaces are targets');
  v.fileMany(['t1', 'c1'], 'compliance');
  assert.deepEqual(c.state.sessions.filter((r) => r.spaceId === 'compliance').map((r) => r.id).sort(), ['c1', 't1']);
  assert.match(c.state.toast, /2 sessions added to Compliance/);
  c.renderVals().sessionsPage.fileMany(['t1'], null);
  assert.equal(c.state.sessions.find((r) => r.id === 't1').spaceId, null);
  const row = c.renderVals().sessionsPage.groups[0].rows.find((r) => r.id === 't1');
  assert.equal(row.canFile, true, 'a task row offers filing too');
});
