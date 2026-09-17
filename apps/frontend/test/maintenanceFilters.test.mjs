// The Maintenance grid's filter and grouping are SERVER parameters, not a client-side
// narrowing of rows already fetched. This matters for more than tidiness: filtering here
// would leave the cards and the chip counts above the grid describing the whole queue while
// the rows beneath them described a subset, and the two would quietly disagree.
//
// So these tests assert what actually goes on the wire, and that the tallies stay
// whole-scope while the list narrows.
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

const mem = {};
globalThis.window = {
  location: { origin: 'http://test.local' }, scrollTo: () => {},
  addEventListener: () => {}, removeEventListener: () => {},
  localStorage: { getItem: (k) => (k in mem ? mem[k] : null), setItem: (k, v) => { mem[k] = String(v); }, removeItem: (k) => { delete mem[k]; } }
};
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };

// Every decisions call the controller makes, with the query it carried.
let sent;
globalThis.fetch = async (url) => {
  const u = new URL(String(url));
  sent.push({ path: u.pathname, q: Object.fromEntries(u.searchParams) });
  const body = u.pathname.endsWith('/api/maintenance/decisions')
    ? decisionsFor(u.searchParams.get('state'), u.searchParams.get('source'), u.searchParams.get('group_by'))
    : { ok: true };
  return { ok: true, status: 200, statusText: '200', text: async () => JSON.stringify(body) };
};

// Four decisions across two states and two sources. The tallies are over everything in
// scope; only `decisions`/`groups` narrow — the shape the real route returns.
const ROWS = [
  { work_order: 'WO-1', state: 'Blocked', source: 'Vendors', asset: 'Boiler-22', building: 'Town Hall', estimated_cost: 640 },
  { work_order: 'WO-2', state: 'Blocked', source: 'Compliance', asset: 'Lift-1', building: 'Building 5', estimated_cost: null },
  { work_order: 'WO-3', state: 'Deviation', source: 'Vendors', asset: 'AHU-3', building: 'Town Hall', estimated_cost: 120 },
  { work_order: null, state: 'To raise', source: 'Energy', asset: 'CHILLER-102', building: 'Town Hall', estimated_cost: null }
];
function decisionsFor(state, source, groupBy) {
  const kept = ROWS.filter((r) => (!state || r.state === state) && (!source || r.source === source));
  const key = { state: 'state', source: 'source', building: 'building', vendor: 'vendor' }[groupBy] || 'state';
  const buckets = {};
  kept.forEach((r) => { (buckets[String(r[key] || 'Unassigned')] = buckets[String(r[key] || 'Unassigned')] || []).push(r); });
  return {
    ok: true, count: kept.length, total: ROWS.length, filtered: !!(state || source),
    by_state: { Blocked: 2, Deviation: 1, 'To raise': 1 },
    by_source: { Vendors: 2, Compliance: 1, Energy: 1 },
    available: { state: ['Blocked', 'To raise', 'Deviation'], source: ['Compliance', 'Energy', 'Vendors'] },
    group_by: groupBy || null,
    groups: Object.keys(buckets).map((k) => ({
      key: k, count: buckets[k].length,
      blocked: buckets[k].filter((r) => r.state === 'Blocked').length,
      to_raise: buckets[k].filter((r) => r.state === 'To raise').length,
      deviating: buckets[k].filter((r) => r.state === 'Deviation').length,
      awaiting_approval: 0,
      estimated_cost: buckets[k].some((r) => r.estimated_cost !== null)
        ? buckets[k].reduce((q, r) => q + (r.estimated_cost || 0), 0) : null,
      priced: buckets[k].filter((r) => r.estimated_cost !== null).length,
      decisions: buckets[k]
    })),
    decisions: kept
  };
}

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');

const settle = () => new Promise((r) => setTimeout(r, 20));
const lastDecisions = () => sent.filter((x) => x.path.endsWith('/api/maintenance/decisions')).pop();

let c;
beforeEach(() => {
  sent = [];
  c = new HoistraLogic();
  c.setState({ signedIn: true, view: 'module', module: 'ops', filter: 'All', mxGroup: 'State',
    account: { id: 'u1', email: 'a@b.c', role: 'admin', all_buildings: true, organization_name: 'Plenum Tech LLC' } });
});
const cleanup = () => { clearTimeout(c._mxLiveRetry); clearTimeout(c._mxLiveRefresh); clearTimeout(c._tt); };

test('the first read asks for the whole queue, grouped by state', async () => {
  await c.mxLiveLoad();
  await settle();
  const d = lastDecisions();
  assert.equal(d.q.group_by, 'state');
  assert.equal(d.q.state, undefined, 'no filter means no state parameter');
  assert.equal(d.q.source, undefined);
  assert.equal(d.q.limit, '500', 'the route ceiling, so the grid and the cards count the same set');
  cleanup();
});

