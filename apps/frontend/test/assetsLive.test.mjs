// shapeLiveAssetRows — the Assets page's live asset register, shaped from
// svc-work-order-management's GET /api/assets and GET /api/work-orders/. The join is
// asset_name (assets) to the free-text `asset` field on a work order — the only key the
// two tables share, since work_orders.asset is a string typed by whoever raised it, not a
// foreign key. A name with no matching work order still renders, with a zero count.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { shapeLiveAssetRows, healthBand, assetsLiveMethods } from '../src/logic/assetsLive.js';

test('an asset with two open work orders and one closed one counts only the open ones', () => {
  const assets = [{ asset_id: 'a1', asset_name: 'AHU-04', manufacturer: 'Trane', model: 'X200', serial_number: 'SN-1', active: true }];
  const wos = [
    { work_order_id: 'WO-1', asset: 'AHU-04', status: 'active' },
    { work_order_id: 'WO-2', asset: 'AHU-04', status: 'pending_approval' },
    { work_order_id: 'WO-3', asset: 'AHU-04', status: 'closed' },
    { work_order_id: 'WO-4', asset: 'Chiller-1', status: 'active' }
  ];
  const rows = shapeLiveAssetRows(assets, wos);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].name, 'AHU-04');
  assert.equal(rows[0].manufacturer, 'Trane');
  assert.equal(rows[0].openWorkOrders, 2);
});

test('the match on asset name is case-insensitive', () => {
  const assets = [{ asset_id: 'a1', asset_name: 'Chiller-2', manufacturer: null, model: null, serial_number: null, active: true }];
  const wos = [{ work_order_id: 'WO-1', asset: 'chiller-2', status: 'active' }];
  const rows = shapeLiveAssetRows(assets, wos);
  assert.equal(rows[0].openWorkOrders, 1);
});

test('an asset with no matching work order shows a zero count, not a crash', () => {
  const rows = shapeLiveAssetRows([{ asset_id: 'a1', asset_name: 'Pump-9', manufacturer: null, model: null, serial_number: null, active: false }], []);
  assert.equal(rows[0].openWorkOrders, 0);
  assert.equal(rows[0].manufacturer, '—');
  assert.equal(rows[0].status, 'inactive');
});

test('an empty or missing asset list returns an empty array', () => {
  assert.deepEqual(shapeLiveAssetRows([], []), []);
  assert.deepEqual(shapeLiveAssetRows(null, null), []);
});

// healthBand — assets.health_score (0-100) mapped to the same Threat/Watch/In-control
// language the condition-scan section uses, but from a real number. An asset that has
// never been scored is "unscored", not silently folded into "in control".
test('healthBand: below 40 is a threat, 40-69 is a watch, 70+ is in control, null/undefined is unscored', () => {
  assert.equal(healthBand(null).cond, 'unscored');
  assert.equal(healthBand(undefined).cond, 'unscored');
  assert.equal(healthBand(0).cond, 'threat');
  assert.equal(healthBand(39).cond, 'threat');
  assert.equal(healthBand(40).cond, 'watch');
  assert.equal(healthBand(69).cond, 'watch');
  assert.equal(healthBand(70).cond, 'ok');
  assert.equal(healthBand(100).cond, 'ok');
});

// asLiveGroups — buildings → assets on the real assets.building_id column (mixed into the
// controller; called here with a minimal fake `this`: bldData() for building names, no
// cost cache, no open groups).
function fakeController(bldRows) {
  return { bldData: () => bldRows || [], asLiveOpenAsset() {} };
}

