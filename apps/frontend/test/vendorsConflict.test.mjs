// Resolving a work-order conflict from the page, instead of from psql.
//
// A conflicted work order is excluded from scoring — `fetch_completed_work_orders_from_udr`
// filters on `conflict_flag IS NOT TRUE` — and the flag is sticky. The backend has had a
// resolver all along, POST /work-orders/{wo_code}/resolve-conflict, taking whether to accept
// the stored values or the incoming ones. Nothing in the front end ever called it.
//
// So on 24 Sep 2026 two of Meridian's work orders were flagged by a comparison bug, the only
// vendor with a confirmed contract had nothing left to score, and the sole route back was a
// hand-written UPDATE. That is the gap these tests close: the queue item a person can already
// see now carries the two controls that resolve it.
//
// Accept stored / accept incoming is a real choice, not a dismissal. The flag means the two
// records genuinely disagree about a cost or a timestamp, and scoring measures money against
// those numbers — so the row states both sides and the person picks which is true.
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

const mem = {};
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

let handlers, calls;
globalThis.fetch = async (url, opts) => {
  const u = new URL(String(url));
  const method = (opts && opts.method) || 'GET';
  let body = null;
  try { body = opts && opts.body ? JSON.parse(opts.body) : null; } catch { body = opts.body; }
  calls.push({ method, path: u.pathname, body, query: u.search });
  const h = handlers[method + ' ' + u.pathname];
  if (!h) throw new TypeError('Failed to fetch: no handler for ' + method + ' ' + u.pathname);
  const [status, out] = typeof h === 'function' ? h(u, opts, body) : h;
  return {
    ok: status >= 200 && status < 300, status, statusText: String(status),
    text: async () => JSON.stringify(out)
  };
};

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
const { shapeLiveVendors } = await import('../src/logic/vendorsLive.js');

const B = '/backend/ops-intelligence/api/contract-performance';
const USER_UUID = '9b60f4e8-2f11-4d3e-8c7a-11d2a5f6b3c4';

//: The queue item the backend writes when a re-ingestion disagrees with the stored row.
const CONFLICT_ITEM = {
  id: 'a1f0c3d2-0000-4000-8000-000000000001',
  item_type: 'work_order_conflict',
  summary: 'WO-B-101-35 re-ingestion differs from stored values',
  created_at: '2026-09-24T05:50:00Z',
  payload: {
    wo_code: 'WO-B-101-35',
    conflict_details: {
      actual_cost: { stored: null, incoming: 712 },
      completed_at: { stored: '2026-08-12 15:40:00', incoming: '2026-08-12T15:40:00' }
    }
  }
};

let c;
beforeEach(() => {
  calls = [];
  handlers = {};
  c = new HoistraLogic();
  c.setState({ signedIn: true, account: { id: USER_UUID, email: 'pm@test.local', role: 'admin' } });
  c.vpLoad = async () => { c._reloaded = (c._reloaded || 0) + 1; };
});
const cleanup = () => { clearTimeout(c._tt); };

test('a work_order_conflict item becomes a row the page can show', () => {
  const shaped = shapeLiveVendors({ approvals: { items: [CONFLICT_ITEM] } }, new Date('2026-09-24T06:00:00Z'));
  assert.ok(Array.isArray(shaped.conflicts), 'shapeLiveVendors must expose conflicts');
  assert.equal(shaped.conflicts.length, 1);
  const row = shaped.conflicts[0];
  assert.equal(row.woCode, 'WO-B-101-35');
  assert.equal(row.itemId, CONFLICT_ITEM.id);
  // Both sides named, because the person is choosing between them.
  assert.match(row.fields, /actual_cost/);
});

test('an invoice flag is not mistaken for a conflict', () => {
  const invoice = { id: 'x', item_type: 'invoice_flag', payload: { line: {} } };
  const shaped = shapeLiveVendors({ approvals: { items: [invoice] } }, new Date());
  assert.equal((shaped.conflicts || []).length, 0);
});

test('accepting the stored values calls the resolver and reloads', async () => {
  handlers['POST ' + B + '/work-orders/WO-B-101-35/resolve-conflict'] =
    [200, { ok: true, wo_code: 'WO-B-101-35', conflict_flag: false }];
  await c.vpResolveConflict('WO-B-101-35', 'stored');
  const hit = calls.find((k) => /resolve-conflict$/.test(k.path));
  assert.ok(hit, 'the resolver must be called');
  assert.equal(hit.method, 'POST');
  assert.equal(hit.body.accept, 'stored');
  assert.equal(c._reloaded, 1, 'the page re-reads so the row disappears');
  cleanup();
});

test('accepting the incoming values sends that choice instead', async () => {
  handlers['POST ' + B + '/work-orders/WO-B-101-38/resolve-conflict'] =
    [200, { ok: true, wo_code: 'WO-B-101-38', conflict_flag: false }];
  await c.vpResolveConflict('WO-B-101-38', 'incoming');
  const hit = calls.find((k) => /resolve-conflict$/.test(k.path));
  assert.equal(hit.body.accept, 'incoming');
  cleanup();
});

test('a refused resolve surfaces the error and does not claim success', async () => {
  handlers['POST ' + B + '/work-orders/WO-B-101-35/resolve-conflict'] =
    [409, { ok: false, error: 'wo_not_found' }];
  await c.vpResolveConflict('WO-B-101-35', 'stored');
  assert.ok(c.state.vpError, 'the failure must be shown, not swallowed');
  assert.notEqual(c._reloaded, 1, 'a failed write must not look like it worked');
  cleanup();
});

test('an unknown accept value is refused before it reaches the network', async () => {
  await c.vpResolveConflict('WO-B-101-35', 'whatever');
  assert.equal(calls.length, 0, 'only stored or incoming are valid choices');
  cleanup();
});
