// The review of 8 Oct 2026 on the Sessions / Reports changes: reading old history must not
// reorder it, a deleted session stays deleted, Support is not a filing target, one history read
// at a time, and a new report's first run is never skipped.
import { test, afterEach } from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = { location: { origin: 'http://test.local' }, scrollTo: () => {}, addEventListener: () => {}, removeEventListener: () => {} };
globalThis.fetch = () => Promise.reject(new TypeError('Failed to fetch'));
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
const { deepAgentsApi } = await import('../src/api/deepAgents.js');
const { reportsApi } = await import('../src/api/reports.js');
const { makeSession } = await import('../src/logic/sessions.js');

const real = { threads: deepAgentsApi.threads, thread: deepAgentsApi.thread, del: deepAgentsApi.deleteThread, traces: deepAgentsApi.traceTurns, run: reportsApi.runCard, list: reportsApi.list };
let c = null;
afterEach(() => {
  Object.assign(deepAgentsApi, { threads: real.threads, thread: real.thread, deleteThread: real.del, traceTurns: real.traces });
  Object.assign(reportsApi, { runCard: real.run, list: real.list });
  if (c) { clearInterval(c._orchTick); clearTimeout(c._tt); clearTimeout(c._homeRetry); clearTimeout(c._ccRetry); clearTimeout(c._vpRetry); clearTimeout(c._qTimer); clearTimeout(c._homeRefresh); }
  c = null;
});
const OWNER = 'pm@northbridge.co.uk';
const settle = (ms) => new Promise((r) => setTimeout(r, ms || 20));
const boot = (sessions, state) => {
  c = new HoistraLogic({});
  c.setState(Object.assign({ signedIn: true, account: { email: OWNER }, sessions: sessions || [] }, state));
  return c;
};
const chat = (id, at, extra) => Object.assign(makeSession({ id, title: 'Question ' + id, page: 'Home', owner: OWNER, at }), extra || {});
const toast = () => { const seen = []; c.flash = (m) => seen.push(m); return seen; };

test('opening an old conversation reads it back without moving it to "just now"', async () => {
  const old = chat('th-old', 1000, { turns: [], remote: true, serverTurns: 1 });
  boot([chat('th-new', 5000, { turns: [{ role: 'you', text: 'hi' }] }), old]);
  deepAgentsApi.thread = async () => ({ ok: true, thread: { id: 'th-old', title: 'Question th-old', turns: [{ question: 'Which FRAs lapse?', answer: 'Two.' }] } });
  deepAgentsApi.traceTurns = async () => ({ ok: true, turns: [] });
  c.openSession('th-old');
  await settle();
  const rec = c.state.sessions.find((x) => x.id === 'th-old');
  assert.equal(rec.at, 1000, 'reading is not activity');
  assert.equal(c.state.ccChat.length, 2);
  assert.equal(c.state.sessions[0].id, 'th-new', 'the order is unchanged');
});

test('a session deleted while the history is being read does not come back', async () => {
  boot([chat('th-1', 3000), chat('th-2', 2000)]);
  let release;
  deepAgentsApi.threads = () => new Promise((r) => { release = () => r({ ok: true, threads: [
    { id: 'th-1', title: 'Question th-1', last_message_at: '2026-10-07T10:00:00Z' },
    { id: 'th-2', title: 'Question th-2', last_message_at: '2026-10-07T09:00:00Z' }] }); });
  deepAgentsApi.deleteThread = async () => ({ ok: true });
  const sync = c.sessionsSyncFromServer();
  c.deleteSession('th-1');
  release();
  await sync;
  assert.deepEqual(c.state.sessions.map((x) => x.id), ['th-2']);
});

test('a delete the server refused is said, not swallowed', async () => {
  boot([chat('th-1', 3000)]);
  const seen = toast();
  deepAgentsApi.deleteThread = async () => { throw new Error('timed out'); };
  c.deleteSession('th-1');
  await settle();
  assert.ok(seen.some((m) => /could not be deleted on the server/i.test(m)), seen.join(' | '));
});

test('a thread the server does not have (404) is already gone: no "could not be deleted" warning', async () => {
  // The server answers 404 for a thread it never stored or already deleted (8 Oct 2026 review).
  boot([chat('th-1', 3000)]);
  const seen = toast();
  deepAgentsApi.deleteThread = async () => { const e = new Error('Not found'); e.status = 404; throw e; };
  c.deleteSession('th-1');
  await settle();
  assert.ok(!seen.some((m) => /could not be deleted/i.test(m)), seen.join(' | '));
  assert.ok(c._sessDeleted.has('th-1'), 'still hidden from the next history read');
});