test('asLiveGroups: assets group under their real building, named from bldData()', () => {
  const state = {
    asLive: [
      { asset_id: 'a1', asset_name: 'AHU-04', asset_code: 'A-0001', building_id: 'b1', manufacturer: 'Trane', model: null, serial_number: null, active: true, status: 'active', health_score: 82.4, criticality: 'High', category_id: 'c1', category_name: 'Air handling', installation_date: '2019-04-01' },
      { asset_id: 'a2', asset_name: 'Chiller-1', building_id: 'b1', manufacturer: null, model: null, serial_number: null, active: true, status: 'active', health_score: 22, criticality: 'Medium', category_id: null, category_name: null, installation_date: null },
      { asset_id: 'a3', asset_name: 'Pump-9', building_id: null, manufacturer: null, model: null, serial_number: null, active: true, status: 'retired', health_score: null, criticality: null, category_id: null, category_name: null, installation_date: null }
    ],
    asLiveWos: [], asLiveCost: {}, asLiveOpenB: []
  };
  const out = assetsLiveMethods.asLiveGroups.call(
    fakeController([{ buildingId: 'b1', name: 'Town Hall' }]),
    state
  );
  assert.equal(out.asLiveTreeGroups.length, 1);
  const g = out.asLiveTreeGroups[0];
  assert.equal(g.name, 'Town Hall');
  assert.equal(g.rows.length, 2);
  // Threat (health 22) ranks before ok (health 82).
  assert.equal(g.rows[0].name, 'Chiller-1');
  assert.equal(g.rows[0].tone, 'risk');
  assert.equal(g.rows[1].tone, 'ok');
  assert.equal(out.asLiveTreeUnlinked.length, 1);
  assert.equal(out.asLiveTreeUnlinked[0].name, 'Pump-9');
  assert.equal(out.asLiveTreeUnlinked[0].tone, 'dormant');
});

// Upstream resolves category_id to category_name server-side, returns health_score as a
// float (the column is NUMERIC), and carries asset_code and the raw status string.
test('asLiveGroups: upstream fields — category_name shown, float health rounded, code and raw status kept', () => {
  const state = {
    asLive: [
      { asset_id: 'a1', asset_name: 'AHU-04', asset_code: 'A-0001', building_id: 'b1', manufacturer: 'Trane', model: null, serial_number: null, active: true, status: 'active', health_score: 82.4, criticality: 'High', category_id: 'c1', category_name: 'Air handling', installation_date: '2019-04-01' },
      { asset_id: 'a2', asset_name: 'Pump-3', building_id: 'b1', manufacturer: null, model: null, serial_number: null, active: false, status: 'retired', health_score: null, criticality: null, category_id: 'c9', category_name: null, installation_date: null }
    ],
    asLiveWos: [], asLiveCost: {}, asLiveOpenB: []
  };
  const rows = assetsLiveMethods.asLiveGroups.call(
    fakeController([{ buildingId: 'b1', name: 'Town Hall' }]), state
  ).asLiveTreeGroups[0].rows;
  const ahu = rows.find((r) => r.name === 'AHU-04');
  const pump = rows.find((r) => r.name === 'Pump-3');
  // Float rounded for display, but banded on the real value (82.4 -> ok).
  assert.equal(ahu.healthLabel, 'Health score 82/100');
  assert.equal(ahu.healthScore, 82.4);
  assert.equal(ahu.tone, 'ok');
  assert.equal(ahu.code, 'A-0001');
  assert.equal(ahu.categoryName, 'Air handling');
  assert.equal(ahu.categoryText, 'Air handling');
  assert.equal(ahu.status, 'active');
  // The raw status string survives rather than being flattened to active/inactive.
  assert.equal(pump.status, 'retired');
  // A category_id with no resolvable name says so rather than showing a raw key.
  assert.equal(pump.categoryName, null);
  assert.equal(pump.categoryText, 'Uncategorised name not on file');
  assert.equal(pump.healthLabel, 'Not scored');
});

test('asLiveGroups: a building with no match in bldData() still renders, keyed on a shortened id', () => {
  const state = {
    asLive: [{ asset_id: 'a1', asset_name: 'AHU-04', building_id: 'b-does-not-resolve-1234', manufacturer: null, model: null, serial_number: null, active: true, health_score: null, criticality: null, category_id: null, installation_date: null }],
    asLiveWos: [], asLiveCost: {}, asLiveOpenB: []
  };
  const out = assetsLiveMethods.asLiveGroups.call(fakeController([]), state);
  assert.equal(out.asLiveTreeGroups.length, 1);
  assert.match(out.asLiveTreeGroups[0].name, /^Building b-does-n/);
});

test('asLiveGroups: an empty asLive returns no groups and no unlinked rows', () => {
  const out = assetsLiveMethods.asLiveGroups.call(fakeController([]), { asLive: [], asLiveWos: [], asLiveCost: {}, asLiveOpenB: [] });
  assert.deepEqual(out.asLiveTreeGroups, []);
  assert.deepEqual(out.asLiveTreeUnlinked, []);
  assert.equal(out.asLiveTreeEmpty, 'block');
});