test('a state chip goes to the server as state=, and the tallies stay whole-scope', async () => {
  await c.mxLiveLoad();
  await settle();
  const before = c.renderVals();
  assert.equal(before.mxDecSummary, '4 of 4 decisions');

  c.renderVals().modFilters.find((f) => f.label === 'Blocked').click();
  await settle();
  const d = lastDecisions();
  assert.equal(d.q.state, 'Blocked', 'the filter is a server parameter');
  assert.equal(d.q.source, undefined, 'a state is not sent as a source');

  const after = c.renderVals();
  assert.equal(after.mxDecSummary, '2 of 4 decisions', 'the list narrows, the scope does not');
  assert.equal(after.mxGroups[0].items.length, 2);
  after.mxGroups[0].items.forEach((i) => assert.equal(i.state, 'Blocked'));
  // The chip counts still describe the whole queue, not the filtered view.
  assert.equal(after.modFilters.find((f) => f.label === 'Deviation').n, '1');
  assert.equal(after.modFilters.find((f) => f.label === 'All').n, '4');
  cleanup();
});

test('a source chip goes as source=, not as a state', async () => {
  await c.mxLiveLoad();
  await settle();
  c.renderVals().modFilters.find((f) => f.label === 'Vendors').click();
  await settle();
  const d = lastDecisions();
  assert.equal(d.q.source, 'Vendors');
  assert.equal(d.q.state, undefined);
  const v = c.renderVals();
  assert.equal(v.mxDecSummary, '2 of 4 decisions');
  v.mxGroups.forEach((g) => g.items.forEach((i) => assert.equal(i.src, 'Vendors')));
  cleanup();
});

test('only the states and sources the backend says it holds are offered', async () => {
  await c.mxLiveLoad();
  await settle();
  const labels = c.renderVals().modFilters.map((f) => f.label);
  assert.deepEqual(labels, ['All', 'Blocked', 'To raise', 'Deviation', 'Compliance', 'Energy', 'Vendors']);
  // "Awaiting approval" is a real state but this scope holds none, so it is not a chip that
  // could only ever come back empty.
  assert.ok(labels.indexOf('Awaiting approval') < 0);
  cleanup();
});

test('changing Group by re-reads with group_by, rather than regrouping rows in the client', async () => {
  await c.mxLiveLoad();
  await settle();
  const reads = sent.filter((x) => x.path.endsWith('/api/maintenance/decisions')).length;

  c.renderVals().mxGroupOpts.find((g) => g.label === 'Building').pick();
  await settle();
  assert.equal(sent.filter((x) => x.path.endsWith('/api/maintenance/decisions')).length, reads + 1,
    'it went back to the server');
  assert.equal(lastDecisions().q.group_by, 'building');

  const v = c.renderVals();
  assert.deepEqual(v.mxGroups.map((g) => g.name).sort(), ['Building 5', 'Town Hall']);
  const th = v.mxGroups.find((g) => g.name === 'Town Hall');
  assert.equal(th.n, '3');
  assert.equal(th.total, '£760', 'the group total is the backend\'s, over that group only');
  cleanup();
});

test('picking the filter already in force does not re-read', async () => {
  await c.mxLiveLoad();
  await settle();
  const reads = sent.filter((x) => x.path.endsWith('/api/maintenance/decisions')).length;
  c.renderVals().modFilters.find((f) => f.label === 'All').click();
  await settle();
  assert.equal(sent.filter((x) => x.path.endsWith('/api/maintenance/decisions')).length, reads);
  cleanup();
});

test('a filter and a grouping hold together, and clearing the filter restores the whole queue', async () => {
  await c.mxLiveLoad();
  await settle();
  c.renderVals().modFilters.find((f) => f.label === 'Blocked').click();
  await settle();
  c.renderVals().mxGroupOpts.find((g) => g.label === 'Building').pick();
  await settle();
  let d = lastDecisions();
  assert.equal(d.q.state, 'Blocked');
  assert.equal(d.q.group_by, 'building', 'the filter survives the regroup');

  c.renderVals().modFilters.find((f) => f.label === 'All').click();
  await settle();
  d = lastDecisions();
  assert.equal(d.q.state, undefined, 'All clears the state parameter');
  assert.equal(d.q.group_by, 'building', 'and leaves the grouping alone');
  assert.equal(c.renderVals().mxDecSummary, '4 of 4 decisions');
  cleanup();
});

// ── the acting company, on the wire ──────────────────────────────────────────
// A superadmin "viewing as" a company only narrows anything if the parameter actually
// leaves the browser. It did not, which is why two companies showed identical figures.

const { setActingOrg } = await import('../src/api/client.js');

test('acting as a company puts organization_id on every maintenance read', async () => {
  setActingOrg('11111111-2222-3333-4444-555555555555');
  try {
    await c.mxLiveLoad();
    await settle();
    const maintenance = sent.filter((x) => x.path.indexOf('/api/maintenance/') > -1);
    assert.ok(maintenance.length >= 4, 'the page makes several reads');
    // The suggestions chips are the one read with nothing company-shaped behind them.
    maintenance
      .filter((x) => !x.path.endsWith('/ask/suggestions') && !x.path.endsWith('/last-read'))
      .forEach((x) => assert.equal(x.q.organization_id, '11111111-2222-3333-4444-555555555555',
        x.path + ' carries the acting company'));
  } finally {
    setActingOrg(null);
    cleanup();
  }
});

test('with no company chosen nothing is sent, so the server keeps its own default', async () => {
  setActingOrg(null);
  await c.mxLiveLoad();
  await settle();
  sent.filter((x) => x.path.indexOf('/api/maintenance/') > -1)
    .forEach((x) => assert.equal(x.q.organization_id, undefined,
      'an empty VITE_ORGANIZATION_ID must not send a blank parameter'));
  cleanup();
});
