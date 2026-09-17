// componentDidMount must never write. A full-branch review (2026-09-09) found the old
// client-side report scheduler firing an unattended POST to the orchestrator on load and
// every 30s after — a report left overdue from a previous visit was caught up 4 seconds
// after the tab opened, with nobody having asked for anything. Report cards are server-owned
// now (svc-operations-intelligence's /api/reports engine + its own scheduler loop —
// engines/reports/scheduler.py) — refreshing a due card is the SERVER's job, never this
// browser's, so mount only ever reads the cards' current state (GET /api/reports) and polls
// the same way. This file is the regression test that keeps mount (and its poll) a
// read-only surface for reports too, whatever status a card comes back with.
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

const mem = {};
let calls;
globalThis.window = {
  location: { origin: 'http://test.local' }, scrollTo: () => {},
  addEventListener: () => {}, removeEventListener: () => {},
  localStorage: { getItem: (k) => (k in mem ? mem[k] : null), setItem: (k, v) => { mem[k] = String(v); }, removeItem: (k) => { delete mem[k]; } }
};
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };

let handlers;
globalThis.fetch = async (url, opts) => {
  const u = new URL(String(url));
  const method = (opts && opts.method) || 'GET';
  calls.push(method + ' ' + u.pathname);
  const h = handlers[method + ' ' + u.pathname] || handlers[u.pathname];
  // Mount must survive an unregistered route quietly — a 404, not a thrown TypeError — so a
  // reader can tell "nothing was called" from "something was called and rejected".
  if (!h) return { ok: false, status: 404, statusText: '404', text: async () => '{"detail":"not mocked"}' };
  const [status, body] = typeof h === 'function' ? h(u, opts) : h;
  return { ok: status >= 200 && status < 300, status: status, statusText: String(status), text: async () => JSON.stringify(body) };
};

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');

let c;
beforeEach(() => {
  Object.keys(mem).forEach((k) => { delete mem[k]; });
  calls = [];
  handlers = {};
  c = new HoistraLogic();
  c.setState({ signedIn: true, view: 'home' });
});
// Mirrors componentWillUnmount exactly — componentDidMount arms every one of these, and a
// suite that leaves any running is a suite that hangs past its own last assertion instead
// of exiting, rather than a suite that fails loudly.
const cleanup = (x) => {
  const k = x || c;
  clearInterval(k._orchTick); clearInterval(k._cronTimer); clearInterval(k._frameTimer); clearInterval(k._iotTimer);
  clearTimeout(k._ccRetry); clearTimeout(k._homeRetry); clearTimeout(k._homeRefresh);
  clearTimeout(k._vpRetry); clearTimeout(k._vpRefresh); clearTimeout(k._bldRetry);
  clearTimeout(k._gphRetry); clearTimeout(k._gphRefresh); clearTimeout(k._spRetry);
  clearTimeout(k._enRetry); clearTimeout(k._enPosRetry); clearTimeout(k._asLiveRetry); clearTimeout(k._mxLiveRetry);
  clearTimeout(k._usLiveRetry); clearTimeout(k._usLiveRefresh); clearTimeout(k._auLiveRetry);
  clearTimeout(k._saLiveRetry); clearTimeout(k._saLiveRefresh);
  clearTimeout(k._tt);
  k.rpStop();
};

const CARD = (over) => Object.assign({
  id: 'c1', report_id: 'r1', name: 'A report', prompt: 'Which vendors are blocked?',
  refresh_label: 'Refresh every 30 minutes', status: 'pending',
  last_run_at: null, last_tried_at: null, next_run_at: new Date(Date.now() - 90000).toISOString(),
  runs: [], latest_run: null
}, over || {});

test('mounting the controller issues only reads, never a write', async () => {
  c.componentDidMount();
  await new Promise((r) => setTimeout(r, 50));
  const nonGet = calls.filter((w) => !w.startsWith('GET '));
  assert.deepEqual(nonGet, [], 'componentDidMount must never issue a non-GET request');
  cleanup();
});

test('a card overdue from a previous visit does not get auto-run by this tab — that is the server scheduler\'s job', async () => {
  // A card GET /api/reports hands back with next_run_at already well in the past — exactly
  // the shape a card left overdue while this tab was closed would carry. The server's own
  // scheduler (engines/reports/scheduler.py) is the only thing allowed to act on that.
  handlers['GET /backend/ops-intelligence/api/reports'] = () => [200, { reports: [{ id: 'r1', name: 'A report', cards: [CARD()] }] }];
  handlers['GET /backend/ops-intelligence/api/reports/refresh-options'] = () => [200, { presets: [] }];
  handlers['POST /backend/ops-intelligence/api/reports/cards/c1/run'] = () => [200, CARD({ status: 'ready' })];
  c.componentDidMount();
  await new Promise((r) => setTimeout(r, 60));
  assert.equal(calls.some((w) => /cards\/c1\/run/.test(w)), false,
    'an overdue card must wait for the server\'s own scheduler tick (or an explicit Run now) — this browser never calls the run route on its own');
  assert.equal(c.state.reports[0].cards[0].status, 'pending', 'read back exactly as the backend sent it, untouched');
  cleanup();
});
