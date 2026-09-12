// shapeLiveAssetRows — the Assets page's live asset register, shaped from
// svc-work-order-management's GET /api/assets and GET /api/work-orders/. The join is
// asset_name (assets) to the free-text `asset` field on a work order — the only key the
// two tables share, since work_orders.asset is a string typed by whoever raised it, not a
// foreign key. A name with no matching work order still renders, with a zero count.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { shapeLiveAssetRows } from '../src/logic/assetsLive.js';

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
