// "Run energy scan" runs the scan.
//
// It used to send the words "Run energy scan" to the orchestrator and leave a model to pick
// a tool. That is the right shape for a question and the wrong one for a fixed verb: the
// rules are deterministic, the button has exactly one meaning, and the model was a failure
// mode with nothing on the other side of it. With an invalid API key the button did nothing
// at all and said nothing about why — no request reached the gateway, and the page simply
// carried on polling. Even with a working key the tool it reached scans ONE meter and
// cannot sweep history, so the answer covered the last five weeks of a year of readings.
//
// The Assets and Operations buttons already called their own reads directly. This one now
// matches them.
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

const mem = {};
let calls;
globalThis.window = {
  location: { origin: 'http://test.local' }, scrollTo: () => {},
  addEventListener: () => {}, removeEventListener: () => {},
  localStorage: {
    getItem: (k) => (k in mem ? mem[k] : null),
    setItem: (k, v) => { mem[k] = String(v); },
    removeItem: (k) => { delete mem[k]; }
  }
};
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };

let handlers;
globalThis.fetch = async (url, opts) => {
  const u = new URL(String(url));
  const method = (opts && opts.method) || 'GET';
  calls.push({ method, path: u.pathname, query: u.search });
  const h = handlers[method + ' ' + u.pathname] || handlers[u.pathname];
  const [status, body] = h ? (typeof h === 'function' ? h(u, opts) : h) : [200, { ok: true }];
  return {
    ok: status >= 200 && status < 300, status, statusText: String(status),
    text: async () => JSON.stringify(body)
  };
};

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
const SCAN = '/backend/ops-intelligence/api/energy/anomalies/scan-all';

let c;
beforeEach(() => {
  Object.keys(mem).forEach((k) => { delete mem[k]; });
  calls = [];
  handlers = {};
  c = new HoistraLogic();
  c.setState({ signedIn: true, view: 'module', module: 'energy', role: 'admin' });
});
const cleanup = () => { clearInterval(c._orchTick); clearTimeout(c._tt); };
const settle = () => new Promise((r) => setTimeout(r, 30));

test('the button calls the scan itself, and does not ask a model to', async () => {
  handlers['POST ' + SCAN] = () => [200, { ok: true, meters_swept: 4, created: 156 }];
  await c.enRunScan();
  const scan = calls.filter((x) => x.path === SCAN);
  assert.equal(scan.length, 1, 'exactly one scan request');
  assert.equal(scan[0].method, 'POST');
  assert.ok(
    !calls.some((x) => x.path.includes('/deep-agents/')),
    'nothing is routed through the orchestrator — that was the failure mode'
  );
  cleanup();
});

test('it sweeps a year, because a year of readings scanned once reports on its last month', async () => {
  handlers['POST ' + SCAN] = () => [200, { ok: true, meters_swept: 4, created: 156 }];
  await c.enRunScan();
  const scan = calls.find((x) => x.path === SCAN);
  assert.match(scan.query, /history_days=365/);
  cleanup();
});

test('it says what it found, in findings and meters', async () => {
  handlers['POST ' + SCAN] = () => [200, { ok: true, meters_swept: 4, created: 156 }];
  await c.enRunScan();
  await settle();
  assert.match(String(c.state.flowDone || c.state.toast || ''), /156|complete/i);
  cleanup();
});

test('a failure is reported, not swallowed', async () => {
  handlers['POST ' + SCAN] = () => [503, { detail: 'the engine is restarting' }];
  const ok = await c.enRunScan();
  assert.equal(ok, false);
  assert.match(String(c.state.flowDone || c.state.toast || ''), /failed/i);
  assert.equal(c.state.enScanning, false, 'the button is usable again after a failure');
  cleanup();
});

test('pressing it twice runs it once', async () => {
  let inflight;
  handlers['POST ' + SCAN] = () => [200, { ok: true, meters_swept: 4, created: 1 }];
  inflight = c.enRunScan();
  const second = await c.enRunScan();
  await inflight;
  assert.equal(second, false, 'the second press is refused while the first is running');
  assert.equal(calls.filter((x) => x.path === SCAN).length, 1);
  cleanup();
});

test('the register is re-read afterwards, so the page shows what the scan just wrote', async () => {
  handlers['POST ' + SCAN] = () => [200, { ok: true, meters_swept: 4, created: 156 }];
  await c.enRunScan();
  await settle();
  assert.ok(
    calls.some((x) => x.method === 'GET' && x.path.includes('/energy/anomalies')),
    'a scan that leaves the screen stale has only done half the job'
  );
  cleanup();
});