test('a bulk delete removes them at once and sends at most four deletes at a time', async () => {
  const list = Array.from({ length: 30 }, (_, i) => chat('th-' + i, 1000 + i));
  boot(list);
  let inFlight = 0, peak = 0, sent = 0;
  deepAgentsApi.deleteThread = async () => { inFlight += 1; sent += 1; peak = Math.max(peak, inFlight); await settle(2); inFlight -= 1; return { ok: true }; };
  let writes = 0;
  const set = c.setState.bind(c);
  c.setState = (u, cb) => { if (typeof u === 'function' || (u && 'sessions' in u)) writes += 1; return set(u, cb); };
  c.deleteSessions(list.map((x) => x.id));
  assert.equal(c.state.sessions.length, 0, 'gone from the list straight away');
  assert.equal(writes, 1, 'one state write, not one per session');
  await settle(200);
  assert.equal(sent, 30);
  assert.ok(peak <= 4, 'peak ' + peak);
});

test('Support is not somewhere a session can be filed', () => {
  boot([chat('th-1', 3000)]);
  const seen = toast();
  c.renderVals().sessionsPage.fileMany(['th-1'], 'support');
  assert.equal(c.state.sessions[0].spaceId || null, null);
  assert.ok(seen.some((m) => /Support/.test(m)));
});

test('one history read at a time', async () => {
  boot([]);
  let calls = 0;
  deepAgentsApi.threads = async () => { calls += 1; await settle(5); return { ok: true, threads: [] }; };
  await Promise.all([c.sessionsSyncFromServer(), c.sessionsSyncFromServer(), c.sessionsSyncFromServer()]);
  assert.equal(calls, 1);
});

test('the history only claims to be whole when every page was read', async () => {
  boot([]);
  const page = Array.from({ length: 200 }, (_, i) => ({ id: 'th-' + i, title: 'Q', last_message_at: new Date(Date.UTC(2026, 9, 7, 12) - i * 60000).toISOString() }));
  deepAgentsApi.threads = async (q, before) => { if (before) throw new Error('500'); return { ok: true, threads: page }; };
  await c.sessionsSyncFromServer();
  assert.doesNotMatch(c.renderVals().sessionsPage.count, /Full history/);
  deepAgentsApi.threads = async () => ({ ok: true, threads: page.slice(0, 3) });
  await c.sessionsSyncFromServer();
  assert.match(c.renderVals().sessionsPage.count, /Full history/);
});

test("a new report's first run is not skipped because another card is refreshing", async () => {
  boot([]);
  const ran = [];
  reportsApi.runCard = async (id) => { ran.push(id); await settle(30); return { ok: true }; };
  reportsApi.list = async () => ({ ok: true, reports: [] });
  const a = c.rpRunCard('card-a');
  await c.rpRunCard('card-b');
  await a;
  assert.deepEqual(ran.sort(), ['card-a', 'card-b']);
  const again = c.rpRunCard('card-a');
  await c.rpRunCard('card-a');
  await again;
  assert.equal(ran.filter((x) => x === 'card-a').length, 2, 'the same card is still run once at a time');
});

test('the chat page can file in a built-in space and shows where it is filed', () => {
  boot([chat('th-1', 3000, { spaceId: 'compliance' })], { sessionId: 'th-1' });
  const v = c.renderVals();
  assert.equal(v.chatCanFile, true);
  assert.ok(v.chatFileOptions.some((o) => o.value === 'compliance'));
  assert.equal(v.chatFileValue, 'compliance');
});

test('reopening the active conversation whose transcript was shed reads it back', async () => {
  boot([chat('th-1', 3000, { turns: [], remote: true, serverTurns: 1 })], { sessionId: 'th-1', ccChat: [] });
  let asked = 0;
  deepAgentsApi.thread = async () => { asked += 1; return { ok: true, thread: { id: 'th-1', turns: [{ question: 'Q', answer: 'A' }] } }; };
  deepAgentsApi.traceTurns = async () => ({ ok: true, turns: [] });
  c.openSession('th-1');
  await settle();
  assert.equal(asked, 1);
  assert.equal(c.state.ccChat.length, 2);
});
