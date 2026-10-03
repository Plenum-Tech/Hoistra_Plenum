// buildingsGraph — the per-row drawer's cost-drivers section: GET
// /buildings/{id}/cost-drivers, ranked on the gap over contract rather than on billed, with
// spend no work order attributes to plant reported separately. Read-only, so this exercises
// the real controller against a mocked fetch — no live network, but no need to intercept a
// write either.
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

const mem = {};
globalThis.window = {
  location: { origin: 'http://test.local' }, scrollTo: () => {},
  addEventListener: () => {}, removeEventListener: () => {},
  localStorage: { getItem: (k) => (k in mem ? mem[k] : null), setItem: (k, v) => { mem[k] = String(v); }, removeItem: (k) => { delete mem[k]; } }
};
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };

let handlers;
globalThis.fetch = async (url) => {
  const u = new URL(String(url));
  const h = handlers[u.pathname];
  if (!h) throw new TypeError('Failed to fetch: no handler for ' + u.pathname);
  const [status, body] = typeof h === 'function' ? h(u) : h;
  return { ok: status >= 200 && status < 300, status: status, statusText: String(status), text: async () => JSON.stringify(body) };
};

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');

const ID = 'b0000000-0000-0000-0000-000000000001';
const GRAPH_PATH = '/backend/ops-intelligence/api/energy/buildings/' + ID + '/graph';
const COST_PATH = '/backend/ops-intelligence/api/energy/buildings/' + ID + '/cost-drivers';

let c;
beforeEach(() => {
  Object.keys(mem).forEach((k) => { delete mem[k]; });
  handlers = { [GRAPH_PATH]: () => [200, { branches: [] }] };
  c = new HoistraLogic();
  c.setState({ signedIn: true, view: 'buildings', role: 'user' });
});
const cleanup = () => { clearInterval(c._orchTick); clearTimeout(c._tt); };
const settle = () => new Promise((r) => setTimeout(r, 20));

test('opening a row fetches its cost drivers alongside its graph, ranked on over_contract, with unattributed spend kept out of the ranking', async () => {
  handlers[COST_PATH] = () => [200, {
    ok: true,
    assets: [
      { asset_id: 'a1', asset_code: 'LIFT-002', work_orders: 3, lines: 3, billed: 3830, over_contract: 1580, flagged_lines: 2 },
      { asset_id: 'a2', asset_code: 'AHU-004', work_orders: 1, lines: 1, billed: 180, over_contract: 0, flagged_lines: 0 }
    ],
    unattributed: { billed: 310, over_contract: 40, lines: 1 },
    totals: { billed: 4320, over_contract: 1620, lines: 5, work_orders: 4 },
    lines_without_delta: 0, missing: []
  }];

  c.bgToggle(ID);
  await settle();
  const g = c.bgVals().bgFor({ id: ID });
  assert.equal(g.cost.show, 'block');
  assert.equal(g.cost.loading, false);
  assert.equal(g.cost.rows.length, 2);
  // Ranked on over_contract as the API returns it — LIFT-002 (1,580) first even though a
  // higher-billed row could exist; this fixture already lists the biggest gap first, so the
  // test pins that the shaping does not silently re-sort by billed instead.
  assert.equal(g.cost.rows[0].asset, 'LIFT-002');
  assert.equal(g.cost.rows[0].over, '1,580.00');
  assert.equal(g.cost.rows[0].overTone, 'var(--st-risk)');
  assert.equal(g.cost.rows[1].over, '0.00');
  assert.equal(g.cost.rows[1].overTone, 'var(--color-neutral-500)');
  assert.ok(g.cost.unattributed, 'unattributed spend is reported');
  assert.equal(g.cost.unattributed.asset, '(no asset)');
  assert.equal(g.cost.unattributed.lines, '1');
  assert.equal(g.cost.totalBilled, '4,320.00');
  assert.equal(g.cost.totalOver, '1,620.00');
  cleanup();
});

