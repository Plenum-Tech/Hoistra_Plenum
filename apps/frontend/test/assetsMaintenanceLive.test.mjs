// Integration check for the wiring done in assetsLive.js / maintenanceLive.js: renderVals()
// must never throw for the assets or ops module regardless of whether the live register has
// loaded, and once it has, the page vals must actually reflect it (not silently keep
// rendering the seed) — this is the one thing the pure shaping-function tests cannot show,
// since they never go through mxVals()/asVals()/renderVals() the way the screens do.
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = { location: { origin: 'http://test.local' }, scrollTo: () => {} };

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');

let c;
beforeEach(() => {
  c = new HoistraLogic();
  c.setState({ signedIn: true, view: 'module', module: 'assets', filter: 'All' });
});

test('the assets module renders with no live register loaded yet — seed condition-scan section, empty live register', () => {
  const vals = c.renderVals();
  assert.equal(vals.asLiveOn, false);
  assert.deepEqual(vals.asLiveRows, []);
  assert.ok(Array.isArray(vals.asGroups), 'the seed condition-scan groups still render');
});

test('once the asset register loads, the live block reflects it without disturbing the seed section', () => {
  c.setState({
    asLive: [{ asset_id: 'a1', asset_name: 'AHU-04', manufacturer: 'Trane', model: null, serial_number: null, active: true }],
    asLiveWos: [{ work_order_id: 'WO-1', asset: 'AHU-04', status: 'active' }]
  });
  const vals = c.renderVals();
  assert.equal(vals.asLiveOn, true);
  assert.equal(vals.asLiveRows.length, 1);
  assert.equal(vals.asLiveRows[0].openWorkOrders, 1);
  assert.ok(Array.isArray(vals.asGroups), 'the seed condition-scan groups are untouched by the live load');
});

test('the ops module renders with no live data loaded yet — seed decisions and cards', () => {
  c.setState({ module: 'ops' });
  const vals = c.renderVals();
  assert.equal(vals.mxLiveOn, false);
  assert.ok(vals.mxDecisions.length > 0, 'seed decisions still render');
  assert.ok(vals.mxCards.length > 0, 'seed cards still render');
});

test('once work orders and dashboard stats load, the decisions grid and KPI tiles switch to live data', () => {
  c.setState({
    module: 'ops',
    mxStatsLive: { total: 9, by_status: { pending_approval: 2, active: 3, completed: 4 }, by_priority: { urgent: 1 }, by_source: {}, created_today: 1 },
    mxWosLive: [
      { work_order_id: 'WO-9', status: 'pending_approval', asset: 'Chiller-1', location: 'Roof plant', vendor: 'Acme', issue_description: 'Leak', priority: 'urgent', source: 'tenant', created_at: '2026-09-11T00:00:00Z' }
    ]
  });
  const vals = c.renderVals();
  assert.equal(vals.mxLiveOn, true);
  assert.equal(vals.mxCards[0].l, 'Open work orders');
  assert.ok(vals.mxDecisions.some((d) => d.id === 'WO-9'), 'the live work order appears in the decisions grid');
});

test('a successful load with zero open work orders shows a genuinely empty grid, not the seed decisions', () => {
  c.setState({
    module: 'ops',
    mxStatsLive: { total: 0, by_status: {}, by_priority: {}, by_source: {}, created_today: 0 },
    mxWosLive: []
  });
  const vals = c.renderVals();
  assert.deepEqual(vals.mxDecisions, []);
  assert.equal(vals.mxDecEmpty, 'block');
});
