// componentDidMount must never write. A full-branch review (2026-09-09) found rpStart()
// firing an unattended POST to the orchestrator on load and every 30s after — a report left
// overdue from a previous visit (loadReports even forces an interrupted run back to due) was
// caught up 4 seconds after the tab opened, with nobody having asked for anything. This file
// is the regression test that keeps mount a read-only surface, and locks in the fix:
// `_rpBootAt` parks anything overdue from before this boot rather than auto-running it.
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
const { REPORTS_KEY } = await import('../src/logic/reports.js');

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
  clearInterval(k._orchTick); clearInterval(k._cronTimer); clearInterval(k._frameTimer);
  clearTimeout(k._ccRetry); clearTimeout(k._homeRetry); clearTimeout(k._homeRefresh);
  clearTimeout(k._vpRetry); clearTimeout(k._vpRefresh); clearTimeout(k._bldRetry);
  clearTimeout(k._gphRetry); clearTimeout(k._gphRefresh); clearTimeout(k._spRetry);
  clearTimeout(k._tt);
  k.rpStop();
};

const REPORT = (over) => Object.assign({
  key: 'r1', name: 'A report', prompt: 'Which vendors are blocked?', sessionId: null, page: 'Home',
  cad: { i: 0 }, createdAt: Date.now(), lastRunAt: null, lastTriedAt: null, nextRunAt: Date.now(),
  runs: [], error: '', status: 'pending'
}, over || {});

test('mounting the controller issues only reads, never a write', async () => {
  c.componentDidMount();
  await new Promise((r) => setTimeout(r, 50));
  const nonGet = calls.filter((w) => !w.startsWith('GET '));
  assert.deepEqual(nonGet, [], 'componentDidMount must never issue a non-GET request');
  cleanup();
});

test('an overdue report from a previous visit does not auto-run the moment the tab reopens', async () => {
  // What loadReports() actually hands back for a report left running when the tab closed:
  // status forced to pending, nextRunAt forced to the past — exactly the shape that used to
  // trigger the 4-second mount catch-up.
  mem[REPORTS_KEY] = JSON.stringify([REPORT({ nextRunAt: Date.now() - 90000 })]);
  handlers['POST /backend/deep-agents/api/workflow/run-stateful'] = () => [200, { session_id: 's', answer: 'ok', tool_calls: [], success: true }];
  c = new HoistraLogic();
  c.setState({ signedIn: true, view: 'home' });
  c.componentDidMount();
  // The worst case: the recurring tick fires immediately, not 30 seconds from now.
  c.rpTick();
  await new Promise((r) => setTimeout(r, 30));
  assert.equal(calls.some((w) => /run-stateful/.test(w)), false,
    'a report overdue from before this boot must wait for the user (Run now, or its own next cadence) — not fire unattended the instant the tab opens');
  assert.equal(c.state.reports[0].status, 'pending', 'left exactly as it was');
  cleanup();
});

test('a cadence that comes due while the tab is open still runs, unattended, as designed', async () => {
  handlers['POST /backend/deep-agents/api/workflow/run-stateful'] = () => [200, { session_id: 's', answer: 'ok', tool_calls: [], success: true }];
  c.componentDidMount();
  // Due a moment from boot, not before it — this is the case the feature exists for: "the
  // orchestrator runs it against the current Hoist Graph at the next due time while Hoistra
  // is open" (the report page's own copy).
  c.setState({ reports: [REPORT({ nextRunAt: c._rpBootAt + 5 })] });
  await new Promise((r) => setTimeout(r, 20));
  c.rpTick();
  await new Promise((r) => setTimeout(r, 30));
  assert.equal(c.state.reports[0].status, 'ready', 'a cadence due after boot fires on its own, exactly as the report page promises');
  cleanup();
});