test('a building with nothing billed says so instead of showing an empty table', async () => {
  handlers[COST_PATH] = () => [200, { ok: true, assets: [], unattributed: {}, totals: {}, lines_without_delta: 0, missing: [] }];
  c.bgToggle(ID);
  await settle();
  const g = c.bgVals().bgFor({ id: ID });
  assert.equal(g.cost.rows.length, 0);
  assert.equal(g.cost.unattributed, null);
  assert.equal(g.cost.totalsShow, 'none');
  assert.match(g.cost.note, /Nothing billed against this building yet/);
  cleanup();
});

test('a backend without this route yet says so plainly, distinct from a real failure', async () => {
  handlers[COST_PATH] = () => [404, { error: 'not found' }];
  c.bgToggle(ID);
  await settle();
  const g = c.bgVals().bgFor({ id: ID });
  assert.match(g.cost.error, /not on this svc-operations-intelligence yet/);
  cleanup();
});

test('cost drivers are fetched once per building and cached, not re-fetched on every toggle', async () => {
  let hits = 0;
  handlers[COST_PATH] = () => { hits += 1; return [200, { ok: true, assets: [], unattributed: {}, totals: {}, missing: [] }]; };
  c.bgToggle(ID); await settle();
  c.bgToggle(ID); // close
  c.bgToggle(ID); // reopen
  await settle();
  assert.equal(hits, 1);
  cleanup();
});

test('a building that is gone is asked about once, not on every render', async () => {
  // The failure this exists for: an error was cached, the guard read "cached and not an
  // error", so the next render counted it as nothing cached and asked again — immediately,
  // for as long as the tab stayed open. One deleted building code put the gateway under a
  // request per frame from every tab.
  let graphCalls = 0;
  let costCalls = 0;
  handlers[GRAPH_PATH] = () => { graphCalls += 1; return [404, { detail: { ok: false, reason: 'building_not_found' } }]; };
  handlers[COST_PATH] = () => { costCalls += 1; return [404, { detail: { ok: false, reason: 'building_not_found' } }]; };

  c.bgToggle(ID);
  await settle();
  assert.equal(graphCalls, 1);
  assert.equal(costCalls, 1);

  // Whatever re-renders next must not turn a settled "no" back into a question.
  for (let i = 0; i < 5; i += 1) { c.bgLoad(ID); await settle(); }
  assert.equal(graphCalls, 1, 'a 404 is final — the building is gone and asking again cannot change that');
  assert.equal(costCalls, 1);
  cleanup();
});

test('a 401 is not retried either — signing in is what fixes it, not asking again', async () => {
  let calls = 0;
  handlers[GRAPH_PATH] = () => { calls += 1; return [401, { detail: { ok: false, reason: 'missing_token' } }]; };
  handlers[COST_PATH] = () => [401, { detail: { ok: false } }];
  c.bgToggle(ID);
  await settle();
  for (let i = 0; i < 4; i += 1) { c.bgLoad(ID); await settle(); }
  assert.equal(calls, 1);
  cleanup();
});

test('a server error IS retried, but a bounded number of times', async () => {
  // A 5xx or a dropped connection may genuinely differ next time — a restart mid-request is
  // the ordinary case — so these are worth re-asking. Bounded, so a service that stays down
  // does not become the same flood by another route.
  let calls = 0;
  handlers[GRAPH_PATH] = () => { calls += 1; return [503, { detail: 'restarting' }]; };
  handlers[COST_PATH] = () => [503, { detail: 'restarting' }];
  c.bgToggle(ID);
  await settle();
  for (let i = 0; i < 8; i += 1) { c.bgLoad(ID); await settle(); }
  assert.ok(calls > 1, 'a transient failure is worth asking about again');
  assert.ok(calls <= 3, 'but not without end — got ' + calls);
  cleanup();
});
